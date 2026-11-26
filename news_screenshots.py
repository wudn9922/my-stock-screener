
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright


# 可在 GitHub Actions Secrets / Variables 自訂捲動距離
WSJ_SCROLL_Y = int(os.environ.get("WSJ_SCROLL_Y", "650"))
BARRONS_SCROLL_Y = int(os.environ.get("BARRONS_SCROLL_Y", "400"))

# WSJ 會擋 GitHub Actions 的資料中心 IP（驗證頁），所以 WSJ 不直接連線，
# 改截 Internet Archive（Wayback Machine）最近一次的首頁存檔。
# 存檔比現在舊，超過這個小時數就視為過期、不使用。
WAYBACK_MAX_AGE_HOURS = int(os.environ.get("WAYBACK_MAX_AGE_HOURS", "36"))
WAYBACK_CDX_API = "https://web.archive.org/cdx/search/cdx"
WAYBACK_AVAILABLE_API = "https://archive.org/wayback/available"

# JPEG 比 PNG 小很多，LINE 圖片大小有限制（約 1MB）
JPEG_QUALITY = 85

# LINE 的 Flex 圖片每次打開訊息都會重新讀取網址。若網址指向會被覆寫的
# docs/wsj.jpg，舊訊息永遠顯示最新的圖（2026-11-04～06 都顯示同一張）。
# 因此每張送出的圖都另存成「日期＋內容指紋」的檔名，永不覆寫，只保留 30 天。
NEWS_DIR = "docs/news"
NEWS_STATE_PATH = "docs/news/state.json"
NEWS_RETENTION_DAYS = int(os.environ.get("NEWS_RETENTION_DAYS", "30"))
# 同一來源連續失敗時，只在第 1 天和第 7 天說明原因，其餘只寫「今日無新聞」
NEWS_FAILURE_NOTICE_DAYS = (1, 7)
NEWS_FILE_PATTERN = re.compile(r"^(?P<key>[a-z0-9]+)-(?P<date>\d{8})-(?P<digest>[0-9a-f]{12})\.jpg$")

# 防機器人驗證頁的特徵文字與 iframe 網址
BLOCKED_PAGE_MARKERS = (
    "Verification Required",
    "Slide right to secure your access",
    "there is a robot on the same network",
)
BLOCKED_FRAME_URL_MARKERS = (
    "captcha-delivery.com",
    "datadome",
)



def is_blocked_page(text):
    """頁面文字是否為防機器人驗證頁，而不是新聞首頁。"""
    text = text or ""
    return any(marker in text for marker in BLOCKED_PAGE_MARKERS)


def detect_blocked_page(page, response):
    """
    判斷 Playwright 目前頁面是不是防機器人驗證頁，是的話回傳原因。

    驗證頁的文字常放在 iframe 內，只讀主頁面 body 會漏掉，
    所以檢查 HTTP 狀態碼、iframe 網址與所有 frame 的文字。
    """
    status = getattr(response, "status", None)

    if status is not None and status >= 400:
        return f"HTTP {status}"

    for frame in page.frames:
        frame_url = str(getattr(frame, "url", "") or "").lower()

        if any(marker in frame_url for marker in BLOCKED_FRAME_URL_MARKERS):
            return "出現驗證用 iframe"

        try:
            frame_text = frame.evaluate(
                "() => document.body ? "
                "document.body.innerText.slice(0, 3000) : ''"
            )
        except Exception as e:
            print(f"讀取 frame 文字失敗（略過）：{e}")
            continue

        if is_blocked_page(frame_text):
            return "頁面出現 Verification Required"

    return None


def get_wayback_snapshot(site, page_url, max_age_hours=None):
    """
    用 CDX 選取期限內最新的 200 HTML 存檔，失敗時退回 available API。

    回傳 (可直接開啟的存檔網址, 存檔時間)。
    網址使用 if_ 模式，不會顯示 Wayback 工具列。
    找不到、不是 200、或超過 max_age_hours 都會丟出 RuntimeError。
    """
    if max_age_hours is None:
        max_age_hours = WAYBACK_MAX_AGE_HOURS

    now = datetime.now(timezone.utc)
    cdx_params = {
        "url": site,
        "from": (now - timedelta(hours=max_age_hours)).strftime("%Y%m%d%H%M%S"),
        "to": now.strftime("%Y%m%d%H%M%S"),
        "output": "json",
        "fl": "timestamp,statuscode,mimetype",
    }
    # 不在伺服器端過濾狀態與 MIME，才能記錄跳過了幾筆較新的壞存檔及原因。
    cdx_rows = None
    for attempt in range(1, 4):
        try:
            response = requests.get(WAYBACK_CDX_API, params=cdx_params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list) or (data and (
                data[0] != ["timestamp", "statuscode", "mimetype"]
                or any(not isinstance(row, list) or len(row) != 3 for row in data[1:])
            )):
                raise ValueError("CDX 回傳格式錯誤")
            cdx_rows = data[1:]
            break
        except Exception as e:
            print(f"Wayback CDX 查詢第 {attempt}/3 次失敗：{e}")
            if attempt < 3:
                time.sleep(5)

    captures = []
    for timestamp, status, mimetype in cdx_rows or []:
        try:
            snapshot_time = datetime.strptime(
                str(timestamp), "%Y%m%d%H%M%S"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Wayback CDX 略過存檔：時間格式錯誤 {timestamp}")
            continue
        captures.append((snapshot_time, str(timestamp), str(status), mimetype))

    skipped = []
    for snapshot_time, timestamp, status, mimetype in sorted(
        captures, key=lambda capture: capture[0], reverse=True
    ):
        age_hours = (now - snapshot_time).total_seconds() / 3600
        reasons = []
        if status != "200":
            reasons.append(f"HTTP {status}")
        if mimetype != "text/html":
            reasons.append(f"mimetype {mimetype}")
        if age_hours > max_age_hours:
            reasons.append(f"超過上限 {max_age_hours} 小時")
        if snapshot_time > now:
            reasons.append("存檔時間晚於現在")
        if reasons:
            reason = "、".join(reasons)
            skipped.append((snapshot_time, reason))
            print(f"Wayback CDX 略過存檔 {timestamp}：{reason}")
            continue

        newer_skipped = sum(capture_time > snapshot_time for capture_time, _ in skipped)
        print(
            f"Wayback CDX 選用存檔 {timestamp}（{age_hours:.1f} 小時前）；"
            f"略過 {newer_skipped} 筆較新存檔（原因見上方紀錄）"
        )
        return (
            f"https://web.archive.org/web/{timestamp}if_/{page_url}",
            snapshot_time,
        )

    print("Wayback CDX 查詢失敗或沒有期限內可用的 HTML 存檔，改用 available API")
    params = {
        "url": site,
        "timestamp": now.strftime("%Y%m%d%H%M%S"),
    }

    closest = None
    last_error = None

    for attempt in range(1, 4):
        try:
            response = requests.get(
                WAYBACK_AVAILABLE_API,
                params=params,
                timeout=30,
            )
            response.raise_for_status()

            data = response.json()
            closest = (
                data.get("archived_snapshots") or {}
            ).get("closest")
            break

        except Exception as e:
            last_error = e
            print(f"Wayback 查詢第 {attempt}/3 次失敗：{e}")

            if attempt < 3:
                time.sleep(5)

    if not closest:
        raise RuntimeError(
            f"Wayback 沒有 {site} 的存檔"
            + (f"（{last_error}）" if last_error else "")
        )

    if not closest.get("available") or str(closest.get("status")) != "200":
        raise RuntimeError(
            f"Wayback 最近存檔不可用：{closest.get('status')}"
        )

    timestamp = str(closest.get("timestamp", ""))

    try:
        snapshot_time = datetime.strptime(
            timestamp, "%Y%m%d%H%M%S"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        raise RuntimeError(f"Wayback 存檔時間格式錯誤：{timestamp}")

    age_hours = (now - snapshot_time).total_seconds() / 3600

    if age_hours > max_age_hours:
        raise RuntimeError(
            f"Wayback 最近存檔已是 {age_hours:.1f} 小時前，"
            f"超過上限 {max_age_hours} 小時"
        )

    if cdx_rows is None:
        skipped_info = "CDX 查詢失敗，無法確認較新存檔略過數"
    else:
        newer_skipped = sum(capture_time > snapshot_time for capture_time, _ in skipped)
        skipped_info = f"略過 {newer_skipped} 筆 CDX 回傳的較新存檔（原因見上方紀錄）"
    print(
        f"Wayback available 選用存檔 {timestamp}（{age_hours:.1f} 小時前）；"
        f"{skipped_info}"
    )

    return (
        f"https://web.archive.org/web/{timestamp}if_/{page_url}",
        snapshot_time,
    )


def news_key(name):
    """'WSJ' → 'wsj'，"Barron's" → 'barrons'。"""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def file_sha256(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_news_state(path=NEWS_STATE_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "sources": {}}
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        return {"version": 1, "sources": {}}
    return {"version": 1, "sources": data["sources"]}


def save_news_state(state, path=NEWS_STATE_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prune_news_files(now, news_dir=NEWS_DIR, keep_days=NEWS_RETENTION_DAYS):
    """刪除超過保留天數的每日截圖；檔名不符格式的檔案不動。"""
    cutoff = (now - timedelta(days=keep_days)).strftime("%Y%m%d")
    removed = []
    folder = Path(news_dir)
    if not folder.is_dir():
        return removed
    for file in sorted(folder.glob("*.jpg")):
        match = NEWS_FILE_PATTERN.match(file.name)
        if match and match.group("date") < cutoff:
            file.unlink()
            removed.append(file.as_posix())
    return removed


def prepare_news_items(captured_items, failures, now, state, news_dir=NEWS_DIR):
    """
    決定今天哪些截圖要送出，並把要送出的圖另存成不會被覆寫的檔名。

    - 與上次送出的圖完全相同（SHA-256）→ 不送、不給 Dify，改列「今日無新聞」。
    - 截圖失敗 → 累計連續失敗天數，列「今日無新聞」。
    回傳 (send_items, no_news)；no_news = [{"name", "reason", "streak"}]。
    state 會就地更新，呼叫端在發佈前寫回。
    """
    sources = state.setdefault("sources", {})
    send_items, no_news = [], []
    day = now.strftime("%Y%m%d")

    for item in captured_items:
        entry = sources.setdefault(item["name"], {})
        digest = file_sha256(item["path"])
        if entry.get("sha256") == digest:
            print(f"{item['name']} 截圖與上次送出的相同，今日不重複發送。")
            no_news.append({"name": item["name"], "reason": "same", "streak": 0})
            entry["failStreak"] = 0
            continue
        Path(news_dir).mkdir(parents=True, exist_ok=True)
        publish_path = f"{news_dir}/{news_key(item['name'])}-{day}-{digest[:12]}.jpg"
        Path(publish_path).write_bytes(Path(item["path"]).read_bytes())
        send_items.append({**item, "publish_path": publish_path, "sha256": digest})
        entry.update(sha256=digest, sentAt=now.isoformat(), file=publish_path, failStreak=0)
        if item.get("snapshot"):
            entry["snapshot"] = item["snapshot"]

    for failure in failures:
        entry = sources.setdefault(failure["name"], {})
        entry["failStreak"] = int(entry.get("failStreak") or 0) + 1
        entry["lastFailure"] = now.isoformat()
        no_news.append({"name": failure["name"], "reason": "failed", "streak": entry["failStreak"]})

    return send_items, no_news


def no_news_lines(no_news):
    """每個沒有新圖的來源一行；連續失敗只在第 1、7 天補充原因。"""
    lines = []
    for item in no_news:
        line = f"📰 {item['name']}：今日無新聞"
        if item["reason"] == "failed" and item["streak"] in NEWS_FAILURE_NOTICE_DAYS:
            line += (
                "（網站擋下截圖"
                + (f"，已連續 {item['streak']} 天" if item["streak"] > 1 else "")
                + "；之後只顯示今日無新聞）"
            )
        lines.append(line)
    return "\n".join(lines)


def publish_screenshots(captured_items):
    """
    將截圖推送到 GitHub，提供 LINE 可公開讀取的圖片網址。

    GENERATED_BRANCH_MODE=1 時改由 scripts/generated_branch.py 發佈到
    generated 分支；否則直接推送到目前分支。
    推送失敗時（例如其他流程同時推送）先 rebase 再重試一次；
    仍失敗就不提供圖片網址，避免 LINE 顯示舊圖。
    """
    print("正在將截圖同步至 GitHub...")

    generated_mode = os.environ.get("GENERATED_BRANCH_MODE") == "1"

    def git(*args):
        return subprocess.run(["git", *args]).returncode

    if generated_mode:
        # LINE 會立刻讀取圖片：先發佈，再使用 generated 分支的 raw 網址
        pushed = subprocess.run([
            sys.executable,
            "scripts/generated_branch.py",
            "publish",
            "screenshots",
        ]).returncode == 0
    else:
        git("config", "--local", "user.name", "github-actions[bot]")
        git(
            "config", "--local", "user.email",
            "github-actions[bot]@users.noreply.github.com",
        )

        # 舊版 PNG 截圖不再使用（其中 WSJ 那張是驗證頁）
        git(
            "rm", "-q", "--ignore-unmatch",
            "docs/wsj.png", "docs/barrons.png",
        )
        if captured_items:
            git("add", "-f", *[item.get("publish_path", item["path"]) for item in captured_items])
        # 每日截圖、刪除的過期檔與去重狀態檔一起提交（docs/news 平時被 .gitignore 排除）
        git("add", "-A", "-f", "--", NEWS_DIR)

        if git("diff", "--cached", "--quiet") != 0:
            git("commit", "-q", "-m", "Update financial news screenshots")
        else:
            print("截圖沒有變化")

        pushed = git("push") == 0

        if not pushed:
            print("git push 失敗，嘗試 rebase 後重試...")
            git("pull", "--rebase")
            pushed = git("push") == 0

    repo = os.environ.get("GITHUB_REPOSITORY")
    branch = (
        "generated"
        if generated_mode
        else os.environ.get("GITHUB_REF_NAME", "main")
    )

    if not pushed or not repo:
        print("截圖未能同步，LINE 不附圖片。")
        return

    # 每張圖都是「日期＋內容指紋」的專屬檔名，永不覆寫：
    # 舊的 LINE 訊息重新讀取網址時，仍拿到當天那張圖。
    for item in captured_items:
        item["url"] = (
            f"https://raw.githubusercontent.com/"
            f"{repo}/{branch}/{item.get('publish_path', item['path'])}"
        )


def capture_and_upload_screenshots():
    """
    截取 WSJ 與 Barron's 首頁（向下捲動後），存成 JPEG。

    兩者都截 Wayback 最近存檔（避開防機器人驗證）；沒有期限內存檔時才直接連線。
    任何一個被擋或失敗都不會被送出，而是記錄在 failures。
    發佈與去重複由 capture_prepare_and_publish() 處理。

    回傳 (captured_items, failures)：
    captured_items = [{"name": "WSJ", "path": "docs/wsj.jpg", "url": "https://..."}]
    failures = [{"name": "WSJ", "reason": "..."}]
    """
    print("正在啟動 Firefox，準備截取財經新聞頁面...")

    os.makedirs("docs", exist_ok=True)

    targets = [
        {
            "name": "WSJ",
            "url": "https://www.wsj.com/",
            "path": "docs/wsj.jpg",
            "scroll_y": WSJ_SCROLL_Y,
            "wayback_site": "wsj.com",
        },
        {
            "name": "Barron's",
            "url": "https://www.barrons.com",
            "path": "docs/barrons.jpg",
            "scroll_y": BARRONS_SCROLL_Y,
            # 2026-11-05 起直接連線一律回 401 防機器人驗證頁，比照 WSJ 改截 Wayback
            "wayback_site": "barrons.com",
        },
    ]

    captured_items = []
    failures = []

    def record_failure(name, reason):
        print(f"{name} 截圖失敗：{reason}")
        failures.append({"name": name, "reason": str(reason)})

    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)

            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) "
                    "Gecko/20100101 Firefox/130.0"
                ),
                # 增加高度，讓 Dify 一次看到更多新聞標題
                viewport={"width": 1440, "height": 1200},
                locale="en-US",
                timezone_id="America/New_York",
                device_scale_factor=1,
            )

            page = context.new_page()

            page.add_init_script(
                """
                Object.defineProperty(
                    navigator,
                    'webdriver',
                    {get: () => undefined}
                );
                """
            )

            for target in targets:
                try:
                    page_url = target["url"]
                    snapshot_time = None

                    if target.get("wayback_site"):
                        try:
                            page_url, snapshot_time = get_wayback_snapshot(
                                target["wayback_site"],
                                target["url"],
                            )
                        except Exception as wayback_error:
                            print(
                                f"{target['name']} 取得 Wayback 存檔失敗，"
                                f"改為直接連線：{wayback_error}"
                            )

                    print(f"正在進入 {target['name']} 首頁...")

                    response = page.goto(
                        page_url,
                        timeout=90000,
                        wait_until="domcontentloaded",
                    )

                    # 等候網頁主要內容及延遲載入區塊
                    page.wait_for_timeout(6000)

                    # 被防機器人擋下時不要截圖，避免把驗證頁送給 Dify 與 LINE
                    blocked_reason = detect_blocked_page(page, response)

                    if blocked_reason:
                        raise RuntimeError(
                            f"網站回傳防機器人驗證頁：{blocked_reason}"
                        )

                    # 向下捲動，避開首頁上方廣告
                    page.evaluate(
                        "(scrollY) => window.scrollTo(0, scrollY)",
                        target["scroll_y"],
                    )

                    # 等待捲動後的圖片及新聞內容載入
                    page.wait_for_timeout(2500)

                    actual_scroll_y = page.evaluate("window.scrollY")

                    # 驗證頁很短、捲不動；真正的首頁一定捲得動
                    if actual_scroll_y < target["scroll_y"] / 2:
                        raise RuntimeError(
                            f"頁面只能捲到 Y={actual_scroll_y}"
                            f"（目標 {target['scroll_y']}），"
                            "可能不是新聞首頁"
                        )

                    print(
                        f"{target['name']} 已捲動至 Y={actual_scroll_y}，"
                        "開始截圖..."
                    )

                    page.screenshot(
                        path=target["path"],
                        type="jpeg",
                        quality=JPEG_QUALITY,
                        full_page=False,
                        animations="disabled",
                    )

                    captured_items.append({
                        "name": target["name"],
                        "path": target["path"],
                        "url": None,
                        **({"snapshot": snapshot_time.isoformat()} if snapshot_time else {}),
                    })

                    print(f"{target['name']} 截圖完成。")

                except Exception as e:
                    record_failure(target["name"], e)

            browser.close()

    except Exception as e:
        print(f"Playwright Firefox 啟動失敗：{e}")

        done = {item["name"] for item in captured_items}
        done |= {item["name"] for item in failures}

        for target in targets:
            if target["name"] not in done:
                record_failure(target["name"], f"瀏覽器啟動失敗：{e}")

    return captured_items, failures


def capture_prepare_and_publish(now=None):
    """
    截圖 → 去重複並另存不會被覆寫的檔名 → 刪除過期檔 → 與狀態檔一起發佈。

    回傳 (send_items, no_news)：send_items 才會交給 Dify 與 LINE。
    發佈失敗時 send_items 沒有 url，LINE 不附圖（不送舊圖）。
    """
    now = now or datetime.now(timezone.utc)
    captured_items, failures = capture_and_upload_screenshots()
    for failure in failures:
        print(
            f"::warning title={failure['name']} 截圖失敗::"
            f"{failure['reason']}"
        )
    state = load_news_state()
    send_items, no_news = prepare_news_items(captured_items, failures, now, state)
    removed = prune_news_files(now)
    if removed:
        print(f"刪除超過 {NEWS_RETENTION_DAYS} 天的新聞截圖：{len(removed)} 張")
    save_news_state(state)
    # 即使今天沒有新圖，也要發佈狀態檔與刪除，讓明天的去重複與連續失敗天數正確
    publish_screenshots(send_items)
    return send_items, no_news

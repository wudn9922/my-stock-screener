import os
import time
from urllib.parse import quote
import json

import requests
from playwright.sync_api import sync_playwright


DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")

DIFY_USER = "github-actions-bot"

# 可在 GitHub Actions Secrets / Variables 自訂捲動距離
WSJ_SCROLL_Y = int(os.environ.get("WSJ_SCROLL_Y", "650"))
BARRONS_SCROLL_Y = int(os.environ.get("BARRONS_SCROLL_Y", "400"))

# Yahoo Finance 常見的美國交易所代碼與名稱
US_EXCHANGES = {
    "NMS",          # Nasdaq Global Select
    "NGM",          # Nasdaq Global Market
    "NCM",          # Nasdaq Capital Market
    "NYQ",          # NYSE
    "ASE",          # NYSE American
    "PCX",          # NYSE Arca
    "BTS",          # Cboe
    "NASDAQ",
    "NYSE",
    "NYSEArca",
    "NasdaqGS",
    "NasdaqGM",
    "NasdaqCM",
    "NYSE American",
    "Cboe US",
}


def capture_and_upload_screenshots():
    """
    開啟 WSJ 與 Barron's，向下捲動後截圖。

    回傳格式：
    [
        {
            "name": "WSJ",
            "path": "docs/wsj.png",
            "url": "https://..."
        }
    ]
    """
    print("正在啟動 Firefox，準備截取財經新聞頁面...")

    os.makedirs("docs", exist_ok=True)

    targets = [
        {
            "name": "WSJ",
            "url": "https://www.wsj.com",
            "path": "docs/wsj.png",
            "scroll_y": WSJ_SCROLL_Y,
        },
        {
            "name": "Barron's",
            "url": "https://www.barrons.com",
            "path": "docs/barrons.png",
            "scroll_y": BARRONS_SCROLL_Y,
        },
    ]

    captured_items = []

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
                    print(f"正在進入 {target['name']} 首頁...")

                    page.goto(
                        target["url"],
                        timeout=60000,
                        wait_until="domcontentloaded",
                    )

                    # 等候網頁主要內容及延遲載入區塊
                    page.wait_for_timeout(4000)

                    # 向下捲動，避開首頁上方廣告
                    page.evaluate(
                        "(scrollY) => window.scrollTo(0, scrollY)",
                        target["scroll_y"],
                    )

                    # 等待捲動後的圖片及新聞內容載入
                    page.wait_for_timeout(2500)

                    actual_scroll_y = page.evaluate("window.scrollY")
                    print(
                        f"{target['name']} 已捲動至 Y={actual_scroll_y}，"
                        "開始截圖..."
                    )

                    page.screenshot(
                        path=target["path"],
                        full_page=False,
                        animations="disabled",
                    )

                    captured_items.append({
                        "name": target["name"],
                        "path": target["path"],
                        "url": None,
                    })

                    print(f"{target['name']} 截圖完成。")

                except Exception as e:
                    print(f"{target['name']} 截圖失敗：{e}")

            browser.close()

    except Exception as e:
        print(f"Playwright Firefox 啟動失敗：{e}")

    if not captured_items:
        return []

    # 將截圖推送到 GitHub，提供 LINE 可公開讀取的圖片網址
    print("正在將截圖同步至 GitHub...")

    os.system('git config --local user.name "github-actions[bot]"')
    os.system(
        'git config --local user.email '
        '"github-actions[bot]@users.noreply.github.com"'
    )
    os.system("git add docs/wsj.png docs/barrons.png")
    os.system(
        'git commit -m "Update financial news screenshots" '
        '|| echo "截圖沒有變化"'
    )
    os.system("git push")

    repo = os.environ.get("GITHUB_REPOSITORY")
    branch = os.environ.get("GITHUB_REF_NAME", "main")

    # 避免 LINE 取得 GitHub Raw 舊快取
    cache_version = int(time.time())

    if repo:
        for item in captured_items:
            relative_path = item["path"]
            item["url"] = (
                f"https://raw.githubusercontent.com/"
                f"{repo}/{branch}/{relative_path}"
                f"?v={cache_version}"
            )
    else:
        print("找不到 GITHUB_REPOSITORY，無法產生 LINE 圖片網址。")

    return captured_items


def get_yahoo_chart_metadata(symbol, session):
    """取得 Yahoo 商品類型與交易所資訊。"""
    encoded_symbol = quote(symbol, safe="")

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{encoded_symbol}"
    )

    params = {
        "interval": "1d",
        "range": "1d",
    }

    response = session.get(url, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()
    results = data.get("chart", {}).get("result") or []

    if not results:
        return None

    return results[0].get("meta", {})


def is_us_listed_stock(symbol, session):
    """
    只保留美國交易所掛牌的 EQUITY。

    排除：
    - ETF
    - 指數
    - 加密貨幣
    - 期貨
    - 共同基金
    - 非美國交易所股票
    """
    try:
        metadata = get_yahoo_chart_metadata(symbol, session)

        if not metadata:
            return False

        instrument_type = str(
            metadata.get("instrumentType", "")
        ).upper()

        exchange_name = str(
            metadata.get("exchangeName", "")
        ).strip()

        full_exchange_name = str(
            metadata.get("fullExchangeName", "")
        ).strip()

        # 只接受股票，會排除 ETF、INDEX、CRYPTOCURRENCY、FUTURE 等
        if instrument_type != "EQUITY":
            print(
                f"排除 {symbol}：商品類型為 "
                f"{instrument_type or '未知'}"
            )
            return False

        if (
            exchange_name not in US_EXCHANGES
            and full_exchange_name not in US_EXCHANGES
        ):
            print(
                f"排除 {symbol}：非指定美國交易所 "
                f"({exchange_name} / {full_exchange_name})"
            )
            return False

        print(
            f"保留 {symbol}："
            f"{instrument_type}, "
            f"{full_exchange_name or exchange_name}"
        )
        return True

    except Exception as e:
        print(f"驗證 {symbol} 失敗：{e}")
        return False


def fetch_yahoo_realtime_trending(limit=5):
    """取得 Yahoo 熱門商品，再篩選為美國交易所掛牌股票。"""
    print("正在取得 Yahoo Finance 美國熱門商品...")

    url = "https://query1.finance.yahoo.com/v1/finance/trending/US"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    try:
        with requests.Session() as session:
            session.headers.update(headers)

            response = session.get(url, timeout=15)
            response.raise_for_status()

            data = response.json()

            results = data.get("finance", {}).get("result") or []
            if not results:
                print("Yahoo 熱門商品清單為空。")
                return ""

            trending_list = results[0].get("quotes") or []

            filtered_symbols = []
            checked_symbols = set()

            for item in trending_list:
                symbol = item.get("symbol")
                quote_type = str(item.get("quoteType", "")).upper()

                if not symbol or symbol in checked_symbols:
                    continue

                checked_symbols.add(symbol)

                # 若熱門 API 已明確表示不是股票，直接排除
                if quote_type and quote_type != "EQUITY":
                    print(
                        f"排除 {symbol}：熱門 API 類型為 {quote_type}"
                    )
                    continue

                if is_us_listed_stock(symbol, session):
                    filtered_symbols.append(symbol)

                if len(filtered_symbols) >= limit:
                    break

            if not filtered_symbols:
                print("目前沒有取得符合條件的美國熱門股票。")
                return ""

            result = ", ".join(filtered_symbols)
            print(f"篩選後的美國熱門股票：{result}")
            return result

    except Exception as e:
        print(f"Yahoo 熱門商品 API 呼叫失敗：{e}")
        return ""


def upload_image_to_dify(image_path):
    """
    將本機圖片上傳至 Dify。

    回傳 Dify upload_file_id。
    """
    if not DIFY_API_KEY:
        raise ValueError("未設定 DIFY_API_KEY")

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"找不到圖片：{image_path}")

    url = "https://api.dify.ai/v1/files/upload"

    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
    }

    with open(image_path, "rb") as image_file:
        files = {
            "file": (
                os.path.basename(image_path),
                image_file,
                "image/png",
            )
        }

        form_data = {
            "user": DIFY_USER,
        }

        response = requests.post(
            url,
            headers=headers,
            files=files,
            data=form_data,
            timeout=60,
        )

    if not response.ok:
        raise RuntimeError(
            f"Dify 圖片上傳失敗："
            f"{response.status_code} {response.text}"
        )

    upload_id = response.json().get("id")

    if not upload_id:
        raise RuntimeError(
            f"Dify 沒有回傳檔案 ID：{response.text}"
        )

    print(f"已上傳至 Dify：{image_path}")
    return upload_id


def run_dify_workflow(captured_items, trending_stocks):
    """
    將新聞截圖及熱門美股資料傳入 Dify Workflow。

    使用 streaming 模式，避免 blocking 模式長時間等待而發生 504。
    圖片只上傳一次；Dify 暫時性錯誤時，最多重試一次。
    """
    print("正在上傳新聞截圖並執行 Dify Workflow...")

    if not DIFY_API_KEY:
        raise ValueError("未設定 DIFY_API_KEY")

    # 先將圖片上傳至 Dify。
    # 即使後續 Workflow 重試，也不會重複上傳圖片。
    dify_images = []
    source_names = []

    for item in captured_items:
        try:
            upload_id = upload_image_to_dify(item["path"])

            dify_images.append({
                "transfer_method": "local_file",
                "upload_file_id": upload_id,
                "type": "image",
            })

            source_names.append(item["name"])

        except Exception as e:
            print(f"{item['name']} 上傳至 Dify 失敗：{e}")

    if not dify_images:
        raise RuntimeError("沒有任何圖片成功上傳至 Dify。")

    source_description = "、".join(source_names)

    # 保留原本傳給 Dify 的任務內容
    instruction = f"""
請辨識 news_images 內的財經新聞截圖。

圖片來源依序為：{source_description}。

請完成以下工作：
1. 找出每張圖片中所有清楚可見的新聞標題。
2. 將英文新聞標題翻譯成繁體中文。
3. 依新聞來源分組。
4. 保留公司名稱、股票代碼、人物姓名、數字與專有名詞。
5. 不要翻譯選單、訂閱按鈕、廣告文字或頁尾。
6. 無法清楚辨識的內容不要猜測或自行補寫。
7. 最後整理熱門美股代碼並提供非常簡短的說明。

目前篩選後的熱門美股代碼：
{trending_stocks or "目前沒有取得符合條件的熱門美股"}
""".strip()

    url = "https://api.dify.ai/v1/workflows/run"

    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    payload = {
        "inputs": {
            # 保留原本 Dify 工作流的變數名稱
            "wsj_raw_headlines": instruction,
            "yahoo_trending_symbols": (
                trending_stocks
                or "目前沒有取得符合條件的熱門美股"
            ),
            "news_images": dify_images,
        },

        # 改用串流，避免 Cloudflare 等待完整結果而發生 504
        "response_mode": "streaming",
        "user": DIFY_USER,
    }

    # 最多執行兩次：第一次失敗後重試一次
    max_attempts = 2
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            print(
                f"正在串流執行 Dify Workflow，"
                f"第 {attempt}/{max_attempts} 次嘗試..."
            )

            with requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,

                # 連線逾時 30 秒；串流讀取逾時 300 秒
                timeout=(30, 300),
            ) as response:

                # 4xx 通常是 API Key、輸入變數或工作流設定問題，
                # 重試通常沒有意義。
                if 400 <= response.status_code < 500:
                    error_text = response.text[:1500]

                    raise ValueError(
                        f"Dify 請求設定錯誤："
                        f"{response.status_code} {error_text}"
                    )

                # 502、503、504 等伺服器錯誤可進行重試
                if response.status_code >= 500:
                    error_text = response.text[:1500]

                    raise RuntimeError(
                        f"Dify 暫時性伺服器錯誤："
                        f"{response.status_code} {error_text}"
                    )

                response.raise_for_status()

                final_outputs = {}
                text_chunks = []
                workflow_finished = False

                for raw_line in response.iter_lines(
                    decode_unicode=True
                ):
                    if not raw_line:
                        continue

                    line = raw_line.strip()

                    # SSE keep-alive 註解
                    if line.startswith(":"):
                        continue

                    # Dify SSE 正常資料格式為 data: {...}
                    if not line.startswith("data:"):
                        continue

                    event_json = line[len("data:"):].strip()

                    if not event_json:
                        continue

                    try:
                        event = json.loads(event_json)
                    except json.JSONDecodeError:
                        print(
                            "忽略無法解析的 Dify 串流資料："
                            f"{event_json[:200]}"
                        )
                        continue

                    event_name = event.get("event", "")
                    event_data = event.get("data") or {}

                    if event_name == "workflow_started":
                        print("Dify Workflow 已開始執行。")

                    elif event_name == "node_started":
                        node_title = event_data.get("title", "")

                        if node_title:
                            print(f"Dify 節點開始：{node_title}")

                    elif event_name == "node_finished":
                        node_title = event_data.get("title", "")
                        node_status = event_data.get("status", "")

                        if node_title:
                            print(
                                f"Dify 節點完成：{node_title}"
                                f"（{node_status or '完成'}）"
                            )

                    elif event_name == "text_chunk":
                        # 部分 Dify 工作流會逐段回傳文字
                        text_piece = event_data.get("text", "")

                        if isinstance(text_piece, str) and text_piece:
                            text_chunks.append(text_piece)

                    elif event_name == "workflow_finished":
                        workflow_finished = True

                        workflow_status = event_data.get("status", "")
                        workflow_error = event_data.get("error")

                        if (
                            workflow_status == "failed"
                            or workflow_error
                        ):
                            raise RuntimeError(
                                "Dify Workflow 執行失敗："
                                f"{workflow_error or workflow_status}"
                            )

                        final_outputs = (
                            event_data.get("outputs") or {}
                        )

                        print("Dify Workflow 執行完成。")
                        break

                    elif event_name in {
                        "error",
                        "workflow_error",
                    }:
                        error_message = (
                            event.get("message")
                            or event_data.get("message")
                            or event_data.get("error")
                            or "未知錯誤"
                        )

                        raise RuntimeError(
                            f"Dify 串流執行失敗：{error_message}"
                        )

                    # event_name == "ping" 等其他事件直接忽略

                if not workflow_finished:
                    raise RuntimeError(
                        "Dify 串流連線已結束，"
                        "但沒有收到 workflow_finished 事件。"
                    )

                # 優先取得 End 節點的 text
                report_text = final_outputs.get("text", "")

                if isinstance(report_text, str):
                    report_text = report_text.strip()
                else:
                    report_text = ""

                # 若 End 節點沒有 text，嘗試取得其他文字輸出
                if not report_text:
                    for output_name, output_value in (
                        final_outputs.items()
                    ):
                        if (
                            isinstance(output_value, str)
                            and output_value.strip()
                        ):
                            print(
                                "Dify 的 text 輸出為空，"
                                f"改用輸出變數：{output_name}"
                            )
                            report_text = output_value.strip()
                            break

                # 部分工作流可能只回傳 text_chunk
                if not report_text and text_chunks:
                    report_text = "".join(text_chunks).strip()

                if not report_text:
                    # 已成功執行但輸出空白，通常不是暫時性網路問題，
                    # 不重試，避免重複計費。
                    raise ValueError(
                        "Dify Workflow 已成功完成，"
                        "但沒有產生文字內容。"
                        f"實際 outputs：{final_outputs}"
                    )

                return report_text

        except ValueError:
            # 4xx 或 Dify 成功但輸出空白，不進行重試
            raise

        except Exception as e:
            last_error = e

            print(
                f"Dify 第 {attempt}/{max_attempts} 次執行失敗："
                f"{e}"
            )

            if attempt < max_attempts:
                wait_seconds = 15

                print(
                    f"{wait_seconds} 秒後重新嘗試 Dify..."
                )

                time.sleep(wait_seconds)

    raise RuntimeError(
        f"Dify Workflow 重試後仍執行失敗：{last_error}"
    )


def split_line_text(text, max_length=4900):
    """避免超過 LINE 單則文字訊息 5000 字元限制。"""
    if not text:
        return []

    chunks = []

    while len(text) > max_length:
        split_position = text.rfind("\n", 0, max_length)

        if split_position <= 0:
            split_position = max_length

        chunks.append(text[:split_position].strip())
        text = text[split_position:].strip()

    if text:
        chunks.append(text)

    return chunks


def send_line_report(message, image_urls):
    """將 Dify 文字報告及新聞截圖推送至 LINE。"""
    print("正在準備 LINE 推播...")

    if not LINE_ACCESS_TOKEN:
        raise ValueError("未設定 LINE_ACCESS_TOKEN")

    if not LINE_USER_ID:
        raise ValueError("未設定 LINE_USER_ID")

    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    messages = []

    # LINE 一次最多傳送 5 個 message objects
    for text_chunk in split_line_text(message):
        messages.append({
            "type": "text",
            "text": text_chunk,
        })

    for image_url in image_urls:
        if image_url:
            messages.append({
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url,
            })

    if not messages:
        print("沒有任何內容可以發送。")
        return

    # 若 Dify 文字很長，需分批推送，每批最多 5 則
    for index in range(0, len(messages), 5):
        message_batch = messages[index:index + 5]

        payload = {
            "to": LINE_USER_ID,
            "messages": message_batch,
        }

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(
                f"LINE 推播失敗："
                f"{response.status_code} {response.text}"
            )

    print("LINE 財經報表與截圖推播成功。")


if __name__ == "__main__":
    try:
        # 1. 向下捲動後截取 WSJ 與 Barron's
        captured_news = capture_and_upload_screenshots()

        # 2. 取得熱門商品，並只保留美國交易所掛牌股票
        stocks_live = fetch_yahoo_realtime_trending(limit=5)

        # 3. 將圖片上傳給 Dify Vision 模型辨識及翻譯
        report_text = ""

        if captured_news:
            try:
                report_text = run_dify_workflow(
                    captured_news,
                    stocks_live,
                )

            except Exception as e:
                # Dify 即使發生 504、模型輸出空白或服務異常，
                # 仍保留 LINE 圖片及熱門股推播功能。
                print(
                    "Dify 分析失敗，但仍會發送新聞截圖："
                    f"{e}"
                )

                report_text = (
                    "⚠️ 今日 AI 新聞翻譯暫時無法完成，"
                    "已附上 WSJ 與 Barron's 原始新聞截圖。"
                    "\n\n"
                    "📈 今日熱門美股\n"
                    f"{stocks_live or '目前沒有取得符合條件的熱門美股'}"
                    "\n\n"
                    "請稍後至 Dify 執行紀錄確認服務狀態。"
                )

        else:
            print(
                "沒有成功取得新聞截圖，"
                "略過 Dify 圖片分析。"
            )

            # 即使截圖失敗，若有取得熱門美股仍可發送文字
            if stocks_live:
                report_text = (
                    "⚠️ 今日新聞截圖取得失敗。"
                    "\n\n"
                    "📈 今日熱門美股\n"
                    f"{stocks_live}"
                )

        # 4. 將 Dify 報告及原始截圖發送至 LINE
        image_urls = [
            item["url"]
            for item in captured_news
            if item.get("url")
        ]

        if report_text or image_urls:
            send_line_report(
                report_text,
                image_urls,
            )
        else:
            print("沒有報告或圖片可以發送。")

    except Exception as e:
        print(f"執行失敗：{e}")


import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


import requests
from breakout_alert.atlas_links import plain_ticker_links


DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")

DIFY_USER = "github-actions-bot"

from news_screenshots import (
    WAYBACK_CDX_API as WAYBACK_CDX_API,
    WAYBACK_AVAILABLE_API as WAYBACK_AVAILABLE_API,
)




from news_screenshots import (
    is_blocked_page as is_blocked_page,
    detect_blocked_page as detect_blocked_page,
    get_wayback_snapshot as get_wayback_snapshot,
    file_sha256 as file_sha256,
    load_news_state as load_news_state,
    save_news_state as save_news_state,
    prune_news_files as prune_news_files,
    prepare_news_items as prepare_news_items,
    no_news_lines,
    publish_screenshots as publish_screenshots,
    capture_and_upload_screenshots as capture_and_upload_screenshots,
    capture_prepare_and_publish,
)

from trending_stocks import fetch_yahoo_realtime_trending

from us_rates_report import (
    fetch_us_rates_report,
    insert_rates_below_trending,
)

def upload_image_to_dify(image_path):
    """
    將本機圖片上傳至 Dify。

    回傳 Dify upload_file_id。
    """
    if not DIFY_API_KEY:
        raise ValueError("未設定 DIFY_API_KEY")

    if not os.path.exists(image_path):
        raise FileNotFoundError(
            f"找不到圖片：{image_path}"
        )

    url = "https://api.dify.ai/v1/files/upload"

    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
    }

    mime_type = (
        "image/jpeg"
        if image_path.lower().endswith((".jpg", ".jpeg"))
        else "image/png"
    )

    with open(image_path, "rb") as image_file:
        files = {
            "file": (
                os.path.basename(image_path),
                image_file,
                mime_type,
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
            upload_id = upload_image_to_dify(
                item["path"]
            )

            dify_images.append({
                "transfer_method": "local_file",
                "upload_file_id": upload_id,
                "type": "image",
            })

            source_names.append(item["name"])

        except Exception as e:
            print(
                f"{item['name']} 上傳至 Dify 失敗：{e}"
            )

    if not dify_images:
        raise RuntimeError(
            "沒有任何圖片成功上傳至 Dify。"
        )

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
   若某張圖是防機器人驗證頁（例如 Verification Required）
   而不是新聞首頁，請在該來源下只寫「該來源截圖被網站阻擋」，
   不要編造任何新聞。
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

                    event_json = line[
                        len("data:"):
                    ].strip()

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
                        print(
                            "Dify Workflow 已開始執行。"
                        )

                    elif event_name == "node_started":
                        node_title = event_data.get(
                            "title",
                            "",
                        )

                        if node_title:
                            print(
                                f"Dify 節點開始：{node_title}"
                            )

                    elif event_name == "node_finished":
                        node_title = event_data.get(
                            "title",
                            "",
                        )
                        node_status = event_data.get(
                            "status",
                            "",
                        )

                        if node_title:
                            print(
                                f"Dify 節點完成：{node_title}"
                                f"（{node_status or '完成'}）"
                            )

                    elif event_name == "text_chunk":
                        # 部分 Dify 工作流會逐段回傳文字
                        text_piece = event_data.get(
                            "text",
                            "",
                        )

                        if (
                            isinstance(text_piece, str)
                            and text_piece
                        ):
                            text_chunks.append(text_piece)

                    elif event_name == "workflow_finished":
                        workflow_finished = True

                        workflow_status = event_data.get(
                            "status",
                            "",
                        )
                        workflow_error = event_data.get(
                            "error"
                        )

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

                        print(
                            "Dify Workflow 執行完成。"
                        )
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
                            "Dify 串流執行失敗："
                            f"{error_message}"
                        )

                    # event_name == "ping" 等其他事件直接忽略

                if not workflow_finished:
                    raise RuntimeError(
                        "Dify 串流連線已結束，"
                        "但沒有收到 workflow_finished 事件。"
                    )

                # 優先取得 End 節點的 text
                report_text = final_outputs.get(
                    "text",
                    "",
                )

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

                            report_text = (
                                output_value.strip()
                            )
                            break

                # 部分工作流可能只回傳 text_chunk
                if not report_text and text_chunks:
                    report_text = "".join(
                        text_chunks
                    ).strip()

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
        split_position = text.rfind(
            "\n",
            0,
            max_length,
        )

        if split_position <= 0:
            split_position = max_length

        chunks.append(
            text[:split_position].strip()
        )

        text = text[split_position:].strip()

    if text:
        chunks.append(text)

    return chunks


def build_image_flex_message(image_urls):
    """把所有截圖放進同一個 Flex 氣泡，整組只佔一則訊息。"""
    return {
        "type": "flex",
        "altText": "新聞截圖",
        "contents": {
            "type": "bubble",
            "size": "giga",
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "0px",
                "contents": [
                    {
                        "type": "image",
                        "url": image_url,
                        "size": "full",
                        "aspectMode": "fit",
                        "aspectRatio": "6:5",
                        "action": {
                            "type": "uri",
                            "label": "開啟",
                            "uri": image_url,
                        },
                    }
                    for image_url in image_urls
                ],
            },
        },
    }


def build_line_messages(message, image_urls, as_flex=True):
    """
    組出要推播的訊息：文字報告 + 截圖，通常合計兩則。

    as_flex=False 時改成每張圖一則圖片訊息（Flex 被拒時的備援）。
    """
    messages = [
        {"type": "text", "text": text_chunk}
        for text_chunk in split_line_text(message)
    ]

    image_urls = [image_url for image_url in image_urls if image_url]

    if image_urls and as_flex:
        messages.append(build_image_flex_message(image_urls))
    else:
        for image_url in image_urls:
            messages.append({
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url,
            })

    return messages


def push_line_messages(messages):
    """推播至 LINE，失敗時回傳 response 給呼叫端判斷。"""
    # LINE 一次最多傳送 5 個 message objects；本流程通常只有 2 則
    return requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "to": LINE_USER_ID,
            "messages": messages[:5],
        },
        timeout=30,
    )


def save_trending_stocks(stocks, fetched_at, path="docs/report/trending-stocks.json"):
    """Save the same ordered symbols sent to LINE; an empty fetch retains the last good report."""
    symbols = list(dict.fromkeys(
        symbol for s in stocks.split(",")
        if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol := s.strip().upper())
    ))[:5]
    if not symbols:
        return False
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "source": "Yahoo Finance US trending · LINE 財經報告",
        "fetchedAt": fetched_at,
        "sentAt": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return True


def send_line_report(message, image_urls):
    """將 Dify 文字報告及新聞截圖推送至 LINE（合併為一到兩則訊息）。"""
    print("正在準備 LINE 推播...")

    if not LINE_ACCESS_TOKEN:
        raise ValueError(
            "未設定 LINE_ACCESS_TOKEN"
        )

    if not LINE_USER_ID:
        raise ValueError(
            "未設定 LINE_USER_ID"
        )

    messages = build_line_messages(message, image_urls)

    if not messages:
        print("沒有任何內容可以發送。")
        return

    response = push_line_messages(messages)

    has_flex = any(m["type"] == "flex" for m in messages)

    if not response.ok and has_flex:
        # Flex 被 LINE 拒絕（例如圖片過大）時，退回一般圖片訊息
        print(
            "LINE 拒絕 Flex 訊息，改用一般圖片訊息："
            f"{response.status_code} {response.text[:300]}"
        )

        response = push_line_messages(
            build_line_messages(message, image_urls, as_flex=False)
        )

    if not response.ok:
        raise RuntimeError(
            f"LINE 推播失敗："
            f"{response.status_code} {response.text}"
        )

    print("LINE 財經報表與截圖推播成功。")


if __name__ == "__main__":
    try:
        # 1. 截取 WSJ 與 Barron's；與上次相同或失敗的來源改列「今日無新聞」
        captured_news, no_news = capture_prepare_and_publish()

        # 2. 取得熱門商品，並只保留美國交易所掛牌股票
        stocks_live = fetch_yahoo_realtime_trending(
            limit=5
        )
        stocks_fetched_at = datetime.now(timezone.utc).isoformat()

        # 3. 取得聯邦基金目標區間及 US10Y
        rates_report = fetch_us_rates_report()

        # 4. 將圖片上傳給 Dify Vision 模型辨識及翻譯
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

                captured_names = "、".join(
                    item["name"] for item in captured_news
                )

                report_text = (
                    "⚠️ 今日 AI 新聞翻譯暫時無法完成，"
                    f"已附上 {captured_names} 原始新聞截圖。"
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

            # 沒有新截圖時，若有取得熱門美股仍可發送文字
            if stocks_live:
                report_text = (
                    "📈 今日熱門美股\n"
                    f"{stocks_live}"
                )

        # 沒有新圖的來源（與上次相同或截圖失敗）只列一行「今日無新聞」，
        # 不再每天重複同樣的失敗警告
        if no_news:
            report_text = (
                f"{no_news_lines(no_news)}\n\n{report_text}"
            ).strip()

        # 5. 將兩行利率資訊插入熱門股下方
        report_text = insert_rates_below_trending(
            report_text,
            rates_report,
        )

        # 6. 將 Dify 報告、利率及原始截圖發送至 LINE
        image_urls = [
            item["url"]
            for item in captured_news
            if item.get("url")
        ]

        # 熱門美股附上 Atlas 圖表連結（只加在 LINE 訊息）
        trending_symbols = [
            symbol.strip()
            for symbol in (stocks_live or "").split(",")
            if symbol.strip()
        ]

        if report_text and trending_symbols:
            report_text = (
                f"{report_text.rstrip()}\n\n"
                "📈 Atlas 圖表\n"
                f"{plain_ticker_links(trending_symbols)}"
            )

        if report_text or image_urls:
            send_line_report(
                report_text,
                image_urls,
            )
            save_trending_stocks(stocks_live, stocks_fetched_at)
        else:
            print("沒有報告或圖片可以發送。")

    except Exception as e:
        print(f"執行失敗：{e}")

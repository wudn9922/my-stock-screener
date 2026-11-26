import json
import os
import time

import requests


DIFY_API_KEY = os.environ.get("DIFY_API_KEY")

DIFY_USER = "github-actions-bot"


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

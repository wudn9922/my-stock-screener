
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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



from news_screenshots import (
    is_blocked_page as is_blocked_page,
    detect_blocked_page as detect_blocked_page,
    get_wayback_snapshot as get_wayback_snapshot,
    news_key as news_key,
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

    response = session.get(
        url,
        params=params,
        timeout=15,
    )
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
        metadata = get_yahoo_chart_metadata(
            symbol,
            session,
        )

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

            response = session.get(
                url,
                timeout=15,
            )
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
                quote_type = str(
                    item.get("quoteType", "")
                ).upper()

                if not symbol or symbol in checked_symbols:
                    continue

                checked_symbols.add(symbol)

                # 若熱門 API 已明確表示不是股票，直接排除
                if quote_type and quote_type != "EQUITY":
                    print(
                        f"排除 {symbol}："
                        f"熱門 API 類型為 {quote_type}"
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



# 讀取環境變數（包含 FRED API Key 與備援設定）
FRED_API_KEY = os.getenv("FRED_API_KEY")
FED_TARGET_LOWER_FALLBACK = os.getenv("FED_TARGET_LOWER_FALLBACK")
FED_TARGET_UPPER_FALLBACK = os.getenv("FED_TARGET_UPPER_FALLBACK")


def create_retry_session(retries=2, backoff_factor=1):
    """建立具備 User-Agent 與快速重試策略的 requests Session。"""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
    )

    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_latest_fred_api_value(series_id, fallback_value=None):
    """從 FRED 官方 REST API 取得指定數列的最新有效值。

    特性：
    - 採用 `sort_order=desc` 倒序排列，第一個抓到的有效數字即為最新數值。
    - 設定 `limit=10` 只抓最近 10 筆資料，傳輸極速。
    - 支援無 API Key 或網路異常時自動切換至備援值。
    """
    print(f"正在透過 REST API 取得 FRED 數列：{series_id}...")

    if not FRED_API_KEY:
        print("⚠️ 未偵測到 FRED_API_KEY 環境變數。")
        return _handle_fallback(series_id, fallback_value, "未提供 API Key")

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",  # 倒序排列：最新日期的資料會在陣列最前方
        "limit": 10,           # 僅索取最近 10 筆，節省頻寬
    }

    session = create_retry_session()

    try:
        # 官方 API 響應極快，連線 5 秒 / 讀取 15 秒即可落實 Fail-Fast
        response = session.get(url, params=params, timeout=(5, 15))
        response.raise_for_status()

        data = response.json()
        observations = data.get("observations", [])

        if not observations:
            raise RuntimeError(f"FRED 數列 {series_id} 未回傳任何觀測資料。")

        latest_value = None
        for obs in observations:
            raw_value = obs.get("value")
            # 排除 FRED 回應中的無效字串與缺失點 ('.')
            if raw_value in (None, "", "."):
                continue

            try:
                latest_value = float(raw_value)
                break  # 已拿到最新有效值，直接跳出迴圈
            except (TypeError, ValueError):
                continue

        if latest_value is None:
            raise RuntimeError(f"FRED 數列 {series_id} 的最近資料全為無效值。")

        return latest_value

    except Exception as e:
        print(f"⚠️ FRED REST API 數列 {series_id} 讀取失敗：{e}")
        return _handle_fallback(series_id, fallback_value, str(e))

    finally:
        session.close()


def _handle_fallback(series_id, fallback_value, error_reason):
    """處理備援值判斷與錯誤拋出的輔助函式。"""
    if fallback_value is not None:
        try:
            valid_fallback = float(fallback_value)
            print(f"🔄 改用備援值 {series_id}：{valid_fallback}")
            return valid_fallback
        except (TypeError, ValueError):
            print(f"❌ 備援值格式錯誤：{fallback_value}")

    raise RuntimeError(
        f"FRED 數列 {series_id} 無法取得（原因：{error_reason}）且無有效備援值。"
    )


def fetch_federal_funds_target_range():
    """取得聯邦基金目標區間（下限 DFEDTARL，上限 DFEDTARU）。"""
    print("正在取得聯邦基金目標區間...")

    lower_rate = fetch_latest_fred_api_value(
        "DFEDTARL", fallback_value=FED_TARGET_LOWER_FALLBACK
    )
    upper_rate = fetch_latest_fred_api_value(
        "DFEDTARU", fallback_value=FED_TARGET_UPPER_FALLBACK
    )

    print(f"聯邦基金目標區間：{lower_rate:.2f}%～{upper_rate:.2f}%")
    return lower_rate, upper_rate


def fetch_yahoo_us10y():
    """
    從 Yahoo Finance 的 ^TNX 取得美國 10 年期公債殖利率。

    Yahoo ^TNX 的 regularMarketPrice 可直接視為百分比，
    例如 4.372 代表 4.372%。
    """
    print("正在取得美國 10 年期公債殖利率...")

    encoded_symbol = quote("^TNX", safe="")

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{encoded_symbol}"
    )

    params = {
        "interval": "1d",
        "range": "5d",
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()

    data = response.json()
    results = data.get("chart", {}).get("result") or []

    if not results:
        yahoo_error = data.get("chart", {}).get("error")

        raise RuntimeError(
            f"Yahoo ^TNX 沒有回傳資料：{yahoo_error}"
        )

    chart_result = results[0]
    metadata = chart_result.get("meta") or {}

    current_yield = metadata.get("regularMarketPrice")

    # 如果 metadata 沒有最新數值，改從歷史收盤資料取得
    if current_yield is None:
        indicators = chart_result.get("indicators") or {}
        quote_results = indicators.get("quote") or []

        closes = []

        if quote_results:
            raw_closes = (
                quote_results[0].get("close") or []
            )

            for close_value in raw_closes:
                if close_value is None:
                    continue

                try:
                    closes.append(float(close_value))
                except (TypeError, ValueError):
                    continue

        if not closes:
            raise RuntimeError(
                "Yahoo ^TNX 沒有可用的殖利率資料。"
            )

        current_yield = closes[-1]

    current_yield = float(current_yield)

    print(
        "美國 10 年期公債殖利率："
        f"{current_yield:.3f}%"
    )

    return current_yield


def fetch_us10y_with_fallback():
    """
    優先從 Yahoo ^TNX 取得 US10Y。

    Yahoo 無法取得時，使用 FRED DGS10 作為備援。
    """
    try:
        return fetch_yahoo_us10y()

    except Exception as yahoo_error:
        print(
            "Yahoo ^TNX 取得失敗，"
            f"改用 FRED DGS10：{yahoo_error}"
        )

        us10y = fetch_latest_fred_api_value(
            "DGS10"
        )

        print(
            "FRED 美國 10 年期公債殖利率："
            f"{us10y:.3f}%"
        )

        return us10y


def fetch_us_rates_report():
    """
    產生只包含兩行的利率資訊。

    即使其中一項取得失敗，仍會保留固定的兩行格式。
    """
    print("正在整理美國利率資訊...")

    lower_rate = None
    upper_rate = None
    us10y = None

    try:
        lower_rate, upper_rate = (
            fetch_federal_funds_target_range()
        )
    except Exception as e:
        print(f"聯邦基金目標區間取得失敗：{e}")

    try:
        us10y = fetch_us10y_with_fallback()
    except Exception as e:
        print(f"美國 10 年期殖利率取得失敗：{e}")

    if lower_rate is not None and upper_rate is not None:
        federal_funds_line = (
            "聯邦基金目標區間："
            f"{lower_rate:.2f}%～{upper_rate:.2f}%"
        )
    else:
        federal_funds_line = (
            "聯邦基金目標區間：暫時無法取得"
        )

    if us10y is not None:
        us10y_line = (
            "美國 10 年期殖利率："
            f"{us10y:.3f}%"
        )
    else:
        us10y_line = (
            "美國 10 年期殖利率：暫時無法取得"
        )

    return (
        f"{federal_funds_line}\n"
        f"{us10y_line}"
    )


def insert_rates_below_trending(report_text, rates_report):
    """
    將利率資訊放在熱門股區塊下方。

    若 Dify 報告最後包含分隔線及風險提醒，
    則將利率資訊插入風險提醒之前。

    若找不到風險提醒，則直接放在報告最後。
    """
    if not rates_report:
        return report_text or ""

    if not report_text:
        return rates_report

    report_text = report_text.strip()
    rates_report = rates_report.strip()

    # 尋找最後一條分隔線。
    # 正常 Dify 格式會在熱門股與風險提醒之間放置此分隔線。
    separator = "━━━━━━━━━━━━"
    separator_index = report_text.rfind(separator)

    if separator_index >= 0:
        text_after_separator = report_text[
            separator_index + len(separator):
        ]

        # 只有當最後一條分隔線後方是風險提醒時，
        # 才把利率資訊插入分隔線之前。
        if (
            "風險" in text_after_separator
            or "投資建議" in text_after_separator
            or "⚠️" in text_after_separator
        ):
            before_risk = report_text[
                :separator_index
            ].rstrip()

            risk_section = report_text[
                separator_index:
            ].lstrip()

            return (
                f"{before_risk}\n\n"
                f"{rates_report}\n\n"
                f"{risk_section}"
            )

    # 找不到固定格式時，直接附加在報告最後。
    return (
        f"{report_text}\n\n"
        f"{rates_report}"
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

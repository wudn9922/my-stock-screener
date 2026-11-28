import os
from urllib.parse import quote

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import requests


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

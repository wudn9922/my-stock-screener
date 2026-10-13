"""
台股中文名稱對照。

證交所即時報價（MIS）只有在取得成交價時才附中文簡稱，
改用 Yahoo 備援或沒有即時報價的股票就只剩代號。
這裡從證交所與櫃買中心的公開資料建立「代號 → 中文簡稱」對照，
讓通知在任何報價來源下都帶得出名稱。

資料來源與欄位與 main.py 的 get_tw_tickers 相同（已在正式環境使用）：
  上市：exchangeReport/STOCK_DAY_ALL?response=open_data（CSV，證券代號／證券名稱）
  上櫃：openapi/v1/tpex_mainboard_daily_close_quotes（JSON，CompanyName 等）

任何一個來源失敗都只略過該來源，不影響監控；
成功載入的結果會在行程內快取，Cloud Run 同一個實例重複呼叫不會重抓。
"""

import csv
import io
import threading
import time
from dataclasses import replace


TWSE_NAMES_URL = (
    "https://www.twse.com.tw/"
    "exchangeReport/STOCK_DAY_ALL"
    "?response=open_data"
)

TPEX_NAMES_URL = (
    "https://www.tpex.org.tw/"
    "openapi/v1/"
    "tpex_mainboard_daily_close_quotes"
)

TPEX_NAME_KEYS = (
    "CompanyName",
    "SecuritiesCompanyName",
    "SecuritiesName",
    "公司名稱",
    "證券名稱",
)

# 名稱很少變動；成功後 12 小時內不重抓，
# 失敗（含部分來源失敗）10 分鐘後才再試，避免每次排程都重打。
CACHE_TTL_SECONDS = 12 * 60 * 60
RETRY_AFTER_FAILURE_SECONDS = 10 * 60
REQUEST_TIMEOUT_SECONDS = 15

_lock = threading.Lock()
_names = {}
_next_refresh_at = 0.0


def _clean(value):
    return str(value or "").strip()


def parse_twse_csv(text):
    """解析上市 STOCK_DAY_ALL CSV，回傳 {代號: 名稱}。"""
    names = {}

    reader = csv.reader(io.StringIO(text))

    header = next(reader, None)

    if not header:
        return names

    header = [_clean(column).lstrip("﻿") for column in header]

    code_index = (
        header.index("證券代號")
        if "證券代號" in header
        else 0
    )

    name_index = (
        header.index("證券名稱")
        if "證券名稱" in header
        else None
    )

    if name_index is None:
        return names

    for row in reader:
        if len(row) <= max(code_index, name_index):
            continue

        code = _clean(row[code_index])
        name = _clean(row[name_index])

        if code and name:
            names[code] = name

    return names


def parse_tpex_items(items):
    """解析上櫃 JSON 清單，回傳 {代號: 名稱}。"""
    names = {}

    if not isinstance(items, list):
        return names

    for item in items:
        if not isinstance(item, dict):
            continue

        code = _clean(item.get("SecuritiesCompanyCode"))

        if not code:
            continue

        for key in TPEX_NAME_KEYS:
            name = _clean(item.get(key))

            if name:
                names[code] = name
                break

    return names


def _fetch_source(http, url, parser, label):
    try:
        response = http.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS
        )

        if response.status_code != 200:
            print(
                f"⚠️ {label}名稱資料 HTTP 狀態："
                f"{response.status_code}"
            )
            return None

        return parser(response)

    except Exception as exc:
        print(
            f"⚠️ {label}名稱資料取得失敗："
            f"{type(exc).__name__}: {exc}"
        )
        return None


def refresh_names(http):
    """重新載入名稱對照；回傳是否兩個來源都成功。"""
    global _next_refresh_at

    twse = _fetch_source(
        http,
        TWSE_NAMES_URL,
        lambda response: parse_twse_csv(
            response.content.decode(
                "utf-8-sig",
                errors="replace"
            )
        ),
        "上市"
    )

    tpex = _fetch_source(
        http,
        TPEX_NAMES_URL,
        lambda response: parse_tpex_items(
            response.json()
        ),
        "上櫃"
    )

    with _lock:
        # 成功的來源覆蓋；失敗的來源沿用舊資料。
        if twse:
            for code, name in twse.items():
                _names[f"{code}.TW"] = name
                _names.setdefault(code, name)

        if tpex:
            for code, name in tpex.items():
                _names[f"{code}.TWO"] = name
                _names.setdefault(code, name)

        ok = bool(twse) and bool(tpex)

        _next_refresh_at = time.time() + (
            CACHE_TTL_SECONDS
            if ok
            else RETRY_AFTER_FAILURE_SECONDS
        )

    if twse or tpex:
        print(
            f"🈶 已載入台股中文名稱："
            f"上市 {len(twse or {})} 檔、"
            f"上櫃 {len(tpex or {})} 檔"
        )

    return ok


def get_tw_name(ticker):
    ticker = _clean(ticker).upper()

    if not ticker:
        return ""

    with _lock:
        return (
            _names.get(ticker)
            or _names.get(ticker.split(".")[0])
            or ""
        )


def fill_missing_names(http, quotes):
    """
    替沒有中文名稱的台股報價補上名稱。

    只有真的缺名稱、且快取過期時才會連線；
    回傳新的 {ticker: PriceQuote}，不修改傳入的物件。
    """
    missing = [
        ticker
        for ticker, quote in quotes.items()
        if quote.market == "TW"
        and not quote.display_name
        and not ticker.startswith("^")
    ]

    if not missing:
        return quotes

    if time.time() >= _next_refresh_at:
        try:
            refresh_names(http)
        except Exception as exc:
            print(
                "⚠️ 台股中文名稱載入失敗，已略過："
                f"{type(exc).__name__}: {exc}"
            )

    filled = dict(quotes)

    for ticker in missing:
        name = get_tw_name(ticker)

        if name:
            filled[ticker] = replace(
                quotes[ticker],
                display_name=name
            )

    return filled

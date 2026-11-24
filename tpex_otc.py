import json
import math
import os
import re
import time
from datetime import datetime

import pandas as pd
import requests
import kline_integrity as kline


HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

DATA_DIR = "data"


def _bind_ohlcv_helpers(clean, write):
    """Use the shared OHLCV helpers supplied by the report entry point."""
    global clean_ohlcv_dataframe, write_kline_csv
    clean_ohlcv_dataframe = clean
    write_kline_csv = write


# =========================================================================
# 櫃買指數（^TWOII）：Yahoo 沒有可用資料，改用櫃買中心官方日資料
# =========================================================================
TPEX_OTC_SYMBOL = "^TWOII"
TPEX_OTC_NAME = "櫃買指數"
TPEX_SOURCE_LABEL = "TPEx 櫃買中心"
# analyze_index_trend 需要至少 750 根日 K，約 37 個月，初次建檔抓 42 個月
TPEX_BOOTSTRAP_MONTHS = 42
# CSV 最多保留的日 K 數（約 6 年）
TPEX_MAX_ROWS = 1500
TPEX_REQUEST_TIMEOUT = 20
TPEX_REQUEST_DELAY = 1.2
# 暫時錯誤（HTTP 5xx、連線失敗、逾時）的重試間隔（秒）
TPEX_RETRY_DELAYS = (3, 8)
# 單次執行最多送出的月份請求數，避免來源異常時拖太久
TPEX_MAX_MONTH_REQUESTS = 60
# 連續失敗幾次就停止本次抓取
TPEX_MAX_CONSECUTIVE_FAILURES = 3
# 最後一根 K 線超過此天數視為過期，改回 Yahoo 舊流程
TPEX_STALE_DAYS = 10
# 新版網站（tables/fields/data 格式，日期參數為西元 YYYY/MM/01）
TPEX_INDEX_HISTORY_URL = (
    "https://www.tpex.org.tw/www/zh-tw/indexInfo/inxh"
)
# 舊版網站（aaData 格式，日期參數為民國 YYY/MM）
TPEX_INDEX_HISTORY_LEGACY_URL = (
    "https://www.tpex.org.tw/web/stock/iNdex_info/"
    "inxh/Inx_result.php"
)
# 成交量值（選用，用來補 Volume；失敗不影響指數）
TPEX_TRADING_INDEX_URL = (
    "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndex"
)
TPEX_TRADING_INDEX_LEGACY_URL = (
    "https://www.tpex.org.tw/web/stock/aftertrading/"
    "daily_trading_index/st41_result.php"
)

# =========================================================================
# 櫃買指數（^TWOII）：櫃買中心官方日資料
#
# 來源（依序嘗試，成功的方式會記住，下個月份優先使用）：
# 1. 新版網站 GET  /www/zh-tw/indexInfo/inxh?date=YYYY/MM/01&response=json
# 2. 新版網站 POST 同上（部分頁面只接受 POST）
# 3. 舊版網站 GET  /web/stock/iNdex_info/inxh/Inx_result.php?l=zh-tw&d=YYY/MM&o=json
#
# 回應格式兩種都支援：
# - {"tables":[{"title":..,"fields":["日期","開市","最高","最低","收市",..],
#               "data":[["115/10/08","268.12",..], ..]}], "stat":"ok"}
# - {"reportDate":"115/10","iTotalRecords":n,"aaData":[["115/10/08",..], ..]}
# 另外也接受 OpenAPI 風格的 [{"Date":"1151008","Close":"..."}, ..]。
# 日期可為民國（115/10/08、1151008）或西元，數字可含千分位逗號。
# =========================================================================
TPEX_HTTP_HEADERS = {
    **HTTP_HEADERS,
    "Accept": (
        "application/json, text/javascript, */*; q=0.01"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.tpex.org.tw/"
}

TPEX_EMPTY_TEXT = {
    "",
    "-",
    "--",
    "---",
    "x",
    "n/a",
    "null",
    "none"
}

# 欄位名稱關鍵字（小寫、去空白後做「包含」比對，依序優先）
TPEX_INDEX_FIELD_ALIASES = {
    "Date": (
        "日期",
        "date"
    ),
    "Open": (
        "開市",
        "開盤",
        "open"
    ),
    "High": (
        "最高",
        "high"
    ),
    "Low": (
        "最低",
        "low"
    ),
    "Close": (
        "收市",
        "收盤",
        "close",
        "櫃買指數"
    )
}

TPEX_TRADING_FIELD_ALIASES = {
    "Date": (
        "日期",
        "date"
    ),
    "Volume": (
        "成交股數",
        "股數",
        # 2025 年起欄位改為「成交張數」（1 張 = 1,000 股）
        "成交張數",
        "張數",
        "tradevolume",
        "volume"
    )
}

# 沒有欄位名稱時使用的位置
TPEX_INDEX_POSITIONS = {
    "Date": 0,
    "Open": 1,
    "High": 2,
    "Low": 3,
    "Close": 4
}

TPEX_TRADING_POSITIONS = {
    "Date": 0,
    "Volume": 1
}

# 本次執行中的 TPEx 狀態
TPEX_RUNTIME = {
    # 2026-10 實測：新版 inxh 路徑回 404 HTML，舊版 Inx_result.php 回 JSON，先試舊版
    "index_attempt": "legacy-get",
    "trading_attempt": None,
    "trading_disabled": False,
    "history": None,
    "history_loaded": False
}

def parse_tpex_number(value):
    """'1,234.56' → 1234.56；'--'、空字串等 → None。"""
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None

    text = re.sub(
        r"<[^>]*>",
        "",
        str(value)
    )

    text = (
        text.replace(",", "")
        .replace("，", "")
        .replace("　", "")
        .strip()
    )

    if text.lower() in TPEX_EMPTY_TEXT:
        return None

    try:
        number = float(text)
    except ValueError:
        return None

    return number if math.isfinite(number) else None


def parse_tpex_date(value):
    """
    民國或西元日期 → pd.Timestamp，無法解析回傳 None。
    支援 115/10/08、115/10/08＊、1151008、2026/10/08、
    2026-10-08、20261008。
    """
    text = re.sub(
        r"[^0-9/\-.]",
        "",
        str(value or "")
    )

    year = month = day = None

    match = re.fullmatch(
        r"(\d{2,4})[/\-.](\d{1,2})[/\-.](\d{1,2})",
        text
    )

    if match:
        year, month, day = (
            int(part)
            for part in match.groups()
        )

    elif re.fullmatch(r"\d{7}", text):
        year = int(text[:3])
        month = int(text[3:5])
        day = int(text[5:7])

    elif re.fullmatch(r"\d{8}", text):
        year = int(text[:4])
        month = int(text[4:6])
        day = int(text[6:8])

    else:
        return None

    if year < 1911:
        year += 1911

    try:
        return pd.Timestamp(
            year=year,
            month=month,
            day=day
        )
    except (ValueError, OverflowError):
        return None


def _normalize_tpex_field(name):
    return re.sub(
        r"\s+",
        "",
        str(name or "")
    ).lower()


def _match_tpex_fields(fields, aliases):
    """回傳 {欄位: 位置}，每個位置只會被用一次。"""
    normalized = [
        _normalize_tpex_field(field)
        for field in fields
    ]

    used = set()
    result = {}

    for column, keywords in aliases.items():
        for keyword in keywords:
            keyword = keyword.lower()

            found = next(
                (
                    position
                    for position, field in enumerate(
                        normalized
                    )
                    if position not in used
                    and keyword in field
                ),
                None
            )

            if found is not None:
                result[column] = found
                used.add(found)
                break

    return result


def extract_tpex_tables(payload):
    """
    將 TPEx 各種回應整理成 [(title, fields, rows)]。
    rows 可能是 list[list] 或 list[dict]。
    """
    tables = []

    if isinstance(payload, list):
        if payload:
            tables.append(("", None, payload))
        return tables

    if not isinstance(payload, dict):
        return tables

    for table in payload.get("tables") or []:
        if not isinstance(table, dict):
            continue

        rows = table.get("data") or []

        if rows:
            tables.append(
                (
                    str(table.get("title") or ""),
                    table.get("fields"),
                    rows
                )
            )

    if payload.get("aaData"):
        tables.append(
            (
                str(payload.get("reportTitle") or ""),
                payload.get("fields")
                or payload.get("aaFields"),
                payload["aaData"]
            )
        )

    if (
        not tables
        and isinstance(payload.get("data"), list)
        and payload["data"]
    ):
        tables.append(
            (
                str(payload.get("title") or ""),
                payload.get("fields"),
                payload["data"]
            )
        )

    return tables


def _tpex_table_rows_to_records(
    fields,
    rows,
    aliases,
    positions,
    required
):
    records = []

    if rows and isinstance(rows[0], dict):
        keys = list(rows[0].keys())
        mapping = _match_tpex_fields(
            keys,
            aliases
        )

        for row in rows:
            if not isinstance(row, dict):
                continue

            records.append(
                {
                    column: row.get(
                        keys[position]
                    )
                    for column, position in (
                        mapping.items()
                    )
                }
            )

        return records, mapping

    mapping = {}

    if fields:
        mapping = _match_tpex_fields(
            fields,
            aliases
        )

    # 沒有欄位名稱、或必要欄位（日期與數值）對不上時改用固定位置
    if not all(
        column in mapping
        for column in required
    ):
        mapping = dict(positions)

    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue

        records.append(
            {
                column: (
                    row[position]
                    if position < len(row)
                    else None
                )
                for column, position in (
                    mapping.items()
                )
            }
        )

    return records, mapping


def _tpex_volume_multiplier(fields, mapping):
    position = mapping.get("Volume")

    if (
        position is None
        or not fields
        or position >= len(fields)
    ):
        return 1000.0

    field = str(fields[position])

    if (
        "仟" in field
        or "千" in field
        or "張" in field
    ):
        return 1000.0

    return 1.0


def parse_tpex_index_payload(payload):
    """
    解析櫃買指數月資料 → DataFrame(index=Date, Open/High/Low/Close/Volume)。
    多個表格時優先使用標題含「櫃買指數」且不含「報酬」的表格。
    """
    tables = extract_tpex_tables(payload)

    def table_rank(table):
        title = table[0]

        if "報酬" in title:
            return 2

        if "櫃買指數" in title or not title:
            return 0

        return 1

    for title, fields, rows in sorted(
        tables,
        key=table_rank
    ):
        records, mapping = (
            _tpex_table_rows_to_records(
                fields,
                rows,
                TPEX_INDEX_FIELD_ALIASES,
                TPEX_INDEX_POSITIONS,
                ("Date", "Close")
            )
        )

        parsed_rows = []

        for record in records:
            trade_date = parse_tpex_date(
                record.get("Date")
            )

            close_value = parse_tpex_number(
                record.get("Close")
            )

            if (
                trade_date is None
                or close_value is None
                or close_value <= 0
            ):
                continue

            open_value = parse_tpex_number(
                record.get("Open")
            )
            high_value = parse_tpex_number(
                record.get("High")
            )
            low_value = parse_tpex_number(
                record.get("Low")
            )

            open_value = (
                open_value
                if open_value and open_value > 0
                else close_value
            )
            high_value = (
                high_value
                if high_value and high_value > 0
                else max(open_value, close_value)
            )
            low_value = (
                low_value
                if low_value and low_value > 0
                else min(open_value, close_value)
            )

            parsed_rows.append(
                {
                    "Date": trade_date,
                    "Open": open_value,
                    "High": max(
                        high_value,
                        open_value,
                        close_value
                    ),
                    "Low": min(
                        low_value,
                        open_value,
                        close_value
                    ),
                    "Close": close_value,
                    "Volume": 0.0
                }
            )

        if parsed_rows:
            df = pd.DataFrame(
                parsed_rows
            ).set_index("Date")

            df.index.name = "Date"

            return df[
                ~df.index.duplicated(
                    keep="last"
                )
            ].sort_index()

    return pd.DataFrame(
        columns=[
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]
    )


def parse_tpex_trading_payload(payload):
    """解析櫃買市場每日成交量值 → Series(index=Date, 成交股數)。"""
    for title, fields, rows in extract_tpex_tables(
        payload
    ):
        records, mapping = (
            _tpex_table_rows_to_records(
                fields,
                rows,
                TPEX_TRADING_FIELD_ALIASES,
                TPEX_TRADING_POSITIONS,
                ("Date", "Volume")
            )
        )

        multiplier = _tpex_volume_multiplier(
            fields,
            mapping
        )

        values = {}

        for record in records:
            trade_date = parse_tpex_date(
                record.get("Date")
            )

            volume = parse_tpex_number(
                record.get("Volume")
            )

            if (
                trade_date is None
                or volume is None
                or volume < 0
            ):
                continue

            values[trade_date] = (
                volume * multiplier
            )

        if values:
            series = pd.Series(
                values,
                dtype="float64"
            ).sort_index()

            series.index.name = "Date"

            return series

    return pd.Series(dtype="float64")


def _tpex_send(url, params, method):
    if method == "post":
        return requests.post(
            url,
            data=params,
            headers=TPEX_HTTP_HEADERS,
            timeout=TPEX_REQUEST_TIMEOUT
        )

    return requests.get(
        url,
        params=params,
        headers=TPEX_HTTP_HEADERS,
        timeout=TPEX_REQUEST_TIMEOUT
    )


def _tpex_request_json(
    url,
    params,
    method="get",
    sleep=time.sleep
):
    # 櫃買中心偶爾回 HTTP 520／502／503 或逾時（2026-10-09 實測），屬暫時錯誤，稍候重試
    for attempt, delay in enumerate(
        TPEX_RETRY_DELAYS + (None,)
    ):
        try:
            response = _tpex_send(url, params, method)
        except (
            requests.ConnectionError,
            requests.Timeout
        ):
            if delay is None:
                raise
            sleep(delay)
            continue

        if (
            response.status_code >= 500
            and delay is not None
        ):
            sleep(delay)
            continue

        break

    response.raise_for_status()

    text = response.text.lstrip("﻿").strip()

    if not text or text[0] not in "[{":
        raise ValueError(
            "非 JSON 回應："
            f"{text[:80]!r}"
        )

    return json.loads(text)


def _tpex_month_attempts(
    year,
    month,
    new_url,
    legacy_url
):
    western = f"{year}/{month:02d}/01"
    roc = f"{year - 1911}/{month:02d}"

    return [
        (
            "new-get",
            new_url,
            {
                "date": western,
                "response": "json"
            },
            "get"
        ),
        (
            "new-post",
            new_url,
            {
                "date": western,
                "response": "json"
            },
            "post"
        ),
        (
            "legacy-get",
            legacy_url,
            {
                "l": "zh-tw",
                "d": roc,
                "o": "json"
            },
            "get"
        )
    ]


def _tpex_fetch_month(
    year,
    month,
    new_url,
    legacy_url,
    parser,
    state_key,
    label,
    sleep=time.sleep
):
    """
    依序嘗試各個端點，回傳 (結果, 使用的方式)。
    上次成功的方式會排在第一個。全部失敗時拋出最後一個錯誤。
    """
    attempts = _tpex_month_attempts(
        year,
        month,
        new_url,
        legacy_url
    )

    preferred = TPEX_RUNTIME.get(state_key)

    attempts.sort(
        key=lambda attempt: (
            0
            if attempt[0] == preferred
            else 1
        )
    )

    last_error = None

    for index, (
        attempt_name,
        url,
        params,
        method
    ) in enumerate(attempts):
        if index > 0:
            sleep(TPEX_REQUEST_DELAY)

        try:
            payload = _tpex_request_json(
                url,
                params,
                method=method,
                sleep=sleep
            )

            result = parser(payload)

        except Exception as exc:
            last_error = exc

            print(
                f"⚠️ TPEx {label} "
                f"{year}/{month:02d} "
                f"[{attempt_name}] 失敗："
                f"{type(exc).__name__}: {exc}"
            )
            continue

        if len(result) > 0:
            result_in_month = result[
                (result.index.year == year)
                & (result.index.month == month)
            ].copy()
        else:
            result_in_month = result

        if len(result_in_month) > 0:
            TPEX_RUNTIME[state_key] = (
                attempt_name
            )
            return result_in_month, attempt_name

        last_error = ValueError(
            "回應中沒有該月份資料"
        )

        print(
            f"ℹ️ TPEx {label} "
            f"{year}/{month:02d} "
            f"[{attempt_name}] 沒有資料"
        )

    raise last_error or ValueError(
        "沒有可用的 TPEx 端點"
    )


def fetch_tpex_otc_index_month(
    year,
    month,
    sleep=time.sleep
):
    df, _ = _tpex_fetch_month(
        year,
        month,
        TPEX_INDEX_HISTORY_URL,
        TPEX_INDEX_HISTORY_LEGACY_URL,
        parse_tpex_index_payload,
        "index_attempt",
        "櫃買指數",
        sleep=sleep
    )

    return df


def fetch_tpex_otc_volume_month(
    year,
    month,
    sleep=time.sleep
):
    series, _ = _tpex_fetch_month(
        year,
        month,
        TPEX_TRADING_INDEX_URL,
        TPEX_TRADING_INDEX_LEGACY_URL,
        parse_tpex_trading_payload,
        "trading_attempt",
        "成交量值",
        sleep=sleep
    )

    return series


def load_ohlcv_csv(csv_path):
    if not os.path.exists(csv_path):
        return pd.DataFrame()

    try:
        local_data = kline.read_csv(
            csv_path,
            index_col=0,
            parse_dates=True
        )
    except Exception as exc:
        print(
            f"⚠️ 讀取 {csv_path} 失敗："
            f"{type(exc).__name__}: {exc}"
        )
        return pd.DataFrame()

    return clean_ohlcv_dataframe(
        local_data
    )


def merge_ohlcv_history(
    local_df,
    new_frames,
    max_rows=TPEX_MAX_ROWS
):
    """
    合併本地 CSV 與新抓到的資料：同一天以新資料為準，
    但新資料沒有成交量（0）時保留舊的成交量。
    """
    frames = [
        frame
        for frame in new_frames
        if frame is not None
        and not frame.empty
    ]

    local_df = (
        local_df
        if local_df is not None
        else pd.DataFrame()
    )

    if not frames:
        return clean_ohlcv_dataframe(
            local_df
        ).tail(max_rows)

    new_df = clean_ohlcv_dataframe(
        pd.concat(frames)
    )

    if not local_df.empty and not new_df.empty:
        old_volume = local_df["Volume"].reindex(
            new_df.index
        )

        keep_old = (
            (new_df["Volume"] <= 0)
            & (old_volume.fillna(0) > 0)
        )

        new_df.loc[
            keep_old,
            "Volume"
        ] = old_volume[keep_old]

    combined = clean_ohlcv_dataframe(
        pd.concat(
            [
                local_df,
                new_df
            ]
        )
    )

    combined.index.name = "Date"

    return combined.tail(max_rows)


def plan_tpex_months(
    local_df,
    today=None,
    bootstrap_months=TPEX_BOOTSTRAP_MONTHS
):
    """
    決定要抓哪些月份（新到舊）：
    1. 最後一根 K 線所在月份到本月（每日更新只會是本月，月初多一個上月）。
    2. 近 bootstrap_months 個月內本地完全沒有資料的月份（初次建檔或補洞）。
    """
    today = pd.Timestamp(
        today or datetime.now()
    ).normalize()

    current = (today.year, today.month)

    def shift(year_month, offset):
        index = (
            year_month[0] * 12
            + year_month[1] - 1
            + offset
        )
        return (index // 12, index % 12 + 1)

    window = [
        shift(current, -offset)
        for offset in range(
            bootstrap_months
        )
    ]

    existing = set()
    last_month = None

    if local_df is not None and not local_df.empty:
        existing = {
            (stamp.year, stamp.month)
            for stamp in local_df.index
        }

        last_stamp = local_df.index.max()
        last_month = (
            last_stamp.year,
            last_stamp.month
        )

    months = []

    if last_month is not None:
        cursor = current

        while cursor >= last_month:
            months.append(cursor)
            cursor = shift(cursor, -1)

    for year_month in window:
        if (
            year_month not in existing
            and year_month not in months
        ):
            months.append(year_month)

    return months


def write_ohlcv_csv_atomic(df, csv_path):
    return write_kline_csv(df, csv_path)


def update_tpex_otc_index_history(
    data_dir=DATA_DIR,
    today=None,
    sleep=time.sleep
):
    """
    更新 data/^TWOII.csv（Date,Open,High,Low,Close,Volume）。
    每日只抓本月（月初多抓上月）；第一次執行回補約 42 個月。
    任何失敗都只記錄，不會拋出；回傳最新的完整歷史 DataFrame。
    """
    csv_path = os.path.join(
        data_dir,
        f"{TPEX_OTC_SYMBOL}.csv"
    )

    local_df = load_ohlcv_csv(csv_path)

    months = plan_tpex_months(
        local_df,
        today=today
    )

    if len(months) > TPEX_MAX_MONTH_REQUESTS:
        print(
            f"ℹ️ TPEx 需要 {len(months)} 個月份，"
            f"本次先抓 {TPEX_MAX_MONTH_REQUESTS} 個"
        )
        months = months[:TPEX_MAX_MONTH_REQUESTS]

    print(
        f"🏦 TPEx 櫃買指數：本地 {len(local_df)} 筆，"
        f"本次抓取 {len(months)} 個月份"
    )

    current_month = months[0] if months else None
    new_frames = []
    consecutive_failures = 0

    for request_index, (year, month) in enumerate(
        months
    ):
        if request_index > 0:
            sleep(TPEX_REQUEST_DELAY)

        try:
            month_df = fetch_tpex_otc_index_month(
                year,
                month,
                sleep=sleep
            )

        except Exception as exc:
            # 本月月初（尚未開盤）沒有資料是正常的，不計入失敗；
            # 但若月中仍失敗，代表今天的櫃買指數會沿用舊資料，要明確警告
            if (year, month) == current_month:
                if pd.Timestamp(
                    today or datetime.now()
                ).day > 5:
                    print(
                        f"⚠️ TPEx 本月 {year}/{month:02d} "
                        "取得失敗，櫃買指數將沿用上月資料："
                        f"{exc}"
                    )
                else:
                    print(
                        f"ℹ️ TPEx 本月 {year}/{month:02d} "
                        f"尚無資料：{exc}"
                    )
                continue

            consecutive_failures += 1

            if (
                consecutive_failures
                >= TPEX_MAX_CONSECUTIVE_FAILURES
            ):
                print(
                    "❌ TPEx 連續失敗 "
                    f"{consecutive_failures} 次，"
                    "停止本次抓取"
                )
                break

            continue

        consecutive_failures = 0

        if not TPEX_RUNTIME["trading_disabled"]:
            sleep(TPEX_REQUEST_DELAY)

            try:
                volume_series = (
                    fetch_tpex_otc_volume_month(
                        year,
                        month,
                        sleep=sleep
                    )
                )

                month_df["Volume"] = (
                    volume_series
                    .reindex(month_df.index)
                    .fillna(0.0)
                    .astype(float)
                )

            except Exception as exc:
                # 成交量為選用資料，失敗一次就不再嘗試
                TPEX_RUNTIME["trading_disabled"] = True

                print(
                    "⚠️ TPEx 成交量值無法取得，"
                    "本次 Volume 以 0 記錄："
                    f"{type(exc).__name__}: {exc}"
                )

        print(
            f"✅ TPEx 櫃買指數 {year}/{month:02d}："
            f"{len(month_df)} 筆，最後 "
            f"{month_df.index[-1]:%Y-%m-%d} "
            f"收 {month_df['Close'].iloc[-1]:,.2f}"
        )

        new_frames.append(month_df)

    merged = merge_ohlcv_history(
        local_df,
        new_frames
    )

    if new_frames and not merged.empty:
        try:
            write_ohlcv_csv_atomic(
                merged,
                csv_path
            )

            print(
                f"💾 {csv_path} 已更新："
                f"{len(merged)} 筆，最新 "
                f"{merged.index[-1]:%Y-%m-%d}"
            )

        except Exception as exc:
            print(
                f"❌ 寫入 {csv_path} 失敗："
                f"{type(exc).__name__}: {exc}"
            )

    elif not new_frames:
        print(
            "⚠️ TPEx 本次沒有取得新資料，"
            "沿用本地 CSV"
        )

    TPEX_RUNTIME["history"] = merged
    TPEX_RUNTIME["history_loaded"] = True

    return merged


def get_tpex_otc_full_history(data_dir=DATA_DIR):
    """回傳完整的 ^TWOII 歷史（不檢查新舊）。"""
    if not TPEX_RUNTIME["history_loaded"]:
        TPEX_RUNTIME["history"] = load_ohlcv_csv(
            os.path.join(
                data_dir,
                f"{TPEX_OTC_SYMBOL}.csv"
            )
        )
        TPEX_RUNTIME["history_loaded"] = True

    history = TPEX_RUNTIME["history"]

    if history is None:
        return pd.DataFrame()

    return history


def get_tpex_otc_history(now=None):
    """
    回傳可用於分析的 ^TWOII 歷史；
    沒有資料或最後一根 K 線超過 TPEX_STALE_DAYS 天時回傳空表，
    呼叫端會改回原本的 Yahoo 流程。
    """
    history = get_tpex_otc_full_history()

    if history.empty:
        return pd.DataFrame()

    now = pd.Timestamp(now or datetime.now())
    age_days = (now - history.index[-1]).days

    if age_days > TPEX_STALE_DAYS:
        print(
            "⚠️ TPEx 櫃買指數資料已過期"
            f"（最後 {history.index[-1]:%Y-%m-%d}），"
            "改用 Yahoo"
        )
        return pd.DataFrame()

    return history

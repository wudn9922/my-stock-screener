# =========================================================================
# main.py 完整整合版
# 第 1 部分：Imports、全域設定、共用工具、Supabase 動態群組
# =========================================================================
import io
import json
import math
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf


# =========================================================================
# 全域核心參數
# =========================================================================
TW_MIN_VOLUME = 1_000_000
US_MIN_VOLUME = 100_000

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

DATA_DIR = "data"
DOCS_DIR = "docs"
MAX_DAYS = 201

# 超過此天數沒有新 K 線的 CSV 會被自動清除
STALE_CSV_DAYS = 45
# 單次最多刪除全部 CSV 的比例，超過視為異常不刪
STALE_CSV_MAX_DELETE_RATIO = 0.5


# =========================================================================
# Atlas 報告 JSON（docs/report/，網址 /my-stock-screener/report/）
# =========================================================================
REPORT_DIR = os.path.join(DOCS_DIR, "report")
REPORT_SERIES_DIR = os.path.join(REPORT_DIR, "series")
REPORT_JSON_VERSION = 1

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

# prune_stale_csv 不會刪除的 CSV（長期歷史快取，不是掃描用的個股）
STALE_CSV_EXCLUDE = {
    f"{TPEX_OTC_SYMBOL}.csv"
}

# =========================================================================
# LIFF、報告與畫線同步
# =========================================================================

# 圖表報告專用 LIFF
REPORT_LIFF_ID = "2010330411-6JhrotT9"
REPORT_LIFF_URL = (
    "https://liff.line.me/"
    f"{REPORT_LIFF_ID}"
)

# 原本的參數控制台 LIFF，保持不變
SETTING_LIFF_ID = "2010330411-SbwvRXRN"
SETTING_LIFF_URL = (
    "https://liff.line.me/"
    f"{SETTING_LIFF_ID}"
)

# 原本另一套控制台，保持不變
BITGET_SETTING_URL = (
    "https://liff.line.me/"
    "2010535859-y3CoAWaC"
)

# =========================================================================
# 類股設定
# =========================================================================

# 類股固定使用週均線，不讀取 Supabase
SECTOR_WEEKLY_MA_LIST = [20, 60]
SECTOR_BENCHMARK = "SPY"
SECTOR_CHART_WEEKS = 160

SECTOR_CONFIGS = [
    {
        "ticker": "XLK",
        "name": "科技類股",
        "sort_order": 1
    },
    {
        "ticker": "XLC",
        "name": "通訊服務",
        "sort_order": 2
    },
    {
        "ticker": "XLY",
        "name": "非必需消費",
        "sort_order": 3
    },
    {
        "ticker": "XLP",
        "name": "必需消費",
        "sort_order": 4
    },
    {
        "ticker": "XLE",
        "name": "能源類股",
        "sort_order": 5
    },
    {
        "ticker": "XLF",
        "name": "金融類股",
        "sort_order": 6
    },
    {
        "ticker": "XLV",
        "name": "醫療保健",
        "sort_order": 7
    },
    {
        "ticker": "XLI",
        "name": "工業類股",
        "sort_order": 8
    },
    {
        "ticker": "XLB",
        "name": "原物料",
        "sort_order": 9
    },
    {
        "ticker": "XLRE",
        "name": "房地產",
        "sort_order": 10
    },
    {
        "ticker": "XLU",
        "name": "公用事業",
        "sort_order": 11
    }
]

# =========================================================================
# 台股中文名稱快取
# =========================================================================

TW_STOCK_NAMES = {
    "2330.TW": "台積電",
    "2330": "台積電",
    "2317.TW": "鴻海",
    "2317": "鴻海",
    "2454.TW": "聯發科",
    "2454": "聯發科",
    "2603.TW": "長榮",
    "2603": "長榮",
    "0050.TW": "元大台灣50",
    "0050": "元大台灣50"
}

# =========================================================================
# 自訂群組篩選設定
# =========================================================================

# True：Supabase 自訂群組只要可下載就顯示
# False：套用均線距離條件
CUSTOM_GROUP_TEST_MODE = (
    os.environ.get(
        "CUSTOM_GROUP_TEST_MODE",
        "false"
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on"
    }
)

# 股價位於均線下方 3% 至上方 1%
CUSTOM_MA_MIN_RATIO = 0.97
CUSTOM_MA_MAX_RATIO = 1.01


# =========================================================================
# 共用工具
# =========================================================================
def safe_int(value, default=None):
    try:
        if value is None or value == "":
            return default

        return int(value)

    except (TypeError, ValueError):
        return default


def safe_float(value, default=0.0):
    try:
        if value is None or pd.isna(value):
            return default

        result = float(value)

        if result in (
            float("inf"),
            float("-inf")
        ):
            return default

        return result

    except (TypeError, ValueError):
        return default


def clean_json_value(value):
    if isinstance(value, dict):
        return {
            key: clean_json_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            clean_json_value(item)
            for item in value
        ]

    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, float):
        if pd.isna(value):
            return None

        if value in (
            float("inf"),
            float("-inf")
        ):
            return None

    return value


def register_tw_stock_name(ticker, name):
    ticker = str(
        ticker or ""
    ).strip().upper()

    name = str(
        name or ""
    ).strip()

    if not ticker or not name:
        return

    TW_STOCK_NAMES[ticker] = name

    stock_code = ticker.split(".")[0]

    if stock_code:
        TW_STOCK_NAMES[stock_code] = name


def get_tw_stock_name(ticker):
    ticker = str(
        ticker or ""
    ).strip().upper()

    if not ticker:
        return ""

    stock_code = ticker.split(".")[0]

    return (
        TW_STOCK_NAMES.get(ticker)
        or TW_STOCK_NAMES.get(stock_code)
        or ""
    )


def extract_yfinance_data(
    downloaded_data,
    ticker=None
):
    if (
        downloaded_data is None
        or downloaded_data.empty
    ):
        return pd.DataFrame()

    df = downloaded_data.copy()

    if not isinstance(
        df.columns,
        pd.MultiIndex
    ):
        return df

    level_0 = [
        str(value)
        for value in df.columns.get_level_values(0)
    ]

    level_1 = [
        str(value)
        for value in df.columns.get_level_values(1)
    ]

    if ticker:
        ticker = str(ticker)

        if ticker in level_1:
            return df.xs(
                ticker,
                level=1,
                axis=1
            ).copy()

        if ticker in level_0:
            return df.xs(
                ticker,
                level=0,
                axis=1
            ).copy()

    price_fields = {
        "Open",
        "High",
        "Low",
        "Close",
        "Adj Close",
        "Volume"
    }

    if price_fields.intersection(
        set(level_0)
    ):
        df.columns = (
            df.columns.get_level_values(0)
        )
        return df

    if price_fields.intersection(
        set(level_1)
    ):
        df.columns = (
            df.columns.get_level_values(1)
        )
        return df

    return pd.DataFrame()


def clean_ohlcv_dataframe(df):
    if df is None or df.empty:
        return pd.DataFrame()

    required_cols = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume"
    ]

    if not all(
        column in df.columns
        for column in required_cols
    ):
        return pd.DataFrame()

    result = df[required_cols].copy()

    for column in required_cols:
        result[column] = pd.to_numeric(
            result[column],
            errors="coerce"
        )

    result = result.dropna(
        subset=[
            "Open",
            "High",
            "Low",
            "Close"
        ]
    )

    result["Volume"] = (
        result["Volume"]
        .fillna(0)
        .clip(lower=0)
    )

    if result.empty:
        return result

    result.index = pd.to_datetime(
        result.index,
        errors="coerce"
    )

    result = result[
        ~result.index.isna()
    ]

    if (
        getattr(
            result.index,
            "tz",
            None
        )
        is not None
    ):
        result.index = (
            result.index.tz_localize(None)
        )

    result = result[
        ~result.index.duplicated(
            keep="last"
        )
    ].sort_index()

    return result


def calculate_return(
    close_series,
    periods
):
    if close_series is None:
        return 0.0

    if len(close_series) <= periods:
        return 0.0

    current_value = safe_float(
        close_series.iloc[-1],
        None
    )

    previous_value = safe_float(
        close_series.iloc[-1 - periods],
        None
    )

    if (
        current_value is None
        or previous_value is None
        or previous_value == 0
    ):
        return 0.0

    return (
        (current_value / previous_value) - 1
    ) * 100


# =========================================================================
# 動態自訂群組工具
# =========================================================================
def normalize_group_name(value):
    name = str(
        value or ""
    ).strip()

    # 合併連續空白
    name = re.sub(
        r"\s+",
        " ",
        name
    )

    # 同時接受有空格與無空格的國旗市場前綴
    name = name.replace(
        "🇹🇼 台股",
        "🇹🇼台股"
    )

    name = name.replace(
        "🇺🇸 美股",
        "🇺🇸美股"
    )

    return name


def detect_group_market(group_name):
    normalized_name = normalize_group_name(
        group_name
    )

    if normalized_name.startswith(
        "🇹🇼台股"
    ):
        return "tw"

    if normalized_name.startswith(
        "🇺🇸美股"
    ):
        return "us"

    # 固定群組沒有國旗時仍可辨識
    if normalized_name.startswith("台股"):
        return "tw"

    if normalized_name.startswith("美股"):
        return "us"

    return None


def get_custom_group_number(group_key):
    group_key = str(
        group_key or ""
    ).strip()

    if not group_key.startswith("custom_"):
        return 999_999

    return safe_int(
        group_key.replace(
            "custom_",
            "",
            1
        ),
        999_999
    )


def normalize_custom_ticker(ticker):
    ticker = str(
        ticker or ""
    ).strip().upper()

    if not ticker:
        return ""

    if (
        ticker.endswith(".TW")
        or ticker.endswith(".TWO")
    ):
        return ticker

    if ticker.isdigit():
        return ticker

    # Yahoo Finance 使用 BRK-B，不是 BRK.B
    return ticker.replace(".", "-")


def get_group_ma_list(group_item):
    ma_list = []

    for ma_key in [
        "ma1",
        "ma2",
        "ma3",
        "ma4"
    ]:
        ma_value = safe_int(
            group_item.get(ma_key)
        )

        if (
            ma_value is not None
            and ma_value > 0
        ):
            ma_list.append(ma_value)

    return (
        sorted(set(ma_list))
        or [20]
    )


def get_stock_ma_list(stock_item):
    ma_list = []

    for ma_key in [
        "ma1",
        "ma2",
        "ma3",
        "ma4"
    ]:
        ma_value = safe_int(
            stock_item.get(ma_key)
        )

        if (
            ma_value is not None
            and ma_value > 0
        ):
            ma_list.append(ma_value)

    return sorted(set(ma_list))


# =========================================================================
# Supabase 設定
# =========================================================================
def get_supabase_settings():
    base_url = os.environ.get(
        "SUPABASE_URL",
        ""
    ).strip().rstrip("/")

    service_key = os.environ.get(
        "SUPABASE_SERVICE_ROLE_KEY",
        ""
    ).strip()

    api_key = (
        service_key
        or os.environ.get(
            "SUPABASE_ANON_KEY"
        )
        or ""
    ).strip()

    if api_key and not service_key:
        print(
            "⚠️ 未設定 SUPABASE_SERVICE_ROLE_KEY，"
            "改用 anon key 讀取；若資料表已鎖定匿名讀取，"
            "將讀不到自選股與大盤設定"
        )

    if not base_url:
        print("❌ 未設定 SUPABASE_URL")
        return None, None

    if not api_key:
        print("❌ 未設定 Supabase API Key")
        return None, None

    if base_url.endswith("/rest/v1"):
        rest_url = base_url
    else:
        rest_url = f"{base_url}/rest/v1"

    return rest_url, api_key


def get_default_index_configs():
    return [
        {
            "ticker": "^TWII",
            "name": "台灣加權指數",
            "ma1": 23,
            "ma2": 29,
            "ma3": 61,
            "ma4": None,
            "enabled": True,
            "sort_order": 1
        },
        {
            "ticker": "^TWOII",
            "name": "台灣櫃買指數(OTC)",
            "ma1": 20,
            "ma2": 60,
            "ma3": 120,
            "ma4": None,
            "enabled": True,
            "sort_order": 2
        },
        {
            "ticker": "^GSPC",
            "name": "美國標普500",
            "ma1": 23,
            "ma2": 60,
            "ma3": None,
            "ma4": None,
            "enabled": True,
            "sort_order": 3
        },
        {
            "ticker": "^DJI",
            "name": "美國道瓊工業",
            "ma1": 20,
            "ma2": 23,
            "ma3": 55,
            "ma4": None,
            "enabled": True,
            "sort_order": 4
        },
        {
            "ticker": "^IXIC",
            "name": "美國那斯達克",
            "ma1": 29,
            "ma2": None,
            "ma3": None,
            "ma4": None,
            "enabled": True,
            "sort_order": 5
        },
        {
            "ticker": "^RUT",
            "name": "美國羅素2000",
            "ma1": 21,
            "ma2": 56,
            "ma3": None,
            "ma4": None,
            "enabled": True,
            "sort_order": 6
        },
        {
            "ticker": "^SOX",
            "name": "美國費城半導體",
            "ma1": 20,
            "ma2": 58,
            "ma3": 108,
            "ma4": None,
            "enabled": True,
            "sort_order": 7
        }
    ]


def get_default_group_metadata():
    return {
        "tw_g1": {
            "key": "tw_g1",
            "name": "台股-權值精選",
            "market": "tw",
            "is_custom": False,
            "sort_order": 1,
            "ma_list": [20]
        },
        "tw_g2": {
            "key": "tw_g2",
            "name": "台股-熱門",
            "market": "tw",
            "is_custom": False,
            "sort_order": 2,
            "ma_list": [20]
        },
        "us_g1": {
            "key": "us_g1",
            "name": "美股-權值精選",
            "market": "us",
            "is_custom": False,
            "sort_order": 1,
            "ma_list": [20]
        },
        "us_g2": {
            "key": "us_g2",
            "name": "美股-低本益比",
            "market": "us",
            "is_custom": False,
            "sort_order": 2,
            "ma_list": [20]
        },
        "us_g3": {
            "key": "us_g3",
            "name": "美股-超級績效",
            "market": "us",
            "is_custom": False,
            "sort_order": 3,
            "ma_list": [20]
        },
        "us_g4": {
            "key": "us_g4",
            "name": "美股-熱門",
            "market": "us",
            "is_custom": False,
            "sort_order": 4,
            "ma_list": [20]
        }
    }


def load_configs_from_supabase(
    target_user_id
):
    configs = {
        "tw_g1": {},
        "tw_g2": {},
        "us_g1": {},
        "us_g2": {},
        "us_g3": {},
        "us_g4": {}
    }

    group_metadata = (
        get_default_group_metadata()
    )

    index_configs = []
    default_index_configs = (
        get_default_index_configs()
    )

    target_user_id = str(
        target_user_id or ""
    ).strip()

    supabase_url, supabase_key = (
        get_supabase_settings()
    )

    if not supabase_url or not supabase_key:
        return (
            configs,
            default_index_configs,
            group_metadata
        )

    headers = {
        "apikey": supabase_key,
        "Authorization": (
            f"Bearer {supabase_key}"
        ),
        "Content-Type": "application/json"
    }

    name_mapping = {
        "台股-權值精選": "tw_g1",
        "台股-熱門": "tw_g2",
        "美股-權值精選": "us_g1",
        "美股-低本益比": "us_g2",
        "美股-超級績效": "us_g3",
        "美股-熱門": "us_g4"
    }

    group_id_to_key = {}

    try:
        groups_response = requests.get(
            f"{supabase_url}/groups",
            headers=headers,
            params={
                "select": "*"
            },
            timeout=15
        )

        stocks_response = requests.get(
            f"{supabase_url}/stocks",
            headers=headers,
            params={
                "select": "*"
            },
            timeout=15
        )

        print(
            "📡 Supabase groups 狀態："
            f"{groups_response.status_code}"
        )

        print(
            "📡 Supabase stocks 狀態："
            f"{stocks_response.status_code}"
        )

        if groups_response.status_code == 200:
            groups_data = (
                groups_response.json()
            )
        else:
            groups_data = []

            print(
                "❌ groups 查詢失敗："
                f"{groups_response.text[:500]}"
            )

        if stocks_response.status_code == 200:
            stocks_data = (
                stocks_response.json()
            )
        else:
            stocks_data = []

            print(
                "❌ stocks 查詢失敗："
                f"{stocks_response.text[:500]}"
            )

        print(
            "📋 Supabase 群組總數："
            f"{len(groups_data)}"
        )

        print(
            "📋 Supabase 股票總數："
            f"{len(stocks_data)}"
        )

        # -----------------------------------------------------------------
        # 建立 groups.id 與 stocks.group_id 的對照
        #
        # 固定群組：
        # groups.id = 1
        # stocks.group_id 可能為 tw_g1 或 1
        #
        # 自訂群組：
        # groups.id = 22
        # stocks.group_id = custom_22
        # -----------------------------------------------------------------
        for group_item in groups_data:
            raw_group_id = str(
                group_item.get("id", "")
            ).strip()

            raw_group_name = str(
                group_item.get("name", "")
            ).strip()

            group_name = normalize_group_name(
                raw_group_name
            )

            if (
                not raw_group_id
                or not group_name
            ):
                continue

            group_ma_list = (
                get_group_ma_list(
                    group_item
                )
            )

            # 固定群組
            if group_name in name_mapping:
                group_key = (
                    name_mapping[group_name]
                )

                group_id_to_key[
                    raw_group_id
                ] = group_key

                group_id_to_key[
                    group_key
                ] = group_key

                group_metadata[
                    group_key
                ]["ma_list"] = group_ma_list

                continue

            # 動態群組只載入目前使用者
            group_user_id = str(
                group_item.get(
                    "line_user_id",
                    ""
                )
                or ""
            ).strip()

            if group_user_id != target_user_id:
                continue

            market = detect_group_market(
                group_name
            )

            if market not in {
                "tw",
                "us"
            }:
                print(
                    "⚠️ 無法判斷自訂群組市場："
                    f"id={raw_group_id}, "
                    f"name={group_name}"
                )
                continue

            group_key = (
                f"custom_{raw_group_id}"
            )

            configs.setdefault(
                group_key,
                {}
            )

            group_metadata[group_key] = {
                "key": group_key,
                "id": raw_group_id,
                "name": group_name,
                "market": market,
                "is_custom": True,
                "sort_order": safe_int(
                    raw_group_id,
                    999_999
                ),
                "ma_list": group_ma_list,
                "created_at": str(
                    group_item.get(
                        "created_at",
                        ""
                    )
                )
            }

            group_id_to_key[
                raw_group_id
            ] = group_key

            group_id_to_key[
                group_key
            ] = group_key

            print(
                "✅ 載入動態群組："
                f"{group_key} / "
                f"{group_name} / "
                f"market={market} / "
                f"MA={group_ma_list}"
            )

        matched_user_count = 0
        loaded_count = 0

        for stock_item in stocks_data:
            stock_user_id = str(
                stock_item.get(
                    "line_user_id",
                    ""
                )
            ).strip()

            if stock_user_id != target_user_id:
                continue

            matched_user_count += 1

            group_id = str(
                stock_item.get(
                    "group_id",
                    ""
                )
            ).strip()

            ticker = str(
                stock_item.get(
                    "ticker",
                    ""
                )
            ).strip().upper()

            stock_name = str(
                stock_item.get("name")
                or stock_item.get("stock_name")
                or stock_item.get("company_name")
                or stock_item.get("chinese_name")
                or stock_item.get("tw_name")
                or ""
            ).strip()

            if not ticker:
                print(
                    "⚠️ 發現 ticker 為空的資料"
                )
                continue

            if group_id == "admin_index":
                print(
                    "ℹ️ 略過 admin_index："
                    f"{ticker}"
                )
                continue

            mapped_key = (
                group_id_to_key.get(
                    group_id
                )
            )

            # 固定群組直接保存 tw_g1、us_g1 等代碼
            if (
                not mapped_key
                and group_id in configs
            ):
                mapped_key = group_id

            # 動態群組防呆
            if (
                not mapped_key
                and group_id.startswith(
                    "custom_"
                )
                and group_id in group_metadata
            ):
                mapped_key = group_id

            if not mapped_key:
                print(
                    "⚠️ 股票找不到對應群組："
                    f"ticker={ticker}, "
                    f"group_id={group_id}"
                )
                continue

            metadata = group_metadata.get(
                mapped_key,
                {}
            )

            market = metadata.get(
                "market"
            )

            ticker = normalize_custom_ticker(
                ticker
            )

            if market == "us":
                ticker = ticker.replace(
                    ".",
                    "-"
                )

            if (
                market == "tw"
                and stock_name
            ):
                register_tw_stock_name(
                    ticker,
                    stock_name
                )

            # 股票均線優先；全為 0 或空值時繼承群組均線
            stock_ma_list = (
                get_stock_ma_list(
                    stock_item
                )
            )

            if stock_ma_list:
                final_ma_list = stock_ma_list
                ma_source = "stock"
            else:
                final_ma_list = (
                    metadata.get("ma_list")
                    or [20]
                )
                ma_source = "group"

            configs.setdefault(
                mapped_key,
                {}
            )

            configs[mapped_key][
                ticker
            ] = final_ma_list

            loaded_count += 1

            print(
                "✅ 載入 Supabase 股票："
                f"{mapped_key} / "
                f"{ticker} / "
                f"MA={final_ma_list} / "
                f"source={ma_source}"
            )

        print(
            "👤 符合 Supabase User ID "
            f"的資料數：{matched_user_count}"
        )

        print(
            "📊 成功載入群組股票數："
            f"{loaded_count}"
        )

        if matched_user_count == 0:
            print(
                "⚠️ Supabase 查詢不到目前使用者。"
                "請確認 SUPABASE_USER_ID 是否與 "
                "stocks.line_user_id 相同。"
            )

    except Exception as exc:
        print(
            "❌ 讀取雲端個股失敗："
            f"{type(exc).__name__}: {exc}"
        )

    try:
        index_response = requests.get(
            f"{supabase_url}/index_configs",
            headers=headers,
            params={
                "select": "*"
            },
            timeout=15
        )

        if index_response.status_code == 200:
            index_configs = (
                index_response.json()
            )

            print(
                "📡 成功從雲端同步全球大盤"
                "自訂均線參數，共 "
                f"{len(index_configs)} 筆"
            )
        else:
            print(
                "⚠️ index_configs 查詢失敗："
                f"HTTP "
                f"{index_response.status_code} "
                f"{index_response.text[:500]}"
            )

    except Exception as exc:
        print(
            "⚠️ 讀取大盤設定失敗："
            f"{type(exc).__name__}: {exc}"
        )

    if not index_configs:
        index_configs = (
            default_index_configs
        )

    print(
        "\n===== Supabase 群組讀取結果 ====="
    )

    sorted_group_keys = sorted(
        configs.keys(),
        key=lambda key: (
            1 if key.startswith(
                "custom_"
            ) else 0,
            (
                get_custom_group_number(key)
                if key.startswith("custom_")
                else 0
            ),
            key
        )
    )

    for group_key in sorted_group_keys:
        stocks = configs[group_key]

        metadata = group_metadata.get(
            group_key,
            {}
        )

        print(
            f"{group_key}: "
            f"{len(stocks)} 檔 / "
            f"{metadata.get('name', group_key)}"
        )

        for ticker, ma_list in stocks.items():
            print(
                f"  └─ {ticker}: {ma_list}"
            )

    print(
        "================================\n"
    )

    return (
        configs,
        index_configs,
        group_metadata
    )


# =========================================================================
# 第 1 部分結束
# 下一部分將從 LINE Messaging API、台美股清單開始
# =========================================================================
# =========================================================================
# 第 2 部分：LINE、股票清單、圖表、全市場與自訂群組
# =========================================================================


# =========================================================================
# LINE Messaging API
# =========================================================================
def send_line_message(
    message,
    access_token,
    user_id
):
    if not access_token or not user_id:
        print(
            "⚠️ LINE 設定不完整，略過推播"
        )
        return None

    access_token = str(
        access_token
    ).strip()

    user_id = str(
        user_id
    ).strip()

    message = str(message)

    if not user_id.startswith("U"):
        print(
            "❌ LINE_USER_ID 格式錯誤，"
            "Messaging API User ID 應以 U 開頭"
        )
        return None

    if len(message) > 5000:
        message = (
            message[:4980]
            + "\n...(訊息已截斷)"
        )

    url = (
        "https://api.line.me/"
        "v2/bot/message/push"
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": (
            f"Bearer {access_token}"
        )
    }

    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=20
        )

        print(
            "📨 LINE 推播狀態："
            f"{response.status_code}"
        )

        if response.status_code >= 400:
            request_id = (
                response.headers.get(
                    "x-line-request-id",
                    "無"
                )
            )

            print(
                "⚠️ LINE 推播失敗："
                f"{response.text[:500]}"
            )

            print(
                f"LINE Request ID：{request_id}"
            )
        else:
            print("✅ LINE 推播成功")

        return response.status_code

    except Exception as exc:
        print(
            "⚠️ LINE 推播異常："
            f"{type(exc).__name__}: {exc}"
        )

        return None


# =========================================================================
# 台美股清單
# =========================================================================
def get_tw_tickers(min_volume):
    tickers = []

    twse_url = (
        "https://www.twse.com.tw/"
        "exchangeReport/STOCK_DAY_ALL"
        "?response=open_data"
    )

    for attempt in range(3):
        try:
            response = requests.get(
                twse_url,
                headers=HTTP_HEADERS,
                timeout=20
            )

            if response.status_code != 200:
                print(
                    "⚠️ TWSE HTTP 狀態："
                    f"{response.status_code}"
                )
                time.sleep(2)
                continue

            df_twse = pd.read_csv(
                io.StringIO(response.text)
            )

            code_col = (
                "證券代號"
                if "證券代號" in df_twse.columns
                else df_twse.columns[0]
            )

            name_col = (
                "證券名稱"
                if "證券名稱" in df_twse.columns
                else None
            )

            volume_col = (
                "成交股數"
                if "成交股數" in df_twse.columns
                else None
            )

            for _, row in df_twse.iterrows():
                try:
                    code = str(
                        row[code_col]
                    ).strip()

                    if (
                        len(code) != 4
                        or not code.isdigit()
                        or code.startswith("0")
                    ):
                        continue

                    ticker = f"{code}.TW"

                    if name_col:
                        register_tw_stock_name(
                            ticker,
                            row.get(name_col, "")
                        )

                    if volume_col:
                        volume = float(
                            str(
                                row[volume_col]
                            ).replace(",", "")
                        )

                        if volume < min_volume:
                            continue

                    tickers.append(ticker)

                except Exception:
                    continue

            break

        except Exception as exc:
            print(
                "⚠️ TWSE 讀取失敗 "
                f"({attempt + 1}/3)：{exc}"
            )
            time.sleep(2)

    tpex_url = (
        "https://www.tpex.org.tw/"
        "openapi/v1/"
        "tpex_mainboard_daily_close_quotes"
    )

    for attempt in range(3):
        try:
            response = requests.get(
                tpex_url,
                headers=HTTP_HEADERS,
                timeout=20
            )

            if response.status_code != 200:
                print(
                    "⚠️ TPEx HTTP 狀態："
                    f"{response.status_code}"
                )
                time.sleep(2)
                continue

            items = response.json()

            if not isinstance(items, list):
                print(
                    "⚠️ TPEx 回傳格式不是清單"
                )
                break

            for item in items:
                code = str(
                    item.get(
                        "SecuritiesCompanyCode",
                        ""
                    )
                ).strip()

                if (
                    len(code) != 4
                    or not code.isdigit()
                ):
                    continue

                ticker = f"{code}.TWO"
                company_name = ""

                for name_key in [
                    "CompanyName",
                    "SecuritiesCompanyName",
                    "SecuritiesName",
                    "公司名稱",
                    "證券名稱"
                ]:
                    if item.get(name_key):
                        company_name = str(
                            item.get(name_key)
                        ).strip()
                        break

                register_tw_stock_name(
                    ticker,
                    company_name
                )

                volume = 0.0

                for volume_key in [
                    "TradingShares",
                    "TradingVolume",
                    "成交股數"
                ]:
                    if volume_key not in item:
                        continue

                    try:
                        volume = float(
                            str(
                                item[volume_key]
                            ).replace(",", "")
                        )
                        break
                    except (
                        TypeError,
                        ValueError
                    ):
                        continue

                if volume >= min_volume:
                    tickers.append(ticker)

            break

        except Exception as exc:
            print(
                "⚠️ TPEx 讀取失敗 "
                f"({attempt + 1}/3)：{exc}"
            )
            time.sleep(2)

    tickers = sorted(set(tickers))

    if not tickers:
        print(
            "⚠️ 無法取得台股清單，"
            "改用預設股票"
        )

        return [
            "2330.TW",
            "2317.TW",
            "2454.TW",
            "2603.TW",
            "0050.TW"
        ]

    print(
        f"📋 台股候選清單："
        f"{len(tickers)} 檔"
    )

    return tickers


def get_us_tickers():
    try:
        url = (
            "https://en.wikipedia.org/wiki/"
            "List_of_S%26P_500_companies"
        )

        response = requests.get(
            url,
            headers=HTTP_HEADERS,
            timeout=20
        )

        response.raise_for_status()

        table = pd.read_html(
            io.StringIO(response.text)
        )[0]

        tickers = [
            str(ticker)
            .strip()
            .upper()
            .replace(".", "-")
            for ticker in table[
                "Symbol"
            ].tolist()
        ]

        tickers = sorted(set(tickers))

        print(
            f"📋 美股候選清單："
            f"{len(tickers)} 檔"
        )

        return tickers

    except Exception as exc:
        print(
            f"⚠️ 美股清單取得失敗：{exc}"
        )

        return [
            "AAPL",
            "MSFT",
            "NVDA"
        ]


# =========================================================================
# Plotly 圖表資料
# =========================================================================

def build_stock_data(
    df_chart,
    ticker,
    title_suffix,
    ma_list,
    show_volume=True,
    display_name=None,
    timeframe_label="日K"
):
    """
    將 DataFrame 轉成 TradingView Lightweight Charts 使用的格式。

    此函數只改變圖表資料格式，不改變：
    - 股票篩選
    - 均線計算
    - 成交量門檻
    - Supabase 參數
    """

    if df_chart is None or df_chart.empty:
        return {
            "ticker": str(ticker),
            "display_name": str(
                display_name or ""
            ),
            "title_suffix": str(
                title_suffix or ""
            ),
            "timeframe": str(
                timeframe_label or "日K"
            ),
            "drawing_timeframe": (
                "1w"
                if timeframe_label == "週K"
                else "1d"
            ),
            "candles": [],
            "volume": [],
            "moving_averages": [],
            "ohlcv": []
        }

    display_name = str(
        display_name or ""
    ).strip()

    timeframe_label = str(
        timeframe_label or "日K"
    ).strip()

    drawing_timeframe = (
        "1w"
        if timeframe_label == "週K"
        else "1d"
    )

    chart_df = df_chart.copy()

    chart_df = chart_df[
        ~chart_df.index.duplicated(
            keep="last"
        )
    ].sort_index()

    candles = []
    volume_data = []
    ohlcv_records = []

    ma_series_map = {}

    colors = [
        "#ffb74d",
        "#42a5f5",
        "#66bb6a",
        "#ec407a",
        "#ab47bc",
        "#26c6da"
    ]

    for index, ma_window in enumerate(
        ma_list
    ):
        ma_column = f"MA{ma_window}"

        if ma_column not in chart_df.columns:
            continue

        ma_series_map[ma_column] = {
            "name": ma_column,
            "window": int(ma_window),
            "color": colors[
                index % len(colors)
            ],
            "data": []
        }

    for row_index, row in chart_df.iterrows():
        date_value = pd.Timestamp(
            row_index
        ).strftime("%Y-%m-%d")

        open_value = safe_float(
            row.get("Open"),
            None
        )

        high_value = safe_float(
            row.get("High"),
            None
        )

        low_value = safe_float(
            row.get("Low"),
            None
        )

        close_value = safe_float(
            row.get("Close"),
            None
        )

        volume_value = safe_float(
            row.get("Volume"),
            0.0
        )

        if (
            open_value is None
            or high_value is None
            or low_value is None
            or close_value is None
        ):
            continue

        candle_item = {
            "time": date_value,
            "open": float(open_value),
            "high": float(high_value),
            "low": float(low_value),
            "close": float(close_value)
        }

        candles.append(candle_item)

        # 台灣市場習慣：上漲紅、下跌綠
        if close_value > open_value:
            volume_color = (
                "rgba(239,83,80,0.62)"
            )
        elif close_value < open_value:
            volume_color = (
                "rgba(38,166,154,0.62)"
            )
        else:
            volume_color = (
                "rgba(148,163,184,0.52)"
            )

        if show_volume:
            volume_data.append(
                {
                    "time": date_value,
                    "value": max(
                        0.0,
                        float(volume_value)
                    ),
                    "color": volume_color
                }
            )

        ohlcv_records.append(
            {
                "date": date_value,
                "time": date_value,
                "open": float(open_value),
                "high": float(high_value),
                "low": float(low_value),
                "close": float(close_value),
                "volume": max(
                    0.0,
                    float(volume_value)
                ),
                "timeframe": timeframe_label
            }
        )

        for (
            ma_column,
            ma_config
        ) in ma_series_map.items():
            ma_value = row.get(ma_column)

            if pd.isna(ma_value):
                continue

            numeric_ma = safe_float(
                ma_value,
                None
            )

            if numeric_ma is None:
                continue

            ma_config["data"].append(
                {
                    "time": date_value,
                    "value": float(
                        numeric_ma
                    )
                }
            )

    moving_averages = []

    for ma_config in ma_series_map.values():
        if not ma_config["data"]:
            continue

        moving_averages.append(
            ma_config
        )

    latest_close = (
        candles[-1]["close"]
        if candles
        else None
    )

    full_display_name = str(ticker)

    if display_name:
        full_display_name = (
            f"{ticker}　{display_name}"
        )

    return {
        "ticker": str(ticker),
        "display_name": display_name,
        "full_display_name": (
            full_display_name
        ),
        "title_suffix": str(
            title_suffix or ""
        ),
        "timeframe": timeframe_label,
        "drawing_timeframe": (
            drawing_timeframe
        ),
        "show_volume": bool(show_volume),
        "latest_close": latest_close,
        "candles": candles,
        "volume": volume_data,
        "moving_averages": (
            moving_averages
        ),
        "ohlcv": ohlcv_records
    }



# =========================================================================
# 全市場掃描
# =========================================================================

def download_single_ticker_with_retry(
    ticker,
    period="15d",
    max_attempts=3
):
    ticker = str(
        ticker or ""
    ).strip().upper()

    if not ticker:
        return pd.DataFrame()

    for attempt in range(
        1,
        max_attempts + 1
    ):
        try:
            print(
                f"⬇️ 單檔下載 {ticker} "
                f"({attempt}/{max_attempts})"
            )

            downloaded = yf.download(
                ticker,
                period=period,
                interval="1d",
                progress=False,
                threads=False,
                auto_adjust=False,
                actions=False,
                group_by="column",
                timeout=30
            )

            df = extract_yfinance_data(
                downloaded,
                ticker
            )

            df = clean_ohlcv_dataframe(df)

            if not df.empty:
                print(
                    f"✅ {ticker} 單檔下載成功，"
                    f"最新日期："
                    f"{df.index[-1]:%Y-%m-%d}"
                )

                return df

            print(
                f"⚠️ {ticker} 單檔下載為空"
            )

        except Exception as exc:
            print(
                f"⚠️ {ticker} 單檔下載失敗："
                f"{type(exc).__name__}: {exc}"
            )

        time.sleep(
            min(attempt * 2, 6)
        )

    return pd.DataFrame()

def download_market_data(
    tickers,
    period,
    max_attempts=3
):
    if not tickers:
        return pd.DataFrame()

    for attempt in range(
        1,
        max_attempts + 1
    ):
        try:
            downloaded = yf.download(
                tickers,
                period=period,
                interval="1d",
                progress=False,
                threads=False,
                auto_adjust=False,
                actions=False,
                group_by="column",
                timeout=45
            )

            if (
                downloaded is not None
                and not downloaded.empty
            ):
                return downloaded

            print(
                "⚠️ 全市場批次下載為空："
                f"第 {attempt}/{max_attempts} 次"
            )

        except Exception as exc:
            print(
                "⚠️ 全市場批次下載失敗："
                f"第 {attempt}/{max_attempts} 次，"
                f"{type(exc).__name__}: {exc}"
            )

        time.sleep(
            min(attempt * 3, 9)
        )

    print(
        "❌ 全市場批次下載重試後仍失敗"
    )

    return pd.DataFrame()

def scan_market(
    tickers,
    min_volume
):
    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    matched_list = []
    chunk_size = 40
    need_init = []
    need_update = []

    # -----------------------------------------------------------------
    # 判斷哪些股票需要初始化、哪些只需要更新
    # -----------------------------------------------------------------
    for ticker in tickers:
        csv_path = os.path.join(
            DATA_DIR,
            f"{ticker}.csv"
        )

        if os.path.exists(csv_path):
            need_update.append(ticker)
        else:
            need_init.append(ticker)

    print(
        f"📂 需要初始化：{len(need_init)} 檔，"
        f"需要更新：{len(need_update)} 檔"
    )

    # -----------------------------------------------------------------
    # 初始化尚未建立 CSV 的股票
    # -----------------------------------------------------------------
    for start in range(
        0,
        len(need_init),
        chunk_size
    ):
        chunk = need_init[
            start:start + chunk_size
        ]

        downloaded = download_market_data(
            chunk,
            "250d"
        )

        batch_frames = {}

        if not downloaded.empty:
            for ticker in chunk:
                try:
                    ticker_df = (
                        extract_yfinance_data(
                            downloaded,
                            ticker
                        )
                    )

                    ticker_df = (
                        clean_ohlcv_dataframe(
                            ticker_df
                        )
                    )

                    if not ticker_df.empty:
                        batch_frames[
                            ticker
                        ] = ticker_df

                except Exception as exc:
                    print(
                        f"⚠️ {ticker} "
                        "初始化批次資料解析失敗："
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    )

        for ticker in chunk:
            try:
                df = batch_frames.get(
                    ticker,
                    pd.DataFrame()
                )

                # 批次下載漏掉個別股票時，改用單檔重試
                if df.empty:
                    print(
                        f"⚠️ {ticker} "
                        "初始化批次資料為空，"
                        "改用單檔下載"
                    )

                    df = (
                        download_single_ticker_with_retry(
                            ticker,
                            period="250d",
                            max_attempts=3
                        )
                    )

                if df.empty:
                    print(
                        f"❌ {ticker} "
                        "初始化失敗，無可用資料"
                    )
                    continue

                df = (
                    clean_ohlcv_dataframe(
                        df
                    )
                )

                if df.empty:
                    continue

                df = df.tail(
                    MAX_DAYS
                )

                csv_path = os.path.join(
                    DATA_DIR,
                    f"{ticker}.csv"
                )

                df.to_csv(csv_path)

                print(
                    f"✅ {ticker} 初始化完成，"
                    "最新 K 線："
                    f"{df.index[-1]:%Y-%m-%d}"
                )

            except Exception as exc:
                print(
                    f"⚠️ {ticker} 初始化失敗："
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

    # -----------------------------------------------------------------
    # 更新已經存在 CSV 的股票
    # -----------------------------------------------------------------
    for start in range(
        0,
        len(need_update),
        chunk_size
    ):
        chunk = need_update[
            start:start + chunk_size
        ]

        # 改抓 15 日資料，增加連假及 Yahoo 漏資料時的容錯空間
        downloaded = download_market_data(
            chunk,
            "15d"
        )

        batch_frames = {}
        batch_latest_date = None

        if not downloaded.empty:
            for ticker in chunk:
                try:
                    ticker_df = (
                        extract_yfinance_data(
                            downloaded,
                            ticker
                        )
                    )

                    ticker_df = (
                        clean_ohlcv_dataframe(
                            ticker_df
                        )
                    )

                    if ticker_df.empty:
                        continue

                    batch_frames[
                        ticker
                    ] = ticker_df

                    ticker_latest_date = (
                        ticker_df.index[-1]
                        .normalize()
                    )

                    if (
                        batch_latest_date is None
                        or ticker_latest_date
                        > batch_latest_date
                    ):
                        batch_latest_date = (
                            ticker_latest_date
                        )

                except Exception as exc:
                    print(
                        f"⚠️ {ticker} "
                        "解析批次資料失敗："
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    )

        for ticker in chunk:
            try:
                today_data = batch_frames.get(
                    ticker,
                    pd.DataFrame()
                )

                need_single_retry = (
                    today_data.empty
                )

                # 如果個股日期落後同一批股票的最新日期，
                # 視為 Yahoo 批次下載可能漏掉最新資料。
                if (
                    not need_single_retry
                    and batch_latest_date is not None
                ):
                    ticker_latest_date = (
                        today_data.index[-1]
                        .normalize()
                    )

                    if (
                        ticker_latest_date
                        < batch_latest_date
                    ):
                        print(
                            f"⚠️ {ticker} "
                            "批次資料日期落後："
                            f"{ticker_latest_date:%Y-%m-%d}，"
                            "批次最新日期："
                            f"{batch_latest_date:%Y-%m-%d}，"
                            "改用單檔重試"
                        )

                        need_single_retry = True

                if need_single_retry:
                    retry_data = (
                        download_single_ticker_with_retry(
                            ticker,
                            period="15d",
                            max_attempts=3
                        )
                    )

                    if not retry_data.empty:
                        today_data = retry_data

                if today_data.empty:
                    print(
                        f"❌ {ticker} "
                        "無法取得更新資料，"
                        "本次保留原 CSV"
                    )
                    continue

                csv_path = os.path.join(
                    DATA_DIR,
                    f"{ticker}.csv"
                )

                if os.path.exists(csv_path):
                    local_data = pd.read_csv(
                        csv_path,
                        index_col=0,
                        parse_dates=True
                    )

                    local_data = (
                        clean_ohlcv_dataframe(
                            local_data
                        )
                    )
                else:
                    local_data = (
                        pd.DataFrame()
                    )

                combined = pd.concat(
                    [
                        local_data,
                        today_data
                    ]
                )

                combined = (
                    clean_ohlcv_dataframe(
                        combined
                    )
                )

                if combined.empty:
                    continue

                combined = combined.tail(
                    MAX_DAYS
                )

                combined.to_csv(
                    csv_path
                )

                print(
                    f"✅ {ticker} CSV 已更新，"
                    "最新 K 線："
                    f"{combined.index[-1]:%Y-%m-%d}"
                )

            except Exception as exc:
                print(
                    f"⚠️ {ticker} 更新失敗："
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

    # -----------------------------------------------------------------
    # 讀取完成更新的 CSV，執行成交量及 MA20 篩選
    # -----------------------------------------------------------------
    for ticker in tickers:
        csv_path = os.path.join(
            DATA_DIR,
            f"{ticker}.csv"
        )

        if not os.path.exists(csv_path):
            continue

        try:
            df = pd.read_csv(
                csv_path,
                index_col=0,
                parse_dates=True
            )

            df = clean_ohlcv_dataframe(
                df
            )

            if len(df) < 20:
                continue

            latest_volume = safe_float(
                df["Volume"].iloc[-1]
            )

            if latest_volume < min_volume:
                continue

            df["MA20"] = (
                df["Close"]
                .rolling(
                    window=20,
                    min_periods=20
                )
                .mean()
            )

            price = safe_float(
                df["Close"].iloc[-1]
            )

            ma20_value = (
                df["MA20"].iloc[-1]
            )

            if pd.isna(ma20_value):
                continue

            ma20 = float(
                ma20_value
            )

            if not (
                ma20 * 0.98
                <= price
                <= ma20 * 1.01
            ):
                continue

            diff_pct = (
                (price / ma20) - 1
            ) * 100

            title = (
                f"(現價:{price:.2f} | "
                f"距MA20:{diff_pct:.2f}%)"
            )

            is_tw_stock = (
                ticker.endswith(".TW")
                or ticker.endswith(".TWO")
            )

            display_name = (
                get_tw_stock_name(ticker)
                if is_tw_stock
                else ""
            )

            chart_data = build_stock_data(
                df.tail(180),
                ticker,
                title,
                [20],
                show_volume=True,
                display_name=display_name,
                timeframe_label="日K"
            )

            matched_list.append(
                {
                    "ticker": ticker,
                    "name": display_name,
                    "volume": int(
                        latest_volume
                    ),
                    "ma_list": [20],
                    "chart_data": chart_data
                }
            )

        except Exception as exc:
            print(
                f"⚠️ {ticker} "
                "全市場篩選失敗："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    matched_list.sort(
        key=lambda item: item["volume"],
        reverse=True
    )

    return matched_list

# =========================================================================
# Supabase 固定與動態自訂群組
# =========================================================================
def get_ticker_candidates(
    raw_ticker,
    group_key=None,
    group_market=None
):
    ticker = str(
        raw_ticker or ""
    ).strip().upper()

    if not ticker:
        return []

    if (
        ticker.endswith(".TW")
        or ticker.endswith(".TWO")
    ):
        return [ticker]

    # 自訂群組可混合輸入格式；
    # 純數字一律嘗試台股上市與上櫃。
    if ticker.isdigit():
        return [
            f"{ticker}.TW",
            f"{ticker}.TWO"
        ]

    return [
        ticker.replace(".", "-")
    ]


def download_custom_stock(
    raw_ticker,
    group_key=None,
    group_market=None
):
    candidates = get_ticker_candidates(
        raw_ticker,
        group_key=group_key,
        group_market=group_market
    )

    for candidate in candidates:
        print(
            f"⬇️ 嘗試下載自訂股票："
            f"{candidate}"
        )

        df = download_single_ticker_with_retry(
            candidate,
            period="2y",
            max_attempts=3
        )

        if df.empty:
            print(
                f"⚠️ {candidate} "
                "重試後仍無資料"
            )
            continue

        print(
            f"✅ {candidate} 下載成功，"
            f"共 {len(df)} 筆，"
            f"最新日期："
            f"{df.index[-1]:%Y-%m-%d}"
        )

        return candidate, df

    return None, pd.DataFrame()


def process_custom_groups(
    group_dict,
    group_key,
    test_mode=CUSTOM_GROUP_TEST_MODE,
    group_metadata=None
):
    matched_list = []

    group_metadata = (
        group_metadata or {}
    )

    if not group_dict:
        print(
            f"⚠️ {group_key} "
            "沒有讀到 Supabase 股票"
        )

        return matched_list

    group_market = group_metadata.get(
        "market"
    )

    group_name = group_metadata.get(
        "name",
        group_key
    )

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    print(
        "\n===================================="
    )
    print(
        f"📋 開始處理自訂群組："
        f"{group_key} / {group_name}"
    )
    print(
        f"📋 Supabase 股票數："
        f"{len(group_dict)}"
    )
    print(
        f"🧪 測試模式：{test_mode}"
    )
    print(
        "===================================="
    )

    for (
        raw_ticker,
        raw_ma_list
    ) in group_dict.items():
        try:
            print(
                f"\n🔍 開始處理："
                f"{raw_ticker}，"
                f"MA={raw_ma_list}"
            )

            (
                actual_ticker,
                downloaded
            ) = download_custom_stock(
                raw_ticker,
                group_key=group_key,
                group_market=group_market
            )

            if (
                not actual_ticker
                or downloaded.empty
            ):
                continue

            csv_path = os.path.join(
                DATA_DIR,
                f"{actual_ticker}.csv"
            )

            combined = downloaded.copy()

            if os.path.exists(csv_path):
                try:
                    local_data = pd.read_csv(
                        csv_path,
                        index_col=0,
                        parse_dates=True
                    )

                    local_data = (
                        clean_ohlcv_dataframe(
                            local_data
                        )
                    )

                    if not local_data.empty:
                        combined = pd.concat(
                            [
                                local_data,
                                downloaded
                            ]
                        )

                except Exception as exc:
                    print(
                        f"⚠️ {actual_ticker} "
                        "讀取 CSV 失敗："
                        f"{exc}"
                    )

            combined = (
                clean_ohlcv_dataframe(
                    combined
                )
            )

            if combined.empty:
                continue

            ma_list = []

            for raw_ma in raw_ma_list:
                ma_value = safe_int(raw_ma)

                if (
                    ma_value is not None
                    and ma_value > 0
                ):
                    ma_list.append(ma_value)

            ma_list = (
                sorted(set(ma_list))
                or [20]
            )

            max_ma = max(ma_list)

            keep_days = max(
                MAX_DAYS,
                max_ma + 60
            )

            combined = combined.tail(
                keep_days
            ).copy()

            combined[
                [
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "Volume"
                ]
            ].to_csv(csv_path)

            for ma_window in ma_list:
                combined[
                    f"MA{ma_window}"
                ] = (
                    combined["Close"]
                    .rolling(
                        window=ma_window,
                        min_periods=ma_window
                    )
                    .mean()
                )

            price = safe_float(
                combined["Close"].iloc[-1]
            )

            latest_volume = safe_float(
                combined["Volume"].iloc[-1]
            )

            matched_any_ma = False
            available_ma_count = 0
            triggered_info = []

            for ma_window in ma_list:
                ma_col = f"MA{ma_window}"

                ma_value = combined[
                    ma_col
                ].iloc[-1]

                if pd.isna(ma_value):
                    continue

                available_ma_count += 1
                ma_value = float(ma_value)

                diff_pct = (
                    (price / ma_value) - 1
                ) * 100

                if test_mode:
                    matched_any_ma = True

                    triggered_info.append(
                        f"MA{ma_window}差距"
                        f"({diff_pct:.2f}%)"
                    )

                elif (
                    ma_value
                    * CUSTOM_MA_MIN_RATIO
                    <= price
                    <= ma_value
                    * CUSTOM_MA_MAX_RATIO
                ):
                    matched_any_ma = True

                    triggered_info.append(
                        f"近MA{ma_window}"
                        f"({diff_pct:.2f}%)"
                    )

            if (
                test_mode
                and available_ma_count == 0
            ):
                matched_any_ma = True

                triggered_info.append(
                    "均線資料不足，K線正常"
                )

            if not matched_any_ma:
                print(
                    f"⏭️ {actual_ticker} "
                    "未符合均線條件"
                )
                continue

            ma_status_list = []

            for ma_window in ma_list:
                ma_col = f"MA{ma_window}"

                ma_value = combined[
                    ma_col
                ].iloc[-1]

                if not pd.isna(ma_value):
                    ma_status_list.append(
                        f"MA{ma_window}:"
                        f"{float(ma_value):.2f}"
                    )

            mode_text = (
                "Supabase測試模式"
                if test_mode
                else "均線潛伏"
            )

            trigger_text = (
                " / ".join(triggered_info)
                if triggered_info
                else "無可用均線"
            )

            ma_status_text = (
                " | ".join(ma_status_list)
                if ma_status_list
                else "均線資料不足"
            )

            title = (
                f"({mode_text} | "
                f"現價:{price:.2f} | "
                f"{trigger_text} | "
                f"{ma_status_text})"
            )

            display_name = ""

            if group_market == "tw":
                display_name = (
                    get_tw_stock_name(
                        actual_ticker
                    )
                )

            chart_data = build_stock_data(
                combined.tail(180),
                actual_ticker,
                title,
                ma_list,
                show_volume=True,
                display_name=display_name,
                timeframe_label="日K"
            )

            matched_list.append(
                {
                    "ticker": actual_ticker,
                    "name": display_name,
                    "group_key": group_key,
                    "group_name": group_name,
                    "volume": int(
                        latest_volume
                    ),
                    "ma_list": ma_list,
                    "chart_data": chart_data
                }
            )

            print(
                f"✅ {actual_ticker} "
                "已成功加入圖表"
            )

        except Exception as exc:
            print(
                f"❌ {group_key} / "
                f"{raw_ticker} 處理失敗："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    matched_list.sort(
        key=lambda item: item["volume"],
        reverse=True
    )

    print(
        f"\n📊 {group_key} 最後產生 "
        f"{len(matched_list)} 張圖表"
    )

    return matched_list


# =========================================================================
# 第 2 部分結束
# 下一部分：全球指數、美股類股週 K、大盤趨勢分析
# =========================================================================

# =========================================================================
# 第 3 部分：全球指數、類股週 K、大盤趨勢分析
# =========================================================================


# =========================================================================
# 大盤參數與指數圖表
# =========================================================================
def get_ma_list_from_item(item):
    ma_list = []

    for ma_key in [
        "ma1",
        "ma2",
        "ma3",
        "ma4"
    ]:
        ma_value = safe_int(
            item.get(ma_key)
        )

        if (
            ma_value is not None
            and ma_value > 0
        ):
            ma_list.append(ma_value)

    return sorted(set(ma_list)) or [20]


def is_config_enabled(item):
    enabled_value = item.get(
        "enabled",
        True
    )

    if isinstance(enabled_value, str):
        return (
            enabled_value.strip().lower()
            not in {
                "0",
                "false",
                "no",
                "off"
            }
        )

    return bool(enabled_value)


def process_index_charts(index_configs):
    matched_list = []

    if not index_configs:
        print(
            "⚠️ 沒有可用的大盤指數設定"
        )
        return matched_list

    sorted_configs = sorted(
        index_configs,
        key=lambda item: safe_int(
            item.get("sort_order"),
            9999
        )
    )

    processed_tickers = set()

    for item in sorted_configs:
        ticker = str(
            item.get("ticker", "")
        ).strip().upper()

        name = str(
            item.get("name")
            or ticker
        ).strip()

        if not ticker:
            continue

        if ticker in processed_tickers:
            print(
                f"⚠️ 略過重複指數：{ticker}"
            )
            continue

        if not is_config_enabled(item):
            print(
                f"⏭️ 指數已停用：{ticker}"
            )
            continue

        processed_tickers.add(ticker)

        ma_list = get_ma_list_from_item(
            item
        )

        try:
            print(
                "📉 開始建立指數圖表："
                f"{ticker} / {name} / "
                f"MA={ma_list}"
            )

            # ^TWOII 優先使用櫃買中心資料，其餘照舊走 Yahoo
            downloaded = download_index_history(
                ticker,
                period="4y",
                progress=False,
                threads=False,
                auto_adjust=False,
                group_by="column"
            )

            df = extract_yfinance_data(
                downloaded,
                ticker
            )

            df = clean_ohlcv_dataframe(df)

            if df.empty:
                print(
                    "⚠️ 指數下載結果為空："
                    f"{ticker}"
                )
                continue

            for ma_window in ma_list:
                df[f"MA{ma_window}"] = (
                    df["Close"]
                    .rolling(
                        window=ma_window,
                        min_periods=ma_window
                    )
                    .mean()
                )

            latest_close = safe_float(
                df["Close"].iloc[-1]
            )

            latest_volume = safe_float(
                df["Volume"].iloc[-1]
            )

            ma_status = []

            for ma_window in ma_list:
                ma_col = f"MA{ma_window}"
                ma_value = df[
                    ma_col
                ].iloc[-1]

                if pd.isna(ma_value):
                    continue

                ma_value = float(ma_value)

                if ma_value == 0:
                    continue

                diff_pct = (
                    (latest_close / ma_value)
                    - 1
                ) * 100

                ma_status.append(
                    f"MA{ma_window}:"
                    f"{ma_value:.2f}"
                    f"({diff_pct:+.2f}%)"
                )

            title_parts = [
                "Supabase 指數參數",
                f"收盤:{latest_close:,.2f}"
            ]

            if latest_volume > 0:
                title_parts.append(
                    f"成交量:{latest_volume:,.0f}"
                )
            else:
                title_parts.append(
                    "成交量:Yahoo未提供"
                )

            if ma_status:
                title_parts.append(
                    " | ".join(ma_status)
                )

            title = (
                "("
                + " | ".join(title_parts)
                + ")"
            )

            chart_data = build_stock_data(
                df.tail(90),
                ticker,
                title,
                ma_list,
                show_volume=True,
                display_name=name,
                timeframe_label="日K"
            )

            matched_list.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "volume": int(
                        latest_volume
                    ),
                    "ma_list": ma_list,
                    "chart_data": chart_data
                }
            )

            print(
                "✅ 指數圖表建立完成："
                f"{ticker} {name}"
            )

        except Exception as exc:
            print(
                "❌ 指數圖表建立失敗："
                f"{ticker} / "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    print(
        "📊 全球指數圖表數量："
        f"{len(matched_list)}"
    )

    return matched_list


# =========================================================================
# 美股 11 大類股週 K
# =========================================================================
def convert_daily_to_weekly(
    daily_df,
    drop_incomplete_week=True
):
    daily_df = clean_ohlcv_dataframe(
        daily_df
    )

    if daily_df.empty:
        return pd.DataFrame()

    last_daily_date = (
        daily_df.index[-1].normalize()
    )

    weekly_df = daily_df.resample(
        "W-FRI"
    ).agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum"
        }
    )

    weekly_df = clean_ohlcv_dataframe(
        weekly_df
    )

    if weekly_df.empty:
        return weekly_df

    # 若資料最後日期尚未到該週星期五，
    # resample 仍會產生一根未完成週 K。
    # 為避免星期一推播誤用本週資料，將它移除。
    if (
        drop_incomplete_week
        and weekly_df.index[-1].normalize()
        > last_daily_date
    ):
        weekly_df = weekly_df.iloc[:-1]

    return weekly_df


def determine_sector_price_volume_status(
    weekly_df,
    weekly_return,
    volume_ratio
):
    if weekly_df.empty:
        return "資料不足"

    if (
        weekly_return > 0
        and volume_ratio >= 1.05
    ):
        return "上漲放量，資金流入"

    if (
        weekly_return > 0
        and volume_ratio < 0.95
    ):
        return "上漲縮量，動能普通"

    if (
        weekly_return < 0
        and volume_ratio >= 1.05
    ):
        return "下跌放量，資金流出"

    if (
        weekly_return < 0
        and volume_ratio < 0.95
    ):
        return "下跌縮量，賣壓減弱"

    return "量價中性"


def determine_sector_trend(
    latest_close,
    ma20,
    ma60
):
    above_ma20 = (
        ma20 is not None
        and latest_close > ma20
    )

    above_ma60 = (
        ma60 is not None
        and latest_close > ma60
    )

    if above_ma20 and above_ma60:
        return "週線多頭"

    if not above_ma20 and not above_ma60:
        return "週線空頭"

    if above_ma60 and not above_ma20:
        return "多頭回檔"

    return "空頭反彈"


def process_sector_weekly_charts():
    sector_tickers = [
        item["ticker"]
        for item in SECTOR_CONFIGS
    ]

    download_tickers = list(
        dict.fromkeys(
            sector_tickers
            + [SECTOR_BENCHMARK]
        )
    )

    print(
        "\n===================================="
    )
    print(
        "🧭 開始建立美股 11 大類股週 K"
    )
    print(
        "📏 固定週均線："
        f"{SECTOR_WEEKLY_MA_LIST}"
    )
    print(
        "📊 相對強弱基準："
        f"{SECTOR_BENCHMARK}"
    )
    print(
        "===================================="
    )

    try:
        downloaded = yf.download(
            download_tickers,
            period="10y",
            progress=False,
            threads=False,
            auto_adjust=False,
            group_by="column"
        )

    except Exception as exc:
        print(
            "❌ 類股批次下載失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return [], []

    if downloaded.empty:
        print(
            "❌ 類股批次下載結果為空"
        )
        return [], []

    benchmark_daily = (
        extract_yfinance_data(
            downloaded,
            SECTOR_BENCHMARK
        )
    )

    benchmark_daily = (
        clean_ohlcv_dataframe(
            benchmark_daily
        )
    )

    benchmark_weekly = (
        convert_daily_to_weekly(
            benchmark_daily,
            drop_incomplete_week=True
        )
    )

    if benchmark_weekly.empty:
        print(
            f"❌ {SECTOR_BENCHMARK} "
            "週線資料不足"
        )
        return [], []

    benchmark_return_13w = (
        calculate_return(
            benchmark_weekly["Close"],
            13
        )
    )

    sector_results = []

    for config in SECTOR_CONFIGS:
        ticker = str(
            config["ticker"]
        ).strip().upper()

        name = str(
            config["name"]
        ).strip()

        try:
            daily_df = (
                extract_yfinance_data(
                    downloaded,
                    ticker
                )
            )

            daily_df = (
                clean_ohlcv_dataframe(
                    daily_df
                )
            )

            weekly_df = (
                convert_daily_to_weekly(
                    daily_df,
                    drop_incomplete_week=True
                )
            )

            if (
                weekly_df.empty
                or len(weekly_df) < 61
            ):
                print(
                    f"⚠️ {ticker} "
                    "週線資料不足"
                )
                continue

            for ma_window in (
                SECTOR_WEEKLY_MA_LIST
            ):
                weekly_df[
                    f"MA{ma_window}"
                ] = (
                    weekly_df["Close"]
                    .rolling(
                        window=ma_window,
                        min_periods=ma_window
                    )
                    .mean()
                )

            weekly_df["VOL_MA10"] = (
                weekly_df["Volume"]
                .rolling(
                    window=10,
                    min_periods=5
                )
                .mean()
            )

            latest_close = safe_float(
                weekly_df[
                    "Close"
                ].iloc[-1]
            )

            latest_volume = safe_float(
                weekly_df[
                    "Volume"
                ].iloc[-1]
            )

            return_1w = calculate_return(
                weekly_df["Close"],
                1
            )

            return_4w = calculate_return(
                weekly_df["Close"],
                4
            )

            return_13w = calculate_return(
                weekly_df["Close"],
                13
            )

            relative_strength_13w = (
                return_13w
                - benchmark_return_13w
            )

            # 使用前十個完整週的平均量，
            # 不將當週量納入基準。
            previous_volumes = (
                weekly_df["Volume"]
                .iloc[-11:-1]
            )

            average_volume_10w = safe_float(
                previous_volumes.mean(),
                0.0
            )

            if average_volume_10w > 0:
                volume_ratio = (
                    latest_volume
                    / average_volume_10w
                )
            else:
                volume_ratio = 1.0

            volume_change_pct = (
                volume_ratio - 1
            ) * 100

            ma20_value = weekly_df[
                "MA20"
            ].iloc[-1]

            ma60_value = weekly_df[
                "MA60"
            ].iloc[-1]

            ma20 = (
                None
                if pd.isna(ma20_value)
                else float(ma20_value)
            )

            ma60 = (
                None
                if pd.isna(ma60_value)
                else float(ma60_value)
            )

            trend_status = (
                determine_sector_trend(
                    latest_close,
                    ma20,
                    ma60
                )
            )

            volume_status = (
                determine_sector_price_volume_status(
                    weekly_df,
                    return_1w,
                    volume_ratio
                )
            )

            # 類股動能綜合分數：
            # 20% 一週、25% 四週、
            # 30% 十三週、25% 相對 SPY。
            momentum_score = (
                return_1w * 0.20
                + return_4w * 0.25
                + return_13w * 0.30
                + relative_strength_13w
                * 0.25
            )

            title = (
                "(完整週K | "
                f"本週:{return_1w:+.2f}% | "
                f"4週:{return_4w:+.2f}% | "
                f"13週:{return_13w:+.2f}% | "
                "相對SPY:"
                f"{relative_strength_13w:+.2f}% | "
                f"{trend_status} | "
                f"{volume_status} "
                f"{volume_change_pct:+.1f}%)"
            )

            chart_data = build_stock_data(
                weekly_df.tail(
                    SECTOR_CHART_WEEKS
                ),
                ticker,
                title,
                SECTOR_WEEKLY_MA_LIST,
                show_volume=True,
                display_name=name,
                timeframe_label="週K"
            )

            sector_results.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "sort_order": config[
                        "sort_order"
                    ],
                    "volume": int(
                        latest_volume
                    ),
                    "latest_close": (
                        latest_close
                    ),
                    "return_1w": return_1w,
                    "return_4w": return_4w,
                    "return_13w": return_13w,
                    "benchmark_return_13w": (
                        benchmark_return_13w
                    ),
                    "relative_strength_13w": (
                        relative_strength_13w
                    ),
                    "volume_ratio": (
                        volume_ratio
                    ),
                    "volume_change_pct": (
                        volume_change_pct
                    ),
                    "volume_status": (
                        volume_status
                    ),
                    "trend_status": (
                        trend_status
                    ),
                    "momentum_score": (
                        momentum_score
                    ),
                    "ma_list": (
                        SECTOR_WEEKLY_MA_LIST.copy()
                    ),
                    "chart_data": chart_data
                }
            )

            print(
                f"✅ {ticker} {name}："
                f"動能={momentum_score:+.2f}，"
                f"13週={return_13w:+.2f}%，"
                "相對SPY="
                f"{relative_strength_13w:+.2f}%"
            )

        except Exception as exc:
            print(
                "❌ 類股處理失敗："
                f"{ticker} / "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    sector_results.sort(
        key=lambda item: (
            item["momentum_score"]
        ),
        reverse=True
    )

    sector_summary = []

    result_count = len(
        sector_results
    )

    for rank, item in enumerate(
        sector_results,
        start=1
    ):
        item["rank"] = rank

        if rank <= 3:
            strength_group = "strong"
            strength_label = "強勢領先"

        elif rank > max(
            3,
            result_count - 3
        ):
            strength_group = "weak"
            strength_label = "弱勢落後"

        else:
            strength_group = "neutral"
            strength_label = "中性輪動"

        item["strength_group"] = (
            strength_group
        )

        item["strength_label"] = (
            strength_label
        )

        sector_summary.append(
            {
                "rank": rank,
                "ticker": item["ticker"],
                "name": item["name"],
                "return_1w": (
                    item["return_1w"]
                ),
                "return_4w": (
                    item["return_4w"]
                ),
                "return_13w": (
                    item["return_13w"]
                ),
                "relative_strength_13w": (
                    item[
                        "relative_strength_13w"
                    ]
                ),
                "momentum_score": (
                    item["momentum_score"]
                ),
                "volume_status": (
                    item["volume_status"]
                ),
                "trend_status": (
                    item["trend_status"]
                ),
                "strength_group": (
                    strength_group
                ),
                "strength_label": (
                    strength_label
                )
            }
        )

    print(
        "📊 類股週 K 圖表數量："
        f"{len(sector_results)}"
    )

    return (
        sector_results,
        sector_summary
    )


def build_sector_line_message(
    today_str,
    sector_summary
):
    """LINE 用的精簡類股週線輪動（無連結）；網頁沿用 build_sector_monday_message。"""
    if not sector_summary:
        return (
            f"📅 {today_str} 美股類股週線\n"
            "⚠️ 本週類股資料不足"
        )

    def describe(item):
        return (
            f"{item['ticker']} "
            f"{item['name']} "
            f"{item['return_13w']:+.1f}%"
        )

    strongest = sector_summary[:3]
    weakest = sector_summary[-3:]

    inflow = [
        item["name"]
        for item in sector_summary
        if "資金流入" in item["volume_status"]
    ]

    outflow = [
        item["name"]
        for item in sector_summary
        if "資金流出" in item["volume_status"]
    ]

    lines = [
        f"📅 {today_str} 美股類股週線（13週）",
        "🟢 強：" + "、".join(
            describe(item)
            for item in strongest
        ),
        "🔴 弱：" + "、".join(
            describe(item)
            for item in weakest
        ),
        "💰 流入："
        + ("、".join(inflow) if inflow else "無")
        + "｜流出："
        + ("、".join(outflow) if outflow else "無")
    ]

    return "\n".join(lines)


def build_sector_monday_message(
    today_str,
    sector_summary,
    report_url
):
    if not sector_summary:
        return (
            f"📅 {today_str} "
            "美股類股週線輪動\n\n"
            "⚠️ 本週類股資料不足，"
            "請至網頁稍後重新查看。\n\n"
            f"🔗 完整圖表：\n{report_url}"
        )

    strongest = sector_summary[:3]
    weakest = sector_summary[-3:]

    lines = [
        f"📅 {today_str} "
        "美股類股週線輪動",
        "📊 週均線：MA20 / MA60",
        (
            "📌 排名依據：1週、4週、"
            "13週與相對SPY強弱"
        ),
        "",
        "🟢 【強勢前三名】"
    ]

    for item in strongest:
        lines.append(
            f"{item['rank']}. "
            f"{item['ticker']} "
            f"{item['name']}\n"
            "   ├ 13週："
            f"{item['return_13w']:+.2f}%\n"
            "   ├ 相對SPY："
            f"{item['relative_strength_13w']:+.2f}%\n"
            f"   └ {item['trend_status']}｜"
            f"{item['volume_status']}"
        )

    lines.extend(
        [
            "",
            "🔴 【弱勢後三名】"
        ]
    )

    for item in weakest:
        lines.append(
            f"{item['rank']}. "
            f"{item['ticker']} "
            f"{item['name']}\n"
            "   ├ 13週："
            f"{item['return_13w']:+.2f}%\n"
            "   ├ 相對SPY："
            f"{item['relative_strength_13w']:+.2f}%\n"
            f"   └ {item['trend_status']}｜"
            f"{item['volume_status']}"
        )

    inflow_items = [
        item
        for item in sector_summary
        if "資金流入"
        in item["volume_status"]
    ]

    outflow_items = [
        item
        for item in sector_summary
        if "資金流出"
        in item["volume_status"]
    ]

    lines.extend(
        [
            "",
            "💰 【量價資金狀態】"
        ]
    )

    if inflow_items:
        lines.append(
            "資金流入："
            + "、".join(
                item["name"]
                for item in inflow_items
            )
        )
    else:
        lines.append(
            "資金流入：目前無明顯類股"
        )

    if outflow_items:
        lines.append(
            "資金流出："
            + "、".join(
                item["name"]
                for item in outflow_items
            )
        )
    else:
        lines.append(
            "資金流出：目前無明顯類股"
        )

    lines.extend(
        [
            "",
            "🔗 查看完整類股週K：",
            report_url
        ]
    )

    return "\n".join(lines)


# =========================================================================
# 大盤多空趨勢分析
# 1. 均線糾纏自適應
# 2. 0.1% 過濾
# 3. 大趨勢與小走勢：道氏狀態機
#    - 小走勢：波峰波谷取前後各 5 根（狀態約持續 3 週至 3 個月）
#    - 大趨勢：波峰波谷取前後各 30 根（狀態約持續 4 個月以上），
#      收盤創三年新高時直接轉為多頭
# =========================================================================
DOW_SWING_PIVOT_BARS = 5
DOW_TREND_PIVOT_BARS = 30
DOW_TREND_NEW_HIGH_BARS = 756


def compute_dow_states(
    highs,
    lows,
    closes,
    pivot_bars,
    new_high_bars=0
):
    """
    道氏狀態機，回傳每一天的狀態：1 為多頭，-1 為空頭。

    波峰（波谷）是前後各 pivot_bars 根 K 棒中的最高（最低）點，
    要等後面 pivot_bars 根走完才算確認，所以不會用到未來資料。

    多轉空：最近一個波谷之後的反彈高點，低於該波谷之前的波峰，
            且收盤跌破該波谷。
    空轉多：最近一個波峰之後的回檔低點，高於該波峰之前的波谷，
            且收盤突破該波峰。
    其餘時間維持原狀態。

    new_high_bars 大於 0 時，空頭期間只要收盤高於
    前 new_high_bars 根的最高價，就直接轉為多頭。
    """
    count = len(closes)
    states = []
    state = 1

    # 交替排列的波峰波谷：[種類, 位置, 價格]
    swings = []

    for today in range(count):
        pivot = today - pivot_bars

        if pivot - pivot_bars >= 0:
            is_peak = (
                highs[pivot]
                > max(highs[pivot - pivot_bars:pivot])
                and highs[pivot]
                >= max(highs[pivot + 1:today + 1])
            )

            if is_peak:
                # 連續兩個波峰時，補上中間的最低點當波谷
                if swings and swings[-1][0] == "H":
                    start = swings[-1][1] + 1

                    if pivot > start:
                        between = min(
                            range(start, pivot),
                            key=lows.__getitem__
                        )

                        swings.append(
                            ["L", between, lows[between]]
                        )

                        swings.append(
                            ["H", pivot, highs[pivot]]
                        )

                else:
                    swings.append(
                        ["H", pivot, highs[pivot]]
                    )

            is_trough = (
                lows[pivot]
                < min(lows[pivot - pivot_bars:pivot])
                and lows[pivot]
                <= min(lows[pivot + 1:today + 1])
            )

            if is_trough:
                # 連續兩個波谷時，補上中間的最高點當波峰
                if swings and swings[-1][0] == "L":
                    start = swings[-1][1] + 1

                    if pivot > start:
                        between = max(
                            range(start, pivot),
                            key=highs.__getitem__
                        )

                        swings.append(
                            ["H", between, highs[between]]
                        )

                        swings.append(
                            ["L", pivot, lows[pivot]]
                        )

                else:
                    swings.append(
                        ["L", pivot, lows[pivot]]
                    )

        if len(swings) >= 2:
            if state == 1:
                # 多轉空：先有較低的高點，再收盤跌破前一個波谷
                position = (
                    len(swings) - 1
                    if swings[-1][0] == "L"
                    else len(swings) - 2
                )

                if position >= 1:
                    trough_index = swings[position][1]
                    trough_price = swings[position][2]
                    prior_peak = swings[position - 1][2]

                    if (
                        closes[today] < trough_price
                        and today > trough_index
                        and max(
                            highs[trough_index + 1:today + 1]
                        ) < prior_peak
                    ):
                        state = -1

            else:
                # 空轉多：先有較高的低點，再收盤突破前一個波峰
                position = (
                    len(swings) - 1
                    if swings[-1][0] == "H"
                    else len(swings) - 2
                )

                if position >= 1:
                    peak_index = swings[position][1]
                    peak_price = swings[position][2]
                    prior_trough = swings[position - 1][2]

                    if (
                        closes[today] > peak_price
                        and today > peak_index
                        and min(
                            lows[peak_index + 1:today + 1]
                        ) > prior_trough
                    ):
                        state = 1

        if (
            new_high_bars
            and state == -1
            and today >= new_high_bars
            and closes[today] > max(
                highs[today - new_high_bars + 1:today]
            )
        ):
            state = 1

        states.append(state)

    return states


def analyze_index_trend(
    ticker,
    name,
    ma_list
):
    if not ma_list:
        return (
            f"⚪ {name}: "
            "未設定任何均線參數"
        )

    try:
        # ^TWOII 優先使用櫃買中心資料，其餘照舊走 Yahoo
        df = download_index_history(
            ticker,
            period="10y",
            progress=False,
            threads=False,
            auto_adjust=False
        )

        if df.empty or len(df) < 750:
            return (
                f"⚪ {name}: "
                "數據不足無法分析"
            )

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):
            df = extract_yfinance_data(
                df,
                ticker
            )

        df = clean_ohlcv_dataframe(df)

        if df.empty or len(df) < 750:
            return (
                f"⚪ {name}: "
                "數據不足無法分析"
            )

        available_mas = []

        for ma in ma_list:
            col_name = f"MA{ma}"

            df[col_name] = (
                df["Close"]
                .rolling(
                    window=ma,
                    min_periods=ma
                )
                .mean()
            )

            available_mas.append(
                col_name
            )

        df = df.dropna(
            subset=available_mas
        )

        if len(df) < 5:
            return (
                f"⚪ {name}: "
                "計算後可用數據小於5日"
            )

        latest = df.iloc[-1]
        score = 0

        total_ma_count = len(
            available_mas
        )

        df_last5 = df.tail(5)

        # 最近五天至少三天 K 棒碰到均線，
        # 該均線視為糾纏，計為中性 0 分。
        for ma_col in available_mas:
            touch_count = 0

            for _, row_5 in (
                df_last5.iterrows()
            ):
                if (
                    row_5["Low"]
                    <= row_5[ma_col]
                    <= row_5["High"]
                ):
                    touch_count += 1

            if touch_count >= 3:
                continue

            latest_close = safe_float(
                latest["Close"]
            )

            latest_ma = safe_float(
                latest[ma_col]
            )

            if latest_close > (
                latest_ma * 1.001
            ):
                score += 1

            elif latest_close < (
                latest_ma * 0.999
            ):
                score -= 1

        if score == total_ma_count:
            score_label = "看多"

        elif score > 0:
            score_label = "偏多"

        elif score == 0:
            score_label = "多空不明"

        elif score == -total_ma_count:
            score_label = "看空"

        else:
            score_label = "偏空"

        # -------------------------------------------------------------
        # 道氏狀態機：大趨勢與小走勢
        # 兩者規則相同，只差波段大小。
        # 狀態會延續，直到出現反轉訊號才切換。
        # -------------------------------------------------------------
        highs = [
            safe_float(value)
            for value in df["High"].tolist()
        ]

        lows = [
            safe_float(value)
            for value in df["Low"].tolist()
        ]

        closes = [
            safe_float(value)
            for value in df["Close"].tolist()
        ]

        # 大趨勢：4 個月以上的波段
        macro_state = compute_dow_states(
            highs,
            lows,
            closes,
            DOW_TREND_PIVOT_BARS,
            DOW_TREND_NEW_HIGH_BARS
        )[-1]

        # 小走勢：3 週至 3 個月的波段
        micro_state = compute_dow_states(
            highs,
            lows,
            closes,
            DOW_SWING_PIVOT_BARS
        )[-1]

        macro_trend = (
            "多頭趨勢"
            if macro_state == 1
            else "空頭趨勢"
        )

        micro_trend = (
            "多頭走勢"
            if micro_state == 1
            else "空頭走勢"
        )

        final_status = (
            f"{macro_trend}中的"
            f"{micro_trend}"
        )

        if (
            macro_trend == "多頭趨勢"
            and micro_trend == "多頭走勢"
        ):
            icon = "🔺"

        elif (
            macro_trend == "多頭趨勢"
            and micro_trend == "空頭走勢"
        ):
            icon = "💡"

        elif (
            macro_trend == "空頭趨勢"
            and micro_trend == "空頭走勢"
        ):
            icon = "🔻"

        else:
            icon = "⚡"

        ma_string = "/".join(
            str(value)
            for value in ma_list
        )

        return (
            f"{icon} {name}\n"
            f"   ├ 均線: {score_label} "
            f"({score}/{total_ma_count}MA - "
            f"{ma_string})\n"
            f"   └  {final_status}"
        )

    except Exception as exc:
        print(
            f"⚠️ {ticker} "
            "大盤分析異常："
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return (
            f"⚪ {name}: "
            "分析發生異常"
        )


# =========================================================================
# 第 3 部分結束
# 下一部分：HTML、動態群組頁籤、LIFF 與畫線同步
# =========================================================================

# =========================================================================
# 第 4 部分：動態群組工具
# =========================================================================

def get_sorted_custom_group_keys(
    group_metadata,
    market=None
):
    keys = []

    for group_key, metadata in (
        group_metadata.items()
    ):
        if not metadata.get(
            "is_custom",
            False
        ):
            continue

        if (
            market is not None
            and metadata.get("market")
            != market
        ):
            continue

        keys.append(group_key)

    return sorted(
        keys,
        key=get_custom_group_number
    )


# =========================================================================
# 第 6 部分：GitHub Pages 推送與主程式
# =========================================================================


# =========================================================================
# Git 指令工具
# =========================================================================
def run_git_command(command):
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False
    )

    if result.stdout.strip():
        print(result.stdout.strip())

    if result.stderr.strip():
        print(result.stderr.strip())

    return result.returncode


def get_csv_last_date(csv_path):
    """
    讀取 CSV 最後一列的日期，失敗時回傳 None。
    只讀檔尾，避免整檔載入。
    """
    try:
        with open(
            csv_path,
            "rb"
        ) as csv_file:
            csv_file.seek(0, os.SEEK_END)
            size = csv_file.tell()
            csv_file.seek(
                max(0, size - 512)
            )
            tail_lines = (
                csv_file.read()
                .decode(
                    "utf-8",
                    errors="ignore"
                )
                .strip()
                .splitlines()
            )

        if len(tail_lines) < 2:
            return None

        return datetime.strptime(
            tail_lines[-1]
            .split(",")[0]
            .strip()[:10],
            "%Y-%m-%d"
        )

    except Exception:
        return None


def prune_stale_csv(
    data_dir=DATA_DIR,
    max_age_days=STALE_CSV_DAYS
):
    """
    刪除最後一根 K 線已超過 max_age_days 天沒更新的 CSV。

    掉出掃描名單、下市或已從群組移除的股票不會再被更新，
    它們的 CSV 會因此逐漸過期並被清掉，避免 repo 無限膨脹。
    仍在名單內的股票每天都會更新，不會被刪除。
    """
    if not os.path.isdir(data_dir):
        return 0

    now = datetime.now()
    csv_paths = [
        os.path.join(data_dir, name)
        for name in os.listdir(data_dir)
        if name.endswith(".csv")
        and name not in STALE_CSV_EXCLUDE
    ]

    stale_paths = []

    for csv_path in csv_paths:
        last_date = get_csv_last_date(
            csv_path
        )

        if last_date is None:
            continue

        if (
            now - last_date
        ).days > max_age_days:
            stale_paths.append(csv_path)

    # 安全閥：資料來源整批異常時，不要一次刪光
    if (
        csv_paths
        and len(stale_paths)
        > len(csv_paths)
        * STALE_CSV_MAX_DELETE_RATIO
    ):
        print(
            "⚠️ 過期 CSV 數量異常"
            f"（{len(stale_paths)}/"
            f"{len(csv_paths)}），"
            "略過清理"
        )
        return 0

    for csv_path in stale_paths:
        try:
            os.remove(csv_path)
        except OSError as exc:
            print(
                f"⚠️ 刪除失敗 {csv_path}："
                f"{exc}"
            )

    print(
        f"🧹 已清除過期 CSV："
        f"{len(stale_paths)} 檔"
    )

    return len(stale_paths)


def push_report_to_github():
    run_git_command(
        [
            "git",
            "config",
            "--global",
            "user.name",
            "github-actions[bot]"
        ]
    )

    run_git_command(
        [
            "git",
            "config",
            "--global",
            "user.email",
            (
                "github-actions[bot]"
                "@users.noreply.github.com"
            )
        ]
    )

    # docs/report：Atlas 報告 JSON 與 data 歷史資料
    add_paths = [
        path
        for path in [
            "docs/report",
            "data"
        ]
        if os.path.exists(path)
    ]

    if not add_paths:
        print(
            "ℹ️ 沒有需要提交的報告路徑"
        )
        return True

    add_result = run_git_command(
        [
            "git",
            "add",
            "-A",
            "--"
        ]
        + add_paths
    )

    if add_result != 0:
        print("❌ git add 失敗")
        return False

    diff_result = subprocess.run(
        [
            "git",
            "diff",
            "--cached",
            "--quiet"
        ],
        check=False
    )

    if diff_result.returncode == 0:
        print(
            "ℹ️ 沒有 Git 異動需要提交"
        )
        return True

    commit_result = run_git_command(
        [
            "git",
            "commit",
            "-m",
            (
                "⚙️ 量化報告自動更新 "
                "(動態群組與畫線同步)"
            )
        ]
    )

    if commit_result != 0:
        print("❌ git commit 失敗")
        return False

    push_result = run_git_command(
        [
            "git",
            "push"
        ]
    )

    if push_result == 0:
        print(
            "✅ GitHub Pages 資料推送完成"
        )
        return True

    print("❌ git push 失敗")
    return False


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

# 指數歷史快取：ticker → {"df": 清理後 DataFrame, "source": "TPEx|Yahoo"}
INDEX_HISTORY_CACHE = {}


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
        local_data = pd.read_csv(
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
    directory = os.path.dirname(csv_path) or "."

    os.makedirs(directory, exist_ok=True)

    output = df[
        [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]
    ].copy()

    output.index.name = "Date"

    file_descriptor, tmp_path = tempfile.mkstemp(
        prefix=".tmp-",
        suffix=".csv",
        dir=directory
    )

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
            newline=""
        ) as file:
            output.to_csv(
                file,
                date_format="%Y-%m-%d"
            )

        os.replace(tmp_path, csv_path)

    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


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


def download_index_history(
    ticker,
    **yf_kwargs
):
    """
    指數日 K 下載入口：
    - ^TWOII 有新鮮的櫃買中心資料時直接使用；
    - 其他指數（或 TPEx 不可用時）照舊呼叫 yf.download(ticker, **yf_kwargs)。
    下載結果會清理後存入 INDEX_HISTORY_CACHE，供報告 JSON 讀取收盤、漲跌與均線。
    """
    normalized_ticker = str(
        ticker
    ).strip().upper()

    if normalized_ticker == TPEX_OTC_SYMBOL:
        try:
            tpex_df = get_tpex_otc_history()
        except Exception as exc:
            print(
                "⚠️ 讀取 TPEx 櫃買指數失敗："
                f"{type(exc).__name__}: {exc}"
            )
            tpex_df = pd.DataFrame()

        if not tpex_df.empty:
            INDEX_HISTORY_CACHE[
                normalized_ticker
            ] = {
                "df": tpex_df.copy(),
                "source": "TPEx"
            }

            return tpex_df.copy()

    downloaded = yf.download(
        ticker,
        **yf_kwargs
    )

    try:
        INDEX_HISTORY_CACHE[
            normalized_ticker
        ] = {
            "df": clean_ohlcv_dataframe(
                extract_yfinance_data(
                    downloaded,
                    ticker
                )
            ),
            "source": "Yahoo"
        }
    except Exception:
        pass

    return downloaded


# =========================================================================
# Atlas 報告 JSON：docs/report/latest.json 與 docs/report/series/*.json
# =========================================================================

# LINE 訊息的「亞洲市場」在 JSON 拆成日本、韓國
REPORT_MARKETS = [
    {
        "key": "tw",
        "name": "台灣",
        "flag": "🇹🇼"
    },
    {
        "key": "jp",
        "name": "日本",
        "flag": "🇯🇵"
    },
    {
        "key": "kr",
        "name": "韓國",
        "flag": "🇰🇷"
    },
    {
        "key": "eu",
        "name": "歐洲",
        "flag": "🇪🇺"
    },
    {
        "key": "us",
        "name": "美國",
        "flag": "🇺🇸"
    }
]

# 只出現在 JSON 的世界指數（不加入 LINE 訊息）
REPORT_EXTRA_INDEX_MA_LIST = [20, 60]

WORLD_EXTRA_INDEX_CONFIGS = [
    {
        "ticker": "^HSI",
        "name": "香港恆生指數",
        "market": "hk"
    },
    {
        "ticker": "000001.SS",
        "name": "上證綜合指數",
        "market": "cn"
    },
    {
        "ticker": "^BSESN",
        "name": "印度孟買SENSEX",
        "market": "in"
    },
    {
        "ticker": "^AXJO",
        "name": "澳洲ASX 200",
        "market": "au"
    },
    {
        "ticker": "^STOXX50E",
        "name": "歐洲斯托克50",
        "market": "eu"
    },
    {
        "ticker": "^GSPTSE",
        "name": "加拿大S&P/TSX",
        "market": "ca"
    },
    {
        "ticker": "^BVSP",
        "name": "巴西聖保羅指數",
        "market": "br"
    }
]

# LINE 指數清單中，Supabase 未啟用時在 worldIndices 顯示的名稱
REPORT_CORE_INDEX_NAMES = {
    "^TWII": "台灣加權指數",
    "^TWOII": "台灣櫃買指數(OTC)",
    "^GSPC": "美國標普500",
    "^DJI": "美國道瓊工業",
    "^IXIC": "美國那斯達克",
    "^RUT": "美國羅素2000",
    "^SOX": "美國費城半導體",
    "^FCHI": "法國CAC40",
    "^FTSE": "英國富時100",
    "^GDAXI": "德國DAX",
    "^N225": "日經225",
    "^KS11": "韓國綜合指數"
}

REPORT_SCAN_GROUP_NAMES = {
    "tw_all": "台股-全市場潛伏",
    "us_all": "美股-全市場潛伏"
}

# 趨勢圖示 → bull / bear / neutral
# 🔺 多頭趨勢中的多頭走勢、🔻 空頭趨勢中的空頭走勢、
# 💡 多頭趨勢中的空頭走勢（回檔）、⚡ 空頭趨勢中的多頭走勢（反彈）
INDEX_TREND_ICON_MAP = {
    "🔺": "bull",
    "🟢": "bull",
    "📈": "bull",
    "🔻": "bear",
    "🔴": "bear",
    "📉": "bear",
    "💡": "neutral",
    "⚡": "neutral",
    "🟡": "neutral"
}


def round_report_number(value, digits=2):
    number = safe_float(value, None)

    if number is None:
        return None

    if number != 0 and abs(number) < 1:
        digits = max(digits, 4)

    return round(number, digits)


def parse_index_trend_text(text):
    """
    從 analyze_index_trend 的 LINE 文字讀出結構化欄位，不重算趨勢。
    例：
        🔺 台灣加權指數
           ├ 均線: 看多 (3/3MA - 23/29/62)
           └  多頭趨勢中的多頭走勢
    失敗時的單行文字（⚪ 名稱: 數據不足無法分析）→ trend=unknown。
    """
    text = str(text or "").strip()

    result = {
        "trend": "unknown",
        "trendLabel": "",
        "score": None,
        "scoreMax": None,
        "scoreLabel": None,
        "macroTrend": None,
        "microTrend": None
    }

    if not text:
        return result

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    first_line = lines[0]
    icon = first_line.split(" ", 1)[0]

    if len(lines) == 1:
        message = (
            first_line.split(":", 1)[1].strip()
            if ":" in first_line
            else first_line
        )

        result["trendLabel"] = (
            f"{icon} {message}".strip()
        )
        return result

    status_text = re.sub(
        r"^[├└│\s]+",
        "",
        lines[-1]
    ).strip()

    result["trendLabel"] = (
        f"{icon} {status_text}".strip()
    )

    score_match = re.search(
        r"均線[:：]\s*(\S+)\s*\((-?\d+)\s*/\s*(\d+)\s*MA",
        text
    )

    if score_match:
        result["scoreLabel"] = score_match.group(1)
        result["score"] = int(score_match.group(2))
        result["scoreMax"] = int(score_match.group(3))

    phase_match = re.search(
        r"(多頭|空頭)趨勢中的(多頭|空頭)走勢",
        text
    )

    if phase_match:
        result["macroTrend"] = (
            "bull"
            if phase_match.group(1) == "多頭"
            else "bear"
        )
        result["microTrend"] = (
            "bull"
            if phase_match.group(2) == "多頭"
            else "bear"
        )

    if icon in INDEX_TREND_ICON_MAP:
        result["trend"] = INDEX_TREND_ICON_MAP[icon]

    elif result["macroTrend"]:
        result["trend"] = (
            result["macroTrend"]
            if result["macroTrend"]
            == result["microTrend"]
            else "neutral"
        )

    elif "多頭" in status_text and "空頭" not in status_text:
        result["trend"] = "bull"

    elif "空頭" in status_text and "多頭" not in status_text:
        result["trend"] = "bear"

    else:
        result["trend"] = "neutral"

    return result


def build_index_snapshot(ticker, ma_list):
    """從 INDEX_HISTORY_CACHE 讀出收盤、漲跌幅、均線值與日期。"""
    cached = INDEX_HISTORY_CACHE.get(
        str(ticker).strip().upper()
    ) or {}

    df = cached.get("df")

    snapshot = {
        "close": None,
        "changePct": None,
        "maValues": {},
        "asOf": None,
        "source": cached.get("source")
    }

    if df is None or df.empty:
        return snapshot

    close_series = df["Close"].dropna()

    if close_series.empty:
        return snapshot

    latest_close = safe_float(
        close_series.iloc[-1],
        None
    )

    snapshot["close"] = round_report_number(
        latest_close
    )

    snapshot["asOf"] = (
        pd.Timestamp(
            close_series.index[-1]
        ).strftime("%Y-%m-%d")
    )

    if len(close_series) >= 2:
        previous_close = safe_float(
            close_series.iloc[-2],
            None
        )

        if previous_close:
            snapshot["changePct"] = round(
                (latest_close / previous_close - 1)
                * 100,
                2
            )

    for ma_window in ma_list:
        if len(close_series) < ma_window:
            continue

        snapshot["maValues"][
            str(ma_window)
        ] = round_report_number(
            close_series.tail(ma_window).mean()
        )

    return snapshot


def build_index_status(
    ticker,
    name,
    market,
    ma_list,
    line_text,
    configured=True
):
    ma_list = [
        int(value)
        for value in ma_list
    ]

    snapshot = build_index_snapshot(
        ticker,
        ma_list
    )

    parsed = parse_index_trend_text(
        line_text
    )

    return {
        "symbol": ticker,
        "name": name,
        "market": market,
        "maList": ma_list,
        "close": snapshot["close"],
        "changePct": snapshot["changePct"],
        "trend": parsed["trend"],
        "trendLabel": parsed["trendLabel"],
        "score": parsed["score"],
        "scoreMax": parsed["scoreMax"],
        "scoreLabel": parsed["scoreLabel"],
        "macroTrend": parsed["macroTrend"],
        "microTrend": parsed["microTrend"],
        "maValues": snapshot["maValues"],
        "asOf": snapshot["asOf"],
        "source": snapshot["source"],
        "configured": bool(configured),
        "lineText": str(line_text or "")
    }


def analyze_index_for_report(
    ticker,
    name,
    ma_list
):
    """JSON 專用的額外指數分析；任何錯誤只影響該指數。"""
    try:
        return analyze_index_trend(
            ticker,
            name,
            ma_list
        )
    except Exception as exc:
        print(
            f"⚠️ {ticker} 報告指數分析失敗："
            f"{type(exc).__name__}: {exc}"
        )
        return f"⚪ {name}: 分析發生異常"


def build_report_indices(
    index_map,
    market_tickers,
    captured_index_lines,
    market_line_texts,
    extra_index_configs=None,
    analyze=analyze_index_for_report
):
    """
    回傳 (markets, world_indices)。
    markets 與 LINE 指數訊息一致（只含 Supabase 啟用的指數）；
    world_indices 另外加上：其他已啟用的 index_configs、
    LINE 清單中未啟用的指數（configured=false，預設 MA）、
    以及 WORLD_EXTRA_INDEX_CONFIGS。
    """
    if extra_index_configs is None:
        extra_index_configs = (
            WORLD_EXTRA_INDEX_CONFIGS
        )

    markets = []
    world_indices = []
    seen = set()

    for market_meta in REPORT_MARKETS:
        market_key = market_meta["key"]
        statuses = []

        for raw_ticker in market_tickers.get(
            market_key,
            []
        ):
            ticker = str(raw_ticker).strip().upper()
            item = index_map.get(ticker)

            if not item:
                continue

            name = item.get("name", ticker)
            ma_list = get_ma_list_from_item(item)

            line_text = captured_index_lines.get(
                ticker
            )

            if line_text is None:
                line_text = analyze(
                    ticker,
                    name,
                    ma_list
                )

            try:
                status = build_index_status(
                    ticker,
                    name,
                    market_key,
                    ma_list,
                    line_text
                )
            except Exception as exc:
                print(
                    f"⚠️ {ticker} 報告指數整理失敗："
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            statuses.append(status)
            world_indices.append(status)
            seen.add(ticker)

        markets.append(
            {
                "key": market_key,
                "name": market_meta["name"],
                "flag": market_meta["flag"],
                "lineText": str(
                    market_line_texts.get(
                        market_key,
                        ""
                    )
                ),
                "indices": statuses
            }
        )

    ticker_market = {
        str(ticker).strip().upper(): market_key
        for market_key, tickers in (
            market_tickers.items()
        )
        for ticker in tickers
    }

    pending = []

    # 其他已啟用、但不在 LINE 清單的 index_configs
    for ticker, item in index_map.items():
        if ticker in seen:
            continue

        pending.append(
            (
                ticker,
                item.get("name", ticker),
                ticker_market.get(
                    ticker,
                    "world"
                ),
                get_ma_list_from_item(item),
                True
            )
        )

    # LINE 清單中 Supabase 未啟用的指數（只供比較）
    for ticker, market_key in ticker_market.items():
        if ticker in seen or ticker in index_map:
            continue

        pending.append(
            (
                ticker,
                REPORT_CORE_INDEX_NAMES.get(
                    ticker,
                    ticker
                ),
                market_key,
                list(REPORT_EXTRA_INDEX_MA_LIST),
                False
            )
        )

    for config in extra_index_configs:
        ticker = str(
            config.get("ticker", "")
        ).strip().upper()

        if not ticker:
            continue

        pending.append(
            (
                ticker,
                config.get("name", ticker),
                config.get("market", "world"),
                list(
                    config.get("ma_list")
                    or REPORT_EXTRA_INDEX_MA_LIST
                ),
                False
            )
        )

    for (
        ticker,
        name,
        market_key,
        ma_list,
        configured
    ) in pending:
        if ticker in seen:
            continue

        seen.add(ticker)

        try:
            line_text = analyze(
                ticker,
                name,
                ma_list
            )

            world_indices.append(
                build_index_status(
                    ticker,
                    name,
                    market_key,
                    ma_list,
                    line_text,
                    configured=configured
                )
            )

        except Exception as exc:
            print(
                f"⚠️ {ticker} 世界指數失敗："
                f"{type(exc).__name__}: {exc}"
            )

    return markets, world_indices


def _chart_last_ma_values(chart_data):
    candles = chart_data.get("candles") or []

    if not candles:
        return {}

    last_time = candles[-1].get("time")
    values = {}

    for ma_config in (
        chart_data.get("moving_averages") or []
    ):
        points = ma_config.get("data") or []

        if (
            not points
            or points[-1].get("time") != last_time
        ):
            continue

        window = safe_int(
            ma_config.get("window")
        )

        if window is None:
            continue

        values[str(window)] = round_report_number(
            points[-1].get("value")
        )

    return values


def _chart_close_change(chart_data):
    candles = chart_data.get("candles") or []

    close_value = safe_float(
        chart_data.get("latest_close"),
        None
    )

    if close_value is None and candles:
        close_value = safe_float(
            candles[-1].get("close"),
            None
        )

    change_pct = None

    if len(candles) >= 2 and close_value is not None:
        previous_close = safe_float(
            candles[-2].get("close"),
            None
        )

        if previous_close:
            change_pct = round(
                (close_value / previous_close - 1)
                * 100,
                2
            )

    as_of = (
        candles[-1].get("time")
        if candles
        else None
    )

    return (
        round_report_number(close_value),
        change_pct,
        as_of
    )


def _strip_title_parentheses(text):
    text = str(text or "").strip()

    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()

    return text


def build_report_group_item(item, market):
    chart_data = item.get("chart_data") or {}

    symbol = str(
        item.get("ticker")
        or chart_data.get("ticker")
        or ""
    ).strip().upper()

    name = str(
        item.get("name")
        or chart_data.get("display_name")
        or ""
    ).strip()

    if not name and (
        symbol.endswith(".TW")
        or symbol.endswith(".TWO")
    ):
        name = get_tw_stock_name(symbol)

    ma_list = [
        int(value)
        for value in (
            item.get("ma_list")
            or [
                ma_config.get("window")
                for ma_config in (
                    chart_data.get(
                        "moving_averages"
                    )
                    or []
                )
                if safe_int(
                    ma_config.get("window")
                )
            ]
            or [20]
        )
    ]

    (
        close_value,
        change_pct,
        as_of
    ) = _chart_close_change(chart_data)

    return {
        "symbol": symbol,
        "name": name,
        "maList": ma_list,
        "close": close_value,
        "changePct": change_pct,
        "maValues": _chart_last_ma_values(
            chart_data
        ),
        "note": _strip_title_parentheses(
            chart_data.get("title_suffix")
        ),
        "volume": int(
            safe_float(item.get("volume"), 0)
        ),
        "asOf": as_of
    }


def build_report_groups(
    data_dict,
    group_metadata,
    display_order
):
    groups = []

    for group_key in display_order:
        if group_key in {"indices", "sectors"}:
            continue

        metadata = group_metadata.get(
            group_key,
            {}
        )

        market = str(
            metadata.get("market")
            or (
                "tw"
                if group_key.startswith("tw_")
                else "us"
                if group_key.startswith("us_")
                else ""
            )
        ).upper()

        if group_key in REPORT_SCAN_GROUP_NAMES:
            kind = "scan"
        elif (
            group_key.startswith("custom_")
            or metadata.get("is_custom")
        ):
            kind = "custom"
        else:
            kind = "fixed"

        name = normalize_group_name(
            metadata.get("name")
            or REPORT_SCAN_GROUP_NAMES.get(
                group_key
            )
            or group_key
        )

        items = []

        for item in data_dict.get(
            group_key,
            []
        ) or []:
            try:
                items.append(
                    build_report_group_item(
                        item,
                        market
                    )
                )
            except Exception as exc:
                print(
                    f"⚠️ {group_key} 報告項目略過："
                    f"{type(exc).__name__}: {exc}"
                )

        group_ma_list = [
            int(value)
            for value in (
                metadata.get("ma_list")
                or [20]
            )
        ]

        groups.append(
            {
                "key": group_key,
                "name": name,
                "market": market,
                "kind": kind,
                "maList": group_ma_list,
                "count": len(items),
                "items": items
            }
        )

    return groups


def build_report_sectors(sector_items):
    sectors = []

    for item in sorted(
        sector_items or [],
        key=lambda value: safe_int(
            value.get("rank"),
            9999
        )
    ):
        chart_data = item.get("chart_data") or {}
        candles = chart_data.get("candles") or []

        sectors.append(
            {
                "symbol": item.get("ticker"),
                "name": item.get("name"),
                "trend": item.get("trend_status"),
                "status": item.get("volume_status"),
                "rank": safe_int(item.get("rank")),
                "changePct": round_report_number(
                    item.get("return_1w")
                ),
                "return4w": round_report_number(
                    item.get("return_4w")
                ),
                "return13w": round_report_number(
                    item.get("return_13w")
                ),
                "relativeStrength13w": (
                    round_report_number(
                        item.get(
                            "relative_strength_13w"
                        )
                    )
                ),
                "momentumScore": round_report_number(
                    item.get("momentum_score")
                ),
                "strength": item.get(
                    "strength_group"
                ),
                "strengthLabel": item.get(
                    "strength_label"
                ),
                "close": round_report_number(
                    item.get("latest_close")
                ),
                "volumeChangePct": round_report_number(
                    item.get("volume_change_pct")
                ),
                "timeframe": "1W",
                "maList": [
                    int(value)
                    for value in (
                        item.get("ma_list")
                        or SECTOR_WEEKLY_MA_LIST
                    )
                ],
                "maValues": _chart_last_ma_values(
                    chart_data
                ),
                "asOf": (
                    candles[-1].get("time")
                    if candles
                    else None
                )
            }
        )

    return sectors


def build_report_payload(
    report_date,
    data_dict,
    group_metadata,
    display_order,
    index_map,
    market_tickers,
    captured_index_lines,
    market_line_texts,
    line_messages,
    generated_at=None,
    extra_index_configs=None,
    analyze=analyze_index_for_report
):
    generated_at = generated_at or (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )

    markets, world_indices = build_report_indices(
        index_map,
        market_tickers,
        captured_index_lines,
        market_line_texts,
        extra_index_configs=extra_index_configs,
        analyze=analyze
    )

    return {
        "version": REPORT_JSON_VERSION,
        "generatedAt": generated_at,
        "reportDate": report_date,
        "markets": markets,
        "worldIndices": world_indices,
        "lineMessages": {
            "index": line_messages.get("index"),
            "sectors": line_messages.get("sectors"),
            "stocks": line_messages.get("stocks")
        },
        "groups": build_report_groups(
            data_dict,
            group_metadata,
            display_order
        ),
        "sectors": build_report_sectors(
            data_dict.get("sectors")
        )
    }


def _json_default(value):
    if hasattr(value, "item"):
        return value.item()

    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")

    return str(value)


def write_json_atomic(path, payload):
    """寫到同目錄暫存檔後 os.replace，避免網站讀到寫到一半的檔案。"""
    directory = os.path.dirname(path) or "."

    os.makedirs(directory, exist_ok=True)

    file_descriptor, tmp_path = tempfile.mkstemp(
        prefix=".tmp-",
        suffix=".json",
        dir=directory
    )

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                clean_json_value(payload),
                file,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                default=_json_default
            )

        os.replace(tmp_path, path)

    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return path


def write_report_json(
    payload,
    report_dir=REPORT_DIR
):
    path = os.path.join(
        report_dir,
        "latest.json"
    )

    write_json_atomic(path, payload)

    print(
        f"✅ Atlas 報告 JSON 已產生：{path}"
        f"（{os.path.getsize(path) / 1024:,.1f} KB）"
    )

    return path


def build_series_payload(
    symbol,
    df,
    name,
    source,
    interval="1D",
    as_of=None
):
    df = clean_ohlcv_dataframe(df)

    bars = []

    for stamp, row in df.iterrows():
        open_value = safe_float(row["Open"], None)
        high_value = safe_float(row["High"], None)
        low_value = safe_float(row["Low"], None)
        close_value = safe_float(row["Close"], None)

        if (
            None in (
                open_value,
                high_value,
                low_value,
                close_value
            )
            or min(
                open_value,
                high_value,
                low_value,
                close_value
            ) <= 0
        ):
            continue

        volume = safe_float(row["Volume"], 0.0)

        bars.append(
            {
                "time": int(
                    pd.Timestamp(
                        stamp.year,
                        stamp.month,
                        stamp.day,
                        tz="UTC"
                    ).timestamp()
                ),
                "open": round_report_number(
                    open_value,
                    4
                ),
                "high": round_report_number(
                    max(
                        high_value,
                        open_value,
                        close_value
                    ),
                    4
                ),
                "low": round_report_number(
                    min(
                        low_value,
                        open_value,
                        close_value
                    ),
                    4
                ),
                "close": round_report_number(
                    close_value,
                    4
                ),
                "volume": (
                    int(volume)
                    if float(volume).is_integer()
                    else round(volume, 2)
                )
            }
        )

    return {
        "symbol": symbol,
        "name": name,
        "source": source,
        "interval": interval,
        "asOf": int(
            as_of
            if as_of is not None
            else time.time()
        ),
        "bars": bars
    }


def write_report_series(
    symbol,
    df,
    name,
    source,
    interval="1D",
    series_dir=REPORT_SERIES_DIR,
    as_of=None
):
    """
    寫出 docs/report/series/<SYMBOL>.json（原始代號，例如 ^TWOII.json），
    並更新 series/index.json 的 symbols 清單。
    """
    payload = build_series_payload(
        symbol,
        df,
        name,
        source,
        interval=interval,
        as_of=as_of
    )

    if not payload["bars"]:
        print(
            f"⚠️ {symbol} 沒有可用 K 線，"
            "不寫出 series JSON"
        )
        return None

    path = os.path.join(
        series_dir,
        f"{symbol}.json"
    )

    write_json_atomic(path, payload)

    index_path = os.path.join(
        series_dir,
        "index.json"
    )

    symbols = {symbol}

    try:
        with open(
            index_path,
            encoding="utf-8"
        ) as file:
            symbols.update(
                json.load(file).get(
                    "symbols",
                    []
                )
            )
    except (OSError, ValueError, AttributeError):
        pass

    symbols = sorted(
        value
        for value in symbols
        if isinstance(value, str)
        and os.path.exists(
            os.path.join(
                series_dir,
                f"{value}.json"
            )
        )
    )

    write_json_atomic(
        index_path,
        {"symbols": symbols}
    )

    print(
        f"✅ series JSON 已產生：{path}"
        f"（{len(payload['bars'])} 根）"
    )

    return path


def write_tpex_otc_series():
    history = get_tpex_otc_full_history()

    if history.empty:
        print(
            "⚠️ 沒有 TPEx 櫃買指數歷史，"
            "略過 series JSON"
        )
        return None

    return write_report_series(
        TPEX_OTC_SYMBOL,
        history,
        TPEX_OTC_NAME,
        TPEX_SOURCE_LABEL
    )


# =========================================================================
# 主程式共用工具
# =========================================================================
def process_group_for_data_dict(
    data_dict,
    user_configs,
    group_metadata,
    group_key
):
    group_dict = user_configs.get(
        group_key,
        {}
    )

    metadata = group_metadata.get(
        group_key,
        {}
    )

    data_dict[group_key] = (
        process_custom_groups(
            group_dict,
            group_key,
            test_mode=(
                CUSTOM_GROUP_TEST_MODE
            ),
            group_metadata=metadata
        )
    )


def build_index_map(index_configs):
    index_map = {}

    for item in index_configs:
        if not is_config_enabled(item):
            continue

        ticker = str(
            item.get("ticker", "")
        ).strip().upper()

        if not ticker:
            continue

        if ticker in index_map:
            print(
                "⚠️ index_configs "
                f"出現重複 ticker：{ticker}，"
                "將使用最後一筆"
            )

        index_map[ticker] = item

    return index_map


INDEX_SHORT_NAMES = {
    "^TWII": "加權",
    "^TWOII": "櫃買",
    "^GSPC": "標普500",
    "^DJI": "道瓊",
    "^IXIC": "那斯達克",
    "^RUT": "羅素2000",
    "^SOX": "費半",
    "^FCHI": "CAC40",
    "^FTSE": "富時100",
    "^GDAXI": "DAX",
    "^N225": "日經225",
    "^KS11": "KOSPI"
}

INDEX_LINE_LEGEND = (
    "均線：🔺多 🔻空 ➖不明｜"
    "道氏：🔺多 🔻空 💡大多小空 ⚡大空小多"
)


def build_index_line_short(
    ticker,
    name,
    index_line
):
    """
    LINE 用的單行大盤摘要：「簡稱 均線符號 道氏符號」。
    只依 analyze_index_trend 的文字解析，不改變網頁使用的原文。
    """
    short_name = INDEX_SHORT_NAMES.get(
        ticker,
        str(name or ticker)
    )

    parsed = parse_index_trend_text(
        index_line
    )

    score = parsed.get("score")

    if (
        score is None
        and parsed.get("macroTrend") is None
    ):
        return f"{short_name} ⚪分析異常"

    if score is None or score == 0:
        ma_symbol = "➖"
    elif score > 0:
        ma_symbol = "🔺"
    else:
        ma_symbol = "🔻"

    macro = parsed.get("macroTrend")
    micro = parsed.get("microTrend")

    if macro == "bull" and micro == "bull":
        dow_symbol = "🔺"
    elif macro == "bear" and micro == "bear":
        dow_symbol = "🔻"
    elif macro == "bull":
        dow_symbol = "💡"
    elif macro == "bear":
        dow_symbol = "⚡"
    else:
        dow_symbol = "➖"

    return f"{short_name} {ma_symbol}{dow_symbol}"


def append_index_market_report(
    lines,
    market_title,
    tickers,
    index_map,
    captured_lines=None,
    short_lines=None
):
    """
    將單一市場的指數分析加入 lines（完整文字，Atlas 報告 JSON 使用）。

    captured_lines（選用 dict）會收到「ticker → 該指數的分析文字」，
    供 Atlas 報告 JSON 使用；傳入與否都不會改變 lines 的內容。
    short_lines（選用 list）會收到 LINE 用的精簡單行摘要。
    回傳本次加入 lines 的區塊（list）。
    """
    block_start = len(lines)

    lines.append(market_title)

    if short_lines is not None:
        short_lines.append(market_title)

    found_count = 0

    for ticker in tickers:
        normalized_ticker = str(
            ticker
        ).strip().upper()

        item = index_map.get(
            normalized_ticker
        )

        if not item:
            continue

        found_count += 1

        index_line = analyze_index_trend(
            normalized_ticker,
            item.get(
                "name",
                normalized_ticker
            ),
            get_ma_list_from_item(
                item
            )
        )

        if captured_lines is not None:
            captured_lines[
                normalized_ticker
            ] = index_line

        lines.append(index_line)

        if short_lines is not None:
            short_lines.append(
                build_index_line_short(
                    normalized_ticker,
                    item.get(
                        "name",
                        normalized_ticker
                    ),
                    index_line
                )
            )

    if found_count == 0:
        lines.append(
            "⚪ 此市場目前沒有啟用的指數設定"
        )

        if short_lines is not None:
            short_lines.append(
                "⚪ 沒有啟用的指數設定"
            )

    lines.append("")

    if short_lines is not None:
        short_lines.append("")

    return lines[block_start:]


# =========================================================================
# 主程式
# =========================================================================
def main():
    access_token = os.environ.get(
        "LINE_ACCESS_TOKEN"
    )

    # LINE Messaging API 推播使用者 ID
    line_push_user_id = os.environ.get(
        "LINE_USER_ID"
    )

    # Supabase 股票名單查詢使用者 ID
    # 若未設定，才退回使用 LINE_USER_ID
    supabase_user_id = (
        os.environ.get(
            "SUPABASE_USER_ID"
        )
        or line_push_user_id
    )

    if not line_push_user_id:
        print(
            "❌ 未設定 LINE_USER_ID"
        )
        return

    if not supabase_user_id:
        print(
            "❌ 未設定 SUPABASE_USER_ID"
        )
        return

    line_push_user_id = str(
        line_push_user_id
    ).strip()

    supabase_user_id = str(
        supabase_user_id
    ).strip()

    print(
        "👤 LINE 推播使用：LINE_USER_ID"
    )

    if os.environ.get(
        "SUPABASE_USER_ID"
    ):
        print(
            "👤 Supabase 查詢使用："
            "SUPABASE_USER_ID"
        )
    else:
        print(
            "⚠️ 未設定 SUPABASE_USER_ID，"
            "Supabase 查詢將使用 LINE_USER_ID"
        )

    today = datetime.now()
    today_str = today.strftime(
        "%Y-%m-%d"
    )
    weekday = today.weekday()

    # -----------------------------------------------------------------
    # 讀取 Supabase 股票群組、動態群組與指數參數
    # -----------------------------------------------------------------
    (
        user_configs,
        db_index_configs,
        group_metadata
    ) = load_configs_from_supabase(
        supabase_user_id
    )

    # -----------------------------------------------------------------
    # 台股全市場
    # -----------------------------------------------------------------
    print(
        "📊 開始掃描台股全市場"
    )

    tw_tickers = get_tw_tickers(
        TW_MIN_VOLUME
    )

    tw_all = scan_market(
        tw_tickers,
        min_volume=TW_MIN_VOLUME
    )

    # -----------------------------------------------------------------
    # 美股全市場
    # -----------------------------------------------------------------
    print(
        "📊 開始掃描美股全市場"
    )

    us_tickers = get_us_tickers()

    us_all = scan_market(
        us_tickers,
        min_volume=US_MIN_VOLUME
    )

    # -----------------------------------------------------------------
    # 櫃買指數（^TWOII）：更新櫃買中心官方日資料
    # 失敗時 ^TWOII 會自動退回原本的 Yahoo 流程
    # -----------------------------------------------------------------
    print(
        "🏦 開始更新櫃買指數（TPEx）"
    )

    try:
        update_tpex_otc_index_history()
    except Exception as exc:
        print(
            "❌ TPEx 櫃買指數更新失敗："
            f"{type(exc).__name__}: {exc}"
        )

    # -----------------------------------------------------------------
    # 全球大盤圖表
    # 使用 Supabase index_configs 的均線設定
    # -----------------------------------------------------------------
    print(
        "🌍 開始建立全球指數圖表"
    )

    index_charts = process_index_charts(
        db_index_configs
    )

    # -----------------------------------------------------------------
    # 美股 11 大類股週 K
    # -----------------------------------------------------------------
    print(
        "🧭 開始建立類股週 K 圖表"
    )

    (
        sector_charts,
        sector_summary
    ) = process_sector_weekly_charts()

    # -----------------------------------------------------------------
    # 建立固定分類
    # -----------------------------------------------------------------
    data_dict = {
        "indices": index_charts,
        "sectors": sector_charts,
        "tw_all": tw_all,
        "us_all": us_all
    }

    fixed_group_keys = [
        "tw_g1",
        "tw_g2",
        "us_g1",
        "us_g2",
        "us_g3",
        "us_g4"
    ]

    for group_key in fixed_group_keys:
        process_group_for_data_dict(
            data_dict,
            user_configs,
            group_metadata,
            group_key
        )

    # -----------------------------------------------------------------
    # 動態自訂群組
    #
    # groups.id = 22
    # → group_key = custom_22
    # → stocks.group_id = custom_22
    # -----------------------------------------------------------------
    custom_group_keys = sorted(
        [
            group_key
            for group_key in user_configs
            if group_key.startswith(
                "custom_"
            )
        ],
        key=get_custom_group_number
    )

    for group_key in custom_group_keys:
        process_group_for_data_dict(
            data_dict,
            user_configs,
            group_metadata,
            group_key
        )

    # -----------------------------------------------------------------
    # 最終圖表數量
    # -----------------------------------------------------------------
    print(
        "\n===== 最終圖表數量 ====="
    )

    display_order = [
        "indices",
        "sectors",
        "tw_all",
        "tw_g1",
        "tw_g2"
    ]

    display_order.extend(
        get_sorted_custom_group_keys(
            group_metadata,
            market="tw"
        )
    )

    display_order.extend(
        [
            "us_all",
            "us_g1",
            "us_g2",
            "us_g3",
            "us_g4"
        ]
    )

    display_order.extend(
        get_sorted_custom_group_keys(
            group_metadata,
            market="us"
        )
    )

    display_order = list(
        dict.fromkeys(
            display_order
        )
    )

    for key in display_order:
        items = data_dict.get(
            key,
            []
        )

        metadata = group_metadata.get(
            key,
            {}
        )

        group_name = metadata.get(
            "name",
            key
        )

        print(
            f"{key}: "
            f"{len(items)} 檔 / "
            f"{group_name}"
        )

    print(
        "========================\n"
    )

    # -----------------------------------------------------------------
    # 報告網址（LINE 訊息不再附連結，只留給網頁報告 JSON 使用）
    # -----------------------------------------------------------------
    report_url = REPORT_LIFF_URL

    # -----------------------------------------------------------------
    # LINE 個股與網頁報告推播
    #
    # 依需求：動態自訂群組不加入 LINE 統計，
    # 避免未來新增大量群組後訊息過長。
    # -----------------------------------------------------------------
    def stock_count(group_key):
        return len(data_dict.get(group_key, []))

    line_message_stocks = (
        f"🎯 {today_str} 量化日報\n"
        "🇹🇼 符合檔數："
        f"全市場 {stock_count('tw_all')}"
        f"｜權值 {stock_count('tw_g1')}"
        f"｜熱門 {stock_count('tw_g2')}\n"
        "🇺🇸 符合檔數："
        f"全市場 {stock_count('us_all')}"
        f"｜權值 {stock_count('us_g1')}"
        f"｜低本益 {stock_count('us_g2')}"
        f"｜績效 {stock_count('us_g3')}"
        f"｜熱門 {stock_count('us_g4')}"
    )

    send_line_message(
        line_message_stocks,
        access_token,
        line_push_user_id
    )

    # -----------------------------------------------------------------
    # 原本的大盤分類與趨勢推播
    # -----------------------------------------------------------------
    tw_indices = [
        "^TWII",
        "^TWOII"
    ]

    us_indices = [
        "^GSPC",
        "^DJI",
        "^IXIC",
        "^RUT",
        "^SOX"
    ]

    eu_indices = [
        "^FCHI",
        "^FTSE",
        "^GDAXI"
    ]

    asia_indices = [
        "^N225",
        "^KS11"
    ]

    index_map = build_index_map(
        db_index_configs
    )

    # 每個指數的 LINE 分析文字，供 Atlas 報告 JSON 使用
    captured_index_lines = {}

    index_lines = [
        (
            f"🌍 {today_str} "
            "全球大盤多空量化報告"
        ),
        (
            "📊 評分標準: "
            "均線糾纏自適應/"
            "0.1%過濾機制"
        ),
        "========================",
        ""
    ]

    # LINE 只送精簡版；index_lines 的完整文字留給網頁（Atlas 報告 JSON）
    line_index_lines = [
        f"🌍 {today_str} 全球大盤多空",
        ""
    ]

    tw_index_block = append_index_market_report(
        index_lines,
        "【 🇹🇼 台灣市場 】",
        tw_indices,
        index_map,
        captured_lines=captured_index_lines,
        short_lines=line_index_lines
    )

    us_index_block = append_index_market_report(
        index_lines,
        "【 🇺🇸 美國市場 】",
        us_indices,
        index_map,
        captured_lines=captured_index_lines,
        short_lines=line_index_lines
    )

    eu_index_block = append_index_market_report(
        index_lines,
        "【 🇪🇺 歐洲市場 】",
        eu_indices,
        index_map,
        captured_lines=captured_index_lines,
        short_lines=line_index_lines
    )

    append_index_market_report(
        index_lines,
        "【 🌏 亞洲市場 】",
        asia_indices,
        index_map,
        captured_lines=captured_index_lines,
        short_lines=line_index_lines
    )

    index_message = "\n".join(
        index_lines
    ).rstrip()

    line_index_lines.append(
        INDEX_LINE_LEGEND
    )

    send_line_message(
        "\n".join(
            line_index_lines
        ),
        access_token,
        line_push_user_id
    )

    # -----------------------------------------------------------------
    # 每週一限定類股週線輪動摘要
    # -----------------------------------------------------------------
    sectors_message = None

    if weekday == 0:
        sectors_message = (
            build_sector_monday_message(
                today_str,
                sector_summary,
                report_url
            )
        )

        # LINE 送精簡版；sectors_message 的完整文字留給網頁（Atlas 報告 JSON）
        send_line_message(
            build_sector_line_message(
                today_str,
                sector_summary
            ),
            access_token,
            line_push_user_id
        )

    # -----------------------------------------------------------------
    # Atlas 報告 JSON（docs/report/latest.json）
    # 失敗只記錄，不影響 CSV 清理與推送
    # -----------------------------------------------------------------
    try:
        report_payload = build_report_payload(
            today_str,
            data_dict,
            group_metadata,
            display_order,
            index_map,
            {
                "tw": tw_indices,
                "jp": [
                    ticker
                    for ticker in asia_indices
                    if ticker == "^N225"
                ],
                "kr": [
                    ticker
                    for ticker in asia_indices
                    if ticker == "^KS11"
                ],
                "eu": eu_indices,
                "us": us_indices
            },
            captured_index_lines,
            {
                "tw": "\n".join(
                    tw_index_block
                ).rstrip(),
                "jp": captured_index_lines.get(
                    "^N225",
                    ""
                ),
                "kr": captured_index_lines.get(
                    "^KS11",
                    ""
                ),
                "eu": "\n".join(
                    eu_index_block
                ).rstrip(),
                "us": "\n".join(
                    us_index_block
                ).rstrip()
            },
            {
                "index": index_message,
                "sectors": sectors_message,
                "stocks": line_message_stocks
            }
        )

        write_report_json(report_payload)

    except Exception as exc:
        print(
            "❌ Atlas 報告 JSON 產生失敗："
            f"{type(exc).__name__}: {exc}"
        )

    try:
        write_tpex_otc_series()
    except Exception as exc:
        print(
            "❌ 櫃買指數 series JSON 產生失敗："
            f"{type(exc).__name__}: {exc}"
        )

    # -----------------------------------------------------------------
    # 推送最新報告 JSON 與 CSV 到 GitHub Pages
    # -----------------------------------------------------------------
    prune_stale_csv()
    push_report_to_github()


if __name__ == "__main__":
    main()


# =========================================================================
# 完整 main.py 結束
# =========================================================================

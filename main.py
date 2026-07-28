# =========================================================================
# main.py 完整整合版
# 第 1 部分：Imports、全域設定、共用工具、Supabase 動態群組
# =========================================================================

import io
import json
import os
import re
import subprocess
import time
from datetime import datetime

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

SUPABASE_PROJECT_REF = "bxhqpfeberqbtxymghyt"

DRAWING_SYNC_URL = (
    "https://"
    f"{SUPABASE_PROJECT_REF}"
    ".supabase.co/functions/v1/chart-drawings"
)

# 下列分類不提供人工畫線，避免大量全市場圖表造成負擔
DRAWING_DISABLED_MARKETS = {
    "tw_all",
    "us_all"
}

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


def format_signed_percent(value):
    value = safe_float(value)

    return f"{value:+.2f}%"


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

    api_key = (
        os.environ.get(
            "SUPABASE_SERVICE_ROLE_KEY"
        )
        or os.environ.get(
            "SUPABASE_ANON_KEY"
        )
        or ""
    ).strip()

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
    show_volume=False,
    display_name=None,
    timeframe_label="日K"
):
    display_name = str(
        display_name or ""
    ).strip()

    display_ticker = str(ticker)

    if display_name:
        display_ticker = (
            f"{ticker}　{display_name}"
        )

    timeframe_label = str(
        timeframe_label or "日K"
    ).strip()

    date_strings = [
        str(date)[:10]
        for date in df_chart.index
    ]

    open_values = [
        safe_float(value)
        for value in df_chart[
            "Open"
        ].tolist()
    ]

    high_values = [
        safe_float(value)
        for value in df_chart[
            "High"
        ].tolist()
    ]

    low_values = [
        safe_float(value)
        for value in df_chart[
            "Low"
        ].tolist()
    ]

    close_values = [
        safe_float(value)
        for value in df_chart[
            "Close"
        ].tolist()
    ]

    volume_values = [
        safe_float(value)
        for value in df_chart[
            "Volume"
        ].tolist()
    ]

    ohlcv_records = []

    for (
        date_value,
        open_value,
        high_value,
        low_value,
        close_value,
        volume_value
    ) in zip(
        date_strings,
        open_values,
        high_values,
        low_values,
        close_values,
        volume_values
    ):
        ohlcv_records.append(
            {
                "date": date_value,
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "volume": volume_value,
                "timeframe": timeframe_label
            }
        )

    traces = [
        {
            "type": "candlestick",
            "name": display_ticker,
            "x": date_strings,
            "open": open_values,
            "high": high_values,
            "low": low_values,
            "close": close_values,
            "yaxis": "y",
            "increasing": {
                "line": {
                    "color": "#ef5350",
                    "width": 1
                },
                "fillcolor": "#ef5350"
            },
            "decreasing": {
                "line": {
                    "color": "#26a69a",
                    "width": 1
                },
                "fillcolor": "#26a69a"
            },
            "hoverlabel": {
                "bgcolor": "#1e222d",
                "bordercolor": "#3b4252",
                "font": {
                    "color": "#ffffff"
                }
            }
        }
    ]

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
        ma_col = f"MA{ma_window}"

        if ma_col not in df_chart.columns:
            continue

        ma_values = [
            (
                None
                if pd.isna(value)
                else float(value)
            )
            for value in df_chart[
                ma_col
            ].tolist()
        ]

        if not any(
            value is not None
            for value in ma_values
        ):
            continue

        color = colors[
            index % len(colors)
        ]

        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": ma_col,
                "x": date_strings,
                "y": ma_values,
                "yaxis": "y",
                "line": {
                    "color": color,
                    "width": 1.8
                },
                "hovertemplate": (
                    f"{ma_col}: %{{y:.2f}}"
                    "<extra></extra>"
                )
            }
        )

    if show_volume:
        volume_colors = []

        for open_price, close_price in zip(
            open_values,
            close_values
        ):
            if close_price >= open_price:
                volume_colors.append(
                    "rgba(239,83,80,0.62)"
                )
            else:
                volume_colors.append(
                    "rgba(38,166,154,0.62)"
                )

        traces.append(
            {
                "type": "bar",
                "name": "成交量",
                "x": date_strings,
                "y": volume_values,
                "yaxis": "y2",
                "marker": {
                    "color": volume_colors,
                    "line": {
                        "width": 0
                    }
                },
                "opacity": 0.85,
                "hovertemplate": (
                    "成交量: %{y:,.0f}"
                    "<extra></extra>"
                )
            }
        )

    if show_volume:
        price_domain = [0.27, 1.0]
        volume_domain = [0.0, 0.19]
        chart_height = 520
        bottom_margin = 42
    else:
        price_domain = [0.0, 1.0]
        volume_domain = None
        chart_height = 440
        bottom_margin = 35

    layout = {
        "title": {
            "text": (
                f"<b>{display_ticker}</b>"
                "　"
                "<span style='font-size:12px;"
                "color:#c084fc'>"
                f"{timeframe_label}"
                "</span>"
                "<br>"
                "<span style='font-size:12px;"
                "color:#9ca3af'>"
                f"{title_suffix}"
                "</span>"
            ),
            "x": 0.02,
            "xanchor": "left",
            "y": 0.97,
            "yanchor": "top",
            "font": {
                "size": 18,
                "color": "#f8fafc"
            }
        },
        "paper_bgcolor": "#131722",
        "plot_bgcolor": "#131722",
        "font": {
            "family": (
                "Arial, "
                "'Noto Sans TC', "
                "sans-serif"
            ),
            "color": "#d1d4dc"
        },
        "xaxis": {
            "type": "category",
            "categoryorder": "array",
            "categoryarray": date_strings,
            "anchor": (
                "y2"
                if show_volume
                else "y"
            ),
            "rangeslider": {
                "visible": False
            },
            "fixedrange": False,
            "showgrid": True,
            "gridcolor": (
                "rgba(255,255,255,0.055)"
            ),
            "gridwidth": 1,
            "showline": False,
            "zeroline": False,
            "tickfont": {
                "size": 10,
                "color": "#8b949e"
            },
            "tickangle": 0,
            "nticks": 12,
            "spikemode": "across",
            "spikesnap": "cursor",
            "showspikes": True,
            "spikecolor": "#64748b",
            "spikethickness": 1
        },
        "yaxis": {
            "domain": price_domain,
            "side": "right",
            "anchor": "x",
            "fixedrange": False,
            "showgrid": True,
            "gridcolor": (
                "rgba(255,255,255,0.055)"
            ),
            "gridwidth": 1,
            "showline": False,
            "zeroline": False,
            "tickfont": {
                "size": 10,
                "color": "#8b949e"
            },
            "tickformat": ",.2f",
            "automargin": True,
            "spikemode": "across",
            "spikesnap": "cursor",
            "showspikes": True,
            "spikecolor": "#64748b",
            "spikethickness": 1
        },
        "legend": {
            "orientation": "h",
            "x": 0.01,
            "y": 1.02,
            "xanchor": "left",
            "yanchor": "bottom",
            "font": {
                "size": 11,
                "color": "#cbd5e1"
            },
            "bgcolor": "rgba(0,0,0,0)"
        },
        "hovermode": "x unified",
        "clickmode": "event+select",
        "hoverlabel": {
            "bgcolor": "#1e222d",
            "bordercolor": "#374151",
            "font": {
                "color": "#ffffff",
                "size": 12
            }
        },
        "margin": {
            "l": 18,
            "r": 68,
            "t": 110,
            "b": bottom_margin
        },
        "height": chart_height,
        "dragmode": "pan",
        "bargap": 0.15,
        "uirevision": ticker,
        "newshape": {
            "line": {
                "color": "#facc15",
                "width": 2
            },
            "fillcolor": (
                "rgba(250,204,21,0.12)"
            ),
            "opacity": 0.9
        }
    }

    if show_volume:
        layout["yaxis2"] = {
            "domain": volume_domain,
            "side": "right",
            "anchor": "x",
            "fixedrange": False,
            "showgrid": False,
            "showline": False,
            "zeroline": False,
            "rangemode": "tozero",
            "tickfont": {
                "size": 9,
                "color": "#64748b"
            },
            "tickformat": ".2s",
            "automargin": True,
            "title": {
                "text": "成交量",
                "font": {
                    "size": 10,
                    "color": "#64748b"
                }
            }
        }

        layout["shapes"] = [
            {
                "type": "line",
                "xref": "paper",
                "yref": "paper",
                "x0": 0,
                "x1": 1,
                "y0": 0.225,
                "y1": 0.225,
                "editable": False,
                "line": {
                    "color": (
                        "rgba(148,163,184,0.20)"
                    ),
                    "width": 1
                },
                "name": (
                    "__volume_separator__"
                )
            }
        ]

    drawing_timeframe = (
        "1w"
        if timeframe_label == "週K"
        else "1d"
    )

    return {
        "data": traces,
        "layout": layout,
        "ohlcv": ohlcv_records,
        "timeframe": timeframe_label,
        "drawing_timeframe": (
            drawing_timeframe
        )
    }


# =========================================================================
# 全市場掃描
# =========================================================================
def download_market_data(
    tickers,
    period
):
    try:
        return yf.download(
            tickers,
            period=period,
            progress=False,
            threads=False,
            auto_adjust=False,
            group_by="column"
        )

    except Exception as exc:
        print(
            "❌ 全市場批次下載失敗："
            f"{type(exc).__name__}: {exc}"
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

    for ticker in tickers:
        csv_path = os.path.join(
            DATA_DIR,
            f"{ticker}.csv"
        )

        if os.path.exists(csv_path):
            need_update.append(ticker)
        else:
            need_init.append(ticker)

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

        if downloaded.empty:
            continue

        for ticker in chunk:
            try:
                df = extract_yfinance_data(
                    downloaded,
                    ticker
                )

                df = clean_ohlcv_dataframe(
                    df
                )

                if df.empty:
                    continue

                df.tail(
                    MAX_DAYS
                ).to_csv(
                    os.path.join(
                        DATA_DIR,
                        f"{ticker}.csv"
                    )
                )

            except Exception as exc:
                print(
                    f"⚠️ {ticker} "
                    f"初始化失敗：{exc}"
                )

    for start in range(
        0,
        len(need_update),
        chunk_size
    ):
        chunk = need_update[
            start:start + chunk_size
        ]

        downloaded = download_market_data(
            chunk,
            "5d"
        )

        if downloaded.empty:
            continue

        for ticker in chunk:
            try:
                today_data = (
                    extract_yfinance_data(
                        downloaded,
                        ticker
                    )
                )

                today_data = (
                    clean_ohlcv_dataframe(
                        today_data
                    )
                )

                if today_data.empty:
                    continue

                csv_path = os.path.join(
                    DATA_DIR,
                    f"{ticker}.csv"
                )

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

                combined = pd.concat(
                    [
                        local_data,
                        today_data
                    ]
                )

                combined = combined[
                    ~combined.index.duplicated(
                        keep="last"
                    )
                ].sort_index().tail(
                    MAX_DAYS
                )

                combined.to_csv(csv_path)

            except Exception as exc:
                print(
                    f"⚠️ {ticker} "
                    f"更新失敗：{exc}"
                )

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

            df = clean_ohlcv_dataframe(df)

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

            ma20 = float(ma20_value)

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
                df.tail(60),
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
        try:
            print(
                f"⬇️ 嘗試下載：{candidate}"
            )

            downloaded = yf.download(
                candidate,
                period="2y",
                progress=False,
                threads=False,
                auto_adjust=False,
                group_by="column"
            )

            df = extract_yfinance_data(
                downloaded,
                candidate
            )

            df = clean_ohlcv_dataframe(df)

            if df.empty:
                print(
                    f"⚠️ {candidate} "
                    "下載結果為空"
                )
                continue

            print(
                f"✅ {candidate} 下載成功，"
                f"共 {len(df)} 筆"
            )

            return candidate, df

        except Exception as exc:
            print(
                f"❌ {candidate} 下載失敗："
                f"{type(exc).__name__}: {exc}"
            )

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
                combined.tail(60),
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

            downloaded = yf.download(
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
# 保留原本的：
# 1. 均線糾纏自適應
# 2. 0.1% 過濾
# 3. 三年高點
# 4. 波峰與波谷
# 5. 大趨勢與小走勢
# =========================================================================
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
        df = yf.download(
            ticker,
            period="4y",
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
        # 三年高點與距今月份
        # -------------------------------------------------------------
        df_3y = df.tail(252 * 3)

        idx_3y_high = (
            df_3y["High"].idxmax()
        )

        latest_date = df.index[-1]

        months_since_high = (
            latest_date - idx_3y_high
        ).days / 30.0

        # -------------------------------------------------------------
        # 近 120 日波峰與波谷
        # -------------------------------------------------------------
        df_recent = df.tail(120).copy()

        peaks = []
        troughs = []

        for index in range(
            2,
            len(df_recent) - 2
        ):
            current_high = safe_float(
                df_recent[
                    "High"
                ].iloc[index]
            )

            if (
                current_high
                > safe_float(
                    df_recent[
                        "High"
                    ].iloc[index - 1]
                )
                and current_high
                > safe_float(
                    df_recent[
                        "High"
                    ].iloc[index - 2]
                )
                and current_high
                > safe_float(
                    df_recent[
                        "High"
                    ].iloc[index + 1]
                )
                and current_high
                > safe_float(
                    df_recent[
                        "High"
                    ].iloc[index + 2]
                )
            ):
                peaks.append(
                    (
                        df_recent.index[index],
                        current_high
                    )
                )

            current_low = safe_float(
                df_recent[
                    "Low"
                ].iloc[index]
            )

            if (
                current_low
                < safe_float(
                    df_recent[
                        "Low"
                    ].iloc[index - 1]
                )
                and current_low
                < safe_float(
                    df_recent[
                        "Low"
                    ].iloc[index - 2]
                )
                and current_low
                < safe_float(
                    df_recent[
                        "Low"
                    ].iloc[index + 1]
                )
                and current_low
                < safe_float(
                    df_recent[
                        "Low"
                    ].iloc[index + 2]
                )
            ):
                troughs.append(
                    (
                        df_recent.index[index],
                        current_low
                    )
                )

        lower_peak_count = 0
        lower_trough_count = 0

        for index in range(
            1,
            len(peaks)
        ):
            if (
                peaks[index][1]
                < peaks[index - 1][1]
            ):
                lower_peak_count += 1

        for index in range(
            1,
            len(troughs)
        ):
            if (
                troughs[index][1]
                < troughs[index - 1][1]
            ):
                lower_trough_count += 1

        # -------------------------------------------------------------
        # 大趨勢判斷
        # -------------------------------------------------------------
        macro_trend = "多頭趨勢"

        if months_since_high >= 4.0:
            df_bear_period = df.loc[
                idx_3y_high:latest_date
            ]

            if len(df_bear_period) > 5:
                bear_low = safe_float(
                    df_bear_period[
                        "Low"
                    ]
                    .iloc[:-1]
                    .min()
                )

                if (
                    safe_float(
                        latest["Close"]
                    )
                    < bear_low
                ):
                    macro_trend = (
                        "空頭趨勢"
                    )

        # -------------------------------------------------------------
        # 小走勢判斷
        # -------------------------------------------------------------
        micro_trend = "多頭走勢"

        if (
            months_since_high >= 1.0
            and (
                lower_peak_count
                + lower_trough_count
            ) >= 2
        ):
            micro_trend = "空頭走勢"

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
# 第 4 部分：HTML、動態群組頁籤、圖表與畫線工具介面
# =========================================================================

import html as html_module


# =========================================================================
# HTML 共用工具
# =========================================================================
def escape_html(value):
    return html_module.escape(
        str(value or ""),
        quote=True
    )


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


def is_drawing_enabled_market(
    market_key
):
    return (
        market_key
        not in DRAWING_DISABLED_MARKETS
    )


# =========================================================================
# 類股週線總覽 HTML
# =========================================================================
def generate_sector_overview_html(
    sector_summary
):
    if not sector_summary:
        return """
<div class="sector-overview">
    <div class="sector-overview-title">
        🧭 類股週線強弱總覽
    </div>

    <div class="no-data">
        類股強弱資料不足
    </div>
</div>
"""

    groups = [
        (
            "strong",
            "🟢 強勢領先",
            [
                item
                for item in sector_summary
                if item.get(
                    "strength_group"
                ) == "strong"
            ]
        ),
        (
            "neutral",
            "🟡 中性輪動",
            [
                item
                for item in sector_summary
                if item.get(
                    "strength_group"
                ) == "neutral"
            ]
        ),
        (
            "weak",
            "🔴 弱勢落後",
            [
                item
                for item in sector_summary
                if item.get(
                    "strength_group"
                ) == "weak"
            ]
        )
    ]

    result = """
<div class="sector-overview">
    <div class="sector-overview-title">
        🧭 美股 11 大類股週線強弱總覽
    </div>

    <div class="sector-overview-subtitle">
        固定週均線 MA20／MA60，
        排名綜合 1 週、4 週、13 週報酬
        與相對 SPY 強弱
    </div>

    <div class="sector-summary-grid">
"""

    for (
        group_class,
        group_title,
        items
    ) in groups:
        result += (
            '<div class="sector-summary-column '
            f'{escape_html(group_class)}">'
            '<div class="sector-summary-title">'
            f'{escape_html(group_title)}'
            '</div>'
        )

        if not items:
            result += (
                '<div class="sector-summary-empty">'
                '目前沒有類股'
                '</div>'
            )

        for item in items:
            relative_strength = safe_float(
                item.get(
                    "relative_strength_13w"
                )
            )

            relative_class = (
                "positive"
                if relative_strength >= 0
                else "negative"
            )

            rank = safe_int(
                item.get("rank"),
                0
            )

            ticker = escape_html(
                item.get("ticker")
            )

            name = escape_html(
                item.get("name")
            )

            return_1w = safe_float(
                item.get("return_1w")
            )

            return_4w = safe_float(
                item.get("return_4w")
            )

            return_13w = safe_float(
                item.get("return_13w")
            )

            trend_status = escape_html(
                item.get("trend_status")
            )

            volume_status = escape_html(
                item.get("volume_status")
            )

            result += (
                '<div class="sector-summary-item">'

                '<div class="sector-summary-head">'

                '<span class="sector-rank">'
                f'#{rank}'
                '</span>'

                '<span class="sector-symbol">'
                f'{ticker}'
                '</span>'

                '<span class="sector-name">'
                f'{name}'
                '</span>'

                '</div>'

                '<div class="sector-summary-metrics">'

                '<span>'
                f'1週 {return_1w:+.2f}%'
                '</span>'

                '<span>'
                f'4週 {return_4w:+.2f}%'
                '</span>'

                '<span>'
                f'13週 {return_13w:+.2f}%'
                '</span>'

                f'<span class="{relative_class}">'
                '相對SPY '
                f'{relative_strength:+.2f}%'
                '</span>'

                '</div>'

                '<div class="sector-summary-status">'
                f'{trend_status}｜{volume_status}'
                '</div>'

                '</div>'
            )

        result += "</div>"

    result += """
    </div>
</div>
"""

    return result


# =========================================================================
# 動態群組頁籤按鈕
# =========================================================================
def generate_custom_group_buttons_html(
    data_dict,
    group_metadata,
    market
):
    group_keys = (
        get_sorted_custom_group_keys(
            group_metadata,
            market=market
        )
    )

    if not group_keys:
        return ""

    result = """
<div class="custom-tabs-divider">
    <span>自訂群組</span>
</div>
"""

    for group_key in group_keys:
        metadata = group_metadata.get(
            group_key,
            {}
        )

        group_name = normalize_group_name(
            metadata.get(
                "name",
                group_key
            )
        )

        result += (
            '<button '
            f'id="btn-{escape_html(group_key)}" '
            'class="tab-btn custom-group-btn" '
            'onclick="switchMarket('
            "event, "
            f"'{escape_html(group_key)}'"
            ')">'
            f'{escape_html(group_name)} '
            '('
            f'{len(data_dict.get(group_key, []))}'
            ')'
            '</button>'
        )

    return result


# =========================================================================
# 圖表卡片 HTML
# =========================================================================
def generate_chart_card_html(
    market_key,
    chart_index,
    item,
    drawing_enabled
):
    chart_id = (
        f"chart-{market_key}-{chart_index}"
    )

    info_id = (
        f"info-{market_key}-{chart_index}"
    )

    drawing_status_id = (
        f"drawing-status-"
        f"{market_key}-{chart_index}"
    )

    ticker = str(
        item.get("ticker", "")
    ).strip().upper()

    chart_data = item.get(
        "chart_data",
        {}
    )

    drawing_timeframe = str(
        chart_data.get(
            "drawing_timeframe",
            "1d"
        )
    ).strip()

    timeframe_label = str(
        chart_data.get(
            "timeframe",
            "日K"
        )
    ).strip()

    placeholder_text = (
        "點擊週 K 後固定顯示週期與 OHLCV"
        if drawing_timeframe == "1w"
        else "點擊 K 線後固定顯示日期與 OHLCV"
    )

    result = """
<div class="chart-card">
    <div class="chart-toolbar">
        <div class="chart-toolbar-row">
            <div class="zoom-buttons">
"""

    result += (
        '<button '
        'type="button" '
        'class="chart-tool-btn" '
        f'onclick="zoomChart('
        f'\'{escape_html(chart_id)}\', '
        '0.70)">'
        '＋ 放大'
        '</button>'
    )

    result += (
        '<button '
        'type="button" '
        'class="chart-tool-btn" '
        f'onclick="zoomChart('
        f'\'{escape_html(chart_id)}\', '
        '1.40)">'
        '－ 縮小'
        '</button>'
    )

    result += (
        '<button '
        'type="button" '
        'class="chart-tool-btn reset-btn" '
        f'onclick="resetChart('
        f'\'{escape_html(chart_id)}\')">'
        '↺ 重設'
        '</button>'
    )

    result += """
            </div>
"""

    if drawing_enabled:
        result += (
            '<div class="drawing-status" '
            f'id="{escape_html(drawing_status_id)}">'
            '畫線尚未同步'
            '</div>'
        )
    else:
        result += (
            '<div class="drawing-status disabled">'
            '全市場圖表不開放畫線'
            '</div>'
        )

    result += """
        </div>
"""

    if drawing_enabled:
        result += """
        <div class="drawing-toolbar">
            <div class="drawing-tool-group">
"""

        result += (
            '<button '
            'type="button" '
            'class="drawing-tool-btn" '
            f'onclick="activateDrawingTool('
            f'\'{escape_html(chart_id)}\', '
            '\'drawline\')">'
            '╱ 趨勢線'
            '</button>'
        )

        result += (
            '<button '
            'type="button" '
            'class="drawing-tool-btn" '
            f'onclick="activateHorizontalLineTool('
            f'\'{escape_html(chart_id)}\')">'
            '─ 水平線'
            '</button>'
        )

        result += (
            '<button '
            'type="button" '
            'class="drawing-tool-btn" '
            f'onclick="activateDrawingTool('
            f'\'{escape_html(chart_id)}\', '
            '\'drawrect\')">'
            '▭ 矩形'
            '</button>'
        )

        result += """
            </div>

            <div class="drawing-style-group">
                <label class="drawing-label">
                    顏色
                </label>

                <select
                    class="drawing-select"
"""

        result += (
            f'id="drawing-color-'
            f'{escape_html(chart_id)}" '
            f'onchange="applyDrawingStyle('
            f'\'{escape_html(chart_id)}\')">'
        )

        result += """
                    <option value="#facc15">
                        黃色
                    </option>
                    <option value="#38bdf8">
                        藍色
                    </option>
                    <option value="#ef4444">
                        紅色
                    </option>
                    <option value="#22c55e">
                        綠色
                    </option>
                    <option value="#a855f7">
                        紫色
                    </option>
                    <option value="#f8fafc">
                        白色
                    </option>
                </select>

                <label class="drawing-label">
                    粗細
                </label>

                <select
                    class="drawing-select"
"""

        result += (
            f'id="drawing-width-'
            f'{escape_html(chart_id)}" '
            f'onchange="applyDrawingStyle('
            f'\'{escape_html(chart_id)}\')">'
        )

        result += """
                    <option value="1">
                        1 px
                    </option>
                    <option value="2" selected>
                        2 px
                    </option>
                    <option value="3">
                        3 px
                    </option>
                    <option value="4">
                        4 px
                    </option>
                </select>
            </div>

            <div class="drawing-action-group">
"""

        result += (
            '<button '
            'type="button" '
            'class="drawing-tool-btn danger-outline" '
            f'onclick="activateEraseTool('
            f'\'{escape_html(chart_id)}\')">'
            '⌫ 刪除圖形'
            '</button>'
        )

        result += (
            '<button '
            'type="button" '
            'class="drawing-tool-btn danger" '
            f'onclick="clearAllDrawings('
            f'\'{escape_html(chart_id)}\')">'
            '清除全部'
            '</button>'
        )

        result += (
            '<button '
            'type="button" '
            'class="drawing-tool-btn sync-btn" '
            f'onclick="syncChartDrawings('
            f'\'{escape_html(chart_id)}\', '
            'true)">'
            '☁ 立即同步'
            '</button>'
        )

        result += """
            </div>
        </div>
"""

    result += (
        f'<div id="{escape_html(info_id)}" '
        'class="ohlcv-fixed-panel">'
        '<span class="ohlcv-placeholder">'
        f'{escape_html(placeholder_text)}'
        '</span>'
        '</div>'
    )

    result += (
        f'<div id="{escape_html(chart_id)}" '
        f'data-market-id="{escape_html(market_key)}" '
        f'data-chart-index="{chart_index}" '
        f'data-ticker="{escape_html(ticker)}" '
        f'data-timeframe="{escape_html(drawing_timeframe)}" '
        f'data-drawing-enabled="'
        f'{"true" if drawing_enabled else "false"}" '
        'class="plotly-container">'
        '</div>'
    )

    result += """
    </div>
</div>
"""

    return result


# =========================================================================
# 完整 HTML 產生
# =========================================================================
def generate_html(
    data_dict,
    date_str,
    sector_summary=None,
    group_metadata=None
):
    sector_summary = (
        sector_summary or []
    )

    group_metadata = (
        group_metadata or {}
    )

    clean_data = clean_json_value(
        data_dict
    )

    chart_json = json.dumps(
        clean_data,
        ensure_ascii=False,
        allow_nan=False
    ).replace(
        "</script>",
        "<\\/script>"
    )

    tw_custom_group_keys = (
        get_sorted_custom_group_keys(
            group_metadata,
            market="tw"
        )
    )

    us_custom_group_keys = (
        get_sorted_custom_group_keys(
            group_metadata,
            market="us"
        )
    )

    all_custom_group_keys = (
        tw_custom_group_keys
        + us_custom_group_keys
    )

    html = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">

<title>
    台美股均線潛伏報告
</title>

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<script src="https://static.line-scdn.net/liff/edge/2/sdk.js"></script>
<script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>

<style>
:root {
    color-scheme: dark;
    --page-bg: #080a0f;
    --panel-bg: rgba(19, 23, 34, 0.94);
    --chart-bg: #131722;
    --toolbar-bg: #111827;
    --border: rgba(255, 255, 255, 0.08);
    --text-main: #f8fafc;
    --text-secondary: #cbd5e1;
    --text-muted: #64748b;
    --blue: #38bdf8;
    --purple: #a855f7;
    --green: #10b981;
    --red: #ef4444;
    --yellow: #facc15;
}

* {
    box-sizing: border-box;
}

body {
    min-height: 100vh;
    margin: 0;
    padding: 16px;
    color: var(--text-main);
    background:
        radial-gradient(
            circle at top,
            #182033 0,
            #0b0e14 45%,
            #080a0f 100%
        );
    font-family:
        Arial,
        "Noto Sans TC",
        sans-serif;
}

button,
select {
    font-family: inherit;
}

.header {
    max-width: 1100px;
    margin: 0 auto 18px;
    padding: 22px 18px;
    text-align: center;
    background: var(--panel-bg);
    border: 1px solid var(--border);
    border-radius: 14px;
    box-shadow:
        0 12px 35px
        rgba(0, 0, 0, 0.30);
}

.header h2 {
    margin: 0;
    font-size: 24px;
    letter-spacing: 0.5px;
}

.header p {
    margin: 8px 0 0;
    color: #00ff88;
    font-size: 13px;
}

.liff-status {
    max-width: 1100px;
    margin: 0 auto 12px;
    padding: 10px 14px;
    color: #93c5fd;
    text-align: center;
    background: rgba(30, 64, 175, 0.18);
    border: 1px solid rgba(59, 130, 246, 0.30);
    border-radius: 10px;
    font-size: 12px;
}

.liff-status.success {
    color: #86efac;
    background: rgba(22, 101, 52, 0.22);
    border-color: rgba(34, 197, 94, 0.34);
}

.liff-status.warning {
    color: #fde68a;
    background: rgba(120, 53, 15, 0.26);
    border-color: rgba(245, 158, 11, 0.34);
}

.category-box {
    max-width: 1100px;
    margin: 0 auto 14px;
    padding: 14px 16px;
    background: var(--panel-bg);
    border: 1px solid var(--border);
    border-left: 4px solid var(--blue);
    border-radius: 12px;
}

.category-title {
    margin-bottom: 12px;
    color: #e2e8f0;
    font-size: 15px;
    font-weight: 700;
}

.tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.custom-tabs-divider {
    display: flex;
    align-items: center;
    width: 100%;
    margin: 5px 0 1px;
    color: #c084fc;
    font-size: 11px;
    font-weight: 700;
}

.custom-tabs-divider::after {
    flex: 1;
    height: 1px;
    margin-left: 8px;
    background:
        rgba(192, 132, 252, 0.25);
    content: "";
}

.tab-btn {
    padding: 9px 14px;
    color: #94a3b8;
    background: #1e293b;
    border:
        1px solid
        rgba(255, 255, 255, 0.06);
    border-radius: 8px;
    font-size: 13px;
    cursor: pointer;
    transition:
        background 0.2s ease,
        color 0.2s ease,
        transform 0.2s ease,
        border-color 0.2s ease;
}

.tab-btn:hover {
    color: #ffffff;
    background: #334155;
    border-color:
        rgba(56, 189, 248, 0.35);
    transform: translateY(-1px);
}

.tab-btn.active {
    color: #ffffff;
    background:
        linear-gradient(
            135deg,
            #0284c7,
            #2563eb
        );
    border-color: #38bdf8;
    box-shadow:
        0 5px 16px
        rgba(37, 99, 235, 0.30);
}

.index-btn.active {
    background:
        linear-gradient(
            135deg,
            #7c3aed,
            #a855f7
        );
    border-color: #c084fc;
}

.sector-btn.active {
    background:
        linear-gradient(
            135deg,
            #0f766e,
            #059669
        );
    border-color: #34d399;
}

.custom-group-btn {
    border-color:
        rgba(192, 132, 252, 0.28);
}

.custom-group-btn.active {
    background:
        linear-gradient(
            135deg,
            #6d28d9,
            #9333ea
        );
    border-color: #c084fc;
}

.market-section {
    display: none;
    max-width: 1100px;
    margin: 0 auto;
}

.market-section.active {
    display: block;
}

.test-notice {
    max-width: 1100px;
    margin: 0 auto 15px;
    padding: 11px 16px;
    color: #fde68a;
    text-align: center;
    background:
        rgba(120, 53, 15, 0.35);
    border:
        1px solid
        rgba(245, 158, 11, 0.30);
    border-radius: 10px;
}

.sector-overview {
    margin-bottom: 18px;
    padding: 16px;
    background:
        rgba(15, 23, 42, 0.95);
    border:
        1px solid
        rgba(52, 211, 153, 0.22);
    border-radius: 14px;
}

.sector-overview-title {
    color: #f8fafc;
    font-size: 18px;
    font-weight: 800;
}

.sector-overview-subtitle {
    margin-top: 6px;
    color: #94a3b8;
    font-size: 12px;
}

.sector-summary-grid {
    display: grid;
    grid-template-columns:
        repeat(3, minmax(0, 1fr));
    gap: 12px;
    margin-top: 14px;
}

.sector-summary-column {
    padding: 12px;
    background:
        rgba(30, 41, 59, 0.62);
    border:
        1px solid
        rgba(148, 163, 184, 0.14);
    border-radius: 11px;
}

.sector-summary-column.strong {
    border-top: 3px solid #22c55e;
}

.sector-summary-column.neutral {
    border-top: 3px solid #eab308;
}

.sector-summary-column.weak {
    border-top: 3px solid #ef4444;
}

.sector-summary-title {
    margin-bottom: 10px;
    color: #e2e8f0;
    font-size: 14px;
    font-weight: 800;
}

.sector-summary-item {
    margin-bottom: 9px;
    padding: 10px;
    background:
        rgba(15, 23, 42, 0.75);
    border-radius: 8px;
}

.sector-summary-head {
    display: flex;
    align-items: center;
    gap: 7px;
}

.sector-rank {
    color: #fbbf24;
    font-size: 12px;
    font-weight: 800;
}

.sector-symbol {
    color: #f8fafc;
    font-size: 13px;
    font-weight: 800;
}

.sector-name {
    color: #cbd5e1;
    font-size: 12px;
}

.sector-summary-metrics {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 9px;
    margin-top: 7px;
    color: #94a3b8;
    font-size: 10px;
}

.sector-summary-status {
    margin-top: 7px;
    color: #a5b4fc;
    font-size: 10px;
}

.positive {
    color: #4ade80;
}

.negative {
    color: #fb7185;
}

.sector-summary-empty {
    color: #64748b;
    font-size: 12px;
}

.chart-card {
    overflow: hidden;
    margin-bottom: 20px;
    padding: 4px;
    background: var(--chart-bg);
    border: 1px solid var(--border);
    border-radius: 14px;
    box-shadow:
        0 12px 30px
        rgba(0, 0, 0, 0.32);
    transition:
        border-color 0.2s ease,
        transform 0.2s ease;
}

.chart-card:hover {
    border-color:
        rgba(56, 189, 248, 0.35);
    transform: translateY(-2px);
}

.chart-toolbar {
    padding: 8px 10px;
    background: var(--toolbar-bg);
    border-bottom: 1px solid var(--border);
    border-radius: 10px 10px 0 0;
}

.chart-toolbar-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
}

.zoom-buttons,
.drawing-tool-group,
.drawing-style-group,
.drawing-action-group {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
}

.chart-tool-btn,
.drawing-tool-btn {
    padding: 7px 11px;
    color: #e2e8f0;
    background: #1e293b;
    border:
        1px solid
        rgba(148, 163, 184, 0.22);
    border-radius: 7px;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    touch-action: manipulation;
}

.chart-tool-btn:hover,
.drawing-tool-btn:hover {
    color: #ffffff;
    background: #334155;
    border-color: #38bdf8;
}

.chart-tool-btn.reset-btn {
    color: #ddd6fe;
    border-color:
        rgba(168, 85, 247, 0.38);
}

.drawing-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px 12px;
    margin-top: 8px;
    padding-top: 8px;
    border-top:
        1px solid
        rgba(255, 255, 255, 0.06);
}

.drawing-tool-btn.active {
    color: #111827;
    background: #facc15;
    border-color: #fde047;
}

.drawing-tool-btn.danger-outline {
    color: #fca5a5;
    border-color:
        rgba(239, 68, 68, 0.35);
}

.drawing-tool-btn.danger {
    color: #ffffff;
    background:
        rgba(185, 28, 28, 0.72);
    border-color: #ef4444;
}

.drawing-tool-btn.sync-btn {
    color: #bfdbfe;
    border-color:
        rgba(59, 130, 246, 0.40);
}

.drawing-label {
    color: #94a3b8;
    font-size: 11px;
}

.drawing-select {
    min-width: 72px;
    padding: 6px 8px;
    color: #e2e8f0;
    background: #0f172a;
    border:
        1px solid
        rgba(148, 163, 184, 0.28);
    border-radius: 6px;
    font-size: 11px;
}

.drawing-status {
    color: #94a3b8;
    font-size: 11px;
}

.drawing-status.success {
    color: #4ade80;
}

.drawing-status.warning {
    color: #fbbf24;
}

.drawing-status.error {
    color: #fb7185;
}

.drawing-status.disabled {
    color: #64748b;
}

.ohlcv-fixed-panel {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 12px;
    min-height: 46px;
    padding: 8px 12px;
    color: #cbd5e1;
    background: #0f172a;
    border-bottom:
        1px solid
        rgba(255, 255, 255, 0.06);
    font-size: 12px;
    line-height: 1.5;
}

.ohlcv-placeholder {
    color: #64748b;
}

.ohlcv-date {
    color: #f8fafc;
    font-weight: 700;
}

.ohlcv-timeframe {
    color: #c084fc;
    font-weight: 700;
}

.ohlcv-open {
    color: #fbbf24;
}

.ohlcv-high {
    color: #fb7185;
}

.ohlcv-low {
    color: #2dd4bf;
}

.ohlcv-close {
    color: #60a5fa;
}

.ohlcv-volume {
    color: #c084fc;
}

.ohlcv-change {
    color: #94a3b8;
}

.ohlcv-up {
    color: #ef5350;
    font-weight: 700;
}

.ohlcv-down {
    color: #26a69a;
    font-weight: 700;
}

.ohlcv-flat {
    color: #cbd5e1;
    font-weight: 700;
}

.plotly-container {
    width: 100%;
    height: 520px;
    background: #131722;
    border-radius: 0 0 12px 12px;
    touch-action: none;
}

.no-data {
    margin-top: 20px;
    padding: 55px 20px;
    color: #64748b;
    text-align: center;
    background:
        rgba(19, 23, 34, 0.75);
    border:
        1px dashed
        rgba(148, 163, 184, 0.25);
    border-radius: 12px;
}

.plotly-error {
    padding: 40px 15px;
    color: #fca5a5;
    text-align: center;
    background:
        rgba(127, 29, 29, 0.20);
    border-radius: 10px;
}

@media (max-width: 760px) {
    .sector-summary-grid {
        grid-template-columns: 1fr;
    }
}

@media (max-width: 600px) {
    body {
        padding: 8px;
    }

    .header {
        padding: 16px 10px;
        border-radius: 10px;
    }

    .header h2 {
        font-size: 19px;
    }

    .category-box {
        padding: 12px 10px;
    }

    .tab-btn {
        flex: 1 1 auto;
        padding: 9px 8px;
        font-size: 12px;
    }

    .chart-toolbar-row {
        align-items: stretch;
        flex-direction: column;
    }

    .zoom-buttons {
        width: 100%;
    }

    .chart-tool-btn {
        flex: 1;
        padding: 8px 5px;
        font-size: 11px;
    }

    .drawing-toolbar {
        align-items: stretch;
        flex-direction: column;
    }

    .drawing-tool-group,
    .drawing-style-group,
    .drawing-action-group {
        width: 100%;
    }

    .drawing-tool-btn {
        flex: 1;
        padding: 8px 5px;
        font-size: 10px;
    }

    .drawing-status {
        text-align: left;
    }

    .ohlcv-fixed-panel {
        min-height: 52px;
        padding: 7px 8px;
        font-size: 11px;
    }

    .plotly-container {
        min-height: 470px;
        height: auto;
    }

    .chart-card {
        margin-bottom: 14px;
        border-radius: 10px;
    }
}
</style>
</head>

<body>
<div class="header">
    <h2>
        📈 台美股量化潛伏網頁報告
        (__REPORT_DATE__)
    </h2>

    <p>
        專屬訂製滾動數據儲存版
    </p>
</div>

<div
    id="liff-status"
    class="liff-status"
>
    正在初始化 LINE LIFF 與畫線同步功能……
</div>
"""

    html = html.replace(
        "__REPORT_DATE__",
        escape_html(date_str)
    )

    if CUSTOM_GROUP_TEST_MODE:
        html += """
<div class="test-notice">
    目前為 Supabase 測試模式，
    自訂群組不套用均線距離篩選
</div>
"""

    html += f"""
<div
    class="category-box"
    style="border-left-color:#a855f7;"
>
    <div class="category-title">
        🌍 全球大盤指數
    </div>

    <div class="tabs">
        <button
            id="btn-indices"
            class="tab-btn index-btn"
            onclick="switchMarket(event, 'indices')"
        >
            全球指數圖表
            ({len(data_dict.get("indices", []))})
        </button>
    </div>
</div>

<div
    class="category-box"
    style="border-left-color:#10b981;"
>
    <div class="category-title">
        🧭 美股類股週 K
    </div>

    <div class="tabs">
        <button
            id="btn-sectors"
            class="tab-btn sector-btn"
            onclick="switchMarket(event, 'sectors')"
        >
            11 大類股週線輪動
            ({len(data_dict.get("sectors", []))})
        </button>
    </div>
</div>

<div
    class="category-box"
    style="border-left-color:#ff5252;"
>
    <div class="category-title">
        🇹🇼 台灣股市
    </div>

    <div class="tabs">
        <button
            id="btn-tw_all"
            class="tab-btn active"
            onclick="switchMarket(event, 'tw_all')"
        >
            全市場潛伏
            ({len(data_dict.get("tw_all", []))})
        </button>

        <button
            id="btn-tw_g1"
            class="tab-btn"
            onclick="switchMarket(event, 'tw_g1')"
        >
            權值精選
            ({len(data_dict.get("tw_g1", []))})
        </button>

        <button
            id="btn-tw_g2"
            class="tab-btn"
            onclick="switchMarket(event, 'tw_g2')"
        >
            熱門
            ({len(data_dict.get("tw_g2", []))})
        </button>
"""

    html += generate_custom_group_buttons_html(
        data_dict,
        group_metadata,
        market="tw"
    )

    html += """
    </div>
</div>
"""

    html += f"""
<div
    class="category-box"
    style="border-left-color:#00b0ff;"
>
    <div class="category-title">
        🇺🇸 美國股市
    </div>

    <div class="tabs">
        <button
            id="btn-us_all"
            class="tab-btn"
            onclick="switchMarket(event, 'us_all')"
        >
            全市場潛伏
            ({len(data_dict.get("us_all", []))})
        </button>

        <button
            id="btn-us_g1"
            class="tab-btn"
            onclick="switchMarket(event, 'us_g1')"
        >
            權值精選
            ({len(data_dict.get("us_g1", []))})
        </button>

        <button
            id="btn-us_g2"
            class="tab-btn"
            onclick="switchMarket(event, 'us_g2')"
        >
            低本益比
            ({len(data_dict.get("us_g2", []))})
        </button>

        <button
            id="btn-us_g3"
            class="tab-btn"
            onclick="switchMarket(event, 'us_g3')"
        >
            超級績效
            ({len(data_dict.get("us_g3", []))})
        </button>

        <button
            id="btn-us_g4"
            class="tab-btn"
            onclick="switchMarket(event, 'us_g4')"
        >
            熱門
            ({len(data_dict.get("us_g4", []))})
        </button>
"""

    html += generate_custom_group_buttons_html(
        data_dict,
        group_metadata,
        market="us"
    )

    html += """
    </div>
</div>
"""

    keys = [
        "indices",
        "sectors",
        "tw_all",
        "tw_g1",
        "tw_g2",
        *tw_custom_group_keys,
        "us_all",
        "us_g1",
        "us_g2",
        "us_g3",
        "us_g4",
        *us_custom_group_keys
    ]

    # 去除重複，保持原順序
    keys = list(dict.fromkeys(keys))

    for key in keys:
        active_class = (
            " active"
            if key == "tw_all"
            else ""
        )

        html += (
            f'<div id="{escape_html(key)}-market" '
            f'class="market-section{active_class}">'
        )

        if key == "sectors":
            html += (
                generate_sector_overview_html(
                    sector_summary
                )
            )

        items = data_dict.get(
            key,
            []
        )

        if items:
            drawing_enabled = (
                is_drawing_enabled_market(key)
            )

            for chart_index, item in enumerate(
                items
            ):
                html += generate_chart_card_html(
                    key,
                    chart_index,
                    item,
                    drawing_enabled
                )

        else:
            if key == "indices":
                no_data_text = (
                    "目前沒有可顯示的指數"
                )

            elif key == "sectors":
                no_data_text = (
                    "目前沒有可顯示的類股週 K"
                )

            elif key.startswith("custom_"):
                metadata = group_metadata.get(
                    key,
                    {}
                )

                group_name = metadata.get(
                    "name",
                    key
                )

                no_data_text = (
                    f"{group_name} "
                    "目前沒有符合均線條件的股票"
                )

            else:
                no_data_text = (
                    "此分類目前沒有可顯示的股票"
                )

            html += (
                '<div class="no-data">'
                f'{escape_html(no_data_text)}'
                '</div>'
            )

        html += "</div>"

    # JavaScript 與 LIFF 畫線同步會在第 5 部分加入。
    # 目前先將已建立的 HTML 保存在 html 變數中。


# =========================================================================
# 第 4 部分結束
# 注意：generate_html() 尚未結束
# 第 5 部分必須直接接續，保持 4 個空白縮排
# =========================================================================

    html += """
<script>
const chartDataStore = __CHART_JSON__;

const REPORT_LIFF_ID = "__REPORT_LIFF_ID__";
const DRAWING_SYNC_URL = "__DRAWING_SYNC_URL__";

let liffReady = false;
let liffIdToken = "";
let lineProfile = null;

const chartSyncTimers = {};
const chartLoadingFlags = {};
const horizontalLineModes = {};
const eraseModes = {};


/* ======================================================================
 * 一般格式工具
 * ====================================================================== */
function formatPrice(value) {
    const numberValue = Number(value);

    if (!Number.isFinite(numberValue)) {
        return "--";
    }

    return numberValue.toLocaleString(
        "zh-TW",
        {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }
    );
}


function formatVolume(value) {
    const numberValue = Number(value);

    if (!Number.isFinite(numberValue)) {
        return "--";
    }

    return Math.round(
        numberValue
    ).toLocaleString("zh-TW");
}


function normalizeDate(value) {
    return String(
        value || ""
    ).substring(0, 10);
}


function setLiffStatus(
    message,
    statusClass = ""
) {
    const statusElement = (
        document.getElementById(
            "liff-status"
        )
    );

    if (!statusElement) {
        return;
    }

    statusElement.textContent = message;

    statusElement.className = (
        "liff-status"
        + (
            statusClass
                ? " " + statusClass
                : ""
        )
    );
}


function setDrawingStatus(
    container,
    message,
    statusClass = ""
) {
    if (!container) {
        return;
    }

    const marketId = (
        container.dataset.marketId
    );

    const chartIndex = (
        container.dataset.chartIndex
    );

    const statusElement = (
        document.getElementById(
            "drawing-status-"
            + marketId
            + "-"
            + chartIndex
        )
    );

    if (!statusElement) {
        return;
    }

    statusElement.textContent = message;

    statusElement.className = (
        "drawing-status"
        + (
            statusClass
                ? " " + statusClass
                : ""
        )
    );
}


/* ======================================================================
 * LIFF 初始化
 * ====================================================================== */
async function initializeLiff() {
    if (typeof liff === "undefined") {
        setLiffStatus(
            "⚠️ LIFF SDK 載入失敗，"
            + "畫線仍會保存在本機瀏覽器。",
            "warning"
        );

        return;
    }

    try {
        await liff.init({
            liffId: REPORT_LIFF_ID
        });

        if (!liff.isLoggedIn()) {
            setLiffStatus(
                "正在導向 LINE 登入……"
            );

            liff.login({
                redirectUri: window.location.href
            });

            return;
        }

        liffIdToken = (
            liff.getIDToken() || ""
        );

        if (!liffIdToken) {
            setLiffStatus(
                "⚠️ 無法取得 LINE ID Token，"
                + "畫線只會保存在目前裝置。",
                "warning"
            );

            return;
        }

        try {
            lineProfile = (
                await liff.getProfile()
            );
        } catch (profileError) {
            console.warn(
                "無法取得 LINE Profile：",
                profileError
            );
        }

        liffReady = true;

        const displayName = (
            lineProfile
            && lineProfile.displayName
                ? lineProfile.displayName
                : "LINE 使用者"
        );

        setLiffStatus(
            "✅ 已登入 "
            + displayName
            + "，畫線可跨裝置同步。",
            "success"
        );

        await synchronizeRenderedCharts();

    } catch (error) {
        console.error(
            "LIFF 初始化失敗：",
            error
        );

        setLiffStatus(
            "⚠️ LIFF 初始化失敗，"
            + "畫線仍會保存在本機瀏覽器。",
            "warning"
        );
    }
}


/* ======================================================================
 * 圖表資料取得
 * ====================================================================== */
function getChartItem(container) {
    if (!container) {
        return null;
    }

    const marketId = (
        container.dataset.marketId
    );

    const chartIndex = Number(
        container.dataset.chartIndex
    );

    if (
        !marketId
        || !Number.isInteger(chartIndex)
        || !Array.isArray(
            chartDataStore[marketId]
        )
    ) {
        return null;
    }

    return (
        chartDataStore[marketId][
            chartIndex
        ]
        || null
    );
}


function getDrawingIdentity(container) {
    const item = getChartItem(
        container
    );

    if (!item) {
        return null;
    }

    const ticker = String(
        item.ticker
        || container.dataset.ticker
        || ""
    ).trim().toUpperCase();

    const timeframe = String(
        (
            item.chart_data
            && item.chart_data.drawing_timeframe
        )
        || container.dataset.timeframe
        || "1d"
    ).trim();

    const marketKey = String(
        container.dataset.marketId
        || ""
    ).trim();

    if (!ticker) {
        return null;
    }

    return {
        ticker,
        timeframe,
        marketKey
    };
}


function getDrawingStorageKey(container) {
    const identity = (
        getDrawingIdentity(container)
    );

    if (!identity) {
        return "";
    }

    return (
        "stock_chart_drawings::"
        + identity.ticker
        + "::"
        + identity.timeframe
    );
}


/* ======================================================================
 * 畫線資料清理
 * ====================================================================== */
function clonePlainObject(value) {
    try {
        return JSON.parse(
            JSON.stringify(value)
        );
    } catch (error) {
        return null;
    }
}


function isSystemShape(shape) {
    if (!shape) {
        return true;
    }

    const shapeName = String(
        shape.name || ""
    );

    return (
        shapeName === "__volume_separator__"
        || shapeName === "__selected_candle__"
    );
}


function getBaseShapes(container) {
    if (
        container
        && Array.isArray(
            container._baseShapes
        )
    ) {
        return container._baseShapes
            .map(clonePlainObject)
            .filter(Boolean);
    }

    return [];
}


function getUserDrawings(container) {
    if (
        !container
        || !container.layout
        || !Array.isArray(
            container.layout.shapes
        )
    ) {
        return [];
    }

    return container.layout.shapes
        .filter(
            (shape) => !isSystemShape(shape)
        )
        .map(clonePlainObject)
        .filter(Boolean);
}


function normalizeLoadedDrawings(
    drawings
) {
    if (!Array.isArray(drawings)) {
        return [];
    }

    return drawings
        .filter(
            (shape) => (
                shape
                && typeof shape === "object"
                && !isSystemShape(shape)
            )
        )
        .slice(0, 200)
        .map(clonePlainObject)
        .filter(Boolean);
}


function applyUserDrawings(
    container,
    drawings
) {
    if (
        !container
        || typeof Plotly === "undefined"
    ) {
        return Promise.resolve();
    }

    const baseShapes = (
        getBaseShapes(container)
    );

    const userShapes = (
        normalizeLoadedDrawings(
            drawings
        )
    );

    container._drawingLoadInProgress = true;

    return Plotly.relayout(
        container,
        {
            shapes: [
                ...baseShapes,
                ...userShapes
            ]
        }
    ).finally(() => {
        setTimeout(() => {
            container._drawingLoadInProgress = false;
        }, 100);
    });
}


/* ======================================================================
 * localStorage 保存
 * ====================================================================== */
function loadLocalDrawingRecord(
    container
) {
    const storageKey = (
        getDrawingStorageKey(container)
    );

    if (!storageKey) {
        return {
            drawings: [],
            updatedAt: null
        };
    }

    try {
        const rawValue = (
            localStorage.getItem(
                storageKey
            )
        );

        if (!rawValue) {
            return {
                drawings: [],
                updatedAt: null
            };
        }

        const parsedValue = JSON.parse(
            rawValue
        );

        return {
            drawings: (
                normalizeLoadedDrawings(
                    parsedValue.drawings
                )
            ),
            updatedAt: (
                parsedValue.updatedAt
                || null
            )
        };

    } catch (error) {
        console.error(
            "讀取本機畫線失敗：",
            error
        );

        return {
            drawings: [],
            updatedAt: null
        };
    }
}


function saveLocalDrawingRecord(
    container,
    drawings,
    updatedAt = null
) {
    const storageKey = (
        getDrawingStorageKey(container)
    );

    if (!storageKey) {
        return null;
    }

    const finalUpdatedAt = (
        updatedAt
        || new Date().toISOString()
    );

    const record = {
        drawings: (
            normalizeLoadedDrawings(
                drawings
            )
        ),
        updatedAt: finalUpdatedAt
    };

    try {
        localStorage.setItem(
            storageKey,
            JSON.stringify(record)
        );

        return record;

    } catch (error) {
        console.error(
            "保存本機畫線失敗：",
            error
        );

        return null;
    }
}


/* ======================================================================
 * Edge Function 請求
 * ====================================================================== */
async function requestDrawingSync(
    payload
) {
    if (
        !liffReady
        || !liffIdToken
    ) {
        throw new Error(
            "LIFF 尚未完成登入"
        );
    }

    const response = await fetch(
        DRAWING_SYNC_URL,
        {
            method: "POST",
            headers: {
                "Content-Type":
                    "application/json"
            },
            body: JSON.stringify({
                ...payload,
                idToken: liffIdToken
            })
        }
    );

    let responseData = {};

    try {
        responseData = (
            await response.json()
        );
    } catch (error) {
        responseData = {};
    }

    if (
        !response.ok
        || responseData.ok !== true
    ) {
        throw new Error(
            responseData.error
            || (
                "同步失敗，HTTP "
                + response.status
            )
        );
    }

    return responseData;
}


/* ======================================================================
 * 遠端載入與衝突處理
 * ====================================================================== */
function parseTimestamp(value) {
    if (!value) {
        return 0;
    }

    const timestamp = Date.parse(value);

    return (
        Number.isFinite(timestamp)
            ? timestamp
            : 0
    );
}


async function loadAndMergeDrawings(
    container
) {
    if (
        !container
        || container.dataset.drawingEnabled
            !== "true"
    ) {
        return;
    }

    const identity = (
        getDrawingIdentity(container)
    );

    if (!identity) {
        return;
    }

    const localRecord = (
        loadLocalDrawingRecord(
            container
        )
    );

    if (localRecord.drawings.length) {
        await applyUserDrawings(
            container,
            localRecord.drawings
        );

        setDrawingStatus(
            container,
            "已載入本機畫線",
            "success"
        );
    }

    if (
        !liffReady
        || !liffIdToken
    ) {
        setDrawingStatus(
            container,
            "目前只使用本機保存",
            "warning"
        );

        return;
    }

    if (
        chartLoadingFlags[
            getDrawingStorageKey(container)
        ]
    ) {
        return;
    }

    const loadingKey = (
        getDrawingStorageKey(container)
    );

    chartLoadingFlags[
        loadingKey
    ] = true;

    try {
        setDrawingStatus(
            container,
            "正在讀取雲端畫線……"
        );

        const remoteRecord = (
            await requestDrawingSync({
                action: "load",
                ticker: identity.ticker,
                timeframe:
                    identity.timeframe,
                marketKey:
                    identity.marketKey
            })
        );

        const remoteDrawings = (
            normalizeLoadedDrawings(
                remoteRecord.drawings
            )
        );

        const localTimestamp = (
            parseTimestamp(
                localRecord.updatedAt
            )
        );

        const remoteTimestamp = (
            parseTimestamp(
                remoteRecord.updatedAt
            )
        );

        if (
            remoteTimestamp
            > localTimestamp
        ) {
            await applyUserDrawings(
                container,
                remoteDrawings
            );

            saveLocalDrawingRecord(
                container,
                remoteDrawings,
                remoteRecord.updatedAt
            );

            setDrawingStatus(
                container,
                "已載入雲端畫線",
                "success"
            );

        } else if (
            localTimestamp > remoteTimestamp
            && localRecord.drawings.length
        ) {
            await saveDrawingsToRemote(
                container,
                localRecord.drawings,
                false
            );

        } else if (
            !localRecord.drawings.length
            && remoteDrawings.length
        ) {
            await applyUserDrawings(
                container,
                remoteDrawings
            );

            saveLocalDrawingRecord(
                container,
                remoteDrawings,
                remoteRecord.updatedAt
            );

            setDrawingStatus(
                container,
                "已載入雲端畫線",
                "success"
            );

        } else {
            setDrawingStatus(
                container,
                "畫線已同步",
                "success"
            );
        }

    } catch (error) {
        console.error(
            "載入雲端畫線失敗：",
            error
        );

        setDrawingStatus(
            container,
            "雲端同步失敗，本機畫線仍保留",
            "warning"
        );

    } finally {
        chartLoadingFlags[
            loadingKey
        ] = false;
    }
}


/* ======================================================================
 * 遠端保存
 * ====================================================================== */
async function saveDrawingsToRemote(
    container,
    drawings,
    showStatus = true
) {
    if (
        !container
        || container.dataset.drawingEnabled
            !== "true"
    ) {
        return null;
    }

    const identity = (
        getDrawingIdentity(container)
    );

    if (!identity) {
        return null;
    }

    if (
        !liffReady
        || !liffIdToken
    ) {
        if (showStatus) {
            setDrawingStatus(
                container,
                "已存本機；LIFF 未登入，尚未同步",
                "warning"
            );
        }

        return null;
    }

    try {
        if (showStatus) {
            setDrawingStatus(
                container,
                "正在同步畫線……"
            );
        }

        const responseData = (
            await requestDrawingSync({
                action: "save",
                ticker: identity.ticker,
                timeframe:
                    identity.timeframe,
                marketKey:
                    identity.marketKey,
                drawings:
                    normalizeLoadedDrawings(
                        drawings
                    )
            })
        );

        saveLocalDrawingRecord(
            container,
            drawings,
            responseData.updatedAt
        );

        setDrawingStatus(
            container,
            "畫線已跨裝置同步",
            "success"
        );

        return responseData;

    } catch (error) {
        console.error(
            "保存雲端畫線失敗：",
            error
        );

        setDrawingStatus(
            container,
            "雲端同步失敗，本機畫線仍保留",
            "warning"
        );

        return null;
    }
}


function scheduleDrawingSave(
    container
) {
    if (
        !container
        || container.dataset.drawingEnabled
            !== "true"
        || container._drawingLoadInProgress
    ) {
        return;
    }

    const drawings = (
        getUserDrawings(container)
    );

    const localRecord = (
        saveLocalDrawingRecord(
            container,
            drawings
        )
    );

    if (localRecord) {
        setDrawingStatus(
            container,
            "已保存至本機，等待雲端同步"
        );
    }

    const storageKey = (
        getDrawingStorageKey(container)
    );

    if (!storageKey) {
        return;
    }

    if (chartSyncTimers[storageKey]) {
        clearTimeout(
            chartSyncTimers[storageKey]
        );
    }

    chartSyncTimers[storageKey] = (
        setTimeout(() => {
            saveDrawingsToRemote(
                container,
                drawings,
                true
            );
        }, 900)
    );
}


async function syncChartDrawings(
    chartId,
    showStatus = true
) {
    const container = (
        document.getElementById(
            chartId
        )
    );

    if (!container) {
        return;
    }

    const drawings = (
        getUserDrawings(container)
    );

    saveLocalDrawingRecord(
        container,
        drawings
    );

    await saveDrawingsToRemote(
        container,
        drawings,
        showStatus
    );
}


async function synchronizeRenderedCharts() {
    const containers = (
        document.querySelectorAll(
            '.plotly-container'
            + '[data-drawing-enabled="true"]'
            + '[data-done="true"]'
        )
    );

    for (const container of containers) {
        await loadAndMergeDrawings(
            container
        );
    }
}


/* ======================================================================
 * 畫線樣式與工具
 * ====================================================================== */
function getDrawingStyle(chartId) {
    const colorSelect = (
        document.getElementById(
            "drawing-color-" + chartId
        )
    );

    const widthSelect = (
        document.getElementById(
            "drawing-width-" + chartId
        )
    );

    const color = (
        colorSelect
            ? colorSelect.value
            : "#facc15"
    );

    const width = Number(
        widthSelect
            ? widthSelect.value
            : 2
    );

    return {
        color,
        width: (
            Number.isFinite(width)
                ? width
                : 2
        )
    };
}


function clearActiveDrawingButtons(
    container
) {
    if (!container) {
        return;
    }

    const card = container.closest(
        ".chart-card"
    );

    if (!card) {
        return;
    }

    card.querySelectorAll(
        ".drawing-tool-btn"
    ).forEach((button) => {
        button.classList.remove(
            "active"
        );
    });
}


function applyDrawingStyle(chartId) {
    const container = (
        document.getElementById(
            chartId
        )
    );

    if (
        !container
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    const style = (
        getDrawingStyle(chartId)
    );

    Plotly.relayout(
        container,
        {
            "newshape.line.color":
                style.color,
            "newshape.line.width":
                style.width,
            "newshape.fillcolor":
                style.color + "22",
            "newshape.opacity": 0.90
        }
    );
}


function activateDrawingTool(
    chartId,
    toolName
) {
    const container = (
        document.getElementById(
            chartId
        )
    );

    if (
        !container
        || container.dataset.done
            !== "true"
        || container.dataset.drawingEnabled
            !== "true"
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    horizontalLineModes[chartId] = false;
    eraseModes[chartId] = false;

    clearActiveDrawingButtons(
        container
    );

    applyDrawingStyle(chartId);

    Plotly.relayout(
        container,
        {
            dragmode: toolName
        }
    );

    setDrawingStatus(
        container,
        (
            toolName === "drawrect"
                ? "矩形模式：拖曳建立區域"
                : "趨勢線模式：拖曳建立線段"
        )
    );
}


function activateHorizontalLineTool(
    chartId
) {
    const container = (
        document.getElementById(
            chartId
        )
    );

    if (
        !container
        || container.dataset.done
            !== "true"
        || container.dataset.drawingEnabled
            !== "true"
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    clearActiveDrawingButtons(
        container
    );

    eraseModes[chartId] = false;
    horizontalLineModes[chartId] = true;

    Plotly.relayout(
        container,
        {
            dragmode: "pan"
        }
    );

    setDrawingStatus(
        container,
        "水平線模式：請點擊一根 K 線的價格位置"
    );
}


function activateEraseTool(chartId) {
    const container = (
        document.getElementById(
            chartId
        )
    );

    if (
        !container
        || container.dataset.done
            !== "true"
        || container.dataset.drawingEnabled
            !== "true"
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    clearActiveDrawingButtons(
        container
    );

    horizontalLineModes[chartId] = false;
    eraseModes[chartId] = true;

    Plotly.relayout(
        container,
        {
            dragmode: "eraseshape"
        }
    ).catch(() => {
        setDrawingStatus(
            container,
            "請先點選圖形，再使用 Plotly 刪除工具",
            "warning"
        );
    });

    setDrawingStatus(
        container,
        "刪除模式：點擊要刪除的圖形"
    );
}


function addHorizontalPriceLine(
    container,
    price
) {
    if (
        !container
        || !Number.isFinite(price)
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    const chartId = container.id;
    const style = (
        getDrawingStyle(chartId)
    );

    const currentShapes = (
        Array.isArray(
            container.layout.shapes
        )
            ? container.layout.shapes
                .map(clonePlainObject)
                .filter(Boolean)
            : []
    );

    const horizontalShape = {
        type: "line",
        xref: "paper",
        yref: "y",
        x0: 0,
        x1: 1,
        y0: price,
        y1: price,
        editable: true,
        line: {
            color: style.color,
            width: style.width,
            dash: "solid"
        }
    };

    container._drawingLoadInProgress = true;

    Plotly.relayout(
        container,
        {
            shapes: [
                ...currentShapes,
                horizontalShape
            ],
            dragmode: "pan"
        }
    ).then(() => {
        horizontalLineModes[
            chartId
        ] = false;

        setTimeout(() => {
            container._drawingLoadInProgress = false;
            scheduleDrawingSave(
                container
            );
        }, 100);

        setDrawingStatus(
            container,
            "水平線已建立",
            "success"
        );
    });
}


async function clearAllDrawings(
    chartId
) {
    const container = (
        document.getElementById(
            chartId
        )
    );

    if (
        !container
        || container.dataset.drawingEnabled
            !== "true"
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    const confirmed = window.confirm(
        "確定要清除這個商品、"
        + "這個週期的全部人工畫線嗎？"
    );

    if (!confirmed) {
        return;
    }

    horizontalLineModes[chartId] = false;
    eraseModes[chartId] = false;

    container._drawingLoadInProgress = true;

    await Plotly.relayout(
        container,
        {
            shapes: getBaseShapes(
                container
            ),
            dragmode: "pan"
        }
    );

    container._drawingLoadInProgress = false;

    saveLocalDrawingRecord(
        container,
        []
    );

    await saveDrawingsToRemote(
        container,
        [],
        true
    );

    setDrawingStatus(
        container,
        "全部人工畫線已清除",
        "success"
    );
}


/* ======================================================================
 * OHLCV 點擊與選取線
 * ====================================================================== */
function findOhlcvRecord(
    item,
    clickedDate
) {
    if (
        !item
        || !item.chart_data
        || !Array.isArray(
            item.chart_data.ohlcv
        )
    ) {
        return null;
    }

    const normalizedDate = (
        normalizeDate(clickedDate)
    );

    return (
        item.chart_data.ohlcv.find(
            (row) => (
                row.date
                === normalizedDate
            )
        )
        || null
    );
}


function clearSelectedCandle(
    container
) {
    if (
        !container
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    const shapes = (
        Array.isArray(
            container.layout.shapes
        )
            ? container.layout.shapes
                .filter(
                    (shape) => (
                        String(
                            shape.name || ""
                        )
                        !== "__selected_candle__"
                    )
                )
            : []
    );

    container._drawingLoadInProgress = true;

    Plotly.relayout(
        container,
        {
            shapes,
            annotations: []
        }
    ).finally(() => {
        setTimeout(() => {
            container._drawingLoadInProgress = false;
        }, 50);
    });

    delete container.dataset.selectedDate;
}


function markSelectedDate(
    container,
    record
) {
    if (
        !container
        || !record
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    const existingShapes = (
        Array.isArray(
            container.layout.shapes
        )
            ? container.layout.shapes
                .filter(
                    (shape) => (
                        String(
                            shape.name || ""
                        )
                        !== "__selected_candle__"
                    )
                )
                .map(clonePlainObject)
                .filter(Boolean)
            : []
    );

    const selectedShape = {
        type: "line",
        name: "__selected_candle__",
        xref: "x",
        yref: "paper",
        x0: record.date,
        x1: record.date,
        y0: 0,
        y1: 1,
        editable: false,
        line: {
            color: "#facc15",
            width: 1.5,
            dash: "dot"
        }
    };

    const selectedAnnotation = {
        xref: "x",
        yref: "paper",
        x: record.date,
        y: 1,
        text: (
            record.date
            + "<br>收 "
            + formatPrice(
                record.close
            )
        ),
        showarrow: true,
        arrowhead: 2,
        arrowsize: 1,
        arrowwidth: 1,
        arrowcolor: "#facc15",
        ax: 0,
        ay: -38,
        bgcolor:
            "rgba(17,24,39,0.95)",
        bordercolor: "#facc15",
        borderwidth: 1,
        borderpad: 4,
        font: {
            color: "#f8fafc",
            size: 10
        }
    };

    container._drawingLoadInProgress = true;

    Plotly.relayout(
        container,
        {
            shapes: [
                ...existingShapes,
                selectedShape
            ],
            annotations: [
                selectedAnnotation
            ]
        }
    ).finally(() => {
        setTimeout(() => {
            container._drawingLoadInProgress = false;
        }, 50);
    });
}


function showFixedOhlcv(
    container,
    clickedDate
) {
    const item = (
        getChartItem(container)
    );

    const record = (
        findOhlcvRecord(
            item,
            clickedDate
        )
    );

    if (!record) {
        return;
    }

    const marketId = (
        container.dataset.marketId
    );

    const chartIndex = (
        container.dataset.chartIndex
    );

    const infoPanel = (
        document.getElementById(
            "info-"
            + marketId
            + "-"
            + chartIndex
        )
    );

    if (!infoPanel) {
        return;
    }

    const openValue = Number(
        record.open
    );

    const closeValue = Number(
        record.close
    );

    let directionClass = (
        "ohlcv-flat"
    );

    let directionText = "平盤";
    let changeText = "--";

    if (
        Number.isFinite(openValue)
        && Number.isFinite(closeValue)
    ) {
        const changeValue = (
            closeValue - openValue
        );

        if (closeValue > openValue) {
            directionClass = "ohlcv-up";
            directionText = "上漲";

        } else if (
            closeValue < openValue
        ) {
            directionClass = (
                "ohlcv-down"
            );

            directionText = "下跌";
        }

        if (openValue !== 0) {
            const changePercent = (
                changeValue / openValue
            ) * 100;

            changeText = (
                (
                    changeValue >= 0
                        ? "+"
                        : ""
                )
                + formatPrice(changeValue)
                + " / "
                + (
                    changePercent >= 0
                        ? "+"
                        : ""
                )
                + changePercent.toFixed(2)
                + "%"
            );
        }
    }

    const timeframe = (
        record.timeframe
        || (
            item
            && item.chart_data
            && item.chart_data.timeframe
        )
        || "日K"
    );

    infoPanel.innerHTML = (
        '<span class="ohlcv-date">'
        + record.date
        + '</span>'

        + '<span class="ohlcv-timeframe">'
        + timeframe
        + '</span>'

        + '<span class="'
        + directionClass
        + '">'
        + directionText
        + '</span>'

        + '<span class="ohlcv-open">'
        + '開 '
        + formatPrice(record.open)
        + '</span>'

        + '<span class="ohlcv-high">'
        + '高 '
        + formatPrice(record.high)
        + '</span>'

        + '<span class="ohlcv-low">'
        + '低 '
        + formatPrice(record.low)
        + '</span>'

        + '<span class="ohlcv-close">'
        + '收 '
        + formatPrice(record.close)
        + '</span>'

        + '<span class="ohlcv-volume">'
        + '量 '
        + formatVolume(record.volume)
        + '</span>'

        + '<span class="ohlcv-change">'
        + 'K棒 '
        + changeText
        + '</span>'
    );

    container.dataset.selectedDate = (
        record.date
    );

    markSelectedDate(
        container,
        record
    );
}


/* ======================================================================
 * 圖表範圍
 * ====================================================================== */
function getCategoryDates(item) {
    if (
        !item
        || !item.chart_data
        || !Array.isArray(
            item.chart_data.ohlcv
        )
    ) {
        return [];
    }

    return item.chart_data.ohlcv.map(
        (record) => record.date
    );
}


function getDefaultRange(
    totalPoints
) {
    const visibleCount = Math.min(
        60,
        totalPoints
    );

    return [
        Math.max(
            -0.5,
            totalPoints
            - visibleCount
            - 0.5
        ),
        totalPoints - 0.5
    ];
}


function categoryRangeToIndex(
    rangeValue,
    dateList,
    fallbackValue
) {
    if (
        typeof rangeValue === "number"
        && Number.isFinite(rangeValue)
    ) {
        return rangeValue;
    }

    if (
        typeof rangeValue === "string"
    ) {
        const normalizedDate = (
            normalizeDate(rangeValue)
        );

        const foundIndex = (
            dateList.indexOf(
                normalizedDate
            )
        );

        if (foundIndex >= 0) {
            return foundIndex;
        }
    }

    return fallbackValue;
}


function zoomChart(
    chartId,
    factor
) {
    const container = (
        document.getElementById(
            chartId
        )
    );

    if (
        !container
        || container.dataset.done
            !== "true"
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    const item = getChartItem(
        container
    );

    const dateList = (
        getCategoryDates(item)
    );

    const totalPoints = (
        dateList.length
    );

    if (totalPoints <= 1) {
        return;
    }

    const xaxisLayout = (
        container.layout
        && container.layout.xaxis
            ? container.layout.xaxis
            : {}
    );

    const currentRange = (
        Array.isArray(
            xaxisLayout.range
        )
            ? xaxisLayout.range
            : getDefaultRange(
                totalPoints
            )
    );

    let rangeStart = (
        categoryRangeToIndex(
            currentRange[0],
            dateList,
            -0.5
        )
    );

    let rangeEnd = (
        categoryRangeToIndex(
            currentRange[1],
            dateList,
            totalPoints - 0.5
        )
    );

    if (rangeEnd < rangeStart) {
        const temporaryValue = (
            rangeStart
        );

        rangeStart = rangeEnd;
        rangeEnd = temporaryValue;
    }

    const currentWidth = Math.max(
        1,
        rangeEnd - rangeStart
    );

    const minimumWidth = Math.min(
        8,
        Math.max(
            1,
            totalPoints - 1
        )
    );

    const maximumWidth = (
        totalPoints
    );

    let nextWidth = (
        currentWidth
        * Number(factor)
    );

    nextWidth = Math.max(
        minimumWidth,
        Math.min(
            maximumWidth,
            nextWidth
        )
    );

    const center = (
        rangeStart + rangeEnd
    ) / 2;

    let nextStart = (
        center - nextWidth / 2
    );

    let nextEnd = (
        center + nextWidth / 2
    );

    const minimumRange = -0.5;
    const maximumRange = (
        totalPoints - 0.5
    );

    if (nextStart < minimumRange) {
        nextEnd += (
            minimumRange - nextStart
        );

        nextStart = minimumRange;
    }

    if (nextEnd > maximumRange) {
        nextStart -= (
            nextEnd - maximumRange
        );

        nextEnd = maximumRange;
    }

    nextStart = Math.max(
        minimumRange,
        nextStart
    );

    nextEnd = Math.min(
        maximumRange,
        nextEnd
    );

    Plotly.relayout(
        container,
        {
            "xaxis.autorange": false,
            "xaxis.range": [
                nextStart,
                nextEnd
            ]
        }
    );
}


function resetChart(chartId) {
    const container = (
        document.getElementById(
            chartId
        )
    );

    if (
        !container
        || container.dataset.done
            !== "true"
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    const item = getChartItem(
        container
    );

    const dateList = (
        getCategoryDates(item)
    );

    const defaultRange = (
        getDefaultRange(
            dateList.length
        )
    );

    clearSelectedCandle(
        container
    );

    horizontalLineModes[
        chartId
    ] = false;

    eraseModes[
        chartId
    ] = false;

    Plotly.relayout(
        container,
        {
            "xaxis.autorange": false,
            "xaxis.range":
                defaultRange,
            "yaxis.autorange": true,
            "yaxis2.autorange": true,
            dragmode: "pan"
        }
    );
}


/* ======================================================================
 * 圖表事件
 * ====================================================================== */
function bindChartEvents(container) {
    if (
        !container
        || container.dataset.eventsBound
            === "true"
    ) {
        return;
    }

    container.on(
        "plotly_click",
        (eventData) => {
            if (
                !eventData
                || !Array.isArray(
                    eventData.points
                )
                || !eventData.points.length
            ) {
                return;
            }

            const clickedPoint = (
                eventData.points[0]
            );

            const chartId = (
                container.id
            );

            if (
                horizontalLineModes[
                    chartId
                ]
            ) {
                const clickedPrice = Number(
                    clickedPoint.y
                );

                if (
                    Number.isFinite(
                        clickedPrice
                    )
                ) {
                    addHorizontalPriceLine(
                        container,
                        clickedPrice
                    );
                }

                return;
            }

            if (
                clickedPoint.x !== undefined
                && clickedPoint.x !== null
            ) {
                showFixedOhlcv(
                    container,
                    clickedPoint.x
                );
            }
        }
    );

    container.on(
        "plotly_relayout",
        (eventData) => {
            if (
                container._drawingLoadInProgress
                || container.dataset.drawingEnabled
                    !== "true"
            ) {
                return;
            }

            const eventKeys = Object.keys(
                eventData || {}
            );

            const shapeChanged = (
                eventKeys.some(
                    (key) => (
                        key === "shapes"
                        || key.startsWith(
                            "shapes["
                        )
                    )
                )
            );

            if (shapeChanged) {
                scheduleDrawingSave(
                    container
                );
            }
        }
    );

    container.on(
        "plotly_doubleclick",
        () => {
            resetChart(
                container.id
            );

            return false;
        }
    );

    container.dataset.eventsBound = (
        "true"
    );
}


/* ======================================================================
 * 圖表渲染
 * ====================================================================== */
function renderMarketCharts(
    marketId
) {
    const items = (
        chartDataStore[marketId]
    );

    if (
        !items
        || !Array.isArray(items)
    ) {
        return;
    }

    if (
        typeof Plotly === "undefined"
    ) {
        console.error(
            "Plotly 載入失敗"
        );

        const section = (
            document.getElementById(
                marketId + "-market"
            )
        );

        if (section) {
            section.innerHTML = (
                '<div class="plotly-error">'
                + 'Plotly 圖表套件載入失敗，'
                + '請重新整理頁面或檢查網路。'
                + '</div>'
            );
        }

        return;
    }

    items.forEach(
        (item, index) => {
            const id = (
                "chart-"
                + marketId
                + "-"
                + index
            );

            const container = (
                document.getElementById(
                    id
                )
            );

            if (
                !container
                || container.dataset.done
                    === "true"
            ) {
                return;
            }

            if (
                !item
                || !item.chart_data
                || !Array.isArray(
                    item.chart_data.data
                )
            ) {
                container.innerHTML = (
                    '<div class="plotly-error">'
                    + '圖表資料格式錯誤'
                    + '</div>'
                );

                return;
            }

            container.dataset.marketId = (
                marketId
            );

            container.dataset.chartIndex = (
                String(index)
            );

            const originalLayout = (
                item.chart_data.layout
                || {}
            );

            const categoryDates = (
                getCategoryDates(item)
            );

            const defaultRange = (
                getDefaultRange(
                    categoryDates.length
                )
            );

            const layout = {
                ...originalLayout,
                margin: {
                    ...(
                        originalLayout.margin
                        || {}
                    )
                },
                title: {
                    ...(
                        originalLayout.title
                        || {}
                    )
                },
                legend: {
                    ...(
                        originalLayout.legend
                        || {}
                    )
                },
                xaxis: {
                    ...(
                        originalLayout.xaxis
                        || {}
                    ),
                    type: "category",
                    categoryorder:
                        "array",
                    categoryarray:
                        categoryDates,
                    range:
                        defaultRange,
                    autorange: false,
                    rangeslider: {
                        visible: false
                    },
                    fixedrange: false
                },
                yaxis: {
                    ...(
                        originalLayout.yaxis
                        || {}
                    ),
                    fixedrange: false
                },
                dragmode: "pan"
            };

            if (
                originalLayout.yaxis2
            ) {
                layout.yaxis2 = {
                    ...originalLayout.yaxis2,
                    fixedrange: false
                };
            }

            const hasVolume = Boolean(
                originalLayout.yaxis2
            );

            if (
                window.innerWidth <= 600
            ) {
                layout.height = (
                    hasVolume
                        ? 470
                        : 390
                );

                layout.margin = {
                    ...layout.margin,
                    l: 8,
                    r: 52,
                    t: 120,
                    b: 34
                };

                layout.title = {
                    ...layout.title,
                    font: {
                        ...(
                            layout.title.font
                            || {}
                        ),
                        size: 15
                    }
                };

                layout.legend = {
                    ...layout.legend,
                    font: {
                        ...(
                            layout.legend.font
                            || {}
                        ),
                        size: 10
                    }
                };

            } else {
                layout.height = (
                    originalLayout.height
                    || (
                        hasVolume
                            ? 520
                            : 440
                    )
                );
            }

            const drawingEnabled = (
                container.dataset.drawingEnabled
                === "true"
            );

            const config = {
                responsive: true,
                displayModeBar: true,
                displaylogo: false,
                scrollZoom: true,
                doubleClick: false,
                showTips: false,
                editable: drawingEnabled,
                edits: {
                    shapePosition:
                        drawingEnabled
                },
                modeBarButtonsToAdd: (
                    drawingEnabled
                        ? [
                            "drawline",
                            "drawrect",
                            "eraseshape"
                        ]
                        : []
                ),
                modeBarButtonsToRemove: [
                    "select2d",
                    "lasso2d",
                    "toggleSpikelines",
                    "hoverClosestCartesian",
                    "hoverCompareCartesian",
                    "toImage"
                ]
            };

            Plotly.newPlot(
                container,
                item.chart_data.data,
                layout,
                config
            )
            .then(async () => {
                container.dataset.done = (
                    "true"
                );

                container._baseShapes = (
                    Array.isArray(
                        originalLayout.shapes
                    )
                        ? originalLayout.shapes
                            .map(clonePlainObject)
                            .filter(Boolean)
                        : []
                );

                bindChartEvents(
                    container
                );

                applyDrawingStyle(
                    container.id
                );

                if (drawingEnabled) {
                    await loadAndMergeDrawings(
                        container
                    );
                }

                requestAnimationFrame(
                    () => {
                        Plotly.Plots.resize(
                            container
                        );
                    }
                );
            })
            .catch((error) => {
                console.error(
                    "圖表繪製失敗：",
                    marketId,
                    index,
                    error
                );

                container.innerHTML = (
                    '<div class="plotly-error">'
                    + '圖表繪製失敗'
                    + '</div>'
                );
            });
        }
    );
}


function resizeMarketCharts(
    marketId
) {
    const items = (
        chartDataStore[marketId]
    );

    if (
        !items
        || !Array.isArray(items)
    ) {
        return;
    }

    items.forEach(
        (item, index) => {
            const container = (
                document.getElementById(
                    "chart-"
                    + marketId
                    + "-"
                    + index
                )
            );

            if (
                container
                && container.dataset.done
                    === "true"
                && typeof Plotly
                    !== "undefined"
            ) {
                Plotly.Plots.resize(
                    container
                );
            }
        }
    );
}


function switchMarket(
    event,
    marketId
) {
    document
        .querySelectorAll(
            ".market-section"
        )
        .forEach((element) => {
            element.classList.remove(
                "active"
            );
        });

    document
        .querySelectorAll(
            ".tab-btn"
        )
        .forEach((element) => {
            element.classList.remove(
                "active"
            );
        });

    const section = (
        document.getElementById(
            marketId + "-market"
        )
    );

    if (section) {
        section.classList.add(
            "active"
        );
    }

    if (
        event
        && event.currentTarget
    ) {
        event.currentTarget.classList.add(
            "active"
        );

    } else {
        const button = (
            document.getElementById(
                "btn-" + marketId
            )
        );

        if (button) {
            button.classList.add(
                "active"
            );
        }
    }

    renderMarketCharts(
        marketId
    );

    setTimeout(() => {
        resizeMarketCharts(
            marketId
        );
    }, 150);
}


window.addEventListener(
    "load",
    async () => {
        renderMarketCharts(
            "tw_all"
        );

        await initializeLiff();
    }
);


window.addEventListener(
    "resize",
    () => {
        const activeSection = (
            document.querySelector(
                ".market-section.active"
            )
        );

        if (!activeSection) {
            return;
        }

        const marketId = (
            activeSection.id.replace(
                "-market",
                ""
            )
        );

        resizeMarketCharts(
            marketId
        );
    }
);
</script>
</body>
</html>
"""

    html = html.replace(
        "__CHART_JSON__",
        chart_json
    )

    html = html.replace(
        "__REPORT_LIFF_ID__",
        REPORT_LIFF_ID
    )

    html = html.replace(
        "__DRAWING_SYNC_URL__",
        DRAWING_SYNC_URL
    )

    os.makedirs(
        DOCS_DIR,
        exist_ok=True
    )

    path = os.path.join(
        DOCS_DIR,
        "index.html"
    )

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as file:
        file.write(html)

    print(
        f"✅ HTML 已產生：{path}"
    )


# =========================================================================
# 第 5 部分結束
# generate_html() 已完整結束
# 下一部分：GitHub Pages 推送與 main()
# =========================================================================

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

    add_result = run_git_command(
        [
            "git",
            "add",
            "docs/index.html",
            "data"
        ]
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


def append_index_market_report(
    lines,
    market_title,
    tickers,
    index_map
):
    lines.append(market_title)

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

        lines.append(
            analyze_index_trend(
                normalized_ticker,
                item.get(
                    "name",
                    normalized_ticker
                ),
                get_ma_list_from_item(
                    item
                )
            )
        )

    if found_count == 0:
        lines.append(
            "⚪ 此市場目前沒有啟用的指數設定"
        )

    lines.append("")


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
    # 產生 GitHub Pages HTML
    # -----------------------------------------------------------------
    generate_html(
        data_dict,
        today_str,
        sector_summary=sector_summary,
        group_metadata=group_metadata
    )

    # -----------------------------------------------------------------
    # 報告與控制台網址
    # -----------------------------------------------------------------
    report_url = REPORT_LIFF_URL

    # 原本參數控制台：
    # 2010330411-SbwvRXRN
    # → my-stock-backend
    liff_setting_url = (
        SETTING_LIFF_URL
    )

    # 另一套原有系統，維持不變
    bitget_setting_url = (
        BITGET_SETTING_URL
    )

    # -----------------------------------------------------------------
    # LINE 個股與網頁報告推播
    #
    # 依需求：動態自訂群組不加入 LINE 統計，
    # 避免未來新增大量群組後訊息過長。
    # -----------------------------------------------------------------
    line_message_stocks = (
        f"🎯 {today_str} "
        "專屬量化看盤網頁！\n\n"

        "🌍 【全球指數區塊】\n"
        " └ 指數圖表："
        f"{len(data_dict.get('indices', []))} "
        "張\n\n"

        "🧭 【美股類股週K】\n"
        " └ 類股圖表："
        f"{len(data_dict.get('sectors', []))} "
        "張\n\n"

        "🇹🇼 【台灣股市區塊】\n"
        " ├ 1. 全市場符合："
        f"{len(data_dict.get('tw_all', []))} "
        "檔\n"
        " ├ 2. 權值精選符合："
        f"{len(data_dict.get('tw_g1', []))} "
        "檔\n"
        " └ 3. 熱門符合："
        f"{len(data_dict.get('tw_g2', []))} "
        "檔\n\n"

        "🇺🇸 【美國股市區塊】\n"
        " ├ 1. 全市場符合："
        f"{len(data_dict.get('us_all', []))} "
        "檔\n"
        " ├ 2. 權值精選符合："
        f"{len(data_dict.get('us_g1', []))} "
        "檔\n"
        " ├ 3. 低本益比符合："
        f"{len(data_dict.get('us_g2', []))} "
        "檔\n"
        " ├ 4. 超級績效符合："
        f"{len(data_dict.get('us_g3', []))} "
        "檔\n"
        " └ 5. 熱門符合："
        f"{len(data_dict.get('us_g4', []))} "
        "檔\n\n"

        "🔗 1. 專屬潛伏圖表網頁：\n"
        f"{report_url}\n\n"

        "⚙️ 2. 手機自訂參數控制台：\n"
        f"{liff_setting_url}\n\n"

        "💰 3. 自動交易參數控制台：\n"
        f"{bitget_setting_url}"
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

    append_index_market_report(
        index_lines,
        "【 🇹🇼 台灣市場 】",
        tw_indices,
        index_map
    )

    append_index_market_report(
        index_lines,
        "【 🇺🇸 美國市場 】",
        us_indices,
        index_map
    )

    append_index_market_report(
        index_lines,
        "【 🇪🇺 歐洲市場 】",
        eu_indices,
        index_map
    )

    append_index_market_report(
        index_lines,
        "【 🌏 亞洲市場 】",
        asia_indices,
        index_map
    )

    send_line_message(
        "\n".join(index_lines).rstrip(),
        access_token,
        line_push_user_id
    )

    # -----------------------------------------------------------------
    # 每週一限定類股週線輪動摘要
    # -----------------------------------------------------------------
    if weekday == 0:
        sectors_message = (
            build_sector_monday_message(
                today_str,
                sector_summary,
                report_url
            )
        )

        send_line_message(
            sectors_message,
            access_token,
            line_push_user_id
        )

    # -----------------------------------------------------------------
    # 推送最新 HTML 與 CSV 到 GitHub Pages
    # -----------------------------------------------------------------
    push_report_to_github()


if __name__ == "__main__":
    main()


# =========================================================================
# 完整 main.py 結束
# =========================================================================
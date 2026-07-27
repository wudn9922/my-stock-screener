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


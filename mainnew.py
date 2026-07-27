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

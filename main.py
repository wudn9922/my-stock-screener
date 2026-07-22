import io
import json
import os
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

# 台股中文名稱快取
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

# True：Supabase 自訂群組只要可下載就顯示
# False：套用均線距離條件
CUSTOM_GROUP_TEST_MODE = (
    os.environ.get("CUSTOM_GROUP_TEST_MODE", "false")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

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
    ticker = str(ticker or "").strip().upper()
    name = str(name or "").strip()

    if not ticker or not name:
        return

    TW_STOCK_NAMES[ticker] = name

    stock_code = ticker.split(".")[0]

    if stock_code:
        TW_STOCK_NAMES[stock_code] = name


def get_tw_stock_name(ticker):
    ticker = str(ticker or "").strip().upper()

    if not ticker:
        return ""

    stock_code = ticker.split(".")[0]

    return (
        TW_STOCK_NAMES.get(ticker)
        or TW_STOCK_NAMES.get(stock_code)
        or ""
    )


def extract_yfinance_data(downloaded_data, ticker=None):
    if downloaded_data is None or downloaded_data.empty:
        return pd.DataFrame()

    df = downloaded_data.copy()

    if not isinstance(df.columns, pd.MultiIndex):
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

    if price_fields.intersection(set(level_0)):
        df.columns = df.columns.get_level_values(0)
        return df

    if price_fields.intersection(set(level_1)):
        df.columns = df.columns.get_level_values(1)
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

    if getattr(result.index, "tz", None) is not None:
        result.index = result.index.tz_localize(None)

    result = result[
        ~result.index.duplicated(keep="last")
    ].sort_index()

    return result


def calculate_return(close_series, periods):
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
# Supabase 設定
# =========================================================================
def get_supabase_settings():
    base_url = os.environ.get(
        "SUPABASE_URL",
        ""
    ).strip().rstrip("/")

    api_key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
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


def load_configs_from_supabase(target_user_id):
    configs = {
        "tw_g1": {},
        "tw_g2": {},
        "us_g1": {},
        "us_g2": {},
        "us_g3": {},
        "us_g4": {}
    }

    index_configs = []
    default_index_configs = get_default_index_configs()

    target_user_id = str(
        target_user_id or ""
    ).strip()

    supabase_url, supabase_key = get_supabase_settings()

    if not supabase_url or not supabase_key:
        return configs, default_index_configs

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
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

    try:
        groups_response = requests.get(
            f"{supabase_url}/groups",
            headers=headers,
            params={"select": "*"},
            timeout=15
        )

        stocks_response = requests.get(
            f"{supabase_url}/stocks",
            headers=headers,
            params={"select": "*"},
            timeout=15
        )

        print(
            f"📡 Supabase groups 狀態："
            f"{groups_response.status_code}"
        )
        print(
            f"📡 Supabase stocks 狀態："
            f"{stocks_response.status_code}"
        )

        if groups_response.status_code == 200:
            groups_data = groups_response.json()
        else:
            groups_data = []
            print(
                f"❌ groups 查詢失敗："
                f"{groups_response.text[:500]}"
            )

        if stocks_response.status_code == 200:
            stocks_data = stocks_response.json()
        else:
            stocks_data = []
            print(
                f"❌ stocks 查詢失敗："
                f"{stocks_response.text[:500]}"
            )

        print(
            f"📋 Supabase 群組總數："
            f"{len(groups_data)}"
        )
        print(
            f"📋 Supabase 股票總數："
            f"{len(stocks_data)}"
        )

        group_id_to_key = {}

        for group_item in groups_data:
            group_name = str(
                group_item.get("name", "")
            ).strip()

            group_id = str(
                group_item.get("id", "")
            ).strip()

            if group_name in name_mapping and group_id:
                group_id_to_key[group_id] = (
                    name_mapping[group_name]
                )

        matched_user_count = 0
        loaded_count = 0

        for stock_item in stocks_data:
            stock_user_id = str(
                stock_item.get("line_user_id", "")
            ).strip()

            if stock_user_id != target_user_id:
                continue

            matched_user_count += 1

            group_id = str(
                stock_item.get("group_id", "")
            ).strip()

            ticker = str(
                stock_item.get("ticker", "")
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
                print("⚠️ 發現 ticker 為空的資料")
                continue

            if group_id == "admin_index":
                print(f"ℹ️ 略過 admin_index：{ticker}")
                continue

            mapped_key = group_id_to_key.get(group_id)

            if not mapped_key and group_id in configs:
                mapped_key = group_id

            if not mapped_key:
                print(
                    f"⚠️ 股票找不到對應群組："
                    f"ticker={ticker}, "
                    f"group_id={group_id}"
                )
                continue

            if mapped_key.startswith("us_"):
                ticker = ticker.replace(".", "-")

            if mapped_key.startswith("tw_") and stock_name:
                register_tw_stock_name(
                    ticker,
                    stock_name
                )

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

                if ma_value is not None and ma_value > 0:
                    ma_list.append(ma_value)

            if not ma_list:
                ma_list = [20]

            ma_list = sorted(set(ma_list))

            configs[mapped_key][ticker] = ma_list
            loaded_count += 1

            print(
                f"✅ 載入 Supabase 股票："
                f"{mapped_key} / "
                f"{ticker} / "
                f"MA={ma_list}"
            )

        print(
            f"👤 符合 Supabase User ID 的資料數："
            f"{matched_user_count}"
        )
        print(
            f"📊 成功載入自訂群組股票數："
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
            f"❌ 讀取雲端個股失敗："
            f"{type(exc).__name__}: {exc}"
        )

    try:
        index_response = requests.get(
            f"{supabase_url}/index_configs",
            headers=headers,
            params={"select": "*"},
            timeout=15
        )

        if index_response.status_code == 200:
            index_configs = index_response.json()
            print(
                "📡 成功從雲端同步全球大盤自訂均線參數"
            )
        else:
            print(
                f"⚠️ index_configs 查詢失敗："
                f"HTTP {index_response.status_code} "
                f"{index_response.text[:500]}"
            )

    except Exception as exc:
        print(
            f"⚠️ 讀取大盤設定失敗："
            f"{type(exc).__name__}: {exc}"
        )

    if not index_configs:
        index_configs = default_index_configs

    print("\n===== Supabase 自訂群組讀取結果 =====")

    for group_key, stocks in configs.items():
        print(f"{group_key}: {len(stocks)} 檔")

        for ticker, ma_list in stocks.items():
            print(f"  └─ {ticker}: {ma_list}")

    print("====================================\n")

    return configs, index_configs


# =========================================================================
# LINE Messaging API
# =========================================================================
def send_line_message(message, access_token, user_id):
    if not access_token or not user_id:
        print("⚠️ LINE 設定不完整，略過推播")
        return None

    access_token = str(access_token).strip()
    user_id = str(user_id).strip()
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

    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
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
            f"📨 LINE 推播狀態："
            f"{response.status_code}"
        )

        if response.status_code >= 400:
            request_id = response.headers.get(
                "x-line-request-id",
                "無"
            )

            print(
                f"⚠️ LINE 推播失敗："
                f"{response.text[:500]}"
            )
            print(f"LINE Request ID：{request_id}")
        else:
            print("✅ LINE 推播成功")

        return response.status_code

    except Exception as exc:
        print(
            f"⚠️ LINE 推播異常："
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
                    code = str(row[code_col]).strip()

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
                f"⚠️ TWSE 讀取失敗 "
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
                time.sleep(2)
                continue

            items = response.json()

            if not isinstance(items, list):
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

                volume = 0

                for key in [
                    "TradingShares",
                    "TradingVolume",
                    "成交股數"
                ]:
                    if key not in item:
                        continue

                    try:
                        volume = float(
                            str(item[key]).replace(",", "")
                        )
                        break
                    except (TypeError, ValueError):
                        continue

                if volume >= min_volume:
                    tickers.append(ticker)

            break

        except Exception as exc:
            print(
                f"⚠️ TPEx 讀取失敗 "
                f"({attempt + 1}/3)：{exc}"
            )
            time.sleep(2)

    tickers = sorted(set(tickers))

    if not tickers:
        return [
            "2330.TW",
            "2317.TW",
            "2454.TW",
            "2603.TW",
            "0050.TW"
        ]

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

        return [
            str(ticker).replace(".", "-")
            for ticker in table["Symbol"].tolist()
        ]

    except Exception as exc:
        print(f"⚠️ 美股清單取得失敗：{exc}")

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
        float(value)
        for value in df_chart["Open"].tolist()
    ]

    high_values = [
        float(value)
        for value in df_chart["High"].tolist()
    ]

    low_values = [
        float(value)
        for value in df_chart["Low"].tolist()
    ]

    close_values = [
        float(value)
        for value in df_chart["Close"].tolist()
    ]

    volume_values = [
        float(value)
        for value in df_chart["Volume"].tolist()
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

    for index, ma_window in enumerate(ma_list):
        ma_col = f"MA{ma_window}"

        if ma_col not in df_chart.columns:
            continue

        ma_values = [
            None if pd.isna(value) else float(value)
            for value in df_chart[ma_col].tolist()
        ]

        if not any(
            value is not None
            for value in ma_values
        ):
            continue

        color = colors[index % len(colors)]

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
                    "rgba(239, 83, 80, 0.62)"
                )
            else:
                volume_colors.append(
                    "rgba(38, 166, 154, 0.62)"
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
                f"　<span style='font-size:12px;"
                f"color:#c084fc'>{timeframe_label}</span>"
                f"<br><span style='font-size:12px;"
                f"color:#9ca3af'>{title_suffix}</span>"
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
            "anchor": "y2" if show_volume else "y",
            "rangeslider": {
                "visible": False
            },
            "fixedrange": False,
            "showgrid": True,
            "gridcolor": "rgba(255,255,255,0.055)",
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
            "gridcolor": "rgba(255,255,255,0.055)",
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
        "uirevision": ticker
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
                "line": {
                    "color": "rgba(148,163,184,0.20)",
                    "width": 1
                }
            }
        ]

    return {
        "data": traces,
        "layout": layout,
        "ohlcv": ohlcv_records,
        "timeframe": timeframe_label
    }


# =========================================================================
# 全市場掃描
# =========================================================================
def download_market_data(tickers, period):
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
            f"❌ 全市場批次下載失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return pd.DataFrame()


def scan_market(tickers, min_volume):
    os.makedirs(DATA_DIR, exist_ok=True)

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

                df = clean_ohlcv_dataframe(df)

                if df.empty:
                    continue

                df.tail(MAX_DAYS).to_csv(
                    os.path.join(
                        DATA_DIR,
                        f"{ticker}.csv"
                    )
                )

            except Exception as exc:
                print(
                    f"⚠️ {ticker} 初始化失敗：{exc}"
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
                today_data = extract_yfinance_data(
                    downloaded,
                    ticker
                )

                today_data = clean_ohlcv_dataframe(
                    today_data
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

                local_data = clean_ohlcv_dataframe(
                    local_data
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
                ].sort_index().tail(MAX_DAYS)

                combined.to_csv(csv_path)

            except Exception as exc:
                print(
                    f"⚠️ {ticker} 更新失敗：{exc}"
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

            latest_volume = float(
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

            price = float(
                df["Close"].iloc[-1]
            )

            ma20 = df["MA20"].iloc[-1]

            if pd.isna(ma20):
                continue

            ma20 = float(ma20)

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
                    "volume": int(latest_volume),
                    "chart_data": chart_data
                }
            )

        except Exception as exc:
            print(
                f"⚠️ {ticker} 全市場篩選失敗："
                f"{type(exc).__name__}: {exc}"
            )

    matched_list.sort(
        key=lambda item: item["volume"],
        reverse=True
    )

    return matched_list


# =========================================================================
# Supabase 自訂群組處理
# =========================================================================
def get_ticker_candidates(raw_ticker, group_key):
    ticker = str(
        raw_ticker or ""
    ).strip().upper()

    if not ticker:
        return []

    if group_key.startswith("us_"):
        return [
            ticker.replace(".", "-")
        ]

    if group_key.startswith("tw_"):
        if (
            ticker.endswith(".TW")
            or ticker.endswith(".TWO")
        ):
            return [ticker]

        if ticker.isdigit():
            return [
                f"{ticker}.TW",
                f"{ticker}.TWO"
            ]

    return [ticker]


def download_custom_stock(raw_ticker, group_key):
    candidates = get_ticker_candidates(
        raw_ticker,
        group_key
    )

    for candidate in candidates:
        try:
            print(f"⬇️ 嘗試下載：{candidate}")

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
                    f"⚠️ {candidate} 下載結果為空"
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
    test_mode=CUSTOM_GROUP_TEST_MODE
):
    matched_list = []

    if not group_dict:
        print(
            f"⚠️ {group_key} "
            f"沒有讀到 Supabase 股票"
        )

        return matched_list

    os.makedirs(DATA_DIR, exist_ok=True)

    print("\n====================================")
    print(f"📋 開始處理自訂群組：{group_key}")
    print(f"📋 Supabase 股票數：{len(group_dict)}")
    print(f"🧪 測試模式：{test_mode}")
    print("====================================")

    for raw_ticker, raw_ma_list in group_dict.items():
        try:
            print(
                f"\n🔍 開始處理："
                f"{raw_ticker}，"
                f"MA={raw_ma_list}"
            )

            actual_ticker, downloaded = (
                download_custom_stock(
                    raw_ticker,
                    group_key
                )
            )

            if not actual_ticker or downloaded.empty:
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

                    local_data = clean_ohlcv_dataframe(
                        local_data
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
                        f"讀取 CSV 失敗：{exc}"
                    )

            combined = clean_ohlcv_dataframe(
                combined
            )

            if combined.empty:
                continue

            ma_list = []

            for raw_ma in raw_ma_list:
                ma_value = safe_int(raw_ma)

                if ma_value is not None and ma_value > 0:
                    ma_list.append(ma_value)

            ma_list = sorted(set(ma_list)) or [20]

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
                combined[f"MA{ma_window}"] = (
                    combined["Close"]
                    .rolling(
                        window=ma_window,
                        min_periods=ma_window
                    )
                    .mean()
                )

            price = float(
                combined["Close"].iloc[-1]
            )

            latest_volume = float(
                combined["Volume"].iloc[-1]
            )

            matched_any_ma = False
            available_ma_count = 0
            triggered_info = []

            for ma_window in ma_list:
                ma_col = f"MA{ma_window}"
                ma_value = combined[ma_col].iloc[-1]

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
                    ma_value * CUSTOM_MA_MIN_RATIO
                    <= price
                    <= ma_value * CUSTOM_MA_MAX_RATIO
                ):
                    matched_any_ma = True

                    triggered_info.append(
                        f"近MA{ma_window}"
                        f"({diff_pct:.2f}%)"
                    )

            if test_mode and available_ma_count == 0:
                matched_any_ma = True
                triggered_info.append(
                    "均線資料不足，K線正常"
                )

            if not matched_any_ma:
                print(
                    f"⏭️ {actual_ticker} "
                    f"未符合均線條件"
                )
                continue

            ma_status_list = []

            for ma_window in ma_list:
                ma_col = f"MA{ma_window}"
                ma_value = combined[ma_col].iloc[-1]

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

            title = (
                f"({mode_text} | "
                f"現價:{price:.2f} | "
                f"{' / '.join(triggered_info)} | "
                f"{' | '.join(ma_status_list)})"
            )

            display_name = ""

            if group_key.startswith("tw_"):
                display_name = get_tw_stock_name(
                    actual_ticker
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
                    "volume": int(latest_volume),
                    "chart_data": chart_data
                }
            )

            print(
                f"✅ {actual_ticker} "
                f"已成功加入圖表"
            )

        except Exception as exc:
            print(
                f"❌ {group_key} / "
                f"{raw_ticker} 處理失敗："
                f"{type(exc).__name__}: {exc}"
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

        if ma_value is not None and ma_value > 0:
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
        print("⚠️ 沒有可用的大盤指數設定")
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
            print(f"⚠️ 略過重複指數：{ticker}")
            continue

        if not is_config_enabled(item):
            print(f"⏭️ 指數已停用：{ticker}")
            continue

        processed_tickers.add(ticker)

        ma_list = get_ma_list_from_item(item)

        try:
            print(
                f"📉 開始建立指數圖表："
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
                    f"⚠️ 指數下載結果為空："
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

            latest_close = float(
                df["Close"].iloc[-1]
            )

            latest_volume = float(
                df["Volume"].iloc[-1]
            )

            ma_status = []

            for ma_window in ma_list:
                ma_col = f"MA{ma_window}"
                ma_value = df[ma_col].iloc[-1]

                if pd.isna(ma_value):
                    continue

                ma_value = float(ma_value)

                diff_pct = (
                    (latest_close / ma_value) - 1
                ) * 100

                ma_status.append(
                    f"MA{ma_window}:"
                    f"{ma_value:.2f}"
                    f"({diff_pct:+.2f}%)"
                )

            title_parts = [
                "Supabase 指數參數",
                f"收盤:{latest_close:,.2f}",
                f"成交量:{latest_volume:,.0f}"
            ]

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
                    "volume": int(latest_volume),
                    "ma_list": ma_list,
                    "chart_data": chart_data
                }
            )

            print(
                f"✅ 指數圖表建立完成："
                f"{ticker} {name}"
            )

        except Exception as exc:
            print(
                f"❌ 指數圖表建立失敗："
                f"{ticker} / "
                f"{type(exc).__name__}: {exc}"
            )

    print(
        f"📊 全球指數圖表數量："
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
    daily_df = clean_ohlcv_dataframe(daily_df)

    if daily_df.empty:
        return pd.DataFrame()

    last_daily_date = daily_df.index[-1].normalize()

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

    # 若本週尚未到週五，移除未完成週 K。
    # 星期一推播因此不會誤用尚未完成的當週資料。
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

    if weekly_return > 0 and volume_ratio >= 1.05:
        return "上漲放量，資金流入"

    if weekly_return > 0 and volume_ratio < 0.95:
        return "上漲縮量，動能普通"

    if weekly_return < 0 and volume_ratio >= 1.05:
        return "下跌放量，資金流出"

    if weekly_return < 0 and volume_ratio < 0.95:
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

    download_tickers = (
        sector_tickers
        + [SECTOR_BENCHMARK]
    )

    print("\n====================================")
    print("🧭 開始建立美股 11 大類股週 K")
    print(
        f"📏 固定週均線："
        f"{SECTOR_WEEKLY_MA_LIST}"
    )
    print(
        f"📊 相對強弱基準："
        f"{SECTOR_BENCHMARK}"
    )
    print("====================================")

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
            f"❌ 類股批次下載失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return [], []

    if downloaded.empty:
        print("❌ 類股批次下載結果為空")
        return [], []

    benchmark_daily = extract_yfinance_data(
        downloaded,
        SECTOR_BENCHMARK
    )

    benchmark_daily = clean_ohlcv_dataframe(
        benchmark_daily
    )

    benchmark_weekly = convert_daily_to_weekly(
        benchmark_daily,
        drop_incomplete_week=True
    )

    if benchmark_weekly.empty:
        print("❌ SPY 週線資料不足")
        return [], []

    benchmark_return_13w = calculate_return(
        benchmark_weekly["Close"],
        13
    )

    sector_results = []

    for config in SECTOR_CONFIGS:
        ticker = config["ticker"]
        name = config["name"]

        try:
            daily_df = extract_yfinance_data(
                downloaded,
                ticker
            )

            daily_df = clean_ohlcv_dataframe(
                daily_df
            )

            weekly_df = convert_daily_to_weekly(
                daily_df,
                drop_incomplete_week=True
            )

            if (
                weekly_df.empty
                or len(weekly_df) < 61
            ):
                print(
                    f"⚠️ {ticker} 週線資料不足"
                )
                continue

            for ma_window in SECTOR_WEEKLY_MA_LIST:
                weekly_df[f"MA{ma_window}"] = (
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
                weekly_df["Close"].iloc[-1]
            )

            latest_volume = safe_float(
                weekly_df["Volume"].iloc[-1]
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

            # 使用前十週平均量作為比較基準，
            # 避免當週成交量被納入平均後稀釋。
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

            trend_status = determine_sector_trend(
                latest_close,
                ma20,
                ma60
            )

            volume_status = (
                determine_sector_price_volume_status(
                    weekly_df,
                    return_1w,
                    volume_ratio
                )
            )

            # 類股動能分數：
            # 20% 一週、25% 四週、30% 十三週、
            # 25% 相對 SPY 十三週強弱。
            momentum_score = (
                return_1w * 0.20
                + return_4w * 0.25
                + return_13w * 0.30
                + relative_strength_13w * 0.25
            )

            title = (
                f"(完整週K | "
                f"本週:{return_1w:+.2f}% | "
                f"4週:{return_4w:+.2f}% | "
                f"13週:{return_13w:+.2f}% | "
                f"相對SPY:{relative_strength_13w:+.2f}% | "
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
                    "sort_order": config["sort_order"],
                    "volume": int(latest_volume),
                    "latest_close": latest_close,
                    "return_1w": return_1w,
                    "return_4w": return_4w,
                    "return_13w": return_13w,
                    "benchmark_return_13w": (
                        benchmark_return_13w
                    ),
                    "relative_strength_13w": (
                        relative_strength_13w
                    ),
                    "volume_ratio": volume_ratio,
                    "volume_change_pct": (
                        volume_change_pct
                    ),
                    "volume_status": volume_status,
                    "trend_status": trend_status,
                    "momentum_score": momentum_score,
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
                f"相對SPY="
                f"{relative_strength_13w:+.2f}%"
            )

        except Exception as exc:
            print(
                f"❌ 類股處理失敗："
                f"{ticker} / "
                f"{type(exc).__name__}: {exc}"
            )

    sector_results.sort(
        key=lambda item: item["momentum_score"],
        reverse=True
    )

    sector_summary = []

    for rank, item in enumerate(
        sector_results,
        start=1
    ):
        item["rank"] = rank

        if rank <= 3:
            strength_group = "strong"
            strength_label = "強勢領先"
        elif rank > max(
            0,
            len(sector_results) - 3
        ):
            strength_group = "weak"
            strength_label = "弱勢落後"
        else:
            strength_group = "neutral"
            strength_label = "中性輪動"

        item["strength_group"] = strength_group
        item["strength_label"] = strength_label

        sector_summary.append(
            {
                "rank": rank,
                "ticker": item["ticker"],
                "name": item["name"],
                "return_1w": item["return_1w"],
                "return_4w": item["return_4w"],
                "return_13w": item["return_13w"],
                "relative_strength_13w": (
                    item["relative_strength_13w"]
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
                "strength_group": strength_group,
                "strength_label": strength_label
            }
        )

    print(
        f"📊 類股週 K 圖表數量："
        f"{len(sector_results)}"
    )

    return sector_results, sector_summary


def build_sector_monday_message(
    today_str,
    sector_summary,
    report_url
):
    if not sector_summary:
        return (
            f"📅 {today_str} 美股類股週線輪動\n\n"
            "⚠️ 本週類股資料不足，"
            "請至網頁稍後重新查看。\n\n"
            f"🔗 完整圖表：\n{report_url}"
        )

    strongest = sector_summary[:3]
    weakest = sector_summary[-3:]

    lines = [
        f"📅 {today_str} 美股類股週線輪動",
        "📊 週均線：MA20 / MA60",
        "📌 排名依據：1週、4週、13週與相對SPY強弱",
        "",
        "🟢 【強勢前三名】"
    ]

    for item in strongest:
        lines.append(
            f"{item['rank']}. "
            f"{item['ticker']} "
            f"{item['name']}\n"
            f"   ├ 13週："
            f"{item['return_13w']:+.2f}%\n"
            f"   ├ 相對SPY："
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
            f"   ├ 13週："
            f"{item['return_13w']:+.2f}%\n"
            f"   ├ 相對SPY："
            f"{item['relative_strength_13w']:+.2f}%\n"
            f"   └ {item['trend_status']}｜"
            f"{item['volume_status']}"
        )

    inflow_items = [
        item
        for item in sector_summary
        if "資金流入" in item["volume_status"]
    ]

    outflow_items = [
        item
        for item in sector_summary
        if "資金流出" in item["volume_status"]
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
        lines.append("資金流入：目前無明顯類股")

    if outflow_items:
        lines.append(
            "資金流出："
            + "、".join(
                item["name"]
                for item in outflow_items
            )
        )
    else:
        lines.append("資金流出：目前無明顯類股")

    lines.extend(
        [
            "",
            "🔗 查看完整類股週K：",
            report_url
        ]
    )

    return "\n".join(lines)


# =========================================================================
# HTML 產生
# =========================================================================
def generate_sector_overview_html(sector_summary):
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
                if item.get("strength_group") == "strong"
            ]
        ),
        (
            "neutral",
            "🟡 中性輪動",
            [
                item
                for item in sector_summary
                if item.get("strength_group") == "neutral"
            ]
        ),
        (
            "weak",
            "🔴 弱勢落後",
            [
                item
                for item in sector_summary
                if item.get("strength_group") == "weak"
            ]
        )
    ]

    html = """
<div class="sector-overview">
    <div class="sector-overview-title">
        🧭 美股 11 大類股週線強弱總覽
    </div>
    <div class="sector-overview-subtitle">
        固定週均線 MA20 / MA60，
        排名綜合 1週、4週、13週報酬與相對 SPY 強弱
    </div>
    <div class="sector-summary-grid">
"""

    for group_class, group_title, items in groups:
        html += (
            f'<div class="sector-summary-column '
            f'{group_class}">'
            f'<div class="sector-summary-title">'
            f'{group_title}'
            f'</div>'
        )

        if not items:
            html += (
                '<div class="sector-summary-empty">'
                '目前沒有類股'
                '</div>'
            )

        for item in items:
            relative_class = (
                "positive"
                if item["relative_strength_13w"] >= 0
                else "negative"
            )

            html += (
                '<div class="sector-summary-item">'
                '<div class="sector-summary-head">'
                f'<span class="sector-rank">'
                f'#{item["rank"]}'
                '</span>'
                f'<span class="sector-symbol">'
                f'{item["ticker"]}'
                '</span>'
                f'<span class="sector-name">'
                f'{item["name"]}'
                '</span>'
                '</div>'
                '<div class="sector-summary-metrics">'
                f'<span>1週 '
                f'{item["return_1w"]:+.2f}%</span>'
                f'<span>4週 '
                f'{item["return_4w"]:+.2f}%</span>'
                f'<span>13週 '
                f'{item["return_13w"]:+.2f}%</span>'
                f'<span class="{relative_class}">'
                f'相對SPY '
                f'{item["relative_strength_13w"]:+.2f}%'
                '</span>'
                '</div>'
                '<div class="sector-summary-status">'
                f'{item["trend_status"]}｜'
                f'{item["volume_status"]}'
                '</div>'
                '</div>'
            )

        html += "</div>"

    html += """
    </div>
</div>
"""

    return html


def generate_html(
    data_dict,
    date_str,
    sector_summary=None
):
    sector_summary = sector_summary or []

    clean_data = clean_json_value(data_dict)

    chart_json = json.dumps(
        clean_data,
        ensure_ascii=False,
        allow_nan=False
    ).replace("</script>", "<\\/script>")

    html = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>台美股均線潛伏報告</title>
<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>
<script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>

<style>
body {
    background:
        radial-gradient(
            circle at top,
            #182033 0,
            #0b0e14 45%,
            #080a0f 100%
        );
    color: #f8fafc;
    font-family:
        Arial,
        "Noto Sans TC",
        sans-serif;
    margin: 0;
    padding: 16px;
}

.header {
    max-width: 1100px;
    margin: 0 auto 18px;
    padding: 22px 18px;
    text-align: center;
    background: rgba(19, 23, 34, 0.92);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 14px;
    box-sizing: border-box;
    box-shadow:
        0 12px 35px rgba(0, 0, 0, 0.3);
}

.header h2 {
    margin: 0;
    font-size: 24px;
    letter-spacing: 0.5px;
}

.header p {
    margin: 8px 0 0;
}

.category-box {
    max-width: 1100px;
    margin: 0 auto 14px;
    padding: 14px 16px;
    background: rgba(19, 23, 34, 0.9);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-left: 4px solid #00b0ff;
    border-radius: 12px;
    box-sizing: border-box;
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

.tab-btn {
    padding: 9px 14px;
    color: #94a3b8;
    background: #1e293b;
    border: 1px solid rgba(255, 255, 255, 0.06);
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
    border-color: rgba(56, 189, 248, 0.35);
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
        0 5px 16px rgba(37, 99, 235, 0.3);
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

.market-section {
    display: none;
    max-width: 1100px;
    margin: 0 auto;
}

.market-section.active {
    display: block;
}

.sector-overview {
    margin-bottom: 18px;
    padding: 16px;
    background: rgba(15, 23, 42, 0.95);
    border: 1px solid rgba(52, 211, 153, 0.22);
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
    background: rgba(30, 41, 59, 0.62);
    border: 1px solid rgba(148, 163, 184, 0.14);
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
    background: rgba(15, 23, 42, 0.75);
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
    background: #131722;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    box-sizing: border-box;
    box-shadow:
        0 12px 30px rgba(0, 0, 0, 0.32);
    transition:
        border-color 0.2s ease,
        transform 0.2s ease;
}

.chart-card:hover {
    border-color: rgba(56, 189, 248, 0.35);
    transform: translateY(-2px);
}

.chart-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-height: 48px;
    padding: 8px 10px;
    background: #111827;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px 10px 0 0;
    box-sizing: border-box;
}

.zoom-buttons {
    display: flex;
    flex-shrink: 0;
    gap: 6px;
}

.chart-tool-btn {
    padding: 7px 11px;
    color: #e2e8f0;
    background: #1e293b;
    border: 1px solid rgba(148, 163, 184, 0.22);
    border-radius: 7px;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    touch-action: manipulation;
}

.chart-tool-btn:hover {
    color: #ffffff;
    background: #334155;
    border-color: #38bdf8;
}

.chart-tool-btn.reset-btn {
    color: #ddd6fe;
    border-color: rgba(168, 85, 247, 0.38);
}

.ohlcv-fixed-panel {
    display: flex;
    flex: 1;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: 4px 12px;
    min-width: 0;
    color: #cbd5e1;
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
    text-align: center;
    color: #64748b;
    background: rgba(19, 23, 34, 0.75);
    border: 1px dashed rgba(148, 163, 184, 0.25);
    border-radius: 12px;
}

.test-notice {
    max-width: 1100px;
    margin: 0 auto 15px;
    padding: 11px 16px;
    color: #fde68a;
    text-align: center;
    background: rgba(120, 53, 15, 0.35);
    border: 1px solid rgba(245, 158, 11, 0.3);
    border-radius: 10px;
    box-sizing: border-box;
}

.plotly-error {
    padding: 40px 15px;
    color: #fca5a5;
    text-align: center;
    background: rgba(127, 29, 29, 0.2);
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

    .chart-toolbar {
        align-items: stretch;
        flex-direction: column;
        gap: 8px;
        padding: 8px;
    }

    .zoom-buttons {
        width: 100%;
    }

    .chart-tool-btn {
        flex: 1;
        padding: 8px 5px;
        font-size: 11px;
    }

    .ohlcv-fixed-panel {
        justify-content: flex-start;
        min-height: 42px;
        padding: 2px 4px;
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

    <p style="color:#00ff88;font-size:13px;">
        專屬訂製滾動數據儲存版
    </p>
</div>
"""

    html = html.replace(
        "__REPORT_DATE__",
        str(date_str)
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
            全市場潛伏 ({len(data_dict.get("tw_all", []))})
        </button>

        <button
            id="btn-tw_g1"
            class="tab-btn"
            onclick="switchMarket(event, 'tw_g1')"
        >
            權值精選 ({len(data_dict.get("tw_g1", []))})
        </button>

        <button
            id="btn-tw_g2"
            class="tab-btn"
            onclick="switchMarket(event, 'tw_g2')"
        >
            熱門 ({len(data_dict.get("tw_g2", []))})
        </button>
    </div>
</div>

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
            全市場潛伏 ({len(data_dict.get("us_all", []))})
        </button>

        <button
            id="btn-us_g1"
            class="tab-btn"
            onclick="switchMarket(event, 'us_g1')"
        >
            權值精選 ({len(data_dict.get("us_g1", []))})
        </button>

        <button
            id="btn-us_g2"
            class="tab-btn"
            onclick="switchMarket(event, 'us_g2')"
        >
            低本益比 ({len(data_dict.get("us_g2", []))})
        </button>

        <button
            id="btn-us_g3"
            class="tab-btn"
            onclick="switchMarket(event, 'us_g3')"
        >
            超級績效 ({len(data_dict.get("us_g3", []))})
        </button>

        <button
            id="btn-us_g4"
            class="tab-btn"
            onclick="switchMarket(event, 'us_g4')"
        >
            熱門 ({len(data_dict.get("us_g4", []))})
        </button>
    </div>
</div>
"""

    keys = [
        "indices",
        "sectors",
        "tw_all",
        "tw_g1",
        "tw_g2",
        "us_all",
        "us_g1",
        "us_g2",
        "us_g3",
        "us_g4"
    ]

    for key in keys:
        active_class = (
            " active"
            if key == "tw_all"
            else ""
        )

        html += (
            f'<div id="{key}-market" '
            f'class="market-section{active_class}">'
        )

        if key == "sectors":
            html += generate_sector_overview_html(
                sector_summary
            )

        items = data_dict.get(key, [])

        if items:
            for index in range(len(items)):
                chart_id = f"chart-{key}-{index}"
                info_id = f"info-{key}-{index}"

                timeframe_text = (
                    "點擊週 K 後固定顯示週期與 OHLCV"
                    if key == "sectors"
                    else "點擊 K 線後固定顯示日期與 OHLCV"
                )

                html += (
                    '<div class="chart-card">'

                    '<div class="chart-toolbar">'

                    '<div class="zoom-buttons">'

                    '<button '
                    'type="button" '
                    'class="chart-tool-btn" '
                    f'onclick="zoomChart('
                    f'\'{chart_id}\', 0.7)">'
                    '＋ 放大'
                    '</button>'

                    '<button '
                    'type="button" '
                    'class="chart-tool-btn" '
                    f'onclick="zoomChart('
                    f'\'{chart_id}\', 1.4)">'
                    '－ 縮小'
                    '</button>'

                    '<button '
                    'type="button" '
                    'class="chart-tool-btn reset-btn" '
                    f'onclick="resetChart('
                    f'\'{chart_id}\')">'
                    '↺ 重設'
                    '</button>'

                    '</div>'

                    f'<div id="{info_id}" '
                    'class="ohlcv-fixed-panel">'

                    '<span class="ohlcv-placeholder">'
                    f'{timeframe_text}'
                    '</span>'

                    '</div>'
                    '</div>'

                    f'<div id="{chart_id}" '
                    f'data-market-id="{key}" '
                    f'data-chart-index="{index}" '
                    'class="plotly-container"></div>'

                    '</div>'
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
            else:
                no_data_text = (
                    "此分類目前沒有可顯示的股票"
                )

            html += (
                '<div class="no-data">'
                f"{no_data_text}"
                "</div>"
            )

        html += "</div>"

    html += """
<script>
const chartDataStore = __CHART_JSON__;

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

    return Math.round(numberValue).toLocaleString(
        "zh-TW"
    );
}

function getChartItem(container) {
    if (!container) {
        return null;
    }

    const marketId = container.dataset.marketId;
    const chartIndex = Number(
        container.dataset.chartIndex
    );

    if (
        !marketId
        || !Number.isInteger(chartIndex)
        || !Array.isArray(chartDataStore[marketId])
    ) {
        return null;
    }

    return chartDataStore[marketId][chartIndex] || null;
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

    if (typeof rangeValue === "string") {
        const normalizedDate = rangeValue.substring(0, 10);
        const foundIndex = dateList.indexOf(normalizedDate);

        if (foundIndex >= 0) {
            return foundIndex;
        }
    }

    return fallbackValue;
}

function zoomChart(chartId, factor) {
    const container = document.getElementById(
        chartId
    );

    if (
        !container
        || container.dataset.done !== "true"
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    const item = getChartItem(container);

    if (
        !item
        || !item.chart_data
        || !Array.isArray(item.chart_data.ohlcv)
    ) {
        return;
    }

    const dateList = item.chart_data.ohlcv.map(
        (record) => record.date
    );

    const totalPoints = dateList.length;

    if (totalPoints <= 1) {
        return;
    }

    const xaxisLayout = (
        container.layout
        && container.layout.xaxis
    )
        ? container.layout.xaxis
        : {};

    const currentRange = Array.isArray(
        xaxisLayout.range
    )
        ? xaxisLayout.range
        : [
            -0.5,
            totalPoints - 0.5
        ];

    let rangeStart = categoryRangeToIndex(
        currentRange[0],
        dateList,
        -0.5
    );

    let rangeEnd = categoryRangeToIndex(
        currentRange[1],
        dateList,
        totalPoints - 0.5
    );

    if (rangeEnd < rangeStart) {
        const temporaryValue = rangeStart;
        rangeStart = rangeEnd;
        rangeEnd = temporaryValue;
    }

    const currentWidth = Math.max(
        1,
        rangeEnd - rangeStart
    );

    const minimumWidth = Math.min(
        8,
        Math.max(1, totalPoints - 1)
    );

    const maximumWidth = totalPoints;

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

    let nextStart = center - nextWidth / 2;
    let nextEnd = center + nextWidth / 2;

    const minimumRange = -0.5;
    const maximumRange = totalPoints - 0.5;

    if (nextStart < minimumRange) {
        nextEnd += minimumRange - nextStart;
        nextStart = minimumRange;
    }

    if (nextEnd > maximumRange) {
        nextStart -= nextEnd - maximumRange;
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
    const container = document.getElementById(
        chartId
    );

    if (
        !container
        || container.dataset.done !== "true"
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    const update = {
        "xaxis.autorange": true,
        "yaxis.autorange": true
    };

    if (
        container.layout
        && container.layout.yaxis2
    ) {
        update["yaxis2.autorange"] = true;
    }

    Plotly.relayout(
        container,
        update
    );
}

function findOhlcvRecord(item, clickedDate) {
    if (
        !item
        || !item.chart_data
        || !Array.isArray(item.chart_data.ohlcv)
    ) {
        return null;
    }

    const normalizedDate = String(
        clickedDate || ""
    ).substring(0, 10);

    return (
        item.chart_data.ohlcv.find(
            (row) => row.date === normalizedDate
        )
        || null
    );
}

function markSelectedDate(container, record) {
    if (
        !container
        || !record
        || typeof Plotly === "undefined"
    ) {
        return;
    }

    const originalShapes = Array.isArray(
        container._baseShapes
    )
        ? container._baseShapes
        : [];

    const selectedShape = {
        type: "line",
        xref: "x",
        yref: "paper",
        x0: record.date,
        x1: record.date,
        y0: 0,
        y1: 1,
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
            + formatPrice(record.close)
        ),
        showarrow: true,
        arrowhead: 2,
        arrowsize: 1,
        arrowwidth: 1,
        arrowcolor: "#facc15",
        ax: 0,
        ay: -38,
        bgcolor: "rgba(17,24,39,0.95)",
        bordercolor: "#facc15",
        borderwidth: 1,
        borderpad: 4,
        font: {
            color: "#f8fafc",
            size: 10
        }
    };

    Plotly.relayout(
        container,
        {
            shapes: [
                ...originalShapes,
                selectedShape
            ],
            annotations: [
                selectedAnnotation
            ]
        }
    );
}

function showFixedOhlcv(
    container,
    clickedDate
) {
    const item = getChartItem(container);

    const record = findOhlcvRecord(
        item,
        clickedDate
    );

    if (!record) {
        return;
    }

    const marketId = container.dataset.marketId;
    const chartIndex = container.dataset.chartIndex;

    const infoPanel = document.getElementById(
        "info-"
        + marketId
        + "-"
        + chartIndex
    );

    if (!infoPanel) {
        return;
    }

    const openValue = Number(record.open);
    const closeValue = Number(record.close);

    let directionClass = "ohlcv-flat";
    let directionText = "平盤";
    let changeText = "--";

    if (
        Number.isFinite(openValue)
        && Number.isFinite(closeValue)
    ) {
        const changeValue = closeValue - openValue;

        if (closeValue > openValue) {
            directionClass = "ohlcv-up";
            directionText = "上漲";
        } else if (closeValue < openValue) {
            directionClass = "ohlcv-down";
            directionText = "下跌";
        }

        if (openValue !== 0) {
            const changePercent = (
                changeValue / openValue
            ) * 100;

            changeText = (
                (changeValue >= 0 ? "+" : "")
                + formatPrice(changeValue)
                + " / "
                + (changePercent >= 0 ? "+" : "")
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

    container.dataset.selectedDate = record.date;

    markSelectedDate(
        container,
        record
    );
}

function bindChartClickEvent(container) {
    if (
        !container
        || container.dataset.clickBound === "true"
    ) {
        return;
    }

    container.on(
        "plotly_click",
        (eventData) => {
            if (
                !eventData
                || !Array.isArray(eventData.points)
                || eventData.points.length === 0
            ) {
                return;
            }

            const clickedPoint = eventData.points[0];

            if (
                !clickedPoint
                || clickedPoint.x === undefined
                || clickedPoint.x === null
            ) {
                return;
            }

            showFixedOhlcv(
                container,
                clickedPoint.x
            );
        }
    );

    container.dataset.clickBound = "true";
}

function renderMarketCharts(marketId) {
    const items = chartDataStore[marketId];

    if (!items || !Array.isArray(items)) {
        return;
    }

    if (typeof Plotly === "undefined") {
        console.error("Plotly 載入失敗");

        const section = document.getElementById(
            marketId + "-market"
        );

        if (section) {
            section.innerHTML = (
                '<div class="plotly-error">'
                + 'Plotly 圖表套件載入失敗，'
                + '請重新整理頁面或檢查網路連線。'
                + '</div>'
            );
        }

        return;
    }

    items.forEach((item, index) => {
        const id = (
            "chart-"
            + marketId
            + "-"
            + index
        );

        const container = document.getElementById(id);

        if (
            !container
            || container.dataset.done === "true"
        ) {
            return;
        }

        if (
            !item
            || !item.chart_data
            || !Array.isArray(item.chart_data.data)
        ) {
            container.innerHTML = (
                '<div class="plotly-error">'
                + '圖表資料格式錯誤'
                + '</div>'
            );

            return;
        }

        container.dataset.marketId = marketId;
        container.dataset.chartIndex = String(index);

        const originalLayout = (
            item.chart_data.layout || {}
        );

        const categoryDates = (
            Array.isArray(item.chart_data.ohlcv)
                ? item.chart_data.ohlcv.map(
                    (record) => record.date
                )
                : []
        );

        const layout = {
            ...originalLayout,
            margin: {
                ...(originalLayout.margin || {})
            },
            title: {
                ...(originalLayout.title || {})
            },
            legend: {
                ...(originalLayout.legend || {})
            },
            xaxis: {
                ...(originalLayout.xaxis || {}),
                type: "category",
                categoryorder: "array",
                categoryarray: categoryDates,
                rangeslider: {
                    visible: false
                },
                fixedrange: false
            },
            yaxis: {
                ...(originalLayout.yaxis || {}),
                fixedrange: false
            },
            dragmode: "pan"
        };

        if (originalLayout.yaxis2) {
            layout.yaxis2 = {
                ...originalLayout.yaxis2,
                fixedrange: false
            };
        }

        const hasVolume = Boolean(
            originalLayout.yaxis2
        );

        if (window.innerWidth <= 600) {
            layout.height = hasVolume ? 470 : 390;

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
                || (hasVolume ? 520 : 440)
            );
        }

        const config = {
            responsive: true,
            displayModeBar: true,
            displaylogo: false,
            scrollZoom: true,
            doubleClick: "reset",
            showTips: false,
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
        .then(() => {
            container.dataset.done = "true";

            container._baseShapes = Array.isArray(
                originalLayout.shapes
            )
                ? originalLayout.shapes.map(
                    (shape) => ({
                        ...shape
                    })
                )
                : [];

            bindChartClickEvent(container);

            requestAnimationFrame(() => {
                Plotly.Plots.resize(container);
            });
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
    });
}

function resizeMarketCharts(marketId) {
    const items = chartDataStore[marketId];

    if (!items || !Array.isArray(items)) {
        return;
    }

    items.forEach((item, index) => {
        const id = (
            "chart-"
            + marketId
            + "-"
            + index
        );

        const container = document.getElementById(id);

        if (
            container
            && container.dataset.done === "true"
            && typeof Plotly !== "undefined"
        ) {
            Plotly.Plots.resize(container);
        }
    });
}

function switchMarket(event, marketId) {
    document
        .querySelectorAll(".market-section")
        .forEach((element) => {
            element.classList.remove("active");
        });

    document
        .querySelectorAll(".tab-btn")
        .forEach((element) => {
            element.classList.remove("active");
        });

    const section = document.getElementById(
        marketId + "-market"
    );

    if (section) {
        section.classList.add("active");
    }

    if (event && event.currentTarget) {
        event.currentTarget.classList.add("active");
    } else {
        const button = document.getElementById(
            "btn-" + marketId
        );

        if (button) {
            button.classList.add("active");
        }
    }

    renderMarketCharts(marketId);

    setTimeout(() => {
        resizeMarketCharts(marketId);
    }, 150);
}

window.addEventListener("load", () => {
    renderMarketCharts("tw_all");
});

window.addEventListener("resize", () => {
    const activeSection = document.querySelector(
        ".market-section.active"
    );

    if (!activeSection) {
        return;
    }

    const marketId = activeSection.id.replace(
        "-market",
        ""
    );

    resizeMarketCharts(marketId);
});
</script>
</body>
</html>
"""

    html = html.replace(
        "__CHART_JSON__",
        chart_json
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

    print(f"✅ HTML 已產生：{path}")


# =========================================================================
# 大盤多空趨勢分析
# =========================================================================
def analyze_index_trend(ticker, name, ma_list):
    if not ma_list:
        return f"⚪ {name}: 未設定任何均線參數"

    try:
        df = yf.download(
            ticker,
            period="4y",
            progress=False,
            threads=False,
            auto_adjust=False
        )

        if df.empty or len(df) < 750:
            return f"⚪ {name}: 數據不足無法分析"

        if isinstance(df.columns, pd.MultiIndex):
            df = extract_yfinance_data(
                df,
                ticker
            )

        df = clean_ohlcv_dataframe(df)

        if df.empty or len(df) < 750:
            return f"⚪ {name}: 數據不足無法分析"

        available_mas = []

        for ma in ma_list:
            col_name = f"MA{ma}"

            df[col_name] = (
                df["Close"]
                .rolling(window=ma)
                .mean()
            )

            available_mas.append(col_name)

        df = df.dropna(
            subset=available_mas
        )

        if len(df) < 5:
            return f"⚪ {name}: 計算後可用數據小於5日"

        latest = df.iloc[-1]
        score = 0
        total_ma_count = len(available_mas)
        df_last5 = df.tail(5)

        for ma_col in available_mas:
            touch_count = 0

            for _, row_5 in df_last5.iterrows():
                if (
                    row_5["Low"]
                    <= row_5[ma_col]
                    <= row_5["High"]
                ):
                    touch_count += 1

            if touch_count < 3:
                latest_close = float(
                    latest["Close"]
                )

                latest_ma = float(
                    latest[ma_col]
                )

                if latest_close > latest_ma * 1.001:
                    score += 1
                elif latest_close < latest_ma * 0.999:
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

        df_3y = df.tail(252 * 3)

        idx_3y_high = df_3y[
            "High"
        ].idxmax()

        latest_date = df.index[-1]

        months_since_high = (
            latest_date - idx_3y_high
        ).days / 30.0

        df_recent = df.tail(120).copy()

        peaks = []
        troughs = []

        for index in range(
            2,
            len(df_recent) - 2
        ):
            current_high = float(
                df_recent["High"].iloc[index]
            )

            if (
                current_high
                > float(df_recent["High"].iloc[index - 1])
                and current_high
                > float(df_recent["High"].iloc[index - 2])
                and current_high
                > float(df_recent["High"].iloc[index + 1])
                and current_high
                > float(df_recent["High"].iloc[index + 2])
            ):
                peaks.append(
                    (
                        df_recent.index[index],
                        current_high
                    )
                )

            current_low = float(
                df_recent["Low"].iloc[index]
            )

            if (
                current_low
                < float(df_recent["Low"].iloc[index - 1])
                and current_low
                < float(df_recent["Low"].iloc[index - 2])
                and current_low
                < float(df_recent["Low"].iloc[index + 1])
                and current_low
                < float(df_recent["Low"].iloc[index + 2])
            ):
                troughs.append(
                    (
                        df_recent.index[index],
                        current_low
                    )
                )

        lower_peak_count = 0
        lower_trough_count = 0

        for index in range(1, len(peaks)):
            if peaks[index][1] < peaks[index - 1][1]:
                lower_peak_count += 1

        for index in range(1, len(troughs)):
            if (
                troughs[index][1]
                < troughs[index - 1][1]
            ):
                lower_trough_count += 1

        macro_trend = "多頭趨勢"

        if months_since_high >= 4.0:
            df_bear_period = df.loc[
                idx_3y_high:latest_date
            ]

            if len(df_bear_period) > 5:
                bear_low = float(
                    df_bear_period["Low"]
                    .iloc[:-1]
                    .min()
                )

                if float(latest["Close"]) < bear_low:
                    macro_trend = "空頭趨勢"

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
            f"{macro_trend}中的{micro_trend}"
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
            f"⚠️ {ticker} 大盤分析異常："
            f"{type(exc).__name__}: {exc}"
        )

        return f"⚪ {name}: 分析發生異常"


# =========================================================================
# GitHub Pages 推送
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
        return

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
        print("ℹ️ 沒有 Git 異動需要提交")
        return

    commit_result = run_git_command(
        [
            "git",
            "commit",
            "-m",
            (
                "⚙️ 量化報告自動更新 "
                "(新增11大類股週K輪動)"
            )
        ]
    )

    if commit_result != 0:
        print("❌ git commit 失敗")
        return

    push_result = run_git_command(
        [
            "git",
            "push"
        ]
    )

    if push_result == 0:
        print("✅ GitHub Pages 資料推送完成")
    else:
        print("❌ git push 失敗")


# =========================================================================
# 主程式
# =========================================================================
def main():
    access_token = os.environ.get(
        "LINE_ACCESS_TOKEN"
    )

    line_push_user_id = os.environ.get(
        "LINE_USER_ID"
    )

    supabase_user_id = (
        os.environ.get("SUPABASE_USER_ID")
        or line_push_user_id
    )

    if not line_push_user_id:
        print("❌ 未設定 LINE_USER_ID")
        return

    if not supabase_user_id:
        print("❌ 未設定 SUPABASE_USER_ID")
        return

    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    weekday = today.weekday()

    user_configs, db_index_configs = (
        load_configs_from_supabase(
            supabase_user_id
        )
    )

    print("📊 開始掃描台股全市場")

    tw_all = scan_market(
        get_tw_tickers(TW_MIN_VOLUME),
        min_volume=TW_MIN_VOLUME
    )

    print("📊 開始掃描美股全市場")

    us_all = scan_market(
        get_us_tickers(),
        min_volume=US_MIN_VOLUME
    )

    print("🌍 開始建立全球指數圖表")

    index_charts = process_index_charts(
        db_index_configs
    )

    print("🧭 開始建立類股週 K 圖表")

    sector_charts, sector_summary = (
        process_sector_weekly_charts()
    )

    data_dict = {
        "indices": index_charts,
        "sectors": sector_charts,
        "tw_all": tw_all,
        "tw_g1": process_custom_groups(
            user_configs["tw_g1"],
            "tw_g1",
            test_mode=CUSTOM_GROUP_TEST_MODE
        ),
        "tw_g2": process_custom_groups(
            user_configs["tw_g2"],
            "tw_g2",
            test_mode=CUSTOM_GROUP_TEST_MODE
        ),
        "us_all": us_all,
        "us_g1": process_custom_groups(
            user_configs["us_g1"],
            "us_g1",
            test_mode=CUSTOM_GROUP_TEST_MODE
        ),
        "us_g2": process_custom_groups(
            user_configs["us_g2"],
            "us_g2",
            test_mode=CUSTOM_GROUP_TEST_MODE
        ),
        "us_g3": process_custom_groups(
            user_configs["us_g3"],
            "us_g3",
            test_mode=CUSTOM_GROUP_TEST_MODE
        ),
        "us_g4": process_custom_groups(
            user_configs["us_g4"],
            "us_g4",
            test_mode=CUSTOM_GROUP_TEST_MODE
        )
    }

    print("\n===== 最終圖表數量 =====")

    for key, items in data_dict.items():
        print(f"{key}: {len(items)} 檔")

    print("========================\n")

    generate_html(
        data_dict,
        today_str,
        sector_summary=sector_summary
    )

    report_url = (
        "https://wudn9922.github.io/"
        "my-stock-screener/"
    )

    liff_setting_url = (
        "https://liff.line.me/"
        "2010330411-SbwvRXRN"
    )

    bitget_setting_url = (
        "https://liff.line.me/"
        "2010535859-y3CoAWaC"
    )

    line_message_stocks = (
        f"🎯 {today_str} 專屬量化看盤網頁！\n\n"
        f"🌍 【全球指數區塊】\n"
        f" └ 指數圖表："
        f"{len(data_dict['indices'])} 張\n\n"
        f"🧭 【美股類股週K】\n"
        f" └ 類股圖表："
        f"{len(data_dict['sectors'])} 張\n\n"
        f"🇹🇼 【台灣股市區塊】\n"
        f" ├ 1. 全市場符合："
        f"{len(data_dict['tw_all'])} 檔\n"
        f" ├ 2. 權值精選符合："
        f"{len(data_dict['tw_g1'])} 檔\n"
        f" └ 3. 熱門符合："
        f"{len(data_dict['tw_g2'])} 檔\n\n"
        f"🇺🇸 【美國股市區塊】\n"
        f" ├ 1. 全市場符合："
        f"{len(data_dict['us_all'])} 檔\n"
        f" ├ 2. 權值精選符合："
        f"{len(data_dict['us_g1'])} 檔\n"
        f" ├ 3. 低本益比符合："
        f"{len(data_dict['us_g2'])} 檔\n"
        f" ├ 4. 超級績效符合："
        f"{len(data_dict['us_g3'])} 檔\n"
        f" └ 5. 熱門符合："
        f"{len(data_dict['us_g4'])} 檔\n\n"
        f"🔗 1. 專屬潛伏圖表網頁：\n"
        f"{report_url}\n\n"
        f"⚙️ 2. 手機自訂參數控制台：\n"
        f"{liff_setting_url}\n\n"
        f"💰 3. 自動交易參數控制台：\n"
        f"{bitget_setting_url}"
    )

    send_line_message(
        line_message_stocks,
        access_token,
        line_push_user_id
    )

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

    index_map = {
        str(item.get("ticker", "")).strip(): item
        for item in db_index_configs
        if item.get("ticker")
        and is_config_enabled(item)
    }

    index_lines = [
        f"🌍 {today_str} 全球大盤多空量化報告",
        "📊 評分標準: 均線糾纏自適應/0.1%過濾機制",
        "========================",
        "",
        "【 🇹🇼 台灣市場 】"
    ]

    for ticker in tw_indices:
        if ticker in index_map:
            item = index_map[ticker]

            index_lines.append(
                analyze_index_trend(
                    ticker,
                    item.get("name", ticker),
                    get_ma_list_from_item(item)
                )
            )

    index_lines.extend(
        [
            "",
            "【 🇺🇸 美國市場 】"
        ]
    )

    for ticker in us_indices:
        if ticker in index_map:
            item = index_map[ticker]

            index_lines.append(
                analyze_index_trend(
                    ticker,
                    item.get("name", ticker),
                    get_ma_list_from_item(item)
                )
            )

    index_lines.extend(
        [
            "",
            "【 🇪🇺 歐洲市場 】"
        ]
    )

    for ticker in eu_indices:
        if ticker in index_map:
            item = index_map[ticker]

            index_lines.append(
                analyze_index_trend(
                    ticker,
                    item.get("name", ticker),
                    get_ma_list_from_item(item)
                )
            )

    index_lines.extend(
        [
            "",
            "【 🌏 亞洲市場 】"
        ]
    )

    for ticker in asia_indices:
        if ticker in index_map:
            item = index_map[ticker]

            index_lines.append(
                analyze_index_trend(
                    ticker,
                    item.get("name", ticker),
                    get_ma_list_from_item(item)
                )
            )

    send_line_message(
        "\n".join(index_lines),
        access_token,
        line_push_user_id
    )

    # 星期一限定推播：
    # 改為類股週線強弱摘要，不再使用 Finviz。
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

    push_report_to_github()


if __name__ == "__main__":
    main()

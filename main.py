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

# True：Supabase 自訂群組只要可下載就顯示
# False：套用均線距離條件
CUSTOM_GROUP_TEST_MODE = (
    os.environ.get("CUSTOM_GROUP_TEST_MODE", "false")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

# 正式模式的均線距離
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

    if isinstance(value, float):
        if pd.isna(value):
            return None

        if value in (float("inf"), float("-inf")):
            return None

    return value


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
        subset=required_cols
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
            "ma4": None
        },
        {
            "ticker": "^TWOII",
            "name": "台灣櫃買指數(OTC)",
            "ma1": 20,
            "ma2": 60,
            "ma3": 120,
            "ma4": None
        },
        {
            "ticker": "^GSPC",
            "name": "美國標普500",
            "ma1": 23,
            "ma2": 60,
            "ma3": None,
            "ma4": None
        },
        {
            "ticker": "^DJI",
            "name": "美國道瓊工業",
            "ma1": 20,
            "ma2": 23,
            "ma3": 55,
            "ma4": None
        },
        {
            "ticker": "^IXIC",
            "name": "美國那斯達克",
            "ma1": 29,
            "ma2": None,
            "ma3": None,
            "ma4": None
        },
        {
            "ticker": "^RUT",
            "name": "美國羅素2000",
            "ma1": 21,
            "ma2": 56,
            "ma3": None,
            "ma4": None
        },
        {
            "ticker": "^SOX",
            "name": "美國費城半導體",
            "ma1": 20,
            "ma2": 58,
            "ma3": 108,
            "ma4": None
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

            if not ticker:
                print("⚠️ 發現 ticker 為空的資料")
                continue

            if group_id == "admin_index":
                print(
                    f"ℹ️ 略過 admin_index：{ticker}"
                )
                continue

            mapped_key = group_id_to_key.get(group_id)

            # 支援 group_id 直接保存 tw_g1、us_g1
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

    print(
        "\n===== Supabase 自訂群組讀取結果 ====="
    )

    for group_key, stocks in configs.items():
        print(f"{group_key}: {len(stocks)} 檔")

        for ticker, ma_list in stocks.items():
            print(f"  └─ {ticker}: {ma_list}")

    print(
        "====================================\n"
    )

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
            print(
                f"LINE Request ID：{request_id}"
            )
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

                    if volume_col:
                        volume = float(
                            str(
                                row[volume_col]
                            ).replace(",", "")
                        )

                        if volume < min_volume:
                            continue

                    tickers.append(f"{code}.TW")

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
                    tickers.append(f"{code}.TWO")

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
    ma_list
):
    date_strings = [
        str(date)[:10]
        for date in df_chart.index
    ]

    traces = [
        {
            "type": "candlestick",
            "name": ticker,
            "x": date_strings,
            "open": [
                float(value)
                for value in df_chart["Open"].tolist()
            ],
            "high": [
                float(value)
                for value in df_chart["High"].tolist()
            ],
            "low": [
                float(value)
                for value in df_chart["Low"].tolist()
            ],
            "close": [
                float(value)
                for value in df_chart["Close"].tolist()
            ],
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

    layout = {
        "title": {
            "text": (
                f"<b>{ticker}</b>"
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
            "type": "date",
            "rangeslider": {
                "visible": False
            },
            "showgrid": True,
            "gridcolor": "rgba(255,255,255,0.055)",
            "gridwidth": 1,
            "showline": False,
            "zeroline": False,
            "tickfont": {
                "size": 10,
                "color": "#8b949e"
            },
            "hoverformat": "%Y-%m-%d",
            "spikemode": "across",
            "spikesnap": "cursor",
            "showspikes": True,
            "spikecolor": "#64748b",
            "spikethickness": 1,
            "rangebreaks": [
                {
                    "bounds": [
                        "sat",
                        "mon"
                    ]
                }
            ]
        },
        "yaxis": {
            "side": "right",
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
            "t": 100,
            "b": 35
        },
        "height": 440,
        "dragmode": "pan",
        "uirevision": ticker
    }

    return {
        "data": traces,
        "layout": layout
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

            # 保留目前全市場放大條件
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

            chart_data = build_stock_data(
                df.tail(60),
                ticker,
                title,
                [20]
            )

            matched_list.append(
                {
                    "ticker": ticker,
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
        return [ticker.replace(".", "-")]

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

            chart_data = build_stock_data(
                combined.tail(60),
                actual_ticker,
                title,
                ma_list
            )

            matched_list.append(
                {
                    "ticker": actual_ticker,
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
# HTML 產生
# =========================================================================
def generate_html(data_dict, date_str):
    clean_data = clean_json_value(data_dict)

    chart_json = json.dumps(
        clean_data,
        ensure_ascii=False,
        allow_nan=False
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>台美股均線潛伏報告</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
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
    box-shadow: 0 12px 35px rgba(0, 0, 0, 0.3);
}

.header h2 {
    margin: 0;
    font-size: 24px;
    letter-spacing: 0.5px;
}

.category-box {
    max-width: 1076px;
    margin: 0 auto 14px;
    padding: 14px 16px;
    background: rgba(19, 23, 34, 0.9);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-left: 4px solid #00b0ff;
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
        transform 0.2s ease;
}

.tab-btn:hover {
    color: #ffffff;
    background: #334155;
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
        rgba(37, 99, 235, 0.3);
}

.market-section {
    display: none;
    max-width: 1100px;
    margin: 0 auto;
}

.market-section.active {
    display: block;
}

.chart-card {
    overflow: hidden;
    margin-bottom: 20px;
    padding: 4px;
    background: #131722;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    box-shadow:
        0 12px 30px
        rgba(0, 0, 0, 0.32);
    transition:
        border-color 0.2s ease,
        transform 0.2s ease;
}

.chart-card:hover {
    border-color: rgba(56, 189, 248, 0.35);
    transform: translateY(-2px);
}

.plotly-container {
    width: 100%;
    height: 440px;
    background: #131722;
    border-radius: 12px;
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
    max-width: 1076px;
    margin: 0 auto 15px;
    padding: 11px 16px;
    color: #fde68a;
    text-align: center;
    background: rgba(120, 53, 15, 0.35);
    border: 1px solid rgba(245, 158, 11, 0.3);
    border-radius: 10px;
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

    .plotly-container {
        height: 390px;
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
    <h2>📈 台美股量化潛伏網頁報告 ({date_str})</h2>
    <p style="color:#00ff88;font-size:13px;">
        專屬訂製滾動數據儲存版
    </p>
</div>
"""

    if CUSTOM_GROUP_TEST_MODE:
        html += """
<div class="test-notice">
目前為 Supabase 測試模式，自訂群組不套用均線距離篩選
</div>
"""

    html += f"""
<div class="category-box" style="border-left-color:#ff5252;">
    <div class="category-title">🇹🇼 台灣股市區塊</div>
    <div class="tabs">
        <button id="btn-tw_all" class="tab-btn active"
            onclick="switchMarket(event,'tw_all')">
            全市場潛伏 ({len(data_dict["tw_all"])})
        </button>
        <button id="btn-tw_g1" class="tab-btn"
            onclick="switchMarket(event,'tw_g1')">
            權值精選 ({len(data_dict["tw_g1"])})
        </button>
        <button id="btn-tw_g2" class="tab-btn"
            onclick="switchMarket(event,'tw_g2')">
            熱門 ({len(data_dict["tw_g2"])})
        </button>
    </div>
</div>

<div class="category-box">
    <div class="category-title">🇺🇸 美國股市區塊</div>
    <div class="tabs">
        <button id="btn-us_all" class="tab-btn"
            onclick="switchMarket(event,'us_all')">
            全市場潛伏 ({len(data_dict["us_all"])})
        </button>
        <button id="btn-us_g1" class="tab-btn"
            onclick="switchMarket(event,'us_g1')">
            權值精選 ({len(data_dict["us_g1"])})
        </button>
        <button id="btn-us_g2" class="tab-btn"
            onclick="switchMarket(event,'us_g2')">
            低本益比 ({len(data_dict["us_g2"])})
        </button>
        <button id="btn-us_g3" class="tab-btn"
            onclick="switchMarket(event,'us_g3')">
            超級績效 ({len(data_dict["us_g3"])})
        </button>
        <button id="btn-us_g4" class="tab-btn"
            onclick="switchMarket(event,'us_g4')">
            熱門 ({len(data_dict["us_g4"])})
        </button>
    </div>
</div>
"""

    keys = [
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
        active = " active" if key == "tw_all" else ""

        html += (
            f'<div id="{key}-market" '
            f'class="market-section{active}">'
        )

        items = data_dict.get(key, [])

        if items:
            for index in range(len(items)):
                html += (
                    '<div class="chart-card">'
                    f'<div id="chart-{key}-{index}" '
                    'class="plotly-container"></div>'
                    '</div>'
                )
        else:
            html += (
                '<div class="no-data">'
                '此分類目前沒有可顯示的股票'
                '</div>'
            )

        html += "</div>"

    html += f"""
<script>
const chartDataStore = {chart_json};

function renderMarketCharts(marketId) {{
    const items = chartDataStore[marketId];

    if (!items || !Array.isArray(items)) {{
        return;
    }}

    items.forEach((item, index) => {{
        const id = "chart-" + marketId + "-" + index;
        const container = document.getElementById(id);

        if (!container || container.dataset.done) {{
            return;
        }}

        Plotly.newPlot(
            container,
            item.chart_data.data,
            item.chart_data.layout,
            {{
                responsive:true,
                displayModeBar:false
            }}
        );

        container.dataset.done = "true";
    }});
}}

function switchMarket(event, marketId) {{
    document.querySelectorAll(".market-section")
        .forEach((element) => {{
            element.classList.remove("active");
        }});

    document.querySelectorAll(".tab-btn")
        .forEach((element) => {{
            element.classList.remove("active");
        }});

    const section = document.getElementById(
        marketId + "-market"
    );

    if (section) {{
        section.classList.add("active");
    }}

    if (event && event.currentTarget) {{
        event.currentTarget.classList.add("active");
    }}

    renderMarketCharts(marketId);

    setTimeout(() => {{
        window.dispatchEvent(new Event("resize"));
    }}, 100);
}}

window.addEventListener("load", () => {{
    renderMarketCharts("tw_all");
}});
</script>
</body>
</html>
"""

    os.makedirs(DOCS_DIR, exist_ok=True)

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
# 保留原本的均線糾纏、三年高點、波峰波谷與大小趨勢判斷
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

        df = df.copy()

        if isinstance(df.columns, pd.MultiIndex):
            df = extract_yfinance_data(df, ticker)

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

        df = df.dropna(subset=available_mas)

        if len(df) < 5:
            return f"⚪ {name}: 計算後可用數據小於5日"

        latest = df.iloc[-1]
        score = 0
        total_ma_count = len(available_mas)
        df_last5 = df.tail(5)

        # -------------------------------------------------------------
        # 原本的均線糾纏自適應判斷
        # 最近五天至少三天 K 棒碰到均線，該均線計為中性
        # -------------------------------------------------------------
        for ma_col in available_mas:
            touch_count = 0

            for _, row_5 in df_last5.iterrows():
                if (
                    row_5["Low"]
                    <= row_5[ma_col]
                    <= row_5["High"]
                ):
                    touch_count += 1

            if touch_count >= 3:
                score += 0
            else:
                latest_close = float(latest["Close"])
                latest_ma = float(latest[ma_col])

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

        # -------------------------------------------------------------
        # 原本的三年高點與大小趨勢判斷
        # -------------------------------------------------------------
        df_3y = df.tail(252 * 3)

        idx_3y_high = df_3y["High"].idxmax()
        latest_date = df.index[-1]

        months_since_high = (
            latest_date - idx_3y_high
        ).days / 30.0

        # -------------------------------------------------------------
        # 原本的近 120 日波峰／波谷判斷
        # -------------------------------------------------------------
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

        if len(peaks) >= 2:
            for index in range(1, len(peaks)):
                if peaks[index][1] < peaks[index - 1][1]:
                    lower_peak_count += 1

        if len(troughs) >= 2:
            for index in range(1, len(troughs)):
                if (
                    troughs[index][1]
                    < troughs[index - 1][1]
                ):
                    lower_trough_count += 1

        # -------------------------------------------------------------
        # 原本的大趨勢判斷
        # -------------------------------------------------------------
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

        # -------------------------------------------------------------
        # 原本的小走勢判斷
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
                "(完整功能保留版)"
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

    # Messaging API 推播專用 User ID
    line_push_user_id = os.environ.get(
        "LINE_USER_ID"
    )

    # Supabase 股票名單查詢專用 ID
    # 若未設定才使用 LINE_USER_ID
    supabase_user_id = (
        os.environ.get("SUPABASE_USER_ID")
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

    data_dict = {
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
        today_str
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

    index_map = {
        str(item.get("ticker", "")).strip(): item
        for item in db_index_configs
        if item.get("ticker")
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

    # -----------------------------------------------------------------
    # 原本每週一限定推播
    # -----------------------------------------------------------------
    if weekday == 0:
        sectors_url = (
            "https://finviz.com/"
            "groups.ashx?g=sector&v=110"
        )

        sectors_message = (
            "📅 【每週一限定】"
            "美股 11 大類股週線趨勢輪動圖\n"
            "⏳ 包含 1-2 年週線級別核心波段追蹤\n\n"
            f"🔗 類股觀測鏈結：\n"
            f"{sectors_url}"
        )

        send_line_message(
            sectors_message,
            access_token,
            line_push_user_id
        )

    push_report_to_github()


if __name__ == "__main__":
    main()
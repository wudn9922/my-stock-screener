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
# 全域參數
# =========================================================================
TW_MIN_VOLUME = 1_000_000
US_MIN_VOLUME = 100_000

DATA_DIR = "data"
DOCS_DIR = "docs"
MAX_DAYS = 201

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# True：Supabase 股票只要能下載就顯示，不套用均線距離條件
# False：套用 CUSTOM_MA_MIN_RATIO～CUSTOM_MA_MAX_RATIO
CUSTOM_GROUP_TEST_MODE = True

# 正式模式的均線距離條件
# 0.97～1.03 代表均線下方 3% 至上方 3%
CUSTOM_MA_MIN_RATIO = 0.97
CUSTOM_MA_MAX_RATIO = 1.03


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


def sanitize_json_value(value):
    if isinstance(value, dict):
        return {
            key: sanitize_json_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            sanitize_json_value(item)
            for item in value
        ]

    if isinstance(value, float):
        if pd.isna(value):
            return None

        if value == float("inf") or value == float("-inf"):
            return None

        return value

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

    if not all(col in df.columns for col in required_cols):
        return pd.DataFrame()

    result = df[required_cols].copy()

    for col in required_cols:
        result[col] = pd.to_numeric(
            result[col],
            errors="coerce"
        )

    result = result.dropna(
        subset=required_cols
    ).sort_index()

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
# Supabase 設定讀取
# =========================================================================
def get_supabase_settings():
    base_url = os.environ.get(
        "SUPABASE_URL",
        ""
    ).strip().rstrip("/")

    supabase_key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or ""
    ).strip()

    if not base_url:
        print("❌ 未設定 SUPABASE_URL")
        return None, None

    if not supabase_key:
        print(
            "❌ 未設定 SUPABASE_SERVICE_ROLE_KEY "
            "或 SUPABASE_ANON_KEY"
        )
        return None, None

    if base_url.endswith("/rest/v1"):
        rest_url = base_url
    else:
        rest_url = f"{base_url}/rest/v1"

    return rest_url, supabase_key


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

    default_index_configs = [
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

    target_user_id = str(
        target_user_id or ""
    ).strip()

    supabase_url, supabase_key = (
        get_supabase_settings()
    )

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
        res_groups = requests.get(
            f"{supabase_url}/groups",
            headers=headers,
            params={"select": "*"},
            timeout=15
        )

        res_stocks = requests.get(
            f"{supabase_url}/stocks",
            headers=headers,
            params={"select": "*"},
            timeout=15
        )

        print(
            f"📡 Supabase groups 狀態："
            f"{res_groups.status_code}"
        )
        print(
            f"📡 Supabase stocks 狀態："
            f"{res_stocks.status_code}"
        )

        if res_groups.status_code == 200:
            groups_data = res_groups.json()
        else:
            groups_data = []
            print(
                f"❌ groups 查詢失敗："
                f"{res_groups.text[:500]}"
            )

        if res_stocks.status_code == 200:
            stocks_data = res_stocks.json()
        else:
            stocks_data = []
            print(
                f"❌ stocks 查詢失敗："
                f"{res_stocks.text[:500]}"
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
                mapped_key = name_mapping[group_name]
                group_id_to_key[group_id] = mapped_key

                print(
                    f"✅ 群組對應成功："
                    f"{group_name} → {mapped_key}"
                )

        matched_user_stock_count = 0
        loaded_stock_count = 0

        for stock_item in stocks_data:
            stock_user_id = str(
                stock_item.get("line_user_id", "")
            ).strip()

            if stock_user_id != target_user_id:
                continue

            matched_user_stock_count += 1

            group_id = str(
                stock_item.get("group_id", "")
            ).strip()

            raw_ticker = str(
                stock_item.get("ticker", "")
            ).strip().upper()

            if not raw_ticker:
                print("⚠️ 發現 ticker 為空的資料")
                continue

            if group_id == "admin_index":
                print(
                    f"ℹ️ 略過 admin_index："
                    f"{raw_ticker}"
                )
                continue

            # 第一種格式：group_id 為 groups 表中的 ID
            mapped_key = group_id_to_key.get(
                group_id
            )

            # 第二種格式：group_id 直接儲存 tw_g1、us_g1
            if not mapped_key and group_id in configs:
                mapped_key = group_id

            if not mapped_key:
                print(
                    f"⚠️ 股票找不到對應群組："
                    f"ticker={raw_ticker}, "
                    f"group_id={group_id}"
                )
                continue

            if mapped_key.startswith("us_"):
                raw_ticker = raw_ticker.replace(
                    ".",
                    "-"
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

            configs[mapped_key][raw_ticker] = ma_list
            loaded_stock_count += 1

            print(
                f"✅ 載入 Supabase 股票："
                f"{mapped_key} / "
                f"{raw_ticker} / "
                f"MA={ma_list}"
            )

        print(
            f"👤 符合目前 LINE_USER_ID 的資料數："
            f"{matched_user_stock_count}"
        )
        print(
            f"📊 成功載入自訂群組的股票數："
            f"{loaded_stock_count}"
        )

    except Exception as exc:
        print(
            f"❌ 讀取雲端個股失敗："
            f"{type(exc).__name__}: {exc}"
        )

    try:
        res_index = requests.get(
            f"{supabase_url}/index_configs",
            headers=headers,
            params={"select": "*"},
            timeout=15
        )

        if res_index.status_code == 200:
            index_configs = res_index.json()
            print(
                "📡 成功從雲端同步全球大盤自訂均線參數"
            )
        else:
            print(
                f"⚠️ index_configs 查詢失敗："
                f"HTTP {res_index.status_code} "
                f"{res_index.text[:500]}"
            )

    except Exception as exc:
        print(
            f"⚠️ 讀取雲端大盤設定失敗："
            f"{type(exc).__name__}: {exc}"
        )

    if not index_configs:
        index_configs = default_index_configs

    print(
        "\n===== Supabase 自訂群組讀取結果 ====="
    )

    for group_key, stocks in configs.items():
        print(
            f"{group_key}: {len(stocks)} 檔"
        )

        for ticker, ma_list in stocks.items():
            print(
                f"  └─ {ticker}: {ma_list}"
            )

    print(
        "====================================\n"
    )

    return configs, index_configs


# =========================================================================
# LINE 推播
# =========================================================================
def send_line_message(message, access_token, user_id):
    if not access_token or not user_id:
        print("⚠️ LINE 設定不完整，略過推播")
        return None

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
            print(
                f"⚠️ LINE 推播失敗："
                f"{response.text[:500]}"
            )

        return response.status_code

    except Exception as exc:
        print(
            f"⚠️ LINE 推播異常："
            f"{type(exc).__name__}: {exc}"
        )
        return None


# =========================================================================
# 市場股票清單
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

            vol_col = (
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

                    if vol_col:
                        volume = float(
                            str(row[vol_col]).replace(
                                ",",
                                ""
                            )
                        )

                        if volume < min_volume:
                            continue

                    tickers.append(
                        f"{code}.TW"
                    )

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

            data = response.json()

            if not isinstance(data, list):
                break

            for item in data:
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
                    except (TypeError, ValueError):
                        continue

                if volume >= min_volume:
                    tickers.append(
                        f"{code}.TWO"
                    )

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

        tables = pd.read_html(
            io.StringIO(response.text)
        )

        if not tables:
            raise ValueError("Wikipedia 沒有表格資料")

        symbols = tables[0]["Symbol"].tolist()

        return [
            str(symbol).replace(".", "-").strip()
            for symbol in symbols
        ]

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
# 圖表資料
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
            "name": "K線",
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
                    "color": "#ef5350"
                }
            },
            "decreasing": {
                "line": {
                    "color": "#26a69a"
                }
            }
        }
    ]

    colors = [
        "#FF9800",
        "#2196F3",
        "#4CAF50",
        "#E91E63",
        "#9C27B0",
        "#00BCD4"
    ]

    for index, ma_window in enumerate(ma_list):
        ma_col = f"MA{ma_window}"

        if ma_col not in df_chart.columns:
            continue

        ma_values = [
            None
            if pd.isna(value)
            else float(value)
            for value in df_chart[ma_col].tolist()
        ]

        if not any(
            value is not None
            for value in ma_values
        ):
            continue

        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": ma_col,
                "x": date_strings,
                "y": ma_values,
                "line": {
                    "color": colors[
                        index % len(colors)
                    ],
                    "width": 2
                }
            }
        )

    layout = {
        "title": f"{ticker} {title_suffix}",
        "xaxis": {
            "type": "date",
            "rangeslider": {
                "visible": False
            }
        },
        "yaxis": {
            "fixedrange": False
        },
        "template": "plotly_dark",
        "margin": {
            "l": 40,
            "r": 20,
            "t": 60,
            "b": 40
        },
        "height": 400
    }

    return {
        "data": traces,
        "layout": layout
    }


# =========================================================================
# 全市場掃描
# =========================================================================
def download_market_chunk(
    tickers,
    period
):
    try:
        data = yf.download(
            tickers,
            period=period,
            progress=False,
            threads=False,
            auto_adjust=False,
            group_by="column"
        )

        return data

    except Exception as exc:
        print(
            f"❌ 全市場批次下載失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return pd.DataFrame()


def scan_market(tickers, min_volume):
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

        data = download_market_chunk(
            chunk,
            "250d"
        )

        if data.empty:
            continue

        for ticker in chunk:
            try:
                ticker_data = extract_yfinance_data(
                    data,
                    ticker
                )

                ticker_data = clean_ohlcv_dataframe(
                    ticker_data
                )

                if ticker_data.empty:
                    continue

                ticker_data.tail(
                    MAX_DAYS
                ).to_csv(
                    os.path.join(
                        DATA_DIR,
                        f"{ticker}.csv"
                    )
                )

            except Exception as exc:
                print(
                    f"⚠️ {ticker} 初始化失敗："
                    f"{exc}"
                )

    for start in range(
        0,
        len(need_update),
        chunk_size
    ):
        chunk = need_update[
            start:start + chunk_size
        ]

        data = download_market_chunk(
            chunk,
            "5d"
        )

        if data.empty:
            continue

        for ticker in chunk:
            try:
                today_data = extract_yfinance_data(
                    data,
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
                    f"⚠️ {ticker} 更新失敗："
                    f"{exc}"
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

            # 全市場條件：MA20 下方 2% 至上方 1%
            if not (
                ma20 * 0.98
                <= price
                <= ma20 * 1.01
            ):
                continue

            diff_pct = (
                (price / ma20) - 1
            ) * 100

            chart_data = build_stock_data(
                df.tail(60),
                ticker,
                (
                    f"(現價:{price:.2f} | "
                    f"距MA20:{diff_pct:.2f}%)"
                ),
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
# Supabase 自訂群組股票
# =========================================================================
def get_ticker_candidates(
    raw_ticker,
    group_key
):
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


def download_custom_stock(
    raw_ticker,
    group_key
):
    candidates = get_ticker_candidates(
        raw_ticker,
        group_key
    )

    for candidate in candidates:
        try:
            print(
                f"⬇️ 嘗試下載：{candidate}"
            )

            downloaded_data = yf.download(
                candidate,
                period="2y",
                progress=False,
                threads=False,
                auto_adjust=False,
                group_by="column"
            )

            df = extract_yfinance_data(
                downloaded_data,
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

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    print(
        "\n===================================="
    )
    print(
        f"📋 開始處理自訂群組：{group_key}"
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

    for raw_ticker, raw_ma_list in group_dict.items():
        try:
            print(
                f"\n🔍 開始處理："
                f"{raw_ticker}，"
                f"MA={raw_ma_list}"
            )

            actual_ticker, downloaded_data = (
                download_custom_stock(
                    raw_ticker,
                    group_key
                )
            )

            if (
                not actual_ticker
                or downloaded_data.empty
            ):
                print(
                    f"❌ {raw_ticker} "
                    f"所有代碼皆下載失敗"
                )
                continue

            csv_path = os.path.join(
                DATA_DIR,
                f"{actual_ticker}.csv"
            )

            combined = downloaded_data.copy()

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
                                downloaded_data
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
                print(
                    f"❌ {actual_ticker} "
                    f"合併後沒有資料"
                )
                continue

            ma_list = []

            for raw_ma in raw_ma_list:
                ma_value = safe_int(raw_ma)

                if (
                    ma_value is not None
                    and ma_value > 0
                ):
                    ma_list.append(ma_value)

            ma_list = sorted(set(ma_list))

            if not ma_list:
                ma_list = [20]

            max_ma_window = max(ma_list)

            keep_days = max(
                MAX_DAYS,
                max_ma_window + 60
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
                ma_value = combined[
                    ma_col
                ].iloc[-1]

                if pd.isna(ma_value):
                    print(
                        f"⚠️ {actual_ticker} "
                        f"MA{ma_window} 資料不足"
                    )
                    continue

                ma_value = float(ma_value)
                available_ma_count += 1

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
                    f"未符合均線條件"
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

            info_text = (
                " / ".join(triggered_info)
                if triggered_info
                else "無可用均線"
            )

            ma_status_text = (
                " | ".join(ma_status_list)
                if ma_status_list
                else "均線資料不足"
            )

            title_suffix = (
                f"({mode_text} | "
                f"現價:{price:.2f} | "
                f"{info_text} | "
                f"{ma_status_text})"
            )

            chart_data = build_stock_data(
                combined.tail(60),
                actual_ticker,
                title_suffix,
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
    clean_data = sanitize_json_value(
        data_dict
    )

    js_store = (
        "const chartDataStore = "
        + json.dumps(
            clean_data,
            ensure_ascii=False,
            allow_nan=False
        )
        + ";\n"
    )

    html_template = f"""
<!DOCTYPE html>
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
        body {{
            background-color: #111;
            color: #fff;
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 10px;
        }}

        .header {{
            text-align: center;
            padding: 15px 0;
            background: #222;
            margin-bottom: 15px;
            border-radius: 8px;
        }}

        .category-box {{
            background: #1a1a1a;
            padding: 12px;
            margin-bottom: 15px;
            border-radius: 8px;
            border-left: 4px solid #00b0ff;
        }}

        .category-title {{
            font-size: 15px;
            font-weight: bold;
            color: #00ff88;
            margin-bottom: 10px;
            padding-left: 5px;
        }}

        .tabs {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }}

        .tab-btn {{
            background: #2a2a2a;
            color: #aaa;
            border: none;
            padding: 8px 12px;
            font-size: 13px;
            cursor: pointer;
            border-radius: 4px;
        }}

        .tab-btn:hover {{
            background: #3a3a3a;
        }}

        .tab-btn.active {{
            background: #00b0ff;
            color: #fff;
            font-weight: bold;
        }}

        .market-section {{
            display: none;
            max-width: 800px;
            margin: 0 auto;
        }}

        .market-section.active {{
            display: block;
        }}

        .chart-card {{
            background: #1e1e1e;
            margin-bottom: 25px;
            padding: 10px;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
        }}

        .plotly-container {{
            height: 400px;
            background: #151515;
            border-radius: 6px;
        }}

        .no-data {{
            text-align: center;
            color: #888;
            padding: 40px;
            font-size: 14px;
        }}

        .test-notice {{
            text-align: center;
            background: #4d3b00;
            color: #ffd54f;
            padding: 10px;
            margin-bottom: 15px;
            border-radius: 6px;
        }}
    </style>
</head>

<body>
    <div class="header">
        <h2>
            📈 台美股量化潛伏網頁報告
            ({date_str})
        </h2>
        <p style="margin:5px 0 0;color:#00ff88;font-size:13px;">
            專屬訂製滾動數據儲存版
        </p>
    </div>
"""

    if CUSTOM_GROUP_TEST_MODE:
        html_template += """
    <div class="test-notice">
        目前為 Supabase 測試模式：
        自訂群組股票不套用均線距離篩選
    </div>
"""

    html_template += f"""
    <div
        class="category-box"
        style="border-left-color:#ff5252;"
    >
        <div class="category-title">
            🇹🇼 台灣股市區塊
        </div>

        <div class="tabs">
            <button
                id="btn-tw_all"
                class="tab-btn active"
                onclick="switchMarket(event, 'tw_all')"
            >
                全市場潛伏 ({len(data_dict["tw_all"])})
            </button>

            <button
                id="btn-tw_g1"
                class="tab-btn"
                onclick="switchMarket(event, 'tw_g1')"
            >
                權值精選 ({len(data_dict["tw_g1"])})
            </button>

            <button
                id="btn-tw_g2"
                class="tab-btn"
                onclick="switchMarket(event, 'tw_g2')"
            >
                熱門 ({len(data_dict["tw_g2"])})
            </button>
        </div>
    </div>

    <div
        class="category-box"
        style="border-left-color:#00b0ff;"
    >
        <div class="category-title">
            🇺🇸 美國股市區塊
        </div>

        <div class="tabs">
            <button
                id="btn-us_all"
                class="tab-btn"
                onclick="switchMarket(event, 'us_all')"
            >
                全市場潛伏 ({len(data_dict["us_all"])})
            </button>

            <button
                id="btn-us_g1"
                class="tab-btn"
                onclick="switchMarket(event, 'us_g1')"
            >
                權值精選 ({len(data_dict["us_g1"])})
            </button>

            <button
                id="btn-us_g2"
                class="tab-btn"
                onclick="switchMarket(event, 'us_g2')"
            >
                低本益比 ({len(data_dict["us_g2"])})
            </button>

            <button
                id="btn-us_g3"
                class="tab-btn"
                onclick="switchMarket(event, 'us_g3')"
            >
                超級績效 ({len(data_dict["us_g3"])})
            </button>

            <button
                id="btn-us_g4"
                class="tab-btn"
                onclick="switchMarket(event, 'us_g4')"
            >
                熱門 ({len(data_dict["us_g4"])})
            </button>
        </div>
    </div>
"""

    keys_list = [
        "tw_all",
        "tw_g1",
        "tw_g2",
        "us_all",
        "us_g1",
        "us_g2",
        "us_g3",
        "us_g4"
    ]

    for key in keys_list:
        active_class = (
            " active"
            if key == "tw_all"
            else ""
        )

        html_template += (
            f'<div id="{key}-market" '
            f'class="market-section{active_class}">'
        )

        items = data_dict.get(key, [])

        if items:
            for index in range(len(items)):
                html_template += (
                    '<div class="chart-card">'
                    f'<div id="chart-{key}-{index}" '
                    'class="plotly-container">'
                    '</div>'
                    '</div>'
                )
        else:
            html_template += (
                '<div class="no-data">'
                '此分類目前沒有可顯示的股票'
                '</div>'
            )

        html_template += "</div>"

    html_template += f"""
<script>
{js_store}

function renderMarketCharts(marketId) {{
    const items = chartDataStore[marketId];

    if (!items || !Array.isArray(items)) {{
        return;
    }}

    if (typeof Plotly === "undefined") {{
        console.error("Plotly 載入失敗");
        return;
    }}

    items.forEach((item, index) => {{
        const elementId =
            "chart-" + marketId + "-" + index;

        const container =
            document.getElementById(elementId);

        if (!container || container.dataset.done) {{
            return;
        }}

        Plotly.newPlot(
            container,
            item.chart_data.data,
            item.chart_data.layout,
            {{
                responsive: true,
                displayModeBar: false
            }}
        );

        container.dataset.done = "true";
    }});
}}

function switchMarket(event, marketId) {{
    document
        .querySelectorAll(".market-section")
        .forEach((element) => {{
            element.classList.remove("active");
        }});

    document
        .querySelectorAll(".tab-btn")
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
    }} else {{
        const button = document.getElementById(
            "btn-" + marketId
        );

        if (button) {{
            button.classList.add("active");
        }}
    }}

    renderMarketCharts(marketId);

    setTimeout(() => {{
        window.dispatchEvent(
            new Event("resize")
        );
    }}, 100);
}}

window.addEventListener("load", () => {{
    renderMarketCharts("tw_all");
}});
</script>
</body>
</html>
"""

    os.makedirs(
        DOCS_DIR,
        exist_ok=True
    )

    html_path = os.path.join(
        DOCS_DIR,
        "index.html"
    )

    with open(
        html_path,
        "w",
        encoding="utf-8"
    ) as file:
        file.write(html_template)

    print(
        f"✅ HTML 已產生：{html_path}"
    )


# =========================================================================
# 大盤趨勢分析
# =========================================================================
def analyze_index_trend(
    ticker,
    name,
    ma_list
):
    if not ma_list:
        return (
            f"⚪ {name}: 未設定任何均線參數"
        )

    try:
        df = yf.download(
            ticker,
            period="4y",
            progress=False,
            threads=False,
            auto_adjust=False
        )

        df = extract_yfinance_data(
            df,
            ticker
        )

        df = clean_ohlcv_dataframe(df)

        max_ma = max(ma_list)

        if df.empty or len(df) < max_ma + 5:
            return (
                f"⚪ {name}: 數據不足無法分析"
            )

        available_mas = []

        for ma in ma_list:
            column = f"MA{ma}"

            df[column] = (
                df["Close"]
                .rolling(
                    window=ma,
                    min_periods=ma
                )
                .mean()
            )

            available_mas.append(column)

        df = df.dropna(
            subset=available_mas
        )

        if len(df) < 5:
            return (
                f"⚪ {name}: 計算後資料不足"
            )

        latest = df.iloc[-1]
        last_five = df.tail(5)

        score = 0

        for ma_column in available_mas:
            touch_count = 0

            for _, row in last_five.iterrows():
                if (
                    row["Low"]
                    <= row[ma_column]
                    <= row["High"]
                ):
                    touch_count += 1

            if touch_count >= 3:
                continue

            latest_close = float(
                latest["Close"]
            )

            latest_ma = float(
                latest[ma_column]
            )

            if latest_close > latest_ma * 1.001:
                score += 1
            elif latest_close < latest_ma * 0.999:
                score -= 1

        total_ma_count = len(
            available_mas
        )

        if score == total_ma_count:
            score_label = "看多"
            icon = "🔺"
        elif score > 0:
            score_label = "偏多"
            icon = "🟢"
        elif score == 0:
            score_label = "多空不明"
            icon = "⚪"
        elif score == -total_ma_count:
            score_label = "看空"
            icon = "🔻"
        else:
            score_label = "偏空"
            icon = "🟠"

        ma_string = "/".join(
            str(ma)
            for ma in ma_list
        )

        latest_close = float(
            latest["Close"]
        )

        return (
            f"{icon} {name}\n"
            f"   ├ 收盤: {latest_close:.2f}\n"
            f"   └ 均線: {score_label} "
            f"({score}/{total_ma_count}MA - "
            f"{ma_string})"
        )

    except Exception as exc:
        print(
            f"⚠️ {ticker} 大盤分析失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return (
            f"⚪ {name}: 分析發生異常"
        )


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
# Git 更新
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
                "(Supabase群組修復)"
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

    if push_result != 0:
        print("❌ git push 失敗")
    else:
        print("✅ GitHub Pages 資料推送完成")


# =========================================================================
# 主程式
# =========================================================================
def main():
    access_token = os.environ.get(
        "LINE_ACCESS_TOKEN"
    )

    user_id = os.environ.get(
        "LINE_USER_ID"
    )

    if not user_id:
        print(
            "❌ 未設定 LINE_USER_ID，"
            "無法讀取專屬 Supabase 股票"
        )
        return

    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    weekday = today.weekday()

    user_configs, db_index_configs = (
        load_configs_from_supabase(
            user_id
        )
    )

    print("📊 開始掃描台股全市場")

    tw_tickers = get_tw_tickers(
        TW_MIN_VOLUME
    )

    tw_all = scan_market(
        tw_tickers,
        min_volume=TW_MIN_VOLUME
    )

    print("📊 開始掃描美股全市場")

    us_tickers = get_us_tickers()

    us_all = scan_market(
        us_tickers,
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

    print(
        "\n===== 最終圖表數量 ====="
    )

    for group_key, items in data_dict.items():
        print(
            f"{group_key}: {len(items)} 檔"
        )

        if group_key not in {
            "tw_all",
            "us_all"
        }:
            for item in items:
                print(
                    f"  └─ {item['ticker']}"
                )

    print(
        "========================\n"
    )

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
        user_id
    )

    index_map = {
        str(item.get("ticker", "")).strip(): item
        for item in db_index_configs
        if item.get("ticker")
    }

    market_groups = [
        (
            "【 🇹🇼 台灣市場 】",
            [
                "^TWII",
                "^TWOII"
            ]
        ),
        (
            "【 🇺🇸 美國市場 】",
            [
                "^GSPC",
                "^DJI",
                "^IXIC",
                "^RUT",
                "^SOX"
            ]
        ),
        (
            "【 🇪🇺 歐洲市場 】",
            [
                "^FCHI",
                "^FTSE",
                "^GDAXI"
            ]
        ),
        (
            "【 🌏 亞洲市場 】",
            [
                "^N225",
                "^KS11"
            ]
        )
    ]

    index_lines = [
        f"🌍 {today_str} 全球大盤多空量化報告",
        "📊 評分標準：均線糾纏自適應／0.1%過濾",
        "========================",
        ""
    ]

    for market_title, tickers in market_groups:
        index_lines.append(market_title)

        found_any = False

        for ticker in tickers:
            item = index_map.get(ticker)

            if not item:
                continue

            found_any = True

            index_lines.append(
                analyze_index_trend(
                    ticker,
                    item.get(
                        "name",
                        ticker
                    ),
                    get_ma_list_from_item(item)
                )
            )

        if not found_any:
            index_lines.append(
                "⚪ 尚未設定此市場指數"
            )

        index_lines.append("")

    send_line_message(
        "\n".join(index_lines),
        access_token,
        user_id
    )

    if weekday == 0:
        sectors_url = (
            "https://finviz.com/"
            "groups.ashx?g=sector&v=110"
        )

        sectors_message = (
            "📅 【每週一限定】"
            "美股 11 大類股週線趨勢輪動圖\n"
            "⏳ 包含 1-2 年週線級別核心波段追蹤\n\n"
            f"🔗 類股觀測鏈結：\n{sectors_url}"
        )

        send_line_message(
            sectors_message,
            access_token,
            user_id
        )

    push_report_to_github()


if __name__ == "__main__":
    main()
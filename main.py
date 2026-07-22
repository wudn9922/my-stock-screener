import yfinance as yf
import pandas as pd
import requests
import os
from datetime import datetime
import json
import io
import time

# =========================================================================
# ⚙️ 全域核心參數設定
# =========================================================================
TW_MIN_VOLUME = 1000000  # 1,000,000股 = 1000張
US_MIN_VOLUME = 100000   # 美股基本門檻：100,000股

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

DATA_DIR = "data"
MAX_DAYS = 201

# =========================================================================
# 🧪 Supabase 自訂群組測試設定
# =========================================================================

# True：只要股票可下載，就直接顯示圖表，用來檢查 Supabase 名單
# False：恢復均線條件篩選
CUSTOM_GROUP_TEST_MODE = True

# 關閉測試模式後使用的均線篩選範圍
# 0.97～1.03 代表股價位於均線上下 3%
CUSTOM_MA_MIN_RATIO = 0.97
CUSTOM_MA_MAX_RATIO = 1.02

# =========================================================================
# 📡 Supabase 雲端資料庫動態名單與大盤參數讀取器 (單一用戶版)
# =========================================================================
# =========================================================================
# 📡 Supabase 雲端資料庫動態名單與大盤參數讀取器
# =========================================================================
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

    supabase_url = "https://bxhqpfeberqbtxymghyt.supabase.co/rest/v1"
    supabase_key = "sb_publishable_eEJNM_96jblQ_90vpcYC0g_PzyGJNOK"

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}"
    }

    target_user_id = str(target_user_id).strip()

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

        if res_groups.status_code != 200:
            print(
                f"❌ groups 查詢失敗："
                f"{res_groups.text[:500]}"
            )

        if res_stocks.status_code != 200:
            print(
                f"❌ stocks 查詢失敗："
                f"{res_stocks.text[:500]}"
            )

        if (
            res_groups.status_code == 200
            and res_stocks.status_code == 200
        ):
            groups_data = res_groups.json()
            stocks_data = res_stocks.json()

            print(f"📋 Supabase 群組總數：{len(groups_data)}")
            print(f"📋 Supabase 股票總數：{len(stocks_data)}")

            name_mapping = {
                "台股-權值精選": "tw_g1",
                "台股-熱門": "tw_g2",
                "美股-權值精選": "us_g1",
                "美股-低本益比": "us_g2",
                "美股-超級績效": "us_g3",
                "美股-熱門": "us_g4"
            }

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

                    print(
                        f"✅ 群組對應成功："
                        f"{group_name} → "
                        f"{name_mapping[group_name]} "
                        f"(ID={group_id})"
                    )

            matched_user_stock_count = 0

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

                mapped_key = group_id_to_key.get(group_id)

                if not mapped_key and group_id in valid_group_keys:
                    mapped_key = group_id

                raw_ticker = str(
                    stock_item.get("ticker", "")
                ).strip().upper()

                if not raw_ticker:
                    print(
                        f"⚠️ 發現 ticker 為空的資料："
                        f"{stock_item}"
                    )
                    continue

                if not mapped_key:
                    print(
                        f"⚠️ 股票找不到對應群組："
                        f"ticker={raw_ticker}, "
                        f"group_id={group_id}"
                    )
                    continue

                # 美股 Yahoo Finance 使用 BRK-B，而不是 BRK.B
                if mapped_key.startswith("us_"):
                    raw_ticker = raw_ticker.replace(".", "-")

                # 台股純數字先保留，下載時會自動嘗試 .TW 與 .TWO
                ma_list = []

                for ma_key in [
                    "ma1",
                    "ma2",
                    "ma3",
                    "ma4"
                ]:
                    raw_ma = stock_item.get(ma_key)

                    if raw_ma is None:
                        continue

                    try:
                        ma_value = int(raw_ma)

                        if ma_value > 0:
                            ma_list.append(ma_value)

                    except (TypeError, ValueError):
                        print(
                            f"⚠️ {raw_ticker} 的 "
                            f"{ma_key} 格式錯誤：{raw_ma}"
                        )

                if not ma_list:
                    ma_list = [20]

                ma_list = sorted(set(ma_list))

                configs[mapped_key][raw_ticker] = ma_list

                print(
                    f"✅ 載入 Supabase 股票："
                    f"{mapped_key} / "
                    f"{raw_ticker} / "
                    f"MA={ma_list}"
                )

            print(
                f"👤 符合目前 LINE_USER_ID 的股票數："
                f"{matched_user_stock_count}"
            )

    except Exception as e:
        print(
            f"❌ 讀取雲端個股失敗："
            f"{type(e).__name__}: {e}"
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
            print("📡 成功從雲端同步全球大盤自訂均線參數！")
        else:
            print(
                f"⚠️ 讀取大盤設定失敗："
                f"HTTP {res_index.status_code} "
                f"{res_index.text[:500]}"
            )

    except Exception as e:
        print(
            f"⚠️ 讀取雲端大盤設定失敗："
            f"{type(e).__name__}: {e}"
        )

    if not index_configs:
        index_configs = [
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

    print("\n===== Supabase 自訂群組讀取結果 =====")

    for group_key, stocks in configs.items():
        print(f"{group_key}: {len(stocks)} 檔")

        for ticker, ma_list in stocks.items():
            print(f"  └─ {ticker}: {ma_list}")

    print("====================================\n")

    return configs, index_configs

def send_line_message(msg, access_token, user_id):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    payload = {"to": user_id, "messages": [{"type": "text", "text": msg}]}
    res = requests.post(url, json=payload, headers=headers)
    return res.status_code

def get_tw_tickers(min_volume):
    tickers = []
    twse_url = "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data"
    for attempt in range(3):
        try:
            res = requests.get(twse_url, headers=HTTP_HEADERS, timeout=15)
            if res.status_code == 200:
                df_twse = pd.read_csv(io.StringIO(res.text))
                code_col = '證券代號' if '證券代號' in df_twse.columns else df_twse.columns[0]
                vol_col = '成交股數' if '成交股數' in df_twse.columns else None

                for _, row in df_twse.iterrows():
                    try:
                        raw = str(row[code_col]).strip()
                        if len(raw) != 4 or not raw.isdigit() or raw.startswith('0'):
                            continue
                        if vol_col:
                            vol_val = float(str(row[vol_col]).replace(',', ''))
                            if vol_val < min_volume:
                                continue
                        tickers.append(f"{raw}.TW")
                    except Exception:
                        continue
                break
        except Exception: time.sleep(2)

    tpex_url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    tpex_count = 0
    for attempt in range(3):
        try:
            res = requests.get(tpex_url, headers=HTTP_HEADERS, timeout=15)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list):
                    for item in data:
                        code = str(item.get("SecuritiesCompanyCode", "")).strip()
                        if len(code) != 4 or not code.isdigit():
                            continue
                        vol_val = 0
                        for vol_key in ["TradingShares", "TradingVolume", "成交股數"]:
                            if vol_key in item:
                                try:
                                    vol_val = float(str(item[vol_key]).replace(',', ''))
                                    break
                                except: pass
                        
                        if vol_val >= min_volume:
                            tickers.append(f"{code}.TWO")
                            tpex_count += 1
                break
        except Exception: time.sleep(2)

    tickers = list(set(tickers))
    if not tickers:
        return ["2330.TW", "2317.TW", "2454.TW", "2603.TW", "0050.TW"]
    return tickers

def get_us_tickers():
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        html_text = requests.get(url, headers=HTTP_HEADERS).text
        df = pd.read_html(io.StringIO(html_text))[0]
        return [t.replace('.', '-') for t in df['Symbol'].tolist()]
    except Exception: pass
    return ["AAPL", "MSFT", "NVDA"]

# 🛠️ 繪圖函數修正：防止空值引發的斷線或消失
def build_stock_data(df_chart, ticker, title_suffix, ma_list):
    date_strings = [str(d)[:10] for d in df_chart.index]
    traces = []
    
    traces.append({
        "type": "candlestick", "name": "K線", "x": date_strings,
        "open": [float(x) for x in df_chart['Open'].tolist()],
        "high": [float(x) for x in df_chart['High'].tolist()],
        "low": [float(x) for x in df_chart['Low'].tolist()],
        "close": [float(x) for x in df_chart['Close'].tolist()],
        "increasing": {"line": {"color": "#ef5350"}}, "decreasing": {"line": {"color": "#26a69a"}}
    })
    
    colors = ['#FF9800', '#2196F3', '#4CAF50', '#E91E63', '#9C27B0', '#00BCD4']
    for idx, ma_window in enumerate(ma_list):
        ma_col = f'MA{ma_window}'
        if ma_col in df_chart.columns:
            ma_values = [None if pd.isna(val) else float(val) for val in df_chart[ma_col].tolist()]
            # 確保有資料才畫線，避免 Plotly 遇到 NaN 報錯
            if any(v is not None for v in ma_values):
                traces.append({
                    "type": "scatter", "mode": "lines", "name": ma_col,
                    "x": date_strings, "y": ma_values, "line": {"color": colors[idx % len(colors)], "width": 2}
                })
                
    layout = {
        "title": f"{ticker} {title_suffix}", "xaxis": {"type": "date", "rangeslider": {"visible": False}},
        "yaxis": {"fixedrange": False}, "template": "plotly_dark", "margin": {"l": 40, "r": 20, "t": 50, "b": 40}, "height": 400
    }
    return {"data": traces, "layout": layout}

def scan_market(tickers, min_volume):
    os.makedirs(DATA_DIR, exist_ok=True)
    matched_list = []
    chunk_size = 40
    
    need_init = []
    need_update = []
    for ticker in tickers:
        if os.path.exists(os.path.join(DATA_DIR, f"{ticker}.csv")):
            need_update.append(ticker)
        else:
            need_init.append(ticker)

    if need_init:
        for i in range(0, len(need_init), chunk_size):
            chunk = need_init[i:i+chunk_size]
            try:
                data = yf.download(chunk, period="250d", progress=False, threads=False)
                if data.empty: continue
                for ticker in chunk:
                    try:
                        if isinstance(data.columns, pd.MultiIndex):
                            if ticker in data.columns.get_level_values(1):
                                df_t = data.xs(ticker, level=1, axis=1)
                            else: continue
                        else:
                            df_t = data.copy()
                        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
                        if not all(col in df_t.columns for col in required_cols): continue
                        df_clean = df_t[required_cols].dropna().tail(MAX_DAYS)
                        df_clean.to_csv(os.path.join(DATA_DIR, f"{ticker}.csv"))
                    except Exception: continue
            except Exception: pass

    if need_update:
        for i in range(0, len(need_update), chunk_size):
            chunk = need_update[i:i+chunk_size]
            try:
                data = yf.download(chunk, period="5d", progress=False, threads=False)
                if data.empty: continue
                for ticker in chunk:
                    try:
                        if isinstance(data.columns, pd.MultiIndex):
                            if ticker in data.columns.get_level_values(1):
                                df_today = data.xs(ticker, level=1, axis=1)
                            else: continue
                        else:
                            df_today = data.copy()
                        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
                        if not all(col in df_today.columns for col in required_cols): continue
                        df_today_clean = df_today[required_cols].dropna()
                        csv_path = os.path.join(DATA_DIR, f"{ticker}.csv")
                        df_local = pd.read_csv(csv_path, index_col=0, parse_dates=True)
                        df_combined = pd.concat([df_local, df_today_clean])
                        df_combined = df_combined[~df_combined.index.duplicated(keep='last')].sort_index().tail(MAX_DAYS)
                        df_combined.to_csv(csv_path)
                    except Exception: continue
            except Exception: pass

    for ticker in tickers:
        csv_path = os.path.join(DATA_DIR, f"{ticker}.csv")
        if not os.path.exists(csv_path): continue
        try:
            df_clean = pd.read_csv(csv_path, index_col=0, parse_dates=True)
            if len(df_clean) < 20: continue
            latest_vol = float(df_clean['Volume'].iloc[-1])
            if latest_vol < min_volume: continue
            df_clean['MA20'] = df_clean['Close'].rolling(window=20).mean()
            price = float(df_clean['Close'].iloc[-1])
            ma20 = float(df_clean['MA20'].iloc[-1])
            if pd.isna(ma20): continue
            if ma20 * 0.98 <= price < ma20 * 1.01:
                diff_pct = ((price / ma20) - 1) * 100
                df_chart = df_clean.tail(60)
                title_str = f"(現價: {round(price,2)} | 距MA20: {round(diff_pct,2)}%)"
                chart_data = build_stock_data(df_chart, ticker, title_str, [20])
                matched_list.append({'ticker': ticker, 'volume': int(latest_vol), 'chart_data': chart_data})
        except Exception: continue

    matched_list.sort(key=lambda x: x['volume'], reverse=True)
    return matched_list

# =========================================================================
# 🛠️ yfinance 自訂群組資料處理工具
# =========================================================================
def extract_yfinance_data(downloaded_data, ticker):
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

    # 常見格式：(Price, Ticker)
    if ticker in level_1:
        return df.xs(
            ticker,
            level=1,
            axis=1
        ).copy()

    # 另一種格式：(Ticker, Price)
    if ticker in level_0:
        return df.xs(
            ticker,
            level=0,
            axis=1
        ).copy()

    # 單一股票時可能仍是 MultiIndex
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


def get_ticker_candidates(raw_ticker, group_key):
    ticker = str(raw_ticker).strip().upper()

    if not ticker:
        return []

    if group_key.startswith("us_"):
        return [ticker.replace(".", "-")]

    if group_key.startswith("tw_"):
        if ticker.endswith(".TW") or ticker.endswith(".TWO"):
            return [ticker]

        if ticker.isdigit():
            # 純數字無法確認上市或上櫃時，依序嘗試
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

            downloaded_data = yf.download(
                candidate,
                period="2y",
                progress=False,
                threads=False,
                auto_adjust=False
            )

            df = extract_yfinance_data(
                downloaded_data,
                candidate
            )

            if df.empty:
                print(f"⚠️ {candidate} 下載結果為空")
                continue

            required_cols = [
                "Open",
                "High",
                "Low",
                "Close",
                "Volume"
            ]

            missing_cols = [
                col
                for col in required_cols
                if col not in df.columns
            ]

            if missing_cols:
                print(
                    f"⚠️ {candidate} 缺少欄位："
                    f"{missing_cols}"
                )
                continue

            df = df[required_cols].copy()

            for col in required_cols:
                df[col] = pd.to_numeric(
                    df[col],
                    errors="coerce"
                )

            df = df.dropna(
                subset=required_cols
            ).sort_index()

            if df.empty:
                print(
                    f"⚠️ {candidate} 清理後沒有可用資料"
                )
                continue

            df.index = pd.to_datetime(df.index)

            # 移除時區，避免與 CSV 合併時發生錯誤
            if getattr(df.index, "tz", None) is not None:
                df.index = df.index.tz_localize(None)

            print(
                f"✅ {candidate} 下載成功，"
                f"共 {len(df)} 筆"
            )

            return candidate, df

        except Exception as e:
            print(
                f"❌ {candidate} 下載失敗："
                f"{type(e).__name__}: {e}"
            )

    return None, pd.DataFrame()

# =========================================================================
# 📊 Supabase 自訂群組股票處理
# =========================================================================
def process_custom_groups(
    group_dict,
    group_key,
    test_mode=CUSTOM_GROUP_TEST_MODE
):
    matched_list = []

    if not group_dict:
        print(f"⚠️ {group_key} 沒有讀到 Supabase 股票")
        return matched_list

    os.makedirs(DATA_DIR, exist_ok=True)

    print("\n====================================")
    print(f"📋 開始處理自訂群組：{group_key}")
    print(f"📋 Supabase 股票數：{len(group_dict)}")
    print(f"🧪 測試模式：{test_mode}")
    print("====================================")

    for raw_ticker, ma_list in group_dict.items():
        try:
            print(
                f"\n🔍 開始處理：{raw_ticker}，"
                f"MA={ma_list}"
            )

            actual_ticker, df_downloaded = (
                download_custom_stock(
                    raw_ticker,
                    group_key
                )
            )

            if not actual_ticker or df_downloaded.empty:
                print(
                    f"❌ {raw_ticker} 所有 Yahoo "
                    f"Finance 代碼皆下載失敗"
                )
                continue

            csv_path = os.path.join(
                DATA_DIR,
                f"{actual_ticker}.csv"
            )

            # 合併本機歷史資料
            if os.path.exists(csv_path):
                try:
                    df_local = pd.read_csv(
                        csv_path,
                        index_col=0,
                        parse_dates=True
                    )

                    required_cols = [
                        "Open",
                        "High",
                        "Low",
                        "Close",
                        "Volume"
                    ]

                    if all(
                        col in df_local.columns
                        for col in required_cols
                    ):
                        df_local = df_local[
                            required_cols
                        ].copy()

                        for col in required_cols:
                            df_local[col] = pd.to_numeric(
                                df_local[col],
                                errors="coerce"
                            )

                        df_local = df_local.dropna(
                            subset=required_cols
                        )

                        if (
                            getattr(
                                df_local.index,
                                "tz",
                                None
                            )
                            is not None
                        ):
                            df_local.index = (
                                df_local.index.tz_localize(
                                    None
                                )
                            )

                        df_combined = pd.concat([
                            df_local,
                            df_downloaded
                        ])
                    else:
                        print(
                            f"⚠️ {actual_ticker} 本機 CSV "
                            f"欄位不完整，改用下載資料"
                        )
                        df_combined = df_downloaded.copy()

                except Exception as e:
                    print(
                        f"⚠️ {actual_ticker} 讀取本機 "
                        f"CSV 失敗，改用下載資料：{e}"
                    )
                    df_combined = df_downloaded.copy()
            else:
                df_combined = df_downloaded.copy()

            df_combined = (
                df_combined[
                    ~df_combined.index.duplicated(
                        keep="last"
                    )
                ]
                .sort_index()
            )

            if df_combined.empty:
                print(
                    f"❌ {actual_ticker} 合併後無資料"
                )
                continue

            ma_list = sorted({
                int(ma)
                for ma in ma_list
                if int(ma) > 0
            })

            if not ma_list:
                ma_list = [20]

            # 保留足夠計算最大均線的歷史資料
            max_ma_window = max(ma_list)

            keep_days = max(
                MAX_DAYS,
                max_ma_window + 60
            )

            df_combined = df_combined.tail(
                keep_days
            ).copy()

            # 儲存原始 OHLCV，不將 MA 寫入 CSV
            df_combined[
                [
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "Volume"
                ]
            ].to_csv(csv_path)

            # 先計算所有均線
            for ma_window in ma_list:
                df_combined[f"MA{ma_window}"] = (
                    df_combined["Close"]
                    .rolling(
                        window=ma_window,
                        min_periods=ma_window
                    )
                    .mean()
                )

            price = float(
                df_combined["Close"].iloc[-1]
            )

            latest_volume = float(
                df_combined["Volume"].iloc[-1]
            )

            triggered_info = []
            available_ma_count = 0
            matched_any_ma = False

            for ma_window in ma_list:
                ma_col = f"MA{ma_window}"

                if ma_col not in df_combined.columns:
                    continue

                ma_value = df_combined[
                    ma_col
                ].iloc[-1]

                if pd.isna(ma_value):
                    print(
                        f"⚠️ {actual_ticker} 的 "
                        f"MA{ma_window} 資料不足："
                        f"目前 {len(df_combined)} 筆"
                    )
                    continue

                available_ma_count += 1
                ma_value = float(ma_value)

                diff_pct = (
                    (price / ma_value) - 1
                ) * 100

                if test_mode:
                    # 測試模式不套用均線距離條件
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

            # 測試模式下，即使均線資料不足，
            # 只要 K 線可下載，仍然顯示圖表
            if test_mode and available_ma_count == 0:
                matched_any_ma = True
                triggered_info.append(
                    "測試模式：均線資料不足，但K線正常"
                )

            if not matched_any_ma:
                print(
                    f"⏭️ {actual_ticker} 未符合均線條件"
                )
                continue

            df_chart = df_combined.tail(60).copy()

            ma_status_list = []

            for ma_window in ma_list:
                ma_col = f"MA{ma_window}"

                if ma_col not in df_combined.columns:
                    continue

                latest_ma = df_combined[
                    ma_col
                ].iloc[-1]

                if not pd.isna(latest_ma):
                    ma_status_list.append(
                        f"MA{ma_window}:"
                        f"{float(latest_ma):.2f}"
                    )

            if test_mode:
                mode_text = "Supabase測試模式"
            else:
                mode_text = "均線潛伏"

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

            title_str = (
                f"({mode_text} | "
                f"現價:{price:.2f} | "
                f"{info_text} | "
                f"{ma_status_text})"
            )

            chart_data = build_stock_data(
                df_chart,
                actual_ticker,
                title_str,
                ma_list
            )

            matched_list.append({
                "ticker": actual_ticker,
                "volume": int(latest_volume),
                "chart_data": chart_data
            })

            print(
                f"✅ {actual_ticker} 已成功加入圖表"
            )

        except Exception as e:
            print(
                f"❌ {group_key} / {raw_ticker} "
                f"處理失敗："
                f"{type(e).__name__}: {e}"
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

def generate_html(data_dict, date_str):
    js_store = "const chartDataStore = " + json.dumps(data_dict, ensure_ascii=False) + ";\n"
    html_template = f"""<!DOCTYPE html><html><head><title>台美股均線潛伏報告</title><meta name="viewport" content="width=device-width, initial-scale=1.0"><script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script><style>body {{ background-color: #111; color: #fff; font-family: Arial, sans-serif; margin: 0; padding: 10px; }} .header {{ text-align: center; padding: 15px 0; background: #222; margin-bottom: 15px; border-radius: 8px; }} .category-box {{ background: #1a1a1a; padding: 12px; margin-bottom: 15px; border-radius: 8px; border-left: 4px solid #00b0ff; }} .category-title {{ font-size: 15px; font-weight: bold; color: #00ff88; margin-bottom: 10px; padding-left: 5px; }} .tabs {{ display: flex; flex-wrap: wrap; gap: 6px; }} .tab-btn {{ background: #2a2a2a; color: #aaa; border: none; padding: 8px 12px; font-size: 13px; cursor: pointer; border-radius: 4px; transition: 0.3s; }} .tab-btn:hover {{ background: #3a3a3a; }} .tab-btn.active {{ background: #00b0ff; color: #fff; font-weight: bold; }} .market-section {{ display: none; max-width: 800px; margin: 0 auto; }} .market-section.active {{ display: block; }} .chart-card {{ background: #1e1e1e; margin-bottom: 25px; padding: 10px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }} .plotly-container {{ height: 400px; background: #151515; border-radius: 6px; }} .no-data {{ text-align: center; color: #888; padding: 40px; font-size: 14px; }}</style></head><body><div class="header"><h2>📈 台美股量化潛伏網頁報告 ({date_str})</h2><p style="margin: 5px 0 0 0; color:#00ff88; font-size:13px;">專屬訂製滾動數據儲存版</p></div>
    
    <div class="category-box" style="border-left-color: #ff5252;">
        <div class="category-title">🇹🇼 台灣股市區塊</div>
        <div class="tabs">
            <button id="btn-tw_all" class="tab-btn active" onclick="switchMarket(event, 'tw_all')">全市場潛伏 ({len(data_dict['tw_all'])})</button>
            <button id="btn-tw_g1" class="tab-btn" onclick="switchMarket(event, 'tw_g1')">權值精選 ({len(data_dict['tw_g1'])})</button>
            <button id="btn-tw_g2" class="tab-btn" onclick="switchMarket(event, 'tw_g2')">熱門 ({len(data_dict['tw_g2'])})</button>
        </div>
    </div>

    <div class="category-box" style="border-left-color: #00b0ff;">
        <div class="category-title">🇺🇸 美國股市區塊</div>
        <div class="tabs">
            <button id="btn-us_all" class="tab-btn" onclick="switchMarket(event, 'us_all')">全市場潛伏 ({len(data_dict['us_all'])})</button>
            <button id="btn-us_g1" class="tab-btn" onclick="switchMarket(event, 'us_g1')">權值精選 ({len(data_dict['us_g1'])})</button>
            <button id="btn-us_g2" class="tab-btn" onclick="switchMarket(event, 'us_g2')">低本益比 ({len(data_dict['us_g2'])})</button>
            <button id="btn-us_g3" class="tab-btn" onclick="switchMarket(event, 'us_g3')">超級績效 ({len(data_dict['us_g3'])})</button>
            <button id="btn-us_g4" class="tab-btn" onclick="switchMarket(event, 'us_g4')">熱門 ({len(data_dict['us_g4'])})</button>
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
        active_class = " active" if key == "tw_all" else ""

        html_template += (
            f'<div id="{key}-market" '
            f'class="market-section{active_class}">'
        )

        if data_dict.get(key):
            for idx in range(len(data_dict[key])):
                html_template += (
                    '<div class="chart-card">'
                    f'<div id="chart-{key}-{idx}" '
                    'class="plotly-container"></div>'
                    '</div>'
                )
        else:
            html_template += (
                '<div class="no-data">'
                '此分類目前沒有可顯示的股票，'
                '請查看 GitHub Actions 執行紀錄確認 '
                'Supabase 與 yfinance 狀態'
                '</div>'
            )

        html_template += "</div>"
        
    html_template += f"""<script>{js_store} function renderMarketCharts(marketId) {{ const items = chartDataStore[marketId]; if (!items) return; items.forEach((item, idx) => {{ const elementId = "chart-" + marketId + "-" + idx; const container = document.getElementById(elementId); if (container && !container.dataset.done) {{ Plotly.newPlot(container, item.chart_data.data, item.chart_data.layout, {{responsive: true, displayModeBar: false}}); container.dataset.done = "true"; }} }}); }} function switchMarket(event, marketId) {{ document.querySelectorAll('.market-section').forEach(el => el.classList.remove('active')); document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active')); document.getElementById(marketId + '-market').classList.add('active'); if(event) {{ event.currentTarget.classList.add('active'); }} else {{ document.getElementById('btn-' + marketId).classList.add('active'); }} renderMarketCharts(marketId); window.dispatchEvent(new Event('resize')); }} window.addEventListener("load", function() {{ renderMarketCharts('tw_all'); }});</script></body></html>"""
    
    os.makedirs("docs", exist_ok=True)
    # 網頁恢復為 index.html
    with open("docs/index.html", "w", encoding="utf-8") as f: f.write(html_template)


def analyze_index_trend(ticker, name, ma_list):
    if not ma_list: return f"⚪ {name}: 未設定任何均線參數"
    try:
        df = yf.download(ticker, period="4y", progress=False, threads=False)
        if df.empty or len(df) < 750: return f"⚪ {name}: 數據不足無法分析"
        df = df.copy()
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        
        available_mas = []
        for ma in ma_list:
            col_name = f'MA{ma}'
            df[col_name] = df['Close'].rolling(window=ma).mean()
            available_mas.append(col_name)
        
        df = df.dropna(subset=available_mas)
        if len(df) < 5: return f"⚪ {name}: 計算後可用數據小於5日"
        
        latest = df.iloc[-1]
        score = 0
        total_ma_count = len(available_mas)
        df_last5 = df.tail(5)
        
        for ma_col in available_mas:
            touch_count = 0
            for _, row_5 in df_last5.iterrows():
                if row_5['Low'] <= row_5[ma_col] <= row_5['High']:
                    touch_count += 1
            
            if touch_count >= 3:
                score += 0 
            else:
                latest_close = latest['Close']
                latest_ma = latest[ma_col]
                if latest_close > latest_ma * 1.001: score += 1
                elif latest_close < latest_ma * 0.999: score -= 1
                else: score += 0
                    
        if score == total_ma_count: score_label = "看多"
        elif score > 0: score_label = "偏多"
        elif score == 0: score_label = "多空不明"
        elif score == -total_ma_count: score_label = "看空"
        else: score_label = "偏空"
        
        df_3y = df.tail(252 * 3)
        idx_3y_high = df_3y['High'].idxmax()
        latest_date = df.index[-1]
        months_since_high = (latest_date - idx_3y_high).days / 30.0
        df_recent = df.tail(120).copy()
        peaks = []
        troughs = []
        for i in range(2, len(df_recent)-2):
            if df_recent['High'].iloc[i] > df_recent['High'].iloc[i-1] and df_recent['High'].iloc[i] > df_recent['High'].iloc[i-2] and df_recent['High'].iloc[i] > df_recent['High'].iloc[i+1] and df_recent['High'].iloc[i] > df_recent['High'].iloc[i+2]: peaks.append((df_recent.index[i], df_recent['High'].iloc[i]))
            if df_recent['Low'].iloc[i] < df_recent['Low'].iloc[i-1] and df_recent['Low'].iloc[i] < df_recent['Low'].iloc[i-2] and df_recent['Low'].iloc[i] < df_recent['Low'].iloc[i+1] and df_recent['Low'].iloc[i] < df_recent['Low'].iloc[i+2]: troughs.append((df_recent.index[i], df_recent['Low'].iloc[i]))
        lower_peak_count = 0
        lower_trough_count = 0
        if len(peaks) >= 2:
            for j in range(1, len(peaks)):
                if peaks[j][1] < peaks[j-1][1]: lower_peak_count += 1
        if len(troughs) >= 2:
            for j in range(1, len(troughs)):
                if troughs[j][1] < troughs[j-1][1]: lower_trough_count += 1
        macro_trend = "多頭趨勢" 
        if months_since_high >= 4.0:
            df_bear_period = df.loc[idx_3y_high:latest_date]
            if len(df_bear_period) > 5:
                bear_low = df_bear_period['Low'].iloc[:-1].min()
                if latest['Close'] < bear_low: macro_trend = "空頭趨勢"
        micro_走勢 = "多頭走勢"
        if months_since_high >= 1.0 and (lower_peak_count + lower_trough_count) >= 2: micro_走勢 = "空頭走勢"
        final_status = f"{macro_trend}中的{micro_走勢}"
        if macro_trend == "多頭趨勢" and micro_走勢 == "多頭走勢": icon = "🔺"
        elif macro_trend == "多頭趨勢" and micro_走勢 == "空頭走勢": icon = "💡"
        elif macro_trend == "空頭趨勢" and micro_走勢 == "空頭走勢": icon = "🔻"
        else: icon = "⚡"
        
        ma_str_list = "/".join([str(x) for x in ma_list])
        return f"{icon} {name}\n   ├ 均線: {score_label} ({score}/{total_ma_count}MA - {ma_str_list})\n   └  {final_status}"
    except Exception: return f"⚪ {name}: 分析發生異常"


def main():
    access_token = os.environ.get("LINE_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    
    if not access_token or not user_id: 
        print("未設定 LINE_ACCESS_TOKEN 或 LINE_USER_ID，停止執行。")
        return
        
    today_str = datetime.now().strftime("%Y-%m-%d")
    weekday = datetime.now().weekday() 

    # 1. 取得專屬你的雲端設定
    user_configs, db_index_configs = load_configs_from_supabase(user_id)
    
    # 2. 爬取市場資料 (全市場)
    tw_all = scan_market(get_tw_tickers(TW_MIN_VOLUME), min_volume=TW_MIN_VOLUME)
    us_all = scan_market(get_us_tickers(), min_volume=US_MIN_VOLUME)

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

    for group_key, items in data_dict.items():
        print(f"{group_key}: {len(items)} 檔")

        for item in items:
            print(f"  └─ {item['ticker']}")

    print("========================\n")
    
    # 產生 index.html
    generate_html(data_dict, today_str)
    
    # =========================================================================
    # ✉️ 組合推播訊息 (個股)
    # =========================================================================
    # 網址改回根目錄的 index.html
    report_url = "https://wudn9922.github.io/my-stock-screener/"
    liff_setting_url = "https://liff.line.me/2010330411-SbwvRXRN"
    bitget_setting_url = "https://liff.line.me/2010535859-y3CoAWaC"
    
    line_msg_stocks = f"🎯 {today_str} 專屬量化看盤網頁！\n\n"
    line_msg_stocks += f"🇹🇼 【台灣股市區塊】\n"
    line_msg_stocks += f" ├ 1. 全市場符合：{len(data_dict['tw_all'])} 檔\n"
    line_msg_stocks += f" ├ 2. 權值精選符合：{len(data_dict['tw_g1'])} 檔\n"
    line_msg_stocks += f" └ 3. 熱門符合：{len(data_dict['tw_g2'])} 檔\n\n"
    line_msg_stocks += f"🇺🇸 【美國股市區塊】\n"
    line_msg_stocks += f" ├ 1. 全市場符合：{len(data_dict['us_all'])} 檔\n"
    line_msg_stocks += f" ├ 2. 權值精選符合：{len(data_dict['us_g1'])} 檔\n"
    line_msg_stocks += f" ├ 3. 低本益比符合：{len(data_dict['us_g2'])} 檔\n"
    line_msg_stocks += f" ├ 4. 超級績效符合：{len(data_dict['us_g3'])} 檔\n"
    line_msg_stocks += f" └ 5. 熱門符合：{len(data_dict['us_g4'])} 檔\n\n"
    line_msg_stocks += f"🔗 1. 專屬潛伏圖表網頁：\n{report_url}\n\n"
    line_msg_stocks += f"⚙️ 2. 手機自訂參數控制台：\n{liff_setting_url}\n\n"
    line_msg_stocks += f"💰 3. 自動交易參數控制台：\n{bitget_setting_url}"
    
    send_line_message(line_msg_stocks, access_token, user_id)

    # =========================================================================
    # ✉️ 組合推播訊息 (大盤)
    # =========================================================================
    line_msg_index = f"🌍 {today_str} 全球大盤多空量化報告\n"
    line_msg_index += f"📊 評分標準: 均線糾纏自適應/0.1%過濾機制\n"
    line_msg_index += f"========================\n\n"
    
    tw_indices = ["^TWII", "^TWOII"]
    us_indices = ["^GSPC", "^DJI", "^IXIC", "^RUT", "^SOX"]
    eu_indices = ["^FCHI", "^FTSE", "^GDAXI"]
    as_indices = ["^N225", "^KS11"]
    
    idx_map = {item["ticker"]: item for item in db_index_configs}
    
    def get_ma_list_from_item(item):
        ma_list = []
        for ma_key in ["ma1", "ma2", "ma3", "ma4"]:
            if item.get(ma_key) is not None and int(item[ma_key]) > 0:
                ma_list.append(int(item[ma_key]))
        return ma_list if ma_list else [20]

    line_msg_index += f"【 🇹🇼 台灣市場 】\n"
    for t in tw_indices:
        if t in idx_map: line_msg_index += analyze_index_trend(t, idx_map[t]["name"], get_ma_list_from_item(idx_map[t])) + "\n"
    line_msg_index += "\n"
    
    line_msg_index += f"【 🇺🇸 美國市場 】\n"
    for t in us_indices:
        if t in idx_map: line_msg_index += analyze_index_trend(t, idx_map[t]["name"], get_ma_list_from_item(idx_map[t])) + "\n"
    line_msg_index += "\n"
    
    line_msg_index += f"【 🇪🇺 歐洲市場 】\n"
    for t in eu_indices:
        if t in idx_map: line_msg_index += analyze_index_trend(t, idx_map[t]["name"], get_ma_list_from_item(idx_map[t])) + "\n"
    line_msg_index += "\n"
    
    line_msg_index += f"【 🌏 亞洲市場 】\n"
    for t in as_indices:
        if t in idx_map: line_msg_index += analyze_index_trend(t, idx_map[t]["name"], get_ma_list_from_item(idx_map[t])) + "\n"
            
    send_line_message(line_msg_index, access_token, user_id)

    # 每週一限定推播
    if weekday == 0:  
        sectors_url = "https://finviz.com/groups.ashx?g=sector&v=110"
        line_msg_sectors = f"📅 【每週一限定】美股 11 大類股週線趨勢輪動圖\n"
        line_msg_sectors += f"⏳ 包含 1-2 年週線級別核心波段追蹤\n\n"
        line_msg_sectors += f"🔗 類股觀測鏈結：\n{sectors_url}"
        
        send_line_message(line_msg_sectors, access_token, user_id)

    # =========================================================================
    # 💾 把最新的 index.html 推送到 Github Pages
    # =========================================================================
    os.system('git config --global user.name "github-actions[bot]"')
    os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
    # 注意這邊改回 index.html 了
    os.system('git add docs/index.html data/*.csv')
    os.system('git commit -m "⚙️ 量化報告自動更新 (單用戶模式+圖表修復)"')
    os.system('git push')

if __name__ == "__main__":
    main()

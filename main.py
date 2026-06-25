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
# 📡 Supabase 雲端資料庫動態名單讀取器
# =========================================================================
def load_configs_from_supabase():
    configs = {
        "tw_g1": {}, "tw_g2": {},
        "us_g1": {}, "us_g2": {}, "us_g3": {}, "us_g4": {}
    }
    
    supabase_url = "https://bxhqpfeberqbtxymghyt.supabase.co/rest/v1"
    supabase_key = "sb_publishable_eEJNM_96jblQ_90vpcYC0g_PzyGJNOK"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}"
    }
    
    try:
        res_groups = requests.get(f"{supabase_url}/groups", headers=headers, timeout=10)
        res_stocks = requests.get(f"{supabase_url}/stocks", headers=headers, timeout=10)
        
        if res_groups.status_code == 200 and res_stocks.status_code == 200:
            groups_data = res_groups.json()
            stocks_data = res_stocks.json()
            
            name_mapping = {
                "台股-權值精選": "tw_g1",
                "台股-熱門": "tw_g2",
                "美股-權值精選": "us_g1",
                "美股-低本益比": "us_g2",
                "美股-超級績效": "us_g3",
                "美股-熱門": "us_g4"
            }
            
            group_id_to_key = {}
            for g in groups_data:
                g_name = g['name'].strip()
                if g_name in name_mapping:
                    group_id_to_key[g['id']] = name_mapping[g_name]
            
            for s in stocks_data:
                g_id = s.get('group_id')
                mapped_key = group_id_to_key.get(g_id)
                
                if mapped_key:
                    ma_list = []
                    for ma_key in ['ma1', 'ma2', 'ma3', 'ma4']:
                        if s.get(ma_key) is not None and int(s[ma_key]) > 0:
                            ma_list.append(int(s[ma_key]))
                    
                    if not ma_list: 
                        ma_list = [20]
                    
                    configs[mapped_key][s['ticker']] = ma_list
                    print(f"🔗 雲端同步：【{s['ticker']}】成功納入 {mapped_key}，獨立均線: {ma_list}MA")
                    
    except Exception as e:
        print(f"⚠️ 讀取雲端資料庫失敗，原因: {e}")
        
    return configs

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
                print(f"✅ TWSE 上市：{len(tickers)} 檔通過")
                break
        except Exception as e:
            if attempt == 2: print(f"❌ 獲取上市清單失敗: {e}")
            else: time.sleep(2)

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
                print(f"✅ TPEx 上櫃：{tpex_count} 檔通過")
                break
        except Exception as e:
            if attempt == 2: print(f"❌ 獲取上櫃清單失敗: {e}")
            else: time.sleep(2)

    tickers = list(set(tickers))
    if not tickers:
        return ["2330.TW", "2317.TW", "2454.TW", "2603.TW", "0050.TW"]
    print(f"🔥 雙引擎完成！台股總計: {len(tickers)} 檔 (含上市+上櫃)")
    return tickers

def get_us_tickers():
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        html_text = requests.get(url, headers=HTTP_HEADERS).text
        df = pd.read_html(io.StringIO(html_text))[0]
        return [t.replace('.', '-') for t in df['Symbol'].tolist()]
    except Exception as e: print(f"獲取美股清單失敗: {e}")
    return ["AAPL", "MSFT", "NVDA"]

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
    colors = ['#FF9800', '#2196F3', '#4CAF50', '#E91E63']
    for idx, ma_window in enumerate(ma_list):
        ma_col = f'MA{ma_window}'
        if ma_col in df_chart.columns:
            ma_values = [None if pd.isna(val) else float(val) for val in df_chart[ma_col].tolist()]
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
            if ma20 * 0.97 <= price < ma20:
                diff_pct = ((price / ma20) - 1) * 100
                df_chart = df_clean.tail(60)
                title_str = f"(現價: {round(price,2)} | 距MA20: {round(diff_pct,2)}%)"
                chart_data = build_stock_data(df_chart, ticker, title_str, [20])
                matched_list.append({'ticker': ticker, 'volume': int(latest_vol), 'chart_data': chart_data})
        except Exception: continue

    print(f"掃描結果: {len(matched_list)} 檔符合條件")
    matched_list.sort(key=lambda x: x['volume'], reverse=True)
    return matched_list

def process_custom_groups(group_dict):
    matched_list = []
    if not group_dict: return matched_list
    tickers = list(group_dict.keys())
    os.makedirs(DATA_DIR, exist_ok=True)
    
    try:
        data = yf.download(tickers, period="5d", progress=False, threads=False)
        for ticker in tickers:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if ticker in data.columns.get_level_values(1):
                        df_today = data.xs(ticker, level=1, axis=1)
                    elif ticker in data.columns.get_level_values(0):
                        df_today = data[ticker]
                    else: continue
                else:
                    df_today = data.copy()
                
                required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
                if not all(col in df_today.columns for col in required_cols): continue
                df_today_clean = df_today[required_cols].dropna()
                
                csv_path = os.path.join(DATA_DIR, f"{ticker}.csv")
                if os.path.exists(csv_path):
                    df_local = pd.read_csv(csv_path, index_col=0, parse_dates=True)
                    df_combined = pd.concat([df_local, df_today_clean])
                else:
                    df_init = yf.download(ticker, period="250d", progress=False, threads=False)
                    if isinstance(df_init.columns, pd.MultiIndex):
                        df_init = df_init.xs(ticker, level=1, axis=1) if ticker in df_init.columns.get_level_values(1) else df_init[ticker]
                    df_combined = df_init[required_cols].dropna()
                    
                df_combined = df_combined[~df_combined.index.duplicated(keep='last')].sort_index().tail(MAX_DAYS)
                df_combined.to_csv(csv_path)
                
                ma_list = group_dict[ticker]
                for ma_window in ma_list: 
                    df_combined[f'MA{ma_window}'] = df_combined['Close'].rolling(window=ma_window).mean()
                    
                price = float(df_combined['Close'].iloc[-1])
                matched_any_ma = False
                triggered_info = []
                
                for ma_window in ma_list:
                    ma_val = float(df_combined[f'MA{ma_window}'].iloc[-1])
                    if pd.isna(ma_val): continue
                    
                    if ma_val * 0.98 <= price < ma_val:
                        diff_pct = ((price / ma_val) - 1) * 100
                        triggered_info.append(f"近MA{ma_window}({round(diff_pct,2)}%)")
                        matched_any_ma = True
                
                if not matched_any_ma: continue
                
                df_chart = df_combined.tail(60)
                ma_status_str = " | ".join([f"MA{w}:{round(df_combined[f'MA{w}'].iloc[-1], 2)}" for w in ma_list if not pd.isna(df_combined[f'MA{w}'].iloc[-1])])
                title_str = f"(現價: {round(price,2)} | {'/'.join(triggered_info)} | {ma_status_str})"
                chart_data = build_stock_data(df_chart, ticker, title_str, ma_list)
                matched_list.append({'ticker': ticker, 'volume': 0, 'chart_data': chart_data})
            except Exception: continue
    except Exception: pass
    return matched_list

def generate_html(data_dict, date_str):
    js_store = "const chartDataStore = " + json.dumps(data_dict, ensure_ascii=False) + ";\n"
    html_template = f"""<!DOCTYPE html><html><head><title>台美股均線潛伏報告</title><meta name="viewport" content="width=device-width, initial-scale=1.0"><script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script><style>body {{ background-color: #111; color: #fff; font-family: Arial, sans-serif; margin: 0; padding: 10px; }} .header {{ text-align: center; padding: 15px 0; background: #222; margin-bottom: 15px; border-radius: 8px; }} .category-box {{ background: #1a1a1a; padding: 12px; margin-bottom: 15px; border-radius: 8px; border-left: 4px solid #00b0ff; }} .category-title {{ font-size: 15px; font-weight: bold; color: #00ff88; margin-bottom: 10px; padding-left: 5px; }} .tabs {{ display: flex; flex-wrap: wrap; gap: 6px; }} .tab-btn {{ background: #2a2a2a; color: #aaa; border: none; padding: 8px 12px; font-size: 13px; cursor: pointer; border-radius: 4px; transition: 0.3s; }} .tab-btn:hover {{ background: #3a3a3a; }} .tab-btn.active {{ background: #00b0ff; color: #fff; font-weight: bold; }} .market-section {{ display: none; max-width: 800px; margin: 0 auto; }} .market-section.active {{ display: block; }} .chart-card {{ background: #1e1e1e; margin-bottom: 25px; padding: 10px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }} .plotly-container {{ height: 400px; background: #151515; border-radius: 6px; }} .no-data {{ text-align: center; color: #888; padding: 40px; font-size: 14px; }}</style></head><body><div class="header"><h2>📈 台美股量化潛伏網頁報告 ({date_str})</h2><p style="margin: 5px 0 0 0; color:#00ff88; font-size:13px;">增量滾動數據儲存版</p></div>
    
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
    
    keys_list = ['tw_all', 'tw_g1', 'tw_g2', 'us_all', 'us_g1', 'us_g2', 'us_g3', 'us_g4']
    for key in keys_list:
        active_class = " active" if key == 'tw_all' else ""
        html_template += f'<div id="{key}-market" class="market-section{active_class}">'
        if data_dict[key]:
            for idx in range(len(data_dict[key])): html_template += f'<div class="chart-card"><div id="chart-{key}-{idx}" class="plotly-container"></div></div>'
        else: html_template += '<div class="no-data">此分類目前無股票符合群組自訂均線潛伏條件</div>'
        html_template += '</div>'
        
    html_template += f"""<script>{js_store} function renderMarketCharts(marketId) {{ const items = chartDataStore[marketId]; if (!items) return; items.forEach((item, idx) => {{ const elementId = "chart-" + marketId + "-" + idx; const container = document.getElementById(elementId); if (container && !container.dataset.done) {{ Plotly.newPlot(container, item.chart_data.data, item.chart_data.layout, {{responsive: true, displayModeBar: false}}); container.dataset.done = "true"; }} }}); }} function switchMarket(event, marketId) {{ document.querySelectorAll('.market-section').forEach(el => el.classList.remove('active')); document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active')); document.getElementById(marketId + '-market').classList.add('active'); if(event) {{ event.currentTarget.classList.add('active'); }} else {{ document.getElementById('btn-' + marketId).classList.add('active'); }} renderMarketCharts(marketId); window.dispatchEvent(new Event('resize')); }} window.addEventListener("load", function() {{ renderMarketCharts('tw_all'); }});</script></body></html>"""
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f: f.write(html_template)

# =========================================================================
# 🎯 核心修正：大盤扣抵判定大腦 (收盤正負0.1%增減分 + 5天內3天觸碰強制+0)
# =========================================================================
def analyze_index_trend(ticker, name, ma_list=[20, 60, 240]):
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
        
        # 獲取最近 5 個交易日的數據（包含今天）
        df_last5 = df.tail(5)
        
        for ma_col in available_mas:
            # 💡 檢查 5 天內是否有 3 天以上（含）實體或上下影線碰觸到均線 (Low <= MA <= High)
            touch_count = 0
            for _, row_5 in df_last5.iterrows():
                if row_5['Low'] <= row_5[ma_col] <= row_5['High']:
                    touch_count += 1
            
            if touch_count >= 3:
                # 🟢 糾纏狀態：5天內有3天以上觸碰，此均線判定直接給 +0
                score += 0
            else:
                # 📈 未糾纏：改看最新收盤價是否超出 0.1% 的關鍵門檻
                latest_close = latest['Close']
                latest_ma = latest[ma_col]
                
                if latest_close > latest_ma * 1.001:    # 收盤價在均線上 0.1% 以上
                    score += 1
                elif latest_close < latest_ma * 0.999:  # 收盤價在均線下 0.1% 以下
                    score -= 1
                else:
                    score += 0                          # 落在正負 0.1% 緩衝區內記為 +0
                    
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
        return f"{icon} {name}\n   ├ 均線: {score_label} ({score}/{total_ma_count}MA)\n   └  {final_status}"
    except Exception as e: return f"⚪ {name}: 分析發生異常"

def main():
    access_token = os.environ.get("LINE_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not access_token or not user_id: return
    today_str = datetime.now().strftime("%Y-%m-%d")
    weekday = datetime.now().weekday() 

    db_configs = load_configs_from_supabase()
    
    tw_g1_config = db_configs["tw_g1"]
    tw_g2_config = db_configs["tw_g2"]
    us_g1_config = db_configs["us_g1"]
    us_g2_config = db_configs["us_g2"]
    us_g3_config = db_configs["us_g3"]
    us_g4_config = db_configs["us_g4"]
    
    tw_market_pool = get_tw_tickers(TW_MIN_VOLUME)
    
    data_dict = {
        'tw_all': scan_market(tw_market_pool, min_volume=TW_MIN_VOLUME), 
        'tw_g1': process_custom_groups(tw_g1_config), 
        'tw_g2': process_custom_groups(tw_g2_config),
        
        'us_all': scan_market(get_us_tickers(), min_volume=US_MIN_VOLUME),
        'us_g1': process_custom_groups(us_g1_config), 
        'us_g2': process_custom_groups(us_g2_config),
        'us_g3': process_custom_groups(us_g3_config), 
        'us_g4': process_custom_groups(us_g4_config)
    }
    generate_html(data_dict, today_str)
    
    os.system('git config --global user.name "github-actions[bot]"')
    os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
    
    os.system('git add docs/index.html data/*.csv')
    os.system('git commit -m "⚙️ 量化報告自動更新"')
    os.system('git push')

    web_url = "https://wudn9922.github.io/my-stock-screener/"
    
    line_msg_stocks = f"🎯 {today_str} 全市場增量看盤網頁！\n\n"
    line_msg_stocks += f"🇹🇼 【台灣股市區塊】\n"
    line_msg_stocks += f" ├ 1. 全市場符合(含上市櫃)：{len(data_dict['tw_all'])} 檔\n"
    line_msg_stocks += f" ├ 2. 權值精選符合：{len(data_dict['tw_g1'])} 檔\n"
    line_msg_stocks += f" └ 3. 熱門符合：{len(data_dict['tw_g2'])} 檔\n\n"
    line_msg_stocks += f"🇺🇸 【美國股市區塊】\n"
    line_msg_stocks += f" ├ 1. 全市場符合：{len(data_dict['us_all'])} 檔\n"
    line_msg_stocks += f" ├ 2. 權值精選符合：{len(data_dict['us_g1'])} 檔\n"
    line_msg_stocks += f" ├ 3. 低本益比符合：{len(data_dict['us_g2'])} 檔\n"
    line_msg_stocks += f" ├ 4. 超級績效符合：{len(data_dict['us_g3'])} 檔\n"
    line_msg_stocks += f" └ 5. 熱門符合：{len(data_dict['us_g4'])} 檔\n\n"
    line_msg_stocks += f"🔗 點擊網址：\n{web_url}"
    send_line_message(line_msg_stocks, access_token, user_id)

    line_msg_index = f"🌍 {today_str} 全球大盤多空量化報告\n"
    line_msg_index += f"📊 評分標準: 均線糾纏自適應/0.1%過濾機制\n"
    line_msg_index += f"========================\n\n"
    
    line_msg_index += f"【 🇹🇼 台灣市場 】\n"
    line_msg_index += analyze_index_trend("^TWII", "台灣加權指數", ma_list=[20, 27, 61]) + "\n"
    line_msg_index += analyze_index_trend("^TWOII", "台灣櫃買指數(OTC)", ma_list=[20, 60, 120]) + "\n\n"
    
    line_msg_index += f"【 🇺🇸 美國市場 】\n"
    line_msg_index += analyze_index_trend("^GSPC", "美國標普500", ma_list=[23, 60]) + "\n"
    line_msg_index += analyze_index_trend("^DJI", "美國道瓊工業", ma_list=[20, 23, 55]) + "\n"
    line_msg_index += analyze_index_trend("^IXIC", "美國那斯達克", ma_list=[29]) + "\n"
    line_msg_index += analyze_index_trend("^RUT", "美國羅素2000", ma_list=[21, 56]) + "\n"
    line_msg_index += analyze_index_trend("^SOX", "美國費城半導體", ma_list=[20, 58, 108]) + "\n\n"
    
    line_msg_index += f"【 🇪🇺 歐洲市場 】\n"
    line_msg_index += analyze_index_trend("^FCHI", "法國CAC40", ma_list=[21]) + "\n"
    line_msg_index += analyze_index_trend("^FTSE", "英國富時100", ma_list=[20]) + "\n"
    line_msg_index += analyze_index_trend("^GDAXI", "德國DAX指數", ma_list=[23]) + "\n\n"
    
    line_msg_index += f"【 🌏 亞洲市場 】\n"
    line_msg_index += analyze_index_trend("^N225", "日本日經225", ma_list=[24]) + "\n"
    line_msg_index += analyze_index_trend("^KS11", "韓國綜合指數", ma_list=[22]) + "\n"
    
    send_line_message(line_msg_index, access_token, user_id)

    if weekday == 0:  
        sectors_url = "https://finviz.com/groups.ashx?g=sector&v=110"
        line_msg_sectors = f"📅 【每週一限定】美股 11 大類股週線趨勢輪動圖\n"
        line_msg_sectors += f"⏳ 包含 1-2 年週線級別核心波段追蹤\n\n"
        line_msg_sectors += f"🔗 類股觀測鏈結：\n{sectors_url}"
        send_line_message(line_msg_sectors, access_token, user_id)

if __name__ == "__main__":
    main()

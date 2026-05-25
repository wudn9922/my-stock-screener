import yfinance as yf
import pandas as pd
import requests
import os
from datetime import datetime
import json

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def send_line_message(msg, access_token, user_id):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    payload = {"to": user_id, "messages": [{"type": "text", "text": msg}]}
    res = requests.post(url, json=payload, headers=headers)
    return res.status_code

def get_tw_tickers():
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        res = requests.get(url, headers=HTTP_HEADERS, timeout=15)
        if res.status_code == 200:
            return [f"{item['Code'].strip()}.TW" for item in res.json() if len(item['Code'].strip()) == 4 and item['Code'].strip().isdigit()]
    except Exception as e: print(f"獲取台股清單失敗: {e}")
    return ["2330.TW", "2317.TW", "2454.TW"]

def get_us_tickers():
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        df = pd.read_html(requests.get(url, headers=HTTP_HEADERS).text)[0]
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
    matched_list = []
    chunk_size = 40
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i+chunk_size]
        try:
            data = yf.download(chunk, period="150d", progress=False)
            if data.empty: continue
            for ticker in chunk:
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        if ticker in data.columns.get_level_values(1): df_t = data.xs(ticker, axis=1, level=1)
                        elif ticker in data.columns.get_level_values(0): df_t = data.xs(ticker, axis=1, level=0)
                        else: continue
                    else: df_t = data.copy()
                    required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
                    if not all(col in df_t.columns for col in required_cols): continue
                    df_clean = df_t[required_cols].dropna()
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
        except Exception as e: print(f"批次錯誤 {i}: {e}")
    matched_list.sort(key=lambda x: x['volume'], reverse=True)
    return matched_list

def process_custom_groups(group_dict):
    matched_list = []
    tickers = list(group_dict.keys())
    if not tickers: return matched_list
    try: data = yf.download(tickers, period="500d", progress=False)
    except Exception as e: return matched_list
    for ticker in tickers:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if ticker in data.columns.get_level_values(1): df_t = data.xs(ticker, axis=1, level=1)
                elif ticker in data.columns.get_level_values(0): df_t = data.xs(ticker, axis=1, level=0)
                else: continue
            else: df_t = data.copy()
            required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
            if not all(col in df_t.columns for col in required_cols): continue
            df_clean = df_t[required_cols].dropna()
            if df_clean.empty: continue
            ma_list = group_dict[ticker]
            for ma_window in ma_list: df_clean[f'MA{ma_window}'] = df_clean['Close'].rolling(window=ma_window).mean()
            price = float(df_clean['Close'].iloc[-1])
            df_chart = df_clean.tail(60)
            ma_info = " | ".join([f"MA{w}: {round(df_clean[f'MA{w}'].iloc[-1], 2)}" for w in ma_list if not pd.isna(df_clean[f'MA{w}'].iloc[-1])])
            title_str = f"(現價: {round(price,2)} | {ma_info})"
            chart_data = build_stock_data(df_chart, ticker, title_str, ma_list)
            matched_list.append({'ticker': ticker, 'volume': 0, 'chart_data': chart_data})
        except Exception: continue
    return matched_list

def generate_html(data_dict, date_str):
    js_store = "const chartDataStore = " + json.dumps(data_dict, ensure_ascii=False) + ";\n"
    html_template = f"""<!DOCTYPE html><html><head><title>台美股均線潛伏報告</title><meta name="viewport" content="width=device-width, initial-scale=1.0"><script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script><style>body {{ background-color: #111; color: #fff; font-family: Arial, sans-serif; margin: 0; padding: 10px; }} .header {{ text-align: center; padding: 15px 0; background: #222; margin-bottom: 15px; border-radius: 8px; }} .tabs {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 5px; margin-bottom: 20px; }} .tab-btn {{ background: #333; color: #ccc; border: none; padding: 10px 15px; font-size: 14px; cursor: pointer; border-radius: 4px; transition: 0.3s; }} .tab-btn.active {{ background: #00b0ff; color: #fff; font-weight: bold; }} .market-section {{ display: none; max-width: 800px; margin: 0 auto; }} .market-section.active {{ display: block; }} .chart-card {{ background: #1e1e1e; margin-bottom: 25px; padding: 10px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }} .plotly-container {{ height: 400px; background: #151515; border-radius: 6px; }} .no-data {{ text-align: center; color: #888; padding: 40px; font-size: 16px; }}</style></head><body><div class="header"><h2>📈 台美股量化潛伏網頁報告 ({date_str})</h2><p style="margin: 5px 0 0 0; color:#00ff88; font-size:13px;">穩定修復版 | K線與均線正常對齊</p></div><div class="tabs"><button class="tab-btn active" onclick="switchMarket(event, 'tw')">🇹🇼 台股 ({len(data_dict['tw'])})</button><button class="tab-btn" onclick="switchMarket(event, 'us')">🇺🇸 美股 ({len(data_dict['us'])})</button><button class="tab-btn" onclick="switchMarket(event, 'g1')">🚀 超級績效股 ({len(data_dict['g1'])})</button><button class="tab-btn" onclick="switchMarket(event, 'g2')">💎 績優股 ({len(data_dict['g2'])})</button><button class="tab-btn" onclick="switchMarket(event, 'g3')">🎯 重點關注股 ({len(data_dict['g3'])})</button><button class="tab-btn" onclick="switchMarket(event, 'g4')">👀 近期關注股 ({len(data_dict['g4'])})</button><button class="tab-btn" onclick="switchMarket(event, 'g5')">🎲 投機股 ({len(data_dict['g5'])})</button></div>"""
    for key in ['tw', 'us', 'g1', 'g2', 'g3', 'g4', 'g5']:
        active_class = " active" if key == 'tw' else ""
        html_template += f'<div id="{key}-market" class="market-section{active_class}">'
        if data_dict[key]:
            for idx in range(len(data_dict[key])): html_template += f'<div class="chart-card"><div id="chart-{key}-{idx}" class="plotly-container"></div></div>'
        else: html_template += '<div class="no-data">此分類目前無股票資料</div>'
        html_template += '</div>'
    html_template += f"""<script>{js_store} function renderMarketCharts(marketId) {{ const items = chartDataStore[marketId]; if (!items) return; items.forEach((item, idx) => {{ const elementId = "chart-" + marketId + "-" + idx; const container = document.getElementById(elementId); if (container && !container.dataset.done) {{ Plotly.newPlot(container, item.chart_data.data, item.chart_data.layout, {{responsive: true, displayModeBar: false}}); container.dataset.done = "true"; }} }}); }} function switchMarket(event, marketId) {{ document.querySelectorAll('.market-section').forEach(el => el.classList.remove('active')); document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active')); document.getElementById(marketId + '-market').classList.add('active'); event.currentTarget.classList.add('active'); renderMarketCharts(marketId); window.dispatchEvent(new Event('resize')); }} window.addEventListener("load", function() {{ renderMarketCharts('tw'); }});</script></body></html>"""
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f: f.write(html_template)


# =========================================================================
# 🧠 【大盤多空量化分析系統 - 修正：動態均線條數滿分機制】
# =========================================================================
def analyze_index_trend(ticker, name, ma_list=[20, 60, 240]):
    try:
        df = yf.download(ticker, period="4y", progress=False)
        if df.empty or len(df) < 750: return f"⚪ {name}: 數據不足無法分析"
        
        df = df.copy()
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        
        # 根據傳入的 ma_list 動態計算均線
        available_mas = []
        for ma in ma_list:
            col_name = f'MA{ma}'
            df[col_name] = df['Close'].rolling(window=ma).mean()
            available_mas.append(col_name)
            
        df = df.dropna(subset=available_mas)
        
        # --- 1. 均線得分計算 (動態上限) ---
        latest = df.iloc[-1]
        score = 0
        total_ma_count = len(available_mas) # 動態獲取目前比對了幾條均線
        
        for ma_col in available_mas:
            ma_val = latest[ma_col]
            upper_bound = ma_val * 1.005
            lower_bound = ma_val * 0.995
            
            if latest['Open'] > upper_bound and latest['High'] > upper_bound and latest['Low'] > upper_bound and latest['Close'] > upper_bound:
                score += 1
            elif latest['Open'] < lower_bound and latest['High'] < lower_bound and latest['Low'] < lower_bound and latest['Close'] < lower_bound:
                score -= 1

        # 🎯 核心更正：基於 total_ma_count 進行動態文字標籤轉換
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

        # --- 2. 歷史極值與時間回溯 ---
        df_3y = df.tail(252 * 3)
        idx_3y_high = df_3y['High'].idxmax()
        val_3y_high = df_3y['High'].max()
        
        latest_date = df.index[-1]
        months_since_high = (latest_date - idx_3y_high).days / 30.0
        
        # 尋找最近 120 天波段轉折點
        df_recent = df.tail(120).copy()
        peaks = []
        troughs = []
        for i in range(2, len(df_recent)-2):
            if df_recent['High'].iloc[i] > df_recent['High'].iloc[i-1] and df_recent['High'].iloc[i] > df_recent['High'].iloc[i-2] and \
               df_recent['High'].iloc[i] > df_recent['High'].iloc[i+1] and df_recent['High'].iloc[i] > df_recent['High'].iloc[i+2]:
                peaks.append((df_recent.index[i], df_recent['High'].iloc[i]))
            if df_recent['Low'].iloc[i] < df_recent['Low'].iloc[i-1] and df_recent['Low'].iloc[i] < df_recent['Low'].iloc[i-2] and \
               df_recent['Low'].iloc[i] < df_recent['Low'].iloc[i+1] and df_recent['Low'].iloc[i] < df_recent['Low'].iloc[i+2]:
                troughs.append((df_recent.index[i], df_recent['Low'].iloc[i]))

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
                if latest['Close'] < bear_low:
                    macro_trend = "空頭趨勢"

        micro_走勢 = "多頭走勢"
        if months_since_high >= 1.0 and (lower_peak_count + lower_trough_count) >= 2:
            micro_走勢 = "空頭走勢"

        final_status = f"{macro_trend}中的{micro_走勢}"
        
        if macro_trend == "多頭趨勢" and micro_走勢 == "多頭走勢": icon = "🔺"
        elif macro_trend == "多頭趨勢" and micro_走勢 == "空頭走勢": icon = "💡"
        elif macro_trend == "空頭趨勢" and micro_走勢 == "空頭走勢": icon = "🔻"
        else: icon = "⚡"

        return f"{icon} {name}: 均線狀態「{score_label}」({score}/{total_ma_count}MA) | 當前狀態: {final_status}"
    
    except Exception as e:
        return f"⚪ {name}: 分析發生異常 ({str(e)[:15]})"


def main():
    access_token = os.environ.get("LINE_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not access_token or not user_id: return
    today_str = datetime.now().strftime("%Y-%m-%d")
    weekday = datetime.now().weekday() 

    g1_config = {"2330.TW": [10, 20], "NVDA": [10, 20], "AMD": [20]}
    g2_config = {"2317.TW": [20, 60], "AAPL": [20, 120], "MSFT": [20, 60, 120]}
    g3_config = {"2454.TW": [5, 10, 20], "TSLA": [10, 20]}
    g4_config = {"2603.TW": [20, 60], "AMZN": [20]}
    g5_config = {"2609.TW": [5, 10], "0050.TW": [5, 20]}
    
    data_dict = {
        'tw': scan_market(get_tw_tickers(), min_volume=2000000), 
        'us': scan_market(get_us_tickers(), min_volume=1000000),
        'g1': process_custom_groups(g1_config), 'g2': process_custom_groups(g2_config),
        'g3': process_custom_groups(g3_config), 'g4': process_custom_groups(g4_config), 'g5': process_custom_groups(g5_config)
    }
    generate_html(data_dict, today_str)
    
    os.system('git config --global user.name "github-actions[bot]"')
    os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
    os.system('git add docs/index.html')
    os.system('git commit -m "⚙️ 修復：支持動態均線數目的滿分標籤判定"')
    os.system('git push')

    # =========================================================================
    # ✉️ 【發送 訊息一：每日個股均線潛伏報告】
    # =========================================================================
    web_url = "https://wudn9922.github.io/my-stock-screener/"
    line_msg_stocks = f"🎯 {today_str} 全市場看盤網頁！\n"
    line_msg_stocks += f"🇹🇼 台股符合：{len(data_dict['tw'])} 檔\n"
    line_msg_stocks += f"🇺🇸 美股符合：{len(data_dict['us'])} 檔\n"
    line_msg_stocks += f"🔥 自選股狀態：\n"
    line_msg_stocks += f" ├ 🚀 超級績效股：{len(data_dict['g1'])} 檔\n"
    line_msg_stocks += f" ├ 💎 績優股：{len(data_dict['g2'])} 檔\n"
    line_msg_stocks += f" ├ 🎯 重點關注：{len(data_dict['g3'])} 檔\n"
    line_msg_stocks += f" ├ 👀 近期關注股：{len(data_dict['g4'])} 檔\n"
    line_msg_stocks += f" └ 🎲 投機股：{len(data_dict['g5'])} 檔\n"
    line_msg_stocks += f"🔗 點擊網址：\n{web_url}"
    send_line_message(line_msg_stocks, access_token, user_id)

    # =========================================================================
    # ✉️ 【發送 訊息二：每日全球大盤多空量化報告】
    # =========================================================================
    # 這裡你可以針對不同的指數，自由客製化傳入不同數量的均線列表
    # 格式：analyze_index_trend(代碼, 名稱, ma_list=[均線天數])
    line_msg_index = f"🌍 {today_str} 全球大盤多空量化報告\n"
    line_msg_index += f"📊 評分標準: 均線0.5%緩衝/自適應滿分機制\n"
    line_msg_index += f"------------------------\n"
    
    line_msg_index += analyze_index_trend("^TWII", "台灣加權指數", ma_list=[20, 60, 240]) + "\n"
    line_msg_index += analyze_index_trend("^GSPC", "美國標普500", ma_list=[20, 60, 240]) + "\n"
    line_msg_index += analyze_index_trend("^DJI", "美國道瓊工業", ma_list=[20, 60]) + "\n" # 範例：只測2條
    line_msg_index += analyze_index_trend("^IXIC", "美國那斯達克", ma_list=[20, 60, 240]) + "\n"
    line_msg_index += analyze_index_trend("^RUT", "美國羅素2000", ma_list=[240]) + "\n"      # 範例：只測1條
    line_msg_index += analyze_index_trend("^SOX", "美國費城半導體", ma_list=[20, 60, 240]) + "\n"
    line_msg_index += analyze_index_trend("^FCHI", "法國CAC40", ma_list=[20, 60, 240]) + "\n"
    line_msg_index += analyze_index_trend("^FTSE", "英國富時100", ma_list=[20, 60, 240]) + "\n"
    line_msg_index += analyze_index_trend("^GDAXI", "德國DAX指數", ma_list=[20, 60, 240]) + "\n"
    line_msg_index += analyze_index_trend("^N225", "日本日經225", ma_list=[20, 60, 240]) + "\n"
    line_msg_index += analyze_index_trend("^KS11", "韓國綜合指數", ma_list=[20, 60, 240]) + "\n"
    
    send_line_message(line_msg_index, access_token, user_id)

    # =========================================================================
    # ✉️ 【發送 訊息三：每週一限定 - 美股類股週線圖網址】
    # =========================================================================
    if weekday == 0:  
        print("檢測到今天為週一，發送最新免登入美股類股觀測鏈結...")
        sectors_url = "https://finviz.com/groups.ashx?g=sector&v=110"
        
        line_msg_sectors = f"📅 【每週一限定】美股 11 大類股週線趨勢輪動圖\n"
        line_msg_sectors += f"⏳ 包含 1-2 年週線級別核心波段追蹤\n"
        line_msg_sectors += f"🔗 類股觀測鏈結：\n{sectors_url}"
        send_line_message(line_msg_sectors, access_token, user_id)

if __name__ == "__main__":
    main()
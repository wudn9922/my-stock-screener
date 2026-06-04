import yfinance as yf
import pandas as pd
import requests
import os
from datetime import datetime
import json
import time

DATA_DIR = "data"
MAX_DAYS = 201 # 嚴格維持資料集最大天數

def send_line_message(msg, access_token, user_id):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    payload = {"to": user_id, "messages": [{"type": "text", "text": msg}]}
    try: requests.post(url, json=payload, headers=headers, timeout=10)
    except Exception: pass

def build_stock_data(df_chart, ticker, title_suffix):
    # 用於 Plotly 網頁的繪圖格式
    date_strings = [str(d)[:10] for d in df_chart.index]
    return {
        "data": [
            {
                "type": "candlestick", "name": "K線", "x": date_strings,
                "open": df_chart['Open'].astype(float).tolist(),
                "high": df_chart['High'].astype(float).tolist(),
                "low": df_chart['Low'].astype(float).tolist(),
                "close": df_chart['Close'].astype(float).tolist(),
                "increasing": {"line": {"color": "#ef5350"}}, "decreasing": {"line": {"color": "#26a69a"}}
            },
            {
                "type": "scatter", "mode": "lines", "name": "MA20",
                "x": date_strings, "y": df_chart['MA20'].astype(float).tolist(),
                "line": {"color": "#FF9800", "width": 2}
            }
        ],
        "layout": {
            "title": f"{ticker} {title_suffix}", 
            "xaxis": {"type": "date", "rangeslider": {"visible": False}},
            "yaxis": {"fixedrange": False}, "template": "plotly_dark", 
            "margin": {"l": 40, "r": 20, "t": 50, "b": 40}, "height": 400
        }
    }

def update_and_scan_dataset(tickers, is_us=False):
    """
    🔄 核心邏輯：
    1. 判斷本地是否有歷史 CSV，若無則初始化下載 250 天。
    2. 若有，則只向 Yahoo 下載最新 5 天的資料（防週末跳空），並進行聯集（Combine）。
    3. 嚴格對資料集進行瘦身，只保留最新的 201 天紀錄，並存回 CSV。
    4. 進行 MA20 潛伏股篩選。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    matched_list = []
    
    # 判斷哪些股票需要初始化（新加入的），哪些可以進行快速增量更新
    need_init = []
    need_update = []
    
    for ticker in tickers:
        csv_path = os.path.join(DATA_DIR, f"{ticker}.csv")
        if os.path.exists(csv_path):
            need_update.append(ticker)
        else:
            need_init.append(ticker)
            
    # 1. 處理新股票（初始化下載較長歷史）
    if need_init:
        print(f"🆕 發現 {len(need_init)} 檔新標的，進行首次歷史資料下載...")
        try:
            init_data = yf.download(need_init, period="250d", progress=False, group_by='ticker', timeout=30)
            for ticker in need_init:
                df_t = init_data[ticker] if ticker in init_data.columns.get_level_values(0) else None
                if df_t is not None and not df_t.empty:
                    df_clean = df_t[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                    df_clean = df_clean.tail(MAX_DAYS) # 只留 201 天
                    df_clean.to_csv(os.path.join(DATA_DIR, f"{ticker}.csv"))
        except Exception as e:
            print(f"初始化下載失敗: {e}")

    # 2. 處理舊股票（增量更新：只下載最新 5 天以包含最新交易日）
    if need_update:
        print(f"⚡ 正在對 {len(need_update)} 檔股票進行「當日最新進度」增量更新...")
        try:
            today_data = yf.download(need_update, period="5d", progress=False, group_by='ticker', timeout=20)
            for ticker in need_update:
                df_today = today_data[ticker] if ticker in today_data.columns.get_level_values(0) else None
                csv_path = os.path.join(DATA_DIR, f"{ticker}.csv")
                
                # 讀取本地舊資料
                df_local = pd.read_csv(csv_path, index_col=0, parse_dates=True)
                
                if df_today is not None and not df_today.empty:
                    df_today_clean = df_today[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                    
                    # 合併新舊資料，並以「日期」去重（重複的以最新下載的為準）
                    df_combined = pd.concat([df_local, df_today_clean])
                    df_combined = df_combined ~ df_combined.index.duplicated(keep='last')
                    df_combined = df_combined.sort_index()
                    
                    # ✂️ 關鍵：只保留最新 201 天的資料，刪除更早以前的
                    df_combined = df_combined.tail(MAX_DAYS)
                    
                    # 存回本地檔案系統
                    df_combined.to_csv(csv_path)
        except Exception as e:
            print(f"增量更新過程中發生錯誤: {e}. 將使用本地既有資料進行分析。")

    # 3. 全面掃描本地已更新完畢的資料集，計算技術指標
    for ticker in tickers:
        try:
            csv_path = os.path.join(DATA_DIR, f"{ticker}.csv")
            if not os.path.exists(csv_path): continue
            
            df_local = pd.read_csv(csv_path, index_col=0, parse_dates=True)
            if len(df_local) < 20: continue
            
            # 計算 MA20
            df_local['MA20'] = df_local['Close'].rolling(window=20).mean()
            price = float(df_local['Close'].iloc[-1])
            ma20 = float(df_local['MA20'].iloc[-1])
            if pd.isna(ma20): continue
            
            # 均線潛伏條件：現價在 MA20 之下且距離 3% 以內
            if ma20 * 0.97 <= price < ma20:
                diff_pct = ((price / ma20) - 1) * 100
                df_chart = df_local.tail(30) # 網頁只展示近 30 天 K 線
                title_str = f"(現價: {round(price,2)} | 距MA20: {round(diff_pct,2)}%)"
                chart_data = build_stock_data(df_chart, ticker, title_str)
                matched_list.append({
                    'ticker': ticker, 
                    'volume': int(df_local['Volume'].iloc[-1]), 
                    'chart_data': chart_data
                })
        except Exception:
            continue
            
    matched_list.sort(key=lambda x: x['volume'], reverse=True)
    return matched_list

def generate_html(data_dict, date_str):
    js_store = "const chartDataStore = " + json.dumps(data_dict, ensure_ascii=False) + ";\n"
    html_template = f"""<!DOCTYPE html><html><head><title>均線潛伏報告</title><meta name="viewport" content="width=device-width, initial-scale=1.0"><script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script><style>body {{ background-color: #111; color: #fff; font-family: Arial, sans-serif; margin: 0; padding: 10px; }} .header {{ text-align: center; padding: 15px 0; background: #222; margin-bottom: 15px; border-radius: 8px; }} .category-box {{ background: #1a1a1a; padding: 12px; margin-bottom: 15px; border-radius: 8px; border-left: 4px solid #00b0ff; }} .tabs {{ display: flex; flex-wrap: wrap; gap: 6px; }} .tab-btn {{ background: #2a2a2a; color: #aaa; border: none; padding: 8px 12px; font-size: 13px; cursor: pointer; border-radius: 4px; }} .tab-btn.active {{ background: #00b0ff; color: #fff; font-weight: bold; }} .market-section {{ display: none; max-width: 800px; margin: 0 auto; }} .market-section.active {{ display: block; }} .chart-card {{ background: #1e1e1e; margin-bottom: 25px; padding: 10px; border-radius: 8px; }} .plotly-container {{ height: 400px; }} .no-data {{ text-align: center; color: #888; padding: 40px; }}</style></head><body><div class="header"><h2>📈 增量更新版·均線潛伏報告 ({date_str})</h2></div>
    <div class="category-box" style="border-left-color: #ff5252;">
        <div class="tabs">
            <button id="btn-tw" class="tab-btn active" onclick="switchMarket('tw')">🇹🇼 台股精選 ({len(data_dict['tw'])})</button>
            <button id="btn-us" class="tab-btn" onclick="switchMarket('us')">🇺🇸 美股巨頭 ({len(data_dict['us'])})</button>
        </div>
    </div>
    <div id="tw-market" class="market-section active">"""
    for idx in range(len(data_dict['tw'])): html_template += f'<div class="chart-card"><div id="chart-tw-{idx}" class="plotly-container"></div></div>'
    if not data_dict['tw']: html_template += '<div class="no-data">今日台股無符合潛伏標的</div>'
    html_template += '</div><div id="us-market" class="market-section">'
    for idx in range(len(data_dict['us'])): html_template += f'<div class="chart-card"><div id="chart-us-{idx}" class="plotly-container"></div></div>'
    if not data_dict['us']: html_template += '<div class="no-data">今日美股無符合潛伏標的</div>'
    html_template += f"""</div><script>{js_store} function renderMarketCharts(marketId) {{ const items = chartDataStore[marketId]; if (!items) return; items.forEach((item, idx) => {{ const elementId = "chart-" + marketId + "-" + idx; const container = document.getElementById(elementId); if (container && !container.dataset.done) {{ Plotly.newPlot(container, item.chart_data.data, item.chart_data.layout, {{responsive: true, displayModeBar: false}}); container.dataset.done = "true"; }} }}); }} function switchMarket(marketId) {{ document.querySelectorAll('.market-section').forEach(el => el.classList.remove('active')); document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active')); document.getElementById(marketId + '-market').classList.add('active'); document.getElementById('btn-' + marketId).classList.add('active'); renderMarketCharts(marketId); }} window.addEventListener("load", function() {{ renderMarketCharts('tw'); }});</script></body></html>"""
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f: f.write(html_template)

def main():
    access_token = os.environ.get("LINE_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not access_token or not user_id: return
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 🇹🇼 嚴選台灣核心成分股 (150 檔)
    tw_core_tickers = [
        "0050.TW", "0056.TW", "00878.TW", "00919.TW", "00929.TW", "2330.TW", "2317.TW", "2454.TW", "2382.TW", "2308.TW",
        "2881.TW", "2882.TW", "2891.TW", "2886.TW", "2884.TW", "2892.TW", "2885.TW", "2880.TW", "2883.TW", "5880.TW",
        "2303.TW", "3711.TW", "2324.TW", "2357.TW", "2353.TW", "2327.TW", "2345.TW", "3231.TW", "6669.TW", 
        "2603.TW", "2609.TW", "2615.TW", "2618.TW", "2610.TW", "2201.TW", "2207.TW", "2352.TW", "2379.TW", "3034.TW",
        "3037.TW", "3189.TW", "8046.TW", "2408.TW", "2344.TW", "2449.TW", "3008.TW", "3406.TW", "2313.TW", "2360.TW",
        "3044.TW", "2409.TW", "3481.TW", "6116.TW", "1101.TW", "1102.TW", "1301.TW", "1303.TW", "1326.TW", "1402.TW",
        "2105.TW", "2002.TW", "2606.TW", "9904.TW", "9921.TW", "9945.TW", "2912.TW", "5904.TW", "1216.TW", "1227.TW",
        "1722.TW", "1717.TW", "4147.TW", "1795.TW", "6446.TW", "3702.TW", "2347.TW", "3036.TW", "2474.TW", "2354.TW",
        "2356.TW", "2395.TW", "6239.TW", "6205.TW", "3005.TW", "3596.TW", "3017.TW", "2421.TW", "3035.TW", "3443.TW",
        "3661.TW", "5269.TW", "6415.TW", "8054.TW", "2455.TW", "2458.TW", "3532.TW", "4919.TW", "4961.TW", "6271.TW",
        "6147.TWO", "5347.TWO", "6488.TWO", "8299.TWO", "3105.TWO", "5483.TWO", "3529.TWO", "3293.TWO", "6182.TWO", "4108.TWO",
        "4128.TWO", "4162.TWO", "4743.TWO", "6547.TWO", "3264.TWO", "5425.TWO", "6138.TWO", "8069.TWO", "8358.TWO", "3081.TWO",
        "3548.TWO", "3624.TWO", "4966.TWO", "5274.TWO", "5289.TWO", "6180.TWO", "6223.TWO", "6510.TWO", "8086.TWO", "8436.TWO",
        "1513.TW", "1519.TW", "1503.TW", "1514.TW", "1605.TW", "1608.TW", "1609.TW", "6806.TW", "3019.TW", "2368.TW",
        "5434.TW", "2481.TW", "3042.TW", "2457.TW", "2367.TW", "3515.TW", "2412.TW", "4904.TW", "3045.TW", "2498.TW"
    ]

    # 🇺🇸 嚴選美國市值前 50 大巨頭
    us_core_tickers = [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "LLY", "AVGO",
        "V", "JPM", "NVO", "UNH", "MA", "WMT", "XOM", "HD", "PG", "ORCL",
        "COST", "ASML", "AMD", "NFLX", "CTAS", "AAL", "INTC", "QCOM", "TXN", "MU",
        "AMAT", "LRCX", "ADI", "NXPI", "MRVL", "PANW", "SNPS", "CDNS", "MS", "GS",
        "BAC", "CAT", "GE", "HON", "BA", "LMT", "UPS", "FDX", "DIS", "NKE"
    ]

    data_dict = {
        'tw': update_and_scan_dataset(tw_core_tickers, is_us=False),
        'us': update_and_scan_dataset(us_core_tickers, is_us=True)
    }
    
    generate_html(data_dict, today_str)
    
    # 這裡的 git add 要同步把 data 資料夾加進去，才能把 CSV 進度存回 GitHub
    os.system('git config --global user.name "github-actions[bot]"')
    os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
    os.system('git add docs/index.html data/*.csv')
    os.system('git commit -m "⚙️ 滾動增量更新資料集與網頁報告"')
    os.system('git push')

    web_url = f"https://{os.environ.get('GITHUB_REPOSITORY_OWNER', 'wudn9922')}.github.io/my-stock-screener/"
    line_msg = f"🎯 {today_str} 增量儲存版報告\n\n"
    line_msg += f"🇹🇼 台股符合：{len(data_dict['tw'])} 檔\n"
    line_msg += f"🇺🇸 美股符合：{len(data_dict['us'])} 檔\n\n"
    line_msg += f"🔗 網址：\n{web_url}"
    send_line_message(line_msg, access_token, user_id)

if __name__ == "__main__":
    main()

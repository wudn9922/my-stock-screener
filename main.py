import yfinance as yf
import pandas as pd
import requests
import os
from datetime import datetime
import plotly.graph_objects as go

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def send_line_message(msg, access_token, user_id):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    payload = {"to": user_id, "messages": [{"type": "text", "text": msg}]}
    requests.post(url, json=payload, headers=headers)

def get_tw_tickers():
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        res = requests.get(url, headers=HTTP_HEADERS, timeout=15)
        if res.status_code == 200:
            return [f"{item['Code'].strip()}.TW" for item in res.json() if len(item['Code'].strip()) == 4 and item['Code'].strip().isdigit()]
    except Exception as e:
        print(f"獲取台股清單失敗: {e}")
    return ["2330.TW", "2317.TW", "2454.TW"]

def get_us_tickers():
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        df = pd.read_html(requests.get(url, headers=HTTP_HEADERS).text)[0]
        return [t.replace('.', '-') for t in df['Symbol'].tolist()]
    except Exception as e:
        print(f"獲取美股清單失敗: {e}")
    return ["AAPL", "MSFT", "NVDA"]

def draw_chart(df_chart, ticker, title_suffix, ma_list):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df_chart.index.strftime('%Y-%m-%d'), 
        open=df_chart['Open'], high=df_chart['High'], 
        low=df_chart['Low'], close=df_chart['Close'], name='K線'
    ))
    
    colors = ['#FF9800', '#2196F3', '#4CAF50', '#E91E63', '#9C27B0', '#00BCD4']
    for idx, ma_window in enumerate(ma_list):
        ma_col = f'MA{ma_window}'
        if ma_col in df_chart.columns:
            color = colors[idx % len(colors)]
            fig.add_trace(go.Scatter(
                x=df_chart.index.strftime('%Y-%m-%d'), y=df_chart[ma_col], 
                line=dict(color=color, width=2), name=ma_col
            ))
            
    fig.update_layout(
        title=f"{ticker} {title_suffix}",
        xaxis_rangeslider_visible=False, template='plotly_dark',
        margin=dict(l=10, r=10, t=50, b=20), height=400
    )
    return fig.to_json()

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
                        chart_json = draw_chart(df_chart, ticker, title_str, [20])
                        matched_list.append({'ticker': ticker, 'volume': int(latest_vol), 'chart_json': chart_json})
                except Exception: continue
        except Exception as e: print(f"批次錯誤 {i}: {e}")
    matched_list.sort(key=lambda x: x['volume'], reverse=True)
    return matched_list

def process_custom_groups(group_dict):
    matched_list = []
    tickers = list(group_dict.keys())
    if not tickers: return matched_list
    
    try:
        data = yf.download(tickers, period="500d", progress=False)
    except Exception as e:
        print(f"自選股下載失敗: {e}")
        return matched_list
        
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
            for ma_window in ma_list:
                df_clean[f'MA{ma_window}'] = df_clean['Close'].rolling(window=ma_window).mean()
                
            price = float(df_clean['Close'].iloc[-1])
            df_chart = df_clean.tail(60)
            
            ma_info = " | ".join([f"MA{w}: {round(df_clean[f'MA{w}'].iloc[-1], 2)}" for w in ma_list if not pd.isna(df_clean[f'MA{w}'].iloc[-1])])
            title_str = f"(現價: {round(price,2)} | {ma_info})"
            
            chart_json = draw_chart(df_chart, ticker, title_str, ma_list)
            matched_list.append({'ticker': ticker, 'volume': 0, 'chart_json': chart_json})
        except Exception as e:
            print(f"處理自選股 {ticker} 失敗: {e}")
            continue
    return matched_list

def generate_html(data_dict, date_str):
    js_store = "const chartDataStore = {\n"
    for key in ['tw', 'us', 'g1', 'g2', 'g3', 'g4', 'g5']:
        js_store += f"  '{key}': [\n"
        for s in data_dict[key]:
            js_store += f"    {s['chart_json']},\n"
        js_store += "  ],\n"
    js_store += "};\n"

    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>台美股均線潛伏報告</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
        <style>
            body {{ background-color: #111; color: #fff; font-family: Arial, sans-serif; margin: 0; padding: 10px; }}
            .header {{ text-align: center; padding: 15px 0; background: #222; margin-bottom: 15px; border-radius: 8px; }}
            .tabs {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 5px; margin-bottom: 20px; }}
            .tab-btn {{ background: #333; color: #ccc; border: none; padding: 10px 15px; font-size: 14px; cursor: pointer; border-radius: 4px; transition: 0.3s; }}
            .tab-btn.active {{ background: #00b0ff; color: #fff; font-weight: bold; }}
            .market-section {{ display: none; max-width: 800px; margin: 0 auto; }}
            .market-section.active {{ display: block; }}
            .chart-card {{ background: #1e1e1e; margin-bottom: 25px; padding: 5px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
            .plotly-container {{ height: 400px; background: #151515; border-radius: 6px; display: flex; align-items: center; justify-content: center; }}
            .loading-placeholder {{ color: #555; font-size: 14px; letter-spacing: 1px; }}
            .no-data {{ text-align: center; color: #888; padding: 40px; font-size: 16px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>📈 台美股量化潛伏網頁報告 ({date_str})</h2>
            <p style="margin: 5px 0 0 0; color:#aaa; font-size:13px;">全市場完整呈現 | 終極時間軸修正版 (K線均線100%歸位)</p>
        </div>
        
        <div class="tabs">
            <button class="tab-btn active" onclick="switchMarket(event, 'tw')">🇹🇼 台股 ({len(data_dict['tw'])})</button>
            <button class="tab-btn" onclick="switchMarket(event, 'us')">🇺🇸 美股 ({len(data_dict['us'])})</button>
            <button class="tab-btn" onclick="switchMarket(event, 'g1')">🚀 超級績效股 ({len(data_dict['g1'])})</button>
            <button class="tab-btn" onclick="switchMarket(event, 'g2')">💎 績優股 ({len(data_dict['g2'])})</button>
            <button class="tab-btn" onclick="switchMarket(event, 'g3')">🎯 重點關注股 ({len(data_dict['g3'])})</button>
            <button class="tab-btn" onclick="switchMarket(event, 'g4')">👀 近期關注股 ({len(data_dict['g4'])})</button>
            <button class="tab-btn" onclick="switchMarket(event, 'g5')">🎲 投機股 ({len(data_dict['g5'])})</button>
        </div>
    """
    
    for key in ['tw', 'us', 'g1', 'g2', 'g3', 'g4', 'g5']:
        active_class = " active" if key == 'tw' else ""
        html_template += f'<div id="{key}-market" class="market-section{active_class}">'
        if data_dict[key]:
            for idx, s in enumerate(data_dict[key]):
                html_template += f"""
                <div class="chart-card" data-market="{key}" data-index="{idx}">
                    <div class="plotly-container">
                        <div class="loading-placeholder">滾動至此自動加載圖表...</div>
                    </div>
                </div>
                """
        else:
            html_template += f'<div class="no-data">此分類目前無股票資料</div>'
        html_template += '</div>'
    
    html_template += f"""
        <script>
            {js_store}

            function switchMarket(event, marketId) {{
                document.querySelectorAll('.market-section').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
                document.getElementById(marketId + '-market').classList.add('active');
                event.currentTarget.classList.add('active');
                window.dispatchEvent(new Event('resize'));
            }}

            document.addEventListener("DOMContentLoaded", function() {{
                const observer = new IntersectionObserver((entries) => {{
                    entries.forEach(entry => {{
                        const card = entry.target;
                        const market = card.dataset.market;
                        const index = parseInt(card.dataset.index);
                        const container = card.querySelector('.plotly-container');
                        
                        if (entry.isIntersecting) {{
                            if (!container.dataset.rendered) {{
                                const item = chartDataStore[market][index];
                                if (item && item.data && item.layout) {{
                                    container.innerHTML = ""; 
                                    
                                    // 🧠 核心修復 1：深拷貝確保數據純淨
                                    const chartData = JSON.parse(JSON.stringify(item.data));
                                    const chartLayout = JSON.parse(JSON.stringify(item.layout));
                                    
                                    // 🎯 核心修復 2：還原標準時間軸，並用 rangebreaks 完美挖除週末空隙
                                    chartLayout.xaxis = chartLayout.xaxis || {{}};
                                    chartLayout.xaxis.type = 'date';
                                    chartLayout.xaxis.rangebreaks = [
                                        {{ bounds: ["sat", "mon"] }} 
                                    ];
                                    chartLayout.xaxis.rangeslider = {{ visible: false }};
                                    
                                    Plotly.newPlot(container, chartData, chartLayout, {{responsive: true, displayModeBar: false}});
                                    container.dataset.rendered = "true";
                                }}
                            }}
                        }} else {{
                            if (container.dataset.rendered === "true") {{
                                Plotly.purge(container);
                                container.innerHTML = '<div class="loading-placeholder">滾動至此自動加載圖表...</div>';
                                container.removeAttribute('data-rendered');
                            }}
                        }}
                    }});
                }}, {{ 
                    rootMargin: '600px 0px 600px 0px' // 擴大加載緩衝，提前畫好圖表避免閃爍
                }});

                document.querySelectorAll('.chart-card').forEach(card => observer.observe(card));
            }});
        </script>
    </body>
    </html>
    """
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f: f.write(html_template)

def main():
    access_token = os.environ.get("LINE_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not access_token or not user_id: return
    today_str = datetime.now().strftime("%Y-%m-%d")

    # =========================================================================
    # 📝 【自選股與專屬均線設定區】
    # =========================================================================
    g1_config = {"2330.TW": [10, 20], "NVDA": [10, 20], "AMD": [20]}
    g2_config = {"2317.TW": [20, 60], "AAPL": [20, 120], "MSFT": [20, 60, 120]}
    g3_config = {"2454.TW": [5, 10, 20], "TSLA": [10, 20]}
    g4_config = {"2603.TW": [20, 60], "AMZN": [20]}
    g5_config = {"2609.TW": [5, 10], "0050.TW": [5, 20]}
    # =========================================================================

    print("正在執行全市場終極修正掃描...")
    
    raw_tw = scan_market(get_tw_tickers(), min_volume=0)
    raw_us = scan_market(get_us_tickers(), min_volume=0)
    
    data_dict = {
        'tw': raw_tw, 
        'us': raw_us,
        'g1': process_custom_groups(g1_config),
        'g2': process_custom_groups(g2_config),
        'g3': process_custom_groups(g3_config),
        'g4': process_custom_groups(g4_config),
        'g5': process_custom_groups(g5_config)
    }

    generate_html(data_dict, today_str)
    
    os.system('git config --global user.name "github-actions[bot]"')
    os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
    os.system('git add docs/index.html')
    os.system('git commit -m "🔥 終極修正：還原date軸，使用rangebreaks排除週末斷層"')
    os.system('git push')

    github_user = "wudn9922"
    github_repo = "my-stock-screener"
    web_url = f"https://{github_user}.github.io/{github_repo}/"
    
    line_msg = f"\n🎯 {today_str} 全市場看盤網頁（終極修正版）已更新！\n"
    line_msg += f"🇹🇼 台股符合：{len(data_dict['tw'])} 檔\n"
    line_msg += f"🇺🇸 美股符合：{len(data_dict['us'])} 檔\n"
    line_msg += f"🔗 點擊網址查看完美清晰圖表：\n{web_url}"
    
    send_line_message(line_msg, access_token, user_id)
    print("終極修正網頁更新成功！")

if __name__ == "__main__":
    main()

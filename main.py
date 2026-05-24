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
        margin=dict(l=10, r=10, t=40, b=10), height=400
    )
    return fig.to_html(full_html=False, include_plotlyjs='cdn')

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
                        chart_html = draw_chart(df_chart, ticker, title_str, [20])
                        matched_list.append({'ticker': ticker, 'volume': int(latest_vol), 'chart_html': chart_html})
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
            # 修正處：完美閉合 isinstance 的括號
            if isinstance(data.columns, pd.MultiIndex):
                if ticker in data.columns.get_level_values(1): df_t = data.xs(ticker, axis=1, level=1)
                elif ticker in data.columns.get_level_values(0): df_t = data.xs(ticker, axis=1, level=0)
                else: continue
            else: 
                df_t = data.copy()
            
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
            
            chart_html = draw_chart(df_chart, ticker, title_str, ma_list)
            matched_list.append({'ticker': ticker, 'volume': 0, 'chart_html': chart_html})
        except Exception as e:
            print(f"處理自選股 {ticker} 失敗: {e}")
            continue
    return matched_list

def generate_html(data_dict, date_str):
    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>台美股均線潛伏報告</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ background-color: #111; color: #fff; font-family: Arial, sans-serif; margin: 0; padding: 10px; }}
            .header {{ text-align: center; padding: 15px 0; background: #222; margin-bottom: 15px; border-radius: 8px; }}
            .tabs {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 5px; margin-bottom: 20px; }}
            .tab-btn {{ background: #333; color: #ccc; border: none; padding: 10px 15px; font-size: 14px; cursor: pointer; border-radius: 4px; transition: 0.3s; }}
            .tab-btn.active {{ background: #00b0ff; color: #fff; font-weight: bold; }}
            .market-section {{ display: none; max-width: 800px; margin: 0 auto; }}
            .market-section.active {{ display: block; }}
            .chart-card {{ background: #1e1e1e; margin-bottom: 25px; padding: 10px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
            .no-data {{ text-align: center; color: #888; padding: 40px; font-size: 16px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>📈 台美股量化潛伏網頁報告 ({date_str})</h2>
            <p style="margin: 5px 0 0 0; color:#aaa; font-size:13px;">全市場條件：最新收盤價在 20MA 之下 (0% ~ -3%) | 自選股採獨立客製化均線</p>
        </div>
        
        <div class="tabs">
            <button class="tab-btn active" onclick="switchMarket('tw')">🇹🇼 台股 ({len(data_dict['tw'])})</button>
            <button class="tab-btn" onclick="switchMarket('us')">🇺🇸 美股 ({len(data_dict['us'])})</button>
            <button class="tab-btn" onclick="switchMarket('g1')">🚀 超級績效股 ({len(data_dict['g1'])})</button>
            <button class="tab-btn" onclick="switchMarket('g2')">💎 績優股 ({len(data_dict['g2'])})</button>
            <button class="tab-btn" onclick="switchMarket('g3')">🎯 重點關注股 ({len(data_dict['g3'])})</button>
            <button class="tab-btn" onclick="switchMarket('g4')">👀 近期關注股 ({len(data_dict['g4'])})</button>
            <button class="tab-btn" onclick="switchMarket('g5')">🎲 投機股 ({len(data_dict['g5'])})</button>
        </div>
    """
    
    for key in ['tw', 'us', 'g1', 'g2', 'g3', 'g4', 'g5']:
        active_class = " active" if key == 'tw' else ""
        html_template += f'<div id="{key}-market" class="market-section{active_class}">'
        if data_dict[key]:
            for s in data_dict[key]: html_template += f'<div class="chart-card">{s["chart_html"]}</div>'
        else:
            html_template += f'<div class="no-data">此分類目前無股票資料</div>'
        html_template += '</div>'
    
    html_template += """
        <script>
            function switchMarket(marketId) {
                document.querySelectorAll('.market-section').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
                document.getElementById(marketId + '-market').classList.add('active');
                event.target.classList.add('active');
                window.dispatchEvent(new Event('resize'));
            }
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
    # 📝 【自選股設定區：請在這裡修改你的自選股票與各自的均線天數】
    # =========================================================================
    # 🚀 1. 超級績效股
    g1_config = {
        "2330.TW": [10, 20], 
        "NVDA": [10, 20],
        "AMD": [20]
    }
    # 💎 2. 績優股
    g2_config = {
        "2317.TW": [20, 60],
        "AAPL": [20, 120],
        "MSFT": [20, 60, 120]
    }
    # 🎯 3. 重點關注股
    g3_config = {
        "2454.TW": [5, 10, 20],
        "TSLA": [10, 20]
    }
    # 👀 4. 近期關注股
    g4_config = {
        "2603.TW": [20, 60],
        "AMZN": [20]
    }
    # 🎲 5. 投機股
    g5_config = {
        "2609.TW": [5, 10],
        "0050.TW": [5, 20]
    }
    # =========================================================================

    print("正在全面掃描全市場與全新客製化自選股(修復版)...")
    
    data_dict = {
        'tw': scan_market(get_tw_tickers(), min_volume=0),
        'us': scan_market(get_us_tickers(), min_volume=0),
        
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
    os.system('git commit -m "🐛 修正：閉合第 115 行的判斷式括號"')
    os.system('git push')

    github_user = "wudn9922"
    github_repo = "my-stock-screener"
    web_url = f"https://{github_user}.github.io/{github_repo}/"
    
    line_msg = f"\n🎯 {today_str} 看盤網頁（Bug修復版）已更新！\n"
    line_msg += f"🇹🇼 台股全市場：{len(data_dict['tw'])} 檔\n"
    line_msg += f"🇺🇸 美股全市場：{len(data_dict['us'])} 檔\n"
    line_msg += f"🔥 自選股狀態：\n"
    line_msg += f" ├ 🚀 超級績效股：{len(data_dict['g1'])} 檔\n"
    line_msg += f" ├ 💎 績優股：{len(data_dict['g2'])} 檔\n"
    line_msg += f" ├ 🎯 重點關注股：{len(data_dict['g3'])} 檔\n"
    line_msg += f" ├ 👀 近期關注股：{len(data_dict['g4'])} 檔\n"
    line_msg += f" └ 🎲 投機股：{len(data_dict['g5'])} 檔\n"
    line_msg += f"🔗 點擊網址直接「高清」瀏覽：\n{web_url}"
    
    send_line_message(line_msg, access_token, user_id)
    print("網頁更新成功，修復版已發送！")

if __name__ == "__main__":
    main()

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
    return ["2330.TW", "2317.TW", "2454.TW", "2303.TW", "2881.TW"]

def get_us_tickers():
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        df = pd.read_html(requests.get(url, headers=HTTP_HEADERS).text)[0]
        return [t.replace('.', '-') for t in df['Symbol'].tolist()]
    except Exception as e:
        print(f"獲取美股清單失敗: {e}")
    return ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]

def scan_market(tickers, min_volume):
    matched_list = []
    chunk_size = 40  # 稍微縮減分批大小，提高下載穩定度
    
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i+chunk_size]
        try:
            # 確保獲取足夠計算 20MA 的天數
            data = yf.download(chunk, period="150d", progress=False)
            if data.empty: continue
            
            for ticker in chunk:
                try:
                    # 【全新相容邏輯】精準抓取單一股票的 OHLCV 資料，通殺新舊版 yfinance 結構
                    if isinstance(data.columns, pd.MultiIndex):
                        # 檢查 ticker 是否在 columns 的第二層或第一層
                        if ticker in data.columns.get_level_values(1):
                            df_t = data.xs(ticker, axis=1, level=1)
                        elif ticker in data.columns.get_level_values(0):
                            df_t = data.xs(ticker, axis=1, level=0)
                        else:
                            continue
                    else:
                        # 如果是單層 Index (當分批剛好只有一檔，或平鋪結構)
                        df_t = data.copy()
                    
                    # 確保必要欄位存在
                    required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
                    if not all(col in df_t.columns for col in required_cols):
                        continue
                        
                    df_clean = df_t[required_cols].dropna()
                    if len(df_clean) < 20: continue
                    
                    latest_vol = float(df_clean['Volume'].iloc[-1])
                    if latest_vol < min_volume: continue
                    
                    df_clean['MA20'] = df_clean['Close'].rolling(window=20).mean()
                    price = float(df_clean['Close'].iloc[-1])
                    ma20 = float(df_clean['MA20'].iloc[-1])
                    
                    if pd.isna(ma20): continue
                    
                    # 判斷條件：收盤價在 20MA 之下 0% 到 -3%
                    if ma20 * 0.97 <= price < ma20:
                        diff_pct = ((price / ma20) - 1) * 100
                        df_chart = df_clean.tail(120)  # 取最近 6 個月畫圖
                        
                        # 繪製互動式 K 線圖
                        fig = go.Figure()
                        fig.add_trace(go.Candlestick(
                            x=df_chart.index.strftime('%Y-%m-%d'), 
                            open=df_chart['Open'], high=df_chart['High'], 
                            low=df_chart['Low'], close=df_chart['Close'], name='K線'
                        ))
                        fig.add_trace(go.Scatter(
                            x=df_chart.index.strftime('%Y-%m-%d'), y=df_chart['MA20'], 
                            line=dict(color='orange', width=2), name='20MA'
                        ))
                        fig.update_layout(
                            title=f"{ticker} (現價: {round(price,2)} | 距MA20: {round(diff_pct,2)}%)",
                            xaxis_rangeslider_visible=False, template='plotly_dark',
                            margin=dict(l=10, r=10, t=40, b=10), height=400
                        )
                        chart_html = fig.to_html(full_html=False, include_plotlyjs='cdn')
                        
                        matched_list.append({
                            'ticker': ticker, 'volume': int(latest_vol), 'chart_html': chart_html
                        })
                except Exception as e: 
                    continue
        except Exception as e: 
            print(f"批次錯誤 {i}: {e}")
            
    matched_list.sort(key=lambda x: x['volume'], reverse=True)
    return matched_list

def generate_html(tw_stocks, us_stocks, date_str):
    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>台美股均線潛伏報告</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ background-color: #111; color: #fff; font-family: Arial, sans-serif; margin: 0; padding: 10px; }}
            .header {{ text-align: center; padding: 20px 0; background: #222; margin-bottom: 20px; border-radius: 8px; }}
            .tabs {{ display: flex; justify-content: center; margin-bottom: 20px; }}
            .tab-btn {{ background: #333; color: #ccc; border: none; padding: 12px 30px; font-size: 16px; cursor: pointer; transition: 0.3s; }}
            .tab-btn.active {{ background: #00b0ff; color: #fff; font-weight: bold; }}
            .tab-btn:first-child {{ border-radius: 5px 0 0 5px; }}
            .tab-btn:last-child {{ border-radius: 0 5px 5px 0; }}
            .market-section {{ display: none; max-width: 800px; margin: 0 auto; }}
            .market-section.active {{ display: block; }}
            .chart-card {{ background: #1e1e1e; margin-bottom: 25px; padding: 10px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
            .no-data {{ text-align: center; color: #888; padding: 40px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>📈 台美股量化潛伏網頁報告 ({date_str})</h2>
            <p>條件：最新收盤價在 20MA 之下 (0% ~ -3%) | 依成交量排序</p>
        </div>
        <div class="tabs">
            <button class="tab-btn active" onclick="switchMarket('tw')">🇹🇼 台股 ({len(tw_stocks)})</button>
            <button class="tab-btn" onclick="switchMarket('us')">🇺🇸 美股 ({len(us_stocks)})</button>
        </div>
        <div id="tw-market" class="market-section active">
    """
    if tw_stocks:
        for s in tw_stocks: html_template += f'<div class="chart-card">{s["chart_html"]}</div>'
    else: html_template += '<div class="no-data">今日台股無符合標的</div>'
    
    html_template += '</div><div id="us-market" class="market-section">'
    if us_stocks:
        for s in us_stocks: html_template += f'<div class="chart-card">{s["chart_html"]}</div>'
    else: html_template += '<div class="no-data">今日美股無符合標的</div>'
    
    html_template += """
        </div>
        <script>
            function switchMarket(market) {
                document.querySelectorAll('.market-section').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
                if(market === 'tw') {
                    document.getElementById('tw-market').classList.add('active');
                    event.target.classList.add('active');
                } else {
                    document.getElementById('us-market').classList.add('active');
                    event.target.classList.add('active');
                }
                window.dispatchEvent(new Event('resize'));
            }
        </script>
    </body>
    </html>
    """
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html_template)

def main():
    access_token = os.environ.get("LINE_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not access_token or not user_id: return
    today_str = datetime.now().strftime("%Y-%m-%d")

    print("正在全面掃描全市場...")
    # 🧪 這裡目前依然維持限制為 0（壓力測試用），讓你能立刻看到有股票跑出來
    tw_matches = scan_market(get_tw_tickers(), min_volume=0)
    us_matches = scan_market(get_us_tickers(), min_volume=0)

    generate_html(tw_matches, us_matches, today_str)
    
    os.system('git config --global user.name "github-actions[bot]"')
    os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
    os.system('git add docs/index.html')
    os.system('git commit -m "📊 自動更新 K 線網頁報告"')
    os.system('git push')

    # 💡 請記得將下面兩行換成你的 GitHub 帳號與專案庫名稱！
    github_user = "wudn9922"
    github_repo = "my-stock-screener"
    web_url = f"https://{github_user}.github.io/{github_repo}/"
    
    line_msg = f"\n🎯 {today_str} 均線潛伏圖表網頁已生成！\n"
    line_msg += f"🇹🇼 台股符合：{len(tw_matches)} 檔\n"
    line_msg += f"🇺🇸 美股符合：{len(us_matches)} 檔\n"
    line_msg += f"🔗 點擊網址直接「滑圖」瀏覽：\n{web_url}"
    
    send_line_message(line_msg, access_token, user_id)
    print("網頁更新成功，LINE 通知已發送！")

if __name__ == "__main__":
    main()

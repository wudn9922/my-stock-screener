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
                        df_chart = df_clean.tail(60)  # 📊 關鍵修正：這裡已從 120 天改為 60 天（約3個月）
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
            if isinstance(data.columns

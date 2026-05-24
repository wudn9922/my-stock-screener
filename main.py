import yfinance as yf
import pandas as pd
import requests
import os
from datetime import datetime

def send_line_message(msg, access_token, user_id):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": msg}]
    }
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code != 200:
        print(f"LINE 發送失敗，錯誤碼: {response.status_code}, 回應: {response.text}")

def main():
    # ------------------ 自選股清單（可自由修改） ------------------
    stock_config = {
        "2330.TW": [20, 0.03],   # 台積電：20MA，低於 3%
        "2317.TW": [60, 0.05],   # 鴻海：60MA，低於 5%
        "AAPL": [20, 0.02],      # 蘋果：20MA，低於 2%
        "NVDA": [30, 0.05]       # 輝達：30MA，低於 5%
    }
    # -----------------------------------------------------------
    
    access_token = os.environ.get("LINE_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    
    if not access_token or not user_id:
        print("錯誤：找不到 LINE_ACCESS_TOKEN 或 LINE_USER_ID")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    report = f"\n📊 {today_str} 股票均線篩選報告\n" + "-"*20 + "\n"
    hit_count = 0
    
    for symbol, params in stock_config.items():
        ma_len = params[0]
        threshold_pct = params[1]
        
        try:
            df = yf.download(symbol, period=f"{ma_len + 20}d", progress=False)
            if df.empty: continue
            
            ma_col = f'MA{ma_len}'
            df[ma_col] = df['Close'].rolling(window=ma_len).mean()
            
            latest_price = float(df['Close'].iloc[-1])
            latest_ma = float(df[ma_col].iloc[-1])
            
            if latest_price < latest_ma * (1 - threshold_pct):
                diff_pct = round((latest_price / latest_ma - 1) * 100, 2)
                report += f"⚠️ {symbol}: 跌破 {ma_len}MA ({diff_pct}%)\n   現價: {round(latest_price, 2)}\n"
                hit_count += 1
        except Exception as e:
            print(f"處理 {symbol} 出錯: {e}")
            
    if hit_count == 0:
        report += "今日自選股皆未達標。\n"
        
    send_line_message(report, access_token, user_id)

if __name__ == "__main__":
    main()

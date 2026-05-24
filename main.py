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
        print(f"LINE 發送失敗: {response.status_code}, {response.text}")

def get_tw_tickers():
    """自動爬取台股所有上市股票代號"""
    try:
        url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
        res = requests.get(url)
        res.encoding = 'big5'
        df = pd.read_html(res.text)[0]
        tickers = []
        for item in df[0].dropna().tolist():
            if ' ' in item:
                code = item.split(' ')[0].strip()
                if len(code) == 4 and code.isdigit():
                    tickers.append(f"{code}.TW")
        return tickers
    except Exception as e:
        print(f"獲取台股清單失敗: {e}")
        return ["2330.TW", "2317.TW", "2454.TW", "2308.TW"]

def get_us_tickers():
    """自動爬取美股標普 500 清單"""
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        df = pd.read_html(url)[0]
        tickers = df['Symbol'].tolist()
        return [t.replace('.', '-') for t in tickers]
    except Exception as e:
        print(f"獲取美股清單失敗: {e}")
        return ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]

def scan_market(tickers, min_volume):
    matched_list = []
    chunk_size = 100
    
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i+chunk_size]
        try:
            data = yf.download(chunk, period="40d", progress=False, group_by='ticker')
            
            for ticker in chunk:
                if isinstance(data.columns, pd.MultiIndex):
                    if ticker not in data.columns.levels[0]: continue
                    df = data[ticker].dropna()
                else:
                    df = data.dropna() if len(chunk) == 1 else pd.DataFrame()
                
                if len(df) < 20: continue
                
                # 1. 檢查最新成交量
                latest_vol = float(df['Volume'].iloc[-1])
                if latest_vol < min_volume: continue
                
                # 2. 計算 20MA
                df['MA20'] = df['Close'].rolling(window=20).mean()
                price = float(df['Close'].iloc[-1])
                ma20 = float(df['MA20'].iloc[-1])
                
                # 3. 篩選條件：20MA 之下 0% 到 3%
                if ma20 * 0.97 <= price < ma20:
                    diff_pct = ((price / ma20) - 1) * 100
                    matched_list.append({
                        'ticker': ticker,
                        'price': round(price, 2),
                        'diff': round(diff_pct, 2),
                        'volume': int(latest_vol)
                    })
        except Exception as e:
            print(f"批次下載出錯 ({i}-{i+chunk_size}): {e}")
            
    matched_list.sort(key=lambda x: x['volume'], reverse=True)
    return matched_list

def main():
    access_token = os.environ.get("LINE_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not access_token or not user_id: return

    today_str = datetime.now().strftime("%Y-%m-%d")

    # ==================== 【第一部分：核心自選股監控 (MA下方 0~3% 修正版)】 ====================
    # 設定格式：[自選均線天數 , 下方容許趴數]
    # 例如：台積電看 20MA 下方 3% 內；鴻海看 60MA 下方 3% 內
    stock_config = {
        "2330.TW": [20, 0.03],   # 台積電：20MA，下方 0~3% 區間
        "2317.TW": [60, 0.03],   # 鴻海：60MA，下方 0~3% 區間
        "AAPL": [20, 0.03],      # 蘋果：20MA，下方 0~3% 區間
        "NVDA": [30, 0.03]       # 輝達：30MA，下方 0~3% 區間
    }
    
    my_report = f"\n📊 {today_str} 核心自選股潛伏報告\n"
    my_report += "🎯 條件: 股價在自選MA之下 (0 ~ -3%)\n" + "-"*20 + "\n"
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
            
            # 修正關鍵邏輯：股價必須在 MA 的 97% 到 100% 之間 (即 MA 下方 0~3%)
            if latest_ma * (1 - threshold_pct) <= latest_price < latest_ma:
                diff_pct = round((latest_price / latest_ma - 1) * 100, 2)
                my_report += f"🎯 {symbol}: 在 {ma_len}MA 下方 ({diff_pct}%)\n   現價: {round(latest_price, 2)} | MA: {round(latest_ma, 2)}\n"
                hit_count += 1
        except Exception as e:
            print(f"處理自選股 {symbol} 出錯: {e}")
            
    if hit_count == 0:
        my_report += "今日核心自選股皆未進入均線下方 0~3% 區間。\n"
        
    # 發送第一則訊息
    send_line_message(my_report, access_token, user_id)


    # ==================== 【第二部分：全市場量化篩選】 ====================
    market_report = f"\n🚀 {today_str} 全市場量化篩選報告\n"
    market_report += "🔥 條件: 20MA之下(0~-3%) + 高量能\n" + "="*20 + "\n"

    # --- 執行台股全市場掃描 (成交量 > 2000張) ---
    print("正在掃描台股全市場...")
    tw_tickers = get_tw_tickers()
    tw_matches = scan_market(tw_tickers, min_volume=2000000)
    
    market_report += f"🇹🇼 台股符合條件 ({len(tw_matches)} 檔):\n"
    for item in tw_matches[:20]:
        vol_lots = int(item['volume'] / 1000)
        market_report += f"📈 {item['ticker'].replace('.TW','')}: 現價{item['price']} (距MA20: {item['diff']}% | 量: {vol_lots}張)\n"
    if not tw_matches: market_report += "今日台股無符合標的。\n"
    
    market_report += "-"*20 + "\n"

    # --- 執行美股標普500掃描 (成交量 > 100萬股) ---
    print("正在掃描美股標普500...")
    us_tickers = get_us_tickers()
    us_matches = scan_market(us_tickers, min_volume=1000000)
    
    market_report += f"🇺🇸 美股符合條件 ({len(us_matches)} 檔):\n"
    for item in us_matches[:20]:
        vol_million = round(item['volume'] / 1000000, 1)
        market_report += f"🍏 {item['ticker']}: 現價{item['price']} (距MA20: {item['diff']}% | 量: {vol_million}百萬股)\n"
    if not us_matches: market_report += "今日美股無符合標的。\n"

    # 發送第二則訊息
    send_line_message(market_report, access_token, user_id)
    print("兩份報告皆已成功發送！")

if __name__ == "__main__":
    main()

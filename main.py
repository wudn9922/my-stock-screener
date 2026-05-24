import yfinance as yf
import pandas as pd
import requests
import os
from datetime import datetime

# 建立一個通用的偽裝瀏覽器標頭，防止網站阻擋機器人
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

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
        res = requests.get(url, headers=HTTP_HEADERS)
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
        res = requests.get(url, headers=HTTP_HEADERS)
        df = pd.read_html(res.text)[0]
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
            data = yf.download(chunk, period="40d", progress=False, keep_multiindex=True)
            if data.empty: continue
            
            for ticker in chunk:
                try:
                    if ticker not in data['Close'].columns: continue
                    
                    close_series = data['Close'][ticker]
                    volume_series = data['Volume'][ticker]
                    df_ticker = pd.DataFrame({'Close': close_series, 'Volume': volume_series}).dropna()
                    
                    if len(df_ticker) < 20: continue
                    
                    latest_vol = float(df_ticker['Volume'].iloc[-1])
                    if latest_vol < min_volume: continue
                    
                    df_ticker['MA20'] = df_ticker['Close'].rolling(window=20).mean()
                    price = float(df_ticker['Close'].iloc[-1])
                    ma20 = float(df_ticker['MA20'].iloc[-1])
                    
                    if pd.isna(ma20): continue
                    
                    if ma20 * 0.97 <= price < ma20:
                        diff_pct = ((price / ma20) - 1) * 100
                        matched_list.append({
                            'ticker': ticker,
                            'price': round(price, 2),
                            'diff': round(diff_pct, 2),
                            'volume': int(latest_vol)
                        })
                except Exception:
                    continue
        except Exception as e:
            print(f"批次下載出錯 ({i}-{i+chunk_size}): {e}")
            
    matched_list.sort(key=lambda x: x['volume'], reverse=True)
    return matched_list

def main():
    access_token = os.environ.get("LINE_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not access_token or not user_id: return

    today_str = datetime.now().strftime("%Y-%m-%d")

    # ==================== 【第一部分：10 大自選股分組監控】 ====================
    # 格式說明： "分組名稱": { "股票代號": [設定均線, 下方容許趴數] }
    # 提示：如果某個分組暫時沒有股票，保持 {} 即可
    stock_groups = {
        "1️⃣ 超級績效股": {
            "2330.TW": [20, 0.03],  # 台積電：20MA，下方 0~3% 
            "NVDA": [20, 0.03]      # 輝達：20MA，下方 0~3%
        },
        "2️⃣ 核心績優股": {
            "2317.TW": [60, 0.03],  # 鴻海：60MA，下方 0~3%
            "AAPL": [20, 0.03]      # 蘋果：20MA，下方 0~3%
        },
        "3️⃣ 轉機投機股": {
            "2303.TW": [20, 0.03]   # 聯電：20MA，下方 0~3%
        },
        "4️⃣ 近期關注股": {
            "2454.TW": [20, 0.03]   # 聯發科：20MA，下方 0~3%
        },
        "5️⃣ 殖利率概念": {},
        "6️⃣ 產業龍頭股": {},
        "7️⃣ 跌深反彈股": {},
        "8️⃣ 投信作帳股": {},
        "9️⃣ 外資鎖定股": {},
        "🔟 強勢整理股": {}
    }
    
    my_report = f"\n📊 {today_str} 分組自選股潛伏報告\n"
    my_report += "🎯 條件: 股價在自選MA之下 (0 ~ -3%)\n" + "="*20 + "\n"
    total_hit_count = 0
    
    # 開始逐組掃描
    for group_name, stocks in stock_groups.items():
        if not stocks: continue # 如果這組是空的，直接跳過不處理
        
        group_content = f"【{group_name}】\n"
        group_hit_count = 0
        
        for symbol, params in stocks.items():
            ma_len = params[0]
            threshold_pct = params[1]
            try:
                df = yf.download(symbol, period=f"{ma_len + 20}d", progress=False)
                if df.empty: continue
                
                df_clean = df[['Close', 'Volume']].dropna()
                if len(df_clean) < ma_len: continue
                
                ma_col = f'MA{ma_len}'
                df_clean[ma_col] = df_clean['Close'].rolling(window=ma_len).mean()
                latest_price = float(df_clean['Close'].iloc[-1])
                latest_ma = float(df_clean[ma_col].iloc[-1])
                
                if latest_ma * (1 - threshold_pct) <= latest_price < latest_ma:
                    diff_pct = round((latest_price / latest_ma - 1) * 100, 2)
                    group_content += f" 📈 {symbol}: 在 {ma_len}MA 下方 ({diff_pct}%)\n    現價: {round(latest_price, 2)} | MA: {round(latest_ma, 2)}\n"
                    group_hit_count += 1
                    total_hit_count += 1
            except Exception as e:
                print(f"處理自選股 {symbol} 出錯: {e}")
                
        # 只有當這一組真的有股票觸發訊號時，才把這一組塞進報告裡
        if group_hit_count > 0:
            my_report += group_content + "-"*15 + "\n"
            
    if total_hit_count == 0:
        my_report += "今日各分組自選股皆未進入均線下方 0~3% 區間。\n"
        
    # 發送第一則訊息：分組自選股報告
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

    # 發送第二則訊息：全市場報告
    send_line_message(market_report, access_token, user_id)
    print("兩份報告皆已成功發送！")

if __name__ == "__main__":
    main()

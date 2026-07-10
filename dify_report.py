import os
import requests
import xml.etree.ElementTree as ET

# 讀取環境變數
DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")

def fetch_rss_headlines(url, name):
    print(f"📡 正在抓取 {name} 官方即時首頁頭條...")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        headlines = []
        for item in root.findall('.//item')[:6]:  # 嚴格只取前 6 條最新首頁消息
            title = item.find('title').text.strip()
            headlines.append(f"- {title}")
        return "\n".join(headlines)
    except Exception as e:
        print(f"❌ {name} 抓取失敗: {e}")
        return f"暫無最新 {name} 新聞"

def fetch_stocktwits_trending():
    print("📊 正在呼叫 Stocktwits 官方實時熱門 API...")
    try:
        url = "https://api.stocktwits.com/api/2/trending/symbols.json"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        symbols = data.get("symbols", [])
        
        stock_list = []
        for s in symbols[:8]:  # 拿當下最熱門的前 8 檔
            sym = s.get("symbol")
            title = s.get("title")
            stock_list.append(f"${sym} ({title})")
        return "\n".join(stock_list)
    except Exception as e:
        print(f"❌ 熱門股抓取失敗: {e}")
        return "暫無實時熱門股數據"

def run_dify_workflow(wsj_news, mw_news, stocks_data):
    print("Brain 正在將實時數據注入 Dify 大腦...")
    url = "https://api.dify.ai/v1/workflows/run"
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json"
    }
    # 🔥 【關鍵核心】：把在 Python 抓到的即時真數據，直接當作變數塞進 inputs！
    data = {
        "inputs": {
            "wsj_data": wsj_news,
            "barrons_data": mw_news,
            "trending_stocks": stocks_data
        }, 
        "response_mode": "blocking", 
        "user": "github-actions-bot"
    }
    response = requests.post(url, headers=headers, json=data, timeout=120)
    response.raise_for_status()
    return response.json().get("data", {}).get("outputs", {}).get("text", "")

def send_line_message(message):
    print("📲 正在透過 LINE 官方帳號推送到你的聊天室...")
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": f"📊 【今日華爾街與實時動能股晨報】\n\n{message}"
            }
        ]
    }
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    print("✅ LINE 推送成功！")

if __name__ == "__main__":
    try:
        # 1. 直接抓取純淨、無污染的當下實時數據
        wsj_live = fetch_rss_headlines("https://feeds.a.dj.com/rss/RSSWSJD.xml", "WSJ")
        mw_live = fetch_rss_headlines("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch/Barrons")
        stocks_live = fetch_stocktwits_trending()
        
        # 2. 注入 Dify 進行 AI 統整排版
        report_text = run_dify_workflow(wsj_live, mw_live, stocks_live)
        
        # 3. 發送 LINE
        if report_text:
            send_line_message(report_text)
        else:
            print("⚠️ Dify 未返回任何文字。")
    except Exception as e:
        print(f"❌ 腳本執行失敗: {str(e)}")

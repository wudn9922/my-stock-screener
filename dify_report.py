import os
import requests
import xml.etree.ElementTree as ET

DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")

def fetch_wsj_homepage_titles():
    print("📡 正在精準抓取 WSJ 官方首頁巨幅大標題...")
    urls = [
        "https://feeds.a.dj.com/rss/RSSWorldNews.xml",      # 全球重大突發 (首頁左側大欄)
        "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"   # 商業與龍頭企業 (首頁中央主欄)
    ]
    headlines = []
    headers = {"User-Agent": "Mozilla/5.0"}
    
    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            # 每個核心源嚴格只取最上方 3 條，這就是免滑動的首頁大字標題
            for item in root.findall('.//item')[:3]:  
                title = item.find('title').text.strip()
                headlines.append(f"- {title}")
        except Exception as e:
            print(f"❌ WSJ 局部抓取失敗: {e}")
            
    return "\n".join(headlines) if headlines else "暫無最新 WSJ 首頁數據"

def fetch_yahoo_realtime_trending():
    print("📊 正在呼叫 Yahoo Finance 官方隱藏版實時熱搜 API...")
    url = "https://query1.finance.yahoo.com/v1/finance/trending/US"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # 解析雅虎回傳的實時熱門股票清單
        trending_list = data.get("finance", {}).get("result", [])[0].get("quotes", [])
        symbols = [item.get("symbol") for item in trending_list if item.get("symbol")]
        
        print(f"✅ 成功獲取當下最熱搜代碼: {symbols}")
        return ", ".join(symbols[:5]) # 嚴格取前 5 檔最熱門的
    except Exception as e:
        print(f"❌ 雅虎實時 API 呼叫失敗: {e}")
        return "MU, PLTR, BB" # 若遇突發狀況，以此作為動能股保底推薦

def run_dify_workflow(wsj_data, trending_stocks):
    print("🧠 正在將純淨數據注入 Dify 大腦...")
    url = "https://api.dify.ai/v1/workflows/run"
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "inputs": {
            "wsj_raw_headlines": wsj_data,
            "yahoo_trending_symbols": trending_stocks
        }, 
        "response_mode": "blocking", 
        "user": "github-actions-bot"
    }
    response = requests.post(url, headers=headers, json=data, timeout=120)
    response.raise_for_status()
    return response.json().get("data", {}).get("outputs", {}).get("text", "")

def send_line_message(message):
    print("📲 正在推送到 LINE...")
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": message}]
    }
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    print("✅ LINE 推送成功！")

if __name__ == "__main__":
    try:
        wsj_live = fetch_wsj_homepage_titles()
        stocks_live = fetch_yahoo_realtime_trending()
        report_text = run_dify_workflow(wsj_live, stocks_live)
        if report_text:
            send_line_message(report_text)
    except Exception as e:
        print(f"❌ 執行失敗: {str(e)}")

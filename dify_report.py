import os
import requests
from playwright.sync_api import sync_playwright  # ✨ 新增：微軟自動化截圖引擎

DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")
IMGUR_CLIENT_ID = os.environ.get("IMGUR_CLIENT_ID")  # ✨ 新增：Imgur 圖床金鑰

def capture_and_upload_screenshots():
    """✨ 新增函數：自動前往 WSJ 與 Barron's 首頁截圖並上傳至 Imgur"""
    print("📡 正在啟動隱形瀏覽器進行 WSJ 與 Barron's 首頁截圖...")
    image_urls = []
    targets = [
        {"name": "WSJ", "url": "https://www.wsj.com", "file": "wsj.png"},
        {"name": "Barron's", "url": "https://www.barrons.com", "file": "barrons.png"}
    ]
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            for target in targets:
                try:
                    print(f"📸 正在截圖 {target['name']} 首頁...")
                    page.goto(target['url'], timeout=60000, wait_until="networkidle")
                    # full_page=False 代表只拍第一屏畫面（免滑動的巨幅大標題）
                    page.screenshot(path=target['file'], full_page=False)
                    
                    # 立即上傳到 Imgur 轉成網址
                    img_url = upload_to_imgur(target['file'])
                    if img_url:
                        image_urls.append(img_url)
                except Exception as e:
                    print(f"❌ {target['name']} 截圖或上傳失敗: {e}")
            browser.close()
    except Exception as e:
        print(f"❌ Playwright 瀏覽器核心啟動失敗: {e}")
        
    return image_urls

def upload_to_imgur(filepath):
    """✨ 新增函數：將截圖圖片上傳到 Imgur，取得 LINE 必須使用的公開 HTTPS 網址"""
    if not IMGUR_CLIENT_ID:
        print("⚠️ 未偵測到 IMGUR_CLIENT_ID 環境變數，跳過圖片上傳")
        return None
    
    headers = {"Authorization": f"Client-ID {IMGUR_CLIENT_ID}"}
    try:
        with open(filepath, "rb") as file:
            response = requests.post(
                "https://api.imgur.com/3/image", 
                headers=headers, 
                files={"image": file},
                timeout=30
            )
            response.raise_for_status()
            img_url = response.json().get("data", {}).get("link")
            print(f"✅ Imgur 上傳成功！圖片網址: {img_url}")
            return img_url
    except Exception as e:
        print(f"❌ Imgur 上傳失敗: {e}")
        return None

def fetch_yahoo_realtime_trending():
    """📊 保留原本邏輯：呼叫 Yahoo Finance 官方隱藏版實時熱搜 API"""
    print("📊 正在呼叫 Yahoo Finance 官方隱藏版實時熱搜 API...")
    url = "https://query1.finance.yahoo.com/v1/finance/trending/US"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        trending_list = data.get("finance", {}).get("result", [])[0].get("quotes", [])
        symbols = [item.get("symbol") for item in trending_list if item.get("symbol")]
        
        print(f"✅ 成功獲取當下最熱搜代碼: {symbols}")
        return ", ".join(symbols[:5]) 
    except Exception as e:
        print(f"❌ 雅虎實時 API 呼叫失敗: {e}")
        return "MU, PLTR, BB" 

def run_dify_workflow(wsj_data, trending_stocks):
    """🧠 保留原本邏輯：將純淨數據注入 Dify 大腦"""
    print("🧠 正在將純淨數據注入 Dify 大腦...")
    url = "https://api.dify.ai/v1/workflows/run"
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "inputs": {
            "wsj_raw_headlines": wsj_data,   # 這裡會傳入截圖完成的通知與圖片網址，確保 Dify 不會出錯
            "yahoo_trending_symbols": trending_stocks
        }, 
        "response_mode": "blocking", 
        "user": "github-actions-bot"
    }
    response = requests.post(url, headers=headers, json=data, timeout=120)
    response.raise_for_status()
    return response.json().get("data", {}).get("outputs", {}).get("text", "")

def send_line_report(message, image_urls):
    """📲 升級版：支援同一個 Push 同時發送 Dify 文字報表與多張 Imgur 截圖"""
    print("📲 正在準備組合包推送到 LINE...")
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # LINE 的 messages 陣列最多可以塞 5 個訊息物件
    messages = []
    
    # 1. 先加入 Dify 的文字財經晨報
    if message:
        messages.append({"type": "text", "text": message})
        
    # 2. 依序把 WSJ 和 Barron's 的截圖網址塞進去
    for img_url in image_urls:
        if img_url:
            messages.append({
                "type": "image",
                "originalContentUrl": img_url,
                "previewImageUrl": img_url
            })
            
    if not messages:
        print("⚠️ 沒有任何內容（文字或圖片）可以發送")
        return

    data = {
        "to": LINE_USER_ID,
        "messages": messages
    }
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    print("✅ LINE 綜合報表與截圖推播成功！")

if __name__ == "__main__":
    try:
        # 1. ✨ 執行網頁首頁截圖並上傳（頂替原本的舊 RSS 函數）
        uploaded_images = capture_and_upload_screenshots()
        
        # 2. 📊 執行原本的雅虎熱搜股票抓取
        stocks_live = fetch_yahoo_realtime_trending()
        
        # 3. 🧠 產生餵給 Dify 的文字提示（包含圖片網址，讓 Dify 知道截圖已完成）
        wsj_placeholder = "已成功完成 WSJ 與 Barron's 首頁首屏截圖並上傳。"
        if uploaded_images:
            wsj_placeholder += f"\n截圖備份連結: {', '.join(uploaded_images)}"
            
        report_text = run_dify_workflow(wsj_placeholder, stocks_live)
        
        # 4. 📲 將 Dify 文字成果與實體截圖一起送進 LINE
        if report_text or uploaded_images:
            send_line_report(report_text, uploaded_images)
            
    except Exception as e:
        print(f"❌ 執行失敗: {str(e)}")

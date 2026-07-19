import os
import requests
from playwright.sync_api import sync_playwright

DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")

def capture_and_upload_screenshots():
    """自動前往 WSJ 與 Barron's 首頁截圖，並直接同步至 GitHub 倉庫轉為公開網址"""
    print("📡 正在啟動隱形瀏覽器進行 WSJ 與 Barron's 首頁截圖...")
    
    # 確保 docs 資料夾存在
    os.makedirs("docs", exist_ok=True)
    
    targets = [
        {"name": "WSJ", "url": "https://www.wsj.com", "file": "docs/wsj.png"},
        {"name": "Barron's", "url": "https://www.barrons.com", "file": "docs/barrons.png"}
    ]
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            for target in targets:
                try:
                    print(f"📸 正在截圖 {target['name']} 首頁並儲存至本機...")
                    page.goto(target['url'], timeout=60000, wait_until="networkidle")
                    # 只拍第一屏畫面（免滑動的巨幅大標題）
                    page.screenshot(path=target['file'], full_page=False)
                except Exception as e:
                    print(f"❌ {target['name']} 截圖失敗: {e}")
            browser.close()
    except Exception as e:
        print(f"❌ Playwright 瀏覽器核心啟動失敗: {e}")
    
    # 🚀 核心魔法：直接在雲端把截圖 Git Push 到你的倉庫，讓它變成網路上活生生的圖片網址
    print("📤 正在將最新截圖即時同步回推至 GitHub 倉庫...")
    os.system('git config --local user.name "github-actions[bot]"')
    os.system('git config --local user.email "github-actions[bot]@users.noreply.github.com"')
    os.system('git add docs/wsj.png docs/barrons.png')
    os.system('git commit -m "🤖 雲端自動更新 LINE 所需財經截圖" || echo "截圖無變化"')
    os.system('git push')
    
    # 算出 GitHub 官方提供的 Raw 圖片公開直連網址
    repo = os.environ.get("GITHUB_REPOSITORY")  # 格式如 "你的帳號/你的專案"
    branch = os.environ.get("GITHUB_REF_NAME", "main")  # 當前分支名稱
    
    image_urls = []
    if os.path.exists("docs/wsj.png"):
        image_urls.append(f"https://raw.githubusercontent.com/{repo}/{branch}/docs/wsj.png")
    if os.path.exists("docs/barrons.png"):
        image_urls.append(f"https://raw.githubusercontent.com/{repo}/{branch}/docs/barrons.png")
        
    print(f"🔗 產生 GitHub 官方圖片直連網址: {image_urls}")
    return image_urls

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
            "wsj_raw_headlines": wsj_data,
            "yahoo_trending_symbols": trending_stocks
        }, 
        "response_mode": "blocking", 
        "user": "github-actions-bot"
    }
    response = requests.post(url, headers=headers, json=data, timeout=120)
    response.raise_for_status()
    return response.json().get("data", {}).get("outputs", {}).get("text", "")

def send_line_report(message, image_urls):
    """📲 同一個 Push 同時發送 Dify 文字報表與 GitHub 直連截圖"""
    print("📲 正在準備綜合包推送到 LINE...")
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    messages = []
    if message:
        messages.append({"type": "text", "text": message})
        
    for img_url in image_urls:
        if img_url:
            messages.append({
                "type": "image",
                "originalContentUrl": img_url,
                "previewImageUrl": img_url
            })
            
    if not messages:
        print("⚠️ 沒有任何內容可以發送")
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
        # 1. 執行網頁首頁截圖並直接同步回倉庫
        uploaded_images = capture_and_upload_screenshots()
        
        # 2. 執行原本的雅虎熱搜股票抓取
        stocks_live = fetch_yahoo_realtime_trending()
        
        # 3. 產生餵給 Dify 的文字提示
        wsj_placeholder = "已成功完成 WSJ 與 Barron's 首頁首屏截圖並上傳至 GitHub 伺服器。"
        if uploaded_images:
            wsj_placeholder += f"\n截圖直連網址: {', '.join(uploaded_images)}"
            
        report_text = run_dify_workflow(wsj_placeholder, stocks_live)
        
        # 4. 將 Dify 文字成果與 GitHub 直連圖片一起送進 LINE
        if report_text or uploaded_images:
            send_line_report(report_text, uploaded_images)
            
    except Exception as e:
        print(f"❌ 執行失敗: {str(e)}")

import os
import requests

# 讀取 Dify 與 LINE Notify 的專屬鑰匙
DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
LINE_TOKEN = os.environ.get("LINE_TOKEN")

def get_dify_report():
    print("🚀 正在呼叫 Dify 大腦，撰寫今日晨報...")
    url = "https://api.dify.ai/v1/workflows/run"
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "inputs": {}, 
        "response_mode": "blocking", 
        "user": "github-actions-bot"
    }
    response = requests.post(url, headers=headers, json=data, timeout=120)
    response.raise_for_status()
    result = response.json()
    return result.get("data", {}).get("outputs", {}).get("text", "")

def send_line_notify(message):
    print("📲 正在透過 LINE Notify 推送...")
    url = "https://notify-api.line.me/api/notify"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}"
    }
    data = {
        "message": f"\n{message}"
    }
    response = requests.post(url, headers=headers, data=data)
    response.raise_for_status()
    print("✅ LINE Notify 推送成功！")

if __name__ == "__main__":
    try:
        report_text = get_dify_report()
        if report_text:
            send_line_notify(report_text)
        else:
            print("⚠️ Dify 未返回任何文字。")
    except Exception as e:
        print(f"❌ 晨報生成失敗: {str(e)}")

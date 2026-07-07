import os
import requests

# 1. 讀取 Dify 金鑰，以及原本就在使用的 LINE 官方帳號變數
DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")

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

def send_line_message(message):
    print("📲 正在透過 LINE 官方帳號推送到你的聊天室...")
    # 注意：這裡的網址和原本 LINE Notify 的完全不同
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    # LINE 官方帳號要求的資料結構
    data = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": f"📊 【今日華爾街與熱門股晨報】\n\n{message}"
            }
        ]
    }
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    print("✅ LINE 推送成功！")

if __name__ == "__main__":
    try:
        report_text = get_dify_report()
        if report_text:
            send_line_message(report_text)
        else:
            print("⚠️ Dify 未返回任何文字。")
    except Exception as e:
        print(f"❌ 晨報生成失敗: {str(e)}")

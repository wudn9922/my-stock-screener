import requests


# =========================================================================
# LINE Messaging API
# =========================================================================
def send_line_message(
    message,
    access_token,
    user_id
):
    if not access_token or not user_id:
        print(
            "⚠️ LINE 設定不完整，略過推播"
        )
        return None

    access_token = str(
        access_token
    ).strip()

    user_id = str(
        user_id
    ).strip()

    message = str(message)

    if not user_id.startswith("U"):
        print(
            "❌ LINE_USER_ID 格式錯誤，"
            "Messaging API User ID 應以 U 開頭"
        )
        return None

    if len(message) > 5000:
        message = (
            message[:4980]
            + "\n...(訊息已截斷)"
        )

    url = (
        "https://api.line.me/"
        "v2/bot/message/push"
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": (
            f"Bearer {access_token}"
        )
    }

    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=20
        )

        print(
            "📨 LINE 推播狀態："
            f"{response.status_code}"
        )

        if response.status_code >= 400:
            request_id = (
                response.headers.get(
                    "x-line-request-id",
                    "無"
                )
            )

            print(
                "⚠️ LINE 推播失敗："
                f"{response.text[:500]}"
            )

            print(
                f"LINE Request ID：{request_id}"
            )
        else:
            print("✅ LINE 推播成功")

        return response.status_code

    except Exception as exc:
        print(
            "⚠️ LINE 推播異常："
            f"{type(exc).__name__}: {exc}"
        )

        return None


def send_line_messages_combined(
    messages,
    access_token,
    user_id,
    max_length=4900
):
    """
    把多段簡短訊息合併成一則 LINE 文字訊息再推播。

    各段之間空一行；合併後超過 LINE 單則 5000 字上限時，
    以「整段」為單位拆成最少則數，不會把單一段落切成兩半。
    """
    chunks = []
    current = ""

    for message in messages:
        message = str(message or "").strip()

        if not message:
            continue

        if not current:
            current = message
        elif len(current) + 2 + len(message) <= max_length:
            current = f"{current}\n\n{message}"
        else:
            chunks.append(current)
            current = message

    if current:
        chunks.append(current)

    for chunk in chunks:
        send_line_message(
            chunk,
            access_token,
            user_id
        )

    return len(chunks)

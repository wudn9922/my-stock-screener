import requests
from dify_config import LINE_ACCESS_TOKEN, LINE_USER_ID


def split_line_text(text, max_length=4900):
    """避免超過 LINE 單則文字訊息 5000 字元限制。"""
    if not text:
        return []

    chunks = []

    while len(text) > max_length:
        split_position = text.rfind(
            "\n",
            0,
            max_length,
        )

        if split_position <= 0:
            split_position = max_length

        chunks.append(
            text[:split_position].strip()
        )

        text = text[split_position:].strip()

    if text:
        chunks.append(text)

    return chunks


def build_image_flex_message(image_urls):
    """把所有截圖放進同一個 Flex 氣泡，整組只佔一則訊息。"""
    return {
        "type": "flex",
        "altText": "新聞截圖",
        "contents": {
            "type": "bubble",
            "size": "giga",
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "0px",
                "contents": [
                    {
                        "type": "image",
                        "url": image_url,
                        "size": "full",
                        "aspectMode": "fit",
                        "aspectRatio": "6:5",
                        "action": {
                            "type": "uri",
                            "label": "開啟",
                            "uri": image_url,
                        },
                    }
                    for image_url in image_urls
                ],
            },
        },
    }


def build_line_messages(message, image_urls, as_flex=True):
    """
    組出要推播的訊息：文字報告 + 截圖，通常合計兩則。

    as_flex=False 時改成每張圖一則圖片訊息（Flex 被拒時的備援）。
    """
    messages = [
        {"type": "text", "text": text_chunk}
        for text_chunk in split_line_text(message)
    ]

    image_urls = [image_url for image_url in image_urls if image_url]

    if image_urls and as_flex:
        messages.append(build_image_flex_message(image_urls))
    else:
        for image_url in image_urls:
            messages.append({
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url,
            })

    return messages


def push_line_messages(messages):
    """推播至 LINE，失敗時回傳 response 給呼叫端判斷。"""
    # LINE 一次最多傳送 5 個 message objects；本流程通常只有 2 則
    return requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "to": LINE_USER_ID,
            "messages": messages[:5],
        },
        timeout=30,
    )


import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


import requests
from breakout_alert.atlas_links import plain_ticker_links


from dify_config import LINE_ACCESS_TOKEN, LINE_USER_ID


from news_screenshots import (
    WAYBACK_CDX_API as WAYBACK_CDX_API,
    WAYBACK_AVAILABLE_API as WAYBACK_AVAILABLE_API,
)




from news_screenshots import (
    is_blocked_page as is_blocked_page,
    detect_blocked_page as detect_blocked_page,
    get_wayback_snapshot as get_wayback_snapshot,
    file_sha256 as file_sha256,
    load_news_state as load_news_state,
    save_news_state as save_news_state,
    prune_news_files as prune_news_files,
    prepare_news_items as prepare_news_items,
    no_news_lines,
    publish_screenshots as publish_screenshots,
    capture_and_upload_screenshots as capture_and_upload_screenshots,
    capture_prepare_and_publish,
)

from trending_stocks import fetch_yahoo_realtime_trending

from us_rates_report import (
    fetch_us_rates_report,
    insert_rates_below_trending,
)

from dify_client import run_dify_workflow

from report_line_messages import (
    build_line_messages,
    push_line_messages,
)

def save_trending_stocks(stocks, fetched_at, path="docs/report/trending-stocks.json"):
    """Save the same ordered symbols sent to LINE; an empty fetch retains the last good report."""
    symbols = list(dict.fromkeys(
        symbol for s in stocks.split(",")
        if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol := s.strip().upper())
    ))[:5]
    if not symbols:
        return False
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "source": "Yahoo Finance US trending · LINE 財經報告",
        "fetchedAt": fetched_at,
        "sentAt": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return True


def send_line_report(message, image_urls):
    """將 Dify 文字報告及新聞截圖推送至 LINE（合併為一到兩則訊息）。"""
    print("正在準備 LINE 推播...")

    if not LINE_ACCESS_TOKEN:
        raise ValueError(
            "未設定 LINE_ACCESS_TOKEN"
        )

    if not LINE_USER_ID:
        raise ValueError(
            "未設定 LINE_USER_ID"
        )

    messages = build_line_messages(message, image_urls)

    if not messages:
        print("沒有任何內容可以發送。")
        return

    response = push_line_messages(messages)

    has_flex = any(m["type"] == "flex" for m in messages)

    if not response.ok and has_flex:
        # Flex 被 LINE 拒絕（例如圖片過大）時，退回一般圖片訊息
        print(
            "LINE 拒絕 Flex 訊息，改用一般圖片訊息："
            f"{response.status_code} {response.text[:300]}"
        )

        response = push_line_messages(
            build_line_messages(message, image_urls, as_flex=False)
        )

    if not response.ok:
        raise RuntimeError(
            f"LINE 推播失敗："
            f"{response.status_code} {response.text}"
        )

    print("LINE 財經報表與截圖推播成功。")


if __name__ == "__main__":
    try:
        # 1. 截取 WSJ 與 Barron's；與上次相同或失敗的來源改列「今日無新聞」
        captured_news, no_news = capture_prepare_and_publish()

        # 2. 取得熱門商品，並只保留美國交易所掛牌股票
        stocks_live = fetch_yahoo_realtime_trending(
            limit=5
        )
        stocks_fetched_at = datetime.now(timezone.utc).isoformat()

        # 3. 取得聯邦基金目標區間及 US10Y
        rates_report = fetch_us_rates_report()

        # 4. 將圖片上傳給 Dify Vision 模型辨識及翻譯
        report_text = ""

        if captured_news:
            try:
                report_text = run_dify_workflow(
                    captured_news,
                    stocks_live,
                )

            except Exception as e:
                # Dify 即使發生 504、模型輸出空白或服務異常，
                # 仍保留 LINE 圖片及熱門股推播功能。
                print(
                    "Dify 分析失敗，但仍會發送新聞截圖："
                    f"{e}"
                )

                captured_names = "、".join(
                    item["name"] for item in captured_news
                )

                report_text = (
                    "⚠️ 今日 AI 新聞翻譯暫時無法完成，"
                    f"已附上 {captured_names} 原始新聞截圖。"
                    "\n\n"
                    "📈 今日熱門美股\n"
                    f"{stocks_live or '目前沒有取得符合條件的熱門美股'}"
                    "\n\n"
                    "請稍後至 Dify 執行紀錄確認服務狀態。"
                )

        else:
            print(
                "沒有成功取得新聞截圖，"
                "略過 Dify 圖片分析。"
            )

            # 沒有新截圖時，若有取得熱門美股仍可發送文字
            if stocks_live:
                report_text = (
                    "📈 今日熱門美股\n"
                    f"{stocks_live}"
                )

        # 沒有新圖的來源（與上次相同或截圖失敗）只列一行「今日無新聞」，
        # 不再每天重複同樣的失敗警告
        if no_news:
            report_text = (
                f"{no_news_lines(no_news)}\n\n{report_text}"
            ).strip()

        # 5. 將兩行利率資訊插入熱門股下方
        report_text = insert_rates_below_trending(
            report_text,
            rates_report,
        )

        # 6. 將 Dify 報告、利率及原始截圖發送至 LINE
        image_urls = [
            item["url"]
            for item in captured_news
            if item.get("url")
        ]

        # 熱門美股附上 Atlas 圖表連結（只加在 LINE 訊息）
        trending_symbols = [
            symbol.strip()
            for symbol in (stocks_live or "").split(",")
            if symbol.strip()
        ]

        if report_text and trending_symbols:
            report_text = (
                f"{report_text.rstrip()}\n\n"
                "📈 Atlas 圖表\n"
                f"{plain_ticker_links(trending_symbols)}"
            )

        if report_text or image_urls:
            send_line_report(
                report_text,
                image_urls,
            )
            save_trending_stocks(stocks_live, stocks_fetched_at)
        else:
            print("沒有報告或圖片可以發送。")

    except Exception as e:
        print(f"執行失敗：{e}")

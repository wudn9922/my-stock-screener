import pandas as pd

from chart_data import build_stock_data
from group_config import get_ma_list_from_item, is_config_enabled
from index_history import download_index_history
from index_trend import analyze_index_trend
from market_data import extract_yfinance_data
from report_json import parse_index_trend_text
from screener_common import clean_ohlcv_dataframe, safe_float, safe_int


def process_index_charts(index_configs):
    matched_list = []

    if not index_configs:
        print(
            "⚠️ 沒有可用的大盤指數設定"
        )
        return matched_list

    sorted_configs = sorted(
        index_configs,
        key=lambda item: safe_int(
            item.get("sort_order"),
            9999
        )
    )

    processed_tickers = set()

    for item in sorted_configs:
        ticker = str(
            item.get("ticker", "")
        ).strip().upper()

        name = str(
            item.get("name")
            or ticker
        ).strip()

        if not ticker:
            continue

        if ticker in processed_tickers:
            print(
                f"⚠️ 略過重複指數：{ticker}"
            )
            continue

        if not is_config_enabled(item):
            print(
                f"⏭️ 指數已停用：{ticker}"
            )
            continue

        processed_tickers.add(ticker)

        ma_list = get_ma_list_from_item(
            item
        )

        try:
            print(
                "📉 開始建立指數圖表："
                f"{ticker} / {name} / "
                f"MA={ma_list}"
            )

            # ^TWOII 優先使用櫃買中心資料，其餘照舊走 Yahoo
            downloaded = download_index_history(
                ticker,
                period="4y",
                progress=False,
                threads=False,
                auto_adjust=False,
                group_by="column"
            )

            df = extract_yfinance_data(
                downloaded,
                ticker
            )

            df = clean_ohlcv_dataframe(df)

            if df.empty:
                print(
                    "⚠️ 指數下載結果為空："
                    f"{ticker}"
                )
                continue

            for ma_window in ma_list:
                df[f"MA{ma_window}"] = (
                    df["Close"]
                    .rolling(
                        window=ma_window,
                        min_periods=ma_window
                    )
                    .mean()
                )

            latest_close = safe_float(
                df["Close"].iloc[-1]
            )

            latest_volume = safe_float(
                df["Volume"].iloc[-1]
            )

            ma_status = []

            for ma_window in ma_list:
                ma_col = f"MA{ma_window}"
                ma_value = df[
                    ma_col
                ].iloc[-1]

                if pd.isna(ma_value):
                    continue

                ma_value = float(ma_value)

                if ma_value == 0:
                    continue

                diff_pct = (
                    (latest_close / ma_value)
                    - 1
                ) * 100

                ma_status.append(
                    f"MA{ma_window}:"
                    f"{ma_value:.2f}"
                    f"({diff_pct:+.2f}%)"
                )

            title_parts = [
                "Supabase 指數參數",
                f"收盤:{latest_close:,.2f}"
            ]

            if latest_volume > 0:
                title_parts.append(
                    f"成交量:{latest_volume:,.0f}"
                )
            else:
                title_parts.append(
                    "成交量:Yahoo未提供"
                )

            if ma_status:
                title_parts.append(
                    " | ".join(ma_status)
                )

            title = (
                "("
                + " | ".join(title_parts)
                + ")"
            )

            chart_data = build_stock_data(
                df.tail(90),
                ticker,
                title,
                ma_list,
                show_volume=True,
                display_name=name,
                timeframe_label="日K"
            )

            matched_list.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "volume": int(
                        latest_volume
                    ),
                    "ma_list": ma_list,
                    "chart_data": chart_data
                }
            )

            print(
                "✅ 指數圖表建立完成："
                f"{ticker} {name}"
            )

        except Exception as exc:
            print(
                "❌ 指數圖表建立失敗："
                f"{ticker} / "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    print(
        "📊 全球指數圖表數量："
        f"{len(matched_list)}"
    )

    return matched_list


def build_index_map(index_configs):
    index_map = {}

    for item in index_configs:
        if not is_config_enabled(item):
            continue

        ticker = str(
            item.get("ticker", "")
        ).strip().upper()

        if not ticker:
            continue

        if ticker in index_map:
            print(
                "⚠️ index_configs "
                f"出現重複 ticker：{ticker}，"
                "將使用最後一筆"
            )

        index_map[ticker] = item

    return index_map


INDEX_SHORT_NAMES = {
    "^TWII": "加權",
    "^TWOII": "櫃買",
    "^GSPC": "標普500",
    "^DJI": "道瓊",
    "^IXIC": "那斯達克",
    "^RUT": "羅素2000",
    "^SOX": "費半",
    "^FCHI": "CAC40",
    "^FTSE": "富時100",
    "^GDAXI": "DAX",
    "^N225": "日經225",
    "^KS11": "KOSPI"
}

INDEX_LINE_LEGEND = (
    "均線：▲▲看多 ▲偏多 －不明 ▼偏空 ▼▼看空｜"
    "道氏：🔺多 🔻空 💡大多小空 ⚡大空小多"
)

# 均線五個等級，對應 analyze_index_trend 的 看多／偏多／多空不明／偏空／看空
INDEX_MA_SYMBOLS = {
    "看多": "▲▲",
    "偏多": "▲",
    "多空不明": "－",
    "偏空": "▼",
    "看空": "▼▼"
}


def build_index_line_short(
    ticker,
    name,
    index_line
):
    """
    LINE 用的單行大盤摘要：「簡稱 均線符號 道氏符號」。
    均線五級：▲▲看多、▲偏多、－不明、▼偏空、▼▼看空。
    道氏單一符號：🔺多、🔻空、💡大多小空、⚡大空小多、➖不明。
    只依 analyze_index_trend 的文字解析，不改變網頁使用的原文。
    """
    short_name = INDEX_SHORT_NAMES.get(
        ticker,
        str(name or ticker)
    )

    parsed = parse_index_trend_text(
        index_line
    )

    score = parsed.get("score")

    if (
        score is None
        and parsed.get("macroTrend") is None
    ):
        return f"{short_name} 分析異常"

    ma_symbol = INDEX_MA_SYMBOLS.get(
        parsed.get("scoreLabel")
    )

    if ma_symbol is None:
        score_max = parsed.get("scoreMax")

        if score is None or score == 0:
            ma_symbol = "－"
        elif score_max and abs(score) >= score_max:
            ma_symbol = "▲▲" if score > 0 else "▼▼"
        else:
            ma_symbol = "▲" if score > 0 else "▼"

    macro = parsed.get("macroTrend")
    micro = parsed.get("microTrend")

    if macro == "bull" and micro == "bull":
        dow_symbol = "🔺"
    elif macro == "bear" and micro == "bear":
        dow_symbol = "🔻"
    elif macro == "bull":
        dow_symbol = "💡"
    elif macro == "bear":
        dow_symbol = "⚡"
    else:
        dow_symbol = "➖"

    return (
        f"{short_name} 均線{ma_symbol} "
        f"道氏{dow_symbol}"
    )


def append_index_market_report(
    lines,
    market_title,
    tickers,
    index_map,
    captured_lines=None,
    short_lines=None
):
    """
    將單一市場的指數分析加入 lines（完整文字，Atlas 報告 JSON 使用）。

    captured_lines（選用 dict）會收到「ticker → 該指數的分析文字」，
    供 Atlas 報告 JSON 使用；傳入與否都不會改變 lines 的內容。
    short_lines（選用 list）會收到 LINE 用的精簡單行摘要。
    回傳本次加入 lines 的區塊（list）。
    """
    block_start = len(lines)

    lines.append(market_title)

    if short_lines is not None:
        short_lines.append(market_title)

    found_count = 0

    for ticker in tickers:
        normalized_ticker = str(
            ticker
        ).strip().upper()

        item = index_map.get(
            normalized_ticker
        )

        if not item:
            continue

        found_count += 1

        index_line = analyze_index_trend(
            normalized_ticker,
            item.get(
                "name",
                normalized_ticker
            ),
            get_ma_list_from_item(
                item
            )
        )

        if captured_lines is not None:
            captured_lines[
                normalized_ticker
            ] = index_line

        lines.append(index_line)

        if short_lines is not None:
            short_lines.append(
                build_index_line_short(
                    normalized_ticker,
                    item.get(
                        "name",
                        normalized_ticker
                    ),
                    index_line
                )
            )

    if found_count == 0:
        lines.append(
            "⚪ 此市場目前沒有啟用的指數設定"
        )

        if short_lines is not None:
            short_lines.append(
                "⚪ 沒有啟用的指數設定"
            )

    lines.append("")

    if short_lines is not None:
        short_lines.append("")

    return lines[block_start:]

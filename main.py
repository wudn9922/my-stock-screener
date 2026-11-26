# =========================================================================
# main.py 完整整合版
# 第 1 部分：Imports、全域設定、共用工具、Supabase 動態群組
# =========================================================================
import io
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf
import kline_integrity as kline
from breakout_alert.supabase_store import FIXED_STOCK_GROUP_ID, fixed_parameter_map, parameter_ticker_key
from breakout_alert.account_permissions import is_public_report_owner, storage_mode
from tpex_otc import (
    TPEX_INDEX_HISTORY_LEGACY_URL,
    TPEX_OTC_NAME,
    TPEX_OTC_SYMBOL,
    TPEX_RUNTIME,
    TPEX_SOURCE_LABEL,
    get_tpex_otc_full_history,
    get_tpex_otc_history,
    update_tpex_otc_index_history,
)


import screener_config
from screener_config import (
    TW_MIN_VOLUME,
    HTTP_HEADERS,
    DATA_DIR,
    REPORT_NON_TRADING_DATES,
    DOCS_DIR,
    MAX_DAYS,
    BACKFILL_ENABLED,
    BACKFILL_MIN_ROWS,
    BACKFILL_MAX_PER_RUN,
    REPORT_DIR,
    REPORT_SERIES_DIR,
    REPORT_JSON_VERSION,
    REPORT_LIFF_ID,
    REPORT_LIFF_URL,
    SETTING_LIFF_ID,
    SETTING_LIFF_URL,
    BITGET_SETTING_URL,
)

# =========================================================================
# 類股設定
# =========================================================================

from sector_weekly import (
    SECTOR_WEEKLY_MA_LIST,
    SECTOR_BENCHMARK,
    SECTOR_CHART_WEEKS,
    SECTOR_CONFIGS,
)

from screener_common import TW_STOCK_NAMES

from screener_config import (
    CUSTOM_GROUP_TEST_MODE,
    CUSTOM_MA_MIN_RATIO,
    CUSTOM_MA_MAX_RATIO,
)


# =========================================================================
# 共用工具
# =========================================================================
from screener_common import (
    safe_int,
    safe_float,
    clean_json_value,
    register_tw_stock_name,
    get_tw_stock_name,
)


from market_data import (
    _extract_yfinance_data,
    refetch_kline_once,
    extract_yfinance_data,
    write_kline_csv,
)


from screener_common import (
    clean_ohlcv_dataframe,
    calculate_return,
)


from group_config import (
    normalize_group_name,
    detect_group_market,
    get_custom_group_number,
    normalize_custom_ticker,
    get_group_ma_list,
    get_stock_ma_list,
    get_supabase_settings,
    get_default_index_configs,
    get_default_group_metadata,
    load_configs_from_supabase,
)


# =========================================================================
# 第 1 部分結束
# 下一部分將從 LINE Messaging API、台美股清單開始
# =========================================================================
# =========================================================================
# 第 2 部分：LINE、股票清單、圖表、全市場與自訂群組
# =========================================================================


from line_messaging import send_line_message, send_line_messages_combined


from ticker_lists import get_tw_tickers, get_us_tickers


# =========================================================================
# Plotly 圖表資料
# =========================================================================

from chart_data import build_stock_data



from market_data import (
    download_single_ticker_with_retry,
    download_market_data,
)

from market_scan import (
    _report_analysis_prices,
    _scan_analysis_frames,
    scan_market,
    collect_market_frames,
    _market_scan_report,
    _rs_rank_or_none,
    report_rs_rank_of,
    backfill_short_csvs,
)

from custom_groups import (
    get_ticker_candidates,
    download_custom_stock,
    _failed_stock_symbol,
    _record_failed_stock,
    _record_unmatched_stock,
    process_custom_groups,
)


# =========================================================================
# 第 2 部分結束
# 下一部分：全球指數、美股類股週 K、大盤趨勢分析
# =========================================================================

# =========================================================================
# 第 3 部分：全球指數、類股週 K、大盤趨勢分析
# =========================================================================


# =========================================================================
# 大盤參數與指數圖表
# =========================================================================
from group_config import get_ma_list_from_item, is_config_enabled


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


# =========================================================================
# 美股 11 大類股週 K
# =========================================================================
from sector_weekly import (
    convert_daily_to_weekly,
    determine_sector_price_volume_status,
    determine_sector_trend,
    process_sector_weekly_charts,
    build_sector_line_message,
    build_sector_monday_message,
)


from index_trend import (
    DOW_SWING_PIVOT_BARS,
    DOW_TREND_PIVOT_BARS,
    DOW_TREND_NEW_HIGH_BARS,
    compute_dow_states,
    analyze_index_trend,
)


# =========================================================================
# 第 3 部分結束
# 下一部分：HTML、動態群組頁籤、LIFF 與畫線同步
# =========================================================================

from group_config import get_sorted_custom_group_keys


# =========================================================================
# 第 6 部分：GitHub Pages 推送與主程式
# =========================================================================


# =========================================================================
# Git 指令工具
# =========================================================================
from git_publish import run_git_command, get_csv_last_date, push_report_to_github


def prune_stale_csv(
    data_dir=screener_config.DATA_DIR,
    max_age_days=None
):
    """K 線 CSV 是 owner 永久保留的紀錄；保留呼叫介面，但永不刪檔。

    掉出名單、下市、過期或損壞的 CSV 仍留在 main 與完整 git 歷史中。
    data_dir／max_age_days 只為既有呼叫相容而保留，不再用於清理。
    """
    return 0


from index_history import INDEX_HISTORY_CACHE, download_index_history


from report_json import (
    REPORT_MARKETS,
    REPORT_EXTRA_INDEX_MA_LIST,
    WORLD_EXTRA_INDEX_CONFIGS,
    REPORT_CORE_INDEX_NAMES,
    REPORT_SCAN_GROUP_NAMES,
    INDEX_TREND_ICON_MAP,
    parse_index_trend_text,
    build_index_snapshot,
    build_index_status,
    analyze_index_for_report,
    build_report_indices,
    _chart_last_ma_values,
    _chart_close_change,
    _strip_title_parentheses,
    build_report_group_item,
    build_report_unmatched_item,
    build_report_groups,
    build_report_sectors,
    build_report_payload,
    _json_default,
    write_json_atomic,
    write_report_json,
)

from screener_common import round_report_number


from report_universe import (
    build_report_universe,
    write_report_universe,
    build_series_payload,
    write_report_series,
    write_tpex_otc_series,
)


# =========================================================================
# 主程式共用工具
# =========================================================================
from custom_groups import process_group_for_data_dict


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


# =========================================================================
# 主程式
# =========================================================================
def main():
    access_token = os.environ.get(
        "LINE_ACCESS_TOKEN"
    )

    # LINE Messaging API 推播使用者 ID
    line_push_user_id = os.environ.get(
        "LINE_USER_ID"
    )

    # Supabase 股票名單查詢使用者 ID
    # 若未設定，才退回使用 LINE_USER_ID
    supabase_user_id = (
        os.environ.get(
            "SUPABASE_USER_ID"
        )
        or line_push_user_id
    )

    if not line_push_user_id:
        print(
            "❌ 未設定 LINE_USER_ID"
        )
        return

    if not supabase_user_id:
        print(
            "❌ 未設定 SUPABASE_USER_ID"
        )
        return

    line_push_user_id = str(
        line_push_user_id
    ).strip()

    supabase_user_id = str(
        supabase_user_id
    ).strip()

    # This command creates public JSON, chart images and GitHub artifacts. It is
    # not a per-user report endpoint: never publish another account's settings.
    if not is_public_report_owner(supabase_user_id):
        print("ℹ️ 公開日報僅接受管理員帳號；略過私人設定讀取及所有報告產物寫入。")
        return

    REPORT_NON_TRADING_DATES.update(TW=frozenset(), US=frozenset())

    print(
        "👤 LINE 推播使用：LINE_USER_ID"
    )

    if os.environ.get(
        "SUPABASE_USER_ID"
    ):
        print(
            "👤 Supabase 查詢使用："
            "SUPABASE_USER_ID"
        )
    else:
        print(
            "⚠️ 未設定 SUPABASE_USER_ID，"
            "Supabase 查詢將使用 LINE_USER_ID"
        )

    today = datetime.now()
    today_str = today.strftime(
        "%Y-%m-%d"
    )
    weekday = today.weekday()

    # -----------------------------------------------------------------
    # 讀取 Supabase 股票群組、動態群組與指數參數
    # -----------------------------------------------------------------
    (
        user_configs,
        db_index_configs,
        group_metadata
    ) = load_configs_from_supabase(
        supabase_user_id
    )

    # -----------------------------------------------------------------
    # 台股全市場
    # -----------------------------------------------------------------
    print(
        "📊 開始掃描台股全市場"
    )

    tw_tickers = get_tw_tickers()

    # 先下載並更新所有台股 CSV（不設成交股數門檻）；tw_all 清單要等 RS 百分位算好後
    # 才篩選與排序（見下方「選股篩選器」），所以這裡只取回日 K，不另外下載任何資料
    tw_frames = collect_market_frames(
        tw_tickers
    )

    # -----------------------------------------------------------------
    # 美股全市場
    # -----------------------------------------------------------------
    print(
        "📊 開始掃描美股全市場"
    )

    us_tickers = get_us_tickers()

    us_all = scan_market(
        us_tickers
    )

    # -----------------------------------------------------------------
    # 選用：補齊歷史不足 240 根的 CSV（52 週高點需要 252 根）
    # 預設不啟用（BACKFILL_ENABLED），失敗不影響其他流程
    # -----------------------------------------------------------------
    if BACKFILL_ENABLED:
        try:
            backfill_short_csvs(
                list(tw_tickers) + list(us_tickers)
            )
        except Exception as exc:
            print(
                "❌ CSV 歷史回補失敗："
                f"{type(exc).__name__}: {exc}"
            )

    # -----------------------------------------------------------------
    # 櫃買指數（^TWOII）：更新櫃買中心官方日資料
    # 失敗時 ^TWOII 會自動退回原本的 Yahoo 流程
    # -----------------------------------------------------------------
    print(
        "🏦 開始更新櫃買指數（TPEx）"
    )

    try:
        update_tpex_otc_index_history()
    except Exception as exc:
        print(
            "❌ TPEx 櫃買指數更新失敗："
            f"{type(exc).__name__}: {exc}"
        )

    # -----------------------------------------------------------------
    # 全球大盤圖表
    # 使用 Supabase index_configs 的均線設定
    # -----------------------------------------------------------------
    print(
        "🌍 開始建立全球指數圖表"
    )

    index_charts = process_index_charts(
        db_index_configs
    )

    # -----------------------------------------------------------------
    # 美股 11 大類股週 K
    # -----------------------------------------------------------------
    print(
        "🧭 開始建立類股週 K 圖表"
    )

    (
        sector_charts,
        sector_summary
    ) = process_sector_weekly_charts()

    # -----------------------------------------------------------------
    # 選股篩選器：全市場 universe 與群組指標（report_metrics.py）
    # 失敗時報告照舊產生，只是沒有 metrics 與 universe.json，tw_all 改依成交量排序。
    # 要在 tw_all 篩選前建立：RS 百分位（metrics_lookup 的 rsRank）用於 tw_all 排序，
    # 指標只讀已更新的 CSV 與 INDEX_HISTORY_CACHE 的 ^TWII，不增加下載
    # （^TWII 已由 process_index_charts 放進快取；美股基準 SPY 仍只下載這一次）。
    # -----------------------------------------------------------------
    report_universes = None
    metrics_lookup = None

    try:
        (
            report_universes,
            metrics_lookup
        ) = build_report_universe(
            tw_tickers,
            us_tickers
        )
    except Exception as exc:
        print(
            "❌ 選股篩選器指標產生失敗："
            f"{type(exc).__name__}: {exc}"
        )

    # -----------------------------------------------------------------
    # 台股 tw_all（全市場潛伏）：收盤在 MA20 −2%～+1%、最新日 K 至少 100 萬股、
    # MA20 上彎，依 RS 百分位由高到低排序（沒有百分位的排最後，同百分位依成交量）。
    # 低量與非上彎的股票照常下載、保留在全部資料，只是不進這份報告清單。
    # -----------------------------------------------------------------
    tw_all = _market_scan_report(
        tw_tickers,
        tw_frames,
        TW_MIN_VOLUME,
        latent_rules=True,
        rs_rank_of=report_rs_rank_of(
            metrics_lookup
        )
    )

    # -----------------------------------------------------------------
    # 建立固定分類
    # -----------------------------------------------------------------
    data_dict = {
        "indices": index_charts,
        "sectors": sector_charts,
        "tw_all": tw_all,
        "us_all": us_all
    }

    # 未成功股票（unmatched／failed）：只給網頁報告，LINE／圖表／Dify 不使用
    unmatched_by_group = {}

    fixed_group_keys = [
        "tw_g1",
        "tw_g2",
        "us_g1",
        "us_g2",
        "us_g3",
        "us_g4"
    ]

    for group_key in fixed_group_keys:
        process_group_for_data_dict(
            data_dict,
            user_configs,
            group_metadata,
            group_key,
            unmatched_by_group=unmatched_by_group
        )

    # -----------------------------------------------------------------
    # 動態自訂群組
    #
    # groups.id = 22
    # → group_key = custom_22
    # → stocks.group_id = custom_22
    # -----------------------------------------------------------------
    custom_group_keys = sorted(
        [
            group_key
            for group_key in user_configs
            if group_key.startswith(
                "custom_"
            )
        ],
        key=get_custom_group_number
    )

    for group_key in custom_group_keys:
        process_group_for_data_dict(
            data_dict,
            user_configs,
            group_metadata,
            group_key,
            unmatched_by_group=unmatched_by_group
        )

    # -----------------------------------------------------------------
    # 最終圖表數量
    # -----------------------------------------------------------------
    print(
        "\n===== 最終圖表數量 ====="
    )

    display_order = [
        "indices",
        "sectors",
        "tw_all",
        "tw_g1",
        "tw_g2"
    ]

    display_order.extend(
        get_sorted_custom_group_keys(
            group_metadata,
            market="tw"
        )
    )

    display_order.extend(
        [
            "us_all",
            "us_g1",
            "us_g2",
            "us_g3",
            "us_g4"
        ]
    )

    display_order.extend(
        get_sorted_custom_group_keys(
            group_metadata,
            market="us"
        )
    )

    display_order = list(
        dict.fromkeys(
            display_order
        )
    )

    for key in display_order:
        items = data_dict.get(
            key,
            []
        )

        metadata = group_metadata.get(
            key,
            {}
        )

        group_name = metadata.get(
            "name",
            key
        )

        print(
            f"{key}: "
            f"{len(items)} 檔 / "
            f"{group_name}"
        )

    print(
        "========================\n"
    )

    # -----------------------------------------------------------------
    # 報告網址（LINE 訊息不再附連結，只留給網頁報告 JSON 使用）
    # -----------------------------------------------------------------
    report_url = REPORT_LIFF_URL

    # -----------------------------------------------------------------
    # LINE 個股與網頁報告推播
    #
    # 依需求：動態自訂群組不加入 LINE 統計，
    # 避免未來新增大量群組後訊息過長。
    # -----------------------------------------------------------------
    def stock_count(group_key):
        return len(data_dict.get(group_key, []))

    line_message_stocks = (
        f"🎯 {today_str} 量化日報\n"
        "🇹🇼 符合檔數："
        f"全市場 {stock_count('tw_all')}"
        f"｜權值 {stock_count('tw_g1')}"
        f"｜熱門 {stock_count('tw_g2')}\n"
        "🇺🇸 符合檔數："
        f"全市場 {stock_count('us_all')}"
        f"｜權值 {stock_count('us_g1')}"
        f"｜低本益 {stock_count('us_g2')}"
        f"｜績效 {stock_count('us_g3')}"
        f"｜熱門 {stock_count('us_g4')}"
    )

    # 股票、大盤（週一再加類股）合併成一則 LINE 訊息，
    # 在下方類股區塊結束後一次送出
    pending_line_messages = [line_message_stocks]

    # -----------------------------------------------------------------
    # 原本的大盤分類與趨勢推播
    # -----------------------------------------------------------------
    tw_indices = [
        "^TWII",
        "^TWOII"
    ]

    us_indices = [
        "^GSPC",
        "^DJI",
        "^IXIC",
        "^RUT",
        "^SOX"
    ]

    eu_indices = [
        "^FCHI",
        "^FTSE",
        "^GDAXI"
    ]

    asia_indices = [
        "^N225",
        "^KS11"
    ]

    index_map = build_index_map(
        db_index_configs
    )

    # 每個指數的 LINE 分析文字，供 Atlas 報告 JSON 使用
    captured_index_lines = {}

    index_lines = [
        (
            f"🌍 {today_str} "
            "全球大盤多空量化報告"
        ),
        (
            "📊 評分標準: "
            "均線糾纏自適應/"
            "0.1%過濾機制"
        ),
        "========================",
        ""
    ]

    # LINE 只送精簡版；index_lines 的完整文字留給網頁（Atlas 報告 JSON）
    line_index_lines = [
        f"🌍 {today_str} 全球大盤多空",
        ""
    ]

    tw_index_block = append_index_market_report(
        index_lines,
        "【 🇹🇼 台灣市場 】",
        tw_indices,
        index_map,
        captured_lines=captured_index_lines,
        short_lines=line_index_lines
    )

    us_index_block = append_index_market_report(
        index_lines,
        "【 🇺🇸 美國市場 】",
        us_indices,
        index_map,
        captured_lines=captured_index_lines,
        short_lines=line_index_lines
    )

    eu_index_block = append_index_market_report(
        index_lines,
        "【 🇪🇺 歐洲市場 】",
        eu_indices,
        index_map,
        captured_lines=captured_index_lines,
        short_lines=line_index_lines
    )

    append_index_market_report(
        index_lines,
        "【 🌏 亞洲市場 】",
        asia_indices,
        index_map,
        captured_lines=captured_index_lines,
        short_lines=line_index_lines
    )

    index_message = "\n".join(
        index_lines
    ).rstrip()

    line_index_lines.append(
        INDEX_LINE_LEGEND
    )

    pending_line_messages.append(
        "\n".join(
            line_index_lines
        )
    )

    # -----------------------------------------------------------------
    # 每週一限定類股週線輪動摘要
    # -----------------------------------------------------------------
    sectors_message = None

    if weekday == 0:
        sectors_message = (
            build_sector_monday_message(
                today_str,
                sector_summary,
                report_url
            )
        )

        # LINE 送精簡版；sectors_message 的完整文字留給網頁（Atlas 報告 JSON）
        pending_line_messages.append(
            build_sector_line_message(
                today_str,
                sector_summary
            )
        )

    send_line_messages_combined(
        pending_line_messages,
        access_token,
        line_push_user_id
    )

    # -----------------------------------------------------------------
    # Atlas 報告 JSON（docs/report/latest.json）
    # 失敗只記錄，不影響 CSV 清理與推送
    # -----------------------------------------------------------------
    try:
        report_payload = build_report_payload(
            today_str,
            data_dict,
            group_metadata,
            display_order,
            index_map,
            {
                "tw": tw_indices,
                "jp": [
                    ticker
                    for ticker in asia_indices
                    if ticker == "^N225"
                ],
                "kr": [
                    ticker
                    for ticker in asia_indices
                    if ticker == "^KS11"
                ],
                "eu": eu_indices,
                "us": us_indices
            },
            captured_index_lines,
            {
                "tw": "\n".join(
                    tw_index_block
                ).rstrip(),
                "jp": captured_index_lines.get(
                    "^N225",
                    ""
                ),
                "kr": captured_index_lines.get(
                    "^KS11",
                    ""
                ),
                "eu": "\n".join(
                    eu_index_block
                ).rstrip(),
                "us": "\n".join(
                    us_index_block
                ).rstrip()
            },
            {
                "index": index_message,
                "sectors": sectors_message,
                "stocks": line_message_stocks
            },
            metrics_lookup=metrics_lookup,
            unmatched_by_group=unmatched_by_group
        )

        write_report_json(report_payload, source_user_id=supabase_user_id)

    except Exception as exc:
        print(
            "❌ Atlas 報告 JSON 產生失敗："
            f"{type(exc).__name__}: {exc}"
        )

    if report_universes is not None:
        try:
            write_report_universe(
                report_universes,
                today_str
            )
        except Exception as exc:
            print(
                "❌ 全市場 universe JSON 產生失敗："
                f"{type(exc).__name__}: {exc}"
            )

    try:
        write_tpex_otc_series()
    except Exception as exc:
        print(
            "❌ 櫃買指數 series JSON 產生失敗："
            f"{type(exc).__name__}: {exc}"
        )

    # -----------------------------------------------------------------
    # 推送最新報告 JSON 與 CSV 到 GitHub Pages
    # -----------------------------------------------------------------
    prune_stale_csv()
    # The correction journal is batched for the run; git must see it.
    kline.flush_journal()
    push_report_to_github()


if __name__ == "__main__":
    with kline.batched_journal():
        main()


# =========================================================================
# 完整 main.py 結束
# =========================================================================

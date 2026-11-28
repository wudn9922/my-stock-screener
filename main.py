import os
from datetime import datetime

import kline_integrity as kline
from breakout_alert.account_permissions import is_public_report_owner
from tpex_otc import update_tpex_otc_index_history
from screener_config import (
    TW_MIN_VOLUME,
    REPORT_NON_TRADING_DATES,
    BACKFILL_ENABLED,
    REPORT_LIFF_URL,
)
from group_config import (
    get_custom_group_number,
    load_configs_from_supabase,
    get_sorted_custom_group_keys,
)
from line_messaging import send_line_messages_combined
from ticker_lists import get_tw_tickers, get_us_tickers
from market_scan import (
    scan_market,
    collect_market_frames,
    _market_scan_report,
    report_rs_rank_of,
    backfill_short_csvs,
)
from index_charts import (
    process_index_charts,
    build_index_map,
    INDEX_LINE_LEGEND,
    append_index_market_report,
)
from sector_weekly import (
    process_sector_weekly_charts,
    build_sector_line_message,
    build_sector_monday_message,
)
from git_publish import push_report_to_github, prune_stale_csv
from report_json import build_report_payload, write_report_json
from report_universe import build_report_universe, write_report_universe, write_tpex_otc_series
from custom_groups import process_group_for_data_dict


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

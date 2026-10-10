import sys

import requests

from breakout_alert.breakout_engine import (
    BreakoutEngine,
)
from breakout_alert.discord_notifier import (
    DiscordNotifier,
)
from breakout_alert.market_clock import (
    get_market_decisions,
)
from breakout_alert.market_data_provider import (
    MarketDataProvider,
)
from breakout_alert.price_provider import (
    PriceProvider,
)
from breakout_alert.supabase_store import (
    SupabaseStore,
)


def print_market_decision(decision):
    status_text = (
        "執行"
        if decision.should_run
        else "略過"
    )

    print(
        f"🕒 {decision.market}"
        f"｜{decision.session_type}"
        f"｜{status_text}"
        f"｜{decision.reason}"
    )


def count_actions(results):
    counts = {}

    for result in results:
        counts[result.action] = (
            counts.get(
                result.action,
                0
            )
            + 1
        )

    return counts


def load_drawing_watchlist_safely(store):
    """
    載入 Atlas 畫線。

    畫線模組在這裡才 import，任何錯誤（含 import）
    都只會停用本次畫線提醒，不影響均線提醒。
    """
    try:
        from breakout_alert import drawing_alerts

        if not drawing_alerts.DRAWING_ALERTS_ENABLED:
            print("ℹ️ Atlas 畫線提醒已關閉")
            return None

        watchlist = (
            drawing_alerts.load_drawing_watchlist(
                store
            )
        )

        print(
            f"📐 Atlas 畫線提醒："
            f"{len(watchlist.entries)} 檔、"
            f"{watchlist.line_count} 條線"
        )

        return watchlist

    except Exception as exc:
        print(
            "⚠️ Atlas 畫線載入失敗，已略過："
            f"{type(exc).__name__}: {exc}"
        )

        return None


def prune_drawing_states_safely(
    store,
    watchlist
):
    # 只在畫線讀取成功且至少一列時清理
    if (
        watchlist is None
        or watchlist.row_count <= 0
    ):
        return

    try:
        store.prune_drawing_states(
            watchlist.valid_tickers
        )

    except Exception as exc:
        print(
            "⚠️ 清理畫線狀態列失敗，已略過："
            f"{type(exc).__name__}: {exc}"
        )


def get_due_drawing_configs(
    watchlist,
    due_decisions
):
    if watchlist is None:
        return []

    try:
        return [
            {
                "ticker": entry.ticker,
                "market": entry.market
            }
            for entry in watchlist.entries
            if entry.market in due_decisions
        ]

    except Exception as exc:
        print(
            "⚠️ Atlas 畫線設定異常，已略過："
            f"{type(exc).__name__}: {exc}"
        )

        return []


def fetch_due_quotes(
    price_provider,
    due_configs,
    drawing_configs,
    due_decisions
):
    # 畫線股票併入同一批報價；
    # 失敗時退回只取均線監控股票（原本行為）。
    if drawing_configs:
        try:
            return price_provider.get_due_quotes(
                due_configs + drawing_configs,
                due_decisions
            )

        except Exception as exc:
            print(
                "⚠️ 含畫線股票的報價失敗，"
                "改為只取均線監控報價："
                f"{type(exc).__name__}: {exc}"
            )

            if not due_configs:
                return {}

    return price_provider.get_due_quotes(
        due_configs,
        due_decisions
    )


def run_drawing_alerts(
    store,
    notifier,
    watchlist,
    quotes,
    due_decisions
):
    # 獨立於均線提醒：任何例外只記錄，不中斷本次執行
    try:
        from breakout_alert import drawing_alerts

        engine = drawing_alerts.DrawingAlertEngine(
            store=store,
            notifier=notifier
        )

        summary = engine.process(
            watchlist=watchlist,
            quotes=quotes,
            due_decisions=due_decisions
        )

        print("\n===== Atlas 畫線提醒結果 =====")
        print(
            "建立基準："
            f"{summary.get('baseline', 0)}"
        )
        print(
            "等待確認："
            f"{summary.get('pending', 0)}"
        )
        print(
            "提醒成功："
            f"{summary.get('alert_sent', 0)}"
        )
        print(
            "提醒失敗："
            f"{summary.get('alert_failed', 0)}"
        )
        print(
            "本日已提醒略過："
            f"{summary.get('suppressed', 0)}"
        )
        print(
            "超過上限延後："
            f"{summary.get('deferred', 0)}"
        )
        print(
            "資料略過："
            f"{summary.get('skipped', 0)}"
        )

        blocked = (
            summary.get("blocked", 0)
            + summary.get("state_failed", 0)
            + summary.get("processing_failed", 0)
        )

        if blocked:
            print(
                "狀態異常未提醒："
                f"{blocked}"
            )

        print("==============================")

    except Exception as exc:
        print(
            "❌ Atlas 畫線提醒處理失敗"
            "（不影響均線提醒）："
            f"{type(exc).__name__}: {exc}"
        )


def run_moving_average_alerts(
    *,
    store,
    notifier,
    market_data_provider,
    due_configs,
    quotes,
    due_decisions
):
    analytics = (
        market_data_provider
        .build_analytics(
            due_configs,
            quotes,
            due_decisions
        )
    )

    unique_tickers = {
        config["ticker"]
        for config in due_configs
    }

    # quotes 可能另含畫線股票，只計算均線監控股票
    quoted_count = sum(
        1
        for ticker in unique_tickers
        if ticker in quotes
    )

    print(
        f"💹 成功取得最新價："
        f"{quoted_count}/"
        f"{len(unique_tickers)} 檔"
    )

    print(
        f"📊 成功建立分析資料："
        f"{len(analytics)}/"
        f"{len(unique_tickers)} 檔"
    )

    engine = BreakoutEngine(
        store=store,
        notifier=notifier
    )

    results = engine.process_all(
        monitor_configs=due_configs,
        quotes=quotes,
        analytics=analytics,
        due_decisions=due_decisions
    )

    action_counts = count_actions(
        results
    )

    print("\n===== 突破監控執行結果 =====")
    print(
        "建立基準："
        f"{action_counts.get('initialized', 0)}"
    )
    print(
        "未發生突破："
        f"{action_counts.get('unchanged', 0)}"
    )
    print(
        "提醒成功："
        f"{action_counts.get('alert_sent', 0)}"
    )
    print(
        "提醒失敗："
        f"{action_counts.get('alert_failed', 0)}"
    )
    print(
        "資料略過："
        f"{action_counts.get('skipped', 0)}"
    )
    print(
        "處理失敗："
        f"{action_counts.get('processing_failed', 0)}"
    )
    print("============================")

    failed_count = (
        action_counts.get(
            "alert_failed",
            0
        )
        + action_counts.get(
            "processing_failed",
            0
        )
    )

    if failed_count > 0:
        print(
            "⚠️ 部分股票處理失敗，"
            "其餘股票已繼續完成"
        )


def main():
    decisions = get_market_decisions()

    print("===== 市場時段判斷 =====")

    for decision in decisions.values():
        print_market_decision(decision)

    print("========================")

    due_decisions = {
        market: decision
        for market, decision
        in decisions.items()
        if decision.should_run
    }

    if not due_decisions:
        print(
            "ℹ️ 目前沒有市場需要執行，"
            "程式正常結束"
        )
        return

    store = SupabaseStore()
    price_provider = PriceProvider()
    market_data_provider = (
        MarketDataProvider()
    )
    notifier = DiscordNotifier()

    try:
        monitor_configs = (
            store.get_monitor_configs()
        )

        # 清理失敗不能影響本次監控
        try:
            store.prune_orphan_states(
                monitor_configs
            )
        except Exception as exc:
            print(
                "⚠️ 清理孤兒狀態列失敗，已略過："
                f"{type(exc).__name__}: {exc}"
            )

        due_configs = [
            config
            for config in monitor_configs
            if config["market"]
            in due_decisions
        ]

        print(
            "✅ 正式突破監控設定載入完成"
        )

        print(
            f"📈 本次監控設定數："
            f"{len(due_configs)}"
        )

        drawing_watchlist = (
            load_drawing_watchlist_safely(
                store
            )
        )

        prune_drawing_states_safely(
            store,
            drawing_watchlist
        )

        drawing_configs = (
            get_due_drawing_configs(
                drawing_watchlist,
                due_decisions
            )
        )

        if not due_configs and not drawing_configs:
            print(
                "ℹ️ 目前到執行時間的市場，"
                "沒有設定監控股票"
            )
            return

        quotes = fetch_due_quotes(
            price_provider,
            due_configs,
            drawing_configs,
            due_decisions
        )

        try:
            if due_configs:
                run_moving_average_alerts(
                    store=store,
                    notifier=notifier,
                    market_data_provider=(
                        market_data_provider
                    ),
                    due_configs=due_configs,
                    quotes=quotes,
                    due_decisions=due_decisions
                )
            else:
                print(
                    "ℹ️ 沒有到執行時間的均線監控設定，"
                    "只檢查 Atlas 畫線"
                )

        finally:
            # 均線流程的例外照常往外拋（原本行為），
            # 但畫線提醒仍會先執行。
            if drawing_configs:
                run_drawing_alerts(
                    store,
                    notifier,
                    drawing_watchlist,
                    quotes,
                    due_decisions
                )

    finally:
        notifier.close()
        price_provider.close()
        store.close()


if __name__ == "__main__":
    try:
        main()

    except requests.Timeout:
        print("❌ 外部服務連線逾時")
        sys.exit(1)

    except requests.RequestException as exc:
        print(
            "❌ 網路請求失敗："
            f"{type(exc).__name__}: {exc}"
        )
        sys.exit(1)

    except RuntimeError as exc:
        print(f"❌ {exc}")
        sys.exit(1)

    except Exception as exc:
        print(
            "❌ 未預期錯誤："
            f"{type(exc).__name__}: {exc}"
        )
        sys.exit(1)

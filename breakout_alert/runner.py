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

        if not due_configs:
            print(
                "ℹ️ 目前到執行時間的市場，"
                "沒有設定監控股票"
            )
            return

        quotes = price_provider.get_due_quotes(
            due_configs,
            due_decisions
        )

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

        print(
            f"💹 成功取得最新價："
            f"{len(quotes)}/"
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

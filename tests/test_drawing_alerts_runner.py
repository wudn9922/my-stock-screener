"""Runner integration for Atlas drawing alerts.

The real price provider, market clock and market data provider import
yfinance / exchange_calendars and talk to the network, so they are replaced
with stub modules before breakout_alert.runner is imported and removed again
afterwards.
"""

import os
import sys
import types
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

STUBBED = (
    "breakout_alert.market_clock",
    "breakout_alert.price_provider",
    "breakout_alert.market_data_provider",
)
_saved_modules = {}
runner = None
drawing_alerts = None

NOW_TS = 1791504000


def setUpModule():
    global runner, drawing_alerts
    for name in STUBBED + ("breakout_alert.runner",):
        _saved_modules[name] = sys.modules.pop(name, None)
    clock = types.ModuleType("breakout_alert.market_clock")
    clock.get_market_decisions = lambda: {}
    price = types.ModuleType("breakout_alert.price_provider")
    price.PriceProvider = object
    market_data = types.ModuleType("breakout_alert.market_data_provider")
    market_data.MarketDataProvider = object
    sys.modules["breakout_alert.market_clock"] = clock
    sys.modules["breakout_alert.price_provider"] = price
    sys.modules["breakout_alert.market_data_provider"] = market_data
    import breakout_alert.runner as runner_module
    import breakout_alert.drawing_alerts as drawing_module
    runner = runner_module
    drawing_alerts = drawing_module


def tearDownModule():
    for name in STUBBED + ("breakout_alert.runner",):
        sys.modules.pop(name, None)
        if _saved_modules.get(name) is not None:
            sys.modules[name] = _saved_modules[name]


def decision(market, should_run=True):
    return SimpleNamespace(
        market=market,
        session_type="regular" if should_run else "closed",
        should_run=should_run,
        reason="test",
        session_date="2026-10-09",
    )


def horizontal(symbol, drawing_id, price):
    return {
        "id": drawing_id, "symbol": symbol, "type": "horizontal", "visible": True,
        "points": [{"time": NOW_TS, "logical": 1, "price": price, "timeframe": "1D"}],
        "scope": {"timeframes": ["1D"]}, "style": {},
    }


class World:
    """Shared fakes for one runner.main() call."""

    def __init__(self, configs, drawing_rows):
        self.configs = configs
        self.drawing_rows = drawing_rows
        self.drawings_error = None
        self.quote_calls = []
        self.quote_error_with_drawings = False
        self.analytics_calls = []
        self.engine_calls = []
        self.pruned_drawing_states = []
        self.state_rows = {}
        self.messages = []
        self.closed = []
        world = self

        class Store:
            def get_monitor_configs(self):
                return list(world.configs)

            def prune_orphan_states(self, configs):
                return 0

            def get_atlas_drawings(self):
                if world.drawings_error:
                    raise world.drawings_error
                return world.drawing_rows

            def prune_drawing_states(self, valid):
                world.pruned_drawing_states.append(set(valid))
                return 0

            def get_drawing_states(self):
                return list(world.state_rows.values())

            def get_drawing_state_fallback(self):
                return None

            def upsert_drawing_states(self, payloads):
                for payload in payloads:
                    world.state_rows[payload["ticker"]] = payload

            def save_drawing_state_fallback(self, state):
                raise AssertionError("fallback not expected")

            def close(self):
                world.closed.append("store")

        class Prices:
            def get_due_quotes(self, configs, decisions):
                tickers = [c["ticker"] for c in configs]
                world.quote_calls.append(tickers)
                ma_tickers = {c["ticker"] for c in world.configs}
                if world.quote_error_with_drawings and set(tickers) - ma_tickers:
                    raise RuntimeError("quote batch exploded")
                return {
                    t: SimpleNamespace(price=100.0, display_name="", source="test")
                    for t in tickers
                }

            def close(self):
                world.closed.append("prices")

        class MarketData:
            def build_analytics(self, configs, quotes, decisions):
                world.analytics_calls.append([c["ticker"] for c in configs])
                return {c["ticker"]: object() for c in configs}

        class Engine:
            def __init__(self, store, notifier):
                pass

            def process_all(self, *, monitor_configs, quotes, analytics, due_decisions):
                world.engine_calls.append([c["ticker"] for c in monitor_configs])
                return []

        class Notifier:
            def send_drawing_breakout(self, content):
                world.messages.append(content)
                return True

            def close(self):
                world.closed.append("notifier")

        self.patches = {
            "get_market_decisions": lambda: {"TW": decision("TW"), "US": decision("US", False)},
            "SupabaseStore": Store,
            "PriceProvider": Prices,
            "MarketDataProvider": MarketData,
            "BreakoutEngine": Engine,
            "DiscordNotifier": Notifier,
        }


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.originals = {}

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(runner, name, value)

    def patch(self, world):
        for name, value in world.patches.items():
            self.originals.setdefault(name, getattr(runner, name))
            setattr(runner, name, value)

    def patch_drawing(self, name, value):
        original = getattr(drawing_alerts, name)
        setattr(drawing_alerts, name, value)
        self.addCleanup(setattr, drawing_alerts, name, original)

    def ma_config(self, ticker="2330.TW", market="TW"):
        return {"ticker": ticker, "market": market, "group_id": "tw_g1", "group_name": "x", "ma_list": [20]}

    def test_drawing_engine_crash_does_not_break_ma_flow(self):
        world = World([self.ma_config()], [{"ticker": "2317.TW", "drawings": [horizontal("2317.TW", "a", 100)]}])
        self.patch(world)

        class Exploding:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("boom")

        self.patch_drawing("DrawingAlertEngine", Exploding)
        runner.main()
        self.assertEqual(world.engine_calls, [["2330.TW"]])
        self.assertEqual(world.analytics_calls, [["2330.TW"]])
        self.assertEqual(world.quote_calls, [["2330.TW", "2317.TW"]])
        self.assertEqual(sorted(world.closed), ["notifier", "prices", "store"])

    def test_drawing_load_failure_keeps_ma_flow(self):
        world = World([self.ma_config()], [])
        world.drawings_error = RuntimeError("supabase down")
        self.patch(world)
        runner.main()
        self.assertEqual(world.quote_calls, [["2330.TW"]])
        self.assertEqual(world.engine_calls, [["2330.TW"]])
        self.assertEqual(world.pruned_drawing_states, [])

    def test_only_drawings_still_runs(self):
        world = World([], [{"ticker": "2317.TW", "drawings": [horizontal("2317.TW", "a", 100)]}])
        self.patch(world)
        runner.main()
        self.assertEqual(world.quote_calls, [["2317.TW"]])
        self.assertEqual(world.engine_calls, [])
        self.assertEqual(world.analytics_calls, [])
        self.assertIn("2317.TW", world.state_rows)
        self.assertEqual(world.pruned_drawing_states, [{"2317.TW"}])

    def test_combined_quote_failure_falls_back_to_ma_quotes(self):
        world = World([self.ma_config()], [{"ticker": "2317.TW", "drawings": [horizontal("2317.TW", "a", 100)]}])
        world.quote_error_with_drawings = True
        self.patch(world)
        runner.main()
        self.assertEqual(world.quote_calls, [["2330.TW", "2317.TW"], ["2330.TW"]])
        self.assertEqual(world.engine_calls, [["2330.TW"]])
        self.assertEqual(world.state_rows, {})

    def test_markets_not_due_are_not_quoted(self):
        world = World([self.ma_config()], [{"ticker": "AAPL", "drawings": [horizontal("AAPL", "a", 100)]}])
        self.patch(world)
        runner.main()
        self.assertEqual(world.quote_calls, [["2330.TW"]])
        # Pruning uses every market's tickers, not just the due ones.
        self.assertEqual(world.pruned_drawing_states, [{"AAPL"}])

    def test_no_prune_without_drawing_rows(self):
        world = World([self.ma_config()], [])
        self.patch(world)
        runner.main()
        self.assertEqual(world.pruned_drawing_states, [])

    def test_disabled_switch(self):
        world = World([self.ma_config()], [{"ticker": "2317.TW", "drawings": [horizontal("2317.TW", "a", 100)]}])
        self.patch(world)
        self.patch_drawing("DRAWING_ALERTS_ENABLED", False)
        runner.main()
        self.assertEqual(world.quote_calls, [["2330.TW"]])
        self.assertEqual(world.state_rows, {})
        self.assertEqual(world.pruned_drawing_states, [])

    def test_ma_failure_still_propagates_after_drawings_run(self):
        world = World([self.ma_config()], [{"ticker": "2317.TW", "drawings": [horizontal("2317.TW", "a", 100)]}])
        self.patch(world)

        class BrokenMarketData:
            def build_analytics(self, configs, quotes, decisions):
                raise RuntimeError("analytics down")

        runner.MarketDataProvider = BrokenMarketData
        with self.assertRaises(RuntimeError):
            runner.main()
        self.assertIn("2317.TW", world.state_rows)
        self.assertEqual(sorted(world.closed), ["notifier", "prices", "store"])

    def test_nothing_to_do_returns_early(self):
        world = World([], [])
        self.patch(world)
        runner.main()
        self.assertEqual(world.quote_calls, [])
        self.assertEqual(sorted(world.closed), ["notifier", "prices", "store"])


if __name__ == "__main__":
    unittest.main()

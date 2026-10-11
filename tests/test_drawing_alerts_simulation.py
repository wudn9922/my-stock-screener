"""Deterministic multi-run simulations of the Atlas drawing alert engine.

Each test drives DrawingAlertEngine through many consecutive runs with the
in-memory fakes from test_drawing_alerts and checks invariants that protect
against Discord spam and wrong-direction or duplicate alerts.
"""

import os
import random
import sys
import unittest
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
for path in (ROOT, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

from test_drawing_alerts import (  # noqa: E402
    NOW,
    FakeNotifier,
    FakeStore,
    SupabaseRequestError,
    da,
    horizontal,
    run,
    watchlist_for,
)


def make_engine():
    store = FakeStore()
    notifier = FakeNotifier()
    return store, notifier, da.DrawingAlertEngine(store=store, notifier=notifier)


def tw_watchlist(*lines, ticker="2330.TW"):
    return watchlist_for([{"ticker": ticker, "drawings": [
        horizontal(drawing_id, price, symbol=ticker) for drawing_id, price in lines]}])


def up_count(messages):
    return sum(message.count("向上突破") for message in messages)


class RandomWalkTest(unittest.TestCase):
    def simulate(self, store, notifier, engine, seed):
        rng = random.Random(seed)
        wl = tw_watchlist(("a", 100.0), ("b", 103.0))
        price = 100.0
        seen = {}
        for day in range(4):
            session = f"2026-10-{12 + day:02d}"
            for step in range(55):
                price = max(1.0, price * (1 + rng.uniform(-0.007, 0.007)))
                before = len(notifier.messages)
                run(engine, wl, {"2330.TW": price},
                    now=NOW + timedelta(days=day, minutes=5 * step), session_date=session)
                for message in notifier.messages[before:]:
                    for block in message.split("\n\n"):
                        if "畫線\n" not in block:
                            continue
                        up = "向上突破" in block
                        level = 100.0 if "水平線 100.00" in block else 103.0
                        # direction always matches where the price is now
                        if up:
                            self.assertGreaterEqual(price, level * (1 + da.LINE_UP_RATIO))
                        else:
                            self.assertLessEqual(price, level * (1 - da.LINE_DOWN_RATIO))
                        key = (session, level, up)
                        seen[key] = seen.get(key, 0) + 1
        return seen

    def test_at_most_one_alert_per_line_direction_and_session(self):
        for seed in range(40):
            store, notifier, engine = make_engine()
            seen = self.simulate(store, notifier, engine, seed)
            self.assertTrue(all(count == 1 for count in seen.values()), (seed, seen))

    def test_same_bound_when_only_the_fallback_store_works(self):
        for seed in range(20):
            store, notifier, engine = make_engine()
            store.upsert_error = SupabaseRequestError("check violation", status_code=400)
            seen = self.simulate(store, notifier, engine, seed)
            self.assertEqual(store.rows, {})
            self.assertTrue(all(count == 1 for count in seen.values()), (seed, seen))


class SessionBoundaryTest(unittest.TestCase):
    def test_pending_from_previous_session_needs_two_runs_again(self):
        store, notifier, engine = make_engine()
        wl = tw_watchlist(("a", 100.0))
        run(engine, wl, {"2330.TW": 98}, session_date="2026-10-08")
        # last run of the day crosses once, then the market closes
        self.assertEqual(run(engine, wl, {"2330.TW": 101}, session_date="2026-10-08")["pending"], 1)
        # the opening print alone must not confirm yesterday's single crossing
        next_day = NOW + timedelta(days=1)
        summary = run(engine, wl, {"2330.TW": 101.5}, now=next_day, session_date="2026-10-09")
        self.assertEqual(summary.get("pending"), 1)
        self.assertEqual(notifier.messages, [])
        summary = run(engine, wl, {"2330.TW": 101.5},
                      now=next_day + timedelta(minutes=1), session_date="2026-10-09")
        self.assertEqual(summary.get("alert_sent"), 1)
        self.assertEqual(up_count(notifier.messages), 1)

    def test_two_markets_keep_separate_state(self):
        store, notifier, engine = make_engine()
        wl = watchlist_for([
            {"ticker": "2330.TW", "drawings": [horizontal("t", 100.0)]},
            {"ticker": "AAPL", "drawings": [horizontal("u", 200.0, symbol="AAPL")]},
        ])
        run(engine, wl, {"2330.TW": 98, "AAPL": 190}, markets=("TW",))
        self.assertEqual(set(store.rows), {"2330.TW"})
        # US runs do not touch the TW row and vice versa
        for _ in range(3):
            run(engine, wl, {"2330.TW": 150, "AAPL": 190}, markets=("US",))
        self.assertEqual(store.lines("2330.TW")["t#h"]["side"], "below")
        self.assertEqual(notifier.messages, [])


class DeliveryTest(unittest.TestCase):
    def test_deferred_and_failed_alerts_are_each_delivered_once(self):
        store, notifier, engine = make_engine()
        wl = tw_watchlist(*[(f"l{i}", 100.0 + i) for i in range(14)])
        run(engine, wl, {"2330.TW": 90})
        run(engine, wl, {"2330.TW": 130})
        notifier.fail = True
        run(engine, wl, {"2330.TW": 130})
        self.assertEqual(notifier.messages, [])
        notifier.fail = False
        for _ in range(4):
            run(engine, wl, {"2330.TW": 130})
        self.assertEqual(up_count(notifier.messages), 14)
        for index in range(14):
            self.assertEqual(sum(f"水平線 {100 + index:,.2f}" in m for m in notifier.messages), 1)

    def test_moving_or_reshowing_a_line_across_the_price_never_alerts(self):
        store, notifier, engine = make_engine()
        run(engine, tw_watchlist(("a", 100.0)), {"2330.TW": 98})
        for _ in range(3):
            run(engine, tw_watchlist(("a", 95.0)), {"2330.TW": 98})
        hidden = watchlist_for([{"ticker": "2330.TW", "drawings": [
            horizontal("a", 95.0, visible=False), horizontal("z", 500.0)]}])
        for price in (90, 90):
            run(engine, hidden, {"2330.TW": price})
        for _ in range(3):
            run(engine, tw_watchlist(("a", 95.0)), {"2330.TW": 90})
        self.assertEqual(notifier.messages, [])


if __name__ == "__main__":
    unittest.main()

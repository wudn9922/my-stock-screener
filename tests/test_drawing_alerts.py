"""Atlas drawing alerts (breakout_alert/drawing_alerts.py).

Offline only: no network, no yfinance, and the price provider / market clock
modules are never imported (they pull in yfinance and exchange_calendars).
"""

import copy
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from breakout_alert import drawing_alerts as da  # noqa: E402
from breakout_alert.discord_notifier import DiscordNotifier  # noqa: E402
from breakout_alert.supabase_store import (  # noqa: E402
    SupabaseRequestError,
    SupabaseStore,
)

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "drawings", "rows.json")
NOW_TS = 1791504000  # 2026-10-09T00:00:00Z
NOW = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)


def load_rows():
    with open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


def horizontal(drawing_id, price, symbol="2330.TW", visible=True, timeframe="1D"):
    return {
        "id": drawing_id,
        "symbol": symbol,
        "type": "horizontal",
        "points": [{"time": NOW_TS - 86400, "logical": 10, "price": price, "timeframe": timeframe}],
        "locked": False,
        "visible": visible,
        "scope": {"timeframes": [timeframe]},
        "style": {"color": "#fff", "lineWidth": 1},
    }


def watchlist_for(rows):
    return da.build_watchlist(rows, now_ts=NOW_TS)


def quote(price, name="台積電", source="twse_mis"):
    return SimpleNamespace(price=price, display_name=name, source=source)


def decision(session_date="2026-10-09"):
    return SimpleNamespace(session_type="regular", session_date=session_date)


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.fallback = None
        self.upsert_calls = []
        self.fallback_calls = []
        self.fail_read = False
        self.fail_fallback_read = False
        self.upsert_error = None
        self.fallback_error = None

    def get_drawing_states(self):
        if self.fail_read:
            raise RuntimeError("read failed")
        return [copy.deepcopy(row) for row in self.rows.values()]

    def get_drawing_state_fallback(self):
        if self.fail_fallback_read:
            raise RuntimeError("fallback read failed")
        return copy.deepcopy(self.fallback)

    def upsert_drawing_states(self, payloads):
        self.upsert_calls.append(copy.deepcopy(payloads))
        if self.upsert_error is not None:
            raise self.upsert_error
        for payload in payloads:
            self.rows[payload["ticker"]] = copy.deepcopy(payload)

    def save_drawing_state_fallback(self, state):
        self.fallback_calls.append(copy.deepcopy(state))
        if self.fallback_error is not None:
            raise self.fallback_error
        self.fallback = copy.deepcopy(state)

    def lines(self, ticker):
        return self.rows[ticker]["metadata"]["lines"]


class FakeNotifier:
    def __init__(self):
        self.messages = []
        self.fail = False

    def send_drawing_breakout(self, content):
        if self.fail:
            raise RuntimeError("discord down")
        self.messages.append(content)
        return True


def run(engine, wl, price_by_ticker, now=NOW, session_date="2026-10-09", markets=("TW", "US")):
    quotes = {ticker: quote(price) for ticker, price in price_by_ticker.items()}
    return engine.process(
        watchlist=wl,
        quotes=quotes,
        due_decisions={market: decision(session_date) for market in markets},
        now=now,
    )


class SymbolMappingTest(unittest.TestCase):
    def test_market_mapping(self):
        self.assertEqual(da.map_atlas_symbol("2330.TW"), ("2330.TW", "TW"))
        self.assertEqual(da.map_atlas_symbol("6488.two"), ("6488.TWO", "TW"))
        self.assertEqual(da.map_atlas_symbol("BRK.B"), ("BRK-B", "US"))
        self.assertEqual(da.map_atlas_symbol("AAPL"), ("AAPL", "US"))

    def test_skipped_symbols(self):
        for symbol in ["^TWII", "^GSPC", "000001.SS", "399001.SZ", "", None, "_ALERTSTATE", "2330.TW; DROP"]:
            self.assertIsNone(da.map_atlas_symbol(symbol), symbol)


class ExtractionTest(unittest.TestCase):
    def test_fixture_lines(self):
        wl = watchlist_for(load_rows())
        by_ticker = {entry.ticker: entry for entry in wl.entries}
        self.assertEqual(set(by_ticker), {"2330.TW", "BRK-B", "6488.TWO"})
        self.assertEqual(wl.row_count, 9)
        self.assertEqual(wl.valid_tickers, {"2330.TW", "BRK-B", "6488.TWO"})

        tsmc = by_ticker["2330.TW"]
        self.assertEqual(tsmc.market, "TW")
        self.assertEqual(tsmc.symbol, "2330.TW")
        lines = {line.key: line for line in tsmc.lines}
        self.assertEqual(
            set(lines),
            {"h1#h", "ray-right#h", "box-new#top", "box-new#bottom"},
        )
        self.assertEqual(lines["h1#h"].level, 1085.0)
        self.assertEqual(lines["h1#h"].label, "水平線")
        self.assertEqual(lines["h1#h"].timeframe, "1D")
        self.assertEqual(lines["ray-right#h"].level, 1120.0)
        self.assertEqual(lines["ray-right#h"].label, "水平射線")
        self.assertEqual(lines["ray-right#h"].timeframe, "1W")
        self.assertEqual(lines["box-new#top"].level, 1070.0)
        self.assertEqual(lines["box-new#top"].label, "箱頂")
        self.assertEqual(lines["box-new#top"].description, "矩形上緣")
        self.assertEqual(lines["box-new#bottom"].level, 1040.0)
        self.assertEqual(lines["box-new#bottom"].label, "箱底")
        self.assertEqual(lines["box-new#bottom"].description, "矩形下緣")

        brk = by_ticker["BRK-B"]
        self.assertEqual((brk.market, brk.symbol), ("US", "BRK.B"))
        self.assertEqual([line.level for line in brk.lines], [480.5])
        self.assertEqual(by_ticker["6488.TWO"].market, "TW")

    def test_fibonacci_only_when_enabled(self):
        fib = next(d for d in load_rows()[0]["drawings"] if isinstance(d, dict) and d.get("id") == "fib1")
        self.assertEqual(da.extract_alert_lines(fib, "2330.TW", NOW_TS), [])
        lines = da.extract_alert_lines(fib, "2330.TW", NOW_TS, fib_alerts=True)
        self.assertEqual([line.key for line in lines], ["fib1#fib:0.382", "fib1#fib:0.618"])
        self.assertAlmostEqual(lines[0].level, 1000 + 200 * 0.382)
        self.assertAlmostEqual(lines[1].level, 1000 + 200 * 0.618)
        self.assertEqual(lines[1].label, "Fib 0.618")

    def test_hidden_line_is_alert_off(self):
        self.assertEqual(da.extract_alert_lines(horizontal("x", 100, visible=False), "2330.TW", NOW_TS), [])

    def test_ray_direction(self):
        drawing = {
            "id": "r", "symbol": "AAPL", "type": "ray", "visible": True,
            "points": [
                {"time": 100, "logical": 1, "price": 200.0, "timeframe": "1H"},
                {"time": 100, "logical": 1, "price": 200.0, "timeframe": "1H"},
            ],
        }
        self.assertEqual(len(da.extract_alert_lines(drawing, "AAPL", NOW_TS)), 1)
        drawing["points"][1]["time"] = 99
        self.assertEqual(da.extract_alert_lines(drawing, "AAPL", NOW_TS), [])

    def test_rectangle_age_boundary(self):
        def rect(right_time):
            return {
                "id": "b", "symbol": "AAPL", "type": "rectangle", "visible": True,
                "points": [
                    {"time": right_time, "logical": 1, "price": 10.0, "timeframe": "1D"},
                    {"time": right_time - 86400 * 200, "logical": 1, "price": 12.0, "timeframe": "1D"},
                ],
            }

        self.assertEqual(len(da.extract_alert_lines(rect(NOW_TS - 60 * 86400), "AAPL", NOW_TS)), 2)
        self.assertEqual(da.extract_alert_lines(rect(NOW_TS - 60 * 86400 - 1), "AAPL", NOW_TS), [])
        # Millisecond timestamps are tolerated.
        self.assertEqual(len(da.extract_alert_lines(rect((NOW_TS - 86400) * 1000), "AAPL", NOW_TS)), 2)

    def test_malformed_rows_never_raise(self):
        weird = [None, 1, "x", {"ticker": "AAPL"}, {"ticker": "AAPL", "drawings": [{"id": 5}]},
                 {"ticker": "AAPL", "drawings": [{"id": "a", "symbol": "AAPL", "type": "horizontal",
                                                   "visible": True, "points": [None]}]},
                 {"ticker": "AAPL", "drawings": [{"id": "a", "symbol": "AAPL", "type": "horizontal",
                                                   "visible": True, "points": [{"price": float("nan")}]}]}]
        wl = da.build_watchlist(weird, now_ts=NOW_TS)
        self.assertEqual(wl.entries, [])
        self.assertEqual(da.build_watchlist(None, now_ts=NOW_TS).entries, [])

    def test_load_drawing_watchlist_uses_store(self):
        store = SimpleNamespace(get_atlas_drawings=lambda: [{"ticker": "AAPL", "drawings": [horizontal("a", 100, "AAPL")]}])
        wl = da.load_drawing_watchlist(store, now_ts=NOW_TS)
        self.assertEqual([entry.ticker for entry in wl.entries], ["AAPL"])


class LineStateMachineTest(unittest.TestCase):
    def setUp(self):
        self.line = da.AlertLine(key="a#h", drawing_id="a", level=100.0, label="水平線",
                                 description="水平線", drawing_type="horizontal", timeframe="1D")

    def step(self, previous, price, session="2026-10-09", line=None):
        return da.advance_line(previous, line or self.line, price, now_iso="T", session_date=session)

    def test_zones(self):
        self.assertEqual(da.classify_zone(100.5, 100), "above")
        self.assertEqual(da.classify_zone(99.5, 100), "below")
        self.assertEqual(da.classify_zone(100.49, 100), "middle")
        self.assertEqual(da.classify_zone(99.51, 100), "middle")
        self.assertIsNone(da.classify_zone(None, 100))
        self.assertIsNone(da.classify_zone(100, 0))

    def test_baseline_uses_raw_side_inside_buffer(self):
        state, event = self.step(None, 100.2)
        self.assertEqual((state["side"], event), ("above", "baseline"))
        state, event = self.step(None, 99.9)
        self.assertEqual((state["side"], event), ("below", "baseline"))
        self.assertIsNone(state["pending"])
        self.assertEqual(state["level"], 100.0)

    def test_two_run_confirmation(self):
        state, _ = self.step(None, 98)
        state, event = self.step(state, 101)
        self.assertEqual(event, "pending")
        self.assertEqual(state["side"], "below")
        self.assertEqual(state["pending"]["count"], 1)
        state, event = self.step(state, 101.5)
        self.assertEqual(event, "alert")
        self.assertEqual(state["side"], "above")
        self.assertIsNone(state["pending"])
        self.assertEqual(state["lastAlertDirection"], "breakout_up")
        self.assertEqual(state["lastAlertSession"], "2026-10-09")
        state, event = self.step(state, 102)
        self.assertEqual(event, "unchanged")

    def test_middle_zone_keeps_side_and_resets_pending(self):
        state, _ = self.step(None, 98)
        state, event = self.step(state, 101)
        self.assertEqual(event, "pending")
        state, event = self.step(state, 100.1)
        self.assertEqual(event, "unchanged")
        self.assertEqual(state["side"], "below")
        self.assertIsNone(state["pending"])
        state, event = self.step(state, 101)
        self.assertEqual(event, "pending")

    def test_daily_cooldown_and_hysteresis(self):
        state, _ = self.step(None, 98)
        state, _ = self.step(state, 101)
        state, event = self.step(state, 101)
        self.assertEqual(event, "alert")
        # Back below the lower buffer: a down alert is allowed (different direction).
        state, _ = self.step(state, 99)
        state, event = self.step(state, 99)
        self.assertEqual(event, "alert")
        self.assertEqual(state["lastAlertDirection"], "breakout_down")
        # Each direction alerts at most once per trading day.
        state, _ = self.step(state, 101)
        state, event = self.step(state, 101)
        self.assertEqual(event, "suppressed")
        self.assertEqual(state["side"], "above")
        state, _ = self.step(state, 99)
        state, event = self.step(state, 99)
        self.assertEqual(event, "suppressed")
        self.assertEqual(state["side"], "below")
        # Next trading day the same direction can alert again.
        state, _ = self.step(state, 101, session="2026-10-12")
        state, event = self.step(state, 101, session="2026-10-12")
        self.assertEqual(event, "alert")

    def test_hysteresis_needs_the_far_buffer(self):
        state, _ = self.step(None, 98)
        state, _ = self.step(state, 101)
        state, event = self.step(state, 101)
        self.assertEqual(event, "alert")
        # Dipping into the middle zone and back never re-arms anything.
        for price in (100.2, 99.6, 100.4, 101, 99.7, 101):
            state, event = self.step(state, price, session="2026-10-12")
            self.assertEqual(event, "unchanged", price)
            self.assertEqual(state["side"], "above")

    def test_moved_line_rebaselines(self):
        state, _ = self.step(None, 98)
        state, _ = self.step(state, 101)
        moved = da.AlertLine(key="a#h", drawing_id="a", level=95.0, label="水平線",
                             description="水平線", drawing_type="horizontal", timeframe="1D")
        state, event = self.step(state, 101, line=moved)
        self.assertEqual(event, "baseline")
        self.assertEqual(state["side"], "above")
        self.assertEqual(state["level"], 95.0)
        self.assertIsNone(state["pending"])
        # Tiny float noise is not a move.
        same = da.AlertLine(key="a#h", drawing_id="a", level=95.0 + 1e-9, label="水平線",
                            description="水平線", drawing_type="horizontal", timeframe="1D")
        _, event = self.step(state, 101, line=same)
        self.assertEqual(event, "unchanged")

    def test_gap_still_needs_confirmation(self):
        state, _ = self.step(None, 80)
        state, event = self.step(state, 130)
        self.assertEqual(event, "pending")
        state, event = self.step(state, 131)
        self.assertEqual(event, "alert")

    def test_invalid_price_is_skip(self):
        state, _ = self.step(None, 98)
        new_state, event = self.step(state, None)
        self.assertEqual(event, "skipped")
        self.assertEqual(new_state, state)

    def test_corrupt_previous_state_rebaselines(self):
        state, event = self.step({"level": 100.0, "side": "sideways"}, 101)
        self.assertEqual(event, "baseline")
        state, event = self.step("garbage", 101)
        self.assertEqual(event, "baseline")


class MessageFormatTest(unittest.TestCase):
    def test_single_message(self):
        line = da.AlertLine(key="b#top", drawing_id="b", level=1085.0, label="箱頂",
                            description="矩形上緣", drawing_type="rectangle", timeframe="1D")
        text = da.format_alert_block(
            ticker="2330.TW", symbol="2330.TW", display_name="台積電", line=line,
            price=1092.0, direction="breakout_up", nearest_above=1120.0, nearest_below=1040.0,
        )
        self.assertEqual(
            text,
            "📐 2330.TW 台積電｜Atlas 畫線\n"
            "🚨 向上突破 箱頂 1,085.00｜現價 1,092.00（+0.65%）\n"
            "週期 1D・矩形上緣｜確認：連續 2 次\n"
            "上方最近畫線 1,120.00（+2.6%）｜下方 1,040.00（−4.8%）\n"
            "https://liff.line.me/2010330411-6JhrotT9?page=chart&symbol=2330.TW&tf=1D",
        )

    def test_down_message_without_neighbours(self):
        line = da.AlertLine(key="r#h", drawing_id="r", level=480.5, label="水平射線",
                            description="水平射線", drawing_type="ray", timeframe="1W")
        text = da.format_alert_block(
            ticker="BRK-B", symbol="BRK.B", display_name="", line=line,
            price=470.0, direction="breakout_down", nearest_above=None, nearest_below=None,
        )
        self.assertEqual(
            text,
            "📐 BRK-B｜Atlas 畫線\n"
            "⚠️ 向下跌破 水平射線 480.50｜現價 470.00（−2.19%）\n"
            "週期 1W・水平射線｜確認：連續 2 次\n"
            "上方最近畫線 無｜下方 無\n"
            "https://liff.line.me/2010330411-6JhrotT9?page=chart&symbol=BRK.B&tf=1W",
        )

    def test_packing(self):
        blocks = [f"block {i}" for i in range(3)]
        self.assertEqual(da.pack_alert_messages(blocks), [("block 0", [0]), ("block 1", [1]), ("block 2", [2])])
        blocks = [("x" * 600) + str(i) for i in range(5)]
        packed = da.pack_alert_messages(blocks)
        self.assertGreater(len(packed), 1)
        self.assertEqual(sorted(i for _, idx in packed for i in idx), [0, 1, 2, 3, 4])
        for content, _ in packed:
            self.assertLessEqual(len(content), 2000)
            self.assertTrue(content.startswith("📐 Atlas 畫線提醒"))


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.notifier = FakeNotifier()
        self.engine = da.DrawingAlertEngine(store=self.store, notifier=self.notifier)
        self.wl = watchlist_for([{"ticker": "2330.TW", "drawings": [horizontal("h1", 100.0)]}])

    def cycle(self, price, **kwargs):
        return run(self.engine, self.wl, {"2330.TW": price}, **kwargs)

    def test_exactly_one_alert(self):
        summary = self.cycle(98)
        self.assertEqual(summary["baseline"], 1)
        self.assertEqual(self.notifier.messages, [])
        row = self.store.rows["2330.TW"]
        self.assertEqual(row["group_id"], "atlas_lines")
        self.assertEqual(row["ma_period"], 1)
        self.assertEqual(row["previous_side"], "unknown")
        self.assertEqual(row["market"], "TW")
        self.assertEqual(row["session_type"], "regular")
        self.assertEqual(row["last_price"], 98)
        self.assertEqual(row["metadata"]["kind"], "atlas_lines")
        self.assertEqual(row["metadata"]["v"], 1)
        self.assertEqual(self.store.lines("2330.TW")["h1#h"]["side"], "below")

        self.assertEqual(self.cycle(101)["pending"], 1)
        self.assertEqual(self.notifier.messages, [])
        summary = self.cycle(101.2)
        self.assertEqual(summary["alert_sent"], 1)
        self.assertEqual(len(self.notifier.messages), 1)
        self.assertIn("向上突破 水平線 100.00", self.notifier.messages[0])
        for _ in range(3):
            self.cycle(101.5)
        self.assertEqual(len(self.notifier.messages), 1)

    def test_no_write_when_nothing_changed(self):
        self.cycle(98)
        self.cycle(97)
        self.cycle(96)
        self.assertEqual(len(self.store.upsert_calls), 1)

    def test_notifier_failure_keeps_state_and_retries(self):
        self.cycle(98)
        self.cycle(101)
        before = copy.deepcopy(self.store.lines("2330.TW")["h1#h"])
        self.notifier.fail = True
        summary = self.cycle(101)
        self.assertEqual(summary["alert_failed"], 1)
        self.assertEqual(self.store.lines("2330.TW")["h1#h"], before)
        self.notifier.fail = False
        summary = self.cycle(101)
        self.assertEqual(summary["alert_sent"], 1)
        self.assertEqual(len(self.notifier.messages), 1)

    def test_state_read_failure_sends_nothing(self):
        self.cycle(98)
        self.cycle(101)
        self.store.fail_read = True
        summary = self.cycle(101)
        self.assertEqual(summary["state_failed"], 1)
        self.assertEqual(self.notifier.messages, [])
        self.store.fail_read = False
        self.store.fail_fallback_read = True
        self.cycle(101)
        self.assertEqual(self.notifier.messages, [])

    def test_primary_4xx_uses_fallback(self):
        self.store.upsert_error = SupabaseRequestError("bad", status_code=400)
        self.cycle(98)
        self.assertEqual(self.store.rows, {})
        self.assertEqual(self.store.fallback["kind"], "atlas_lines_state")
        self.assertEqual(self.store.fallback["tickers"]["2330.TW"]["lines"]["h1#h"]["side"], "below")
        self.cycle(101)
        self.cycle(101)
        self.assertEqual(len(self.notifier.messages), 1)
        self.cycle(101)
        self.assertEqual(len(self.notifier.messages), 1)

    def test_newer_state_wins_between_primary_and_fallback(self):
        self.cycle(98)  # primary row, side below
        self.store.upsert_error = SupabaseRequestError("bad", status_code=409)
        self.cycle(101)
        self.cycle(101)  # alert recorded only in fallback
        self.assertEqual(len(self.notifier.messages), 1)
        self.store.upsert_error = None
        self.cycle(101)
        self.cycle(101)
        self.assertEqual(len(self.notifier.messages), 1)

    def test_stale_fallback_record_rebaselines(self):
        self.store.upsert_error = SupabaseRequestError("bad", status_code=400)
        old = datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc)
        self.cycle(98, now=old)
        self.cycle(101, now=old)
        self.assertEqual(self.store.fallback["tickers"]["2330.TW"]["lines"]["h1#h"]["pending"]["count"], 1)
        # Eight days later the old fallback record is ignored: baseline, no alert.
        summary = self.cycle(101)
        self.assertEqual(summary["baseline"], 1)
        self.assertEqual(self.notifier.messages, [])
        self.assertEqual(self.store.fallback["tickers"]["2330.TW"]["lines"]["h1#h"]["side"], "above")

    def test_both_writes_fail_sends_nothing(self):
        self.cycle(98)
        self.cycle(101)
        self.store.upsert_error = SupabaseRequestError("bad", status_code=400)
        self.store.fallback_error = SupabaseRequestError("bad", status_code=400)
        summary = self.cycle(101)
        self.assertEqual(self.notifier.messages, [])
        self.assertEqual(summary["blocked"], 1)

    def test_server_error_does_not_use_fallback(self):
        self.cycle(98)
        self.cycle(101)
        self.store.upsert_error = SupabaseRequestError("down", status_code=503)
        self.cycle(101)
        self.assertEqual(self.store.fallback_calls, [])
        self.assertEqual(self.notifier.messages, [])

    def test_market_not_due_is_ignored(self):
        summary = self.cycle(98, markets=("US",))
        self.assertEqual(self.store.upsert_calls, [])
        self.assertEqual(summary.get("baseline", 0), 0)

    def test_missing_quote_is_skipped(self):
        summary = run(self.engine, self.wl, {})
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(self.store.upsert_calls, [])

    def test_removed_line_is_dropped_from_state(self):
        self.cycle(98)
        self.wl = watchlist_for([{"ticker": "2330.TW", "drawings": [horizontal("h2", 90.0)]}])
        self.cycle(98)
        self.assertEqual(set(self.store.lines("2330.TW")), {"h2#h"})

    def test_combined_and_capped_messages(self):
        drawings = [horizontal(f"h{i}", 100.0 + i * 0.01) for i in range(12)]
        self.wl = watchlist_for([{"ticker": "2330.TW", "drawings": drawings}])
        self.cycle(90)
        self.cycle(120)
        summary = self.cycle(120)
        self.assertEqual(summary["alert_sent"], 10)
        self.assertEqual(summary["deferred"], 2)
        self.assertTrue(self.notifier.messages)
        for content in self.notifier.messages:
            self.assertTrue(content.startswith("📐 Atlas 畫線提醒"))
            self.assertLessEqual(len(content), 2000)
        sent_text = "\n".join(self.notifier.messages)
        self.assertEqual(sent_text.count("向上突破"), 10)
        summary = self.cycle(120)
        self.assertEqual(summary["alert_sent"], 2)
        summary = self.cycle(120)
        self.assertEqual(summary.get("alert_sent", 0), 0)

    def test_nearest_lines_in_message(self):
        self.wl = watchlist_for([{"ticker": "2330.TW", "drawings": [
            horizontal("a", 100.0), horizontal("b", 110.0), horizontal("c", 90.0)]}])
        self.cycle(95)
        self.cycle(102)
        self.cycle(102)
        self.assertEqual(len(self.notifier.messages), 1)
        self.assertIn("上方最近畫線 110.00（+7.8%）｜下方 90.00（−11.8%）", self.notifier.messages[0])


class StoreFilterTest(unittest.TestCase):
    def make_store(self, responses):
        store = SupabaseStore.__new__(SupabaseStore)
        store.user_id = "U123"
        store.calls = []

        def fake_request(method, table_name, *, params=None, json_body=None, extra_headers=None):
            store.calls.append((method, table_name, params, json_body, extra_headers))
            return responses.pop(0) if responses else None

        store._request = fake_request
        return store

    def test_prune_orphan_states_ignores_atlas_rows(self):
        rows = [
            {"id": 1, "group_id": "tw_g1", "ticker": "2330.TW", "ma_period": 20},
            {"id": 2, "group_id": "tw_g1", "ticker": "2317.TW", "ma_period": 20},
            {"id": 3, "group_id": "atlas_lines", "ticker": "2330.TW", "ma_period": 1},
            {"id": 4, "group_id": "atlas_lines", "ticker": "AAPL", "ma_period": 1},
            {"id": 5, "group_id": "atlas_lines", "ticker": "NVDA", "ma_period": 1},
        ]
        store = self.make_store([rows, None])
        configs = [{"group_id": "tw_g1", "ticker": "2330.TW", "ma_list": [20]}]
        # Only the MA rows count: 1 orphan of 2 rows is within the 50% guard,
        # and the atlas_ rows are neither deleted nor counted as orphans.
        self.assertEqual(store.prune_orphan_states(configs), 1)
        delete = store.calls[1]
        self.assertEqual(delete[0], "DELETE")
        self.assertEqual(delete[2]["id"], "in.(2)")

    def test_cached_ticker_states_exclude_atlas_rows(self):
        store = self.make_store([[
            {"group_id": "tw_g1", "ticker": "2330.TW"},
            {"group_id": "atlas_lines", "ticker": "2330.TW"},
        ]])
        rows = store.get_cached_ticker_states("2330.tw")
        self.assertEqual(rows, [{"group_id": "tw_g1", "ticker": "2330.TW"}])
        self.assertEqual(store.calls[0][2]["group_id"], "not.like.atlas_*")

    def test_get_atlas_drawings_query(self):
        store = self.make_store([[{"ticker": "AAPL", "drawings": []}]])
        self.assertEqual(store.get_atlas_drawings(), [{"ticker": "AAPL", "drawings": []}])
        method, table, params, _, _ = store.calls[0]
        self.assertEqual((method, table), ("GET", "chart_drawings"))
        self.assertEqual(params["line_user_id"], "eq.U123")
        self.assertEqual(params["timeframe"], "eq.atlas")
        self.assertEqual(params["limit"], "2000")
        self.assertEqual(params["select"], "ticker,drawings,updated_at")

    def test_prune_drawing_states(self):
        store = self.make_store([[
            {"id": 7, "ticker": "2330.TW"},
            {"id": 8, "ticker": "AAPL"},
        ], None])
        self.assertEqual(store.prune_drawing_states({"2330.TW"}), 1)
        self.assertEqual(store.calls[0][2]["group_id"], "eq.atlas_lines")
        delete = store.calls[1]
        self.assertEqual(delete[0], "DELETE")
        self.assertEqual(delete[2]["id"], "in.(8)")
        self.assertEqual(delete[2]["group_id"], "eq.atlas_lines")

    def test_drawing_state_fallback_roundtrip(self):
        state = {"kind": "atlas_lines_state", "v": 1, "tickers": {}}
        store = self.make_store([None, [{"drawings": [state]}]])
        store.save_drawing_state_fallback(state)
        method, table, params, body, headers = store.calls[0]
        self.assertEqual((method, table), ("POST", "chart_drawings"))
        self.assertEqual(params["on_conflict"], "line_user_id,ticker,timeframe")
        self.assertEqual(body["ticker"], "_ALERTSTATE")
        self.assertEqual(body["timeframe"], "alert-state")
        self.assertEqual(body["drawings"], [state])
        self.assertEqual(store.get_drawing_state_fallback(), state)

    def test_upsert_drawing_states_bulk(self):
        store = self.make_store([None])
        store.upsert_drawing_states([{"ticker": "AAPL"}])
        method, table, params, body, headers = store.calls[0]
        self.assertEqual((method, table), ("POST", "breakout_alert_state"))
        self.assertEqual(params["on_conflict"], "line_user_id,group_id,ticker,ma_period")
        self.assertEqual(body, [{"ticker": "AAPL", "line_user_id": "U123"}])

    def test_request_error_keeps_runtime_error_contract(self):
        error = SupabaseRequestError("x", status_code=404)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(error.status_code, 404)


class NotifierTest(unittest.TestCase):
    def test_send_drawing_breakout_handles_429(self):
        responses = [
            SimpleNamespace(status_code=429, json=lambda: {"retry_after": 0}, text=""),
            SimpleNamespace(status_code=204, json=lambda: {}, text=""),
        ]
        posted = []

        class Session:
            def post(self, url, json, timeout):
                posted.append(json)
                return responses.pop(0)

        notifier = DiscordNotifier.__new__(DiscordNotifier)
        notifier.webhook_url = "https://discord.com/api/webhooks/x"
        notifier.session = Session()
        import breakout_alert.discord_notifier as module
        original_sleep = module.time.sleep
        module.time.sleep = lambda seconds: None
        try:
            self.assertTrue(notifier.send_drawing_breakout("hello"))
        finally:
            module.time.sleep = original_sleep
        self.assertEqual(len(posted), 2)
        self.assertEqual(posted[0]["content"], "hello")
        self.assertEqual(posted[0]["allowed_mentions"], {"parse": []})

    def test_send_drawing_breakout_raises_on_error(self):
        class Session:
            def post(self, url, json, timeout):
                return SimpleNamespace(status_code=500, json=lambda: {}, text="boom")

        notifier = DiscordNotifier.__new__(DiscordNotifier)
        notifier.webhook_url = "https://discord.com/api/webhooks/x"
        notifier.session = Session()
        with self.assertRaises(RuntimeError):
            notifier.send_drawing_breakout("hello")


class IsolationTest(unittest.TestCase):
    def test_module_does_not_import_price_or_clock(self):
        code = (
            "import sys; import breakout_alert.drawing_alerts; "
            "bad = [m for m in ('breakout_alert.price_provider', 'breakout_alert.market_clock', "
            "'yfinance', 'pandas', 'exchange_calendars') if m in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

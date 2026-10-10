"""
F2 US growth data: atlas/scripts/build_fundamentals_us.py (offline; handwritten Yahoo fixtures).

Run: python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "f2")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_fundamentals_us as growth  # noqa: E402

TODAY = date(2026, 10, 9)
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as file:
        return json.load(file)


# Previous run's AAPL record: older quarters that Yahoo no longer returns
PREVIOUS_Q = [
    ["2024-06-29", 1.40, 85777000000],
    ["2024-09-28", 0.97, 94930000000],
    ["2024-12-28", 2.40, 124300000000],
    ["2025-03-29", 1.65, 95359000000],
    ["2025-06-28", 1.50, 94036000000],
]


class ParseTest(unittest.TestCase):
    def test_parse_filters_currency_nulls_and_period(self):
        parsed = growth.parse_growth(fixture("yahoo_timeseries_aapl.json"))
        self.assertEqual(len(parsed["diluted"]), 5)
        self.assertEqual(parsed["diluted"]["2026-06-27"], 1.80)
        self.assertEqual(len(parsed["basic"]), 6)
        # EUR point dropped, empty currency kept, TTM point ignored
        self.assertNotIn("2024-12-28", parsed["revenue"])
        self.assertEqual(parsed["revenue"]["2025-12-27"], 143756000000)
        self.assertEqual(parsed["revenue"]["2026-06-27"], 103000000000)

    def test_foreign_currency_is_dropped(self):
        parsed = growth.parse_growth(fixture("yahoo_timeseries_foreign.json"))
        self.assertEqual(parsed, {"diluted": {}, "basic": {}, "revenue": {}})
        q, basis = growth.quarters_from_parsed(parsed)
        self.assertEqual(q, [])

    def test_empty_and_garbage(self):
        self.assertEqual(
            growth.parse_growth(fixture("yahoo_timeseries_empty.json")),
            {"diluted": {}, "basic": {}, "revenue": {}},
        )
        for payload in (None, {}, {"timeseries": None}, {"timeseries": {"result": [None, 5]}}):
            self.assertEqual(
                growth.parse_growth(payload), {"diluted": {}, "basic": {}, "revenue": {}}
            )

    def test_quarters_prefer_diluted_with_basic_fallback(self):
        q, basis = growth.quarters_from_parsed(
            growth.parse_growth(fixture("yahoo_timeseries_aapl.json"))
        )
        self.assertEqual(basis, "diluted")
        self.assertEqual([row[0] for row in q], sorted(row[0] for row in q))
        self.assertEqual(q[0], ["2025-03-29", 1.66, 95359000000])  # basic fallback
        self.assertEqual(q[-1], ["2026-06-27", 1.80, 103000000000])

    def test_basic_only(self):
        parsed = {"diluted": {}, "basic": {"2026-06-30": 0.5}, "revenue": {"2026-03-31": 10.0}}
        q, basis = growth.quarters_from_parsed(parsed)
        self.assertEqual(basis, "basic")
        self.assertEqual(q, [["2026-03-31", None, 10.0], ["2026-06-30", 0.5, None]])


class MergeAndYoyTest(unittest.TestCase):
    def new_quarters(self):
        return growth.quarters_from_parsed(
            growth.parse_growth(fixture("yahoo_timeseries_aapl.json"))
        )[0]

    def test_merge_accumulates_and_new_values_win(self):
        merged = growth.merge_quarters(PREVIOUS_Q, self.new_quarters())
        dates = [row[0] for row in merged]
        self.assertEqual(dates[0], "2024-06-29")
        self.assertEqual(len(dates), len(set(dates)))
        self.assertEqual(dates, sorted(dates))
        by_date = {row[0]: row for row in merged}
        self.assertEqual(by_date["2025-06-28"][1], 1.57)  # refreshed value replaces the stored one
        self.assertEqual(by_date["2025-03-29"][1], 1.66)

    def test_merge_keeps_old_value_when_new_is_missing(self):
        merged = growth.merge_quarters(
            [["2026-03-31", 1.0, 50.0]], [["2026-03-31", None, 55.0]]
        )
        self.assertEqual(merged, [["2026-03-31", 1.0, 55.0]])

    def test_merge_keeps_twelve_quarters(self):
        previous = [[f"{2020 + i // 4}-{(i % 4) * 3 + 3:02d}-28", 1.0, 1.0] for i in range(15)]
        merged = growth.merge_quarters(previous, [])
        self.assertEqual(len(merged), 12)
        self.assertEqual(merged[-1], previous[-1])

    def test_merge_ignores_malformed_rows(self):
        merged = growth.merge_quarters(
            [["bad"], None, ["2026-01-31", "x", 3], ["2026-02-30", 1, 1]], []
        )
        self.assertEqual(merged, [["2026-01-31", None, 3.0]])

    def test_yoy(self):
        q = growth.merge_quarters(PREVIOUS_Q, self.new_quarters())
        record = growth.build_growth_record(q, "diluted", "2026-10-09")
        self.assertEqual(record["latest"], "2026-06-27")
        self.assertEqual(record["checkedAt"], "2026-10-09")
        self.assertEqual(record["basis"], "diluted")
        self.assertEqual(record["q"], q)
        self.assertEqual(
            record["epsYoY"],
            [
                round((1.80 / 1.57 - 1) * 100, 1),
                round((1.65 / 1.66 - 1) * 100, 1),
                round((2.84 / 2.40 - 1) * 100, 1),
                round((1.85 / 0.97 - 1) * 100, 1),
            ],
        )
        self.assertEqual(
            record["revYoY"],
            [
                round((103000000000 / 94036000000 - 1) * 100, 1),
                round((104000000000 / 95359000000 - 1) * 100, 1),
                round((143756000000 / 124300000000 - 1) * 100, 1),
                round((102466000000 / 94930000000 - 1) * 100, 1),
            ],
        )
        self.assertFalse(record["epsTurn"])

    def test_yoy_alignment_window_and_negative_base(self):
        q = [
            ["2025-03-31", -0.20, 100.0],
            ["2025-06-30", 0.10, None],
            ["2025-09-30", 0.50, 100.0],
            ["2026-03-31", 0.30, 120.0],   # vs 2025-03-31 (365 d): base EPS <= 0 → null
            ["2026-07-20", 0.40, 130.0],   # vs 2025-06-30 is 385 d (inside ±25), revenue base missing
            ["2026-11-15", 0.60, 140.0],   # 2025-09-30 is 411 d before → no match
        ]
        record = growth.build_growth_record(q, "diluted", "2026-12-01")
        self.assertEqual(record["epsYoY"], [None, 300.0, None, None])
        self.assertEqual(record["revYoY"], [None, None, 20.0, None])
        self.assertFalse(record["epsTurn"])

    def test_eps_turnaround(self):
        q = [["2025-06-30", -0.10, 10.0], ["2026-06-30", 0.25, 12.0]]
        record = growth.build_growth_record(q, "diluted", "2026-10-09")
        self.assertTrue(record["epsTurn"])
        self.assertEqual(record["epsYoY"], [None, None])
        self.assertEqual(record["revYoY"], [20.0, None])

    def test_empty_quarters_give_no_record(self):
        self.assertIsNone(growth.build_growth_record([], "diluted", "2026-10-09"))
        self.assertIsNone(
            growth.build_growth_record([["2026-06-30", None, None]], "diluted", "2026-10-09")
        )


class StalenessTest(unittest.TestCase):
    def test_needs_refresh(self):
        fresh = {"latest": "2026-06-30", "checkedAt": "2026-10-08"}
        self.assertFalse(growth.needs_refresh(fresh, TODAY))
        self.assertTrue(growth.needs_refresh(None, TODAY))
        # Latest quarter > 100 days old: re-check every 3 days for the new report
        self.assertFalse(growth.needs_refresh({"latest": "2026-06-01", "checkedAt": "2026-10-07"}, TODAY))
        self.assertTrue(growth.needs_refresh({"latest": "2026-06-01", "checkedAt": "2026-10-05"}, TODAY))
        # Anything checked more than 30 days ago
        self.assertTrue(growth.needs_refresh({"latest": "2026-09-30", "checkedAt": "2026-09-08"}, TODAY))
        self.assertFalse(growth.needs_refresh({"latest": "2026-09-30", "checkedAt": "2026-09-10"}, TODAY))
        self.assertTrue(growth.needs_refresh({"latest": "bad", "checkedAt": "2026-10-08"}, TODAY))
        self.assertTrue(growth.needs_refresh({"latest": "2026-09-30"}, TODAY))

    def test_misses_retry_every_three_days(self):
        self.assertFalse(growth.needs_refresh(None, TODAY, miss_checked="2026-10-07"))
        self.assertTrue(growth.needs_refresh(None, TODAY, miss_checked="2026-10-05"))
        self.assertTrue(growth.needs_refresh(None, TODAY, miss_checked="garbage"))


class SymbolSourcesTest(unittest.TestCase):
    def test_collects_from_report_files(self):
        with tempfile.TemporaryDirectory() as report_dir, tempfile.TemporaryDirectory() as data_dir:
            with open(os.path.join(report_dir, "universe.json"), "w", encoding="utf-8") as file:
                json.dump({
                    "fields": ["symbol", "name"],
                    "markets": {
                        "TW": {"rows": [["2330.TW", "台積電"]]},
                        "US": {"rows": [["AAPL", ""], ["BRK-B", ""], [None, ""], "bad"]},
                    },
                }, file)
            with open(os.path.join(report_dir, "latest.json"), "w", encoding="utf-8") as file:
                json.dump({"groups": [
                    {"market": "US", "items": [{"symbol": "MSFT"}, {"symbol": "^GSPC"}]},
                    {"market": "TW", "items": [{"symbol": "2317.TW"}]},
                    {"market": "", "items": [{"symbol": "PLTR"}, {"symbol": "6488.TWO"}]},
                ]}, file)
            with open(os.path.join(report_dir, "themes.json"), "w", encoding="utf-8") as file:
                json.dump({"themes": [
                    {"etf": {"symbol": "SMH"}, "constituents": [{"symbol": "NVDA"}, {"symbol": "aapl"}]},
                    {"constituents": None},
                ]}, file)
            open(os.path.join(data_dir, "ZZZ.csv"), "w").close()

            symbols = growth.collect_symbols(report_dir, data_dir)
            self.assertEqual(symbols, ["AAPL", "BRK-B", "MSFT", "NVDA", "PLTR"])

    def test_csv_stands_in_for_missing_universe(self):
        with tempfile.TemporaryDirectory() as report_dir, tempfile.TemporaryDirectory() as data_dir:
            with open(os.path.join(report_dir, "latest.json"), "w", encoding="utf-8") as file:
                json.dump({"groups": [{"market": "US", "items": [{"symbol": "PLTR"}]}]}, file)
            open(os.path.join(report_dir, "universe.json"), "w").write("{broken")
            for name in ("AAPL.csv", "2330.TW.csv"):
                open(os.path.join(data_dir, name), "w").close()
            self.assertEqual(growth.collect_symbols(report_dir, data_dir), ["AAPL", "PLTR"])

    def test_falls_back_to_csv(self):
        with tempfile.TemporaryDirectory() as report_dir, tempfile.TemporaryDirectory() as data_dir:
            for name in ("AAPL.csv", "2330.TW.csv", "6488.TWO.csv", "^TWOII.csv", "MSFT.csv", "notes.txt"):
                open(os.path.join(data_dir, name), "w").close()
            self.assertEqual(growth.collect_symbols(report_dir, data_dir), ["AAPL", "MSFT"])


class FakeClock:
    def __init__(self, step=0.0):
        self.now = 0.0
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


class RefreshTest(unittest.TestCase):
    def previous_doc(self):
        return {
            "version": 1,
            "source": "Yahoo Finance",
            "items": {
                "AAPL": growth.build_growth_record(PREVIOUS_Q, "diluted", "2026-09-01"),
                "KEEP": growth.build_growth_record(
                    [["2026-06-30", 1.0, 10.0]], "diluted", "2026-10-08"
                ),
                "GONE": growth.build_growth_record(
                    [["2025-06-30", 1.0, 10.0]], "diluted", "2026-05-01"
                ),
            },
            "misses": {"TSM": "2026-10-08"},
        }

    def test_refresh_merges_and_skips_fresh_records(self):
        requested = []
        payloads = {
            "AAPL": fixture("yahoo_timeseries_aapl.json"),
            "XYZ": fixture("yahoo_timeseries_empty.json"),
        }

        def fetch(symbol):
            requested.append(symbol)
            return payloads[symbol]

        document, stats = growth.run_refresh(
            ["AAPL", "KEEP", "TSM", "XYZ"], self.previous_doc(), fetch, NOW,
            budget_seconds=600, sleep=lambda seconds: None, clock=FakeClock(),
        )
        # Never-seen symbols first, then the oldest check
        self.assertEqual(requested, ["XYZ", "AAPL"])
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["market"], "US")
        self.assertEqual(document["source"], "Yahoo Finance")
        self.assertEqual(document["generatedAt"], "2026-10-09T12:00:00Z")
        items = document["items"]
        self.assertEqual(sorted(items), ["AAPL", "KEEP"])  # GONE: unlisted and stale
        self.assertEqual(items["AAPL"]["q"][0][0], "2024-06-29")
        self.assertEqual(items["AAPL"]["latest"], "2026-06-27")
        self.assertEqual(items["AAPL"]["checkedAt"], "2026-10-09")
        self.assertEqual(len(items["AAPL"]["epsYoY"]), 4)
        self.assertEqual(items["KEEP"]["checkedAt"], "2026-10-08")
        self.assertEqual(document["misses"], {"TSM": "2026-10-08", "XYZ": "2026-10-09"})
        self.assertIsNone(stats["stopped"])
        self.assertEqual(stats["requested"], 2)
        json.dumps(document, allow_nan=False)

    def test_consecutive_failures_stop_and_keep_old_data(self):
        calls = []

        def fetch(symbol):
            calls.append(symbol)
            raise RuntimeError("HTTP 429")

        symbols = [f"S{i:02d}" for i in range(20)] + ["AAPL"]
        document, stats = growth.run_refresh(
            symbols, self.previous_doc(), fetch, NOW,
            budget_seconds=600, sleep=lambda seconds: None, clock=FakeClock(),
        )
        self.assertEqual(len(calls), growth.MAX_CONSECUTIVE_FAILURES)
        self.assertIn("consecutive failures", stats["stopped"])
        self.assertTrue(stats["failed"])
        self.assertEqual(document["items"]["AAPL"]["checkedAt"], "2026-09-01")

    def test_time_budget(self):
        def fetch(symbol):
            return fixture("yahoo_timeseries_empty.json")

        document, stats = growth.run_refresh(
            [f"S{i:02d}" for i in range(10)], {}, fetch, NOW,
            budget_seconds=3, sleep=lambda seconds: None, clock=FakeClock(step=1.0),
        )
        self.assertEqual(stats["stopped"], "time budget")
        self.assertFalse(stats["failed"])
        self.assertLess(stats["requested"], 10)
        self.assertEqual(stats["pending"], 10)

    def test_ignores_previous_from_other_source(self):
        previous = self.previous_doc()
        previous["source"] = "Something else"
        document, _ = growth.run_refresh(
            [], previous, lambda symbol: {}, NOW,
            budget_seconds=600, sleep=lambda seconds: None, clock=FakeClock(),
        )
        self.assertEqual(document["items"], {})


if __name__ == "__main__":
    unittest.main()

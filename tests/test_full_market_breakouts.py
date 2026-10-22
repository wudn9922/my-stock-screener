"""全市場名單／完整快取整合：合成資料、無網路，不把資料缺漏或沒有訊號算成失敗。"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))

import build_full_market_breakouts as full  # noqa: E402
from test_breakouts import AS_OF, flat_then, make_bars  # noqa: E402


class FullMarketBreakoutsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = self.tmp.name
        self.today = datetime.now(timezone.utc).date().isoformat()
        self.listing = {
            "TWSE": {"2330.TW": {}}, "TPEx": {"7777.TWO": {}},
            "US": {"LARGE": {}, "SMALL": {}, "MICRO": {}},
        }
        self.state = {"version": 1, "symbols": {}}
        self.minimum = patch.object(full, "MIN_LIST", {"TWSE": 1, "TPEx": 1, "US": 1})
        self.minimum.start()
        self.addCleanup(self.minimum.stop)
        for symbol in ("2330.TW", "7777.TWO", "LARGE", "SMALL", "MICRO", "NOT_LISTED"):
            bars = make_bars(flat_then([101, 102, 103, 104]) if symbol in ("SMALL", "7777.TWO") else [100] * 60)
            bars["Open"] = bars["Close"]
            full.uc.write_bars(full.uc.bars_path(self.cache, symbol), bars)
            self.state["symbols"][symbol] = {"full": self.today, "last": AS_OF}
        self.save()

    def save(self, date=None):
        full.uc.write_json(os.path.join(self.cache, "lists.json"), {
            exchange: {"items": items, "fetchedAt": date or self.today, "source": "synthetic exchange list"}
            for exchange, items in self.listing.items()
        })
        full.uc.save_state(self.cache, self.state)

    def build(self):
        return full.build_payload(full.load_inputs(self.cache, self.today), self.today)

    def test_full_listing_includes_small_caps_and_otc_without_a_market_cap_or_liquidity_filter(self):
        inputs = full.load_inputs(self.cache, self.today)
        self.assertEqual(set(inputs["us"]["frames"]), {"LARGE", "SMALL", "MICRO"})
        self.assertIn("7777.TWO", inputs["tw"]["frames"])
        self.assertNotIn("NOT_LISTED", inputs["us"]["frames"])
        us = self.build()["markets"]["us"]
        self.assertEqual((us["scope"], us["universeCount"], us["evaluated"]), ("full", 3, 3))
        self.assertEqual(us["coverage"], {"loaded": 3, "missing": 0})

    def test_success_is_conditional_on_signals_not_all_listed_stocks(self):
        # Only SMALL signals. LARGE/MICRO have no signal, and must not dilute 1/1 to 1/3.
        for key in ("tw", "us"):
            market = self.build()["markets"][key]
            for method in ("box20", "ema20"):
                stats = market["windows"]["1w"]["strategies"][method]
                self.assertEqual((stats["n"], stats["maturedN"], stats["wins"], stats["winRate"]), (1, 1, 1, 100))
            self.assertEqual(set(market["windows"]["1w"]["strategies"]), {"swingHigh", "box20", "ema20"})

    def test_missing_or_incomplete_history_does_not_shrink_the_listing(self):
        os.remove(full.uc.bars_path(self.cache, "MICRO"))
        self.state["symbols"]["LARGE"].pop("full")
        self.save()
        us = self.build()["markets"]["us"]
        self.assertEqual(us["universeCount"], 3)
        self.assertEqual(us["coverage"], {"loaded": 1, "missing": 2})
        with patch.object(full.bo, "MIN_EVALUATED", 1):
            self.assertTrue(any("未達 90%" in problem for problem in full.insufficient_markets(self.build())))

    def test_stale_history_is_disclosed_and_excluded_from_signal_outcomes(self):
        frame = full.uc.read_bars(full.uc.bars_path(self.cache, "MICRO"))
        full.uc.write_bars(full.uc.bars_path(self.cache, "MICRO"), frame.iloc[:-1])
        us = self.build()["markets"]["us"]
        self.assertEqual((us["evaluated"], us["stale"], us["coverage"]["missing"]), (2, 1, 0))

    def test_missing_exchange_or_expired_list_is_not_replaced_by_csv_names(self):
        self.listing["TPEx"] = {}
        self.save()
        with self.assertRaisesRegex(ValueError, "TPEx"):
            full.load_inputs(self.cache, self.today)
        self.listing["TPEx"] = {"7777.TWO": {}}
        self.save("2000-01-01")
        with self.assertRaisesRegex(ValueError, "過期"):
            full.load_inputs(self.cache, self.today)

    def test_main_preserves_old_report_on_incomplete_coverage_and_publishes_full_output_on_success(self):
        out = os.path.join(self.cache, "report.json")
        with open(out, "w", encoding="utf-8") as file:
            file.write('{"previous":true}')
        self.listing["US"]["MISSING"] = {}
        self.save()
        with patch.object(full.bo, "MIN_EVALUATED", 1), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(full.main(["--cache-dir", self.cache, "--out", out]), 1)
        with open(out, encoding="utf-8") as file:
            self.assertEqual(json.load(file), {"previous": True})
        self.listing["US"].pop("MISSING")
        self.save()
        with patch.object(full.bo, "MIN_EVALUATED", 1), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(full.main(["--cache-dir", self.cache, "--out", out]), 0)
        with open(out, encoding="utf-8") as file:
            report = json.load(file)
            self.assertTrue(all(market["scope"] == "full" for market in report["markets"].values()))
        self.assertEqual(report["asOf"], max(m["asOf"] for m in report["markets"].values()))
        archive = Path(out).parent / "breakouts-archive" / f"{report['asOf']}.json"
        self.assertEqual(archive.read_bytes(), Path(out).read_bytes())


class BreakoutsArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.report = Path(self.tmp.name) / "breakouts.json"

    def publish(self, as_of, generated_at="2030-01-01T00:00:00Z"):
        full.write_json_atomic(str(self.report), {"asOf": as_of, "generatedAt": generated_at, "markets": {}})
        return full.archive_snapshot(self.report, as_of)

    def test_first_snapshot_is_byte_identical_in_either_mode(self):
        for mode in ("", "1"):
            with self.subTest(mode=mode), patch.dict(os.environ, {"GENERATED_BRANCH_MODE": mode}):
                self.report = Path(self.tmp.name) / f"mode-{mode or 'off'}" / "breakouts.json"
                snapshot = self.publish("2026-09-30")
                self.assertEqual(snapshot.name, "2026-09-30.json")
                self.assertEqual(snapshot.read_bytes(), self.report.read_bytes())
                self.assertEqual(sorted(p.name for p in snapshot.parent.iterdir()), [snapshot.name])

    def test_19_calendar_days_does_not_archive(self):
        first = self.publish("2026-09-30")
        self.assertIsNone(self.publish("2026-10-19"))
        self.assertEqual(list(first.parent.glob("*.json")), [first])

    def test_20_calendar_days_archives_using_asof_instead_of_generated_at(self):
        first = self.publish("2026-09-30", "2030-01-01T00:00:00Z")
        original = first.read_bytes()
        snapshot = self.publish("2026-10-20", "2030-01-02T00:00:00Z")
        self.assertEqual(snapshot.name, "2026-10-20.json")
        self.assertEqual(snapshot.read_bytes(), self.report.read_bytes())
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(len(list(first.parent.glob("*.json"))), 2)

    def test_same_day_rerun_is_an_archive_noop_even_when_report_changes(self):
        first = self.publish("2026-09-30")
        original, mtime = first.read_bytes(), first.stat().st_mtime_ns
        self.assertIsNone(self.publish("2026-09-30", "2030-01-02T00:00:00Z"))
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(first.stat().st_mtime_ns, mtime)
        self.assertNotEqual(first.read_bytes(), self.report.read_bytes())

    def test_newest_snapshot_is_used_and_older_reports_do_not_backfill_archives(self):
        self.publish("2026-09-01")
        self.publish("2026-09-21")
        self.assertIsNone(self.publish("2026-10-10"))  # newest is 19 days old
        self.assertIsNone(self.publish("2026-08-01"))
        self.assertEqual(len(list((self.report.parent / "breakouts-archive").glob("*.json"))), 2)

    def test_combined_asof_is_the_newest_market_data_date(self):
        markets = {"tw": {"asOf": "2026-10-22"}, "us": {"asOf": "2026-10-21"}}
        inputs = {key: {"frames": [], "listed": 0, "listingAsOf": "2026-10-22", "listingSource": "fixture"}
                  for key in markets}
        with patch.object(full.bo, "build_breakouts_payload", return_value={"markets": markets}):
            for market in markets.values():
                market["windows"] = {}
            self.assertEqual(full.build_payload(inputs, "2030-01-01T00:00:00Z")["asOf"], "2026-10-22")


if __name__ == "__main__":
    unittest.main()

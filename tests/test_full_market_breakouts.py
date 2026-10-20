"""全市場名單／完整快取整合：合成資料、無網路，不把資料缺漏或沒有訊號算成失敗。"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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
            for method in ("high20", "box20", "ema20"):
                stats = market["windows"]["1w"]["strategies"][method]
                self.assertEqual((stats["n"], stats["maturedN"], stats["wins"], stats["winRate"]), (1, 1, 1, 100))

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
            self.assertTrue(all(market["scope"] == "full" for market in json.load(file)["markets"].values()))


if __name__ == "__main__":
    unittest.main()

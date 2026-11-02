"""Full-list breadth integration: offline cache, no writes to prices or the archive."""
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

import build_full_market_breadth as breadth  # noqa: E402
from test_breakouts import AS_OF, make_bars  # noqa: E402


class FullMarketBreadthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = self.tmp.name
        self.today = datetime.now(timezone.utc).date().isoformat()
        self.listing = {"TWSE": {"2330.TW": {}}, "TPEx": {"7777.TWO": {}},
                        "US": {"LARGE": {}, "SMALL": {}, "MICRO": {}}}
        self.state = {"version": 1, "symbols": {}}
        for symbol in ("2330.TW", "7777.TWO", "LARGE", "SMALL", "MICRO", "NOT_LISTED"):
            bars = make_bars(list(range(100, 180)) if symbol == "SMALL" else [100] * 80)
            bars["Open"] = bars["Close"]
            breadth.full.uc.write_bars(breadth.full.uc.bars_path(self.cache, symbol), bars)
            self.state["symbols"][symbol] = {"full": self.today, "last": AS_OF}
        minimum = patch.object(breadth.full, "MIN_LIST", {"TWSE": 1, "TPEx": 1, "US": 1})
        minimum.start()
        self.addCleanup(minimum.stop)
        self.save()

    def save(self, date=None):
        breadth.full.uc.write_json(os.path.join(self.cache, "lists.json"), {
            exchange: {"items": items, "fetchedAt": date or self.today, "source": "synthetic full list"}
            for exchange, items in self.listing.items()
        })
        breadth.full.uc.save_state(self.cache, self.state)

    def build(self):
        return breadth.build_payload(breadth.full.load_inputs(self.cache, self.today), self.today)

    def test_whole_market_includes_small_caps_and_tpex_and_keeps_ma_denominators(self):
        payload = self.build()
        tw, us = payload["markets"]["tw"], payload["markets"]["us"]
        self.assertEqual((tw["scope"], tw["universeCount"]), ("full", 2))
        self.assertEqual([tw["segments"][key]["count"] for key in ("all", "twse", "tpex")], [2, 1, 1])
        self.assertEqual(us["universeCount"], 3)  # excludes the unlisted cache file
        self.assertEqual(us["segments"]["all"]["ma60"], {"above": 1, "total": 3, "pct": 33.3})
        self.assertEqual(breadth.insufficient_markets(payload), [])
        self.assertEqual(us["history"]["dates"][-1], AS_OF)

    def test_missing_incomplete_and_stale_bars_are_not_counted_as_below_ma(self):
        os.remove(breadth.full.uc.bars_path(self.cache, "MICRO"))
        path = breadth.full.uc.bars_path(self.cache, "LARGE")
        breadth.full.uc.write_bars(path, breadth.full.uc.read_bars(path).iloc[:-1])
        us = self.build()["markets"]["us"]
        self.assertEqual(us["universeCount"], 3)
        self.assertEqual(us["coverage"], {"loaded": 2, "missing": 1, "notTraded": 1})
        self.assertEqual(us["segments"]["all"]["ma60"], {"above": 1, "total": 1, "pct": 100})
        self.assertTrue(breadth.insufficient_markets(self.build()))
        self.state["symbols"]["SMALL"].pop("full")
        self.save()
        self.assertTrue(breadth.insufficient_markets(self.build()))

    def test_closed_day_placeholder_retains_last_traded_session(self):
        import pandas as pd
        for symbol in self.state["symbols"]:
            path = breadth.full.uc.bars_path(self.cache, symbol)
            bars = breadth.full.uc.read_bars(path)
            bars.loc[pd.Timestamp("2026-10-12")] = [100, 101, 99, 100, 0]
            breadth.full.uc.write_bars(path, bars)
        payload = self.build()
        self.assertTrue(all(m["asOf"] == AS_OF for m in payload["markets"].values()))
        self.assertEqual(breadth.insufficient_markets(payload), [])

    def test_output_retained_on_missing_expired_listing_or_inadequate_coverage(self):
        out = Path(self.cache) / "breadth.json"
        old = b'{"previous":true}'
        for failure in ("missing-list", "expired", "coverage"):
            self.save("2000-01-01" if failure == "expired" else None)
            if failure == "missing-list":
                os.remove(os.path.join(self.cache, "lists.json"))
            if failure == "coverage":
                os.remove(breadth.full.uc.bars_path(self.cache, "MICRO"))
            out.write_bytes(old)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(breadth.main(["--cache-dir", self.cache, "--out", str(out)]), 1)
            self.assertEqual(out.read_bytes(), old)

    def test_success_does_not_mutate_cache_or_prices(self):
        before = {path.relative_to(self.cache): path.read_bytes() for path in Path(self.cache).rglob("*") if path.is_file()}
        out = Path(self.cache) / "result.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(breadth.main(["--cache-dir", self.cache, "--out", str(out)]), 0)
        for path, content in before.items():
            self.assertEqual((Path(self.cache) / path).read_bytes(), content)
        self.assertTrue(all(m["scope"] == "full" for m in json.loads(out.read_text())["markets"].values()))

    def test_only_universe_workflow_and_owner_publish_breadth(self):
        root = Path(__file__).resolve().parents[1]
        owners = json.loads((root / ".github/generated-paths.json").read_text())["owners"]
        self.assertNotIn("docs/report/breadth.json", owners["daily"])
        self.assertIn("docs/report/breadth.json", owners["universe"])
        self.assertNotIn("scripts/build_breadth.py", (root / ".github/workflows/schedule.yml").read_text())
        self.assertIn("scripts/build_full_market_breadth.py", (root / ".github/workflows/universe-full.yml").read_text())

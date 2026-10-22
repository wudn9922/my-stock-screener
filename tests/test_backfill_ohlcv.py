import importlib.util
from pathlib import Path
import unittest
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location("backfill_ohlcv", Path(__file__).resolve().parents[1] / "scripts/backfill_ohlcv.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def bar(day, **values):
    return {"Date": day, "Open": 10, "High": 12, "Low": 9, "Close": 11, "Volume": 100, **values}


class BackfillTest(unittest.TestCase):
    def test_repair_preserves_existing_prices_and_zero_volume(self):
        local = [bar("2026-10-19", Close="", Volume=0)]
        rows, added, repaired = module.reconcile(local, [bar("2026-10-19", Open=11), bar("2026-10-20")])
        self.assertEqual(added, ["2026-10-20"])
        self.assertEqual(repaired, [{"date": "2026-10-19", "column": "Close"}])
        self.assertEqual(rows[0]["Open"], 10)
        self.assertEqual(rows[0]["Volume"], 0)
        self.assertEqual(module.reconcile(rows, [bar("2026-10-19"), bar("2026-10-20")])[1:], ([], []))

    def test_no_fabricated_or_partial_or_weekend_bars(self):
        rows, added, _ = module.reconcile([], [bar("2026-10-17"), bar("2026-10-19", Low=None), bar("2026-10-20")])
        self.assertEqual(added, ["2026-10-20"])
        self.assertEqual(len(rows), 1)

    def test_only_completed_market_days(self):
        now = datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(module.cutoff("2330.TW", now), "2026-10-21")
        self.assertEqual(module.cutoff("AAPL", now), "2026-10-20")
        self.assertEqual(module.cutoff("AAPL", datetime(2026, 10, 21, 19, 0, tzinfo=timezone.utc)), "2026-10-20")


if __name__ == "__main__":
    unittest.main()

import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_tw_cash_dividends as cash

NOW = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)


class CashDividendSnapshotTests(unittest.TestCase):
    def test_snapshot_isolation_window_bad_stock_and_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            fin = report / "financials"
            fin.mkdir()
            def put(symbol, rows):
                (fin / (symbol + ".json")).write_text(json.dumps({
                    "stockDividends": {"checked": "2026-11-28", "cashEvents": rows}
                }))
            put("2330.TW", [
                {"exDate": "2025-11-01", "perShare": 99},
                {"exDate": "2026-04-01", "perShare": 2},
                {"exDate": "2026-12-01", "perShare": 3.5},
                {"exDate": "2027-02-01", "perShare": 4, "paymentDate": "2027-03-01"},
                {"exDate": "2027-06-01", "perShare": 99},
            ])
            put("6488.TWO", [{"exDate": "invalid", "perShare": 1}])
            put("AAPL", [{"exDate": "2026-12-01", "perShare": 3}])
            upcoming = report / "upcoming-events.json"
            upcoming.write_text('{"version":1,"events":[]}')
            old = upcoming.read_bytes()
            self.assertTrue(cash.run(report, NOW, dry_run=True))
            target = report / "tw-cash-dividends.json"
            self.assertFalse(target.exists())
            self.assertTrue(cash.run(report, NOW))
            payload = json.loads(target.read_bytes())
            self.assertEqual(set(payload), {"version", "generatedAt", "stocks"})
            self.assertEqual(set(payload["stocks"]), {"2330.TW"})
            stock = payload["stocks"]["2330.TW"]
            self.assertEqual(stock["checked"], "2026-11-28")
            self.assertEqual(len(stock["events"]), 3)
            self.assertIsNone(stock["events"][1]["paymentDate"])
            self.assertEqual(stock["events"][2]["paymentDate"], "2027-03-01")
            self.assertEqual(upcoming.read_bytes(), old)
            good = target.read_bytes()
            put("2330.TW", [{"exDate": "invalid", "perShare": 2}])
            self.assertFalse(cash.run(report, NOW))
            self.assertEqual(target.read_bytes(), good)
            self.assertEqual(upcoming.read_bytes(), old)

    def test_initial_uncollected_data_is_empty_but_not_missing_financials(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            self.assertFalse(cash.run(report, NOW))
            self.assertFalse((report / "tw-cash-dividends.json").exists())
            fin = report / "financials"
            fin.mkdir()
            record = fin / "2330.TW.json"
            record.write_text(json.dumps({"stockDividends": {"checked": "2026-11-28", "events": []}}))
            self.assertTrue(cash.run(report, NOW))
            target = report / "tw-cash-dividends.json"
            self.assertEqual(json.loads(target.read_bytes())["stocks"], {})
            record.write_text(json.dumps({"stockDividends": {"checked": "2026-11-28", "cashEvents": []}}))
            self.assertTrue(cash.run(report, NOW))
            covered = target.read_bytes()
            record.write_text(json.dumps({"stockDividends": {"checked": "2026-11-28", "events": []}}))
            self.assertFalse(cash.run(report, NOW))
            self.assertEqual(target.read_bytes(), covered)

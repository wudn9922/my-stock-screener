"""Offline event timeline tests; no upstream requests or user records."""
from datetime import datetime, timezone
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "atlas/scripts"))
from scripts import build_upcoming_events as ue
from scripts.output_guard import OutputRejected
import build_events_us as eu
import tw_stock_dividends as dividends


class UpcomingEventsTests(unittest.TestCase):
    def test_bmo_amc_and_holiday(self):
        start, end = "2026-07-01", "2026-07-10"
        sessions = sorted(d for d in ue.exchange_sessions("us", start, end) if start <= d <= end)
        self.assertNotIn("2026-07-03", sessions)
        bars = {d: (100., 110., 90., 100.) for d in sessions}
        bars["2026-07-02"] = (105., 110., 100., 102.)
        bars["2026-07-06"] = (112.2, 120., 100., 91.8)
        bmo = ue.earnings_moves({"2026-07-02": "bmo"}, bars, sessions, end)
        self.assertEqual((bmo["samples"], bmo["gapPct"], bmo["dayPct"], bmo["last"]),
                         (1, 5., 2., "2026-07-02"))
        amc = ue.earnings_moves({"2026-07-02": "amc"}, bars, sessions, end)
        self.assertEqual((amc["samples"], amc["gapPct"], amc["dayPct"], amc["last"]),
                         (1, 10., 10., "2026-07-06"))
        del bars["2026-07-06"]
        self.assertEqual(ue.earnings_moves({"2026-07-02": "amc"}, bars, sessions, end)["samples"], 0)

    def test_max_eight_and_pending(self):
        sessions = sorted(d for d in ue.exchange_sessions("us", "2024-01-01", "2026-11-25")
                          if "2024-01-01" <= d <= "2026-11-25")
        bars = {d: (50., 60., 40., 50.) for d in sessions}
        history = {d: "amc" for d in sessions[10:400:40]}
        history[sessions[-1]] = "amc"
        result = ue.earnings_moves(history, bars, sessions, sessions[-1])
        self.assertEqual(result["samples"], 8)
        self.assertEqual(result["gapPct"], 0)
        self.assertLess(result["last"], sessions[-1])

    def test_history_only_past_confirmed_timestamp(self):
        now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
        at = datetime(2026, 10, 29, 17, tzinfo=eu.EASTERN).timestamp()
        raw = {"quoteType": "EQUITY", "estimate": False, "at": at}
        old = {"history": {"AAPL": {"2026-07-29": "amc"}}}
        result = eu.observed_history(old, {"AAPL": raw, "NVDA": {**raw, "estimate": True},
                                          "MSFT": {**raw, "at": now.timestamp() + 86400}}, now)
        self.assertEqual(result, {"AAPL": {"2026-07-29": "amc", "2026-10-29": "amc"}})
        self.assertEqual(eu.observed_history({"history": result}, {}, now), result)

    def test_cash_only_does_not_change_stock_eps_events(self):
        rows = [{"CashExDividendTradingDate": "2026-12-01", "CashEarningsDistribution": 3,
                 "CashStatutorySurplus": 0.5, "StockEarningsDistribution": 0, "StockStatutorySurplus": 0}]
        self.assertEqual(dividends.parse_events(rows), [])
        self.assertEqual(dividends.parse_cash_events(rows)[0]["perShare"], 3.5)

    def test_bad_input_preserves_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "docs/report/upcoming-events.json"
            target.parent.mkdir(parents=True)
            target.write_text('{"version":1,"coverage":{}}')
            previous = target.read_bytes()
            earnings = root / "docs/atlas/valuation/us-events.json"
            earnings.parent.mkdir(parents=True)
            earnings.write_text(json.dumps({"version": 1, "market": "US", "generatedAt": "2020-01-01T00:00:00Z", "items": {}}))
            with self.assertRaises(OutputRejected):
                ue.build(root, root / "archive", datetime(2026, 11, 28, tzinfo=timezone.utc))
            self.assertEqual(target.read_bytes(), previous)

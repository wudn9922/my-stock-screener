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
        bars["2026-07-06"] = (112.2, 120., 90., 91.8)
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
                 "CashStatutorySurplus": 0.5, "CashDividendPaymentDate": "2026-12-30", "StockEarningsDistribution": 0, "StockStatutorySurplus": 0}]
        self.assertEqual(dividends.parse_events(rows), [])
        self.assertEqual(dividends.parse_cash_events(rows)[0]["perShare"], 3.5)
        self.assertEqual(dividends.parse_cash_events(rows)[0]["paymentDate"], "2026-12-30")
        self.assertNotIn("paymentDate", dividends.parse_cash_events([dict(rows[0], CashDividendPaymentDate="")])[0])

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

    def test_complete_fixture_replay_and_missing_bar_retains_good_report(self):
        from scripts.build_market_calendar import build as build_calendar
        now = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "archive"

            def put(relative, value):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")

            put("docs/atlas/valuation/us-events.json", {
                "version": 1, "market": "US", "generatedAt": now.isoformat(),
                "items": {"AAPL": {"earningsDate": "2026-12-02", "estimate": False, "session": "amc"}},
                "history": {"AAPL": {"2026-11-25": "bmo"}},
            })
            put("docs/report/market-calendar.json", build_calendar(now))
            put("docs/report/research/alerts.json", {
                "version": 1, "generatedAt": now.isoformat(),
                "sources": {f"{m}:{k}": {"status": "ok"}
                            for m in ("TW", "TWO") for k in ("notice", "disposal")},
                "events": [{"market": "TW", "kind": "disposal", "symbol": "2330.TW",
                            "date": "2026-11-27", "start": "2026-11-30", "end": "2026-12-10"}],
            })
            put("docs/report/financials/2330.TW.json", {"stockDividends": {
                "events": [{"exDate": "2026-12-01", "perShare": 1.5}],
                "checked": "2026-11-28",
                "cashEvents": [{"exDate": "2026-12-01", "perShare": 3.5},
                               {"exDate": "2027-02-01", "perShare": 4, "paymentDate": "2027-03-01"},
                               {"exDate": "2026-04-01", "perShare": 2}],
            }})
            put("config/fomc-decisions.json", {
                "version": 1, "validThrough": "2027-12-31", "dates": ["2026-12-09"],
                "source": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            })
            put(str(Path("archive") / ue.LOG), {"version": 1, "quarantined": [], "events": []})
            price = archive / "us/AAPL/2026.csv"
            price.parent.mkdir(parents=True)
            price.write_text("Date,Open,High,Low,Close,Volume\n"
                             "2026-11-24,50.0000,52.0000,49.0000,50.0000,100\n"
                             "2026-11-25,55.0000,56.0000,52.0000,54.0000,100\n")
            doc = ue.build(root, archive, now)
            self.assertEqual(doc["moves"]["AAPL"]["samples"], 1)
            self.assertEqual(doc["moves"]["AAPL"]["gapPct"], 10)
            self.assertEqual(doc["moves"]["AAPL"]["dayPct"], 8)
            kinds = {row["kind"] for row in doc["events"]}
            self.assertTrue({"earnings", "stock-dividend", "cash-dividend",
                             "disposal-start", "disposal-end", "fomc"} <= kinds)
            cash = doc["cashDividends"]["2330.TW"]
            self.assertEqual(cash["checked"], "2026-11-28")
            self.assertEqual(len(cash["events"]), 3)
            self.assertIsNone(cash["events"][0]["paymentDate"])
            self.assertEqual(cash["events"][1]["paymentDate"], "2027-03-01")
            report = root / "docs/report/upcoming-events.json"
            old = report.read_bytes()
            price.write_text("Date,Open,High,Low,Close,Volume\n"
                             "2026-11-24,50.0000,52.0000,49.0000,50.0000,100\n")
            with self.assertRaises(OutputRejected):
                ue.build(root, archive, now)
            self.assertEqual(report.read_bytes(), old)

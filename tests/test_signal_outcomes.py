"""Offline fixtures; no price download or production writes."""
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from scripts import build_signal_outcomes as so
from scripts.output_guard import OutputRejected


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def snapshot(stamp, close=100):
    return {"version": 1, "market": "US", "dataDate": stamp,
            "universe": {"fields": ["symbol", "close", "asOf", "ma20", "ma20Slope5"],
                         "markets": {"US": {"asOf": stamp, "scope": "full",
                                            "rows": [["AAPL", close, stamp, 0, 1],
                                                     ["MSFT", 50, stamp, 0, -1]]}}},
            "report": {"groups": [
                {"key": "us_all", "market": "US", "kind": "scan", "count": 1,
                 "items": [{"symbol": "MSFT", "close": 50, "asOf": stamp}]},
                {"key": "custom_1", "market": "US", "kind": "custom", "count": 1,
                 "items": [{"symbol": "PRIVATE", "close": 100, "asOf": stamp}]}
            ]}}


class SignalOutcomesTests(unittest.TestCase):
    def test_capture_is_append_only_and_excludes_personal_groups(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            history = root / "docs/report/screener-history"
            ledger = root / "docs/report/signal-ledger"
            stamp = "2026-07-02"
            dump(history / "index.json", {"version": 1, "markets": {"TW": [], "US": [stamp]}})
            dump(history / f"US-{stamp}.json", snapshot(stamp))
            self.assertEqual(so.capture(root), 2)
            before = {p.name: p.read_bytes() for p in ledger.iterdir()}
            self.assertEqual(so.load(ledger / f"US-{stamp}-ambush-v1.json")["rows"], [["AAPL", 100]])
            self.assertEqual(so.load(ledger / f"US-{stamp}-daily-scan-v1.json")["rows"], [["MSFT", 50]])
            dump(history / f"US-{stamp}.json", snapshot(stamp, 200))
            self.assertEqual(so.capture(root), 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in ledger.iterdir()})
            later = "2026-07-06"
            dump(history / "index.json", {"version": 1, "markets": {"TW": [], "US": [stamp, later]}})
            dump(history / f"US-{later}.json", snapshot(later))
            self.assertEqual(so.capture(root), 2)
            self.assertEqual(len(list(ledger.iterdir())), 4)

    def test_exact_exchange_sessions_split_adjustment_and_maturity(self):
        dates = sorted(so.exchange_sessions("us", "2026-07-02", "2026-09-01"))
        self.assertEqual(dates[:2], ["2026-07-02", "2026-07-06"])  # Independence Day observed
        stamp = dates[0]
        # Original observed entry 100 before a 2:1 split; archive now uses 50 at entry.
        stock = {d: (55., 57., 48., 55.) for d in dates}
        stock[stamp] = (50., 50., 50., 50.)
        index = {d: (100., 102., 99., 101.) for d in dates}
        index[stamp] = (100., 100., 100., 100.)
        bars = {"AAPL": stock, "^GSPC": index}.__getitem__
        pending = so.evaluate(stamp, "AAPL", dates, stamp, bars, "^GSPC")
        self.assertEqual(pending, {})
        first = so.evaluate(stamp, "AAPL", dates, dates[3], bars, "^GSPC")
        self.assertAlmostEqual(first[1][0], 10.)
        self.assertNotIn(5, first)
        self.assertNotIn(20, first)
        full = so.evaluate(stamp, "AAPL", dates, dates[20], bars, "^GSPC")
        self.assertAlmostEqual(full[20][0], 10.)
        self.assertAlmostEqual(full["mfe"], 14.)
        self.assertAlmostEqual(full["mae"], -4.)
        self.assertAlmostEqual(full[20][1], 1.)
        report = so.summarize([full] * 20 + [pending, first])
        self.assertEqual(report["samples"], 22)
        self.assertEqual(report["horizons"]["20"]["mature"], 20)
        self.assertEqual(report["horizons"]["20"]["pending"], 2)
        self.assertEqual(report["horizons"]["20"]["winRate"], 100.)
        self.assertIsNone(so.summarize([full])["horizons"]["20"]["winRate"])
        del stock[dates[2]]
        with self.assertRaises(OutputRejected):
            so.evaluate(stamp, "AAPL", dates, dates[20], bars, "^GSPC")

    def test_build_preserves_previous_on_missing_ledger_or_bar(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / "docs/report"
            ledger = report / "signal-ledger"
            archive = root / "archive"
            stamp = "2026-07-02"
            name = f"US-{stamp}-ambush-v1.json"
            dump(ledger / name, {"version": 1, "market": "US", "signalType": "ambush-v1",
                                 "triggerDate": stamp, "rows": [["AAPL", 100]]})
            dump(archive / so.LOG, {"version": 1, "quarantined": [], "events": []})
            dates = sorted(so.exchange_sessions("us", stamp, "2026-08-05"))
            for symbol in ("AAPL", "^GSPC"):
                path = archive / "us" / symbol / "2026.csv"
                path.parent.mkdir(parents=True)
                path.write_text("Date,Open,High,Low,Close,Volume\n" +
                                "".join(f"{d},50.0000,55.0000,49.0000,50.0000,100\n" for d in dates))
            result = so.build(root, archive)
            self.assertEqual(result["markets"]["US"]["signals"]["ambush-v1"]["windows"]["3"]["samples"], 1)
            output = report / "signal-outcomes.json"
            old = output.read_bytes()
            path = archive / "us/AAPL/2026.csv"
            lines = path.read_text().splitlines()
            path.write_text("\n".join(lines[:2] + lines[3:]) + "\n")
            with self.assertRaises(OutputRejected):
                so.build(root, archive)
            self.assertEqual(old, output.read_bytes())
            (ledger / name).unlink()
            with self.assertRaises(OutputRejected):
                so.build(root, archive)
            self.assertEqual(old, output.read_bytes())

    def test_taiwan_volume_is_average_not_daily_volume(self):
        stamp = "2026-07-02"
        doc = snapshot(stamp)
        doc["market"] = "TW"
        doc["universe"] = {"fields": ["symbol", "close", "asOf", "ma20", "ma20Slope5", "volumeAvg20"],
                          "markets": {"TW": {"asOf": stamp, "scope": "full", "rows": [
                              ["2330.TW", 100, stamp, -2, 1, 1_000_000],
                              ["2317.TW", 100, stamp, 1, 1, 999_999]
                          ]}}}
        self.assertEqual(so.snapshot_cohorts(doc, "TW", stamp), {"ambush-v1": [("2330.TW", 100)]})

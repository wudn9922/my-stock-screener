"""Offline publication gate fixtures; no formal data or remote requests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import report_universe as series
from scripts import build_screener_history as history
from scripts import report_run_status as status
from scripts.output_guard import OutputRejected

ROOT = Path(__file__).resolve().parents[1]


def bars(count=20, start=0):
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{"time": int((epoch + timedelta(days=i)).timestamp()), "close": 100}
            for i in range(start, start + count)]


def series_doc(symbol, values):
    return {"symbol": symbol, "asOf": 1795737600, "bars": values}


class SeriesGateTest(unittest.TestCase):
    def test_series_rejection_isolated_and_status_records_each_failure(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RUNNER_TEMP": folder}):
            status.reset_status()
            dest = Path(folder) / "series"
            dest.mkdir()
            path = dest / "^TWOII.json"
            index = dest / "index.json"
            prior = series_doc("^TWOII", bars())
            raw = json.dumps(prior, indent=2).encode()
            path.write_bytes(raw)
            index.write_text('{"symbols":["^TWOII"]}\n')
            old_index = index.read_bytes()
            invalid = [
                [], bars(19), bars()[:5] + bars()[7:],
                bars()[:1] + bars()[:1] + bars()[1:],
                list(reversed(bars())),
                [{**bars()[0], "close": float("inf")}] + bars()[1:],
                [{**bars()[0], "close": 0}] + bars()[1:],
            ]
            for values in invalid:
                with self.subTest(values=values), patch.object(series, "build_series_payload", return_value=series_doc("^TWOII", values)):
                    self.assertIsNone(series.write_report_series("^TWOII", None, "OTC", "fixture", series_dir=dest))
                    self.assertEqual(path.read_bytes(), raw)
                    self.assertEqual(index.read_bytes(), old_index)
            self.assertEqual(status._read(), ["series/^TWOII.json: OutputRejected"] * len(invalid))
            self.assertEqual(status.main(), 1)
            with patch.object(series, "build_series_payload", return_value=series_doc("AAPL", bars(1))):
                self.assertIsNotNone(series.write_report_series("AAPL", None, "Apple", "fixture", series_dir=dest))
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(set(json.loads(index.read_text())["symbols"]), {"^TWOII", "AAPL"})

    def test_boundary_rolling_start_and_cold_start(self):
        prior = series_doc("AAPL", bars())
        series.validate_series(series_doc("AAPL", bars()[:10] + bars()[11:] + bars(1, 20)), prior)
        series.validate_series(series_doc("AAPL", bars(10, 10)), prior)
        series.validate_series(series_doc("AAPL", bars(1)), None)
        with self.assertRaises(OutputRejected):
            series.validate_series(series_doc("AAPL", []), None)
        with self.assertRaises(OutputRejected):
            series.validate_series(series_doc("AAPL", bars()[:5] + bars()[7:] + bars(2, 20)), prior)

    def test_actual_writer_and_empty_tpex_status(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RUNNER_TEMP": folder}):
            status.reset_status()
            dates = pd.date_range("2026-01-01", periods=20)
            frame = pd.DataFrame({"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 10}, index=dates)
            path = Path(folder) / "^TWOII.json"
            series.write_report_series("^TWOII", frame, "OTC", "fixture", series_dir=folder)
            first = path.read_bytes()
            series.write_report_series("^TWOII", frame.drop(dates[5]), "OTC", "fixture", series_dir=folder)
            self.assertNotEqual(path.read_bytes(), first)
            self.assertEqual(len(json.loads(path.read_text())["bars"]), 19)
            self.assertEqual(status.main(), 0)
            prior = path.read_bytes()
            real_writer = series.write_report_series
            with patch.object(series, "get_tpex_otc_full_history", return_value=pd.DataFrame()), patch.object(
                    series, "write_report_series", side_effect=lambda *args: real_writer(*args, series_dir=folder)):
                self.assertIsNone(series.write_tpex_otc_series())
            self.assertEqual(path.read_bytes(), prior)
            self.assertEqual(status._read(), ["series/^TWOII.json: OutputRejected"])


def source(day, n, market, offset=0):
    return {"asOf": day, "rows": [[f"{market}{i}"] for i in range(offset, offset + n)]}


class HistoryGateTest(unittest.TestCase):
    def write_source(self, root, **markets):
        path = root / "docs/report/universe.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "fields": ["symbol"], "markets": markets}))

    def snapshot_bytes(self, root):
        return {p.name: p.read_bytes() for p in (root / "docs/report/screener-history").glob("*.json")}

    def test_rejected_candidate_does_not_write_or_prune_any_market(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for i in range(1, 11):
                day = f"2026-11-{i:02d}"
                self.write_source(root, TW=source(day, 100, "TW"), US=source(day, 100, "US"))
                self.assertEqual(history.capture(root, day), 2)
            before = self.snapshot_bytes(root)
            day = "2026-11-11"
            bad_sources = [source(day, 0, "US"), source(day, 84, "US"), source(day, 100, "US", 100),
                           {"asOf": day, "rows": [["US0"]] * 100}]
            for bad in bad_sources:
                self.write_source(root, TW=source(day, 100, "TW"), US=bad)
                with self.subTest(bad=bad), self.assertRaises(OutputRejected):
                    history.capture(root, day)
                self.assertEqual(self.snapshot_bytes(root), before)
            process = subprocess.run([sys.executable, str(ROOT / "scripts/build_screener_history.py"), "--root", folder],
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 1)
            self.assertIn("US screener-history", process.stdout)
            self.assertEqual(self.snapshot_bytes(root), before)
            self.write_source(root, TW=source(day, 85, "TW"), US=source(day, 85, "US"))
            self.assertEqual(history.capture(root, day), 2)
            after = self.snapshot_bytes(root)
            self.assertNotIn("TW-2026-11-01.json", after)
            self.assertNotIn("US-2026-11-01.json", after)
            self.assertIn("TW-2026-11-11.json", after)
            self.assertEqual(after["TW-2026-11-10.json"], before["TW-2026-11-10.json"])

    def test_small_prior_legal_changes_and_first_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.write_source(root, TW=source("2026-11-01", 99, "TW"))
            self.assertEqual(history.capture(root), 1)
            self.write_source(root, TW=source("2026-11-02", 1, "TW", 1000))
            self.assertEqual(history.capture(root), 1)
            before = self.snapshot_bytes(root)
            self.write_source(root, TW=source("2026-11-02", 0, "TW"))
            self.assertEqual(history.capture(root), 0)
            self.assertEqual(self.snapshot_bytes(root)["TW-2026-11-02.json"], before["TW-2026-11-02.json"])

    def test_cold_start_empty_new_date_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.write_source(root, US=source("2026-11-02", 0, "US"))
            with self.assertRaises(OutputRejected):
                history.capture(root)
            self.assertEqual(self.snapshot_bytes(root), {})


if __name__ == "__main__":
    unittest.main()

"""Offline coverage gate and daily LINE / publication failure propagation."""
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import main
import report_json
from scripts.output_guard import OutputRejected
from scripts import report_run_status

ROOT = Path(__file__).resolve().parents[1]


def fixture(day="2026-11-27", n=20):
    return {
        "reportDate": day, "generatedAt": "fixture",
        "markets": [{"key": market, "indices": [{"symbol": symbol, "close": 100}]}
                    for market, symbol in [("tw", "^TWII"), ("jp", "^N225"), ("kr", "^KS11"), ("eu", "^FCHI"), ("us", "^GSPC")]],
        "groups": [{"key": market.lower() + "_all", "market": market, "items": [
            {"symbol": f"{market}{i}", "close": i + 1} for i in range(n)]} for market in ("TW", "US")],
        "worldIndices": [{"symbol": f"INDEX{i}", "close": 100} for i in range(20)],
        "sectors": [],
    }


class LatestReportGateTest(unittest.TestCase):
    def test_rejects_before_atomic_write(self):
        prior = fixture()
        mutations = [
            lambda p: p.clear(),
            lambda p: p.update(reportDate="2026-02-30"),
            lambda p: p.update(reportDate="2026-11-26"),
            lambda p: p["markets"].pop(1),
            lambda p: p["markets"][1]["indices"].clear(),
            lambda p: p["markets"][0]["indices"][0].update(close=0),
            lambda p: p["markets"][-1]["indices"][0].update(close=float("inf")),
            lambda p: p["groups"].append(deepcopy(p["groups"][0])),
            lambda p: p["groups"][0].update(items=p["groups"][0]["items"][:16]),
            lambda p: p["groups"][1].update(items=[]),
            lambda p: p.update(worldIndices=p["worldIndices"][:16]),
            lambda p: p.update(worldIndices=[]),
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(report_json, "is_public_report_owner", return_value=True):
            path = Path(directory) / "latest.json"
            raw = json.dumps(prior, indent=2).encode()
            for mutate in mutations:
                with self.subTest(mutate=mutate):
                    path.write_bytes(raw)
                    candidate = fixture("2026-11-28")
                    mutate(candidate)
                    with self.assertRaises(OutputRejected):
                        report_json.write_report_json(candidate, directory, source_user_id="owner")
                    self.assertEqual(path.read_bytes(), raw)

    def test_boundary_group_changes_and_cold_start(self):
        prior = fixture()
        candidate = fixture("2026-11-28", 17)
        candidate["groups"][0]["key"] = "custom_new"
        candidate["groups"].append({"key": "empty", "market": "US", "items": []})
        candidate["worldIndices"] = candidate["worldIndices"][:17]
        candidate["sectors"] = []
        report_json.validate_report(candidate, prior)
        with tempfile.TemporaryDirectory() as directory, patch.object(report_json, "is_public_report_owner", return_value=True):
            path = Path(directory) / "latest.json"
            path.write_text(json.dumps(prior))
            report_json.write_report_json(candidate, directory, source_user_id="owner")
            self.assertEqual(json.loads(path.read_text()), candidate)
        small = fixture(n=19)
        small["worldIndices"] = small["worldIndices"][:4]
        empty = fixture(n=0)
        empty["worldIndices"] = []
        report_json.validate_report(empty, small)
        report_json.validate_report(empty, None)
        empty["markets"][0]["indices"] = []
        with self.assertRaises(OutputRejected):
            report_json.validate_report(empty, None)

    def test_duplicate_group_members_cannot_hide_drop(self):
        prior = fixture()
        candidate = fixture(n=16)
        candidate["groups"].append({**deepcopy(candidate["groups"][0]), "key": "copy"})
        with self.assertRaisesRegex(OutputRejected, "TW groups"):
            report_json.validate_report(candidate, prior)


class DailyOutputStatusTest(unittest.TestCase):
    def test_actual_main_preserves_line_and_continues_after_gate_rejection(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "RUNNER_TEMP": directory, "LINE_USER_ID": "owner", "SUPABASE_USER_ID": "owner"
        }):
            path = Path(directory) / "latest.json"
            old = fixture()
            raw = json.dumps(old, indent=2).encode()
            path.write_bytes(raw)
            candidate = fixture("2026-11-28")
            candidate["markets"][0]["indices"] = []
            line, universe, tpex, push = Mock(), Mock(), Mock(), Mock()
            def write(payload, **kwargs):
                line.assert_called_once()
                return report_json.write_report_json(payload, directory, **kwargs)
            mocks = dict(
                is_public_report_owner=Mock(return_value=True), load_configs_from_supabase=Mock(return_value=({}, {}, {})),
                get_tw_tickers=Mock(return_value=[]), get_us_tickers=Mock(return_value=[]),
                collect_market_frames=Mock(return_value={}), scan_market=Mock(return_value=[]),
                BACKFILL_ENABLED=False, update_tpex_otc_index_history=Mock(),
                process_index_charts=Mock(return_value=[]), process_sector_weekly_charts=Mock(return_value=([], {})),
                build_report_universe=Mock(return_value=({}, {})), _market_scan_report=Mock(return_value=[]),
                process_group_for_data_dict=Mock(), get_sorted_custom_group_keys=Mock(return_value=[]),
                build_index_map=Mock(return_value={}), append_index_market_report=Mock(return_value=[]),
                build_sector_monday_message=Mock(return_value="sectors"), build_sector_line_message=Mock(return_value="sectors"),
                send_line_messages_combined=line, build_report_payload=Mock(return_value=candidate),
                write_report_json=Mock(side_effect=write), write_report_universe=universe, write_tpex_otc_series=tpex,
                prune_stale_csv=Mock(), push_report_to_github=push,
            )
            def check_status():
                return subprocess.run([sys.executable, str(ROOT / "scripts/report_run_status.py")],
                                      capture_output=True, text=True, timeout=10).returncode
            with patch.multiple(main, **mocks), patch.object(main.kline, "flush_journal"), patch.object(report_json, "is_public_report_owner", return_value=True):
                self.assertIsNone(main.main())
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(report_run_status._read(), ["latest.json: OutputRejected"])
                self.assertEqual(check_status(), 1)
                line.assert_called_once()
                universe.assert_called_once()
                tpex.assert_called_once()
                push.assert_called_once()
                line.reset_mock()
                candidate["markets"][0]["indices"] = [{"symbol": "^TWII", "close": 100}]
                self.assertIsNone(main.main())
                self.assertEqual(json.loads(path.read_text()), candidate)
                self.assertEqual(report_run_status._read(), [])
                self.assertEqual(check_status(), 0)
                line.reset_mock()
                universe.side_effect = OSError("universe error")
                tpex.side_effect = ValueError("series error")
                self.assertIsNone(main.main())
                self.assertEqual(report_run_status._read(), ["universe.json write: OSError", "TPEx series: ValueError"])
                self.assertEqual(check_status(), 1)
                self.assertEqual(push.call_count, 3)

    def test_status_check_missing_file_is_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RUNNER_TEMP": directory}):
            self.assertEqual(report_run_status.main(), 1)
            report_run_status.reset_status()
            self.assertEqual(report_run_status.main(), 0)


if __name__ == "__main__":
    unittest.main()

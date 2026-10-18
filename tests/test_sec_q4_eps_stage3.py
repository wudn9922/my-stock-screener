"""Offline stage-three selection and comparison checks; no SEC network calls."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
STAGE3 = ROOT / "docs/report/sec-q4-eps-stage3"
sys.path.insert(0, str(STAGE3))
import stage3_poc as stage3  # noqa: E402


class StageThreeSelectionTests(unittest.TestCase):
    def test_sample_uses_new_seed_and_excludes_every_stage_two_control_ticker(self):
        with patch.object(stage3.stage2.Stage2SecClient, "get", side_effect=AssertionError("network forbidden")):
            sample = stage3.build_sample("offline-stage3-selection-fixture")
        stage3.check_sample(sample)
        stage2_sample = stage3.stage2.load_json(stage3.STAGE2 / "sample.json")
        old_tickers = {row["symbol"] for row in stage2_sample["controls"]}
        new_tickers = {row["symbol"] for row in sample["controls"]}
        self.assertNotEqual(sample["selection_seed"], stage3.stage2.SEED)
        self.assertEqual(len(sample["missing_companies"]), 60)
        self.assertEqual(len(sample["controls"]), 60)
        self.assertEqual(len(old_tickers), 60)
        self.assertFalse(old_tickers.intersection(new_tickers))
        self.assertTrue(all(row["answer_key_mod_8"] in {4, 5, 6, 7}
                            for row in sample["controls"]))
        self.assertTrue(all(len(company["periods"]) in (1, 2)
                            for company in sample["missing_companies"]))
        self.assertEqual(sample["selection_audit"]["missing_company_count"], 60)


class StageThreeControlComparisonTests(unittest.TestCase):
    def test_comparison_counts_correct_sign_error_and_unverifiable_control(self):
        fixture_path = ROOT / "tests/fixtures/sec_q4_eps_stage3/control_comparison.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        result = stage3.compare_control_rows(
            fixture["sample"], fixture["extraction"], fixture["answer_set"])
        for key, expected in fixture["expected_metrics"].items():
            self.assertEqual(result["metrics"][key], expected)
        self.assertFalse(result["metrics"]["acceptance_bar"]["passed"])
        self.assertTrue(result["rows"][1]["sign_error"])
        self.assertIsNone(result["rows"][2]["correct_within_0_005"])


if __name__ == "__main__":
    unittest.main()

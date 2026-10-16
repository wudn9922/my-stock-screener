"""Synthetic checks that Basic EPS validation uses Basic shares and split basis."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atlas/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import reconcile_q4_eps_sec as reconcile  # noqa: E402


def share_fact(concept, value):
    return {"unit": "shares", "value": value, "start": "2023-07-01", "end": "2023-09-30",
            "form": "10-Q", "filed": "2023-11-01", "accession": "0000001234-23-000010"}


class Q4EpsReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.document = {
            "quarterly": {
                "fy": [2023, 2023, 2023],
                "fq": [2, 3, 4],
                "end": ["2023-06-30", "2023-09-30", "2023-12-31"],
                "values": {"dilutedShares": [2200, 4000, None]},
            },
            "split": {"events": [["2024-01-02", 2]]},
        }
        self.raw = {"concepts": {
            "WeightedAverageNumberOfSharesOutstandingBasic": [share_fact(
                "WeightedAverageNumberOfSharesOutstandingBasic", 1000)],
            "WeightedAverageNumberOfDilutedSharesOutstanding": [share_fact(
                "WeightedAverageNumberOfDilutedSharesOutstanding", 2000)],
        }}
        self.candidate = {"filed": "2023-11-01", "accession": "0000001234-23-000010"}

    def test_basic_crosscheck_uses_basic_shares_adjusted_with_seed_suffix_factor(self):
        shares, source = reconcile._basic_q3_shares(
            self.document, 2023, self.raw, self.candidate)
        self.assertEqual(shares, 2000)
        self.assertEqual(source["concept"], "WeightedAverageNumberOfSharesOutstandingBasic")
        self.assertEqual(source["rawValue"], 1000)
        self.assertEqual(source["splitFactor"], 2)
        self.assertEqual(source["splitBasis"].split(";")[0], "current financials basis")

    def test_missing_basic_share_fact_is_not_replaced_with_diluted_shares(self):
        self.raw["concepts"].pop("WeightedAverageNumberOfSharesOutstandingBasic")
        shares, source = reconcile._basic_q3_shares(
            self.document, 2023, self.raw, self.candidate)
        self.assertIsNone(shares)
        self.assertEqual(source["failureReason"], "missing-or-conflicting-q3-basic-shares")


if __name__ == "__main__":
    unittest.main()

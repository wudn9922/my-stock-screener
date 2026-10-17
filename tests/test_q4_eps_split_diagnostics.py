"""Regression tests for split normalization and formula-only Q4 EPS diagnostics."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_financials as financials  # noqa: E402


class Q4EpsSplitSafetyTests(unittest.TestCase):
    def test_eps_to_income_share_ratio_never_rewrites_reported_values(self):
        period = {"end": "2022-12-31", "src": "sec", "values": {
            "netIncome": 100, "dilutedShares": 100, "epsDiluted": 2.0, "epsBasic": 1.9,
        }}
        before = dict(period["values"])
        changed = financials.repair_split_eps([[period]], [["2023-06-01", 2]])
        self.assertEqual(changed, 0)
        self.assertEqual(period["values"], before)

    def test_independently_recorded_split_and_share_counts_still_normalize_basis(self):
        old = {"end": "2022-12-31", "src": "sec", "values": {
            "dilutedShares": 100, "epsDiluted": 2.0, "epsBasic": 1.9,
        }}
        new = {"end": "2023-12-31", "src": "sec", "values": {
            "dilutedShares": 200, "epsDiluted": 1.0, "epsBasic": 0.95,
        }}
        changed = financials.adjust_splits([[old, new]], [["2023-06-01", 2]])
        self.assertTrue(changed)
        self.assertEqual(old["values"]["dilutedShares"], 200)
        self.assertEqual(old["values"]["epsDiluted"], 1.0)
        self.assertEqual(old["values"]["epsBasic"], 0.95)


if __name__ == "__main__":
    unittest.main()

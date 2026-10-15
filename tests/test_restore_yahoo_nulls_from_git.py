"""Offline regression tests for the one-time pre-refresh Yahoo null restoration."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import restore_yahoo_nulls_from_git as restore  # noqa: E402


class YahooNullRestorationTests(unittest.TestCase):
    @staticmethod
    def block(source, values, *, end="2024-12-31", fy=2024):
        return {
            "end": [end],
            "src": [source],
            "fy": [fy],
            "values": {field: [value] for field, value in values.items()},
        }

    def test_fills_only_sec_null_cells_without_mutating_inputs_or_periods(self):
        current = self.block("sec", {"revenue": 100, "costOfRevenue": None, "grossProfit": None})
        yahoo = self.block("yahoo", {"revenue": 90, "costOfRevenue": 60, "grossProfit": 40})

        repaired, rows, excluded = restore.restore_section(current, yahoo, "annual", "2026-10-15")

        self.assertEqual(repaired["values"]["revenue"], [100])
        self.assertEqual(repaired["values"]["costOfRevenue"], [60])
        self.assertEqual(repaired["values"]["grossProfit"], [40])
        self.assertEqual(repaired["end"], ["2024-12-31"])
        self.assertEqual(repaired["src"], ["sec"])
        self.assertEqual(rows, [{"end": "2024-12-31", "fields": ["costOfRevenue", "grossProfit"]}])
        self.assertEqual(excluded, [])
        self.assertIsNone(current["values"]["costOfRevenue"][0])
        self.assertEqual(yahoo["values"]["grossProfit"], [40])

    def test_partial_eps_fill_requires_matching_share_basis_and_is_idempotent(self):
        current = self.block("sec", {"epsDiluted": None, "epsBasic": None, "dilutedShares": 10_000_000})
        yahoo = self.block("yahoo", {"epsDiluted": 1.25, "epsBasic": 1.24, "dilutedShares": 10_000_000})

        blocked, blocked_rows, _ = restore.restore_section(current, yahoo, "annual", "2026-10-15")
        self.assertEqual(blocked_rows, [])

        repaired, rows, _ = restore.restore_section(
            current, yahoo, "annual", "2026-10-15", allow_matching_share_basis=True,
        )
        self.assertEqual(repaired["values"]["epsDiluted"], [1.25])
        self.assertEqual(repaired["values"]["epsBasic"], [1.24])
        self.assertEqual(repaired["values"]["dilutedShares"], [10_000_000])
        self.assertEqual(rows, [{"end": "2024-12-31", "fields": ["epsBasic", "epsDiluted"]}])

        repeated, repeated_rows, _ = restore.restore_section(
            repaired, yahoo, "annual", "2026-10-15", allow_matching_share_basis=True,
        )
        self.assertEqual(repeated, repaired)
        self.assertEqual(repeated_rows, [])

    def test_partial_eps_fill_rejects_a_different_share_basis(self):
        current = self.block("sec", {"epsDiluted": None, "epsBasic": None, "dilutedShares": 10_500_000})
        yahoo = self.block("yahoo", {"epsDiluted": 1.25, "epsBasic": 1.24, "dilutedShares": 10_000_000})

        repaired, rows, _ = restore.restore_section(
            current, yahoo, "annual", "2026-10-15", allow_matching_share_basis=True,
        )

        self.assertEqual(repaired, current)
        self.assertEqual(rows, [])

    def test_old_operating_income_alias_does_not_shadow_sec_value(self):
        current = self.block("sec", {"operatingIncome": 20, "operatingIncomeReported": None})
        yahoo = self.block("yahoo", {"operatingIncome": 16, "operatingIncomeReported": 15})

        repaired, rows, excluded = restore.restore_section(current, yahoo, "annual", "2026-10-15")

        self.assertEqual(repaired["values"]["operatingIncome"], [20])
        self.assertEqual(repaired["values"]["operatingIncomeReported"], [None])
        self.assertNotIn("operatingIncomeReported", rows[0]["fields"] if rows else [])
        self.assertEqual(excluded, [{
            "end": "2024-12-31",
            "fields": {"operatingIncomeReported": "sec_operating_income_preferred"},
        }])

    def test_conflicting_gross_profit_does_not_override_sec_revenue_cost_margin(self):
        current = self.block("sec", {"revenue": 100, "costOfRevenue": 60, "grossProfit": None})
        yahoo = self.block("yahoo", {"revenue": 100, "costOfRevenue": 60, "grossProfit": 30})

        repaired, rows, excluded = restore.restore_section(current, yahoo, "annual", "2026-10-15")

        self.assertEqual(repaired["values"]["grossProfit"], [None])
        self.assertEqual(rows, [])
        self.assertEqual(excluded[0]["fields"]["grossProfit"], "conflicts_with_revenue_minus_cost")

    def test_conflicting_cost_does_not_override_sec_revenue_gross_profit_margin(self):
        current = self.block("sec", {"revenue": 100, "costOfRevenue": None, "grossProfit": 40})
        yahoo = self.block("yahoo", {"revenue": 100, "costOfRevenue": 70, "grossProfit": 30})

        repaired, rows, excluded = restore.restore_section(current, yahoo, "annual", "2026-10-15")

        self.assertEqual(repaired["values"]["costOfRevenue"], [None])
        self.assertEqual(repaired["values"]["grossProfit"], [40])
        self.assertEqual(rows, [])
        self.assertEqual(excluded[0]["fields"]["costOfRevenue"], "conflicts_with_sec_revenue_and_gross_profit")

    def test_split_basis_requires_explicit_matching_metadata(self):
        base = {"split": {"adjusted": False, "events": []}}
        self.assertTrue(restore.split_metadata_matches(base, base))
        self.assertFalse(restore.split_metadata_matches(base, {"split": {"adjusted": True, "events": []}}))
        self.assertFalse(restore.split_metadata_matches(base, {"split": {"adjusted": False, "events": [["2020-01-01", 2]]}}))
        self.assertFalse(restore.split_metadata_matches({}, {}))


if __name__ == "__main__":
    unittest.main()

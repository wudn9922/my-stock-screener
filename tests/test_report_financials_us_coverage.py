"""Offline coverage checks for the tracked US financial history report generator."""
import os
import json
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import report_financials_us_coverage as coverage  # noqa: E402


class FinancialCoverageTests(unittest.TestCase):
    def test_refresh_log_bytes_are_distinguished_from_cached_sec_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "refresh.log")
            manifest_path = os.path.join(tmp, "manifest.json")
            with open(log_path, "w", encoding="utf-8") as output:
                output.write("requests: 3, received 100 bytes, written 25 bytes\n")
            with open(manifest_path, "w", encoding="utf-8") as output:
                json.dump({"responses": {
                    "one": {"file": "a.body", "bytes": 40},
                    "two": {"file": "a.body", "bytes": 40},
                    "three": {"file": "b.body", "bytes": 60},
                }}, output)

            summary = coverage.parse_refresh_summary(log_path, {}, manifest_path)

        self.assertEqual(summary["requests"]["receivedBytes"], 100)
        self.assertEqual(summary["requests"]["financialAndIndexJsonBytesWritten"], 25)
        self.assertEqual(summary["rawSecCache"], {"uniqueBodies": 2, "uniqueRawBodyBytes": 100})

    def test_counts_the_same_fallbacks_as_the_financials_page(self):
        values = {
            "epsDiluted": [None],
            "epsBasic": [2.5],
            "revenue": [100],
            "costOfRevenue": [60],
            "grossProfit": [None],
            "operatingIncomeReported": [None],
            "operatingIncome": [15],
            "pretaxIncome": [12],
            "netIncome": [10],
        }
        found = coverage.metric_availability(values, 0)
        self.assertTrue(all(available for available, _ in found.values()))
        self.assertEqual(coverage.chosen_operating_income(values, 0), 15)
        self.assertEqual(coverage.metric_availability(values, 0)["nonOperatingIncome"], (True, None))

    def test_null_cost_can_be_displayed_from_same_period_gross_profit(self):
        values = {
            "revenue": [100, 100],
            "grossProfit": [40, None],
            "costOfRevenue": [None, None],
            "netIncome": [10, None],
        }
        first = coverage.metric_availability(values, 0)
        second = coverage.metric_availability(values, 1)
        self.assertEqual(first["grossMargin"], (True, None))
        self.assertEqual(first["costOfRevenue"], (True, None))
        self.assertEqual(coverage.displayed_cost_of_revenue(values, 0), (60, "derived"))
        self.assertEqual(second["grossMargin"], (False, "missing_gross_profit_and_cost"))
        self.assertEqual(second["costOfRevenue"], (False, "missing_gross_profit"))
        self.assertEqual(second["netMargin"], (False, "missing_net_income"))

    def test_git_baseline_cost_counts_only_raw_reported_values(self):
        values = {"revenue": [100], "grossProfit": [40], "costOfRevenue": [None]}
        self.assertEqual(coverage.displayed_cost_of_revenue(values, 0, allow_derived=False), (None, None))
        self.assertEqual(coverage.metric_availability(values, 0, allow_derived_cost=False)["costOfRevenue"], (False, "missing_reported_cost"))

    def test_cost_of_revenue_display_counts_only_reported_or_safe_derived_values(self):
        values = {
            "revenue": [100, 100, 100, -10],
            "grossProfit": [40, -10, 120, -20],
            "costOfRevenue": [75, None, None, None],
        }
        self.assertEqual(coverage.displayed_cost_of_revenue(values, 0), (75, "reported"))
        self.assertEqual(coverage.displayed_cost_of_revenue(values, 1), (110, "derived"))
        self.assertEqual(coverage.displayed_cost_of_revenue(values, 2), (None, None))
        self.assertEqual(coverage.displayed_cost_of_revenue(values, 3), (None, None))
        self.assertEqual(coverage.metric_availability(values, 1)["costOfRevenue"], (True, None))

    def test_calendar_cutoff_latest_window_and_gap_detection_are_separate(self):
        block = {
            "end": ["2011-09-30", "2012-09-30", "2013-09-30", "2015-09-30"],
            "values": {"revenue": [1, 2, 3, 4]},
        }
        calendar = coverage.period_coverage(block, "annual", coverage.date(2011, 10, 15), None)
        latest = coverage.period_coverage(block, "annual", coverage.date(2011, 10, 15), 3)
        self.assertEqual(calendar["periods"], 3)
        self.assertEqual(calendar["first"], "2012-09-30")
        self.assertEqual(calendar["gaps"], [{"from": "2013-09-30", "to": "2015-09-30", "days": 730}])
        self.assertEqual(latest["periods"], 3)
        self.assertEqual(latest["metrics"]["revenue"]["available"], 3)
        self.assertEqual(coverage.gap_rows(["2024-03-31", "2024-06-30", "2024-12-31"], "quarterly")[0]["days"], 184)

    def test_target_completeness_does_not_count_a_short_but_populated_history(self):
        block = {
            "end": ["2023-12-31", "2024-12-31", "2025-12-31"],
            "values": {
                "epsDiluted": [1, 1, 1],
                "revenue": [100, 100, 100],
                "costOfRevenue": [60, 60, 60],
                "grossProfit": [40, 40, 40],
                "operatingIncomeReported": [20, 20, 20],
                "pretaxIncome": [25, 25, 25],
                "netIncome": [15, 15, 15],
            },
        }
        annual = coverage.period_coverage(block, "annual", coverage.date(2011, 10, 15), 15)
        empty = coverage.period_coverage({"end": [], "values": {}}, "quarterly", coverage.date(2011, 10, 15), 60)
        row = {"windows": {"latestRecords": {"annual": annual, "quarterly": empty}}}
        result = coverage.aggregate({"IPO": row}, "latestRecords")["annual"]
        self.assertEqual(result["metrics"]["revenue"]["symbolsCompleteWithinObservedPeriods"], 1)
        self.assertEqual(result["metrics"]["revenue"]["symbolsWithTargetCompletePeriods"], 0)
        self.assertEqual(result["symbolsWithAllSevenMetricsCompleteAtTarget"], 0)


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.financial_history_audit import (
    analyze_company,
    calculate_data_delta,
    fiscal_rows,
    period_field_counts,
    stale_source_crosscheck_rows,
)
from scripts.verify_financial_history_sources import merge_crosscheck_rows, period_map


class FinancialHistoryAuditTests(unittest.TestCase):
    def test_uses_fiscal_labels_for_full_ten_year_coverage(self):
        fields = ("revenue", "netIncome", "totalAssets", "equity", "operatingCashFlow")
        annual = {
            "end": [f"{year}-09-30" for year in range(2016, 2026)],
            "fy": list(range(2016, 2026)),
            "src": ["sec"] * 10,
            "values": {field: [1_000] * 10 for field in fields},
        }
        quarterly = {
            "end": [f"{year}-{quarter * 3:02d}-28" for year in range(2016, 2026) for quarter in range(1, 5)],
            "fy": [year for year in range(2016, 2026) for _ in range(4)],
            "fq": [quarter for _ in range(10) for quarter in range(1, 5)],
            "src": ["sec"] * 40,
            "values": {field: [250] * 40 for field in fields},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SAMPLE.json"
            path.write_text(json.dumps({"currency": "USD", "source": "SEC", "annual": annual, "quarterly": quarterly}), encoding="utf-8")
            issues = []
            row, _ = analyze_company("SAMPLE", "US", "Sample", "NASDAQ", True, "", "", {}, path, {"cik": "0000000001"}, issues)

        self.assertTrue(row["annualTenYearComplete"])
        self.assertTrue(row["quarterFortyComplete"])
        self.assertTrue(row["coreAnnualComplete"])
        self.assertTrue(row["coreQuarterlyComplete"])
        self.assertEqual(row["annualFY2016_2025Present"], 10)
        self.assertEqual(row["quarterFY2016_2025Present"], 40)

    def test_flags_duplicate_fiscal_year_and_misaligned_columns(self):
        issues = []
        rows = fiscal_rows({"fy": [2016, 2016], "end": ["2016-12-31"], "values": {"revenue": [10, 20]}},
                           "annual", "SAMPLE", issues)
        self.assertEqual(len(rows), 2)
        self.assertTrue(any(item["category"] == "structure" for item in issues))

    def test_field_completeness_ignores_duplicate_periods(self):
        records = [[
            {"fy": 2016, "values": {"revenue": 10}},
            {"fy": 2016, "values": {"revenue": 11}},
            {"fy": 2017, "values": {"revenue": 20}},
        ]]
        present, fields = period_field_counts(records, "annual")
        self.assertEqual(present, 1)
        self.assertEqual(fields["revenue"], 1)

    def test_delta_counts_deleted_financial_file_and_rejects_unknown_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "docs/report/financials/TEST.json"
            path.parent.mkdir(parents=True)
            body = json.dumps({
                "market": "US",
                "annual": {"fy": [2020], "values": {"revenue": [12]}},
                "quarterly": {"fy": [2020], "fq": [1], "values": {"revenue": [3]}},
            })
            path.write_text(body, encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "add", "docs/report/financials/TEST.json"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            path.unlink()

            delta = calculate_data_delta(root, base)
            self.assertEqual(delta["changedFinancialFiles"], 1)
            self.assertEqual(delta["markets"]["US"]["annualPeriodsRemoved"], 1)
            self.assertEqual(delta["markets"]["US"]["quarterPeriodsRemoved"], 1)
            self.assertEqual(delta["markets"]["US"]["netBytesAdded"], -len(body.encode("utf-8")))
            with self.assertRaises(ValueError):
                calculate_data_delta(root, "missing-revision")

    def test_source_crosscheck_uses_project_fiscal_calendar_rules(self):
        class ExtractedFacts:
            annual = [{"end": "2025-09-27", "values": {"revenue": 100}}]
            quarterly = [{"end": "2025-09-27", "values": {"revenue": 25}}]

        self.assertIn((2025,), period_map(ExtractedFacts, "annual"))
        self.assertIn((2025, 4), period_map(ExtractedFacts, "quarterly"))

    def test_crosscheck_append_refreshes_matching_symbol_and_cik(self):
        existing = [
            {"symbol": "AAPL", "cik": "0000320193", "status": "old"},
            {"symbol": "XOM", "cik": "0000034088", "status": "historical"},
        ]
        refreshed = [{"symbol": "AAPL", "cik": "0000320193", "status": "new"}]
        merged = merge_crosscheck_rows(existing, refreshed)
        self.assertEqual(merged, [existing[1], refreshed[0]])

    def test_stale_crosscheck_checks_file_hash_and_current_cik(self):
        rows = [
            {"symbol": "AAPL", "cik": "0000320193", "financialFileSha256": "current", "sampleGroup": "large-cap"},
            {"symbol": "OLD", "cik": "0000000001", "financialFileSha256": "current", "sampleGroup": "old:cik-override"},
            {"symbol": "WMT", "cik": "0000104169", "financialFileSha256": "old", "sampleGroup": "retail"},
        ]
        stale = stale_source_crosscheck_rows(
            rows,
            {"AAPL": "current", "OLD": "current", "WMT": "current"},
            {"AAPL": {"cik": "0000320193"}, "OLD": {"cik": "0000000002"}, "WMT": {"cik": "0000104169"}},
        )
        self.assertEqual([row["symbol"] for row in stale], ["WMT"])


if __name__ == "__main__":
    unittest.main()

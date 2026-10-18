"""Offline tests for the read-only fiscal-period-end mismatch detector on synthetic documents."""
from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "atlas" / "scripts"))

import detect_fiscal_period_mismatch as detector  # noqa: E402


def make_document(symbol="SYN", market="US", annual=(), quarterly=()):
    return {
        "symbol": symbol,
        "market": market,
        "annual": {"end": [end for _, end in annual], "fy": [fy for fy, _ in annual]},
        "quarterly": {
            "end": [end for _, _, end in quarterly],
            "fy": [fy for fy, _, _ in quarterly],
            "fq": [fq for _, fq, _ in quarterly],
        },
    }


# A 52/53-week style calendar: 2021 Q4 ends 25 December (15 weeks), and 2022 Q4 ends 31 December (16 weeks).
CLEAN_QUARTERS = [
    (2021, 1, "2021-03-27"), (2021, 2, "2021-06-19"), (2021, 3, "2021-09-11"), (2021, 4, "2021-12-25"),
    (2022, 1, "2022-03-26"), (2022, 2, "2022-06-18"), (2022, 3, "2022-09-10"), (2022, 4, "2022-12-31"),
]
CLEAN_ANNUAL = [(2021, "2021-12-25"), (2022, "2022-12-31")]


def analyze(**kwargs):
    return detector.analyze(make_document(**kwargs))


class Rule1Q4AgainstAnnualTests(unittest.TestCase):
    def test_q4_end_more_than_seven_days_from_annual_end_is_flagged(self):
        result = analyze(annual=[(2018, "2018-12-31")], quarterly=[(2018, 4, "2018-03-31")])

        self.assertIn(detector.Q4_VS_ANNUAL, result["rules"])
        [q4] = result["affectedQ4"]
        self.assertEqual(q4["end"], "2018-03-31")
        self.assertEqual(q4["annualEnds"], ["2018-12-31"])
        self.assertEqual(q4["nearestAnnualEnd"], "2018-12-31")
        self.assertEqual(q4["daysFromNearestAnnualEnd"], -275)
        self.assertIn(detector.Q4_VS_ANNUAL, q4["rules"])

    def test_q4_end_eight_days_out_is_flagged_and_six_days_out_is_not(self):
        flagged = analyze(annual=[(2018, "2018-12-31")], quarterly=[(2018, 4, "2019-01-08")])
        accepted = analyze(annual=[(2018, "2018-12-29")], quarterly=[(2018, 4, "2019-01-04")])

        self.assertEqual(len(flagged["affectedQ4"]), 1)
        self.assertEqual(accepted["rules"], [])

    def test_q4_without_same_fy_annual_row_is_not_evaluated(self):
        result = analyze(annual=[(2017, "2017-12-31")], quarterly=[(2018, 4, "2018-03-31")])

        self.assertNotIn(detector.Q4_VS_ANNUAL, result["rules"])
        self.assertEqual(result["q4RowsWithoutSameFyAnnual"], 1)
        self.assertEqual(result["q4RowsEvaluated"], 0)

    def test_q4_matching_any_duplicate_annual_end_of_a_transition_year_is_not_flagged(self):
        result = analyze(
            annual=[(2017, "2017-03-31"), (2017, "2017-12-31")],
            quarterly=[(2017, 4, "2017-12-31")],
        )

        self.assertNotIn(detector.Q4_VS_ANNUAL, result["rules"])
        self.assertNotIn(detector.QUARTER_CALENDAR, result["rules"])
        self.assertEqual(result["rules"], [detector.FYE_MONTH_CHANGE])

    def test_unparseable_q4_end_is_counted_and_not_evaluated(self):
        result = analyze(annual=[(2018, "2018-12-31")], quarterly=[(2018, 4, None)])

        self.assertEqual(result["q4Rows"], 1)
        self.assertEqual(result["q4RowsUnparsedEnd"], 1)
        self.assertEqual(result["rules"], [])


class Rule2QuarterSequenceTests(unittest.TestCase):
    def test_gap_that_is_not_a_whole_number_of_quarters_is_flagged_on_the_later_row(self):
        result = analyze(quarterly=[
            (2010, 3, "2010-08-31"), (2010, 4, "2010-11-30"), (2011, 1, "2011-03-31"),
        ])

        self.assertEqual(result["affectedQ4"], [])
        [flag] = result["otherQuarterFlags"]
        self.assertEqual((flag["fy"], flag["fq"], flag["end"]), (2011, 1, "2011-03-31"))
        self.assertEqual(flag["rules"], [detector.QUARTER_SPACING])
        self.assertEqual(flag["previousQuarter"], {"fy": 2010, "fq": 4, "end": "2010-11-30", "days": 121})

    def test_fiscal_quarter_label_that_does_not_advance_with_the_date_gap_is_flagged(self):
        result = analyze(quarterly=[(2017, 1, "2017-03-31"), (2017, 3, "2017-06-30")])

        self.assertEqual([(f["fq"], f["rules"]) for f in result["otherQuarterFlags"]],
                         [(3, [detector.QUARTER_SPACING])])

    def test_sixteen_week_fiscal_quarter_is_accepted(self):
        result = analyze(annual=CLEAN_ANNUAL, quarterly=CLEAN_QUARTERS)

        self.assertEqual(result["rules"], [])

    def test_label_consistent_gap_across_missing_quarters_is_accepted(self):
        result = analyze(quarterly=[(2017, 1, "2017-03-31"), (2017, 4, "2017-12-31")])

        self.assertEqual(result["otherQuarterFlags"], [])

    def test_quarter_labelled_in_the_wrong_fiscal_year_is_flagged_by_the_annual_calendar_rule(self):
        # The same shape as TDW: the annual end is 31 December but fq 1 is dated 30 June of the prior year.
        result = analyze(
            annual=[(2018, "2018-12-31")],
            quarterly=[(2018, 1, "2017-06-30"), (2018, 4, "2018-03-31")],
        )

        [flag] = result["otherQuarterFlags"]
        self.assertEqual((flag["fq"], flag["end"]), (1, "2017-06-30"))
        self.assertEqual(flag["rules"], [detector.QUARTER_CALENDAR])
        self.assertIn(detector.Q4_VS_ANNUAL, result["affectedQ4"][0]["rules"])
        self.assertIn(detector.QUARTER_CALENDAR, result["affectedQ4"][0]["rules"])


class Rule3FiscalYearEndMonthTests(unittest.TestCase):
    def test_first_annual_year_end_month_change_is_reported(self):
        result = analyze(annual=[
            (2016, "2016-03-31"), (2017, "2017-03-31"), (2018, "2018-12-31"), (2019, "2019-12-31"),
        ])

        self.assertEqual(result["fyeChanges"]["annual"], {
            "fy": 2018,
            "end": "2018-12-31",
            "fromEnd": "2017-03-31",
            "fromMonth": 3,
            "toMonth": 12,
            "changeCount": 1,
        })
        self.assertIsNone(result["fyeChanges"]["q4"])
        self.assertEqual(result["rules"], [detector.FYE_MONTH_CHANGE])

    def test_first_fq4_month_change_is_reported_separately(self):
        result = analyze(quarterly=[
            (2023, 4, "2023-12-31"), (2024, 4, "2025-01-31"), (2025, 4, "2026-01-31"),
        ])

        self.assertEqual(result["fyeChanges"]["q4"]["fy"], 2024)
        self.assertEqual(result["fyeChanges"]["q4"]["fromMonth"], 12)
        self.assertEqual(result["fyeChanges"]["q4"]["toMonth"], 1)
        self.assertIsNone(result["fyeChanges"]["annual"])
        self.assertEqual(result["rules"], [detector.FYE_MONTH_CHANGE])

    def test_january_52_53_week_year_end_is_still_december(self):
        result = analyze(annual=[(2019, "2020-01-02"), (2020, "2021-01-01"), (2021, "2021-12-31")])

        self.assertIsNone(result["fyeChanges"]["annual"])
        self.assertEqual(result["rules"], [])


class CleanFileTests(unittest.TestCase):
    def test_clean_file_with_consistent_calendar_is_not_flagged(self):
        result = analyze(annual=CLEAN_ANNUAL, quarterly=CLEAN_QUARTERS)

        self.assertEqual(result["rules"], [])
        self.assertEqual(result["affectedQ4"], [])
        self.assertEqual(result["otherQuarterFlags"], [])
        self.assertEqual(result["q4Rows"], 2)
        self.assertEqual(result["q4RowsEvaluated"], 2)


class BuildReportTests(unittest.TestCase):
    def write_fixture_directory(self, directory: Path) -> None:
        documents = {
            "TDW.json": make_document("TDW", annual=[(2018, "2018-12-31")],
                                      quarterly=[(2018, 1, "2017-06-30"), (2018, 4, "2018-03-31")]),
            "CLEAN.json": make_document("CLEAN", annual=CLEAN_ANNUAL, quarterly=CLEAN_QUARTERS),
            "TW1101.json": make_document("1101.TW", market="TW", annual=[(2018, "2018-12-31")],
                                         quarterly=[(2018, 4, "2018-03-31")]),
            "index.json": {"version": 1, "items": []},
        }
        for name, document in documents.items():
            (directory / name).write_text(json.dumps(document, sort_keys=True), encoding="utf-8")

    def test_report_totals_reconcile_and_known_examples_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write_fixture_directory(directory)
            report = detector.build_report(directory)

        totals = report["totals"]
        self.assertEqual(totals["usFiles"], 2)
        self.assertEqual(totals["usQ4Rows"], 3)
        self.assertEqual(totals["q4RowsEvaluatedAgainstAnnual"], 3)
        self.assertEqual(totals["filesFlagged"], 1)
        self.assertEqual(totals["q4RowsFlagged"], 1)
        self.assertEqual([entry["symbol"] for entry in report["flaggedFiles"]], ["TDW"])
        self.assertTrue(report["knownExamples"]["TDW"]["flagged"])
        self.assertFalse(report["knownExamples"]["BNC"]["present"])
        self.assertFalse(report["knownExamples"]["BNC"]["flagged"])

    def test_rebuild_is_byte_identical_and_writes_only_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            financials = Path(tmp) / "financials"
            financials.mkdir()
            self.write_fixture_directory(financials)
            before = sorted(path.name for path in financials.iterdir())
            out = Path(tmp) / "report" / "fiscal-period-mismatch.json"

            self.assertEqual(detector.main(["--financials", str(financials), "--out", str(out)]), 0)
            first = out.read_bytes()
            self.assertEqual(detector.main(["--financials", str(financials), "--out", str(out)]), 0)
            second = out.read_bytes()
            after = sorted(path.name for path in financials.iterdir())

        self.assertEqual(first, second)
        self.assertEqual(before, after)
        text = first.decode("utf-8")
        self.assertEqual(text, json.dumps(json.loads(text), sort_keys=True, indent=2, ensure_ascii=False) + "\n")
        self.assertNotIn("generatedAt", text)

    def test_refuses_to_write_under_the_financials_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            financials = Path(tmp) / "financials"
            financials.mkdir()
            self.write_fixture_directory(financials)
            target = financials / "fiscal-period-mismatch.json"

            self.assertEqual(detector.main(["--financials", str(financials), "--out", str(target)]), 2)
            self.assertFalse(target.exists())


class NoNetworkTests(unittest.TestCase):
    def test_module_imports_no_network_library(self):
        source = (ROOT / "atlas" / "scripts" / "detect_fiscal_period_mismatch.py").read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        forbidden = {"socket", "ssl", "http", "urllib", "requests", "ftplib", "smtplib", "asyncio", "urllib3"}
        self.assertFalse(imported & forbidden, imported & forbidden)


if __name__ == "__main__":
    unittest.main()

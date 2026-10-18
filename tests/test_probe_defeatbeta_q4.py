"""Synthetic tests for atlas/scripts/probe_defeatbeta_q4.py: the matching rule (including the 7-day window), the agreement
tolerance, the Wilson bound, the sign-error count, the coverage tallies, the rule that no EPS value is written, the
offline guarantee, and the manual read-only workflow. Every input is built here; no dataset or financial file is read.
Run: python3 -m unittest discover -s tests -p "test_probe_defeatbeta_q4.py" -v
"""
import ast
import contextlib
import csv
import hashlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import probe_defeatbeta_q4 as probe  # noqa: E402

SCRIPT_PATH = os.path.join(SCRIPTS, "probe_defeatbeta_q4.py")
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "defeatbeta-probe.yml")
FORBIDDEN_IMPORTS = {"urllib", "urllib3", "requests", "http", "socket", "ssl", "curl_cffi", "httpx", "aiohttp",
                     "ftplib", "smtplib", "yfinance", "subprocess"}
CLASS_ORDER = ["no-q4-net-income", "no-annual-diluted-eps", "q1-q3-incomplete", "derivation-rejected", "ledger-blocked"]
CARD = "---\nlicense: apache-2.0\n---\n# Synthetic card\n\nSynthetic text for tests.\n"


def ordinal(text):
    return date.fromisoformat(text).toordinal()


def statement_row(symbol, report_date, value, item="diluted_eps", finance="income_statement", period="quarterly"):
    return {"symbol": symbol, "report_date": report_date, "item_name": item,
            "item_value": "" if value is None else repr(value), "finance_type": finance, "period_type": period}


def quarter(end, fy, fq, eps, src="sec", derived=None, grade=None):
    return {"end": end, "fy": fy, "fq": fq, "eps": eps, "src": src, "derived": derived, "grade": grade}


def us_document(quarters, annual=()):
    """annual: (fy, diluted EPS) pairs for the annual block."""
    return {"market": "US", "quarterly": {
        "end": [q["end"] for q in quarters],
        "fy": [q["fy"] for q in quarters],
        "fq": [q["fq"] for q in quarters],
        "src": [q["src"] for q in quarters],
        "derived": [q["derived"] for q in quarters],
        "values": {"epsDiluted": [q["eps"] for q in quarters]},
        "epsQuality": [({"epsDiluted": q["grade"]} if q["grade"] else None) for q in quarters],
    }, "annual": {
        "end": [f"{fy}-12-31" for fy, _ in annual],
        "fy": [fy for fy, _ in annual],
        "values": {"epsDiluted": [eps for _, eps in annual]},
    }}


def classified(symbol, end, cls, fy):
    return {"symbol": symbol, "end": end, "class": cls, "detail": "synthetic", "fy": fy}


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def index_of(self, rows):
        path = os.path.join(self.tmp, "rows.csv")
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=probe.CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return probe.read_statement(path)

    def run_probe(self, documents, classification_rows, statement_rows, card_text=CARD, sizes_override=None):
        run_dir = tempfile.mkdtemp(dir=self.tmp)
        repo = os.path.join(run_dir, "repo")
        financials = os.path.join(repo, "docs", "report", "financials")
        os.makedirs(financials)
        for symbol, document in documents.items():
            with open(os.path.join(financials, f"{symbol}.json"), "w", encoding="utf-8") as handle:
                json.dump(document, handle)
        with open(os.path.join(repo, "docs", "report", "q4-eps-missing-classification.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"rowDefinition": {"classOrder": CLASS_ORDER}, "counts": {}, "rows": classification_rows},
                      handle)
        card_path = os.path.join(run_dir, "README.md")
        card_bytes = card_text.encode("utf-8")
        with open(card_path, "wb") as handle:
            handle.write(card_bytes)
        csv_path = os.path.join(run_dir, "rows.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=probe.CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(statement_rows)
        sizes = sizes_override if sizes_override is not None else [
            {"file": "README.md", "bytes": len(card_bytes), "sha256": hashlib.sha256(card_bytes).hexdigest()},
            {"file": "data/US/stock_statement.parquet", "bytes": 12345, "sha256": "0" * 64},
        ]
        sizes_path = os.path.join(run_dir, "download-sizes.json")
        with open(sizes_path, "w", encoding="utf-8") as handle:
            json.dump(sizes, handle)
        out_dir = tempfile.mkdtemp(dir=run_dir)
        out = os.path.join(out_dir, "probe-report.json")
        args = ["--card", card_path, "--statement-csv", csv_path, "--download-sizes", sizes_path,
                "--repo-root", repo, "--out", out]
        with contextlib.redirect_stdout(io.StringIO()):
            probe.main(args)
        with open(out, "rb") as handle:
            body = handle.read()
        return json.loads(body.decode("utf-8")), body, args


class MatchingRule(ProbeCase):
    def test_exact_date_matches_with_zero_offset(self):
        index, _ = self.index_of([statement_row("AAA", "2024-06-30", 1.1)])
        self.assertEqual(probe.match(index, "AAA", ordinal("2024-06-30")), ("ok", 1.1, 0))

    def test_seven_days_either_side_matches_and_eight_does_not(self):
        index, _ = self.index_of([statement_row("AAA", "2024-06-30", 1.1)])
        self.assertEqual(probe.match(index, "AAA", ordinal("2024-06-23"))[0], "ok")
        self.assertEqual(probe.match(index, "AAA", ordinal("2024-07-07"))[0], "ok")
        self.assertEqual(probe.match(index, "AAA", ordinal("2024-06-22")), ("none", None, None))
        self.assertEqual(probe.match(index, "AAA", ordinal("2024-07-08")), ("none", None, None))

    def test_offset_is_signed_days_from_the_period_end(self):
        index, _ = self.index_of([statement_row("AAA", "2024-06-30", 1.1)])
        self.assertEqual(probe.match(index, "AAA", ordinal("2024-06-27"))[2], 3)
        self.assertEqual(probe.match(index, "AAA", ordinal("2024-07-03"))[2], -3)

    def test_nearest_date_wins(self):
        index, _ = self.index_of([statement_row("AAA", "2024-06-27", 1.0),
                                  statement_row("AAA", "2024-07-02", 2.0)])
        status, value, offset = probe.match(index, "AAA", ordinal("2024-06-30"))
        self.assertEqual((status, value, offset), ("ok", 2.0, 2))

    def test_equidistant_candidates_are_a_tie_and_do_not_match(self):
        index, _ = self.index_of([statement_row("AAA", "2024-06-27", 1.0),
                                  statement_row("AAA", "2024-07-03", 2.0)])
        self.assertEqual(probe.match(index, "AAA", ordinal("2024-06-30")), ("tie", None, None))

    def test_null_value_and_conflicting_values_do_not_match(self):
        index, stats = self.index_of([statement_row("AAA", "2024-06-30", None),
                                      statement_row("BBB", "2024-06-30", 1.0),
                                      statement_row("BBB", "2024-06-30", 1.5)])
        self.assertEqual(probe.match(index, "AAA", ordinal("2024-06-30"))[0], "null")
        self.assertEqual(probe.match(index, "BBB", ordinal("2024-06-30"))[0], "ambiguous")
        self.assertEqual(stats["conflictingDates"], 1)

    def test_other_symbol_never_matches(self):
        index, _ = self.index_of([statement_row("AAA", "2024-06-30", 1.1)])
        self.assertEqual(probe.match(index, "BBB", ordinal("2024-06-30")), ("none", None, None))

    def test_only_quarterly_income_statement_diluted_eps_rows_are_kept(self):
        index, stats = self.index_of([
            statement_row("AAA", "2024-06-30", 1.1),
            statement_row("AAA", "2024-06-30", 1.12, item="basic_eps"),
            statement_row("AAA", "2024-06-30", 4.4, period="annual"),
            statement_row("AAA", "2024-06-30", 9.9, finance="balance_sheet"),
            statement_row("AAA", "2024-09-30", 1.2, item="DILUTED_EPS"),
            statement_row("AAA", "TTM", 4.5),
        ])
        self.assertEqual(stats["rowsKept"], 3)
        self.assertEqual(stats["rowsSkippedDate"], 1)
        self.assertEqual(probe.match(index, "AAA", ordinal("2024-06-30")), ("ok", 1.1, 0))
        self.assertEqual(probe.match(index, "AAA", ordinal("2024-09-30")), ("ok", 1.2, 0))

    def test_no_diluted_rows_stops_the_probe(self):
        with self.assertRaises(probe.ProbeError):
            self.index_of([statement_row("AAA", "2024-06-30", 1.1, item="basic_eps")])

    def test_missing_columns_stop_the_probe(self):
        path = os.path.join(self.tmp, "bad.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("symbol,report_date\nAAA,2024-06-30\n")
        with self.assertRaises(probe.ProbeError):
            probe.read_statement(path)


class AgreementTolerance(unittest.TestCase):
    def test_floor_applies_to_small_values(self):
        self.assertAlmostEqual(probe.tolerance(0.2), 0.011)
        self.assertAlmostEqual(probe.tolerance(-0.2), 0.011)

    def test_relative_part_applies_to_large_values(self):
        self.assertAlmostEqual(probe.tolerance(400.0), 2.0)
        self.assertAlmostEqual(probe.tolerance(-400.0), 2.0)

    def test_agreement_inside_and_outside_the_tolerance(self):
        self.assertTrue(probe.agrees(1.0, 1.010))
        self.assertFalse(probe.agrees(1.0, 1.012))
        self.assertTrue(probe.agrees(400.0, 401.9))
        self.assertFalse(probe.agrees(400.0, 402.1))

    def test_sign_error_is_counted_and_is_never_agreement(self):
        self.assertTrue(probe.sign_error(0.005, -0.005))
        self.assertFalse(probe.agrees(0.005, -0.005))
        self.assertTrue(probe.sign_error(-1.0, 1.0))
        self.assertFalse(probe.agrees(-1.0, 1.0))

    def test_zero_is_never_a_sign_error(self):
        self.assertFalse(probe.sign_error(0.0, -0.005))
        self.assertTrue(probe.agrees(0.0, -0.005))


class WilsonBound(unittest.TestCase):
    def test_known_values(self):
        self.assertAlmostEqual(probe.wilson_lower(50, 100), 0.4038, places=4)
        self.assertAlmostEqual(probe.wilson_lower(3, 3), 0.438503, places=5)
        self.assertAlmostEqual(probe.wilson_lower(10, 10), 0.722467, places=5)

    def test_no_successes_gives_zero_and_no_trials_gives_none(self):
        self.assertEqual(probe.wilson_lower(0, 10), 0.0)
        self.assertIsNone(probe.wilson_lower(0, 0))

    def test_bound_is_below_the_point_estimate(self):
        for successes, trials in ((7, 9), (990, 1000), (1, 2)):
            self.assertLess(probe.wilson_lower(successes, trials), successes / trials)


class Comparison(ProbeCase):
    def test_counts_sign_errors_and_lists_keys_only(self):
        index, _ = self.index_of([
            statement_row("AAA", "2024-03-31", 1.0),
            statement_row("AAA", "2024-06-30", -1.0),
            statement_row("BBB", "2024-03-31", 0.005),
            statement_row("BBB", "2024-06-30", 0.0),
            statement_row("CCC", "2024-03-31", 2.0),
        ])
        entries = [
            ("AAA", "2024-03-31", ordinal("2024-03-31"), 1.0),       # agrees
            ("AAA", "2024-06-30", ordinal("2024-06-30"), 1.0),       # sign error
            ("BBB", "2024-03-31", ordinal("2024-03-31"), -0.005),    # sign error, inside the 0.011 floor
            ("BBB", "2024-06-30", ordinal("2024-06-30"), -0.005),    # zero dataset value: agrees, no sign error
            ("CCC", "2024-03-31", ordinal("2024-03-31"), 2.5),       # magnitude disagreement
            ("DDD", "2024-03-31", ordinal("2024-03-31"), 3.0),       # no row
        ]
        entries = [(symbol, end, ordinal, sec_value, 2024) for symbol, end, ordinal, sec_value in entries]
        result = probe.compare(entries, index)
        self.assertEqual(result["population"], 6)
        self.assertEqual(result["compared"], 5)
        self.assertEqual(result["agreements"], 2)
        self.assertEqual(result["disagreements"], 3)
        self.assertEqual(result["signErrors"], 2)
        self.assertEqual(result["notCompared"]["noRow"], 1)
        self.assertEqual(result["disagreementKeys"],
                         [{"symbol": "AAA", "end": "2024-06-30"}, {"symbol": "BBB", "end": "2024-03-31"},
                          {"symbol": "CCC", "end": "2024-03-31"}])
        self.assertEqual(result["signErrorKeys"],
                         [{"symbol": "AAA", "end": "2024-06-30"}, {"symbol": "BBB", "end": "2024-03-31"}])
        self.assertAlmostEqual(result["wilsonLower95"], probe.wilson_lower(2, 5))


class Coverage(ProbeCase):
    def test_coverage_by_class_and_year_with_the_seven_day_window(self):
        documents = {
            "AAA": us_document([
                quarter("2024-03-30", 2024, 1, 1.00),
                quarter("2024-06-29", 2024, 2, 1.10, derived=1),
                quarter("2024-09-28", 2024, 3, 1.20),
                quarter("2024-12-28", 2024, 4, None),
                quarter("2025-12-27", 2025, 4, 2.00, grade="A"),
            ]),
            "BBB": us_document([
                quarter("2024-06-30", 2024, 2, 0.5),
                quarter("2024-12-31", 2024, 4, None),
            ]),
        }
        classification = [
            classified("AAA", "2024-12-28", "derivation-rejected", 2024),
            classified("BBB", "2024-12-31", "no-q4-net-income", 2024),
        ]
        statements = [
            statement_row("AAA", "2024-03-30", 1.00),
            statement_row("AAA", "2024-06-29", 1.10),
            statement_row("AAA", "2024-09-28", 1.25),
            statement_row("AAA", "2024-12-27", 0.90),
            statement_row("AAA", "2025-12-27", 2.01),
            statement_row("AAA", "TTM", 4.5),
        ]
        report, _, _ = self.run_probe(documents, classification, statements)
        coverage = report["coverage"]
        self.assertEqual(coverage["denominator"], 2)
        self.assertEqual(coverage["total"]["missing"], 2)
        self.assertEqual(coverage["total"]["covered"], 1)
        self.assertEqual(coverage["total"]["within7Days"], 1)
        self.assertEqual(coverage["total"]["noRow"], 1)
        self.assertEqual(coverage["byClass"]["derivation-rejected"]["covered"], 1)
        self.assertEqual(coverage["byClass"]["no-q4-net-income"]["noRow"], 1)
        self.assertEqual(coverage["byClass"]["ledger-blocked"]["missing"], 0)
        self.assertEqual(coverage["byFiscalYear"]["2024"]["missing"], 2)
        self.assertEqual(coverage["byFiscalYear"]["2024"]["covered"], 1)

    def test_set_a_and_set_b_populations_and_agreement(self):
        documents = {
            "AAA": us_document([
                quarter("2024-03-30", 2024, 1, 1.00),
                quarter("2024-06-29", 2024, 2, 1.10, derived=1),
                quarter("2024-09-28", 2024, 3, 1.20),
                quarter("2024-12-28", 2024, 4, None),
                quarter("2025-12-27", 2025, 4, 2.00, grade="A"),
            ]),
            "BBB": us_document([
                quarter("2024-06-30", 2024, 2, 0.5),
                quarter("2024-12-31", 2024, 4, None),
            ]),
        }
        classification = [
            classified("AAA", "2024-12-28", "derivation-rejected", 2024),
            classified("BBB", "2024-12-31", "no-q4-net-income", 2024),
        ]
        statements = [
            statement_row("AAA", "2024-03-30", 1.00),
            statement_row("AAA", "2024-06-29", 1.10),
            statement_row("AAA", "2024-09-28", 1.25),
            statement_row("AAA", "2024-12-27", 0.90),
            statement_row("AAA", "2025-12-27", 2.01),
        ]
        report, _, _ = self.run_probe(documents, classification, statements)
        set_a = report["agreement"]["setA"]
        set_b = report["agreement"]["setB"]
        self.assertEqual(report["repo"]["setAPopulation"], 3)
        self.assertEqual(report["repo"]["setADerivedPopulation"], 1)
        self.assertEqual(set_a["compared"], 2)
        self.assertEqual(set_a["agreements"], 1)
        self.assertEqual(set_a["disagreementKeys"], [{"symbol": "AAA", "end": "2024-09-28"}])
        self.assertEqual(set_a["notCompared"]["noRow"], 1)
        self.assertEqual(report["agreement"]["setADerived"]["compared"], 1)
        self.assertEqual(report["agreement"]["setADerived"]["agreements"], 1)
        self.assertEqual(set_b["population"], 1)
        self.assertEqual(set_b["agreements"], 1)
        self.assertEqual(set_b["subtraction"]["notTestable"], 1)
        self.assertAlmostEqual(set_b["wilsonLower95"], 1 / (1 + probe.Z95 * probe.Z95), places=6)


class Subtraction(ProbeCase):
    """The subtraction test: annual diluted EPS minus (Q1 + Q2 + Q3), on covered missing rows and on covered set b rows."""

    @staticmethod
    def scan_with(annual, quarters):
        return {"annual": {(symbol, fy): values for (symbol, fy), values in annual.items()},
                "quarterly": quarters}

    def base_scan(self):
        return self.scan_with({("AAA", 2024): [4.4]},
                              {("AAA", 2024, 1): [1.0], ("AAA", 2024, 2): [1.1], ("AAA", 2024, 3): [1.2]})

    def test_subtraction_value_is_annual_minus_the_three_quarters(self):
        self.assertAlmostEqual(probe.subtraction_value(self.base_scan(), "AAA", 2024), 1.1, places=9)

    def test_missing_or_duplicated_input_is_not_testable(self):
        scan = self.base_scan()
        scan["quarterly"][("AAA", 2024, 2)] = []
        self.assertIsNone(probe.subtraction_value(scan, "AAA", 2024))
        scan = self.base_scan()
        scan["quarterly"][("AAA", 2024, 1)] = [1.0, 1.0]
        self.assertIsNone(probe.subtraction_value(scan, "AAA", 2024))
        scan = self.base_scan()
        scan["annual"][("AAA", 2024)] = [4.4, 4.4]
        self.assertIsNone(probe.subtraction_value(scan, "AAA", 2024))
        scan = self.base_scan()
        scan["annual"][("AAA", 2024)] = [None]
        self.assertIsNone(probe.subtraction_value(scan, "AAA", 2024))
        self.assertIsNone(probe.subtraction_value(self.base_scan(), "AAA", 2023))

    def test_outcome_is_within_outside_or_not_testable(self):
        scan = self.base_scan()
        self.assertEqual(probe.subtraction_outcome(scan, "AAA", 2024, 1.1 + 0.005), probe.WITHIN)
        self.assertEqual(probe.subtraction_outcome(scan, "AAA", 2024, 1.1 + 0.02), probe.OUTSIDE)
        self.assertEqual(probe.subtraction_outcome(scan, "AAA", 2023, 1.1), probe.NOT_TESTABLE)

    def test_tolerance_scales_with_the_subtraction(self):
        scan = self.scan_with({("BIG", 2024): [404.0]},
                              {("BIG", 2024, 1): [1.0], ("BIG", 2024, 2): [1.0], ("BIG", 2024, 3): [1.0]})
        self.assertEqual(probe.subtraction_outcome(scan, "BIG", 2024, 402.5), probe.WITHIN)
        self.assertEqual(probe.subtraction_outcome(scan, "BIG", 2024, 403.5), probe.OUTSIDE)

    def test_covered_missing_rows_and_set_b_by_class(self):
        documents = {
            "AAA": us_document([
                quarter("2024-03-30", 2024, 1, 1.0),
                quarter("2024-06-29", 2024, 2, 1.1),
                quarter("2024-09-28", 2024, 3, 1.2),
                quarter("2024-12-28", 2024, 4, None),
                quarter("2025-03-29", 2025, 1, 0.5),
                quarter("2025-06-28", 2025, 2, 0.5),
                quarter("2025-09-27", 2025, 3, 0.5),
                quarter("2025-12-27", 2025, 4, 2.5, grade="A"),
            ], annual=[(2024, 4.4), (2025, 4.0)]),
            "BBB": us_document([
                quarter("2024-03-31", 2024, 1, 1.0),
                quarter("2024-06-30", 2024, 2, None),
                quarter("2024-09-30", 2024, 3, 1.0),
                quarter("2024-12-31", 2024, 4, None),
            ], annual=[(2024, 4.0)]),
            "CCC": us_document([
                quarter("2024-03-31", 2024, 1, 1.0),
                quarter("2024-06-30", 2024, 2, 1.0),
                quarter("2024-09-30", 2024, 3, 1.0),
                quarter("2024-12-31", 2024, 4, None),
            ], annual=[(2024, 4.0)]),
            "DDD": us_document([
                quarter("2024-03-31", 2024, 1, 1.0),
                quarter("2024-06-30", 2024, 2, 1.0),
                quarter("2024-09-30", 2024, 3, 1.0),
                quarter("2024-12-31", 2024, 4, None),
            ], annual=[(2024, 4.0)]),
        }
        classification = [
            classified("AAA", "2024-12-28", "derivation-rejected", 2024),
            classified("BBB", "2024-12-31", "no-q4-net-income", 2024),
            classified("CCC", "2024-12-31", "derivation-rejected", 2024),
            classified("DDD", "2024-12-31", "derivation-rejected", 2024),
        ]
        statements = [
            statement_row("AAA", "2024-12-27", 1.1),   # covered, 1 day off, equals 4.4 - 3.3
            statement_row("AAA", "2025-12-27", 2.5),   # set b, equals 4.0 - 1.5
            statement_row("BBB", "2024-12-31", 0.9),   # covered, Q2 is null: not testable
            statement_row("CCC", "2024-12-31", 2.5),   # covered, 4.0 - 3.0 = 1.0, so outside
        ]
        report, _, _ = self.run_probe(documents, classification, statements)
        covered = report["subtraction"]["coveredMissing"]
        self.assertEqual(covered["covered"], 3)
        self.assertEqual(covered["testable"], 2)
        self.assertEqual(covered["withinSubtraction"], 1)
        self.assertEqual(covered["outsideSubtraction"], 1)
        self.assertEqual(covered["notTestable"], 1)
        self.assertEqual(covered["coveredNotWithinSubtraction"], 2)
        self.assertEqual(covered["byClass"]["derivation-rejected"]["covered"], 2)
        self.assertEqual(covered["byClass"]["derivation-rejected"]["withinSubtraction"], 1)
        self.assertEqual(covered["byClass"]["derivation-rejected"]["outsideSubtraction"], 1)
        self.assertEqual(covered["byClass"]["no-q4-net-income"]["notTestable"], 1)
        self.assertEqual(covered["byClass"]["no-q4-net-income"]["testable"], 0)
        self.assertEqual(report["coverage"]["total"]["covered"], covered["covered"])
        set_b = report["agreement"]["setB"]["subtraction"]
        self.assertEqual((set_b["testable"], set_b["withinSubtraction"], set_b["notTestable"]), (1, 1, 0))


class LicenseText(unittest.TestCase):
    def test_frontmatter_license_is_quoted_verbatim(self):
        result = probe.card_license("---\nlicense: apache-2.0\n---\n# Card\nText.\n")
        self.assertEqual(result["status"], "STATED")
        self.assertEqual(result["text"], "license: apache-2.0")

    def test_license_section_body_is_quoted(self):
        result = probe.card_license("# Card\n\n## License\n\nCC-BY-4.0 applies.\n\n## Usage\nNo.\n")
        self.assertEqual(result["status"], "STATED")
        self.assertEqual(result["sections"][0]["body"], "CC-BY-4.0 applies.")

    def test_card_without_a_license_says_so(self):
        result = probe.card_license("# Card\n\nNo terms are given here.\n")
        self.assertEqual(result["status"], "NO LICENSE STATED")
        self.assertEqual(result["text"], "NO LICENSE STATED")


class NoValueWritten(ProbeCase):
    def test_report_carries_no_eps_value(self):
        sec_value = 7.31337
        dataset_value = -7.31999
        documents = {"AAA": us_document([quarter("2024-03-30", 2024, 1, sec_value),
                                         quarter("2024-12-28", 2024, 4, None),
                                         quarter("2025-12-27", 2025, 4, 6.54321, grade="A")])}
        classification = [classified("AAA", "2024-12-28", "derivation-rejected", 2024)]
        statements = [statement_row("AAA", "2024-03-30", dataset_value),
                      statement_row("AAA", "2024-12-27", 8.88888),
                      statement_row("AAA", "2025-12-27", 6.54)]
        report, body, _ = self.run_probe(documents, classification, statements)
        text = body.decode("utf-8")
        for needle in ("7.31337", "7.31999", "8.88888", "6.54321", "6.54"):
            self.assertNotIn(needle, text)
        self.assertFalse(report["writesEpsValues"])
        self.assertFalse(report["fillsValues"])
        injected = (sec_value, dataset_value, 8.88888, 6.54321, 6.54)
        for number in self.walk_numbers(report):
            for value in injected:
                self.assertNotAlmostEqual(number, value, places=9)

    @staticmethod
    def walk_numbers(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from NoValueWritten.walk_numbers(value)
        elif isinstance(node, list):
            for value in node:
                yield from NoValueWritten.walk_numbers(value)
        elif isinstance(node, float):
            yield node

    def test_repeat_runs_are_byte_identical(self):
        documents = {"AAA": us_document([quarter("2024-03-30", 2024, 1, 1.0),
                                         quarter("2024-12-28", 2024, 4, None)])}
        classification = [classified("AAA", "2024-12-28", "derivation-rejected", 2024)]
        statements = [statement_row("AAA", "2024-03-30", 1.0), statement_row("AAA", "2024-12-28", 1.0)]
        _, first, _ = self.run_probe(documents, classification, statements)
        _, second, _ = self.run_probe(documents, classification, statements)
        self.assertEqual(first, second)

    def test_output_inside_the_repo_is_refused(self):
        documents = {"AAA": us_document([quarter("2024-03-30", 2024, 1, 1.0)])}
        _, _, args = self.run_probe(documents, [], [statement_row("AAA", "2024-03-30", 1.0)])
        repo = args[args.index("--repo-root") + 1]
        inside = os.path.join(repo, "docs", "report", "probe.json")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            probe.main(args[:-1] + [inside])

    def test_mismatched_classification_stops_the_probe(self):
        documents = {"AAA": us_document([quarter("2024-03-30", 2024, 1, 1.0),
                                         quarter("2024-12-28", 2024, 4, None)])}
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self.run_probe(documents, [], [statement_row("AAA", "2024-12-27", 1.0)])

    def test_card_that_does_not_match_the_downloaded_size_stops_the_probe(self):
        documents = {"AAA": us_document([quarter("2024-03-30", 2024, 1, 1.0)])}
        sizes = [{"file": "README.md", "bytes": 3, "sha256": "f" * 64},
                 {"file": "data/US/stock_statement.parquet", "bytes": 9, "sha256": "e" * 64}]
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self.run_probe(documents, [], [statement_row("AAA", "2024-03-30", 1.0)], sizes_override=sizes)


class OfflineAndWorkflow(ProbeCase):
    def test_script_imports_no_network_library(self):
        with open(SCRIPT_PATH, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported & FORBIDDEN_IMPORTS, imported & FORBIDDEN_IMPORTS)

    def test_run_opens_no_socket(self):
        documents = {"AAA": us_document([quarter("2024-03-30", 2024, 1, 1.0)])}
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network use")), \
                mock.patch.object(socket, "create_connection", side_effect=AssertionError("network use")):
            self.run_probe(documents, [], [statement_row("AAA", "2024-03-30", 1.0)])

    def test_workflow_is_manual_read_only_and_names_no_sec_host(self):
        with open(WORKFLOW_PATH, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("sec.gov", text.lower())
        self.assertNotIn("git push", text)
        self.assertNotIn("git commit", text)
        self.assertIn("if-no-files-found: error", text)
        self.assertIn("name: defeatbeta-probe", text)
        self.assertIn("https://huggingface.co/datasets/defeatbeta/yahoo-finance-data/resolve/main", text)


if __name__ == "__main__":
    unittest.main()

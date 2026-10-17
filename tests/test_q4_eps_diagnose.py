"""Fixed-layout classification fixtures and read-only diagnose-mode behavior."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "q4_8k")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import backfill_q4_eps_8k as bq  # noqa: E402


class FixtureSec:
    requests = 0

    def __init__(self, fixture):
        self.fixture = fixture

    def get_json(self, url):
        self.requests += 1
        if url.endswith("index.json"):
            return {"directory": {"item": [{"name": "exhibit99.htm", "type": "EX-99.1"}]}}
        return {"filings": {"recent": {"form": [], "filingDate": [],
                                        "accessionNumber": [], "items": []}}}

    def get_text(self, url):
        self.requests += 1
        if url.endswith("-index.html") or url.endswith("-index.htm"):
            return ""
        return self.fixture


class ClassificationFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "classification_cases.json"), encoding="utf-8") as source:
            cls.manifest = json.load(source)

    def diagnose(self, fixture_name):
        with open(os.path.join(FIXTURES, fixture_name), encoding="utf-8") as source:
            html = source.read()
        filing = {"accessionNumber": "0000000001-24-000001", "filingDate": "2025-02-15"}
        detail, _ = bq.diagnose_filing(FixtureSec(html), 1, filing,
                                       self.manifest["end"], self.manifest["target_net_income"])
        return {"candidateFilings": [detail]}, detail

    def test_fixed_html_layouts_have_stable_class_hints(self):
        for case in self.manifest["cases"]:
            with self.subTest(class_hint=case["class"]):
                if case["class"] == "L7":
                    diagnosis = {"candidateFilings": []}
                    detail = None
                else:
                    diagnosis, detail = self.diagnose(case["file"])
                self.assertEqual(bq.layout_class_hint(diagnosis), case["class"])
                if detail and case["class"] in ("L1", "L2", "L3"):
                    tables = detail["documents"][0]["tables"]
                    self.assertTrue(tables)
                    self.assertIn("columnCounts", tables[-1])
                    self.assertIn("niLabels", tables[-1])
                if detail and case.get("negative"):
                    document = detail["documents"][0]
                    self.assertFalse(document["netIncomeTargetMatches"])
                    self.assertGreaterEqual(document["tableCount"], 1)

    def test_earlier_header_evidence_and_closest_net_income_are_explicit(self):
        diagnosis, detail = self.diagnose("L1-earlier-table.html")
        self.assertEqual(bq.layout_class_hint(diagnosis), "L1")
        target_table = next(table for table in detail["documents"][0]["tables"]
                            if table["netIncomeTargetMatches"])
        self.assertEqual(target_table["targetMatches"][0]["value"], 1000.0)
        self.assertTrue(target_table["nearestPrecedingNonempty"][0]["periodNamed"])
        self.assertEqual(target_table["niRows"][0]["closestToTarget"]["relativeDifference"], 0.0)

    def test_selected_source_table_is_exported_as_trimmed_html(self):
        fixture_name = "L2-period-wording.html"
        with open(os.path.join(FIXTURES, fixture_name), encoding="utf-8") as source:
            html = source.read()
        filing = {"accessionNumber": "0000000001-24-000001", "filingDate": "2025-02-15"}
        spec = {"accession": filing["accessionNumber"], "document": "exhibit99.htm",
                "tableIndex": 0, "file": "SAMPLE-2024-12-31-L2.html", "class": "L2",
                "key": "SAMPLE", "end": "2024-12-31", "url": "https://www.sec.gov/example"}
        detail, _ = bq.diagnose_filing(FixtureSec(html), 1, filing,
                                       self.manifest["end"], self.manifest["target_net_income"], [spec])
        document = next(item for item in detail["documents"] if item.get("name") == "exhibit99.htm")
        captured = document["fixtureHtml"][0]
        self.assertEqual(captured["tableIndex"], 0)
        self.assertIn("<!-- Source URL: https://www.sec.gov/example -->", captured["html"])
        self.assertTrue(captured["html"].split("\n", 1)[1].startswith("<table"))
        self.assertNotIn("<html", captured["html"].lower())


class S1ClassificationReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "docs", "report", "q4-8k", "s1-no-table-layouts.json"),
                  encoding="utf-8") as source:
            cls.report = json.load(source)
        with open(os.path.join(FIXTURES, "s1_fixture_selection.json"), encoding="utf-8") as source:
            cls.selection = json.load(source)

    def test_all_run18_rows_are_classified_and_summarized(self):
        rows = self.report["rows"]
        self.assertEqual(self.report["rowCount"], 235)
        self.assertEqual(len(rows), 235)
        self.assertEqual(len({(row["key"], row["end"]) for row in rows}), 235)
        self.assertTrue(all(set(row) == {"key", "end", "class", "accession", "url", "note"} for row in rows))
        counts = {}
        for row in rows:
            counts[row["class"]] = counts.get(row["class"], 0) + 1
            self.assertTrue(row["url"].startswith("https://www.sec.gov/"))
        self.assertEqual(counts, self.report["classCounts"])
        self.assertEqual(counts["L9"], 36)
        self.assertFalse(self.report["otherLimit"]["passed"])

    def test_fixture_selection_covers_s2_classes_and_records_dev_tickers(self):
        cases = self.selection["cases"]
        positives = [case for case in cases if case["role"] == "S2-positive"]
        negatives = [case for case in cases if case["role"] == "negative"]
        self.assertEqual(len(positives), 6)
        self.assertEqual(len(negatives), 2)
        for class_code in ("L1", "L2", "L3"):
            self.assertEqual(sum(case["class"] == class_code for case in positives), 2)
        self.assertEqual(self.selection["devTickers"], self.report["devTickers"])
        self.assertTrue(all(case["url"].startswith("https://www.sec.gov/") for case in cases))


class DiagnoseReadOnlyTests(unittest.TestCase):
    def test_only_no_table_rows_are_returned(self):
        source = os.path.join(FIXTURES, "diagnose_input_report.json")
        with tempfile.TemporaryDirectory() as temp_dir:
            abc = {
                "quarterly": {
                    "end": ["2024-12-31"], "fq": [4],
                    "values": {"netIncome": [1000]}
                }
            }
            with open(os.path.join(temp_dir, "ABC.json"), "w", encoding="utf-8") as output:
                json.dump(abc, output)
            sec = FixtureSec("")
            args = SimpleNamespace(source_report=source, tickers="", limit=0)
            with mock.patch.object(bq, "FIN_DIR", temp_dir), \
                 mock.patch.object(bq.seed, "parse_ticker_map", return_value={"ABC": 1}):
                report = bq.run_diagnose(args, sec)
        self.assertEqual(report["sourceRows"], 2)
        self.assertEqual(report["diagnosed"], 2)
        self.assertEqual([row["key"] for row in report["rows"]], ["ABC", "XYZ"])
        self.assertEqual(report["rows"][0]["classHint"], "L7")
        self.assertEqual(report["rows"][1]["classHint"], "L9")

    def test_diagnose_stdout_is_full_json_and_ignores_report_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = os.path.join(temp_dir, "must-not-be-written.json")
            payload = {"mode": "diagnose", "diagnosed": 1,
                       "rows": [{"key": "ABC", "end": "2024-12-31", "classHint": "L7"}]}
            output = io.StringIO()
            with mock.patch.object(bq, "Sec", return_value=object()), \
                 mock.patch.object(bq, "run", return_value=payload), \
                 mock.patch.object(bq.seed, "resolve_user_agent", return_value="test-agent"), \
                 contextlib.redirect_stdout(output):
                status = bq.main(["--mode", "diagnose", "--report", report_path])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), payload)
            self.assertFalse(os.path.exists(report_path))


HEADER = "Three Months Ended December 31, 2024"
EVIDENCE_END = "2024-12-31"
EVIDENCE_TARGET = 1_234_000


def tr(*cells):
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def tbl(*rows):
    return "<table>" + "".join(rows) + "</table>"


def doc(*tables):
    return "<html><body>" + "".join(tables) + "</body></html>"


def evidence_row(html, end=EVIDENCE_END, target=EVIDENCE_TARGET, cik=1):
    """A diagnosed row from one synthetic document: the real diagnose_filing output, no SEC access."""
    filing = {"accessionNumber": "0000000001-25-000001", "filingDate": "2025-02-15"}
    detail, _ = bq.diagnose_filing(FixtureSec(html), cik, filing, end, target)
    return {"key": "SYN", "end": end, "cik": cik, "targetNetIncome": target,
            "hintNote": "Heuristic only.", "candidateFilings": [detail]}


L10_TABLE = tbl(tr(HEADER, "x"), tr("Net income", "1,234"), tr("Diluted earnings per share", "(0.25)"))
L8_TABLE = tbl(tr(HEADER, "x"), tr("Net income attributable to Acme", "1,234"),
               tr("Net income per common share:"), tr("Basic", "0.26"), tr("Diluted", "0.25"))
L3_TABLE = tbl(tr(HEADER, "x"), tr("Net income attributable to Acme, including noncontrolling interests", "1,234"),
               tr("Diluted earnings per share", "0.25"))
EARLIER_HEADER = tbl(tr(HEADER, "x"))
L1_TABLE = tbl(tr("Net income attributable to Acme", "1,234"), tr("Revenue", "5,000"))
L2_TABLE = tbl(tr("Fourth Quarter 2024", "x"), tr("Net income", "1,234"))
L5_TABLE = tbl(tr("Twelve Months Ended December 31, 2023", "x"), tr("Net income", "999"))
L4_TABLE = tbl(tr(HEADER, "x"), tr("Net income", "999"))
L9A_TABLE = tbl(tr("Net income attributable to Acme", "1,234"), tr("Diluted earnings per share", "0.25"))
L9B_TABLE = tbl(tr(HEADER, "x"), tr("Net income attributable to Acme", "1,234"))
L9C_TABLE = tbl(tr(HEADER, "x", "y"), tr("Net income", "1,234", "1,234"), tr("Net income per common share:"),
                tr("Basic", "0.26", "0.26"), tr("Diluted", "0.25", "0.25"))
L9D_TABLE = tbl(tr(HEADER, "x"), tr("Revenue", "1,234"), tr("Net income", "999"))
L9E_TABLE = tbl(tr(HEADER, "x"), tr("Net income attributable to Acme", "1,234"),
                tr("Diluted earnings per share", "0.25"))


class StrictMatchTests(unittest.TestCase):
    def test_eps_of_0_37_billion_matches_no_214_million_target(self):
        self.assertEqual(bq.strict_scales(0.37, 2, 214_000_000), [])

    def test_a_whole_number_matches_at_its_own_unit_scale(self):
        self.assertEqual(bq.strict_scales(214, 0, 214_300_000), [1_000_000])

    def test_digits_after_the_point_are_counted_from_the_cell(self):
        label, pairs = bq.cells_with_decimals(["Net income", "1,234.5"])
        self.assertEqual((label, pairs), ("Net income", [(1234.5, 1)]))
        self.assertEqual(bq.cells_with_decimals(["Net income", "(0.12)", "—"])[1], [(-0.12, 2), (None, 0)])

    def test_cells_with_decimals_agrees_with_row_of_on_every_fixture_row(self):
        for name in sorted(os.listdir(FIXTURES)):
            if not name.endswith(".html"):
                continue
            with open(os.path.join(FIXTURES, name), encoding="utf-8") as source:
                tables = bq.tables_of(source.read())
            for table in tables:
                for cells in table:
                    label, values = bq.row_of(cells)
                    other_label, pairs = bq.cells_with_decimals(cells)
                    self.assertEqual(other_label, label, name)
                    self.assertEqual([value for value, _ in pairs], values, name)


class EpsTableLabelTests(unittest.TestCase):
    def labels(self, *rows):
        return bq.diluted_per_share_labels(bq.tables_of(tbl(*rows))[0])

    def test_wrld_style_label_with_diluted_at_the_end(self):
        self.assertEqual(self.labels(tr("Net income per common share, diluted", "1.00")),
                         ["net income per common share, diluted"])

    def test_urbn_style_heading_gives_the_diluted_row_under_it(self):
        self.assertEqual(self.labels(tr("Net income per common share:"), tr("Basic", "0.26"),
                                     tr("Diluted", "0.25")), ["diluted"])

    def test_txt_style_diluted_heading_is_an_eps_label(self):
        self.assertEqual(self.labels(tr("Diluted earnings per share:"), tr("Net income", "1,234")),
                         ["diluted earnings per share:"])

    def test_weighted_share_counts_and_adjusted_eps_are_not_eps_labels(self):
        self.assertEqual(self.labels(tr("Weighted average shares used in computing diluted net income per share",
                                        "4,936")), [])
        self.assertEqual(self.labels(tr("Adjusted diluted EPS", "0.30")), [])

    def test_a_diluted_row_four_rows_after_the_heading_is_read_and_a_fifth_is_not(self):
        revenue = [tr("Revenue", "5")]
        self.assertEqual(self.labels(tr("Net income per common share:"), *revenue * 3, tr("Diluted", "0.25")),
                         ["diluted"])
        self.assertEqual(self.labels(tr("Net income per common share:"), *revenue * 4, tr("Diluted", "0.25")), [])


class PeriodWordingTests(unittest.TestCase):
    def test_quarter_wording_with_the_year_is_a_variant_header(self):
        self.assertTrue(bq.variant_period("Fourth Quarter 2019", "2019-12-31"))

    def test_fiscal_year_header_in_early_january_is_a_variant(self):
        self.assertTrue(bq.variant_period("Fourth Quarter of Fiscal Years 2013", "2014-01-03"))

    def test_the_end_month_and_day_with_no_year_is_a_variant_when_the_year_appears(self):
        self.assertTrue(bq.variant_period("Year-end results: December 31 (reported in 2019)", "2019-12-31"))
        self.assertFalse(bq.variant_period("Year-end results: December 31, 2019", "2019-12-31"))

    def test_a_numeric_date_equal_to_the_end_is_a_variant(self):
        self.assertTrue(bq.variant_period("Quarter ended 12/31/19", "2019-12-31"))

    def test_the_exact_period_is_not_a_variant(self):
        self.assertFalse(bq.variant_period("Three Months Ended December 31, 2019", "2019-12-31"))

    def test_another_quarter_end_is_detected(self):
        # the TXT 2016-12-31 next-quarter header
        self.assertTrue(bq.other_quarter_end("Three Months Ended April 1, 2017", "2016-12-31"))
        self.assertTrue(bq.other_quarter_end("Second Quarter 2019", "2019-12-31"))
        self.assertFalse(bq.other_quarter_end("Fourth Quarter 2019", "2019-12-31"))

    def test_december_is_not_another_quarter_end_in_early_january(self):
        self.assertFalse(bq.other_quarter_end("Twelve weeks ended December 28, 2013", "2014-01-03"))
        self.assertTrue(bq.other_quarter_end("Three Months Ended April 1, 2017", "2014-01-03"))

    def test_header_text_stops_at_the_first_numeric_row_and_is_capped(self):
        table = bq.tables_of(tbl(tr("Consolidated", "x"), tr(HEADER, "x"), tr("Net income", "1,234"),
                                 tr("Ignored", "5")))[0]
        self.assertEqual(bq.header_text(table), "Consolidated x " + HEADER + " x")
        long_table = [[f"{'a' * 100}"]] * 8
        self.assertEqual(len(bq.header_text(long_table)), 400)


class ClassifyRowRuleTests(unittest.TestCase):
    def assertClass(self, row, cls, table=None):
        result = bq.classify_row(row)
        self.assertEqual(result["class"], cls, result)
        self.assertLessEqual(len(result["note"]), 300)
        self.assertNotIn("\n", result["note"])
        if table is not None:
            self.assertEqual(result["tableIndex"], table, result)
        return result

    def test_rule_0_input_gap_takes_the_hint_note(self):
        row = {"key": "SYN", "end": EVIDENCE_END, "cik": 1, "targetNetIncome": None,
               "hintNote": "Financials file or Q4 netIncome target is missing.", "candidateFilings": []}
        result = self.assertClass(row, "L9")
        self.assertEqual(result["note"], row["hintNote"])
        self.assertEqual(result["url"], "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=1&type=8-K")

    def test_rule_0_without_a_cik_has_no_url(self):
        row = {"key": "SYN", "end": EVIDENCE_END, "cik": None, "targetNetIncome": 5,
               "hintNote": "Ticker is missing from the SEC company ticker map.", "candidateFilings": []}
        result = self.assertClass(row, "L9")
        self.assertIsNone(result["url"])

    def test_rule_1_no_candidate_filing(self):
        row = {"key": "SYN", "end": EVIDENCE_END, "cik": 1, "targetNetIncome": 5, "hintNote": "none",
               "candidateFilings": []}
        result = self.assertClass(row, "L7")
        self.assertTrue(result["note"].startswith("no candidate filing"))
        self.assertIn("CIK=1&type=8-K", result["url"])

    def test_rule_2_no_document_with_a_table(self):
        row = {"key": "SYN", "end": EVIDENCE_END, "cik": 1, "targetNetIncome": 5, "hintNote": "h",
               "candidateFilings": [{"accession": "A-1", "filingUrl": "https://www.sec.gov/x/",
                                     "documents": [{"name": "ex.htm", "url": "u", "tableCount": 0, "tables": []}]}]}
        result = self.assertClass(row, "L6")
        self.assertEqual((result["accession"], result["url"]), ("A-1", "https://www.sec.gov/x/"))

    def test_rule_3_check_reject_gives_the_reason(self):
        result = self.assertClass(evidence_row(doc(L10_TABLE)), "L10", table=0)
        self.assertEqual(result["tableEpsReason"], "EPS sign differs from net income")

    def test_rule_4_eps_row_missing_from_a_one_column_table(self):
        self.assertClass(evidence_row(doc(L8_TABLE)), "L8", table=0)

    def test_rule_5_net_income_under_a_label_that_is_not_accepted(self):
        self.assertClass(evidence_row(doc(L3_TABLE)), "L3", table=0)

    def test_rule_6_period_in_the_earlier_table(self):
        result = self.assertClass(evidence_row(doc(EARLIER_HEADER, L1_TABLE)), "L1", table=1)
        self.assertEqual(result["tableIndices"], [0, 1])

    def test_rule_7_variant_period_header(self):
        self.assertClass(evidence_row(doc(L2_TABLE)), "L2", table=0)

    def test_rule_8_year_wording_and_no_quarter_column(self):
        self.assertClass(evidence_row(doc(L5_TABLE)), "L5", table=0)

    def test_rule_9_no_strict_match_gives_the_closest_value(self):
        result = self.assertClass(evidence_row(doc(L4_TABLE)), "L4", table=0)
        self.assertIn("closest net income-like value", result["note"])

    def test_rule_10a_accepted_match_with_no_period_header(self):
        result = self.assertClass(evidence_row(doc(L9A_TABLE)), "L9", table=0)
        self.assertTrue(result["note"].startswith("(a) "))

    def test_rule_10b_net_income_only_in_a_period_table_without_eps(self):
        self.assertTrue(bq.classify_row(evidence_row(doc(L9B_TABLE)))["note"].startswith("(b) "))

    def test_rule_10c_net_income_in_two_columns_of_an_eps_table_without_a_diluted_row(self):
        self.assertTrue(bq.classify_row(evidence_row(doc(L9C_TABLE)))["note"].startswith("(c) "))

    def test_rule_10d_strict_match_only_under_a_non_net_income_label(self):
        self.assertTrue(bq.classify_row(evidence_row(doc(L9D_TABLE)))["note"].startswith("(d) "))

    def test_rule_10e_the_fallback_names_the_tables(self):
        result = bq.classify_row(evidence_row(doc(L9E_TABLE)))
        self.assertEqual(result["class"], "L9")
        self.assertTrue(result["note"].startswith("(e) "), result["note"])

    def test_precedence_l10_beats_l8(self):
        self.assertClass(evidence_row(doc(L8_TABLE, L10_TABLE)), "L10", table=1)

    def test_precedence_l8_beats_l3(self):
        self.assertClass(evidence_row(doc(L3_TABLE, L8_TABLE)), "L8", table=1)

    def test_precedence_l3_beats_l1(self):
        self.assertClass(evidence_row(doc(EARLIER_HEADER, L1_TABLE, L3_TABLE)), "L3", table=2)

    def test_precedence_l1_beats_l2(self):
        self.assertClass(evidence_row(doc(EARLIER_HEADER, L1_TABLE, L2_TABLE)), "L1", table=1)

    def test_precedence_l5_beats_l4(self):
        row = evidence_row(doc(L5_TABLE))
        documents = row["candidateFilings"][0]["documents"]
        self.assertFalse(any(table["strictMatches"] for document in documents for table in document["tables"]))
        self.assertClass(row, "L5", table=0)


class M1EvidenceFieldTests(unittest.TestCase):
    def test_recorded_tables_carry_the_m1_3_fields(self):
        row = evidence_row(doc(L8_TABLE))
        table = row["candidateFilings"][0]["documents"][0]["tables"][0]
        for field in ("strictMatches", "acceptedStrictColumns", "epsTable", "dilutedPerShareLabels", "headerText",
                      "yearWording", "tableEps", "dilutedAvoidLabels"):
            self.assertIn(field, table)
        self.assertEqual(table["acceptedStrictColumns"], [[0, 1000]])
        self.assertEqual(table["tableEps"], {"result": False, "reason": None, "epsRowFound": False})
        self.assertIsInstance(row["candidateFilings"][0]["documents"][0]["rawStrictTextMatch"], bool)

    def test_classify_row_reproduces_from_json_alone(self):
        row = evidence_row(doc(L10_TABLE, L8_TABLE))
        restored = json.loads(json.dumps(row))
        self.assertEqual(bq.classify_row(restored), bq.classify_row(row))


if __name__ == "__main__":
    unittest.main()

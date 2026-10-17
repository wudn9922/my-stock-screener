"""Fixed-layout classification fixtures and read-only diagnose-mode behavior."""
import contextlib
import hashlib
import io
import json
import os
import re
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


S1_REPORT = os.path.join(ROOT, "docs", "report", "q4-8k", "s1-no-table-layouts.json")
S1_MANIFEST = os.path.join(FIXTURES, "s1_fixtures.json")


def s1_report_is_classify_row_output():
    try:
        with open(S1_REPORT, encoding="utf-8") as source:
            return json.load(source).get("generator") == "classify_row"
    except (OSError, ValueError, AttributeError):
        return False


@unittest.skipUnless(s1_report_is_classify_row_output(),
                     "s1-no-table-layouts.json is missing or was not written by classify_row (run 19 not merged yet)")
class S1ClassificationReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(S1_REPORT, encoding="utf-8") as source:
            cls.report = json.load(source)
        with open(S1_MANIFEST, encoding="utf-8") as source:
            cls.manifest = json.load(source)

    def test_rows_are_unique_and_each_is_in_exactly_one_class(self):
        rows = self.report["rows"]
        self.assertEqual(self.report["rowCount"], 235)
        self.assertEqual(len(rows), 235)
        self.assertEqual(len({(row["key"], row["end"]) for row in rows}), 235)
        counts = {}
        for row in rows:
            counts[row["class"]] = counts.get(row["class"], 0) + 1
        self.assertEqual(sum(counts.values()), 235)
        self.assertEqual({k: v for k, v in self.report["classCounts"].items() if v}, counts)

    def test_other_limit_passes_and_every_class_has_count_examples_and_disposition(self):
        self.assertTrue(self.report["otherLimit"]["passed"])
        self.assertLessEqual(self.report["classes"]["L9"]["count"], 23)
        for name, entry in self.report["classes"].items():
            with self.subTest(cls=name):
                self.assertIn("count", entry)
                self.assertIsInstance(entry["examples"], list)
                self.assertIn("disposition", entry)

    def test_every_fixture_exists_with_its_sha256_and_its_rows_class(self):
        classes = {(row["key"], row["end"]): row["class"] for row in self.report["rows"]}
        for fixture in self.manifest["fixtures"]:
            with open(os.path.join(FIXTURES, fixture["file"]), "rb") as source:
                data = source.read()
            self.assertEqual(hashlib.sha256(data).hexdigest(), fixture["sha256"], fixture["file"])
            self.assertEqual(classes[(fixture["key"], fixture["end"])], fixture["class"], fixture["file"])

    def test_s2_classes_have_two_positives_and_there_are_at_least_five_negatives(self):
        fixtures = self.manifest["fixtures"]
        for cls, entry in self.report["classes"].items():
            if entry["disposition"] == "S2 handles":
                positives = [f for f in fixtures if f["role"] == "positive" and f["class"] == cls]
                self.assertGreaterEqual(len(positives), 2, cls)
        self.assertGreaterEqual(sum(f["role"] == "negative" for f in fixtures), 5)

    def test_every_fixture_round_trips_and_dev_tickers_match(self):
        fixtures = self.manifest["fixtures"]
        self.assertTrue(all(f["roundTrip"] for f in fixtures))
        self.assertEqual(self.report["devTickers"], self.manifest["devTickers"])
        self.assertEqual(self.manifest["devTickers"], sorted({f["key"] for f in fixtures}))

    def test_every_positive_has_a_numeric_eps_diluted_read(self):
        for fixture in self.manifest["fixtures"]:
            if fixture["role"] == "positive":
                self.assertIsInstance(fixture["epsDilutedRead"], (int, float), fixture["file"])


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


FIVE_TABLES = doc(tbl(), tbl(tr("Revenue", "5,000")), tbl(), tbl(tr(HEADER, "x")),
                  tbl(tr("Net income attributable to Acme", "1,234")))


class MultiTableFixtureTests(unittest.TestCase):
    def test_five_tables_with_an_empty_third_export_tables_one_three_four_with_the_marker_before_four(self):
        source = bq.tables_of(FIVE_TABLES)
        fixture, error = bq.export_fixture(FIVE_TABLES, "https://www.sec.gov/example", [1, 3, 4], 4)
        self.assertIsNone(error)
        lines = fixture.split("\n")
        self.assertEqual(lines[0], "<!-- Source URL: https://www.sec.gov/example -->")
        marker = lines.index(bq.TARGET_MARKER)
        self.assertTrue(lines[marker + 1].startswith("<table"))
        self.assertIn("Net income attributable to Acme", lines[marker + 1])
        self.assertEqual(bq.tables_of(fixture), [source[1], source[3], source[4]])
        self.assertEqual(bq.tables_of(fixture)[2][0][0], "Net income attributable to Acme")

    def test_the_l1_evidence_names_the_same_tables_the_export_uses(self):
        row = evidence_row(FIVE_TABLES)
        result = bq.classify_row(row)
        self.assertEqual((result["class"], result["tableIndex"], result["tableIndices"]), ("L1", 4, [1, 3, 4]))
        fixture, error = bq.export_fixture(FIVE_TABLES, "u", result["tableIndices"], result["tableIndex"])
        self.assertIsNone(error)
        self.assertEqual(bq.tables_of(fixture), [bq.tables_of(FIVE_TABLES)[i] for i in (1, 3, 4)])


def classified_row(key, end, html):
    row = evidence_row(html, end=end)
    row.update(key=key, end=end)
    return bq._classified_row(row)


class CompactOutputTests(unittest.TestCase):
    def rows(self):
        return [classified_row("BBB", "2024-12-31", doc(L8_TABLE)),
                classified_row("AAA", "2024-12-31", doc(L10_TABLE)),
                classified_row("AAA", "2023-12-31", doc(L9A_TABLE)),
                classified_row("CCC", "2024-12-31", doc(L5_TABLE))]

    def report(self, rows=None, **changes):
        rows = self.rows() if rows is None else rows
        report = {"mode": "diagnose", "sourceReport": "docs/report/q4-8k/input/run-17/q4-8k-report.json",
                  "sourceRows": len(rows), "diagnosed": len(rows), "stopped": None, "requests": 7,
                  "rows": rows, "fixtures": [], "fixtureErrors": []}
        report.update(changes)
        return report

    def test_s1_classes_has_the_schema_and_its_counts_add_up(self):
        document = bq.build_s1_classes(self.report(), env={"GITHUB_RUN_ID": "123", "GITHUB_SHA": "abc"})
        self.assertEqual(document["schemaVersion"], 2)
        self.assertEqual(document["generator"], "classify_row")
        self.assertEqual(document["status"], "complete")
        self.assertEqual(document["diagnoseRun"], {"runId": 123, "mainCommit": "abc", "requests": 7, "stopped": None})
        self.assertEqual(document["rowCount"], 4)
        self.assertEqual(sum(document["classCounts"].values()), document["rowCount"])
        self.assertEqual(set(document["classes"]), set(bq.S1_CLASS_NAMES))
        for cls, entry in document["classes"].items():
            self.assertEqual(set(entry), {"name", "count", "examples", "disposition"})
        self.assertEqual(document["classes"]["L9"]["disposition"], "needs Opus")
        self.assertEqual(document["classes"]["L8"]["disposition"], "out of scope")
        self.assertEqual(document["otherLimit"], {"limit": 0, "count": 1, "passed": False})

    def test_every_row_record_has_eight_keys_and_is_under_one_kilobyte(self):
        document = bq.build_s1_classes(self.report())
        for record in document["rows"]:
            self.assertEqual(set(record), {"key", "end", "class", "accession", "url", "tableIndex",
                                           "tableEpsReason", "note"})
            self.assertLess(len(json.dumps(record)), 1024)
        self.assertEqual([(r["key"], r["end"]) for r in document["rows"]],
                         sorted((r["key"], r["end"]) for r in document["rows"]))

    def test_examples_are_the_first_urls_in_key_order_and_status_follows_the_run(self):
        document = bq.build_s1_classes(self.report())
        aaa = [r for r in document["rows"] if r["key"] == "AAA"]
        self.assertEqual(document["classes"]["L10"]["examples"], [aaa[1]["url"]])
        stopped = bq.build_s1_classes(self.report(stopped="Forbidden: x"))
        self.assertEqual(stopped["status"], "incomplete")
        short = bq.build_s1_classes(self.report(diagnosed=3))
        self.assertEqual(short["status"], "incomplete")

    def test_out_dir_inside_the_repo_is_refused_before_any_sec_request(self):
        target = os.path.join(ROOT, "tests", "should-not-be-created")
        with mock.patch.object(bq, "Sec") as sec, mock.patch.object(bq, "run") as run, \
                contextlib.redirect_stderr(io.StringIO()):
            status = bq.main(["--mode", "diagnose", "--out-dir", target])
        self.assertEqual(status, 2)
        sec.assert_not_called()
        run.assert_not_called()
        self.assertFalse(os.path.exists(target))

    def test_out_dir_writes_three_outputs_outside_the_repo_and_prints_only_the_summary(self):
        payload = self.report()
        with tempfile.TemporaryDirectory() as temp_dir:
            out = os.path.join(temp_dir, "out")
            output = io.StringIO()
            with mock.patch.object(bq, "Sec", return_value=object()), \
                    mock.patch.object(bq, "run", return_value=payload), \
                    mock.patch.object(bq.seed, "resolve_user_agent", return_value="test-agent"), \
                    contextlib.redirect_stdout(output):
                status = bq.main(["--mode", "diagnose", "--out-dir", out])
            self.assertEqual(status, 0)
            summary = json.loads(output.getvalue())
            self.assertNotIn("rows", summary)
            self.assertNotIn("fixtures", summary)
            self.assertEqual(summary["diagnosed"], 4)
            with open(os.path.join(out, "s1-classes.json"), encoding="utf-8") as source:
                self.assertEqual(json.load(source)["rowCount"], 4)
            with open(os.path.join(out, "fixtures", "s1_fixtures.json"), encoding="utf-8") as source:
                self.assertEqual(json.load(source), {"schemaVersion": 2, "devTickers": [], "fixtures": []})
            written = sorted(os.path.relpath(os.path.join(dirpath, name), temp_dir)
                             for dirpath, _, names in os.walk(temp_dir) for name in names)
            self.assertEqual(written, [os.path.join("out", "fixtures", "s1_fixtures.json"),
                                       os.path.join("out", "s1-classes.json")])

    def test_verbose_report_holds_the_evidence_and_no_fixture_html(self):
        payload = self.report(fixtures=[{"file": "x.html", "html": "<table></table>"}])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "verbose.json")
            with mock.patch.object(bq, "Sec", return_value=object()), \
                    mock.patch.object(bq, "run", return_value=payload), \
                    mock.patch.object(bq.seed, "resolve_user_agent", return_value="test-agent"), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bq.main(["--mode", "diagnose", "--verbose-report", path]), 0)
            with open(path, encoding="utf-8") as source:
                verbose = json.load(source)
        self.assertEqual(len(verbose["rows"]), 4)
        self.assertNotIn("fixtures", verbose)
        self.assertIn("classification", verbose["rows"][0])


def pool_entry(key, end, cls, role="positive", kind="positive", auto_kind=None, negative_for=None,
               accession="A", document="ex.htm", error=None, html="<table></table>\n"):
    return {"kind": kind, "autoKind": auto_kind, "role": role, "key": key, "end": end, "class": cls,
            "negativeFor": negative_for, "mustReject": None, "accession": accession, "document": document,
            "url": f"https://www.sec.gov/x/{key}.htm", "tableIndices": [0], "targetTableIndex": 0,
            "storedEps": 0.25, "html": None if error else html, "fixtureError": error}


def selection(positives=None, explicit=(), auto=()):
    return {"autoPositives": positives or {}, "explicitNegatives": list(explicit), "autoNegatives": list(auto)}


class SelectionRuleTests(unittest.TestCase):
    def test_positives_are_one_per_ticker_in_key_order(self):
        pool = [pool_entry("BBB", "2023-12-31", "L1"), pool_entry("BBB", "2024-12-31", "L1"),
                pool_entry("AAA", "2024-12-31", "L1"), pool_entry("CCC", "2024-12-31", "L1")]
        fixtures, errors = bq.select_fixtures(pool, selection({"L1": 2}))
        self.assertEqual([f["file"] for f in fixtures], ["AAA-2024-12-31-L1.html", "BBB-2023-12-31-L1.html"])
        self.assertEqual(errors, [])

    def test_a_ticker_already_selected_by_an_explicit_negative_is_preferred(self):
        explicit = {"key": "CCC", "end": "2025-06-30", "accession": "A", "document": "ex.htm", "tableIndices": [0],
                    "negativeFor": "L4", "mustReject": "no strict NI match"}
        pool = [pool_entry("CCC", "2025-06-30", "L4", role="negative", kind="explicit", negative_for="L4"),
                pool_entry("AAA", "2024-12-31", "L1"), pool_entry("CCC", "2024-12-31", "L1")]
        fixtures, _ = bq.select_fixtures(pool, selection({"L1": 1}, explicit=[explicit]))
        self.assertEqual([f["file"] for f in fixtures],
                         ["CCC-2025-06-30-L4-negative.html", "CCC-2024-12-31-L1.html"])

    def test_a_candidate_with_a_fixture_error_is_skipped(self):
        pool = [pool_entry("AAA", "2024-12-31", "L2", error="too large"), pool_entry("BBB", "2024-12-31", "L2")]
        fixtures, errors = bq.select_fixtures(pool, selection({"L2": 1}))
        self.assertEqual([f["key"] for f in fixtures], ["BBB"])
        self.assertEqual([(e["key"], e["error"]) for e in errors], [("AAA", "too large")])

    def test_auto_negatives_take_the_first_row_then_the_next_unused_fallback(self):
        pool = [pool_entry("TXT", "2016-12-31", "L2", role="negative", kind="auto", auto_kind="next-quarter-table",
                           negative_for="next-quarter-table"),
                pool_entry("AAA", "2024-12-31", "L9", role="negative", kind="auto", auto_kind="next-quarter-table",
                           negative_for="next-quarter-table"),
                pool_entry("BBB", "2024-12-31", "L9", role="negative", kind="auto",
                           auto_kind="period-named-non-eps-ni", negative_for="period-named-non-eps-ni")]
        fixtures, errors = bq.select_fixtures(pool, selection(auto=["L1-year-header", "L8-non-gaap-diluted-row"]))
        self.assertEqual([f["file"] for f in fixtures], ["AAA-2024-12-31-next-quarter-table-negative.html",
                                                         "BBB-2024-12-31-period-named-non-eps-ni-negative.html"])
        self.assertIn("used because L1-year-header was not found", fixtures[0]["mustReject"])
        self.assertEqual(errors, [])

    def test_an_auto_kind_with_no_candidate_or_fallback_is_an_error(self):
        fixtures, errors = bq.select_fixtures([], selection(auto=["L1-year-header"]))
        self.assertEqual(fixtures, [])
        self.assertEqual(errors[0]["error"], "auto-negative not found and no fallback kind left")

    def test_an_explicit_negative_that_was_not_read_is_an_error(self):
        explicit = {"key": "WLY", "end": "2018-04-30", "accession": "B", "document": "x.htm", "tableIndices": [36],
                    "negativeFor": "L4", "mustReject": "no strict NI match"}
        fixtures, errors = bq.select_fixtures([], selection(explicit=[explicit]))
        self.assertEqual(fixtures, [])
        self.assertEqual(errors[0]["error"], "explicit negative was not read in this run")

    def test_the_total_cap_refuses_the_fixture_that_would_exceed_it(self):
        big = "x" * 700_000
        pool = [pool_entry("AAA", "2024-12-31", "L1", html=big), pool_entry("BBB", "2024-12-31", "L1", html=big)]
        fixtures, errors = bq.select_fixtures(pool, selection({"L1": 2}))
        self.assertEqual([f["key"] for f in fixtures], ["AAA"])
        self.assertEqual(errors[0]["error"], "exported fixture HTML would exceed 1.2 MB in total")


class RoutingSec:
    """Serves one synthetic 8-K per CIK from memory: no network."""

    def __init__(self, documents):
        self.documents = documents
        self.requests = 0

    def get_json(self, url):
        self.requests += 1
        if url.endswith("company_tickers.json"):
            return {}
        if "/submissions/" in url:
            cik = int(re.search(r"CIK(\d+)\.json", url).group(1))
            return {"filings": {"recent": {"form": ["8-K"], "filingDate": ["2025-02-05"],
                                           "accessionNumber": [f"{cik:010d}-25-000001"], "items": ["2.02"]}}}
        if url.endswith("index.json"):
            return {"directory": {"item": [{"name": "ex99.htm", "type": "EX-99.1"}]}}
        raise AssertionError(url)

    def get_text(self, url):
        self.requests += 1
        if url.endswith(("-index.html", "-index.htm")):
            return ""
        return self.documents[int(url.split("/data/")[1].split("/")[0])]


def tree_snapshot():
    found = {}
    for part in ("docs", "tests", os.path.join("atlas", "scripts")):
        for dirpath, dirnames, names in os.walk(os.path.join(ROOT, part)):
            dirnames[:] = [name for name in dirnames if name != "__pycache__"]
            for name in names:
                path = os.path.join(dirpath, name)
                found[path] = (os.stat(path).st_mtime_ns, os.stat(path).st_size)
    return found


class DiagnoseEndToEndTests(unittest.TestCase):
    def test_a_routed_run_writes_the_three_outputs_and_changes_nothing_in_the_repo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fin_dir = os.path.join(temp_dir, "financials")
            os.makedirs(fin_dir)
            for key in ("AAA", "BBB"):
                with open(os.path.join(fin_dir, key + ".json"), "w", encoding="utf-8") as target:
                    json.dump({"quarterly": {"end": ["2024-12-31"], "fq": [4],
                                             "values": {"netIncome": [1234000]}}}, target)
            source = os.path.join(temp_dir, "source.json")
            with open(source, "w", encoding="utf-8") as target:
                json.dump({"rows": [{"key": key, "end": "2024-12-31", "stored": 0.25,
                                     "reason": "no table shows this quarter's net income"}
                                    for key in ("AAA", "BBB")]}, target)
            manifest = os.path.join(temp_dir, "selection.json")
            with open(manifest, "w", encoding="utf-8") as target:
                json.dump({"autoPositives": {"L1": 1, "L8": 1}, "explicitNegatives": [],
                           "autoNegatives": []}, target)
            out = os.path.join(temp_dir, "out")
            sec = RoutingSec({1: doc(L8_TABLE), 2: doc(EARLIER_HEADER, L1_TABLE)})
            before = tree_snapshot()
            with mock.patch.object(bq, "FIN_DIR", fin_dir), \
                    mock.patch.object(bq.seed, "parse_ticker_map", return_value={"AAA": 1, "BBB": 2}), \
                    mock.patch.object(bq, "Sec", return_value=sec), \
                    mock.patch.object(bq.seed, "resolve_user_agent", return_value="test-agent"), \
                    contextlib.redirect_stdout(io.StringIO()):
                status = bq.main(["--mode", "diagnose", "--source-report", source,
                                  "--fixture-selection", manifest, "--out-dir", out])
            self.assertEqual(status, 0)
            with open(os.path.join(out, "s1-classes.json"), encoding="utf-8") as handle:
                s1 = json.load(handle)
            self.assertEqual((s1["rowCount"], s1["status"]), (2, "complete"))
            self.assertEqual((s1["classCounts"]["L8"], s1["classCounts"]["L1"]), (1, 1))
            self.assertEqual(sorted(os.listdir(os.path.join(out, "fixtures"))),
                             ["AAA-2024-12-31-L8.html", "BBB-2024-12-31-L1.html", "s1_fixtures.json"])
            with open(os.path.join(out, "fixtures", "s1_fixtures.json"), encoding="utf-8") as handle:
                manifest_doc = json.load(handle)
            self.assertEqual(manifest_doc["devTickers"], ["AAA", "BBB"])
            for record in manifest_doc["fixtures"]:
                self.assertEqual(record["role"], "positive")
                with open(os.path.join(out, "fixtures", record["file"]), "rb") as handle:
                    self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), record["sha256"])
            with open(os.path.join(out, "fixtures", "BBB-2024-12-31-L1.html"), encoding="utf-8") as handle:
                fixture = handle.read()
            self.assertTrue(fixture.startswith("<!-- Source URL: https://www.sec.gov/"))
            self.assertEqual(bq.tables_of(fixture), bq.tables_of(doc(EARLIER_HEADER, L1_TABLE)))
        self.assertEqual(tree_snapshot(), before)


class TrimAndRoundTripTests(unittest.TestCase):
    def test_attributes_are_gone_except_colspan_and_rowspan(self):
        raw = ('<table class="a"><tr><td style="x" colspan="2" data-x=\'1\'>A &amp; B<!-- note --></td>'
               '<td rowspan=3>C</td></tr></table>')
        self.assertEqual(bq.trim_table_html(raw),
                         '<table><tr><td colspan="2">A &amp; B</td><td rowspan="3">C</td></tr></table>')

    def test_every_synthetic_fixture_table_round_trips(self):
        checked = 0
        for name in sorted(os.listdir(FIXTURES)):
            if not name.endswith(".html"):
                continue
            with open(os.path.join(FIXTURES, name), encoding="utf-8") as source:
                text = source.read()
            for index in range(len(bq.tables_of(text))):
                fixture, error = bq.export_fixture(text, "https://www.sec.gov/x", [index], index)
                self.assertIsNone(error, (name, index))
                checked += 1
        self.assertGreater(checked, 0)

    def test_a_nested_table_round_trips(self):
        html = ("<table><tr><td>Outer</td></tr>"
                "<table><tr><td>Inner 1,234</td></tr></table></table>")
        self.assertEqual(len(bq.html_tables_of(html)), len(bq.tables_of(html)))
        fixture, error = bq.export_fixture(html, "u", [0], 0)
        self.assertIsNone(error)
        self.assertEqual(bq.tables_of(fixture), bq.tables_of(html))

    def test_a_table_of_70_kb_is_a_fixture_error(self):
        html = doc(tbl(tr("Net income", "x" * 70_000)))
        fixture, error = bq.export_fixture(html, "u", [0], 0)
        self.assertIsNone(fixture)
        self.assertEqual(error, "too large")


if __name__ == "__main__":
    unittest.main()

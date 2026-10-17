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


if __name__ == "__main__":
    unittest.main()

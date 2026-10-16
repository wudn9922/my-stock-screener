"""Synthetic 8-K earnings-release tables, validation, pacing, budget and fill rules for backfill_q4_eps_8k."""
import io
import json
import os
import sys
import tempfile
import unittest
from urllib import error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import backfill_q4_eps_8k as bq  # noqa: E402

RELEASE = """
<html><body><table>
<tr><td></td><td>Three Months Ended December 31, 2024</td><td></td><td>Three Months Ended December 31, 2023</td></tr>
<tr><td>Net income attributable to Acme</td><td>$</td><td>1,234</td><td>$</td><td>(567</td><td>)</td></tr>
<tr><td>Diluted earnings per share</td><td>$</td><td>0.25</td><td>$</td><td>(0.12</td><td>)</td></tr>
<tr><td>Basic earnings per share</td><td>$</td><td>0.26</td><td>$</td><td>(0.12</td><td>)</td></tr>
<tr><td>Diluted weighted average shares outstanding</td><td></td><td>4,936</td><td></td><td>4,700</td></tr>
</table></body></html>
"""


def table_of(html):
    return bq.tables_of(html)[0]


class NumberAndRowTests(unittest.TestCase):
    def test_number_parsing(self):
        self.assertEqual(bq.number("1,234"), 1234.0)
        self.assertEqual(bq.number("(567"), -567.0)
        self.assertEqual(bq.number("(0.12)"), -0.12)
        self.assertEqual(bq.number("$ 0.25"), 0.25)
        self.assertIsNone(bq.number("—"))
        self.assertIsNone(bq.number("Dec. 31, 2024"))

    def test_row_keeps_dashes_as_none_so_columns_line_up(self):
        label, values = bq.row_of(["Net income", "$", "1,000", "—", "(5", ")"])
        self.assertEqual(label, "Net income")
        self.assertEqual(values, [1000.0, None, -5.0])


class TableEpsTests(unittest.TestCase):
    def test_matches_the_column_with_the_stored_net_income_in_thousands(self):
        result, reason = bq.table_eps(table_of(RELEASE), 1_234_000)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["epsBasic"], 0.26)
        self.assertEqual(result["dilutedShares"], 4_936_000)
        self.assertEqual(result["scale"], 1000)

    def test_prior_year_column_is_used_when_it_is_the_stored_quarter(self):
        result, _ = bq.table_eps(table_of(RELEASE), -567_000)
        self.assertEqual(result["epsDiluted"], -0.12)
        self.assertEqual(result["dilutedShares"], 4_700_000)

    def test_a_table_without_this_quarter_is_not_a_candidate(self):
        self.assertEqual(bq.table_eps(table_of(RELEASE), 999), (None, None))

    def test_ambiguous_columns_are_rejected(self):
        html = ("<table><tr><td>Net income</td><td>1,000</td><td>1,000</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>1.00</td><td>1.00</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_000_000)
        self.assertIsNone(result)
        self.assertEqual(reason, "net income matches more than one column or unit")

    def test_eps_sign_must_follow_net_income(self):
        html = ("<table><tr><td>Net loss</td><td>(100</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.50</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), -100_000)
        self.assertIsNone(result)
        self.assertEqual(reason, "EPS sign differs from net income")

    def test_net_income_divided_by_shares_must_agree_with_eps(self):
        html = RELEASE.replace("<td>0.25</td>", "<td>0.40</td>")
        result, reason = bq.table_eps(table_of(html), 1_234_000)
        self.assertIsNone(result)
        self.assertEqual(reason, "net income divided by shares differs from EPS")


class FilingTests(unittest.TestCase):
    def test_candidate_window_and_order(self):
        rows = [
            {"form": "8-K", "items": "2.02,9.01", "filingDate": "2025-02-05", "accessionNumber": "a"},
            {"form": "8-K", "items": "5.02", "filingDate": "2025-02-01", "accessionNumber": "b"},
            {"form": "10-Q", "items": "", "filingDate": "2025-02-03", "accessionNumber": "c"},
            {"form": "8-K", "items": "2.02", "filingDate": "2025-01-20", "accessionNumber": "d"},
            {"form": "8-K", "items": "2.02", "filingDate": "2026-09-01", "accessionNumber": "e"},
        ]
        from datetime import date
        picked = bq.candidate_filings(rows, date(2024, 12, 31))
        self.assertEqual([row["accessionNumber"] for row in picked], ["d", "a"])

    def test_find_quarter_reads_the_exhibit_of_the_release(self):
        class FakeSec:
            def get_json(self, url):
                return {"directory": {"item": [{"name": "ex99-1.htm", "type": "EX-99.1"},
                                               {"name": "form8k.htm", "type": "8-K"}]}}

            def get_text(self, url):
                self.asked = url
                return RELEASE

        rows = [{"form": "8-K", "items": "2.02", "filingDate": "2025-02-05", "accessionNumber": "0001-25-000001"}]
        result, reason = bq.find_quarter(FakeSec(), 1234, rows, "2024-12-31", 1_234_000)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["accession"], "0001-25-000001")
        self.assertTrue(result["url"].endswith("/ex99-1.htm"))


class FillTests(unittest.TestCase):
    def test_fill_writes_only_null_fields_and_records_the_source(self):
        doc = {"quarterly": {"end": ["2024-03-31", "2024-12-31"], "fq": [1, 4],
                             "values": {"netIncome": [5, 1234000], "epsDiluted": [0.5, None],
                                        "epsBasic": [0.6, 0.3]}}}
        found = {"2024-12-31": {"epsDiluted": 0.25, "epsBasic": 0.26, "dilutedShares": 4936000,
                                "accession": "a", "url": "u", "filingDate": "2025-02-05"}}
        bq.apply_fill(doc, found)
        values = doc["quarterly"]["values"]
        self.assertEqual(values["epsDiluted"], [0.5, 0.25])
        self.assertEqual(values["epsBasic"], [0.6, 0.3])              # existing value kept
        self.assertEqual(values["dilutedShares"], [None, 4936000])
        self.assertEqual(doc["q4EpsSources"]["2024-12-31"]["accession"], "a")
        self.assertNotIn("2024-03-31", doc["q4EpsSources"])


class SecClientTests(unittest.TestCase):
    def sec(self, opener, budget=None):
        return bq.Sec("test agent", budget=budget, opener=opener, sleep=lambda seconds: None)

    def test_403_stops_at_once(self):
        def opener(req):
            raise error.HTTPError(req.full_url, 403, "Forbidden", None, io.BytesIO(b""))
        with self.assertRaises(bq.Forbidden):
            self.sec(opener).get("https://www.sec.gov/x")

    def test_404_is_none(self):
        def opener(req):
            raise error.HTTPError(req.full_url, 404, "Not Found", None, io.BytesIO(b""))
        self.assertIsNone(self.sec(opener).get("https://www.sec.gov/x"))

    def test_budget_pauses_before_the_next_request(self):
        sec = self.sec(lambda req: b"{}", budget=1)
        self.assertEqual(sec.get_json("https://www.sec.gov/a"), {})
        with self.assertRaises(bq.OutOfBudget):
            sec.get("https://www.sec.gov/b")
        self.assertEqual(sec.requests, 1)

    def test_user_agent_and_no_compression_are_sent(self):
        seen = {}

        def opener(req):
            seen.update(req.headers)
            return b"ok"
        self.sec(opener).get("https://www.sec.gov/x")
        self.assertEqual(seen.get("User-agent"), "test agent")
        self.assertEqual(seen.get("Accept-encoding"), "identity")


class SummaryTests(unittest.TestCase):
    def test_backtest_summary_counts_correct_share_of_all_periods(self):
        rows = [
            {"stored": 0.25, "result": {"epsDiluted": 0.25}, "reason": None},
            {"stored": 1.00, "result": {"epsDiluted": 1.50}, "reason": None},
            {"stored": 0.40, "result": None, "reason": "EPS sign differs from net income"},
            {"stored": 0.10, "result": None, "reason": "no 8-K with Item 2.02 in the window"},
        ]
        report = bq.summarize(rows, "backtest")
        self.assertEqual(report["accepted"], 2)
        self.assertEqual(report["coverage"], 0.5)
        self.assertEqual(report["correct_share_of_all_periods"], 0.25)
        self.assertEqual(report["reasons"]["no 8-K with Item 2.02 in the window"], 1)


class RunTests(unittest.TestCase):
    """run() end to end with a fake SEC client and temporary report folders."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fin_dir = os.path.join(self.tmp.name, "financials")
        os.makedirs(self.fin_dir)
        self.state_path = os.path.join(self.tmp.name, "q4-8k-state.json")
        self.saved = (bq.FIN_DIR, bq.STATE_PATH)
        bq.FIN_DIR, bq.STATE_PATH = self.fin_dir, self.state_path

    def tearDown(self):
        bq.FIN_DIR, bq.STATE_PATH = self.saved
        self.tmp.cleanup()

    class FakeSec:
        def __init__(self):
            self.requests = 0

        def get_json(self, url):
            self.requests += 1
            if url.endswith("company_tickers.json"):
                return {"0": {"cik_str": 1234, "ticker": "TEST"}}
            if "/submissions/" in url:
                return {"filings": {"recent": {"form": ["8-K"], "filingDate": ["2025-02-05"],
                                               "accessionNumber": ["0001-25-000001"], "items": ["2.02"]}}}
            if url.endswith("index.json"):
                return {"directory": {"item": [{"name": "ex99-1.htm", "type": "EX-99.1"}]}}
            raise AssertionError(url)

        def get_text(self, url):
            self.requests += 1
            return RELEASE

    def write_test_file(self, eps):
        doc = {"symbol": "TEST", "market": "US", "currency": "USD", "annual": {"end": [], "fy": [], "values": {}},
               "quarterly": {"end": ["2024-09-30", "2024-12-31"], "fq": [3, 4], "fy": [2024, 2024],
                             "values": {"netIncome": [1, 1234000], "epsDiluted": [0.1, eps],
                                        "epsBasic": [0.1, None]}}}
        with open(os.path.join(self.fin_dir, "TEST.json"), "w", encoding="utf-8") as file:
            json.dump(doc, file)

    def args(self, mode):
        return bq.parse_args(["--mode", mode, "--tickers", "TEST", "--budget", "100"])

    def test_fill_writes_the_null_q4_only_and_saves_state(self):
        self.write_test_file(None)
        report = bq.run(self.args("fill"), self.FakeSec())
        self.assertEqual(report["filled"], 1)
        with open(os.path.join(self.fin_dir, "TEST.json"), encoding="utf-8") as file:
            doc = json.load(file)
        self.assertEqual(doc["quarterly"]["values"]["epsDiluted"], [0.1, 0.25])
        self.assertEqual(doc["quarterly"]["values"]["epsBasic"], [0.1, 0.26])
        self.assertEqual(doc["quarterly"]["values"]["dilutedShares"], [None, 4936000])
        self.assertEqual(doc["q4EpsSources"]["2024-12-31"]["accession"], "0001-25-000001")
        with open(self.state_path, encoding="utf-8") as file:
            self.assertIn("TEST", json.load(file)["done"])

    def test_fill_never_overwrites_a_stored_q4_eps(self):
        self.write_test_file(0.99)
        report = bq.run(self.args("fill"), self.FakeSec())
        self.assertEqual(report["filled"], 0)
        with open(os.path.join(self.fin_dir, "TEST.json"), encoding="utf-8") as file:
            self.assertEqual(json.load(file)["quarterly"]["values"]["epsDiluted"], [0.1, 0.99])

    def test_backtest_reads_only_and_reports_against_the_stored_value(self):
        self.write_test_file(0.25)
        target = os.path.join(self.fin_dir, "TEST.json")
        with open(target, "rb") as file:
            before = file.read()
        report = bq.run(self.args("backtest"), self.FakeSec())
        self.assertEqual(report["accepted"], 1)
        self.assertEqual(report["correct_share_of_all_periods"], 1.0)
        with open(target, "rb") as file:
            self.assertEqual(file.read(), before)
        self.assertFalse(os.path.exists(self.state_path))


if __name__ == "__main__":
    unittest.main()

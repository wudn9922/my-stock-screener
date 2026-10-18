"""Synthetic 8-K earnings-release tables, validation, pacing, budget and fill rules for backfill_q4_eps_8k."""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
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


END = "2024-12-31"


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
        result, reason = bq.table_eps(table_of(RELEASE), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["epsBasic"], 0.26)
        self.assertEqual(result["dilutedShares"], 4_936_000)
        self.assertEqual(result["scale"], 1000)

    def test_prior_year_column_is_used_when_it_is_the_stored_quarter(self):
        result, _ = bq.table_eps(table_of(RELEASE), -567_000, END)
        self.assertEqual(result["epsDiluted"], -0.12)
        self.assertEqual(result["dilutedShares"], 4_700_000)

    def test_a_table_without_this_quarter_is_not_a_candidate(self):
        self.assertEqual(bq.table_eps(table_of(RELEASE), 999, END), (None, None))

    def test_ambiguous_columns_are_rejected(self):
        html = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                "<tr><td>Net income</td><td>1,000</td><td>1,000</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>1.00</td><td>1.00</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_000_000, END)
        self.assertIsNone(result)
        self.assertEqual(reason, "net income matches more than one column or unit")

    def test_eps_sign_must_follow_net_income(self):
        html = ("<table><tr><td>Three Months Ended December 31, 2024</td></tr>"
                "<tr><td>Net loss</td><td>(100</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.50</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), -100_000, END)
        self.assertIsNone(result)
        self.assertEqual(reason, "EPS sign differs from net income")

    def test_net_income_divided_by_shares_must_agree_with_eps(self):
        html = RELEASE.replace("<td>0.25</td>", "<td>0.40</td>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(result)
        self.assertEqual(reason, "net income divided by shares differs from EPS")

    def test_a_table_for_another_period_is_not_used(self):
        result, reason = bq.table_eps(table_of(RELEASE), 1_234_000, "2023-06-30")
        self.assertEqual((result, reason), (None, None))

    def test_the_period_name_must_match_month_and_year(self):
        self.assertTrue(bq.shows_period("Three Months Ended February 1, 2020", "2020-02-01"))
        self.assertTrue(bq.shows_period("three months ended Jan. 31, 2026", "2026-01-31"))
        self.assertFalse(bq.shows_period("Twelve Months Ended December 31, 2023", "2024-12-31"))


class NonGaapTests(unittest.TestCase):
    def test_non_gaap_rows_are_never_used(self):
        html = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                "<tr><td>Non-GAAP net income</td><td>1,500</td></tr>"
                "<tr><td>Non-GAAP diluted earnings per share</td><td>0.30</td></tr>"
                "<tr><td>Net income</td><td>1,234</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)


class PerShareHeadingTests(unittest.TestCase):
    def test_basic_and_diluted_rows_under_a_per_share_heading_are_eps_rows(self):
        html = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,234</td></tr>"
                "<tr><td>Net income per share:</td></tr>"
                "<tr><td>Basic</td><td>0.26</td></tr>"
                "<tr><td>Diluted</td><td>0.25</td></tr>"
                "<tr><td>Diluted weighted average shares outstanding</td><td>4,936</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["epsBasic"], 0.26)
        self.assertEqual(result["dilutedShares"], 4_936_000)


class PerShareHeadingGuardTests(unittest.TestCase):
    def test_share_counts_under_the_heading_are_not_eps(self):
        html = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,234</td></tr>"
                "<tr><td>Net income per share:</td></tr>"
                "<tr><td>Basic</td><td>11,680,033</td></tr>"
                "<tr><td>Diluted</td><td>12,041,825</td></tr>"
                "<tr><td>Basic earnings per share</td><td>0.26</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)

    def test_the_heading_reaches_only_the_two_rows_under_it(self):
        html = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,234</td></tr>"
                "<tr><td>Net income per share:</td></tr>"
                "<tr><td>Revenue</td><td>5,000</td></tr>"
                "<tr><td>Gross profit</td><td>3,000</td></tr>"
                "<tr><td>Diluted</td><td>0.99</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)


class EpsRangeTests(unittest.TestCase):
    def test_a_share_count_read_as_eps_is_rejected(self):
        html = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,234</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>26,671</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(result)
        self.assertEqual(reason, "EPS is out of range (a share count?)")


class ZeroAndNetRowTests(unittest.TestCase):
    def test_zero_eps_is_never_accepted(self):
        html = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                "<tr><td>Net income</td><td>1,234</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>(0.00)</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(result)
        self.assertEqual(reason, "EPS is zero")

    def test_the_total_eps_is_used_not_the_continuing_operations_row(self):
        html = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,234</td></tr>"
                "<tr><td>Earnings per share from continuing operations - diluted</td><td>0.30</td></tr>"
                "<tr><td>Earnings per share - diluted</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)

    def test_a_later_net_income_row_can_match_the_stored_value(self):
        html = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                "<tr><td>Net income</td><td>1,500</td></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,234</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["netLabel"], "net income attributable to acme")


class StrictNetIncomeTableTests(unittest.TestCase):
    """S2a-2 (e): table_eps matches net income with strict_scales (decimals-aware, capped at 1% of the target)."""

    @staticmethod
    def table(net_cells, eps="0.25"):
        return bq.tables_of("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                            f"<tr><td>Net income</td>{net_cells}</tr>"
                            f"<tr><td>Diluted earnings per share</td><td>{eps}</td></tr></table>")[0]

    def test_a_zero_does_not_match_a_small_target_at_a_big_scale(self):
        table = self.table("<td>0</td>")
        self.assertEqual(bq.table_eps(table, -486_275, END), (None, None))

    def test_a_one_decimal_value_must_match_to_its_own_half_unit(self):
        table = self.table("<td>0.2</td>")
        self.assertEqual(bq.table_eps(table, 210_000, END), (None, None))
        result, reason = bq.table_eps(self.table("<td>0.21</td>"), 210_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["scale"], 1_000_000)

    def test_a_negative_loss_with_one_decimal_matches_at_its_unit(self):
        result, reason = bq.table_eps(self.table("<td>(0.4)</td>", eps="(0.25)"), -400_000, END)
        self.assertIsNone(reason)
        self.assertEqual((result["scale"], result["epsDiluted"]), (1_000_000, -0.25))


INDEX_PAGE = ("<table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>"
              "<tr><td>1</td><td>8-K</td><td><a>form8k.htm</a></td><td>8-K</td><td>1000</td></tr>"
              "<tr><td>2</td><td>Press release</td><td><a>ex_309174.htm</a></td><td>EX-99.1</td><td>2000</td></tr>"
              "</table>")


class IndexPageTests(unittest.TestCase):
    def test_the_type_column_names_the_release_exhibit(self):
        self.assertEqual(bq.index_exhibits(INDEX_PAGE), ["ex_309174.htm"])
        self.assertEqual(bq.index_exhibits(None), [])

    def test_a_numbered_exhibit_file_is_read_through_the_index_page(self):
        class FakeSec:
            def get_json(self, url):
                return {"directory": {"item": [{"name": "ex_309174.htm"}, {"name": "form8k.htm"}]}}

            def get_text(self, url):
                if url.endswith("-index.html"):
                    return INDEX_PAGE
                return RELEASE if url.endswith("ex_309174.htm") else "<table></table>"

        filing = {"accessionNumber": "0001-25-000002", "filingDate": "2025-02-05"}
        result, reason = bq.from_filing(FakeSec(), 1234, filing, 1_234_000, END)
        self.assertIsNone(reason)
        self.assertTrue(result["url"].endswith("/ex_309174.htm"))

    def test_release_in_another_document_is_read_when_the_exhibit_has_no_table(self):
        class FakeSec:
            def get_json(self, url):
                return {"directory": {"item": [{"name": "exhibit99.htm"}, {"name": "form8k.htm"}]}}

            def get_text(self, url):
                if url.endswith("-index.html") or url.endswith("-index.htm"):
                    return None
                return RELEASE if url.endswith("form8k.htm") else "<table><tr><td>Revenue</td><td>5</td></tr></table>"

        filing = {"accessionNumber": "0001-25-000003", "filingDate": "2025-02-05"}
        result, reason = bq.from_filing(FakeSec(), 1234, filing, 1_234_000, END)
        self.assertIsNone(reason)
        self.assertTrue(result["url"].endswith("/form8k.htm"))


class SplitAndToleranceTests(unittest.TestCase):
    def test_split_factor_counts_only_splits_after_the_filing(self):
        doc = {"split": {"events": [["2000-06-19", 3], ["2021-07-20", 4], ["2024-06-10", 10]]}}
        self.assertEqual(bq.split_factor(doc, "2020-02-05"), 40.0)
        self.assertEqual(bq.split_factor(doc, "2024-08-01"), 1.0)
        self.assertEqual(bq.split_factor({}, "2020-02-05"), 1.0)

    def test_one_cent_rounding_is_within_tolerance(self):
        self.assertTrue(bq.within_tolerance(0.24, 0.25))
        self.assertFalse(bq.within_tolerance(0.20, 0.25))


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


NEXT_YEAR_RELEASE = """
<html><body><table>
<tr><td></td><td>Three Months Ended December 31, 2025</td><td></td><td>Three Months Ended December 31, 2024</td></tr>
<tr><td>Net income attributable to Acme</td><td>$</td><td>999</td><td>$</td><td>1,234</td></tr>
<tr><td>Diluted earnings per share</td><td>$</td><td>0.30</td><td>$</td><td>0.25</td></tr>
</table></body></html>
"""


class SplitPeriodHeaderTests(unittest.TestCase):
    def test_a_period_header_split_over_rows_is_read_column_by_column(self):
        html = ("<table><tr><td></td><td>Fourth Quarter Ended</td></tr>"
                "<tr><td>March 29,</td><td>March 29,</td></tr>"
                "<tr><td>2014</td><td>2013</td></tr>"
                "<tr><td>Net income</td><td>1,234</td><td>999</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td><td>0.20</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, "2014-03-29")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)

    def test_the_year_must_match_the_split_header(self):
        html = ("<table><tr><td>March 29,</td></tr><tr><td>2013</td></tr>"
                "<tr><td>Net income</td><td>1,234</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr></table>")
        self.assertFalse(bq.shows_period_by_column(table_of(html), "2014-03-29"))


class OrphanDiagnosticTests(unittest.TestCase):
    def test_a_net_income_table_without_its_period_is_counted_with_the_table_before_it(self):
        html = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr></table>"
                "<table><tr><td>Net income attributable to Acme</td><td>$</td><td>999</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>$</td><td>0.30</td></tr></table>")

        class FakeSec:
            def get_json(self, url):
                return {"directory": {"item": [{"name": "ex99-1.htm"}]}}

            def get_text(self, url):
                return None if url.endswith(("-index.html", "-index.htm")) else html

        filing = {"accessionNumber": "0001-26-000009", "filingDate": "2026-02-05"}
        debug = []
        result, reason = bq.from_filing(FakeSec(), 1234, filing, 1_234_000, "2024-12-31", debug)
        self.assertIsNone(result)
        self.assertEqual(debug[0]["orphans"]["net_tables_without_period"], 1)
        self.assertEqual(debug[0]["orphans"]["period_in_previous_table"], 1)


ZERO_EPS_RELEASE = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                    "<tr><td>Net income</td><td>1,234</td></tr>"
                    "<tr><td>Diluted earnings per share</td><td>(0.00)</td></tr></table>")
AMBIGUOUS_RELEASE = ("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                     "<tr><td>Net income</td><td>1,234</td><td>1,234</td></tr>"
                     "<tr><td>Diluted earnings per share</td><td>1.00</td><td>1.00</td></tr></table>")
NO_QUARTER_RELEASE = "<table><tr><td>Revenue</td><td>5</td></tr></table>"


class FindQuarterReasonTests(unittest.TestCase):
    """S2a-2 (f): the first specific reason across the candidate filings; the generic one only if none is specific."""

    @staticmethod
    def filings(*accessions):
        return [{"form": "8-K", "items": "2.02", "filingDate": f"2025-0{2 + i}-05", "accessionNumber": accession}
                for i, accession in enumerate(accessions)]

    @staticmethod
    def sec(pages):
        class FakeSec:
            def get_json(self, url):
                if url.endswith("index.json"):
                    return {"directory": {"item": [{"name": "ex99-1.htm", "type": "EX-99.1"}]}}
                return None

            def get_text(self, url):
                for folder, html in pages.items():
                    if folder in url and url.endswith("ex99-1.htm"):
                        return html
                return None
        return FakeSec()

    def reason(self, first, second):
        rows = self.filings("0001-25-000011", "0001-25-000012")
        pages = {"000125000011": first, "000125000012": second}
        return bq.find_quarter(self.sec(pages), 1234, rows, END, 1_234_000)

    def test_the_first_specific_reason_wins_over_a_later_generic_one(self):
        self.assertEqual(self.reason(ZERO_EPS_RELEASE, NO_QUARTER_RELEASE), (None, "EPS is zero"))

    def test_a_later_specific_reason_is_used_when_the_first_filing_is_generic(self):
        self.assertEqual(self.reason(NO_QUARTER_RELEASE, AMBIGUOUS_RELEASE),
                         (None, "net income matches more than one column or unit"))

    def test_the_first_of_two_specific_reasons_wins(self):
        self.assertEqual(self.reason(ZERO_EPS_RELEASE, AMBIGUOUS_RELEASE), (None, "EPS is zero"))

    def test_the_generic_reason_is_used_only_when_no_filing_is_specific(self):
        self.assertEqual(self.reason(NO_QUARTER_RELEASE, NO_QUARTER_RELEASE), (None, bq.GENERIC_REASON))

    def test_no_candidate_filing_gives_the_window_reason(self):
        self.assertEqual(bq.find_quarter(self.sec({}), 1234, [], END, 1_234_000),
                         (None, "no 8-K with Item 2.02 in the window"))

    def test_the_generic_reason_text_is_the_one_from_filing(self):
        rows = self.filings("0001-25-000013")
        result, reason = bq.from_filing(self.sec({"000125000013": NO_QUARTER_RELEASE}), 1234, rows[0], 1_234_000, END)
        self.assertIsNone(result)
        self.assertEqual(reason, bq.GENERIC_REASON)


class IndependentCheckTests(unittest.TestCase):
    rows = [{"form": "8-K", "items": "2.02", "filingDate": "2026-02-05", "accessionNumber": "0001-26-000001"}]

    @staticmethod
    def sec_with(html):
        class FakeSec:
            def get_json(self, url):
                return {"directory": {"item": [{"name": "ex99-1.htm"}]}}

            def get_text(self, url):
                return None if url.endswith(("-index.html", "-index.htm")) else html

        return FakeSec()

    def test_the_prior_year_column_of_the_next_release_confirms_the_value(self):
        check = bq.verify_quarter(self.sec_with(NEXT_YEAR_RELEASE), 1234, self.rows, END, 1_234_000, 0.25, {})
        self.assertEqual(check["status"], "confirmed")
        self.assertEqual(check["epsDiluted"], 0.25)

    def test_a_different_prior_year_eps_is_a_conflict(self):
        check = bq.verify_quarter(self.sec_with(NEXT_YEAR_RELEASE), 1234, self.rows, END, 1_234_000, 0.20, {})
        self.assertEqual(check["status"], "conflict")

    def test_a_restated_prior_year_net_income_is_kept_as_a_diagnostic(self):
        check = bq.verify_quarter(self.sec_with(NEXT_YEAR_RELEASE), 1234, self.rows, END, 1_200_000, 0.25, {})
        self.assertEqual(check["status"], "unavailable")
        self.assertEqual(check["reason"], "no table shows this quarter's net income")
        self.assertEqual(check["debug"][0]["period_net_rows"], [["net income attributable to acme", [999.0, 1234.0]]])

    def test_no_next_year_release_is_unavailable(self):
        check = bq.verify_quarter(self.sec_with(NEXT_YEAR_RELEASE), 1234, [], END, 1_234_000, 0.25, {})
        self.assertEqual(check["status"], "unavailable")
        self.assertIn("Item 2.02", check["reason"])


def next_year_release(prior_eps):
    return f"""
<html><body><table>
<tr><td></td><td>Three Months Ended December 31, 2025</td><td></td><td>Three Months Ended December 31, 2024</td></tr>
<tr><td>Net income attributable to Acme</td><td>$</td><td>999</td><td>$</td><td>1,234</td></tr>
<tr><td>Diluted earnings per share</td><td>$</td><td>0.30</td><td>$</td><td>{prior_eps}</td></tr>
</table></body></html>
"""


class IndependentCheckToleranceTests(unittest.TestCase):
    """S2a-2 (d): "confirmed" needs |checked - eps| <= max(0.011, 0.005 * |eps|)."""
    rows = [{"form": "8-K", "items": "2.02", "filingDate": "2026-02-05", "accessionNumber": "0001-26-000001"}]

    def status(self, prior_eps, stored_eps):
        sec = IndependentCheckTests.sec_with(next_year_release(prior_eps))
        return bq.verify_quarter(sec, 1234, self.rows, END, 1_234_000, stored_eps, {})["status"]

    def test_a_gap_of_one_and_a_half_cents_on_a_dollar_eps_is_a_conflict(self):
        self.assertEqual(self.status("1.015", 1.00), "conflict")

    def test_the_eleven_cent_boundary_is_still_confirmed(self):
        self.assertEqual(self.status("1.0105", 1.00), "confirmed")
        self.assertEqual(self.status("1.0115", 1.00), "conflict")

    def test_the_half_percent_applies_to_a_larger_eps(self):
        self.assertEqual(self.status("10.04", 10.00), "confirmed")
        self.assertEqual(self.status("10.06", 10.00), "conflict")


class FillGradeTests(unittest.TestCase):
    def test_a_confirmed_value_is_grade_a_and_an_unconfirmed_one_is_grade_c(self):
        doc = {"quarterly": {"end": ["2024-12-31", "2023-12-31"], "fq": [4, 4],
                             "values": {"netIncome": [1234000, 5], "epsDiluted": [None, None]}}}
        found = {
            "2024-12-31": {"epsDiluted": 0.25, "epsBasic": None, "dilutedShares": None, "accession": "a",
                           "url": "u", "filingDate": "2025-02-05", "verified": {"status": "confirmed"}},
            "2023-12-31": {"epsDiluted": 0.10, "epsBasic": None, "dilutedShares": None, "accession": "b",
                           "url": "v", "filingDate": "2024-02-05", "verified": {"status": "unavailable"}},
        }
        bq.apply_fill(doc, found)
        quality = doc["quarterly"]["epsQuality"]
        self.assertEqual(quality[0]["epsDiluted"], "A")
        self.assertEqual(quality[0]["epsDilutedFiled"], "2025-02-05")
        self.assertEqual(quality[1]["epsDiluted"], "C")
        self.assertEqual(doc["q4EpsSources"]["2024-12-31"]["grade"], "A")
        self.assertEqual(doc["quarterly"]["values"]["epsDiluted"], [0.25, 0.10])

    def test_an_existing_eps_keeps_its_own_grade(self):
        doc = {"quarterly": {"end": ["2024-12-31"], "fq": [4], "values": {"netIncome": [1234000],
                                                                     "epsDiluted": [None], "epsBasic": [0.26]},
                             "epsQuality": [{"epsBasic": "A"}]}}
        found = {"2024-12-31": {"epsDiluted": 0.25, "epsBasic": 0.27, "dilutedShares": None, "accession": "a",
                                "url": "u", "filingDate": "2025-02-05", "verified": {"status": "confirmed"}}}
        bq.apply_fill(doc, found)
        entry = doc["quarterly"]["epsQuality"][0]
        self.assertEqual(entry["epsBasic"], "A")
        self.assertEqual(entry["epsDiluted"], "A")
        self.assertEqual(doc["quarterly"]["values"]["epsBasic"], [0.26])


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


ANSWER_SET_PATH = os.path.join(ROOT, "docs", "report", "q4-8k", "answer-set.json")


class ShardTests(unittest.TestCase):
    def test_every_key_is_in_exactly_one_shard_for_each_split(self):
        keys = sorted(bq.load_answer_set(ANSWER_SET_PATH))
        self.assertEqual(len(keys), 563)
        for count in (1, 2, 8, 16):
            with self.subTest(count=count):
                parts = [bq.shard_keys(keys, (index, count)) for index in range(count)]
                flat = [key for part in parts for key in part]
                self.assertEqual(len(flat), len(set(flat)))
                self.assertEqual(sorted(flat), keys)
                for index, part in enumerate(parts):
                    self.assertTrue(all(keys.index(key) % count == index for key in part))

    def test_shards_take_sorted_unique_keys_by_index_mod_n(self):
        self.assertEqual(bq.shard_keys(["b", "a", "b", "c", "d"], None), ["a", "b", "c", "d"])
        self.assertEqual(bq.shard_keys(["b", "a", "b", "c", "d"], (0, 2)), ["a", "c"])
        self.assertEqual(bq.shard_keys(["b", "a", "b", "c", "d"], (1, 2)), ["b", "d"])

    def test_shard_argument_must_be_k_over_n_with_k_below_n(self):
        self.assertEqual(bq.parse_shard("3/8"), (3, 8))
        for bad in ("8/8", "0/0", "x/2", "-1/2", "2", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(argparse.ArgumentTypeError):
                    bq.parse_shard(bad)


class AnswerSetFileTests(unittest.TestCase):
    def write(self, rows):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"rows": rows}, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_the_committed_answer_set_has_its_pinned_counts(self):
        answer = bq.load_answer_set(ANSWER_SET_PATH)
        self.assertEqual(len(answer), 563)
        self.assertEqual(sum(len(rows) for rows in answer.values()), 2649)

    def test_a_duplicate_key_and_end_is_refused(self):
        row = {"key": "ACME", "end": "2024-12-31", "fy": 2024, "value": 0.25, "netIncome": 1}
        with self.assertRaises(ValueError):
            bq.load_answer_set(self.write([row, dict(row, value=0.3)]))

    def test_a_row_without_a_numeric_net_income_is_refused(self):
        row = {"key": "ACME", "end": "2024-12-31", "fy": 2024, "value": 0.25, "netIncome": None}
        with self.assertRaises(ValueError):
            bq.load_answer_set(self.write([row]))

    def test_rows_are_grouped_by_key_and_sorted_by_end(self):
        rows = [{"key": "ACME", "end": "2024-12-31", "fy": 2024, "value": 0.25, "netIncome": 1},
                {"key": "ACME", "end": "2023-12-31", "fy": 2023, "value": 0.5, "netIncome": 2}]
        answer = bq.load_answer_set(self.write(rows))
        self.assertEqual([row["end"] for row in answer["ACME"]], ["2023-12-31", "2024-12-31"])


GATE_ANSWERS = [
    {"key": "ACME", "end": "2023-12-31", "fy": 2023, "value": 0.50, "netIncome": 2000000, "accession": "A2"},
    {"key": "ACME", "end": "2024-12-31", "fy": 2024, "value": 0.25, "netIncome": 1234000, "accession": "A1"},
    {"key": "BETA", "end": "2024-12-31", "fy": 2024, "value": -0.10, "netIncome": -100000, "accession": "B1"},
    {"key": "DEV", "end": "2024-12-31", "fy": 2024, "value": 1.00, "netIncome": 5000000, "accession": "D1"},
]


def gate_row(key, end, estimate, status="confirmed", checked=None):
    result = None if estimate is None else {
        "epsDiluted": estimate, "url": f"https://example.test/{key}/{end}",
        "verified": {"status": status, "epsDiluted": checked if checked is not None else estimate,
                     "url": f"https://example.test/check/{key}/{end}"}}
    return {"key": key, "end": end, "stored": None, "result": result,
            "reason": None if estimate is not None else "EPS sign differs from net income"}


class GateTests(unittest.TestCase):
    """--mode gate: offline, no SEC client, exit 3 and no summary unless each answer row is in exactly one report."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.answer = os.path.join(self.tmp.name, "answer-set.json")
        with open(self.answer, "w", encoding="utf-8") as file:
            json.dump({"rows": GATE_ANSWERS}, file)
        self.dev = os.path.join(self.tmp.name, "s1_fixtures.json")
        with open(self.dev, "w", encoding="utf-8") as file:
            json.dump({"devTickers": ["DEV"]}, file)
        self.summary = os.path.join(self.tmp.name, "summary.json")
        saved = bq.DEV_FIXTURES
        bq.DEV_FIXTURES = self.dev
        self.addCleanup(setattr, bq, "DEV_FIXTURES", saved)
        no_sec = mock.patch.object(bq, "Sec", side_effect=AssertionError("gate mode must not create a SEC client"))
        self.sec = no_sec.start()
        self.addCleanup(no_sec.stop)

    def report(self, name, rows):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as file:
            json.dump({"mode": "backtest", "rows": rows}, file)
        return path

    def gate(self, *reports):
        argv = ["--mode", "gate", "--answer-set", self.answer, "--reports", *reports, "--report", self.summary]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return bq.main(argv)

    def all_correct(self):
        return [gate_row("ACME", "2023-12-31", 0.50), gate_row("ACME", "2024-12-31", 0.25),
                gate_row("BETA", "2024-12-31", -0.10), gate_row("DEV", "2024-12-31", 1.00)]

    def read_summary(self):
        with open(self.summary, encoding="utf-8") as file:
            return json.load(file)

    def test_every_answer_row_in_one_report_passes_and_writes_the_summary(self):
        code = self.gate(self.report("a.json", self.all_correct()))
        self.assertEqual(code, 0)
        summary = self.read_summary()
        self.assertEqual((summary["all"]["accepted"], summary["all"]["correct"]), (4, 4))
        self.assertEqual(summary["all"]["coverage"], 1.0)
        self.assertEqual(summary["all"]["precision"], 1.0)
        self.assertEqual(summary["devTickers"], ["DEV"])
        self.sec.assert_not_called()

    def test_shards_that_partition_the_keys_pass(self):
        first = self.report("a.json", self.all_correct()[:2])
        second = self.report("b.json", self.all_correct()[2:])
        self.assertEqual(self.gate(first, second), 0)
        self.assertEqual(self.read_summary()["all"]["accepted"], 4)

    def test_a_key_in_no_report_exits_3_and_writes_nothing(self):
        code = self.gate(self.report("a.json", self.all_correct()[:3]))
        self.assertEqual(code, 3)
        self.assertFalse(os.path.exists(self.summary))

    def test_a_key_in_two_reports_exits_3_and_writes_nothing(self):
        code = self.gate(self.report("a.json", self.all_correct()),
                         self.report("b.json", [gate_row("ACME", "2024-12-31", 0.25)]))
        self.assertEqual(code, 3)
        self.assertFalse(os.path.exists(self.summary))

    def test_a_split_shard_that_repeats_a_key_of_the_full_shard_exits_3(self):
        full_shard = self.report("full.json", self.all_correct())
        half_shard = self.report("half.json", [gate_row("ACME", "2023-12-31", 0.50)])
        self.assertEqual(self.gate(full_shard, half_shard), 3)
        self.assertFalse(os.path.exists(self.summary))

    def test_a_sign_error_is_counted_and_listed(self):
        rows = self.all_correct()
        rows[1] = gate_row("ACME", "2024-12-31", -0.25)
        self.assertEqual(self.gate(self.report("a.json", rows)), 0)
        summary = self.read_summary()
        self.assertEqual(summary["all"]["signErrors"], 1)
        self.assertEqual(summary["all"]["correct"], 3)
        self.assertEqual([(row["key"], row["estimate"], row["answer"]) for row in summary["signErrors"]],
                         [("ACME", -0.25, 0.25)])
        self.assertEqual(len(summary["incorrect"]), 1)

    def test_a_dev_ticker_is_left_out_of_the_accuracy_count(self):
        rows = self.all_correct()
        rows[3] = gate_row("DEV", "2024-12-31", 3.00)
        self.assertEqual(self.gate(self.report("a.json", rows)), 0)
        summary = self.read_summary()
        self.assertEqual((summary["all"]["accepted"], summary["all"]["correct"]), (4, 3))
        self.assertEqual((summary["excludingDevTickers"]["accepted"], summary["excludingDevTickers"]["correct"]),
                         (3, 3))
        self.assertEqual(summary["excludingDevTickers"]["precision"], 1.0)
        self.assertEqual(summary["excludingDevTickers"]["answerRows"], 3)

    def test_a_conflict_is_counted_over_extracted_rows_not_only_accepted_ones(self):
        rows = self.all_correct()
        rows[1] = gate_row("ACME", "2024-12-31", 0.25, status="conflict", checked=0.30)
        self.assertEqual(self.gate(self.report("a.json", rows)), 0)
        summary = self.read_summary()
        self.assertEqual(summary["all"]["conflicts"], 1)
        self.assertEqual(summary["all"]["accepted"], 3)
        self.assertEqual(summary["conflicts"][0]["checked"], 0.30)

    def test_the_tolerance_is_the_s3_correct_rule(self):
        self.assertTrue(bq.within_s3_tolerance(0.2605, 0.25))
        self.assertFalse(bq.within_s3_tolerance(0.2615, 0.25))
        self.assertTrue(bq.within_s3_tolerance(10.04, 10.0))
        self.assertFalse(bq.within_s3_tolerance(10.06, 10.0))

    def test_a_row_outside_the_answer_set_is_counted_as_extra_and_changes_no_metric(self):
        rows = self.all_correct() + [gate_row("ZZZZ", "2024-12-31", 9.0)]
        self.assertEqual(self.gate(self.report("a.json", rows)), 0)
        summary = self.read_summary()
        self.assertEqual((summary["extraRows"], summary["all"]["accepted"]), (1, 4))

    def test_the_dev_tickers_come_from_the_s1_manifest_by_default(self):
        bq.DEV_FIXTURES = os.path.join(ROOT, "tests", "fixtures", "q4_8k", "s1_fixtures.json")
        self.assertEqual(bq.load_dev_tickers(), ["TRAK", "TXT", "UI", "UNFI", "URBN", "VIAV", "VNCE", "WLY"])

    def test_gate_needs_an_answer_set_and_reports_and_no_out_dir(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bq.main(["--mode", "gate", "--reports", self.report("a.json", [])]), 2)
            self.assertEqual(bq.main(["--mode", "gate", "--answer-set", self.answer]), 2)
            self.assertEqual(bq.main(["--mode", "gate", "--answer-set", self.answer, "--reports",
                                      self.report("a.json", []), "--out-dir", self.tmp.name]), 2)
        self.sec.assert_not_called()


class AnswerSetBacktestTests(unittest.TestCase):
    """run() with --answer-set: only the answer rows of the shard are processed, against their value and netIncome."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fin_dir = os.path.join(self.tmp.name, "financials")
        os.makedirs(self.fin_dir)
        self.saved = (bq.FIN_DIR, bq.STATE_PATH)
        bq.FIN_DIR, bq.STATE_PATH = self.fin_dir, os.path.join(self.tmp.name, "state.json")
        self.addCleanup(setattr, bq, "FIN_DIR", self.saved[0])
        self.addCleanup(setattr, bq, "STATE_PATH", self.saved[1])
        doc = {"symbol": "TEST", "market": "US", "quarterly": {
            "end": ["2023-12-31", "2024-09-30", "2024-12-31"], "fq": [4, 3, 4], "fy": [2023, 2024, 2024],
            "values": {"netIncome": [9, 1, 1234000], "epsDiluted": [0.9, 0.1, None]}}}
        with open(os.path.join(self.fin_dir, "TEST.json"), "w", encoding="utf-8") as file:
            json.dump(doc, file)
        self.answer = os.path.join(self.tmp.name, "answer-set.json")
        with open(self.answer, "w", encoding="utf-8") as file:
            json.dump({"rows": [{"key": "TEST", "end": "2024-12-31", "fy": 2024, "value": 0.25,
                                 "netIncome": 1234000, "accession": "T1"}]}, file)

    def args(self, *extra):
        return bq.parse_args(["--mode", "backtest", "--answer-set", self.answer, "--budget", "100", *extra])

    def test_only_the_answer_row_is_processed_and_its_value_is_the_reference(self):
        report = bq.run(self.args(), RunTests.FakeSec())
        self.assertEqual([(row["key"], row["end"], row["stored"]) for row in report["rows"]],
                         [("TEST", "2024-12-31", 0.25)])
        self.assertEqual(report["accepted"], 1)
        self.assertEqual(report["correct_share_of_all_periods"], 1.0)

    def test_the_financials_file_is_not_written(self):
        target = os.path.join(self.fin_dir, "TEST.json")
        with open(target, "rb") as file:
            before = file.read()
        bq.run(self.args(), RunTests.FakeSec())
        with open(target, "rb") as file:
            self.assertEqual(file.read(), before)

    def test_a_shard_processes_only_its_keys(self):
        with open(self.answer, encoding="utf-8") as file:
            document = json.load(file)
        document["rows"].append({"key": "AAAA", "end": "2024-12-31", "fy": 2024, "value": 1.0, "netIncome": 1})
        with open(self.answer, "w", encoding="utf-8") as file:
            json.dump(document, file)
        other = bq.run(self.args("--shard", "0/2"), RunTests.FakeSec())
        mine = bq.run(self.args("--shard", "1/2"), RunTests.FakeSec())
        self.assertEqual(other["rows"], [])
        self.assertEqual([row["key"] for row in mine["rows"]], ["TEST"])

    def test_answer_set_options_are_refused_before_any_sec_request(self):
        with mock.patch.object(bq, "Sec", side_effect=AssertionError("no SEC client")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bq.main(["--mode", "fill", "--answer-set", self.answer]), 2)
            self.assertEqual(bq.main(["--mode", "backtest", "--shard", "0/2"]), 2)
            self.assertEqual(bq.main(["--mode", "backtest", "--answer-set", self.answer, "--tickers", "TEST"]), 2)
            self.assertEqual(bq.main(["--mode", "backtest", "--reports", self.answer]), 2)


if __name__ == "__main__":
    unittest.main()

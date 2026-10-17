"""Synthetic 8-K earnings-release tables, validation, pacing, budget and fill rules for backfill_q4_eps_8k."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from urllib import error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import backfill_q4_eps_8k as bq  # noqa: E402

RELEASE = """
<html><body><table>
<tr><td colspan="5">(USD in thousands, except per share amounts)</td></tr>
<tr><th></th><th colspan="2">Three Months Ended December 31, 2024</th>
    <th colspan="2">Three Months Ended December 31, 2023</th></tr>
<tr><td>Net income attributable to Acme</td><td>$</td><td>1,234</td><td>$</td><td>(567)</td></tr>
<tr><td>Diluted earnings per share</td><td>$</td><td>0.25<sup>1</sup></td><td>$</td><td>(0.12)</td></tr>
<tr><td>Basic earnings per share</td><td>$</td><td>0.26</td><td>$</td><td>(0.12)</td></tr>
<tr><td>Diluted weighted average shares outstanding</td><td></td><td>4,936</td><td></td><td>4,700</td></tr>
<tr><td colspan="5">(1) Per share amounts reflect the reported period.</td></tr>
</table></body></html>
"""


END = "2024-12-31"


def table_of(html):
    return bq.structured_tables_of(html)[0]


def audited_source(end, field, value, accession, filing_date="2025-02-05", cik="1"):
    name = "ex99.htm"
    compact = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/{name}"
    return {"coverageComplete": True, "allVersionsAgree": True, "periodEnd": end,
            "fields": {field: {"field": field, "reportedValue": value, "periodEnd": end,
                               "periodStart": "2024-10-01", "periodKind": "Q4", "gaap": True,
                               "shareClass": "entity-total", "unit": "USD/shares", "cik": cik,
                               "accession": accession, "form": "8-K", "filingDate": filing_date,
                               "document": name, "url": url, "sha256": "a" * 64,
                               "excerpt": "GAAP Diluted earnings per share 0.25",
                               "locator": {"tableIndex": 0, "row": 1, "column": 1}}}}


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
        result, reason = bq.table_eps(table_of(RELEASE), 1_234_000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["epsBasic"], 0.26)
        self.assertEqual(result["dilutedShares"], 4_936_000)
        self.assertEqual(result["scale"], 1000)

    def test_basic_only_gaap_quarter_is_extractable_without_inventing_diluted_eps(self):
        html = ("<table><caption>USD</caption><tr><th></th><th colspan='2'>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Basic earnings per share</td><td>$</td><td>(0.13)</td></tr></table>")
        result, reason = bq.table_basic_eps(table_of(html), END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsBasic"], -0.13)
        self.assertIsNone(result["epsDiluted"])
        self.assertTrue(result["evidence"]["classification"]["gaap"])

    def test_class_specific_basic_only_eps_stays_pending(self):
        html = ("<table><caption>USD</caption><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Basic earnings per share, Class A</td><td>0.13</td></tr></table>")
        result, reason = bq.table_basic_eps(table_of(html), END)
        self.assertIsNone(result)
        self.assertIn("class-specific", reason)

    def test_class_specific_basic_row_is_not_mislabeled_as_entity_basic(self):
        html = ("<table><caption>USD</caption><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,000</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr>"
                "<tr><td>Basic earnings per share, Class A</td><td>0.26</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1000, END)
        self.assertIsNone(reason)
        self.assertIsNone(result["epsBasic"])
        self.assertEqual(result["epsDiluted"], 0.25)

    def test_basic_eps_under_continuing_operations_heading_is_not_entity_total(self):
        html = ("<table><caption>USD</caption><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,000</td></tr>"
                "<tr><td>Earnings per share, diluted</td><td>0.25</td></tr>"
                "<tr><td colspan='2'>Continuing operations</td></tr>"
                "<tr><td>Earnings per share, basic</td><td>0.26</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertIsNone(result["epsBasic"])
        self.assertIn("basic-eps-operations-scope-is-not-entity-total",
                      result["qualityByField"]["epsBasic"]["reviewFlags"])
        self.assertTrue(result["qualityByField"]["epsDiluted"]["candidateValidated"])

    def test_basic_eps_under_class_heading_is_not_entity_total(self):
        html = ("<table><caption>USD</caption><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,000</td></tr>"
                "<tr><td>Earnings per share, diluted</td><td>0.25</td></tr>"
                "<tr><td colspan='2'>Class A</td></tr>"
                "<tr><td>Earnings per share, basic</td><td>0.26</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertIsNone(result["epsBasic"])
        self.assertIn("basic-eps-share-class-or-ownership-needs-review",
                      result["qualityByField"]["epsBasic"]["reviewFlags"])
        self.assertTrue(result["qualityByField"]["epsDiluted"]["candidateValidated"])

    def test_foreign_currency_eps_is_not_validated_as_usd(self):
        html = ("<table><caption>EUR</caption><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,000</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertIn("eps-currency-is-not-verified-usd", result["reviewFlags"])
        self.assertFalse(result["candidateValidated"])

    def test_selected_column_currency_overrides_conflicting_table_caption(self):
        html = ("<table><caption>Amounts in USD</caption><tr><th></th>"
                "<th>Three Months Ended December 31, 2024 (CAD)</th></tr>"
                "<tr><td>Net income</td><td>1,000</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>$0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertNotEqual(result["evidence"]["currency"]["status"], "verified-usd")
        self.assertFalse(result["candidateValidated"])

    def test_mixed_currency_caption_without_selected_usd_column_stays_ambiguous(self):
        html = ("<table><caption>Amounts in USD and CAD</caption><tr><th></th>"
                "<th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,000</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>$0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["evidence"]["currency"]["status"], "ambiguous")
        self.assertFalse(result["candidateValidated"])

    def test_class_caption_is_not_entity_total_diluted_or_basic_eps(self):
        html = ("<table><caption>Class A common stock</caption><tr><th></th>"
                "<th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,000</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>$0.25</td></tr>"
                "<tr><td>Basic earnings per share</td><td>$0.26</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertIn("eps-share-class-or-ownership-needs-review", result["reviewFlags"])
        self.assertFalse(result["candidateValidated"])
        basic, reason = bq.table_basic_eps(table_of(html), END, expected_currency="USD")
        self.assertIsNone(basic)
        self.assertIn("class-specific", reason)

    def test_basic_currency_is_validated_independently_from_diluted_row(self):
        html = ("<table><caption>USD</caption><tr><th></th>"
                "<th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,000</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>$0.25</td></tr>"
                "<tr><td>Basic earnings per share CAD</td><td>$0.26</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertIsNone(result["epsBasic"])
        self.assertNotEqual(result["evidence"]["basicCurrency"]["status"], "verified-usd")

    def test_currency_text_in_another_period_column_does_not_contaminate_target_eps(self):
        html = ("<table><caption>USD</caption>"
                "<tr><th></th><th>Three Months Ended December 31, 2024</th>"
                "<th>Three Months Ended December 31, 2023</th></tr>"
                "<tr><td>Net income</td><td>$1,000</td><td>CAD 900</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>$0.25</td><td>CAD 0.20</td></tr>"
                "</table>")
        result, reason = bq.table_eps(table_of(html), 1000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["evidence"]["currency"]["status"], "verified-usd")

    def test_explicit_usd_release_is_verified_even_when_it_uses_dollar_symbol(self):
        result, reason = bq.table_eps(table_of(RELEASE), 1_234_000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["evidence"]["currency"]["status"], "verified-usd")
        self.assertTrue(result["candidateValidated"])

    def test_dollar_sign_plus_expected_usd_does_not_verify_source_currency(self):
        html = ("<table><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,000</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>$0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["evidence"]["currency"]["status"], "dollar-symbol-currency-ambiguous")
        self.assertFalse(result["candidateValidated"])

    def test_adjusted_section_heading_cannot_be_overridden_by_gaap_column_header(self):
        html = ("<table><tr><th></th><th>GAAP Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,000</td></tr><tr><td>Adjusted Results</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.30</td></tr>"
                "<tr><td>Basic earnings per share</td><td>0.29</td></tr></table>")
        table = table_of(html)
        diluted, reason = bq.table_eps(table, 1000, END, expected_currency="USD")
        basic, basic_reason = bq.table_basic_eps(table, END, expected_currency="USD")
        self.assertIsNone(diluted)
        self.assertIsNone(reason)
        self.assertIsNone(basic)
        self.assertIsNone(basic_reason)

    def test_per_ads_eps_is_not_common_share_eps(self):
        html = ("<table><caption>USD</caption><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,000</td></tr>"
                "<tr><td>Diluted earnings per ADS</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1000, END, expected_currency="USD")
        self.assertIsNone(result)
        self.assertIsNone(reason)

    def test_prior_q4_column_does_not_override_the_target_quarter_header(self):
        result, reason = bq.table_eps(table_of(RELEASE), -567_000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertIn("net-income-does-not-match-stored-quarter-value", result["reviewFlags"])
        self.assertEqual(result["evidence"]["columnHeader"],
                         "(USD in thousands, except per share amounts) Three Months Ended December 31, 2024")

    def test_eps_remains_a_candidate_when_stored_net_income_does_not_match(self):
        result, reason = bq.table_eps(table_of(RELEASE), 999, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertIn("net-income-does-not-match-stored-quarter-value", result["reviewFlags"])
        self.assertFalse(result["diagnostics"]["netIncomeMatchesStored"])

    def test_ambiguous_columns_are_rejected(self):
        html = ("<table><tr><th></th><th>Three Months Ended December 31, 2024</th>"
                "<th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,000</td><td>1,000</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>1.00</td><td>1.00</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_000_000, END)
        self.assertIsNone(result)
        self.assertIn("multiple distinct column groups", reason)
        self.assertIn("table", reason)
        self.assertIn("columns", reason)

    def test_eps_sign_disagreement_is_a_review_flag_not_a_rejection(self):
        html = ("<table><caption>USD</caption><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net loss</td><td>(100)</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.50</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), -100_000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.50)
        self.assertIn("diluted-eps-sign-differs-from-net-income", result["reviewFlags"])
        self.assertTrue(result["uniqueCandidate"])
        self.assertFalse(result["candidateValidated"])
        self.assertTrue(result["manualReviewRequired"])

    def test_share_formula_disagreement_is_a_review_flag_not_a_rejection(self):
        html = RELEASE.replace("0.25<sup>1</sup>", "0.40")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.40)
        self.assertIn("net-income-divided-by-shares-differs-from-diluted-eps", result["reviewFlags"])
        self.assertFalse(result["diagnostics"]["shareFormulaWithinTolerance"])
        self.assertTrue(result["uniqueCandidate"])
        self.assertFalse(result["candidateValidated"])
        self.assertTrue(result["manualReviewRequired"])

    def test_accounting_parentheses_split_across_cells_preserve_negative_values(self):
        html = ("<table><tr><th></th><th colspan='3'>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net loss</td><td>(</td><td>1,234</td><td>)</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>(</td><td>0.25</td><td>)</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), -1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], -0.25)
        self.assertEqual(result["net"], -1_234_000)
        self.assertTrue(result["diagnostics"]["epsSignMatchesNetIncome"])

    def test_a_table_for_another_period_is_not_used(self):
        result, reason = bq.table_eps(table_of(RELEASE), 1_234_000, "2023-06-30")
        self.assertEqual((result, reason), (None, None))

    def test_the_period_name_must_match_month_and_year(self):
        self.assertTrue(bq.shows_period("Three Months Ended February 1, 2020", "2020-02-01"))
        self.assertTrue(bq.shows_period("three months ended Jan. 31, 2026", "2026-01-31"))
        self.assertFalse(bq.shows_period("Twelve Months Ended December 31, 2023", "2024-12-31"))


class NonGaapTests(unittest.TestCase):
    def test_non_gaap_rows_are_never_used(self):
        html = ("<table><caption>Amounts in USD</caption>"
                "<tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Non-GAAP net income</td><td>1,500</td></tr>"
                "<tr><td>Non-GAAP diluted earnings per share</td><td>0.30</td></tr>"
                "<tr><td>Net income</td><td>1,234</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)

    def test_adjusted_eps_and_adjusted_column_headers_are_excluded(self):
        html = ("<table><tr><th></th><th>GAAP Three Months Ended December 31, 2024</th>"
                "<th>Adjusted Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,234</td><td>1,500</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td><td>0.30</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)

    def test_non_gaap_table_caption_excludes_unqualified_rows(self):
        html = ("<table><caption>Non-GAAP financial results</caption>"
                "<tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,500</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.30</td></tr></table>")
        self.assertEqual(bq.table_eps(table_of(html), 1_234_000, END), (None, None))

    def test_mixed_gaap_reconciliation_table_selects_explicit_gaap_row(self):
        html = ("<table><caption>Reconciliation from GAAP to Non-GAAP</caption>"
                "<tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>GAAP diluted earnings per share</td><td>0.46</td></tr>"
                "<tr><td>Non-GAAP diluted earnings per share</td><td>0.80</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 396_100_000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.46)


class PerShareHeadingTests(unittest.TestCase):
    def test_basic_and_diluted_rows_under_a_per_share_heading_are_eps_rows(self):
        html = ("<table><caption>Amounts in USD</caption>"
                "<tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
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
        html = ("<table><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
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
        html = ("<table><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,234</td></tr>"
                "<tr><td>Net income per share:</td></tr>"
                "<tr><td>Revenue</td><td>5,000</td></tr>"
                "<tr><td>Gross profit</td><td>3,000</td></tr>"
                "<tr><td>Diluted</td><td>0.99</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)


class ZeroAndNetRowTests(unittest.TestCase):
    def test_zero_eps_remains_a_reported_candidate(self):
        html = ("<table><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,234</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>(0.00)</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.0)

    def test_total_eps_is_used_not_continuing_or_discontinued_operations(self):
        html = ("<table><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,234</td></tr>"
                "<tr><td>Earnings per share from continuing operations - diluted</td><td>0.30</td></tr>"
                "<tr><td>Earnings per share from discontinued operations - diluted</td><td>0.04</td></tr>"
                "<tr><td>Earnings per share - diluted</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)

    def test_a_later_net_income_row_can_match_the_stored_value(self):
        html = ("<table><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,500</td></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,234</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["netLabel"], "net income attributable to acme")


class StructuralPeriodHeaderTests(unittest.TestCase):
    def test_period_header_accepts_actual_fiscal_end_within_five_days(self):
        html = "Three Months Ended January 4, 2025"
        self.assertTrue(bq._has_target_date(html, "2024-12-31"))

    def test_sec_period_header_accepts_adjacent_year_after_comma(self):
        self.assertTrue(bq._has_target_date("Three Months Ended January 31,2026", "2026-01-31"))

    def test_period_header_accepts_six_day_week_53_period_drift(self):
        html = "Three Months Ended January 6, 2025"
        self.assertTrue(bq._has_target_date(html, "2024-12-31"))

    def test_period_header_rejects_date_more_than_seven_days_from_fiscal_end(self):
        html = "Three Months Ended January 8, 2025"
        self.assertFalse(bq._has_target_date(html, "2024-12-31"))

    def test_multirow_colspan_and_rowspan_separate_q4_from_full_year(self):
        html = ("<table><caption>Fourth quarter and fiscal year results in USD for December 31, 2024</caption>"
                "<tr><td colspan='5'>(in millions, except per share data; shares in thousands)</td></tr>"
                "<tr><th rowspan='2'></th><th colspan='2'>Three Months Ended</th>"
                "<th colspan='2'>Year Ended</th></tr>"
                "<tr><th colspan='2'>December 31, 2024</th><th colspan='2'>December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>$</td><td>1.234</td><td>$</td><td>10.000</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>$</td><td>0.25<sup>1</sup></td>"
                "<td>$</td><td>1.25</td></tr>"
                "<tr><td>Basic earnings per share</td><td>$</td><td>0.26</td><td>$</td><td>1.27</td></tr>"
                "<tr><td>Diluted weighted average shares outstanding</td><td></td><td>4,936</td>"
                "<td></td><td>40,000</td></tr>"
                "<tr><td colspan='5'>(1) Includes amounts attributable to the reported period.</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END, expected_currency="USD")
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["epsBasic"], 0.26)
        self.assertEqual(result["dilutedShares"], 4_936_000)
        self.assertEqual(result["scale"], 1_000_000)
        self.assertTrue(result["diagnostics"]["shareFormulaWithinTolerance"])
        self.assertEqual(result["evidence"]["targetColumns"], [1, 2])
        self.assertIn("fiscal year", result["evidence"]["tableCaption"])
        self.assertEqual(result["evidence"]["epsCell"]["footnoteMarkers"], ["1"])
        self.assertEqual(result["evidence"]["tableUnits"],
                         ["(in millions, except per share data; shares in thousands)"])
        self.assertEqual(result["evidence"]["footnotes"]["rows"][0]["row"], 7)

    def test_period_column_without_a_unique_gaap_diluted_row_is_not_written(self):
        html = ("<table><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,234</td></tr>"
                "<tr><td>Adjusted diluted EPS</td><td>0.40</td></tr>"
                "<tr><td>Non-GAAP diluted earnings per share</td><td>0.41</td></tr></table>")
        self.assertEqual(bq.table_eps(table_of(html), 1_234_000, END), (None, None))

    def test_multiple_total_diluted_rows_are_ambiguous(self):
        html = ("<table><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income</td><td>1,234</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr>"
                "<tr><td>Diluted EPS</td><td>0.26</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(result)
        self.assertIn("multiple GAAP diluted EPS rows", reason)
        self.assertIn("row 2", reason)
        self.assertIn("row 3", reason)

    def test_contradictory_money_unit_scales_keep_the_candidate_pending(self):
        html = ("<table><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td colspan='2'>(in thousands; amounts in millions)</td></tr>"
                "<tr><td>Net income</td><td>1,234</td></tr>"
                "<tr><td>Diluted earnings per share</td><td>0.25</td></tr></table>")
        result, reason = bq.table_eps(table_of(html), 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertIn("table-money-unit-is-ambiguous", result["reviewFlags"])
        self.assertTrue(result["manualReviewRequired"])


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
        self.assertEqual(result["filingDate"], "2025-02-05")
        self.assertEqual(result["source"], "SEC EDGAR 8-K exhibit")
        self.assertEqual(result["accession"], filing["accessionNumber"])
        self.assertRegex(result["sourceTextSha256"], r"^[0-9a-f]{64}$")
        evidence = result["evidence"]
        self.assertEqual(evidence["tableIndex"], 0)
        self.assertEqual(evidence["reportPeriod"], {"end": END, "kind": "Q4"})
        self.assertEqual(evidence["classification"]["gaap"], True)
        self.assertEqual(evidence["classification"]["diluted"], True)
        self.assertEqual(evidence["classification"]["operations"], "total")
        self.assertIn("row", evidence["epsCell"])
        self.assertIn("column", evidence["epsCell"])
        self.assertIn("row", evidence["netIncomeRow"])
        self.assertIn("column", evidence["netIncomeRow"])

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
    @staticmethod
    def complete_split_document(events):
        return {
            "symbol": "AAA", "checkedAt": "2026-06-01",
            "annual": {"end": [END]}, "quarterly": {"end": [END]},
            "split": {
                "events": events, "eventsComplete": True,
                "eventsSource": "Yahoo Finance chart range=max split events",
                "eventsCheckedAt": "2026-06-01", "eventsSymbol": "AAA",
                "eventsCoverageStart": "1990-01-01", "eventsCoverageEnd": "2026-06-01",
                "eventsParserVersion": 3,
            },
        }

    def test_split_factor_counts_only_splits_after_the_filing(self):
        doc = self.complete_split_document(
            [["2000-06-19", 3], ["2021-07-20", 4], ["2024-06-10", 10]])
        self.assertEqual(bq.split_factor(doc, "2020-02-05", END), 40.0)
        self.assertEqual(bq.split_factor(doc, "2024-08-01", END), 1.0)
        self.assertIsNone(bq.split_factor({}, "2020-02-05", END))

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
<tr><th></th><th colspan="2">Three Months Ended December 31, 2025</th>
    <th colspan="2">Three Months Ended December 31, 2024</th></tr>
<tr><td>Net income attributable to Acme</td><td>$</td><td>999</td><td>$</td><td>1,234</td></tr>
<tr><td>Diluted earnings per share</td><td>$</td><td>0.30</td><td>$</td><td>0.25</td></tr>
</table></body></html>
"""


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
        doc = SplitAndToleranceTests.complete_split_document([])
        check = bq.verify_quarter(self.sec_with(NEXT_YEAR_RELEASE), 1234, self.rows, END, 1_234_000, 0.25, doc)
        self.assertEqual(check["status"], "confirmed")
        self.assertEqual(check["epsDiluted"], 0.25)

    def test_a_different_prior_year_eps_is_a_conflict(self):
        doc = SplitAndToleranceTests.complete_split_document([])
        check = bq.verify_quarter(self.sec_with(NEXT_YEAR_RELEASE), 1234, self.rows, END, 1_234_000, 0.20, doc)
        self.assertEqual(check["status"], "conflict")

    def test_a_restated_prior_year_net_income_is_kept_as_a_diagnostic(self):
        doc = SplitAndToleranceTests.complete_split_document([])
        check = bq.verify_quarter(self.sec_with(NEXT_YEAR_RELEASE), 1234, self.rows, END, 1_200_000, 0.25, doc)
        self.assertEqual(check["status"], "confirmed")
        self.assertFalse(check["diagnostics"]["netIncomeMatchesStored"])
        self.assertIn("net-income-does-not-match-stored-quarter-value", check["reviewFlags"])

    def test_no_next_year_release_is_unavailable(self):
        check = bq.verify_quarter(self.sec_with(NEXT_YEAR_RELEASE), 1234, [], END, 1_234_000, 0.25, {})
        self.assertEqual(check["status"], "unavailable")
        self.assertIn("Item 2.02", check["reason"])


class FillGradeTests(unittest.TestCase):
    def test_metadata_only_source_audit_cannot_promote_grade_a(self):
        doc = {"quarterly": {"end": ["2024-12-31", "2023-12-31"], "fq": [4, 4],
                             "values": {"netIncome": [1234000, 5], "epsDiluted": [None, None]}}}
        accession_a, accession_b = "0000000001-25-000101", "0000000001-24-000102"
        url_a = audited_source("2024-12-31", "epsDiluted", 0.25, accession_a)["fields"]["epsDiluted"]["url"]
        url_b = audited_source("2023-12-31", "epsDiluted", 0.10, accession_b)["fields"]["epsDiluted"]["url"]
        found = {
            "2024-12-31": {"epsDiluted": 0.25, "epsBasic": None, "dilutedShares": None,
                           "accession": accession_a, "url": url_a, "filingDate": "2025-02-05", "uniqueCandidate": True,
                           "candidateValidated": True, "sourceAudit": audited_source("2024-12-31", "epsDiluted", 0.25, accession_a),
                           "verified": {"status": "conflict"}},
            "2023-12-31": {"epsDiluted": 0.10, "epsBasic": None, "dilutedShares": None,
                           "accession": accession_b, "url": url_b, "filingDate": "2024-02-05", "uniqueCandidate": True,
                           "candidateValidated": True, "sourceAudit": audited_source("2023-12-31", "epsDiluted", 0.10, accession_b),
                           "verified": {"status": "unavailable"}},
        }
        self.assertEqual(bq.apply_fill(doc, found), 0)
        self.assertEqual(doc["quarterly"]["values"]["epsDiluted"], [None, None])
        self.assertNotIn("q4EpsSources", doc)

    def test_manual_review_candidate_stays_pending_and_does_not_write(self):
        doc = {"quarterly": {"end": ["2024-12-31"], "values": {"epsDiluted": [None]}}}
        found = {"2024-12-31": {"epsDiluted": 0.25, "accession": "a", "url": "u",
                                "filingDate": "2025-02-05", "uniqueCandidate": True,
                                "candidateValidated": False, "manualReviewRequired": True,
                                "reviewFlags": ["net-income-divided-by-shares-differs-from-diluted-eps"]}}
        bq.apply_fill(doc, found)
        self.assertEqual(doc["quarterly"]["values"]["epsDiluted"], [None])
        self.assertNotIn("q4EpsSources", doc)

    def test_audited_manual_candidate_can_be_promoted(self):
        doc = {"quarterly": {"end": ["2024-12-31"], "values": {"epsDiluted": [None]}}}
        accession = "0000000001-25-000103"
        source = audited_source("2024-12-31", "epsDiluted", 0.25, accession)
        found = {"2024-12-31": {"epsDiluted": 0.25, "accession": accession,
                                "url": source["fields"]["epsDiluted"]["url"],
                                "filingDate": "2025-02-05", "uniqueCandidate": True,
                                "candidateValidated": False, "manualReviewRequired": True,
                                "auditApproved": True, "sourceAudit": source}}
        self.assertEqual(bq.apply_fill(doc, found), 0)
        self.assertEqual(doc["quarterly"]["values"]["epsDiluted"], [None])
        self.assertNotIn("q4EpsSources", doc)

    def test_an_existing_eps_keeps_its_own_grade(self):
        doc = {"quarterly": {"end": ["2024-12-31"], "fq": [4], "values": {"netIncome": [1234000],
                                                                     "epsDiluted": [None], "epsBasic": [0.26]},
                             "epsQuality": [{"epsBasic": "A"}]}}
        accession = "0000000001-25-000104"
        source = audited_source("2024-12-31", "epsDiluted", 0.25, accession)
        found = {"2024-12-31": {"epsDiluted": 0.25, "epsBasic": 0.27, "dilutedShares": None,
                                "accession": accession, "url": source["fields"]["epsDiluted"]["url"],
                                "filingDate": "2025-02-05", "uniqueCandidate": True,
                                "candidateValidated": True, "sourceAudit": source,
                                "verified": {"status": "confirmed"}}}
        self.assertEqual(bq.apply_fill(doc, found), 0)
        entry = doc["quarterly"]["epsQuality"][0]
        self.assertEqual(entry["epsBasic"], "A")
        self.assertNotIn("epsDiluted", entry)
        self.assertEqual(doc["quarterly"]["values"]["epsBasic"], [0.26])
        self.assertEqual(doc["quarterly"]["values"]["epsDiluted"], [None])


class FillTests(unittest.TestCase):
    def test_fill_writes_only_null_fields_and_records_the_source(self):
        doc = {"quarterly": {"end": ["2024-03-31", "2024-12-31"], "fq": [1, 4],
                             "values": {"netIncome": [5, 1234000], "epsDiluted": [0.5, None],
                                        "epsBasic": [0.6, 0.3]}}}
        accession = "0000000001-25-000105"
        source = audited_source("2024-12-31", "epsDiluted", 0.25, accession)
        found = {"2024-12-31": {"epsDiluted": 0.25, "epsBasic": 0.26, "dilutedShares": 4936000,
                                "accession": accession, "url": source["fields"]["epsDiluted"]["url"],
                                "filingDate": "2025-02-05", "uniqueCandidate": True,
                                "candidateValidated": True, "sourceAudit": source}}
        self.assertEqual(bq.apply_fill(doc, found), 0)
        values = doc["quarterly"]["values"]
        self.assertEqual(values["epsDiluted"], [0.5, None])
        self.assertEqual(values["epsBasic"], [0.6, 0.3])              # existing value kept
        self.assertEqual(values.get("dilutedShares"), None)
        self.assertNotIn("q4EpsSources", doc)
        self.assertNotIn("q4EpsSources", doc)

    def test_filling_diluted_keeps_existing_basic_source_record(self):
        doc = {"quarterly": {"end": [END], "fq": [4],
                              "values": {"netIncome": [1234000], "epsDiluted": [None], "epsBasic": [0.26]}},
               "q4EpsSources": {END: {"epsBasic": {"accession": "old-basic", "url": "old-url",
                                                    "grade": "A", "reportedValue": 0.26}}}}
        accession = "0000000001-25-000106"
        source = audited_source(END, "epsDiluted", 0.25, accession)
        found = {END: {"epsDiluted": 0.25, "epsBasic": 0.27,
                       "accession": accession, "url": source["fields"]["epsDiluted"]["url"],
                       "filingDate": "2025-02-05", "uniqueCandidate": True,
                       "candidateValidated": True, "sourceAudit": source}}

        self.assertEqual(bq.apply_fill(doc, found), 0)
        recorded = doc["q4EpsSources"][END]
        self.assertEqual(recorded["epsBasic"]["accession"], "old-basic")
        self.assertNotIn("epsDiluted", recorded)


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


class LegacyFillModeTests(unittest.TestCase):
    def test_legacy_fill_mode_stops_before_network_or_writes(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = bq.main(["--mode", "fill"])
        self.assertEqual(code, 2)
        self.assertIn("lacks a cache-bound raw-document audit", output.getvalue())


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

    def test_legacy_fill_mode_does_not_write_without_complete_source_audit(self):
        self.write_test_file(None)
        target = os.path.join(self.fin_dir, "TEST.json")
        with open(target, "rb") as file:
            before = file.read()
        report = bq.run(self.args("fill"), self.FakeSec())
        self.assertEqual(report["filled"], 0)
        with open(target, "rb") as file:
            self.assertEqual(file.read(), before)
        self.assertFalse(os.path.exists(self.state_path))

    def test_fill_never_overwrites_a_stored_q4_eps(self):
        self.write_test_file(0.99)
        report = bq.run(self.args("fill"), self.FakeSec())
        self.assertEqual(report["filled"], 0)
        with open(os.path.join(self.fin_dir, "TEST.json"), encoding="utf-8") as file:
            self.assertEqual(json.load(file)["quarterly"]["values"]["epsDiluted"], [0.1, 0.99])

    def test_fill_leaves_a_formula_mismatch_pending_without_writing_financial_data(self):
        self.write_test_file(None)
        target = os.path.join(self.fin_dir, "TEST.json")
        with open(target, "rb") as file:
            before = file.read()

        class MismatchSec(self.FakeSec):
            def get_text(self, url):
                self.requests += 1
                return RELEASE.replace("0.25<sup>1</sup>", "0.40")

        report = bq.run(self.args("fill"), MismatchSec())
        self.assertEqual(report["filled"], 0)
        result = report["rows"][0]["result"]
        self.assertTrue(result["uniqueCandidate"])
        self.assertTrue(result["manualReviewRequired"])
        self.assertFalse(result["candidateValidated"])
        with open(target, "rb") as file:
            self.assertEqual(file.read(), before)
        self.assertFalse(os.path.exists(self.state_path))

    def test_fill_does_not_mark_an_ambiguous_source_as_done(self):
        self.write_test_file(None)

        class AmbiguousSec(self.FakeSec):
            def get_text(self, url):
                self.requests += 1
                if url.endswith("ex99-1.htm"):
                    return ("<table><tr><th></th><th>Three Months Ended December 31, 2024</th>"
                            "<th>Three Months Ended December 31, 2024</th></tr>"
                            "<tr><td>Net income</td><td>1,234</td><td>1,234</td></tr>"
                            "<tr><td>Diluted earnings per share</td><td>0.25</td><td>0.25</td></tr></table>")
                return RELEASE

        report = bq.run(self.args("fill"), AmbiguousSec())
        self.assertEqual(report["filled"], 0)
        self.assertIsNone(report["rows"][0]["result"])
        self.assertIn("multiple distinct column groups", report["rows"][0]["reason"])
        self.assertFalse(os.path.exists(self.state_path))

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

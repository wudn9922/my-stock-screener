"""S2b rules on the real 8-K tables of tests/fixtures/q4_8k (decision §4): the L8 heading rules here, the L1 look-back below.

The target net income and the full-year net income of each fixture are inlined from docs/report/financials; the tests
never read that folder.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import backfill_q4_eps_8k as bq  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "q4_8k")

# (file, end, stored Q4 net income, full-year net income of the same fiscal year)
URBN_2012 = ("URBN-2012-01-31-L8.html", "2012-01-31", 39_255_000, 185_251_000)
UNFI_2013 = ("UNFI-2013-08-03-L8.html", "2013-08-03", 32_077_000, 107_854_000)
TXT_2012 = ("TXT-2012-12-29-L8.html", "2012-12-29", 148_000_000, 589_000_000)
TXT_2016 = ("TXT-2016-12-31-L2-negative.html", "2016-12-31", 214_000_000, 962_000_000)
TXT_2020 = ("TXT-2020-01-04-L8-non-gaap-diluted-row-negative.html", "2020-01-04", 199_000_000, 815_000_000)

END = "2024-12-31"


def fixture_tables(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as source:
        return bq.tables_of(source.read())


def period_table(*rows):
    """A synthetic table: a 'Three Months Ended December 31, 2024' header, then the given rows (cell HTML)."""
    body = "".join(f"<tr>{row}</tr>" for row in rows)
    return bq.tables_of("<table><tr><td>Three Months Ended December 31, 2024</td><td>x</td></tr>"
                        f"{body}</table>")[0]


class L8FixtureTests(unittest.TestCase):
    """S2b L8 (decision §1 items 1 and 3, §4): the positives and the negatives named in the decision."""

    @staticmethod
    def check(case, **options):
        name, end, target, _ = case
        tables = fixture_tables(name)
        assert len(tables) == 1, name
        return bq.table_eps(tables[0], target, end, **options)

    def test_urbn_2012_per_common_share_heading_gives_eps_0_27(self):
        result, reason = self.check(URBN_2012)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.27)
        self.assertEqual(result["epsBasic"], 0.27)
        self.assertEqual(result["net"], 39_255_000)
        self.assertEqual(result["netLabel"], "net income")

    def test_unfi_2013_diluted_heading_gives_eps_0_65(self):
        result, reason = self.check(UNFI_2013)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.65)
        self.assertEqual(result["net"], 32_077_000)
        self.assertEqual(result["dilutedShares"], 49_646_000)

    def test_txt_2020_and_txt_2016_stay_rejected(self):
        self.assertEqual(self.check(TXT_2020), (None, None))
        self.assertEqual(self.check(TXT_2016), (None, None))

    def test_txt_2012_is_not_an_l8_positive(self):
        self.assertEqual(self.check(TXT_2012), (None, None))

    def test_the_full_year_net_income_rejects_a_renamed_row(self):
        name, end, target, annual = URBN_2012
        table = fixture_tables(name)[0]
        self.assertEqual(bq.table_eps(table, target, end, annual=target),
                         (None, "net income equals the full-year net income"))
        self.assertIsNone(bq.table_eps(table, target, end, annual=annual)[1])

    def test_the_s1_matcher_does_not_read_the_renamed_rows(self):
        for name, end, target, _ in (URBN_2012, UNFI_2013):
            table = fixture_tables(name)[0]
            self.assertEqual(bq.table_eps(table, target, end, s2=False), (None, None), name)


class L8SyntheticTests(unittest.TestCase):
    """S2b L8 synthetic cases: each one is a heading or row shape the decision keeps out of the rule."""

    def test_a_basic_heading_gives_no_diluted_row(self):
        table = period_table("<td>Net income</td><td>1,234</td>",
                             "<td>Basic per share data:</td>",
                             "<td>Net income</td><td>0.26</td>",
                             "<td>Weighted average basic shares</td><td>4,700</td>")
        self.assertEqual(bq.table_eps(table, 1_234_000, END), (None, None))

    def test_a_basic_and_diluted_heading_is_not_renamed(self):
        table = period_table("<td>Net income</td><td>1,234</td>",
                             "<td>Basic and diluted net income per share:</td>",
                             "<td>Net income</td><td>0.25</td>",
                             "<td>Weighted average diluted shares</td><td>4,936</td>")
        self.assertEqual(bq.table_eps(table, 1_234_000, END), (None, None))

    def test_a_diluted_heading_does_not_rename_continuing_operations(self):
        table = period_table("<td>Net income</td><td>1,234</td>",
                             "<td>Diluted per share data:</td>",
                             "<td>Income from continuing operations</td><td>0.30</td>",
                             "<td>Weighted average diluted shares</td><td>4,936</td>")
        self.assertEqual(bq.table_eps(table, 1_234_000, END), (None, None))

    def test_a_per_diluted_share_line_is_not_a_heading(self):
        table = period_table("<td>Net income</td><td>1,234</td>",
                             "<td>Net income per diluted share:</td>",
                             "<td>Net income</td><td>0.25</td>")
        self.assertEqual(bq.table_eps(table, 1_234_000, END), (None, None))

    def test_a_diluted_heading_renames_its_net_income_row(self):
        table = period_table("<td>Net income</td><td>1,234</td>",
                             "<td>Diluted per share data:</td>",
                             "<td>Net income</td><td>0.25</td>",
                             "<td>Weighted average diluted shares</td><td>4,936</td>")
        result, reason = bq.table_eps(table, 1_234_000, END)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["dilutedShares"], 4_936_000)

    def test_a_diluted_heading_renames_one_row_only(self):
        table = period_table("<td>Net income</td><td>1,234</td>",
                             "<td>Diluted per share data:</td>",
                             "<td>Net income</td><td>0.25</td>",
                             "<td>Net income</td><td>0.26</td>")
        labels = [label for label, _ in bq._labelled_rows(table)]
        self.assertEqual(labels.count("diluted earnings per share"), 1)
        self.assertEqual(labels.count("net income"), 2)

    def test_a_renamed_row_with_a_value_of_a_thousand_or_more_is_not_renamed(self):
        table = period_table("<td>Net income</td><td>1,234</td>",
                             "<td>Diluted per share data:</td>",
                             "<td>Net income</td><td>1,500</td>")
        self.assertEqual(bq.table_eps(table, 1_234_000, END), (None, None))


if __name__ == "__main__":
    unittest.main()

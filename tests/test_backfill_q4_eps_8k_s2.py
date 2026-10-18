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


# S2b L1 (decision §1, §2, §4). (file, end, stored Q4 net income, full-year net income of the same fiscal year)
VNCE_2024 = ("VNCE-2024-02-03-L1.html", "2024-02-03", -4_668_000, 25_446_000)
URBN_2016 = ("URBN-2016-01-31-L1.html", "2016-01-31", 72_878_000, 224_489_000)
VNCE_2026 = ("VNCE-2026-01-31-L1-year-header-negative.html", "2026-01-31", -3_605_000, 6_378_000)
WLY_2023 = ("WLY-2023-04-30-L1.html", "2023-04-30", 68_344_000, 17_233_000)
URBN_2014 = ("URBN-2014-01-31-L2.html", "2014-01-31", 88_682_000, 282_360_000)
TRAK_2019 = ("TRAK-2019-06-30-L2.html", "2019-06-30", 182_303, 3_902_406)
TXT_2022 = ("TXT-2022-01-01-L2.html", "2022-01-01", 207_000_000, 746_000_000)
UI_2014 = ("UI-2014-06-30-L3.html", "2014-06-30", 49_418_000, 176_937_000)
UNFI_2019 = ("UNFI-2019-08-03-L3.html", "2019-08-03", 19_197_000, -285_000_000)
VIAV_2021 = ("VIAV-2021-07-03-L3.html", "2021-07-03", 3_300_000, 67_500_000)

FILING = {"accessionNumber": "0001-25-000030", "filingDate": "2025-02-05"}


def table_html(*rows):
    return "<table>" + "".join(f"<tr>{row}</tr>" for row in rows) + "</table>"


class ExhibitSec:
    """A SEC client that serves one release exhibit (ex99-1.htm) for FILING and no index page."""

    def __init__(self, html):
        self.html = html

    def get_json(self, url):
        return {"directory": {"item": [{"name": "ex99-1.htm", "type": "EX-99.1"}]}}

    def get_text(self, url):
        return self.html if url.endswith("ex99-1.htm") else None


class LookBackFixtureTests(unittest.TestCase):
    """The real fixture files, read through from_filing so that each table gets its own preceding tables."""

    @staticmethod
    def read(case):
        name, end, target, annual = case
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as source:
            html = source.read()
        return bq.from_filing(ExhibitSec(html), 1234, FILING, target, end, annual=annual)

    def test_vnce_2024_look_back_gives_eps_minus_0_37(self):
        result, reason = self.read(VNCE_2024)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], -0.37)
        self.assertEqual(result["net"], -4_668_000)

    def test_urbn_2016_look_back_gives_eps_0_61(self):
        result, reason = self.read(URBN_2016)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.61)
        self.assertEqual(result["net"], 72_878_000)

    def test_vnce_2026_year_header_stays_rejected(self):
        result, _ = self.read(VNCE_2026)
        self.assertIsNone(result)

    def test_wly_2023_net_income_table_has_no_eps_row(self):
        result, _ = self.read(WLY_2023)
        self.assertIsNone(result)

    def test_out_of_scope_fixtures_give_no_result(self):
        for case in (URBN_2014, TRAK_2019, TXT_2022, UI_2014, UNFI_2019, VIAV_2021):
            with self.subTest(fixture=case[0]):
                result, _ = self.read(case)
                self.assertIsNone(result)


class LookBackSyntheticTests(unittest.TestCase):
    """The look-back rules on synthetic tables: a header that names the quarter, a full year, misaligned, none."""

    NET = ("<td>Net income</td><td>1,234</td><td>4,000</td>",
           "<td>Diluted earnings per share</td><td>0.25</td><td>1.00</td>")
    HEADER = ("<td>Three Months Ended December 31,</td><td>Twelve Months Ended December 31,</td>",
              "<td>2024</td><td>2024</td>")

    def net(self):
        return bq.tables_of(table_html(*self.NET))[0]

    def header(self, *rows):
        return bq.tables_of(table_html(*(rows or self.HEADER)))[0]

    def test_the_quarter_column_is_read_through_the_header(self):
        result, reason = bq.table_eps(self.net(), 1_234_000, END, preceding=(self.header(),))
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)
        self.assertEqual(result["net"], 1_234_000)

    def test_a_twelve_month_column_is_rejected(self):
        result, reason = bq.table_eps(self.net(), 4_000_000, END, preceding=(self.header(),))
        self.assertIsNone(result)
        self.assertEqual(reason, "matched column header is a full year")

    def test_a_header_with_a_different_column_count_is_not_aligned(self):
        header = self.header(("<td>Three Months Ended December 31,</td>"), ("<td>2024</td><td>2024</td><td>2023</td>"))
        self.assertEqual(bq.table_eps(self.net(), 1_234_000, END, preceding=(header,)),
                         (None, "period header not aligned"))

    def test_a_rowspan_header_is_not_aligned(self):
        header = self.header('<td rowspan="2">Three Months Ended December 31,</td><td>Twelve Months Ended</td>',
                             "<td>2024</td>")
        self.assertEqual(bq.table_eps(self.net(), 1_234_000, END, preceding=(header,)),
                         (None, "period header not aligned"))

    def test_a_full_year_header_before_the_table_is_rejected(self):
        header = self.header("<td>For the Fiscal Year December 31, 2024</td>")
        self.assertEqual(bq.table_eps(self.net(), 1_234_000, END, preceding=(header,)),
                         (None, "period header is a full year, not a quarter"))

    def test_the_full_year_net_income_rejects_the_look_back(self):
        self.assertEqual(bq.table_eps(self.net(), 1_234_000, END, annual=1_234_000, preceding=(self.header(),)),
                         (None, "net income equals the full-year net income"))

    def test_no_preceding_table_that_names_the_quarter_gives_no_result(self):
        self.assertEqual(bq.table_eps(self.net(), 1_234_000, END), (None, None))
        other = self.header("<td>Three Months Ended March 31, 2024</td>")
        self.assertEqual(bq.table_eps(self.net(), 1_234_000, END, preceding=(other,)), (None, None))

    def test_the_s1_matcher_does_not_read_through_a_header(self):
        self.assertEqual(bq.table_eps(self.net(), 1_234_000, END, preceding=(self.header(),), s2=False), (None, None))


class LookBackFilingTests(unittest.TestCase):
    """from_filing gives each table the up to 3 non-empty tables before it in the same document (S2b L1)."""

    HEADER = ("<table><tr><td>Three Months Ended December 31,</td><td>Twelve Months Ended December 31,</td></tr>"
              "<tr><td>2024</td><td>2024</td></tr></table>")
    FILLER = "<table><tr><td>Revenue</td><td>5</td><td>6</td></tr></table>"
    NET = ("<table><tr><td>Net income</td><td>1,234</td><td>4,000</td></tr>"
           "<tr><td>Diluted earnings per share</td><td>0.25</td><td>1.00</td></tr></table>")

    @staticmethod
    def read(html, annual=None):
        return bq.from_filing(ExhibitSec(html), 1234, FILING, 1_234_000, END, annual=annual)

    def test_the_third_preceding_table_is_read(self):
        result, reason = self.read(self.HEADER + self.FILLER + self.FILLER + self.NET)
        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], 0.25)

    def test_a_fourth_preceding_table_is_not_read(self):
        result, reason = self.read(self.HEADER + self.FILLER + self.FILLER + self.FILLER + self.NET)
        self.assertIsNone(result)
        self.assertEqual(reason, bq.GENERIC_REASON)

    def test_the_full_year_check_applies_to_a_look_back_row(self):
        result, reason = self.read(self.HEADER + self.NET, annual=1_234_000)
        self.assertIsNone(result)
        self.assertEqual(reason, "net income equals the full-year net income")


class AnnualNetIncomeTests(unittest.TestCase):
    DOC = {"quarterly": {"end": ["2024-02-03", "2024-06-01", "2025-02-01"], "fy": [2024, 2024, 2025]},
           "annual": {"fy": [2023, 2024], "values": {"netIncome": [10, 20]}}}

    def test_quarter_index_finds_the_period_end(self):
        self.assertEqual(bq.quarter_index(self.DOC, "2024-06-01"), 1)
        self.assertIsNone(bq.quarter_index(self.DOC, "2030-01-31"))

    def test_the_full_year_net_income_of_the_quarters_fiscal_year(self):
        self.assertEqual(bq.annual_net_income(self.DOC, 0), 20)
        self.assertIsNone(bq.annual_net_income(self.DOC, 2))         # fiscal year 2025 is not in the annual block
        self.assertIsNone(bq.annual_net_income(self.DOC, None))
        self.assertIsNone(bq.annual_net_income({}, 0))


if __name__ == "__main__":
    unittest.main()

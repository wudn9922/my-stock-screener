"""Synthetic checks for Inline XBRL Q4 EPS spot-check parsing."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atlas/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sec_inline_xbrl_q4 as parser  # noqa: E402
import spot_check_q4_eps_filings as spot  # noqa: E402


class InlineXbrlQ4Tests(unittest.TestCase):
    def test_8k_selection_requires_item_2_02_and_selects_first_post_year_end_release(self):
        submissions = {"filings": {"recent": {
            "form": ["8-K", "8-K", "8-K/A"],
            "filingDate": ["2026-01-21", "2026-02-01", "2026-01-22"],
            "accessionNumber": ["first", "later", "amendment"],
            "items": ["2.02,9.01", "2.02,9.01", "2.02,9.01"],
        }}}
        chosen = spot._choose_q4_earnings_8k(submissions, "2025-12-31")
        self.assertEqual(chosen["accessionNumber"], "first")

    def test_8k_selection_ignores_other_items_and_out_of_window_filings(self):
        submissions = {"filings": {"recent": {
            "form": ["8-K", "8-K"], "filingDate": ["2025-12-30", "2026-05-15"],
            "accessionNumber": ["pre-year-end", "too-late"], "items": ["2.02", "2.02"],
        }}}
        self.assertIsNone(spot._choose_q4_earnings_8k(submissions, "2025-12-31"))

    def test_archive_index_finds_ex_99_1(self):
        index = {"directory": {"item": [
            {"name": "primary.htm", "type": "8-K"},
            {"name": "ex99-1.htm", "type": "EX-99.1"},
        ]}}
        self.assertEqual(spot._ex99_document(index), "ex99-1.htm")

    def test_parses_duration_context_dimensions_sign_and_usd_per_share_unit(self):
        body = """
          <xbrli:unit id="U1"><xbrli:divide>
            <xbrli:unitnumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitnumerator>
            <xbrli:unitdenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitdenominator>
          </xbrli:divide></xbrli:unit>
          <xbrli:context id="Q4"><xbrli:period>
            <xbrli:startDate>2025-10-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate>
          </xbrli:period><xbrli:segment>
            <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:ClassBCommonStockMember</xbrldi:explicitMember>
          </xbrli:segment></xbrli:context>
          <ix:nonfraction name="us-gaap:EarningsPerShareDiluted" contextref="Q4" unitref="U1" sign="-" scale="0">0.24</ix:nonfraction>
        """
        result = parser.parse_inline_xbrl(body)
        fact = result["facts"][0]
        self.assertEqual(fact["name"], "EarningsPerShareDiluted")
        self.assertEqual(str(fact["value"]), "-0.24")
        self.assertEqual(fact["unit"], "USD/shares")
        self.assertEqual(fact["context"]["start"], "2025-10-01")
        self.assertEqual(fact["context"]["dimensions"][0]["member"], "us-gaap:ClassBCommonStockMember")


if __name__ == "__main__":
    unittest.main()

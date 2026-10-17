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
        body = """<html xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
          xmlns:us-gaap="http://fasb.org/us-gaap/2025">
          <xbrli:unit id="U1"><xbrli:divide>
            <xbrli:unitnumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitnumerator>
            <xbrli:unitdenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitdenominator>
          </xbrli:divide></xbrli:unit>
          <xbrli:context id="Q4"><xbrli:entity><xbrli:identifier scheme="https://www.sec.gov/">0000320193</xbrli:identifier></xbrli:entity><xbrli:period>
            <xbrli:startDate>2025-10-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate>
          </xbrli:period><xbrli:segment>
            <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:ClassBCommonStockMember</xbrldi:explicitMember>
          </xbrli:segment></xbrli:context>
          <ix:nonfraction name="us-gaap:EarningsPerShareDiluted" contextref="Q4" unitref="U1" sign="-" scale="0">0.24</ix:nonfraction>
        </html>"""
        result = parser.parse_inline_xbrl(body)
        fact = result["facts"][0]
        self.assertEqual(fact["name"], "EarningsPerShareDiluted")
        self.assertEqual(str(fact["value"]), "-0.24")
        self.assertEqual(fact["unit"], "USD/shares")
        self.assertEqual(fact["namespaceUri"], "http://fasb.org/us-gaap/2025")
        self.assertEqual(fact["context"]["start"], "2025-10-01")
        self.assertEqual(fact["context"]["dimensions"][0]["member"], "us-gaap:ClassBCommonStockMember")
        self.assertEqual(fact["context"]["entityIdentifiers"],
                         [{"scheme": "https://www.sec.gov/", "identifier": "0000320193"}])

    def test_parses_typed_dimensions_and_segment_scenario_presence(self):
        body = """
          <xbrli:context id="Q4"><xbrli:period>
            <xbrli:startDate>2025-10-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate>
          </xbrli:period><xbrli:segment><xbrldi:typedMember dimension="custom:ShareClassAxis">
            <custom:ClassName>Class A</custom:ClassName></xbrldi:typedMember></xbrli:segment>
          <xbrli:scenario></xbrli:scenario></xbrli:context>
          <ix:nonfraction name="us-gaap:EarningsPerShareDiluted" contextref="Q4" unitref="U1">0.46</ix:nonfraction>
        """
        context = parser.parse_inline_xbrl(body)["contexts"]["Q4"]
        self.assertTrue(context["hasSegment"])
        self.assertTrue(context["hasScenario"])
        self.assertEqual(context["typedDimensions"][0]["dimension"], "custom:ShareClassAxis")
        self.assertIn("Class A", context["typedDimensions"][0]["value"])

    def test_custom_namespace_measures_do_not_become_usd_per_share(self):
        body = """<html xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:custom="https://example.com/taxonomy">
          <xbrli:unit id="U"><xbrli:divide>
            <xbrli:unitNumerator><xbrli:measure>custom:USD</xbrli:measure></xbrli:unitNumerator>
            <xbrli:unitDenominator><xbrli:measure>custom:shares</xbrli:measure></xbrli:unitDenominator>
          </xbrli:divide></xbrli:unit>
          <ix:nonfraction name="us-gaap:EarningsPerShareDiluted" unitref="U">0.46</ix:nonfraction>
          </html>"""
        fact = parser.parse_inline_xbrl(body)["facts"][0]
        self.assertNotEqual(fact["unit"], "USD/shares")
        self.assertIn("example.com", fact["unit"])

    def test_normalizes_single_currency_and_share_units(self):
        body = """<html xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
          <xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
          <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
          <ix:nonfraction name="us-gaap:NetIncomeLoss" unitref="USD">10</ix:nonfraction>
          <ix:nonfraction name="us-gaap:WeightedAverageNumberOfSharesOutstandingBasic"
            unitref="shares">100</ix:nonfraction></html>"""
        units = [fact["unit"] for fact in parser.parse_inline_xbrl(body)["facts"]]
        self.assertEqual(units, ["USD", "shares"])

    def test_inline_number_transform_is_applied_explicitly_and_unknown_forms_rejected(self):
        body = """
          <ix:nonfraction name="us-gaap:EarningsPerShareDiluted" contextref="Q4" unitref="U1"
            format="ixt:numcommadecimal">1.234,56</ix:nonfraction>
          <ix:nonfraction name="us-gaap:EarningsPerShareBasic" contextref="Q4" unitref="U1"
            format="custom:unknown">0.46</ix:nonfraction>
          <ix:nonfraction name="us-gaap:EarningsPerShareBasic" contextref="Q4" unitref="U1">0,46</ix:nonfraction>
        """
        facts = parser.parse_inline_xbrl(body)["facts"]
        self.assertEqual(str(facts[0]["value"]), "1234.56")
        self.assertIsNone(facts[1]["value"])
        self.assertIn("unsupported", facts[1]["valueError"])
        self.assertIsNone(facts[2]["value"])


if __name__ == "__main__":
    unittest.main()

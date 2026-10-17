"""Regression cases from a real SEC earnings exhibit, with source accession and document hash."""
import json
import gzip
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atlas" / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "sec_q4"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backfill_q4_eps_8k as parser  # noqa: E402


class MarvellFiscal2026Q4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads((FIXTURES / "mrvl-2026-q4-source.json").read_text(encoding="utf-8"))
        cls.period_end = cls.source["periodEnd"]
        cls.official_gaap_diluted = cls.source["officialPublishedValues"]["epsDilutedGAAP"]

    def parse_fixture(self, name):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        tables = parser.structured_tables_of(text)
        self.assertEqual(len(tables), 1)
        result, reason = parser.table_eps(tables[0], 396_100_000, self.period_end,
                                          expected_currency="USD")
        self.assertIsNone(reason)
        return text, result

    def test_original_statement_table_reports_gaap_diluted_046(self):
        _text, result = self.parse_fixture("mrvl-2026-q4-gaap-income-statement.html")
        self.assertEqual(result["epsDiluted"], self.official_gaap_diluted)

    def test_mixed_gaap_to_nongaap_table_does_not_select_080(self):
        text, result = self.parse_fixture("mrvl-2026-q4-gaap-nongaap-reconciliation.html")
        self.assertIn("0.80", text)  # official Non-GAAP line in the same SEC table
        self.assertEqual(result["epsDiluted"], 0.46)
        self.assertNotEqual(result["epsDiluted"], self.source["officialPublishedValues"]["epsDilutedNonGAAP"])

    def test_fixture_records_original_sec_identity_and_full_document_hash(self):
        self.assertEqual(self.source["cik"], "0001835632")
        self.assertEqual(self.source["accession"], "0001835632-26-000006")
        self.assertEqual(self.source["form"], "8-K")
        self.assertEqual(self.source["exhibit"], "EX-99.1")
        self.assertEqual(self.source["filingDate"], "2026-03-05")
        self.assertEqual(self.source["url"],
                         "https://www.sec.gov/Archives/edgar/data/1835632/"
                         "000183563226000006/q426_8kx1312026ex-991.htm")
        self.assertRegex(self.source["sourceDocumentSha256"], r"^[0-9a-f]{64}$")


class VeevaFiscal2026Q4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads((FIXTURES / "veev-2026-q4-source.json").read_text(encoding="utf-8"))
        compressed = (FIXTURES / "veev-2026-q4-source.html.gz").read_bytes()
        cls.document = gzip.decompress(compressed)

    def test_captured_sec_response_hash_and_filing_identity(self):
        import hashlib

        self.assertEqual(hashlib.sha256(self.document).hexdigest(), self.source["sourceDocumentSha256"])
        self.assertEqual(len(self.document), self.source["sourceResponseBytes"])
        self.assertEqual(self.source["cik"], "0001393052")
        self.assertEqual(self.source["accession"], "0001393052-26-000007")
        self.assertEqual(self.source["form"], "8-K")
        self.assertEqual(self.source["exhibit"], "EX-99.1")
        self.assertEqual(self.source["filingDate"], "2026-03-04")
        self.assertNotIn(b"<ix:", self.document)

    def test_original_sec_release_yields_gaap_diluted_eps_and_keeps_currency_gate(self):
        text = self.document.decode("utf-8", errors="replace")
        tables = parser.structured_tables_of(text)
        index = self.source["sourceTableIndex"]
        self.assertLess(index, len(tables))

        result, reason = parser.table_eps(
            tables[index],
            self.source["officialPublishedValues"]["q4NetIncomeUSD"],
            self.source["periodEnd"],
            table_index=index,
            expected_currency="USD",
        )

        self.assertIsNone(reason)
        self.assertEqual(result["epsDiluted"], self.source["officialPublishedValues"]["epsDilutedGAAP"])
        self.assertTrue(result["evidence"]["classification"]["gaap"])
        self.assertTrue(result["evidence"]["classification"]["diluted"])
        self.assertEqual(result["evidence"]["classification"]["period"], "Q4")
        self.assertEqual(result["evidence"]["reportPeriod"]["end"], self.source["periodEnd"])
        self.assertIn("eps-currency-is-not-verified-usd", result["reviewFlags"])
        self.assertFalse(result["candidateValidated"])


if __name__ == "__main__":
    unittest.main()

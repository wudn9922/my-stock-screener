"""Offline regression fixtures for the five missing-period stage-two 8-K values."""

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/sec_q4_eps_stage2_regression"
sys.path.insert(0, str(ROOT / "docs/report/sec-q4-eps-stage2"))
import stage2_poc as stage2  # noqa: E402


MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


class EightKRegressionTests(unittest.TestCase):
    def assert_case_verified(self, ticker, fy, expected):
        case = next(row for row in MANIFEST["cases"]
                    if row["symbol"] == ticker and row["fy"] == fy)
        body = (FIXTURES / case["fixture_file"]).read_bytes()
        self.assertEqual(hashlib.sha256(body).hexdigest(), case["source_document_sha256"])
        self.assertEqual(case["fixture_sha256"], case["source_document_sha256"])
        candidates = stage2.parse_source_document(
            body,
            {"q4_end": case["q4_end"], "q4_start_estimate": case["q4_start_estimate"]},
            {"form": "8-K", "accession": case["accession"], "filing_date": case["filing_date"]},
            case["source_url"],
            case["split_events"],
        )
        verified = [row for row in candidates if row.get("verified")]
        self.assertEqual(len(verified), 1, candidates)
        result = verified[0]
        self.assertAlmostEqual(result["value"], expected, places=5)
        self.assertEqual(result["classification"], "A_verified_direct_q4_eps")
        self.assertIn("diluted", result["row_text"].lower())
        self.assertEqual(result["context"]["end"], case["q4_end"])
        self.assertEqual(result["context"]["duration_basis"], "explicit three-month fiscal-Q4 column")
        self.assertEqual(result["filing"]["accession"], case["accession"])
        self.assertEqual(result["source_url"], case["source_url"])
        self.assertEqual(result["split_basis"]["value_basis"], "as-reported in the cited SEC filing")

    def test_astc_fy2014_q4_minus_004(self):
        self.assert_case_verified("ASTC", 2014, -0.04)

    def test_fgc_fy2016_q4_plus_027(self):
        self.assert_case_verified("FGC", 2016, 0.27)

    def test_fgc_fy2022_q4_minus_003(self):
        self.assert_case_verified("FGC", 2022, -0.03)

    def test_wrap_fy2022_q4_minus_009(self):
        self.assert_case_verified("WRAP", 2022, -0.09)

    def test_ulbi_fy2021_q4_minus_007(self):
        self.assert_case_verified("ULBI", 2021, -0.07)


if __name__ == "__main__":
    unittest.main()

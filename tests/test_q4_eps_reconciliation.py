"""Synthetic checks that Basic EPS validation uses Basic shares and split basis."""
import os
import json
import hashlib
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atlas/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import reconcile_q4_eps_sec as reconcile  # noqa: E402


def share_fact(concept, value):
    return {"unit": "shares", "value": value, "start": "2023-07-01", "end": "2023-09-30",
            "form": "10-Q", "filed": "2023-11-01", "accession": "0000001234-23-000010"}


class Q4EpsReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.document = {
            "symbol": "AAA",
            "quarterly": {
                "fy": [2023, 2023, 2023],
                "fq": [2, 3, 4],
                "end": ["2023-06-30", "2023-09-30", "2023-12-31"],
                "values": {"dilutedShares": [2200, 4000, None]},
            },
            "split": {"events": [["2024-01-02", 2]]},
        }
        self.raw = {"concepts": {
            "WeightedAverageNumberOfSharesOutstandingBasic": [share_fact(
                "WeightedAverageNumberOfSharesOutstandingBasic", 1000)],
            "WeightedAverageNumberOfDilutedSharesOutstanding": [share_fact(
                "WeightedAverageNumberOfDilutedSharesOutstanding", 2000)],
        }}
        self.candidate = {"filed": "2023-11-01", "accession": "0000001234-23-000010"}

    def test_basic_crosscheck_uses_basic_shares_adjusted_with_seed_suffix_factor(self):
        shares, source = reconcile._basic_q3_shares(
            self.document, 2023, self.raw, self.candidate)
        self.assertEqual(shares, 2000)
        self.assertEqual(source["concept"], "WeightedAverageNumberOfSharesOutstandingBasic")
        self.assertEqual(source["rawValue"], 1000)
        self.assertEqual(source["splitFactor"], 2)
        self.assertEqual(source["splitBasis"].split(";")[0], "current financials basis")

    def test_missing_basic_share_fact_is_not_replaced_with_diluted_shares(self):
        self.raw["concepts"].pop("WeightedAverageNumberOfSharesOutstandingBasic")
        shares, source = reconcile._basic_q3_shares(
            self.document, 2023, self.raw, self.candidate)
        self.assertIsNone(shares)
        self.assertEqual(source["failureReason"], "missing-or-conflicting-q3-basic-shares")

    def test_split_factor_trace_names_the_independent_corporate_action_source(self):
        source = reconcile._source(self.candidate, split_factor=2, document=self.document)
        trace = source["splitBasis"]["eventTrace"]
        self.assertEqual(trace["provider"], "Yahoo Finance Chart API events=split (independent of EPS values)")
        self.assertEqual(trace["eventsAfterFiling"], [{"date": "2024-01-02", "ratio": 2}])
        self.assertIn("AAA", trace["url"])

    def test_legacy_metadata_only_evidence_cannot_be_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "AAA.json"
            document = {
                "symbol": "AAA",
                "quarterly": {
                    "fy": [2023], "fq": [4], "end": ["2023-12-31"],
                    "values": {"epsDiluted": [None]},
                },
            }
            path.write_text(json.dumps(document), encoding="utf-8")
            accession = "0000001234-24-000001"
            evidence = {
                "accession": accession, "form": "10-K", "cik": "0000001234",
                "periodStart": "2023-10-01", "periodEnd": "2023-12-31",
                "field": "epsDiluted", "reportedValue": "1.25", "unit": "USD/shares",
                "url": "https://www.sec.gov/Archives/edgar/data/1234/000000123424000001/ex99.htm",
                "filingSha256": "a" * 64, "excerpt": "Diluted EPS 1.25",
                "locator": {"table": 1, "row": 3, "column": 2},
                "reportedBasis": "as-filed",
            }
            record = {
                "symbol": "AAA", "grade": "A", "status": "fill-null", "oldValue": None,
                "period": {"fiscalYear": 2023, "end": "2023-12-31"},
                "field": "epsDiluted", "value": 1.25, "method": "direct-sec-filing-table",
                "validation": {"passed": True},
                "source": {
                    "concept": "EarningsPerShareDiluted", "unit": "USD/shares",
                    "start": "2023-10-01", "end": "2023-12-31", "form": "10-K",
                    "filed": "2024-02-15", "accession": accession, "rawValue": "1.25",
                    "splitBasis": {"splitFactorDivisor": 1}, "cik": "0000001234",
                    "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000001234.json",
                    "filingUrl": evidence["url"], "version": "as-filed",
                    "rawFilingEvidence": evidence,
                },
            }
            result = reconcile.apply_records([record], directory)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["fieldsProcessed"], 0)
            self.assertEqual(stored["quarterly"]["values"]["epsDiluted"], [None])
            self.assertNotIn("epsQuality", stored["quarterly"])
            self.assertNotIn("secQ4EpsFacts", stored)

    def _direct_q4_record(self, directory, *, source_start="2023-10-01", target_start="2023-10-01",
                          field="epsDiluted"):
        accession = "0000001234-24-000001"
        fact_name = "EarningsPerShareDiluted" if field == "epsDiluted" else "EarningsPerShareBasic"
        fact_id = "q4Fact"
        body = f'''<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
          xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
          xmlns:us-gaap="http://fasb.org/us-gaap/2023">
          <xbrli:unit id="usdPerShare"><xbrli:divide><xbrli:unitNumerator>
          <xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator>
          <xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
          <xbrli:context id="q4"><xbrli:entity>
          <xbrli:identifier scheme="https://www.sec.gov/">1234</xbrli:identifier></xbrli:entity>
          <xbrli:period><xbrli:startDate>{source_start}</xbrli:startDate>
          <xbrli:endDate>2023-12-31</xbrli:endDate></xbrli:period></xbrli:context>
          <ix:nonFraction id="{fact_id}" name="us-gaap:{fact_name}" contextRef="q4"
          unitRef="usdPerShare" decimals="2">1.25</ix:nonFraction></html>'''.encode()
        digest = hashlib.sha256(body).hexdigest()
        body_path = Path(directory) / f"{source_start}.htm"
        body_path.write_bytes(body)
        url = "https://www.sec.gov/Archives/edgar/data/1234/000000123424000001/exhibit99.htm"
        raw = {
            "accession": accession, "form": "10-K", "cik": "0000001234",
            "periodStart": source_start, "periodEnd": "2023-12-31", "field": field,
            "reportedValue": "1.25", "unit": "USD/shares", "url": url, "rawBodyUrl": url,
            "rawBodyPath": str(body_path), "filingSha256": digest, "sha256": digest,
            "excerpt": "Three months ended December 31, 2023; GAAP diluted EPS $1.25",
            "locator": {"type": "ixbrl", "factId": fact_id, "contextRef": "q4"},
            "reportedBasis": "as filed",
        }
        return {
            "symbol": "AAA", "grade": "A", "status": "fill-null", "oldValue": None,
            "period": {"fiscalYear": 2023, "start": target_start, "end": "2023-12-31"},
            "field": field, "value": 1.25, "method": "direct-sec-filing-ixbrl",
            "validation": {"passed": True},
            "source": {
                "concept": fact_name, "unit": "USD/shares", "start": source_start, "end": "2023-12-31",
                "form": "10-K", "filed": "2024-02-15", "accession": accession, "rawValue": "1.25",
                "splitBasis": {"splitFactorDivisor": 1}, "cik": "0000001234",
                "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000001234.json",
                "filingUrl": url, "version": "as-filed", "rawFilingEvidence": raw,
            },
        }

    def _write_apply_financial(self, path, value=None):
        today = date.today().isoformat()
        path.write_text(json.dumps({
            "symbol": "AAA", "checkedAt": today,
            "quarterly": {"fy": [2023], "fq": [4], "end": ["2023-12-31"],
                          "values": {"epsDiluted": [value]}},
            "annual": {"end": ["2023-12-31"]},
            "split": {
                "events": [], "eventsComplete": True,
                "eventsSource": "Yahoo Finance chart range=max split events",
                "eventsCheckedAt": today, "eventsSymbol": "AAA",
                "eventsCoverageStart": "1990-01-01", "eventsCoverageEnd": today,
                "eventsParserVersion": 4,
            },
        }), encoding="utf-8")

    def test_apply_rechecks_original_ixbrl_and_fills_a_null_q4(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "AAA.json"
            self._write_apply_financial(path)
            result = reconcile.apply_records([self._direct_q4_record(directory)], directory)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["fieldsProcessed"], 1)
            self.assertEqual(stored["quarterly"]["values"]["epsDiluted"], [1.25])
            self.assertEqual(stored["quarterly"]["epsQuality"][0]["epsDiluted"], "A")

    def test_apply_rejects_valid_q4_ixbrl_if_start_differs_from_target_period(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "AAA.json"
            self._write_apply_financial(path)
            record = self._direct_q4_record(
                directory, source_start="2023-10-02", target_start="2023-10-01")
            result = reconcile.apply_records([record], directory)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["fieldsProcessed"], 0)
            self.assertEqual(stored["quarterly"]["values"]["epsDiluted"], [None])

    def test_apply_rejects_unknown_field_with_valid_basic_locator(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "AAA.json"
            self._write_apply_financial(path)
            record = self._direct_q4_record(directory, field="epsBasic")
            record["field"] = "unknownMetric"
            record["period"]["start"] = "2023-10-01"
            record["source"]["rawFilingEvidence"]["field"] = "unknownMetric"
            result = reconcile.apply_records([record], directory)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["fieldsProcessed"], 0)
            self.assertNotIn("unknownMetric", stored["quarterly"]["values"])
            self.assertNotIn("epsQuality", stored["quarterly"])


if __name__ == "__main__":
    unittest.main()

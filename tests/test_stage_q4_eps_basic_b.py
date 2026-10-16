"""Synthetic checks for SEC source validation and null-only Basic Q4 EPS fills."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atlas/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_financials as fin  # noqa: E402
import seed_financials_sec as sec  # noqa: E402
import stage_q4_eps_basic_b as stage  # noqa: E402


CIK = "0000001234"
ACCESSION = "0000001234-24-000001"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeSecClient:
    def __init__(self, clock, *, forbidden=False):
        self.clock = clock
        self.requests = 0
        self.forbidden = forbidden

    def get_json(self, url):
        self.requests += 1
        self.clock.now += 1
        if self.forbidden:
            raise sec.SecForbidden(url)
        return {"filings": {"recent": {
            "accessionNumber": [ACCESSION], "form": ["10-K"],
            "filingDate": ["2024-02-15"], "primaryDocument": ["synthetic-10k.htm"],
        }}}

    def get_bytes(self, url):
        self.requests += 1
        self.clock.now += 1
        return b"<html><body>synthetic SEC filing</body></html>"


def fetch_record():
    fact = {"cik": CIK, "accession": ACCESSION, "form": "10-K"}
    return {"company": {"cik": CIK}, "inputFacts": {
        "annualIncome": fact, "annualShares": fact,
        "nineMonthIncome": fact, "nineMonthShares": fact,
        "q123BasicShares": [fact, fact, fact],
    }}


def synthetic_numerator_review():
    labels = {
        "annualIncome": "annualIncomeFact", "nineMonthIncome": "nineMonthIncomeFact",
        "annualBasicShares": "annualBasicShareFact", "nineMonthBasicShares": "nineMonthBasicShareFact",
    }
    facts, review = {}, {"decision": "approved"}
    for index, (fact_label, review_label) in enumerate(labels.items(), 1):
        fact = {"cik": CIK, "concept": "SyntheticBasicFact", "unit": "USD" if "Income" in fact_label else "shares",
                "start": "2023-01-01", "end": "2023-12-31" if "annual" in fact_label.lower() else "2023-09-30",
                "form": "10-K" if "annual" in fact_label.lower() else "10-Q",
                "filed": "2024-02-15", "accession": ACCESSION,
                "filingUrl": "https://www.sec.gov/Archives/synthetic", "filingSha256": "abc123",
                "rawValue": str(index * 100)}
        facts[fact_label] = fact
        review[review_label] = dict(fact)
    return review, facts


class SourceFetchTests(unittest.TestCase):
    def test_sec_filing_cache_resumes_after_budget_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = Clock()
            first_client = FakeSecClient(clock)
            first = stage.fetch_filings(
                [fetch_record()], cache=root / "filings", state_path=root / "state.json",
                budget=0.5, client=first_client,
                environ={"SEC_USER_AGENT": "Synthetic Q4 EPS test test@example.com"}, clock=clock)
            self.assertEqual(first["status"], "paused")
            self.assertEqual(first["requestsThisRun"], 1)
            self.assertEqual(first["filingsOk"], 0)

            resume_clock = Clock()
            second_client = FakeSecClient(resume_clock)
            second = stage.fetch_filings(
                [fetch_record()], cache=root / "filings", state_path=root / "state.json",
                budget=0.5, client=second_client,
                environ={"SEC_USER_AGENT": "Synthetic Q4 EPS test test@example.com"}, clock=resume_clock)
            self.assertEqual(second["status"], "done")
            self.assertEqual(second["requestsThisRun"], 1)
            self.assertEqual(second["filingsOk"], 1)
            self.assertEqual(second["requestsTotal"], 2)
            self.assertEqual(second["sources"][CIK + ":" + ACCESSION]["form"], "10-K")

    def test_first_403_stops_fetch_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            client = FakeSecClient(clock, forbidden=True)
            result = stage.fetch_filings(
                [fetch_record()], cache=Path(directory) / "filings",
                state_path=Path(directory) / "state.json", budget=30,
                client=client, environ={"SEC_USER_AGENT": "Synthetic Q4 EPS test test@example.com"},
                clock=clock)
            self.assertEqual(result["status"], "stopped-403")
            self.assertEqual(client.requests, 1)


class RawFactAndWriteTests(unittest.TestCase):
    def test_exact_inline_fact_is_matched_and_missing_source_fact_is_rejected(self):
        html = b"""<html xmlns:ix=\"http://www.xbrl.org/2013/inlineXBRL\"
          xmlns:xbrli=\"http://www.xbrl.org/2003/instance\"
          xmlns:us-gaap=\"http://fasb.org/us-gaap/2023\">
          <xbrli:context id=\"fy\"><xbrli:entity><xbrli:identifier scheme=\"x\">1</xbrli:identifier></xbrli:entity>
          <xbrli:period><xbrli:startDate>2023-01-01</xbrli:startDate><xbrli:endDate>2023-12-31</xbrli:endDate></xbrli:period></xbrli:context>
          <xbrli:unit id=\"usd\"><xbrli:measure>xbrli:USD</xbrli:measure></xbrli:unit>
          <ix:nonFraction name=\"us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic\" contextRef=\"fy\" unitRef=\"usd\" scale=\"0\">365</ix:nonFraction>
          </html>"""
        parsed = stage.inline.parse_inline_xbrl(html)
        fact = {"concept": "NetIncomeLossAvailableToCommonStockholdersBasic", "unit": "USD",
                "start": "2023-01-01", "end": "2023-12-31", "value": "365"}
        match, reason = stage._period_fact(parsed, fact, role="annual-income")
        self.assertIsNone(reason)
        self.assertEqual(match["rawValue"], "365")
        missing, reason = stage._period_fact(parsed, {**fact, "value": "366"}, role="annual-income")
        self.assertIsNone(missing)
        self.assertEqual(reason, "companyfacts-vs-raw-filing-value-mismatch-annual-income")

    def test_share_class_proof_requires_one_cover_table_common_class(self):
        fact = {"concept": "WeightedAverageNumberOfSharesOutstandingBasic"}
        parsed = {"facts": [], "filingText": (
            "Securities registered pursuant to Section 12(b): Title of each class "
            "Class A common shares FTAI New York Stock Exchange Series A Preferred Shares "
            "Securities registered pursuant to Section 12(g): None Indicate by check mark")}
        count, proof = stage._share_class_proof(parsed, fact, "2023-01-01", "2023-12-31")
        self.assertEqual(count, 1)
        self.assertEqual(proof["shareClass"], "Class A")
        self.assertIn("Class A common shares", proof["coverTableExcerpts"][0])

    def test_share_class_proof_rejects_missing_or_multiple_cover_classes(self):
        fact = {"concept": "WeightedAverageNumberOfSharesOutstandingBasic"}
        missing = {"facts": [], "filingText": "No class table in this filing."}
        count, proof = stage._share_class_proof(missing, fact, "2023-01-01", "2023-12-31")
        self.assertIsNone(count)
        self.assertEqual(proof["reason"], "cover-table-does-not-prove-one-registered-common-class")

        multiple = {"facts": [], "filingText": (
            "Title of each class Class A common shares AAA Exchange "
            "Class B common shares BBB Exchange Indicate by check mark")}
        count, proof = stage._share_class_proof(multiple, fact, "2023-01-01", "2023-12-31")
        self.assertIsNone(count)
        self.assertEqual(len(proof["registeredCommonClasses"]), 2)

    def test_share_class_proof_ignores_class_of_common_wording(self):
        fact = {"concept": "WeightedAverageNumberOfSharesOutstandingBasic"}
        parsed = {"facts": [], "filingText": (
            "Title of each class Common Stock XYZ Exchange Indicate by check mark "
            "EPS is calculated under the two-class method for each class of common stock.")}
        count, proof = stage._share_class_proof(parsed, fact, "2023-01-01", "2023-12-31")
        self.assertEqual(count, 1)
        self.assertEqual(proof["shareClass"], "Common")

    def test_share_class_proof_keeps_cover_label_separate_from_body_alias(self):
        fact = {"concept": "WeightedAverageNumberOfSharesOutstandingBasic"}
        parsed = {"facts": [], "filingText": (
            "Title of each class Common Stock JRVR Exchange Indicate by check mark "
            "Shareholders' equity: Class A common shares, par value $0.0002.")}
        count, proof = stage._share_class_proof(parsed, fact, "2025-01-01", "2025-12-31")
        self.assertEqual(count, 1)
        self.assertEqual(proof["shareClass"], "Common")
        self.assertEqual(proof["filingTextClasses"], ["Class A"])

    def test_numerator_review_requires_approval_and_all_four_source_facts(self):
        review, facts = synthetic_numerator_review()
        self.assertIsNone(stage.basic_eps_numerator_review_failure(review, facts))
        self.assertEqual(stage.basic_eps_numerator_review_failure(None, facts),
                         "basic-eps-numerator-note-review-missing")
        rejected = {**review, "decision": "retain-missing", "failureReason": "allocation-unknown"}
        self.assertEqual(stage.basic_eps_numerator_review_failure(rejected, facts), "allocation-unknown")
        missing_fact = dict(facts)
        missing_fact.pop("nineMonthIncome")
        self.assertEqual(stage.basic_eps_numerator_review_failure(review, missing_fact),
                         "basic-eps-numerator-review-source-missing:nineMonthIncome")

    def test_numerator_review_is_bound_to_accession_and_raw_value(self):
        review, facts = synthetic_numerator_review()
        changed_accession = {**facts, "annualIncome": {**facts["annualIncome"],
                                                        "accession": "0000001234-24-000002"}}
        self.assertEqual(stage.basic_eps_numerator_review_failure(review, changed_accession),
                         "basic-eps-numerator-review-source-mismatch:annualIncome")
        changed_value = {**facts, "nineMonthBasicShares": {**facts["nineMonthBasicShares"],
                                                            "rawValue": "901"}}
        self.assertEqual(stage.basic_eps_numerator_review_failure(review, changed_value),
                         "basic-eps-numerator-review-source-mismatch:nineMonthBasicShares:rawValue")

    def test_quality_gate_stops_when_absolute_error_threshold_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "backtest.json"
            report.write_text(json.dumps({"groups": {stage.GROUP: {
                "n": 30, "p50NormalizedErrorFloor010": 0.01, "p90NormalizedErrorFloor010": 0.04,
                "p50AbsUsdPerShare": 0.004, "p90AbsUsdPerShare": 0.021, "signErrorCount": 0,
            }}}), encoding="utf-8")
            passed, metrics = stage._gate(report)
        self.assertFalse(passed)
        self.assertEqual(metrics["p90AbsUsdPerShare"], 0.021)

    def test_later_split_multiplies_source_share_count_to_current_basis(self):
        fact = {"value": "1000", "val": "1000", "rawValue": "1000",
                "filed": "2023-02-15", "accession": ACCESSION,
                "concept": "WeightedAverageNumberOfSharesOutstandingBasic", "unit": "shares"}
        adjusted, problem = stage._adjust_share_fact(
            fact, {"split": {"events": [["2024-06-01", 2.0]]}})
        self.assertIsNone(problem)
        self.assertEqual(fact["val"], "2000.0")
        self.assertEqual(adjusted["rawValue"], "1000")
        self.assertEqual(adjusted["splitFactor"], 2.0)
        self.assertEqual(adjusted["adjustedValue"], "2000.0")

    def test_apply_fills_null_only_sets_grade_b_and_quality_issue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "SYN.json"
            document = {"symbol": "SYN", "market": "US", "quality": {"issues": []},
                        "quarterly": {"fy": [2023], "fq": [4], "end": ["2023-12-31"],
                                       "values": {"epsBasic": [None]}, "epsQuality": [None]}}
            path.write_text(json.dumps(document), encoding="utf-8")
            decision = {"symbol": "SYN", "grade": "B", "status": "validated", "value": "0.092",
                        "period": {"fiscalYear": 2023, "start": "2023-10-01", "end": "2023-12-31", "days": 92},
                        "method": "basic-eps-actual-day-reconstruction", "calculation": {}, "source": {
                            "version": {"annual": {"filed": "2024-02-15", "accession": ACCESSION}}},
                        "validation": {"passed": True}}
            ledger = root / "decisions.jsonl"
            self.assertEqual(stage.apply_records([decision], root, ledger), 1)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["quarterly"]["values"]["epsBasic"], [0.092])
            self.assertEqual(saved["quarterly"]["epsQuality"][0]["epsBasic"], "B")
            self.assertIn("q4-eps-from-shares", saved["quality"]["issues"])
            self.assertEqual(stage.apply_records([decision], root, ledger), 0)
            rerun = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(rerun, saved)
            self.assertIn("q4-eps-from-shares", fin.ISSUES)

    def test_apply_refuses_existing_nonmatching_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "SYN.json"
            document = {"symbol": "SYN", "market": "US", "quality": {"issues": []},
                        "quarterly": {"fy": [2023], "fq": [4], "end": ["2023-12-31"],
                                       "values": {"epsBasic": [0.123]}, "epsQuality": [None]}}
            path.write_text(json.dumps(document), encoding="utf-8")
            decision = {"symbol": "SYN", "grade": "B", "status": "validated", "value": "0.092",
                        "period": {"fiscalYear": 2023, "start": "2023-10-01", "end": "2023-12-31", "days": 92},
                        "method": "basic-eps-actual-day-reconstruction", "calculation": {}, "source": {
                            "version": {"annual": {"filed": "2024-02-15", "accession": ACCESSION}}},
                        "validation": {"passed": True}}
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite non-null Basic Q4 EPS"):
                stage.apply_records([decision], root, root / "decisions.jsonl")
            unchanged = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(unchanged, document)


if __name__ == "__main__":
    unittest.main()

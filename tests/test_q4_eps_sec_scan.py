"""Synthetic SEC submissions/archive fixtures for the historical Q4 filing scanner."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atlas/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import q4_eps_sec_scan as scan  # noqa: E402
import seed_financials_sec as sec  # noqa: E402


CIK = "0000012345"
TARGET = "2020-12-31"


def company_document(symbol="AAA"):
    return {
        "version": 1,
        "symbol": symbol,
        "market": "US",
        "annual": {"end": [], "fy": [], "src": [], "values": {}},
        "quarterly": {
            "end": [TARGET], "fy": [2020], "fq": [4], "src": ["sec"],
            "values": {"epsBasic": [None], "epsDiluted": [0.6]},
        },
        "secQ4EpsFacts": {
            TARGET: {
                "epsBasic": {
                    "method": "direct-sec-companyfacts", "cik": CIK,
                    "url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json",
                },
            },
        },
    }


def filing(accession, form, report_date, filing_date, items="", primary=None):
    return {
        "accessionNumber": accession,
        "form": form,
        "reportDate": report_date,
        "filingDate": filing_date,
        "items": items,
        "primaryDocument": primary or f"{accession.replace('-', '')}.htm",
    }


def submissions(recent_rows, files=None):
    recent = {}
    fields = ("accessionNumber", "form", "reportDate", "filingDate", "items", "primaryDocument")
    for field in fields:
        recent[field] = [row.get(field) for row in recent_rows]
    return {"cik": int(CIK), "filings": {"recent": recent, "files": files or []}}


class FakeClient:
    def __init__(self, json_responses, byte_responses=None, *, forbidden_url=None, clock_state=None):
        self.json_responses = json_responses
        self.byte_responses = byte_responses or {}
        self.forbidden_url = forbidden_url
        self.clock_state = clock_state
        self.requests = 0
        self.urls = []

    def get_json(self, url, allow_404=False):
        self.requests += 1
        self.urls.append(url)
        if self.clock_state is not None:
            self.clock_state[0] += 1
        if url == self.forbidden_url:
            raise sec.SecForbidden(url)
        if url == sec.TICKERS_URL and url not in self.json_responses:
            return {"0": {"cik_str": int(CIK), "ticker": "AAA"}}
        if url not in self.json_responses:
            if allow_404:
                return None
            raise sec.FetchError(f"missing mocked URL {url}")
        return self.json_responses[url]

    def get_bytes(self, url, allow_404=False):
        self.requests += 1
        self.urls.append(url)
        if self.clock_state is not None:
            self.clock_state[0] += 1
        if url == self.forbidden_url:
            raise sec.SecForbidden(url)
        if url not in self.byte_responses:
            if allow_404:
                return None
            raise sec.FetchError(f"missing mocked URL {url}")
        return self.byte_responses[url]


class CandidateMappingTests(unittest.TestCase):
    def test_targets_include_existing_ungraded_values_for_raw_source_verification(self):
        doc = company_document()
        row = doc["quarterly"]["values"]
        row["epsBasic"] = [0.62]
        doc["secQ4EpsFacts"][TARGET]["epsBasic"]["grade"] = "A"
        targets = scan.q4_targets(doc)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["missing"], [])
        self.assertEqual(targets[0]["verificationFields"], ["epsBasic", "epsDiluted"])
        self.assertEqual(targets[0]["existingValues"], {"epsBasic": 0.62, "epsDiluted": 0.6})
        self.assertEqual(targets[0]["existingGrades"]["epsBasic"], "A")

    def test_targets_omit_only_a_field_with_complete_raw_filing_trace(self):
        doc = company_document()
        evidence = {
            "accession": "0000000001-21-000001", "form": "10-K", "periodStart": "2020-10-01",
            "periodEnd": TARGET, "field": "epsDiluted", "reportedValue": 0.6, "normalizedValue": 0.6,
            "unit": "USD/shares", "currency": "USD", "cik": CIK, "splitBasisVerified": True,
            "url": "https://www.sec.gov/Archives/edgar/data/12345/000000000121000001/ex99.htm",
            "filingSha256": "a" * 64, "excerpt": "Diluted EPS 0.60", "locator": {"table": 2, "row": 5},
            "reportedBasis": "as-filed",
        }
        doc["secQ4EpsFacts"][TARGET]["epsDiluted"] = {"method": "direct-sec-companyfacts", "cik": CIK}
        doc["secQ4EpsFacts"][TARGET]["epsDiluted"]["grade"] = "A"
        doc["secQ4EpsFacts"][TARGET]["epsDiluted"]["rawFilingEvidence"] = evidence
        targets = scan.q4_targets(doc)
        self.assertEqual(targets[0]["verificationFields"], ["epsBasic"])
        self.assertEqual(targets[0]["existingValues"]["epsDiluted"], 0.6)

    def test_inconsistent_grade_a_trace_does_not_suppress_source_verification(self):
        doc = company_document()
        evidence = {
            "accession": "0000000001-21-000001", "form": "10-K", "periodStart": "2020-10-01",
            "periodEnd": TARGET, "field": "epsDiluted", "reportedValue": 0.6, "normalizedValue": 0.6,
            "unit": "USD/shares", "currency": "USD", "cik": CIK, "splitBasisVerified": True,
            "url": "https://www.sec.gov/Archives/edgar/data/99999/000000000121000001/ex99.htm",
            "filingSha256": "a" * 64, "excerpt": "Diluted EPS 0.60", "locator": {"table": 2, "row": 5},
        }
        doc["secQ4EpsFacts"][TARGET]["epsDiluted"] = {"grade": "A", "cik": CIK,
                                                         "rawFilingEvidence": evidence}
        targets = scan.q4_targets(doc)
        self.assertEqual(targets[0]["verificationFields"], ["epsBasic", "epsDiluted"])

    def test_flat_legacy_source_ledger_recovers_historical_cik_without_companyfacts(self):
        doc = company_document()
        doc.pop("secQ4EpsFacts")
        doc["q4EpsSources"] = {TARGET: {
            "accession": "0000012345-21-000001", "url":
            "https://www.sec.gov/Archives/edgar/data/12345/000001234521000001/ex99.htm",
        }}
        self.assertEqual(scan.q4_targets(doc)[0]["provenanceCik"], CIK)

    def test_existing_basic_grade_b_exception_is_preserved_and_not_reclassified(self):
        doc = company_document()
        doc["quarterly"]["values"]["epsBasic"] = [0.62]
        doc["quarterly"]["epsQuality"] = [{"epsBasic": "B"}]
        targets = scan.q4_targets(doc)
        self.assertEqual(targets[0]["verificationFields"], ["epsDiluted"])
        self.assertEqual(targets[0]["preservedExceptions"], {
            "epsBasic": "existing-ADR-grade-B-exception-preserved"})

    def test_historical_cik_is_recovered_only_from_matching_sec_archive_accession(self):
        doc = company_document()
        doc["secQ4EpsFacts"][TARGET] = {}
        doc["q4EpsSources"] = {TARGET: {"epsDiluted": {
            "cik": CIK, "accession": "0000012345-21-000001",
            "url": "https://www.sec.gov/Archives/edgar/data/12345/000001234521000001/annual.htm",
        }}}
        self.assertEqual(scan.q4_targets(doc)[0]["provenanceCik"], CIK)
        doc["q4EpsSources"][TARGET]["epsDiluted"]["url"] = \
            "https://www.sec.gov/Archives/edgar/data/99999/000001234521000001/annual.htm"
        self.assertIsNone(scan.q4_targets(doc)[0]["provenanceCik"])

    def test_companyfacts_method_label_without_matching_official_url_is_not_identity(self):
        doc = company_document()
        doc["secQ4EpsFacts"][TARGET] = {"epsDiluted": {
            "method": "direct-sec-companyfacts", "cik": CIK,
            "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000099999.json",
        }}
        self.assertIsNone(scan.q4_targets(doc)[0]["provenanceCik"])

    def test_period_uses_report_date_and_keeps_late_amendments_and_all_relevant_forms(self):
        rows = [
            filing("0000000001-20-000001", "10-K", TARGET, "2021-02-20"),
            filing("0000000001-22-000002", "10-K/A", TARGET, "2022-08-15"),  # > 120 days late
            filing("0000000001-22-000003", "10-KT", TARGET, "2022-09-01"),
            filing("0000000001-22-000004", "10-KT/A", TARGET, "2022-09-02"),
            filing("0000000001-21-000005", "8-K", TARGET, "2021-03-10", "2.02; 9.01"),
            filing("0000000001-21-000006", "8-K/A", TARGET, "2021-03-11", "8.01"),
            filing("0000000001-22-000007", "10-Q/A", TARGET, "2022-03-01"),
            filing("0000000001-22-000008", "6-K", TARGET, "2022-03-05"),
            filing("0000000001-22-000009", "20-F", "2021-12-31", "2022-03-05"),
            filing("0000000001-22-000010", "10-Q", "2021-06-30", "2021-08-01"),
            filing("0000000001-21-000011", "8-K", "2021-08-15", "2021-08-15", "2.02"),
            filing("0000000001-21-000012", "6-K", None, "2021-10-01"),
            filing("0000000001-22-000013", "10-K", "2021-12-31", "2022-02-28"),
            filing("0000000001-22-000014", "8-K", "2022-02-15", "2022-02-15", "2.02"),
        ]
        matched = scan.candidate_filings(TARGET, rows)
        forms = {row["form"] for row in matched}
        self.assertEqual(forms, {"10-K", "10-K/A", "10-KT", "10-KT/A", "8-K", "8-K/A", "10-Q/A", "6-K", "20-F"})
        delayed = next(row for row in matched if row["accessionNumber"] == "0000000001-22-000002")
        self.assertEqual(delayed["filedDaysAfterQ4End"], 592)
        self.assertEqual(delayed["matchReasons"], ["report-date-for-period", "amendment-after-q4-end-full-history"])
        items = next(row for row in matched if row["form"] == "8-K")
        self.assertIn("8-k-item-2.02", items["matchReasons"])
        other = next(row for row in matched if row["form"] == "8-K/A")
        self.assertIn("8-k-reported-period-end", other["matchReasons"])
        delayed_earnings = next(row for row in matched if row["accessionNumber"] == "0000000001-21-000011")
        self.assertEqual(delayed_earnings["filedDaysAfterQ4End"], 227)
        self.assertIn("later-8-k-results-or-financial-exhibits", delayed_earnings["matchReasons"])
        foreign_cycle = next(row for row in matched if row["accessionNumber"] == "0000000001-21-000012")
        self.assertIn("later-6-k-full-history", foreign_cycle["matchReasons"])
        annual_release = next(row for row in matched if row["accessionNumber"] == "0000000001-22-000014")
        self.assertEqual(annual_release["filedDaysAfterQ4End"], 411)
        self.assertIn("later-8-k-results-or-financial-exhibits", annual_release["matchReasons"])
        comparative = next(row for row in matched if row["form"] == "20-F")
        self.assertIn("annual-comparative-period", comparative["matchReasons"])

    def test_late_8k_amendment_after_next_annual_remains_a_candidate(self):
        original_event = filing("0000000001-21-000101", "8-K", "2021-01-20", "2021-01-20", "2.02,9.01")
        next_annual = filing("0000000001-22-000102", "10-K", "2021-12-31", "2022-02-20")
        late_amendment = filing("0000000001-24-000103", "8-K/A", "2021-01-20", "2024-03-01", "2.02,9.01")
        matched = scan.candidate_filings(TARGET, [original_event, next_annual, late_amendment], "2021-12-31")
        row = next(row for row in matched if row["accessionNumber"] == late_amendment["accessionNumber"])
        self.assertIn("amendment-after-q4-end-full-history", row["matchReasons"])

    def test_later_8k_6k_and_annual_comparatives_are_not_cut_off_at_next_annual(self):
        next_annual = filing("0000000001-21-000201", "10-K", "2021-12-31", "2022-02-20")
        late_8k = filing("0000000001-26-000202", "8-K", "2026-02-01", "2026-02-02", "2.02")
        late_6k = filing("0000000001-26-000203", "6-K", "2026-02-01", "2026-02-02")
        late_annual = filing("0000000001-26-000204", "20-F", "2025-12-31", "2026-02-02")
        matched = scan.candidate_filings(TARGET, [next_annual, late_8k, late_6k, late_annual])
        by_accession = {row["accessionNumber"]: row for row in matched}
        self.assertIn("later-8-k-results-or-financial-exhibits",
                      by_accession[late_8k["accessionNumber"]]["matchReasons"])
        self.assertIn("later-6-k-full-history", by_accession[late_6k["accessionNumber"]]["matchReasons"])
        self.assertIn("annual-comparative-period", by_accession[late_annual["accessionNumber"]]["matchReasons"])

    def test_deduplicates_recent_and_archived_accessions_but_keeps_source_blocks(self):
        first = filing("0000000001-20-000001", "10-K", TARGET, "2021-02-20")
        duplicate = dict(first, primaryDocument="annual.htm")
        rows = [dict(first, sourceBlocks=["recent"]), dict(duplicate, sourceBlocks=["old.json"])]
        result = scan.dedupe_filings(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sourceBlocks"], ["old.json", "recent"])
        self.assertEqual(result[0]["primaryDocument"], first["primaryDocument"])

    def test_item_901_filings_are_screened_even_when_primary_description_is_generic(self):
        rows = [
            filing("0000000001-21-000001", "8-K", "2021-02-10", "2021-02-10", "9.01",
                   "corporate-update.htm"),
            filing("0000000001-21-000002", "8-K", "2021-01-20", "2021-01-20", "8.01",
                   "leadership-update.htm"),
            filing("0000000001-21-000003", "8-K", "2020-12-31", "2021-02-10", "9.01",
                   "earnings-release.htm"),
        ]
        rows[2]["primaryDocDescription"] = "Exhibit 99.1 earnings release with quarterly results"
        matched = scan.candidate_filings(TARGET, rows)
        self.assertEqual({row["accessionNumber"] for row in matched},
                         {rows[0]["accessionNumber"], rows[2]["accessionNumber"]})
        generic = next(row for row in matched if row["accessionNumber"] == rows[0]["accessionNumber"])
        self.assertIn("8-k-item-9.01-screen", generic["matchReasons"])
        earnings = next(row for row in matched if row["accessionNumber"] == rows[2]["accessionNumber"])
        self.assertIn("8-k-item-9.01-screen", earnings["matchReasons"])

    def test_historical_submissions_block_must_have_accession_array(self):
        self.assertTrue(scan._valid_submissions_block_payload(submissions([], []), CIK))
        self.assertFalse(scan._valid_submissions_block_payload({"filings": {"recent": {"form": []}}}))
        self.assertFalse(scan._valid_submissions_block_payload({"unrelated": []}))
        wrong_cik = submissions([], [])
        wrong_cik["cik"] = 99999
        self.assertFalse(scan._valid_submissions_block_payload(wrong_cik, CIK))

    def test_historical_submissions_block_accepts_blank_primary_document(self):
        payload = submissions([], [])
        historical = filing("0000000001-00-000768", "8-K", "2000-05-21", "2000-05-24", "5,7")
        historical["primaryDocument"] = ""
        payload.update({key: [value] for key, value in historical.items()})
        self.assertTrue(scan._valid_submissions_block_payload(payload, CIK))

    def test_submissions_history_requires_all_filing_selection_arrays_to_align(self):
        valid = submissions([filing("0000000001-20-000001", "10-K", TARGET, "2021-02-20")], [])
        self.assertTrue(scan._valid_submissions_payload(valid, CIK))
        broken = json.loads(json.dumps(valid))
        broken["filings"]["recent"]["primaryDocument"] = []
        self.assertFalse(scan._valid_submissions_payload(broken, CIK))
        malformed = json.loads(json.dumps(valid))
        malformed["filings"]["recent"]["accessionNumber"] = [None]
        self.assertFalse(scan._valid_submissions_payload(malformed, CIK))
        mismatch = json.loads(json.dumps(valid))
        mismatch["cik"] = 99999
        self.assertFalse(scan._valid_submissions_payload(mismatch, CIK))
        self.assertFalse(scan._valid_submissions_payload({"filings": valid["filings"]}, CIK))

    def test_direct_report_date_tolerance_accepts_52_53_week_labels(self):
        rows = [
            filing("0000000001-21-000001", "10-K", "2021-01-07", "2021-02-10"),
            filing("0000000001-21-000002", "10-K", "2021-01-06", "2021-02-10"),
            filing("0000000001-21-000003", "10-K", "2021-01-08", "2021-02-10"),
        ]
        matched = scan.candidate_filings(TARGET, rows)
        self.assertEqual({row["accessionNumber"] for row in matched},
                         {rows[0]["accessionNumber"], rows[1]["accessionNumber"]})

    def test_event_candidate_is_not_cut_off_when_next_annual_filing_is_not_yet_available(self):
        late = filing("0000000001-22-000099", "8-K", "2022-05-01", "2022-05-01", "2.02")
        matched = scan.candidate_filings(TARGET, [late])
        self.assertEqual([row["accessionNumber"] for row in matched], [late["accessionNumber"]])
        self.assertGreater(matched[0]["filedDaysAfterQ4End"], 365)

    def test_document_selection_finds_legacy_embedded_exhibit_names(self):
        record = filing("0000000001-21-000001", "8-K", TARGET, "2021-02-10",
                         "2.02; 9.01", "a11-29772_18k.htm")
        index = {"directory": {"item": [
            {"name": "a11-29772_18k.htm", "type": "8-K"},
            {"name": "a11-29772_1ex99d1.htm", "type": "EX-99.1"},
            {"name": "a11-29772_1ex99d1.pdf", "type": "EX-99.1"},
            {"name": "a11-29772_1.xml", "type": "EX-99.1"},
        ]}}
        self.assertEqual(scan._document_names(record, index),
                         ["a11-29772_18k.htm", "a11-29772_1ex99d1.htm",
                          "a11-29772_1ex99d1.pdf", "a11-29772_1.xml"])

    def test_annual_filing_includes_eps_supplement_exhibits(self):
        record = filing("0000000001-21-000001", "10-K", TARGET, "2021-02-10", primary="annual.htm")
        index = {"directory": {"item": [
            {"name": "annual.htm", "type": "10-K"},
            {"name": "q4-supplement.htm", "type": "EX-99.2", "description": "Quarterly EPS data"},
            {"name": "instance.xml", "type": "EX-101.INS"},
        ]}}
        self.assertEqual(scan._document_names(record, index), ["annual.htm", "q4-supplement.htm", "instance.xml"])

    def test_format_and_parse_failures_have_separate_statuses(self):
        self.assertEqual(scan._body_format_status("release.html", b"not an html document"),
                         ("format-error", "not-attempted"))
        self.assertEqual(scan._body_format_status("release.html", b"<html><body>Q4 EPS</body></html>"),
                         ("ok", "parsed-prefix"))
        with patch.object(scan._MarkupProbe, "feed", side_effect=ValueError("fixture parser failure")):
            format_status, parse_status = scan._body_format_status("release.html", b"<html><body>Q4</body></html>")
        self.assertEqual(format_status, "ok")
        self.assertTrue(parse_status.startswith("parse-error:"))
        self.assertEqual(scan._body_format_status("release.pdf", b"%PDF-1.7 fixture"), ("ok", "not-attempted"))


class ScannerRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="q4-eps-sec-scan-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.financials = self.root / "financials"
        self.financials.mkdir()
        (self.financials / "AAA.json").write_text(json.dumps(company_document()), encoding="utf-8")
        self.cache = self.root / "cache"
        self.state = self.cache / "state.json"
        self.cache.mkdir()
        self.state.write_text(json.dumps({"version": scan.STATE_VERSION,
                                          "candidateRulesVersion": scan.CANDIDATE_RULES_VERSION,
                                          "tickerMap": {"AAA": CIK}, "urls": {}, "companies": {},
                                          "requests": 0}), encoding="utf-8")
        self.submissions_url = scan.SUBMISSIONS_URL.format(cik=CIK)
        self.block_url = scan.SUBMISSIONS_BLOCK_URL.format(name="CIK0000012345-old.json")

    def test_reads_all_historical_blocks_deduplicates_and_reuses_per_url_cache_on_resume(self):
        recent = filing("0000000001-20-000001", "10-K", TARGET, "2021-02-20", primary="annual.htm")
        late = filing("0000000001-22-000002", "8-K", TARGET, "2022-08-15", "2.02; 9.01", "earnings.htm")
        next_annual = filing("0000000001-22-000003", "10-K", "2021-12-31", "2022-02-28", primary="next-annual.htm")
        next_earnings = filing("0000000001-22-000004", "8-K", "2022-02-15", "2022-02-15", "2.02", "next-release.htm")
        payload = submissions([recent], [{"name": "CIK0000012345-old.json"}])
        block = submissions([dict(recent, primaryDocument="different-annual.htm"), late, next_annual, next_earnings])
        index_urls = {
            scan.ARCHIVES_INDEX_URL.format(cik="12345", accession=scan._accession_path(row["accessionNumber"])): row
            for row in (recent, late, next_annual, next_earnings)
        }
        json_responses = {self.submissions_url: payload, self.block_url: block}
        byte_responses = {}
        for url, row in index_urls.items():
            json_responses[url] = {"directory": {"item": [{"name": row["primaryDocument"], "type": "10-K"}]}}
            doc_url = scan.ARCHIVES_DOCUMENT_URL.format(
                cik="12345", accession=scan._accession_path(row["accessionNumber"]), name=row["primaryDocument"])
            byte_responses[doc_url] = b"<html><body>EPS 0.4</body></html>"
        client = FakeClient(json_responses, byte_responses)
        result = scan.run(financials_dir=self.financials, cache_dir=self.cache, state_path=self.state,
                          client=client, environ={"SEC_USER_AGENT": "Offline fixture contact@example.com"}, reporter=lambda _message: None)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["candidateFilings"], 4)
        self.assertEqual(client.urls.count(self.submissions_url), 1)
        self.assertEqual(client.urls.count(self.block_url), 1)
        manifest = json.loads((self.cache / "results/AAA.json").read_text(encoding="utf-8"))
        period = manifest["periods"][0]
        self.assertEqual(period["cik"], CIK)
        self.assertEqual({row["form"] for row in period["candidateFilings"]}, {"10-K", "8-K"})
        self.assertEqual(period["searchTrajectory"][-1]["rule"],
                         "reportDate within 7 days, all later 2.02/9.01 8-Ks and 6-Ks, all annual comparative periods and amendments; no fixed filingDate cutoff")
        self.assertEqual(sum(row["accessionNumber"] == recent["accessionNumber"]
                             for row in period["candidateFilings"]), 1)
        second_client = FakeClient({}, {})
        resumed = scan.run(financials_dir=self.financials, cache_dir=self.cache, state_path=self.state,
                           client=second_client, environ={"SEC_USER_AGENT": "Offline fixture contact@example.com"},
                           reporter=lambda _message: None)
        self.assertEqual(resumed["status"], "done")
        self.assertEqual(second_client.requests, 0)

    def test_zero_budget_does_not_start_sec_requests(self):
        client = FakeClient({}, {})
        result = scan.run(financials_dir=self.financials, cache_dir=self.cache, state_path=self.state, budget=0,
                          client=client, environ={"SEC_USER_AGENT": "Offline fixture contact@example.com"},
                          reporter=lambda _message: None)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(client.requests, 0)

    def test_symbol_filter_limits_a_prioritized_scan_to_requested_issuers(self):
        (self.financials / "BBB.json").write_text(json.dumps(company_document("BBB")), encoding="utf-8")
        payload = submissions([], [])
        client = FakeClient({self.submissions_url: payload}, {})

        result = scan.run(financials_dir=self.financials, cache_dir=self.cache, state_path=self.state,
                          client=client, symbols=["aaa"],
                          environ={"SEC_USER_AGENT": "Offline fixture contact@example.com"}, reporter=lambda _message: None)

        self.assertEqual(result["companies"], 1)
        self.assertTrue((self.cache / "results/AAA.json").is_file())
        self.assertFalse((self.cache / "results/BBB.json").exists())

    def test_budget_checkpoints_after_a_request_and_resumes_from_cached_submissions(self):
        recent = filing("0000000001-20-000001", "10-K", TARGET, "2021-02-20", primary="annual.htm")
        payload = submissions([recent], [{"name": "CIK0000012345-old.json"}])
        block = submissions([], [])
        index_url = scan.ARCHIVES_INDEX_URL.format(cik="12345", accession=scan._accession_path(recent["accessionNumber"]))
        doc_url = scan.ARCHIVES_DOCUMENT_URL.format(
            cik="12345", accession=scan._accession_path(recent["accessionNumber"]), name="annual.htm")
        json_responses = {
            self.submissions_url: payload,
            self.block_url: block,
            index_url: {"directory": {"item": [{"name": "annual.htm", "type": "10-K"}]}},
        }
        byte_responses = {doc_url: b"<html><body>EPS 0.4</body></html>"}
        clock_state = [0]
        first_client = FakeClient(json_responses, byte_responses, clock_state=clock_state)
        paused = scan.run(financials_dir=self.financials, cache_dir=self.cache, state_path=self.state, budget=1,
                          client=first_client, clock=lambda: clock_state[0],
                          environ={"SEC_USER_AGENT": "Offline fixture contact@example.com"}, reporter=lambda _message: None)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(first_client.urls, [self.submissions_url])
        partial = json.loads((self.cache / "results/AAA.json").read_text(encoding="utf-8"))
        trajectory = partial["periods"][0]["searchTrajectory"]
        self.assertEqual(trajectory[-1]["status"], "paused")

        resumed_client = FakeClient(json_responses, byte_responses)
        resumed = scan.run(financials_dir=self.financials, cache_dir=self.cache, state_path=self.state,
                           client=resumed_client,
                           environ={"SEC_USER_AGENT": "Offline fixture contact@example.com"}, reporter=lambda _message: None)
        self.assertEqual(resumed["status"], "done")
        self.assertNotIn(self.submissions_url, resumed_client.urls)
        self.assertIn(self.block_url, resumed_client.urls)

    def test_partial_accession_resume_fetches_remaining_exhibit_without_refetching_cached_body(self):
        annual = filing("0000000001-20-000031", "8-K", TARGET, "2021-02-20", "9.01", "annual.htm")
        payload = submissions([annual], [{"name": "CIK0000012345-old.json"}])
        index_url = scan.ARCHIVES_INDEX_URL.format(cik="12345", accession=scan._accession_path(annual["accessionNumber"]))
        index_payload = {"directory": {"item": [
            {"name": "annual.htm", "type": "8-K"},
            {"name": "q4-supplement.htm", "type": "EX-99.2", "description": "Quarterly EPS data"},
        ]}}
        annual_url = scan.ARCHIVES_DOCUMENT_URL.format(
            cik="12345", accession=scan._accession_path(annual["accessionNumber"]), name="annual.htm")
        exhibit_url = scan.ARCHIVES_DOCUMENT_URL.format(
            cik="12345", accession=scan._accession_path(annual["accessionNumber"]), name="q4-supplement.htm")
        block_url = self.block_url
        json_responses = {self.submissions_url: payload, block_url: submissions([], []), index_url: index_payload}
        byte_responses = {annual_url: b"<html><body>annual filing</body></html>",
                          exhibit_url: b"<html><body>Q4 supplement</body></html>"}
        clock_state = [0]
        first_client = FakeClient(json_responses, byte_responses, clock_state=clock_state)

        paused = scan.run(financials_dir=self.financials, cache_dir=self.cache, state_path=self.state,
                          budget=3.5, client=first_client, clock=lambda: clock_state[0],
                          environ={"SEC_USER_AGENT": "Offline fixture contact@example.com"}, reporter=lambda _message: None)

        self.assertEqual(paused["status"], "paused")
        manifest = json.loads((self.cache / "results/AAA.json").read_text(encoding="utf-8"))
        accession_result = manifest["periods"][0]["candidateFilings"][0]["cache"]
        self.assertEqual(accession_result["status"], "partial")
        self.assertEqual([row["name"] for row in accession_result["documents"]],
                         ["annual.htm", "q4-supplement.htm"])
        self.assertEqual([row["status"] for row in accession_result["documents"]], ["ok", "paused"])

        resumed_client = FakeClient(json_responses, byte_responses)
        resumed = scan.run(financials_dir=self.financials, cache_dir=self.cache, state_path=self.state,
                           client=resumed_client,
                           environ={"SEC_USER_AGENT": "Offline fixture contact@example.com"}, reporter=lambda _message: None)

        self.assertEqual(resumed["status"], "done")
        self.assertIn(exhibit_url, resumed_client.urls)
        self.assertNotIn(annual_url, resumed_client.urls)

    def test_first_403_stops_before_any_historical_block_request(self):
        client = FakeClient({}, forbidden_url=self.submissions_url)
        result = scan.run(financials_dir=self.financials, cache_dir=self.cache, state_path=self.state,
                          client=client, environ={"SEC_USER_AGENT": "Offline fixture contact@example.com"},
                          reporter=lambda _message: None)
        self.assertEqual(result["status"], "stopped-403")
        self.assertEqual(client.urls, [self.submissions_url])

    def test_existing_html_body_is_read_only_reused_and_hash_verified(self):
        accession = "0000000001-20-000001"
        doc_url = scan.ARCHIVES_DOCUMENT_URL.format(cik="12345", accession=scan._accession_path(accession), name="annual.htm")
        old_dir = self.root / "old/raw-filings"
        old_dir.mkdir(parents=True)
        old_body = b"<html><body>already cached SEC source</body></html>"
        old_path = old_dir / f"{scan._accession_path(accession)}.html"
        old_path.write_bytes(old_body)
        old_state_path = self.root / "old/raw-filing-state.json"
        old_state_path.write_text(json.dumps({
            "version": 1, "sources": {f"{CIK}:{accession}": {
                "status": "ok", "cik": CIK, "accession": accession, "documentUrl": doc_url,
                "htmlCache": str(old_path), "sha256": hashlib.sha256(old_body).hexdigest(),
            }}, "submissions": {},
        }), encoding="utf-8")
        state = scan._read_state(self.state)
        client = FakeClient({}, {})
        runner = scan.ScanRun(self.cache, self.state, state, client, None, lambda: 0, lambda _message: None,
                              existing_raw_cache_dir=old_dir, existing_raw_state_path=old_state_path)
        body, status, cached_path = runner.binary(doc_url, Path("filings") / CIK / scan._accession_path(accession) / "annual.htm")
        self.assertEqual(body, old_body)
        self.assertEqual(status, "reused-cache")
        self.assertEqual(Path(cached_path), old_path)
        self.assertEqual(client.requests, 0)
        self.assertEqual(state["urls"][doc_url]["sha256"], hashlib.sha256(old_body).hexdigest())
        self.assertEqual(old_path.read_bytes(), old_body)

    def test_existing_submissions_and_index_json_are_reused_by_exact_url(self):
        accession = "0000000001-20-000001"
        old_dir = self.root / "old/raw-filings"
        old_dir.mkdir(parents=True)
        old_submissions = submissions([], [])
        submissions_path = old_dir / f"CIK{CIK}-submissions.json"
        submissions_path.write_text(json.dumps(old_submissions), encoding="utf-8")
        index_url = scan.ARCHIVES_INDEX_URL.format(cik="12345", accession=scan._accession_path(accession))
        old_index = {"directory": {"item": [{"name": "annual.htm", "type": "10-K"}]}}
        index_path = old_dir / f"{scan._accession_path(accession)}-index.json"
        index_path.write_text(json.dumps(old_index), encoding="utf-8")
        old_state_path = self.root / "old/raw-filing-state.json"
        old_state_path.write_text(json.dumps({
            "version": 1, "sources": {f"{CIK}:{accession}": {
                "status": "ok", "cik": CIK, "accession": accession, "indexUrl": index_url,
            }},
            "submissions": {CIK: {"url": scan.SUBMISSIONS_URL.format(cik=CIK), "cachePath": str(submissions_path)}},
        }), encoding="utf-8")
        state = scan._read_state(self.state)
        client = FakeClient({}, {})
        runner = scan.ScanRun(self.cache, self.state, state, client, None, lambda: 0, lambda _message: None,
                              existing_raw_cache_dir=old_dir, existing_raw_state_path=old_state_path)
        fetched_submissions, status = runner.json(scan.SUBMISSIONS_URL.format(cik=CIK))
        self.assertEqual(status, "reused-existing-cache")
        self.assertEqual(fetched_submissions, old_submissions)
        fetched_index, status = runner.json(index_url, allow_404=True)
        self.assertEqual(status, "reused-existing-cache")
        self.assertEqual(fetched_index, old_index)
        self.assertEqual(client.requests, 0)
        self.assertEqual(Path(state["urls"][index_url]["externalCachePath"]), index_path)

    def test_refresh_submissions_bypasses_cached_submission_and_history_json(self):
        """Refresh mode checks current SEC history while normal resumes continue using cache."""
        url = scan.SUBMISSIONS_URL.format(cik=CIK)
        cached = submissions([], [])
        cache_path = self.cache / "http" / "submission.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text(json.dumps(cached), encoding="utf-8")
        state = scan._read_state(self.state)
        state["urls"][url] = {"status": "ok", "path": "http/submission.json"}
        client = FakeClient({url: cached})
        runner = scan.ScanRun(self.cache, self.state, state, client, None, lambda: 0, lambda _message: None,
                              existing_raw_cache_dir=self.root / "no-old-cache",
                              existing_raw_state_path=self.root / "no-old-state.json",
                              refresh_submissions=True)
        result, status = runner.json(url)
        self.assertEqual(status, "fetched")
        self.assertEqual(result, cached)
        self.assertEqual(client.requests, 1)

    def test_refresh_submissions_resume_reuses_urls_already_refreshed_in_generation(self):
        url = scan.SUBMISSIONS_URL.format(cik=CIK)
        payload = submissions([], [])
        state = scan._read_state(self.state)
        state["refreshGeneration"] = {"id": "generation-1", "status": "in-progress",
                                      "candidateRulesVersion": scan.CANDIDATE_RULES_VERSION,
                                      "refreshedUrls": [url]}
        cache_path = self.cache / "http" / "submission.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
        state["urls"][url] = {"status": "ok", "path": "http/submission.json"}
        client = FakeClient({})
        runner = scan.ScanRun(self.cache, self.state, state, client, None, lambda: 0, lambda _message: None,
                              existing_raw_cache_dir=self.root / "no-old-cache",
                              existing_raw_state_path=self.root / "no-old-state.json",
                              refresh_submissions=True)

        result, status = runner.json(url)
        self.assertEqual((result, status), (payload, "cached"))
        self.assertEqual(client.requests, 0)

    def test_cached_404_and_missing_primary_are_distinct(self):
        accession = "0000000001-20-000001"
        row = filing(accession, "10-K", TARGET, "2021-02-20", primary="annual.htm")
        index_url = scan.ARCHIVES_INDEX_URL.format(cik="12345", accession=scan._accession_path(accession))
        runner = scan.ScanRun(self.cache, self.state, scan._read_state(self.state), FakeClient({index_url: None}), None,
                              lambda: 0, lambda _message: None,
                              existing_raw_cache_dir=self.root / "no-old-cache",
                              existing_raw_state_path=self.root / "no-old-state.json")
        runner.client.json_responses[index_url] = None
        result = scan._fetch_candidate_documents(runner, CIK, row)
        self.assertEqual(result["status"], "index-404")

        no_primary = {"directory": {"item": [{"name": "notes.xlsx", "type": "EX-99"}]}}
        runner.client.json_responses[index_url] = no_primary
        # Clear the first 404 URL checkpoint so this setup can exercise the successful empty index.
        runner.state["urls"].pop(index_url, None)
        result = scan._fetch_candidate_documents(runner, CIK, row)
        self.assertEqual(result["status"], "missing-document")


if __name__ == "__main__":
    unittest.main()

"""Synthetic offline audit tests for cached Q4 EPS SEC filing manifests."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atlas/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import audit_q4_eps_sec_scan as audit  # noqa: E402


END = "2024-12-31"
RELEASE = """<html><body><table><caption>USD</caption>
<tr><th></th><th colspan="2">Three Months Ended December 31, 2024</th></tr>
<tr><td>Net income</td><td>$</td><td>1,000</td></tr>
<tr><td>Diluted earnings per share</td><td>$</td><td>0.25</td></tr>
<tr><td>Basic earnings per share</td><td>$</td><td>0.26</td></tr>
</table></body></html>"""

IXBRL_RELEASE = """<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance"
xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:us-gaap="http://fasb.org/us-gaap/2025">
<xbrli:unit id="USDperShare"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>
<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
<xbrli:context id="Q4"><xbrli:entity><xbrli:identifier scheme="https://www.sec.gov/">1</xbrli:identifier></xbrli:entity>
<xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
<ix:nonFraction id="dilutedQ4" name="us-gaap:EarningsPerShareDiluted" contextRef="Q4" unitRef="USDperShare" decimals="2">0.25</ix:nonFraction>
<ix:nonFraction id="basicQ4" name="us-gaap:EarningsPerShareBasic" contextRef="Q4" unitRef="USDperShare" decimals="2">0.26</ix:nonFraction>
</html>"""


def financial_document(*, diluted=None, basic=None, split_events=None):
    return {
        "market": "US",
        "currency": "USD",
        "symbol": "AAA",
        "checkedAt": "2026-01-01",
        "quarterly": {"end": [END], "fy": [2024], "fq": [4],
                      "values": {"netIncome": [1000], "epsDiluted": [diluted], "epsBasic": [basic]}},
        "split": {"adjusted": False, "events": [] if split_events is None else split_events,
                  "eventsComplete": True, "eventsSource": "test fixture: complete split events",
                  "eventsCheckedAt": "2026-01-01", "eventsSymbol": "AAA",
                  "eventsCoverageStart": "2000-01-01", "eventsCoverageEnd": "2026-01-01",
                  "eventsParserVersion": 4},
    }


class AuditTests(unittest.TestCase):
    def test_archive_accession_prefix_may_be_filing_agent_when_target_path_matches(self):
        accession = "0001445305-13-002007"
        item = {"name": "aaon-20130930.htm",
                "url": "https://www.sec.gov/Archives/edgar/data/824142/000144530513002007/aaon-20130930.htm"}
        target = {"cik": "0000824142"}
        filing = {"accessionNumber": accession}
        self.assertIsNone(audit._archive_identity_error(item, target, filing))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="q4-eps-audit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.manifests = self.cache / "results"
        self.manifests.mkdir(parents=True)
        self.financials = self.root / "financials"
        self.financials.mkdir()
        self.report_path = self.root / "staging.json"
        self.financial_path = self.financials / "AAA.json"
        self.write_financials(financial_document())

    def write_financials(self, document):
        self.financial_path.write_text(json.dumps(document), encoding="utf-8")

    def document(self, accession, body=RELEASE.encode(), *, name="ex99.htm", filing_date="2025-02-15",
                 expected_body=None, status="fetched"):
        path = self.cache / "filings" / f"{accession}-{name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        expected = body if expected_body is None else expected_body
        compact = accession.replace("-", "")
        return {"name": name, "url": f"https://www.sec.gov/Archives/edgar/data/1/{compact}/{name}",
                "status": status, "formatStatus": "ok", "parseStatus": "parsed-prefix",
                "cachePath": str(path), "sha256": hashlib.sha256(expected).hexdigest()}

    def filing(self, accession, document, *, filing_date="2025-02-15", form="8-K"):
        return {"accessionNumber": accession, "form": form, "filingDate": filing_date,
                "matchReasons": ["report-date-for-period"],
                "cache": {"status": "ok", "documents": [document]}}

    def write_manifest(self, filings, *, snapshot=None):
        snapshot = {} if snapshot is None else snapshot
        manifest = {"schema": "q4-eps-sec-scan-v1", "candidateRulesVersion": audit.CANDIDATE_RULES_VERSION,
                    "symbol": "AAA", "generatedAt": "2026-01-01T00:00:00+00:00", "periods": [{
            "fy": 2024, "end": END, "cik": "0000000001", "existingValues": snapshot,
            "issuerIdentityVerified": True,
            "issuerIdentitySource": "stored-sec-provenance-matched-to-current-official-ticker-map",
            "sourceCoverageComplete": True, "sourceCoverageFresh": True,
            "sourceCoverageRefreshGeneration": "test-generation",
            "searchTrajectory": [{"stage": "submissions", "status": "fetched", "refreshRequested": True,
                                  "refreshGeneration": "test-generation"},
                                 {"stage": "historical-blocks", "complete": True, "failures": []}],
            "candidateFilings": filings,
        }]}
        (self.manifests / "AAA.json").write_text(json.dumps(manifest), encoding="utf-8")

    def run_audit(self):
        return audit.run(manifest_dir=self.manifests, cache_dir=self.cache,
                         financials_dir=self.financials, output_path=self.report_path)

    def period(self, report):
        return report["companies"][0]["periods"][0]

    def test_verified_html_stages_null_values_with_complete_provenance_and_no_financial_write(self):
        original_financial_bytes = self.financial_path.read_bytes()
        doc = self.document("0000000001-25-000001")
        self.write_manifest([self.filing("0000000001-25-000001", doc)])

        report = self.run_audit()

        self.assertTrue(report["offline"])
        self.assertFalse(report["financialJsonModified"])
        self.assertTrue(self.report_path.is_file())
        self.assertEqual(self.financial_path.read_bytes(), original_financial_bytes)
        self.assertEqual({row["action"] for row in report["stagedDecisions"]}, {"fill-null"})
        self.assertEqual({row["field"] for row in report["stagedDecisions"]}, {"epsBasic", "epsDiluted"})
        diluted = next(row for row in report["stagedDecisions"] if row["field"] == "epsDiluted")
        self.assertEqual(diluted["grade"], "A")
        source = diluted["provenance"][0]
        self.assertEqual(source["accession"], "0000000001-25-000001")
        self.assertEqual(source["form"], "8-K")
        self.assertEqual(source["exhibit"], "ex99.htm")
        self.assertEqual(source["filingDate"], "2025-02-15")
        self.assertEqual(source["sha256"], doc["sha256"])
        self.assertEqual(source["rowColumn"]["row"], 2)
        self.assertEqual(source["rowColumn"]["column"], 2)
        self.assertIn("Diluted earnings per share", source["excerpt"]["text"])
        self.assertEqual(diluted["splitNormalizationSource"], "financial-document.split.events")

    def test_scanner_does_not_stage_basic_eps_from_continuing_operations_section(self):
        body = ("<html><body><table><caption>USD</caption>"
                "<tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Net income attributable to Acme</td><td>1,000</td></tr>"
                "<tr><td>Earnings per share, diluted</td><td>0.25</td></tr>"
                "<tr><td colspan='2'>Continuing operations</td></tr>"
                "<tr><td>Earnings per share, basic</td><td>0.26</td></tr>"
                "</table></body></html>").encode()
        accession = "0000000001-25-000037"
        doc = self.document(accession, body=body)
        self.write_manifest([self.filing(accession, doc)])

        period = self.period(self.run_audit())

        self.assertEqual([row["field"] for row in period["stagedDecisions"]], ["epsDiluted"])
        diluted_candidates = period["fieldComparisons"]["epsDiluted"]["candidates"]
        self.assertTrue(diluted_candidates[0]["quality"]["candidateValidated"])

    def test_current_ticker_map_identity_does_not_promote_historical_period(self):
        doc = self.document("0000000001-25-000034")
        self.write_manifest([self.filing("0000000001-25-000034", doc)])
        manifest_path = self.manifests / "AAA.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["periods"][0]["identityFromTickerMap"] = True
        manifest["periods"][0]["issuerIdentityVerified"] = False
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])
        self.assertEqual(period["fieldComparisons"]["epsDiluted"]["status"], "manual-review")
        self.assertIn("historical-issuer-lineage-unverified",
                      period["fieldComparisons"]["epsDiluted"]["reason"])

    def test_exact_existing_value_is_staged_as_source_confirmation(self):
        self.write_financials(financial_document(diluted=0.25))
        self.write_manifest([self.filing("0000000001-25-000002", self.document("0000000001-25-000002"))],
                            snapshot={"epsDiluted": 0.25})

        report = self.run_audit()

        diluted = next(row for row in report["stagedDecisions"] if row["field"] == "epsDiluted")
        self.assertEqual(diluted["action"], "exact-source-confirmation")
        self.assertEqual(diluted["existingValue"], 0.25)
        self.assertEqual(diluted["proposedValue"], 0.25)

    def test_existing_value_conflict_is_unapplied_and_financial_json_stays_unchanged(self):
        self.write_financials(financial_document(diluted=0.33))
        original_financial_bytes = self.financial_path.read_bytes()
        self.write_manifest([self.filing("0000000001-25-000011", self.document("0000000001-25-000011"))],
                            snapshot={"epsDiluted": 0.33})

        period = self.period(self.run_audit())

        self.assertEqual([row["field"] for row in period["stagedDecisions"]], ["epsBasic"])
        diluted = period["fieldComparisons"]["epsDiluted"]
        self.assertEqual(diluted["status"], "manual-review")
        self.assertIn("conflicts with the existing", diluted["reason"])
        self.assertEqual(self.financial_path.read_bytes(), original_financial_bytes)

    def test_official_eps_is_staged_with_sign_mismatch_as_diagnostic(self):
        negative_net = RELEASE.replace("Net income", "Net loss").replace("1,000", "(1,000)")
        doc = self.document("0000000001-25-000012", body=negative_net.encode())
        self.write_manifest([self.filing("0000000001-25-000012", doc)])

        period = self.period(self.run_audit())

        diluted = next(row for row in period["stagedDecisions"] if row["field"] == "epsDiluted")
        self.assertEqual(diluted["proposedValue"], 0.25)
        self.assertEqual(diluted["diagnosticAdjudication"]["status"], "formula-diagnostic-only")
        self.assertIn("diluted-eps-sign-differs-from-net-income",
                      diluted["diagnosticAdjudication"]["flags"])
        diluted = period["fieldComparisons"]["epsDiluted"]
        self.assertEqual(diluted["status"], "staged")

    def test_ixbrl_usd_per_share_q4_duration_stages_diluted_with_context_evidence(self):
        accession = "0000000001-25-000013"
        doc = self.document(accession, body=IXBRL_RELEASE.encode(), name="primary.htm")
        self.write_manifest([self.filing(accession, doc, form="10-K")])

        period = self.period(self.run_audit())

        self.assertEqual({row["field"] for row in period["stagedDecisions"]}, {"epsDiluted"})
        self.assertEqual(period["fieldComparisons"]["epsBasic"]["status"], "manual-review")
        self.assertIn("inline-basic-not-corroborated-by-entity-total-visible-row",
                      period["fieldComparisons"]["epsBasic"]["candidates"][0]["quality"]["reviewFlags"])
        candidate = next(row for row in period["fieldComparisons"]["epsDiluted"]["candidates"])
        xbrl = candidate["provenance"]["ixbrl"]
        self.assertEqual(xbrl["namespace"], "us-gaap")
        self.assertEqual(xbrl["concept"], "EarningsPerShareDiluted")
        self.assertEqual(xbrl["unit"], "USD/shares")
        self.assertEqual(xbrl["durationDays"], 91)
        self.assertEqual(xbrl["contextRef"], "Q4")
        self.assertEqual(xbrl["factId"], "dilutedQ4")
        self.assertEqual(xbrl["entityIdentity"], {"scheme": "https://www.sec.gov/", "cik": "1"})

    def test_inline_basic_fact_cannot_override_class_specific_visible_basic_row(self):
        visible_table = ("<body><table><caption>USD</caption>"
                         "<tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                         "<tr><td>Net income attributable to Acme</td><td>1,000</td></tr>"
                         "<tr><td>Earnings per share, diluted</td><td>0.25</td></tr>"
                         "<tr><td>Class A</td></tr>"
                         "<tr><td>Earnings per share, basic</td><td>0.26</td></tr></table></body>")
        accession = "0000000001-25-000038"
        doc = self.document(accession, body=(visible_table + IXBRL_RELEASE).encode(), name="primary.htm")
        self.write_manifest([self.filing(accession, doc, form="10-K")])

        period = self.period(self.run_audit())

        self.assertEqual([row["field"] for row in period["stagedDecisions"]], ["epsDiluted"])
        basic = period["fieldComparisons"]["epsBasic"]
        self.assertEqual(basic["status"], "manual-review")
        inline = next(row for row in basic["candidates"] if "ixbrl" in row["provenance"])
        self.assertIn("inline-basic-has-visible-scoped-or-class-specific-row",
                      inline["quality"]["reviewFlags"])

    def test_inline_basic_and_visible_entity_total_row_must_agree(self):
        accession = "0000000001-25-000039"
        doc = self.document(accession, body=(RELEASE + IXBRL_RELEASE).encode(), name="primary.htm")
        self.write_manifest([self.filing(accession, doc, form="10-K")])

        period = self.period(self.run_audit())

        self.assertEqual({row["field"] for row in period["stagedDecisions"]}, {"epsBasic", "epsDiluted"})
        inline_basic = next(row for row in period["fieldComparisons"]["epsBasic"]["candidates"]
                            if "ixbrl" in row["provenance"])
        self.assertTrue(inline_basic["quality"]["candidateValidated"])
        self.assertTrue(inline_basic["provenance"]["ixbrl"]["visibleBasicRowMatched"])

    def test_ixbrl_context_for_another_cik_cannot_be_staged(self):
        body = IXBRL_RELEASE.replace('identifier scheme="https://www.sec.gov/">1<',
                                     'identifier scheme="https://www.sec.gov/">2<')
        accession = "0000000001-25-000031"
        doc = self.document(accession, body=body.encode(), name="primary.htm")
        self.write_manifest([self.filing(accession, doc, form="10-K")])

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])

    def test_ixbrl_custom_us_gaap_namespace_uri_cannot_be_staged(self):
        accession = "0000000001-25-000035"
        body = IXBRL_RELEASE.replace("http://fasb.org/us-gaap/2025", "https://example.com/us-gaap/2025").encode()
        doc = self.document(accession, body, name="ixbrl.htm")
        self.write_manifest([self.filing(accession, doc)])
        period = self.period(self.run_audit())
        self.assertEqual(period["stagedDecisions"], [])
        ambiguities = [message for item in period["filings"][0]["documents"]
                       for message in item.get("ambiguities", [])]
        self.assertTrue(any("namespace URI" in message for message in ambiguities))

    def test_ixbrl_sec_entity_scheme_hostname_is_exact(self):
        accession = "0000000001-25-000036"
        body = IXBRL_RELEASE.replace("https://www.sec.gov/", "https://www.sec.gov.example/").encode()
        doc = self.document(accession, body, name="ixbrl.htm")
        self.write_manifest([self.filing(accession, doc)])
        period = self.period(self.run_audit())
        self.assertEqual(period["stagedDecisions"], [])
        ambiguities = [message for item in period["filings"][0]["documents"]
                       for message in item.get("ambiguities", [])]
        self.assertEqual(period["fieldComparisons"]["epsDiluted"]["status"], "manual-review")
        self.assertTrue(any("does not match target CIK" in ambiguity for ambiguity in ambiguities))

    def test_complete_submission_txt_with_duplicate_context_ids_stays_pending(self):
        body = ("<DOCUMENT>" + IXBRL_RELEASE + "</DOCUMENT>"
                "<DOCUMENT><xbrli:context id=\"Q4\"><xbrli:period>"
                "<xbrli:startDate>2023-10-01</xbrli:startDate>"
                "<xbrli:endDate>2023-12-31</xbrli:endDate>"
                "</xbrli:period></xbrli:context></DOCUMENT>").encode()
        accession = "0000000001-25-000032"
        doc = self.document(accession, body=body, name="complete-submission.txt")
        self.write_manifest([self.filing(accession, doc, form="10-K")])

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])
        self.assertEqual(period["fieldComparisons"]["epsDiluted"]["status"], "manual-review")
        self.assertTrue(any("multiple DOCUMENT blocks" in ambiguity
                            for ambiguity in period["filings"][0]["documents"][0]["ambiguities"]))

    def test_ixbrl_dimensioned_or_non_usd_q4_fact_stays_pending(self):
        dimensioned = IXBRL_RELEASE.replace(
            "</xbrli:period></xbrli:context>",
            "</xbrli:period><xbrli:segment><xbrldi:explicitMember dimension=\"us-gaap:StatementClassOfStockAxis\">us-gaap:ClassACommonStockMember</xbrldi:explicitMember></xbrli:segment></xbrli:context>")
        accession = "0000000001-25-000014"
        doc = self.document(accession, body=dimensioned.encode(), name="primary.htm")
        self.write_manifest([self.filing(accession, doc, form="10-K")])

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])
        self.assertEqual(period["fieldComparisons"]["epsDiluted"]["status"], "manual-review")
        ambiguity = period["filings"][0]["documents"][0]["ambiguities"]
        self.assertTrue(any("dimensions" in row for row in ambiguity))

    def test_ixbrl_typed_member_and_empty_segment_are_not_entity_wide(self):
        dimensioned = IXBRL_RELEASE.replace(
            "</xbrli:period></xbrli:context>",
            "</xbrli:period><xbrli:segment><xbrldi:typedMember dimension=\"custom:ShareClassAxis\">"
            "<custom:ClassName>Class A</custom:ClassName></xbrldi:typedMember></xbrli:segment></xbrli:context>")
        accession = "0000000001-25-000023"
        doc = self.document(accession, body=dimensioned.encode(), name="primary.htm")
        self.write_manifest([self.filing(accession, doc, form="10-K")])

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])
        self.assertEqual(period["fieldComparisons"]["epsDiluted"]["status"], "manual-review")
        self.assertTrue(any("typed dimensions" in row or "segment/scenario" in row
                            for row in period["filings"][0]["documents"][0]["ambiguities"]))

    def test_missing_split_history_completeness_never_means_no_split(self):
        document = financial_document()
        document["split"].pop("eventsComplete")
        self.write_financials(document)
        accession = "0000000001-25-000024"
        doc = self.document(accession)
        self.write_manifest([self.filing(accession, doc)])

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])
        self.assertEqual(period["fieldComparisons"]["epsDiluted"]["status"], "manual-review")
        candidate = period["fieldComparisons"]["epsDiluted"]["candidates"][0]
        self.assertIn("split event history is missing", candidate["splitNormalization"]["reason"])

    def test_formula_diagnostic_does_not_require_second_sec_accession(self):
        changed_income = RELEASE.replace("1,000", "2,000")
        first_accession, second_accession = "0000000001-25-000015", "0000000001-25-000016"
        first = self.filing(first_accession, self.document(first_accession, changed_income.encode()))
        second = self.filing(second_accession, self.document(second_accession, changed_income.encode()),
                             filing_date="2025-03-01")
        self.write_manifest([first, second])

        period = self.period(self.run_audit())

        diluted = next(row for row in period["stagedDecisions"] if row["field"] == "epsDiluted")
        self.assertEqual(diluted["proposedValue"], 0.25)
        self.assertEqual(diluted["diagnosticAdjudication"]["status"],
                         "formula-diagnostic-only")
        self.assertIn("net-income-does-not-match-stored-quarter-value",
                      diluted["diagnosticAdjudication"]["flags"])

    def test_table_and_ixbrl_agreement_in_one_accession_is_a_single_official_source(self):
        changed_income = RELEASE.replace("1,000", "2,000") + IXBRL_RELEASE
        accession = "0000000001-25-000025"
        doc = self.document(accession, body=changed_income.encode(), name="primary.htm")
        self.write_manifest([self.filing(accession, doc, form="10-K")])

        period = self.period(self.run_audit())

        diluted = next(row for row in period["stagedDecisions"] if row["field"] == "epsDiluted")
        self.assertEqual(diluted["proposedValue"], 0.25)
        diluted = period["fieldComparisons"]["epsDiluted"]
        self.assertEqual(diluted["status"], "staged")
        self.assertEqual(diluted["diagnosticAdjudication"]["status"], "formula-diagnostic-only")

    def test_sha256_mismatch_is_reported_and_not_parsed_or_staged(self):
        body = RELEASE.encode()
        doc = self.document("0000000001-25-000003", body=body, expected_body=b"different")
        self.write_manifest([self.filing("0000000001-25-000003", doc)])

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])
        audited_doc = period["filings"][0]["documents"][0]
        self.assertEqual(audited_doc["status"], "hash-mismatch")
        self.assertIn("SHA-256", audited_doc["reason"])

    def test_pdf_is_marked_unreadable_separately(self):
        pdf = self.document("0000000001-25-000004", body=b"%PDF-1.7 synthetic", name="release.pdf")
        self.write_manifest([self.filing("0000000001-25-000004", pdf)])

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])
        self.assertEqual(period["filings"][0]["documents"][0]["status"], "pdf-unreadable")

    def test_disagreeing_sec_versions_are_unapplied(self):
        other_release = RELEASE.replace("0.25", "0.30").replace("0.26", "0.31").encode()
        first = self.filing("0000000001-25-000005", self.document("0000000001-25-000005"))
        second = self.filing("0000000001-25-000006", self.document("0000000001-25-000006", other_release),
                             filing_date="2025-03-01")
        self.write_manifest([first, second])

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])
        diluted = period["fieldComparisons"]["epsDiluted"]
        self.assertEqual(diluted["status"], "manual-review")
        self.assertIn("disagree", diluted["reason"])
        self.assertEqual(len(diluted["candidates"]), 2)

    def test_split_between_period_end_and_filing_date_leaves_candidate_pending(self):
        self.write_financials(financial_document(split_events=[["2025-01-15", 2]]))
        doc = self.document("0000000001-25-000007", filing_date="2025-02-15")
        self.write_manifest([self.filing("0000000001-25-000007", doc)])

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])
        diluted = period["fieldComparisons"]["epsDiluted"]
        self.assertEqual(diluted["status"], "manual-review")
        self.assertIn("basis is uncertain", diluted["reason"])
        candidate = diluted["candidates"][0]
        self.assertEqual(candidate["splitNormalization"]["source"], "financial-document.split.events")
        self.assertEqual(candidate["splitNormalization"]["status"], "pending")
        self.assertIsNone(candidate["normalizedValue"])

    def test_split_after_filing_is_normalized_from_independent_events(self):
        self.write_financials(financial_document(split_events=[["2025-03-15", 2]]))
        body = RELEASE.replace("0.25", "0.50").replace("0.26", "0.52").encode()
        doc = self.document("0000000001-25-000008", body=body, filing_date="2025-02-15")
        self.write_manifest([self.filing("0000000001-25-000008", doc)])

        report = self.run_audit()

        diluted = next(row for row in report["stagedDecisions"] if row["field"] == "epsDiluted")
        self.assertEqual(diluted["proposedValue"], 0.25)
        split = diluted["splitNormalization"][0]
        self.assertEqual(split["factor"], 2)
        self.assertEqual(split["status"], "normalized")
        self.assertIn("financial-document.split.events", diluted["splitNormalizationSource"])

    def test_table_ambiguity_and_no_result_are_reported_as_distinct_reasons(self):
        ambiguous = ("<html><table><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                     "<tr><td>Net income</td><td>1,000</td></tr>"
                     "<tr><td>Diluted earnings per share</td><td>0.25</td></tr>"
                     "<tr><td>Diluted EPS</td><td>0.25</td></tr></table></html>").encode()
        first = self.filing("0000000001-25-000009", self.document("0000000001-25-000009", ambiguous))
        second_html = RELEASE.replace("December 31, 2024", "September 30, 2024").encode()
        second = self.filing("0000000001-25-000010", self.document("0000000001-25-000010", second_html),
                             filing_date="2025-03-01")
        self.write_manifest([first, second])

        period = self.period(self.run_audit())

        docs = [filing["documents"][0] for filing in period["filings"]]
        self.assertEqual(docs[0]["status"], "ambiguous")
        self.assertIn("multiple GAAP diluted EPS rows", docs[0]["reason"])
        self.assertEqual(docs[1]["status"], "no-result")
        self.assertEqual(period["fieldComparisons"]["epsDiluted"]["status"], "manual-review")
        self.assertEqual(period["stagedDecisions"], [])

    def test_incomplete_historical_blocks_cannot_stage_an_otherwise_valid_candidate(self):
        accession = "0000000001-25-000020"
        doc = self.document(accession)
        filing = self.filing(accession, doc)
        filing["historyCoverageUnknown"] = True
        target = {
            "fy": 2024, "end": END, "cik": "0000000001", "issuerIdentityVerified": True,
            "existingValues": {},
            "sourceCoverageComplete": False, "sourceCoverageFresh": False,
            "sourceCoverageFailures": [{"name": "old.json", "status": "parse-error"}],
            "searchTrajectory": [{"stage": "submissions", "status": "fetched"},
                                 {"stage": "historical-blocks", "complete": False,
                                  "failures": [{"name": "old.json", "status": "parse-error"}]}],
            "candidateFilings": [filing],
        }
        manifest = {"schema": "q4-eps-sec-scan-v1", "candidateRulesVersion": audit.CANDIDATE_RULES_VERSION,
                    "symbol": "AAA", "periods": [target]}
        (self.manifests / "AAA.json").write_text(json.dumps(manifest), encoding="utf-8")

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])
        self.assertEqual(period["fieldComparisons"]["epsDiluted"]["status"], "manual-review")
        self.assertIn("at least one SEC candidate", period["fieldComparisons"]["epsDiluted"]["reason"])

    def test_archive_url_with_wrong_cik_or_accession_is_not_parsed(self):
        accession = "0000000001-25-000021"
        doc = self.document(accession)
        doc["url"] = doc["url"].replace("/data/1/", "/data/2/")
        self.write_manifest([self.filing(accession, doc)])

        period = self.period(self.run_audit())

        self.assertEqual(period["stagedDecisions"], [])
        self.assertEqual(period["filings"][0]["documents"][0]["status"], "source-identity-mismatch")

    def test_stale_scan_rule_version_is_rejected(self):
        self.write_manifest([])
        manifest_path = self.manifests / "AAA.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["candidateRulesVersion"] = audit.CANDIDATE_RULES_VERSION - 1
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        report = self.run_audit()

        self.assertEqual(report["stagedDecisions"], [])
        self.assertEqual(report["summary"]["errors"], 1)
        self.assertIn("unsupported", report["errors"][0]["reason"])

    def test_basic_only_official_q4_table_stages_basic_without_diluted(self):
        accession = "0000000001-25-000022"
        html = ("<html><table><caption>USD</caption><tr><th></th><th>Three Months Ended December 31, 2024</th></tr>"
                "<tr><td>Basic earnings per share</td><td>0.13</td></tr></table></html>").encode()
        self.write_manifest([self.filing(accession, self.document(accession, html))])

        report = self.run_audit()

        self.assertEqual([(row["field"], row["proposedValue"]) for row in report["stagedDecisions"]],
                         [("epsBasic", 0.13)])


if __name__ == "__main__":
    unittest.main()

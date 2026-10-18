"""Offline fixtures for stage-two selection, evidence gates, and SEC request controls."""

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
STAGE = ROOT / "docs/report/sec-q4-eps-stage2"
sys.path.insert(0, str(STAGE))
import stage2_poc as stage2  # noqa: E402


class SelectionTests(unittest.TestCase):
    def test_stratified_selector_balances_year_cells_and_unique_ciks(self):
        rows = []
        for year in (2012, 2013, 2014):
            for index in range(7):
                rows.append({"symbol": f"T{year}{index}", "cik": f"{year}{index:03d}",
                             "period_end_year": year, "fy": year,
                             "q4_end": f"{year}-12-31"})
        used = set()
        selected, strata = stage2._pick_primary_rows("fixture-class", rows, used, "fixed-seed", 9)
        self.assertEqual(len(selected), 9)
        self.assertEqual(len({row["cik"] for row in selected}), 9)
        self.assertEqual(strata["selected"], {"2012": 3, "2013": 3, "2014": 3})

    def test_committed_sample_rebuild_is_reproducible_and_values_are_not_in_controls(self):
        path = STAGE / "sample.json"
        if not path.exists():
            self.skipTest("the sample is frozen by the pre-query commit")
        sample = stage2.load_json(path)
        rebuilt = stage2.make_sample(sample["selection_seed"])
        self.assertEqual(sample["freeze_sha256"], rebuilt["freeze_sha256"])
        self.assertEqual(len(sample["missing_companies"]), 60)
        self.assertEqual(len(sample["controls"]), 60)
        self.assertEqual({row["class"] for row in sample["missing_companies"]}, set(stage2.MISSING_CLASS_ORDER))
        self.assertTrue(all(len(company["periods"]) in (1, 2) for company in sample["missing_companies"]))
        forbidden = {"value", "stored", "netIncome", "accession", "filed"}
        self.assertTrue(all(not forbidden.intersection(row) for row in sample["controls"]))
        stage2.check_sample(sample)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = (STAGE / "tests/fixtures/direct_q4_release.html").read_bytes()
        self.target = {"q4_end": "2020-12-31", "q4_start_estimate": "2020-10-01"}
        self.filing = {"form": "8-K", "accession": "0000000000-20-000001", "filing_date": "2021-02-10"}
        self.url = "https://www.sec.gov/Archives/edgar/data/1/000000000020000001/ex99-1.htm"

    def candidates(self, body=None, target=None):
        return stage2.table_eps_candidates(
            body or self.fixture, target or self.target, source_url=self.url,
            filing=self.filing, split_events=[("2021-05-01", 0.5)],
        )

    def test_dated_three_month_gaap_diluted_q4_cell_is_verified(self):
        rows = self.candidates()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["verified"], rows[0]["issues"])
        self.assertEqual(rows[0]["value"], 1.23)
        self.assertEqual(rows[0]["locator"]["column_index"], 1)
        self.assertEqual(rows[0]["split_basis"]["future_split_factor_after_filing"], 0.5)

    def test_annual_comparative_is_not_mistaken_for_q4(self):
        annual_only = self.fixture.replace(b"Three Months Ended", b"Twelve Months Ended")
        self.assertEqual(self.candidates(annual_only), [])

    def test_wrong_end_date_is_not_accepted(self):
        wrong_end = self.fixture.replace(b"December 31, 2020", b"September 30, 2020", 1)
        self.assertEqual(self.candidates(wrong_end), [])

    def test_basic_only_and_adjusted_eps_are_not_accepted(self):
        basic = self.fixture.replace(b"diluted common share", b"basic common share")
        self.assertEqual(self.candidates(basic), [])
        adjusted = self.fixture.replace(b"Net income per diluted", b"Adjusted non-GAAP diluted")
        self.assertEqual(self.candidates(adjusted), [])

    def test_release_table_requires_consolidated_gaap_and_common_share_evidence(self):
        no_scope = self.fixture.replace(b"Consolidated Statements", b"Statements").replace(b"(GAAP)", b"")
        result = self.candidates(no_scope)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["verified"])
        self.assertIn("consolidated_scope_unverified", result[0]["issues"])
        self.assertIn("gaap_statement_basis_unverified", result[0]["issues"])

    def test_continuing_operations_eps_is_not_accepted_as_total_q4_eps(self):
        continuing = self.fixture.replace(
            b"Net income per diluted common share",
            b"Income from continuing operations per diluted common share",
        )
        self.assertEqual(self.candidates(continuing), [])

    def test_dimensioned_segment_fact_is_only_a_candidate(self):
        parsed = {"eps_facts": [{
            "concept": "us-gaap:EarningsPerShareDiluted",
            "context": {
                "start": "2020-10-01", "end": "2020-12-31",
                "dimensions": [{"axis": "us-gaap:StatementBusinessSegmentsAxis",
                                "member": "ex:RetailMember"}],
            },
            "unit_measures": ["iso4217:USD", "xbrli:shares"],
            "unit": "USD", "value": 9.99,
            "row_text": "Net income per diluted common share",
            "table_text": "Consolidated Statements of Operations",
        }]}
        rows = stage2.fact_eps_candidates(
            parsed, {**self.target, "q4_end_matches_local_annual_end": True},
            source_url=self.url, filing=self.filing, split_events=[],
        )
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["verified"])
        self.assertIn("dimensioned_eps_scope_unresolved", rows[0]["issues"])

    def test_continuing_operations_fact_is_not_verified_total_eps(self):
        parsed = {"eps_facts": [{
            "concept": "us-gaap:IncomeLossFromContinuingOperationsPerDilutedShare",
            "context": {"start": "2020-10-01", "end": "2020-12-31", "dimensions": []},
            "unit_measures": ["iso4217:USD", "xbrli:shares"],
            "unit": "USD", "value": 9.99,
            "row_text": "Income from continuing operations per diluted share",
            "table_text": "Consolidated Statements of Operations",
        }]}
        rows = stage2.fact_eps_candidates(
            parsed, {**self.target, "q4_end_matches_local_annual_end": True},
            source_url=self.url, filing=self.filing, split_events=[],
        )
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["verified"])
        self.assertIn("total_net_income_eps_basis_unverified", rows[0]["issues"])


class ExtractionFailureTests(unittest.TestCase):
    def test_eight_k_index_failure_always_returns_three_values(self):
        class FakeClient:
            def get(self, _url, *, context):
                return {"status": 404, "body": b"", "error": None, "cache_hit": False}

        result = stage2.inspect_eight_k(
            FakeClient(), {"accession": "0000000000-20-000001"},
            {"q4_end": "2020-12-31", "split_events": []},
            {"cik": "0000000001", "symbol": "TST"}, [],
        )
        self.assertEqual(result, ([], "technical_failure", []))

    def test_8k_candidates_receive_local_split_events(self):
        class FakeClient:
            def get(self, _url, *, context):
                return {"status": 200, "body": json.dumps({"directory": {"item": [
                    {"name": "ex99-1.htm", "type": "EX-99.1", "description": "Earnings release"},
                ]}}).encode(), "error": None, "cache_hit": False}

        captured = []

        def fake_doc(*_args, **kwargs):
            captured.append(kwargs["split_events"])
            return ({"status": 200, "body": b"fixture"}, [], "parsed")

        with patch.object(stage2, "_url_doc", side_effect=fake_doc):
            result = stage2.inspect_eight_k(
                FakeClient(), {"accession": "0000000000-20-000001", "primary_document": "primary.htm"},
                {"q4_end": "2020-12-31", "split_events": [["2021-05-01", 0.5]]},
                {"cik": "0000000001", "symbol": "TST"}, [],
            )
        self.assertEqual(len(result), 3)
        self.assertEqual(captured, [[["2021-05-01", 0.5]], [["2021-05-01", 0.5]]])

    def test_initial_transport_failure_keeps_every_frozen_period_as_E(self):
        sample = stage2.load_json(STAGE / "sample.json")
        targets = stage2.target_rows(sample)
        payload = {"period_results": [stage2._empty_period_result(row) for row in targets]}
        stage2._mark_pending_as_technical_failure(payload, "first 403")
        self.assertEqual(len(payload["period_results"]), len(targets))
        self.assertTrue(all(row["status"] == "E_technical_failure" for row in payload["period_results"]))

    def test_real_sec_fixture_uses_existing_poc_evidence_gate_without_network(self):
        fixture_path = ROOT / "tests/fixtures/sec_q4_eps_poc/fgc_2016_q4_release.json"
        if not fixture_path.exists():
            self.skipTest("existing real SEC fixture is not available in this checkout")
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        candidate = stage2.poc.assess_inline_eps_fact(
            fixture["diluted_eps_fact"], fixture["q4_target"]
        )
        self.assertTrue(candidate["verified"], candidate["issues"])
        self.assertEqual(candidate["value"], 0.27)


class RequestGuardTests(unittest.TestCase):
    def test_hard_cap_blocks_before_any_wire_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            guard = stage2.GuardedOpener(cap=0, ledger_path=Path(temporary) / "ledger.jsonl")
            with self.assertRaises(stage2.RequestCapReached):
                guard(None)
            self.assertEqual(guard.attempts, 0)

    def test_first_403_is_recorded_and_stops_immediately(self):
        class FakeOpener:
            attempts = 0

            def __call__(self, request, timeout=60):
                self.attempts += 1
                raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b"blocked"))

        fake = FakeOpener()
        with tempfile.TemporaryDirectory() as temporary:
            client = stage2.Stage2SecClient(cache_dir=temporary, opener=fake, sleeper=lambda _: None)
            with self.assertRaises(stage2.SEC403):
                client.get("https://www.sec.gov/Archives/test", context={"kind": "fixture"})
        self.assertEqual(fake.attempts, 1)
        self.assertEqual(client.ledger[0]["http_status"], 403)
        self.assertEqual(client.first_403["url"], "https://www.sec.gov/Archives/test")


if __name__ == "__main__":
    unittest.main()

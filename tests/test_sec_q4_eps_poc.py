"""Unit tests for the isolated SEC Q4 EPS POC's conservative acceptance gates."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sec_q4_eps_poc as poc  # noqa: E402


def direct_fact(*, concept="us-gaap:EarningsPerShareDiluted", start="2020-10-01",
                end="2020-12-31", value=1.23, row="Net income per diluted share",
                table="Net income per diluted share", dimensions=None):
    return {
        "concept": concept,
        "context": {"start": start, "end": end, "dimensions": dimensions or []},
        "unit_measures": ["iso4217:USD", "xbrli:shares"],
        "value": value,
        "row_text": row,
        "table_text": table,
    }


def target(**overrides):
    result = {
        "q4_end": "2020-12-31",
        "q4_start_estimate": "2020-10-01",
        "q4_end_matches_local_annual_end": True,
    }
    result.update(overrides)
    return result


class SecQ4EpsPocTests(unittest.TestCase):
    def test_q4_duration_is_required_and_fy_is_rejected(self):
        fact = direct_fact(start="2020-01-01", end="2020-12-31")
        result = poc.assess_inline_eps_fact(fact, target())
        self.assertFalse(result["verified"])
        self.assertIn("not_single_quarter_duration", result["issues"])

    def test_basic_eps_cannot_pass_as_diluted(self):
        fact = direct_fact(concept="us-gaap:EarningsPerShareBasic")
        result = poc.assess_inline_eps_fact(fact, target())
        self.assertFalse(result["verified"])
        self.assertIn("basic_only_or_dilution_unverified", result["issues"])

    def test_adjusted_or_non_gaap_table_cannot_pass_as_gaap(self):
        fact = direct_fact(row="Adjusted diluted earnings per share",
                           table="Non-GAAP adjusted EPS reconciliation")
        result = poc.assess_inline_eps_fact(fact, target())
        self.assertFalse(result["verified"])
        self.assertIn("non_gaap_context_possible", result["issues"])

    def test_comparative_prior_year_fact_does_not_match_target_end(self):
        fact = direct_fact(start="2019-10-01", end="2019-12-31")
        result = poc.assess_inline_eps_fact(fact, target())
        self.assertFalse(result["verified"])
        self.assertIn("end_date_mismatch", result["issues"])

    def test_split_basis_is_normalized_before_comparing_values(self):
        event = [("2021-04-01", 0.5)]
        adjusted = poc.eps_on_current_split_basis(1.2, "2020-12-31", event)
        self.assertEqual(adjusted, 2.4)
        self.assertEqual(poc.compare_positive_control(adjusted, 2.4)["status"], "consistent")
        fact = direct_fact()
        result = poc.assess_inline_eps_fact(fact, target(), split_adjustment_verified=False)
        self.assertIn("split_basis_unresolved", result["issues"])

    def test_class_dimension_must_match_the_requested_share_class(self):
        fact = direct_fact(dimensions=[{
            "axis": "us-gaap:StatementClassOfStockAxis",
            "member": "issuer:ClassBCommonStockMember",
        }])
        unresolved = poc.assess_inline_eps_fact(fact, target())
        self.assertIn("share_class_unresolved", unresolved["issues"])
        mismatch = poc.assess_inline_eps_fact(fact, target(), expected_class="ClassA")
        self.assertIn("wrong_share_class", mismatch["issues"])

    def test_10k_and_amendment_value_conflict_is_not_silently_accepted(self):
        original = [direct_fact(value=1.23)]
        amendment = [direct_fact(value=1.20)]
        result = poc.compare_filing_versions(original, amendment, target())
        self.assertEqual(result["status"], "version_conflict")

    def test_real_sec_10k_and_10ka_versions_remain_incomplete_if_amendment_fact_was_not_extracted(self):
        fixture = self.fixture("tdw_2019_10k_amendment_versions.json")
        original = fixture["original_10k"]["q4_fact"]
        result = poc.compare_filing_versions(
            [original], [], {
                "q4_end": fixture["target_q4_end"],
                "q4_start_estimate": fixture["target_q4_start"],
            }
        )
        self.assertEqual(fixture["original_10k"]["accession"], "0001564590-20-008191")
        self.assertEqual(fixture["amended_10ka"]["accession"], "0001564590-20-019722")
        self.assertEqual(result["status"], "one_version_missing_direct_q4_fact")

    def test_empty_zip_accession_falls_back_to_original_filing(self):
        status = poc.dataset_fallback_status(200, "parsed", accession_present=False)
        self.assertEqual(status, "fallback_to_next_source")
        self.assertNotIn("D", status)

    def test_network_failure_is_technical_e_not_no_disclosure(self):
        self.assertEqual(poc.dataset_fallback_status(503, "fetch_failed", False), "E_technical_failure")
        self.assertEqual(poc.dataset_fallback_status(403, "fetch_failed", False), "E_permission_error")

    def test_positive_control_requires_exact_sec_value_match(self):
        self.assertEqual(poc.compare_positive_control(-0.58, -0.58)["status"], "consistent")
        self.assertEqual(poc.compare_positive_control(-0.58, -0.60)["status"], "mismatch")
        self.assertEqual(poc.compare_positive_control(None, -0.58)["status"],
                         "unverifiable_missing_value")

    def test_frozen_positive_controls_are_not_claimed_as_grade_a_without_the_source_ledger(self):
        sample = json.loads((ROOT / "docs/report/sec-q4-eps-poc/sample.json").read_text(encoding="utf-8"))
        controls = [row for row in sample["sample"] if row["sample_role"] == "positive_control_candidate"]
        self.assertEqual({row["ticker"] for row in controls}, {"NMTC", "TDW"})
        self.assertTrue(all(row["historical_grade_a_status"].startswith("not_auditable:")
                            for row in controls))
        self.assertIn("docs/report/q4-eps-acceptance-ledger.json", sample["input_audit"]["missing_inputs"])

    def release_disclosure(self, fixture):
        return {
            key: fixture[key] for key in (
                "measure_label", "statement_scope", "period_columns", "row_values",
                "gaap_statement_basis_verified", "consolidated_scope_verified",
                "share_class", "split_review_complete", "accession", "source_url",
            )
        }

    def release_target(self, fixture):
        return {
            "q4_end": fixture["q4_end"],
            "q4_start_estimate": fixture["q4_start"],
            "q4_end_matches_local_annual_end": True,
        }

    def test_real_sec_release_tables_select_three_month_q4_column_not_fy_or_prior_year(self):
        for filename, accession, expected in (
            ("fgc_2022_q4_release.json", "0001493152-23-008865", -0.03),
            ("wrap_2022_q4_release.json", "0001437749-23-005259", -0.09),
        ):
            with self.subTest(filename=filename):
                fixture = self.fixture(filename)
                result = poc.assess_release_eps_disclosure(
                    self.release_disclosure(fixture), self.release_target(fixture)
                )
                self.assertEqual(fixture["accession"], accession)
                self.assertTrue(result["verified"], result["issues"])
                self.assertEqual(result["selected_q4_column"], 0)
                self.assertEqual(result["value"], expected)
                self.assertNotEqual(result["value"], float(fixture["row_values"][2].replace("(", "-").replace(")", "")))

    def test_real_sec_8k_excerpts_retain_diluted_eps_and_table_period_headers(self):
        fgc = self.fixture("fgc_2022_q4_release.json")
        parsed_fgc = poc.parse_inline_filing(fgc["source_html_excerpt"].encode("utf-8"))
        self.assertIn("per fully diluted share", " ".join(hit["term"] for hit in parsed_fgc["keyword_hits"]))
        fgc_hits = " ".join(hit["excerpt"] for hit in parsed_fgc["keyword_hits"])
        self.assertIn("fourth quarter of 2022", fgc_hits)
        self.assertIn("Basic and diluted net earnings (loss) per common share", fgc_hits)

        wrap = self.fixture("wrap_2022_q4_release.json")
        parsed_wrap = poc.parse_inline_filing(wrap["source_html_excerpt"].encode("utf-8"))
        self.assertTrue(any("Three Months Ended December 31" in table["text"]
                            and "Twelve Months Ended December 31" in table["text"]
                            and "Net loss per basic and diluted common share" in table["text"]
                            for table in parsed_wrap["eps_tables"]))

    def test_generic_sec_exhibit_link_and_nontext_image_are_not_missed_or_html_parsed(self):
        fixture = self.fixture("wrap_2022_q4_release.json")
        index_items = [{"name": "ex_483215.htm", "type": "text.gif", "description": ""}]
        selected = poc.linked_results_exhibits(
            fixture["primary_link_excerpt"].encode("utf-8"), index_items
        )
        self.assertEqual([item["name"] for item in selected], ["ex_483215.htm"])
        self.assertTrue(poc.likely_results_exhibit("ofg8K20171231EX99.htm"))
        self.assertTrue(poc.is_nontext_payload("scan.jpg", b"\xff\xd8\xff\xe0rest"))

    def test_release_verifier_rejects_wrong_fiscal_period_basic_only_non_gaap_and_split_conflicts(self):
        fixture = self.fixture("wrap_2022_q4_release.json")
        disclosure = self.release_disclosure(fixture)
        wrong_period = poc.assess_release_eps_disclosure(
            disclosure, {**self.release_target(fixture), "q4_end": "2022-09-30"}
        )
        self.assertFalse(wrong_period["verified"])
        self.assertIn("single_q4_column_not_resolved", wrong_period["issues"])

        basic_only = {**disclosure, "measure_label": "Net loss per basic common share"}
        self.assertIn("basic_only_or_dilution_unverified",
                      poc.assess_release_eps_disclosure(basic_only, self.release_target(fixture))["issues"])
        adjusted = {**disclosure, "measure_label": "Adjusted non-GAAP net loss per basic and diluted common share"}
        self.assertIn("non_gaap_context_possible",
                      poc.assess_release_eps_disclosure(adjusted, self.release_target(fixture))["issues"])
        split_unresolved = {**disclosure, "split_review_complete": False}
        self.assertIn("split_basis_unresolved",
                      poc.assess_release_eps_disclosure(split_unresolved, self.release_target(fixture))["issues"])
        wrong_class = {**disclosure, "share_class": "preferred"}
        self.assertIn("share_class_unresolved",
                      poc.assess_release_eps_disclosure(wrong_class, self.release_target(fixture))["issues"])

    def fixture(self, name):
        path = ROOT / "tests/fixtures/sec_q4_eps_poc" / name
        return json.loads(path.read_text(encoding="utf-8"))

    def test_real_sec_q4_fixture_passes_only_for_actual_fiscal_q4(self):
        fixture = self.fixture("tdw_2019_q4.json")
        self.assertEqual(fixture["accession"], "0001564590-20-008191")
        self.assertIn("www.sec.gov/Archives/edgar/data/", fixture["source_url"])
        actual = poc.assess_inline_eps_fact(fixture["fact"], fixture["actual_q4_target"])
        self.assertTrue(actual["verified"], actual["issues"])
        self.assertEqual(actual["value"], -1.52)
        local_period = poc.assess_inline_eps_fact(fixture["fact"], fixture["frozen_local_q4_target"])
        self.assertFalse(local_period["verified"], "the frozen March period is Q1, not fiscal Q4")
        self.assertIn("local_fiscal_period_mapping_mismatch", local_period["issues"])
        same_date_local = fixture["database_row_with_same_end_date"]
        comparison = poc.compare_positive_control(actual["value"], same_date_local["epsDiluted"])
        self.assertEqual(comparison["status"], "consistent")
        self.assertEqual((same_date_local["fy"], same_date_local["fq"]), (2020, 3))

    def test_real_sec_non_inline_q4_disclosures_pass_with_source_evidence(self):
        fixtures = [
            ("astc_2014_q4_release.json", "0001571049-14-004870", -0.04),
            ("fgc_2016_q4_release.json", "0001387131-17-001425", 0.27),
            ("ofg_2017_q4_table.json", "0001030469-18-000021", 0.30),
            ("ofg_2020_q4_narrative.json", "0001030469-21-000009", 0.42),
        ]
        for filename, accession, expected in fixtures:
            with self.subTest(filename=filename):
                fixture = self.fixture(filename)
                self.assertEqual(fixture["accession"], accession)
                self.assertTrue(fixture["source_url"].startswith("https://www.sec.gov/Archives/"))
                result = poc.assess_inline_eps_fact(
                    fixture["diluted_eps_fact"], fixture["q4_target"]
                )
                self.assertTrue(result["verified"], result["issues"])
                self.assertEqual(result["value"], expected)

    def test_real_sec_ofg_2020_q4_disclosure_is_direct_and_not_adjusted(self):
        fixture = self.fixture("ofg_2020_q4_narrative.json")
        result = poc.assess_inline_eps_fact(
            fixture["diluted_eps_fact"], fixture["q4_target"]
        )
        self.assertTrue(result["verified"], result["issues"])
        self.assertEqual(result["value"], 0.42)
        corroboration = fixture["corroborating_source"]
        self.assertEqual(corroboration["accession"], "0001030469-21-000012")
        self.assertIn("$0.42 in 4Q20", corroboration["text_excerpt"])
        self.assertEqual(
            poc.compare_positive_control(result["value"], fixture["same_period_database_value"])["status"],
            "unverifiable_missing_value",
        )

    def test_annual_non_gaap_figure_in_real_release_is_not_q4_eps(self):
        fixture = self.fixture("fgc_2016_q4_release.json")
        annual_non_gaap = direct_fact(
            concept="custom:ReleaseBasicAndDilutedEps",
            start="2016-01-01",
            end="2016-12-31",
            value=fixture["excluded_non_gaap_annual_candidate"]["value"],
            row="Earnings per share before catastrophe events",
            table="Non-GAAP annual reconciliation before catastrophe events",
        )
        result = poc.assess_inline_eps_fact(annual_non_gaap, target(
            q4_end="2016-12-31", q4_start_estimate="2016-10-01"
        ))
        self.assertFalse(result["verified"])
        self.assertIn("not_single_quarter_duration", result["issues"])
        self.assertIn("non_gaap_context_possible", result["issues"])


if __name__ == "__main__":
    unittest.main()

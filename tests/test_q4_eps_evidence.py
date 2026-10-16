"""Synthetic, offline tests for auditable Q4 EPS evidence staging."""
import os
import sys
import unittest

from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import q4_eps_evidence as evidence  # noqa: E402


CIK = "0000001234"
COMPANYFACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"


def eps_fact(value, *, concept, filed="2024-02-15", accn="0000001234-24-000001",
             form="10-K", start="2023-10-01", end="2023-12-31"):
    return {"val": value, "start": start, "end": end, "filed": filed,
            "form": form, "accn": accn}


def companyfacts(*, basic=(), diluted=(), other_units=None, cik=1234):
    basic_units = {"USD/shares": list(basic)}
    diluted_units = {"USD/shares": list(diluted)}
    if other_units:
        basic_units.update(other_units)
    return {"cik": cik, "facts": {"us-gaap": {
        "EarningsPerShareBasic": {"units": basic_units},
        "EarningsPerShareDiluted": {"units": diluted_units},
    }}}


def direct_checks(*, basic_income=250, diluted_income=250, basis="current"):
    return {
        "epsBasic": {
            "income": {"value": basic_income, "unit": "USD", "attribution": "common"},
            "shares": {"value": 1000, "unit": "shares", "basis": basis},
        },
        "epsDiluted": {
            "income": {"value": diluted_income, "unit": "USD", "attribution": "common"},
            "shares": {"value": 1000, "unit": "shares", "basis": basis},
        },
    }


def reconstruction_fact(value, *, concept, unit, start, end, form, filed, accn,
                        cik=CIK, **metadata):
    fact = {
        "cik": cik,
        "concept": concept,
        "unit": unit,
        "val": value,
        "start": start,
        "end": end,
        "filed": filed,
        "form": form,
        "accn": accn,
        "url": COMPANYFACTS_URL,
    }
    fact.update(metadata)
    return fact


def basic_inputs(*, annual_income=365, nine_income=273,
                 annual_shares=1000, nine_shares=1000,
                 annual_income_concept="NetIncomeLossAvailableToCommonStockholdersBasic",
                 nine_income_concept="NetIncomeLossAvailableToCommonStockholdersBasic",
                 annual_share_basis="post-split-basis-1",
                 nine_share_basis="post-split-basis-1",
                 annual_income_unit="USD", nine_income_unit="USD",
                 annual_share_unit="shares", nine_share_unit="shares",
                 annual_income_metadata=None, nine_income_metadata=None,
                 annual_share_metadata=None, nine_share_metadata=None):
    annual_period = ("2023-01-01", "2023-12-31")
    nine_period = ("2023-01-01", "2023-09-30")
    annual_version = {"form": "10-K", "filed": "2024-02-15", "accn": "0000001234-24-000001"}
    nine_version = {"form": "10-Q", "filed": "2023-11-03", "accn": "0000001234-23-000004"}
    common_metadata = {
        "incomeAttribution": "common",
        "preferredDividendsStatus": "included",
        "noncontrollingInterestStatus": "excluded",
    }
    share_metadata = {"shareClass": "common", "shareClassCount": 1}
    common_metadata.update(annual_income_metadata or {})
    nine_common = dict(common_metadata)
    nine_common.update(nine_income_metadata or {})
    annual_share_metadata = {**share_metadata, **(annual_share_metadata or {})}
    nine_share_metadata = {**share_metadata, **(nine_share_metadata or {})}
    return {
        "annual_income_facts": [reconstruction_fact(
            annual_income, concept=annual_income_concept, unit=annual_income_unit,
            start=annual_period[0], end=annual_period[1], **annual_version, **common_metadata)],
        "nine_month_income_facts": [reconstruction_fact(
            nine_income, concept=nine_income_concept, unit=nine_income_unit,
            start=nine_period[0], end=nine_period[1], **nine_version, **nine_common)],
        "annual_basic_share_facts": [reconstruction_fact(
            annual_shares, concept="WeightedAverageNumberOfSharesOutstandingBasic",
            unit=annual_share_unit, start=annual_period[0], end=annual_period[1],
            shareBasis=annual_share_basis, **annual_version, **annual_share_metadata)],
        "nine_month_basic_share_facts": [reconstruction_fact(
            nine_shares, concept="WeightedAverageNumberOfSharesOutstandingBasic",
            unit=nine_share_unit, start=nine_period[0], end=nine_period[1],
            shareBasis=nine_share_basis, **nine_version, **nine_share_metadata)],
    }


def reconstruct(**overrides):
    inputs = basic_inputs(**overrides)
    return evidence.reconstruct_basic_q4_eps(
        symbol="SYN", cik=CIK, fiscal_year=2023,
        fiscal_start="2023-01-01", nine_month_end="2023-09-30",
        q4_start="2023-10-01", fiscal_end="2023-12-31",
        target_share_basis="post-split-basis-1", **inputs)


class DirectOfficialFactTests(unittest.TestCase):
    def test_exact_usd_shares_duration_and_end_window_are_required(self):
        valid = eps_fact(0.25, concept="EarningsPerShareBasic")
        annual = eps_fact(2.0, concept="EarningsPerShareBasic", start="2023-01-01")
        short = eps_fact(0.2, concept="EarningsPerShareBasic", start="2023-11-15")
        outside_end = eps_fact(0.2, concept="EarningsPerShareBasic", end="2024-01-06")
        payload = companyfacts(basic=[valid, annual, short, outside_end], other_units={
            "USD / shares": [eps_fact(99, concept="EarningsPerShareBasic")],
            "USD": [eps_fact(88, concept="EarningsPerShareBasic")],
        })
        extracted = evidence.extract_direct_q4_candidates(
            payload, cik=CIK, period_end="2023-12-31", companyfacts_url=COMPANYFACTS_URL)
        self.assertEqual([item["rawValue"] for item in extracted["epsBasic"]], ["0.25"])
        self.assertEqual(extracted["epsDiluted"], [])

    def test_latest_filing_version_is_selected_and_full_provenance_is_kept(self):
        older = eps_fact("0.24", concept="EarningsPerShareBasic",
                         filed="2024-02-01", accn="0000001234-24-000001")
        latest = eps_fact("0.25", concept="EarningsPerShareBasic",
                          filed="2024-03-01", accn="0000001234-24-000002", form="10-K/A")
        payload = companyfacts(basic=[older, latest])
        extracted = evidence.extract_direct_q4_candidates(
            payload, cik=CIK, period_end="2023-12-31", companyfacts_url=COMPANYFACTS_URL)
        selected, reason = evidence.select_direct_q4_candidate(
            extracted["epsBasic"], period_start="2023-10-01", period_end="2023-12-31")
        self.assertIsNone(reason)
        self.assertEqual(selected["rawValue"], "0.25")
        self.assertEqual(selected["accession"], "0000001234-24-000002")
        self.assertEqual(selected["filed"], "2024-03-01")
        self.assertEqual(selected["form"], "10-K/A")
        self.assertEqual(selected["url"], COMPANYFACTS_URL)
        self.assertTrue(selected["filingUrl"].startswith("https://www.sec.gov/Archives/"))
        self.assertEqual(selected["basis"], "as-reported-at-filing")
        self.assertEqual(selected["version"]["accession"], "0000001234-24-000002")

    def test_conflicting_values_in_one_filing_version_fail_closed(self):
        first = eps_fact("0.25", concept="EarningsPerShareBasic")
        second = eps_fact("0.35", concept="EarningsPerShareBasic")
        extracted = evidence.extract_direct_q4_candidates(
            companyfacts(basic=[first, second]), cik=CIK, period_end="2023-12-31",
            companyfacts_url=COMPANYFACTS_URL)
        selected, reason = evidence.select_direct_q4_candidate(
            extracted["epsBasic"], period_start="2023-10-01", period_end="2023-12-31")
        self.assertIsNone(selected)
        self.assertEqual(reason, "conflicting-facts-in-selected-filing-version")

    def test_direct_grade_a_requires_cross_check_and_adjusts_only_on_given_split_basis(self):
        payload = companyfacts(
            basic=[eps_fact("0.50", concept="EarningsPerShareBasic")],
            diluted=[eps_fact("0.50", concept="EarningsPerShareDiluted")],
        )
        staged = evidence.stage_direct_q4_eps(
            payload, symbol="SYN", cik=CIK, period_start="2023-10-01",
            period_end="2023-12-31", companyfacts_url=COMPANYFACTS_URL,
            cross_checks=direct_checks(), split_multiplier="0.5", target_share_basis="current")
        self.assertEqual(staged["epsBasic"]["grade"], "A")
        self.assertEqual(staged["epsBasic"]["value"], "0.250")
        self.assertEqual(staged["epsDiluted"]["grade"], "A")
        self.assertEqual(staged["epsBasic"]["source"]["basis"]["splitMultiplier"], "0.5")
        self.assertEqual(staged["epsBasic"]["source"]["concept"], "EarningsPerShareBasic")
        self.assertEqual(staged["epsDiluted"]["source"]["concept"], "EarningsPerShareDiluted")

        failed = evidence.stage_direct_q4_eps(
            payload, symbol="SYN", cik=CIK, period_start="2023-10-01",
            period_end="2023-12-31", companyfacts_url=COMPANYFACTS_URL,
            cross_checks=direct_checks(basic_income=-250), split_multiplier=1,
            target_share_basis="current")
        self.assertIsNone(failed["epsBasic"]["grade"])
        self.assertEqual(failed["epsBasic"]["validation"]["failureReason"],
                         "cross-check-sign-mismatch")

    def test_wrong_issuer_or_nonofficial_companyfacts_url_is_rejected(self):
        payload = companyfacts()
        with self.assertRaisesRegex(evidence.EvidenceError, "companyfacts-cik-mismatch"):
            evidence.extract_direct_q4_candidates(
                payload, cik="0000009999", period_end="2023-12-31",
                companyfacts_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000009999.json")
        with self.assertRaisesRegex(evidence.EvidenceError, "nonofficial"):
            evidence.extract_direct_q4_candidates(
                payload, cik=CIK, period_end="2023-12-31",
                companyfacts_url="https://example.test/CIK0000001234.json")


class BasicReconstructionTests(unittest.TestCase):
    def test_same_concept_common_income_and_actual_day_weighted_basic_shares_pass(self):
        staged = reconstruct()
        self.assertEqual(staged["grade"], "B")
        self.assertEqual(staged["status"], "validated")
        self.assertEqual(staged["field"], "epsBasic")
        self.assertEqual(staged["calculation"]["q4CommonIncome"], "92")
        self.assertEqual(staged["calculation"]["q4BasicWeightedAverageShares"], "1000")
        self.assertEqual(staged["value"], "0.092")
        self.assertEqual(staged["calculation"]["annualDays"], 365)
        self.assertEqual(staged["calculation"]["nineMonthDays"], 273)
        self.assertEqual(staged["calculation"]["q4Days"], 92)
        self.assertTrue(staged["source"]["basis"]["splitAdjustedConsistently"])
        self.assertEqual(staged["source"]["annualIncome"]["accession"], "0000001234-24-000001")
        self.assertEqual(staged["source"]["nineMonthIncome"]["accession"], "0000001234-23-000004")

    def test_53_week_period_is_allowed_only_with_exact_contiguous_boundaries(self):
        inputs = {}
        annual_version = {"form": "10-K", "filed": "2024-02-15", "accn": "0000001234-24-000001"}
        nine_version = {"form": "10-Q", "filed": "2022-11-03", "accn": "0000001234-22-000004"}
        common = {"incomeAttribution": "common", "preferredDividendsStatus": "none",
                  "noncontrollingInterestStatus": "none"}
        shares = {"shareBasis": "post-split-basis-1", "shareClass": "common", "shareClassCount": 1}
        inputs["annual_income_facts"] = [reconstruction_fact(
            371, concept="NetIncomeLossAvailableToCommonStockholdersBasic", unit="USD",
            start="2022-01-02", end="2023-01-07", **annual_version, **common)]
        inputs["nine_month_income_facts"] = [reconstruction_fact(
            273, concept="NetIncomeLossAvailableToCommonStockholdersBasic", unit="USD",
            start="2022-01-02", end="2022-10-01", **nine_version, **common)]
        inputs["annual_basic_share_facts"] = [reconstruction_fact(
            1000, concept="WeightedAverageNumberOfSharesOutstandingBasic", unit="shares",
            start="2022-01-02", end="2023-01-07", **annual_version, **shares)]
        inputs["nine_month_basic_share_facts"] = [reconstruction_fact(
            1000, concept="WeightedAverageNumberOfSharesOutstandingBasic", unit="shares",
            start="2022-01-02", end="2022-10-01", **nine_version, **shares)]
        staged = evidence.reconstruct_basic_q4_eps(
            symbol="SYN", cik=CIK, fiscal_year=2022, fiscal_start="2022-01-02",
            nine_month_end="2022-10-01", q4_start="2022-10-02", fiscal_end="2023-01-07",
            target_share_basis="post-split-basis-1", **inputs)
        self.assertEqual(staged["grade"], "B")
        self.assertEqual(staged["calculation"]["annualDays"], 371)
        self.assertEqual(staged["calculation"]["nineMonthDays"], 273)
        self.assertEqual(staged["calculation"]["q4Days"], 98)
        self.assertEqual(staged["value"], "0.098")

    def test_rejects_mismatched_concept_units_and_period_continuity(self):
        self.assertEqual(reconstruct(nine_income_concept="NetIncomeLoss")["validation"]["failureReason"],
                         "income-concept-mismatch")
        self.assertEqual(reconstruct(annual_income_unit="EUR")["validation"]["failureReason"],
                         "unit-mismatch-annual-income")
        inputs = basic_inputs()
        staged = evidence.reconstruct_basic_q4_eps(
            symbol="SYN", cik=CIK, fiscal_year=2023, fiscal_start="2023-01-01",
            nine_month_end="2023-09-29", q4_start="2023-10-01", fiscal_end="2023-12-31",
            target_share_basis="post-split-basis-1", **inputs)
        self.assertEqual(staged["validation"]["failureReason"], "discontinuous-fiscal-periods")

    def test_rejects_uncertain_common_income_preferred_dividend_and_nci_treatment(self):
        self.assertEqual(reconstruct(annual_income_metadata={"incomeAttribution": "consolidated"})
                         ["validation"]["failureReason"], "income-not-confirmed-common-attributable")
        self.assertEqual(reconstruct(annual_income_metadata={"preferredDividendsStatus": "unknown"})
                         ["validation"]["failureReason"], "preferred-dividend-treatment-uncertain")
        self.assertEqual(reconstruct(nine_income_metadata={"noncontrollingInterestStatus": "unknown"})
                         ["validation"]["failureReason"], "noncontrolling-interest-treatment-uncertain")

    def test_rejects_share_basis_mismatch_multiple_classes_and_nonpositive_q4_shares(self):
        self.assertEqual(reconstruct(nine_share_basis="pre-split-basis")
                         ["validation"]["failureReason"], "split-basis-mismatch")
        self.assertEqual(reconstruct(annual_share_metadata={"shareClassCount": 2})
                         ["validation"]["failureReason"], "multiple-or-uncertain-common-share-classes")
        self.assertEqual(reconstruct(annual_shares=100, nine_shares=2000)
                         ["validation"]["failureReason"], "computed-q4-basic-shares-nonpositive")

    def test_conflicting_same_version_and_later_nine_month_restatement_are_rejected(self):
        inputs = basic_inputs()
        inputs["annual_income_facts"].append(dict(inputs["annual_income_facts"][0], val=366))
        staged = evidence.reconstruct_basic_q4_eps(
            symbol="SYN", cik=CIK, fiscal_year=2023, fiscal_start="2023-01-01",
            nine_month_end="2023-09-30", q4_start="2023-10-01", fiscal_end="2023-12-31",
            target_share_basis="post-split-basis-1", **inputs)
        self.assertEqual(staged["validation"]["failureReason"],
                         "conflicting-facts-in-selected-version-annual-income")

        inputs = basic_inputs()
        inputs["nine_month_income_facts"][0]["filed"] = "2024-03-01"
        inputs["nine_month_basic_share_facts"][0]["filed"] = "2024-03-01"
        staged = evidence.reconstruct_basic_q4_eps(
            symbol="SYN", cik=CIK, fiscal_year=2023, fiscal_start="2023-01-01",
            nine_month_end="2023-09-30", q4_start="2023-10-01", fiscal_end="2023-12-31",
            target_share_basis="post-split-basis-1", **inputs)
        self.assertEqual(staged["validation"]["failureReason"],
                         "fact-filed-after-reference-version-nine-month-income")

    def test_no_diluted_reconstruction_is_exposed(self):
        self.assertFalse(hasattr(evidence, "reconstruct_diluted_q4_eps"))
        self.assertEqual(evidence.reconstruct_basic_q4_eps.__name__, "reconstruct_basic_q4_eps")


if __name__ == "__main__":
    unittest.main()

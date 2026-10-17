"""Synthetic SEC 3-month Q4 EPS extraction, split checks, validation and resumable fill tests."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import repair_q4_eps as repair  # noqa: E402
import seed_financials_sec as sec  # noqa: E402


def sec_fact(value, year, *, end=None, start=None, form="10-K", filed=None):
    return {
        "val": value,
        "start": start or f"{year}-10-02",
        "end": end or f"{year}-12-31",
        "form": form,
        "filed": filed or f"{year + 1}-02-01",
        "accn": f"0000000000-{year + 1:02d}-000001",
    }


def companyfacts(*, diluted=None, basic=None):
    return {"facts": {"us-gaap": {
        "EarningsPerShareDiluted": {"units": {"USD / shares": diluted or []}},
        "EarningsPerShareBasic": {"units": {"USD / shares": basic or []}},
    }}}


def stored_document():
    ends = [
        "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
        "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
    ]
    return {
        "version": 1,
        "symbol": "TEST",
        "market": "US",
        "currency": "USD",
        "foreignReporter": False,
        "generatedAt": "2026-01-01T00:00:00Z",
        "checkedAt": "2026-01-01",
        "source": "SEC EDGAR companyfacts + Yahoo Finance",
        "fyEndMonth": 12,
        "annual": {
            "end": ["2023-12-31", "2024-12-31"],
            "fy": [2023, 2024],
            "src": ["sec", "sec"],
            "values": {"netIncome": [500, 200], "epsDiluted": [0.5, 0.2], "dilutedShares": [1000, 1000]},
        },
        "quarterly": {
            "end": ends,
            "fy": [2023] * 4 + [2024] * 4,
            "fq": [1, 2, 3, 4] * 2,
            "src": ["sec"] * 8,
            "values": {
                "netIncome": [100, 110, 120, 500, 100, 110, 120, 200],
                "epsDiluted": [0.1, 0.11, 0.12, 0.5, 0.1, 0.11, 0.12, None],
                "epsBasic": [0.11, 0.12, 0.13, 0.52, 0.11, 0.12, 0.13, None],
                "dilutedShares": [1000, 1000, 1000, None, 1000, 1000, 1000, None],
            },
        },
        "split": {"adjusted": False, "events": []},
        "quality": {"level": "good", "annualYears": 2, "quarters": 8, "issues": []},
    }


def verified_split_document(events):
    return {
        "symbol": "TEST",
        "checkedAt": "2026-01-01",
        "annual": {"end": ["2024-12-31"]},
        "quarterly": {"end": ["2024-12-31"]},
        "split": {
            "events": events, "eventsComplete": True,
            "eventsSource": "Yahoo Finance chart range=max split events",
            "eventsCheckedAt": "2026-01-01", "eventsSymbol": "TEST",
            "eventsCoverageStart": "1990-01-01", "eventsCoverageEnd": "2026-01-01",
            "eventsParserVersion": 3,
        },
    }


class FakeClient:
    def __init__(self, payload, clock_state=None, forbidden=False):
        self.payload = payload
        self.requests = 0
        self.clock_state = clock_state
        self.forbidden = forbidden

    def ticker_map(self):
        self.requests += 1
        if self.forbidden:
            raise sec.SecForbidden("https://www.sec.gov/files/company_tickers.json")
        if self.clock_state is not None:
            self.clock_state[0] = 2
        return {"TEST": "0000000001"}

    def company_facts(self, cik):
        self.requests += 1
        return self.payload


class SecQ4FactTests(unittest.TestCase):
    def test_extracts_only_80_to_100_day_usd_share_facts_near_q4_end(self):
        payload = companyfacts(diluted=[
            sec_fact(0.75, 2024),
            sec_fact(99.0, 2024, start="2024-01-01", end="2024-12-31"),  # annual duration
            sec_fact(88.0, 2024, start="2024-10-02", end="2025-01-07"),  # outside end tolerance
            sec_fact(77.0, 2024, start="2024-11-01", end="2024-12-31"),  # too short
        ])
        payload["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD"] = [sec_fact(55, 2024)]
        matched = repair.extract_q4_eps_facts(payload, ["2024-12-31"])
        self.assertEqual([item["value"] for item in matched["2024-12-31"]["epsDiluted"]], [0.75])
        self.assertEqual(matched["2024-12-31"]["epsDiluted"][0]["unit"], "USD/shares")
        self.assertEqual(matched["2024-12-31"]["epsBasic"], [])

    def test_split_factor_uses_dated_event_not_eps_ratio_and_checks_are_diagnostic_only(self):
        document = verified_split_document([["2025-06-01", 2], ["2010-01-01", 3]])
        candidate = {"value": 2.0}
        normalized, factor, reason = repair.normalize_and_check_q4_eps(
            document, "2024-12-31", 2_000_000, 2_000_000,
            {**candidate, "filed": "2025-02-01"})
        self.assertEqual((normalized, factor, reason), (1.0, 2.0, None))
        normalized, _factor, reason = repair.normalize_and_check_q4_eps(
            document, "2024-12-31", 2_000_000, 2_000_000, {"value": -2.0})
        self.assertIsNone(normalized)  # a split after the period needs a publication date
        self.assertEqual(reason, "split-basis-publication-date-missing")
        normalized, _factor, reason = repair.normalize_and_check_q4_eps(
            verified_split_document([]), "2024-12-31", 2_000_000, 2_000_000,
            {"value": -2.0, "filed": "2025-02-01"})
        self.assertEqual((normalized, reason), (-2.0, None))
        diagnostic = repair.q4_eps_diagnostics(normalized, 2_000_000, 2_000_000)
        self.assertIn("sign-mismatch-review", diagnostic["flags"])
        self.assertIn("outside-25-percent-review", diagnostic["flags"])
        # The numerical split factor cannot be selected by changing EPS or the NI/share ratio.
        normalized, factor, reason = repair.normalize_and_check_q4_eps(
            document, "2024-12-31", 200_000, 2_000_000,
            {"value": 900.0, "filed": "2025-02-01"})
        self.assertEqual((normalized, factor, reason), (450.0, 2.0, None))
        normalized, _factor, reason = repair.normalize_and_check_q4_eps(
            verified_split_document([["2025-02-01", 2]]), "2024-12-31",
            2_000_000, 2_000_000, {"value": 0.5, "filed": "2025-02-01"})
        self.assertIsNone(normalized)
        self.assertEqual(reason, "split-basis-same-day-as-filing")

    def test_split_between_period_end_and_filing_requires_reporting_basis_review(self):
        normalized, factor, reason = repair.normalize_and_check_q4_eps(
            verified_split_document([["2025-01-15", 2]]), "2024-12-31",
            2_000_000, 2_000_000, {"value": 1.0, "filed": "2025-02-01"})
        self.assertIsNone(normalized)
        self.assertEqual(factor, 1.0)
        self.assertEqual(reason, "split-basis-event-before-filing-needs-reporting-basis-review")

    def test_split_factor_refuses_unverified_or_partial_event_history(self):
        incomplete = verified_split_document([["2025-06-01", 2]])
        incomplete["split"]["eventsComplete"] = False
        normalized, factor, reason = repair.normalize_and_check_q4_eps(
            incomplete, "2024-12-31", 2_000_000, 2_000_000,
            {"value": 1.0, "filed": "2025-02-01"})
        self.assertIsNone(normalized)
        self.assertEqual(factor, 1.0)
        self.assertEqual(reason, "split-event-history-unverified-or-incomplete")

    def test_companyfacts_candidates_do_not_fill_without_original_filing_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TEST.json")
            state_path = os.path.join(directory, "state.json")
            original = stored_document()
            original["split"] = verified_split_document([])["split"]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(original, handle)
            payload = companyfacts(
                diluted=[sec_fact(0.5, 2023), sec_fact(0.2, 2024)],
                basic=[sec_fact(0.52, 2023), sec_fact(0.21, 2024)],
            )
            summary = repair.sec_repair_directory(
                directory, state_path=state_path, client=FakeClient(payload),
                environ={"SEC_USER_AGENT": "TestAgent test@example.com"})
            with open(path, encoding="utf-8") as handle:
                result = json.load(handle)
            self.assertEqual(summary["status"], "done")
            self.assertEqual(summary["validationCompared"], 2)
            self.assertAlmostEqual(summary["validationMedian"], 0)
            self.assertEqual(summary["filledPeriods"], 0)
            self.assertEqual(summary["pendingRawFilingReview"], {"epsBasic": 1, "epsDiluted": 1})
            self.assertEqual(result["quarterly"]["values"]["epsDiluted"][3], 0.5)
            self.assertEqual(result["quarterly"]["values"]["epsBasic"][3], 0.52)
            self.assertIsNone(result["quarterly"]["values"]["epsDiluted"][7])
            self.assertIsNone(result["quarterly"]["values"]["epsBasic"][7])
            self.assertNotIn("2024-12-31", result.get("secQ4EpsFacts", {}))
            self.assertEqual(result["quality"]["issues"], [])

    def test_budget_pause_resumes_after_ticker_map_and_403_stops_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TEST.json")
            state_path = os.path.join(directory, "state.json")
            original = stored_document()
            original["split"] = verified_split_document([])["split"]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(original, handle)
            payload = companyfacts(diluted=[sec_fact(0.5, 2023), sec_fact(0.2, 2024)],
                                   basic=[sec_fact(0.52, 2023), sec_fact(0.21, 2024)])
            now = [0]
            first = repair.sec_repair_directory(
                directory, state_path=state_path, budget=1, client=FakeClient(payload, now),
                environ={"SEC_USER_AGENT": "TestAgent test@example.com"}, clock=lambda: now[0])
            self.assertEqual(first["status"], "paused")
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), original)
            resumed = repair.sec_repair_directory(
                directory, state_path=state_path, client=FakeClient(payload),
                environ={"SEC_USER_AGENT": "TestAgent test@example.com"})
            self.assertEqual(resumed["status"], "done")
            self.assertEqual(resumed["filledPeriods"], 0)
            self.assertEqual(resumed["pendingRawFilingReview"], {"epsBasic": 1, "epsDiluted": 1})
            with open(path, encoding="utf-8") as handle:
                result = json.load(handle)
            self.assertIsNone(result["quarterly"]["values"]["epsDiluted"][7])

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TEST.json")
            original = stored_document()
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(original, handle)
            stopped = repair.sec_repair_directory(
                directory, state_path=os.path.join(directory, "state.json"),
                client=FakeClient(None, forbidden=True),
                environ={"SEC_USER_AGENT": "TestAgent test@example.com"})
            self.assertEqual(stopped["status"], "stopped-403")
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), original)


if __name__ == "__main__":
    unittest.main()

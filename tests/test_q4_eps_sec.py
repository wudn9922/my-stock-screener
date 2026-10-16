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

    def test_split_suffix_factor_puts_eps_on_current_basis_and_rejects_bad_sign_or_magnitude(self):
        document = {"split": {"events": [["2025-06-01", 2], ["2010-01-01", 3]]}}
        candidate = {"value": 2.0}
        normalized, factor, reason = repair.normalize_and_check_q4_eps(
            document, "2024-12-31", 2_000_000, 2_000_000, candidate)
        self.assertEqual((normalized, factor, reason), (1.0, 2.0, None))
        normalized, _factor, reason = repair.normalize_and_check_q4_eps(
            document, "2024-12-31", 2_000_000, 2_000_000, {"value": -2.0})
        self.assertIsNone(normalized)
        self.assertEqual(reason, "sign-mismatch")
        normalized, _factor, reason = repair.normalize_and_check_q4_eps(
            {"split": {"events": []}}, "2024-12-31", 2_000_000, 2_000_000, {"value": 0.5})
        self.assertIsNone(normalized)
        self.assertEqual(reason, "outside-q4-income-q3-shares-25pct")

    def test_fetch_validates_then_fills_only_null_values_with_fact_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TEST.json")
            state_path = os.path.join(directory, "state.json")
            original = stored_document()
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(original, handle)
            payload = companyfacts(
                diluted=[sec_fact(0.5, 2023), sec_fact(0.2, 2024)],
                basic=[sec_fact(0.52, 2023), sec_fact(0.21, 2024)],
            )
            summary = repair.sec_repair_directory(
                directory, state_path=state_path, client=FakeClient(payload), environ={})
            with open(path, encoding="utf-8") as handle:
                result = json.load(handle)
            self.assertEqual(summary["status"], "done")
            self.assertEqual(summary["validationCompared"], 2)
            self.assertAlmostEqual(summary["validationMedian"], 0)
            self.assertEqual(summary["filledPeriods"], 1)
            self.assertEqual(result["quarterly"]["values"]["epsDiluted"][3], 0.5)
            self.assertEqual(result["quarterly"]["values"]["epsBasic"][3], 0.52)
            self.assertEqual(result["quarterly"]["values"]["epsDiluted"][7], 0.2)
            self.assertEqual(result["quarterly"]["values"]["epsBasic"][7], 0.21)
            self.assertEqual(result["secQ4EpsFacts"]["2024-12-31"]["epsDiluted"]["concept"],
                             "EarningsPerShareDiluted")
            self.assertEqual(result["quality"]["issues"], [])

    def test_budget_pause_resumes_after_ticker_map_and_403_stops_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TEST.json")
            state_path = os.path.join(directory, "state.json")
            original = stored_document()
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(original, handle)
            payload = companyfacts(diluted=[sec_fact(0.5, 2023), sec_fact(0.2, 2024)],
                                   basic=[sec_fact(0.52, 2023), sec_fact(0.21, 2024)])
            now = [0]
            first = repair.sec_repair_directory(
                directory, state_path=state_path, budget=1, client=FakeClient(payload, now),
                environ={}, clock=lambda: now[0])
            self.assertEqual(first["status"], "paused")
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), original)
            resumed = repair.sec_repair_directory(
                directory, state_path=state_path, client=FakeClient(payload), environ={})
            self.assertEqual(resumed["status"], "done")
            self.assertEqual(resumed["filledPeriods"], 1)

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TEST.json")
            original = stored_document()
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(original, handle)
            stopped = repair.sec_repair_directory(
                directory, state_path=os.path.join(directory, "state.json"),
                client=FakeClient(None, forbidden=True), environ={})
            self.assertEqual(stopped["status"], "stopped-403")
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), original)


if __name__ == "__main__":
    unittest.main()

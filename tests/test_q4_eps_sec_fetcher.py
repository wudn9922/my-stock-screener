"""Synthetic tests for resumable and fail-fast SEC CompanyFacts fetching."""
import json
import gzip
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
SCRIPTS = ROOT / "atlas/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fetch_q4_eps_companyfacts as fetcher  # noqa: E402
import seed_financials_sec as sec  # noqa: E402


def financial(symbol):
    return {
        "version": 1,
        "symbol": symbol,
        "market": "US",
        "currency": "USD",
        "annual": {"end": [], "src": [], "values": {}},
        "quarterly": {
            "end": ["2023-12-31"], "fy": [2023], "fq": [4], "src": ["sec"],
            "values": {"epsBasic": [None], "epsDiluted": [None]},
        },
    }


class FakeClient:
    def __init__(self, forbidden=False):
        self.requests = 0
        self.calls = []
        self.forbidden = forbidden

    def company_facts(self, cik):
        self.requests += 1
        self.calls.append(cik)
        if self.forbidden:
            raise sec.SecForbidden(f"403 for {cik}")
        return {"cik": int(cik), "facts": {"us-gaap": {
            "EarningsPerShareBasic": {"units": {"USD/shares": []}},
        }}}


class CompanyFactsFetcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="q4-eps-fetch-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.financials = self.root / "financials"
        self.cache = self.root / "cache"
        self.financials.mkdir()
        for symbol in ("AAA", "BBB"):
            (self.financials / f"{symbol}.json").write_text(
                json.dumps(financial(symbol)), encoding="utf-8")
        self.q4_state = self.root / "old-state.json"
        self.q4_state.write_text(json.dumps({"version": 1, "tickerMap": {"AAA": "0000000001", "BBB": "0000000002"}}), encoding="utf-8")

    def test_budget_checkpoints_then_resume_is_idempotent(self):
        client = FakeClient()
        paused = fetcher.run(financials_dir=self.financials, cache_dir=self.cache,
                             q4_state_path=self.q4_state, budget=0, client=client,
                             environ={"SEC_USER_AGENT": "Test test@example.com"})
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["cachedCompanies"], 0)
        self.assertEqual(client.calls, [])

        resumed = fetcher.run(financials_dir=self.financials, cache_dir=self.cache,
                              q4_state_path=self.q4_state, client=client,
                              environ={"SEC_USER_AGENT": "Test test@example.com"})
        self.assertEqual(resumed["status"], "done")
        self.assertEqual(resumed["cachedCompanies"], 2)
        self.assertEqual(client.calls, ["0000000001", "0000000002"])

        rerun = fetcher.run(financials_dir=self.financials, cache_dir=self.cache,
                            q4_state_path=self.q4_state, client=client,
                            environ={"SEC_USER_AGENT": "Test test@example.com"})
        self.assertEqual(rerun["status"], "done")
        self.assertEqual(rerun["companiesFetched"], 0)
        self.assertEqual(client.requests, 2)

    def test_first_403_stops_without_requesting_next_company(self):
        client = FakeClient(forbidden=True)
        result = fetcher.run(financials_dir=self.financials, cache_dir=self.cache,
                             q4_state_path=self.q4_state, client=client,
                             environ={"SEC_USER_AGENT": "Test test@example.com"})
        self.assertEqual(result["status"], "stopped-403")
        self.assertTrue(result["stopped403"])
        self.assertEqual(client.requests, 1)

    def test_sec_client_raw_bytes_uses_user_agent_and_stops_on_first_403(self):
        captured = []
        client = sec.SecClient("Test contact@example.com",
                               opener=lambda request: (captured.append(request) or (200, b"<html/>")),
                               sleeper=lambda _seconds: None, stop_on_forbidden=True)
        self.assertEqual(client.get_bytes("https://www.sec.gov/Archives/example.htm"), b"<html/>")
        self.assertEqual(captured[0].get_header("User-agent"), "Test contact@example.com")
        forbidden = sec.SecClient("Test contact@example.com", opener=lambda _request: (403, b""),
                                  sleeper=lambda _seconds: None, stop_on_forbidden=True)
        with self.assertRaises(sec.SecForbidden):
            forbidden.get_bytes("https://www.sec.gov/Archives/blocked.htm")
        self.assertEqual(forbidden.requests, 1)

    def test_sec_client_decompresses_gzip_html(self):
        compressed = gzip.compress(b"<html><p>Q4 EPS</p></html>")
        client = sec.SecClient("Test contact@example.com", opener=lambda _request: (200, compressed),
                               sleeper=lambda _seconds: None)
        self.assertEqual(client.get_bytes("https://www.sec.gov/Archives/example.htm"),
                         b"<html><p>Q4 EPS</p></html>")


if __name__ == "__main__":
    unittest.main()

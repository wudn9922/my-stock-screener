"""
SEC EDGAR seeding of the financial statement history: atlas/scripts/seed_financials_sec.py.

Offline and fast: no network, no real sleeping (every sleeper is injected), every file lives in a
temporary directory. ALL companyfacts payloads below are hand-written SYNTHETIC data (a made-up
"Synthetic Inc." with round numbers, a 52/53-week September fiscal year and hand-picked quirks);
none of it comes from SEC. Run: python -m unittest discover -s tests -p "test_seed_financials_sec.py" -v
"""
import contextlib
import copy
import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "financials")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_financials as fin  # noqa: E402
import seed_financials_sec as sec  # noqa: E402

_REAL_YAHOO_GUARD = None


def setUpModule():
    """No test may ever open a real Yahoo session (main() without an injected factory included)."""
    global _REAL_YAHOO_GUARD
    _REAL_YAHOO_GUARD = mock.patch.object(sec, "_default_yahoo", side_effect=AssertionError("real Yahoo"))
    _REAL_YAHOO_GUARD.start()


def tearDownModule():
    _REAL_YAHOO_GUARD.stop()

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
TODAY = NOW.date().isoformat()
D = date.fromisoformat
B = 1_000_000_000


# ---------------------------------------------------------------------------------------------
# SYNTHETIC companyfacts of "Synthetic Inc.": FY2020-FY2024 complete (September fiscal year, FY2023
# is a 53-week year), FY2025 in progress (Q1-Q3). 10-Q: 3-month values (income statement) and
# year-to-date values (cash flow only YTD), 10-K: annual values restated one year later.
# ---------------------------------------------------------------------------------------------
FYS = {
    2020: ("2019-09-29", ["2019-12-28", "2020-03-28", "2020-06-27", "2020-09-26"]),
    2021: ("2020-09-27", ["2020-12-26", "2021-03-27", "2021-06-26", "2021-09-25"]),
    2022: ("2021-09-26", ["2021-12-25", "2022-03-26", "2022-06-25", "2022-09-24"]),
    2023: ("2022-09-25", ["2022-12-31", "2023-04-01", "2023-07-01", "2023-09-30"]),   # 53 weeks
    2024: ("2023-10-01", ["2023-12-30", "2024-03-30", "2024-06-29", "2024-09-28"]),
    2025: ("2024-09-29", ["2024-12-28", "2025-03-29", "2025-06-28"]),                  # in progress
}
REV = {
    2020: [82.0, 68.0, 60.0, 64.5],
    2021: [111.0, 89.5, 81.0, 84.3],
    2022: [124.0, 103.0, 91.0, 95.7],
    2023: [117.0, 95.0, 82.0, 89.3],
    2024: [119.6, 90.8, 86.0, 94.9],
    2025: [124.3, 95.4, 94.0],
}
SHARES = {fy: [int((16.5 - 0.2 * (fy - 2020) - 0.05 * q) * B) for q in range(4)] for fy in FYS}
RATIO = {"cost": 0.55, "oi": 0.30, "pretax": 0.31, "tax": 0.05, "ni": 0.26, "int": 0.002,
         "ocf": 0.29, "capex": 0.03, "div": 0.035, "buy": 0.2}
FLOW_CONCEPT = {
    "cost": "CostOfRevenue", "oi": "OperatingIncomeLoss",
    "pretax": "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "tax": "IncomeTaxExpenseBenefit", "ni": "NetIncomeLoss", "int": "InterestExpense",
    "ocf": "NetCashProvidedByUsedInOperatingActivities", "capex": "PaymentsToAcquirePropertyPlantAndEquipment",
    "div": "PaymentsOfDividends", "buy": "PaymentsForRepurchaseOfCommonStock",
}
CASH_FLOW = {"ocf", "capex", "div", "buy"}
NI_RESTATE = 1.03            # FY2021 net income restated in the later 10-Ks


def rev(fy, q):
    return int(REV[fy][q] * B)


def item(kind, fy, q):
    if kind == "rev":
        return rev(fy, q)
    if kind == "gp":
        return rev(fy, q) - int(rev(fy, q) * RATIO["cost"])
    return int(rev(fy, q) * RATIO[kind])


def eps(fy, q):
    return round(item("ni", fy, q) / SHARES[fy][q], 2)


def annual(kind, fy, restated=False):
    total = sum(item(kind, fy, q) for q in range(4))
    if kind == "ni" and fy == 2021 and restated:
        total = int(total * NI_RESTATE)
    return total


def annual_eps(fy, restated=False):
    return round(annual("ni", fy, restated) / (sum(SHARES[fy]) / 4), 2)


def rev_concept(fy):
    return "Revenues" if fy <= 2021 else "RevenueFromContractWithCustomerExcludingAssessedTax"


def q_start(fy, q):
    start, ends = FYS[fy]
    return start if q == 0 else (D(ends[q - 1]) + timedelta(days=1)).isoformat()


def build_companyfacts():
    facts = {}

    def add(concept, unit, point):
        facts.setdefault(concept, {}).setdefault(unit, []).append(point)

    def flows(fy, q, form, filed, accn):
        """3M and YTD facts of quarter q (0-based) of fy as filed in a 10-Q (cash flow: YTD only)."""
        start, ends = FYS[fy]
        end = ends[q]
        base = {"form": form, "filed": filed, "accn": accn, "fy": 2099, "fp": "Q9"}
        for kind in ["rev", "gp"] + list(FLOW_CONCEPT):
            concept = rev_concept(fy) if kind == "rev" else "GrossProfit" if kind == "gp" else FLOW_CONCEPT[kind]
            ytd = sum(item(kind, fy, k) for k in range(q + 1))
            if kind not in CASH_FLOW:
                add(concept, "USD", dict(base, start=q_start(fy, q), end=end, val=item(kind, fy, q)))
            if q > 0 or kind in CASH_FLOW:
                add(concept, "USD", dict(base, start=start, end=end, val=ytd))
        add("EarningsPerShareDiluted", "USD/shares", dict(base, start=q_start(fy, q), end=end, val=eps(fy, q)))
        add("EarningsPerShareBasic", "USD/shares",
            dict(base, start=q_start(fy, q), end=end, val=round(eps(fy, q) + 0.02, 2)))
        add("WeightedAverageNumberOfDilutedSharesOutstanding", "shares",
            dict(base, start=q_start(fy, q), end=end, val=SHARES[fy][q]))

    def balance(end, form, filed, accn, fy):
        base = {"form": form, "filed": filed, "accn": accn, "fy": 2099, "fp": "FY"}
        add("StockholdersEquity", "USD", dict(base, end=end, val=70 * B + fy))
        add("Assets", "USD", dict(base, end=end, val=360 * B))
        add("CashAndCashEquivalentsAtCarryingValue", "USD", dict(base, end=end, val=30 * B))
        add("MarketableSecuritiesCurrent", "USD", dict(base, end=end, val=25 * B))
        if end == "2020-09-26":
            add("LongTermDebt", "USD", dict(base, end=end, val=110 * B))          # alone → debt-approx
        else:
            add("LongTermDebtNoncurrent", "USD", dict(base, end=end, val=90 * B))
            add("LongTermDebtCurrent", "USD", dict(base, end=end, val=10 * B))

    for fy, (start, ends) in FYS.items():
        for q in range(min(3, len(ends))):
            filed = (D(ends[q]) + timedelta(days=35)).isoformat()
            accn = f"0000320193-{fy % 100:02d}-0000{q + 1}"
            flows(fy, q, "10-Q", filed, accn)
            balance(ends[q], "10-Q", filed, accn, fy)
            if fy - 1 in FYS:       # prior-year comparative columns of the same 10-Q
                balance(FYS[fy - 1][1][3], "10-Q", filed, accn, fy - 1)
                flows(fy - 1, q, "10-Q", filed, accn)
        if len(ends) == 4:
            filed = (D(ends[3]) + timedelta(days=40)).isoformat()
            accn = f"0000320193-{fy % 100:02d}-00010"
            for year in (fy, fy - 1, fy - 2):          # a 10-K also carries the two prior years
                if year not in FYS or len(FYS[year][1]) < 4:
                    continue
                restated = fy >= 2022
                span = {"form": "10-K", "filed": filed, "accn": accn, "fy": fy, "fp": "FY",
                        "start": FYS[year][0], "end": FYS[year][1][3]}
                add(rev_concept(year), "USD", dict(span, val=annual("rev", year)))
                add("GrossProfit", "USD", dict(span, val=annual("gp", year)))
                for kind, concept in FLOW_CONCEPT.items():
                    add(concept, "USD", dict(span, val=annual(kind, year, restated)))
                add("EarningsPerShareDiluted", "USD/shares", dict(span, val=annual_eps(year, restated)))
                add("EarningsPerShareBasic", "USD/shares", dict(span, val=round(annual_eps(year, restated) + 0.02, 2)))
                add("WeightedAverageNumberOfDilutedSharesOutstanding", "shares",
                    dict(span, val=int(sum(SHARES[year]) / 4)))
            balance(ends[3], "10-K", filed, accn, fy)
            if fy - 1 in FYS:
                balance(FYS[fy - 1][1][3], "10-K", filed, accn, fy - 1)

    # Noise that must be ignored: other units, a frame duplicate, a non-finite value, an 8-K
    add("Revenues", "EUR", {"start": "2019-09-29", "end": "2020-09-26", "val": 1, "form": "10-K",
                            "filed": "2030-01-01", "accn": "x"})
    add("EarningsPerShareDiluted", "USD", {"start": "2019-09-29", "end": "2020-09-26", "val": 999,
                                           "form": "10-K", "filed": "2030-01-01", "accn": "x"})
    add("Revenues", "USD", {"start": "2019-09-29", "end": "2020-09-26", "val": annual("rev", 2020),
                            "form": "10-K", "filed": "2020-01-01", "accn": "0", "frame": "CY2020"})
    add("Revenues", "USD", {"start": "2019-09-29", "end": "2020-09-26", "val": "NaN", "form": "10-K",
                            "filed": "2031-01-01", "accn": "z"})
    add("Revenues", "USD", {"start": "2019-09-29", "end": "2020-09-26", "val": 5, "form": "8-K",
                            "filed": "2031-01-01", "accn": "z"})
    return {
        "cik": 320193, "entityName": "Synthetic Inc.",
        "facts": {
            "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
                {"end": "2025-07-20", "val": 15 * B, "form": "10-Q", "filed": "2025-08-02", "accn": "q"}]}}},
            "us-gaap": {concept: {"label": concept, "units": units} for concept, units in facts.items()},
        },
    }


SYNTHETIC = build_companyfacts()
SYNTHETIC_BYTES = json.dumps(SYNTHETIC).encode("utf-8")


def facts():
    return copy.deepcopy(SYNTHETIC)


def mini(points):
    """{concept: [(start, end, value, form, filed, accn)]} → a minimal synthetic companyfacts payload."""
    gaap = {}
    for concept, rows in points.items():
        unit = "USD/shares" if concept.startswith("EarningsPerShare") else "USD"
        gaap[concept] = {"units": {unit: [
            {"start": start, "end": end, "val": value, "form": form, "filed": filed, "accn": accn}
            for start, end, value, form, filed, accn in rows
        ]}}
    return {"cik": 1, "entityName": "Synthetic Mini", "facts": {"us-gaap": gaap}}


def k(start, end, value, filed="2022-02-01", accn="a-1", form="10-K"):
    return (start, end, value, form, filed, accn)


def strip(document):
    return {key: value for key, value in document.items() if key not in ("generatedAt", "checkedAt")}


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as file:
        return json.load(file)


def read_bytes(path):
    with open(path, "rb") as file:
        return file.read()


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(value, file)


def read_json(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def ok(payload, gzipped=False):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return 200, gzip.compress(body) if gzipped else body


class FakeSec:
    """Offline stand-in for SEC (the SecClient `opener`): URL → scripted responses, the last one repeats."""

    def __init__(self, events=None):
        self.routes, self.calls, self.requests = {}, {}, []
        self.events = events if events is not None else []

    def route(self, url, *responses):
        self.routes[url] = list(responses)
        self.calls[url] = 0

    def __call__(self, request):
        url = request.full_url
        self.requests.append(request)
        self.events.append(("request", url))
        responses = self.routes.get(url)
        if responses is None:
            return 404, b"{}"
        response = responses[min(self.calls[url], len(responses) - 1)]
        self.calls[url] += 1
        if isinstance(response, BaseException):
            raise response
        return response

    def urls(self):
        return [request.full_url for request in self.requests]


def world(entries):
    """entries: ticker → (cik, payload bytes | int HTTP status) → FakeSec serving them."""
    fake = FakeSec()
    fake.route(sec.TICKERS_URL, ok({
        str(index): {"cik_str": cik, "ticker": ticker, "title": ticker + " Synthetic"}
        for index, (ticker, (cik, _)) in enumerate(entries.items())
    }))
    for ticker, (cik, payload) in entries.items():
        url = sec.FACTS_URL.format(cik=sec.cik10(cik))
        fake.route(url, (payload, b"{}") if isinstance(payload, int) else ok(payload))
    return fake


FOREIGN_BYTES = json.dumps({"cik": 1046179, "entityName": "Synthetic Foreign Ltd", "facts": {
    "ifrs-full": {"Revenue": {"units": {"USD": [
        {"start": "2022-01-01", "end": "2022-12-31", "val": 5, "form": "20-F", "filed": "2023-03-01"}]}}},
    "dei": {"X": {"units": {"shares": [{"end": "2023-01-01", "val": 1, "form": "20-F", "filed": "2023-03-01"}]}}},
}}).encode("utf-8")


class Runner:
    """Runs main() against a FakeSec in a temporary directory; no sleeping, stdout captured."""

    def __init__(self, test):
        self.tmp = tempfile.mkdtemp(prefix="sec-seed-test-")
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = os.path.join(self.tmp, "financials")
        self.report = os.path.join(self.tmp, "report")
        self.data = os.path.join(self.tmp, "data")
        self.sleeps = []
        self.yahoo_sleeps = []          # split checks: connect retries and REQUEST_DELAY (never really slept)
        self.unexpected_yahoo = []

    def path(self, name):
        return os.path.join(self.out, name)

    def files(self):
        return sorted(os.listdir(self.out)) if os.path.isdir(self.out) else []

    def _no_yahoo(self):
        self.unexpected_yahoo.append(1)
        raise AssertionError("Yahoo must not be contacted in this test")

    def run(self, fake, *args, environ=None, now=NOW, dirs=True, yahoo=None, clock=None):
        """yahoo: the injected Yahoo session factory; by default any Yahoo use fails the test. clock: --budget."""
        argv = list(args)
        if dirs:
            argv += ["--out", self.out, "--report-dir", self.report, "--data-dir", self.data]
        client = sec.SecClient("Test Agent test@example.com", opener=fake, sleeper=self.sleeps.append)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = sec.main(argv, client=client, now=now, environ={} if environ is None else environ,
                            yahoo_factory=yahoo or self._no_yahoo, sleep=self.yahoo_sleeps.append, clock=clock)
        self.client = client
        if yahoo is None:
            assert not self.unexpected_yahoo, "Yahoo was contacted"
        return code, buffer.getvalue()


def three_year_payload(income=True):
    """Mini payload with 3 annual periods (FY2019-FY2021, calendar years)."""
    rows = [k("2019-01-01", "2019-12-31", 100), k("2020-01-01", "2020-12-31", 110, filed="2021-02-01"),
            k("2021-01-01", "2021-12-31", 121, filed="2022-02-01")]
    points = {"Revenues": rows}
    if income:
        points["NetIncomeLoss"] = [k(s, e, v // 10, f, f) for (s, e, v, _, f, _) in
                                   [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]]
    return mini(points)


# ---------------------------------------------------------------------------------------------
# SEC access rules
# ---------------------------------------------------------------------------------------------
class AccessRulesTest(unittest.TestCase):
    def test_default_user_agent_is_exact(self):
        self.assertEqual(sec.DEFAULT_USER_AGENT, "SecSeed w556087587@gmail.com")
        self.assertEqual(sec.resolve_user_agent({}), "SecSeed w556087587@gmail.com")
        self.assertEqual(sec.resolve_user_agent({"PATH": "x"}), "SecSeed w556087587@gmail.com")

    def test_env_overrides_and_is_trimmed(self):
        self.assertEqual(sec.resolve_user_agent({"SEC_USER_AGENT": "  Me me@example.com "}), "Me me@example.com")

    def test_blank_env_is_refused(self):
        for value in ("", "   ", "\t\n"):
            with self.assertRaises(sec.UserAgentError):
                sec.resolve_user_agent({"SEC_USER_AGENT": value})
        with self.assertRaises(sec.UserAgentError):
            sec.resolve_user_agent({"SEC_USER_AGENT": "bad\nagent"})

    def test_main_refuses_blank_user_agent_without_any_request(self):
        runner = Runner(self)
        fake = world({"AAPL": (320193, SYNTHETIC_BYTES)})
        for value in ("", "  "):
            code, text = runner.run(fake, "--tickers", "AAPL", environ={"SEC_USER_AGENT": value})
            self.assertEqual(code, 2)
            self.assertIn("SEC_USER_AGENT", text)
        self.assertEqual(fake.requests, [])
        self.assertEqual(runner.files(), [])

    def test_main_refuses_in_ci(self):
        runner = Runner(self)
        fake = world({"AAPL": (320193, SYNTHETIC_BYTES)})
        for env in ({"CI": "true"}, {"GITHUB_ACTIONS": "true"}, {"CI": "1"}, {"GITHUB_ACTIONS": "yes"}):
            code, text = runner.run(fake, "--tickers", "AAPL", environ=env)
            self.assertEqual(code, 2, env)
            self.assertIn("local manual use only", text)
        self.assertEqual(fake.requests, [])
        # a false-looking CI variable does not block
        for env in ({"CI": "false"}, {"CI": "0"}, {"CI": ""}, {"GITHUB_ACTIONS": "false"}):
            self.assertIsNone(sec.refusal_reason(env), env)
        self.assertIsNotNone(sec.refusal_reason({"CI": "true"}))

    def test_every_request_carries_user_agent_and_gzip_is_decoded(self):
        fake = FakeSec()
        fake.route(sec.TICKERS_URL, ok({"0": {"cik_str": 320193, "ticker": "AAPL"}}, gzipped=True))
        fake.route(sec.FACTS_URL.format(cik="0000320193"), ok(SYNTHETIC_BYTES, gzipped=True))
        client = sec.SecClient(sec.resolve_user_agent({}), opener=fake, sleeper=lambda seconds: None)
        self.assertEqual(client.ticker_map(), {"AAPL": "0000320193"})
        payload = client.company_facts(320193)
        self.assertEqual(payload["entityName"], "Synthetic Inc.")
        self.assertEqual(len(fake.requests), 2)
        for request in fake.requests:
            self.assertEqual(request.get_header("User-agent"), "SecSeed w556087587@gmail.com")
            self.assertIn("gzip", request.get_header("Accept-encoding"))

    def test_default_client_uses_env_user_agent_and_urllib_opener(self):
        runner = Runner(self)
        fake = world({"AAPL": (320193, SYNTHETIC_BYTES)})
        buffer = io.StringIO()
        with mock.patch.object(sec, "urllib_opener", fake), mock.patch.object(sec.time, "sleep") as sleep, \
                contextlib.redirect_stdout(buffer):
            code = sec.main(["--tickers", "AAPL", "--out", runner.out, "--report-dir", runner.report,
                             "--data-dir", runner.data], now=NOW, environ={"SEC_USER_AGENT": "Env Agent a@b.co"})
        self.assertEqual(code, 0)
        self.assertEqual({r.get_header("User-agent") for r in fake.requests}, {"Env Agent a@b.co"})
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(sleep.call_count, 2)       # one sleep per request, nothing really slept


# ---------------------------------------------------------------------------------------------
# SecClient: pacing, backoff, failure budget
# ---------------------------------------------------------------------------------------------
class ClientTest(unittest.TestCase):
    URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json"

    def client(self, *responses, url=None, **kwargs):
        events = []
        fake = FakeSec(events)
        fake.route(url or self.URL, *responses)
        client = sec.SecClient("T t@example.com", opener=fake, sleeper=lambda s: events.append(("sleep", s)), **kwargs)
        return client, fake, events

    def sleeps(self, events):
        return [value for kind, value in events if kind == "sleep"]

    def test_sleep_follows_every_request_success_or_failure(self):
        client, fake, events = self.client((500, b"x"), (200, b"{}"), url=None)
        client.get_json(self.URL)
        self.assertEqual(sec.MIN_INTERVAL, 0.13)
        requests = [i for i, (kind, _) in enumerate(events) if kind == "request"]
        self.assertEqual(len(requests), 2)
        for index in requests:
            kind, seconds = events[index + 1]
            self.assertEqual(kind, "sleep")
            self.assertGreaterEqual(seconds, 0.13)
        # a request that raises is followed by the pacing sleep too
        client, fake, events = self.client(urllib.error.URLError("boom"), (200, b"{}"))
        client.get_json(self.URL)
        self.assertEqual(events[0][0], "request")
        self.assertEqual(events[1], ("sleep", sec.MIN_INTERVAL))

    def test_429_backs_off_exponentially_then_succeeds(self):
        client, fake, events = self.client((429, b""), (429, b""), (429, b""), (200, b'{"a": 1}'))
        self.assertEqual(client.get_json(self.URL), {"a": 1})
        self.assertEqual(self.sleeps(events), [0.13, 2, 0.13, 4, 0.13, 8, 0.13])
        self.assertEqual(client.consecutive_failures, 0)

    def test_gives_up_on_one_url_after_bounded_attempts(self):
        client, fake, events = self.client((429, b""))
        with self.assertRaises(sec.FetchError):
            client.get_json(self.URL)
        self.assertEqual(len(fake.requests), 6)
        self.assertEqual([s for s in self.sleeps(events) if s != 0.13], [2, 4, 8, 16, 32])
        self.assertEqual(client.consecutive_failures, 6)

    def test_retryable_problems(self):
        for response in ((403, b""), (503, b""), (500, b""), urllib.error.URLError("down"),
                         TimeoutError("slow"), (200, b"not json"), (200, b"[1"), (200, b"\x1f\x8bbroken")):
            client, fake, events = self.client(response, (200, b"{}"))
            self.assertEqual(client.get_json(self.URL), {}, response)
            self.assertEqual(len(fake.requests), 2, response)
            self.assertIn(2, self.sleeps(events), response)

    def test_other_client_errors_are_not_retried(self):
        client, fake, events = self.client((400, b""), (200, b"{}"))
        with self.assertRaises(sec.FetchError):
            client.get_json(self.URL)
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(client.consecutive_failures, 1)

    def test_404_on_companyfacts_is_not_a_failure(self):
        client, fake, events = self.client((404, b"{}"))
        for _ in range(30):
            self.assertIsNone(client.company_facts(1))
        self.assertEqual(client.consecutive_failures, 0)
        self.assertEqual(client.failed_attempts, 0)
        self.assertEqual(len(fake.requests), 30)
        # ... but a missing company_tickers.json is
        client, fake, events = self.client((404, b"{}"), url=sec.TICKERS_URL)
        with self.assertRaises(sec.FetchError):
            client.ticker_map()

    def test_ten_consecutive_failures_across_urls_stop_the_run(self):
        events = []
        fake = FakeSec(events)
        urls = [f"https://data.sec.gov/api/xbrl/companyfacts/CIK{n:010d}.json" for n in range(1, 5)]
        for url in urls:
            fake.route(url, (429, b""))
        client = sec.SecClient("T", opener=fake, sleeper=lambda s: None)
        with self.assertRaises(sec.FetchError):
            client.get_json(urls[0])                    # 6 failed attempts
        self.assertEqual(client.consecutive_failures, 6)
        with self.assertRaises(sec.TooManyFailures):
            client.get_json(urls[1])                    # 4 more → 10
        self.assertEqual(client.consecutive_failures, 10)
        self.assertEqual(len(fake.requests), 10)

    def test_404_and_success_reset_the_failure_count(self):
        events = []
        fake = FakeSec(events)
        bad = [f"https://data.sec.gov/api/xbrl/companyfacts/CIK{n:010d}.json" for n in (1, 2, 3)]
        for url in bad:
            fake.route(url, (500, b""))
        gone = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000009.json"
        fine = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000008.json"
        fake.route(gone, (404, b""))
        fake.route(fine, ok({}))
        client = sec.SecClient("T", opener=fake, sleeper=lambda s: None)
        with self.assertRaises(sec.FetchError):
            client.get_json(bad[0])                     # 6
        self.assertIsNone(client.get_json(gone, allow_404=True))
        self.assertEqual(client.consecutive_failures, 0)
        with self.assertRaises(sec.FetchError):
            client.get_json(bad[1])                     # 6 again: no TooManyFailures thanks to the 404
        client.get_json(fine)
        self.assertEqual(client.consecutive_failures, 0)
        with self.assertRaises(sec.FetchError):
            client.get_json(bad[2])

    def test_urllib_opener_returns_status_of_http_errors(self):
        error = urllib.error.HTTPError("https://x", 429, "Too Many Requests", {}, io.BytesIO(b"slow down"))
        self.addCleanup(error.close)
        with mock.patch.object(sec.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(sec.urllib_opener(object()), (429, b"slow down"))
        with mock.patch.object(sec.urllib.request, "urlopen", side_effect=urllib.error.URLError("dns")):
            with self.assertRaises(OSError):
                sec.urllib_opener(object())

    def test_cik_url_is_zero_padded(self):
        self.assertEqual(sec.cik10(320193), "0000320193")
        self.assertEqual(sec.cik10("1067983"), "0001067983")
        client, fake, events = self.client(ok({}), url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
        self.assertEqual(client.company_facts(320193), {})
        self.assertEqual(fake.urls(), ["https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"])

    def test_ticker_map(self):
        payload = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"},
            "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire"},
            "2": {"cik_str": 14693, "ticker": "BF.B", "title": "Brown Forman"},
            "3": {"cik_str": "789019", "ticker": "msft"},
            "4": {"cik_str": 999, "ticker": "AAPL"},                 # duplicate: the first wins
            "5": {"cik_str": 5, "ticker": "^BAD"},
            "6": {"cik_str": None, "ticker": "NOCIK"},
            "7": {"cik_str": True, "ticker": "BOOL"},
            "8": "junk",
            "9": {"cik_str": 7, "ticker": "2330.TW"},                # not a US key
        }
        self.assertEqual(sec.parse_ticker_map(payload), {
            "AAPL": "0000320193", "BRK-B": "0001067983", "BF-B": "0000014693", "MSFT": "0000789019",
        })
        self.assertEqual(sec.parse_ticker_map([]), {})
        self.assertEqual(sec.parse_ticker_map(None), {})


# ---------------------------------------------------------------------------------------------
# Extraction rules
# ---------------------------------------------------------------------------------------------
class ExtractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = sec.extract_periods(facts())
        cls.A = {p["end"]: p["values"] for p in cls.result.annual}
        cls.Q = {p["end"]: p for p in cls.result.quarterly}

    def test_overall_shape(self):
        result = self.result
        self.assertIsNone(result.skip)
        self.assertEqual(result.cik, "0000320193")
        self.assertEqual(result.entity, "Synthetic Inc.")
        self.assertEqual([p["end"] for p in result.annual], [FYS[y][1][3] for y in range(2020, 2025)])
        self.assertEqual(len(result.quarterly), 23)
        self.assertEqual(result.issues, {"q4-derived", "debt-approx"})
        self.assertTrue(all(p["src"] == "sec" for p in result.annual + result.quarterly))

    def test_extraction_is_deterministic_and_pure(self):
        payload = facts()
        again = sec.extract_periods(payload)
        self.assertEqual(payload, SYNTHETIC)
        self.assertEqual(again.annual, self.result.annual)
        self.assertEqual(again.quarterly, self.result.quarterly)

    def test_form_filtering_10k_10q_only(self):
        # 8-K / 10-K405 / S-1 facts never create or change a period; an 8-K-only year is not a year
        payload = mini({
            "Revenues": [
                k("2019-01-01", "2019-12-31", 100), k("2020-01-01", "2020-12-31", 110),
                k("2020-01-01", "2020-12-31", 777, form="8-K", filed="2030-01-01", accn="z"),
                k("2021-01-01", "2021-12-31", 888, form="8-K", filed="2030-01-01", accn="z"),
                k("2018-01-01", "2018-12-31", 666, form="S-1", filed="2030-01-01", accn="y"),
                k("2017-01-01", "2017-12-31", 555, form="10-K405", filed="2030-01-01", accn="y"),
            ],
        })
        result = sec.extract_periods(payload)
        self.assertEqual([(p["end"], p["values"]["revenue"]) for p in result.annual],
                         [("2019-12-31", 100), ("2020-12-31", 110)])
        # 10-K/A and 10-KT count as annual forms, a 10-Q full-year-length fact does not
        payload = mini({"Revenues": [
            k("2019-01-01", "2019-12-31", 100), k("2020-01-01", "2020-12-31", 110, form="10-K/A"),
            k("2021-01-01", "2021-12-31", 121, form="10-KT"),
            k("2022-01-01", "2022-12-31", 133, form="10-Q"),
        ]})
        self.assertEqual([p["end"] for p in sec.extract_periods(payload).annual],
                         ["2019-12-31", "2020-12-31", "2021-12-31"])

    def test_10q_values_feed_quarters_not_annual(self):
        self.assertEqual(set(self.A), {FYS[y][1][3] for y in range(2020, 2025)})
        self.assertEqual(self.Q["2024-03-30"]["values"]["revenue"], rev(2024, 1))

    def test_noise_facts_do_not_change_values(self):
        # EUR / wrong EPS unit / frame duplicate / NaN / 8-K on FY2020 never win
        self.assertEqual(self.A["2020-09-26"]["revenue"], annual("rev", 2020))
        self.assertEqual(self.A["2020-09-26"]["epsDiluted"], annual_eps(2020))

    def test_restatement_latest_filed_wins(self):
        self.assertEqual(self.A["2021-09-25"]["netIncome"], annual("ni", 2021, True))
        self.assertNotEqual(annual("ni", 2021, True), annual("ni", 2021))
        # Same period: the latest filing wins even when listed first; an accession number breaks a tie
        payload = mini({"Revenues": [
            k("2019-01-01", "2019-12-31", 100, filed="2020-02-01", accn="a-1"),
            k("2020-01-01", "2020-12-31", 999, filed="2023-02-01", accn="a-0"),
            k("2020-01-01", "2020-12-31", 110, filed="2021-02-01", accn="a-9"),
            k("2020-01-01", "2020-12-31", 115, filed="2022-02-01", accn="a-5"),
        ]})
        self.assertEqual({p["end"]: p["values"]["revenue"] for p in sec.extract_periods(payload).annual},
                         {"2019-12-31": 100, "2020-12-31": 999})
        payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"][1] = (
            dict(payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"][3], val=120, accn="a-7"))
        self.assertEqual(sec.extract_periods(payload).annual[1]["values"]["revenue"], 120)   # a-7 > a-5, same day

    def test_q4_is_annual_minus_nine_month_ytd_and_flagged(self):
        for fy in range(2020, 2025):
            ends = FYS[fy][1]
            q4 = self.Q[ends[3]]
            expected = annual("ni", fy, fy == 2021) - sum(item("ni", fy, q) for q in range(3))
            self.assertEqual(q4["values"]["netIncome"], expected, fy)
            self.assertEqual(q4["values"]["revenue"], rev(fy, 3), fy)
            self.assertEqual(q4["extra"]["derived"], 1)
            self.assertIn("q4-derived", self.result.period_issues[("quarterly", ends[3])])
            self.assertIsNone(self.Q[ends[0]]["extra"]["derived"])
            self.assertNotIn("dilutedShares", q4["values"])        # no Q4 share count
            self.assertEqual(q4["values"]["equity"], 70 * B + fy)   # balance sheet from the 10-K
        # Q4 EPS = annual EPS - (Q1 + Q2 + Q3)
        q4 = self.Q["2024-09-28"]["values"]["epsDiluted"]
        self.assertEqual(q4, fin.clean_value(annual_eps(2024) - sum(eps(2024, q) for q in range(3))))

    def test_q4_without_nine_month_ytd_uses_the_three_quarters(self):
        payload = facts()
        points = payload["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
        payload["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"] = [
            p for p in points if not (p.get("start") == "2023-10-01" and p["end"] == "2024-06-29")
        ]
        q = {p["end"]: p["values"] for p in sec.extract_periods(payload).quarterly}
        self.assertEqual(q["2024-09-28"]["netIncome"], annual("ni", 2024) - sum(item("ni", 2024, k_) for k_ in range(3)))

    def test_ytd_cash_flow_is_subtracted_for_q2_q3_q4(self):
        for fy in range(2020, 2025):
            ends = FYS[fy][1]
            for q in range(4):
                values = self.Q[ends[q]]["values"]
                self.assertEqual(values["operatingCashFlow"], item("ocf", fy, q), (fy, q))
                self.assertEqual(values["capex"], item("capex", fy, q), (fy, q))
                self.assertGreater(values["capex"], 0)
            # Q1 cash flow is the first year-to-date value itself (not subtracted → not derived)
            self.assertIsNone(self.Q[ends[0]]["extra"]["derived"])
            self.assertEqual(self.Q[ends[1]]["extra"]["derived"], 1)       # Q2 cash flow was subtracted
        q3 = self.Q["2025-06-28"]
        self.assertEqual(q3["values"]["operatingCashFlow"], item("ocf", 2025, 2))
        self.assertEqual(q3["extra"]["derived"], 1)
        self.assertNotIn("q4-derived", self.result.period_issues[("quarterly", "2025-06-28")])

    def test_ytd_subtraction_needs_every_piece(self):
        concept = "NetCashProvidedByUsedInOperatingActivities"
        payload = facts()
        points = payload["facts"]["us-gaap"][concept]["units"]["USD"]
        payload["facts"]["us-gaap"][concept]["units"]["USD"] = [
            p for p in points if not (p.get("start") == "2023-10-01" and p["end"] == "2024-03-30")
        ]
        q = {p["end"]: p["values"] for p in sec.extract_periods(payload).quarterly}
        self.assertNotIn("operatingCashFlow", q["2024-03-30"])      # no 6M YTD
        self.assertNotIn("operatingCashFlow", q["2024-06-29"])      # 9M - 6M impossible, nothing invented
        self.assertEqual(q["2024-09-28"]["operatingCashFlow"], item("ocf", 2024, 3))   # 12M - 9M still fine
        # YTD values that do not start on the same day are never subtracted
        payload = facts()
        for point in payload["facts"]["us-gaap"][concept]["units"]["USD"]:
            if point.get("start") == "2023-10-01" and point["end"] == "2024-06-29":
                point["start"] = "2023-10-02"
        q = {p["end"]: p["values"] for p in sec.extract_periods(payload).quarterly}
        self.assertNotIn("operatingCashFlow", q["2024-06-29"])

    def test_units_are_filtered(self):
        gaap = copy.deepcopy(SYNTHETIC["facts"]["us-gaap"])
        # EUR money facts dropped even next to USD ones
        gaap["Revenues"]["units"]["EUR"] = [
            {"start": "2020-09-27", "end": "2021-09-25", "val": 12345, "form": "10-K", "filed": "2040-01-01", "accn": "e"}]
        # share counts only count in "shares", EPS only in "USD/shares"
        gaap["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["pure"] = [
            {"start": "2020-09-27", "end": "2021-09-25", "val": 1, "form": "10-K", "filed": "2040-01-01", "accn": "e"}]
        result = sec.extract_periods({"cik": 1, "facts": {"us-gaap": gaap}})
        a = {p["end"]: p["values"] for p in result.annual}
        self.assertNotEqual(a["2021-09-25"]["revenue"], 12345)
        self.assertEqual(a["2021-09-25"]["revenue"], annual("rev", 2021))
        self.assertEqual(a["2021-09-25"]["dilutedShares"], int(sum(SHARES[2021]) / 4))

    def test_non_usd_only_is_skipped(self):
        payload = facts()
        for entry in payload["facts"]["us-gaap"].values():
            if "USD" in entry["units"]:
                entry["units"]["EUR"] = entry["units"].pop("USD")
        result = sec.extract_periods(payload)
        self.assertEqual(result.skip, "non-usd")
        self.assertEqual(sec.merge_into_document(None, result, "AAPL", NOW)[0], None)

    def test_concept_fallback_is_per_period(self):
        r = self.result
        self.assertEqual(r.period_concepts[("annual", "2020-09-26")]["revenue"], ["Revenues"])
        self.assertEqual(r.period_concepts[("annual", "2023-09-30")]["revenue"],
                         ["RevenueFromContractWithCustomerExcludingAssessedTax"])
        self.assertEqual(r.concepts["revenue"],
                         sorted(["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]))
        # order of preference inside one period: Revenues beats the later candidates
        payload = mini({
            "Revenues": [k("2019-01-01", "2019-12-31", 100), k("2020-01-01", "2020-12-31", 110)],
            "SalesRevenueNet": [k("2019-01-01", "2019-12-31", 1), k("2020-01-01", "2020-12-31", 2),
                                k("2021-01-01", "2021-12-31", 3)],
        })
        a = {p["end"]: p["values"]["revenue"] for p in sec.extract_periods(payload).annual}
        self.assertEqual(a, {"2019-12-31": 100, "2020-12-31": 110, "2021-12-31": 3})

    def test_debt_cash_and_yahoo_only_fields(self):
        a = self.A
        self.assertEqual(a["2020-09-26"]["totalDebt"], 110 * B)
        self.assertIn("debt-approx", self.result.period_issues[("annual", "2020-09-26")])
        self.assertEqual(a["2021-09-25"]["totalDebt"], 100 * B)
        self.assertNotIn("debt-approx", self.result.period_issues[("annual", "2021-09-25")])
        self.assertEqual(a["2021-09-25"]["cash"], 55 * B)
        self.assertEqual(self.result.concepts["totalDebt"], ["LongTermDebt", "LongTermDebtCurrent", "LongTermDebtNoncurrent"])
        self.assertEqual(set(a["2024-09-28"]), set(sec.CONCEPTS))
        for name in ("operatingIncomeReported", "fcfYahoo"):
            self.assertNotIn(name, a["2024-09-28"])
        payload = facts()           # DebtCurrent next to the noncurrent part replaces LongTermDebtCurrent
        payload["facts"]["us-gaap"]["DebtCurrent"] = {"units": {"USD": [
            {"end": "2024-09-28", "val": 15 * B, "form": "10-K", "filed": "2024-11-07", "accn": "k"}]}}
        a2 = {p["end"]: p["values"] for p in sec.extract_periods(payload).annual}
        self.assertEqual(a2["2024-09-28"]["totalDebt"], 105 * B)

    def test_52_53_week_fiscal_calendar(self):
        # 364 / 371-day years and 91 / 98 / 84-day quarters, measured exactly start → end
        years = [("2018-12-29", "2019-12-28", [100, 100, 100, 100]),
                 ("2019-12-28", "2021-01-02", [110, 110, 110, 130]),      # 53 weeks, 371 days
                 ("2021-01-02", "2022-01-01", [120, 120, 120, 120]),
                 ("2022-01-01", "2022-12-31", [130, 130, 130, 130])]
        quarter_ends = {
            2020: ["2020-03-28", "2020-06-27", "2020-09-26"],
            2021: ["2021-04-03", "2021-07-03", "2021-10-02"],
            2022: ["2022-04-09", "2022-07-02", "2022-10-01"],             # 98, 84, 91 days
        }
        annual_rows, quarter_rows = [], []
        for start, end, quarters in years:
            annual_rows.append(k(start, end, sum(quarters), filed=end[:4] + "-12-31", accn=end))
        for (start, end, quarters), fy in zip(years[1:], (2020, 2021, 2022)):
            previous = start
            for q, quarter_end in enumerate(quarter_ends[fy]):
                quarter_rows.append(k(previous, quarter_end, quarters[q], form="10-Q", filed=quarter_end, accn=quarter_end))
                previous = quarter_end
        spans = [(D(e) - D(s)).days for s, e, _ in years]
        self.assertEqual(spans, [364, 371, 364, 364])
        self.assertEqual([(D(r[1]) - D(r[0])).days for r in quarter_rows[:1] + quarter_rows[6:9]], [91, 98, 84, 91])
        result = sec.extract_periods(mini({"Revenues": annual_rows + quarter_rows}))
        self.assertIsNone(result.skip)
        self.assertEqual([p["end"] for p in result.annual], ["2019-12-28", "2021-01-02", "2022-01-01", "2022-12-31"])
        doc, info = sec.merge_into_document(None, result, "TEST", NOW)
        self.assertEqual(doc["fyEndMonth"], 12)
        self.assertEqual(doc["annual"]["fy"], [fin.fiscal_year(e) for e in doc["annual"]["end"]])
        self.assertEqual(doc["annual"]["fy"], [2019, 2020, 2021, 2022])
        labels = list(zip(doc["quarterly"]["end"], doc["quarterly"]["fy"], doc["quarterly"]["fq"]))
        for end, fy, fq in labels:
            self.assertEqual((fy, fq), fin.fiscal_quarter(end, 12), end)
        self.assertEqual(labels, [
            ("2020-03-28", 2020, 1), ("2020-06-27", 2020, 2), ("2020-09-26", 2020, 3), ("2021-01-02", 2020, 4),
            ("2021-04-03", 2021, 1), ("2021-07-03", 2021, 2), ("2021-10-02", 2021, 3), ("2022-01-01", 2021, 4),
            ("2022-04-09", 2022, 1), ("2022-07-02", 2022, 2), ("2022-10-01", 2022, 3), ("2022-12-31", 2022, 4),
        ])
        values = dict(zip(doc["quarterly"]["end"], doc["quarterly"]["values"]["revenue"]))
        self.assertEqual(values["2021-01-02"], 130)                 # 14-week Q4 = 440 - 3 * 110
        self.assertEqual(values["2022-04-09"], 130)                 # 98-day quarter taken as reported
        self.assertEqual(doc["quarterly"]["derived"][3], 1)
        self.assertNotIn("quarter-annual-mismatch", doc["quality"]["issues"])
        # the Jan 1 year end of FY2021 is labelled with the year before
        self.assertEqual(fin.fiscal_year("2022-01-01"), 2021)

    def test_foreign_filers_are_skipped(self):
        ifrs = sec.extract_periods(json.loads(FOREIGN_BYTES))
        self.assertEqual(ifrs.skip, "foreign-filer")
        document, info = sec.merge_into_document(None, ifrs, "TSM", NOW)
        self.assertIsNone(document)
        self.assertEqual((info["status"], info["reason"]), ("skipped", "foreign-filer"))
        payload = facts()           # us-gaap numbers but filed on 20-F / 6-K forms
        for entry in payload["facts"]["us-gaap"].values():
            for points in entry["units"].values():
                for point in points:
                    point["form"] = {"10-K": "20-F", "10-Q": "6-K"}.get(point["form"], point["form"])
        self.assertEqual(sec.extract_periods(payload).skip, "foreign-filer")
        self.assertEqual(sec.extract_periods({}).skip, "no-us-gaap")
        short = facts()
        for entry in short["facts"]["us-gaap"].values():
            for name in list(entry["units"]):
                entry["units"][name] = [p for p in entry["units"][name]
                                        if p["form"] != "10-K" or p["end"] == "2024-09-28"]
        self.assertEqual(sec.extract_periods(short).skip, "short-history")


# ---------------------------------------------------------------------------------------------
# Regression tests for the audit findings A-D. Every payload is SYNTHETIC (calendar years, round
# made-up numbers); each mimics the SHAPE of a real filer's data named in the comment, nothing more.
# ---------------------------------------------------------------------------------------------
Q_SPANS = (("01-01", "03-31", "05-10"), ("04-01", "06-30", "08-10"), ("07-01", "09-30", "11-10"))


def quarters(year, values, prefix="q", form="10-Q", filed_year=None):
    """3-month rows of Q1..Q3 of `year` (10-Q, filed after each quarter unless filed_year is given)."""
    return [k(f"{year}-{start}", f"{year}-{end}", value, form=form,
              filed=f"{filed_year or year}-{filed}", accn=f"{prefix}{year}{index}")
            for index, ((start, end, filed), value) in enumerate(zip(Q_SPANS, values)) if value is not None]


def ytd(year, months, value, filed=None, accn=None):
    """A 6- or 9-month YTD row of `year` (10-Q)."""
    end = {6: "06-30", 9: "09-30"}[months]
    return k(f"{year}-01-01", f"{year}-{end}", value, form="10-Q",
             filed=filed or f"{year}-{'08-10' if months == 6 else '11-10'}",
             accn=accn or f"q{year}{1 if months == 6 else 2}")


def year(year_, value, filed=None, accn=None, form="10-K"):
    """An annual row of `year_` (10-K filed the next February unless given)."""
    return k(f"{year_}-01-01", f"{year_}-12-31", value, form=form, filed=filed or f"{year_ + 1}-02-15",
             accn=accn or f"k{year_}")


def q4(year_, value, filed=None, accn=None):
    """A 3-month Q4 row of `year_` as a 10-K reports it."""
    return k(f"{year_}-10-01", f"{year_}-12-31", value, filed=filed or f"{year_ + 1}-02-15", accn=accn or f"k{year_}")


def by_end(rows, name):
    return {p["end"]: p["values"].get(name) for p in rows}


class AuditRegressionTest(unittest.TestCase):
    """SYNTHETIC payloads only."""

    def test_partial_revenue_concept_never_replaces_the_total(self):
        # GIS / BLK-like: "Revenues" holds a partial line item (10%), annual only; the total is in
        # RevenueFromContractWithCustomerExcludingAssessedTax, annual + quarterly
        total, partial = "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"
        payload = mini({
            total: [row for y in (2021, 2022, 2023) for row in quarters(y, [100 + 10 * (y - 2021)] * 3)
                    + [year(y, 400 + 40 * (y - 2021))]],
            partial: [year(y, 40 + 4 * (y - 2021)) for y in (2021, 2022, 2023)],
            "NetIncomeLoss": [year(y, 10) for y in (2021, 2022, 2023)],
        })
        result = sec.extract_periods(payload)
        self.assertEqual(by_end(result.annual, "revenue"), {"2021-12-31": 400, "2022-12-31": 440, "2023-12-31": 480})
        self.assertEqual(by_end(result.quarterly, "revenue")["2022-12-31"], 110)          # Q4 = 440 − 330
        self.assertEqual(result.concepts["revenue"], [total])
        doc, _ = sec.merge_into_document(None, result, "TEST", NOW)
        self.assertEqual(doc["secConcepts"]["revenue"], [total])
        self.assertNotIn("quarter-annual-mismatch", doc["quality"]["issues"])

    def test_later_filed_recast_concept_wins_over_older_total(self):
        # EBAY-like: "Revenues" (first candidate) keeps the pre-recast years; SalesRevenueNet carries
        # the recast years (filed later) and their restated quarters (3-month values in the 10-K)
        payload = mini({
            "Revenues": [year(2019, 200, filed="2021-02-15", accn="k2020"), year(2020, 220, filed="2021-02-15", accn="k2020")],
            "SalesRevenueNet": [
                year(2019, 100, filed="2022-02-15", accn="k2021"), year(2020, 110, filed="2022-02-15", accn="k2021"),
                year(2021, 121, filed="2022-02-15", accn="k2021"),
                # the restated 2020 quarters, 3-month values in the same 10-K as the recast years
                *[row[:4] + ("2022-02-15", "k2021") for row in quarters(2020, [25, 27, 28], form="10-K")],
                q4(2020, 30, filed="2022-02-15", accn="k2021"),
                *quarters(2021, [25, 30, 30]),
            ],
            "NetIncomeLoss": [year(y, 10) for y in (2019, 2020, 2021)],
        })
        result = sec.extract_periods(payload)
        self.assertEqual(by_end(result.annual, "revenue"), {"2019-12-31": 100, "2020-12-31": 110, "2021-12-31": 121})
        q = by_end(result.quarterly, "revenue")
        self.assertEqual([q["2020-03-31"], q["2020-06-30"], q["2020-09-30"], q["2020-12-31"]], [25, 27, 28, 30])
        self.assertEqual(q["2021-12-31"], 36)
        self.assertEqual(result.concepts["revenue"], ["SalesRevenueNet"])

    def test_restated_quarter_only_in_a_10k_is_used(self):
        # DD / AIG-like: Q1 2023 restated only inside a later 10-K (3-month context); a 10-K 3-month
        # value equal to the year (mis-tagged context, LHX-like) and a lone 3-month value in a 10-K
        # without the year are ignored
        payload = mini({
            "Revenues": [
                *quarters(2023, [1000, 1000, 1000]), year(2023, 4000),
                *quarters(2024, [1000, 1000, 1000]), year(2024, 4000),
                year(2023, 2400, filed="2025-02-15", accn="k2024"),                  # recast year
                k("2023-01-01", "2023-03-31", 600, filed="2025-02-15", accn="k2024"),  # restated Q1
                q4(2024, 4000),                                                     # = the year: mis-tag
                k("2024-07-01", "2024-09-30", 9999, filed="2025-03-01", accn="lone"),  # no year in its filing
            ],
            "NetIncomeLoss": [row for y in (2023, 2024) for row in quarters(y, [1, 1, 1]) + [year(y, 4)]],
        })
        result = sec.extract_periods(payload)
        q = by_end(result.quarterly, "revenue")
        self.assertEqual(q["2023-03-31"], 600)
        self.assertIsNone(q["2023-12-31"])           # 2400 − (600 + 1000 + 1000) < 0 and mixed versions
        self.assertEqual(q["2024-09-30"], 1000)
        self.assertEqual(q["2024-12-31"], 1000)      # 4000 − 3000, the mis-tagged 4000 is not a quarter
        self.assertEqual(by_end(result.annual, "revenue")["2023-12-31"], 2400)

    def test_mixed_version_q4_is_null_not_fabricated(self):
        # DD / AIG / EBAY / APP-like: the annual value was recast after the 9-month YTD was filed
        rows = [
            *quarters(2022, [100, 100, 100]), ytd(2022, 9, 300), year(2022, 400),
            year(2022, 320, filed="2024-02-15", accn="k2023"),         # recast; Q4 would be 20 > 0
            *quarters(2023, [100, 100, 100]), ytd(2023, 9, 300), year(2023, 410),
            *quarters(2024, [100, 150, 100]), ytd(2024, 9, 300), year(2024, 420),   # Q2 ≠ 9M − 6M version
            *quarters(2025, [100, 100, 100]), ytd(2025, 9, 300), year(2025, 250),   # 250 − 300 < 0
        ]
        income = [row for y in range(2022, 2026) for row in quarters(y, [1, 1, 1]) + [year(y, 4)]]
        result = sec.extract_periods(mini({"Revenues": rows, "NetIncomeLoss": income}))
        q = by_end(result.quarterly, "revenue")
        self.assertIsNone(q["2022-12-31"])          # annual version ≠ the one the YTD saw
        self.assertEqual(q["2023-12-31"], 110)      # consistent: derived and flagged
        self.assertIn("q4-derived", result.period_issues[("quarterly", "2023-12-31")])
        self.assertIsNone(q["2024-12-31"])          # Q1+Q2+Q3 = 350 ≠ 9M 300
        self.assertIsNone(q["2025-12-31"])          # negative revenue is never stored
        self.assertTrue(all(v is None or v >= 0 for v in q.values()))

    def test_derived_negative_outflow_is_null_and_direct_sign_flip_is_abs(self):
        capex = "PaymentsToAcquirePropertyPlantAndEquipment"
        rows = [
            # IVZ-like: annual 10 < 9-month 17 → Q4 would be −7: not stored (and never abs()'d to +7)
            k("2023-01-01", "2023-03-31", 5, form="10-Q", filed="2023-05-10", accn="q20230"),
            ytd(2023, 6, 10), ytd(2023, 9, 17), year(2023, 10),
            # a filer tagging payments negative: reported values are abs()'d, differences of them stay positive
            k("2024-01-01", "2024-03-31", -5, form="10-Q", filed="2024-05-10", accn="q20240"),
            ytd(2024, 6, -10), ytd(2024, 9, -15), year(2024, -20),
        ]
        revenue = [row for y in (2023, 2024) for row in quarters(y, [100, 100, 100]) + [year(y, 400)]]
        result = sec.extract_periods(mini({capex: rows, "Revenues": revenue}))
        q = by_end(result.quarterly, "capex")
        self.assertEqual([q["2023-03-31"], q["2023-06-30"], q["2023-09-30"], q["2023-12-31"]], [5, 5, 7, None])
        self.assertEqual([q["2024-03-31"], q["2024-06-30"], q["2024-09-30"], q["2024-12-31"]], [5, 5, 5, 5])
        self.assertEqual(by_end(result.annual, "capex"), {"2023-12-31": 10, "2024-12-31": 20})
        self.assertIn("capex-sign", result.issues)

    def test_outlier_concept_does_not_win(self):
        # ASTS-like: RevenueFromContractWithCustomerExcludingAssessedTax repeats an older year's value
        # (2024 = 2022) and a stray Q1 2023; the Including concept is complete and consistent
        excl, incl = ("RevenueFromContractWithCustomerExcludingAssessedTax",
                      "RevenueFromContractWithCustomerIncludingAssessedTax")
        payload = mini({
            "Revenues": [year(2023, 0), year(2024, 44), *quarters(2024, [5, 9, 11])],
            excl: [*quarters(2021, [15, 15, 15]), year(2021, 60), *quarters(2022, [30, 30, 30]), year(2022, 138),
                   *quarters(2023, [24, None, None]), year(2024, 138)],
            incl: [*quarters(2021, [15, 15, 15]), year(2021, 60), *quarters(2022, [30, 30, 30]), year(2022, 138),
                   *quarters(2023, [0, 0, 0]), year(2023, 0), *quarters(2024, [5, 9, 11]), year(2024, 44)],
            "NetIncomeLoss": [year(y, -10) for y in range(2021, 2025)],
        })
        result = sec.extract_periods(payload)
        self.assertEqual(by_end(result.annual, "revenue")["2024-12-31"], 44)
        q = by_end(result.quarterly, "revenue")
        self.assertEqual(q["2023-03-31"], 0)
        self.assertEqual(q["2024-12-31"], 19)
        self.assertEqual(result.concepts["revenue"], [incl])

    def test_first_quarter_from_restated_half_year(self):
        # APP-like: a later 10-Q restates the 6-month YTD and Q2 (discontinued operation); Q1 was
        # last reported before the recast → Q1 = restated 6M − restated Q2
        rows = [
            k("2024-01-01", "2024-03-31", 1058, form="10-Q", filed="2025-05-07", accn="q2025a"),
            k("2024-04-01", "2024-06-30", 1080, form="10-Q", filed="2024-08-07", accn="q2024b"),
            ytd(2024, 6, 2138, filed="2024-08-07", accn="q2024b"),
            k("2024-04-01", "2024-06-30", 711, form="10-Q", filed="2025-08-06", accn="q2025b"),
            ytd(2024, 6, 1389, filed="2025-08-06", accn="q2025b"),
            # an unrestated half year never replaces a reported Q1 (rounding noise: 6M − Q2 = 101)
            k("2023-01-01", "2023-03-31", 100, form="10-Q", filed="2023-05-10", accn="q2023a"),
            k("2023-04-01", "2023-06-30", 100, form="10-Q", filed="2023-08-10", accn="q2023b"),
            ytd(2023, 6, 201, filed="2023-08-10", accn="q2023b"),
        ]
        result = sec.extract_periods(mini({"Revenues": rows + [year(2023, 400), year(2024, 3000)]}))
        q = {p["end"]: p for p in result.quarterly}
        self.assertEqual(q["2024-03-31"]["values"]["revenue"], 678)
        self.assertEqual(q["2024-03-31"]["extra"]["derived"], 1)
        self.assertEqual(q["2024-06-30"]["values"]["revenue"], 711)
        self.assertEqual(q["2023-03-31"]["values"]["revenue"], 100)
        self.assertIsNone(q["2023-03-31"]["extra"]["derived"])

    def test_quarters_of_two_reporting_calendars_keep_the_later_filed(self):
        # JCI / KDP-like: one CIK carries the predecessor's quarters (ending the 25th) and the
        # registrant's (month ends, filed later as comparatives)
        rows = [
            k("2015-09-26", "2015-12-25", 2376, form="10-Q", filed="2016-02-01", accn="old1"),
            k("2015-10-01", "2015-12-31", 4696, form="10-Q", filed="2017-02-01", accn="new1"),
            k("2016-01-01", "2016-03-31", 4733, form="10-Q", filed="2017-05-01", accn="new2"),
            year(2015, 18000), year(2016, 19000),
        ]
        result = sec.extract_periods(mini({"Revenues": rows}))
        self.assertEqual(result.dropped_quarters, [D("2015-12-25")])
        self.assertNotIn("2015-12-25", by_end(result.quarterly, "revenue"))
        self.assertEqual(by_end(result.quarterly, "revenue")["2015-12-31"], 4696)
        # a file seeded before the guard loses that stored SEC quarter on the next seed (Yahoo ones stay)
        stale = copy.deepcopy(result)
        stale.quarterly.append(fin.period("2015-12-25", {"revenue": 2376}, "sec"))
        stale.dropped_quarters = []
        stored, _ = sec.merge_into_document(None, stale, "TEST", NOW)
        self.assertIn("2015-12-25", stored["quarterly"]["end"])
        doc, info = sec.merge_into_document(stored, result, "TEST", NOW)
        self.assertNotIn("2015-12-25", doc["quarterly"]["end"])
        self.assertEqual(info["counts"]["droppedSec"], 1)
        again, _ = sec.merge_into_document(copy.deepcopy(doc), result, "TEST", NOW)
        self.assertTrue(fin.same_content(doc, again))

    def test_net_income_and_equity_keep_priority_order_fallback_only_fills_gaps(self):
        # ProfitLoss (includes noncontrolling interests) only fills periods NetIncomeLoss lacks, however many
        # periods it covers (user decision: these two fields are exempt from the coverage criterion)
        ni = [year(y, 100) for y in range(2022, 2024)]
        pl = [year(y, 120) for y in range(2014, 2024)]
        result = sec.extract_periods(mini({"NetIncomeLoss": ni, "ProfitLoss": pl, "Revenues": [year(y, 1000) for y in range(2014, 2024)]}))
        values = by_end(result.annual, "netIncome")
        self.assertEqual((values["2023-12-31"], values["2022-12-31"], values["2020-12-31"]), (100, 100, 120))
        self.assertEqual(result.concepts["netIncome"], ["NetIncomeLoss", "ProfitLoss"])

    def test_new_rules_keep_extraction_deterministic_and_merge_idempotent(self):
        payload = mini({
            "Revenues": [*quarters(2022, [100, 100, 100]), ytd(2022, 9, 300), year(2022, 400),
                         year(2022, 320, filed="2024-02-15", accn="k2023"),
                         *quarters(2023, [100, 100, 100]), ytd(2023, 9, 300), year(2023, 410),
                         k("2023-01-01", "2023-03-31", 90, filed="2024-02-15", accn="k2023"), year(2023, 410, accn="k2023",
                                                                                              filed="2024-02-15")],
            "RevenueFromContractWithCustomerExcludingAssessedTax": [year(2022, 40), year(2023, 41)],
            "NetIncomeLoss": [year(y, 10) for y in (2022, 2023)],
        })
        first = sec.extract_periods(copy.deepcopy(payload))
        shuffled = copy.deepcopy(payload)
        for entry in shuffled["facts"]["us-gaap"].values():
            entry["units"]["USD"].reverse()
        second = sec.extract_periods(shuffled)
        self.assertEqual((first.annual, first.quarterly, first.concepts), (second.annual, second.quarterly, second.concepts))
        doc, _ = sec.merge_into_document(None, first, "TEST", NOW)
        again, info = sec.merge_into_document(copy.deepcopy(doc), second, "TEST", NOW + timedelta(days=1))
        self.assertTrue(fin.same_content(doc, again))
        self.assertEqual(info["counts"]["replacedYahoo"], 0)


# ---------------------------------------------------------------------------------------------
# Regression tests for the second audit. Every payload is SYNTHETIC (round made-up numbers); each
# mimics the SHAPE of a real filer's data named in the comment, nothing more.
# ---------------------------------------------------------------------------------------------
SHARES_CONCEPT = "WeightedAverageNumberOfDilutedSharesOutstanding"
EXCL = "RevenueFromContractWithCustomerExcludingAssessedTax"
LEASE = "OperatingLeaseLeaseIncome"


def with_shares(payload, rows):
    """Adds diluted share-count rows (unit "shares") to a mini() payload."""
    payload["facts"]["us-gaap"][SHARES_CONCEPT] = {"units": {"shares": [
        {"start": start, "end": end, "val": value, "form": form, "filed": filed, "accn": accn}
        for start, end, value, form, filed, accn in rows
    ]}}
    return payload


def yahoo_document(annual, quarterly=None, key="TEST"):
    """A SYNTHETIC Yahoo-only stored file: {end: revenue} per section (net income 1 everywhere)."""
    def section(points):
        ends = sorted(points)
        return {"end": ends, "values": {"revenue": [points[e] for e in ends], "netIncome": [1] * len(ends)}}
    return {"version": 1, "symbol": key, "market": "US", "currency": "USD", "fyEndMonth": 12,
            "annual": section(annual), "quarterly": section(quarterly or {})}


def lessor_payload():
    """CPT-like: the contract-revenue tag holds a narrow fee line (~1%), lease income is the total."""
    years = range(2019, 2025)
    return mini({
        EXCL: [row for y in years for row in quarters(y, [2, 2, 2], prefix="e") + [year(y, 8 + y - 2019)]],
        LEASE: [row for y in years for row in quarters(y, [220 + 10 * (y - 2019)] * 3, prefix="l")
                + [year(y, 900 + 50 * (y - 2019))]],
        "NetIncomeLoss": [year(y, 100) for y in years],
    })


LESSOR_YAHOO = {"2022-12-31": 1050, "2023-12-31": 1100, "2024-12-31": 1150}


class SecondAuditRegressionTest(unittest.TestCase):
    """SYNTHETIC payloads only."""

    def test_narrow_refiled_revenue_is_not_adopted_in_any_table(self):
        # CPT-like (ASC 842): later filings re-use the revenue tag of old periods for a narrow line item
        rows = [
            year(2017, 900, filed="2018-02-15", accn="k2017"),
            year(2017, 8, filed="2020-02-15", accn="k2019"),                        # annual table
            *quarters(2018, [220, 230, None]),
            k("2018-04-01", "2018-06-30", 2, form="10-Q", filed="2019-08-01", accn="q2019b"),  # quarter table
            ytd(2018, 6, 450), ytd(2018, 9, 690),
            ytd(2018, 6, 4, filed="2019-08-01", accn="q2019b"),                      # YTD table
            year(2018, 920),
        ]
        income = [year(2017, 100, filed="2018-02-15", accn="k2017"), year(2017, 5, filed="2020-02-15", accn="k2019"),
                  year(2018, 90)]
        payload = mini({"Revenues": rows, "NetIncomeLoss": income})
        result = sec.extract_periods(payload)
        self.assertEqual(by_end(result.annual, "revenue"), {"2017-12-31": 900, "2018-12-31": 920})
        q = by_end(result.quarterly, "revenue")
        self.assertEqual([q["2018-03-31"], q["2018-06-30"], q["2018-09-30"], q["2018-12-31"]], [220, 230, 240, 230])
        # only revenue concepts are guarded: a net income restated down to 5% is adopted as filed
        self.assertEqual(by_end(result.annual, "netIncome")["2017-12-31"], 5)
        # whatever order the facts arrive in
        for entry in payload["facts"]["us-gaap"].values():
            entry["units"]["USD"].reverse()
        again = sec.extract_periods(payload)
        self.assertEqual((again.annual, again.quarterly), (result.annual, result.quarterly))

    def test_ten_k_quarter_equal_to_its_year_is_a_mistag_whatever_quarter(self):
        # HSIC-like: a 10-K tags the full-year value on the Q3 context (end 3 months before the year end)
        payload = with_shares(mini({
            "Revenues": [*quarters(2019, [600, 620, 640]), year(2019, 2500),
                         k("2019-07-01", "2019-09-30", 2500, filed="2020-02-15", accn="k2019")],
        }), [
            # a share count may equal the year's average for real: the 10-K's Q1 value is kept
            k("2019-01-01", "2019-03-31", 101_000_000, form="10-Q", filed="2019-05-10", accn="q20190"),
            k("2019-01-01", "2019-03-31", 100_000_000, filed="2020-02-15", accn="k2019"),
            year(2019, 100_000_000, accn="k2019", filed="2020-02-15"),
        ])
        result = sec.extract_periods(payload)
        q = {p["end"]: p["values"] for p in result.quarterly}
        self.assertEqual(q["2019-09-30"]["revenue"], 640)
        self.assertEqual(q["2019-12-31"]["revenue"], 640)                   # 2500 − 1860
        self.assertEqual(q["2019-03-31"]["dilutedShares"], 100_000_000)

    def test_sixteen_week_quarters_are_quarters(self):
        # KR-like 16/12/12/12-week fiscal year: Q1 lasts 111 days; its 6- and 9-month YTD stay YTD values
        start, ends = "2023-02-05", ["2023-05-27", "2023-08-19", "2023-11-11", "2024-02-03"]
        starts = [start, "2023-05-28", "2023-08-20", "2023-11-12"]
        revenue = [k(starts[i], ends[i], value, form="10-Q", filed=f"2023-{6 + 3 * i:02d}-20", accn=f"q{i}")
                   for i, value in enumerate([400, 300, 300])]
        revenue += [k(start, ends[2], 1000, form="10-Q", filed="2023-12-10", accn="q2"),
                    k(start, ends[3], 1300, filed="2024-03-20", accn="k")]
        cash = [k(start, ends[0], 40, form="10-Q", filed="2023-06-20", accn="q0"),
                k(start, ends[1], 70, form="10-Q", filed="2023-09-20", accn="q1"),
                k(start, ends[2], 100, form="10-Q", filed="2023-12-10", accn="q2"),
                k(start, ends[3], 140, filed="2024-03-20", accn="k")]
        prior = [k("2022-01-30", "2023-02-04", 1200, filed="2023-03-20", accn="k0")]
        result = sec.extract_periods(mini({"Revenues": revenue + prior,
                                           "NetCashProvidedByUsedInOperatingActivities": cash}))
        q = {p["end"]: p["values"] for p in result.quarterly}
        self.assertEqual([q[end]["revenue"] for end in ends], [400, 300, 300, 300])
        self.assertEqual([q[end]["operatingCashFlow"] for end in ends], [40, 30, 30, 40])
        doc, _ = sec.merge_into_document(None, result, "TEST", NOW)
        labels = list(zip(doc["quarterly"]["fy"], doc["quarterly"]["fq"]))
        self.assertEqual(labels[-4:], [(2024, 1), (2024, 2), (2024, 3), (2024, 4)])      # FY named by its end

    def test_yahoo_month_end_labels_of_52_53_week_quarters_are_all_replaced(self):
        # COST / AZO / DPZ-like: Yahoo labels quarters with month ends 11-24 days from the SEC end
        result = sec.extract_periods(mini({"Revenues": [
            *[k(s, e, 300, form="10-Q", filed=f, accn=a) for s, e, f, a in (
                ("2023-01-01", "2023-03-25", "2023-05-01", "q1"), ("2023-03-26", "2023-06-17", "2023-08-01", "q2"),
                ("2023-06-18", "2023-09-09", "2023-11-01", "q3"))],
            k("2023-01-01", "2023-12-30", 1300, filed="2024-02-15", accn="k"),
            k("2022-01-02", "2022-12-31", 1200, filed="2023-02-15", accn="k0"),
        ]}))
        sec_ends = [p["end"] for p in result.quarterly]
        self.assertEqual(sec_ends, ["2023-03-25", "2023-06-17", "2023-09-09", "2023-12-30"])
        stored = yahoo_document({"2023-12-31": 1300}, {
            "2023-03-31": 300, "2023-06-30": 300,           # 6 and 13 days away
            "2023-09-30": 300, "2023-10-03": 300,           # 21 and 24 days from 2023-09-09: one is a re-label
            "2023-12-31": 400,
        })
        doc, info = sec.merge_into_document(stored, result, "TEST", NOW)
        ends = doc["quarterly"]["end"]
        # every Yahoo label of the same fiscal quarter goes (the second one, 2023-10-03, too: B3)
        self.assertEqual(ends, sec_ends)
        self.assertEqual(info["counts"]["replacedYahoo"], 1 + 5)
        labels = list(zip(doc["quarterly"]["fy"], doc["quarterly"]["fq"]))
        self.assertEqual(len(labels), len(set(labels)))
        # annual sections keep the 10-day window (a Yahoo year 12 days off is another period)
        self.assertEqual(sec.YAHOO_MATCH_DAYS, 10)
        self.assertEqual(sec.YAHOO_QUARTER_MATCH_DAYS, 25)

    def test_q4_of_another_scope_in_a_10k_is_rejected(self):
        # GE-like: the 10-K tags a segment's Q4 (118) next to the consolidated year (1470; 9-month YTD 1075)
        payload = mini({
            "Revenues": [*quarters(2012, [350, 360, 365]), ytd(2012, 9, 1075), year(2012, 1470), q4(2012, 118),
                         # in scope: 1500 − 1100 = 400 reported → kept as reported
                         *quarters(2013, [360, 370, 370]), ytd(2013, 9, 1100), year(2013, 1500), q4(2013, 400),
                         # only the 10-K's own quarters as evidence: 1600 − (110 + 115 + 120) ≫ 125 → rejected
                         *[row[:4] + ("2015-02-15", "k2014") for row in quarters(2014, [110, 115, 120], form="10-K")],
                         year(2014, 1600, filed="2015-02-15", accn="k2014"), q4(2014, 125, filed="2015-02-15", accn="k2014")],
            # a Q4 near zero: 12 vs 1000 − 995 = 5 misses by > 25% of itself but by < 1% of the year → kept
            "NetIncomeLoss": [*quarters(2013, [330, 330, 335]), ytd(2013, 9, 995), year(2013, 1000), q4(2013, 12)],
        })
        result = sec.extract_periods(payload)
        q = {p["end"]: p for p in result.quarterly}
        self.assertEqual(q["2012-12-31"]["values"]["revenue"], 395)            # derived instead of 118
        self.assertEqual(q["2012-12-31"]["extra"]["derived"], 1)
        self.assertEqual(q["2013-12-31"]["values"]["revenue"], 400)
        self.assertIsNone(q["2013-12-31"]["extra"]["derived"])
        self.assertEqual(q["2013-12-31"]["values"]["netIncome"], 12)
        self.assertNotEqual(q["2014-12-31"]["values"].get("revenue"), 125)

    def test_new_total_revenue_concepts_fill_where_the_original_four_are_missing(self):
        # MS-like: "Revenues" ends in 2014, total net revenues continue as RevenuesNetOfInterestExpense;
        # DTE-like: RegulatedAndUnregulatedOperatingRevenue; a lessor's lease income is never used alone
        net = "RevenuesNetOfInterestExpense"
        payload = mini({
            "Revenues": [year(y, 300 + y - 2013) for y in (2013, 2014)],
            net: [year(y, 300 + y - 2013) for y in range(2013, 2018)],
            LEASE: [year(y, 7) for y in range(2013, 2018)],
            "NetIncomeLoss": [year(y, 10) for y in range(2013, 2018)],
        })
        result = sec.extract_periods(payload)
        self.assertEqual(by_end(result.annual, "revenue"),
                         {f"{y}-12-31": 300 + y - 2013 for y in range(2013, 2018)})
        self.assertNotIn(LEASE, result.concepts["revenue"])
        lease_only = mini({LEASE: [year(y, 7) for y in range(2015, 2018)],
                           "NetIncomeLoss": [year(y, 10) for y in range(2015, 2018)]})
        self.assertNotIn("revenue", sec.extract_periods(lease_only).concepts)

    def test_yahoo_arbiter_picks_the_total_and_only_the_concept(self):
        payload = lessor_payload()
        # no reference: the concept rules alone (narrow fees), nothing stored
        plain = sec.extract_periods(payload)
        self.assertEqual(by_end(plain.annual, "revenue")["2024-12-31"], 13)
        self.assertEqual(plain.arbiter, {})
        doc, _ = sec.merge_into_document(None, plain, "TEST", NOW)
        self.assertNotIn("arbiter", doc["secConcepts"])
        # with Yahoo's annual revenue: lease income is the series, every value still SEC's
        result = sec.extract_periods(payload, {"annual": LESSOR_YAHOO})
        self.assertEqual(by_end(result.annual, "revenue"), {f"{y}-12-31": 900 + 50 * (y - 2019) for y in range(2019, 2025)})
        self.assertEqual(by_end(result.quarterly, "revenue")["2024-12-31"], 1150 - 3 * 270)
        self.assertEqual(result.arbiter["revenue"][0], LEASE)
        self.assertEqual(result.arbiter["revenue"][-1], EXCL)              # contradicted by Yahoo: last
        doc, _ = sec.merge_into_document(None, result, "TEST", NOW)
        self.assertEqual(doc["secConcepts"]["arbiter"], result.arbiter)
        # a reference that cannot judge (no overlap) changes nothing
        far = sec.extract_periods(payload, {"annual": {"2010-12-31": 5}})
        self.assertEqual((far.annual, far.arbiter), (plain.annual, {}))

    def test_arbiter_decision_is_stored_and_reseeding_is_idempotent(self):
        payload = lessor_payload()
        stored = yahoo_document(dict(LESSOR_YAHOO, **{"2025-12-31": 1200}))
        reference = sec.reference_from_document(stored)
        self.assertEqual(reference["annual"], dict(LESSOR_YAHOO, **{"2025-12-31": 1200}))
        doc1, _ = sec.merge_into_document(stored, sec.extract_periods(payload, reference), "TEST", NOW)
        self.assertEqual(doc1["annual"]["src"], ["sec"] * 6 + ["yahoo"])
        # the re-seed sees no overlapping Yahoo year any more: the stored decision is reused
        again = sec.reference_from_document(doc1)
        self.assertEqual(again["annual"], {"2025-12-31": 1200})
        self.assertEqual(again["arbiter"], doc1["secConcepts"]["arbiter"])
        doc2, info = sec.merge_into_document(copy.deepcopy(doc1), sec.extract_periods(payload, again), "TEST",
                                             NOW + timedelta(days=1))
        self.assertTrue(fin.same_content(doc1, doc2))
        self.assertEqual(info["counts"]["replacedYahoo"], 0)
        # one new overlapping Yahoo year (a later 10-K) does not overturn the stored decision
        later = copy.deepcopy(again)
        later["annual"] = {"2024-12-31": 13}
        self.assertEqual(sec.extract_periods(payload, later).arbiter, doc1["secConcepts"]["arbiter"])
        # a stored decision whose first concept has no data any more is ignored (concept rules)
        gone = {"arbiter": {"revenue": ["SalesRevenueNet", EXCL]}}
        self.assertEqual(sec.extract_periods(payload, gone).annual, sec.extract_periods(payload).annual)

    def test_files_seeded_before_the_arbiter_use_their_yahoo_differs_revisions(self):
        payload = lessor_payload()
        stored = yahoo_document(LESSOR_YAHOO)
        old_seed, _ = sec.merge_into_document(stored, sec.extract_periods(payload), "TEST", NOW)
        differs = {r["end"]: r["new"] for r in old_seed["quality"]["revisions"] if r["field"] == "revenue"}
        self.assertEqual(differs, LESSOR_YAHOO)
        reference = sec.reference_from_document(old_seed)
        self.assertEqual((reference["annual"], reference["differs"]), ({}, LESSOR_YAHOO))
        doc1, _ = sec.merge_into_document(copy.deepcopy(old_seed), sec.extract_periods(payload, reference), "TEST", NOW)
        self.assertEqual(by_end(fin.read_sections(doc1)["annual"], "revenue")["2024-12-31"], 1150)
        doc2, _ = sec.merge_into_document(copy.deepcopy(doc1), sec.extract_periods(
            payload, sec.reference_from_document(doc1)), "TEST", NOW + timedelta(days=1))
        self.assertTrue(fin.same_content(doc1, doc2))

    def test_arbiter_grey_zone_and_unjudged_old_concepts(self):
        years = range(2019, 2025)
        # HAS-like: "Revenues" files 14% above Yahoo, the contract-revenue concept matches it
        payload = mini({
            "Revenues": [row for y in years for row in quarters(y, [114] * 3) + [year(y, 456)]],
            EXCL: [row for y in years for row in quarters(y, [100] * 3) + [year(y, 400)]],
            "NetIncomeLoss": [year(y, 10) for y in years],
        })
        yahoo = {f"{y}-12-31": 400 for y in (2022, 2023, 2024)}
        result = sec.extract_periods(payload, {"annual": yahoo})
        self.assertEqual(result.arbiter["revenue"][0], EXCL)
        self.assertEqual(set(by_end(result.annual, "revenue").values()), {400})
        # Yahoo defining revenue otherwise (every concept > 25% off): the concept rules' order is kept
        # (and stored, so a re-seed repeats it)
        other = sec.extract_periods(payload, {"annual": {e: 200 for e in yahoo}})
        self.assertEqual(other.annual, sec.extract_periods(payload).annual)
        self.assertEqual(other.arbiter["revenue"][0], sec.extract_periods(payload).concepts["revenue"][0])
        # PG / LHX-like: the rules' primary (SalesRevenueNet, quarters included) ended before Yahoo's
        # years; "Revenues" is narrow in its old years. Yahoo cannot judge the old years: kept as they were
        old = mini({
            "SalesRevenueNet": [row for y in range(2015, 2019) for row in quarters(y, [200] * 3) + [year(y, 800)]],
            "Revenues": [year(2015, 28), year(2016, 29)] + [year(y, 800 + y) for y in range(2017, 2025)],
            "NetIncomeLoss": [year(y, 10) for y in range(2015, 2025)],
        })
        judged = sec.extract_periods(old, {"annual": {f"{y}-12-31": 800 + y for y in (2022, 2023, 2024)}})
        self.assertEqual(by_end(judged.annual, "revenue"), by_end(sec.extract_periods(old).annual, "revenue"))
        self.assertEqual(by_end(judged.annual, "revenue")["2015-12-31"], 800)

    def test_cli_reseed_with_force_is_unchanged_after_arbitration(self):
        runner = Runner(self)
        write_json(runner.path("CPTX.json"), yahoo_document(LESSOR_YAHOO, key="CPTX"))
        fake = world({"CPTX": (777, json.dumps(lessor_payload()).encode("utf-8"))})
        code, text = runner.run(fake, "--tickers", "CPTX", "--no-splits")
        self.assertEqual(code, 0, text)
        self.assertIn("CPTX written", text)
        first = read_bytes(runner.path("CPTX.json"))
        document = json.loads(first)
        self.assertEqual(document["secConcepts"]["arbiter"]["revenue"][0], LEASE)
        self.assertEqual(dict(zip(document["annual"]["end"], document["annual"]["values"]["revenue"]))["2024-12-31"], 1150)
        code, text = runner.run(fake, "--tickers", "CPTX", "--no-splits", "--force", now=NOW + timedelta(days=3))
        self.assertEqual(code, 0, text)
        self.assertIn("CPTX unchanged", text)
        self.assertEqual(first, read_bytes(runner.path("CPTX.json")))


# ---------------------------------------------------------------------------------------------
# Merge into stored documents, quality gates
# ---------------------------------------------------------------------------------------------
class MergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = sec.extract_periods(facts())
        cls.doc0, cls.info0 = sec.merge_into_document(None, cls.result, "AAPL", NOW)
        cls.yahoo = fixture("sample-AAPL.json")
        cls.doc1, cls.info1 = sec.merge_into_document(copy.deepcopy(cls.yahoo), cls.result, "AAPL", NOW)

    def test_fresh_document_schema(self):
        doc = self.doc0
        self.assertEqual(self.info0["status"], "ok")
        self.assertEqual(list(doc)[:13], list(fin.DOCUMENT_KEYS))
        self.assertEqual(list(doc)[13:], ["secConcepts"])
        self.assertEqual((doc["symbol"], doc["market"], doc["currency"], doc["foreignReporter"]),
                         ("AAPL", "US", "USD", False))
        self.assertEqual(doc["source"], fin.SOURCE_MIXED)
        self.assertEqual(doc["generatedAt"], "2026-10-11T09:00:00Z")
        self.assertEqual(doc["checkedAt"], TODAY)
        self.assertEqual(doc["fyEndMonth"], 9)
        self.assertEqual(doc["quality"]["level"], "good")
        self.assertEqual(doc["quality"]["issues"], ["debt-approx", "q4-derived"])
        self.assertEqual(doc["annual"]["src"], ["sec"] * 5)
        self.assertEqual(doc["quarterly"]["src"], ["sec"] * 23)
        self.assertEqual(doc["quarterly"]["fq"][:4], [1, 2, 3, 4])
        labels = dict(zip(doc["quarterly"]["end"], zip(doc["quarterly"]["fy"], doc["quarterly"]["fq"])))
        self.assertEqual(labels["2022-12-31"], (2023, 1))           # 53-week FY2023
        self.assertEqual(labels["2023-09-30"], (2023, 4))
        sections = fin.read_sections(json.loads(json.dumps(doc)))   # loads through the reader without error
        self.assertEqual(len(sections["annual"]), 5)
        self.assertEqual(len(sections["quarterly"]), 23)
        self.assertEqual(doc["quarterly"]["derived"][3], 1)

    def test_merge_is_deterministic_and_idempotent(self):
        again, _ = sec.merge_into_document(None, self.result, "AAPL", NOW + timedelta(days=3))
        self.assertTrue(fin.same_content(self.doc0, again))
        over_itself, info = sec.merge_into_document(copy.deepcopy(self.doc0), self.result, "AAPL", NOW + timedelta(days=3))
        self.assertTrue(fin.same_content(self.doc0, over_itself))
        self.assertEqual(info["counts"]["replacedSec"], 28)
        self.assertEqual(info["counts"]["replacedYahoo"], 0)

    def test_previous_document_is_not_modified(self):
        yahoo = fixture("sample-AAPL.json")
        before = copy.deepcopy(yahoo)
        sec.merge_into_document(yahoo, self.result, "AAPL", NOW)
        self.assertEqual(yahoo, before)

    def test_merge_over_yahoo_file(self):
        doc, info = self.doc1, self.info1
        self.assertEqual((info["status"], info["previous"]), ("ok", "ok"))
        self.assertEqual(doc["annual"]["end"], [FYS[y][1][3] for y in range(2020, 2025)] + ["2025-09-27"])
        self.assertEqual(doc["annual"]["src"], ["sec"] * 5 + ["yahoo"])
        self.assertEqual(doc["quarterly"]["src"], ["sec"] * 23 + ["yahoo"] * 4)
        # newer Yahoo-only periods are preserved
        self.assertEqual(doc["quarterly"]["end"][-4:], ["2025-09-27", "2025-12-27", "2026-03-28", "2026-06-27"])
        revenue = dict(zip(doc["annual"]["end"], doc["annual"]["values"]["revenue"]))
        self.assertEqual(revenue["2022-09-24"], annual("rev", 2022))            # SEC value wins
        self.assertEqual(revenue["2025-09-27"], 416 * B)                         # Yahoo-only year kept
        fcf = dict(zip(doc["annual"]["end"], doc["annual"]["values"]["fcfYahoo"]))
        self.assertIsNone(fcf["2024-09-28"])                                    # no Yahoo values copied into SEC periods
        self.assertEqual(fcf["2025-09-27"], 108160000000)
        self.assertEqual(doc["_fixture"], self.yahoo["_fixture"])               # unknown top-level keys survive
        self.assertEqual(list(doc).index("secConcepts"), len(doc) - 1)
        self.assertEqual(self.info1["counts"]["replacedYahoo"], 4 + 5)          # 4 annual + 5 quarterly overlaps

    def test_yahoo_month_end_label_of_52_53_week_year_is_not_duplicated(self):
        # Yahoo labels the SEC 2022-09-24 year end as 2022-09-30 (6 days apart, beyond SAME_PERIOD_DAYS)
        prev = fixture("sample-AAPL.json")
        index = prev["annual"]["end"].index("2022-09-24")
        prev["annual"]["end"][index] = "2022-09-30"
        doc, info = sec.merge_into_document(prev, self.result, "AAPL", NOW)
        self.assertNotIn("2022-09-30", doc["annual"]["end"])
        self.assertIn("2022-09-24", doc["annual"]["end"])
        self.assertEqual(len(doc["annual"]["end"]), len(set(doc["annual"]["end"])))
        self.assertEqual(info["counts"]["replacedYahoo"], self.info1["counts"]["replacedYahoo"])
        # a Yahoo year 12 days away is beyond the annual window, but overlaps the SEC year: it is not
        # kept next to it (B3, never two overlapping periods)
        prev = fixture("sample-AAPL.json")
        prev["annual"]["end"][index] = "2022-10-06"
        doc, info = sec.merge_into_document(prev, self.result, "AAPL", NOW)
        self.assertNotIn("2022-10-06", doc["annual"]["end"])
        self.assertEqual(info["counts"]["droppedOverlap"], 1)

    def test_yahoo_differs_revision_over_two_percent_only(self):
        revisions = self.doc1["quality"]["revisions"]
        kinds = {(r["kind"], r["period"], r["end"], r["field"]) for r in revisions}
        self.assertIn(("yahoo-differs", "annual", "2022-09-24", "revenue"), kinds)
        self.assertNotIn(("yahoo-differs", "annual", "2024-09-28", "revenue"), kinds)       # within 2 %
        r = next(r for r in revisions if r["end"] == "2022-09-24" and r["field"] == "revenue")
        # same orientation as build_financials.merge_section: old = SEC value, new = Yahoo value
        self.assertEqual((r["old"], r["new"], r["seen"]), (annual("rev", 2022), 394 * B, TODAY))
        self.assertIn("yahoo-differs", self.doc1["quality"]["issues"])
        self.assertEqual(self.doc1["quality"]["issues"][-2:], ["debt-approx", "q4-derived"])   # SEC-only codes last

    def test_second_seed_over_merged_file_is_stable(self):
        doc2, info2 = sec.merge_into_document(copy.deepcopy(self.doc1), self.result, "AAPL", NOW + timedelta(days=1))
        self.assertTrue(fin.same_content(self.doc1, doc2))
        self.assertEqual(info2["counts"]["replacedYahoo"], 0)
        self.assertEqual(info2["counts"]["replacedSec"], 28)

    def test_yahoo_run_never_overwrites_sec_periods(self):
        before = fin.read_sections(self.doc1)
        sec_before = {(f, p["end"]): dict(p["values"]) for f in ("annual", "quarterly")
                      for p in before[f] if p["src"] == "sec"}
        payload = fixture("yahoo_us_full.json")
        updated, _ = fin.update_document(copy.deepcopy(self.doc1), payload, "AAPL", NOW + timedelta(days=2))
        after = fin.read_sections(updated)
        for frequency in ("annual", "quarterly"):
            for p in after[frequency]:
                if (frequency, p["end"]) in sec_before:
                    self.assertEqual(p["src"], "sec", p["end"])
                    for field, value in sec_before[(frequency, p["end"])].items():
                        self.assertEqual(p["values"].get(field), value, (p["end"], field))
        self.assertEqual(updated["annual"]["src"][:5], ["sec"] * 5)
        self.assertEqual(len(sec_before), 28)
        self.assertEqual(sum(1 for f in ("annual", "quarterly") for p in after[f] if p["src"] == "sec"), 28)
        self.assertEqual(updated["secConcepts"], self.doc1["secConcepts"])
        self.assertIn("derived", updated["quarterly"])
        # SEC seeding on top of the Yahoo-updated file lands on the same content again
        doc4, _ = sec.merge_into_document(copy.deepcopy(updated), self.result, "AAPL", NOW + timedelta(days=3))
        doc5, _ = sec.merge_into_document(copy.deepcopy(doc4), self.result, "AAPL", NOW + timedelta(days=4))
        self.assertTrue(fin.same_content(doc4, doc5))

    def test_corrupt_or_foreign_previous_is_flagged(self):
        bad = copy.deepcopy(self.doc1)
        bad["annual"]["src"] = ["sec"]
        doc, info = sec.merge_into_document(bad, self.result, "AAPL", NOW)
        self.assertEqual((info["status"], info["previous"]), ("corrupt-previous", "corrupt"))
        self.assertNotIn("_fixture", doc)                      # nothing of a broken file is carried over
        other = copy.deepcopy(self.doc1)
        other["symbol"] = "MSFT"
        self.assertEqual(sec.merge_into_document(other, self.result, "AAPL", NOW)[1]["status"], "corrupt-previous")
        eur = fixture("sample-AAPL.json")
        eur["currency"] = "EUR"
        self.assertIsNone(sec.merge_into_document(eur, self.result, "AAPL", NOW)[0])
        self.assertIsNone(sec.merge_into_document(None, self.result, "2330.TW", NOW)[0])

    def test_quality_partial_with_fewer_than_four_annual_years(self):
        result = sec.extract_periods(three_year_payload())
        self.assertEqual(len(result.annual), 3)
        doc, info = sec.merge_into_document(None, result, "TEST", NOW)
        self.assertEqual(doc["quality"]["level"], "partial")
        self.assertIn("short-history", doc["quality"]["issues"])
        self.assertEqual(info["level"], "partial")

    def test_quality_gate_four_years_of_each_field(self):
        gated = copy.deepcopy(self.result)
        for p in gated.annual[:2]:                    # only 3 years of operating cash flow
            p["values"].pop("operatingCashFlow")
        doc, _ = sec.merge_into_document(None, gated, "AAPL", NOW)
        self.assertEqual(doc["quality"]["level"], "partial")
        self.assertEqual(self.doc0["quality"]["level"], "good")

    def test_quality_limited_with_quarter_annual_mismatch(self):
        # A reported FY2022 Q4 (a 3-month value in the 10-K) that does not fit the annual value
        payload = facts()
        concept = "RevenueFromContractWithCustomerExcludingAssessedTax"
        points = payload["facts"]["us-gaap"][concept]["units"]["USD"]
        k10 = max((p for p in points if p["form"] == "10-K" and p["end"] == "2022-09-24" and p["start"] == "2021-09-26"),
                  key=lambda p: p["filed"])         # the FY2024 10-K, which also reports FY2022
        # CHANGED (Q4 scope check): a Q4 twice what the 10-K's year leaves after nine months is another
        # scope and is rejected; Q4 is then derived from the year and the 9-month YTD as usual
        doubled = copy.deepcopy(payload)
        doubled["facts"]["us-gaap"][concept]["units"]["USD"].append(dict(k10, start=q_start(2022, 3), val=rev(2022, 3) * 2))
        q = {p["end"]: p for p in sec.extract_periods(doubled).quarterly}["2022-09-24"]
        self.assertNotEqual(q["values"]["revenue"], rev(2022, 3) * 2)
        self.assertEqual(q["extra"]["derived"], 1)
        # 24% too high: still in the year's scope (25%), but four quarters miss the year by > 5%
        points.append(dict(k10, start=q_start(2022, 3), val=round(rev(2022, 3) * 1.24)))
        result = sec.extract_periods(payload)
        self.assertEqual({p["end"]: p["values"]["revenue"] for p in result.quarterly}["2022-09-24"],
                         round(rev(2022, 3) * 1.24))
        doc, _ = sec.merge_into_document(None, result, "AAPL", NOW)
        self.assertIn("quarter-annual-mismatch", doc["quality"]["issues"])
        self.assertEqual(doc["quality"]["level"], "limited")
        # CHANGED (rule C): a doubled Q2 3-month value no longer yields a Q4 that absorbs the error
        # (Q1+Q2+Q3 ≠ 9-month YTD → no Q4 at all instead of a fabricated one)
        payload = facts()
        for point in payload["facts"]["us-gaap"][concept]["units"]["USD"]:
            if point["end"] == "2022-03-26" and point["start"] == "2021-12-26":    # FY2022 Q2, 3M value only
                point["val"] = point["val"] * 2
        q = {p["end"]: p["values"] for p in sec.extract_periods(payload).quarterly}
        self.assertNotIn("revenue", q["2022-09-24"])
        self.assertEqual(q["2022-09-24"]["netIncome"], annual("ni", 2022, True) - sum(item("ni", 2022, i) for i in range(3)))

    def test_eps_inconsistent_flagged(self):
        bad = copy.deepcopy(self.result)
        bad.annual[1]["values"]["epsDiluted"] *= 2
        doc, _ = sec.merge_into_document(None, bad, "AAPL", NOW)
        self.assertIn("eps-inconsistent", doc["quality"]["issues"])
        self.assertNotIn("eps-inconsistent", self.doc0["quality"]["issues"])
        self.assertTrue(sec.eps_inconsistent({"epsDiluted": 1.0, "netIncome": 100, "dilutedShares": 50}))
        self.assertFalse(sec.eps_inconsistent({"epsDiluted": 1.0, "netIncome": 100, "dilutedShares": 100}))
        self.assertFalse(sec.eps_inconsistent({"epsDiluted": 1.0}))

    def test_negative_equity_flagged(self):
        negative = copy.deepcopy(self.result)
        negative.annual[-1]["values"]["equity"] = -5 * B
        doc, _ = sec.merge_into_document(None, negative, "AAPL", NOW)
        self.assertIn("negative-equity", doc["quality"]["issues"])
        self.assertNotIn("negative-equity", self.doc0["quality"]["issues"])

    def test_retention_20_annual_60_quarterly(self):
        many = copy.deepcopy(self.result)
        many.annual = [fin.period("%d-09-28" % y, {"revenue": y, "netIncome": 1}, "sec") for y in range(1995, 2020)] + many.annual
        many.quarterly = [
            fin.period((D("2000-01-01") + timedelta(days=91 * n)).isoformat(), {"revenue": n + 1}, "sec")
            for n in range(60)
        ] + many.quarterly
        doc, info = sec.merge_into_document(None, many, "AAPL", NOW)
        self.assertEqual(len(doc["annual"]["end"]), 20)
        self.assertEqual(doc["annual"]["end"][0], "2005-09-28")
        self.assertEqual(doc["annual"]["end"][-1], "2024-09-28")
        self.assertEqual(len(doc["quarterly"]["end"]), 60)
        self.assertEqual(doc["quarterly"]["end"][-1], "2025-06-28")
        self.assertEqual(info["counts"]["trimmedSec"], 10 + 23)
        self.assertEqual((fin.KEEP_ANNUAL, fin.KEEP_QUARTERLY), (20, 60))

    def test_stored_sec_period_missing_from_new_run_is_kept(self):
        older = copy.deepcopy(self.doc0)
        older["annual"]["end"].insert(0, "2018-09-29")
        older["annual"]["fy"].insert(0, 2018)
        older["annual"]["src"].insert(0, "sec")
        for column in older["annual"]["values"].values():
            column.insert(0, None)
        older["annual"]["values"]["revenue"][0] = 1
        doc, info = sec.merge_into_document(older, self.result, "AAPL", NOW)
        self.assertEqual(doc["annual"]["end"][0], "2018-09-29")
        self.assertEqual(info["counts"]["keptSec"], 1)
        again, _ = sec.merge_into_document(copy.deepcopy(doc), self.result, "AAPL", NOW)
        self.assertTrue(fin.same_content(doc, again))


# ---------------------------------------------------------------------------------------------
# main(): the CLI end to end
# ---------------------------------------------------------------------------------------------
class MainTest(unittest.TestCase):
    ENTRIES = {
        "AAPL": (320193, SYNTHETIC_BYTES),
        "MSFT": (789019, SYNTHETIC_BYTES),
        "BRK-B": (1067983, SYNTHETIC_BYTES),
        "TSM": (1046179, FOREIGN_BYTES),
        "GONE": (999, 404),
    }

    def test_successor_registrant_without_history_falls_back_to_predecessor_cik(self):
        # synthetic: XOM's SEC ticker entry now points at a holding company with a single 10-K year
        successor = mini({"Revenues": [k("2025-01-01", "2025-12-31", 121, filed="2026-02-01")]})
        entries = {"XOM": (2115436, successor), "OLD": (34088, SYNTHETIC_BYTES)}
        runner = Runner(self)
        with mock.patch.dict(sec.PREDECESSOR_CIKS, {}, clear=True):
            code, text = runner.run(world(entries), "--tickers", "XOM", "--no-splits")
        self.assertEqual(code, 0, text)
        self.assertIn("XOM skipped (short-history)", text)
        self.assertEqual(runner.files(), [])
        fake = world(entries)
        with mock.patch.dict(sec.PREDECESSOR_CIKS, {"XOM": "0000034088"}, clear=True):
            code, text = runner.run(fake, "--tickers", "XOM", "--no-splits")
        self.assertEqual(code, 0, text)
        self.assertIn("XOM written", text)
        self.assertEqual(read_json(runner.path("XOM.json"))["symbol"], "XOM")
        self.assertIn("https://data.sec.gov/api/xbrl/companyfacts/CIK0000034088.json", fake.urls())

    def test_end_to_end_new_files(self):
        runner = Runner(self)
        fake = world(self.ENTRIES)
        code, text = runner.run(fake, "--tickers", "aapl, MSFT,brk.b,TSM,GONE,NOCIK,2330.TW,^bad")
        self.assertEqual(code, 0, text)
        self.assertEqual(runner.files(), ["AAPL.json", "BRK-B.json", "MSFT.json"])    # no foreign / 404 / tmp files
        for key in ("AAPL", "MSFT", "BRK-B"):
            document = read_json(runner.path(key + ".json"))
            self.assertEqual(document["symbol"], key)
            self.assertEqual(list(document)[:13], list(fin.DOCUMENT_KEYS))
            fin.read_sections(document)
            self.assertEqual(fin.load_document(runner.path(key + ".json"), key)[0], "ok")
        # progress lines: index/total, key, result, counts, level
        self.assertIn("[1/6] AAPL written annual=5 quarters=23 level=good (new file)", text)
        self.assertIn("[3/6] BRK-B written", text)
        self.assertIn("[4/6] TSM skipped (foreign-filer)", text)
        self.assertIn("[5/6] GONE no-facts", text)
        self.assertIn("[6/6] NOCIK no-cik", text)
        self.assertIn("2330.TW not-us", text)
        self.assertIn("^bad bad-ticker", text)
        self.assertIn("Reminder: estimated duration", text)
        self.assertIn("written: 3", text)
        self.assertIn("skip reason foreign-filer: 1", text)
        self.assertIn("index.json: absent", text)           # never created
        self.assertRegex(text, r"written \d{5,} bytes")
        # requests: tickers map + facts of the 5 tickers having a CIK, in order, zero padded
        self.assertEqual(fake.urls(), [
            sec.TICKERS_URL,
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json",
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0001067983.json",
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0001046179.json",
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000999.json",
        ])
        self.assertEqual(runner.sleeps, [sec.MIN_INTERVAL] * 6)
        # written with the Yahoo flow's writer: compact JSON + newline
        with open(runner.path("AAPL.json"), encoding="utf-8") as file:
            body = file.read()
        self.assertTrue(body.endswith("}\n"))
        self.assertEqual(body, json.dumps(json.loads(body), ensure_ascii=False, separators=(",", ":")) + "\n")
        self.assertFalse(os.path.exists(runner.report))

    def test_second_run_skips_and_force_is_idempotent(self):
        runner = Runner(self)
        fake = world(self.ENTRIES)
        runner.run(fake, "--tickers", "AAPL,MSFT")
        first = {name: read_bytes(runner.path(name)) for name in runner.files()}
        before = len(fake.requests)
        code, text = runner.run(fake, "--tickers", "AAPL,MSFT", now=NOW + timedelta(days=9))
        self.assertEqual(code, 0)
        self.assertIn("AAPL already-sec", text)
        self.assertIn("already-sec: 2", text)
        self.assertEqual(len(fake.requests) - before, 1)       # only the ticker map
        self.assertEqual(first, {name: read_bytes(runner.path(name)) for name in runner.files()})
        # --force fetches again; identical content → the files are not rewritten at all
        code, text = runner.run(fake, "--tickers", "AAPL,MSFT", "--force", now=NOW + timedelta(days=9))
        self.assertEqual(code, 0)
        self.assertIn("AAPL unchanged", text)
        self.assertEqual(first, {name: read_bytes(runner.path(name)) for name in runner.files()})
        # a fresh run elsewhere on another day equals the first one apart from the run timestamps
        other = Runner(self)
        other.run(fake, "--tickers", "AAPL,MSFT", now=NOW + timedelta(days=40))
        for key in ("AAPL", "MSFT"):
            self.assertEqual(strip(read_json(other.path(key + ".json"))), strip(read_json(runner.path(key + ".json"))))
            self.assertNotEqual(read_json(other.path(key + ".json"))["generatedAt"],
                                read_json(runner.path(key + ".json"))["generatedAt"])

    def test_merge_into_existing_yahoo_file_keeps_cadence(self):
        runner = Runner(self)
        yahoo = fixture("sample-AAPL.json")
        yahoo["checkedAt"] = "2026-09-20"
        write_json(runner.path("AAPL.json"), yahoo)
        fake = world(self.ENTRIES)
        code, text = runner.run(fake, "--tickers", "AAPL")
        self.assertEqual(code, 0)
        self.assertIn("AAPL written", text)
        self.assertIn("(merged into existing file)", text)
        document = read_json(runner.path("AAPL.json"))
        self.assertEqual(document["annual"]["src"], ["sec"] * 5 + ["yahoo"])
        self.assertEqual(document["checkedAt"], "2026-09-20")          # Yahoo cadence untouched
        self.assertEqual(document["generatedAt"], "2026-10-11T09:00:00Z")
        # Yahoo-only newer periods survive, the Yahoo run keeps SEC periods afterwards
        updated, _ = fin.update_document(document, fixture("yahoo_us_full.json"), "AAPL", NOW + timedelta(days=1))
        self.assertEqual(updated["annual"]["src"][:5], ["sec"] * 5)

    def test_index_json_only_touches_written_entries(self):
        runner = Runner(self)
        write_json(runner.path("AAPL.json"), dict(fixture("sample-AAPL.json"), checkedAt="2026-09-20"))
        index = {
            "version": 1, "generatedAt": "2026-10-01T00:00:00Z",
            "items": {
                "AAPL": {"market": "US", "currency": "USD", "latestAnnual": "2025-09-27",
                         "latestQuarter": "2026-06-27", "checkedAt": "2026-09-20", "level": "good",
                         "lastListed": "2026-07-01"},
                "OTHER": {"market": "US", "currency": "USD", "latestAnnual": None, "latestQuarter": None,
                          "checkedAt": "2026-08-01", "level": "limited", "lastListed": "2026-08-02"},
            },
            "misses": {"NOPE": "2026-09-01"},
            "failures": {"BAD": "2026-09-02"},
        }
        write_json(runner.path("index.json"), index)
        fake = world(self.ENTRIES)
        code, text = runner.run(fake, "--tickers", "AAPL,MSFT,TSM")
        self.assertEqual(code, 0)
        self.assertIn("index.json: updated (2 entries)", text)
        after = read_json(runner.path("index.json"))
        self.assertEqual(after["items"]["OTHER"], index["items"]["OTHER"])
        self.assertEqual((after["version"], after["generatedAt"], after["misses"], after["failures"]),
                         (1, index["generatedAt"], index["misses"], index["failures"]))
        self.assertEqual(sorted(after["items"]), ["AAPL", "MSFT", "OTHER"])
        aapl = read_json(runner.path("AAPL.json"))
        self.assertEqual(after["items"]["AAPL"], fin.index_item(aapl, "2026-09-20", "2026-07-01"))
        self.assertEqual(after["items"]["AAPL"]["lastListed"], "2026-07-01")           # preserved
        self.assertEqual(after["items"]["AAPL"]["checkedAt"], "2026-09-20")
        self.assertEqual(after["items"]["AAPL"]["latestQuarter"], "2026-06-27")
        self.assertEqual(after["items"]["MSFT"]["lastListed"], TODAY)                   # new entry: today
        self.assertEqual(after["items"]["MSFT"]["level"], "good")
        self.assertNotIn("TSM", after["items"])
        self.assertEqual(runner.files(), ["AAPL.json", "MSFT.json", "index.json"])
        # an unreadable index.json is left alone
        write_json(runner.path("index.json"), ["not", "an", "index"])
        os.remove(runner.path("MSFT.json"))
        code, text = runner.run(fake, "--tickers", "MSFT")
        self.assertIn("index.json: unreadable", text)
        self.assertEqual(read_json(runner.path("index.json")), ["not", "an", "index"])

    def test_corrupt_previous_file_is_never_overwritten(self):
        runner = Runner(self)
        os.makedirs(runner.out)
        garbage = '{"version": 1, "annual": {"end": ["2024-12-31"], "values": {"revenue": []}}}'
        with open(runner.path("AAPL.json"), "w", encoding="utf-8") as file:
            file.write(garbage)
        other = fixture("sample-AAPL.json")
        other["symbol"] = "AAPL"
        write_json(runner.path("MSFT.json"), other)               # MSFT.json holding another stock's data
        fake = world(self.ENTRIES)
        code, text = runner.run(fake, "--tickers", "AAPL,MSFT", "--force")
        self.assertEqual(code, 0)
        self.assertIn("AAPL corrupt-previous", text)
        self.assertIn("MSFT corrupt-previous", text)
        self.assertEqual(read_bytes(runner.path("AAPL.json")).decode("utf-8"), garbage)
        self.assertEqual(read_json(runner.path("MSFT.json")), other)
        self.assertEqual(fake.urls(), [sec.TICKERS_URL])           # not even fetched

    def test_symbol_list_comes_from_collect_symbols_and_limit(self):
        runner = Runner(self)
        write_json(os.path.join(runner.report, "latest.json"), {"groups": [
            {"items": [{"symbol": "2330.TW"}, {"symbol": "MSFT"}, {"symbol": "brk.b"}]}]})
        write_json(os.path.join(runner.report, "themes.json"), {"themes": [{"constituents": [{"symbol": "AAPL"}, {"symbol": "MSFT"}]}]})
        write_json(os.path.join(runner.report, "universe.json"), {"fields": ["symbol", "name"], "markets": {
            "US": {"rows": [["AAPL", ""], ["ZZZZ", ""]]}, "TW": {"rows": [["2330.TW", ""]]}}})
        fake = world(self.ENTRIES)
        code, text = runner.run(fake)
        self.assertEqual(code, 0, text)
        # TW dropped, duplicates dropped, order: latest.json, themes.json, universe.json
        self.assertIn("[1/4] MSFT written", text)
        self.assertIn("[2/4] BRK-B written", text)
        self.assertIn("[3/4] AAPL written", text)
        self.assertIn("[4/4] ZZZZ no-cik", text)
        self.assertEqual(runner.files(), ["AAPL.json", "BRK-B.json", "MSFT.json"])
        limited = Runner(self)
        limited.report = runner.report
        fake = world(self.ENTRIES)
        code, text = limited.run(fake, "--limit", "2")
        self.assertEqual(limited.files(), ["BRK-B.json", "MSFT.json"])
        self.assertIn("2 tickers", text)
        self.assertEqual(len(fake.requests), 3)

    def test_tickers_bypasses_collect_symbols(self):
        runner = Runner(self)
        fake = world(self.ENTRIES)
        with mock.patch.object(sec, "collect_symbols", side_effect=AssertionError("must not be called")):
            code, text = runner.run(fake, "--tickers", "msft")
        self.assertEqual(code, 0)
        self.assertEqual(runner.files(), ["MSFT.json"])
        with mock.patch.object(sec, "collect_symbols", side_effect=AssertionError):
            code, text = runner.run(world(self.ENTRIES), "--tickers", "AAPL,MSFT,BRK-B", "--limit", "1", "--force")
        self.assertIn("[1/1] AAPL", text)

    def test_default_directories_are_relative_to_the_repo_root(self):
        self.assertEqual(os.path.normcase(sec.REPO_ROOT), os.path.normcase(ROOT))
        tmp = tempfile.mkdtemp(prefix="sec-seed-root-")
        self.addCleanup(shutil.rmtree, tmp, True)
        write_json(os.path.join(tmp, "docs", "report", "universe.json"),
                   {"fields": ["symbol"], "markets": {"US": {"rows": [["AAPL"]]}}})
        fake = world(self.ENTRIES)
        client = sec.SecClient("T", opener=fake, sleeper=lambda s: None)
        with mock.patch.object(sec, "REPO_ROOT", tmp), contextlib.redirect_stdout(io.StringIO()):
            code = sec.main([], client=client, now=NOW, environ={})
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(tmp, "docs", "report", "financials", "AAPL.json")))

    def test_stops_after_ten_consecutive_failures_keeping_written_files(self):
        runner = Runner(self)
        entries = {"AAPL": (320193, SYNTHETIC_BYTES), "MSFT": (789019, SYNTHETIC_BYTES)}
        for number in range(6):
            entries["BAD%d" % number] = (5000 + number, 429)
        fake = world(entries)
        write_json(runner.path("index.json"), {"version": 1, "generatedAt": "x", "items": {}, "misses": {}, "failures": {}})
        code, text = runner.run(fake, "--tickers", ",".join(entries))
        self.assertEqual(code, 1)
        self.assertIn("STOPPED: 10 consecutive failed requests", text)
        self.assertEqual(runner.files(), ["AAPL.json", "MSFT.json", "index.json"])
        self.assertIn("[3/8] BAD0 fetch-error", text)          # 6 attempts, then on to the next ticker
        self.assertNotIn("BAD2 ", text)
        self.assertIn("not-processed: 5", text)
        self.assertEqual(sorted(read_json(runner.path("index.json"))["items"]), ["AAPL", "MSFT"])
        self.assertEqual(fin.load_document(runner.path("AAPL.json"), "AAPL")[0], "ok")
        # exponential backoff was used for the 429s (injected sleeper, nothing really slept)
        self.assertEqual([s for s in runner.sleeps if s != sec.MIN_INTERVAL][:5], [2, 4, 8, 16, 32])
        self.assertTrue(all(s >= sec.MIN_INTERVAL for s in runner.sleeps))

    def test_ticker_map_failure_stops_cleanly(self):
        runner = Runner(self)
        fake = FakeSec()
        fake.route(sec.TICKERS_URL, (503, b""))
        code, text = runner.run(fake, "--tickers", "AAPL")
        self.assertEqual(code, 1)
        self.assertIn("STOPPED", text)
        self.assertEqual(runner.files(), [])

    def test_fetch_error_of_one_ticker_does_not_stop_the_run(self):
        runner = Runner(self)
        fake = world({"AAPL": (320193, 500), "MSFT": (789019, SYNTHETIC_BYTES)})
        code, text = runner.run(fake, "--tickers", "AAPL,MSFT")
        self.assertEqual(code, 0)
        self.assertIn("AAPL fetch-error", text)
        self.assertIn("MSFT written", text)
        self.assertEqual(runner.files(), ["MSFT.json"])

    def test_garbage_payload_is_an_error_not_a_crash(self):
        runner = Runner(self)
        fake = world({"AAPL": (320193, json.dumps({"facts": {"us-gaap": "junk"}}).encode()),
                      "MSFT": (789019, SYNTHETIC_BYTES)})
        code, text = runner.run(fake, "--tickers", "AAPL,MSFT")
        self.assertEqual(code, 0)
        self.assertIn("AAPL skipped (no-us-gaap)", text)
        self.assertEqual(runner.files(), ["MSFT.json"])

    def test_unexpected_extraction_error_is_logged_and_the_run_continues(self):
        runner = Runner(self)
        fake = world({"AAPL": (320193, SYNTHETIC_BYTES), "MSFT": (789019, SYNTHETIC_BYTES)})
        real = sec.extract_periods
        calls = []

        def flaky(payload, reference=None):
            calls.append(1)
            if len(calls) == 1:
                raise KeyError("boom")
            return real(payload, reference)

        with mock.patch.object(sec, "extract_periods", flaky):
            code, text = runner.run(fake, "--tickers", "AAPL,MSFT")
        self.assertEqual(code, 0)
        self.assertIn("AAPL extract-error (KeyError", text)
        self.assertIn("MSFT written", text)
        self.assertEqual(runner.files(), ["MSFT.json"])

    def test_script_entry_point_refuses_in_ci_without_touching_the_network(self):
        import subprocess
        env = dict(os.environ, CI="true")
        env.pop("SEC_USER_AGENT", None)
        done = subprocess.run([sys.executable, os.path.join(SCRIPTS, "seed_financials_sec.py"), "--tickers", "AAPL"],
                              env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2)
        self.assertIn("local manual use only", done.stdout)
        env = dict(os.environ, SEC_USER_AGENT="  ")
        env.pop("CI", None)
        env.pop("GITHUB_ACTIONS", None)
        done = subprocess.run([sys.executable, os.path.join(SCRIPTS, "seed_financials_sec.py"), "--tickers", "AAPL"],
                              env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2)
        self.assertIn("SEC_USER_AGENT", done.stdout)

    def test_nothing_to_do(self):
        runner = Runner(self)
        fake = world(self.ENTRIES)
        code, text = runner.run(fake)            # empty report / data dirs
        self.assertEqual(code, 0)
        self.assertIn("No US symbols", text)
        self.assertEqual(fake.requests, [])


# ---------------------------------------------------------------------------------------------
# Splits. SYNTHETIC "Synthetic Splits Corp." (made-up round numbers, calendar fiscal years
# 2010-2023, 10-Qs up to 2024 Q3) with an AAPL-like 7:1 split on 2014-06-09 and 4:1 split on
# 2020-08-31. As in real companyfacts every value is "as reported": a 10-K carries its year and the
# two before, a 10-Q the same quarter of the year before, and the latest filing of a period wins, so
# only the periods restated after a split are on the new basis: the series mixes raw and adjusted
# periods (FY2011 on the 2013 basis ÷28, FY2012-FY2017 ÷4, FY2018+ on today's basis).
# ---------------------------------------------------------------------------------------------
SPLITS = (("2014-06-09", 7), ("2020-08-31", 4))
Q_ENDS = (("01-01", "03-31", "05-10"), ("04-01", "06-30", "08-09"), ("07-01", "09-30", "11-09"))


def today_shares(year):
    """Diluted share count of `year` on today's basis (divisible by 28: adjusted values are exact)."""
    return 28 * (1000 - 20 * (year - 2010)) * 1_000_000


def issuer_shares(year):
    """No split, but shares issued for an acquisition in 2018: × 1.9 (a real share-count change)."""
    return (1_900_000_000 if year >= 2018 else 1_000_000_000) - 10_000_000 * (year - 2010)


def split_income(year):
    return (10 + year - 2010) * B


def split_divisor(filed, splits):
    """How many times smaller than today's a share count reported on `filed` is (splits after it)."""
    divisor = 1
    for day, ratio in splits:
        if day > filed:
            divisor *= ratio
    return divisor


def split_companyfacts(splits=SPLITS, shares=today_shares, name="Synthetic Splits Corp."):
    gaap = {}

    def add(concept, unit, start, end, value, form, filed):
        gaap.setdefault(concept, {"units": {}})["units"].setdefault(unit, []).append(
            {"start": start, "end": end, "val": value, "form": form, "filed": filed, "accn": f"{form}-{filed}"})

    def report(start, end, income, count, form, filed):
        raw = count // split_divisor(filed, splits)
        add("Revenues", "USD", start, end, 4 * income, form, filed)
        add("NetIncomeLoss", "USD", start, end, income, form, filed)
        add("EarningsPerShareDiluted", "USD/shares", start, end, round(income / raw, 2), form, filed)
        add("WeightedAverageNumberOfDilutedSharesOutstanding", "shares", start, end, raw, form, filed)

    for fy in range(2010, 2024):
        for year in (fy, fy - 1, fy - 2):
            if year >= 2010:
                report(f"{year}-01-01", f"{year}-12-31", split_income(year), shares(year), "10-K", f"{fy + 1}-02-15")
    for fy in range(2010, 2025):
        for start, end, filed in Q_ENDS:
            for year in (fy, fy - 1):
                if 2010 <= year <= 2023:
                    report(f"{year}-{start}", f"{year}-{end}", split_income(year) // 4, shares(year), "10-Q",
                           f"{fy}-{filed}")
    return {"cik": 4242, "entityName": name, "facts": {"us-gaap": gaap}}


def chart(*events):
    """SYNTHETIC Yahoo chart payload (events=split) with the given (day, numerator, denominator)."""
    splits = {}
    for day, numerator, denominator in events:
        stamp = int(datetime.fromisoformat(day + "T13:30:00+00:00").timestamp())
        splits[str(stamp)] = {"date": stamp, "numerator": numerator, "denominator": denominator,
                              "splitRatio": f"{numerator}:{denominator}"}
    result = {"meta": {"symbol": "SYN"}, "events": {"splits": splits}} if splits else {"meta": {"symbol": "SYN"}}
    return {"chart": {"result": [result], "error": None}}


SPLIT_CHART = chart(("2014-06-09", 7, 1), ("2020-08-31", 4, 1))
SPLIT_FACTS = split_companyfacts()
SPLIT_BYTES = json.dumps(SPLIT_FACTS).encode("utf-8")
ISSUER_FACTS = split_companyfacts(splits=(), shares=issuer_shares, name="Synthetic Issuer Corp.")
ISSUER_BYTES = json.dumps(ISSUER_FACTS).encode("utf-8")


class Fetch:
    """fetch_splits stand-in: returns `payload` (or raises it), counting calls."""

    def __init__(self, payload=None):
        self.payload, self.calls = payload, 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


def columns(document, frequency):
    section = document[frequency]
    return {end: {name: column[i] for name, column in section["values"].items()}
            for i, end in enumerate(section["end"])}


class PreEpochSplitTest(unittest.TestCase):
    """Synthetic chart payload: Windows cannot convert negative timestamps, so pre-1970 splits are dropped."""

    def test_pre_epoch_events_dropped_and_parse_splits_works(self):
        payload = {"chart": {"result": [{"events": {"splits": {
            "-1": {"date": -1262304000, "numerator": 2, "denominator": 1},
            "2": {"date": 1400000000, "numerator": 2, "denominator": 1},
        }}}]}}
        cleaned = sec.drop_pre_epoch_splits(payload)
        self.assertEqual(list(cleaned["chart"]["result"][0]["events"]["splits"]), ["2"])
        self.assertEqual(fin.parse_splits(cleaned), [["2014-05-13", 2.0]])
        self.assertEqual(sec.drop_pre_epoch_splits({}), {})
        self.assertEqual(sec.drop_pre_epoch_splits(None), None)


class SplitMergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = sec.extract_periods(copy.deepcopy(SPLIT_FACTS))
        cls.fetch = Fetch(SPLIT_CHART)
        cls.doc, cls.info = sec.merge_into_document(None, cls.result, "SYN", NOW, cls.fetch)
        cls.raw, cls.raw_info = sec.merge_into_document(None, cls.result, "SYN", NOW)

    def assert_one_basis(self, document):
        """Every share count is today's, every EPS = net income / today's share count (± the 2-decimal
        rounding of the reported EPS; a missed split would be off by ×4 or more)."""
        for frequency in ("annual", "quarterly"):
            for end, values in columns(document, frequency).items():
                year = int(end[:4])
                if values.get("dilutedShares") is not None:
                    self.assertEqual(values["dilutedShares"], today_shares(year), (frequency, end))
                if values.get("epsDiluted") is not None:
                    q4 = frequency == "quarterly" and end.endswith("12-31")    # annual − 3 rounded quarters
                    self.assertAlmostEqual(values["epsDiluted"], values["netIncome"] / today_shares(year),
                                           delta=0.0201 if q4 else 0.0051, msg=(frequency, end))
        sections = fin.read_sections(json.loads(json.dumps(document)))
        self.assertFalse(fin.share_jump([sections["annual"], sections["quarterly"]]))

    def test_synthetic_input_mixes_raw_and_restated_periods(self):
        a = {p["end"]: p["values"] for p in self.result.annual}
        self.assertEqual(a["2011-12-31"]["dilutedShares"], today_shares(2011) // 28)   # last filed 2014-02 (pre 7:1)
        self.assertEqual(a["2012-12-31"]["dilutedShares"], today_shares(2012) // 4)    # restated by the 2015 10-K
        self.assertEqual(a["2017-12-31"]["dilutedShares"], today_shares(2017) // 4)
        self.assertEqual(a["2018-12-31"]["dilutedShares"], today_shares(2018))         # restated after the 4:1
        q = {p["end"]: p["values"] for p in self.result.quarterly}
        self.assertEqual(q["2013-03-31"]["dilutedShares"], today_shares(2013) // 28)   # 10-Q filed 2014-05
        self.assertEqual(q["2013-09-30"]["dilutedShares"], today_shares(2013) // 4)    # 10-Q filed 2014-11
        self.assertTrue(fin.share_jump([self.result.annual, self.result.quarterly]))

    def test_split_fetched_once_and_applied_to_annual_and_quarterly(self):
        self.assertEqual(self.fetch.calls, 1)
        self.assertEqual(self.info["chart"], "fetched")
        self.assertFalse(self.info["shareJump"])
        self.assertTrue(self.info["splitAdjusted"])
        self.assertEqual(self.doc["split"], {"adjusted": True, "events": [["2014-06-09", 7], ["2020-08-31", 4]]})
        self.assertIn("split-adjusted", self.doc["quality"]["issues"])
        self.assertNotIn("eps-inconsistent", self.doc["quality"]["issues"])
        self.assert_one_basis(self.doc)
        a = columns(self.doc, "annual")
        self.assertEqual(a["2011-12-31"]["dilutedShares"], today_shares(2011))         # × 28
        self.assertEqual(a["2011-12-31"]["epsDiluted"], round(round(split_income(2011) / (today_shares(2011) // 28), 2) / 28, 4))
        self.assertEqual(a["2015-12-31"]["dilutedShares"], today_shares(2015))         # × 4
        # periods already on today's basis are left exactly as reported
        reported = {p["end"]: p["values"] for p in self.result.annual}
        for end in ("2018-12-31", "2021-12-31", "2023-12-31"):
            self.assertEqual(a[end]["epsDiluted"], reported[end]["epsDiluted"])
            self.assertEqual(a[end]["dilutedShares"], reported[end]["dilutedShares"])
        # money values never change
        self.assertEqual(self.doc["annual"]["values"]["netIncome"], self.raw["annual"]["values"]["netIncome"])
        self.assertEqual(self.doc["quarterly"]["values"]["revenue"], self.raw["quarterly"]["values"]["revenue"])

    def test_q4_eps_follows_its_annual_period_and_mixed_bases_are_not_derived(self):
        q = columns(self.doc, "quarterly")
        # FY2016: annual EPS and Q1-Q3 EPS all on the ÷4 basis → derived, then × 1/4 with the annual period
        q4_income = split_income(2016) - 3 * (split_income(2016) // 4)
        self.assertAlmostEqual(q["2016-12-31"]["epsDiluted"], q4_income / today_shares(2016), delta=0.002)
        self.assertIsNone(q["2016-12-31"]["dilutedShares"])
        self.assertAlmostEqual(q["2010-12-31"]["epsDiluted"],
                               (split_income(2010) - 3 * (split_income(2010) // 4)) / today_shares(2010), delta=0.002)
        # FY2012 (annual ÷4, quarters ÷28) and FY2019 (annual ×1, quarters ÷4 / ×1): no Q4 EPS at all
        raw_q = {p["end"]: p["values"] for p in self.result.quarterly}
        for end in ("2012-12-31", "2019-12-31"):
            self.assertNotIn("epsDiluted", raw_q[end], end)
            self.assertIsNone(q[end]["epsDiluted"], end)
            self.assertIsNotNone(q[end]["netIncome"], end)
        self.assertIn("epsDiluted", raw_q["2022-12-31"])

    def test_without_fetch_values_stay_as_reported_and_jump_is_flagged(self):
        self.assertTrue(self.raw_info["shareJump"])
        self.assertIsNone(self.raw_info["chart"])
        self.assertEqual(self.raw["split"], {"adjusted": False, "events": []})
        self.assertNotIn("split-adjusted", self.raw["quality"]["issues"])
        a = columns(self.raw, "annual")
        self.assertEqual(a["2011-12-31"]["dilutedShares"], today_shares(2011) // 28)

    def test_fetch_failure_keeps_as_reported_values_and_never_raises_or_prints(self):
        failing = Fetch(RuntimeError("HTTP 429"))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            doc, info = sec.merge_into_document(None, self.result, "SYN", NOW, failing)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))
        self.assertEqual(failing.calls, 1)
        self.assertEqual(info["chart"], "failed")
        self.assertEqual(info["chartError"], "RuntimeError: HTTP 429")
        self.assertTrue(info["shareJump"])
        self.assertFalse(info["splitAdjusted"])
        self.assertTrue(fin.same_content(doc, self.raw))
        # an unparsable chart payload counts as fetched without events (as in update_document)
        doc, info = sec.merge_into_document(None, self.result, "SYN", NOW, Fetch({"junk": 1}))
        self.assertEqual(info["chart"], "fetched")
        self.assertTrue(fin.same_content(doc, self.raw))

    def test_second_merge_reuses_stored_events_and_is_idempotent(self):
        never = Fetch(AssertionError("must not fetch again"))
        again, info = sec.merge_into_document(copy.deepcopy(self.doc), self.result, "SYN", NOW + timedelta(days=5), never)
        self.assertEqual(never.calls, 0)                    # no jump left after the stored events
        self.assertIsNone(info["chart"])
        self.assertTrue(info["splitAdjusted"])              # fresh raw SEC values adjusted again …
        self.assertTrue(fin.same_content(self.doc, again))  # … to exactly the same content (not twice)
        plain, info = sec.merge_into_document(copy.deepcopy(self.doc), self.result, "SYN", NOW, None)
        self.assertTrue(fin.same_content(self.doc, plain))
        self.assertFalse(info["shareJump"])
        # the Yahoo run's own pass over the stored events finds nothing left to adjust
        sections = fin.read_sections(json.loads(json.dumps(self.doc)))
        self.assertFalse(fin.adjust_splits([sections["annual"], sections["quarterly"]], sections["events"]))

    def test_stored_events_are_used_without_a_fetch(self):
        stored = {"version": 1, "symbol": "SYN",
                  "split": {"adjusted": True, "events": [["2014-06-09", 7], ["2020-08-31", 4]]}}
        never = Fetch(AssertionError("must not fetch"))
        doc, info = sec.merge_into_document(stored, self.result, "SYN", NOW, never)
        self.assertEqual(never.calls, 0)
        self.assertTrue(fin.same_content(doc, self.doc))
        # only the newer event stored (e.g. by a Yahoo run): the jump left over triggers one fetch
        partial = {"version": 1, "symbol": "SYN", "split": {"adjusted": True, "events": [["2020-08-31", 4]]}}
        once = Fetch(SPLIT_CHART)
        doc, info = sec.merge_into_document(partial, self.result, "SYN", NOW, once)
        self.assertEqual(once.calls, 1)
        self.assertTrue(fin.same_content(doc, self.doc))

    def test_adjusted_flag_is_kept_forever(self):
        stored = copy.deepcopy(self.doc)
        doc, info = sec.merge_into_document(stored, sec.extract_periods(facts()), "SYN", NOW)
        self.assertTrue(doc["split"]["adjusted"])
        self.assertIn("split-adjusted", doc["quality"]["issues"])

    def test_real_share_count_change_without_split_is_not_altered(self):
        result = sec.extract_periods(copy.deepcopy(ISSUER_FACTS))
        self.assertTrue(fin.share_jump([result.annual, result.quarterly]))
        reference, _ = sec.merge_into_document(None, result, "ISS", NOW)
        for payload in (chart(), chart(("1999-05-03", 2, 1))):          # no event / only an older split
            fetch = Fetch(payload)
            doc, info = sec.merge_into_document(None, result, "ISS", NOW, fetch)
            self.assertEqual(fetch.calls, 1)
            self.assertEqual(info["chart"], "fetched")
            self.assertFalse(info["splitAdjusted"])
            self.assertFalse(info["shareJump"])                         # checked: the jump is real
            self.assertFalse(doc["split"]["adjusted"])
            self.assertEqual(doc["annual"]["values"], reference["annual"]["values"])
            self.assertEqual(doc["quarterly"]["values"], reference["quarterly"]["values"])
            self.assertNotIn("split-adjusted", doc["quality"]["issues"])
        self.assertEqual(doc["split"]["events"], [["1999-05-03", 2]])
        a = columns(doc, "annual")
        self.assertEqual(a["2018-12-31"]["dilutedShares"], issuer_shares(2018))
        self.assertEqual(a["2017-12-31"]["dilutedShares"], issuer_shares(2017))


class FakeYahoo:
    """SYNTHETIC Yahoo session: get_json(url, params) → scripted chart payloads (recorded)."""

    def __init__(self, charts, log):
        self.charts, self.log = charts, log

    def get_json(self, url, params):
        self.log.append(("chart", url, dict(params)))
        payload = self.charts.get(url)
        if payload is None or isinstance(payload, BaseException):
            raise payload or RuntimeError("HTTP 404")
        return payload


class SplitMainTest(unittest.TestCase):
    ENTRIES = {
        "AAPL": (320193, SYNTHETIC_BYTES),
        "SPLT": (4242, SPLIT_BYTES),
        "SPLU": (4243, SPLIT_BYTES),
        "ISSU": (4244, ISSUER_BYTES),
    }

    def factory(self, charts, fail=0):
        log = []

        def make():
            log.append(("connect",))
            if len([e for e in log if e[0] == "connect"]) <= fail:
                raise RuntimeError("Yahoo crumb HTTP 429")
            return FakeYahoo(charts, log)
        return make, log

    @staticmethod
    def url(key):
        return fin.CHART_URL.format(symbol=key)

    def test_yahoo_created_lazily_once_and_splits_applied(self):
        runner = Runner(self)
        make, log = self.factory({self.url("SPLT"): SPLIT_CHART, self.url("SPLU"): SPLIT_CHART,
                                  self.url("ISSU"): chart()})
        fake = world(self.ENTRIES)
        code, text = runner.run(fake, "--tickers", "AAPL,SPLT,ISSU,SPLU", yahoo=make)
        self.assertEqual(code, 0, text)
        self.assertEqual(log, [
            ("connect",),
            ("chart", self.url("SPLT"), {"events": "split", "range": "max", "interval": "1mo"}),
            ("chart", self.url("ISSU"), {"events": "split", "range": "max", "interval": "1mo"}),
            ("chart", self.url("SPLU"), {"events": "split", "range": "max", "interval": "1mo"}),
        ])
        self.assertEqual(runner.yahoo_sleeps, [fin.REQUEST_DELAY] * 3)      # after every chart request
        self.assertEqual(runner.sleeps, [sec.MIN_INTERVAL] * 5)             # SEC pacing unchanged
        self.assertIn("[2/4] SPLT written", text)
        self.assertIn("[split-adjusted, 2 event(s)]", text)
        self.assertNotIn("split-check-pending", text.split("Summary")[0])
        self.assertIn("split checks: Yahoo connected, 3 chart request(s)", text)
        self.assertIn("split events fetched: 3 ticker(s), 2 with split events (SPLT, SPLU)", text)
        self.assertIn("split-adjusted: 2 ticker(s) (SPLT, SPLU)", text)
        self.assertIn("split-check-pending: 0 ticker(s)", text)
        document = read_json(runner.path("SPLT.json"))
        self.assertEqual(document["split"]["events"], [["2014-06-09", 7], ["2020-08-31", 4]])
        SplitMergeTest.assert_one_basis(self, document)
        # a --force re-run reuses the stored events: no Yahoo session at all, files unchanged
        before = read_bytes(runner.path("SPLT.json"))
        make2, log2 = self.factory({self.url("ISSU"): chart()})
        code, text = runner.run(world(self.ENTRIES), "--tickers", "SPLT,SPLU", "--force", yahoo=make2)
        self.assertEqual(log2, [])
        self.assertIn("SPLT unchanged", text)
        self.assertEqual(read_bytes(runner.path("SPLT.json")), before)

    def test_no_yahoo_when_no_share_jump(self):
        runner = Runner(self)
        code, text = runner.run(world(self.ENTRIES), "--tickers", "AAPL")
        self.assertEqual(code, 0)
        self.assertEqual(runner.unexpected_yahoo, [])
        self.assertEqual(runner.yahoo_sleeps, [])
        self.assertIn("split checks: Yahoo not needed, 0 chart request(s)", text)
        self.assertIn("split-check-pending: 0 ticker(s)", text)

    def test_connect_failure_is_remembered_and_seeding_continues(self):
        runner = Runner(self)
        make, log = self.factory({}, fail=99)
        code, text = runner.run(world(self.ENTRIES), "--tickers", "SPLT,AAPL,SPLU", yahoo=make)
        self.assertEqual(code, 0, text)
        # connect_yahoo's own retries (len(CONNECT_DELAYS) + 1 attempts), once for the whole run
        self.assertEqual(log, [("connect",)] * (len(fin.CONNECT_DELAYS) + 1))
        self.assertEqual(runner.yahoo_sleeps, list(fin.CONNECT_DELAYS))
        self.assertEqual(text.count("continuing without split checks"), 1)       # logged once
        self.assertIn("[1/3] SPLT written", text)
        self.assertIn("[split-check-pending] (split events unavailable: SplitUnavailable: Yahoo connection failed", text)
        self.assertIn("[3/3] SPLU written", text)
        self.assertIn("split checks: Yahoo unavailable (Yahoo connection failed: RuntimeError: Yahoo crumb HTTP 429)", text)
        self.assertIn("split-check-pending: 2 ticker(s) (SPLT, SPLU)", text)
        document = read_json(runner.path("SPLT.json"))
        self.assertEqual(document["split"], {"adjusted": False, "events": []})
        raw, _ = sec.merge_into_document(None, sec.extract_periods(copy.deepcopy(SPLIT_FACTS)), "SPLT", NOW)
        self.assertEqual(document["annual"]["values"], raw["annual"]["values"])       # as reported
        self.assertEqual(runner.files(), ["AAPL.json", "SPLT.json", "SPLU.json"])

    def test_connect_retry_then_success(self):
        runner = Runner(self)
        make, log = self.factory({self.url("SPLT"): SPLIT_CHART}, fail=1)
        code, text = runner.run(world(self.ENTRIES), "--tickers", "SPLT", yahoo=make)
        self.assertEqual(code, 0)
        self.assertEqual([e[0] for e in log], ["connect", "connect", "chart"])
        self.assertEqual(runner.yahoo_sleeps, [fin.CONNECT_DELAYS[0], fin.REQUEST_DELAY])
        self.assertIn("split-adjusted: 1 ticker(s) (SPLT)", text)

    def test_chart_failures_flag_pending_and_stop_split_checks(self):
        runner = Runner(self)
        entries = {"SPL%d" % n: (5000 + n, SPLIT_BYTES) for n in range(5)}
        make, log = self.factory({})                                      # every chart request fails
        code, text = runner.run(world(entries), "--tickers", ",".join(entries), yahoo=make)
        self.assertEqual(code, 0)
        self.assertEqual([e[0] for e in log], ["connect"] + ["chart"] * sec.MAX_CHART_FAILURES)
        self.assertEqual(runner.yahoo_sleeps, [fin.REQUEST_DELAY] * sec.MAX_CHART_FAILURES)
        self.assertIn("split-check-pending: 5 ticker(s) (SPL0, SPL1, SPL2, SPL3, SPL4)", text)
        self.assertEqual(len(runner.files()), 5)

    def test_no_splits_flag_never_contacts_yahoo(self):
        runner = Runner(self)
        code, text = runner.run(world(self.ENTRIES), "--tickers", "SPLT,AAPL", "--no-splits")
        self.assertEqual(code, 0)
        self.assertEqual(runner.unexpected_yahoo, [])
        self.assertIn("[1/2] SPLT written", text)
        self.assertIn("[split-check-pending]", text)
        self.assertIn("split checks: disabled (--no-splits)", text)
        self.assertIn("split-check-pending: 1 ticker(s) (SPLT)", text)
        self.assertEqual(read_json(runner.path("SPLT.json"))["split"], {"adjusted": False, "events": []})


# ---------------------------------------------------------------------------------------------
# Third audit (B1-B6 + follow-ups). Every payload, stored file and chart below is SYNTHETIC: hand-made
# numbers that only mimic the shape of the filings named in the comments (no SEC / Yahoo data).
# ---------------------------------------------------------------------------------------------
M = 1_000_000


def facts_of(points, name="Synthetic Seven"):
    """SYNTHETIC companyfacts: {concept: [(start | None, end, value, form, filed, accn)]}, unit by concept."""
    gaap = {}
    for concept, rows in points.items():
        gaap[concept] = {"units": {sec._UNIT_OF_CONCEPT[concept]: [
            {"start": start, "end": end, "val": value, "form": form, "filed": filed, "accn": accn}
            for start, end, value, form, filed, accn in rows
        ]}}
    return {"cik": 7, "entityName": name, "facts": {"us-gaap": gaap}}


def plain_company(years=range(2015, 2025), shares=500 * M, income=250 * M):
    """SYNTHETIC calendar-year filer: 10-Q Q1-Q3 and 10-K years of revenue, net income, EPS, share count."""
    eps_q, eps_y = round(income / shares, 2), round(4 * income / shares, 2)
    rows = {"Revenues": [], "NetIncomeLoss": [], "EarningsPerShareDiluted": [], SHARES_CONCEPT: []}
    for y in years:
        for concept, quarter, annual_value in (("Revenues", 4 * income, 16 * income),
                                               ("NetIncomeLoss", income, 4 * income),
                                               ("EarningsPerShareDiluted", eps_q, eps_y),
                                               (SHARES_CONCEPT, shares, shares)):
            rows[concept] += quarters(y, [quarter] * 3, prefix=concept[:4]) + [year(y, annual_value)]
    return rows


def replace_row(rows, end, value, form=None):
    """rows with the value of the fact ending on `end` (of the given form, if any) replaced."""
    return [row[:2] + (value,) + row[3:] if row[1] == end and (form is None or row[3] == form) else row
            for row in rows]


def values_by_end(items, name):
    return {p["end"]: p["values"].get(name) for p in items}


class UnitErrorTest(unittest.TestCase):
    """B1 (synthetic): XBRL scale errors are never adopted; real splits are never touched."""

    def test_refiled_version_in_another_scale_is_passed_over_for_every_concept(self):
        rows = plain_company()
        shares = rows[SHARES_CONCEPT]
        # WAT-like: a later 10-Q's comparative column files Q1 2023 in thousands-as-units (× 1000)
        shares.append(k("2023-01-01", "2023-03-31", 500 * M * 1000, form="10-Q", filed="2024-05-10", accn="c24"))
        # NTRS-like: a later 10-K re-files the 2018 year × 10^6
        shares.append(year(2018, 500 * M * 10 ** 6, filed="2020-02-15", accn="k2019b"))
        # the first filing was the wrong one: Q2 2019 filed / 1000, corrected by the next year's comparative
        shares[:] = replace_row(shares, "2019-06-30", 500 * M // 1000)
        shares.append(k("2019-04-01", "2019-06-30", 500 * M, form="10-Q", filed="2020-08-10", accn="c20"))
        # any concept: revenue of 2020 re-filed × 1000
        rows["Revenues"].append(year(2020, 16 * 250 * M * 1000, filed="2022-02-15", accn="k2021b"))
        result = sec.extract_periods(facts_of(rows))
        a, q = values_by_end(result.annual, "dilutedShares"), values_by_end(result.quarterly, "dilutedShares")
        self.assertEqual(q["2023-03-31"], 500 * M)
        self.assertEqual(a["2018-12-31"], 500 * M)
        self.assertEqual(q["2019-06-30"], 500 * M)
        self.assertEqual(values_by_end(result.annual, "revenue")["2020-12-31"], 4000 * M)
        self.assertEqual(result.dropped.get("unitRefiled"), 4)
        self.assertNotIn("dilutedShares", result.dropped)
        self.assertFalse(fin.share_jump([result.annual, result.quarterly]))
        doc, info = sec.merge_into_document(None, result, "TEST", NOW)
        self.assertEqual(info["dropped"], result.dropped)

    def test_single_version_share_count_outliers_are_null(self):
        rows = plain_company()
        shares = rows[SHARES_CONCEPT]
        shares[:] = replace_row(shares, "2017-09-30", 500 * M * 1000)            # BWXT-like 118.6B among 118M
        shares[:] = replace_row(shares, "2016-12-31", 500.0, form="10-K")         # HST-like 739.1 (millions)
        for end in ("2024-06-30", "2024-09-30"):                                   # WAT-like tail: 82.1B, 98.2B
            shares[:] = replace_row(shares, end, 500 * M * 1000)
        result = sec.extract_periods(facts_of(rows))
        a, q = values_by_end(result.annual, "dilutedShares"), values_by_end(result.quarterly, "dilutedShares")
        for end in ("2017-09-30", "2024-06-30", "2024-09-30"):
            self.assertIsNone(q[end], end)
        self.assertIsNone(a["2016-12-31"])
        self.assertEqual(result.dropped["dilutedShares"], 4)
        kept = [v for v in list(a.values()) + list(q.values()) if v is not None]
        self.assertEqual(set(kept), {500 * M})
        # EPS / net income of those periods are untouched (null, never rescaled)
        self.assertEqual(values_by_end(result.quarterly, "epsDiluted")["2024-06-30"], 0.5)
        doc, info = sec.merge_into_document(None, result, "TEST", NOW)
        self.assertFalse(info["shareJump"])
        self.assertEqual(info["dropped"]["dilutedShares"], 4)

    def test_a_run_of_10q_counts_in_thousands_is_judged_by_the_eps_implied_level(self):
        # ECHO-like: two years of 10-Q share counts in thousands next to the 10-K's units; the counts
        # themselves are the majority locally, net income / EPS tells which scale is right
        rows = plain_company()
        for y in (2021, 2022):
            for end in ("03-31", "06-30", "09-30"):
                rows[SHARES_CONCEPT] = replace_row(rows[SHARES_CONCEPT], f"{y}-{end}", 500 * M // 1000, form="10-Q")
        result = sec.extract_periods(facts_of(rows))
        q = values_by_end(result.quarterly, "dilutedShares")
        a = values_by_end(result.annual, "dilutedShares")
        self.assertEqual([q[f"{y}-{e}"] for y in (2021, 2022) for e in ("03-31", "06-30", "09-30")], [None] * 6)
        self.assertEqual(a["2021-12-31"], 500 * M)
        self.assertEqual(a["2022-12-31"], 500 * M)
        self.assertEqual(result.dropped["dilutedShares"], 6)

    def test_eps_in_another_scale_is_null(self):
        rows = plain_company()
        eps = rows["EarningsPerShareDiluted"]
        eps[:] = replace_row(eps, "2021-09-30", 500000.0)                  # HAL-like 260000 for 0.26
        eps[:] = replace_row(eps, "2019-12-31", 2.0e6, form="10-K")         # an annual value × 10^6
        # ASTS-like: no share count in 2015-2016, EPS tagged with a share-like number
        rows[SHARES_CONCEPT] = [row for row in rows[SHARES_CONCEPT] if row[1] >= "2017"]
        for end in ("2015-03-31", "2015-06-30", "2016-03-31"):
            eps[:] = replace_row(eps, end, 17600000)
        result = sec.extract_periods(facts_of(rows))
        q, a = values_by_end(result.quarterly, "epsDiluted"), values_by_end(result.annual, "epsDiluted")
        for end in ("2021-09-30", "2015-03-31", "2015-06-30", "2016-03-31"):
            self.assertIsNone(q[end], end)
        self.assertIsNone(a["2019-12-31"])
        self.assertIsNone(q["2019-12-31"])              # no Q4 from a dropped annual value
        self.assertIsNone(q["2021-12-31"])              # nor from a dropped quarter
        self.assertEqual(q["2021-06-30"], 0.5)
        self.assertEqual(q["2015-09-30"], 0.5)          # same years, plausible values stay
        self.assertEqual(result.dropped["epsDiluted"], 5)
        self.assertTrue(all(abs(v) < 100 for v in list(q.values()) + list(a.values()) if v is not None))

    def test_real_splits_are_never_dropped(self):
        # AAPL-like 7:1 + 4:1, NVDA-like 4:1 + 10:1 and CMG-like 50:1: as-reported counts / EPS mix bases
        # (dated so that the newest periods are filed after the splits, as in real life)
        for splits in (SPLITS, (("2017-07-20", 4), ("2020-06-10", 10)), (("2019-06-26", 50),)):
            payload = split_companyfacts(splits=splits)
            result = sec.extract_periods(copy.deepcopy(payload))
            self.assertEqual({k_: v for k_, v in result.dropped.items() if k_ != "unitRefiled"}, {}, splits)
            self.assertNotIn("unitRefiled", result.dropped, splits)
            reported = {}
            for row in payload["facts"]["us-gaap"][SHARES_CONCEPT]["units"]["shares"]:
                reported.setdefault(row["end"], set()).add(row["val"])
            for p in result.annual + result.quarterly:
                if p["values"].get("dilutedShares") is not None:
                    self.assertIn(p["values"]["dilutedShares"], reported[p["end"]])
            events = [(day, ratio, 1) for day, ratio in splits]
            doc, info = sec.merge_into_document(None, result, "SYN", NOW, Fetch(chart(*events)))
            self.assertTrue(info["splitAdjusted"], splits)
            SplitMergeTest.assert_one_basis(self, doc)


GOOGL_SPLITS = (("2022-07-15", 20),)


def googl_companyfacts():
    """GOOGL-like (synthetic): one 20:1 split, EPS as reported, NO share count without a class dimension."""
    payload = split_companyfacts(splits=GOOGL_SPLITS, name="Synthetic Alphabet")
    del payload["facts"]["us-gaap"][SHARES_CONCEPT]
    return payload


class ImpliedShareBasisTest(unittest.TestCase):
    """B2 (synthetic): Q4 EPS needs one share basis, also where only net income / EPS tells it."""

    def test_q4_eps_on_mixed_bases_is_not_derived_without_share_counts(self):
        result = sec.extract_periods(googl_companyfacts())
        q = {p["end"]: p["values"] for p in result.quarterly}
        a = {p["end"]: p["values"] for p in result.annual}
        # FY2020: annual EPS restated after the split (filed 2023), Q1-Q3 as reported before it (2021)
        self.assertLess(a["2020-12-31"]["epsDiluted"], 2)
        self.assertGreater(q["2020-09-30"]["epsDiluted"], 3)
        for end in ("2020-12-31", "2021-12-31"):
            self.assertNotIn("epsDiluted", q[end], end)
            self.assertIn("netIncome", q[end], end)
        for end in ("2019-12-31", "2022-12-31", "2023-12-31"):          # one basis: derived
            self.assertIn("epsDiluted", q[end], end)
        for end, values in q.items():
            if values.get("epsDiluted") and values.get("netIncome"):
                self.assertEqual(values["epsDiluted"] > 0, values["netIncome"] > 0, end)

    def test_split_check_and_adjustment_work_on_implied_share_counts(self):
        result = sec.extract_periods(googl_companyfacts())
        fetch = Fetch(chart(("2022-07-15", 20, 1)))
        doc, info = sec.merge_into_document(None, result, "GOOGL", NOW, fetch)
        self.assertEqual(fetch.calls, 1)                    # the implied counts jump × 20
        self.assertEqual(info["chart"], "fetched")
        self.assertTrue(info["splitAdjusted"])
        self.assertFalse(info["shareJump"])
        self.assertEqual(doc["split"], {"adjusted": True, "events": [["2022-07-15", 20]]})
        for frequency in ("annual", "quarterly"):
            self.assertNotIn("dilutedShares", doc[frequency]["values"])     # implied counts are never written
        SplitMergeTest.assert_one_basis(self, doc)
        a = columns(doc, "annual")
        self.assertAlmostEqual(a["2019-12-31"]["epsDiluted"], split_income(2019) / today_shares(2019), delta=0.001)
        # re-seeding over the result is idempotent and needs no new fetch (stored event)
        never = Fetch(AssertionError("no second fetch"))
        again, info = sec.merge_into_document(copy.deepcopy(doc), result, "GOOGL", NOW, never)
        self.assertEqual(never.calls, 0)
        self.assertTrue(fin.same_content(doc, again))
        # without a chart fetch the jump is reported (split-check-pending), values stay as reported
        raw, info = sec.merge_into_document(None, result, "GOOGL", NOW)
        self.assertTrue(info["shareJump"])
        self.assertNotIn("dilutedShares", raw["annual"]["values"])

    def test_derived_q4_eps_of_the_wrong_sign_is_null(self):
        # ADI / AMD / HPQ-like: four pieces on one basis whose difference contradicts the Q4 net income
        rows = {
            "NetIncomeLoss": quarters(2023, [25 * M] * 3) + [year(2023, 100 * M)],
            "EarningsPerShareDiluted": quarters(2023, [0.40] * 3, prefix="e") + [year(2023, 1.00)],
            SHARES_CONCEPT: quarters(2023, [100 * M] * 3, prefix="s") + [year(2023, 100 * M)],
        }
        result = sec.extract_periods(facts_of(rows))
        q4 = {p["end"]: p for p in result.quarterly}["2023-12-31"]
        self.assertEqual(q4["values"]["netIncome"], 25 * M)
        self.assertNotIn("epsDiluted", q4["values"])                     # 1.00 − 1.20 < 0 < 25M
        self.assertEqual(result.dropped["epsDiluted"], 1)
        self.assertEqual(q4["extra"]["derived"], 1)                      # net income is still derived


def quarter_doc(annual, quarterly, key="TEST", fy_end=12):
    """SYNTHETIC stored Yahoo-only file: {end: revenue or None} per section (None: an empty period)."""
    def section(points):
        ends = sorted(points)
        return {"end": ends, "values": {"revenue": [points[e] for e in ends],
                                        "netIncome": [None if points[e] is None else 1 for e in ends]}}
    return {"version": 1, "symbol": key, "market": "US", "currency": "USD", "fyEndMonth": fy_end,
            "annual": section(annual), "quarterly": section(quarterly)}


def sec_result(annual, quarterly):
    """SYNTHETIC SecResult: {end: revenue} per section (net income 1)."""
    result = sec.SecResult()
    result.annual = [fin.period(end, {"revenue": value, "netIncome": 1}, "sec") for end, value in sorted(annual.items())]
    result.quarterly = [fin.period(end, {"revenue": value, "netIncome": 1}, "sec", {"derived": None})
                        for end, value in sorted(quarterly.items())]
    return result


def yahoo_payload(document):
    """A SYNTHETIC Yahoo timeseries payload carrying the Yahoo periods of a stored file (values as stored)."""
    series = []
    for frequency, prefix, kind in (("annual", "annual", "12M"), ("quarterly", "quarterly", "3M")):
        section = document[frequency]
        for name, yahoo_type in (("revenue", "TotalRevenue"), ("netIncome", "NetIncome")):
            points = [{"asOfDate": end, "periodType": kind, "currencyCode": "USD", "reportedValue": {"raw": value}}
                      for end, value in zip(section["end"], section["values"][name]) if value is not None]
            series.append({"meta": {"type": [prefix + yahoo_type]}, prefix + yahoo_type: points})
    return {"timeseries": {"result": series}}


def labels_unique(test, document):
    for frequency in ("annual", "quarterly"):
        section = document[frequency]
        labels = list(zip(section["fy"], section.get("fq", [0] * len(section["fy"]))))
        test.assertEqual(len(labels), len(set(labels)), (frequency, labels))
        gap = 340 if frequency == "annual" else 80
        for a_, b_ in zip(section["end"], section["end"][1:]):
            test.assertGreaterEqual(fin.days_between(a_, b_), gap, (frequency, a_, b_))


class DuplicatePeriodTest(unittest.TestCase):
    """B3 / B4 (synthetic): no duplicate fiscal period after the seed, nor after the next Yahoo runs."""

    def test_azo_like_empty_label_does_not_leave_the_real_one_behind(self):
        result = sec_result({"2024-08-31": 4000, "2025-08-30": 4100},
                            {"2025-08-30": 1500, "2025-11-22": 1000, "2026-02-14": 950, "2026-05-09": 1300})
        stored = quarter_doc({"2024-08-31": 4000, "2025-08-31": 4100},
                             {"2025-08-31": 1500, "2025-11-30": 1000, "2026-02-28": 950,
                              "2026-04-30": None, "2026-05-31": 1300})
        doc, info = sec.merge_into_document(stored, result, "TEST", NOW)
        self.assertEqual(doc["quarterly"]["end"], ["2025-08-30", "2025-11-22", "2026-02-14", "2026-05-31"])
        self.assertEqual(doc["quarterly"]["src"], ["sec"] * 4)
        self.assertEqual(doc["quarterly"]["values"]["revenue"][-1], 1300)        # SEC values, Yahoo's label
        self.assertEqual(info["counts"]["replacedYahoo"], 2 + 5)
        labels_unique(self, doc)
        run, _ = fin.update_document(copy.deepcopy(doc), yahoo_payload(stored), "TEST", NOW + timedelta(days=1))
        self.assertEqual(run["quarterly"]["end"], doc["quarterly"]["end"])
        labels_unique(self, run)

    def test_dpz_like_earlier_empty_and_later_label(self):
        result = sec_result({"2024-12-29": 4700, "2025-12-28": 4900},
                            {"2025-12-28": 1500, "2026-03-22": 1150, "2026-06-14": 1190})
        stored = quarter_doc({"2024-12-31": 4700, "2025-12-31": 4900},
                             {"2025-12-31": 1500, "2026-03-31": 1150, "2026-05-31": None, "2026-06-30": 1190})
        doc, _ = sec.merge_into_document(stored, result, "TEST", NOW)
        self.assertEqual(doc["quarterly"]["end"], ["2025-12-28", "2026-03-22", "2026-06-30"])
        labels_unique(self, doc)
        run, _ = fin.update_document(copy.deepcopy(doc), yahoo_payload(stored), "TEST", NOW + timedelta(days=1))
        self.assertEqual(run["quarterly"]["end"], doc["quarterly"]["end"])

    def test_csco_like_tail_takes_the_yahoo_label_and_the_next_yahoo_runs_add_nothing_twice(self):
        annual = {"2023-07-29": 57000, "2024-07-27": 54000, "2025-07-26": 56600, "2026-07-25": 59000}
        quarterly = {"2025-10-25": 14800, "2026-01-24": 14900, "2026-04-25": 14700, "2026-07-25": 14600}
        stored = quarter_doc({"2023-07-31": 57000, "2024-07-31": 54000, "2025-07-31": 56600, "2026-07-31": 59000},
                             {"2025-10-31": 14800, "2026-01-31": 14900, "2026-04-30": 14700, "2026-07-31": 14600},
                             key="CSCO", fy_end=7)
        result = sec_result(annual, quarterly)
        doc, info = sec.merge_into_document(stored, result, "CSCO", NOW)
        # only the tail (whose Yahoo label lies after every SEC end) is re-labelled; SEC values kept
        self.assertEqual(doc["annual"]["end"], ["2023-07-29", "2024-07-27", "2025-07-26", "2026-07-31"])
        self.assertEqual(doc["quarterly"]["end"], ["2025-10-25", "2026-01-24", "2026-04-25", "2026-07-31"])
        self.assertEqual(doc["annual"]["src"], ["sec"] * 4)
        self.assertEqual(doc["annual"]["fy"][-1], 2026)
        self.assertEqual(doc["quarterly"]["fq"][-1], 4)
        labels_unique(self, doc)
        # the next Yahoo run finds its 2026-07-31 within ±5 days: nothing is appended
        run1, _ = fin.update_document(copy.deepcopy(doc), yahoo_payload(stored), "CSCO", NOW + timedelta(days=1))
        self.assertEqual((run1["annual"]["end"], run1["quarterly"]["end"]), (doc["annual"]["end"], doc["quarterly"]["end"]))
        # re-seeding (--force) over the seeded and over the Yahoo-updated file is idempotent
        for base in (doc, run1):
            again, info = sec.merge_into_document(copy.deepcopy(base), result, "CSCO", NOW + timedelta(days=2))
            self.assertTrue(fin.same_content(doc, again))
        # a later Yahoo run appends the next quarter once; a later seed with the SEC 2026-10-24 quarter
        # takes that label and gives 2026-07-31 back its SEC date, still without duplicates
        nxt = quarter_doc({}, {"2026-07-31": 14600, "2026-10-31": 15000}, fy_end=7)
        run2, _ = fin.update_document(copy.deepcopy(run1), yahoo_payload(nxt), "CSCO", NOW + timedelta(days=95))
        self.assertEqual(run2["quarterly"]["end"][-2:], ["2026-07-31", "2026-10-31"])
        labels_unique(self, run2)
        later = sec_result(annual, dict(quarterly, **{"2026-10-24": 15000}))
        reseeded, info = sec.merge_into_document(copy.deepcopy(run2), later, "CSCO", NOW + timedelta(days=96))
        self.assertEqual(reseeded["quarterly"]["end"][-2:], ["2026-07-25", "2026-10-31"])
        labels_unique(self, reseeded)
        run3, _ = fin.update_document(copy.deepcopy(reseeded), yahoo_payload(nxt), "CSCO", NOW + timedelta(days=97))
        self.assertEqual(run3["quarterly"]["end"], reseeded["quarterly"]["end"])

    def test_nvda_like_annual_label_six_days_later(self):
        result = sec_result({"2025-01-26": 130000, "2026-01-25": 216000},
                            {"2026-01-25": 68000, "2026-04-26": 74000, "2026-07-26": 80000})
        stored = quarter_doc({"2025-01-31": 130000, "2026-01-31": 216000},
                             {"2026-01-31": 68000, "2026-04-30": 74000, "2026-07-31": 80000}, key="NVDA", fy_end=1)
        doc, _ = sec.merge_into_document(stored, result, "NVDA", NOW)
        self.assertEqual(doc["annual"]["end"], ["2025-01-26", "2026-01-31"])
        # the quarterly tail is within ±5 days of Yahoo's label: build_financials matches it as it is
        self.assertEqual(doc["quarterly"]["end"], ["2026-01-25", "2026-04-26", "2026-07-26"])
        run, _ = fin.update_document(copy.deepcopy(doc), yahoo_payload(stored), "NVDA", NOW + timedelta(days=1))
        self.assertEqual((run["annual"]["end"], run["quarterly"]["end"]), (doc["annual"]["end"], doc["quarterly"]["end"]))
        labels_unique(self, run)
        again, _ = sec.merge_into_document(copy.deepcopy(run), result, "NVDA", NOW + timedelta(days=2))
        self.assertTrue(fin.same_content(doc, again))

    def test_ferg_like_fiscal_year_change_keeps_distinct_periods_but_never_overlapping_ones(self):
        result = sec_result({"2024-07-31": 29600, "2025-07-31": 30700},
                            {"2024-10-31": 7700, "2025-03-31": 7200, "2025-06-30": 8300, "2025-10-31": 8100,
                             "2026-03-31": 7400, "2026-06-30": 8700})
        stored = quarter_doc({"2024-07-31": 29600, "2025-07-31": 30700, "2025-12-31": None},
                             {"2025-04-30": 7600, "2025-07-31": 8500, "2025-12-31": None, "2026-09-30": 8800},
                             key="FERG", fy_end=7)
        doc, info = sec.merge_into_document(stored, result, "FERG", NOW)
        self.assertEqual(doc["quarterly"]["end"], ["2024-10-31", "2025-03-31", "2025-06-30", "2025-10-31",
                                                   "2026-03-31", "2026-06-30", "2026-09-30"])
        self.assertEqual(doc["quarterly"]["src"][-1], "yahoo")           # a distinct newer Yahoo quarter stays
        self.assertEqual(doc["annual"]["end"], ["2024-07-31", "2025-07-31"])
        self.assertEqual(info["counts"]["droppedOverlap"], 3 + 1)

    def test_final_label_pass_keeps_sec_over_yahoo(self):
        def item(end, src, fy, fq=None):
            p = fin.period(end, {"revenue": 1}, src)
            p["fy"], p["fq"] = fy, fq
            return p
        annual = [item("2024-12-28", "sec", 2024), item("2024-12-31", "yahoo", 2024), item("2025-12-31", "yahoo", 2025)]
        quarterly = [item("2025-03-29", "sec", 2025, 1), item("2025-04-30", "yahoo", 2025, 1),
                     item("2025-06-30", "yahoo", 2025, 2), item("2025-09-27", "sec", 2025, 3),
                     item("2025-09-30", "sec", 2025, 3)]
        self.assertEqual(sec._dedupe_labels(annual, quarterly), 2)
        self.assertEqual([p["end"] for p in annual], ["2024-12-28", "2025-12-31"])
        self.assertEqual([p["end"] for p in quarterly], ["2025-03-29", "2025-06-30", "2025-09-27", "2025-09-30"])


def debt_rows(entries):
    """{end: {concept: value}} → instant rows per concept (10-K at year ends, else 10-Q)."""
    rows = {}
    for end, parts in entries.items():
        form = "10-K" if end.endswith("12-31") else "10-Q"
        for concept, value in parts.items():
            rows.setdefault(concept, []).append((None, end, value, form, f"{int(end[:4]) + 1}-01-20", "d" + end))
    return rows


class DebtTest(unittest.TestCase):
    """B5 (synthetic): no total debt made of a current part alone, no partial quarter next to full ones."""

    def test_current_part_alone_is_null_and_partial_quarters_are_null(self):
        full = {"LongTermDebtNoncurrent": 900 * M, "LongTermDebtCurrent": 100 * M}
        debt = debt_rows({
            "2022-12-31": full,
            "2023-03-31": {"DebtCurrent": 100 * M},                     # ACN-like: current debt only
            "2023-06-30": {"LongTermDebt": 300 * M},                    # APD-like: one line of the debt
            "2023-09-30": full,
            "2023-12-31": full,
            "2024-03-31": {"LongTermDebtNoncurrent": 350 * M, "LongTermDebtCurrent": 50 * M},   # repaid
            "2024-06-30": {"LongTermDebtAndCapitalLeaseObligations": 380 * M, "DebtCurrent": 20 * M},
            "2024-09-30": {"LongTermDebtNoncurrent": 390 * M, "LongTermDebtCurrent": 10 * M},
            "2024-12-31": {"LongTermDebtNoncurrent": 400 * M, "LongTermDebtCurrent": 5 * M},
        })
        income = {"NetIncomeLoss": [row for y in (2022, 2023, 2024) for row in quarters(y, [1, 1, 1]) + [year(y, 4)]]}
        result = sec.extract_periods(facts_of(dict(income, **debt)))
        q = values_by_end(result.quarterly, "totalDebt")
        a = values_by_end(result.annual, "totalDebt")
        self.assertIsNone(q["2023-03-31"])
        self.assertIsNone(q["2023-06-30"])
        self.assertEqual(q["2023-09-30"], 1000 * M)
        self.assertEqual(q["2023-12-31"], 1000 * M)
        self.assertEqual(a["2023-12-31"], 1000 * M)                     # Q4 and the year: one definition
        self.assertEqual(q["2024-03-31"], 400 * M)                      # a real fall, same composition
        self.assertEqual(q["2024-06-30"], 400 * M)                      # noncurrent line incl. finance leases
        self.assertEqual(result.dropped["totalDebt"], 1)                # 2023-06-30 (the current-only one is None)
        self.assertNotIn("totalDebt", result.period_concepts[("quarterly", "2023-06-30")])


class RevenueScopeTest(unittest.TestCase):
    """B6 (synthetic): a revenue series of another scope than Yahoo's is not written at all."""

    @staticmethod
    def bank():
        # HBAN / FITB-like: the only revenue tag is the ASC 606 fee line (20% of the total)
        years = range(2020, 2025)
        return mini({EXCL: [row for y in years for row in quarters(y, [50, 50, 50]) + [year(y, 200)]],
                     "NetIncomeLoss": [row for y in years for row in quarters(y, [20, 20, 20]) + [year(y, 80)]]})

    def test_extraction_skips_and_merge_writes_nothing(self):
        stored = yahoo_document({"2021-12-31": 1000, "2022-12-31": 1000, "2023-12-31": 1000, "2024-12-31": 1000})
        result = sec.extract_periods(self.bank(), sec.reference_from_document(stored))
        self.assertEqual(result.skip, "revenue-scope-mismatch")
        doc, info = sec.merge_into_document(stored, result, "BANK", NOW)
        self.assertIsNone(doc)
        self.assertEqual((info["status"], info["reason"]), ("skipped", "revenue-scope-mismatch"))
        # without a Yahoo reference (a new file) or within the limit, the file is written as before
        self.assertIsNone(sec.extract_periods(self.bank()).skip)
        near = yahoo_document({"2023-12-31": 260, "2024-12-31": 260})
        self.assertIsNone(sec.extract_periods(self.bank(), sec.reference_from_document(near)).skip)

    def test_cli_leaves_the_yahoo_file_untouched_and_logs_the_reason(self):
        runner = Runner(self)
        stored = yahoo_document({"2021-12-31": 1000, "2022-12-31": 1000, "2023-12-31": 1000, "2024-12-31": 1000},
                                key="BANK")
        write_json(runner.path("BANK.json"), stored)
        before = read_bytes(runner.path("BANK.json"))
        payload = json.dumps(self.bank()).encode("utf-8")
        for args in (("--tickers", "BANK", "--no-splits"), ("--tickers", "BANK", "--no-splits", "--force")):
            code, text = runner.run(world({"BANK": (777, payload)}), *args)
            self.assertEqual(code, 0, text)
            self.assertIn("BANK skipped (revenue-scope-mismatch)", text)
            self.assertIn("skip reason revenue-scope-mismatch: 1", text)
            self.assertEqual(read_bytes(runner.path("BANK.json")), before)


class FollowUpTest(unittest.TestCase):
    """(i)-(iv) of the third audit (synthetic)."""

    class Client:
        def __init__(self, payloads, failure=None):
            self.payloads, self.failure = payloads, failure

        def company_facts(self, cik):
            if cik == "0000034088" and self.failure is not None:
                raise self.failure
            return self.payloads.get(cik)

    def seed_xom(self, failure):
        tmp = tempfile.mkdtemp(prefix="sec-seed-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        successor = mini({"Revenues": [k("2025-01-01", "2025-12-31", 121, filed="2026-02-01")]})
        client = self.Client({"0002115436": successor}, failure)
        with mock.patch.dict(sec.PREDECESSOR_CIKS, {"XOM": "0000034088"}, clear=True):
            return sec.seed_ticker("XOM", {"XOM": "0002115436"}, client, tmp, False, NOW)

    def test_predecessor_fetch_failures_are_not_extraction_errors(self):
        with self.assertRaises(sec.TooManyFailures):
            self.seed_xom(sec.TooManyFailures("10 consecutive failed requests"))
        outcome = self.seed_xom(sec.FetchError("HTTP 500 for x"))
        self.assertEqual((outcome["result"], outcome["detail"]), ("fetch-error", "HTTP 500 for x"))

    def test_missing_curl_cffi_fails_at_once_without_connect_retries(self):
        sleeps, logs = [], []
        checker = sec.SplitChecker(sleep=sleeps.append, log=logs.append)
        with mock.patch.object(sec.importlib.util, "find_spec", return_value=None):
            with self.assertRaises(sec.SplitUnavailable):
                checker.fetch_chart("AAPL")
        self.assertEqual(sleeps, [])                    # no 30 + 60 + 120 s of retries
        self.assertIn("curl_cffi is not installed", checker.failed)
        with self.assertRaises(sec.SplitUnavailable):  # remembered
            checker.fetch_chart("MSFT")
        self.assertEqual(len(logs), 1)

    def test_foreign_filer_code_only_while_the_filer_still_files_foreign_forms(self):
        # CRH-like: 20-F / IFRS until 2023, 10-K since; the reverse (a 10-K once, 20-F now) stays foreign
        def payload(ifrs_filed, tenk_years):
            data = mini({"Revenues": [year(y, 100 + y) for y in tenk_years],
                         "NetIncomeLoss": [year(y, 10) for y in tenk_years]})
            data["facts"]["ifrs-full"] = {"Revenue": {"units": {"USD": [
                {"start": "2021-01-01", "end": "2021-12-31", "val": 5, "form": "20-F", "filed": ifrs_filed}]}}}
            return data
        moved = sec.extract_periods(payload("2023-03-10", (2023, 2024, 2025)))
        self.assertNotIn("foreign-filer", moved.issues)
        self.assertIsNone(moved.skip)
        still = sec.extract_periods(payload("2026-03-10", (2019, 2020)))
        self.assertIn("foreign-filer", still.issues)
        self.assertEqual(sec.extract_periods(payload("2026-03-10", (2020,))).skip, "foreign-filer")

    def test_eps_fallback_concepts(self):
        cont = "IncomeLossFromContinuingOperationsPerDilutedShare"
        both = "EarningsPerShareBasicAndDiluted"
        years = range(2019, 2025)
        rows = {
            "NetIncomeLoss": [row for y in years for row in quarters(y, [50 * M] * 3) + [year(y, 200 * M)]],
            SHARES_CONCEPT: [row for y in years for row in quarters(y, [100 * M] * 3, prefix="s") + [year(y, 100 * M)]],
            # VRSN-like: the EPS tag stops after 2021, continuing-operations EPS goes on
            "EarningsPerShareDiluted": [row for y in (2019, 2020) for row in quarters(y, [0.5] * 3, prefix="e")
                                        + [year(y, 2.0)]],
            cont: [row for y in years for row in quarters(y, [0.5] * 3, prefix="c") + [year(y, 2.0 if y != 2023 else 1.8)]],
            # ENPH-like: basic = diluted tagged with the combined concept (2021 only here)
            both: quarters(2021, [0.5] * 3, prefix="b") + [year(2021, 2.0)],
        }
        result = sec.extract_periods(facts_of(rows))
        a, q = values_by_end(result.annual, "epsDiluted"), values_by_end(result.quarterly, "epsDiluted")
        self.assertEqual(a["2021-12-31"], 2.0)                            # combined tag before continuing
        self.assertEqual(a["2022-12-31"], 2.0)                            # continuing = net income / shares
        self.assertIsNone(a["2023-12-31"])                                # 1.8 ≠ 2.0: discontinued operations
        self.assertEqual(q["2024-06-30"], 0.5)
        self.assertEqual(q["2021-12-31"], 0.5)                            # Q4 of the combined tag
        self.assertIsNone(q["2022-12-31"])                                # never a Q4 of continuing EPS
        self.assertEqual(result.concepts["epsDiluted"], sorted(["EarningsPerShareDiluted", both, cont]))


# ---------------------------------------------------------------------------------------------
# --due: the quarterly re-check of the stored US files. SYNTHETIC stored files and payloads only.
# ---------------------------------------------------------------------------------------------
TODAY_DAY = NOW.date()


def ago(days):
    return (TODAY_DAY - timedelta(days=days)).isoformat()


def stored_file(key, quarter_ends, annual_ends=("2025-12-31",), src="sec"):
    """A minimal stored file that read_sections accepts: one revenue value per period."""
    def section(ends):
        return {"end": list(ends), "src": [src] * len(ends), "values": {"revenue": [1] * len(ends)}}
    return {"version": fin.VERSION, "symbol": key, "market": fin.market_of(key),
            "currency": "TWD" if key.endswith(".TW") else "USD",
            "annual": section(annual_ends), "quarterly": section(quarter_ends)}


class DueRuleTest(unittest.TestCase):
    """due_reason: the 45 / 100 / 3 day boundaries, with the SEC check date as checkedAt."""

    def reason(self, checked, latest_quarter):
        state = {"checkedAt": checked, "latestQuarter": latest_quarter, "latestAnnual": None}
        return sec.due_reason(state, TODAY_DAY)

    def test_never_checked_is_due(self):
        self.assertEqual(self.reason(None, ago(10)), "never checked")

    def test_last_check_older_than_45_days(self):
        self.assertIsNone(self.reason(ago(45), ago(10)))                 # 45 days is not older than 45
        self.assertEqual(self.reason(ago(46), ago(10)), "last check older than 45 days")

    def test_latest_period_older_than_100_days_and_checked_more_than_3_days_ago(self):
        self.assertIsNone(self.reason(ago(10), ago(100)))                # 100 days is not older than 100
        self.assertEqual(self.reason(ago(10), ago(101)), "latest period older than 100 days")
        self.assertIsNone(self.reason(ago(3), ago(101)))                 # checked 3 days ago: not yet
        self.assertEqual(self.reason(ago(4), ago(101)), "latest period older than 100 days")

    def test_a_finished_run_leaves_nothing_due(self):
        self.assertIsNone(self.reason(ago(0), ago(400)))
        self.assertIsNone(self.reason(ago(0), ago(10)))


class SelectDueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sec-due-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = os.path.join(self.tmp, "financials")
        write_json(os.path.join(self.out, "AAPL.json"), stored_file("AAPL", [ago(103)]))
        write_json(os.path.join(self.out, "MSFT.json"), stored_file("MSFT", [ago(10)]))
        write_json(os.path.join(self.out, "NVDA.json"), stored_file("NVDA", [ago(10)]))
        write_json(os.path.join(self.out, "XOM.json"), stored_file("XOM", [ago(10)]))
        write_json(os.path.join(self.out, "YAHOO.json"), stored_file("YAHOO", [ago(10)], src="yahoo"))
        write_json(os.path.join(self.out, "1101.TW.json"), stored_file("1101.TW", [ago(10)]))
        with open(os.path.join(self.out, "BAD.json"), "w", encoding="utf-8") as file:
            file.write("{")
        self.items = {
            "AAPL": {"checkedAt": "2026-09-20", "secCheckedAt": ago(10)},     # Yahoo date is not the SEC one
            "MSFT": {"secCheckedAt": ago(10)},
            "XOM": {"secCheckedAt": ago(50)},
            "1101.TW": {"secCheckedAt": ago(50)},
        }

    def test_candidates_are_us_files_with_sec_periods_oldest_check_first(self):
        due, left = sec.select_due(self.out, self.items, TODAY_DAY)
        self.assertEqual(due, [
            ("NVDA", "never checked", None),
            ("XOM", "last check older than 45 days", ago(50)),
            ("AAPL", "latest period older than 100 days", ago(10)),
        ])
        self.assertEqual(dict(left), {"not due": 1, "not US": 1, "no SEC periods": 1, "unreadable": 1})

    def test_only_narrows_the_candidates(self):
        due, left = sec.select_due(self.out, self.items, TODAY_DAY, only={"AAPL", "1101.TW"})
        self.assertEqual([key for key, _, _ in due], ["AAPL"])
        self.assertEqual(dict(left), {"not US": 1})


class DueRunTest(unittest.TestCase):
    """The --due CLI end to end against FakeSec: dry run, resume, 403 stop, index.json."""

    ENTRIES = {
        "AAPL": (320193, SYNTHETIC_BYTES),
        "BRK-B": (1067983, SYNTHETIC_BYTES),
        "MSFT": (789019, SYNTHETIC_BYTES),
    }

    def seeded(self, runner):
        """SEC files of the three tickers from a plain seed run, and an index.json without any SEC check date."""
        runner.run(world(self.ENTRIES), "--tickers", "AAPL,BRK-B,MSFT", "--no-splits")
        write_json(runner.path("index.json"), {"version": 1, "generatedAt": "2026-10-01T00:00:00Z",
                                               "items": {}, "misses": {}, "failures": {}})

    def items(self, runner):
        return read_json(runner.path("index.json"))["items"]

    @staticmethod
    def facts(cik):
        return sec.FACTS_URL.format(cik=sec.cik10(cik))

    def test_dry_run_lists_due_files_offline_and_writes_nothing(self):
        runner = Runner(self)
        self.seeded(runner)
        write_json(runner.path("1101.TW.json"), stored_file("1101.TW", [ago(10)]))
        before = {name: read_bytes(runner.path(name)) for name in runner.files()}
        fake = world(self.ENTRIES)
        with mock.patch.object(sec.urllib.request, "urlopen", side_effect=AssertionError("network")):
            code, text = runner.run(fake, "--due", "--dry-run")
            code2, text2 = runner.run(fake, "--due", "--dry-run", "--tickers", "MSFT,2330.TW")
        self.assertEqual((code, code2), (0, 0))
        self.assertEqual(fake.requests, [])
        self.assertIn("due now 3 (never checked: 3)", text)
        self.assertIn("not US 1", text)
        self.assertIn("AAPL BRK-B MSFT", text)
        self.assertNotIn("1101.TW", text.split("Dry run")[1])               # Taiwan file is never a candidate
        self.assertIn("[-] 2330.TW not-us", text2)
        self.assertIn("due now 1 (never checked: 1)", text2)
        self.assertEqual(before, {name: read_bytes(runner.path(name)) for name in runner.files()})

    def test_live_run_checks_each_due_file_once_and_the_next_run_has_nothing_due(self):
        runner = Runner(self)
        self.seeded(runner)
        fake = world(self.ENTRIES)
        before = {name: read_bytes(runner.path(name)) for name in runner.files() if name != "index.json"}
        code, text = runner.run(fake, "--due", "--no-splits")
        self.assertEqual(code, 0, text)
        self.assertIn("3 due file(s) -> ", text)
        self.assertIn("[1/3] AAPL unchanged", text)
        self.assertIn("[3/3] MSFT unchanged", text)
        self.assertIn("due at start: 3", text)
        self.assertIn("unchanged: 3", text)
        self.assertIn("remaining due: 0", text)
        self.assertIn("index.json: updated (3 entries)", text)
        self.assertEqual(fake.urls(), [sec.TICKERS_URL, self.facts(320193), self.facts(1067983), self.facts(789019)])
        self.assertEqual({key: item[sec.SEC_CHECKED_KEY] for key, item in self.items(runner).items()},
                         {"AAPL": TODAY, "BRK-B": TODAY, "MSFT": TODAY})
        # unchanged files are not rewritten
        self.assertEqual(before, {name: read_bytes(runner.path(name)) for name in before})
        self.assertEqual(len(before), 3)
        # the next run and the offline dry run find nothing due
        fake2 = world(self.ENTRIES)
        code, text = runner.run(fake2, "--due", "--no-splits")
        self.assertEqual(code, 0)
        self.assertIn("Nothing is due for a re-check.", text)
        self.assertEqual(fake2.requests, [])
        code, text = runner.run(fake2, "--due", "--dry-run")
        self.assertIn("due now 0", text)

    def test_budget_pauses_between_tickers_and_a_rerun_continues(self):
        runner = Runner(self)
        self.seeded(runner)
        ticks = iter([0, 10, 20, 30, 40, 50])               # clock: start, then one reading per ticker
        code, text = runner.run(world(self.ENTRIES), "--due", "--no-splits", "--budget", "25",
                                clock=lambda: next(ticks))
        self.assertEqual(code, 0, text)
        self.assertIn("[2/3] BRK-B unchanged", text)
        self.assertNotIn("[3/3]", text)
        self.assertIn("PAUSED: time budget of 25 s used up; 1 not processed yet", text)
        self.assertIn("remaining due: 1", text)
        self.assertEqual(sorted(key for key, item in self.items(runner).items() if sec.SEC_CHECKED_KEY in item),
                         ["AAPL", "BRK-B"])
        fake = world(self.ENTRIES)
        code, text = runner.run(fake, "--due", "--no-splits")
        self.assertEqual(code, 0, text)
        self.assertIn("due at start: 1", text)
        self.assertIn("remaining due: 0", text)
        self.assertNotIn("PAUSED", text)
        self.assertEqual(fake.urls(), [sec.TICKERS_URL, self.facts(789019)])

    def test_http_403_stops_at_once_without_retry_and_the_file_in_progress_is_not_recorded(self):
        runner = Runner(self)
        self.seeded(runner)
        entries = dict(self.ENTRIES)
        entries["BRK-B"] = (1067983, 403)
        fake = world(entries)
        sleeps = len(runner.sleeps)
        code, text = runner.run(fake, "--due", "--no-splits")
        self.assertEqual(code, 1, text)
        self.assertIn("[1/3] AAPL unchanged", text)
        self.assertIn("STOPPED: SEC returned HTTP 403 for " + self.facts(1067983), text)
        self.assertNotIn("MSFT", text)                       # the stop comes before the third ticker
        self.assertIn("remaining due: 2", text)
        self.assertEqual(fake.urls(), [sec.TICKERS_URL, self.facts(320193), self.facts(1067983)])   # no retry
        self.assertEqual(runner.sleeps[sleeps:], [sec.MIN_INTERVAL] * 3)                           # no backoff
        self.assertEqual(sorted(key for key, item in self.items(runner).items() if sec.SEC_CHECKED_KEY in item),
                         ["AAPL"])

    def test_every_answer_but_a_403_is_a_check(self):
        runner = Runner(self)
        self.seeded(runner)
        entries = dict(self.ENTRIES, **{"BRK-B": (1067983, 500), "MSFT": (789019, 404)})
        code, text = runner.run(world(entries), "--due", "--no-splits")
        self.assertEqual(code, 0, text)
        self.assertIn("BRK-B fetch-error", text)
        self.assertIn("MSFT no-facts", text)
        self.assertIn("remaining due: 0", text)
        self.assertEqual(sorted(self.items(runner)), ["AAPL", "BRK-B", "MSFT"])
        code, text = runner.run(world(entries), "--due", "--no-splits")
        self.assertIn("Nothing is due for a re-check.", text)

    def test_refuses_without_index_json_and_makes_no_request(self):
        runner = Runner(self)
        runner.run(world(self.ENTRIES), "--tickers", "AAPL", "--no-splits")      # no index.json is written
        fake = world(self.ENTRIES)
        code, text = runner.run(fake, "--due", "--no-splits")
        self.assertEqual(code, 2, text)
        self.assertIn("index.json is absent", text)
        self.assertEqual(fake.requests, [])

    def test_index_items_keep_their_sec_check_date_unless_checked_again(self):
        runner = Runner(self)
        self.seeded(runner)
        write_json(runner.path("index.json"), {"version": 1, "items": {"AAPL": {
            "market": "US", "checkedAt": "2026-09-20", "lastListed": "2026-10-01", sec.SEC_CHECKED_KEY: "2026-10-01"}}})
        documents = {"AAPL": read_json(runner.path("AAPL.json")), "MSFT": read_json(runner.path("MSFT.json"))}
        self.assertEqual(sec.update_index(runner.out, documents, TODAY), ("updated", 2, mock.ANY))
        items = self.items(runner)
        self.assertEqual(items["AAPL"][sec.SEC_CHECKED_KEY], "2026-10-01")       # written, not checked: kept
        self.assertEqual(items["AAPL"]["lastListed"], "2026-10-01")
        self.assertNotIn(sec.SEC_CHECKED_KEY, items["MSFT"])
        self.assertEqual(sec.update_index(runner.out, {}, TODAY), ("unchanged", 0, 0))

    def test_dry_run_needs_due(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            sec.parse_args(["--dry-run"])


if __name__ == "__main__":
    unittest.main()

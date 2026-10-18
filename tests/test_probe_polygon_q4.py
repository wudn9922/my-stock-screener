"""Synthetic-only tests for atlas/scripts/probe_polygon_q4.py. No test calls the network; every Polygon response is built here.
Run: python3 -m unittest discover -s tests -p "test_probe_polygon_q4.py" -v
"""
import ast
import io
import json
import os
import subprocess
import re
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import probe_polygon_q4 as probe  # noqa: E402

FAKE_KEY = "TEST-KEY-0123456789"
WORKFLOW = Path(ROOT) / ".github" / "workflows" / "polygon-probe.yml"
SCRIPT = Path(SCRIPTS) / "probe_polygon_q4.py"


# ---------------------------------------------------------------- helpers

class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(self, body, status=200):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Returns queued responses in order; an Exception in the queue is raised. Records every URL and clock time."""

    def __init__(self, queue, clock=None):
        self.queue = list(queue)
        self.urls = []
        self.auth = []
        self.times = []
        self.clock = clock

    def __call__(self, request, timeout=None):
        self.urls.append(request.full_url)
        self.auth.append(request.get_header("Authorization"))
        self.times.append(self.clock() if self.clock else None)
        item = self.queue.pop(0) if self.queue else FakeResponse(json.dumps({"results": []}).encode())
        if isinstance(item, Exception):
            raise item
        return item


def ok(payload):
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def http_error(code, headers=None):
    return urllib.error.HTTPError("https://api.massive.com/stocks/financials/v1/income-statements",
                                  code, "err", headers or {}, None)


def item(end, fq, eps, filing="2020-02-01", timeframe="quarterly", with_field=True):
    """One result in the vendor's flat income-statement shape (massive/rest/models/financials.py)."""
    row = {"timeframe": timeframe, "period_end": end, "fiscal_quarter": fq, "fiscal_year": int(end[:4]),
           "filing_date": filing}
    if with_field:
        row["diluted_earnings_per_share"] = eps
    return row


def key(group, end, fq=4, reference=None, subtraction=None, split_after_end=False, cls="derivation-rejected"):
    return probe.Key(group, "TEST", date.fromisoformat(end), fq, int(end[:4]),
                     cls if group == "m" else None, reference, subtraction, split_after_end)


QUARTER_END = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}


def repo_doc(symbol, quarters, annual, split=None):
    """quarters: list of (fy, fq, eps, src, derived, grade); annual: list of (fy, eps)."""
    q_end = [f"{fy}-{QUARTER_END[fq]}" for fy, fq, *_ in quarters]
    doc = {
        "symbol": symbol, "market": "US",
        "annual": {"end": [f"{fy}-12-31" for fy, _ in annual], "fy": [fy for fy, _ in annual],
                   "src": ["sec"] * len(annual), "values": {"epsDiluted": [e for _, e in annual]}},
        "quarterly": {"end": q_end, "fy": [q[0] for q in quarters], "fq": [q[1] for q in quarters],
                      "src": [q[3] for q in quarters], "derived": [q[4] for q in quarters],
                      "values": {"epsDiluted": [q[2] for q in quarters]},
                      "epsQuality": [({"epsDiluted": q[5]} if q[5] else None) for q in quarters]},
        "split": split or {"adjusted": False, "events": []},
    }
    return doc


def missing_row(symbol, fy, cls="derivation-rejected"):
    return {"symbol": symbol, "end": f"{fy}-12-31", "fy": fy, "class": cls, "detail": "t"}


def write_repo(root, docs, missing_rows):
    fin = Path(root) / "financials"
    fin.mkdir(parents=True, exist_ok=True)
    for doc in docs:
        (fin / f"{doc['symbol']}.json").write_text(json.dumps(doc), encoding="utf-8")
    (fin / "2330.TW.json").write_text(json.dumps({"symbol": "2330.TW", "market": "TW"}), encoding="utf-8")
    classification = Path(root) / "classification.json"
    classification.write_text(json.dumps({"schema": probe.CLASSIFICATION_SCHEMA, "rows": missing_rows}),
                              encoding="utf-8")
    return classification, fin


def standard_repo():
    """AAA: FY2019 Q1..Q3 EPS 1.0 each, annual 4.0, Q4 missing. BBB: grade-A Q4 2019 EPS 1.5, plus one derived Q2."""
    aaa = repo_doc("AAA", [(2019, 1, 1.0, "sec", None, None), (2019, 2, 1.0, "sec", None, None),
                           (2019, 3, 1.0, "sec", None, None), (2019, 4, None, "sec", 1, None)],
                   [(2019, 4.0)])
    bbb = repo_doc("BBB", [(2019, 1, 0.4, "sec", None, None), (2019, 2, 0.4, "sec", 1, None),
                           (2019, 3, 0.4, "sec", None, None), (2019, 4, 1.5, "sec", None, "A")],
                   [(2019, 2.6)])
    return [aaa, bbb], [missing_row("AAA", 2019)]


# ---------------------------------------------------------------- arithmetic

class ToleranceTests(unittest.TestCase):
    def test_floor_and_relative_part(self):
        self.assertAlmostEqual(probe.tolerance(1.0), 0.011)
        self.assertAlmostEqual(probe.tolerance(-1.0), 0.011)
        self.assertAlmostEqual(probe.tolerance(10.0), 0.05)
        self.assertAlmostEqual(probe.tolerance(-10.0), 0.05)

    def test_boundary(self):
        self.assertTrue(probe.agrees(1.011, 1.0))
        self.assertFalse(probe.agrees(1.0111, 1.0))
        # 0.005 * 10 is not exact in binary, so test clearly inside and clearly outside the band
        self.assertTrue(probe.agrees(10.049, 10.0))
        self.assertFalse(probe.agrees(10.051, 10.0))


class WilsonTests(unittest.TestCase):
    def test_known_values(self):
        self.assertIsNone(probe.wilson_lower(0, 0))
        self.assertAlmostEqual(probe.wilson_lower(10, 10), 0.72247, places=4)
        self.assertAlmostEqual(probe.wilson_lower(8, 10), 0.49016, places=4)
        self.assertAlmostEqual(probe.wilson_lower(0, 10), 0.0, places=9)

    def test_monotone_in_successes(self):
        bounds = [probe.wilson_lower(k, 50) for k in range(0, 51)]
        self.assertEqual(bounds, sorted(bounds))


class SignTests(unittest.TestCase):
    def test_opposite_signs_is_a_sign_error_and_a_disagreement(self):
        flags = probe.evaluate_symbol([key("b", "2019-12-31", reference=1.0)],
                                      [probe.Record(date(2019, 12, 31), "Q4", -1.0, "2020-02-01", "")])
        entry = flags["b|4|2019-12-31"]
        self.assertEqual(entry["status"], "ok")
        self.assertTrue(entry["sign_error"])
        self.assertFalse(entry["agree"])

    def test_zero_is_not_a_sign_error(self):
        self.assertFalse(probe.opposite_signs(0.0, 1.0))
        self.assertFalse(probe.opposite_signs(-1.0, 0.0))
        self.assertTrue(probe.opposite_signs(-0.5, 0.5))


# ---------------------------------------------------------------- matching window and 52/53-week years

class WindowTests(unittest.TestCase):
    def status_for(self, end, record_end, fq=4, record_fq=4):
        records = [probe.Record(date.fromisoformat(record_end), f"Q{record_fq}", 2.0, "2020-02-01", "")]
        flags = probe.evaluate_symbol([key("m", end, fq=fq, subtraction=None)], records)
        return flags[f"m|{fq}|{end}"]["status"]

    def test_seven_days_either_side_matches(self):
        self.assertEqual(self.status_for("2019-12-31", "2019-12-24"), "ok")
        self.assertEqual(self.status_for("2019-12-31", "2020-01-07"), "ok")

    def test_eight_days_does_not_match(self):
        self.assertEqual(self.status_for("2019-12-31", "2019-12-23"), "no_record")
        self.assertEqual(self.status_for("2019-12-31", "2020-01-08"), "no_record")

    def test_quarter_must_match(self):
        self.assertEqual(self.status_for("2019-12-31", "2019-12-31", record_fq=3), "no_record")

    def test_52_and_53_week_years(self):
        # A 52/53-week fiscal year can end on the Saturday or Sunday nearest the calendar end, so the Polygon end date
        # can sit up to a week away from the repo's 12-31 or 12-28 label.
        self.assertEqual(self.status_for("2013-12-28", "2014-01-03"), "ok")
        self.assertEqual(self.status_for("2012-12-29", "2013-01-03"), "ok")
        self.assertEqual(self.status_for("2013-12-31", "2014-01-03"), "ok")
        self.assertEqual(self.status_for("2013-12-28", "2014-01-06"), "no_record")

    def test_ambiguous_values_are_not_covered(self):
        records = [probe.Record(date(2019, 12, 31), "Q4", 1.0, "2020-02-01", ""),
                   probe.Record(date(2019, 12, 31), "Q4", 1.5, "2020-03-01", "")]
        flags = probe.evaluate_symbol([key("m", "2019-12-31", subtraction=None)], records)
        self.assertEqual(flags["m|4|2019-12-31"]["status"], "ambiguous")

    def test_agreeing_duplicates_pick_latest_filing(self):
        records = [probe.Record(date(2019, 12, 31), "Q4", 1.000, "2020-02-01", "https://x/10-q.htm"),
                   probe.Record(date(2019, 12, 31), "Q4", 1.004, "2020-03-01", "https://x/10-q.htm")]
        status, best = probe.select(records)
        self.assertEqual(status, "ok")
        self.assertEqual(best.filing_date, "2020-03-01")


# ---------------------------------------------------------------- labels and the subtraction test

class LabelTests(unittest.TestCase):
    def label(self, eps, subtraction, url):
        records = [probe.Record(date(2019, 12, 31), "Q4", eps, "2020-02-01", url)]
        return probe.evaluate_symbol([key("m", "2019-12-31", subtraction=subtraction)], records)["m|4|2019-12-31"]

    def test_equal_to_subtraction_is_derived(self):
        entry = self.label(2.0, 2.0, "https://x/10-q.htm")
        self.assertEqual(entry["label"], "derived")
        self.assertTrue(entry["equal_subtraction"])

    def test_different_from_subtraction_with_quarterly_filing_is_reported(self):
        entry = self.label(2.5, 2.0, "https://x/10-q.htm")
        self.assertEqual(entry["label"], "reported")
        self.assertFalse(entry["equal_subtraction"])

    def test_annual_filing_url_is_derived_even_when_not_subtraction(self):
        entry = self.label(2.5, 2.0, "https://x/filings/10-k.htm")
        self.assertEqual(entry["label"], "derived")
        self.assertFalse(entry["derived_arithmetic"])
        self.assertTrue(entry["derived_form"])

    def test_form_regex_is_token_bounded(self):
        self.assertTrue(probe.looks_like_10k("https://x/a-10k.htm"))
        self.assertFalse(probe.looks_like_10k("https://x/10-q.htm"))
        self.assertFalse(probe.looks_like_10k("https://x/abc10-k.htm"))
        self.assertFalse(probe.looks_like_10k(""))

    def test_untestable_row_is_never_equal(self):
        entry = self.label(2.0, None, "https://x/10-q.htm")
        self.assertIsNone(entry["equal_subtraction"])
        self.assertEqual(entry["label"], "reported")


class PlanTests(unittest.TestCase):
    def test_subtraction_inputs_and_testability(self):
        doc = repo_doc("AAA", [(2019, 1, 1.0, "sec", None, None), (2019, 2, 1.0, "sec", None, None),
                               (2019, 3, 1.0, "sec", None, None), (2019, 4, None, "sec", 1, None)], [(2019, 4.0)])
        keys = probe.symbol_keys("AAA", doc, [missing_row("AAA", 2019)])
        self.assertEqual(keys[0].subtraction, 1.0)
        doc["quarterly"]["values"]["epsDiluted"][1] = None
        keys = probe.symbol_keys("AAA", doc, [missing_row("AAA", 2019)])
        self.assertIsNone(keys[0].subtraction)

    def test_group_membership(self):
        doc = repo_doc("CCC", [(2019, 1, 1.0, "sec", None, None), (2019, 2, 1.0, "sec", 1, None),
                               (2019, 3, 1.0, "other", None, None), (2019, 4, 1.5, "sec", None, "A")],
                       [(2019, 4.0)])
        groups = sorted((k.group, k.fq) for k in probe.symbol_keys("CCC", doc, []))
        self.assertEqual(groups, [("a", 1), ("ad", 2), ("b", 4)])

    def test_split_after_end_flag(self):
        doc = repo_doc("DDD", [(2019, 1, 1.0, "sec", None, None), (2019, 2, 1.0, "sec", None, None),
                               (2019, 3, 1.0, "sec", None, None), (2019, 4, None, "sec", 1, None)], [(2019, 4.0)],
                       split={"adjusted": True, "events": [["2020-06-01", 2]]})
        self.assertTrue(probe.symbol_keys("DDD", doc, [missing_row("DDD", 2019)])[0].split_after_end)
        doc["split"] = {"adjusted": False, "events": [["2020-06-01", 2]]}
        self.assertFalse(probe.symbol_keys("DDD", doc, [missing_row("DDD", 2019)])[0].split_after_end)

    def test_missing_row_must_exist_in_file(self):
        doc = repo_doc("EEE", [(2019, 1, 1.0, "sec", None, None)], [(2019, 4.0)])
        with self.assertRaises(probe.ProbeError):
            probe.symbol_keys("EEE", doc, [missing_row("EEE", 2019)])


# ---------------------------------------------------------------- Polygon response parsing

class ParseTests(unittest.TestCase):
    def test_schema_mismatch_on_structure(self):
        state = probe.ParseState()
        bad = [{"results": [{"period_end": "2019-12-31", "timeframe": "quarterly"}]}]
        with self.assertRaises(probe.SchemaMismatch):
            probe.parse_pages("TEST", bad, state)

    def test_flat_fields_parse_to_a_record(self):
        records, stats = probe.parse_pages("TEST", [{"results": [item("2019-12-31", 4, 1.25)]}], probe.ParseState())
        self.assertEqual(records[0].end, date(2019, 12, 31))
        self.assertEqual(records[0].fiscal_period, "Q4")
        self.assertEqual(records[0].eps, 1.25)
        self.assertEqual(records[0].filing_date, "2020-02-01")
        self.assertEqual(stats["no_eps"], 0)

    def test_period_end_is_checked_for_quarterly_rows(self):
        for bad in ({"period_end": "2019-13-40"}, {"period_end": 20191231}):
            row = dict(item("2019-12-31", 4, 1.0), **bad)
            with self.assertRaises(probe.SchemaMismatch):
                probe.parse_pages("TEST", [{"results": [row]}], probe.ParseState())
        records, _ = probe.parse_pages("TEST", [{"results": [item("2019-12-31", 4.0, 1.0)]}], probe.ParseState())
        self.assertEqual(records[0].fiscal_period, "Q4")

    def test_fiscal_quarter_outside_one_to_four_is_skipped_and_counted(self):
        for fq in (5, 0, 2.5, True, "4", None):
            records, stats = probe.parse_pages("TEST", [{"results": [item("2019-12-31", fq, 1.0)]}],
                                               probe.ParseState())
            self.assertEqual(records, [], repr(fq))
            self.assertEqual(stats["skipped"], 1, repr(fq))

    def test_annual_row_is_skipped_and_counted_and_quarterly_row_still_parses(self):
        annual = item("2019-12-31", None, 4.0, timeframe="quarterly")   # annual row: fiscal_quarter null
        annual_by_timeframe = item("2019-12-31", None, 4.0, timeframe="annual")
        quarterly = item("2019-12-31", 4, 1.25)
        records, stats = probe.parse_pages("TEST", [{"results": [annual, annual_by_timeframe, quarterly]}],
                                           probe.ParseState())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].fiscal_period, "Q4")
        self.assertEqual(records[0].eps, 1.25)
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(stats["records"], 1)

    def test_page_with_only_annual_rows_does_not_raise(self):
        state = probe.ParseState()
        records, stats = probe.parse_pages("TEST", [{"results": [item("2019-12-31", None, 4.0)]}], state)
        self.assertEqual(records, [])
        self.assertEqual(stats["skipped"], 1)

    def test_field_absent_everywhere_stops_on_first_response(self):
        state = probe.ParseState()
        page = [{"results": [item("2019-12-31", 4, 1.0, with_field=False)]}]
        with self.assertRaises(probe.SchemaMismatch):
            probe.parse_pages("TEST", page, state)

    def test_field_absent_is_tolerated_after_the_field_has_been_seen(self):
        state = probe.ParseState()
        probe.parse_pages("A", [{"results": [item("2019-12-31", 4, 1.0)]}], state)
        records, stats = probe.parse_pages("B", [{"results": [item("2019-12-31", 4, 1.0, with_field=False)]}], state)
        self.assertIsNone(records[0].eps)
        self.assertEqual(stats["no_eps"], 1)

    def test_eps_must_be_a_number_or_null(self):
        for bad in ("1.0", {"value": 1.0}, True):
            with self.assertRaises(probe.SchemaMismatch, msg=repr(bad)):
                probe.parse_pages("TEST", [{"results": [item("2019-12-31", 4, bad)]}], probe.ParseState())

    def test_non_quarterly_results_are_skipped(self):
        records, stats = probe.parse_pages("TEST", [{"results": [item("2019-12-31", 4, 1.0, timeframe="annual"),
                                                                 item("2019-12-31", 4, 1.0)]}], probe.ParseState())
        self.assertEqual(len(records), 1)
        self.assertEqual(stats["skipped"], 1)

    def test_null_value_is_no_eps(self):
        records, stats = probe.parse_pages("TEST", [{"results": [item("2019-12-31", 4, None)]}], probe.ParseState())
        self.assertIsNone(records[0].eps)
        self.assertEqual(stats["no_eps"], 1)


# ---------------------------------------------------------------- pacing, retries, key handling

class PacingTests(unittest.TestCase):
    def test_pacer_spaces_requests_twelve_seconds_apart(self):
        clock = FakeClock()
        pacer = probe.Pacer(probe.PACE_SECONDS, clock=clock, sleep=clock.sleep)
        starts = []
        for _ in range(12):
            pacer.wait()
            starts.append(clock.now)
            clock.now += 1.0  # the request itself takes a second
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(g >= 12.0 for g in gaps), gaps)
        for i in range(len(starts) - 5):
            self.assertGreaterEqual(starts[i + 5] - starts[i], 60.0)

    def test_pace_constant_is_five_per_minute_or_slower(self):
        self.assertGreaterEqual(probe.PACE_SECONDS, 12.0)

    def test_source_paces_every_request_including_retries(self):
        clock = FakeClock()
        opener = FakeOpener([ok({"results": []}), ok({"results": []})], clock=clock)
        source = probe.NetworkSource(FAKE_KEY, probe.Pacer(probe.PACE_SECONDS, clock=clock, sleep=clock.sleep),
                                     opener=opener, sleep=clock.sleep)
        source.fetch("AAA")
        source.fetch("BBB")
        gaps = [b - a for a, b in zip(opener.times, opener.times[1:])]
        self.assertTrue(all(g >= 12.0 for g in gaps), gaps)


class RetryTests(unittest.TestCase):
    def make(self, queue):
        clock = FakeClock()
        opener = FakeOpener(queue, clock=clock)
        source = probe.NetworkSource(FAKE_KEY, probe.Pacer(probe.PACE_SECONDS, clock=clock, sleep=clock.sleep),
                                     opener=opener, sleep=clock.sleep)
        return source, opener, clock

    def test_429_backs_off_then_succeeds(self):
        source, opener, clock = self.make([http_error(429), http_error(429), ok({"results": []})])
        source.fetch("AAA")
        self.assertEqual(len(opener.urls), 3)
        backoffs = [s for s in clock.sleeps if s >= 30]
        self.assertEqual(backoffs, [probe.RETRY_429_SECONDS[0], probe.RETRY_429_SECONDS[1]])

    def test_retry_after_header_is_honoured(self):
        source, opener, clock = self.make([http_error(429, {"Retry-After": "7"}), ok({"results": []})])
        source.fetch("AAA")
        self.assertIn(7.0, clock.sleeps)

    def test_429_exhaustion_stops_with_rate_limited(self):
        queue = [http_error(429) for _ in range(len(probe.RETRY_429_SECONDS) + 1)]
        source, opener, _ = self.make(queue)
        with self.assertRaises(probe.RateLimited):
            source.fetch("AAA")
        self.assertEqual(len(opener.urls), len(probe.RETRY_429_SECONDS) + 1)

    def test_rejected_key_stops_at_once_and_message_has_no_key(self):
        source, opener, _ = self.make([http_error(401)])
        with self.assertRaises(probe.KeyRejected) as caught:
            source.fetch("AAA")
        self.assertEqual(len(opener.urls), 1)
        self.assertNotIn(FAKE_KEY, str(caught.exception))
        self.assertEqual(caught.exception.exit_code, 2)

    def test_forbidden_plan_stops_at_once(self):
        source, opener, _ = self.make([http_error(403)])
        with self.assertRaises(probe.KeyRejected):
            source.fetch("AAA")
        self.assertEqual(len(opener.urls), 1)

    def test_gone_endpoint_stops_at_once_and_is_exit_six(self):
        source, opener, _ = self.make([http_error(410)])
        with self.assertRaises(probe.EndpointGone) as caught:
            source.fetch("AAA")
        self.assertEqual(len(opener.urls), 1)
        self.assertEqual(caught.exception.exit_code, 6)
        self.assertNotIn(FAKE_KEY, str(caught.exception))

    def test_rejected_request_is_a_schema_mismatch(self):
        for code in (400, 422):
            source, opener, _ = self.make([http_error(code)])
            with self.assertRaises(probe.SchemaMismatch, msg=str(code)) as caught:
                source.fetch("AAA")
            self.assertEqual(len(opener.urls), 1)
            self.assertNotIn(FAKE_KEY, str(caught.exception))

    def test_key_is_sent_to_api_host_only(self):
        source, opener, _ = self.make([ok({"results": [], "next_url": "https://evil.example/next?cursor=1"})])
        with self.assertRaises(probe.SchemaMismatch):
            source.fetch("AAA")
        self.assertEqual(len(opener.urls), 1)
        self.assertTrue(all(urllib.parse.urlsplit(u).netloc == probe.API_HOST for u in opener.urls))

    def test_next_url_is_followed_with_key_on_the_api_host(self):
        first = {"results": [item("2019-12-31", 4, 1.0)],
                 "next_url": "https://api.massive.com/stocks/financials/v1/income-statements?cursor=abc"}
        second = {"results": [item("2018-12-31", 4, 0.9)]}
        source, opener, _ = self.make([ok(first), ok(second)])
        pages = source.fetch("AAA")
        self.assertEqual(len(pages), 2)
        self.assertIn("cursor=abc", opener.urls[1])
        self.assertTrue(opener.urls[1].startswith("https://api.massive.com/stocks/financials/v1/income-statements"))
        self.assertEqual(opener.auth, ["Bearer " + FAKE_KEY, "Bearer " + FAKE_KEY])
        self.assertNotIn(FAKE_KEY, opener.urls[1])

    def test_no_request_url_contains_the_key(self):
        """Synthetic responses: the key goes in the Authorization header on every request, in no URL."""
        scenarios = {
            "plain": [ok({"results": [item("2019-12-31", 4, 1.0)]})],
            "paged": [ok({"results": [item("2019-12-31", 4, 1.0)],
                          "next_url": "https://api.massive.com/stocks/financials/v1/income-statements?cursor=a"}),
                      ok({"results": [item("2018-12-31", 4, 0.9)]})],
            "next_url_carries_a_key": [
                ok({"results": [item("2019-12-31", 4, 1.0)],
                    "next_url": "https://api.massive.com/stocks/financials/v1/income-statements?cursor=b&apiKey="
                                + FAKE_KEY}),
                ok({"results": [item("2018-12-31", 4, 0.9)]})],
            "retried": [http_error(429, {"Retry-After": "1"}), ok({"results": [item("2019-12-31", 4, 1.0)]})],
        }
        for name, queue in scenarios.items():
            source, opener, _ = self.make(queue)
            source.fetch("AAA")
            self.assertTrue(opener.urls, name)
            for url in opener.urls:
                self.assertNotIn(FAKE_KEY, url, name)
                self.assertNotIn("apiKey", url, name)
            self.assertEqual(set(opener.auth), {"Bearer " + FAKE_KEY}, name)

    def test_request_uses_tickers_timeframe_and_limit(self):
        source, opener, _ = self.make([ok({"results": []})])
        source.fetch("BRK-B")
        parts = urllib.parse.urlsplit(opener.urls[0])
        self.assertEqual(parts.netloc, "api.massive.com")
        self.assertEqual(parts.path, "/stocks/financials/v1/income-statements")
        query = dict(urllib.parse.parse_qsl(parts.query))
        self.assertEqual(query["tickers"], "BRK.B")
        self.assertNotIn("ticker", query)
        self.assertNotIn("apiKey", query)
        self.assertEqual(opener.auth, ["Bearer " + FAKE_KEY])
        self.assertEqual(query["timeframe"], "quarterly")
        self.assertEqual(query["limit"], str(probe.LIMIT))
        self.assertGreaterEqual(int(query["limit"]), 70)


class KeyHandlingTests(unittest.TestCase):
    def test_empty_key_fails_loudly_without_calling(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"POLYGON_API_KEY": "  "}), \
                mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network used")):
            classification, fin = write_repo(tmp, *standard_repo())
            log = Path(tmp) / "run.log"
            code = probe.main(["--classification", str(classification), "--financials-dir", str(fin),
                               "--checkpoint", str(Path(tmp) / "ck.jsonl"), "--report", str(Path(tmp) / "r.json"),
                               "--log", str(log)])
            self.assertEqual(code, 1)
            self.assertIn("POLYGON_API_KEY is empty", log.read_text(encoding="utf-8"))

    def test_log_and_report_never_contain_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            classification, fin = write_repo(tmp, *standard_repo())
            clock = FakeClock()
            opener = FakeOpener([http_error(401)], clock=clock)
            source = probe.NetworkSource(FAKE_KEY, probe.Pacer(probe.PACE_SECONDS, clock=clock, sleep=clock.sleep),
                                         opener=opener, sleep=clock.sleep)
            log = probe.RunLog(Path(tmp) / "run.log")
            with self.assertRaises(probe.KeyRejected):
                probe.run_probe(classification=classification, financials_dir=fin,
                                checkpoint_path=Path(tmp) / "ck.jsonl", report_path=Path(tmp) / "r.json",
                                log=log, source=source, budget_seconds=3600, fresh=False, clock=clock)
            log.write()
            text = (Path(tmp) / "run.log").read_text(encoding="utf-8") + \
                (Path(tmp) / "r.json").read_text(encoding="utf-8")
            self.assertNotIn(FAKE_KEY, text)
            self.assertIn("KeyRejected", text)
            report = json.loads((Path(tmp) / "r.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "partial")


# ---------------------------------------------------------------- pipeline, checkpoint, determinism, no-EPS rule

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.docs, rows = standard_repo()
        self.classification, self.fin = write_repo(self.root, self.docs, rows)
        self.replay = self.root / "replay"
        self.replay.mkdir()
        (self.replay / "AAA.json").write_text(json.dumps(
            {"results": [item("2019-12-31", 4, 1.0),
                         item("2019-01-02", 4, 0.9, timeframe="annual")]}), encoding="utf-8")
        (self.replay / "BBB.json").write_text(json.dumps(
            [{"results": [item("2019-12-31", 4, 1.5)],
              "next_url": "https://api.massive.com/stocks/financials/v1/income-statements?cursor=z"},
             {"results": [item("2018-12-31", 4, 1.2)]}]), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_replay(self, name, clock=None, budget=3600):
        out = self.root / name
        out.mkdir(exist_ok=True)
        log = probe.RunLog(out / "run.log")
        source = probe.ReplaySource(self.replay)
        probe.run_probe(classification=self.classification, financials_dir=self.fin,
                        checkpoint_path=out / "ck.jsonl", report_path=out / "report.json", log=log,
                        source=source, budget_seconds=budget, fresh=False, clock=clock or FakeClock())
        log.write()
        return (out / "report.json").read_bytes(), json.loads((out / "report.json").read_text(encoding="utf-8"))

    def test_report_values_on_synthetic_repo(self):
        _, report = self.run_replay("one")
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["missing_rows"]["covered"], 1)
        self.assertEqual(report["subtraction"]["equal_to_subtraction"], 1)
        self.assertEqual(report["subtraction"]["covered_not_equal_subtraction"], 0)
        self.assertEqual(report["agreement"]["b_grade_a_q4_no_split_after_end"]["compared"], 1)
        self.assertEqual(report["agreement"]["b_grade_a_q4_no_split_after_end"]["agreements"], 1)
        self.assertEqual(report["agreement"]["b_grade_a_q4_split_after_end"]["rows_in_set"], 0)
        self.assertEqual(report["records"]["non_quarterly_skipped"], 1)
        self.assertEqual(report["symbols"]["planned_with_missing_rows"], 1)

    def test_two_runs_give_identical_bytes(self):
        first, _ = self.run_replay("first")
        second, _ = self.run_replay("second")
        self.assertEqual(first, second)

    def test_interrupted_run_resumes_to_the_same_report(self):
        full, _ = self.run_replay("full")
        clock = FakeClock()
        out = self.root / "stopped"
        out.mkdir()
        original = probe.ReplaySource.fetch
        calls = {"n": 0}

        def stop_after_one(self_, symbol):
            calls["n"] += 1
            if calls["n"] > 1:
                raise probe.ApiError("simulated timeout")
            return original(self_, symbol)

        with mock.patch.object(probe.ReplaySource, "fetch", stop_after_one):
            with self.assertRaises(probe.ApiError):
                probe.run_probe(classification=self.classification, financials_dir=self.fin,
                                checkpoint_path=out / "ck.jsonl", report_path=out / "report.json",
                                log=probe.RunLog(out / "run.log"), source=probe.ReplaySource(self.replay),
                                budget_seconds=3600, fresh=False, clock=clock)
        partial = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["symbols"]["done"], 1)
        self.assertIn("ApiError", partial["stop_reason"])
        probe.run_probe(classification=self.classification, financials_dir=self.fin,
                        checkpoint_path=out / "ck.jsonl", report_path=out / "report.json",
                        log=probe.RunLog(out / "run2.log"), source=probe.ReplaySource(self.replay),
                        budget_seconds=3600, fresh=False, clock=clock)
        self.assertEqual((out / "report.json").read_bytes(), full)

    def test_time_budget_stops_mid_run_and_resumes(self):
        full, _ = self.run_replay("budget-full")
        clock = FakeClock()
        out = self.root / "budget"
        out.mkdir()
        original = probe.ReplaySource.fetch

        def slow_fetch(self_, symbol):
            clock.now += 20.0  # each request "takes" 20 seconds of the budget
            return original(self_, symbol)

        with mock.patch.object(probe.ReplaySource, "fetch", slow_fetch):
            probe.run_probe(classification=self.classification, financials_dir=self.fin,
                            checkpoint_path=out / "ck.jsonl", report_path=out / "report.json",
                            log=probe.RunLog(out / "run.log"), source=probe.ReplaySource(self.replay),
                            budget_seconds=30.0, fresh=False, clock=clock)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["symbols"]["done"], 1)
        self.assertIn("time budget", report["stop_reason"])
        probe.run_probe(classification=self.classification, financials_dir=self.fin,
                        checkpoint_path=out / "ck.jsonl", report_path=out / "report.json",
                        log=probe.RunLog(out / "run2.log"), source=probe.ReplaySource(self.replay),
                        budget_seconds=3600, fresh=False, clock=clock)
        self.assertEqual((out / "report.json").read_bytes(), full)

    def test_checkpoint_from_other_input_is_refused(self):
        self.run_replay("first-input")
        checkpoint = self.root / "first-input" / "ck.jsonl"
        with self.assertRaises(probe.CheckpointMismatch):
            probe.Checkpoint(checkpoint, "not-the-same-input-sha")

    def test_fresh_replaces_checkpoint(self):
        self.run_replay("fresh-first")
        checkpoint = self.root / "fresh-first" / "ck.jsonl"
        loaded = probe.Checkpoint(checkpoint, "other", fresh=True)
        self.assertEqual(loaded.symbols, {})

    def test_torn_last_line_is_dropped_and_truncated(self):
        self.run_replay("torn")
        checkpoint = self.root / "torn" / "ck.jsonl"
        good = checkpoint.read_bytes()
        checkpoint.write_bytes(good + b'{"symbol":"ZZZ","keys"')
        sha = probe.fingerprint(self.classification, self.fin)
        loaded = probe.Checkpoint(checkpoint, sha)
        self.assertNotIn("ZZZ", loaded.symbols)
        self.assertEqual(checkpoint.read_bytes(), good)

    def test_no_eps_value_in_report_or_log(self):
        raw, report = self.run_replay("no-eps")
        log = (self.root / "no-eps" / "run.log").read_text(encoding="utf-8")
        floats = []

        def walk(node, path):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, path + [k])
            elif isinstance(node, list):
                for v in node:
                    walk(v, path)
            elif isinstance(node, float):
                floats.append(path[-1])
        walk(report, [])
        self.assertTrue(floats)
        self.assertEqual(set(floats), {"wilson_lower_95"})
        eps_literals = {"1.5", "1.2", "0.9", "2.6"}
        tokens = set(re.findall(r"-?\d+\.\d+", raw.decode("utf-8") + log))
        self.assertFalse(tokens & eps_literals, tokens & eps_literals)
        self.assertNotIn("epsDiluted", raw.decode("utf-8"))

    def test_checkpoint_has_flags_only(self):
        self.run_replay("flags")
        text = (self.root / "flags" / "ck.jsonl").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\d\.\d", text))

    def test_missing_rows_listed_before_agreement_only_symbols(self):
        plan = probe.build_plan(self.classification, self.fin)
        self.assertEqual(plan.symbols[0], "AAA")
        self.assertEqual(plan.symbols[1], "BBB")


class PlanScopeTests(unittest.TestCase):
    def test_only_set_b_adds_symbols_outside_the_missing_rows(self):
        docs, rows = standard_repo()
        ccc = repo_doc("CCC", [(2019, 1, 1.0, "sec", None, None), (2019, 2, 1.0, "sec", 1, None)], [(2019, 4.0)])
        ddd = repo_doc("DDD", [(2019, 1, 1.0, "sec", None, None), (2019, 2, 1.0, "sec", None, None),
                               (2019, 3, 1.0, "sec", None, None), (2019, 4, 1.5, "sec", None, "A")], [(2019, 4.0)])
        with tempfile.TemporaryDirectory() as tmp:
            classification, fin = write_repo(tmp, docs + [ccc, ddd], rows)
            plan = probe.build_plan(classification, fin)
        self.assertEqual(plan.symbols, ["AAA", "BBB", "DDD"])  # BBB carries a grade-A Q4 row; CCC has set a only
        self.assertNotIn("CCC", plan.keys)
        self.assertEqual({k.group for k in plan.keys["DDD"]}, {"b"})
        self.assertEqual({k.group for k in plan.keys["AAA"]}, {"m", "a"})
        self.assertEqual({k.group for k in plan.keys["BBB"]}, {"b"})


class SplitAndGateTests(unittest.TestCase):
    def aggregate_fixture(self):
        def m(end, fy, subtraction, split=False):
            return probe.Key("m", "AAA", date.fromisoformat(end), 4, fy, "derivation-rejected", None,
                             subtraction, split)

        def b(end, fy, reference, split=False):
            return probe.Key("b", "BBB", date.fromisoformat(end), 4, fy, None, reference, None, split)

        aaa_keys = [m("2019-12-31", 2019, 1.0),                 # covered, equal to subtraction
                    m("2018-12-31", 2018, 1.0),                 # covered, not equal, no split: the gate row
                    m("2017-12-31", 2017, 1.0, split=True),     # covered, not equal, split: reported apart
                    m("2016-12-31", 2016, None)]                # covered, not testable
        bbb_keys = [b("2019-12-31", 2019, 1.5),                 # agrees, no split
                    b("2018-12-31", 2018, 1.5, split=True)]     # disagrees and flips sign, split
        aaa_records = [probe.Record(date(2019, 12, 31), "Q4", 1.0, "2020-02-01", ""),
                       probe.Record(date(2018, 12, 31), "Q4", 1.5, "2019-02-01", ""),
                       probe.Record(date(2017, 12, 31), "Q4", 1.5, "2018-02-01", ""),
                       probe.Record(date(2016, 12, 31), "Q4", 2.0, "2017-02-01", "")]
        bbb_records = [probe.Record(date(2019, 12, 31), "Q4", 1.5, "2020-02-01", ""),
                       probe.Record(date(2018, 12, 31), "Q4", -1.5, "2019-02-01", "")]
        plan = probe.Plan(symbols=["AAA", "BBB"], keys={"AAA": aaa_keys, "BBB": bbb_keys},
                          missing_symbols=["AAA"], missing_rows=4)
        done = {}
        for symbol, keys, records in (("AAA", aaa_keys, aaa_records), ("BBB", bbb_keys, bbb_records)):
            done[symbol] = {"records": len(records), "no_eps": 0, "skipped": 0,
                            "keys": probe.evaluate_symbol(keys, records)}
        return probe.aggregate(plan, done, "sha", "complete", None)

    def test_gate_excludes_split_rows_and_reports_them_apart(self):
        report = self.aggregate_fixture()
        sub = report["subtraction"]
        self.assertEqual(sub["testable_covered"], 3)
        self.assertEqual(sub["equal_to_subtraction"], 1)
        self.assertEqual(sub["covered_not_equal_subtraction"], 2)
        self.assertEqual(sub["covered_not_equal_subtraction_split_after_end"], 1)
        self.assertEqual(sub["covered_not_equal_subtraction_no_split_after_end"], 1)
        self.assertEqual(report["gate"]["covered_not_equal_subtraction_no_split_after_end"], 1)
        self.assertFalse(report["gate"]["met"])

    def test_set_b_is_judged_without_split_rows(self):
        report = self.aggregate_fixture()
        clean = report["agreement"]["b_grade_a_q4_no_split_after_end"]
        split = report["agreement"]["b_grade_a_q4_split_after_end"]
        self.assertEqual((clean["rows_in_set"], clean["compared"], clean["agreements"]), (1, 1, 1))
        self.assertEqual((split["rows_in_set"], split["compared"], split["disagreements"]), (1, 1, 1))
        self.assertEqual(split["sign_errors"], 1)
        self.assertEqual(clean["sign_errors"], 0)

    def test_hyphenated_symbols_are_counted_as_mapped(self):
        plan = probe.Plan(symbols=["BRK-B", "AAPL"], keys={"BRK-B": [], "AAPL": []},
                          missing_symbols=[], missing_rows=0)
        report = probe.aggregate(plan, {}, "sha", "partial", None)
        self.assertEqual(report["symbols"]["hyphen_mapped_to_dot"], 1)

    def test_polygon_ticker_maps_dash_to_dot(self):
        self.assertEqual(probe.polygon_ticker("BRK-B"), "BRK.B")
        self.assertEqual(probe.polygon_ticker("TAP-A"), "TAP.A")
        self.assertEqual(probe.polygon_ticker("AAPL"), "AAPL")


class CommitGuardInlineTests(unittest.TestCase):
    """The commit job's guard is inline in the workflow. These tests run that exact text under python3 -I on synthetic
    reports and staged lists."""
    RUN = "123456"
    DEST = "docs/report/source-probes/polygon/" + RUN

    @classmethod
    def setUpClass(cls):
        text = WORKFLOW.read_text(encoding="utf-8")
        commit_job = text.split("\n  commit:", 1)[1]
        lines = commit_job.splitlines()
        start = next(i for i, ln in enumerate(lines) if "python3 -I - " in ln)
        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "PY")
        body = [ln[len(indent):] if ln.startswith(indent) else ln.lstrip() for ln in lines[start + 1:end]]
        cls.code = "\n".join(body) + "\n"

    def report(self, **extra):
        base = {"schema": "polygon-q4-probe-report-v1",
                "agreement": {"b_grade_a_q4_no_split_after_end": {"wilson_lower_95": 0.91, "compared": 1}},
                "rows": [{"symbol": "AAA", "end": "2019-12-31", "covered": True, "label": "reported",
                          "equal_subtraction": None}]}
        base.update(extra)
        return base

    def run_guard(self, report=None, staged=None, run_id=None, dest=None, report_text=None, write_report=True):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            staged_path = Path(tmp) / "staged.txt"
            if write_report:
                text = report_text if report_text is not None else json.dumps(report)
                report_path.write_text(text, encoding="utf-8")
            staged_path.write_text(staged if staged is not None else f"A\t{self.DEST}/report.json\n",
                                   encoding="utf-8")
            return subprocess.run([sys.executable, "-I", "-", self.RUN if run_id is None else run_id,
                                   self.DEST if dest is None else dest,
                                   str(report_path), str(staged_path)],
                                  input=self.code, capture_output=True, text=True, cwd=tmp)

    def test_valid_report_and_single_addition_pass(self):
        result = self.run_guard(self.report())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("guard passed", result.stdout)

    def test_non_numeric_run_id_is_refused(self):
        for run_id in ("12a", "", "../9", "1 2"):
            result = self.run_guard(self.report(), run_id=run_id, dest="docs/report/source-probes/polygon/" + run_id)
            self.assertEqual(result.returncode, 1, run_id)
            self.assertIn("run id is not numeric", result.stderr)

    def test_destination_must_be_the_run_directory(self):
        result = self.run_guard(self.report(), dest="docs/report/source-probes/polygon/999999")
        self.assertEqual(result.returncode, 1)
        self.assertIn("destination", result.stderr)

    def test_wrong_schema_is_refused(self):
        result = self.run_guard(self.report(schema="polygon-q4-probe-report-v0"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("schema", result.stderr)

    def test_float_in_a_nested_list_is_refused_without_printing_its_value(self):
        result = self.run_guard(self.report(rows=[{"eps": 1.2345678}]))
        self.assertEqual(result.returncode, 1)
        self.assertIn("eps", result.stderr)
        self.assertNotIn("1.2345678", result.stdout + result.stderr)

    def test_float_under_another_top_level_key_is_refused(self):
        result = self.run_guard(self.report(flag=0.5))
        self.assertEqual(result.returncode, 1)
        self.assertIn("flag", result.stderr)

    def test_non_finite_wilson_value_is_refused(self):
        bad = self.report()
        bad["agreement"]["b_grade_a_q4_no_split_after_end"]["wilson_lower_95"] = float("nan")
        result = self.run_guard(bad)
        self.assertEqual(result.returncode, 1)

    def test_extra_staged_file_is_refused(self):
        result = self.run_guard(self.report(), staged=f"A\t{self.DEST}/report.json\nA\tdocs/x.json\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly one staged change", result.stderr)

    def test_modification_of_an_existing_file_is_refused(self):
        result = self.run_guard(self.report(), staged=f"M\t{self.DEST}/report.json\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addition", result.stderr)

    def test_other_path_is_refused(self):
        result = self.run_guard(self.report(), staged="A\tdocs/report/financials/AAPL.json\n")
        self.assertEqual(result.returncode, 1)

    def test_empty_staged_list_is_refused(self):
        result = self.run_guard(self.report(), staged="")
        self.assertEqual(result.returncode, 1)

    def test_missing_report_is_refused(self):
        result = self.run_guard(write_report=False)
        self.assertNotEqual(result.returncode, 0)

    def test_guard_runs_no_repo_code(self):
        commit_job = WORKFLOW.read_text(encoding="utf-8").split("\n  commit:", 1)[1]
        self.assertIn("python3 -I - ", commit_job)
        self.assertNotIn("atlas/scripts", commit_job)
        self.assertNotIn("probe_polygon", commit_job)


class ScriptShapeTests(unittest.TestCase):
    def test_script_is_stdlib_only(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        stdlib = set(sys.stdlib_module_names)
        self.assertTrue(names <= stdlib, names - stdlib)

    def test_no_sec_gov_anywhere(self):
        for path in (SCRIPT, WORKFLOW, Path(__file__)):
            self.assertNotIn("sec" + ".gov", path.read_text(encoding="utf-8").lower(), path.name)

    def test_only_the_api_host_is_named(self):
        hosts = set(re.findall(r"https?://([A-Za-z0-9.\-]+)", SCRIPT.read_text(encoding="utf-8")))
        self.assertEqual(hosts, {"api.massive.com"})


class WorkflowShapeTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.top = self.text.split("\njobs:", 1)[0]
        self.probe_job = self.text.split("\njobs:", 1)[1].split("\n  commit:", 1)[0]
        self.commit_job = self.text.split("\n  commit:", 1)[1]

    def steps(self, job_text):
        return re.split(r"\n\s*- name:", job_text)

    def test_manual_only(self):
        self.assertIn("workflow_dispatch:", self.text)
        self.assertNotIn("schedule:", self.text)
        self.assertNotIn("cron", self.text)

    def test_default_permissions_are_read_only(self):
        block = self.top.split("permissions:", 1)[1].split("\nconcurrency:", 1)[0]
        self.assertIn("contents: read", block)
        self.assertNotIn("write", block)

    def test_probe_job_has_no_write_permission(self):
        self.assertNotIn("permissions:", self.probe_job)
        self.assertNotIn("contents: write", self.probe_job)

    def test_only_the_commit_job_has_write_access(self):
        self.assertIn("    permissions:\n      contents: write", self.commit_job)
        self.assertEqual(sum(1 for ln in self.text.splitlines() if ln.strip() == "contents: write"), 1)
        self.assertIn("needs: probe", self.commit_job)

    def test_no_sec_gov(self):
        self.assertNotIn("sec" + ".gov", self.text.lower())

    def test_timeout_and_artifact(self):
        self.assertIn("timeout-minutes: 330", self.probe_job)
        self.assertIn("name: polygon-probe", self.probe_job)
        self.assertIn("if-no-files-found: error", self.probe_job)
        self.assertNotIn("git push", self.probe_job)
        self.assertNotIn("git commit", self.probe_job)

    def test_secret_passed_only_as_env_to_the_run_step(self):
        self.assertEqual(self.text.count("secrets.POLYGON_API_KEY"), 1)
        run_steps = [st for st in self.steps(self.probe_job) if "python3 -u atlas/scripts/probe_polygon_q4.py" in st]
        self.assertEqual(len(run_steps), 1)
        step = run_steps[0]
        self.assertRegex(step, r"env:\s*\n\s+POLYGON_API_KEY: \$\{\{ secrets\.POLYGON_API_KEY \}\}")
        self.assertLess(step.index("POLYGON_API_KEY: ${{ secrets"), step.index("run:"))
        for other in self.steps(self.text):
            if "python3 -u atlas/scripts/probe_polygon_q4.py" not in other:
                self.assertNotIn("POLYGON_API_KEY", other)
        for line in self.text.splitlines():
            if "echo" in line:
                self.assertNotIn("POLYGON", line)

    def test_uploads_report_and_log_only(self):
        upload = [st for st in self.steps(self.probe_job) if "upload-artifact" in st][0]
        self.assertIn("report.json", upload)
        self.assertIn("run.log", upload)
        self.assertNotIn("checkpoint", upload)

    def test_commit_job_commits_report_json_only_to_runs_branch(self):
        self.assertIn("runs/source-probes", self.commit_job)
        self.assertIn("docs/report/source-probes/polygon/$RUN_ID", self.commit_job)
        self.assertIn("python3 -I - ", self.commit_job)
        self.assertNotIn("atlas/scripts", self.commit_job)
        self.assertIn("cp \"$src\" \"$wt/$dest/report.json\"", self.commit_job)
        self.assertNotIn("POLYGON", self.commit_job)
        self.assertNotIn("secrets.", self.commit_job)
        self.assertIn("git push -q origin \"HEAD:refs/heads/$RUNS_BRANCH\"", self.commit_job)

    def test_commit_step_refuses_on_guard_failure(self):
        guard = [ln for ln in self.commit_job.splitlines() if "python3 -I - " in ln][0]
        self.assertNotIn("|| true", guard)
        self.assertIn("set -euo pipefail", self.commit_job)


if __name__ == "__main__":
    unittest.main()

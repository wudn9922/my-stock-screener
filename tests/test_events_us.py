"""Tests for atlas/scripts/build_events_us.py (US earnings dates, Yahoo quote fixtures; no network)."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "atlas", "scripts"))

import build_events_us as events  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "events")
EASTERN = events.EASTERN


def load_quote_response():
    with open(os.path.join(FIXTURES, "quote-response.json"), encoding="utf-8") as file:
        return json.load(file)


class FakeYahoo:
    """Answers v7 quote requests from the fixture; `fail` makes every request raise."""

    fail = None

    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, dict(params)))
        if self.fail is not None:
            raise RuntimeError(self.fail)
        requested = set(params["symbols"].split(","))
        result = [q for q in load_quote_response()["quoteResponse"]["result"] if q["symbol"] in requested]
        return {"quoteResponse": {"result": result, "error": None}}


class EasternTimeTest(unittest.TestCase):
    def test_market_date_can_differ_from_utc_date(self):
        # 2026-10-29 02:30 UTC is still 2026-10-28 22:30 in New York.
        at = 1793241000
        self.assertEqual(datetime.fromtimestamp(at, timezone.utc).date().isoformat(), "2026-10-29")
        self.assertEqual(events.eastern_date(at), "2026-10-28")

    def test_offset_follows_daylight_saving_time(self):
        # EDT (UTC-4) in late October: 13:00 UTC is 09:00 New York time.
        edt = events.eastern_moment(1793278800)
        self.assertEqual((edt.hour, edt.tzname()), (9, "EDT"))
        self.assertEqual(events.session_of(edt, False), "bmo")
        # EST (UTC-5) from 1 November: 14:00 UTC is 09:00 New York time. A fixed -4 offset would say 10:00.
        est = events.eastern_moment(1793887200)
        self.assertEqual((est.hour, est.tzname()), (9, "EST"))
        self.assertEqual(events.session_of(est, False), "bmo")


class SessionTest(unittest.TestCase):
    def at(self, hour, minute, day=27):
        return datetime(2026, 10, day, hour, minute, tzinfo=EASTERN)

    def test_before_open_is_bmo(self):
        self.assertEqual(events.session_of(self.at(7, 0), False), "bmo")
        self.assertEqual(events.session_of(self.at(9, 29), False), "bmo")

    def test_open_minute_and_regular_hours_are_unknown(self):
        self.assertIsNone(events.session_of(self.at(9, 30), False))
        self.assertIsNone(events.session_of(self.at(12, 0), False))
        self.assertIsNone(events.session_of(self.at(15, 59), False))

    def test_after_close_is_amc(self):
        self.assertEqual(events.session_of(self.at(16, 0), False), "amc")
        self.assertEqual(events.session_of(self.at(16, 30), False), "amc")
        self.assertEqual(events.session_of(self.at(22, 15), False), "amc")

    def test_midnight_means_date_only(self):
        self.assertIsNone(events.session_of(self.at(0, 0), False))

    def test_estimates_never_have_a_session(self):
        self.assertIsNone(events.session_of(self.at(7, 0), True))
        self.assertIsNone(events.session_of(self.at(16, 30), True))


class ParseEventsTest(unittest.TestCase):
    def test_parses_every_quote_with_a_symbol(self):
        parsed = events.parse_events(load_quote_response())
        self.assertEqual(set(parsed), {"AAPL", "MSFT", "NVDA", "INTC", "ORCL", "CSCO", "SNOW", "AMD", "BADX", "NOEVT", "SMH", "VFIAX"})
        self.assertEqual(parsed["AAPL"], {
            "quoteType": "EQUITY", "at": 1793305800.0, "start": 1793305800.0, "end": 1793305800.0, "estimate": False,
        })
        self.assertTrue(parsed["NVDA"]["estimate"])
        self.assertEqual(parsed["SMH"]["quoteType"], "ETF")

    def test_malformed_values_become_missing(self):
        parsed = events.parse_events(load_quote_response())
        self.assertIsNone(parsed["BADX"]["at"])
        self.assertFalse(parsed["BADX"]["estimate"])
        self.assertIsNone(parsed["NOEVT"]["at"])
        self.assertEqual(events.parse_events({"quoteResponse": {"result": [{"quoteType": "EQUITY"}]}}), {})
        self.assertEqual(events.parse_events(None), {})


class BuildItemTest(unittest.TestCase):
    def build(self, symbol):
        return events.build_item(events.parse_events(load_quote_response())[symbol])

    def test_confirmed_after_close(self):
        self.assertEqual(self.build("AAPL"), {
            "earningsDate": "2026-10-29", "earningsAt": 1793305800, "window": None, "estimate": False, "session": "amc",
        })

    def test_confirmed_before_open(self):
        self.assertEqual(self.build("MSFT")["session"], "bmo")
        self.assertEqual(self.build("MSFT")["earningsDate"], "2026-10-28")

    def test_estimate_keeps_date_and_range_without_session(self):
        self.assertEqual(self.build("NVDA"), {
            "earningsDate": "2026-11-19",
            "earningsAt": 1795089600,
            "window": ["2026-11-18", "2026-11-24"],
            "estimate": True,
            "session": None,
        })

    def test_date_uses_eastern_day_not_utc_day(self):
        item = self.build("SNOW")
        self.assertEqual(item["earningsDate"], "2026-10-28")
        self.assertEqual(item["session"], "amc")

    def test_single_day_has_no_window(self):
        self.assertIsNone(self.build("INTC")["window"])
        self.assertIsNone(self.build("INTC")["session"])

    def test_midnight_timestamp_has_no_session(self):
        self.assertEqual(self.build("ORCL")["earningsDate"], "2026-12-10")
        self.assertIsNone(self.build("ORCL")["session"])

    def test_standard_time_session(self):
        # 2026-11-05 09:00 New York (EST): before the open.
        self.assertEqual(self.build("CSCO")["session"], "bmo")
        self.assertEqual(self.build("CSCO")["earningsDate"], "2026-11-05")

    def test_sixteen_hundred_is_the_first_amc_minute(self):
        self.assertEqual(self.build("AMD")["session"], "amc")
        self.assertEqual(self.build("AMD")["earningsDate"], "2026-10-27")

    def test_skips_missing_dates_and_non_equities(self):
        for symbol in ("BADX", "NOEVT", "SMH", "VFIAX"):
            with self.subTest(symbol=symbol):
                self.assertIsNone(events.build_item(events.parse_events(load_quote_response())[symbol]))


class UsTickersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.report = os.path.join(self.tmp, "report")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.report)
        os.makedirs(self.data)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, path, value):
        with open(path, "w", encoding="utf-8") as file:
            json.dump(value, file)

    def test_report_sources_are_us_only_and_unique(self):
        self.write(os.path.join(self.report, "universe.json"), {
            "version": 1,
            "fields": ["name", "symbol", "close"],
            "markets": {
                "TW": {"rows": [["台積電", "2330.TW", 1000]]},
                "US": {"rows": [["Apple", "AAPL", 250], ["Nvidia", "NVDA", 180]]},
            },
        })
        self.write(os.path.join(self.report, "latest.json"), {
            "groups": [
                {"market": "US", "items": [{"symbol": "AAPL"}, {"symbol": "AMD"}]},
                {"market": "TW", "items": [{"symbol": "2317.TW"}]},
            ],
        })
        self.write(os.path.join(self.report, "themes.json"), {
            "themes": [{"constituents": [{"symbol": "AVGO"}, {"symbol": "NVDA"}]}],
        })
        self.assertEqual(events.us_tickers(self.report, self.data), ["AAPL", "AMD", "AVGO", "NVDA"])

    def test_excludes_indices_taiwan_and_non_tickers(self):
        self.write(os.path.join(self.report, "latest.json"), {
            "groups": [{"market": "US", "items": [
                {"symbol": "^GSPC"}, {"symbol": "1101.TW"}, {"symbol": "6488.TWO"}, {"symbol": "ABC.TW"},
                {"symbol": "brk-b"}, {"symbol": "BRK-B"},
            ]}],
        })
        self.assertEqual(events.us_tickers(self.report, self.data), ["BRK-B"])

    def test_falls_back_to_us_csv_files(self):
        for name in ("AAPL.csv", "1101.TW.csv", "^TWOII.csv", "MSFT.csv", "BRK-B.csv"):
            with open(os.path.join(self.data, name), "w", encoding="utf-8") as file:
                file.write("Date,Open,High,Low,Close,Volume\n")
        self.assertEqual(events.us_tickers(self.report, self.data), ["AAPL", "BRK-B", "MSFT"])

    def test_nothing_found_returns_empty(self):
        self.assertEqual(events.us_tickers(self.report, self.data), [])


class FetchQuotesTest(unittest.TestCase):
    def test_batches_requests(self):
        symbols = [f"S{i:03d}" for i in range(251)]
        yahoo = FakeYahoo()
        with mock.patch.object(events.time, "sleep"):
            events.fetch_quotes(yahoo, symbols)
        self.assertEqual(len(yahoo.calls), 2)
        self.assertEqual(yahoo.calls[0][1]["symbols"].count(","), 249)
        self.assertIn("earningsTimestamp", yahoo.calls[0][1]["fields"])

    def test_gives_up_after_consecutive_failures(self):
        yahoo = FakeYahoo()
        yahoo.fail = "HTTP 429"
        with mock.patch.object(events.time, "sleep"), self.assertRaises(RuntimeError), \
                contextlib.redirect_stderr(io.StringIO()):
            events.fetch_quotes(yahoo, [f"S{i:03d}" for i in range(1000)])
        self.assertEqual(len(yahoo.calls), events.MAX_CONSECUTIVE_FAILURES)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.report = os.path.join(self.tmp, "report")
        self.data = os.path.join(self.tmp, "data")
        self.out = os.path.join(self.tmp, "valuation")
        os.makedirs(self.report)
        os.makedirs(self.data)
        with open(os.path.join(self.report, "latest.json"), "w", encoding="utf-8") as file:
            json.dump({"groups": [{"market": "US", "items": [
                {"symbol": s} for s in ["AAPL", "MSFT", "NVDA", "INTC", "ORCL", "CSCO", "SNOW", "AMD", "BADX", "SMH"]
            ]}]}, file)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, yahoo_cls=FakeYahoo):
        with mock.patch.object(events, "Yahoo", yahoo_cls), mock.patch.object(events.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            events.main(report_dir=self.report, data_dir=self.data, out_dir=self.out)

    def read_out(self):
        with open(os.path.join(self.out, "us-events.json"), encoding="utf-8") as file:
            return json.load(file)

    def test_writes_document_with_eastern_dates(self):
        self.run_main()
        document = self.read_out()
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["market"], "US")
        self.assertTrue(document["source"].startswith("Yahoo Finance"))
        self.assertRegex(document["generatedAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        # BADX (no usable date), SMH (ETF) and NOEVT are left out.
        self.assertEqual(sorted(document["items"]), ["AAPL", "AMD", "CSCO", "INTC", "MSFT", "NVDA", "ORCL", "SNOW"])
        self.assertEqual(document["items"]["SNOW"]["earningsDate"], "2026-10-28")
        self.assertEqual(document["items"]["AAPL"]["session"], "amc")
        self.assertEqual(document["items"]["NVDA"]["window"], ["2026-11-18", "2026-11-24"])

    def test_failed_quotes_keep_previous_file(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "us-events.json"), "w", encoding="utf-8") as file:
            file.write('{"previous":true}\n')
        yahoo = FakeYahoo()
        yahoo.fail = "HTTP 429"
        with self.assertRaises(RuntimeError):
            self.run_main(lambda: yahoo)
        with open(os.path.join(self.out, "us-events.json"), encoding="utf-8") as file:
            self.assertEqual(file.read(), '{"previous":true}\n')

    def test_low_quote_coverage_is_rejected(self):
        with open(os.path.join(self.report, "latest.json"), "w", encoding="utf-8") as file:
            json.dump({"groups": [{"market": "US", "items": [
                {"symbol": s} for s in ["AAPL", "MSFT"] + [f"ZZ{i}" for i in range(10)]
            ]}]}, file)
        with self.assertRaises(RuntimeError):
            self.run_main()
        self.assertFalse(os.path.exists(os.path.join(self.out, "us-events.json")))

    def test_no_earnings_dates_is_rejected(self):
        class NoDates(FakeYahoo):
            def get_json(self, url, params):
                payload = super().get_json(url, params)
                for quote in payload["quoteResponse"]["result"]:
                    quote.pop("earningsTimestamp", None)
                return payload

        with self.assertRaises(RuntimeError):
            self.run_main(NoDates)
        self.assertFalse(os.path.exists(os.path.join(self.out, "us-events.json")))


if __name__ == "__main__":
    unittest.main()

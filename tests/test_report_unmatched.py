"""
Unmatched / failed stocks in the Atlas report (non-scan groups).

Run: python3 -m unittest discover -s tests
Synthetic prices only; yfinance is stubbed before main is imported and the
downloader is patched, so nothing touches the network.
"""
import json
import os
import sys
import tempfile
import types
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def import_main():
    if "main" in sys.modules:
        return sys.modules["main"]
    sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
    import main  # noqa: E402

    return main


def make_df(closes, volume=1000.0, end="2026-10-09"):
    closes = [float(value) for value in closes]
    index = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [volume] * len(closes),
        },
        index=index,
    )


FLAT = make_df([100.0] * 60)  # close == MA20 -> matched
RISING = make_df([100.0 + i for i in range(60)], volume=5000.0)  # far above MA20 -> unmatched
FALLING = make_df([200.0 - i for i in range(60)], volume=3000.0)  # far below MA20 -> unmatched


class ProcessCustomGroupsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = import_main()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.saved = {
            name: getattr(self.main, name)
            for name in ("DATA_DIR", "download_custom_stock")
        }
        self.main.DATA_DIR = self.tmp.name
        self.frames = {"AAA": FLAT, "BBB": RISING, "DDD": FALLING}
        self.main.download_custom_stock = self.fake_download

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(self.main, name, value)

    def fake_download(self, raw_ticker, group_key=None, group_market=None):
        if raw_ticker == "BOOM":
            raise RuntimeError("network down")
        if raw_ticker == "NAN":
            frame = FLAT.copy()
            frame["Close"] = float("nan")
            return "NAN", frame
        if raw_ticker in self.frames:
            return raw_ticker, self.frames[raw_ticker].copy()
        return None, pd.DataFrame()

    def group(self):
        return {
            "AAA": [20],
            "BBB": [20, 50],
            "CCC": [20],  # no data at all
            "DDD": [20],
            "NAN": [20],  # rows exist but none usable
            "BOOM": [20],  # raises
        }

    def run_group(self, **kwargs):
        return self.main.process_custom_groups(
            self.group(),
            "custom_1",
            test_mode=False,
            group_metadata={"name": "Mine", "market": "us"},
            **kwargs,
        )

    def test_return_value_is_unchanged_by_collection(self):
        plain = self.run_group()
        bucket = []
        collected = self.run_group(unmatched_out=bucket)
        self.assertEqual([item["ticker"] for item in plain], ["AAA"])
        self.assertEqual(
            json.dumps(plain, sort_keys=True, default=str),
            json.dumps(collected, sort_keys=True, default=str),
        )
        # new tickers never leak into the matched list
        self.assertTrue(all(item["ticker"] == "AAA" for item in collected))

    def test_collects_unmatched_and_failed(self):
        bucket = []
        self.run_group(unmatched_out=bucket)
        by_ticker = {record["ticker"]: record for record in bucket}
        self.assertEqual(set(by_ticker), {"BBB", "DDD", "CCC", "NAN", "BOOM"})

        rising = by_ticker["BBB"]
        self.assertEqual(rising["status"], "unmatched")
        self.assertEqual(rising["group_key"], "custom_1")
        self.assertEqual(rising["ma_list"], [20, 50])
        self.assertEqual(rising["close"], 159.0)
        self.assertEqual(rising["change_pct"], round((159 / 158 - 1) * 100, 2))
        self.assertEqual(rising["as_of"], "2026-10-09")
        self.assertEqual(rising["volume"], 5000)
        self.assertEqual(set(rising["ma_values"]), {"20", "50"})
        self.assertAlmostEqual(rising["ma_values"]["20"], 149.5)
        self.assertTrue(rising["note"].startswith("未符合均線條件"))
        self.assertIn("MA20差距(", rising["note"])
        self.assertIn("MA50差距(", rising["note"])
        self.assertIn("現價:159.00", rising["note"])

        falling = by_ticker["DDD"]
        self.assertEqual(falling["status"], "unmatched")
        self.assertLess(falling["change_pct"], 0)

        for ticker, reason in (
            ("CCC", "下載失敗"),
            ("NAN", "資料不足"),
            ("BOOM", "處理失敗"),
        ):
            record = by_ticker[ticker]
            self.assertEqual(record["status"], "failed")
            self.assertIsNone(record["close"])
            self.assertIsNone(record["change_pct"])
            self.assertEqual(record["note"], reason)
            self.assertEqual(record["ma_values"], {})
            self.assertEqual(record["ma_list"], [20])  # the configured MA, so the chart opens with it

    def test_records_are_json_safe(self):
        bucket = []
        self.run_group(unmatched_out=bucket)
        json.dumps(bucket, allow_nan=False)

    def test_empty_group_collects_nothing(self):
        bucket = []
        result = self.main.process_custom_groups(
            {}, "custom_1", test_mode=False, group_metadata={}, unmatched_out=bucket
        )
        self.assertEqual(result, [])
        self.assertEqual(bucket, [])

    def test_test_mode_matches_everything(self):
        bucket = []
        result = self.main.process_custom_groups(
            {"BBB": [20]},
            "custom_1",
            test_mode=True,
            group_metadata={"market": "us"},
            unmatched_out=bucket,
        )
        self.assertEqual([item["ticker"] for item in result], ["BBB"])
        self.assertEqual(bucket, [])

    def test_failed_symbol_uses_first_candidate_for_digits(self):
        bucket = []
        self.main.process_custom_groups(
            {"2330": [20]},
            "tw_g1",
            test_mode=False,
            group_metadata={"market": "tw"},
            unmatched_out=bucket,
        )
        self.assertEqual(bucket[0]["ticker"], "2330.TW")
        self.assertEqual(bucket[0]["status"], "failed")

    def test_group_for_data_dict_keeps_data_dict_clean(self):
        data_dict = {}
        unmatched = {}
        self.main.process_group_for_data_dict(
            data_dict,
            {"custom_1": self.group()},
            {"custom_1": {"market": "us"}},
            "custom_1",
            unmatched_by_group=unmatched,
        )
        self.assertEqual([item["ticker"] for item in data_dict["custom_1"]], ["AAA"])
        self.assertEqual(len(unmatched["custom_1"]), 5)

        plain = {}
        self.main.process_group_for_data_dict(
            plain, {"custom_1": self.group()}, {"custom_1": {"market": "us"}}, "custom_1"
        )
        self.assertEqual(list(plain), ["custom_1"])


class FailedRecordHardeningTest(unittest.TestCase):
    """Review fixes: recording a failure must never raise, and the failed row shows the right code."""

    def setUp(self):
        self.main = import_main()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.saved_dir = self.main.DATA_DIR
        self.main.DATA_DIR = self.tmp.name
        self.addCleanup(setattr, self.main, "DATA_DIR", self.saved_dir)

    def test_garbage_ma_list_does_not_raise(self):
        out = []
        self.main._record_failed_stock(out, "AAA", "g", "下載失敗", 20)  # int is not iterable
        self.assertEqual(out, [])

    def test_failed_symbol_prefers_candidate_with_a_csv(self):
        open(os.path.join(self.tmp.name, "6488.TWO.csv"), "w").close()
        self.assertEqual(self.main._failed_stock_symbol("6488", group_market="tw"), "6488.TWO")

    def test_failed_symbol_defaults_to_first_candidate(self):
        self.assertEqual(self.main._failed_stock_symbol("6488", group_market="tw"), "6488.TW")
        self.assertEqual(self.main._failed_stock_symbol("brk.b", group_market="us"), "BRK-B")

    def test_failed_row_carries_the_taiwan_name(self):
        out = []
        self.main._record_failed_stock(out, "2330.TW", "g", "下載失敗", [20], group_market="tw")
        self.assertEqual(out[0]["status"], "failed")
        self.assertTrue(out[0]["name"])

    def test_unmatched_calculation_error_is_recorded_as_failed(self):
        out = []
        broken = make_df([10.0] * 5)  # no MA column for the requested window
        self.main._record_unmatched_stock(out, "AAA", "g", "us", [20], broken)
        self.assertEqual([row["status"] for row in out], ["failed"])
        self.assertEqual(out[0]["ticker"], "AAA")


class BuildReportGroupsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = import_main()

    def matched(self, symbol, volume=100):
        return {
            "ticker": symbol,
            "name": symbol,
            "volume": volume,
            "ma_list": [20],
            "chart_data": {
                "candles": [
                    {"time": "2026-10-08", "close": 10},
                    {"time": "2026-10-09", "close": 11},
                ],
                "moving_averages": [
                    {"window": 20, "data": [{"time": "2026-10-09", "value": 10.5}]}
                ],
                "title_suffix": "(現價:11.00)",
            },
        }

    def unmatched(self, symbol, status="unmatched", **extra):
        record = {
            "status": status,
            "ticker": symbol,
            "name": "",
            "group_key": "custom_1",
            "ma_list": [20],
            "volume": 7,
            "close": 12.5 if status == "unmatched" else None,
            "change_pct": 1.25 if status == "unmatched" else None,
            "ma_values": {"20": 10.0} if status == "unmatched" else {},
            "as_of": "2026-10-09" if status == "unmatched" else None,
            "note": "未符合均線條件" if status == "unmatched" else "下載失敗",
        }
        record.update(extra)
        return record

    def groups(self, data_dict, unmatched=None, lookup=None):
        metadata = {key: {"name": key, "market": "us"} for key in data_dict}
        return self.main.build_report_groups(
            data_dict,
            metadata,
            list(data_dict),
            metrics_lookup=lookup,
            unmatched_by_group=unmatched,
        )

    def test_default_output_has_no_unmatched(self):
        groups = self.groups({"custom_1": [self.matched("AAA")]})
        self.assertEqual(groups[0]["count"], 1)
        self.assertEqual(groups[0]["unmatchedCount"], 0)
        self.assertNotIn("status", groups[0]["items"][0])

    def test_matched_items_are_identical_with_and_without_unmatched(self):
        data = {"custom_1": [self.matched("AAA", 5), self.matched("BBB", 4)]}
        plain = self.groups(data)
        extra = self.groups(
            data, {"custom_1": [self.unmatched("ZZZ"), self.unmatched("CCC", "failed")]}
        )
        self.assertEqual(plain[0]["items"], extra[0]["items"][:2])
        self.assertEqual(plain[0]["count"], extra[0]["count"])

    def test_order_counts_and_fields(self):
        data = {"custom_1": [self.matched("BBB"), self.matched("AAA")]}
        unmatched = {
            "custom_1": [
                self.unmatched("ZZZ", "failed"),
                self.unmatched("YYY"),
                self.unmatched("XXX"),
                self.unmatched("WWW", "failed"),
            ]
        }
        group = self.groups(data, unmatched)[0]
        self.assertEqual(
            [(item["symbol"], item.get("status", "matched")) for item in group["items"]],
            [
                ("BBB", "matched"),
                ("AAA", "matched"),
                ("XXX", "unmatched"),
                ("YYY", "unmatched"),
                ("WWW", "failed"),
                ("ZZZ", "failed"),
            ],
        )
        self.assertEqual(group["count"], 2)
        self.assertEqual(group["unmatchedCount"], 4)

        unmatched_item = group["items"][2]
        self.assertEqual(
            unmatched_item,
            {
                "symbol": "XXX",
                "name": "",
                "maList": [20],
                "close": 12.5,
                "changePct": 1.25,
                "maValues": {"20": 10.0},
                "note": "未符合均線條件",
                "volume": 7,
                "asOf": "2026-10-09",
                "status": "unmatched",
            },
        )
        failed_item = group["items"][4]
        self.assertIsNone(failed_item["close"])
        self.assertIsNone(failed_item["changePct"])
        self.assertEqual(failed_item["maValues"], {})
        self.assertIsNone(failed_item["asOf"])
        self.assertEqual(failed_item["status"], "failed")
        self.assertEqual(failed_item["note"], "下載失敗")
        json.dumps(group, allow_nan=False)

    def test_dedupe_against_matched_and_within_unmatched(self):
        data = {"custom_1": [self.matched("AAA")]}
        unmatched = {
            "custom_1": [
                self.unmatched("AAA"),  # already matched
                self.unmatched("BBB"),
                self.unmatched("BBB"),  # duplicate
                self.unmatched("BBB", "failed"),  # same ticker, other status
                self.unmatched("ccc", "failed"),  # lower case still normalised
                self.unmatched("CCC", "failed"),
            ]
        }
        group = self.groups(data, unmatched)[0]
        self.assertEqual(
            [(item["symbol"], item.get("status", "matched")) for item in group["items"]],
            [("AAA", "matched"), ("BBB", "unmatched"), ("CCC", "failed")],
        )
        self.assertEqual(group["unmatchedCount"], 2)

    def test_scan_groups_never_output_unmatched(self):
        data = {"tw_all": [self.matched("2330.TW")], "us_all": []}
        unmatched = {"tw_all": [self.unmatched("1101.TW")], "us_all": [self.unmatched("X")]}
        groups = self.groups(data, unmatched)
        for group in groups:
            self.assertEqual(group["kind"], "scan")
            self.assertEqual(group["unmatchedCount"], 0)
            self.assertTrue(all("status" not in item for item in group["items"]))
        self.assertEqual([len(group["items"]) for group in groups], [1, 0])

    def test_group_without_matches_still_lists_unmatched(self):
        group = self.groups({"custom_1": []}, {"custom_1": [self.unmatched("AAA")]})[0]
        self.assertEqual(group["count"], 0)
        self.assertEqual(group["unmatchedCount"], 1)
        self.assertEqual(len(group["items"]), 1)

    def test_metrics_lookup_runs_for_unmatched_but_not_failed(self):
        seen = []

        def lookup(symbol):
            seen.append(symbol)
            return {"bars": 9}

        group = self.groups(
            {"custom_1": [self.matched("AAA")]},
            {"custom_1": [self.unmatched("BBB"), self.unmatched("CCC", "failed")]},
            lookup=lookup,
        )[0]
        self.assertEqual(seen, ["AAA", "BBB"])
        self.assertEqual(group["items"][1]["metrics"], {"bars": 9})
        self.assertIsNone(group["items"][2]["metrics"])

    def test_payload_passes_unmatched_through(self):
        def analyze(*args, **kwargs):
            return None

        args = (
            "2026-10-09",
            {"custom_1": [self.matched("AAA")]},
            {"custom_1": {"name": "Mine", "market": "us"}},
            ["custom_1"],
            {},
            {},
            {},
            {},
            {},
        )
        plain = self.main.build_report_payload(*args, analyze=analyze)
        self.assertEqual(plain["groups"][0]["unmatchedCount"], 0)

        payload = self.main.build_report_payload(
            *args,
            analyze=analyze,
            unmatched_by_group={"custom_1": [self.unmatched("BBB")]},
        )
        self.assertEqual(payload["groups"][0]["count"], 1)
        self.assertEqual(payload["groups"][0]["unmatchedCount"], 1)
        # LINE text is built from the group counts the caller passes in, never from the payload
        self.assertEqual(payload["lineMessages"], plain["lineMessages"])


if __name__ == "__main__":
    unittest.main()

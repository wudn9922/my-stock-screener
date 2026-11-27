"""Offline tests for the optional Alpaca fallback."""
from datetime import date, datetime, timezone
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import alpaca_bars as a
import kline_integrity as k

NOW = datetime(2026, 10, 22, 22, tzinfo=timezone.utc)
KEYS = {"ALPACA_API_KEY_ID": "fake-id", "ALPACA_API_SECRET_KEY": "fake-secret"}
BAR = dict(Open=10, High=12, Low=9, Close=11, Volume=100)
URL = a.URL
DAY = "2026-10-22"


def frame(day=DAY, **changes):
    return pd.DataFrame([BAR | changes], index=pd.to_datetime([day]))


def response(payload=None, status=200, headers=None):
    return Mock(status_code=status, headers=headers or {}, json=Mock(return_value=payload))


def item(day, **changes):
    return dict(t=day + "T04:00:00Z", o=10, h=12, l=9, c=11, v=100) | changes


class ClientTest(unittest.TestCase):
    def setUp(self):
        for obj, attr, value in [(a, "_cache", {}), (a, "_coverage", {}), (a, "_last_request", None)]:
            p = patch.object(obj, attr, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, KEYS, clear=True)
        p.start()
        self.addCleanup(p.stop)
        p = patch("requests.get")
        self.get = p.start()
        self.addCleanup(p.stop)
        p = patch.object(a.time, "sleep")
        self.sleep = p.start()
        self.addCleanup(p.stop)

    def test_disabled_or_unsupported_no_http(self):
        for symbol in ("^GSPC", "FX-USDJPY", "FX-X", "CMD-GOLD", "CMD-X", "2330.TW", "1234.TWO", "BTC-USD", "GC=F"):
            self.assertIsNone(a.alpaca_symbol(symbol))
            self.assertIsNone(a.alpaca_bar(symbol, DAY))
        self.assertEqual(a.alpaca_symbol("BRK-B"), "BRK.B")
        self.assertEqual(a.alpaca_symbol("SPY"), "SPY")
        with patch.dict(os.environ, {"ALPACA_API_SECRET_KEY": ""}):
            self.assertFalse(a.enabled())
            self.assertIsNone(a.prefetch(["AAPL"], DAY, DAY))
            self.assertIsNone(a.alpaca_bar("AAPL", DAY))
        self.get.assert_not_called()

    def test_new_york_day(self):
        self.assertEqual(a.trading_day("2024-07-02T03:59:59Z"), date(2024, 7, 1))
        self.assertEqual(a.trading_day("2024-01-02T04:59:59Z"), date(2024, 1, 1))

    def test_pages_and_range_cache(self):
        self.get.side_effect = [
            response({"bars": {"BRK.B": [item("2024-07-01")]}, "next_page_token": "next"}),
            response({"bars": {"AAPL": [item("2024-07-02")]}}),
        ]
        self.assertTrue(a.prefetch(["BRK-B", "AAPL"], "2024-07-01", "2024-07-02"))
        self.assertEqual(a.alpaca_bar("BRK-B", "2024-07-01")[0], BAR)
        self.assertEqual(a.alpaca_bar("AAPL", "2024-07-02")[0], BAR)
        self.assertIsNone(a.alpaca_bar("AAPL", "2024-07-01"))
        self.assertEqual(self.get.call_count, 2)
        self.assertEqual(self.get.call_args.kwargs["params"]["page_token"], "next")
        self.assertEqual(self.get.call_args.kwargs["params"]["adjustment"], "split")
        self.assertNotIn("fake-secret", a.alpaca_bar("AAPL", "2024-07-02")[1])

    def test_rate_limit_retries_and_failure(self):
        self.get.side_effect = [response(status=429, headers={"Retry-After": "2"}),
                                response({"bars": {"AAPL": [item("2024-07-01")]}})]
        self.assertEqual(a.alpaca_bar("AAPL", "2024-07-01")[0], BAR)
        self.sleep.assert_any_call(2.0)
        self.get.reset_mock()
        self.get.side_effect = None
        self.get.return_value = response(status=429, headers={"Retry-After": "1"})
        self.assertIsNone(a.alpaca_bar("AAPL", "2024-07-02"))
        self.assertEqual(self.get.call_count, 4)


class AcceptanceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.log = self.root / "journal.json"
        self.source = Mock(return_value=(BAR, URL, NOW.isoformat()))

    def accept(self, raw, symbol="AAPL", **kwargs):
        return k.accept_frame(raw, symbol, now=NOW, official=None,
                              log_path=self.log, **kwargs)

    def test_fallback_only_after_failed_retry(self):
        retry = Mock(return_value=frame(Low=15))
        result = self.accept(frame(Low=15), refetch=retry, us_source=self.source)
        self.assertEqual(result.iloc[0].Low, 9)
        retry.assert_called_once()
        self.source.assert_called_once_with("AAPL", DAY)
        event = k.read_log(self.log)["events"][-1]
        self.assertEqual((event["status"], event["reason"], event["sourceUrl"]),
                         ("corrected", "invalid-source-bar-replaced", URL))

    def test_valid_taiwan_and_historical_never_call_fallback(self):
        self.accept(frame(), us_source=self.source)
        self.accept(frame(Low=15), "2330.TW", us_source=self.source)
        raw = pd.concat([frame("2026-10-21", Low=15), frame()])
        self.assertEqual(len(self.accept(raw, us_source=self.source)), 1)
        self.accept(frame(Low=15), refetch=lambda: frame(), us_source=self.source)
        self.source.assert_not_called()

    def test_previously_accepted_bar_is_not_replaced_by_alpaca(self):
        path = self.root / "AAPL.csv"
        original = "Date,Open,High,Low,Close,Volume\n2026-10-22,10,12,8,10.9,105\n"
        path.write_text(original)
        raw = frame(Low=15)
        result = self.accept(raw, retained_path=path, us_source=self.source)
        self.assertEqual(result.iloc[0].Close, 10.9)
        k.write_csv(path, raw, symbol="AAPL", now=NOW, official=None,
                    us_source=self.source, log_path=self.log)
        self.assertEqual(path.read_text(), original)
        self.source.assert_not_called()

    def test_disabled_default_preserves_reason(self):
        with patch.dict(os.environ, KEYS | {"ALPACA_API_KEY_ID": ""}), patch("requests.get") as http:
            self.assertTrue(self.accept(frame(Low=15)).empty)
            http.assert_not_called()
        self.assertEqual(k.read_log(self.log)["events"][-1]["reason"], "invalid-OHLC-bounds")

    def test_mismatches_and_no_reference(self):
        for raw, expected in [(frame(Low=15, Close=10), "close-mismatch-3pct"),
                              (pd.concat([frame("2026-10-21", Open=5, High=6, Low=4, Close=5),
                                          frame(Close=0)]), "close-mismatch-25pct"),
                              (frame(Close=0), "no-valid-reference-close")]:
            with self.subTest(expected=expected):
                result = self.accept(raw, us_source=self.source)
                self.assertNotIn(pd.Timestamp(DAY), result.index)
                self.assertIn("; alpaca:" + expected, k.read_log(self.log)["events"][-1]["reason"])

    def test_insert_is_sorted_and_preserves_existing_lines(self):
        path = self.root / "AAPL.csv"
        path.write_text("Date,Open,High,Low,Close,Volume\n2026-10-21,10,12,9,11,100\n2026-10-23,10,12,9,11,100\n")
        k.fill_missing_bar(path, "AAPL", DAY, BAR, URL, NOW.isoformat(), self.log)
        self.assertEqual(list(pd.read_csv(path).Date), ["2026-10-21", DAY, "2026-10-23"])
        self.assertEqual(k.read_log(self.log)["events"][-1]["reason"], "missing-bar-filled-alpaca")
        before = path.read_bytes()
        for day, values in [(DAY, BAR), ("2026-10-20", BAR | {"Close": 0})]:
            with self.assertRaises(ValueError):
                k.fill_missing_bar(path, "AAPL", day, values, URL, NOW.isoformat(), self.log)
        self.assertEqual(path.read_bytes(), before)


class HistoricalToolsTest(unittest.TestCase):
    def setUp(self):
        from scripts import kline_archive as archive
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "main"
        self.archive = Path(tmp.name) / "archive"
        self.path = self.root / "data/AAPL.csv"
        self.path.parent.mkdir(parents=True)
        self.log = self.root / archive.LOG
        self.path.write_text("Date,Open,High,Low,Close,Volume\n2024-07-01,10,12,9,11,100\n2024-07-03,10,12,9,11,100\n")
        archive.initialize(self.archive)
        archive.put_symbol(self.archive, "AAPL",
                           [("2024-07-01", 10, 12, 9, 11, 100),
                            ("2024-07-03", 10, 12, 9, 11, 100)])
        archive.update_manifest(self.archive)
        k.log_event("AAPL", "2024-07-02", "rejected", old=BAR | {"Low": 13},
                    reason="invalid-OHLC-bounds", log_path=self.log)
        for target, value in [("enabled", True), ("prefetch", True)]:
            p = patch.object(a, target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("requests.get")
        self.http = p.start()
        self.addCleanup(p.stop)

    def snapshot(self):
        return {str(p): p.read_bytes() for p in self.root.parent.rglob("*") if p.is_file()}

    def test_repair_dry_run_and_apply_both_permanent_copies(self):
        from scripts import repair_us_bars_alpaca as repair
        from scripts import kline_archive as archive
        before = self.snapshot()
        with patch.object(a, "alpaca_bar", return_value=(BAR, URL, NOW.isoformat())):
            report = repair.run(self.root, self.archive)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(report["operations"][0]["destinations"], ["data/AAPL.csv", "archive"])
            repair.run(self.root, self.archive, apply=True)
            self.assertFalse(repair.run(self.root, self.archive)["operations"])
        self.assertEqual(list(pd.read_csv(self.path).Date), ["2024-07-01", "2024-07-02", "2024-07-03"])
        self.assertEqual([r[0] for r in archive.symbol_rows(self.archive, "AAPL")],
                         ["2024-07-01", "2024-07-02", "2024-07-03"])
        for log in (self.log, self.archive / archive.LOG):
            self.assertTrue(any(e["status"] == "corrected" and e["sourceUrl"] == URL
                                for e in k.read_log(log)["events"]))
        archive.verify(self.archive)
        self.http.assert_not_called()

    def test_comparison_reports_price_volume_and_missing_dates_without_writes(self):
        from scripts import compare_us_bars_alpaca as compare
        before = self.snapshot()
        def bar(symbol, day):
            if day == "2024-07-03":
                return None
            return BAR | {"Volume": 50}, URL, NOW.isoformat()
        with patch.object(a, "alpaca_bar", side_effect=bar):
            report = compare.compare(self.root, 1, 2)
        self.assertEqual(report["pricesPct"]["Close"]["median"], 0)
        self.assertEqual(report["volumeRatio"]["median"], 2)
        self.assertEqual(report["yahooOnly"], [["AAPL", "2024-07-03"]])
        self.assertEqual(report["alpacaOnly"], [["AAPL", "2024-07-02"]])
        self.assertEqual(self.snapshot(), before)
        self.http.assert_not_called()


if __name__ == "__main__":
    unittest.main()

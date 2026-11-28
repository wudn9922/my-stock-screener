import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_breakout_warning as bw  # noqa: E402


def write_symbol(root, market, symbol, frame, kind="stock"):
    folder = Path(root) / market / symbol
    folder.mkdir(parents=True, exist_ok=True)
    for year, part in frame.groupby(frame.index.year):
        part.to_csv(folder / f"{year}.csv", index_label="Date")
    index = Path(root) / "index" / market
    index.mkdir(parents=True, exist_ok=True)
    (index / f"{symbol.replace('^', '%5E')}.json").write_text(json.dumps({"kind": kind, "symbol": symbol}))


def trend_frame(days, seed, drift=0.001):
    rng = np.random.default_rng(seed)
    close = 50 * np.exp(np.cumsum(drift + rng.normal(0, 0.02, len(days))))
    return pd.DataFrame({"Open": close * (1 + rng.normal(0, 0.005, len(days))), "High": close * 1.015,
                         "Low": close * 0.985, "Close": close, "Volume": 1_000_000.0}, index=days)


class RateSeriesTest(unittest.TestCase):
    def test_window_uses_signals_with_complete_five_day_outcomes(self):
        days = pd.bdate_range("2024-01-01", periods=60)
        events = [(days[i], "ema20", i % 2 == 0) for i in range(60) for _ in range(2)]
        rate, n = bw.rate_series(events, list(days), "ema20")
        t = 40  # signals on t-20 .. t-5 → 16 days × 2 = 32
        self.assertEqual(n.iloc[t], 32)
        self.assertAlmostEqual(rate.iloc[t], 0.5)
        self.assertTrue(np.isnan(n.iloc[19]))     # window t-20 .. t-5 not yet complete
        self.assertTrue(np.isnan(rate.iloc[19]))

    def test_too_few_signals_gives_no_rate(self):
        days = pd.bdate_range("2024-01-01", periods=40)
        events = [(days[i], "ema20", True) for i in range(40)]  # 16 per window → rate
        rate, _ = bw.rate_series(events, list(days), "ema20")
        self.assertAlmostEqual(rate.iloc[30], 1.0)
        rate, n = bw.rate_series(events[::2], list(days), "ema20")  # 8 per window < 15
        self.assertEqual(n.iloc[30], 8)
        self.assertTrue(np.isnan(rate.iloc[30]))

    def test_other_methods_are_ignored(self):
        days = pd.bdate_range("2024-01-01", periods=40)
        events = [(days[i], "swing", True) for i in range(30) for _ in range(3)]
        rate, n = bw.rate_series(events, list(days), "ema20")
        self.assertTrue(rate.isna().all())
        self.assertEqual(float(n.fillna(0).max()), 0.0)


class PercentileAndEpisodesTest(unittest.TestCase):
    def setUp(self):
        self.saved = dict(bw.PARAMS)
        bw.PARAMS.update(percentileWindow=10, percentileMinValid=4, episodeGapDays=20)

    def tearDown(self):
        bw.PARAMS.clear(); bw.PARAMS.update(self.saved)

    def test_percentile_counts_strictly_lower_past_values_and_skips_gaps(self):
        values = pd.Series([0.5, np.nan, 0.6, 0.7, 0.8, 0.4, 0.9])
        pct = bw.trailing_percentile(values)
        self.assertTrue(np.isnan(pct.iloc[3]))          # 3 valid values incl. today < 4
        self.assertAlmostEqual(pct.iloc[4], 100.0)      # 0.8 > 0.5, 0.6, 0.7
        self.assertAlmostEqual(pct.iloc[5], 0.0)        # 0.4 lowest
        self.assertAlmostEqual(pct.iloc[6], 100.0)

    def test_episodes_merge_starts_within_twenty_sessions(self):
        flags = pd.Series([False] * 100)
        for i in (5, 6, 15, 30, 60, 61):
            flags.iloc[i] = True
        # 5 starts; 15 is 10 sessions after the last flagged run → merged; 30 is 15 after 15 → merged; 60 → new
        self.assertEqual(bw.episode_starts(flags), [5, 60])

    def test_forward_drawdown_needs_a_full_horizon(self):
        saved = bw.PARAMS["horizonDays"]
        bw.PARAMS["horizonDays"] = 3
        try:
            close = pd.Series([100, 100, 100, 100, 100.0])
            low = pd.Series([100, 95, 90, 99, 100.0])
            dd = bw.forward_drawdown(close, low)
            self.assertAlmostEqual(dd.iloc[0], -0.10)
            self.assertAlmostEqual(dd.iloc[1], -0.10)
            self.assertTrue(np.isnan(dd.iloc[2]))
        finally:
            bw.PARAMS["horizonDays"] = saved

    def test_method_payload_streak_and_stats(self):
        days = pd.bdate_range("2024-01-01", periods=8)
        rate = pd.Series([.6, .6, .6, .6, .2, .2, .6, .1], index=days)
        n = pd.Series([20.0] * 8, index=days)
        pct = pd.Series([50, 50, 50, 50, 5, 5, 50, 0.0], index=days)
        dd = pd.Series([-.02, -.02, -.02, -.02, -.10, -.03, np.nan, np.nan], index=days)
        bw.PARAMS["episodeGapDays"] = 2                  # days 4 and 7 are separate episodes
        out = bw.method_payload(rate, n, pct, dd, "x")
        self.assertEqual(out["latest"]["lowDays"], 1)
        self.assertTrue(out["latest"]["low"])
        self.assertEqual(out["stats"]["episodes"], 1)    # the 7th-day start lacks an outcome → pending
        self.assertEqual(out["stats"]["pending"], 1)
        self.assertEqual(out["stats"]["hits"], 1)
        self.assertAlmostEqual(out["stats"]["baseHitRate"], 1 / 6, places=4)


class EndToEndTest(unittest.TestCase):
    def test_builds_configured_methods_and_keeps_old_market_on_failure(self):
        days = pd.bdate_range("2021-01-04", periods=420)
        with tempfile.TemporaryDirectory() as tmp:
            write_symbol(tmp, "us", "^GSPC", trend_frame(days, 1), "index")
            for i in range(6):
                write_symbol(tmp, "us", f"S{i}", trend_frame(days, 10 + i))
            out = Path(tmp) / "breakout-warning.json"
            old = {"version": 1, "markets": {"tw": {"asOf": "2026-01-02", "methods": {}}}}
            out.write_text(json.dumps(old))
            saved = bw.MIN_STOCKS
            bw.MIN_STOCKS = 5
            try:
                code = bw.main(["--archive-dir", tmp, "--out", str(out), "--workers", "1"])
            finally:
                bw.MIN_STOCKS = saved
            data = json.loads(out.read_text())
            self.assertEqual(code, 1)                                  # tw has no archive → reported
            self.assertEqual(data["markets"]["tw"], old["markets"]["tw"])   # previous tw kept
            us = data["markets"]["us"]
            self.assertEqual(sorted(us["methods"]), ["ema20", "swing"])
            self.assertEqual(us["asOf"], days[-1].strftime("%Y-%m-%d"))
            self.assertEqual(us["stocks"], 6)
            for method in us["methods"].values():
                self.assertEqual(set(method["stats"]), {"episodes", "hits", "avgDrawdown", "baseHitRate", "baseAvgDrawdown", "pending", "list"})

    def test_both_markets_failing_keeps_the_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "breakout-warning.json"
            out.write_text('{"keep": true}')
            self.assertEqual(bw.main(["--archive-dir", tmp, "--out", str(out), "--workers", "1"]), 1)
            self.assertEqual(json.loads(out.read_text()), {"keep": True})


class CoverageGateTest(unittest.TestCase):
    def test_market_gate_before_write_and_legacy_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "breakout-warning.json"
            old = {"generatedAt": "old", "markets": {"tw": {"stocks": 200, "failedStocks": 0}, "us": {"stocks": 200, "failedStocks": 0}}}
            raw = json.dumps(old, indent=2).encode()
            args = ["--out", str(out), "--workers", "1"]
            def candidate(market, evaluated, failed):
                return {"asOf": "2026-11-27", "stocks": evaluated + failed, "evaluated": evaluated,
                        "failedStocks": failed, "methods": {}, "closes": []}
            for evaluated, failed in [(0, 200), (169, 0), (180, 21)]:
                out.write_bytes(raw)
                with patch.object(bw, "build_market", side_effect=lambda root, market, workers: candidate(market, evaluated, failed) if market == "tw" else candidate(market, 200, 0)):
                    self.assertEqual(bw.main(args), 1)
                self.assertEqual(out.read_bytes(), raw)
            out.write_bytes(raw)
            with patch.object(bw, "build_market", side_effect=lambda root, market, workers: candidate(market, 170, 0) if market == "tw" else candidate(market, 180, 20)):
                self.assertEqual(bw.main(args), 0)
            self.assertEqual(json.loads(out.read_text())["markets"]["tw"]["evaluated"], 170)
            self.assertEqual(json.loads(out.read_text())["markets"]["us"]["failedStocks"], 20)

    def test_no_signals_are_evaluated_not_failed(self):
        days = pd.bdate_range("2026-01-01", periods=5)
        with patch.object(bw, "load_bars", return_value=trend_frame(days, 1)), patch.object(bw, "archive_symbols", return_value=[f"S{i}" for i in range(200)]), patch.object(bw, "_work", return_value=[]):
            market = bw.build_market(".", "tw", 1)
        self.assertEqual(market["evaluated"], 200)
        self.assertEqual(market["failedStocks"], 0)
        bw.validate_market("tw", market, {})


if __name__ == "__main__":
    unittest.main()

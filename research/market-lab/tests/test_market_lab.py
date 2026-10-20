from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from market_lab.backfill import import_authorized_csv
from market_lab.data import clean_ohlcv, load_financial_inventory, load_market_data
from market_lab.events import build_event_results, detect_event_signals, expand_event_catalog, load_event_spec
from market_lab.pipeline import _holdout_overlaps
from market_lab.statistics import benjamini_hochberg, summarize_events
from market_lab.strategies import apply_round_trip_cost, backtest_candidate, expand_strategy_catalog, split_market_sessions


def frame(dates, opens, highs, lows, closes, volumes=None):
    return pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes or [1000] * len(dates),
    }, index=pd.DatetimeIndex(pd.to_datetime(dates), name="Date"))


class MarketLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = load_event_spec(ROOT / "config/events.json")
        cls.catalog = expand_event_catalog(cls.spec)

    def test_catalog_is_frozen_and_has_many_definitions(self):
        self.assertEqual(self.spec["frozen_on"], "2026-10-19")
        self.assertGreaterEqual(len(self.catalog), 60)
        self.assertFalse(any(item["family"] == "fundamental" for item in self.catalog))

    def test_cleaning_deduplicates_weekends_and_bad_ohlc_without_adjusting_price(self):
        raw = pd.DataFrame({
            "Date": ["2026-03-06T23:00:00-05:00", "2026-03-06T23:00:00-05:00", "2026-03-07", "2026-03-09"],
            "Open": [100, 100, 100, 110], "High": [101, 102, 101, 100],
            "Low": [99, 99, 99, 109], "Close": [100, 101, 100, 110], "Volume": [10, 20, 10, 10],
        })
        cleaned, qa = clean_ohlcv(raw)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(float(cleaned.iloc[0]["Close"]), 101.0)
        self.assertEqual(qa["duplicate_dates"], 1)
        self.assertEqual(qa["weekend_rows"], 1)
        self.assertEqual(qa["invalid_ohlc_rows"], 1)
        self.assertEqual(cleaned.index[0].date().isoformat(), "2026-03-06")

    def test_event_signal_and_return_use_next_session_open(self):
        prices = frame(
            ["2026-01-05", "2026-01-06", "2026-01-07"],
            [10.0, 10.0, 11.5], [10.0, 11.0, 12.2], [10.0, 10.0, 11.4], [10.0, 11.0, 12.075],
        )
        results, _ = build_event_results({"XYZ": prices}, self.catalog, self.spec["holding_sessions"])
        event = results.loc[(results["event_id"] == "daily_return_up_10") & (results["symbol"] == "XYZ")].iloc[0]
        self.assertEqual(event["event_date"], "2026-01-06")
        self.assertEqual(event["entry_date"], "2026-01-07")
        self.assertAlmostEqual(event["entry_price"], 11.5)
        self.assertAlmostEqual(event["return_1d"], 12.075 / 11.5 - 1)

    def test_suspected_split_bar_is_not_an_event_signal(self):
        prices = frame(
            ["2026-01-05", "2026-01-06", "2026-01-07"],
            [100.0, 49.0, 49.0], [100.0, 50.0, 50.0], [100.0, 48.0, 48.0], [100.0, 49.0, 49.0],
        )
        signals = detect_event_signals(prices, "XYZ", self.catalog)
        self.assertFalse(bool(signals["daily_return_down_10"].iloc[1]))

    def test_market_breadth_event_uses_current_universe_snapshot(self):
        dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
        frames = {}
        for index in range(100):
            initial, middle, last = (100.0, 90.0, 91.0) if index < 95 else (100.0, 101.0, 102.0)
            opens = [initial, initial, middle]
            closes = [initial, middle, last]
            highs = [price * 1.01 for price in closes]
            lows = [price * 0.99 for price in closes]
            frames[f"U{index:03d}"] = frame(dates, opens, highs, lows, closes)
        results, _ = build_event_results(frames, self.catalog, [1])
        breadth = results.loc[
            (results["event_id"] == "breadth_decline_90")
            & (results["event_date"] == "2026-01-06")
        ]
        self.assertEqual(len(breadth), 100)
        self.assertEqual(breadth["event_date"].nunique(), 1)

    def test_strategy_cost_grid_is_market_specific(self):
        strategy_spec = json.loads((ROOT / "config/strategies.json").read_text(encoding="utf-8"))
        catalog = expand_strategy_catalog(strategy_spec, "2026-10-19T00:00:00+00:00")
        self.assertEqual(len(catalog), 48)
        costs = json.loads(catalog[0]["cost_scenarios_round_trip_bps_by_market"])
        self.assertEqual(costs["US"], [0, 10, 25, 50])
        self.assertEqual(costs["TW"], [0, 30, 50, 100, 150])

    def test_test_period_is_marked_exposed_when_previous_report_overlaps(self):
        previous = {"market_periods": {"US": {"test": {"start": "2026-08-01", "end": "2026-10-15"}}}}
        overlapping = {"US": {"test": {"start": "2026-09-01", "end": "2026-11-01"}}}
        separate = {"US": {"test": {"start": "2026-11-02", "end": "2027-01-01"}}}
        self.assertTrue(_holdout_overlaps(previous, overlapping))
        self.assertFalse(_holdout_overlaps(previous, separate))

    def test_backtest_excludes_suspected_action_during_holding_window(self):
        dates = pd.bdate_range("2026-01-05", periods=30)
        opens = np.full(30, 100.0)
        highs = np.full(30, 101.0)
        lows = np.full(30, 99.0)
        closes = np.full(30, 100.0)
        opens[22], highs[22], lows[22], closes[22] = 49.0, 50.0, 48.0, 49.0
        prices = frame(dates, opens.tolist(), highs.tolist(), lows.tolist(), closes.tolist())
        signal = pd.Series(False, index=prices.index)
        signal.iloc[20] = True
        strategy = {"strategy_id": "TEST", "candidate_key": "test_h2", "holding_sessions": 2, "event_id": "forced"}
        metrics, trades = backtest_candidate(
            "US", strategy, "test",
            {"start": dates[0].strftime("%Y-%m-%d"), "end": dates[-1].strftime("%Y-%m-%d"), "n_sessions": len(dates)},
            {"XYZ": prices}, {"XYZ": {"forced": signal}}, 0, 25,
            dates.strftime("%Y-%m-%d").tolist(),
        )
        self.assertEqual(len(trades), 0)
        self.assertEqual(metrics["suspected_action_or_price_error_trades_excluded"], 1)

    def test_future_rows_do_not_change_past_signals(self):
        dates = pd.bdate_range("2026-01-01", periods=70)
        closes = np.linspace(100, 120, len(dates))
        closes[40] = closes[39] * 1.06
        original = frame(dates, closes * 0.995, closes * 1.01, closes * 0.99, closes)
        changed = original.copy()
        changed.iloc[41:, changed.columns.get_loc("Close")] *= np.linspace(0.4, 2.0, len(changed) - 41)
        changed.iloc[41:, changed.columns.get_loc("High")] = changed.iloc[41:]["Close"] * 1.01
        changed.iloc[41:, changed.columns.get_loc("Low")] = changed.iloc[41:]["Close"] * 0.99
        left = detect_event_signals(original, "XYZ", self.catalog)
        right = detect_event_signals(changed, "XYZ", self.catalog)
        for event_id in left:
            pd.testing.assert_series_equal(left[event_id].iloc[:41], right[event_id].iloc[:41])

    def test_otc_excess_return_requires_aligned_index_prices(self):
        dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
        stock = frame(dates, [100, 100, 111], [100, 111, 113], [100, 100, 110], [100, 110, 112.11])
        index = frame(dates, [100, 100, 101], [100, 101.5, 102], [100, 100, 100.5], [100, 101, 101.5])
        results, _ = build_event_results({"4101.TWO": stock, "^TWOII": index}, self.catalog, [1])
        row = results.loc[(results["event_id"] == "daily_return_up_10") & (results["symbol"] == "4101.TWO")].iloc[0]
        expected_stock = 112.11 / 111 - 1
        expected_index = 101.5 / 101 - 1
        self.assertAlmostEqual(row["return_1d"], expected_stock)
        self.assertAlmostEqual(row["benchmark_return_1d"], expected_index)
        self.assertAlmostEqual(row["excess_return_1d"], expected_stock - expected_index)

    def test_index_only_emits_index_specific_event_definitions(self):
        prices = frame(
            ["2026-01-05", "2026-01-06", "2026-01-07"],
            [100.0, 100.0, 110.0], [100.0, 111.0, 112.0], [100.0, 99.0, 109.0], [100.0, 110.0, 111.0],
        )
        results, _ = build_event_results({"^TWOII": prices}, self.catalog, [1])
        self.assertGreater(len(results), 0)
        self.assertTrue(results["event_id"].str.startswith("otc_index_return_").all())
        self.assertTrue((results["instrument_type"] == "index").all())

    def test_chronological_split_has_no_random_shuffling(self):
        dates = pd.bdate_range("2026-01-01", periods=100).strftime("%Y-%m-%d").tolist()
        splits = split_market_sessions(dates)
        self.assertEqual(splits["train"]["start"], dates[0])
        self.assertLess(splits["train"]["end"], splits["validation"]["start"])
        self.assertLess(splits["validation"]["end"], splits["test"]["start"])
        self.assertEqual(splits["test"]["end"], dates[-1])

    def test_round_trip_cost_reduces_trade_return(self):
        gross = 0.10
        self.assertAlmostEqual(apply_round_trip_cost(gross, 0), gross)
        self.assertLess(apply_round_trip_cost(gross, 25), gross)
        self.assertLess(apply_round_trip_cost(gross, 50), apply_round_trip_cost(gross, 25))

    def test_benjamini_hochberg_is_monotone_in_rank(self):
        p_values = [0.04, 0.001, 0.02, 0.8]
        q_values = benjamini_hochberg(p_values)
        ordered = sorted(zip(p_values, q_values))
        self.assertEqual(ordered, sorted(ordered, key=lambda item: item[0]))
        self.assertTrue(all(q >= p for p, q in zip(p_values, q_values)))

    def test_sparse_event_dates_get_descriptive_statistics_without_inference(self):
        observations = pd.DataFrame({
            "event_id": ["sparse"] * 20, "family": ["price"] * 20, "market": ["TW"] * 20,
            "event_date": ["2026-01-05"] * 20, "symbol": [f"S{i:02d}" for i in range(20)],
            "instrument_type": ["equity"] * 20, "return_1d": np.linspace(-0.1, 0.1, 20),
            "excess_return_1d": np.linspace(-0.1, 0.1, 20),
        })
        stats, _ = summarize_events(observations, [1], {"TW": pd.bdate_range("2026-01-05", periods=30).strftime("%Y-%m-%d").tolist()}, replicates=100)
        row = stats.iloc[0]
        self.assertEqual(row["unique_event_dates"], 1)
        self.assertTrue(pd.isna(row["p_value_block_bootstrap"]))
        self.assertTrue(pd.isna(row["ci95_low"]))
        self.assertIn("descriptive statistics only", row["inference_warning"])

    def test_standard_financial_snapshot_does_not_invent_announcement_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AAPL.json").write_text(json.dumps({
                "symbol": "AAPL", "currency": "USD", "generatedAt": "2026-10-19T00:00:00Z",
                "annual": {"end": ["2024-09-30", "2025-09-30"]},
                "quarterly": {"end": ["2025-12-31", "2026-03-31"]},
            }), encoding="utf-8")
            inventory = load_financial_inventory(financial_dir=root)
            record = inventory[("US", "AAPL")]
            self.assertEqual(record["annual_row_count"], 2)
            self.assertEqual(record["quarterly_row_count"], 2)
            self.assertEqual(record["announcement_timestamp_rows"], 0)

    def test_coverage_marks_price_adjustments_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            pd.DataFrame({"Date": ["2026-01-05", "2026-01-06"], "Open": [10, 10], "High": [10, 11], "Low": [10, 10], "Close": [10, 11], "Volume": [100, 100]}).to_csv(data_dir / "XYZ.csv", index=False)
            _, coverage, _ = load_market_data(data_dir)
            row = coverage.iloc[0]
            self.assertIn("unverified", row["split_adjustment_status"])
            self.assertFalse(bool(row["dividend_adjusted"]))

    def test_authorized_backfill_is_idempotent_and_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = pd.DataFrame({"Date": ["2020-01-02", "2020-01-03"], "Open": [10, 11], "High": [11, 12], "Low": [9, 10], "Close": [11, 12], "Volume": [100, 110]})
            second = pd.DataFrame({"Date": ["2020-01-03", "2020-01-06"], "Open": [11, 12], "High": [12, 13], "Low": [10, 11], "Close": [12, 13], "Volume": [120, 130]})
            first.to_csv(root / "one.csv", index=False)
            second.to_csv(root / "two.csv", index=False)
            kwargs = {"destination": root / "out", "source_id": "licensed_free", "symbol": "XYZ", "market": "US", "provider": "Example", "source_url": "https://example.test/data", "license_text": "CC-BY-4.0", "adjustment_status": "split-and-dividend-adjusted", "exchange_timezone": "America/New_York"}
            import_authorized_csv(root / "one.csv", **kwargs)
            import_authorized_csv(root / "two.csv", **kwargs)
            target = root / "out/sources/licensed_free/XYZ.csv"
            merged = pd.read_csv(target)
            manifest = json.loads((target.parent / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(merged), 3)
            self.assertEqual(merged["Date"].duplicated().sum(), 0)
            self.assertEqual(manifest["last_completed_symbol"], "XYZ")


if __name__ == "__main__":
    unittest.main()

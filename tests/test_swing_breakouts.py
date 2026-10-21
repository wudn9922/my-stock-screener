"""Substantial price waves: sideways rejection, causal confirmation and actual user examples."""
import json
import math
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from test_breakouts import bo, make_bars
from swing_breakouts import swing_high_signals, swing_high_trace, wilder_atr


def candles(closes, highs=None, lows=None, opens=None):
    return make_bars(closes, highs=highs or [v + .5 for v in closes],
                     lows=lows or [v - .5 for v in closes], opens=opens or closes)


def wave(tail=()):
    # A genuine rise followed by a >2 ATR pullback, unlike small local zigzags.
    return candles([100] * 40 + [102, 104, 106, 108, 110, 109, 107] + list(tail))


class SwingSignalsTest(unittest.TestCase):
    def test_atr_uses_twenty_known_previous_closes_and_wilder_smoothing(self):
        frame = candles([100] * 22)
        frame.iloc[21, frame.columns.get_loc('High')] = 103
        atr = wilder_atr(frame)
        self.assertTrue(atr.iloc[:20].isna().all())
        self.assertEqual(atr.iloc[20], 1)
        self.assertEqual(atr.iloc[21], (19 + 3.5) / 20)

    def test_sideways_local_peaks_do_not_replace_the_substantial_swing(self):
        frame = wave([108, 107, 108, 107, 108, 107, 111])
        trace = swing_high_trace(frame)
        self.assertEqual(len(trace.highs), 1)
        self.assertEqual(trace.highs[0].price, 110.5)
        self.assertTrue((trace.levels.iloc[47:] == 110.5).all())
        self.assertEqual(np.flatnonzero(trace.fresh).tolist(), [53])

    def test_continuous_rise_or_decline_has_no_invented_confirmed_peak(self):
        for tail in ([101 + i for i in range(30)], [99 - i for i in range(30)]):
            trace = swing_high_trace(candles([100] * 40 + tail))
            self.assertFalse(trace.fresh.any())
            self.assertEqual(trace.highs, ())

    def test_pullback_is_confirmed_at_close_and_usable_only_next_bar(self):
        trace = swing_high_trace(wave([108]))
        self.assertEqual([(p.pivot_index, p.confirmed_index) for p in trace.highs], [(44, 46)])
        self.assertTrue(math.isnan(trace.levels.iloc[46]))
        self.assertFalse(trace.known.iloc[46])
        self.assertEqual(trace.levels.iloc[47], 110.5)
        self.assertEqual(trace.pivot_indices.iloc[47], 44)
        self.assertEqual(trace.confirmed_indices.iloc[47], 46)

    def test_intraday_wick_cannot_confirm_a_wave_without_close_reversal(self):
        frame = wave().iloc[:46].copy()
        frame.iloc[-1, frame.columns.get_loc('Low')] = 90
        self.assertEqual(swing_high_trace(frame).highs, ())

    def test_reversal_distance_is_frozen_at_extreme_not_inflated_by_pullback(self):
        frame = wave()
        frame.iloc[-1, frame.columns.get_loc('Low')] = 90
        trace = swing_high_trace(frame)
        self.assertEqual(trace.highs[0].confirmed_index, 46)
        self.assertAlmostEqual(trace.highs[0].reversal_distance, 2 * trace.atr.iloc[44])
        self.assertLess(trace.highs[0].reversal_distance, 2 * trace.atr.iloc[46])

    def test_strict_touch_does_not_break_but_intraday_break_close_below_does(self):
        frame = wave([108, 110, 109])
        frame.iloc[-1, frame.columns.get_loc('High')] = 112
        trace = swing_high_trace(frame)
        self.assertFalse(trace.fresh.iloc[-2])  # high == 110.5
        self.assertTrue(trace.fresh.iloc[-1])
        self.assertLess(frame['Close'].iloc[-1], trace.levels.iloc[-1])

    def test_reference_does_not_expire_or_repeat_after_a_fixed_day_count(self):
        trace = swing_high_trace(wave([107] * 70 + [111, 112]))
        self.assertEqual(trace.levels.iloc[-1], 110.5)
        self.assertEqual(np.flatnonzero(trace.fresh).tolist(), [117])

    def test_missing_open_cannot_invent_an_entry(self):
        trace = swing_high_trace(wave([108, 111]).drop(columns='Open'))
        self.assertFalse(trace.fresh.any())
        self.assertFalse(trace.known.any())

    def test_trough_candle_high_cannot_be_reused_as_a_later_rising_leg_peak(self):
        frame = candles([100] * 40 + [100, 103, 100])
        frame.iloc[40, frame.columns.get_loc('High')] = 120
        frame.iloc[40, frame.columns.get_loc('Low')] = 98
        trace = swing_high_trace(frame)
        self.assertTrue(all(p.pivot_index != 40 for p in trace.highs))

    def test_prices_scaled_to_another_currency_preserve_the_same_waves(self):
        frame = wave([108, 111, 108, 109, 113, 110])
        original = swing_high_trace(frame)
        scaled = frame.copy()
        for column in ('Open', 'High', 'Low', 'Close'):
            scaled[column] *= 31.7
        actual = swing_high_trace(scaled)
        self.assertEqual(actual.fresh.tolist(), original.fresh.tolist())
        np.testing.assert_allclose(actual.levels / 31.7, original.levels, equal_nan=True)

    def test_future_prices_cannot_repaint_past_signal_level_or_dates(self):
        for seed in range(6):
            rng = np.random.default_rng(seed)
            frame = candles(np.exp(np.cumsum(rng.normal(0, .018, 160))) * 100)
            full = swing_high_trace(frame)
            for end in (21, 47, 90, 130):
                prefix = swing_high_trace(frame.iloc[:end])
                for name in ('fresh', 'levels', 'known', 'pivot_indices', 'confirmed_indices', 'atr'):
                    pd.testing.assert_series_equal(getattr(prefix, name), getattr(full, name).iloc[:end])
                self.assertEqual(prefix.highs, tuple(p for p in full.highs if p.confirmed_index < end))

    def test_invalid_parameters_fail_explicitly(self):
        for value in (0, -1, float('inf'), float('nan'), True):
            with self.assertRaises(ValueError):
                swing_high_signals(wave(), reversal_atr=value)
        for value in (0, -1, 2.5, True):
            with self.assertRaises(ValueError):
                swing_high_signals(wave(), atr_period=value)


class ReviewedMarketExamplesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / 'fixtures/breakouts/swing_review_cases.json'
        fixture = json.loads(path.read_text())
        cls.frames = {}
        for symbol, rows in fixture['symbols'].items():
            frame = pd.DataFrame(rows, columns=fixture['columns'])
            frame['Date'] = pd.to_datetime(frame['Date'])
            frame = frame.set_index('Date')
            cls.frames[symbol] = bo.clean_bars(frame, frame.index[-1])[0]

    def test_3230_retains_august_high_and_does_not_buy_the_october_sideways_pivot(self):
        frame = self.frames['3230.TWO']
        trace = swing_high_trace(frame)
        self.assertTrue(trace.fresh.loc['2026-08-27'])
        self.assertAlmostEqual(trace.levels.loc['2026-08-27'], 47.25)
        self.assertFalse(trace.fresh.loc['2026-10-01'])
        self.assertEqual(trace.levels.loc['2026-10-01'], 48.75)
        pivot = int(trace.pivot_indices.loc['2026-10-01'])
        self.assertEqual(str(frame.index[pivot].date()), '2026-08-28')

    def test_3287_keeps_the_september_wick_instead_of_creating_a_sideways_high(self):
        frame = self.frames['3287.TWO']
        trace = swing_high_trace(frame)
        self.assertTrue(trace.fresh.loc['2026-10-12'])
        self.assertEqual(trace.levels.loc['2026-10-12'], 45)
        pivot = int(trace.pivot_indices.loc['2026-10-12'])
        self.assertEqual(str(frame.index[pivot].date()), '2026-09-21')
        self.assertFalse(any(frame.index[p.pivot_index].month == 10 for p in trace.highs))

    def test_veev_no_longer_buys_the_small_september_local_peak(self):
        trace = swing_high_trace(self.frames['VEEV'])
        self.assertFalse(trace.fresh.loc['2026-09-24'])
        self.assertAlmostEqual(trace.levels.loc['2026-09-24'], 296.24, places=2)
        self.assertTrue(trace.fresh.loc['2026-10-09'])
        self.assertAlmostEqual(trace.levels.loc['2026-10-09'], 291.4, places=2)

    def test_2305_preserves_the_reviewed_threshold_and_signal(self):
        frame = self.frames['2305.TW']
        trace = swing_high_trace(frame)
        self.assertTrue(trace.fresh.loc['2026-10-13'])
        self.assertEqual(trace.levels.loc['2026-10-13'], 72.5)
        self.assertEqual(str(frame.index[int(trace.pivot_indices.loc['2026-10-13'])].date()), '2026-09-30')

    def test_mu_failed_close_keeps_the_same_key_for_the_next_days_breakout(self):
        frame = self.frames['MU']
        trace = swing_high_trace(frame)
        self.assertTrue(trace.fresh.loc['2026-09-21'])
        self.assertTrue(trace.fresh.loc['2026-09-22'])
        self.assertAlmostEqual(trace.levels.loc['2026-09-21'], 1042.4, places=2)
        self.assertEqual(trace.levels.loc['2026-09-21'], trace.levels.loc['2026-09-22'])
        self.assertEqual(trace.pivot_indices.loc['2026-09-21'], trace.pivot_indices.loc['2026-09-22'])
        self.assertFalse(trace.fresh.loc['2026-09-23'])  # Qualified close consumed this high.


class SwingEntryStatsTest(unittest.TestCase):
    def evaluate(self, frame):
        return bo.build_market({'X': frame}, 'synthetic', 'tw')['windows']['1m']['strategies']['swingHigh']

    def test_intraday_break_close_below_is_not_a_cheap_close_entry(self):
        frame = wave([108, 109, 109, 109, 109])
        frame.iloc[48, frame.columns.get_loc('High')] = 112
        stats = self.evaluate(frame)
        self.assertEqual((stats['n'], stats['maturedN'], stats['wins']), (1, 1, 0))
        self.assertEqual(stats['avgReturn'], round((109 / 110.5 - 1) * 100, 2))

    def test_gap_above_pivot_uses_open_and_keeps_one_entry_per_wave(self):
        frame = wave([108, 116, 112, 112, 112])
        frame.iloc[48, frame.columns.get_loc('Open')] = 115
        frame.iloc[48, frame.columns.get_loc('High')] = 118
        frame.iloc[48, frame.columns.get_loc('Low')] = 114
        stats = self.evaluate(frame)
        self.assertEqual((stats['n'], stats['maturedN'], stats['wins']), (1, 1, 0))
        self.assertEqual(stats['avgReturn'], round((112 / 115 - 1) * 100, 2))

    def test_separate_substantial_waves_can_enter_inside_ten_days(self):
        frame = wave([108, 111, 108, 109, 113, 112, 111, 110])
        stats = self.evaluate(frame)
        self.assertEqual((stats['eligible'], stats['n'], stats['maturedN']), (1, 2, 2))

    def test_asof_cutoff_does_not_use_future_confirmations_or_prices(self):
        frame = wave([108, 111, 108, 109, 113, 112, 111, 110])
        cutoff = frame.index[-4]
        with_future = bo.build_market({'X': frame}, 'synthetic', 'tw', as_of=cutoff)
        truncated = bo.build_market({'X': frame.loc[:cutoff]}, 'synthetic', 'tw')
        self.assertEqual(with_future, truncated)


if __name__ == '__main__':
    unittest.main()

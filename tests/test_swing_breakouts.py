"""波段前高：確認時間、盤中觸發、每個波段一次、理論停買價格與獨立逐日參考。"""
import math
import random
import unittest

import numpy as np

from test_breakouts import AS_OF, bo, make_bars
from swing_breakouts import swing_high_signals


def bars(highs, closes=None, opens=None):
    # Synthetic candles with long upper wicks make the intraday/close distinction explicit.
    return make_bars(closes or [100] * len(highs), highs=highs, lows=[90] * len(highs),
                     opens=opens or [100] * len(highs))


def slow_reference(frame):
    """Independent exhaustive oracle: find every completed historical pivot for each entry day."""
    highs = frame["High"].tolist()
    opens = frame["Open"].tolist()
    hits, levels = [], []
    for day in range(len(highs)):
        pivots = [p for p in range(2, day - 2)
                  if all(highs[p] >= highs[j] for j in (p - 2, p - 1))
                  and all(highs[p] > highs[j] for j in (p + 1, p + 2))]
        if not pivots:
            levels.append(math.nan)
            hits.append(False)
            continue
        pivot = max(pivots)
        level = highs[pivot]
        levels.append(level)
        hits.append(math.isfinite(opens[day]) and opens[day] > 0 and highs[day] > level
                    and not any(highs[prior] > level for prior in range(pivot + 3, day)))
    return hits, levels


class SwingSignalsTest(unittest.TestCase):
    def test_uses_the_recent_swing_even_when_an_older_n_day_high_is_higher(self):
        frame = bars([150] + [100] * 30 + [102, 110, 108, 107, 111])
        fresh, level, _ = swing_high_signals(frame)
        self.assertTrue(fresh.iloc[-1])
        self.assertEqual(level.iloc[-1], 110)
        # Add a much higher high inside 20 days, but before the more recent swing.
        frame.iloc[-15, frame.columns.get_loc("High")] = 140
        clean, _ = bo.clean_bars(frame, frame.index[-1])
        signals = bo.strategy_signals(clean, 0.14)
        self.assertTrue(signals["swingHigh"][0].iloc[-1])
        self.assertFalse(signals["high20"][0].iloc[-1])

    def test_rising_n_day_highs_without_a_confirmed_turn_do_not_count(self):
        frame = bars([100] * 40 + [101, 102, 103, 104, 105])
        fresh, _, _ = swing_high_signals(frame)
        self.assertFalse(fresh.any())

    def test_a_pivot_is_first_usable_after_its_right_hand_candles_have_closed(self):
        frame = bars([100, 102, 110, 108, 107, 111])
        fresh, level, known = swing_high_signals(frame)
        self.assertTrue(level.iloc[:5].isna().all())
        self.assertFalse(known.iloc[:5].any())
        self.assertEqual((fresh.iloc[5], level.iloc[5]), (True, 110))

    def test_touch_is_not_break_and_close_back_below_the_pivot_still_counts(self):
        frame = bars([100, 102, 110, 108, 107, 110, 111])
        fresh, _, _ = swing_high_signals(frame)
        self.assertEqual(fresh.tolist(), [False] * 6 + [True])
        self.assertLess(frame["Close"].iloc[-1], 110)

    def test_equal_high_plateau_selects_the_last_peak_before_the_decline(self):
        fresh, level, _ = swing_high_signals(bars([100, 105, 110, 110, 108, 107, 111]))
        self.assertEqual(fresh.tolist(), [False] * 6 + [True])
        self.assertEqual(level.iloc[-1], 110)

    def test_the_reference_does_not_roll_off_after_a_fixed_number_of_days(self):
        fresh, level, _ = swing_high_signals(bars([100, 102, 110, 108, 107] + [100] * 70 + [111]))
        self.assertEqual(level.iloc[-1], 110)
        self.assertEqual(np.flatnonzero(fresh).tolist(), [75])

    def test_one_break_per_pivot_and_new_waves_need_no_ten_day_cooldown(self):
        fresh, _, _ = swing_high_signals(bars([100, 102, 110, 108, 107, 111, 109, 108, 112, 113, 114]))
        self.assertEqual(np.flatnonzero(fresh).tolist(), [5, 8])

    def test_higher_low_and_full_123_are_not_extra_filters(self):
        frame = bars([100, 102, 110, 108, 107, 111])
        frame.iloc[4, frame.columns.get_loc("Low")] = 80  # Lower than the initial low.
        self.assertTrue(swing_high_signals(frame)[0].iloc[-1])

    def test_missing_open_never_invents_a_gap_entry(self):
        frame = bars([100, 102, 110, 108, 107, 111]).drop(columns="Open")
        fresh, _, known = swing_high_signals(frame)
        self.assertFalse(fresh.any())
        self.assertFalse(known.any())

    def test_randomized_signal_and_reference_levels_match_exhaustive_oracle(self):
        for seed in range(8):
            rng = random.Random(seed)
            frame = bars([100 + rng.randrange(25) for _ in range(90)])
            expected, levels = slow_reference(frame)
            fresh, actual, _ = swing_high_signals(frame)
            self.assertEqual(fresh.tolist(), expected, f"seed={seed}")
            np.testing.assert_allclose(actual.to_numpy(), levels, equal_nan=True)
            for end in (15, 40, 75):
                prefix = swing_high_signals(frame.iloc[:end])
                self.assertEqual(prefix[0].tolist(), expected[:end])


class SwingEntryStatsTest(unittest.TestCase):
    def evaluate(self, frame):
        return bo.build_market({"X": frame}, "synthetic", "tw")["windows"]["1m"]["strategies"]["swingHigh"]

    def test_intraday_break_but_close_below_is_a_loss_not_a_cheap_close_entry(self):
        frame = bars([100, 102, 110, 108, 107, 112, 109, 109, 109],
                     closes=[100, 100, 100, 100, 100, 105, 108, 108, 108])
        stats = self.evaluate(frame)
        self.assertEqual((stats["n"], stats["maturedN"], stats["wins"]), (1, 1, 0))
        self.assertEqual(stats["avgReturn"], round((108 / 110 - 1) * 100, 2))

    def test_gap_above_pivot_uses_the_open_not_the_old_pivot(self):
        frame = bars([100, 102, 110, 108, 107, 118, 114, 114, 114],
                     closes=[100, 100, 100, 100, 100, 116, 112, 112, 112],
                     opens=[100, 100, 100, 100, 100, 115, 112, 112, 112])
        stats = self.evaluate(frame)
        self.assertEqual(stats["wins"], 0)
        self.assertEqual(stats["avgReturn"], round((112 / 115 - 1) * 100, 2))

    def test_distinct_waves_within_one_window_each_contribute_a_sample(self):
        stats = self.evaluate(bars([100, 102, 110, 108, 107, 111, 109, 108, 112, 109, 108, 107]))
        self.assertEqual((stats["eligible"], stats["n"], stats["maturedN"]), (1, 2, 2))

    def test_all_past_signals_and_returns_are_unchanged_by_future_bars(self):
        frame = bars([100, 102, 110, 108, 107, 111, 109, 108, 112, 109, 108, 107])
        cutoff = frame.index[-4]
        with_future = bo.build_market({"X": frame}, "synthetic", "tw", as_of=cutoff)
        truncated = bo.build_market({"X": frame.loc[:cutoff]}, "synthetic", "tw")
        self.assertEqual(with_future, truncated)


if __name__ == "__main__":
    unittest.main()

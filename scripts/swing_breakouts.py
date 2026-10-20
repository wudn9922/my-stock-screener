"""Causal swing-high breakout signals, distinct from a rolling N-session maximum.

A pivot is the last equal high on a plateau, at least as high as its left neighbours
and strictly higher than its right neighbours. A two-sided pivot is known only after
the right-hand candles close. It can therefore first be traded on the following bar.
Each confirmed pivot is consumed by its first strict intraday break; a new pivot
re-arms the signal without an arbitrary time cooldown or a required 1-2-3 pattern.
"""
import numpy as np
import pandas as pd

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
SWING_KEY = "swingHigh"


def swing_high_signals(bars, left=PIVOT_LEFT, right=PIVOT_RIGHT):
    if not isinstance(left, int) or not isinstance(right, int) or min(left, right) < 1:
        raise ValueError("Pivot confirmation requires positive integer left/right widths")
    high = bars["High"].to_numpy(dtype=float)
    opens = bars["Open"].to_numpy(dtype=float) if "Open" in bars else np.full(len(bars), np.nan)
    fresh = np.zeros(len(bars), dtype=bool)
    levels = np.full(len(bars), np.nan)
    known = (np.arange(len(bars)) >= left + right + 1) & np.isfinite(opens) & (opens > 0)
    reference = np.nan
    armed = False
    for i in range(len(bars)):
        # The confirmation candle is i-1; never use the current day's completed OHLC
        # to create a reference which supposedly existed at that day's open.
        pivot = i - right - 1
        if pivot >= left:
            price = high[pivot]
            if price >= np.max(high[pivot - left:pivot]) and price > np.max(high[pivot + 1:i]):
                reference = price
                armed = True
        levels[i] = reference
        if known[i] and armed and high[i] > reference:
            fresh[i] = True
            armed = False
    return tuple(pd.Series(values, index=bars.index) for values in (fresh, levels, known))

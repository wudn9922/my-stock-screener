"""Causal, volatility-sized price waves rather than small sideways local pivots.

A rising leg starts only after a close rebounds two ATR(20) from its low. Its
highest wick becomes a confirmed swing high only after a later close retreats
two ATR from that high. The distance is frozen at the extreme's ATR; small
countermoves cannot lower an existing breakout level. Confirmation takes effect
on the next bar. Daily OHLC cannot establish the order of an outside bar's wicks,
so an extreme and its reversal must occur on different bars.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from breakout_outcomes import GATE_ATR_PERIOD, qualifies_close

SWING_KEY = "swingHigh"
SWING_METHOD = "atr-reversal-v2"
ATR_PERIOD = 20
REVERSAL_ATR = 2.0


@dataclass(frozen=True)
class SwingHigh:
    pivot_index: int
    confirmed_index: int
    price: float
    reversal_distance: float


@dataclass(frozen=True)
class SwingHighTrace:
    fresh: pd.Series
    levels: pd.Series
    known: pd.Series
    # Actual extreme and confirmation used at each bar's OPEN, even much later.
    pivot_indices: pd.Series
    confirmed_indices: pd.Series
    atr: pd.Series
    highs: tuple


def wilder_atr(bars, period=ATR_PERIOD):
    """Use `period` true ranges with known previous closes, then Wilder smoothing."""
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("ATR period must be a positive integer")
    high, low, close = (bars[name].to_numpy(dtype=float) for name in ("High", "Low", "Close"))
    values = np.full(len(bars), np.nan)
    if len(bars) > period:
        ranges = np.maximum(high[1:] - low[1:], np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))
        values[period] = np.mean(ranges[:period])
        for i in range(period + 1, len(bars)):
            values[i] = ((period - 1) * values[i - 1] + ranges[i - 1]) / period
    return pd.Series(values, index=bars.index)


def swing_high_trace(bars, reversal_atr=REVERSAL_ATR, atr_period=ATR_PERIOD):
    if isinstance(reversal_atr, bool) or not np.isfinite(reversal_atr) or reversal_atr <= 0:
        raise ValueError("Swing reversal must be a positive finite ATR multiple")
    high, low, close = (bars[name].to_numpy(dtype=float) for name in ("High", "Low", "Close"))
    opens = bars["Open"].to_numpy(dtype=float) if "Open" in bars else np.full(len(bars), np.nan)
    atr = wilder_atr(bars, atr_period)
    gate_atr = wilder_atr(bars, GATE_ATR_PERIOD).to_numpy()
    volatility = atr.to_numpy()
    fresh = np.zeros(len(bars), dtype=bool)
    levels = np.full(len(bars), np.nan)
    known = np.zeros(len(bars), dtype=bool)
    pivot_indices = np.full(len(bars), -1, dtype=int)
    confirmed_indices = np.full(len(bars), -1, dtype=int)
    highs = []
    reference = None
    armed = False
    rising = False
    trough = peak = -1

    for i in range(len(bars)):
        # Only information known before this bar can set its stop-entry level.
        if reference is not None:
            levels[i] = reference.price
            pivot_indices[i] = reference.pivot_index
            confirmed_indices[i] = reference.confirmed_index
            known[i] = np.isfinite(opens[i]) and opens[i] > 0
            if known[i] and armed and high[i] > reference.price:
                fresh[i] = True
                # A failed close is an attempt, not invalidation of the key level.
                # Later days may break the same high again; consume only a qualified close.
                armed = not qualifies_close(close[i], reference.price, gate_atr[i])

        if not np.isfinite(volatility[i]) or volatility[i] <= 0:
            continue
        if not rising:
            if trough < 0 or low[i] <= low[trough]:
                trough = i
            if i > trough and close[i] - low[trough] >= reversal_atr * volatility[trough]:
                # Exclude the trough bar: its high may precede its low intraday.
                peak = trough + 1 + int(np.argmax(high[trough + 1:i + 1]))
                rising = True
        else:
            if high[i] >= high[peak]:
                peak = i
            distance = reversal_atr * volatility[peak]
            if i > peak and high[peak] - close[i] >= distance:
                reference = SwingHigh(peak, i, float(high[peak]), float(distance))
                highs.append(reference)
                armed = True
                rising = False
                trough = peak + 1 + int(np.argmin(low[peak + 1:i + 1]))

    def series(values):
        return pd.Series(values, index=bars.index)

    return SwingHighTrace(series(fresh), series(levels), series(known), series(pivot_indices),
                          series(confirmed_indices), atr, tuple(highs))


def swing_high_signals(bars, reversal_atr=REVERSAL_ATR, atr_period=ATR_PERIOD):
    trace = swing_high_trace(bars, reversal_atr, atr_period)
    return trace.fresh, trace.levels, trace.known

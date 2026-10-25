"""Read-only analysis views of a complete TW/US market's daily histories.

Sources sometimes append a flat zero-volume row for every stock on a closure.
Infer those dates from the whole cohort, never by rewinding one halted stock.
Raw caches and permanent archives are not modified.
"""
from __future__ import annotations

import pandas as pd

UNKNOWN_VOLUME_DATES = "atlas_unknown_volume_dates"


def non_trading_dates(frames):
    """Known weekends and broad, known-zero dates; missing volume is not zero.

    At least two independent histories and half the recent observed coverage must
    be present before inferring a weekday closure. A positive-volume observation
    or unknown source volume prevents that inference.
    """
    frames = {s: f for s, f in frames.items() if f is not None and not f.empty}
    if not frames:
        return frozenset()
    present, positive, unknown = {}, {}, {}
    for symbol, frame in frames.items():
        close = pd.to_numeric(frame["Close"], errors="coerce")
        volume = pd.to_numeric(frame["Volume"], errors="coerce")
        days = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
        missing = volume.isna() | (volume < 0)
        lineage = {str(day)[:10] for day in frame.attrs.get(UNKNOWN_VOLUME_DATES, ())}
        if lineage:
            missing |= days.strftime("%Y-%m-%d").isin(lineage)
        observed = close.notna() & (close > 0)
        present[symbol] = pd.Series(observed.to_numpy(), index=days).groupby(level=0).any()
        positive[symbol] = pd.Series((observed & (volume > 0)).to_numpy(), index=days).groupby(level=0).any()
        unknown[symbol] = pd.Series((observed & missing).to_numpy(), index=days).groupby(level=0).any()
    counts = pd.DataFrame(present).sum(axis=1).sort_index()
    active = pd.DataFrame(positive).sum(axis=1).reindex(counts.index, fill_value=0)
    missing = pd.DataFrame(unknown).sum(axis=1).reindex(counts.index, fill_value=0)
    reference = counts.rolling(20, min_periods=1).quantile(0.25)
    closed = (counts >= 2) & (counts >= reference * 0.5) & (active == 0) & (missing == 0)
    closed |= counts.index.dayofweek >= 5
    return frozenset(counts.index[closed].strftime("%Y-%m-%d"))


def analysis_prices(frame, excluded_dates):
    """Remove only cohort-confirmed non-trading dates, preserving unknown-volume lineage."""
    if frame is None or frame.empty or not excluded_dates:
        return frame
    days = pd.DatetimeIndex(frame.index).tz_localize(None).strftime("%Y-%m-%d")
    result = frame.loc[~days.isin(excluded_dates)].copy()
    result.attrs.update(frame.attrs)
    return result


def market_prices(frames):
    """Return projected market histories and the dates needed for custom-stock views."""
    excluded = non_trading_dates(frames)
    projected = {s: analysis_prices(f, excluded) for s, f in frames.items()}
    return {s: f for s, f in projected.items() if f is not None and not f.empty}, excluded

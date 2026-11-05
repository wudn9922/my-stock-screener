"""Versioned counting over the existing causal ATR wave detector, not a new detector."""
import numpy as np

from breakout_outcomes import GATE_ATR_PERIOD, evaluate_attempt, qualifies_close
from swing_breakouts import swing_high_trace, wilder_atr

COUNTING_MODEL = "first-higher-close-v1"


def swing_attempts(bars, calendar, trace=None):
    trace = trace or swing_high_trace(bars)
    previous = {high.pivot_index: trace.highs[i - 1].price if i else None
                for i, high in enumerate(trace.highs)}
    atr = wilder_atr(bars, GATE_ATR_PERIOD)
    positions = {day: i for i, day in enumerate(calendar)}
    seen = set()
    attempts = []
    for i in np.flatnonzero(trace.fresh.to_numpy()):
        day = bars.index[i]
        if day not in positions:
            continue
        pivot = int(trace.pivot_indices.iloc[i])
        level = float(trace.levels.iloc[i])
        prior = previous.get(pivot)
        first = pivot not in seen
        seen.add(pivot)
        qualified = bool(qualifies_close(bars.Close.iloc[i], level, atr.iloc[i]))
        attempts.append({"index": int(i), "date": day, "pivot_index": pivot,
                         "first": first, "higher": None if prior is None else level >= prior,
                         "qualified": qualified, "level": level,
                         "outcome": evaluate_attempt(bars, i, level, atr, calendar, positions)})
    return attempts


def included(attempt):
    return attempt["first"] and attempt["higher"] is True and attempt["qualified"]


def diagnostics(attempts):
    first = [a for a in attempts if a["first"]]
    known = [a for a in first if a["higher"] is not None]
    higher = [a for a in first if a["higher"] is True]
    share = lambda n, d: n / d if d else None
    return {"countingModel": COUNTING_MODEL,
            "first_attempts": len(higher),
            "retry_attempts": sum(not a["first"] for a in attempts),
            "retry_failures": sum(not a["first"] and not a["qualified"] for a in attempts),
            "lower_high_share": share(sum(a["higher"] is False for a in known), len(known)),
            "unknown_previous_high": sum(a["higher"] is None for a in first),
            "close_fail_share": share(sum(not a["qualified"] for a in higher), len(higher))}

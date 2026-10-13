"""
Synthetic FRED series for the US rates tests and the committed fixtures.

  python3 tests/rates_synth.py   # rewrites tests/fixtures/rates/sample-rates*.json

Deterministic (seeded). Everything is made up: a 10Y random walk, a curve slope that inverts from
2022-07-05 to 2024-09-04 (deepest -100bp in the middle) and a step-shaped Fed target range. Only the
series scripts/build_rates.py fetches are generated (DGS3MO, DGS10, DGS20, DGS30, DFEDTARU, DFEDTARL).
Bond-market holidays (Columbus Day, Veterans Day, New Year, July 4, Christmas) have no FRED value.
"""
import math
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "rates")
FIXTURE_NOTE = "synthetic test data (tests/rates_synth.py), not real market data"
START = date(2020, 6, 1)
END = date(2026, 10, 9)
TODAY = date(2026, 10, 9)
NOW = datetime(2026, 10, 9, 22, 30, tzinfo=timezone.utc)
INVERSION = (date(2022, 7, 5), date(2024, 9, 4))

# Tenor loading on the slope s: y_T = y10 + LOADING[T] * s (s is the 10Y minus 2Y spread of a curve that is not output).
LOADING = {"DGS3MO": -1.3, "DGS10": 0.0, "DGS20": 0.3, "DGS30": 0.4}


def holidays(start=START, end=END):
    """(weekdays, bond-market holidays)."""
    days, closed = [], set()
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
            if (cursor.month, cursor.day) in ((1, 1), (7, 4), (12, 25), (11, 11)):
                closed.add(cursor)
            # Columbus Day (2nd Monday of October)
            if cursor.month == 10 and cursor.weekday() == 0 and 8 <= cursor.day <= 14:
                closed.add(cursor)
        cursor += timedelta(days=1)
    return days, closed


def slope(day):
    """Slope in percentage points: +0.9 outside the inversion, a half sine down to -1.0 inside."""
    start, end = INVERSION
    if start <= day <= end:
        phase = (day - start).days / (end - start).days
        return -0.3 - 0.7 * math.sin(math.pi * phase)
    return 0.9


def fed_range(day):
    if day < date(2022, 3, 17):
        return 0.0, 0.25
    if day < date(2023, 7, 27):
        return 5.25, 5.5
    if day < date(2025, 9, 18):
        return 4.25, 4.5
    return 4.0, 4.25


def synthetic_market(seed=7):
    """{FRED id: [(date, value)]} on the synthetic calendar."""
    rng = random.Random(seed)
    days, closed = holidays()
    y10 = 0.70
    curves = []
    for index, day in enumerate(days):
        if index:
            y10 += rng.gauss(0.0012, 0.055)
        y10 = min(max(y10, 0.5), 5.2)
        s = slope(day)
        curves.append({key: max(y10 + loading * s, 0.02) for key, loading in LOADING.items()})

    series = {key: [] for key in list(LOADING) + ["DFEDTARU", "DFEDTARL"]}
    for day, curve in zip(days, curves):
        if day in closed:
            continue
        iso = day.isoformat()
        for key in LOADING:
            # a few isolated missing points ('.') to exercise the look-back
            if rng.random() < 0.004:
                continue
            series[key].append((iso, round(curve[key], 2)))
        lower, upper = fed_range(day)
        series["DFEDTARL"].append((iso, lower))
        series["DFEDTARU"].append((iso, upper))
    return series


def origins_all(series, origin="csv"):
    return {key: origin for key in series}


def build_fixture_payloads():
    import build_rates
    import rates_config

    series = synthetic_market()
    rates, history = build_rates.build_payloads(
        series, origins_all(series), rates_config.ETFS, TODAY, NOW
    )
    return {"_fixture": FIXTURE_NOTE, **rates}, {"_fixture": FIXTURE_NOTE, **history}


def main():
    import build_rates

    rates, history = build_fixture_payloads()
    for name, payload in (("sample-rates.json", rates), ("sample-rates-history.json", history)):
        path = os.path.join(FIXTURE_DIR, name)
        size = build_rates.write_atomic(path, payload)
        print(f"wrote {path} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

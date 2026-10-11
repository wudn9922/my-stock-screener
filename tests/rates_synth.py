"""
Synthetic FRED series and ETF prices for the US rates tests and the committed fixtures.

  python3 tests/rates_synth.py   # rewrites tests/fixtures/rates/sample-rates*.json

Deterministic (seeded). Everything is made up: a 10Y random walk, a curve slope that inverts from
2022-07-05 to 2024-09-04 (deepest -100bp in the middle), TIPS = nominal minus a breakeven walk, a
step-shaped Fed target range and five ETFs whose daily returns are -D·Δy/100 plus carry and noise.
Bond-market-only holidays (Columbus Day, Veterans Day) have no FRED value but ETF prices, like real data.
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

# Tenor loading on the 10Y-2Y slope s: y_T = y10 + LOADING[T] * s (so DGS10 - DGS2 = s).
LOADING = {
    "DGS1MO": -1.4, "DGS3MO": -1.3, "DGS6MO": -1.2, "DGS1": -1.1, "DGS2": -1.0, "DGS3": -0.8,
    "DGS5": -0.5, "DGS7": -0.25, "DGS10": 0.0, "DGS20": 0.3, "DGS30": 0.4,
}
REAL = {"DFII5": "DGS5", "DFII7": "DGS7", "DFII10": "DGS10", "DFII20": "DGS20", "DFII30": "DGS30"}
# Synthetic durations used to generate prices (not the static config values).
ETF_DURATION = {"TLT": 16.5, "EDV": 24.0, "GOVZ": 27.0, "ZROZ": 27.0}
ETF_START = {"GOVZ": date(2020, 9, 22)}


def calendar_days(start=START, end=END):
    """(weekdays, stock-closed days, bond-only-closed days)."""
    days, stock_closed, bond_closed = [], set(), set()
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
            if (cursor.month, cursor.day) in ((1, 1), (7, 4), (12, 25)):
                stock_closed.add(cursor)
            # Columbus Day (2nd Monday of October) and Veterans Day: bond market closed, stocks open
            if cursor.month == 10 and cursor.weekday() == 0 and 8 <= cursor.day <= 14:
                bond_closed.add(cursor)
            if (cursor.month, cursor.day) == (11, 11):
                bond_closed.add(cursor)
        cursor += timedelta(days=1)
    return days, stock_closed, bond_closed


def slope(day):
    """10Y-2Y in percentage points: +0.9 outside the inversion, a half sine down to -1.0 inside."""
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
    """({FRED id: [(date, value)]}, {symbol: [(date, close, adjClose)]}) on the synthetic calendar."""
    rng = random.Random(seed)
    days, stock_closed, bond_closed = calendar_days()
    y10 = 0.70
    breakeven = 1.6
    true_curve = []
    for index, day in enumerate(days):
        if index:
            y10 += rng.gauss(0.0012, 0.055)
            breakeven += rng.gauss(0.0004, 0.02)
        y10 = min(max(y10, 0.5), 5.2)
        breakeven = min(max(breakeven, 1.4), 3.0)
        s = slope(day)
        curve = {key: max(y10 + loading * s, 0.02) for key, loading in LOADING.items()}
        curve["breakeven"] = breakeven
        true_curve.append(curve)

    series = {key: [] for key in list(LOADING) + list(REAL) + ["T5YIE", "T10YIE", "T5YIFR", "DFEDTARU", "DFEDTARL"]}
    for day, curve in zip(days, true_curve):
        if day in bond_closed or day in stock_closed:
            continue
        iso = day.isoformat()
        for key in LOADING:
            # a few isolated missing points ('.') to exercise the look-back
            if rng.random() < 0.004:
                continue
            series[key].append((iso, round(curve[key], 2)))
        for real, nominal in REAL.items():
            if real == "DFII30" and date(2021, 3, 1) <= day <= date(2021, 4, 15):
                continue  # a gap, like the real DFII30 history
            series[real].append((iso, round(curve[nominal] - curve["breakeven"] - 0.05, 2)))
        series["T5YIE"].append((iso, round(curve["breakeven"] - 0.1, 2)))
        series["T10YIE"].append((iso, round(curve["breakeven"], 2)))
        series["T5YIFR"].append((iso, round(curve["breakeven"] + 0.1, 2)))
        lower, upper = fed_range(day)
        series["DFEDTARL"].append((iso, lower))
        series["DFEDTARU"].append((iso, upper))

    prices = {symbol: [] for symbol in ("TLT", "EDV", "GOVZ", "ZROZ", "TMF")}
    level = {symbol: 100.0 for symbol in prices}
    previous = None
    for day, curve in zip(days, true_curve):
        if day in stock_closed:
            continue
        if previous is not None:
            dy20 = curve["DGS20"] - previous["DGS20"]
            dy30 = curve["DGS30"] - previous["DGS30"]
            common = rng.gauss(0, 0.0008)
            tlt_r = -ETF_DURATION["TLT"] * dy20 / 100 + curve["DGS20"] / 100 / 252 + common
            returns = {
                "TLT": tlt_r,
                "EDV": -ETF_DURATION["EDV"] * (dy20 + dy30) / 200 + curve["DGS20"] / 100 / 252 + rng.gauss(0, 0.0008),
                "GOVZ": -ETF_DURATION["GOVZ"] * dy30 / 100 + curve["DGS30"] / 100 / 252 + rng.gauss(0, 0.0008),
                "ZROZ": -ETF_DURATION["ZROZ"] * dy30 / 100 + curve["DGS30"] / 100 / 252 + rng.gauss(0, 0.0008),
                "TMF": 3 * tlt_r - 2 * curve["DGS3MO"] / 100 / 252 - 0.0101 / 252,
            }
            for symbol, ret in returns.items():
                level[symbol] *= 1 + ret
        previous = curve
        for symbol in prices:
            if day < ETF_START.get(symbol, START):
                continue
            adj = round(level[symbol], 4)
            # Close above Adj Close (distributions not yet added back), converging at the end
            close = round(adj * (1 + 0.035 * (END - day).days / 365.25), 4)
            prices[symbol].append((day.isoformat(), close, adj))
    return series, prices


def origins_all(series, origin="csv"):
    return {key: origin for key in series}


def build_fixture_payloads():
    import build_rates
    import rates_config

    series, prices = synthetic_market()
    rates, history = build_rates.build_payloads(
        series, origins_all(series), prices, rates_config.ETFS, None, None, TODAY, NOW
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

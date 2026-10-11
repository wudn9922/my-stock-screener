"""
Synthetic daily prices for the theme rotation tests and the atlas fixture.

  python3 tests/themes_synth.py   # rewrites atlas/src/report/fixtures/themes.sample.json

Deterministic (seeded). A market factor, one factor per theme and idiosyncratic noise give themes
different trends; a few tickers are missing, stale or newly listed so every payload branch shows up.
"""
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# Approximate six-month drift (percent) per theme, to spread the heat map.
THEME_DRIFT = {
    "ai-chips": 55, "memory": 95, "semi-equip": 40, "foundry": 28, "analog-auto": -4,
    "optical": 80, "ai-servers": 35, "power-grid": 45, "nuclear": 30,
    "megacap": 14, "cloud-saas": -16, "cyber": 6, "ai-software": 22, "internet-media": 3, "ecommerce": -2,
    "crypto": -22, "fintech": 5,
    "space": 48, "defense": 18, "drones-evtol": 26, "robotics": 12, "quantum": 62,
    "ev-battery": -12, "solar": 33, "gold-miners": 52, "critical-minerals": 38,
    "biotech": 20, "glp1": -8, "medtech": -6, "china-adr": 9,
}
SPECULATIVE = {"nuclear", "space", "quantum", "crypto", "drones-evtol", "ai-software", "ev-battery", "solar"}


def business_days(end, count):
    days = []
    cursor = date.fromisoformat(end)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return list(reversed(days))


def synthetic_prices(config, end="2026-10-09", bars=252, seed=22, drop=(), stale=(), new_listings=None):
    """{symbol: [(date, close)]}. `drop` symbols are absent, `stale` end 6 days early,
    `new_listings` maps symbol -> number of bars."""
    rng = random.Random(seed)
    calendar = business_days(end, bars)
    market = [rng.gauss(0.0004, 0.009) for _ in calendar]
    theme_factor = {}
    for theme in config["themes"]:
        daily_drift = (1 + THEME_DRIFT.get(theme["key"], 0) * 0.5 / 100) ** (1 / 126) - 1
        vol = 0.016 if theme["key"] in SPECULATIVE else 0.008
        theme_factor[theme["key"]] = [rng.gauss(daily_drift, vol) for _ in calendar]
    owner = {}
    etfs = {}
    for theme in config["themes"]:
        for symbol, _name, _note in theme["tickers"]:
            owner.setdefault(symbol, theme["key"])
        if theme.get("etf"):
            etfs[theme["etf"]] = theme["key"]

    def path(symbol, factor, beta, idio):
        price = rng.uniform(25, 420)
        out = []
        for index, day in enumerate(calendar):
            change = beta * market[index] + (factor[index] if factor else 0.0) + rng.gauss(0, idio)
            price = max(1.0, price * (1 + change))
            out.append((day, round(price, 2)))
        return out

    prices = {}
    level = 450.0
    spy = []
    for index, day in enumerate(calendar):
        level *= 1 + market[index]
        spy.append((day, round(level, 2)))
    prices[config["benchmark"]] = spy
    for symbol, key in sorted(owner.items()):
        if symbol in drop:
            continue
        sigma = 0.02 if key in SPECULATIVE else 0.011
        series = path(symbol, theme_factor[key], rng.uniform(0.8, 1.5), sigma)
        if symbol in (new_listings or {}):
            series = series[-new_listings[symbol]:]
        if symbol in stale:
            series = series[:-4]
        prices[symbol] = series
    for etf, key in sorted(etfs.items()):
        prices[etf] = path(etf, theme_factor[key], 1.0, 0.004)
    return prices


def main():
    import build_themes

    config = build_themes.default_config()
    drop = {"CRCL", "HBM", "MP", "USAR", "UUUU", "LAC", "GLXY"}
    prices = synthetic_prices(
        config,
        drop=drop,
        stale={"AAOI"},
        new_listings={"FLY": 60, "SNDK": 180, "ALAB": 200},
    )
    payload = build_themes.build_payload(prices, config, datetime(2026, 10, 9, 22, 30, tzinfo=timezone.utc))
    out = os.path.join(ROOT, "atlas", "src", "report", "fixtures", "themes.sample.json")
    size = build_themes.write_atomic(out, payload)
    print(f"wrote {out} ({size} bytes)")


if __name__ == "__main__":
    main()

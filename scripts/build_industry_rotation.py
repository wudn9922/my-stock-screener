#!/usr/bin/env python3
"""Read-only industry comparisons from universe + rolling daily bars; no price writes."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import statistics
import re
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_data_status import calendar_for
from build_screener_history import read, write
from universe_cache import read_bars, bars_path, UNKNOWN_VOLUME_DATES

# Single source for the per-market liquidity floor (shared with market warnings).
PARAMS = json.loads((Path(__file__).parent / "market_warnings_params.json").read_text())
MIN_MEMBERS = 3

WINDOWS = {"d1": (1, "changePct"), "w1": (5, "r5"), "m1": (21, "r21"), "m3": (63, "r63")}


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def liquid(row, min_amount, min_price):
    return finite(row.get("turnover20")) and finite(row.get("close")) and row["turnover20"] >= min_amount and row["close"] >= min_price


def cap_weighted(valid):
    """Market-cap weighted mean of returns; None unless at least half of the valid members have a usable cap."""
    capped = [(r["return"], r["marketCap"]) for r in valid if finite(r.get("marketCap")) and r["marketCap"] > 0]
    if not capped or len(capped)*2 < len(valid):
        return None
    return sum(ret*cap for ret, cap in capped) / sum(cap for _, cap in capped)


def summarize(rows, window, dates, min_amount=None, min_price=None, min_members=MIN_MEMBERS):
    """Group statistics over liquid members only.

    With min_amount/min_price set, members lacking turnover20/close or below either floor are excluded
    from every statistic (count still reports all members). Fewer than min_members liquid members with a
    valid return -> every statistic is None and the group is unranked.
    """
    members = rows if min_amount is None and min_price is None else [
        r for r in rows if liquid(r, min_amount or 0, min_price or 0)]
    valid = [r for r in members if finite(r.get("return"))]
    base = {"count": len(rows), "liquidCount": len(members), "validCount": len(valid)}
    if len(valid) < min_members:
        return {**base, "returnPct": None, "medianReturnPct": None, "capWeightedReturnPct": None, "upShare": None,
                "volumeRatio": None, "volumeCount": 0, "pairedCount": 0, "previousReturnPct": None, "momentumChange": None}
    volume = []
    paired = []
    n = window
    for row in members:
        frame = row.get("frame")
        if frame is None or frame.empty:
            continue
        close = frame["Close"].reindex(pd.to_datetime(dates))
        if len(close) == 2*n+1 and close.notna().all() and (close > 0).all():
            paired.append(((close.iloc[-1] / close.iloc[-1-n] - 1)*100, (close.iloc[-1-n] / close.iloc[0] - 1)*100))
        segment = frame["Volume"].reindex(pd.to_datetime(dates[-2*n:]))
        unknown = {str(d)[:10] for d in frame.attrs.get(UNKNOWN_VOLUME_DATES, ())}
        if len(segment) == 2*n and segment.notna().all() and (segment >= 0).all() and not unknown.intersection(dates[-2*n:]):
            before, after = float(segment.iloc[:n].sum()), float(segment.iloc[n:].sum())
            if before > 0:
                volume.append((before, after))
    average = lambda values: sum(values)/len(values) if values else None
    returns = [r["return"] for r in valid]
    return {**base, "returnPct": average(returns), "medianReturnPct": statistics.median(returns),
            "capWeightedReturnPct": cap_weighted(valid),
            "upShare": sum(x > 0 for x in returns)/len(returns)*100,
            "volumeRatio": sum(v[1] for v in volume)/sum(v[0] for v in volume) if volume else None,
            "volumeCount": len(volume), "pairedCount": len(paired),
            "previousReturnPct": average([v[1] for v in paired]),
            "momentumChange": average([v[0]-v[1] for v in paired])}


def market_method(market, min_members=MIN_MEMBERS):
    limits = PARAMS["markets"][market.lower()]
    return {"rankBy": "median", "minAmount": limits["minAmount"], "minPrice": limits["minPrice"], "minMembers": min_members}


def build(root=ROOT, cache=None, now=None):
    root = Path(root)
    cache = cache or root / ".cache/universe-full"
    universe = read(root / "docs/report/universe.json", {})
    fields = universe.get("fields", [])
    markets = {}
    for market, source in universe.get("markets", {}).items():
        if market not in ("TW", "US") or not source.get("asOf"):
            continue
        as_of = source["asOf"]
        frames = {}
        records = []
        for row in source.get("rows", []):
            item = dict(zip(fields, row))
            symbol = item.get("symbol")
            if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=-]{0,31}", symbol):
                continue
            if item.get("asOf") != as_of:
                continue
            group = (item.get("industry") or item.get("sector")) if market == "TW" else item.get("sector")
            if not isinstance(group, str) or not group.strip():
                group = "未分類"
            frames[symbol] = read_bars(bars_path(cache, symbol))
            records.append((group, item, frames[symbol]))
        method = market_method(market)
        cal = calendar_for("XTAI" if market == "TW" else "XNYS", int(as_of[:4]))
        end = pd.Timestamp(as_of)
        if not cal.is_session(end):
            continue
        periods = {}
        for key, (window, field) in WINDOWS.items():
            # sessions_window's count semantics changed across supported 4.x
            # releases. Request an explicit range, then take exactly 2N+1 closes.
            dates = cal.sessions_in_range(end - pd.Timedelta(days=500), end).strftime("%Y-%m-%d").tolist()[-(2*window+1):]
            groups = defaultdict(list)
            for group, item, frame in records:
                groups[group].append({"return": item.get(field), "frame": frame, "close": item.get("close"),
                                      "turnover20": item.get("turnover20"), "marketCap": item.get("marketCap")})
            rows = [{"name": name, **summarize(group, window, dates, method["minAmount"], method["minPrice"], method["minMembers"])} for name, group in groups.items()]
            rows.sort(key=lambda row: (row["medianReturnPct"] is None, -(row["medianReturnPct"] or 0), row["name"]))
            for rank, row in enumerate(rows, 1):
                row["rank"] = rank if row["medianReturnPct"] is not None else None
            periods[key] = {"startDate": dates[-1-window], "endDate": as_of, "volumePreviousStart": dates[-2*window], "rows": rows}
        markets[market] = {"asOf": as_of, "scope": source.get("scope", "liquid"), "method": method, "periods": periods}
    return {"version": 1, "generatedAt": now or datetime.now(timezone.utc).isoformat(), "markets": markets}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    result = build(args.root, args.cache_dir)
    write(args.root / "docs/report/industry-rotation.json", result)
    print("Industry rotation markets: " + ", ".join(result["markets"]))

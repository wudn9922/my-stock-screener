#!/usr/bin/env python3
"""Capture public screener inputs by observed session, without rewriting any prior snapshot.

These are collection-time snapshots, not a reconstruction of information known at
the historical close. No source/price/fundamental/archive files are written.
"""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path, fallback=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return fallback


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def capture(root=ROOT, now=None):
    root = Path(root)
    folder = root / "docs/report/screener-history"
    universe = read(root / "docs/report/universe.json", {})
    report = read(root / "docs/report/latest.json", {})
    growth = read(root / "docs/report/growth.json")
    now = now or datetime.now(timezone.utc).isoformat()
    count = 0
    for market in ("TW", "US"):
        source = universe.get("markets", {}).get(market)
        if not isinstance(source, dict) or not isinstance(source.get("rows"), list) or not source["rows"]:
            continue
        day = source.get("asOf", "")
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        datetime.strptime(day, "%Y-%m-%d")
        path = folder / f"{market}-{day}.json"
        if path.exists():
            # First observed snapshot wins. A later job cannot change the past.
            continue
        stock_growth = growth
        if isinstance(growth, dict) and isinstance(growth.get("rows"), list):
            fields = growth.get("fields", [])
            index = fields.index("symbol") if "symbol" in fields else 0
            symbols = {str(row[0]).replace(".", "-") for row in source["rows"] if isinstance(row, list) and row}
            stock_growth = {**growth, "rows": [r for r in growth["rows"] if isinstance(r, list) and len(r) > index and str(r[index]).replace(".", "-") in symbols]}
        write(path, {"version": 1, "market": market, "dataDate": day, "capturedAt": now,
                     "universe": {"version": 1, "generatedAt": universe.get("generatedAt"), "fields": universe.get("fields"), "markets": {market: source}},
                     "report": {"version": 1, "groups": [g for g in report.get("groups", []) if g.get("market") == market]},
                     "growth": stock_growth,
                     "usGrowth": read(root / "docs/atlas/valuation/us-growth.json") if market == "US" else None,
                     "valuation": read(root / f"docs/atlas/valuation/{market.lower()}.json")})
        count += 1
    # Rebuild from the owned files, retaining every previous date; no deletions.
    dates = {market: sorted(p.stem.removeprefix(market + "-") for p in folder.glob(f"{market}-*.json") if re.fullmatch(market + r"-\d{4}-\d{2}-\d{2}", p.stem)) for market in ("TW", "US")}
    if any(dates.values()):
        write(folder / "index.json", {"version": 1, "generatedAt": now, "markets": dates})
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(f"Screener snapshots captured: {capture(args.root)}")

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

if __package__:
    from .output_guard import OutputRejected, require, retain_keys, unique_keys
else:
    from output_guard import OutputRejected, require, retain_keys, unique_keys

ROOT = Path(__file__).resolve().parents[1]
# Derived snapshots, not permanent records: keep the latest market days per market so the
# Pages site stays small (each day is ~1.5 MB per market). Comparisons need at least two.
KEEP_DATES = 10


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


def snapshot_dates(folder, market):
    return sorted(p.stem.removeprefix(market + "-") for p in folder.glob(f"{market}-*.json")
                  if re.fullmatch(market + r"-\d{4}-\d{2}-\d{2}", p.stem))


def symbols_for(source, fields, label):
    require(isinstance(fields, list) and fields.count("symbol") == 1, label, "missing or duplicate symbol field")
    index = fields.index("symbol")
    rows = source.get("rows") if isinstance(source, dict) else None
    require(isinstance(rows, list), label, "missing rows")
    require(all(isinstance(row, list) and len(row) > index for row in rows),
            label, f"invalid row schema ({len(rows)} rows)")
    return unique_keys((row[index] for row in rows), label)


def validate_snapshot(folder, market, day, source, fields):
    label = f"{market} screener-history {day}"
    current = symbols_for(source, fields, label)
    require(bool(current), label, "0 usable symbols")
    days = snapshot_dates(folder, market)
    if not days:
        return
    previous_day = days[-1]
    require(day > previous_day, label, f"date regressed from {previous_day} ({len(current)} symbols)")
    previous = read(folder / f"{market}-{previous_day}.json")
    require(isinstance(previous, dict) and previous.get("market") == market
            and previous.get("dataDate") == previous_day, label,
            f"invalid previous snapshot {previous_day} ({len(current)} current symbols)")
    universe = previous.get("universe")
    require(isinstance(universe, dict) and isinstance(universe.get("markets"), dict),
            label, f"missing previous universe {previous_day} ({len(current)} current symbols)")
    prior = symbols_for(universe["markets"].get(market), universe.get("fields"),
                        f"{label} previous {previous_day}")
    require(bool(prior), label, f"0 usable previous symbols on {previous_day}")
    if len(prior) >= 100:
        retain_keys(current, prior, label, min_share=.85)


def capture(root=ROOT, now=None):
    root = Path(root)
    folder = root / "docs/report/screener-history"
    universe = read(root / "docs/report/universe.json", {})
    report = read(root / "docs/report/latest.json", {})
    growth = read(root / "docs/report/growth.json")
    now = now or datetime.now(timezone.utc).isoformat()
    candidates = []
    # Preflight every new market date before any snapshot/index write or pruning.
    for market in ("TW", "US"):
        source = universe.get("markets", {}).get(market)
        if source is None:
            # No new market observation: preserve the existing session snapshots.
            continue
        require(isinstance(source, dict), f"{market} screener-history", "invalid market source")
        day = source.get("asOf", "")
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            require(False, f"{market} screener-history {day}", "invalid observation date")
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            require(False, f"{market} screener-history {day}", "invalid observation date")
        path = folder / f"{market}-{day}.json"
        if path.exists():
            # First observed snapshot wins. A later job cannot change the past.
            continue
        validate_snapshot(folder, market, day, source, universe.get("fields"))
        candidates.append((market, day, path, source))
    count = 0
    for market, day, path, source in candidates:
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
    # Rebuild from the owned files. A kept snapshot is never rewritten; only dates older than
    # the latest KEEP_DATES per market are removed.
    dates = {}
    for market in ("TW", "US"):
        kept = snapshot_dates(folder, market)
        for old in kept[:-KEEP_DATES]:
            (folder / f"{market}-{old}.json").unlink()
        dates[market] = kept[-KEEP_DATES:]
    if any(dates.values()):
        write(folder / "index.json", {"version": 1, "generatedAt": now, "markets": dates})
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        print(f"Screener snapshots captured: {capture(args.root)}")
    except OutputRejected as exc:
        print(f"⚠️ {exc}")
        raise SystemExit(1)

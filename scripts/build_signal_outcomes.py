#!/usr/bin/env python3
"""Immutable public signal cohorts and split-consistent forward returns, offline."""
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from scripts.output_guard import require, finite, unique_keys
from scripts.market_sessions import exchange_sessions, verified_closures, read_policy
from scripts.kline_archive import symbol_rows, market_of, LOG
import kline_integrity as ki
from scripts.build_screener_history import write

MARKETS = {"TW": "^TWII", "US": "^GSPC"}
TYPES = ("ambush-v1", "daily-scan-v1")
HORIZONS = (1, 5, 20)
MIN_SAMPLE = 20
NAME = re.compile(r"(TW|US)-(\d{4}-\d{2}-\d{2})-(ambush-v1|daily-scan-v1)\.json")
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.^=-]{0,31}")
LABEL = "signal outcomes"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def day(value):
    require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value, LABEL, "invalid day")
    return date.fromisoformat(value)


def valid_symbol(symbol, market):
    return isinstance(symbol, str) and SYMBOL.fullmatch(symbol) and market_of(symbol) == market.lower()


def positive(value):
    return finite(value) and value > 0


def snapshot_cohorts(snapshot, market, stamp):
    """Atlas preset uses full-market metrics. Daily scan uses actual observed public group."""
    require(snapshot["version"] == 1 and snapshot["market"] == market and snapshot["dataDate"] == stamp,
            LABEL, "snapshot identity mismatch")
    source = snapshot["universe"]["markets"][market]
    require(source["asOf"] == stamp, LABEL, "snapshot universe date mismatch")
    if source.get("scope") != "full":
        return {}  # liquid fallback is not full market
    fields = snapshot["universe"]["fields"]
    required = {"symbol", "close", "asOf", "ma20", "ma20Slope5"}
    if market == "TW":
        required.add("volumeAvg20")
    require(required <= set(fields), LABEL, "snapshot missing preset columns")
    rows = [dict(zip(fields, row)) for row in source["rows"]]
    require(bool(rows), LABEL, "empty full universe")
    unique_keys((row.get("symbol") for row in rows), LABEL)
    current = {row["symbol"]: row for row in rows if row.get("asOf") == stamp
               and positive(row.get("close")) and valid_symbol(row["symbol"], market)}
    usable = [row for row in current.values() if finite(row.get("ma20")) and finite(row.get("ma20Slope5"))
              and (market != "TW" or finite(row.get("volumeAvg20")))]
    # Newly listed and suspended stocks can lack a full 20-session window.
    require(len(usable) >= .5 * len(rows), LABEL, "preset input coverage below 50%")
    ambush = [(row["symbol"], row["close"]) for row in usable
              if -2 - 1e-9 <= row["ma20"] <= 1 + 1e-9 and row["ma20Slope5"] > 0
              and (market != "TW" or row["volumeAvg20"] >= 1_000_000)]
    result = {"ambush-v1": sorted(ambush)}
    groups = [g for g in snapshot["report"]["groups"]
              if g.get("key") == market.lower() + "_all" and g.get("market") == market and g.get("kind") == "scan"]
    require(len(groups) <= 1, LABEL, "duplicate public scan group")
    if groups:
        group = groups[0]
        items = group["items"]
        require(isinstance(items, list) and group.get("count") == len(items), LABEL, "scan count mismatch")
        # Old snapshots omit reportDate. A group with no matching row is not
        # evidence of a zero-signal session; do not fabricate a cohort.
        matching = [item for item in items if item.get("asOf") == stamp]
        if matching:
            unique_keys((item.get("symbol") for item in matching), LABEL)
            require(all(valid_symbol(item.get("symbol"), market) and positive(item.get("close"))
                        for item in matching), LABEL, "invalid scan entry")
            result["daily-scan-v1"] = sorted((item["symbol"], item["close"]) for item in matching)
    return result


def capture(root):
    report = root / "docs/report"
    history = report / "screener-history"
    ledger = report / "signal-ledger"
    index = load(history / "index.json")
    require(index["version"] == 1, LABEL, "history index version")
    staged = []
    for market in MARKETS:
        for stamp in sorted(unique_keys(index["markets"][market], LABEL)):
            day(stamp)
            if stamp not in exchange_sessions(market.lower(), stamp, stamp):
                continue
            if stamp in verified_closures(market.lower()):
                continue
            paths = {kind: ledger / f"{market}-{stamp}-{kind}.json" for kind in TYPES}
            if all(p.exists() for p in paths.values()):
                continue
            cohorts = snapshot_cohorts(load(history / f"{market}-{stamp}.json"), market, stamp)
            for kind, rows in cohorts.items():
                if not paths[kind].exists():
                    staged.append((paths[kind], {"version": 1, "market": market, "signalType": kind,
                                                "triggerDate": stamp, "rows": rows}))
    for path, value in staged:
        write(path, value)
    return len(staged)


def read_ledger(folder, prior):
    paths = sorted(folder.glob("*.json"))
    names = {p.name for p in paths}
    require(set(prior.get("ledgerFiles", [])) <= names, LABEL, "previous signal shard missing")
    require(bool(paths), LABEL, "no signal records")
    cohorts = defaultdict(list)
    for path in paths:
        match = NAME.fullmatch(path.name)
        require(match is not None, LABEL, f"unexpected signal shard {path.name}")
        market, stamp, kind = match.groups()
        day(stamp)
        doc = load(path)
        require(doc["version"] == 1 and (doc["market"], doc["triggerDate"], doc["signalType"]) ==
                (market, stamp, kind), LABEL, "signal shard identity mismatch")
        rows = doc["rows"]
        require(isinstance(rows, list), LABEL, "invalid signal rows")
        unique_keys((row[0] for row in rows), LABEL)
        for symbol, entry in rows:
            require(valid_symbol(symbol, market) and positive(entry), LABEL, "invalid signal price/symbol")
        cohorts[market, kind].append((stamp, rows))
    return cohorts, sorted(names)


def archive_reader(archive, start, end):
    """One coherent archive revision for all dates, including the trigger close."""
    journal = archive / LOG
    require(journal.is_file(), LABEL, "archive quarantine journal missing")
    blocked = defaultdict(set)
    for row in ki.read_log(journal)["quarantined"]:
        blocked[row["symbol"]].add(row["date"])
    years = {str(y) for y in range(start.year, end.year + 1)}
    cache = {}

    def bars(symbol):
        if symbol not in cache:
            rows = symbol_rows(archive, symbol, years=years)
            cache[symbol] = {row[0]: tuple(float(v) for v in row[1:5]) for row in rows
                             if row[0] not in blocked[symbol] and row[5] != "0"}
        return cache[symbol]
    return bars


def evaluate(stamp, symbol, dates, through, bars, benchmark):
    """Mature only at the exact exchange session; never jump over a missing bar."""
    future = dates[dates.index(stamp) + 1:][:20]
    due = [d for d in future if d <= through]
    if not due:
        return {}
    stock, index = bars(symbol), bars(benchmark)
    needed = [stamp, *due]
    require(all(d in stock for d in needed), LABEL, f"{symbol} missing/quarantined bars for {stamp}")
    require(all(d in index for d in needed), LABEL, f"{benchmark} missing bars for {stamp}")
    base, ref = stock[stamp][3], index[stamp][3]
    result = {}
    for n in HORIZONS:
        if len(due) >= n:
            target = due[n - 1]
            result[n] = ((stock[target][3] / base - 1) * 100, (index[target][3] / ref - 1) * 100)
    if 20 in result:
        # Include entry price in extrema, so favourable >=0 and adverse <=0.
        result["mfe"] = max(0., max(stock[d][1] / base - 1 for d in due[:20]) * 100)
        result["mae"] = min(0., min(stock[d][2] / base - 1 for d in due[:20]) * 100)
    return result


def summarize(records):
    total = len(records)
    horizons = {}
    for n in HORIZONS:
        pairs = [r[n] for r in records if n in r]
        values = [p[0] for p in pairs]
        benchmarks = [p[1] for p in pairs]
        count = len(values)
        horizons[str(n)] = {"mature": count, "pending": total - count,
                            "mean": statistics.mean(values) if count else None,
                            "median": statistics.median(values) if count else None,
                            "winRate": 100 * sum(v > 0 for v in values) / count if count >= MIN_SAMPLE else None,
                            "benchmarkMean": statistics.mean(benchmarks) if count else None,
                            "insufficient": count < MIN_SAMPLE}
    complete = [r for r in records if 20 in r]
    return {"samples": total, "horizons": horizons,
            "mfe": statistics.mean(r["mfe"] for r in complete) if complete else None,
            "mae": statistics.mean(r["mae"] for r in complete) if complete else None}


def months_before(value, months):
    # Calendar-month rolling cohort, not a fixed number of trading days.
    from calendar import monthrange
    number = value.year * 12 + value.month - 1 - months
    year, month = divmod(number, 12)
    return date(year, month + 1, min(value.day, monthrange(year, month + 1)[1]))


def build(root, archive):
    report = root / "docs/report"
    path = report / "signal-outcomes.json"
    previous = load(path) if path.exists() else {}
    cohorts, names = read_ledger(report / "signal-ledger", previous)
    latest = max(day(stamp) for rows in cohorts.values() for stamp, _ in rows)
    start = min(day(stamp) for rows in cohorts.values() for stamp, _ in rows)
    # Limit archive reads to the rolling six-month report plus future horizon.
    earliest = max(start, months_before(latest, 6) - timedelta(days=14))
    end = datetime.now(timezone.utc).date() + timedelta(days=40)
    bars = archive_reader(archive, earliest, end)
    policy = read_policy(report / "market-closures.json")
    markets = {}
    for market, benchmark in MARKETS.items():
        stamps = [stamp for (m, _), rows in cohorts.items() if m == market for stamp, _ in rows]
        if not stamps:
            continue
        closed = set(verified_closures(market.lower())) | set(policy.get(market.lower(), {}))
        dates = sorted(exchange_sessions(market.lower(), earliest.isoformat(), end.isoformat()) - closed)
        index = bars(benchmark)
        available = [d for d in dates if d in index]
        require(bool(available), LABEL, f"{market} benchmark missing")
        through = available[-1]
        old_through = previous.get("markets", {}).get(market, {}).get("observedThrough")
        require(not old_through or through >= old_through, LABEL, f"{market} archive date regressed")
        # Archive publishing follows the full-market build. One session of lag
        # is normal; larger lag means the report cannot safely claim freshness.
        latest_signal = max(stamps)
        require(latest_signal in dates and sum(through < d <= latest_signal for d in dates) <= 1,
                LABEL, f"{market} archive behind observed signals")
        anchor = max(latest_signal, through)
        require(anchor in dates, LABEL, "non-session report anchor")
        cutoffs = {str(m): months_before(day(anchor), m).isoformat() for m in (1, 3, 6)}
        types = {}
        for kind in TYPES:
            batches = cohorts.get((market, kind), [])
            if not batches:
                continue
            results = []
            for stamp, rows in batches:
                if stamp < cutoffs["6"]:
                    continue
                require(stamp in dates, LABEL, "signal date is not a market session")
                for symbol, _observed_close in rows:
                    results.append((stamp, evaluate(stamp, symbol, dates, through, bars, benchmark)))
            types[kind] = {"startDate": batches[0][0], "lastSignalDate": batches[-1][0],
                           "capturedSessions": len(batches),
                           "windows": {m: summarize([r for stamp, r in results if stamp >= cutoff])
                                       for m, cutoff in cutoffs.items()}}
        markets[market] = {"observedThrough": through, "asOf": anchor, "benchmark": benchmark, "signals": types}
    require(bool(markets) and set(previous.get("markets", {})) <= set(markets), LABEL, "market coverage lost")
    document = {"version": 1, "generatedAt": datetime.now(timezone.utc).isoformat(), "minSample": MIN_SAMPLE,
                "ledgerFiles": names, "markets": markets}
    # Serialize before replacing anything, reject nonfinite aggregates.
    json.dumps(document, allow_nan=False)
    write(path, document)
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--archive-dir", type=Path, default=ROOT / ".cache/kline-archive")
    parser.add_argument("--capture-only", action="store_true")
    args = parser.parse_args(argv)
    print(f"Signal shards added: {capture(args.root)}")
    if not args.capture_only:
        build(args.root, args.archive_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"::error::Signal outcomes not updated: {error}", file=sys.stderr)
        raise SystemExit(1)

"""Read-only Yahoo/Alpaca comparison; never edits the source CSVs."""
import argparse
from collections import defaultdict, Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import alpaca_bars as alpaca
import kline_integrity as k


def distribution(values):
    if not values:
        return dict(count=0, median=None, p95=None, maximum=None)
    data = pd.Series(values, dtype=float)
    return dict(count=len(data), median=float(data.median()),
                p95=float(data.quantile(.95)), maximum=float(data.max()))


def symbol_details(details):
    groups = defaultdict(list)
    counts = Counter()
    for item in details:
        counts[item["symbol"]] += 1
        if max(item["differencePct"].values()) > 1:
            groups[item["symbol"]].append(item)
    result = []
    for symbol, items in groups.items():
        ratios = [item["yahoo"]["Close"] / item["alpaca"]["Close"] for item in items]
        med = float(pd.Series(ratios).median())
        # Isolated takes priority: one observation cannot establish a constant scale.
        if len(items) <= 3 and counts[symbol] > len(items):
            kind = "isolated"
        elif len(items) > 3 and max(ratios) / min(ratios) <= 1.005:
            kind = "constant-scale"
        else:
            kind = "mixed"
        result.append(dict(symbol=symbol, count=len(items), compared=counts[symbol],
                           median=med, minimum=min(ratios), maximum=max(ratios),
                           first=min(i["date"] for i in items), last=max(i["date"] for i in items),
                           classification=kind))
    return sorted(result, key=lambda item: (-item["count"], item["symbol"]))


def largest_details(details):
    counts = Counter()
    result = []
    for item in sorted(details, key=lambda d: (-max(d["differencePct"].values()), d["symbol"], d["date"])):
        if counts[item["symbol"]] >= 2:
            continue
        result.append(item)
        counts[item["symbol"]] += 1
        if len(result) == 20:
            break
    return result


def compare(root, samples=200, days=250):
    if samples < 1 or days < 1:
        raise ValueError("Sample size and trading days must be positive")
    if not alpaca.enabled():
        raise RuntimeError("Alpaca credentials are not configured")
    paths = sorted(p for p in (Path(root) / "data").glob("*.csv")
                   if alpaca.alpaca_symbol(p.stem))
    chosen = sorted(random.Random(20261127).sample(paths, min(samples, len(paths))))
    frames = {}
    now = datetime.now(timezone.utc)
    for path in chosen:
        raw = pd.read_csv(path, index_col=0, parse_dates=True, **k.ROUND_TRIP)
        raw = k.usable_frame(raw, path.stem, Path(root) / "docs/report/kline-corrections.json")
        raw = raw.loc[[d for d in raw.index if k.session_complete(d, path.stem, now)]]
        if not raw.empty:
            frames[path.stem] = raw
    dates = sorted({str(d.date()) for frame in frames.values() for d in frame.index})[-days:]
    if not dates:
        raise ValueError("No completed Yahoo trading dates in sample")
    start, end = dates[0], dates[-1]
    if alpaca.prefetch(frames, start, end, adjustment=alpaca.MAIN_ADJUSTMENT) is None:
        raise RuntimeError("Alpaca batch fetch failed; comparison is incomplete")
    diffs = {name: [] for name in k.FIELDS[:4]}
    volume = []
    details = []
    yahoo_only, alpaca_only, invalid_alpaca = [], [], []
    # Include the entire calendar interval, even dates missing from all sampled CSVs.
    interval = pd.date_range(start, end, freq="B")
    for symbol, frame in frames.items():
        for stamp in interval:
            day = stamp.date().isoformat()
            row = frame.loc[stamp] if stamp in frame.index else None
            result = alpaca.alpaca_bar(symbol, day, adjustment=alpaca.MAIN_ADJUSTMENT)
            if result is None:
                if row is not None:
                    yahoo_only.append([symbol, day])
                continue
            bar = result[0]
            if row is None:
                alpaca_only.append([symbol, day])
            if k.violation(bar):
                invalid_alpaca.append([symbol, day])
                continue
            if row is None or k.violation(row):
                continue
            delta = {name: abs(float(row[name]) / float(bar[name]) - 1) * 100
                     for name in k.FIELDS[:4]}
            for name, value in delta.items():
                diffs[name].append(value)
            yv, av = row.get("Volume"), bar.get("Volume")
            if pd.notna(yv) and pd.notna(av) and float(av) > 0:
                volume.append(float(yv) / float(av))
            details.append(dict(symbol=symbol, date=day, differencePct=delta,
                                yahoo=k.bar_values(row), alpaca=k.bar_values(bar)))
    stats = {name: distribution(values) | {"over1pct": sum(v > 1 for v in values)}
             for name, values in diffs.items()}
    return dict(sampleRequested=samples, sampleActual=len(frames), tradingDays=len(dates),
                start=start, end=end, adjustment=alpaca.MAIN_ADJUSTMENT, pricesPct=stats, volumeRatio=distribution(volume),
                yahooOnly=yahoo_only, alpacaOnly=alpaca_only, invalidAlpaca=invalid_alpaca,
                bySymbol=symbol_details(details), largest20=largest_details(details))


def markdown(report):
    lines = ["# Yahoo / Alpaca daily comparison", "",
             f"Sample: {report['sampleActual']} stocks; {report['start']} to {report['end']}",
             f"Alpaca adjustment: {report['adjustment']} (main CSV nominal historical basis; mixed legacy rows possible)",
             "", "| Field | Count | Median | P95 | Max | >1% |", "|---|---:|---:|---:|---:|---:|"]
    for name, stats in report["pricesPct"].items():
        lines.append("| " + " | ".join(map(str, [name + " (%)", stats["count"], stats["median"],
                                                 stats["p95"], stats["maximum"], stats["over1pct"]])) + " |")
    v = report["volumeRatio"]
    lines.append(f"| Volume Yahoo / Alpaca | {v['count']} | {v['median']} | {v['p95']} | {v['maximum']} | n/a |")
    for key in ("yahooOnly", "alpacaOnly", "invalidAlpaca"):
        lines.extend(["", f"{key}: {len(report[key])} (full list in artifact)"])
    lines.extend(["", f"## Symbols with >1% differences ({len(report['bySymbol'])}; first 100)",
                  "Ratios use discrepant rows only; classifications are screening hints, not proof.",
                  "", "| Symbol | >1% bars / compared | Median ratio | Min | Max | First | Last | Class |",
                  "|---|---:|---:|---:|---:|---|---|---|"])
    for item in report["bySymbol"][:100]:
        lines.append(f"| {item['symbol']} | {item['count']} / {item['compared']} | {item['median']:.6g} | "
                     f"{item['minimum']:.6g} | {item['maximum']:.6g} | {item['first']} | {item['last']} | {item['classification']} |")
    lines.extend(["", "## Largest 20 price differences (max 2 per symbol)", "", "| Symbol | Date | Open % | High % | Low % | Close % |",
                  "|---|---|---:|---:|---:|---:|"])
    for item in report["largest20"]:
        lines.append("| " + " | ".join([item["symbol"], item["date"]]
                     + [f"{item['differencePct'][f]:.6g}" for f in k.FIELDS[:4]]) + " |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--days", type=int, default=250)
    parser.add_argument("--report", type=Path, help="Optional JSON artifact outside permanent data")
    args = parser.parse_args()
    report = compare(ROOT, args.samples, args.days)
    print(markdown(report))
    if args.report:
        args.report.write_text(json.dumps(report, allow_nan=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

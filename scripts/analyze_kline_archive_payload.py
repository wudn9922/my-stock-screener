"""Read-only archive/payload comparison; no source requests or archive writes.

python scripts/analyze_kline_archive_payload.py --archive-dir /tmp/archive \
    --payload-dir /tmp/payload --output /tmp/comparison.json
"""
import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import time

import kline_archive as archive


def analyze(root, payload):
    root, payload = Path(root), Path(payload)
    groups, seen, identical, rows_seen = {}, set(), 0, {}

    def visit(folder):
        nonlocal identical
        plan = archive.json_read(folder / "operations.json", {})
        if plan.get("version") != 1:
            raise ValueError("Invalid transport")
        if plan.get("baseline"):
            visit(folder / "baseline")
        for item in plan["symbols"]:
            symbol = item["symbol"]
            market = archive.market_of(symbol)
            expected = f"{market}/{symbol}.csv"
            path = folder / expected
            if item["file"] != expected or path.is_symlink() or archive.digest(path.read_bytes()) != item["sha256"]:
                raise ValueError("Invalid transport path/digest")
            identity = (symbol, item["sha256"])
            if identity in seen:
                identical += 1
                continue
            seen.add(identity)
            incoming = archive.read_rows(path, strict=True)
            old = {r[0]: r for r in archive.symbol_rows(root, symbol, {r[0][:4] for r in incoming})}
            latest = archive.latest_rows(root, symbol, 1)
            last = latest[-1][0] if latest else None
            g = groups.setdefault(market, dict(symbols=set(), incoming=0, equal=0, differ=0, missing=0,
                ohlcOnly=0, volumeOnly=0, both=0, appendable=0, historicalHole=0,
                dates={}, fields={}, samples=[]))
            g["symbols"].add(symbol)
            symbol_seen = rows_seen.setdefault(symbol, set())
            for row in incoming:
                if row in symbol_seen:
                    continue
                symbol_seen.add(row)
                g["incoming"] += 1
                before = old.get(row[0])
                kind = "missing" if before is None else "equal" if row == before else "differ"
                g[kind] += 1
                bounds = g["dates"].setdefault(kind, [row[0], row[0]])
                bounds[:] = min(bounds[0], row[0]), max(bounds[1], row[0])
                if before is None:
                    g["appendable" if last is None or row[0] > last else "historicalHole"] += 1
                if kind != "differ":
                    continue
                price, volume = row[1:5] != before[1:5], row[5] != before[5]
                g["both" if price and volume else "ohlcOnly" if price else "volumeOnly"] += 1
                if len(g["samples"]) < 3:
                    g["samples"].append(dict(symbol=symbol, old=list(before), incoming=list(row)))
                for field, a, b in zip(archive.FIELDS[1:], before[1:], row[1:]):
                    if a == b:
                        continue
                    f = g["fields"].setdefault(field, dict(count=0, unknown=0, zeroOld=0,
                        absolute=[], percent=[], buckets=Counter(), dates=[row[0], row[0]]))
                    f["count"] += 1
                    f["dates"] = [min(f["dates"][0], row[0]), max(f["dates"][1], row[0])]
                    if a == "" or b == "":
                        f["unknown"] += 1
                        continue
                    a, b = Decimal(a), Decimal(b)
                    delta = abs(b - a)
                    f["absolute"].append(float(delta))
                    if a == 0:
                        f["zeroOld"] += 1
                        continue
                    pct = float(delta / abs(a) * 100)
                    f["percent"].append(pct)
                    f["buckets"]["<=0.01%" if pct <= .01 else "<=0.1%" if pct <= .1 else
                                 "<=0.5%" if pct <= .5 else "<=1%" if pct <= 1 else ">1%"] += 1

    visit(payload)
    for g in groups.values():
        g["symbolCount"] = len(g.pop("symbols"))
        for f in g["fields"].values():
            for key in ("absolute", "percent"):
                a = sorted(f.pop(key))
                f[key + "Summary"] = dict(min=a[0], median=a[len(a) // 2],
                    p95=a[min(len(a) - 1, int(len(a) * .95))], max=a[-1]) if a else None
    return dict(comparisonUnit="unique symbol + canonical incoming row against pre-apply archive",
                duplicateOperations=identical, markets=groups)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument("--payload-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    result = analyze(args.archive_dir, args.payload_dir)
    result["seconds"] = round(time.perf_counter() - started, 3)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

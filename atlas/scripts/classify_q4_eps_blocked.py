#!/usr/bin/env python3
"""Read-only classification of the US Q4 EPS rows that the acceptance step left blocked or deferred.

Input: docs/report/q4-eps-acceptance-ledger.json (written by accept_q4_eps_sec_facts.py --apply).
Output: docs/report/q4-eps-blocked-classification.json. The financial files are not read or written.

Each blocked row gets one class, the first that applies:
- excluded-first-batch: JAKK 2018, held out of the first batch on Opus's instruction;
- one-cent-boundary: the stored and SEC values differ by exactly one cent (the old `<= 0.01` test rejected them);
- condition-<reason>: a direct SEC correction that failed one of the four conditions (no NI or Q3 shares, not closer,
  diluted and basic signs differ);
- not-direct-class: any other difference, which the ledger class "baseline-diff" does not authorise.
Deferred rows (|EPS| ≥ 1000) are listed as they are.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = str(Path(__file__).resolve().parent)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import accept_q4_eps_sec_facts as accept  # noqa: E402
from build_valuation_us import write_atomic  # noqa: E402

CONDITION_REASONS = ("no-NI-or-Q3-shares", "not-closer-to-NI-over-shares", "diluted-basic-sign", "basis-split")


def classify(row):
    key = (row["symbol"], row["end"], row["field"])
    difference = round(abs(row["old"] - row["new"]), 6)
    if key in accept.EXCLUDED_DIRECT:
        return "excluded-first-batch"
    if abs(difference - 0.01) < 1e-9:
        return "one-cent-boundary"
    reason = row["reason"]
    if reason in CONDITION_REASONS:
        return f"condition-{reason}"
    return "not-direct-class"


def build(ledger):
    rows = []
    for row in ledger["blocked"]:
        rows.append({**row, "class": classify(row), "difference": round(abs(row["old"] - row["new"]), 6)})
    counts = Counter(row["class"] for row in rows)
    return {
        "schema": "q4-eps-blocked-classification-v1",
        "source": "docs/report/q4-eps-acceptance-ledger.json",
        "blocked": len(rows),
        "counts": dict(sorted(counts.items())),
        "deferred": ledger["deferred"],
        "rows": sorted(rows, key=lambda row: (row["class"], row["symbol"], row["end"], row["field"])),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", default="docs/report/q4-eps-acceptance-ledger.json")
    parser.add_argument("--out", default="docs/report/q4-eps-blocked-classification.json")
    parser.add_argument("--write", action="store_true", help="write the report (default: print the counts)")
    args = parser.parse_args(argv)
    ledger = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
    report = build(ledger)
    print(f"blocked {report['blocked']}: " + ", ".join(f"{name} {count}" for name, count in report["counts"].items()))
    print(f"deferred {len(report['deferred'])}: " + ", ".join(f"{d['symbol']} {d['end']}" for d in report["deferred"]))
    if args.write:
        write_atomic(args.out, report)
    return report


if __name__ == "__main__":
    main()

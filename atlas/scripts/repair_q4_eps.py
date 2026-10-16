"""
Offline repair of the missing Q4 EPS in docs/report/financials/*.json (no network, no SEC or Yahoo access).

seed_financials_sec.py derives Q4 EPS = annual EPS − (Q1 + Q2 + Q3 EPS) on the basis the periods hold after
the split adjustment (build_financials.fill_q4_eps). Files seeded before that step keep a null Q4 EPS; this
script applies the same rule to the stored files (build_financials.repair_q4_document):

- only null values change: an existing EPS is never overwritten, and a filled period gets the derived flag
  (quarterly "derived") and "q4-derived" in quality.issues, as the seed writes them;
- idempotent: a second run finds nothing to fill;
- --dry-run lists the files and periods that would change and writes nothing.

The repaired data files are not committed by the change that adds this script.

Usage:
    python atlas/scripts/repair_q4_eps.py --dry-run
    python atlas/scripts/repair_q4_eps.py --symbol NFLX --verbose
    python atlas/scripts/repair_q4_eps.py --dir <copy of docs/report/financials>
"""
import argparse
import os
import sys
from collections import Counter

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_financials as fin  # noqa: E402
from build_valuation_us import read_json, write_atomic  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DIR = os.path.join(REPO_ROOT, "docs", "report", "financials")


def repair_directory(directory, dry_run=False, symbols=None):
    """
    Repair every stored file of `directory` (index.json excluded). symbols: None or the symbols to limit to.
    → {"scanned": int, "changed": [(key, [(end, field, value)])], "skipped": Counter of reasons}.
    Files that cannot be read as a version 1 document are skipped as "corrupt", never rewritten.
    """
    wanted = None if symbols is None else {fin.financials_key(symbol) for symbol in symbols}
    summary = {"scanned": 0, "changed": [], "skipped": Counter()}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name == "index.json":
            continue
        key = name[: -len(".json")]
        if wanted is not None and key not in wanted:
            continue
        summary["scanned"] += 1
        path = os.path.join(directory, name)
        document = read_json(path)
        try:
            fin.read_sections(document)
        except ValueError:
            summary["skipped"]["corrupt"] += 1
            continue
        if document.get("market") != "US":
            summary["skipped"]["not-us"] += 1
            continue
        filled = fin.repair_q4_document(document)
        if not filled:
            continue
        ends = document["quarterly"]["end"]
        values = [
            (end, field, document["quarterly"]["values"][field][ends.index(end)])
            for end, field in filled
        ]
        summary["changed"].append((key, values))
        if not dry_run:
            write_atomic(path, document)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fill null Q4 EPS of stored SEC financials offline.")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="directory of <KEY>.json files")
    parser.add_argument("--dry-run", action="store_true", help="list what would change, write nothing")
    parser.add_argument("--symbol", action="append", help="limit to this symbol (repeatable)")
    parser.add_argument("--verbose", action="store_true", help="print every filled period with its value")
    args = parser.parse_args(argv)
    summary = repair_directory(args.dir, dry_run=args.dry_run, symbols=args.symbol)
    changed = summary["changed"]
    fields = Counter(field for _, values in changed for _, field, _ in values)
    if args.verbose or args.symbol:
        for key, values in changed:
            for end, field, value in values:
                print(f"{key} {end} {field} = {value}")
    print(f"scanned {summary['scanned']} files")
    print(f"files to repair: {len(changed)}")
    print(f"periods filled: {sum(len(values) for _, values in changed)} "
          f"(epsDiluted: {fields['epsDiluted']}, epsBasic: {fields['epsBasic']})")
    if summary["skipped"]:
        print("skipped: " + ", ".join(f"{reason} {count}" for reason, count in sorted(summary["skipped"].items())))
    if args.dry_run:
        print("dry run: nothing written")
    else:
        print(f"files written: {len(changed)}" if changed else "nothing to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())

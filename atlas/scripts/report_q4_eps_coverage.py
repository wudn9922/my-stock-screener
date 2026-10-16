#!/usr/bin/env python3
"""Generate reproducible Q4 EPS grade coverage tables by fiscal year and symbol."""
import argparse
import csv
import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from build_valuation_us import read_json

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "docs/report/financials"
DEFAULT_SNAPSHOT = DEFAULT_DIR / "q4-eps-source-snapshot.json"
FIELDS = ("epsBasic", "epsDiluted")
GRADES = ("A", "B", "C", "D", "ungraded")


def _cell(block, field, index):
    values = (block.get("values") or {}).get(field) or []
    value = values[index] if index < len(values) else None
    grades = block.get("epsQuality") or []
    grade = grades[index].get(field) if index < len(grades) and isinstance(grades[index], dict) else None
    return value, grade if grade in {"A", "B", "C", "D"} else "ungraded"


def _atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _atomic_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def _baseline_doc(commit, symbol, cache):
    if symbol in cache:
        return cache[symbol]
    try:
        body = subprocess.check_output(
            ["git", "show", f"{commit}:docs/report/financials/{symbol}.json"],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL)
        cache[symbol] = json.loads(body)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        cache[symbol] = {}
    return cache[symbol]


def generate(financials_dir=DEFAULT_DIR, snapshot_path=DEFAULT_SNAPSHOT):
    directory = Path(financials_dir)
    snapshot = read_json(snapshot_path)
    base_commit = snapshot["sourceCommit"]
    by_year = defaultdict(Counter)
    by_symbol = defaultdict(Counter)
    all_rows = Counter()
    changes = Counter()
    symbols = set()
    file_hashes = {}
    baseline_cache = {}
    first_end, last_end = None, None
    for path in sorted(directory.glob("*.json")):
        if path.name == "index.json" or path.name == Path(snapshot_path).name:
            continue
        try:
            document = read_json(path)
        except (OSError, ValueError):
            continue
        if document.get("market") != "US":
            continue
        symbol = path.stem
        symbols.add(symbol)
        file_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        current_q = document.get("quarterly") or {}
        current_ids = {}
        for index, (fy, fq, end) in enumerate(zip(current_q.get("fy") or [],
                                                    current_q.get("fq") or [],
                                                    current_q.get("end") or [])):
            if fq != 4:
                continue
            current_ids[(fy, end)] = index
            year_key = str(fy)
            by_year[year_key]["q4Rows"] += 1
            all_rows["q4Rows"] += 1
            if first_end is None or end < first_end:
                first_end = end
            if last_end is None or end > last_end:
                last_end = end
            for field in FIELDS:
                value, grade = _cell(current_q, field, index)
                row = by_year[year_key]
                symbol_row = by_symbol[(symbol, field)]
                for count in (row, symbol_row):
                    count[f"{field}_total"] += 1
                    count[f"{field}_grade_{grade}"] += 1
                    if value is None:
                        count[f"{field}_rawMissing"] += 1
                    else:
                        count[f"{field}_stored"] += 1
                        if grade in {"A", "B"}:
                            count[f"{field}_formalAB"] += 1
                        if grade in {"A", "B"}:
                            count[f"{field}_formalPresent"] += 1
                        if grade not in {"A", "B", "C", "D"}:
                            count[f"{field}_ungradedPresent"] += 1
                    if value is None or grade not in {"A", "B"}:
                        count[f"{field}_formalMissing"] += 1

        baseline = _baseline_doc(base_commit, symbol, baseline_cache)
        old_q = baseline.get("quarterly") or {}
        old_ids = {(fy, end): index for index, (fy, fq, end) in enumerate(zip(
            old_q.get("fy") or [], old_q.get("fq") or [], old_q.get("end") or [])) if fq == 4}
        for key, index in current_ids.items():
            old_index = old_ids.get(key)
            for field in FIELDS:
                current_value, grade = _cell(current_q, field, index)
                old_value = None
                if old_index is not None:
                    old_values = (old_q.get("values") or {}).get(field) or []
                    old_value = old_values[old_index] if old_index < len(old_values) else None
                if old_value == current_value:
                    continue
                if old_value is None and current_value is not None and grade == "A":
                    changes["gradeA-new-field-fills"] += 1
                elif old_value is not None and current_value is not None and grade == "A":
                    changes["gradeA-existing-value-corrections"] += 1
                    if abs(float(old_value) - float(current_value)) > 0.01:
                        changes["gradeA-corrections-over-1-cent"] += 1
                    else:
                        changes["gradeA-corrections-1-cent-or-less"] += 1
                else:
                    changes["other-value-changes"] += 1
        # Count grades by unique fiscal period and EPS field across all SEC A traces.
    year_rows = []
    for year, counts in sorted(by_year.items()):
        row = {"fiscalYear": year, "q4Rows": counts["q4Rows"]}
        for field in FIELDS:
            total = counts[f"{field}_total"]
            row.update({
                f"{field}_rawMissing": counts[f"{field}_rawMissing"],
                f"{field}_formalMissing": counts[f"{field}_formalMissing"],
                f"{field}_formalPresent": counts[f"{field}_formalPresent"],
                f"{field}_gradeA": counts[f"{field}_grade_A"],
                f"{field}_gradeB": counts[f"{field}_grade_B"],
                f"{field}_gradeC": counts[f"{field}_grade_C"],
                f"{field}_gradeD": counts[f"{field}_grade_D"],
                f"{field}_ungradedPresent": counts[f"{field}_ungradedPresent"],
                f"{field}_formalCoveragePct": round(100 * counts[f"{field}_formalPresent"] / total, 4) if total else 0,
            })
        year_rows.append(row)
    symbol_rows = []
    for (symbol, field), counts in sorted(by_symbol.items()):
        total = counts[f"{field}_total"]
        symbol_rows.append({
            "symbol": symbol, "field": field, "q4Rows": total,
            "rawMissing": counts[f"{field}_rawMissing"],
            "formalMissing": counts[f"{field}_formalMissing"],
            "formalPresent": counts[f"{field}_formalPresent"],
            "gradeA": counts[f"{field}_grade_A"], "gradeB": counts[f"{field}_grade_B"],
            "gradeC": counts[f"{field}_grade_C"], "gradeD": counts[f"{field}_grade_D"],
            "ungradedPresent": counts[f"{field}_ungradedPresent"],
            "formalCoveragePct": round(100 * counts[f"{field}_formalPresent"] / total, 4) if total else 0,
        })
    totals = {}
    for field in FIELDS:
        total = sum(row[f"{field}_total"] for row in by_symbol.values())
        stored = sum(row[f"{field}_stored"] for row in by_symbol.values())
        formal = sum(row[f"{field}_formalPresent"] for row in by_symbol.values())
        formal_missing = sum(row[f"{field}_formalMissing"] for row in by_symbol.values())
        totals[field] = {
            "q4Rows": total,
            "rawMissing": sum(row[f"{field}_rawMissing"] for row in by_symbol.values()),
            "stored": stored,
            "formalPresent": formal,
            "formalMissing": formal_missing,
            "gradeA": sum(row[f"{field}_grade_A"] for row in by_symbol.values()),
            "gradeB": sum(row[f"{field}_grade_B"] for row in by_symbol.values()),
            "gradeC": sum(row[f"{field}_grade_C"] for row in by_symbol.values()),
            "gradeD": sum(row[f"{field}_grade_D"] for row in by_symbol.values()),
            "ungradedPresent": sum(row[f"{field}_ungradedPresent"] for row in by_symbol.values()),
            "formalCoveragePct": round(100 * formal / total, 4) if total else 0,
            "AorBCoveragePct": round(100 * (sum(row[f"{field}_grade_A"] + row[f"{field}_grade_B"] for row in by_symbol.values())) / total, 4) if total else 0,
        }
    result = {
        "schema": "q4-eps-coverage-report-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceSnapshotCommit": base_commit,
        "sourceSnapshot": Path(snapshot_path).name,
        "usSymbolFiles": len(symbols),
        "q4Rows": all_rows["q4Rows"],
        "dateRange": {"firstEnd": first_end, "lastEnd": last_end},
        "formalUseDefinition": "Q4 EPS counts as formally usable only with grade A or B; C, D, ungraded legacy values, and nulls are excluded",
        "byEpsField": totals,
        "baselineToCurrentNumericChanges": dict(changes),
        "coverageByFiscalYearCsv": "q4-eps-coverage-by-fiscal-year.csv",
        "coverageBySymbolCsv": "q4-eps-coverage-by-symbol.csv",
        "postRunFileSha256": file_hashes,
    }
    _atomic_json(directory / "q4-eps-coverage.json", result)
    _atomic_csv(directory / "q4-eps-coverage-by-fiscal-year.csv", year_rows,
                ["fiscalYear", "q4Rows"] + [key for field in FIELDS for key in (
                    f"{field}_rawMissing", f"{field}_formalMissing", f"{field}_formalPresent",
                    f"{field}_gradeA", f"{field}_gradeB", f"{field}_gradeC", f"{field}_gradeD",
                    f"{field}_ungradedPresent", f"{field}_formalCoveragePct")])
    _atomic_csv(directory / "q4-eps-coverage-by-symbol.csv", symbol_rows,
                ["symbol", "field", "q4Rows", "rawMissing", "formalMissing", "formalPresent",
                 "gradeA", "gradeB", "gradeC", "gradeD", "ungradedPresent", "formalCoveragePct"])
    return result, year_rows, symbol_rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    args = parser.parse_args(argv)
    report, _, _ = generate(args.dir, args.snapshot)
    summary = {key: value for key, value in report.items() if key != "postRunFileSha256"}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Compare the mutually exclusive missing diluted Q4 EPS audit before and after repair."""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPTS = str(Path(__file__).resolve().parent)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from audit_q4_eps_backfill import REASONS, audit  # noqa: E402


def count_rows(directory):
    periods, _counts, _errors, missing_income = audit(directory)
    totals = Counter(row[3] for row in periods)
    files = defaultdict(set)
    details = Counter()
    detail_files = defaultdict(set)
    for symbol, _fy, _end, reason, detail in periods:
        files[reason].add(symbol)
        if reason == REASONS[3]:
            details[detail] += 1
            detail_files[detail].add(symbol)
    return totals, files, details, detail_files, missing_income, periods


def write_unresolved(periods, output, snapshot):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for symbol, fiscal_year, period_end, category, detail in periods:
            row = {
                "schema": "q4-eps-diluted-unresolved-v1",
                "snapshot": snapshot,
                "symbol": symbol,
                "fiscalYear": fiscal_year,
                "periodEnd": period_end,
                "category": category,
                "failureReason": detail or category,
            }
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_success_by_baseline_category(baseline_periods, current_dir, output):
    current_dir = Path(current_dir)
    records = defaultdict(list)
    documents = {}
    for row in baseline_periods:
        records[row[3]].append(row)
    for rows in records.values():
        for symbol, _fy, _end, _reason, _detail in rows:
            if symbol not in documents:
                path = current_dir / f"{symbol}.json"
                try:
                    documents[symbol] = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    documents[symbol] = {}
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as stream:
        fields = ["baseline_category", "baseline_periods", "formal_A_B_filled", "still_unresolved", "fill_rate_pct"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for category in REASONS:
            rows = records[category]
            filled = 0
            for symbol, fiscal_year, period_end, _reason, _detail in rows:
                quarterly = (documents.get(symbol) or {}).get("quarterly") or {}
                values = quarterly.get("values") or {}
                eps_values = values.get("epsDiluted") or []
                qualities = quarterly.get("epsQuality") or []
                for index, (fy, fq, end) in enumerate(zip(quarterly.get("fy") or [], quarterly.get("fq") or [], quarterly.get("end") or [])):
                    if (fy, fq, end) != (fiscal_year, 4, period_end):
                        continue
                    grade = qualities[index].get("epsDiluted") if index < len(qualities) and isinstance(qualities[index], dict) else None
                    if index < len(eps_values) and eps_values[index] is not None and grade in ("A", "B"):
                        filled += 1
                    break
            total = len(rows)
            writer.writerow({
                "baseline_category": category,
                "baseline_periods": total,
                "formal_A_B_filled": filled,
                "still_unresolved": total - filled,
                "fill_rate_pct": f"{(100 * filled / total if total else 0):.4f}",
            })


def report(baseline_dir, current_dir, output, baseline_label, current_label,
           unresolved_before, unresolved_current, success_by_category):
    before = count_rows(baseline_dir)
    after = count_rows(current_dir)
    rows = []
    for category in REASONS:
        rows.append({
            "baseline_snapshot": baseline_label, "current_snapshot": current_label,
            "scope": "top-level", "category": category, "reason": category,
            "baseline_periods": before[0][category],
            "baseline_files_nonexclusive": len(before[1][category]),
            "current_periods": after[0][category],
            "current_files_nonexclusive": len(after[1][category]),
            "delta_periods": after[0][category] - before[0][category],
        })
    for reason in sorted(set(before[2]) | set(after[2])):
        rows.append({
            "baseline_snapshot": baseline_label, "current_snapshot": current_label,
            "scope": "other-detail", "category": REASONS[3], "reason": reason,
            "baseline_periods": before[2][reason],
            "baseline_files_nonexclusive": len(before[3][reason]),
            "current_periods": after[2][reason],
            "current_files_nonexclusive": len(after[3][reason]),
            "delta_periods": after[2][reason] - before[2][reason],
        })
    fieldnames = [
        "baseline_snapshot", "current_snapshot", "scope", "category", "reason",
        "baseline_periods", "baseline_files_nonexclusive",
        "current_periods", "current_files_nonexclusive", "delta_periods",
    ]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    write_unresolved(before[5], unresolved_before, baseline_label)
    write_unresolved(after[5], unresolved_current, current_label)
    write_success_by_baseline_category(before[5], current_dir, success_by_category)
    print(
        f"baseline={sum(before[0].values())} current={sum(after[0].values())} "
        f"other={before[0][REASONS[3]]}->{after[0][REASONS[3]]} "
        f"missing-net-income-outside-target={before[4]}->{after[4]} output={output}"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--current-dir", default=str(Path(__file__).resolve().parents[2] / "docs/report/financials"))
    parser.add_argument("--output", default=str(Path(__file__).resolve().parents[2] / "docs/report/financials/q4-eps-audit-before-after.csv"))
    parser.add_argument("--baseline-label", default="3f9406d8b")
    parser.add_argument("--current-label", default="working-tree")
    parser.add_argument("--unresolved-before", default=str(Path(__file__).resolve().parents[2] / "docs/report/financials/q4-eps-diluted-unresolved-before.jsonl"))
    parser.add_argument("--unresolved-current", default=str(Path(__file__).resolve().parents[2] / "docs/report/financials/q4-eps-diluted-unresolved-current.jsonl"))
    parser.add_argument("--success-by-category", default=str(Path(__file__).resolve().parents[2] / "docs/report/financials/q4-eps-diluted-repair-success-by-category.csv"))
    args = parser.parse_args(argv)
    # Labels are accepted for the reproducibility command and recorded in the console.
    print(f"baseline_label={args.baseline_label} current_label={args.current_label}")
    report(args.baseline_dir, args.current_dir, args.output, args.baseline_label, args.current_label,
           args.unresolved_before, args.unresolved_current, args.success_by_category)


if __name__ == "__main__":
    main()

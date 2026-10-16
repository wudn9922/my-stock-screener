#!/usr/bin/env python3
"""Audit Q4 EPS numeric changes against the immutable source snapshot."""
import argparse
import json
import os
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FINANCIALS = ROOT / "docs/report/financials"
SNAPSHOT = FINANCIALS / "q4-eps-source-snapshot.json"
DECISIONS = FINANCIALS / "q4-eps-sec-A-decisions.jsonl"
OUTPUT = FINANCIALS / "q4-eps-numeric-corrections.jsonl"
SUMMARY = FINANCIALS / "q4-eps-numeric-corrections.json"
EPS_FIELDS = ("epsBasic", "epsDiluted")


def _baseline_document(commit, symbol, cache):
    if symbol not in cache:
        raw = subprocess.check_output(
            ["git", "show", f"{commit}:docs/report/financials/{symbol}.json"],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL)
        cache[symbol] = json.loads(raw)
    return cache[symbol]


def _jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def generate(financials_dir=FINANCIALS, snapshot_path=SNAPSHOT,
             decisions_path=DECISIONS, output_path=OUTPUT, summary_path=SUMMARY):
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    commit = snapshot["sourceCommit"]
    explicit = {(row.get("symbol"), (row.get("period") or {}).get("end"), row.get("field")): row
                for row in _jsonl(decisions_path)
                if row.get("status") == "correct-existing-with-direct-sec"}
    baseline, rows, counts = {}, [], Counter()
    for current_path in sorted(Path(financials_dir).glob("*.json")):
        if current_path.name in {"index.json", Path(snapshot_path).name}:
            continue
        try:
            current = json.loads(current_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if current.get("market") != "US":
            continue
        symbol = current_path.stem
        try:
            old = _baseline_document(commit, symbol, baseline)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        old_q, new_q = old.get("quarterly") or {}, current.get("quarterly") or {}
        old_index = {(fy, end): i for i, (fy, fq, end) in enumerate(zip(
            old_q.get("fy") or [], old_q.get("fq") or [], old_q.get("end") or [])) if fq == 4}
        new_grades = new_q.get("epsQuality") or []
        new_trace = current.get("secQ4EpsFacts") or {}
        for i, (fy, fq, end) in enumerate(zip(new_q.get("fy") or [], new_q.get("fq") or [],
                                               new_q.get("end") or [])):
            if fq != 4 or (fy, end) not in old_index:
                continue
            previous_i = old_index[(fy, end)]
            for field in EPS_FIELDS:
                previous_values = ((old_q.get("values") or {}).get(field) or [])
                current_values = ((new_q.get("values") or {}).get(field) or [])
                previous = previous_values[previous_i] if previous_i < len(previous_values) else None
                value = current_values[i] if i < len(current_values) else None
                grade = (new_grades[i].get(field) if i < len(new_grades)
                         and isinstance(new_grades[i], dict) else None)
                if previous is None or value is None or float(previous) == float(value) or grade != "A":
                    continue
                source = (new_trace.get(end) or {}).get(field) or {}
                explicit_row = explicit.get((symbol, end, field))
                correction_class = ("direct-sec-existing-value-correction" if explicit_row
                                    else "baseline-diff-current-value-sec-grade-a-source-verified")
                row = {
                    "schema": "q4-eps-numeric-correction-v1", "symbol": symbol,
                    "period": {"fiscalYear": fy, "end": end}, "field": field,
                    "baselineValue": previous, "correctedValue": value,
                    "deltaUsdPerShare": float(value) - float(previous), "grade": grade,
                    "correctionClass": correction_class,
                    "explicitSecCorrectionOldValue": (explicit_row or {}).get("oldValue"),
                    "source": {key: source.get(key) for key in (
                        "concept", "unit", "cik", "accession", "filed", "form", "url", "filingUrl",
                        "method", "reportedValue", "splitFactor", "version")},
                }
                if not row["source"].get("accession") or not row["source"].get("filingUrl"):
                    raise RuntimeError(f"grade-A changed EPS lacks filing provenance: {symbol} {end} {field}")
                rows.append(row)
                counts[correction_class] += 1
                if abs(float(value) - float(previous)) > 0.01:
                    counts["change-over-one-cent"] += 1
                else:
                    counts["change-one-cent-or-less"] += 1
    rows.sort(key=lambda row: (row["symbol"], row["period"]["end"], row["field"]))
    output_path, summary_path = Path(output_path), Path(summary_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_path.parent,
                                     prefix=output_path.name + ".", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, output_path)
    report = {"schema": "q4-eps-numeric-corrections-report-v1", "sourceSnapshotCommit": commit,
              "corrections": len(rows), "byClass": dict(counts),
              "sourceVerifiedGradeA": len(rows), "rowLedger": output_path.name}
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=FINANCIALS)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--decisions", type=Path, default=DECISIONS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    args = parser.parse_args(argv)
    print(json.dumps(generate(args.dir, args.snapshot, args.decisions, args.output, args.summary),
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Restore Q4 cells changed by a SEC candidate that failed its sanity cross-check."""
import argparse
import json
import os
import subprocess
from pathlib import Path

from build_valuation_us import read_json, write_atomic

ROOT = Path(__file__).resolve().parents[2]
FINANCIALS = ROOT / "docs/report/financials"
DECISIONS = FINANCIALS / "q4-eps-sec-A-decisions.jsonl"
SIDECAR = FINANCIALS / "q4-eps-sec-D-restorations.jsonl"


def _baseline_file(commit, symbol):
    return json.loads(subprocess.check_output(
        ["git", "show", f"{commit}:docs/report/financials/{symbol}.json"],
        cwd=ROOT, text=True,
    ))


def stage(decisions_path=DECISIONS, commit=None):
    snapshot_path = FINANCIALS / "q4-eps-source-snapshot.json"
    snapshot = read_json(snapshot_path)
    commit = commit or snapshot.get("sourceCommit")
    if not commit:
        raise RuntimeError("source snapshot has no sourceCommit")
    records = [json.loads(line) for line in Path(decisions_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    baselines = {}
    pending = {}
    result = []
    for record in records:
        period = record.get("period") or {}
        if record.get("status") != "rejected" or record.get("grade") != "D" or not period.get("end"):
            continue
        symbol, field, end = record.get("symbol"), record.get("field"), period["end"]
        if field not in {"epsBasic", "epsDiluted"}:
            continue
        baseline = baselines.setdefault(symbol, _baseline_file(commit, symbol))
        bq = baseline.get("quarterly") or {}
        base_matches = [i for i, (fy, date, fq) in enumerate(zip(
            bq.get("fy") or [], bq.get("end") or [], bq.get("fq") or []))
            if fq == 4 and date == end and (period.get("fiscalYear") is None or fy == period.get("fiscalYear"))]
        if len(base_matches) != 1:
            continue
        bi = base_matches[0]
        bcol = (bq.get("values") or {}).get(field) or []
        base_value = bcol[bi] if bi < len(bcol) else None

        path = FINANCIALS / f"{symbol}.json"
        document = pending.setdefault(symbol, read_json(path))
        q = document.get("quarterly") or {}
        matches = [i for i, (fy, date, fq) in enumerate(zip(
            q.get("fy") or [], q.get("end") or [], q.get("fq") or []))
            if fq == 4 and date == end and (period.get("fiscalYear") is None or fy == period.get("fiscalYear"))]
        if len(matches) != 1:
            raise RuntimeError(f"current period is not unique: {symbol} {end}")
        index = matches[0]
        qvalues = q.get("values") or {}
        column = qvalues.get(field) or []
        current = column[index] if index < len(column) else None
        grades = q.get("epsQuality") or []
        grade = grades[index].get(field) if index < len(grades) and isinstance(grades[index], dict) else None
        trace = ((document.get("secQ4EpsFacts") or {}).get(end) or {}).get(field) or {}
        if current == base_value:
            continue
        if grade != "D" or trace.get("method") != "direct-sec-companyfacts":
            raise RuntimeError(f"refusing to restore unrelated value: {symbol} {end} {field} grade={grade}")
        result.append({
            "schema": "q4-eps-evidence-staging-v1",
            "symbol": symbol,
            "cik": record.get("cik"),
            "period": period,
            "field": field,
            "grade": "D",
            "status": "rejected-sec-value-restored-to-source-snapshot",
            "sourceSnapshotCommit": commit,
            "previouslyStoredValue": base_value,
            "rejectedSecValue": current,
            "source": record.get("source"),
            "failureReason": (record.get("validation") or {}).get("failureReason"),
            "formalUse": "excluded",
        })
        while len(column) < len(q.get("end") or []):
            column.append(None)
        column[index] = base_value
        qvalues[field] = column
        q["values"] = qvalues
        trace["rejectedSecValue"] = current
        trace["restoredPreviousValue"] = base_value
        trace["grade"] = "D"
        document["quarterly"] = q
        pending[symbol] = document
    return result, pending


def apply(records, documents, financials_dir=FINANCIALS, sidecar_path=SIDECAR):
    for symbol, document in documents.items():
        write_atomic(str(Path(financials_dir) / f"{symbol}.json"), document)
    sidecar_path = Path(sidecar_path)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = sidecar_path.with_name(sidecar_path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, sidecar_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--commit")
    args = parser.parse_args(argv)
    records, documents = stage(commit=args.commit)
    print(json.dumps({"status": "applied" if args.apply else "staged",
                      "valuesRestored": len(records), "files": len(documents),
                      "decisionFile": str(DECISIONS)}, ensure_ascii=False, sort_keys=True))
    if args.apply:
        apply(records, documents)
    elif records:
        print("Run with --apply to restore rejected candidates to the immutable source snapshot value.")


if __name__ == "__main__":
    main()

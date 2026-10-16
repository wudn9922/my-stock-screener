#!/usr/bin/env python3
"""Mark prior diluted-share Q4 estimates as grade C and exclude them from formal selection.

The 2026-10-16 migration identifies the 183 epsDiluted values introduced by the
``3f9406d8b`` strict-shares commit, verifies each still equals its formula, preserves
full inputs in a grade-C sidecar. The displayed historical estimate stays in the
source file with a per-period grade; formal growth / selection consumers filter that
grade. It does not change EPS values or touch SEC-backed / unrelated quarters.
"""
import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import audit_q4_eps_backfill as audit  # noqa: E402
from build_valuation_us import read_json, write_atomic  # noqa: E402

DEFAULT_DIR = REPO_ROOT / "docs/report/financials"
DEFAULT_SIDECAR = REPO_ROOT / "docs/report/financials/q4-eps-quarantine-C.jsonl"
INTRODUCING_COMMIT = "3f9406d8b"


def _parent_document(relative_path):
    try:
        body = subprocess.check_output(
            ["git", "show", f"{INTRODUCING_COMMIT}^:{relative_path}"],
            cwd=REPO_ROOT, stderr=subprocess.DEVNULL, text=True,
        )
        return json.loads(body)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def _introduced_values():
    names = subprocess.check_output(
        ["git", "diff", "--name-only", f"{INTRODUCING_COMMIT}^", INTRODUCING_COMMIT,
         "--", "docs/report/financials"], cwd=REPO_ROOT, text=True,
    ).splitlines()
    result = set()
    for name in names:
        if not name.endswith(".json") or name.endswith("/index.json"):
            continue
        old = _parent_document(name)
        if old is None:
            continue
        path = REPO_ROOT / name
        new = read_json(path)
        old_q, new_q = old.get("quarterly") or {}, new.get("quarterly") or {}
        old_index = {(fy, end, fq): i for i, (fy, end, fq) in enumerate(zip(
            old_q.get("fy") or [], old_q.get("end") or [], old_q.get("fq") or []))}
        new_values = new_q.get("values") or {}
        old_values = old_q.get("values") or {}
        for i, key in enumerate(zip(new_q.get("fy") or [], new_q.get("end") or [], new_q.get("fq") or [])):
            fy, end, fq = key
            if fq != 4 or key not in old_index:
                continue
            j = old_index[key]
            before = old_values.get("epsDiluted") or []
            after = new_values.get("epsDiluted") or []
            old_value = before[j] if j < len(before) else None
            new_value = after[i] if i < len(after) else None
            direct_trace = ((new.get("secQ4EpsFacts") or {}).get(end) or {}).get("epsDiluted")
            if old_value is None and new_value is not None and not direct_trace:
                result.add((path.stem, fy, end, i, float(new_value)))
    return result


def stage(directory=DEFAULT_DIR):
    candidates = _introduced_values()
    records = []
    rejected = []
    files = set()
    for symbol, fy, end, index, stored in sorted(candidates):
        path = Path(directory) / f"{symbol}.json"
        document = read_json(path)
        observation, reason = audit.method_b_observation(document, index)
        if reason:
            rejected.append({"symbol": symbol, "fy": fy, "end": end, "reason": reason})
            continue
        if round(float(observation["estimate"]), 4) != stored:
            rejected.append({"symbol": symbol, "fy": fy, "end": end,
                             "reason": "stored-value-does-not-match-reconstructed-estimate",
                             "stored": stored, "recomputed": float(observation["estimate"])})
            continue
        q = document.get("quarterly") or {}
        annual = document.get("annual") or {}
        q3_indices = [i for i, (qfy, qfq) in enumerate(zip(q.get("fy") or [], q.get("fq") or []))
                      if qfy == fy and qfq == 3]
        annual_indices = [i for i, afy in enumerate(annual.get("fy") or []) if afy == fy]
        records.append({
            "schema": "q4-eps-evidence-staging-v1",
            "symbol": symbol,
            "cik": None,
            "period": {"fiscalYear": fy, "end": end},
            "field": "epsDiluted",
            "value": f"{stored:.4f}",
            "currency": document.get("currency"),
            "unit": "USD/shares",
            "grade": "C",
            "status": "quarantined",
            "method": "annual-diluted-shares-times-four-minus-q1-q3",
            "source": {
                "financialsFile": f"docs/report/financials/{symbol}.json",
                "financialsSource": document.get("source"),
                "quarterSource": (q.get("src") or [None] * (index + 1))[index],
                "netIncomeConcept": "NetIncomeLoss",
                "shareConcept": "WeightedAverageNumberOfDilutedSharesOutstanding",
                "shareClassCount": None,
                "accession": None,
                "url": None,
            },
            "calculation": {
                "formula": "Q4 net income / (annual diluted shares * 4 - Q1 - Q2 - Q3 diluted shares)",
                "netIncome": str(observation["netIncome"]),
                "annualShares": str(observation["annualShares"]),
                "q1Shares": str(observation["q123Shares"][0]),
                "q2Shares": str(observation["q123Shares"][1]),
                "q3Shares": str(observation["q123Shares"][2]),
                "estimatedQ4Shares": str(observation["q4Shares"]),
                "shareRangeRatio": str(max(observation["annualShares"], *observation["q123Shares"])
                                        / min(observation["annualShares"], *observation["q123Shares"]) - 1),
                "q3DeviationRatio": str(abs(observation["q4Shares"] / observation["q123Shares"][2] - 1)),
            },
            "validation": {
                "passedForHistoricalEstimationOnly": True,
                "reasonNotFormalGradeB": "diluted Q4 weighted-average shares were not reported or independently validated; annual-share arithmetic is an estimate",
                "previouslyStoredValue": f"{stored:.4f}",
                "quarantinePolicy": "grade C is retained for display/audit but excluded from formal Q4 EPS and growth input",
            },
        })
        files.add(symbol)
    return records, rejected, files


def apply(records, directory=DEFAULT_DIR, dry_run=True):
    if dry_run:
        return len({record["symbol"] for record in records})
    written = set()
    for record in records:
        path = Path(directory) / f"{record['symbol']}.json"
        document = read_json(path)
        q = document.get("quarterly") or {}
        identity = (record["period"]["fiscalYear"], record["period"]["end"], 4)
        matches = [i for i, row in enumerate(zip(q.get("fy") or [], q.get("end") or [], q.get("fq") or []))
                   if row == identity]
        if len(matches) != 1:
            raise RuntimeError(f"period changed while applying C quarantine: {record['symbol']} {identity}")
        i = matches[0]
        values = q.get("values") or {}
        column = values.get("epsDiluted") or []
        if i >= len(column):
            raise RuntimeError(f"EPS column is short: {record['symbol']} {identity}")
        if column[i] is not None and f"{float(column[i]):.4f}" != record["value"]:
            raise RuntimeError(f"EPS value changed while applying C quarantine: {record['symbol']} {identity}")
        if column[i] is None:
            column[i] = float(record["value"])  # recover an interrupted pre-grade migration idempotently
        values["epsDiluted"] = column
        q["values"] = values
        grades = q.get("epsQuality")
        if not isinstance(grades, list):
            grades = [None] * len(q.get("end") or [])
        while len(grades) < len(q.get("end") or []):
            grades.append(None)
        grade = grades[i] if isinstance(grades[i], dict) else {}
        grade["epsDiluted"] = "C"
        grades[i] = grade
        q["epsQuality"] = grades
        document["quarterly"] = q
        write_atomic(str(path), document)
        written.add(record["symbol"])
    return len(written)


def _write_jsonl_atomic(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _read_jsonl(path):
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--sidecar", type=Path, default=DEFAULT_SIDECAR)
    parser.add_argument("--apply", action="store_true", help="attach grade C to the exact staged cells after writing the sidecar")
    parser.add_argument("--dry-run", action="store_true", help="show counts only")
    args = parser.parse_args(argv)
    if args.apply and args.sidecar.exists():
        records = _read_jsonl(args.sidecar)
        rejected, files = [], {record["symbol"] for record in records}
    else:
        records, rejected, files = stage(args.dir)
    if rejected:
        print(json.dumps({"status": "stopped-rejections", "candidateCount": len(records) + len(rejected),
                          "validCount": len(records), "rejected": rejected[:20]}, ensure_ascii=False))
        return 2
    if args.dry_run:
        print(json.dumps({"status": "dry-run", "periods": len(records), "files": len(files)}, ensure_ascii=False))
        return 0
    _write_jsonl_atomic(args.sidecar, records)
    written = apply(records, args.dir, dry_run=not args.apply)
    print(json.dumps({"status": "staged", "periods": len(records), "files": len(files),
                      "formalFilesWritten": written if args.apply else 0,
                      "sidecar": str(args.sidecar)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

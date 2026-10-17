#!/usr/bin/env python3
"""Opus step 1–2 (offline): separate share-count estimates and grade US Q4 EPS by provenance.

1. Share-count estimates (audit_q4_eps_backfill method B, the 'q4-eps-from-shares' fills) are moved out of
   quarterly.values.epsDiluted into document["q4EpsEstimates"][end] with their formula inputs, and the
   epsDiluted cell goes back to null. Only values that method B reproduces exactly are moved; each one
   keeps grade C in the record.
2. Each remaining US Q4 EPS value gets a grade in quarterly.epsQuality[i]:
   - A  when secQ4EpsFacts holds a direct SEC 3-month fact for that field (provenance recorded there);
   - Y  when the Q4 is derived (annual − Q1..Q3, derived == 1) and has no SEC fact;
   - nothing (ungraded) otherwise.
   Files that already carry epsQuality are left alone.

Default mode is a dry run that prints counts. --apply writes the files in place.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = str(Path(__file__).resolve().parent)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import audit_q4_eps_backfill as audit  # noqa: E402
from build_valuation_us import write_atomic  # noqa: E402

ESTIMATE_GRADE = "C"
FIELDS = ("epsDiluted", "epsBasic")


SHARE_FLAG = "q4-eps-from-shares"  # quality issue written by audit_q4_eps_backfill.apply_method_b


def _derivation(document, index):
    """annual EPS − (Q1 + Q2 + Q3 EPS) of the same fiscal year, or None when the pieces are missing."""
    quarterly = document["quarterly"]
    fy = quarterly["fy"][index]
    annual_rows = [i for i, year in enumerate(document.get("annual", {}).get("fy") or []) if year == fy]
    piece_rows = [i for i, (year, fq) in enumerate(zip(quarterly.get("fy") or [], quarterly.get("fq") or []))
                  if year == fy and fq in (1, 2, 3)]
    annual_eps = audit.value(document["annual"], "epsDiluted", annual_rows[0]) if len(annual_rows) == 1 else None
    pieces = [audit.value(quarterly, "epsDiluted", i) for i in piece_rows]
    if annual_eps is None or len(pieces) != 3 or any(piece is None for piece in pieces):
        return None
    return annual_eps - sum(pieces)


def move_share_estimates(document):
    """Move each method-B Q4 diluted EPS into q4EpsEstimates. Returns the number moved.

    A value moves only when all of these hold: the file carries the share flag (apply_method_b fills nulls only,
    so a flagged value was a share estimate when it was written), the value is the method-B estimate reproduced
    to 4 decimals, it has no SEC fact, and it is not exactly the annual − Q1..Q3 derivation at cent precision.
    """
    quarterly = document.get("quarterly") or {}
    eps_values = (quarterly.get("values") or {}).get("epsDiluted")
    if not isinstance(eps_values, list):
        return 0
    issues = (document.get("quality") or {}).get("issues")
    if not isinstance(issues, list) or SHARE_FLAG not in issues:
        return 0
    derived = quarterly.get("derived") if isinstance(quarterly.get("derived"), list) else []
    trace = document.get("secQ4EpsFacts") or {}
    moved = 0
    for index, fq in enumerate(quarterly.get("fq") or []):
        if fq != 4 or index >= len(eps_values) or eps_values[index] is None:
            continue
        end = quarterly["end"][index]
        if "epsDiluted" in trace.get(end, {}):
            continue
        observation, reason = audit.method_b_observation(document, index)
        if reason:
            continue
        stored = round(float(observation["estimate"]), 4)
        if stored != eps_values[index]:
            continue
        derivation = _derivation(document, index)
        if derivation is not None and round(derivation, 2) == eps_values[index]:
            continue
        document.setdefault("q4EpsEstimates", {})[end] = {
            "epsDiluted": stored,
            "grade": ESTIMATE_GRADE,
            "method": "net-income-over-estimated-q4-shares",
            "formula": "netIncome / (4 × annual dilutedShares − Q1..Q3 dilutedShares)",
            "netIncome": float(observation["netIncome"]),
            "annualDilutedShares": float(observation["annualShares"]),
            "q123DilutedShares": [float(shares) for shares in observation["q123Shares"]],
            "estimatedQ4Shares": float(observation["q4Shares"]),
        }
        eps_values[index] = None
        if index < len(derived):
            derived[index] = None
        moved += 1
    if moved:
        quarterly["derived"] = derived
    return moved


def grade_q4_eps(document):
    """Write quarterly.epsQuality for the Q4 EPS values. Returns the number of graded quarters."""
    quarterly = document.get("quarterly") or {}
    if "epsQuality" in quarterly or not isinstance(quarterly.get("values"), dict):
        return 0
    ends = quarterly.get("end") or []
    derived = quarterly.get("derived") if isinstance(quarterly.get("derived"), list) else []
    trace = document.get("secQ4EpsFacts") or {}
    grades = [None] * len(ends)
    graded = 0
    for index, fq in enumerate(quarterly.get("fq") or []):
        if fq != 4 or index >= len(ends):
            continue
        entry = {}
        for field in FIELDS:
            column = quarterly["values"].get(field) or []
            if index >= len(column) or column[index] is None:
                continue
            if field in trace.get(ends[index], {}):
                entry[field] = "A"
            elif index < len(derived) and derived[index] == 1:
                entry[field] = "Y"
        if entry:
            grades[index] = entry
            graded += 1
    if graded:
        quarterly["epsQuality"] = grades
    return graded


def process_file(path, apply):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("market") != "US":
        return None
    moved = move_share_estimates(document)
    graded = grade_q4_eps(document)
    if (moved or graded) and apply:
        write_atomic(str(path), document)
    return {"moved": moved, "graded": graded, "symbol": document.get("symbol")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default="docs/report/financials")
    parser.add_argument("--apply", action="store_true", help="write the changed files (default: dry run)")
    args = parser.parse_args(argv)
    totals = Counter()
    for path in sorted(Path(args.dir).glob("*.json")):
        if path.name == "index.json":
            continue
        result = process_file(path, args.apply)
        if result is None:
            continue
        totals["files"] += 1
        totals["moved"] += result["moved"]
        totals["filesMoved"] += 1 if result["moved"] else 0
        totals["gradedQuarters"] += result["graded"]
        totals["filesGraded"] += 1 if result["graded"] else 0
    mode = "applied" if args.apply else "dry run"
    print(f"{mode}: {totals['files']} US files; share estimates moved {totals['moved']} "
          f"in {totals['filesMoved']} files; graded Q4 quarters {totals['gradedQuarters']} "
          f"in {totals['filesGraded']} files")
    return totals


if __name__ == "__main__":
    main()

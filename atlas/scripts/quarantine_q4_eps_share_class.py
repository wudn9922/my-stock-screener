#!/usr/bin/env python3
"""Quarantine Berkshire B's class-ambiguous SEC EPS fact as grade D."""
import argparse
import json
import os
from pathlib import Path

from build_valuation_us import read_json, write_atomic

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "docs/report/financials/BRK-B.json"
SIDECAR = ROOT / "docs/report/financials/q4-eps-share-class-D.jsonl"
CIK = "0001067983"
PERIOD = {"fiscalYear": 2012, "start": "2012-10-01", "end": "2012-12-31"}
REPORTED = 2757.0


def record():
    return {
        "schema": "q4-eps-evidence-staging-v1",
        "symbol": "BRK-B",
        "cik": CIK,
        "period": PERIOD,
        "field": "epsBasic",
        "grade": "D",
        "status": "quarantined-share-class-basis-unknown",
        "method": "official-fact-class-identity-review",
        "reportedValue": REPORTED,
        "source": {
            "provider": "SEC EDGAR companyfacts",
            "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0001067983.json",
            "filingUrl": "https://www.sec.gov/Archives/edgar/data/1067983/000119312514078778/",
            "accession": "0001193125-14-078778",
            "filed": "2014-03-03",
            "form": "10-K",
            "concept": "EarningsPerShareBasic",
            "unit": "USD/shares",
            "start": PERIOD["start"],
            "end": PERIOD["end"],
        },
        "failureReason": (
            "SEC CompanyFacts is keyed to the issuer CIK and exposes +2757.00 for BRK-A/BRK-B "
            "without a share-class dimension; this value matches BRK-A's basis and cannot be "
            "applied to BRK-B. BRK-B remains missing pending class-specific official evidence."
        ),
        "selectionPolicy": "D evidence is excluded from formal EPS, growth, TTM and P/E inputs",
    }


def apply(path=FILE, sidecar=SIDECAR):
    document = read_json(path)
    q = document.get("quarterly") or {}
    matches = [i for i, (fy, end, fq) in enumerate(zip(q.get("fy") or [], q.get("end") or [], q.get("fq") or []))
               if fy == PERIOD["fiscalYear"] and end == PERIOD["end"] and fq == 4]
    if len(matches) != 1:
        raise RuntimeError("BRK-B FY2012 Q4 period is not unique")
    index = matches[0]
    values = q.get("values") or {}
    column = values.get("epsBasic") or []
    current = column[index] if index < len(column) else None
    trace = ((document.get("secQ4EpsFacts") or {}).get(PERIOD["end"]) or {}).get("epsBasic") or {}
    if current not in (None, REPORTED) or (trace and trace.get("cik") != CIK):
        raise RuntimeError(f"BRK-B EPS changed since class review: value={current}, trace={trace}")
    while len(column) < len(q.get("end") or []):
        column.append(None)
    column[index] = None
    values["epsBasic"] = column
    q["values"] = values
    grades = q.get("epsQuality")
    if not isinstance(grades, list):
        grades = [None] * len(q.get("end") or [])
    while len(grades) < len(q.get("end") or []):
        grades.append(None)
    grade = grades[index] if isinstance(grades[index], dict) else {}
    grade["epsBasic"] = "D"
    grades[index] = grade
    q["epsQuality"] = grades
    document["quarterly"] = q
    trace_map = document.get("secQ4EpsFacts") or {}
    if PERIOD["end"] in trace_map and isinstance(trace_map[PERIOD["end"]].get("epsBasic"), dict):
        trace_map[PERIOD["end"]]["epsBasic"]["grade"] = "D"
        trace_map[PERIOD["end"]]["epsBasic"]["validation"] = {
            "passed": False,
            "failureReason": "SEC companyfacts fact cannot distinguish Berkshire common share classes",
        }
    document["secQ4EpsFacts"] = trace_map
    write_atomic(str(path), document)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    temp = sidecar.with_name(sidecar.name + ".tmp")
    with temp.open("w", encoding="utf-8") as output:
        output.write(json.dumps(record(), ensure_ascii=False, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp, sidecar)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="restore BRK-B FY2012 Q4 Basic EPS to missing and store D evidence")
    args = parser.parse_args()
    if args.apply:
        apply()
        print("BRK-B FY2012 Q4 Basic EPS remains missing; ambiguous issuer-class fact quarantined as D.")
    else:
        print(json.dumps(record(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

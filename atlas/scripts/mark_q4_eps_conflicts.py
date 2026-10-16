#!/usr/bin/env python3
"""Quarantine the unresolved PRPL FY2023 SEC EPS version/sign conflict as grade D."""
import argparse
import json
import os
from pathlib import Path

from build_valuation_us import read_json, write_atomic

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "docs/report/financials/PRPL.json"
SIDECAR = ROOT / "docs/report/financials/q4-eps-prpl-conflict-D.jsonl"
CIK = "0001643953"


def record():
    return {
        "schema": "q4-eps-evidence-staging-v1",
        "symbol": "PRPL",
        "cik": CIK,
        "period": {"fiscalYear": 2023, "start": "2023-01-01", "end": "2023-12-31"},
        "field": ["epsBasic", "epsDiluted"],
        "currency": "USD",
        "unit": "USD/shares",
        "grade": "D",
        "status": "quarantined-unresolved-sec-version-conflict",
        "method": "official-filing-version-investigation",
        "storedValue": 29.25,
        "sourceVersions": [
            {
                "provider": "SEC EDGAR 10-K Inline XBRL",
                "url": "https://www.sec.gov/Archives/edgar/data/1643953/000121390024021861/f10k2023_purpleinnov.htm",
                "cik": CIK,
                "accession": "0001213900-24-021861",
                "filed": "2024-03-12",
                "form": "10-K",
                "fiscalPeriod": {"start": "2023-01-01", "end": "2023-12-31"},
                "concepts": {"epsBasic": "EarningsPerShareBasic", "epsDiluted": "EarningsPerShareDiluted"},
                "reportedValue": -1.17,
                "netIncome": -120757000,
                "weightedAverageSharesBasic": 103602000,
                "weightedAverageSharesDiluted": 103936000,
            },
            {
                "provider": "SEC EDGAR 10-K comparative Inline XBRL",
                "url": "https://www.sec.gov/Archives/edgar/data/1643953/000121390026036974/ea0274767-10k_purple.htm",
                "cik": CIK,
                "accession": "0001213900-26-036974",
                "filed": "2026-03-31",
                "form": "10-K",
                "fiscalPeriod": {"start": "2023-01-01", "end": "2023-12-31"},
                "concepts": {"epsBasic": "EarningsPerShareBasic", "epsDiluted": "EarningsPerShareDiluted"},
                "reportedValue": 1.17,
                "netIncome": -120757000,
                "weightedAverageSharesBasic": 103602000,
                "weightedAverageSharesDiluted": 103936000,
            },
        ],
        "splitBasis": {
            "fileEvent": ["2026-07-20", 0.04],
            "storedTransformation": "reported comparative EPS +1.17 / 0.04 = +29.25",
            "olderFilingPostSplitValueWouldBe": -29.25,
            "decision": "do not choose either conflicting filing value automatically",
        },
        "failureReason": "later official filing reports positive EPS despite negative common net income; earlier official filing reports negative EPS; no resolution source in current evidence",
        "selectionPolicy": "D grade is excluded from formal growth and P/E inputs",
    }


def apply(path=FILE, sidecar=SIDECAR):
    document = read_json(path)
    annual = document.get("annual") or {}
    values = annual.get("values") or {}
    fy_values = annual.get("fy") or []
    indexes = [i for i, fy in enumerate(fy_values) if fy == 2023]
    if len(indexes) != 1:
        raise RuntimeError("PRPL 2023 annual period is not unique")
    index = indexes[0]
    for field in ("epsBasic", "epsDiluted"):
        column = values.get(field) or []
        if index >= len(column) or column[index] != 29.25:
            raise RuntimeError(f"PRPL {field} stored value changed; expected 29.25, found {column[index] if index < len(column) else None}")
    grades = annual.get("epsQuality")
    if not isinstance(grades, list):
        grades = [None] * len(annual.get("end") or [])
    while len(grades) < len(annual.get("end") or []):
        grades.append(None)
    quality = grades[index] if isinstance(grades[index], dict) else {}
    quality.update(epsBasic="D", epsDiluted="D")
    grades[index] = quality
    annual["epsQuality"] = grades
    document["annual"] = annual
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
    parser.add_argument("--apply", action="store_true", help="mark the existing PRPL FY2023 EPS cells as D; values are preserved")
    args = parser.parse_args()
    if args.apply:
        apply()
        print("PRPL FY2023 EPS values preserved and marked grade D; evidence written to " + str(SIDECAR))
    else:
        print(json.dumps(record(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

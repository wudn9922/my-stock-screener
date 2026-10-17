#!/usr/bin/env python3
"""Reconcile manually reviewed SEC filing spot-checks against stored Q4 EPS.

The source rows are curated from the visible GAAP Q4 tables in the named SEC filing.
Raw source hash and period alignment are gates. Net-income/share comparisons are diagnostics only.
The apply path fills nulls and never overwrites an existing EPS value.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import repair_q4_eps as repair  # noqa: E402
import seed_financials_sec as sec  # noqa: E402
from build_valuation_us import read_json, write_atomic  # noqa: E402

FINANCIALS = ROOT / "docs/report/financials"
CACHE = ROOT / ".cache/q4-eps-spot-check"
REPORT = FINANCIALS / "q4-eps-spot-check-reconciliations.json"
LEDGER = FINANCIALS / "q4-eps-spot-check-corrections.jsonl"


def _source(cik, accession, filed, form, url, sha256, cache_file, evidence):
    return {
        "provider": "SEC EDGAR filed GAAP Q4 disclosure",
        "cik": cik,
        "accession": accession,
        "filed": filed,
        "form": form,
        "url": url,
        "filingUrl": url.rsplit("/", 1)[0] + "/",
        "filingSha256": sha256,
        "cacheFile": cache_file,
        "currency": "USD",
        "unit": "USD/shares",
        "evidence": evidence,
    }


# Values are from the Q4 column, with the adjacent table heading included in evidence.
# Tesla's release publishes diluted EPS only; Walmart's headline GAAP EPS is explicitly
# the reported diluted EPS row. No Basic value is inferred where the filing omits one.
DIRECT_Q4 = [
    {"symbol": "NFLX", "fiscalYear": 2025, "periodEnd": "2025-12-31", "field": "epsBasic", "value": 0.57,
     "source": _source("0001065280", "0001065280-26-000034", "2026-01-23", "10-K",
                        "https://www.sec.gov/Archives/edgar/data/1065280/000106528026000034/nflx-20251231.htm",
                        "bead2c9dd4b4ec4e9b25e67faaf1bc395e28a71169d2069522840922e5a1dec3",
                        "NFLX/000106528026000034.html",
                        "Quarterly financial data, three months ended 2025-12-31: Earnings per share, Basic $0.57.")},
    {"symbol": "NFLX", "fiscalYear": 2025, "periodEnd": "2025-12-31", "field": "epsDiluted", "value": 0.56,
     "source": _source("0001065280", "0001065280-26-000034", "2026-01-23", "10-K",
                        "https://www.sec.gov/Archives/edgar/data/1065280/000106528026000034/nflx-20251231.htm",
                        "bead2c9dd4b4ec4e9b25e67faaf1bc395e28a71169d2069522840922e5a1dec3",
                        "NFLX/000106528026000034.html",
                        "Quarterly financial data, three months ended 2025-12-31: Earnings per share, Diluted $0.56.")},
    {"symbol": "GOOGL", "fiscalYear": 2025, "periodEnd": "2025-12-31", "field": "epsBasic", "value": 2.85,
     "source": _source("0001652044", "0001652044-26-000012", "2026-02-04", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000012/googexhibit991q42025.htm",
                        "1112358a5579dd7762a171869d503549062f4254964d84d3d4fed8b2a6e9155e",
                        "GOOGL/000165204426000012-googexhibit991q42025.htm",
                        "Q4 2025 earnings release, Basic net income per share, Q4 2025 column: $2.85.")},
    {"symbol": "GOOGL", "fiscalYear": 2025, "periodEnd": "2025-12-31", "field": "epsDiluted", "value": 2.82,
     "source": _source("0001652044", "0001652044-26-000012", "2026-02-04", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000012/googexhibit991q42025.htm",
                        "1112358a5579dd7762a171869d503549062f4254964d84d3d4fed8b2a6e9155e",
                        "GOOGL/000165204426000012-googexhibit991q42025.htm",
                        "Q4 2025 earnings release, Diluted net income per share, Q4 2025 column: $2.82.")},
    {"symbol": "TSLA", "fiscalYear": 2025, "periodEnd": "2025-12-31", "field": "epsDiluted", "value": 0.24,
     "source": _source("0001318605", "0001628280-26-003837", "2026-01-28", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/1318605/000162828026003837/exhibit991.htm",
                        "4d2bcac33a96c6d926def01442e26c00feda958696005eb242f14bc8303890dc",
                        "TSLA/000162828026003837-exhibit991.htm",
                        "2025 Q4 release reconciliation: EPS attributable to common stockholders, diluted (GAAP), Q4 2025 column: $0.24.")},
    {"symbol": "NVDA", "fiscalYear": 2026, "periodEnd": "2026-01-25", "field": "epsBasic", "value": 1.77,
     "source": _source("0001045810", "0001045810-26-000021", "2026-02-25", "10-K",
                        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm",
                        "29d583f43663825e68b1b45d4a53feb8ffa91ad83642f14ea196e3facf5f8c1f",
                        "NVDA/000104581026000021.html",
                        "FY2026 quarterly data, Q4 ended 2026-01-25: Net income per share, Basic $1.77.")},
    {"symbol": "NVDA", "fiscalYear": 2026, "periodEnd": "2026-01-25", "field": "epsDiluted", "value": 1.76,
     "source": _source("0001045810", "0001045810-26-000021", "2026-02-25", "10-K",
                        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm",
                        "29d583f43663825e68b1b45d4a53feb8ffa91ad83642f14ea196e3facf5f8c1f",
                        "NVDA/000104581026000021.html",
                        "FY2026 quarterly data, Q4 ended 2026-01-25: Net income per share, Diluted $1.76.")},
    {"symbol": "AMZN", "fiscalYear": 2025, "periodEnd": "2025-12-31", "field": "epsBasic", "value": 1.98,
     "source": _source("0001018724", "0001018724-26-000002", "2026-02-05", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/1018724/000101872426000002/amzn-20251231xex991.htm",
                        "71b0437a9c5b0acc35b5e2b465f52ccd64ec17809111bdf4675b3be61f873238",
                        "AMZN/000101872426000002-amzn-20251231xex991.htm",
                        "2025 Q4 earnings release, Basic earnings per share, Q4 2025 column: $1.98.")},
    {"symbol": "AMZN", "fiscalYear": 2025, "periodEnd": "2025-12-31", "field": "epsDiluted", "value": 1.95,
     "source": _source("0001018724", "0001018724-26-000002", "2026-02-05", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/1018724/000101872426000002/amzn-20251231xex991.htm",
                        "71b0437a9c5b0acc35b5e2b465f52ccd64ec17809111bdf4675b3be61f873238",
                        "AMZN/000101872426000002-amzn-20251231xex991.htm",
                        "2025 Q4 earnings release, Diluted earnings per share, Q4 2025 column: $1.95.")},
    {"symbol": "WMT", "fiscalYear": 2026, "periodEnd": "2026-01-31", "field": "epsDiluted", "value": 0.53,
     "source": _source("0000104169", "0000104169-26-000032", "2026-02-19", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/104169/000010416926000032/earningsreleasefy26q4.htm",
                        "fb9337a16b1a0d8992efcec6895ad22764296ef8645d02fb9e9fd637c52f752b",
                        "WMT/000010416926000032-earningsreleasefy26q4.htm",
                        "Three months ended 2026-01-31, Diluted earnings per share, Reported EPS: $0.53.")},
    {"symbol": "AMD", "fiscalYear": 2025, "periodEnd": "2025-12-27", "field": "epsBasic", "value": 0.93,
     "source": _source("0000002488", "0000002488-26-000014", "2026-02-03", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/2488/000000248826000014/q42025991.htm",
                        "19a285df3c0443e9831c2a25237a5283a4906f6a9c3ce232aa269eb99d740465",
                        "AMD/000000248826000014-q42025991.htm",
                        "Q4 2025 selected financial data, GAAP statement EPS table: Basic earnings per share $0.93.")},
    {"symbol": "AMD", "fiscalYear": 2025, "periodEnd": "2025-12-27", "field": "epsDiluted", "value": 0.92,
     "source": _source("0000002488", "0000002488-26-000014", "2026-02-03", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/2488/000000248826000014/q42025991.htm",
                        "19a285df3c0443e9831c2a25237a5283a4906f6a9c3ce232aa269eb99d740465",
                        "AMD/000000248826000014-q42025991.htm",
                        "Q4 2025 selected financial data, GAAP statement EPS table: Diluted earnings per share $0.92.")},
    {"symbol": "AVGO", "fiscalYear": 2025, "periodEnd": "2025-11-02", "field": "epsBasic", "value": 1.80,
     "source": _source("0001730168", "0001730168-25-000116", "2025-12-11", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/1730168/000173016825000116/avgo-11022025x8kxex99.htm",
                        "d970cf95fbb95758caf3d7105167650585981f2434f80d5023c9263a9d74cb48",
                        "AVGO/000173016825000116-avgo-11022025x8kxex99.htm",
                        "Fiscal Q4 2025 results table, Basic income per share, net income per share: $1.80.")},
    {"symbol": "AVGO", "fiscalYear": 2025, "periodEnd": "2025-11-02", "field": "epsDiluted", "value": 1.74,
     "source": _source("0001730168", "0001730168-25-000116", "2025-12-11", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/1730168/000173016825000116/avgo-11022025x8kxex99.htm",
                        "d970cf95fbb95758caf3d7105167650585981f2434f80d5023c9263a9d74cb48",
                        "AVGO/000173016825000116-avgo-11022025x8kxex99.htm",
                        "Fiscal Q4 2025 results table, Diluted income per share, net income per share: $1.74.")},
    {"symbol": "CMG", "fiscalYear": 2025, "periodEnd": "2025-12-31", "field": "epsBasic", "value": 0.25,
     "source": _source("0001058090", "0001058090-26-000007", "2026-02-03", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/1058090/000105809026000007/cmg-20260203xex991.htm",
                        "a995a780db6fb4ab3decb05dbc82e727721a133abb5719139a133ac3013c35c1",
                        "CMG/000105809026000007-cmg-20260203xex991.htm",
                        "Q4 2025 results statement, Earnings per share, Basic: $0.25.")},
    {"symbol": "CMG", "fiscalYear": 2025, "periodEnd": "2025-12-31", "field": "epsDiluted", "value": 0.25,
     "source": _source("0001058090", "0001058090-26-000007", "2026-02-03", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/1058090/000105809026000007/cmg-20260203xex991.htm",
                        "a995a780db6fb4ab3decb05dbc82e727721a133abb5719139a133ac3013c35c1",
                        "CMG/000105809026000007-cmg-20260203xex991.htm",
                        "Q4 2025 results statement, Earnings per share, Diluted: $0.25.")},
    {"symbol": "SMCI", "fiscalYear": 2026, "periodEnd": "2026-06-30", "field": "epsBasic", "value": 1.83,
     "source": _source("0001375365", "0001375365-26-000021", "2026-08-11", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/1375365/000137536526000021/exhibit991_20260630.htm",
                        "4982db31739471e0d33394c8467375bdeb23928b521e8b6a77e855cc01bdd3c4",
                        "SMCI/000137536526000021-exhibit991_20260630.htm",
                        "Three months ended 2026-06-30, GAAP net income per common share - basic: $1.83.")},
    {"symbol": "SMCI", "fiscalYear": 2026, "periodEnd": "2026-06-30", "field": "epsDiluted", "value": 1.62,
     "source": _source("0001375365", "0001375365-26-000021", "2026-08-11", "8-K",
                        "https://www.sec.gov/Archives/edgar/data/1375365/000137536526000021/exhibit991_20260630.htm",
                        "4982db31739471e0d33394c8467375bdeb23928b521e8b6a77e855cc01bdd3c4",
                        "SMCI/000137536526000021-exhibit991_20260630.htm",
                        "Three months ended 2026-06-30, GAAP net income per common share - diluted: $1.62.")},
]


def _column_value(section, field, index):
    values = (section.get("values") or {}).get(field) or []
    return values[index] if index < len(values) else None


def validate_records(directory=FINANCIALS, cache=CACHE):
    checked = []
    failures = []
    for row in DIRECT_Q4:
        doc_path = Path(directory) / f"{row['symbol']}.json"
        doc = read_json(doc_path)
        q = doc.get("quarterly") or {}
        matches = [i for i, (fy, end, fq) in enumerate(zip(q.get("fy") or [], q.get("end") or [], q.get("fq") or []))
                   if fq == 4 and fy == row["fiscalYear"] and end == row["periodEnd"]]
        item = {**row, "financialFile": f"docs/report/financials/{row['symbol']}.json"}
        if len(matches) != 1:
            item["validation"] = {"passed": False, "failureReason": "q4-period-not-unique"}
            failures.append(item)
            checked.append(item)
            continue
        index = matches[0]
        source = row["source"]
        body_path = Path(cache) / source["cacheFile"]
        if not body_path.exists():
            item["validation"] = {"passed": False, "failureReason": "source-cache-missing"}
            failures.append(item)
            checked.append(item)
            continue
        body = body_path.read_bytes()
        if body[:2] == b"\x1f\x8b":
            import gzip
            body = gzip.decompress(body)
        actual_hash = hashlib.sha256(body).hexdigest()
        if actual_hash != source["filingSha256"]:
            item["validation"] = {"passed": False, "failureReason": "source-hash-mismatch",
                                  "expectedSha256": source["filingSha256"], "actualSha256": actual_hash}
            failures.append(item)
            checked.append(item)
            continue
        income = _column_value(q, "netIncome", index)
        q3 = [i for i, (fy, fq) in enumerate(zip(q.get("fy") or [], q.get("fq") or []))
              if fy == row["fiscalYear"] and fq == 3]
        q3_shares = _column_value(q, "dilutedShares", q3[0]) if len(q3) == 1 else None
        q3_end = (q.get("end") or [None] * len(q.get("fq") or []))[q3[0]] if len(q3) == 1 else None
        try:
            start = (date.fromisoformat(q3_end) + timedelta(days=1)).isoformat()
            duration = (date.fromisoformat(row["periodEnd"]) - date.fromisoformat(start)).days + 1
            if not 80 <= duration <= 100:
                start, duration = None, None
        except (TypeError, ValueError):
            start, duration = None, None
        adjusted, split_factor, problem = repair.normalize_and_check_q4_eps(
            doc, row["periodEnd"], income, q3_shares,
            {"value": row["value"], "filed": source.get("filed")})
        if problem is None and (start is None or duration is None):
            problem = "q4-period-duration-not-80-to-100-days"
        old = _column_value(q, row["field"], index)
        grade_rows = q.get("epsQuality") or []
        old_grade = grade_rows[index].get(row["field"]) if index < len(grade_rows) and isinstance(grade_rows[index], dict) else None
        trace = (doc.get("secQ4EpsFacts") or {}).get(row["periodEnd"], {}).get(row["field"])
        if problem is None and isinstance(trace, dict) and trace.get("grade") == "A":
            filed = str(trace.get("filed") or "")
            if filed > str(source["filed"] or ""):
                problem = "newer-existing-grade-A-source"
        diagnostic = repair.q4_eps_diagnostics(adjusted, income, q3_shares) if adjusted is not None else None
        manual_review = bool(diagnostic and diagnostic.get("flags"))
        if problem is None and manual_review:
            problem = "q4-net-income-share-diagnostic-needs-source-review"
        item.update({"oldValue": old, "oldGrade": old_grade, "q4NetIncome": income,
                     "q3DilutedShares": q3_shares, "impliedEps": (income / q3_shares if income and q3_shares else None),
                     "periodStart": start, "durationDays": duration,
                     "splitFactor": split_factor, "adjustedValue": adjusted,
                     "validation": {"passed": problem is None, "failureReason": problem,
                                    "rawDocumentHashVerified": True,
                                    "manualReviewRequired": manual_review,
                                    "diagnostic": diagnostic}})
        if problem:
            failures.append(item)
        checked.append(item)
    return checked, failures


def apply_records(records, directory=FINANCIALS, dry_run=False):
    """Fill only null EPS values after all raw source hashes and periods have been checked."""
    if any(not (row.get("validation") or {}).get("passed") for row in records):
        raise RuntimeError("official spot-check validation failed; financial files were not changed")
    ledger = []
    by_symbol = {}
    for row in records:
        doc_path = Path(directory) / f"{row['symbol']}.json"
        doc = by_symbol.setdefault(row["symbol"], read_json(doc_path))
        q = doc["quarterly"]
        index = next(i for i, (fy, end, fq) in enumerate(zip(q["fy"], q["end"], q["fq"]))
                     if fq == 4 and fy == row["fiscalYear"] and end == row["periodEnd"])
        values = q.setdefault("values", {})
        column = values.setdefault(row["field"], [None] * len(q["end"]))
        while len(column) < len(q["end"]):
            column.append(None)
        old, new = column[index], row["adjustedValue"]
        if old is not None:
            # Existing values are immutable in this workflow, even if a filing candidate differs.
            continue
        if new is None:
            raise RuntimeError(f"refusing to write null normalized EPS: {row['symbol']} {row['periodEnd']}")
        grade_rows = q.setdefault("epsQuality", [None] * len(q["end"]))
        while len(grade_rows) < len(q["end"]):
            grade_rows.append(None)
        grade = grade_rows[index] if isinstance(grade_rows[index], dict) else {}
        if column[index] is not None:
            raise RuntimeError(f"refusing to overwrite non-null EPS during apply: {row['symbol']} {row['periodEnd']} {row['field']}")
        column[index] = new
        values[row["field"]] = column
        grade[row["field"]] = "A"
        grade_rows[index] = grade
        trace = doc.setdefault("secQ4EpsFacts", {}).setdefault(row["periodEnd"], {})
        trace[row["field"]] = {
            "concept": "EarningsPerShareBasic" if row["field"] == "epsBasic" else "EarningsPerShareDiluted",
            "unit": "USD/shares", "start": row["periodStart"], "end": row["periodEnd"],
            "durationDays": row["durationDays"],
            "form": row["source"]["form"], "filed": row["source"]["filed"],
            "accession": row["source"]["accession"], "reportedValue": row["value"],
            "splitFactor": row["splitFactor"], "value": new, "currency": "USD", "grade": "A",
            "method": "direct-sec-quarterly-data-table", "cik": row["source"]["cik"],
            "url": row["source"]["url"], "filingUrl": row["source"]["filingUrl"],
            "version": {"accession": row["source"]["accession"], "filed": row["source"]["filed"],
                        "form": row["source"]["form"]},
            "evidence": row["source"]["evidence"], "filingSha256": row["source"]["filingSha256"],
            "validation": row["validation"],
        }
    for symbol, doc in by_symbol.items():
        if not dry_run:
            write_atomic(str(Path(directory) / f"{symbol}.json"), doc)
    return ledger


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=FINANCIALS)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    rows, failures = validate_records(args.dir, args.cache)
    report = {"schema": "q4-eps-spot-check-reconciliation-v2",
              "status": "validation-failed-no-write" if failures else "validated",
              "count": len(rows), "validationFailures": len(failures), "records": rows,
              "selectionGate": "SEC source hash exact; unique matching Q4 period; official GAAP field and table row reviewed; NI/share comparison is diagnostic only",
              "sourceMethod": "manually reviewed visible GAAP row and Q4 column in SEC 10-K/8-K exhibit"}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if failures:
        print(json.dumps({"status": report["status"], "count": len(rows),
                          "failures": [{"symbol": r["symbol"], "periodEnd": r["periodEnd"],
                                        "field": r["field"], "reason": r["validation"].get("failureReason"),
                                        "q4NetIncome": r.get("q4NetIncome"),
                                        "q3DilutedShares": r.get("q3DilutedShares"),
                                        "reportedValue": r["value"]} for r in failures]}, ensure_ascii=False))
        return 2
    ledger = []
    if args.apply:
        ledger = apply_records(rows, args.dir)
        if ledger:
            existing = args.ledger.read_text(encoding="utf-8") if args.ledger.exists() else ""
            existing_keys = {(item.get("symbol"), (item.get("period") or {}).get("end"), item.get("field"))
                             for item in (json.loads(line) for line in existing.splitlines() if line.strip())}
            with args.ledger.open("a", encoding="utf-8") as stream:
                for item in ledger:
                    key = (item["symbol"], item["period"]["end"], item["field"])
                    if key not in existing_keys:
                        stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
                        existing_keys.add(key)
    report["status"] = "applied" if args.apply else "validated"
    report["correctedExistingValues"] = len(ledger)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "count": len(rows),
                      "correctedExistingValues": len(ledger), "validationFailures": 0,
                      "report": str(args.report), "ledger": str(args.ledger)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Offline, fail-closed audit of possible grade-B Basic Q4 EPS reconstructions."""
import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

from build_valuation_us import read_json
import fetch_q4_eps_companyfacts as fetcher

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "docs/report/financials"
DEFAULT_CACHE = ROOT / ".cache/q4-eps-evidence"
DEFAULT_REPORT = ROOT / "docs/report/financials/q4-eps-basic-B-audit.json"
DEFAULT_DECISIONS = ROOT / "docs/report/financials/q4-eps-basic-B-decisions.jsonl"
COMMON_INCOME = "NetIncomeLossAvailableToCommonStockholdersBasic"
BASIC_SHARES = "WeightedAverageNumberOfSharesOutstandingBasic"


def _facts(company, concept, unit):
    return [item for item in ((company or {}).get("concepts") or {}).get(concept, [])
            if item.get("unit") == unit]


def _matching(rows, start, end, forms):
    forms = {value.upper() for value in forms}
    return [item for item in rows if item.get("start") == start and item.get("end") == end
            and str(item.get("form") or "").upper() in forms
            and item.get("filed") and item.get("accession")]


def _for_end(rows, end, forms):
    forms = {value.upper() for value in forms}
    return [item for item in rows if item.get("end") == end
            and str(item.get("form") or "").upper() in forms
            and item.get("start") and item.get("filed") and item.get("accession")]


def _one_version(rows):
    if not rows:
        return None, "missing"
    latest_filed = max(str(row["filed"]) for row in rows)
    latest = [row for row in rows if str(row["filed"]) == latest_filed]
    latest_accession = sorted(str(row["accession"]) for row in latest)[-1]
    latest = [row for row in latest if str(row["accession"]) == latest_accession]
    values = {str(row.get("value")) for row in latest}
    if len(values) != 1:
        return None, "conflicting-same-version"
    return latest[0], None


def scan(financials_dir=DEFAULT_DIR, cache_dir=DEFAULT_CACHE):
    financials_dir, cache_dir = Path(financials_dir), Path(cache_dir)
    state_path = cache_dir / "state.json"
    state = read_json(state_path) if state_path.exists() else {}
    records, reasons, metrics = [], Counter(), Counter()
    by_year = {}
    for path in sorted(financials_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            doc = read_json(path)
        except (OSError, ValueError):
            continue
        if doc.get("market") != "US":
            continue
        q = doc.get("quarterly") or {}
        vals = q.get("values") or {}
        grades = q.get("epsQuality") or []
        metrics["currently_stored_grade_B_basic_q4"] += sum(
            1 for index, fq in enumerate(q.get("fq") or [])
            if fq == 4 and index < len(grades) and isinstance(grades[index], dict)
            and grades[index].get("epsBasic") == "B"
            and index < len(vals.get("epsBasic") or [])
            and vals["epsBasic"][index] is not None)
        signature = fetcher._signature(doc)
        saved = (state.get("companies") or {}).get(path.stem) or {}
        company_path = cache_dir / "facts" / f"{path.stem}.json"
        company = None
        if (saved.get("status") == "ok" and saved.get("signature") == signature
                and company_path.exists()):
            company = read_json(company_path)
        for index, (fy, fq, end) in enumerate(zip(q.get("fy") or [], q.get("fq") or [], q.get("end") or [])):
            basic = (vals.get("epsBasic") or [])
            value = basic[index] if index < len(basic) else None
            grade = grades[index].get("epsBasic") if index < len(grades) and isinstance(grades[index], dict) else None
            if fq != 4 or (value is not None and grade not in {"C", "D"}):
                continue
            metrics["basic_q4_missing_or_quarantined"] += 1
            counter = by_year.setdefault(str(fy), Counter())
            counter["target"] += 1
            annual_candidates = _for_end(_facts(company, COMMON_INCOME, "USD"), end, {"10-K", "10-K/A"})
            if not annual_candidates:
                reason = "no-companyfacts-cache" if company is None else "no-annual-common-income-fact"
                reasons[reason] += 1
                counter["incompleteInputSets"] += 1
                records.append({"symbol": path.stem, "cik": (company or {}).get("cik"),
                                "period": {"fiscalYear": fy, "end": end}, "field": "epsBasic",
                                "grade": "D", "status": "retained-missing", "method": "basic-eps-actual-day-reconstruction",
                                "failureReason": reason, "source": None})
                continue
            # Annual fact start is the authoritative FY start; the financials file stores only period end.
            starts = sorted({row.get("start") for row in annual_candidates if row.get("start")})
            annual_start = starts[-1] if starts else None
            income_annual, annual_problem = _one_version([row for row in annual_candidates if row.get("start") == annual_start])
            qrows = [i for i, (qfy, qfq) in enumerate(zip(q.get("fy") or [], q.get("fq") or []))
                     if qfy == fy and qfq == 3]
            q3_end = q["end"][qrows[0]] if len(qrows) == 1 else None
            annual_share_rows = _matching(_facts(company, BASIC_SHARES, "shares"), annual_start, end, {"10-K", "10-K/A"})
            nine_income_rows = _matching(_facts(company, COMMON_INCOME, "USD"), annual_start, q3_end, {"10-Q", "10-Q/A"}) if q3_end else []
            nine_share_rows = _matching(_facts(company, BASIC_SHARES, "shares"), annual_start, q3_end, {"10-Q", "10-Q/A"}) if q3_end else []
            annual_cutoff = str((income_annual or {}).get("filed") or "9999-12-31")
            later_nine_filing_exists = any(str(row.get("filed")) > annual_cutoff
                                           for row in (*nine_income_rows, *nine_share_rows))
            nine_income_rows = [row for row in nine_income_rows if str(row.get("filed")) <= annual_cutoff]
            nine_share_rows = [row for row in nine_share_rows if str(row.get("filed")) <= annual_cutoff]
            annual_shares, annual_share_problem = _one_version(annual_share_rows)
            nine_income, nine_income_problem = _one_version(nine_income_rows)
            nine_shares, nine_share_problem = _one_version(nine_share_rows)
            reasons_for_row = []
            if annual_problem:
                reasons_for_row.append("annual-common-income-" + annual_problem)
            if annual_shares is None:
                reasons_for_row.append("annual-basic-shares-" + (annual_share_problem or "missing"))
            if nine_income is None:
                reasons_for_row.append("nine-month-common-income-" + (nine_income_problem or "missing"))
            if nine_shares is None:
                reasons_for_row.append("nine-month-basic-shares-" + (nine_share_problem or "missing"))
            if (income_annual is not None and annual_shares is not None
                    and (income_annual.get("accession"), income_annual.get("filed"), income_annual.get("form")) !=
                    (annual_shares.get("accession"), annual_shares.get("filed"), annual_shares.get("form"))):
                reasons_for_row.append("annual-income-share-filing-version-mismatch")
            if (nine_income is not None and nine_shares is not None
                    and (nine_income.get("accession"), nine_income.get("filed"), nine_income.get("form")) !=
                    (nine_shares.get("accession"), nine_shares.get("filed"), nine_shares.get("form"))):
                reasons_for_row.append("nine-month-income-share-filing-version-mismatch")
            if not nine_income and not nine_shares and later_nine_filing_exists:
                reasons_for_row.append("nine-month-filing-after-annual-version")
            inputs_complete = not reasons_for_row and all((income_annual, annual_shares, nine_income, nine_shares))
            if inputs_complete:
                metrics["matching_common_income_and_basic_share_input_sets"] += 1
                reasons_for_row.extend([
                    "noncontrolling-interest-treatment-not-verified-from-companyfacts",
                    "preferred-dividend-treatment-not-verified-from-context",
                    "common-share-class-count-not-exposed-by-companyfacts",
                    "annual-and-nine-month-split-basis-not-provable-from-entity-wide-facts",
                ])
            reason = ";".join(reasons_for_row) if reasons_for_row else "incomplete-official-inputs"
            reasons[reason] += 1
            sources = {}
            for label, fact in (("annualIncome", income_annual), ("annualBasicShares", annual_shares),
                                ("nineMonthIncome", nine_income), ("nineMonthBasicShares", nine_shares)):
                if fact:
                    sources[label] = {key: fact.get(key) for key in
                                      ("concept", "unit", "value", "start", "end", "form", "filed", "accession")}
            record = {"symbol": path.stem, "cik": (company or {}).get("cik"),
                      "period": {"fiscalYear": fy, "end": end}, "field": "epsBasic",
                      "grade": "D", "status": "retained-missing",
                      "method": "basic-eps-actual-day-reconstruction",
                      "failureReason": reason, "availableInputs": sources or None}
            records.append(record)
            counter["completeInputSets" if inputs_complete else "incompleteInputSets"] += 1
    metrics["records"] = len(records)
    metrics["companyFactsOnlyStrictGradeBValidated"] = 0
    return records, reasons, metrics, by_year


def write_reports(records, reasons, metrics, by_year, report_path, decisions_path):
    source_audit_path = Path(report_path).with_name("q4-eps-basic-B-source-audit.json")
    try:
        source_audit = read_json(source_audit_path)
    except (OSError, ValueError):
        source_audit = {}
    raw_source_b = int(source_audit.get("sourceValidatedGradeB", 0))
    report_status = ("companyfacts-only-screen-with-separately-validated-raw-filing-grade-Bs"
                     if raw_source_b else "companyfacts-only-screen-no-raw-filing-grade-Bs-recorded")
    report = {"schema": "q4-eps-basic-reconstruction-audit-v1",
              "status": report_status,
              "scope": "This report audits CompanyFacts-only inputs; raw SEC filing decisions are maintained in the companion source-audit report.",
              "rawFilingSourceValidatedGradeB": raw_source_b,
              "reason": "CompanyFacts-only input sets omit filing contexts needed to verify noncontrolling-interest and preferred treatment, share-class identity, and consistent split basis. The companion raw-filing audit separately approves only periods with direct source-context verification.",
              "metrics": dict(metrics),
              "failureReasons": dict(reasons),
              "byFiscalYear": {year: dict(values) for year, values in sorted(by_year.items())},
              "decisionFile": str(decisions_path)}
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    decisions_path = Path(decisions_path)
    with decisions_path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    args = parser.parse_args(argv)
    records, reasons, metrics, by_year = scan(args.dir, args.cache)
    write_reports(records, reasons, metrics, by_year, args.report, args.decisions)
    print(json.dumps({"status": "companyfacts-only-screen-complete", "metrics": dict(metrics),
                      "failureReasons": dict(reasons), "report": str(args.report),
                      "decisions": str(args.decisions)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

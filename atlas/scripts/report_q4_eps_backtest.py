#!/usr/bin/env python3
"""Report a stratified, masked backtest of annual-share Q4 diluted-EPS estimates."""
import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

SCRIPTS = str(Path(__file__).resolve().parent)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import audit_q4_eps_backfill as audit  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "docs/report/financials"
DEFAULT_REPORT = DEFAULT_DIR / "q4-eps-backtest.json"
DEFAULT_SUBGROUPS = DEFAULT_DIR / "q4-eps-backtest-subgroups.csv"
DEFAULT_YEARS = DEFAULT_DIR / "q4-eps-backtest-by-fiscal-year.csv"


def _float(value):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _metrics(rows):
    abs_errors = [row["absError"] for row in rows]
    normalized = [row["normalizedError"] for row in rows]
    sign_errors = sum(row["signError"] for row in rows)
    return {
        "n": len(rows),
        "maeUsdPerShare": sum(abs_errors) / len(rows) if rows else None,
        "p50AbsoluteErrorUsdPerShare": audit.percentile(abs_errors, 0.50),
        "p90AbsoluteErrorUsdPerShare": audit.percentile(abs_errors, 0.90),
        "p99AbsoluteErrorUsdPerShare": audit.percentile(abs_errors, 0.99),
        "p50NormalizedError": audit.percentile(normalized, 0.50),
        "p90NormalizedError": audit.percentile(normalized, 0.90),
        "p99NormalizedError": audit.percentile(normalized, 0.99),
        "signErrorCount": sign_errors,
        "signErrorRate": sign_errors / len(rows) if rows else None,
        "passesMedian2P90Six": bool(rows)
        and audit.percentile(normalized, 0.50) <= float(audit.METHOD_B_MEDIAN_MAX)
        and audit.percentile(normalized, 0.90) <= float(audit.METHOD_B_P90_MAX),
    }


def collect(financials_dir=DEFAULT_DIR):
    all_rows = []
    exclusions = defaultdict(int)
    direct_a_rows = []
    for path in sorted(Path(financials_dir).glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            exclusions["corrupt"] += 1
            continue
        if document.get("market") != "US":
            continue
        q = document.get("quarterly") or {}
        annual = document.get("annual") or {}
        annual_map = audit.annual_rows_by_fy(annual)
        q_map = audit.quarter_rows_by_fy(q)
        for index, (fy, fq) in enumerate(zip(q.get("fy") or [], q.get("fq") or [])):
            if fq != 4:
                continue
            actual = _float(audit.value(q, "epsDiluted", index))
            if actual is None:
                exclusions["missing-or-quarantined-q4-actual"] += 1
                continue
            annual_matches = annual_map.get(fy, [])
            q123_matches = [q_map.get(fy, {}).get(period, []) for period in (1, 2, 3)]
            q4_matches = q_map.get(fy, {}).get(4, [])
            if (len(annual_matches) != 1 or any(len(periods) != 1 for periods in q123_matches)
                    or len(q4_matches) != 1 or q4_matches[0] != index):
                exclusions["missing-or-ambiguous-annual-q123-period"] += 1
                continue
            ai = annual_matches[0]
            q123 = [periods[0] for periods in q123_matches]
            annual_shares_dec = audit._decimal(audit.value(annual, "dilutedShares", ai))
            qshares_dec = [audit._decimal(audit.value(q, "dilutedShares", qi)) for qi in q123]
            q4_income_dec = audit._decimal(audit.value(q, "netIncome", index))
            annual_income_dec = audit._decimal(audit.value(annual, "netIncome", ai))
            actual_dec = audit._decimal(actual)
            if (annual_shares_dec is None or annual_shares_dec <= 0 or q4_income_dec is None
                    or any(value is None or value <= 0 for value in qshares_dec)):
                exclusions["missing-or-invalid-income-share-input"] += 1
                continue
            q4_shares_dec = 4 * annual_shares_dec - sum(qshares_dec)
            if q4_shares_dec <= 0:
                exclusions["nonpositive-estimated-q4-shares"] += 1
                continue
            estimate_dec = q4_income_dec / q4_shares_dec
            range_ratio_dec = max(annual_shares_dec, *qshares_dec) / min(annual_shares_dec, *qshares_dec) - 1
            q3_deviation_dec = abs(q4_shares_dec / qshares_dec[2] - 1)
            estimate = float(estimate_dec)
            q4_income = float(q4_income_dec)
            annual_income = float(annual_income_dec) if annual_income_dec is not None else None
            qshares = [float(value) for value in qshares_dec]
            annual_shares = float(annual_shares_dec)
            q4_shares = float(q4_shares_dec)
            range_ratio = float(range_ratio_dec)
            q3_deviation = float(q3_deviation_dec)
            abs_error = abs(float(estimate_dec - actual_dec))
            normalized_error = float(abs(estimate_dec - actual_dec) / max(abs(actual_dec), audit.METHOD_B_ERROR_FLOOR))
            current = {
                "symbol": path.stem,
                "fiscalYear": fy,
                "end": (q.get("end") or [None] * (index + 1))[index],
                "estimate": estimate,
                "actual": actual,
                "absError": abs_error,
                "normalizedError": normalized_error,
                "signError": (estimate > 0) != (actual > 0) if estimate and actual else estimate != actual,
                "q4NetIncome": q4_income,
                "annualNetIncome": annual_income,
                "q4IncomeShare": abs(q4_income) / abs(annual_income) if annual_income else None,
                "shareRangeRatio": range_ratio,
                "q4Shares": q4_shares,
                "q3Shares": qshares[2],
                "q3DeviationRatio": q3_deviation,
                "grade": ((q.get("epsQuality") or [None] * (index + 1))[index] or {}).get("epsDiluted")
                if index < len(q.get("epsQuality") or []) and isinstance(q["epsQuality"][index], dict) else "ungraded",
                "splitAfterPeriod": any(
                    isinstance(event, (list, tuple)) and len(event) > 1 and str(event[0]) > str((q.get("end") or [""])[index])
                    for event in ((document.get("split") or {}).get("events") or [])),
            }
            current["strictSubgroup"] = (
                q4_income_dec > 0 and range_ratio_dec <= audit.METHOD_B_SHARE_RANGE_MAX
                and q4_shares_dec > 0 and q3_deviation_dec <= audit.METHOD_B_Q3_DEVIATION_MAX
            )
            all_rows.append(current)
            if current["grade"] == "A":
                direct_a_rows.append(current)
    return all_rows, direct_a_rows, exclusions


def _groups(rows):
    groups = {"all-formal-existing-eps": rows,
              "strict-positive-income-share-range-le2pct-q4shares-within-q3-15pct":
                  [row for row in rows if row["strictSubgroup"]],
              "grade-A-direct-only": [row for row in rows if row["grade"] == "A"],
              "legacy-ungraded-only": [row for row in rows if row["grade"] == "ungraded"]}
    groups["share-range-le2pct"] = [row for row in rows if row["shareRangeRatio"] <= 0.02]
    groups["share-range-2to5pct"] = [row for row in rows if 0.02 < row["shareRangeRatio"] <= 0.05]
    groups["share-range-5to10pct"] = [row for row in rows if 0.05 < row["shareRangeRatio"] <= 0.10]
    groups["share-range-gt10pct"] = [row for row in rows if row["shareRangeRatio"] > 0.10]
    groups["q4-income-positive"] = [row for row in rows if row["q4NetIncome"] > 0]
    groups["q4-income-negative"] = [row for row in rows if row["q4NetIncome"] < 0]
    groups["q4-income-zero"] = [row for row in rows if row["q4NetIncome"] == 0]
    groups["q4-income-share-le10pct"] = [row for row in rows if row["q4IncomeShare"] is not None and row["q4IncomeShare"] <= 0.10]
    groups["q4-income-share-gt10pct"] = [row for row in rows if row["q4IncomeShare"] is not None and row["q4IncomeShare"] > 0.10]
    groups["actual-eps-near-zero-abs-lt-0.10"] = [row for row in rows if abs(row["actual"]) < 0.10]
    groups["actual-eps-abs-ge-0.10"] = [row for row in rows if abs(row["actual"]) >= 0.10]
    groups["split-event-after-q4-end"] = [row for row in rows if row["splitAfterPeriod"]]
    groups["no-split-event-after-q4-end"] = [row for row in rows if not row["splitAfterPeriod"]]
    for fy in sorted({row["fiscalYear"] for row in rows}):
        groups[f"fiscal-year-{fy}"] = [row for row in rows if row["fiscalYear"] == fy]
    return groups


def report(financials_dir=DEFAULT_DIR, report_path=DEFAULT_REPORT,
           subgroup_csv=DEFAULT_SUBGROUPS, year_csv=DEFAULT_YEARS):
    rows, direct_a_rows, exclusions = collect(financials_dir)
    # Only SEC grade-A actuals are a verified target for the gate. Legacy values without
    # field-level source grades remain descriptive and cannot validate a reconstruction.
    groups = _groups(direct_a_rows)
    summaries = {name: _metrics(items) for name, items in groups.items()}
    summary = {
        "schema": "q4-eps-share-backtest-v1",
        "source": "SEC grade-A direct Q4 diluted EPS only; estimates are masked and recalculated from Q4 net income and annual/Q1-Q3 diluted shares",
        "errorDefinition": "absolute EPS error in USD/share; normalized error is abs(estimate-actual)/max(abs(actual), 0.10)",
        "verifiedGradeAActualSample": len(direct_a_rows),
        "legacyUngradedObservationsExcludedFromGate": len(rows) - len(direct_a_rows),
        "selectedForQuarantineOrFormalMethodB": 0,
        "subgroups": summaries,
        "exclusions": dict(exclusions),
        "notAvailableInFinancialsSnapshot": ["market-cap/company-size at period filing date", "IPO/SPAC flags", "filing-time point-in-time historical snapshots"],
        "interpretation": "This is an in-sample historical mask of direct SEC grade-A values only. The tested diluted-share formula is not accepted for formal grade B because the task requires basic-EPS reconstruction from common-attributable income and actual-day weighted basic shares. Legacy ungraded values are not used as verified targets.",
    }
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows_csv = [{"subgroup": name, **metrics} for name, metrics in summaries.items() if not name.startswith("fiscal-year-")]
    with Path(subgroup_csv).open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows_csv[0]))
        writer.writeheader()
        writer.writerows(rows_csv)
    year_rows = [{"fiscalYear": fy, **_metrics([x for x in direct_a_rows if x["fiscalYear"] == fy])}
                 for fy in sorted({row["fiscalYear"] for row in direct_a_rows})]
    with Path(year_csv).open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(year_rows[0]) if year_rows else ["fiscalYear"])
        writer.writeheader()
        writer.writerows(year_rows)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--subgroups", type=Path, default=DEFAULT_SUBGROUPS)
    parser.add_argument("--years", type=Path, default=DEFAULT_YEARS)
    args = parser.parse_args(argv)
    summary = report(args.dir, args.report, args.subgroups, args.years)
    print(json.dumps({"status": "reported", "sample": summary["verifiedGradeAActualSample"],
                      "strictSubgroup": summary["subgroups"]["strict-positive-income-share-range-le2pct-q4shares-within-q3-15pct"],
                      "report": str(args.report), "subgroups": str(args.subgroups), "years": str(args.years)},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

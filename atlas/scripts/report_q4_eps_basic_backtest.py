#!/usr/bin/env python3
"""Back-test actual-day Basic Q4 EPS reconstruction against SEC grade-A facts."""
import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import audit_q4_eps_basic_reconstruction as audit
import seed_financials_sec as sec

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "docs/report/financials"
DEFAULT_CACHE = ROOT / ".cache/q4-eps-evidence/facts"
DEFAULT_OUTPUT = ROOT / "docs/report/financials/q4-eps-basic-B-backtest.json"
DEFAULT_CSV = ROOT / "docs/report/financials/q4-eps-basic-B-backtest-subgroups.csv"
DEFAULT_SOURCE_DECISIONS = ROOT / "docs/report/financials/q4-eps-basic-B-source-decisions.jsonl"
SHARE_CONCEPT = audit.BASIC_SHARES


def _validated_grade_b_count(decisions_path=DEFAULT_SOURCE_DECISIONS):
    path = Path(decisions_path)
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
               for row in [json.loads(line)]
               if row.get("field") == "epsBasic" and row.get("grade") == "B"
               and row.get("status") == "validated")


def percentile(values, p):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    position = (len(values) - 1) * p
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _split_factor(document, filed):
    """Adjust raw share facts only for splits filed after the source filing was cut off."""
    factor = 1.0
    try:
        cutoff = date.fromisoformat(filed)
    except (TypeError, ValueError):
        return None
    for event in ((document.get("split") or {}).get("events") or []):
        if not isinstance(event, (list, tuple)) or len(event) < 2:
            continue
        try:
            day, ratio = date.fromisoformat(event[0]), float(event[1])
        except (TypeError, ValueError):
            continue
        if day > cutoff and (ratio >= sec.MIN_SPLIT_RATIO or ratio <= 1 / sec.MIN_SPLIT_RATIO):
            factor *= ratio
    return factor


def _exact_share_fact(concepts, start, end, cutoff):
    rows = [row for row in concepts.get(SHARE_CONCEPT, [])
            if row.get("unit") == "shares" and row.get("start") == start and row.get("end") == end
            and row.get("form") in {"10-Q", "10-Q/A"} and row.get("filed")
            and row.get("filed") <= cutoff]
    return audit._one_version(rows)


def _eligible_inputs(document, company, q4_index):
    q = document.get("quarterly") or {}
    fy = q["fy"][q4_index]
    end = q["end"][q4_index]
    q3_indices = [i for i, (year, fq) in enumerate(zip(q.get("fy") or [], q.get("fq") or []))
                  if year == fy and fq == 3]
    if len(q3_indices) != 1:
        return None, "q3-period-not-unique"
    q3_end = q["end"][q3_indices[0]]
    concepts = company.get("concepts") or {}
    annual_rows = [row for row in concepts.get(audit.COMMON_INCOME, [])
                   if row.get("unit") == "USD" and row.get("end") == end
                   and row.get("form") in {"10-K", "10-K/A"} and row.get("start")
                   and row.get("filed") and row.get("accession")]
    if not annual_rows:
        return None, "annual-common-income-missing"
    annual_start = sorted({row["start"] for row in annual_rows})[-1]
    annual_income, problem = audit._one_version([row for row in annual_rows if row.get("start") == annual_start])
    if problem:
        return None, "annual-common-income-conflict"
    annual_shares = [row for row in concepts.get(SHARE_CONCEPT, [])
                     if row.get("unit") == "shares" and row.get("start") == annual_start
                     and row.get("end") == end and row.get("form") in {"10-K", "10-K/A"}
                     and row.get("filed") and row.get("accession")]
    annual_shares, problem = audit._one_version(annual_shares)
    if problem:
        return None, "annual-basic-shares-missing-or-conflicting"
    if (annual_income.get("accession"), annual_income.get("filed")) != (
            annual_shares.get("accession"), annual_shares.get("filed")):
        return None, "annual-income-share-version-mismatch"
    nine_income, problem_income = audit._one_version([
        row for row in concepts.get(audit.COMMON_INCOME, [])
        if row.get("unit") == "USD" and row.get("start") == annual_start and row.get("end") == q3_end
        and row.get("form") in {"10-Q", "10-Q/A"} and row.get("filed")
        and row.get("filed") <= annual_income["filed"] and row.get("accession")])
    nine_shares, problem_shares = audit._one_version([
        row for row in concepts.get(SHARE_CONCEPT, [])
        if row.get("unit") == "shares" and row.get("start") == annual_start and row.get("end") == q3_end
        and row.get("form") in {"10-Q", "10-Q/A"} and row.get("filed")
        and row.get("filed") <= annual_income["filed"] and row.get("accession")])
    if problem_income or problem_shares:
        return None, "nine-month-income-or-share-fact-missing"
    if (nine_income.get("accession"), nine_income.get("filed")) != (
            nine_shares.get("accession"), nine_shares.get("filed")):
        return None, "nine-month-income-share-version-mismatch"

    q1_q2_q3_shares = []
    prior_end = None
    quarter_dates = []
    for fq in (1, 2, 3):
        matches = [i for i, (year, qtr) in enumerate(zip(q.get("fy") or [], q.get("fq") or []))
                   if year == fy and qtr == fq]
        if len(matches) != 1:
            q1_q2_q3_shares.append(None)
            continue
        idx = matches[0]
        qend = q["end"][idx]
        qstart = annual_start if fq == 1 else (prior_end + "~" if prior_end else None)
        if fq != 1:
            try:
                qstart = (date.fromisoformat(prior_end) + __import__("datetime").timedelta(days=1)).isoformat()
            except (TypeError, ValueError):
                qstart = None
        share_fact, share_problem = _exact_share_fact(concepts, qstart, qend, annual_income["filed"]) if qstart else (None, "missing")
        if share_problem:
            q1_q2_q3_shares.append(None)
        else:
            factor = _split_factor(document, share_fact.get("filed"))
            share = float(share_fact["value"]) * factor if factor else None
            q1_q2_q3_shares.append(share)
        prior_end = qend
        quarter_dates.append(qend)

    annual_factor = _split_factor(document, annual_shares.get("filed"))
    nine_factor = _split_factor(document, nine_shares.get("filed"))
    if annual_factor is None or nine_factor is None:
        return None, "share-filing-date-invalid-for-split-basis"
    annual_shares_value = float(annual_shares["value"]) * annual_factor
    nine_shares_value = float(nine_shares["value"]) * nine_factor
    annual_income_value, nine_income_value = float(annual_income["value"]), float(nine_income["value"])
    start_day, end_day, q3_day = date.fromisoformat(annual_start), date.fromisoformat(end), date.fromisoformat(q3_end)
    annual_days = (end_day - start_day).days + 1
    nine_days = (q3_day - start_day).days + 1
    q4_days = annual_days - nine_days
    if q4_days <= 0 or annual_days != nine_days + q4_days:
        return None, "actual-day-intervals-do-not-reconcile"
    q4_shares = (annual_shares_value * annual_days - nine_shares_value * nine_days) / q4_days
    if q4_shares <= 0:
        return None, "estimated-q4-basic-shares-nonpositive"
    q4_income = annual_income_value - nine_income_value
    return {
        "symbol": document.get("symbol") or "",
        "fiscalYear": fy, "periodEnd": end, "annualStart": annual_start, "q3End": q3_end,
        "annualIncome": annual_income_value, "nineMonthIncome": nine_income_value,
        "q4Income": q4_income, "annualShares": annual_shares_value,
        "nineMonthShares": nine_shares_value, "q123BasicShares": q1_q2_q3_shares,
        "q4Shares": q4_shares, "annualDays": annual_days, "nineMonthDays": nine_days,
        "q4Days": q4_days, "estimate": q4_income / q4_shares,
        "annualSource": {"accession": annual_income.get("accession"), "filed": annual_income.get("filed")},
        "nineMonthSource": {"accession": nine_income.get("accession"), "filed": nine_income.get("filed")},
    }, None


def _metrics(records):
    if not records:
        return {"n": 0, "maeUsdPerShare": None, "p50AbsUsdPerShare": None,
                "p90AbsUsdPerShare": None, "p99AbsUsdPerShare": None,
                "p50NormalizedErrorFloor010": None, "p90NormalizedErrorFloor010": None,
                "signErrorCount": 0, "signErrorRate": None}
    absolute = [record["absoluteError"] for record in records]
    normalized = [record["normalizedErrorFloor010"] for record in records]
    signs = sum(record["signError"] for record in records)
    return {"n": len(records), "maeUsdPerShare": sum(absolute) / len(absolute),
            "p50AbsUsdPerShare": percentile(absolute, 0.5), "p90AbsUsdPerShare": percentile(absolute, 0.9),
            "p99AbsUsdPerShare": percentile(absolute, 0.99),
            "p50NormalizedErrorFloor010": percentile(normalized, 0.5),
            "p90NormalizedErrorFloor010": percentile(normalized, 0.9),
            "signErrorCount": signs, "signErrorRate": signs / len(records)}


def _groups(record):
    result = ["all-grade-a-with-complete-inputs"]
    q4_income = record["q4Income"]
    reported_q4_net_income = record.get("reportedQ4NetIncome")
    if reported_q4_net_income is not None:
        if (q4_income < 0) == (reported_q4_net_income < 0) and q4_income != 0 and reported_q4_net_income != 0:
            result.append("q4-common-income-sign-matches-reported-q4-net-income")
            record["q4CommonIncomeSignMatchesReportedNetIncome"] = True
        else:
            record["q4CommonIncomeSignMatchesReportedNetIncome"] = False
    if q4_income > 0:
        result.append("q4-income-positive")
    elif q4_income < 0:
        result.append("q4-income-negative")
    else:
        result.append("q4-income-zero")
    annual_income = abs(record["annualIncome"])
    ratio = abs(q4_income) / annual_income if annual_income else None
    record["q4AnnualIncomeRatio"] = ratio
    if ratio is not None:
        result.append("q4-income-ratio-over-10pct" if ratio > 0.10 else "q4-income-ratio-at-or-under-10pct")
    shares = [value for value in [record["annualShares"], *record["q123BasicShares"]] if value is not None and value > 0]
    if len(shares) == 4:
        dispersion = max(shares) / min(shares) - 1
        record["q123AnnualShareDispersion"] = dispersion
        for limit in (0.02, 0.05, 0.10):
            if dispersion <= limit:
                result.append(f"q1-q3-and-annual-basic-shares-range-le-{int(limit*100)}pct")
    else:
        record["q123AnnualShareDispersion"] = None
    strict_shares = False
    if record["q4Shares"] > 0 and record["q123BasicShares"][2] and abs(record["q4Shares"] / record["q123BasicShares"][2] - 1) <= 0.15:
        result.append("estimated-q4-basic-shares-within-q3-15pct")
        if (record["q4Income"] > 0 and record["q123AnnualShareDispersion"] is not None
                and record["q123AnnualShareDispersion"] <= 0.02):
            result.append("strict-positive-income-shares-range-le-2pct-q4shares-within-q3-15pct")
            strict_shares = True
    if abs(record["actual"]) < 0.10:
        result.append("actual-eps-absolute-below-010")
        if strict_shares:
            result.append("strict-positive-income-shares-range-le-2pct-q4shares-within-q3-15pct-and-actual-eps-absolute-below-010")
    if strict_shares and ratio is not None:
        result.append("strict-positive-income-shares-range-le-2pct-q4shares-within-q3-15pct-and-q4-income-ratio-at-or-under-10pct"
                      if ratio <= 0.10 else
                      "strict-positive-income-shares-range-le-2pct-q4shares-within-q3-15pct-and-q4-income-ratio-over-10pct")
        if (ratio > 0.10 and record.get("q4CommonIncomeSignMatchesReportedNetIncome") is True):
            result.append("strict-positive-income-shares-range-le-2pct-q4shares-within-q3-15pct-and-q4-income-ratio-over-10pct-and-q4-net-income-sign-match")
    return result


def scan(financials_dir=DEFAULT_DIR, fact_cache=DEFAULT_CACHE):
    financials_dir, fact_cache = Path(financials_dir), Path(fact_cache)
    records, skips = [], Counter()
    grade_a_total = 0
    grade_a_with_cache = 0
    for path in sorted(financials_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            skips["invalid-financials-json"] += 1
            continue
        if document.get("market") != "US":
            continue
        q = document.get("quarterly") or {}
        values = q.get("values") or {}
        grades = q.get("epsQuality") or []
        is_grade_a = [i for i, fq in enumerate(q.get("fq") or [])
                      if fq == 4 and i < len(grades) and (grades[i] or {}).get("epsBasic") == "A"]
        grade_a_total += len(is_grade_a)
        cache_path = fact_cache / f"{path.stem}.json"
        if not cache_path.exists():
            skips["grade-a-companyfacts-cache-missing"] += len(is_grade_a)
            continue
        try:
            company = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            skips["invalid-companyfacts-cache"] += 1
            continue
        grade_a_with_cache += len(is_grade_a)
        for i, fq in enumerate(q.get("fq") or []):
            if fq != 4 or i >= len(grades) or (grades[i] or {}).get("epsBasic") != "A":
                continue
            actual_values = values.get("epsBasic") or []
            if i >= len(actual_values) or actual_values[i] is None:
                skips["grade-a-basic-value-missing"] += 1
                continue
            inputs, reason = _eligible_inputs(document, company, i)
            if reason:
                skips[reason] += 1
                continue
            inputs["actual"] = float(actual_values[i])
            reported_q4_values = values.get("netIncome") or []
            inputs["reportedQ4NetIncome"] = (float(reported_q4_values[i])
                                               if i < len(reported_q4_values)
                                               and reported_q4_values[i] is not None else None)
            inputs["absoluteError"] = abs(inputs["estimate"] - inputs["actual"])
            inputs["normalizedErrorFloor010"] = inputs["absoluteError"] / max(abs(inputs["actual"]), 0.10)
            inputs["signError"] = (inputs["estimate"] < 0) != (inputs["actual"] < 0)
            inputs["groups"] = _groups(inputs)
            records.append(inputs)

    grouped = defaultdict(list)
    for record in records:
        for group in record["groups"]:
            grouped[group].append(record)
    report_groups = {}
    for name, members in sorted(grouped.items()):
        metrics = _metrics(members)
        metrics["passesTwoPctSixPctDiagnosticGate"] = (
            metrics["p50NormalizedErrorFloor010"] is not None
            and metrics["p90NormalizedErrorFloor010"] is not None
            and metrics["p50NormalizedErrorFloor010"] <= 0.02
            and metrics["p90NormalizedErrorFloor010"] <= 0.06)
        metrics["passesCentResolutionPrecisionGate"] = (
            metrics["p50AbsUsdPerShare"] is not None and metrics["p90AbsUsdPerShare"] is not None
            and metrics["p50AbsUsdPerShare"] <= 0.005
            and metrics["p90AbsUsdPerShare"] <= 0.02
            and metrics["signErrorCount"] == 0)
        report_groups[name] = metrics
    return {"schema": "q4-eps-basic-actual-day-backtest-v1",
            "generatedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "actualSampleRule": "Q4 epsBasic with an existing SEC grade A; ungraded legacy and C/D rows are excluded",
            "reconstruction": "(annual common-attributable income - 9M common-attributable income) / actual-day-weighted Q4 basic shares",
            "shareFormula": "(annual basic weighted average shares * FY actual days - 9M basic weighted average shares * 9M actual days) / Q4 actual days",
            "normalization": "SEC split events after each fact period are applied to basic share averages before combining; EPS remains on the stored current basis",
            "errorDefinition": "absolute EPS error USD/share; normalized diagnostics use abs(error)/max(abs(actual), 0.10); absolute metrics remain primary near zero",
            "precisionPolicy": {
                "p50AbsoluteUsdPerShareMax": 0.005,
                "p90AbsoluteUsdPerShareMax": 0.02,
                "signErrorCountMax": 0,
                "rationale": "Q4 EPS is commonly reported to cents; require median reconstruction error within half-cent rounding, 90th percentile within two cents, and no sign errors in the passing subgroup.",
            },
            "observations": len(records), "gradeABasicQ4Rows": grade_a_total,
            "gradeABasicQ4RowsWithCompanyFactsCache": grade_a_with_cache,
            "groups": report_groups, "skipReasons": dict(skips),
            "selectedForGradeB": _validated_grade_b_count(),
            "gradeBDecision": (f"{_validated_grade_b_count()} raw-filing B records are source-validated after "
                               "the strict subgroup, EPS numerator-note, share-class, unit, version, and split-basis checks."),
            "decisionEvidence": "q4-eps-basic-B-audit.json",
            "sampleRecords": records}


def write(output_path, csv_path, report):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    import csv
    with Path(csv_path).open("w", encoding="utf-8", newline="") as handle:
        fields = ("group", "n", "maeUsdPerShare", "p50AbsUsdPerShare", "p90AbsUsdPerShare",
                  "p99AbsUsdPerShare", "p50NormalizedErrorFloor010", "p90NormalizedErrorFloor010",
                  "signErrorCount", "signErrorRate", "passesTwoPctSixPctDiagnosticGate",
                  "passesCentResolutionPrecisionGate")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for name, metrics in report["groups"].items():
            writer.writerow({"group": name, **metrics})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--fact-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--source-decisions", type=Path, default=DEFAULT_SOURCE_DECISIONS)
    args = parser.parse_args(argv)
    report = scan(args.dir, args.fact_cache)
    report["selectedForGradeB"] = _validated_grade_b_count(args.source_decisions)
    report["gradeBDecision"] = (f"{report['selectedForGradeB']} raw-filing B records are source-validated after "
                                 "the strict subgroup, EPS numerator-note, share-class, unit, version, and split-basis checks.")
    write(args.output, args.csv, report)
    print(json.dumps({"observations": report["observations"], "groups": report["groups"],
                      "skips": report["skipReasons"], "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

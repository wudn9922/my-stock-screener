#!/usr/bin/env python3
"""Offline audit/backtest for missing Q4 diluted EPS and the share-count estimate.

Default mode is read-only. The historical diluted-shares estimate can only be staged
as grade-C quarantine evidence; it is never written into formal EPS fields.
"""
import argparse
import json
import math
import os
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCRIPTS = str(Path(__file__).resolve().parent)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_financials as fin  # noqa: E402


REASONS = (
    "缺年度 EPS",
    "缺 Q1–Q3 EPS",
    "年度與季度股數差超過25%",
    "其他",
)
METHOD_B_GROUP = "strict-positive"
METHOD_B_GROUP_LABEL = "Q1–Q3 與年度 dilutedShares 差 ≤2%、Q4 淨利為正、推算股數在 Q3 ±15%"
METHOD_B_SHARE_RANGE_MAX = Decimal("0.02")
METHOD_B_Q3_DEVIATION_MAX = Decimal("0.15")
METHOD_B_ERROR_FLOOR = Decimal("0.10")
METHOD_B_MEDIAN_MAX = Decimal("0.02")
METHOD_B_P90_MAX = Decimal("0.06")


def value(block, field, index):
    values = (block.get("values") or {}).get(field) or []
    result = values[index] if index < len(values) else None
    if field in {"epsBasic", "epsDiluted"}:
        grades = block.get("epsQuality") or []
        grade = grades[index].get(field) if index < len(grades) and isinstance(grades[index], dict) else None
        if grade in {"C", "D"}:
            return None
    return result


def annual_rows_by_fy(block):
    result = {}
    for index, fy in enumerate(block.get("fy") or []):
        result.setdefault(fy, []).append(index)
    return result


def quarter_rows_by_fy(block):
    result = {}
    for index, (fy, fq) in enumerate(zip(block.get("fy") or [], block.get("fq") or [])):
        if fq in (1, 2, 3, 4):
            result.setdefault(fy, {}).setdefault(fq, []).append(index)
    return result


def period_item(block, index):
    count = len(block.get("end") or [])
    values = {
        field: value(block, field, index)
        for field in (block.get("values") or {})
    }
    return {
        "end": (block.get("end") or [None] * count)[index],
        "fy": (block.get("fy") or [None] * count)[index],
        "fq": (block.get("fq") or [None] * count)[index] if block.get("fq") else None,
        "src": (block.get("src") or ["yahoo"] * count)[index],
        "values": values,
    }


def method_a_rejection(document, fy):
    """Explain why build_financials.fill_q4_eps would leave diluted Q4 EPS null."""
    annual = document.get("annual") or {}
    quarterly = document.get("quarterly") or {}
    annual_matches = annual_rows_by_fy(annual).get(fy, [])
    by_quarter = quarter_rows_by_fy(quarterly).get(fy, {})
    q4_matches = by_quarter.get(4, [])
    q123_matches = [by_quarter.get(q, []) for q in (1, 2, 3)]
    if len(annual_matches) != 1 or len(q4_matches) != 1 or any(len(rows) != 1 for rows in q123_matches):
        return "年度／季度期別缺漏、重複或不唯一"

    year = period_item(annual, annual_matches[0])
    q4 = period_item(quarterly, q4_matches[0])
    pieces = [period_item(quarterly, rows[0]) for rows in q123_matches]
    if any(item.get("src") != "sec" for item in (year, q4, *pieces)):
        return "年度或季度來源非 SEC"
    if year["values"].get("epsDiluted") is None:
        return "年度 diluted EPS 缺漏"
    if any(item["values"].get("epsDiluted") is None for item in pieces):
        return "Q1–Q3 diluted EPS 缺漏"
    if fin.eps_inconsistent(year["values"]):
        return "年度 EPS 與淨利／股數不一致"
    if q4["values"].get("netIncome") is None:
        return "Q4 淨利缺漏"

    periods = (year, *pieces)
    bases = [
        fin.share_basis(item["values"].get("dilutedShares"), item["values"].get("netIncome"),
                        item["values"].get("epsDiluted"))
        for item in periods
    ]
    for item, basis in zip(periods, bases):
        if basis is not None and basis > 0:
            continue
        if item["values"].get("dilutedShares") is None:
            if item["values"].get("netIncome") is None:
                return "缺股數且缺淨利，無法推算股數"
            if not item["values"].get("epsDiluted"):
                return "缺股數且 EPS 無效，無法推算股數"
            return "缺少可用股數"
        return "股數非正值"
    if max(bases) / min(bases) > 1 + fin.SPLIT_MATCH:
        return "方法 A 股數基準差超過 25%"

    estimate = fin.q4_eps(
        year["values"].get("epsDiluted"),
        [item["values"].get("epsDiluted") for item in pieces],
        bases,
    )
    if estimate is None:
        return "方法 A 缺少可計算輸入"
    income = q4["values"]["netIncome"]
    if estimate and (estimate > 0) != (income > 0):
        return "方法 A 推算 EPS 與 Q4 淨利符號不一致"
    if abs(estimate - income / bases[0]) > fin.EPS_TOLERANCE * abs(estimate) + fin.Q4_EPS_ROUNDING:
        return "方法 A 推算值未通過 Q4 淨利／股數檢查"
    return "方法 A 通過檢查但仍未填值（需檢查資料形狀）"


def classify_missing_q4(document, symbol):
    annual = document.get("annual") or {}
    quarterly = document.get("quarterly") or {}
    annual_by_fy = annual_rows_by_fy(annual)
    quarter_by_fy = quarter_rows_by_fy(quarterly)
    rows = []
    for index, (fy, fq) in enumerate(zip(quarterly.get("fy") or [], quarterly.get("fq") or [])):
        if fq != 4 or value(quarterly, "netIncome", index) is None:
            continue
        if value(quarterly, "epsDiluted", index) is not None:
            continue

        annual_matches = annual_by_fy.get(fy, [])
        annual_index = annual_matches[0] if len(annual_matches) == 1 else None
        annual_eps_values = [value(annual, "epsDiluted", match) for match in annual_matches]
        annual_eps_missing = not any(eps is not None for eps in annual_eps_values)
        q123 = [quarter_by_fy.get(fy, {}).get(q, []) for q in (1, 2, 3)]
        q123_indices = [matches[0] for matches in q123 if len(matches) == 1]
        q123_eps_missing = any(
            not any(value(quarterly, "epsDiluted", match) is not None for match in matches)
            for matches in q123
        )
        ambiguous_period = len(annual_matches) > 1 or any(len(matches) > 1 for matches in q123)
        q123_eps_complete = len(q123_indices) == 3 and not q123_eps_missing

        annual_shares = value(annual, "dilutedShares", annual_index) if annual_index is not None else None
        q123_shares = [value(quarterly, "dilutedShares", q_index) for q_index in q123_indices]
        share_jump = (
            annual_shares is not None
            and annual_shares > 0
            and len(q123_shares) == 3
            and all(shares is not None and shares > 0 for shares in q123_shares)
            and any(abs(float(shares) / float(annual_shares) - 1) > 0.25 for shares in q123_shares)
        )

        # Mutually exclusive period classification: annual EPS, Q1–Q3 EPS,
        # share-count discontinuity, then all remaining cases.
        if annual_eps_missing:
            reason = REASONS[0]
        elif q123_eps_missing:
            reason = REASONS[1]
        elif ambiguous_period:
            reason = REASONS[3]
        elif share_jump:
            reason = REASONS[2]
        else:
            reason = REASONS[3]
        detail = method_a_rejection(document, fy) if reason == REASONS[3] else None
        rows.append((symbol, fy, (quarterly.get("end") or [None] * (index + 1))[index], reason, detail))
    return rows


def percentile(values, p):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    position = (len(values) - 1) * p
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _decimal(value_):
    if isinstance(value_, bool) or value_ is None:
        return None
    try:
        result = Decimal(str(value_))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def method_b_observation(document, q4_index):
    """Build the strict positive-income estimate; return (record, rejection reason)."""
    annual = document.get("annual") or {}
    quarterly = document.get("quarterly") or {}
    annual_by_fy = annual_rows_by_fy(annual)
    quarter_by_fy = quarter_rows_by_fy(quarterly)
    fys = quarterly.get("fy") or []
    fqs = quarterly.get("fq") or []
    if q4_index >= len(fys) or q4_index >= len(fqs):
        return None, "missing_annual_or_q123"
    fy = fys[q4_index]
    annual_matches = annual_by_fy.get(fy, [])
    q123 = [quarter_by_fy.get(fy, {}).get(q, []) for q in (1, 2, 3)]
    q4_matches = quarter_by_fy.get(fy, {}).get(4, [])
    if (len(annual_matches) != 1 or any(len(matches) != 1 for matches in q123)
            or len(q4_matches) != 1 or q4_matches[0] != q4_index):
        return None, "missing_annual_or_q123"

    annual_index = annual_matches[0]
    q123_indices = [matches[0] for matches in q123]
    annual_shares = _decimal(value(annual, "dilutedShares", annual_index))
    q123_shares = [_decimal(value(quarterly, "dilutedShares", index)) for index in q123_indices]
    if annual_shares is None or annual_shares <= 0 or any(shares is None or shares <= 0 for shares in q123_shares):
        return None, "missing_share_inputs"

    net_income = _decimal(value(quarterly, "netIncome", q4_index))
    if net_income is None:
        return None, "no_q4_income"
    if net_income <= 0:
        return None, "q4_income_not_positive"

    all_shares = [annual_shares, *q123_shares]
    if max(all_shares) / min(all_shares) - 1 > METHOD_B_SHARE_RANGE_MAX:
        return None, "share_range_over_2pct"

    q4_shares = Decimal(4) * annual_shares - sum(q123_shares, Decimal(0))
    if q4_shares <= 0:
        return None, "nonpositive_estimated_q4_shares"
    if abs(q4_shares / q123_shares[2] - 1) > METHOD_B_Q3_DEVIATION_MAX:
        return None, "estimated_shares_outside_q3_15pct"

    estimate = net_income / q4_shares
    if estimate <= 0:
        return None, "nonpositive_estimate"
    return {
        "fy": fy,
        "end": (quarterly.get("end") or [None] * (q4_index + 1))[q4_index],
        "netIncome": net_income,
        "annualShares": annual_shares,
        "q123Shares": q123_shares,
        "q4Shares": q4_shares,
        "estimate": estimate,
    }, None


def validate_method_b(document):
    """Back-test the share-count estimate on all eligible existing Q4 diluted EPS."""
    quarterly = document.get("quarterly") or {}
    counts = Counter()
    errors = []
    for index, fq in enumerate(quarterly.get("fq") or []):
        if fq != 4 or value(quarterly, "epsDiluted", index) is None:
            continue
        counts["existing_q4_eps"] += 1
        observation, reason = method_b_observation(document, index)
        if reason:
            counts[reason] += 1
            continue
        actual = _decimal(value(quarterly, "epsDiluted", index))
        if actual is None:
            counts["missing_actual_eps"] += 1
            continue
        errors.append(abs(observation["estimate"] - actual) / max(abs(actual), METHOD_B_ERROR_FLOOR))
        counts["with_q4_income"] += 1
    counts["eligible_compared"] = len(errors)
    return counts, errors


def method_b_subgroup_backtest(directory):
    """Back-test the strict subgroup using existing Q4 diluted EPS values."""
    errors = {METHOD_B_GROUP: []}
    skipped = Counter()
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if document.get("market") != "US":
            continue
        quarterly = document.get("quarterly") or {}
        for index, fq in enumerate(quarterly.get("fq") or []):
            if fq != 4 or value(quarterly, "epsDiluted", index) is None:
                continue
            observation, reason = method_b_observation(document, index)
            if reason:
                skipped[reason] += 1
                continue
            actual = _decimal(value(quarterly, "epsDiluted", index))
            if actual is None:
                skipped["invalid_actual_eps"] += 1
                continue
            error = abs(observation["estimate"] - actual) / max(abs(actual), METHOD_B_ERROR_FLOOR)
            errors[METHOD_B_GROUP].append(error)
    return errors, skipped


def method_b_subgroup_report(errors):
    report = {}
    for key, label in ((METHOD_B_GROUP, METHOD_B_GROUP_LABEL),):
        values = errors.get(key) or []
        median = percentile(values, 0.5)
        p90 = percentile(values, 0.9)
        report[key] = {
            "label": label,
            "n": len(values),
            "median": median,
            "p90": p90,
            "passed": (
                median is not None and p90 is not None
                and Decimal(str(median)) <= METHOD_B_MEDIAN_MAX
                and Decimal(str(p90)) <= METHOD_B_P90_MAX
            ),
            "quarantined": 0,
        }
    return report


def apply_method_b(directory, report, dry_run=False):
    """Stage eligible diluted-share estimates as grade C, never as formal EPS values."""
    summary = {"fills": [], "skipped": Counter(), "writtenFiles": 0}
    if not report.get(METHOD_B_GROUP, {}).get("passed", False):
        summary["skipped"]["strict-subgroup-validation-failed"] += 1
        return summary
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary["skipped"]["corrupt"] += 1
            continue
        if document.get("market") != "US":
            continue
        quarterly = document.get("quarterly") or {}
        if not isinstance(quarterly, dict):
            continue
        eps_values = (quarterly.get("values") or {}).get("epsDiluted")
        if not isinstance(eps_values, list):
            eps_values = [None] * len(quarterly.get("end") or [])
        for index, fq in enumerate(quarterly.get("fq") or []):
            if fq != 4:
                continue
            if index < len(eps_values) and eps_values[index] is not None:
                continue
            observation, reason = method_b_observation(document, index)
            if reason:
                summary["skipped"][reason] += 1
                continue
            stored = round(float(observation["estimate"]), 4)
            if not math.isfinite(stored) or stored == 0:
                summary["skipped"]["invalid-rounded-estimate"] += 1
                continue
            # Recheck that the formal EPS is still null before staging C evidence.
            if index < len(eps_values) and eps_values[index] is not None:
                summary["skipped"]["value-became-non-null"] += 1
                continue
            summary["fills"].append({
                "symbol": path.stem,
                "fy": observation["fy"],
                "end": observation["end"],
                "field": "epsDiluted",
                "value": stored,
                "grade": "C",
                "status": "quarantined",
                "method": "annual-diluted-shares-times-four-minus-q1-q3",
                "source": {"financialsFile": f"docs/report/financials/{path.stem}.json",
                           "source": document.get("source"), "accession": None, "url": None},
                "calculation": {
                    "formula": "Q4 net income / (annual diluted shares * 4 - Q1 - Q2 - Q3 diluted shares)",
                    "netIncome": str(observation["netIncome"]),
                    "annualShares": str(observation["annualShares"]),
                    "q123Shares": [str(item) for item in observation["q123Shares"]],
                    "estimatedQ4Shares": str(observation["q4Shares"]),
                },
                "validation": {
                    "passedForHistoricalEstimationOnly": True,
                    "reasonNotFormalGradeB": "diluted Q4 weighted-average shares were not independently reported or validated",
                },
            })
    summary["quarantinedPeriods"] = len({(fill["symbol"], fill["end"]) for fill in summary["fills"]})
    if not dry_run and summary["fills"]:
        sidecar = Path(directory) / "q4-eps-method-b-quarantine-C.jsonl"
        existing = []
        if sidecar.exists():
            existing = [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()]
        by_key = {(item.get("symbol"), item.get("fy"), item.get("end"), item.get("field")): item
                  for item in existing}
        for item in summary["fills"]:
            by_key[(item["symbol"], item["fy"], item["end"], item["field"])] = item
        temporary = sidecar.with_name(sidecar.name + ".tmp")
        temporary.write_text("".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
            for _, item in sorted(by_key.items())
        ), encoding="utf-8")
        os.replace(temporary, sidecar)
        summary["sidecar"] = str(sidecar)
    report[METHOD_B_GROUP]["quarantined"] = summary["quarantinedPeriods"]
    return summary


def audit(directory):
    periods = []
    q4_counts = Counter()
    method_b_errors = []
    q4_missing_income = 0
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if document.get("market") != "US":
            continue
        quarterly = document.get("quarterly") or {}
        q4_missing_income += sum(
            1 for index, fq in enumerate(quarterly.get("fq") or [])
            if fq == 4 and value(quarterly, "netIncome", index) is None
            and value(quarterly, "epsDiluted", index) is None
        )
        missing = classify_missing_q4(document, path.stem)
        periods.extend(missing)
        if missing:
            q4_counts[path.stem] += 1
        counts, errors = validate_method_b(document)
        method_b_errors.extend(errors)
        for reason, count in counts.items():
            q4_counts[f"method_b:{reason}"] += count

    return periods, q4_counts, method_b_errors, q4_missing_income


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        default=str(Path(__file__).resolve().parents[2] / "docs" / "report" / "financials"),
        help="financial JSON directory (default: docs/report/financials)",
    )
    parser.add_argument("--apply-method-b", action="store_true", help="stage eligible diluted-share estimates as grade-C quarantine records")
    parser.add_argument("--dry-run", action="store_true", help="preview grade-C quarantine records without writing files")
    args = parser.parse_args(argv)
    if args.dry_run and not args.apply_method_b:
        parser.error("--dry-run requires --apply-method-b")
    periods, counts, errors, q4_missing_income = audit(args.dir)
    unique_files = {symbol for symbol, _fy, _end, _reason, _detail in periods}
    reason_periods = Counter(reason for _symbol, _fy, _end, reason, _detail in periods)

    print(f"missing diluted EPS Q4 periods={len(periods)} files={len(unique_files)}")
    print("reason\tperiods\tfiles (non-exclusive)")
    for reason in REASONS:
        files = {symbol for symbol, _fy, _end, row_reason, _detail in periods if row_reason == reason}
        print(f"{reason}\t{reason_periods[reason]}\t{len(files)}")
    other = [(symbol, fy, end, detail) for symbol, fy, end, reason, detail in periods if reason == REASONS[3]]
    other_periods = Counter(detail for _symbol, _fy, _end, detail in other)
    print(f"Q4 with null epsDiluted but missing netIncome (outside target): {q4_missing_income}")
    print("other bucket breakdown\tperiods\tfiles (non-exclusive)")
    for detail in sorted(other_periods):
        files = {symbol for symbol, _fy, _end, row_detail in other if row_detail == detail}
        print(f"{detail}\t{other_periods[detail]}\t{len(files)}")
    print("method B strict-subgroup exclusions among existing Q4 EPS:")
    for name in (
        "existing_q4_eps", "eligible_compared", "no_q4_income", "q4_income_not_positive",
        "missing_annual_or_q123", "missing_share_inputs", "share_range_over_2pct",
        "nonpositive_estimated_q4_shares", "estimated_shares_outside_q3_15pct",
        "nonpositive_estimate", "missing_actual_eps",
    ):
        print(f"  {name}: {counts[f'method_b:{name}']}")
    subgroup_errors, subgroup_skipped = method_b_subgroup_backtest(args.dir)
    subgroup_report = method_b_subgroup_report(subgroup_errors)
    item = subgroup_report[METHOD_B_GROUP]
    median_text = "N/A" if item["median"] is None else f"{item['median'] * 100:.2f}%"
    p90_text = "N/A" if item["p90"] is None else f"{item['p90'] * 100:.2f}%"
    print("method B validation (existing Q4 EPS; error = |estimate − actual| / max(|actual|, 0.10)):")
    print(f"  strict subgroup quarters={item['n']}, median={median_text}, P90={p90_text}, "
          f"gate={'PASS' if item['passed'] else 'FAIL'} (median ≤2%, P90 ≤6%)")
    summary = None
    if args.apply_method_b:
        if not item["passed"]:
            print("Method B stopped: strict-subgroup validation threshold failed; no records were staged.")
            return 2
        summary = apply_method_b(args.dir, subgroup_report, dry_run=args.dry_run)
    if subgroup_skipped:
        print("method B backtest exclusions: " + ", ".join(
            f"{reason} {count}" for reason, count in sorted(subgroup_skipped.items())
        ))
    if summary is not None:
        mode = "would quarantine" if args.dry_run else "quarantined"
        print(f"Method B {mode}: {summary['quarantinedPeriods']} unique Q4 quarters "
              f"({len(summary['fills'])} grade-C estimates; formal EPS files unchanged).")
        if summary.get("sidecar"):
            print(f"grade-C sidecar: {summary['sidecar']}")
        if summary["skipped"]:
            print("Method B candidates skipped: " + ", ".join(
                f"{reason} {count}" for reason, count in sorted(summary["skipped"].items())
            ))
        print(f"  strict subgroup quarantined quarters: {item['quarantined']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

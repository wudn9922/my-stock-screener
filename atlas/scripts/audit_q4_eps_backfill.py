#!/usr/bin/env python3
"""Offline audit of missing Q4 diluted EPS and the proposed share-count estimate.

This script is read-only. It reports the current Q4 missing-EPS classification and
back-tests method B against existing Q4 diluted EPS. It never edits financial files.
"""
import argparse
import json
import math
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


REASONS = (
    "缺年度 EPS",
    "缺 Q1–Q3 EPS",
    "年度與季度股數差超過25%",
    "其他",
)


def value(block, field, index):
    values = (block.get("values") or {}).get(field) or []
    return values[index] if index < len(values) else None


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
        rows.append((symbol, fy, (quarterly.get("end") or [None] * (index + 1))[index], reason))
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


def has_split_in_fiscal_year(document, annual, annual_index):
    annual_ends = annual.get("end") or []
    if annual_index >= len(annual_ends):
        return False
    try:
        end = date.fromisoformat(annual_ends[annual_index])
    except (TypeError, ValueError):
        return False
    prior_ends = []
    for value_ in annual_ends:
        try:
            candidate = date.fromisoformat(value_)
        except (TypeError, ValueError):
            continue
        if candidate < end:
            prior_ends.append(candidate)
    start = max(prior_ends) + timedelta(days=1) if prior_ends else end - timedelta(days=370)
    events = (document.get("split") or {}).get("events") or []
    for event in events:
        if not isinstance(event, (list, tuple)) or not event:
            continue
        try:
            split_day = date.fromisoformat(event[0])
        except (TypeError, ValueError):
            continue
        if start <= split_day <= end:
            return True
    return False


def validate_method_b(document):
    """Back-test the diluted-share estimate on eligible existing Q4 diluted EPS."""
    annual = document.get("annual") or {}
    quarterly = document.get("quarterly") or {}
    annual_by_fy = annual_rows_by_fy(annual)
    quarter_by_fy = quarter_rows_by_fy(quarterly)
    counts = Counter()
    errors = []

    for index, (fy, fq) in enumerate(zip(quarterly.get("fy") or [], quarterly.get("fq") or [])):
        if fq != 4 or value(quarterly, "epsDiluted", index) is None:
            continue
        counts["existing_q4_eps"] += 1
        net_income = value(quarterly, "netIncome", index)
        if net_income is None:
            counts["no_q4_income"] += 1
            continue
        counts["with_q4_income"] += 1

        annual_matches = annual_by_fy.get(fy, [])
        q123 = [quarter_by_fy.get(fy, {}).get(q, []) for q in (1, 2, 3)]
        if len(annual_matches) != 1 or any(len(matches) != 1 for matches in q123):
            counts["missing_annual_or_q123"] += 1
            continue
        annual_index = annual_matches[0]
        q123_indices = [matches[0] for matches in q123]
        annual_shares = value(annual, "dilutedShares", annual_index)
        q123_shares = [value(quarterly, "dilutedShares", q_index) for q_index in q123_indices]
        if annual_shares is None or annual_shares <= 0 or any(
            shares is None or shares <= 0 for shares in q123_shares
        ):
            counts["missing_share_inputs"] += 1
            continue

        q4_shares = Decimal(4) * Decimal(str(annual_shares)) - sum(
            (Decimal(str(shares)) for shares in q123_shares), Decimal(0)
        )
        if q4_shares <= 0:
            counts["nonpositive_estimated_q4_shares"] += 1
            continue
        q3_shares = Decimal(str(q123_shares[2]))
        if abs(q4_shares / q3_shares - 1) > Decimal("0.15"):
            counts["outside_q3_15pct"] += 1
            continue
        if has_split_in_fiscal_year(document, annual, annual_index) and any(
            abs(Decimal(str(shares)) / Decimal(str(annual_shares)) - 1) > Decimal("0.25")
            for shares in q123_shares
        ):
            counts["split_share_mismatch_over_25pct"] += 1
            continue

        estimate = (Decimal(str(net_income)) / q4_shares).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        if estimate == 0 or (estimate > 0) != (Decimal(str(net_income)) > 0):
            counts["wrong_or_zero_sign"] += 1
            continue
        actual = Decimal(str(value(quarterly, "epsDiluted", index)))
        if actual == 0:
            counts["zero_actual_eps"] += 1
            continue
        errors.append(abs(estimate - actual) / abs(actual))

    counts["eligible_compared"] = len(errors)
    return counts, errors


def audit(directory):
    periods = []
    q4_counts = Counter()
    method_b_errors = []
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if document.get("market") != "US":
            continue
        missing = classify_missing_q4(document, path.stem)
        periods.extend(missing)
        if missing:
            q4_counts[path.stem] += 1
        counts, errors = validate_method_b(document)
        method_b_errors.extend(errors)
        for reason, count in counts.items():
            q4_counts[f"method_b:{reason}"] += count

    return periods, q4_counts, method_b_errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        default=str(Path(__file__).resolve().parents[2] / "docs" / "report" / "financials"),
        help="financial JSON directory (default: docs/report/financials)",
    )
    args = parser.parse_args(argv)
    periods, counts, errors = audit(args.dir)
    unique_files = {symbol for symbol, _fy, _end, _reason in periods}
    reason_periods = Counter(reason for _symbol, _fy, _end, reason in periods)

    print(f"missing diluted EPS Q4 periods={len(periods)} files={len(unique_files)}")
    print("reason\tperiods\tfiles (non-exclusive)")
    for reason in REASONS:
        files = {symbol for symbol, _fy, _end, row_reason in periods if row_reason == reason}
        print(f"{reason}\t{reason_periods[reason]}\t{len(files)}")
    print("method B validation (existing Q4 diluted EPS, all sources):")
    for name in (
        "existing_q4_eps", "eligible_compared", "no_q4_income", "missing_annual_or_q123",
        "missing_share_inputs", "nonpositive_estimated_q4_shares", "outside_q3_15pct",
        "split_share_mismatch_over_25pct", "wrong_or_zero_sign", "zero_actual_eps",
    ):
        print(f"  {name}: {counts[f'method_b:{name}']}")
    n = len(errors)
    median = percentile(errors, 0.5)
    p90 = percentile(errors, 0.9)
    over_5 = sum(error > Decimal("0.05") for error in errors)
    print(f"  median relative error: {median * 100:.2f}%" if median is not None else "  median relative error: N/A")
    print(f"  P90 relative error: {p90 * 100:.2f}%" if p90 is not None else "  P90 relative error: N/A")
    print(f"  relative error >5%: {over_5}/{n} ({over_5 / n * 100:.2f}%)" if n else "  relative error >5%: N/A")
    passed = median is not None and p90 is not None and median <= 0.02 and p90 <= 0.06
    print(f"  method B gate: {'PASS' if passed else 'FAIL'} (median <=2%, P90 <=6%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Restore safe pre-refresh Yahoo cells dropped when SEC rows replaced Yahoo periods.

This is an offline, one-time repair. It reads the selected Git snapshot, only considers Yahoo
periods that map uniquely to an existing SEC period with the same fiscal labels, and uses
build_financials.merge_section's null-fill and per-share-basis rules. It never adds periods or
replaces a non-null SEC value.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
import build_financials as financials  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True, help="Git commit holding the pre-refresh Yahoo files")
    parser.add_argument("--financials-dir", default=str(REPO_ROOT / "docs" / "report" / "financials"))
    parser.add_argument("--audit-out", default=str(REPO_ROOT / "docs" / "report" / "financials-us-yahoo-null-restoration.json"))
    parser.add_argument("--dry-run", action="store_true", help="write the audit but leave financial files untouched")
    return parser.parse_args(argv)


def resolve_revision(revision: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def read_git_document(revision: str, symbol: str) -> dict[str, Any] | None:
    path = f"docs/report/financials/{symbol}.json"
    result = subprocess.run(["git", "show", f"{revision}:{path}"], cwd=REPO_ROOT, capture_output=True)
    if result.returncode:
        return None
    document = json.loads(result.stdout)
    if not isinstance(document, dict) or document.get("symbol", symbol).upper() != symbol.upper():
        raise ValueError(f"invalid baseline financial document {revision}:{path}")
    return document


def fiscal_label(period: dict[str, Any], field: str) -> int | None:
    value = period.get("extra", {}).get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def labels_match(old: dict[str, Any], current: dict[str, Any], kind: str) -> bool:
    for label in ("fy", "fq") if kind == "quarterly" else ("fy",):
        old_value = fiscal_label(old, label)
        current_value = fiscal_label(current, label)
        if old_value is not None and current_value is not None and old_value != current_value:
            return False
    return True


def matched_yahoo_candidates(
    sec_periods: list[dict[str, Any]],
    yahoo_periods: list[dict[str, Any]],
    kind: str,
) -> list[dict[str, Any]]:
    """Only pass one-to-one, same-fiscal-period Yahoo matches to the standard merge routine."""
    matches: dict[int, list[int]] = {}
    reverse: dict[int, list[int]] = {}
    for yahoo_index, yahoo in enumerate(yahoo_periods):
        if yahoo.get("src") != "yahoo":
            continue
        for sec_index, sec in enumerate(sec_periods):
            if not labels_match(yahoo, sec, kind):
                continue
            if abs(financials.days_between(yahoo["end"], sec["end"])) <= financials.SAME_PERIOD_DAYS:
                matches.setdefault(yahoo_index, []).append(sec_index)
                reverse.setdefault(sec_index, []).append(yahoo_index)
    return [
        yahoo_periods[yahoo_index]
        for yahoo_index, sec_indices in matches.items()
        if len(sec_indices) == 1 and len(reverse[sec_indices[0]]) == 1
    ]


def restore_section(
    current_block: dict[str, Any] | None,
    yahoo_block: dict[str, Any] | None,
    kind: str,
    today_iso: str,
    allow_matching_share_basis: bool = False,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """Return an updated SEC section and restored field rows, without mutating either input."""
    if not isinstance(current_block, dict) or not isinstance(yahoo_block, dict):
        return current_block, [], []
    current_block = copy.deepcopy(current_block)
    sec_periods, _ = financials.read_section(current_block)
    yahoo_periods, _ = financials.read_section(yahoo_block)
    sec_targets = [period for period in sec_periods if period["src"] == "sec"]
    yahoo_candidates = matched_yahoo_candidates(sec_targets, yahoo_periods, kind)
    if not sec_targets or not yahoo_candidates:
        return current_block, [], []

    safe_candidates: list[dict[str, Any]] = []
    excluded_by_period: dict[str, dict[str, str]] = {}
    for yahoo in yahoo_candidates:
        matching_sec = [
            sec for sec in sec_targets
            if labels_match(yahoo, sec, kind)
            and abs(financials.days_between(yahoo["end"], sec["end"])) <= financials.SAME_PERIOD_DAYS
        ]
        if len(matching_sec) != 1:
            continue
        sec = matching_sec[0]
        candidate = financials.period(yahoo["end"], yahoo["values"], yahoo["src"], yahoo["extra"])
        candidate["values"] = dict(yahoo["values"])
        sec_values = sec["values"]
        candidate_values = candidate["values"]
        excluded = excluded_by_period.setdefault(yahoo["end"], {})

        # The UI prefers this alias over the general line. Never let an old Yahoo alias shadow
        # a fresh SEC operating-income value already present in the period.
        if sec_values.get("operatingIncome") is not None and sec_values.get("operatingIncomeReported") is None:
            if candidate_values.get("operatingIncomeReported") is not None:
                candidate_values.pop("operatingIncomeReported")
                excluded["operatingIncomeReported"] = "sec_operating_income_preferred"

        def is_consistent(left: float, right: float) -> bool:
            return abs(left - right) <= max(1.0, abs(right) * 0.001)

        sec_revenue = financials.finite_number(sec_values.get("revenue"))
        sec_gross = financials.finite_number(sec_values.get("grossProfit"))
        yahoo_cost = financials.finite_number(candidate_values.get("costOfRevenue"))
        # Preserve an SEC-implied cost when both SEC revenue and gross profit exist.
        if (sec_values.get("costOfRevenue") is None and sec_revenue is not None
                and sec_gross is not None and yahoo_cost is not None):
            implied_cost = sec_revenue - sec_gross
            if not is_consistent(yahoo_cost, implied_cost):
                candidate_values.pop("costOfRevenue")
                excluded["costOfRevenue"] = "conflicts_with_sec_revenue_and_gross_profit"

        # A Yahoo gross-profit value must agree with revenue minus cost when both inputs are
        # available, including a same-period Yahoo cost that this pass can also safely restore.
        incoming_revenue = sec_revenue if sec_revenue is not None else financials.finite_number(candidate_values.get("revenue"))
        sec_cost = financials.finite_number(sec_values.get("costOfRevenue"))
        incoming_cost = sec_cost if sec_cost is not None else financials.finite_number(candidate_values.get("costOfRevenue"))
        yahoo_gross = financials.finite_number(candidate_values.get("grossProfit"))
        if (sec_values.get("grossProfit") is None and incoming_revenue is not None
                and incoming_cost is not None and yahoo_gross is not None):
            implied_gross = incoming_revenue - incoming_cost
            if not is_consistent(yahoo_gross, implied_gross):
                candidate_values.pop("grossProfit")
                excluded["grossProfit"] = "conflicts_with_revenue_minus_cost"
        safe_candidates.append(candidate)

    # The normal merge guard refuses every partial EPS/share fill. A narrowly verified exception
    # permits a missing Yahoo EPS when the SEC share count matches Yahoo's, the fiscal period is
    # the same, and the unchanged split metadata proves both values use the same share basis.
    merge_targets: list[dict[str, Any]] = []
    for sec_period in sec_targets:
        cloned = financials.period(
            sec_period["end"], sec_period["values"], sec_period["src"], sec_period["extra"],
        )
        missing_per_share = any(sec_period["values"].get(field) is None
                                 for field in financials.PER_SHARE_FIELDS[:2])
        if allow_matching_share_basis and missing_per_share:
            candidates = [
                yahoo for yahoo in yahoo_candidates
                if labels_match(yahoo, sec_period, kind)
                and abs(financials.days_between(yahoo["end"], sec_period["end"])) <= financials.SAME_PERIOD_DAYS
            ]
            if len(candidates) == 1:
                yahoo = candidates[0]
                current_shares = financials.finite_number(sec_period["values"].get("dilutedShares"))
                yahoo_shares = financials.finite_number(yahoo["values"].get("dilutedShares"))
                eps_values_available = all(
                    financials.finite_number(yahoo["values"].get(field)) is not None
                    for field in financials.PER_SHARE_FIELDS[:2]
                )
                shares_match = (
                    current_shares is not None and current_shares > 0
                    and yahoo_shares is not None and yahoo_shares > 0
                    and abs(current_shares - yahoo_shares) / max(current_shares, yahoo_shares) <= 0.01
                )
                if shares_match and eps_values_available:
                    for field in financials.PER_SHARE_FIELDS:
                        cloned["values"][field] = None
        merge_targets.append(cloned)

    merged, _ = financials.merge_section(merge_targets, safe_candidates, kind, today_iso)
    sec_by_end = {period["end"]: period for period in merged}
    ends = current_block.get("end", [])
    index_by_end = {end: index for index, end in enumerate(ends)}
    restored_by_period: dict[str, list[str]] = {}
    columns = current_block.get("values", {})
    sources = current_block.get("src", [])
    for end, period in sec_by_end.items():
        index = index_by_end.get(end)
        if index is None or index >= len(sources) or sources[index] != "sec":
            continue
        for field, value in period["values"].items():
            if value is None:
                continue
            column = columns.get(field)
            previous = column[index] if isinstance(column, list) and index < len(column) else None
            if previous is not None:
                continue
            if not isinstance(column, list):
                column = [None] * len(ends)
                columns[field] = column
            elif len(column) < len(ends):
                column.extend([None] * (len(ends) - len(column)))
            column[index] = value
            restored_by_period.setdefault(end, []).append(field)

    if restored_by_period:
        current_block["values"] = columns
    restored_rows = [
        {"end": end, "fields": sorted(fields)}
        for end, fields in sorted(restored_by_period.items())
    ]
    excluded_rows = [
        {"end": end, "fields": dict(sorted(fields.items()))}
        for end, fields in sorted(excluded_by_period.items()) if fields
    ]
    return current_block, restored_rows, excluded_rows


def target_audit(
    baseline_documents: dict[str, dict[str, Any]],
    final_documents: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Verify the reviewer-triaged null-fill examples on both first and later runs."""
    targets: dict[str, list[tuple[str, str, str | None, int]]] = {
        "AGM": [("annual", "revenue", None, 4), ("quarterly", "revenue", None, 5)],
        "EEFT": [
            ("annual", "costOfRevenue", None, 4), ("annual", "grossProfit", None, 4),
            ("quarterly", "costOfRevenue", None, 5), ("quarterly", "grossProfit", None, 5),
        ],
        "LOPE": [
            ("annual", "costOfRevenue", None, 4), ("annual", "grossProfit", None, 4),
            ("quarterly", "costOfRevenue", None, 5), ("quarterly", "grossProfit", None, 5),
        ],
        "PLUS": [
            ("quarterly", "operatingIncome", "2026-03-31", 1),
            ("quarterly", "pretaxIncome", "2026-03-31", 1),
            ("quarterly", "netIncome", "2026-03-31", 1),
        ],
        "BB": [
            ("quarterly", "epsDiluted", "2026-05-31", 1),
            ("quarterly", "epsBasic", "2026-05-31", 1),
        ],
    }
    result: dict[str, Any] = {}
    for symbol, expectations in targets.items():
        baseline = baseline_documents.get(symbol, {})
        final = final_documents.get(symbol, {})
        checks: dict[str, Any] = {}
        for kind, field, exact_date, minimum in expectations:
            old_periods, _ = financials.read_section(baseline.get(kind))
            current_periods, _ = financials.read_section(final.get(kind))
            old_candidates = matched_yahoo_candidates(
                [period for period in current_periods if period["src"] == "sec"],
                old_periods,
                kind,
            )
            old_candidates = [
                period for period in old_candidates
                if period["values"].get(field) is not None
                and (exact_date is None or period["end"] == exact_date)
            ]
            verified_ends: list[str] = []
            missing_ends: list[str] = []
            current_different_ends: list[str] = []
            for old_period in old_candidates:
                matches = [
                    period for period in current_periods
                    if period["src"] == "sec"
                    and labels_match(old_period, period, kind)
                    and abs(financials.days_between(old_period["end"], period["end"])) <= financials.SAME_PERIOD_DAYS
                ]
                if len(matches) != 1:
                    missing_ends.append(old_period["end"])
                    continue
                current_value = matches[0]["values"].get(field)
                if current_value is None:
                    missing_ends.append(old_period["end"])
                elif current_value == old_period["values"].get(field):
                    verified_ends.append(matches[0]["end"])
                else:
                    # Keep a non-null SEC value even where Yahoo's previous value differed.
                    current_different_ends.append(matches[0]["end"])
            checks[f"{kind}:{field}"] = {
                "minimumBaselineYahooCells": minimum,
                "baselineYahooCellsChecked": len(old_candidates),
                "baselineValueMatches": len(verified_ends),
                "missingAfterRepair": len(missing_ends),
                "keptDifferentNonNullSecValues": len(current_different_ends),
                "verifiedEnds": verified_ends,
                "missingEnds": missing_ends,
                "differentSecEnds": current_different_ends,
                "passed": len(old_candidates) >= minimum and len(verified_ends) >= minimum and not missing_ends,
            }
        result[symbol] = checks
    baseline_bb = baseline_documents.get("BB", {})
    final_bb = final_documents.get("BB", {})
    old_bb, _ = financials.read_section(baseline_bb.get("quarterly"))
    new_bb, _ = financials.read_section(final_bb.get("quarterly"))
    old_row = next((row for row in old_bb if row["end"] == "2026-05-31" and row["src"] == "yahoo"), None)
    new_row = next((row for row in new_bb if row["end"] == "2026-05-31" and row["src"] == "sec"), None)
    old_shares = financials.finite_number(old_row["values"].get("dilutedShares")) if old_row else None
    new_shares = financials.finite_number(new_row["values"].get("dilutedShares")) if new_row else None
    share_delta = (
        abs(old_shares - new_shares) / max(old_shares, new_shares)
        if old_shares is not None and new_shares is not None and old_shares > 0 and new_shares > 0
        else None
    )
    same_split = split_metadata_matches(final_bb, baseline_bb)
    result["BB"]["perShareBasis"] = {
        "period": "2026-05-31",
        "baselineSource": old_row["src"] if old_row else None,
        "currentSource": new_row["src"] if new_row else None,
        "baselineDilutedShares": old_shares,
        "currentDilutedShares": new_shares,
        "relativeShareDifference": share_delta,
        "splitAdjustedMetadataMatches": same_split,
        "fiscalLabelsMatch": bool(old_row and new_row and labels_match(old_row, new_row, "quarterly")),
        "passed": bool(
            old_row and new_row and old_row["src"] == "yahoo" and new_row["src"] == "sec"
            and share_delta is not None and share_delta <= 0.01 and same_split
            and labels_match(old_row, new_row, "quarterly")
        ),
    }
    return result


def split_metadata_matches(current: dict[str, Any], baseline: dict[str, Any]) -> bool:
    current_split = current.get("split") if isinstance(current.get("split"), dict) else {}
    baseline_split = baseline.get("split") if isinstance(baseline.get("split"), dict) else {}
    return (
        isinstance(current_split.get("adjusted"), bool)
        and isinstance(baseline_split.get("adjusted"), bool)
        and current_split.get("adjusted") is baseline_split.get("adjusted")
        and current_split.get("events", []) == baseline_split.get("events", [])
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    revision = resolve_revision(args.baseline_revision)
    today_iso = date.today().isoformat()
    audit_path = Path(args.audit_out)
    previous_planned_counts: dict[str, Any] = {}
    try:
        previous_audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if previous_audit.get("version") == 2 and previous_audit.get("baselineRevision") == revision:
            previous_planned_counts = previous_audit.get("plannedRestorationCellCounts", {})
    except (OSError, json.JSONDecodeError):
        pass
    counts: dict[str, Counter[str]] = {"annual": Counter(), "quarterly": Counter()}
    excluded_counts: dict[str, Counter[str]] = {"annual": Counter(), "quarterly": Counter()}
    rows_by_symbol: dict[str, Any] = {}
    baseline_files = 0
    current_files = 0
    modified_files = 0
    baseline_documents: dict[str, dict[str, Any]] = {}
    final_documents: dict[str, dict[str, Any]] = {}
    pending_documents: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(Path(args.financials_dir).glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        symbol = current.get("symbol")
        if not isinstance(symbol, str) or current.get("market") != "US":
            continue
        current_files += 1
        baseline = read_git_document(revision, symbol)
        if baseline is None:
            rows_by_symbol[symbol] = {"restoredRows": {}, "excludedRows": {}}
            continue
        baseline_files += 1
        baseline_documents[symbol] = baseline
        if current.get("currency") != "USD" or baseline.get("currency") != "USD":
            rows_by_symbol[symbol] = {"restoredRows": {}, "excludedRows": {}, "skipped": "currency-mismatch-or-unknown"}
            continue
        restored: dict[str, list[dict[str, Any]]] = {}
        excluded: dict[str, list[dict[str, Any]]] = {}
        same_split_metadata = split_metadata_matches(current, baseline)
        for kind in ("annual", "quarterly"):
            current[kind], restored[kind], excluded[kind] = restore_section(
                current.get(kind), baseline.get(kind), kind, today_iso,
                allow_matching_share_basis=same_split_metadata,
            )
            for row in restored[kind]:
                counts[kind].update(row["fields"])
            for row in excluded[kind]:
                excluded_counts[kind].update(row["fields"].values())
        rows_by_symbol[symbol] = {"restoredRows": restored, "excludedRows": excluded}
        final_documents[symbol] = current
        if any(restored.values()):
            modified_files += 1
            pending_documents.append((path, current))

    target_checks = target_audit(baseline_documents, final_documents)
    missing_checks = [
        f"{symbol} {key}"
        for symbol, checks in target_checks.items()
        for key, result in checks.items()
        if not result["passed"]
    ]
    if not target_checks.get("BB", {}).get("perShareBasis", {}).get("passed"):
        missing_checks.append("BB quarterly per-share basis")
    if missing_checks:
        raise RuntimeError("expected source-checked Yahoo null fills were not restored: " + ", ".join(missing_checks))
    if not args.dry_run:
        for path, document in pending_documents:
            path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    current_counts = {kind: dict(sorted(field_counts.items())) for kind, field_counts in counts.items()}
    planned_counts = previous_planned_counts or current_counts
    applied_counts = current_counts if not args.dry_run else {kind: {} for kind in counts}
    summary = {
        "version": 2,
        "baselineRevision": revision,
        "origin": "pre-refresh Yahoo Finance fundamentals-timeseries values in version-1 USD files",
        "policy": "Same-fiscal-period one-to-one matches within the existing five-day tolerance; build_financials.merge_section null-fill behavior; SEC non-null values win; the only partial per-share exception requires Yahoo and SEC diluted shares within 1%, matching split-adjusted/events metadata, and same fiscal labels; no period insertion, relabel, split adjustment, or quality metadata changes.",
        "dryRun": args.dry_run,
        "currentUSFiles": current_files,
        "baselineFiles": baseline_files,
        "filesWithRestoredCellsThisRun": modified_files if not args.dry_run else 0,
        "plannedRestorationCellCounts": planned_counts,
        "cellsAppliedThisRun": applied_counts,
        "excludedCellCountsByReason": {
            kind: dict(sorted(field_counts.items())) for kind, field_counts in excluded_counts.items()
        },
        "excludedRows": {
            symbol: data["excludedRows"] for symbol, data in rows_by_symbol.items()
            if any(data["excludedRows"].values())
        },
        "targetAudit": target_checks,
        "symbols": rows_by_symbol,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Baseline {revision}; files {current_files}; files with cells to restore {modified_files}")
    for kind in ("annual", "quarterly"):
        print(kind + ": " + ", ".join(
            f"{field}={count}" for field, count in summary["plannedRestorationCellCounts"].get(kind, {}).items()
        ))
    print("Excluded cells: " + ", ".join(
        f"{reason}={count}"
        for kind_counts in summary["excludedCellCountsByReason"].values()
        for reason, count in kind_counts.items()
    ))
    print(f"Audit: {audit_path}")
    return summary


def main(argv: list[str] | None = None) -> int:
    run(parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Create a local-only report of US fiscal-period label and derived Q4 EPS changes.

Run from any directory with:
    python atlas/scripts/repair_fiscal_periods.py --dry-run

The command reads docs/report/financials/*.json and writes only
docs/report/fiscal-period-repair-dryrun.json. It never contacts SEC or changes a financials file.
"""
import argparse
import copy
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path

from build_financials import (
    DERIVED_COLUMN,
    Q4_EPS_FIELDS,
    Q4_EPS_SOURCE,
    assign_fiscal_labels,
    fiscal_label_eligible_ends,
    fill_q4_eps,
)
import detect_fiscal_period_mismatch as fiscal_detector


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FINANCIALS_DIR = ROOT / "docs" / "report" / "financials"
DEFAULT_REPORT_PATH = ROOT / "docs" / "report" / "fiscal-period-repair-dryrun.json"


def section_items(document, name):
    """Convert a stored parallel-array section to the in-memory period shape."""
    section = document.get(name)
    if not isinstance(section, dict):
        return []
    ends = section.get("end")
    if not isinstance(ends, list):
        return []
    count = len(ends)
    sources = section.get("src") or ["yahoo"] * count
    fys = section.get("fy") or [None] * count
    fqs = section.get("fq") or [None] * count
    columns = section.get("values") or {}
    result = []
    for index, end in enumerate(ends):
        values = {
            field: column[index] if isinstance(column, list) and index < len(column) else None
            for field, column in columns.items()
        }
        result.append({
            "end": end,
            "fy": fys[index] if index < len(fys) else None,
            "fq": fqs[index] if index < len(fqs) else None,
            "src": sources[index] if index < len(sources) else "yahoo",
            "values": values,
            "extra": {},
        })
    return result


def _value_by_end(periods, end, field):
    return next((item["values"].get(field) for item in periods if item["end"] == end), None)


PROTECTED_EPS_METADATA = ("secQ4EpsFacts", "epsOverrides", "q4EpsEstimates")


def protected_q4_eps_fields(document, end):
    """EPS fields backed by metadata that this repair must leave untouched."""
    protected = set()
    for name in PROTECTED_EPS_METADATA:
        by_end = document.get(name)
        facts = by_end.get(end) if isinstance(by_end, dict) else None
        if isinstance(facts, dict):
            protected.update(field for field in Q4_EPS_FIELDS if field in facts)
    return protected


def infer_existing_derived_q4_eps(
    document, annual, quarterly, eligible_ends=None,
    alternate_annual=None, alternate_quarterly=None,
):
    """Return Q4 EPS fields that the existing fill rule reproduces exactly.

    The stored ``derived`` column is per period, and can also mean that a cash-flow or net-income
    value was subtracted. It is not field-level EPS provenance. Therefore only classify an EPS as
    derived when the existing Q4 rule, with that field blanked in a copy, reproduces its stored
    value exactly. This narrows the candidate set; the stored schema cannot distinguish a reported
    EPS that coincidentally equals the formula result.
    """
    section = document.get("quarterly") or {}
    marks = section.get(DERIVED_COLUMN) or [None] * len(quarterly)
    alternate_annual = alternate_annual if alternate_annual is not None else annual
    alternate_quarterly = alternate_quarterly if alternate_quarterly is not None else quarterly
    alternate_by_end = {item["end"]: item for item in alternate_quarterly}
    inferred = {}
    for index, item in enumerate(quarterly):
        if (eligible_ends is not None and item.get("end") not in eligible_ends):
            continue
        alternate = alternate_by_end.get(item["end"], {})
        if (index >= len(marks) or not marks[index] or item.get("src") != Q4_EPS_SOURCE
                or alternate.get("src", item.get("src")) != Q4_EPS_SOURCE
                or (item.get("fq") != 4 and alternate.get("fq") != 4)):
            continue
        for field in Q4_EPS_FIELDS:
            if field in protected_q4_eps_fields(document, item["end"]):
                continue
            value = item["values"].get(field)
            if value is None:
                continue
            end = item["end"]
            for annual_periods, quarter_periods in (
                (annual, quarterly), (alternate_annual, alternate_quarterly),
            ):
                matching_row = next((row for row in quarter_periods if row["end"] == end), None)
                if matching_row is None or matching_row.get("fq") != 4:
                    continue
                candidate = copy.deepcopy(quarter_periods)
                for row in candidate:
                    if row["end"] == end:
                        row["values"][field] = None
                        break
                fill_q4_eps(copy.deepcopy(annual_periods), candidate)
                if _value_by_end(candidate, end, field) == value:
                    inferred[(end, field)] = value
                    break
    return inferred


def label_differences(old_annual, old_quarterly, new_annual, new_quarterly):
    diffs = {"annual": [], "quarterly": []}
    for section, before, after in (
        ("annual", old_annual, new_annual),
        ("quarterly", old_quarterly, new_quarterly),
    ):
        after_by_end = {item["end"]: item for item in after}
        for old in before:
            new = after_by_end[old["end"]]
            if old.get("fy") == new.get("fy") and old.get("fq") == new.get("fq"):
                continue
            row = {
                "end": old["end"],
                "old": {"fy": old.get("fy"), "fq": old.get("fq")},
                "new": {"fy": new.get("fy"), "fq": new.get("fq")},
            }
            if section == "quarterly":
                row["src"] = old.get("src")
            diffs[section].append(row)
    return diffs


def input_q4_mismatch_counts(document, annual, quarterly):
    """Count existing Q4 rows whose end differs from a same-FY annual period end."""
    annual_ends = defaultdict(set)
    for item in annual:
        annual_ends[item.get("fy")].add(item["end"])
    marks = (document.get("quarterly") or {}).get(DERIVED_COLUMN) or [None] * len(quarterly)
    mismatches = [
        index for index, item in enumerate(quarterly)
        if item.get("fq") == 4
        and item.get("fy") in annual_ends
        and item["end"] not in annual_ends[item.get("fy")]
    ]
    marked = sum(index < len(marks) and bool(marks[index]) for index in mismatches)
    return len(mismatches), marked


def q4_eps_differences(
    old_annual, old_quarterly, new_annual, new_quarterly, existing_derived,
    document=None, eligible_ends=None,
):
    """Compare current and proposed fill_q4_eps outcomes without touching raw values."""
    old_candidate = copy.deepcopy(old_quarterly)
    new_candidate = copy.deepcopy(new_quarterly)
    for end, field in existing_derived:
        for section in (old_candidate, new_candidate):
            for item in section:
                if item["end"] == end:
                    item["values"][field] = None
                    break

    old_filled = set(fill_q4_eps(copy.deepcopy(old_annual), old_candidate))
    new_filled = set(fill_q4_eps(copy.deepcopy(new_annual), new_candidate))
    if eligible_ends is not None:
        old_filled = {key for key in old_filled if key[0] in eligible_ends}
        new_filled = {key for key in new_filled if key[0] in eligible_ends}
    old_values = {
        key: _value_by_end(old_candidate, key[0], key[1]) for key in old_filled
    }
    new_values = {
        key: _value_by_end(new_candidate, key[0], key[1]) for key in new_filled
    }
    original_values = {
        (item["end"], field): item["values"].get(field)
        for item in old_quarterly for field in Q4_EPS_FIELDS
    }
    records = []

    for key, old_value in existing_derived.items():
        if document is not None and key[1] in protected_q4_eps_fields(document, key[0]):
            continue
        new_value = new_values.get(key)
        if new_value is None:
            action = "nulled"
        elif new_value == old_value:
            action = "re-derived"
        else:
            action = "changed"
        records.append({
            "end": key[0], "field": key[1], "action": action,
            "old": old_value, "new": new_value,
        })

    for key, new_value in new_values.items():
        if document is not None and key[1] in protected_q4_eps_fields(document, key[0]):
            continue
        if key in existing_derived:
            continue
        original = original_values.get(key)
        if original is not None:
            # fill_q4_eps does not replace a non-null, non-derived EPS value.
            continue
        old_value = old_values.get(key)
        if old_value is None:
            records.append({
                "end": key[0], "field": key[1], "action": "newly-derived",
                "old": None, "new": new_value,
            })
        elif old_value != new_value:
            records.append({
                "end": key[0], "field": key[1], "action": "changed",
                "old": old_value, "new": new_value,
            })

    records.sort(key=lambda row: (row["end"], row["field"], row["action"]))
    return records


def _json_file(path):
    with path.open(encoding="utf-8") as file:
        document = json.load(file)
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return document


def _flagged_row_ends(flagged_file):
    if not flagged_file:
        return set(), set()
    quarterly_ends = {
        row["end"] for row in flagged_file.get("affectedQ4", []) + flagged_file.get("otherQuarterFlags", [])
    }
    q4_ends = {row["end"] for row in flagged_file.get("affectedQ4", [])}
    return quarterly_ends, q4_ends


def _quarter_spacing_residual(symbol, row):
    previous = row.get("previousQuarter")
    if not previous:
        return {
            "symbol": symbol, "end": row["end"], "fy": row["fy"], "fq": row["fq"],
            "previousQuarter": None, "actualDays": None, "expectedRangeDays": None,
            "residualDays": None, "reason": "No preceding quarterly row is available.",
        }
    quarter_gap = (row["fq"] - previous["fq"]) % 4 or 4
    lower, upper = 84 * quarter_gap, 119 * quarter_gap
    days = previous["days"]
    if days < lower:
        residual = days - lower
        reason = f"{days} days is {abs(residual)} days below {lower}–{upper} for a {quarter_gap}-quarter label gap."
    elif days > upper:
        residual = days - upper
        reason = f"{days} days is {residual} days above {lower}–{upper} for a {quarter_gap}-quarter label gap."
    else:
        residual = 0
        reason = f"The {quarter_gap}-quarter label gap is inconsistent with the {days}-day period interval."
    return {
        "symbol": symbol,
        "end": row["end"],
        "fy": row["fy"],
        "fq": row["fq"],
        "previousQuarter": previous,
        "actualDays": days,
        "expectedRangeDays": [lower, upper],
        "residualDays": residual,
        "reason": reason,
    }


def build_report(financials_dir):
    files = []
    counts = defaultdict(int)
    eps_field_actions = {
        field: {"reDerived": 0, "changed": 0, "nulled": 0, "newlyDerived": 0}
        for field in Q4_EPS_FIELDS
    }
    recognized_derived_eps_values = 0
    recognized_derived_eps_rows = set()
    baseline_detector = fiscal_detector.build_report(financials_dir)
    flagged_by_file = {entry["file"]: entry for entry in baseline_detector["flaggedFiles"]}
    baseline_spacing_residuals = [
        _quarter_spacing_residual(entry["symbol"], row)
        for entry in baseline_detector["flaggedFiles"]
        for row in entry["affectedQ4"]
        if fiscal_detector.QUARTER_SPACING in row["rules"]
    ]
    after_spacing_residuals = []
    after_q4_mismatches = 0
    for path in sorted(financials_dir.glob("*.json"), key=lambda item: item.name):
        document = _json_file(path)
        if document.get("market") != "US":
            continue
        symbol = document.get("symbol") or path.stem
        flagged_file = flagged_by_file.get(path.name)
        flagged_ends, flagged_q4_ends = _flagged_row_ends(flagged_file)
        old_annual = section_items(document, "annual")
        old_quarterly = section_items(document, "quarterly")
        new_annual = copy.deepcopy(old_annual)
        new_quarterly = copy.deepcopy(old_quarterly)
        fallback_month = document.get("fyEndMonth")
        if not isinstance(fallback_month, int) or not 1 <= fallback_month <= 12:
            fallback_month = None
        eligible_ends = fiscal_label_eligible_ends(old_annual, old_quarterly)
        assign_fiscal_labels(
            new_annual, new_quarterly, fallback_end_month=fallback_month,
            eligible_quarter_ends=eligible_ends,
        )

        diffs = label_differences(old_annual, old_quarterly, new_annual, new_quarterly)
        label_rows_changed = len(diffs["annual"]) + len(diffs["quarterly"])
        if label_rows_changed and not flagged_ends:
            raise AssertionError(f"{path.name}: label changes in a file without a flagged quarterly row")
        input_mismatches, input_mismatches_marked = input_q4_mismatch_counts(
            document, old_annual, old_quarterly,
        )
        derived = infer_existing_derived_q4_eps(
            document, old_annual, old_quarterly, eligible_ends=flagged_q4_ends,
            alternate_annual=new_annual, alternate_quarterly=new_quarterly,
        )
        recognized_derived_eps_values += len(derived)
        recognized_derived_eps_rows.update(end for end, _field in derived)
        q4_diffs = q4_eps_differences(
            old_annual, old_quarterly, new_annual, new_quarterly, derived,
            document=document, eligible_ends=eligible_ends,
        )
        for row in q4_diffs:
            eps_field_actions[row["field"]][{
                "re-derived": "reDerived",
                "changed": "changed",
                "nulled": "nulled",
                "newly-derived": "newlyDerived",
            }[row["action"]]] += 1

        qblock = document.get("quarterly") or {}
        ends = qblock.get("end") if isinstance(qblock.get("end"), list) else []
        marks = qblock.get(DERIVED_COLUMN) or []
        derived_q4_rows = sum(
            index < len(marks) and bool(marks[index]) and end in flagged_q4_ends
            for index, end in enumerate(ends)
        )

        after_document = dict(document)
        after_annual = dict(document.get("annual") or {})
        after_annual["fy"] = [item.get("fy") for item in new_annual]
        after_quarterly = dict(qblock)
        after_quarterly["fy"] = [item.get("fy") for item in new_quarterly]
        after_quarterly["fq"] = [item.get("fq") for item in new_quarterly]
        after_document["annual"] = after_annual
        after_document["quarterly"] = after_quarterly
        after_result = fiscal_detector.analyze(after_document)
        after_q4_mismatches += sum(
            fiscal_detector.Q4_VS_ANNUAL in row["rules"]
            for row in after_result["affectedQ4"]
        )
        after_spacing_residuals.extend(
            _quarter_spacing_residual(symbol, row)
            for row in after_result["affectedQ4"]
            if fiscal_detector.QUARTER_SPACING in row["rules"]
        )

        counts["usFiles"] += 1
        counts["annualRowsChanged"] += len(diffs["annual"])
        counts["quarterlyRowsChanged"] += len(diffs["quarterly"])
        counts["labelRowsChanged"] += label_rows_changed
        counts["filesWithLabelChanges"] += bool(label_rows_changed)
        counts["filesWithFlaggedRows"] += bool(flagged_file)
        counts["filesWithFlaggedQuarterRows"] += bool(flagged_ends)
        counts["filesWithoutFlaggedQuarterRowsWithLabelChanges"] += bool(
            not flagged_ends and label_rows_changed
        )
        counts["baselineFlaggedRows"] += len(flagged_ends)
        counts["q4RowsWithDerivedMarker"] += derived_q4_rows
        counts["inputQ4EndAnnualEndMismatchRows"] += input_mismatches
        counts["inputQ4EndAnnualEndMismatchRowsWithDerivedFlag"] += input_mismatches_marked
        counts["q4RowsAffected"] += len({row["end"] for row in q4_diffs})
        counts["q4RowsWouldBeDerived"] += len({
            row["end"] for row in q4_diffs if row["action"] in ("newly-derived", "re-derived", "changed")
        })
        counts["q4RowsWouldBeNulled"] += len({
            row["end"] for row in q4_diffs if row["action"] == "nulled"
        })
        counts["q4ValuesReDerived"] += sum(row["action"] == "re-derived" for row in q4_diffs)
        counts["q4ValuesNewlyDerived"] += sum(row["action"] == "newly-derived" for row in q4_diffs)
        counts["q4ValuesChanged"] += sum(row["action"] == "changed" for row in q4_diffs)
        counts["q4ValuesNulled"] += sum(row["action"] == "nulled" for row in q4_diffs)

        files.append({
            "symbol": symbol,
            "detectorFlaggedRowCount": len(flagged_ends),
            "labelRowsChanged": label_rows_changed,
            "annualLabelChanges": diffs["annual"],
            "quarterlyLabelChanges": diffs["quarterly"],
            "q4EpsChanges": q4_diffs,
        })

    if counts["filesWithFlaggedRows"] != baseline_detector["totals"]["filesFlagged"]:
        raise AssertionError("per-file detector flag count does not match the detector report")
    if counts["filesWithoutFlaggedQuarterRowsWithLabelChanges"]:
        raise AssertionError("a file without a flagged quarterly row has label changes")
    if after_q4_mismatches:
        raise AssertionError(f"detector still finds {after_q4_mismatches} Q4/annual-end mismatches")
    if counts["q4RowsWithDerivedMarker"] != 263:
        raise AssertionError(
            f"expected 263 flagged Q4 rows with the derived marker, found {counts['q4RowsWithDerivedMarker']}"
        )
    counts["nonDerivedValuesChanged"] = 0
    counts["afterRepairQ4EndAnnualEndRowsWithSameFyAnnual"] = after_q4_mismatches

    derived_field_actions = {
        field: {
            key: eps_field_actions[field][key]
            for key in ("reDerived", "changed", "nulled", "newlyDerived")
        }
        for field in Q4_EPS_FIELDS
    }
    derived_field_actions["total"] = {
        key: sum(eps_field_actions[field][key] for field in Q4_EPS_FIELDS)
        for key in ("reDerived", "changed", "nulled", "newlyDerived")
    }

    return {
        "version": 1,
        "scope": {
            "market": "US",
            "files": counts["usFiles"],
            "baselineDetectorFlaggedFiles": baseline_detector["totals"]["filesFlagged"],
            "quarterOwnerRule": "first annual end on or after quarter end, within 371 days",
            "labelChanges": "only detector-flagged quarterly rows are relabeled; unlabeled rows are initialized",
            "existingDerivedEpsRecognition": (
                "row-level derived marker plus exact fill_q4_eps reproduction after blanking "
                "the EPS field in a copy; secQ4EpsFacts, epsOverrides, and q4EpsEstimates are excluded"
            ),
        },
        "counts": dict(sorted(counts.items())),
        "derivedQ4Rows": {
            "rowsWithDerivedMarker": counts["q4RowsWithDerivedMarker"],
            "rowsWithRecognizedDerivedEps": len(recognized_derived_eps_rows),
            "rowsWithDerivedMarkerButNoRecognizedDerivedEps": (
                counts["q4RowsWithDerivedMarker"] - len(recognized_derived_eps_rows)
            ),
            "recognizedDerivedEpsValues": recognized_derived_eps_values,
            "epsFieldActions": derived_field_actions,
        },
        "detector": {
            "baseline": {
                "filesFlagged": baseline_detector["totals"]["filesFlagged"],
                "q4EndAnnualEndRowsWithSameFyAnnual": baseline_detector["totals"]["q4RowsFlaggedByRule"].get(
                    fiscal_detector.Q4_VS_ANNUAL, 0
                ),
                "quarterSpacingResiduals": baseline_spacing_residuals,
            },
            "afterRepair": {
                "q4EndAnnualEndRowsWithSameFyAnnual": after_q4_mismatches,
                "quarterSpacingResiduals": after_spacing_residuals,
            },
        },
        "files": files,
    }


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as file:
            temporary_path = Path(file.name)
            file.write(content)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", required=True,
                        help="read local US files and write only the dry-run report")
    parser.add_argument("--financials-dir", type=Path, default=DEFAULT_FINANCIALS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    financials_dir = args.financials_dir.resolve()
    output_path = args.output.resolve()
    if not financials_dir.is_dir():
        raise SystemExit(f"financials directory not found: {financials_dir}")
    if os.path.commonpath((str(financials_dir), str(output_path))) == str(financials_dir):
        raise SystemExit("the dry-run report must not be written under docs/report/financials/")
    report = build_report(financials_dir)
    write_report(output_path, report)
    counts = report["counts"]
    print(
        f"US files: {counts['usFiles']}; label changes: "
        f"{counts['annualRowsChanged']} annual, {counts['quarterlyRowsChanged']} quarterly; "
        f"Q4 EPS: {counts['q4ValuesNewlyDerived']} newly derived, "
        f"{counts['q4ValuesChanged']} changed, {counts['q4ValuesNulled']} nulled"
    )
    print(output_path)


if __name__ == "__main__":
    main()

"""Report or apply the US fiscal-period label and derived Q4 EPS repair.

Run from any directory with:
    python atlas/scripts/repair_fiscal_periods.py --dry-run
    python atlas/scripts/repair_fiscal_periods.py --apply

--dry-run reads docs/report/financials/*.json and writes only
docs/report/fiscal-period-repair-dryrun.json.

--apply writes, to docs/report/financials/, exactly the changes the dry-run report lists, for
every symbol except APPLY_EXCLUDED_SYMBOLS. It writes no other file. Every file is checked before
any file is written: a file with dry-run changes must be either untouched (its planned changes
equal the dry-run changes) or already applied (each dry-run value holds its new value). It never
contacts SEC.
"""
import argparse
import copy
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from build_financials import (
    DERIVED_COLUMN,
    Q4_EPS_FIELDS,
    Q4_EPS_SOURCE,
    assign_fiscal_labels,
    fiscal_label_eligible_ends,
    fill_q4_eps,
)
from build_valuation_us import write_atomic
import detect_fiscal_period_mismatch as fiscal_detector


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FINANCIALS_DIR = ROOT / "docs" / "report" / "financials"
DEFAULT_REPORT_PATH = ROOT / "docs" / "report" / "fiscal-period-repair-dryrun.json"

# Transition-year symbols whose fiscal-label repair is a later task. --apply never writes their
# files; their dry-run changes are reported separately.
APPLY_EXCLUDED_SYMBOLS = frozenset({
    "AIOT", "BRT", "CHRN", "DXLG", "EUDA", "FCUV", "GRNQ", "IMMR", "NTST", "ODYS",
    "RJET", "TRUG", "UA", "UAA", "VFC", "VTOL", "YUMC",
})

# Q4 EPS actions that change a stored value. "re-derived" leaves the stored value as it is.
WRITE_EPS_ACTIONS = ("changed", "nulled", "newly-derived")
# Top-level keys and quarterly columns that --apply must never change.
PROTECTED_TOP_LEVEL = ("secQ4EpsFacts", "epsOverrides", "q4EpsEstimates")
PROTECTED_QUARTERLY_COLUMNS = ("epsQuality",)
_ABSENT = "<absent>"


class ApplyError(ValueError):
    """Raised when the planned changes do not match the dry run or a write check fails."""


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


def plan_path(path, flagged_by_file):
    """Plan the repair for one financials file on disk. Returns None for files that are not US."""
    document = _json_file(path)
    if document.get("market") != "US":
        return None
    plan = plan_document(document, path.name, flagged_by_file.get(path.name))
    plan["path"] = path
    return plan


def plan_document(document, file_name, flagged_file):
    """Plan the repair for one US document, in memory. flagged_file is the detector's entry or None."""
    symbol = document.get("symbol") or Path(file_name).stem
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
        raise AssertionError(f"{file_name}: label changes in a file without a flagged quarterly row")
    input_mismatches, input_mismatches_marked = input_q4_mismatch_counts(
        document, old_annual, old_quarterly,
    )
    derived = infer_existing_derived_q4_eps(
        document, old_annual, old_quarterly, eligible_ends=flagged_q4_ends,
        alternate_annual=new_annual, alternate_quarterly=new_quarterly,
    )
    q4_diffs = q4_eps_differences(
        old_annual, old_quarterly, new_annual, new_quarterly, derived,
        document=document, eligible_ends=eligible_ends,
    )

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

    return {
        "file": file_name,
        "document": document,
        "symbol": symbol,
        "flaggedFilePresent": bool(flagged_file),
        "flaggedEnds": flagged_ends,
        "derivedRecognized": derived,
        "q4Diffs": q4_diffs,
        "derivedQ4Rows": derived_q4_rows,
        "inputQ4Mismatches": input_mismatches,
        "inputQ4MismatchesMarked": input_mismatches_marked,
        "afterQ4Mismatches": sum(
            fiscal_detector.Q4_VS_ANNUAL in row["rules"] for row in after_result["affectedQ4"]
        ),
        "afterSpacingResiduals": [
            _quarter_spacing_residual(symbol, row)
            for row in after_result["affectedQ4"]
            if fiscal_detector.QUARTER_SPACING in row["rules"]
        ],
        "record": {
            "symbol": symbol,
            "detectorFlaggedRowCount": len(flagged_ends),
            "labelRowsChanged": label_rows_changed,
            "annualLabelChanges": diffs["annual"],
            "quarterlyLabelChanges": diffs["quarterly"],
            "q4EpsChanges": q4_diffs,
        },
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
        plan = plan_path(path, flagged_by_file)
        if plan is None:
            continue
        derived = plan["derivedRecognized"]
        recognized_derived_eps_values += len(derived)
        recognized_derived_eps_rows.update(end for end, _field in derived)
        q4_diffs = plan["q4Diffs"]
        for row in q4_diffs:
            eps_field_actions[row["field"]][{
                "re-derived": "reDerived",
                "changed": "changed",
                "nulled": "nulled",
                "newly-derived": "newlyDerived",
            }[row["action"]]] += 1
        record = plan["record"]
        label_rows_changed = record["labelRowsChanged"]
        flagged_ends = plan["flaggedEnds"]
        after_q4_mismatches += plan["afterQ4Mismatches"]
        after_spacing_residuals.extend(plan["afterSpacingResiduals"])

        counts["usFiles"] += 1
        counts["annualRowsChanged"] += len(record["annualLabelChanges"])
        counts["quarterlyRowsChanged"] += len(record["quarterlyLabelChanges"])
        counts["labelRowsChanged"] += label_rows_changed
        counts["filesWithLabelChanges"] += bool(label_rows_changed)
        counts["filesWithFlaggedRows"] += plan["flaggedFilePresent"]
        counts["filesWithFlaggedQuarterRows"] += bool(flagged_ends)
        counts["filesWithoutFlaggedQuarterRowsWithLabelChanges"] += bool(
            not flagged_ends and label_rows_changed
        )
        counts["baselineFlaggedRows"] += len(flagged_ends)
        counts["q4RowsWithDerivedMarker"] += plan["derivedQ4Rows"]
        counts["inputQ4EndAnnualEndMismatchRows"] += plan["inputQ4Mismatches"]
        counts["inputQ4EndAnnualEndMismatchRowsWithDerivedFlag"] += plan["inputQ4MismatchesMarked"]
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

        files.append(record)

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


def normalized_diff(record):
    """The changes --apply writes for one file, as a frozenset of (section, end, field, old, new).

    Label changes are fy/fq values. EPS changes are Q4 values that the dry run changes (changed,
    nulled, newly-derived). Re-derived values keep their stored value, so they are not changes.
    Values are JSON text so that int and float differences are compared exactly.
    """
    changes = set()
    for section, key in (("annual", "annualLabelChanges"), ("quarterly", "quarterlyLabelChanges")):
        for row in record[key]:
            for field in ("fy", "fq"):
                if row["old"][field] != row["new"][field]:
                    changes.add((section, row["end"], field,
                                 json.dumps(row["old"][field]), json.dumps(row["new"][field])))
    for row in record["q4EpsChanges"]:
        if row["action"] in WRITE_EPS_ACTIONS:
            changes.add(("quarterly", row["end"], f"values.{row['field']}",
                         json.dumps(row["old"]), json.dumps(row["new"])))
    return frozenset(changes)


def _end_index(block, end):
    ends = block.get("end")
    if not isinstance(ends, list) or ends.count(end) != 1:
        raise ApplyError(f"period end {end} must appear exactly once in its section")
    return ends.index(end)


def apply_diff(document, diff):
    """Return a copy of document with exactly the normalized diff applied."""
    updated = copy.deepcopy(document)
    for section, end, field, _old_json, new_json in sorted(diff):
        block = updated[section]
        index = _end_index(block, end)
        value = json.loads(new_json)
        if field.startswith("values."):
            column = block["values"][field[len("values."):]]
            column[index] = value
        else:
            block[field][index] = value
    return updated


def leaf_changes(before, after, path=()):
    """Map each JSON path whose value differs between before and after to (old_json, new_json)."""
    if isinstance(before, dict) and isinstance(after, dict):
        changes = {}
        for key in set(before) | set(after):
            if key not in before or key not in after:
                changes[path + (key,)] = (
                    json.dumps(before.get(key, _ABSENT)), json.dumps(after.get(key, _ABSENT)),
                )
            else:
                changes.update(leaf_changes(before[key], after[key], path + (key,)))
        return changes
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        changes = {}
        for index, (old, new) in enumerate(zip(before, after)):
            changes.update(leaf_changes(old, new, path + (index,)))
        return changes
    if json.dumps(before) != json.dumps(after):
        return {path: (json.dumps(before), json.dumps(after))}
    return {}


def verify_written_change(before, after, diff):
    """Check that the only leaves that differ are the dry-run changes, and that no guarded value moved."""
    actual = leaf_changes(before, after)
    expected = {}
    for section, end, field, old_json, new_json in diff:
        index = _end_index(before[section], end)
        expected[(section, *field.split("."), index)] = (old_json, new_json)
    if actual != expected:
        raise ApplyError("the rewritten document differs from the dry-run changes")
    for path, (old_json, _new_json) in actual.items():
        if path[0] in PROTECTED_TOP_LEVEL or (path[0] == "quarterly" and path[1] in PROTECTED_QUARTERLY_COLUMNS):
            raise ApplyError(f"guarded path changed: {path}")
        if path[0] == "quarterly" and path[1] == "values" and old_json != "null":
            marks = before["quarterly"].get(DERIVED_COLUMN) or []
            if path[-1] >= len(marks) or not marks[path[-1]]:
                raise ApplyError(f"non-derived EPS value would change: {path}")


def _diff_summary(record):
    """Changed rows per section and Q4 EPS actions for one file, as the dry run records them."""
    actions = Counter(
        row["action"] for row in record["q4EpsChanges"] if row["action"] in WRITE_EPS_ACTIONS
    )
    return {
        "annualLabelRows": len(record["annualLabelChanges"]),
        "quarterlyLabelRows": len(record["quarterlyLabelChanges"]),
        "eps": {action: actions.get(action, 0) for action in WRITE_EPS_ACTIONS},
    }


def diff_status(document, diff):
    """'pending' when every dry-run change still holds its old value, 'applied' when every one holds its new value.

    Anything else (a value that is neither, or a file half-way between the two) stops the apply.
    """
    statuses = set()
    for section, end, field, old_json, new_json in diff:
        block = document[section]
        index = _end_index(block, end)
        current = block["values"][field[len("values."):]][index] if field.startswith("values.") else block[field][index]
        current_json = json.dumps(current)
        if current_json == old_json:
            statuses.add("pending")
        elif current_json == new_json:
            statuses.add("applied")
        else:
            raise ApplyError(f"{end} {field} holds {current_json}, neither the dry-run old nor new value")
    if statuses == {"pending"}:
        return "pending"
    if statuses == {"applied"}:
        return "applied"
    raise ApplyError("a file is partly applied; nothing was written")


def apply_repairs(financials_dir, dry_run_report, excluded=APPLY_EXCLUDED_SYMBOLS):
    """Write exactly the dry-run changes for non-excluded files. Returns a summary dict.

    Every file is checked before anything is written: a file with dry-run changes must be either
    entirely pending (all old values) or entirely applied (all new values); a file without dry-run
    changes must have no planned change. The plan for a pending file must equal its dry-run changes.
    Re-planning an applied file can propose further changes; they are reported, never written.
    Each rewritten document is verified against the dry run before it replaces the file.
    """
    expected_by_symbol = {entry["symbol"]: entry for entry in dry_run_report["files"]}
    baseline = fiscal_detector.build_report(financials_dir)
    flagged_by_file = {entry["file"]: entry for entry in baseline["flaggedFiles"]}

    pending = []
    applied = []
    replan_after_apply = []
    for path in sorted(financials_dir.glob("*.json"), key=lambda item: item.name):
        plan = plan_path(path, flagged_by_file)
        if plan is None:
            continue
        symbol = plan["symbol"]
        planned = normalized_diff(plan["record"])
        expected_entry = expected_by_symbol.get(symbol)
        expected = normalized_diff(expected_entry) if expected_entry else frozenset()
        if not expected:
            if planned:
                raise ApplyError(f"{path.name}: planned changes are not in the dry run; nothing was written")
            continue
        if diff_status(plan["document"], expected) == "applied":
            applied.append(symbol)
            if planned:
                replan_after_apply.append({"symbol": symbol, **_diff_summary(plan["record"])})
            continue
        if planned != expected:
            raise ApplyError(f"{path.name}: planned changes differ from the dry run; nothing was written")
        pending.append({
            "path": path, "symbol": symbol, "excluded": symbol in excluded,
            "document": plan["document"], "diff": expected, "record": plan["record"],
        })

    rewrites = []
    for item in pending:
        if item["excluded"]:
            continue
        updated = apply_diff(item["document"], item["diff"])
        verify_written_change(item["document"], updated, item["diff"])
        rewrites.append((item, updated))

    written = []
    for item, updated in rewrites:
        write_atomic(str(item["path"]), updated)
        if json.loads(item["path"].read_text(encoding="utf-8")) != updated:
            raise ApplyError(f"{item['path'].name}: the written file does not match the verified document")
        written.append(item)

    return {
        "written": [
            {"symbol": item["symbol"], "file": item["path"].name, **_diff_summary(item["record"])}
            for item in written
        ],
        "excluded": [
            {"symbol": item["symbol"], **_diff_summary(item["record"])}
            for item in sorted(
                (item for item in pending if item["excluded"]), key=lambda item: item["symbol"],
            )
        ],
        "alreadyApplied": sorted(applied),
        "replanAfterApply": replan_after_apply,
    }


FIXPOINT_MAX_PASSES = 5
FIXPOINT_REPORT_PATH = ROOT / "docs" / "report" / "fiscal-period-fixpoint-dryrun.json"
DETECTOR_REPORT_PATH = ROOT / "docs" / "report" / "fiscal-period-mismatch.json"
EPS_ACTION_COUNTS = {"newly-derived": "newlyDerived", "nulled": "nulled", "changed": "changed"}


def detector_flag_entry(document, file_name):
    """The detector's flagged entry for one in-memory document, or None when the detector does not flag it."""
    result = fiscal_detector.analyze(document)
    if not result["rules"]:
        return None
    return {
        "file": file_name,
        "affectedQ4": result["affectedQ4"],
        "otherQuarterFlags": result["otherQuarterFlags"],
    }


def converge(run_pass, max_passes=FIXPOINT_MAX_PASSES):
    """Run passes until one makes no change, stopping after max_passes.

    run_pass(number) makes one pass and returns the list of changes it made; an empty list means no
    change. Returns (the change list of each pass run, converged). converged is True only when the
    last pass run made no change.
    """
    if max_passes < 1:
        raise ValueError("max_passes must be at least 1")
    results = []
    for number in range(1, max_passes + 1):
        changes = run_pass(number)
        results.append(changes)
        if not changes:
            return results, True
    return results, False


def _eps_changes_by_action(record):
    counts = {name: 0 for name in EPS_ACTION_COUNTS.values()}
    counts["reDerivedNoChange"] = 0
    for row in record["q4EpsChanges"]:
        if row["action"] in EPS_ACTION_COUNTS:
            counts[EPS_ACTION_COUNTS[row["action"]]] += 1
        elif row["action"] == "re-derived":
            counts["reDerivedNoChange"] += 1
    return counts


def run_fixpoint(financials_dir, excluded=APPLY_EXCLUDED_SYMBOLS, max_passes=FIXPOINT_MAX_PASSES):
    """Return the fixed-point dry-run report. Writes nothing. See compute_fixpoint."""
    return compute_fixpoint(financials_dir, excluded, max_passes)[0]


def compute_fixpoint(financials_dir, excluded=APPLY_EXCLUDED_SYMBOLS, max_passes=FIXPOINT_MAX_PASSES):
    """Re-plan the repair in memory, pass after pass, until a pass makes no change. Writes nothing.

    Returns (report, states, disk_paths): states maps each changed file name to its in-memory document
    after all passes.

    Pass 1 plans every US file as it is on disk. A later pass plans only the files that the previous
    pass changed, because a file that did not change would plan exactly what it planned before, and
    that plan was empty. Each pass's change set goes through the same planner and diff as the dry run,
    and is checked with verify_written_change before the in-memory document moves on. Excluded
    symbols are planned in pass 1 and reported, but never changed in memory.
    """
    financials_dir = Path(financials_dir)
    flagged_by_file = {
        entry["file"]: entry for entry in fiscal_detector.build_report(financials_dir)["flaggedFiles"]
    }
    disk_paths = {path.name: path for path in sorted(financials_dir.glob("*.json"))}
    states = {}
    active = []
    excluded_plans = {}
    pass_reports = []

    def run_pass(number):
        nonlocal active
        names = list(active)
        if number == 1:
            plans = (
                plan for plan in (plan_path(path, flagged_by_file) for path in disk_paths.values())
                if plan is not None
            )
        else:
            plans = (
                plan_document(states[name], name, detector_flag_entry(states[name], name))
                for name in names
            )
        report = {
            "pass": number, "filesPlanned": 0, "filesChanged": 0,
            "quarterlyLabelRows": 0, "annualLabelRows": 0,
            "eps": {"newlyDerived": 0, "nulled": 0, "changed": 0, "reDerivedNoChange": 0},
        }
        changes = []
        for plan in plans:
            name, symbol, record = plan["file"], plan["symbol"], plan["record"]
            if symbol in excluded:
                if number == 1:
                    excluded_plans[symbol] = {
                        "file": name,
                        "quarterlyLabelRows": len(record["quarterlyLabelChanges"]),
                        "annualLabelRows": len(record["annualLabelChanges"]),
                        "eps": _eps_changes_by_action(record),
                    }
                continue
            report["filesPlanned"] += 1
            eps = _eps_changes_by_action(record)
            report["eps"]["reDerivedNoChange"] += eps["reDerivedNoChange"]
            diff = normalized_diff(record)
            if not diff:
                continue
            before = plan["document"]
            updated = apply_diff(before, diff)
            verify_written_change(before, updated, diff)
            states[name] = updated
            changes.append({"file": name, "symbol": symbol, "diff": diff, "record": record})
            report["filesChanged"] += 1
            report["quarterlyLabelRows"] += len(record["quarterlyLabelChanges"])
            report["annualLabelRows"] += len(record["annualLabelChanges"])
            for key in ("newlyDerived", "nulled", "changed"):
                report["eps"][key] += eps[key]
        pass_reports.append(report)
        active = [change["file"] for change in changes]
        return changes

    results, converged = converge(run_pass, max_passes)
    report = _fixpoint_report(
        financials_dir, disk_paths, states, results, converged, pass_reports, excluded_plans,
        excluded, max_passes,
    )
    return report, states, disk_paths


def _fixpoint_report(financials_dir, disk_paths, states, results, converged, pass_reports,
                     excluded_plans, excluded, max_passes):
    eps_by_pass = []
    for number, changes in enumerate(results, start=1):
        for change in changes:
            for row in change["record"]["q4EpsChanges"]:
                if row["action"] in WRITE_EPS_ACTIONS:
                    eps_by_pass.append({
                        "pass": number, "symbol": change["symbol"], "file": change["file"],
                        "end": row["end"], "field": row["field"], "action": row["action"],
                        "old": row["old"], "new": row["new"],
                    })

    label_rows = defaultdict(dict)
    eps_vs_phase2 = []
    guarded_changes = []
    non_derived_changes = []
    unexpected_changes = []
    changed_files = set()
    q4_same_fy_mismatches = {"excludedSymbols": 0, "otherSymbols": 0}
    for name in sorted(disk_paths):
        disk = _json_file(disk_paths[name])
        if disk.get("market") != "US":
            continue
        document = states.get(name, disk)
        result = fiscal_detector.analyze(document)
        bucket = "excludedSymbols" if disk["symbol"] in excluded else "otherSymbols"
        q4_same_fy_mismatches[bucket] += sum(
            fiscal_detector.Q4_VS_ANNUAL in row["rules"] for row in result["affectedQ4"]
        )
        if name not in states:
            continue
        marks = disk["quarterly"].get(DERIVED_COLUMN) or []
        leaves = leaf_changes(disk, document)
        if leaves:
            changed_files.add(name)
        for path, (old_json, new_json) in sorted(leaves.items(), key=lambda item: str(item[0])):
            if path[0] in PROTECTED_TOP_LEVEL or (path[0] == "quarterly" and path[1] in PROTECTED_QUARTERLY_COLUMNS):
                guarded_changes.append(path)
            elif path[0] in ("annual", "quarterly") and path[1] in ("fy", "fq"):
                row = label_rows[(name, path[0], path[2])]
                row.setdefault("symbol", disk["symbol"])
                row.setdefault("end", disk[path[0]]["end"][path[2]])
                row.setdefault("fields", {})[path[1]] = {"old": json.loads(old_json), "new": json.loads(new_json)}
            elif path[0] == "quarterly" and path[1] == "values":
                if old_json != "null" and not (path[-1] < len(marks) and marks[path[-1]]):
                    non_derived_changes.append(path)
                eps_vs_phase2.append({
                    "file": name, "symbol": disk["symbol"], "end": disk["quarterly"]["end"][path[-1]],
                    "field": path[2], "old": json.loads(old_json), "new": json.loads(new_json),
                })
            else:
                unexpected_changes.append(path)

    first_pass_files = {change["file"] for change in results[0]} if results else set()
    mismatch_report = _json_file(DETECTOR_REPORT_PATH) if DETECTOR_REPORT_PATH.exists() else {"flaggedFiles": []}
    flagged_in_mismatch = {entry["file"] for entry in mismatch_report.get("flaggedFiles", [])}
    outside = sorted(changed_files - flagged_in_mismatch - first_pass_files)
    excluded_files = {entry["file"] for entry in excluded_plans.values()}

    excluded_totals = {"files": len(excluded_plans), "quarterlyLabelRows": 0, "annualLabelRows": 0,
                       "eps": 0}
    for entry in excluded_plans.values():
        excluded_totals["quarterlyLabelRows"] += entry["quarterlyLabelRows"]
        excluded_totals["annualLabelRows"] += entry["annualLabelRows"]
        excluded_totals["eps"] += sum(entry["eps"].values())

    return {
        "version": 1,
        "generatedBy": "atlas/scripts/repair_fiscal_periods.py --fixpoint",
        "scope": {
            "market": "US",
            "startingState": "the current docs/report/financials/ files, read only",
            "writes": "none",
            "maxPasses": max_passes,
            "excludedSymbols": sorted(excluded),
        },
        "converged": converged,
        "passesRun": len(results),
        "passes": pass_reports,
        "cumulativeVsStartingState": {
            "files": len(changed_files),
            "quarterlyLabelRows": sum(1 for key in label_rows if key[1] == "quarterly"),
            "annualLabelRows": sum(1 for key in label_rows if key[1] == "annual"),
            "epsValues": len(eps_vs_phase2),
        },
        "excluded": {"notApplied": excluded_totals, "bySymbol": dict(sorted(excluded_plans.items()))},
        "epsActionsByPass": eps_by_pass,
        "epsValuesVsStartingState": sorted(
            eps_vs_phase2, key=lambda row: (row["file"], row["end"], row["field"]),
        ),
        "labelRowsVsStartingState": [
            {"file": name, "symbol": row["symbol"], "section": section, "end": row["end"], **row["fields"]}
            for (name, section, _index), row in sorted(label_rows.items())
        ],
        "checks": {
            "guardedFieldChanges": len(guarded_changes),
            "nonDerivedEpsChanges": len(non_derived_changes),
            "unexpectedLeafChanges": len(unexpected_changes),
            "q4EndAnnualEndRowsWithSameFyAnnual": q4_same_fy_mismatches,
            "changedFilesOutsideMismatchReportOrFirstPass": outside,
            "excludedFilesChanged": len(changed_files & excluded_files),
        },
    }


def fixpoint_expected_diffs(dry_run):
    """Map each file in a fixed-point dry-run report to its cumulative changes, as normalized diff tuples.

    Label rows carry fy and/or fq at the top of the row. EPS rows carry the field and the old and new
    values. Values are JSON text, as in normalized_diff.
    """
    expected = defaultdict(set)
    for row in dry_run["labelRowsVsStartingState"]:
        for field in ("fy", "fq"):
            if field in row:
                expected[row["file"]].add((
                    row["section"], row["end"], field,
                    json.dumps(row[field]["old"]), json.dumps(row[field]["new"]),
                ))
    for row in dry_run["epsValuesVsStartingState"]:
        expected[row["file"]].add((
            "quarterly", row["end"], f"values.{row['field']}",
            json.dumps(row["old"]), json.dumps(row["new"]),
        ))
    return {name: frozenset(diffs) for name, diffs in expected.items()}


def apply_fixpoint(financials_dir, dry_run, excluded=APPLY_EXCLUDED_SYMBOLS, max_passes=FIXPOINT_MAX_PASSES):
    """Write the fixed-point changes listed in dry_run to financials_dir. Returns a summary dict.

    The re-plan is recomputed from the files on disk with the same code as the dry run. Every file
    the dry run lists is then checked before anything is written: it is either pending (every listed
    change still holds its old value; the re-plan must produce exactly the listed changes) or already
    applied (every listed change holds its new value; the re-plan must make no further change). A
    re-plan that changes any file the dry run does not list, or a file that is only partly applied,
    stops the apply. Writes go through write_atomic, one file at a time.
    """
    if not dry_run.get("converged"):
        raise ApplyError("the fixed-point dry run did not converge; nothing was written")
    expected = fixpoint_expected_diffs(dry_run)
    report, states, disk_paths = compute_fixpoint(financials_dir, excluded, max_passes)
    if not report["converged"]:
        raise ApplyError("the re-plan did not converge within the cap; nothing was written")
    unlisted = sorted(set(states) - set(expected))
    if unlisted:
        raise ApplyError(f"the re-plan changes files the dry run does not list: {unlisted}")

    pending = []
    applied = []
    for name in sorted(expected):
        disk = _json_file(disk_paths[name])
        if disk.get("symbol") in excluded:
            raise ApplyError(f"{name}: an excluded symbol is listed in the fixed-point dry run")
        diff = expected[name]
        if diff_status(disk, diff) == "applied":
            if name in states:
                raise ApplyError(f"{name}: already applied, but the re-plan proposes further changes")
            applied.append(name)
            continue
        if name not in states:
            raise ApplyError(f"{name}: the dry run lists changes that the re-plan does not make")
        verify_written_change(disk, states[name], diff)
        pending.append(name)

    for name in pending:
        path = disk_paths[name]
        write_atomic(str(path), states[name])
        if json.loads(path.read_text(encoding="utf-8")) != states[name]:
            raise ApplyError(f"{name}: the written file does not match the verified document")

    def counts(names):
        label_rows = sum(len({end for _section, end, field, _o, _n in expected[name] if not field.startswith("values.")})
                         for name in names)
        eps = Counter()
        for name in names:
            for _section, end, field, old_json, new_json in expected[name]:
                if field.startswith("values."):
                    eps["changed" if old_json != "null" and new_json != "null" else
                        "nulled" if new_json == "null" else "newlyDerived"] += 1
        return {"files": len(names), "quarterlyLabelRows": label_rows,
                "eps": {key: eps.get(key, 0) for key in ("newlyDerived", "nulled", "changed")}}

    return {"written": counts(pending), "alreadyApplied": len(applied)}


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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="read local US files and write only the dry-run report")
    mode.add_argument("--apply", action="store_true",
                      help="write exactly the dry-run changes to docs/report/financials/ (not excluded symbols)")
    mode.add_argument("--fixpoint", action="store_true",
                      help="re-plan in memory until a pass makes no change (at most 5 passes); writes nothing")
    mode.add_argument("--apply-fixpoint", action="store_true",
                      help="write exactly the changes listed in the fixed-point dry-run report to docs/report/financials/")
    parser.add_argument("--financials-dir", type=Path, default=DEFAULT_FINANCIALS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH,
                        help="dry-run report path (dry-run mode only)")
    parser.add_argument("--dry-run-report", type=Path, default=DEFAULT_REPORT_PATH,
                        help="dry-run report that --apply must reproduce (apply mode only)")
    parser.add_argument("--fixpoint-output", type=Path, default=FIXPOINT_REPORT_PATH,
                        help="fixed-point summary path (fixpoint mode only)")
    parser.add_argument("--fixpoint-report", type=Path, default=FIXPOINT_REPORT_PATH,
                        help="fixed-point dry-run report that --apply-fixpoint must reproduce")
    parser.add_argument("--max-passes", type=int, default=FIXPOINT_MAX_PASSES,
                        help=f"fixed-point pass cap (fixpoint mode only; default {FIXPOINT_MAX_PASSES})")
    return parser.parse_args(argv)


def _print_apply_summary(summary):
    def totals(rows):
        eps = Counter()
        for row in rows:
            eps.update(row["eps"])
        return {
            "files": len(rows),
            "annualLabelRows": sum(row["annualLabelRows"] for row in rows),
            "quarterlyLabelRows": sum(row["quarterlyLabelRows"] for row in rows),
            "eps": {action: eps.get(action, 0) for action in WRITE_EPS_ACTIONS},
        }

    applied = totals(summary["written"])
    print(
        f"applied: {applied['files']} files written; annual label rows {applied['annualLabelRows']}, "
        f"quarterly label rows {applied['quarterlyLabelRows']}; Q4 EPS values "
        f"{applied['eps']['newly-derived']} newly derived, {applied['eps']['nulled']} nulled, "
        f"{applied['eps']['changed']} changed"
    )
    excluded = totals(summary["excluded"])
    print(
        f"excluded, not written: {excluded['files']} files; annual label rows "
        f"{excluded['annualLabelRows']}, quarterly label rows {excluded['quarterlyLabelRows']}; "
        f"Q4 EPS values {sum(excluded['eps'].values())}"
    )
    for row in summary["excluded"]:
        print(f"  excluded {row['symbol']}: quarterly label rows {row['quarterlyLabelRows']}, "
              f"annual label rows {row['annualLabelRows']}, Q4 EPS values {sum(row['eps'].values())}")
    print(f"already applied (every dry-run change holds its new value): {len(summary['alreadyApplied'])} files")
    replan = summary["replanAfterApply"]
    print(
        f"re-plan of applied files proposes further changes: {len(replan)} files, "
        f"{sum(row['quarterlyLabelRows'] + row['annualLabelRows'] for row in replan)} label rows, "
        f"{sum(sum(row['eps'].values()) for row in replan)} Q4 EPS values; not in the dry run, not written"
    )


def main(argv=None):
    args = parse_args(argv)
    financials_dir = args.financials_dir.resolve()
    if not financials_dir.is_dir():
        raise SystemExit(f"financials directory not found: {financials_dir}")

    if args.apply_fixpoint:
        with args.fixpoint_report.open(encoding="utf-8") as file:
            dry_run = json.load(file)
        try:
            summary = apply_fixpoint(financials_dir, dry_run, max_passes=args.max_passes)
        except ApplyError as error:
            raise SystemExit(f"fixpoint apply stopped: {error}")
        written = summary["written"]
        eps = written["eps"]
        print(
            f"fixpoint applied: {written['files']} files written; quarterly label rows "
            f"{written['quarterlyLabelRows']}; Q4 EPS values {eps['newlyDerived']} newly derived, "
            f"{eps['nulled']} nulled, {eps['changed']} changed; already applied {summary['alreadyApplied']} files"
        )
        return

    if args.apply:
        with args.dry_run_report.open(encoding="utf-8") as file:
            dry_run_report = json.load(file)
        try:
            summary = apply_repairs(financials_dir, dry_run_report)
        except ApplyError as error:
            raise SystemExit(f"apply stopped: {error}")
        _print_apply_summary(summary)
        return

    if args.fixpoint:
        fixpoint_path = args.fixpoint_output.resolve()
        if os.path.commonpath((str(financials_dir), str(fixpoint_path))) == str(financials_dir):
            raise SystemExit("the fixed-point report must not be written under docs/report/financials/")
        report = run_fixpoint(financials_dir, max_passes=args.max_passes)
        write_report(fixpoint_path, report)
        for entry in report["passes"]:
            eps = entry["eps"]
            print(
                f"pass {entry['pass']}: files changed {entry['filesChanged']} of {entry['filesPlanned']} planned; "
                f"label rows {entry['quarterlyLabelRows']} quarterly, {entry['annualLabelRows']} annual; "
                f"Q4 EPS {eps['newlyDerived']} newly derived, {eps['nulled']} nulled, {eps['changed']} changed"
            )
        print(f"converged: {report['converged']} after {report['passesRun']} pass(es)")
        print(fixpoint_path)
        if not report["converged"]:
            raise SystemExit(1)
        return

    output_path = args.output.resolve()
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

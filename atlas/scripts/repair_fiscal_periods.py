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
    fill_q4_eps,
)


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


def infer_existing_derived_q4_eps(document, annual, quarterly):
    """Return Q4 EPS fields that the existing fill rule reproduces exactly.

    The stored ``derived`` column is per period, and can also mean that a cash-flow or net-income
    value was subtracted. It is not field-level EPS provenance. Therefore only classify an EPS as
    derived when the existing Q4 rule, with that field blanked in a copy, reproduces its stored
    value exactly. This narrows the candidate set; the stored schema cannot distinguish a reported
    EPS that coincidentally equals the formula result.
    """
    section = document.get("quarterly") or {}
    marks = section.get(DERIVED_COLUMN) or [None] * len(quarterly)
    old_values = {}
    candidate = copy.deepcopy(quarterly)
    for index, item in enumerate(candidate):
        if (index >= len(marks) or not marks[index] or item.get("src") != Q4_EPS_SOURCE
                or item.get("fq") != 4):
            continue
        for field in Q4_EPS_FIELDS:
            value = item["values"].get(field)
            if value is not None:
                old_values[(item["end"], field)] = value
                item["values"][field] = None

    fill_q4_eps(copy.deepcopy(annual), candidate)
    inferred = {}
    for (end, field), stored in old_values.items():
        recalculated = _value_by_end(candidate, end, field)
        if recalculated == stored:
            inferred[(end, field)] = stored
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


def q4_eps_differences(old_annual, old_quarterly, new_annual, new_quarterly, existing_derived):
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
        new_value = new_values.get(key)
        if new_value is None:
            action = "nulled"
        elif new_value == old_value:
            # The derived result remains the same, so there is no value diff to report.
            continue
        else:
            action = "changed"
        records.append({
            "end": key[0], "field": key[1], "action": action,
            "old": old_value, "new": new_value,
        })

    for key, new_value in new_values.items():
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


def build_report(financials_dir):
    files = []
    counts = defaultdict(int)
    for path in sorted(financials_dir.glob("*.json"), key=lambda item: item.name):
        document = _json_file(path)
        if document.get("market") != "US":
            continue
        symbol = document.get("symbol") or path.stem
        old_annual = section_items(document, "annual")
        old_quarterly = section_items(document, "quarterly")
        new_annual = copy.deepcopy(old_annual)
        new_quarterly = copy.deepcopy(old_quarterly)
        fallback_month = document.get("fyEndMonth")
        if not isinstance(fallback_month, int) or not 1 <= fallback_month <= 12:
            fallback_month = None
        assign_fiscal_labels(new_annual, new_quarterly, fallback_end_month=fallback_month)

        diffs = label_differences(old_annual, old_quarterly, new_annual, new_quarterly)
        input_mismatches, input_mismatches_marked = input_q4_mismatch_counts(
            document, old_annual, old_quarterly,
        )
        derived = infer_existing_derived_q4_eps(document, old_annual, old_quarterly)
        q4_diffs = q4_eps_differences(
            old_annual, old_quarterly, new_annual, new_quarterly, derived,
        )
        counts["usFiles"] += 1
        counts["annualRowsChanged"] += len(diffs["annual"])
        counts["quarterlyRowsChanged"] += len(diffs["quarterly"])
        counts["filesWithLabelChanges"] += bool(diffs["annual"] or diffs["quarterly"])
        counts["inputQ4EndAnnualEndMismatchRows"] += input_mismatches
        counts["inputQ4EndAnnualEndMismatchRowsWithDerivedFlag"] += input_mismatches_marked
        counts["q4RowsAffected"] += len({row["end"] for row in q4_diffs})
        counts["q4RowsWouldBeDerived"] += len({
            row["end"] for row in q4_diffs if row["action"] in ("newly-derived", "changed")
        })
        counts["q4RowsWouldBeNulled"] += len({
            row["end"] for row in q4_diffs if row["action"] == "nulled"
        })
        counts["q4ValuesNewlyDerived"] += sum(row["action"] == "newly-derived" for row in q4_diffs)
        counts["q4ValuesChanged"] += sum(row["action"] == "changed" for row in q4_diffs)
        counts["q4ValuesNulled"] += sum(row["action"] == "nulled" for row in q4_diffs)

        files.append({
            "symbol": symbol,
            "annualLabelChanges": diffs["annual"],
            "quarterlyLabelChanges": diffs["quarterly"],
            "q4EpsChanges": q4_diffs,
        })

    return {
        "version": 1,
        "scope": {
            "market": "US",
            "files": counts["usFiles"],
            "quarterOwnerRule": "first annual end on or after quarter end, within 371 days",
            "existingDerivedEpsRecognition": (
                "row-level derived marker plus exact fill_q4_eps reproduction after blanking "
                "the EPS field in a copy"
            ),
        },
        "counts": dict(sorted(counts.items())),
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

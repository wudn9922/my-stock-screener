"""Read-only detector for fiscal-period-end mismatches in the US financial files.

Reads docs/report/financials/*.json for files with market == "US" and writes
docs/report/fiscal-period-mismatch.json. It makes no network calls, and it never writes
under docs/report/financials/. Every rule uses only rows inside the same file.

Rules
  q4-end-vs-annual-end (Rule 1): a Q4 row (quarterly fq == 4) whose end is more than 7 days
    from every annual end of the same fiscal year. A Q4 row with no same-fy annual row is
    not evaluated and is counted separately.
  quarter-spacing (Rule 2a): quarterly rows sorted by end must be separated by a whole number
    n of fiscal quarters, where each quarter is 12 to 17 weeks (84 to 119 days), and fq must
    advance by n modulo 4. The later row of each inconsistent pair is flagged.
  quarter-vs-annual-calendar (Rule 2b): a quarterly row (fq 1-4) of a fiscal year with an
    annual row must end between 84k - 7 and 119k + 7 days before an annual end of that year,
    where k = 4 - fq. For fq 4 (k = 0) this is the Rule 1 test, so such rows carry both rule ids.
  fye-month-change (Rule 3): the fiscal year-end month changes between consecutive rows. It is
    checked on the annual rows and separately on the fq 4 rows. The first changed row of each
    series is reported.

A period end is attributed to the month it closes after subtracting 7 days, so 52/53-week
ends such as 2 January still count as December.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_FINANCIALS = REPO_ROOT / "docs/report/financials"
DEFAULT_OUT = REPO_ROOT / "docs/report/fiscal-period-mismatch.json"
KNOWN_EXAMPLES = ("TDW", "BNC", "NMTC")

ANNUAL_TOLERANCE_DAYS = 7
QUARTER_MIN_DAYS = 84  # 12 weeks
QUARTER_MAX_DAYS = 119  # 17 weeks

Q4_VS_ANNUAL = "q4-end-vs-annual-end"
QUARTER_SPACING = "quarter-spacing"
QUARTER_CALENDAR = "quarter-vs-annual-calendar"
FYE_MONTH_CHANGE = "fye-month-change"
RULES = (Q4_VS_ANNUAL, QUARTER_SPACING, QUARTER_CALENDAR, FYE_MONTH_CHANGE)

RULE_DEFINITIONS = {
    Q4_VS_ANNUAL: "A fq 4 row ends more than 7 days from every annual end of the same fiscal year.",
    QUARTER_SPACING: (
        "Consecutive quarterly rows (by end) are not a whole number n of 12-17 week quarters apart,"
        " or fq does not advance by n modulo 4. The later row is flagged."
    ),
    QUARTER_CALENDAR: (
        "A quarterly row (fq 1-4) does not end between 84k - 7 and 119k + 7 days before any same-fy"
        " annual end, where k = 4 - fq."
    ),
    FYE_MONTH_CHANGE: (
        "The month of the period end changes between consecutive annual rows, or between consecutive"
        " fq 4 rows. The first changed row of each series is reported."
    ),
}


def parse_day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def period_month(day: date) -> int:
    return (day - timedelta(days=7)).month


def aligned_columns(block: Any, names: tuple[str, ...]) -> list[tuple[Any, ...]]:
    if not isinstance(block, dict):
        return []
    lists = []
    for name in names:
        values = block.get(name)
        lists.append(values if isinstance(values, list) else [])
    return list(zip(*lists))


def read_annual(document: dict[str, Any]) -> list[tuple[int, date | None]]:
    return [
        (fy, parse_day(end))
        for fy, end in aligned_columns(document.get("annual"), ("fy", "end"))
        if isinstance(fy, int) and not isinstance(fy, bool)
    ]


def read_quarters(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"fy": fy, "fq": fq, "end": parse_day(end)}
        for fy, fq, end in aligned_columns(document.get("quarterly"), ("fy", "fq", "end"))
        if isinstance(fy, int) and not isinstance(fy, bool) and fq in (1, 2, 3, 4)
    ]


def spacing_breaks(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    days = (current["end"] - previous["end"]).days
    if days <= 0:
        return True
    quarters = (current["fq"] - previous["fq"]) % 4 or 4
    while QUARTER_MIN_DAYS * quarters <= days:
        if days <= QUARTER_MAX_DAYS * quarters:
            return False
        quarters += 4
    return True


def calendar_consistent(end: date, anchor: date, fq: int) -> bool:
    """True when end lies 84k - 7 to 119k + 7 days before anchor, with k = 4 - fq quarters back."""
    quarters_back = 4 - fq
    before = (anchor - end).days
    return (
        QUARTER_MIN_DAYS * quarters_back - ANNUAL_TOLERANCE_DAYS
        <= before
        <= QUARTER_MAX_DAYS * quarters_back + ANNUAL_TOLERANCE_DAYS
    )


def first_change(series: list[tuple[date, int]]) -> dict[str, Any] | None:
    """Series is sorted by end; report the first row whose period-end month differs from its predecessor."""
    first: dict[str, Any] | None = None
    changes = 0
    previous: tuple[date, int] | None = None
    for end, fy in series:
        if previous is not None and period_month(end) != period_month(previous[0]):
            changes += 1
            if first is None:
                first = {
                    "fy": fy,
                    "end": end.isoformat(),
                    "fromEnd": previous[0].isoformat(),
                    "fromMonth": period_month(previous[0]),
                    "toMonth": period_month(end),
                }
        previous = (end, fy)
    if first is None:
        return None
    first["changeCount"] = changes
    return first


def analyze(document: dict[str, Any]) -> dict[str, Any]:
    annual = read_annual(document)
    quarters = read_quarters(document)
    anchors: dict[int, list[date]] = defaultdict(list)
    for fy, end in annual:
        if end is not None:
            anchors[fy].append(end)

    flags: dict[int, set[str]] = defaultdict(set)
    q4_total = sum(1 for row in quarters if row["fq"] == 4)
    q4_evaluated = q4_without_annual = q4_unparsed = 0
    for index, row in enumerate(quarters):
        fy, fq, end = row["fy"], row["fq"], row["end"]
        if end is None:
            if fq == 4:
                q4_unparsed += 1
            continue
        same_fy = anchors.get(fy, [])
        if fq == 4:
            if same_fy:
                q4_evaluated += 1
            else:
                q4_without_annual += 1
        if not same_fy:
            continue
        if fq == 4 and all(abs((end - anchor).days) > ANNUAL_TOLERANCE_DAYS for anchor in same_fy):
            flags[index].add(Q4_VS_ANNUAL)
        if not any(calendar_consistent(end, anchor, fq) for anchor in same_fy):
            flags[index].add(QUARTER_CALENDAR)

    dated = sorted(
        (index for index, row in enumerate(quarters) if row["end"] is not None),
        key=lambda i: (quarters[i]["end"], quarters[i]["fy"], quarters[i]["fq"], i),
    )
    previous_of: dict[int, int] = {}
    for before, after in zip(dated, dated[1:]):
        previous_of[after] = before
        if spacing_breaks(quarters[before], quarters[after]):
            flags[after].add(QUARTER_SPACING)

    affected_q4: list[dict[str, Any]] = []
    other_flags: list[dict[str, Any]] = []
    for index in sorted(flags, key=lambda i: (quarters[i]["end"], quarters[i]["fy"], quarters[i]["fq"])):
        row = quarters[index]
        same_fy = sorted(anchors.get(row["fy"], []))
        entry: dict[str, Any] = {
            "fy": row["fy"],
            "fq": row["fq"],
            "end": row["end"].isoformat(),
            "annualEnds": [anchor.isoformat() for anchor in same_fy],
            "rules": sorted(flags[index]),
            "previousQuarter": None,
        }
        if index in previous_of:
            before_row = quarters[previous_of[index]]
            entry["previousQuarter"] = {
                "fy": before_row["fy"],
                "fq": before_row["fq"],
                "end": before_row["end"].isoformat(),
                "days": (row["end"] - before_row["end"]).days,
            }
        if row["fq"] == 4:
            if same_fy:
                nearest = min(same_fy, key=lambda anchor: abs((row["end"] - anchor).days))
                entry["nearestAnnualEnd"] = nearest.isoformat()
                entry["daysFromNearestAnnualEnd"] = (row["end"] - nearest).days
            else:
                entry["nearestAnnualEnd"] = None
                entry["daysFromNearestAnnualEnd"] = None
            affected_q4.append(entry)
        else:
            other_flags.append(entry)

    annual_series = sorted((end, fy) for fy, end in annual if end is not None)
    q4_series = sorted((row["end"], row["fy"]) for row in quarters if row["fq"] == 4 and row["end"] is not None)
    fye_changes = {"annual": first_change(annual_series), "q4": first_change(q4_series)}

    rules: set[str] = set()
    for entry in affected_q4 + other_flags:
        rules.update(entry["rules"])
    if fye_changes["annual"] or fye_changes["q4"]:
        rules.add(FYE_MONTH_CHANGE)

    return {
        "q4Rows": q4_total,
        "q4RowsEvaluated": q4_evaluated,
        "q4RowsWithoutSameFyAnnual": q4_without_annual,
        "q4RowsUnparsedEnd": q4_unparsed,
        "affectedQ4": affected_q4,
        "otherQuarterFlags": other_flags,
        "fyeChanges": fye_changes,
        "rules": sorted(rules),
    }


def build_report(financials_dir: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    totals: Counter[str] = Counter()
    files_by_rule: Counter[str] = Counter()
    q4_by_rule: Counter[str] = Counter()
    flagged: list[dict[str, Any]] = []
    us_symbols: set[str] = set()

    for path in sorted(financials_dir.glob("*.json")):
        raw = path.read_bytes()
        document = json.loads(raw)
        if not isinstance(document, dict) or document.get("market") != "US":
            continue
        symbol = document.get("symbol") if isinstance(document.get("symbol"), str) else path.stem
        us_symbols.add(symbol)
        digest.update(f"{path.name} {hashlib.sha256(raw).hexdigest()}\n".encode("utf-8"))
        totals["usFiles"] += 1

        result = analyze(document)
        totals["usQ4Rows"] += result["q4Rows"]
        totals["q4RowsEvaluatedAgainstAnnual"] += result["q4RowsEvaluated"]
        totals["q4RowsWithoutSameFyAnnual"] += result["q4RowsWithoutSameFyAnnual"]
        totals["q4RowsUnparsedEnd"] += result["q4RowsUnparsedEnd"]
        if not result["rules"]:
            continue
        flagged.append({
            "symbol": symbol,
            "file": path.name,
            "rules": result["rules"],
            "affectedQ4": result["affectedQ4"],
            "otherQuarterFlags": result["otherQuarterFlags"],
            "fyeChanges": result["fyeChanges"],
        })
        files_by_rule.update(result["rules"])
        totals["filesFlagged"] += 1
        totals["q4RowsFlagged"] += len(result["affectedQ4"])
        totals["nonQ4RowsFlagged"] += len(result["otherQuarterFlags"])
        for entry in result["affectedQ4"]:
            q4_by_rule.update(entry["rules"])

    flagged.sort(key=lambda entry: entry["symbol"])
    by_symbol = {entry["symbol"]: entry for entry in flagged}
    known = {}
    for symbol in KNOWN_EXAMPLES:
        entry = by_symbol.get(symbol)
        known[symbol] = {
            "present": symbol in us_symbols,
            "flagged": entry is not None,
            "rules": entry["rules"] if entry else [],
            "affectedQ4Ends": [q4["end"] for q4 in entry["affectedQ4"]] if entry else [],
        }

    return {
        "version": 1,
        "generatedBy": "atlas/scripts/detect_fiscal_period_mismatch.py",
        "input": {
            "directory": relative_to_repo(financials_dir),
            "market": "US",
            "usFiles": totals["usFiles"],
            "usFilesSha256": digest.hexdigest(),
        },
        "ruleDefinitions": RULE_DEFINITIONS,
        "tolerances": {
            "annualEndDays": ANNUAL_TOLERANCE_DAYS,
            "quarterMinDays": QUARTER_MIN_DAYS,
            "quarterMaxDays": QUARTER_MAX_DAYS,
        },
        "totals": {
            "usFiles": totals["usFiles"],
            "usQ4Rows": totals["usQ4Rows"],
            "q4RowsEvaluatedAgainstAnnual": totals["q4RowsEvaluatedAgainstAnnual"],
            "q4RowsWithoutSameFyAnnual": totals["q4RowsWithoutSameFyAnnual"],
            "q4RowsUnparsedEnd": totals["q4RowsUnparsedEnd"],
            "filesFlagged": totals["filesFlagged"],
            "q4RowsFlagged": totals["q4RowsFlagged"],
            "nonQ4RowsFlagged": totals["nonQ4RowsFlagged"],
            "filesFlaggedByRule": {rule: files_by_rule.get(rule, 0) for rule in RULES},
            "q4RowsFlaggedByRule": {rule: q4_by_rule.get(rule, 0) for rule in RULES if rule != FYE_MONTH_CHANGE},
        },
        "knownExamples": known,
        "flaggedFiles": flagged,
    }


def relative_to_repo(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--financials", type=Path, default=DEFAULT_FINANCIALS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    financials = args.financials.resolve()
    out = args.out.resolve()
    if out == financials or financials in out.parents:
        print("refusing to write the report under the financials directory", file=sys.stderr)
        return 2

    report = build_report(financials)
    atomic_write(out, json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    totals = report["totals"]
    print(
        f"US files {totals['usFiles']}; US Q4 rows {totals['usQ4Rows']}; "
        f"files flagged {totals['filesFlagged']}; Q4 rows flagged {totals['q4RowsFlagged']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

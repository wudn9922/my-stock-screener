#!/usr/bin/env python3
"""Read-only probe: compare the defeatbeta/yahoo-finance-data quarterly diluted EPS with the repo's US Q4 EPS numbers.

Offline. Inputs: the dataset card (README.md), a CSV of the dataset's quarterly income-statement diluted EPS rows
(exported from data/US/stock_statement.parquet by the probe workflow), the download sizes, and the repo's own files
(docs/report/financials/*.json and docs/report/q4-eps-missing-classification.json). Output: one JSON report of counts,
(symbol, end) keys and flags. The report never contains an EPS value. No repo file is written, and the output path
must lie outside the repo. This module imports nothing that opens a network connection.

Matching rule: a repo period (symbol, end) matches the dataset row of the same symbol whose report_date is within
MATCH_DAYS (7) of end. The nearest date wins. An equidistant tie, two different values on the chosen date, or a null
value does not match.

Agreement: |dataset - SEC| <= max(0.011, 0.005 * |SEC|) and no sign error. A sign error (both values nonzero, opposite
signs) is counted and is never agreement. The SEC value (the repo's value) always wins; the dataset is only compared.

Set a: US quarterly rows with fq 1 to 3, quarterly.src == "sec", a stored quarterly.values.epsDiluted and
quarterly.derived != 1. The stored record does not name the SEC form. The rows with quarterly.derived == 1 are reported
as a separate subset with the same measures.
Set b: US Q4 rows with quarterly.epsQuality[i].epsDiluted == "A" and a stored value (the 2,649 grade-A rows). Set b is
the deciding set.
Coverage: the missing US Q4 rows of q4-eps-missing-classification.json, reconciled with the financials files first.
Covered = a matching dataset row with a usable diluted EPS value.
Subtraction: annual diluted EPS minus (Q1 + Q2 + Q3) diluted EPS of the same fiscal year, from the repo's own files.
Tested on the covered missing rows and on the covered set b rows. Not testable when a required input is missing or
duplicated.

Usage:
  python3 -I atlas/scripts/probe_defeatbeta_q4.py --card README.md --statement-csv rows.csv \
      --download-sizes download-sizes.json --repo-root . --out /path/outside/repo/probe-report.json
"""
import argparse
import bisect
import csv
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "defeatbeta-probe-v1"
DATASET = "defeatbeta/yahoo-finance-data"
CARD_PATH = "README.md"
STATEMENT_PATH = "data/US/stock_statement.parquet"
ITEM_NAME = "diluted_eps"
FINANCE_TYPE = "income_statement"
PERIOD_TYPE = "quarterly"
CSV_COLUMNS = ("symbol", "report_date", "item_name", "item_value", "finance_type", "period_type")
CLASSIFICATION = Path("docs/report/q4-eps-missing-classification.json")
FINANCIALS = Path("docs/report/financials")
MATCH_DAYS = 7
TOL_ABS = 0.011
TOL_REL = 0.005
Z95 = 1.959963984540054
NO_LICENSE = "NO LICENSE STATED"
NAME_SOURCE = (
    "data/US/stock_statement.parquet is the path that defeatbeta-api 0.0.62 (PyPI sdist, hugging_face_client."
    "get_url_path with table stock_statement) builds, with columns symbol, report_date, item_name, item_value, "
    "finance_type, period_type (ticker.py _dataframe_to_stock_statements). The item_name diluted_eps is inferred from "
    "the package's snake_case item names, not read from the data. README.md is the Hugging Face dataset card path. "
    "Neither name was checked against the live repository."
)
RULES = {
    "match": "same symbol; the dataset report_date is within 7 days of the repo period end; the nearest date wins; "
             "an equidistant tie, two different values on the chosen date, or a null value does not match",
    "tolerance": "max(0.011, 0.005 * |SEC value|)",
    "agreement": "|dataset - SEC| <= tolerance and no sign error",
    "signError": "both values nonzero and of opposite sign; a sign error is never agreement",
    "wilson": "lower bound of the 95% Wilson score interval (z = 1.959964) on agreements out of compared",
    "setA": "US quarterly rows with fq 1 to 3, quarterly.src == \"sec\", a stored quarterly.values.epsDiluted and "
            "quarterly.derived != 1; the stored record does not name the SEC form",
    "setADerived": "the same rows with quarterly.derived == 1, as a separate subset with the same measures",
    "setB": "US Q4 rows with quarterly.epsQuality[i].epsDiluted == \"A\" and a stored quarterly.values.epsDiluted",
    "coverage": "the missing US Q4 rows of q4-eps-missing-classification.json, reconciled with the financials files; "
                "covered = a matching dataset row with a usable diluted EPS value",
    "subtraction": "annual diluted EPS minus (Q1 + Q2 + Q3) diluted EPS of the same fiscal year, from the repo's own "
                   "files; testable only with exactly one annual row holding a value and exactly one row holding a value "
                   "for each of Q1, Q2 and Q3; within = |dataset - subtraction| <= max(0.011, 0.005 * |subtraction|)",
    "winner": "the SEC value (the repo's value) always wins; the dataset is compared, never copied; no repo file is written",
}

MATCHED, NO_ROW, NULL_VALUE, CONFLICT, TIE = "ok", "none", "null", "ambiguous", "tie"
UNMATCHED_KEY = {NO_ROW: "noRow", NULL_VALUE: "nullValue", CONFLICT: "conflict", TIE: "tie"}
COVERAGE_KEYS = ("missing", "covered", "exactDate", "within7Days", "noRow", "nullValue", "conflict", "tie")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEADING = re.compile(r"^#{1,6}\s")
LICENSE_WORD = re.compile(r"licen[cs]e", re.IGNORECASE)


class ProbeError(Exception):
    """A condition under which the probe stops instead of reporting (missing data, inputs that do not agree)."""


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def parse_date(text):
    """A strict YYYY-MM-DD date, or None (for example 'TTM' or an empty cell)."""
    text = (text or "").strip()
    if not ISO_DATE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def require_ordinal(text, where):
    parsed = parse_date(text)
    if parsed is None:
        raise ProbeError(f"{where}: not a YYYY-MM-DD date: {text!r}")
    return parsed.toordinal()


def tolerance(sec_value):
    return max(TOL_ABS, TOL_REL * abs(sec_value))


def sign_error(sec_value, dataset_value):
    return sec_value != 0 and dataset_value != 0 and (sec_value > 0) != (dataset_value > 0)


def agrees(sec_value, dataset_value):
    """Within tolerance and not a sign error. A sign error never counts as agreement."""
    return not sign_error(sec_value, dataset_value) and abs(dataset_value - sec_value) <= tolerance(sec_value)


def wilson_lower(successes, trials):
    """Lower bound of the 95% Wilson score interval; None when there are no trials."""
    if trials <= 0:
        return None
    p = successes / trials
    z2 = Z95 * Z95
    centre = p + z2 / (2 * trials)
    margin = Z95 * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials))
    return round(max(0.0, (centre - margin) / (1 + z2 / trials)), 6)


def parse_value(text, stats):
    text = (text or "").strip()
    if text.lower() in ("", "nan", "none", "null"):
        stats["rowsNullValue"] += 1
        return None
    try:
        value = float(text)
    except ValueError:
        stats["rowsUnparsableValue"] += 1
        return None
    if not math.isfinite(value):
        stats["rowsNullValue"] += 1
        return None
    return value


def read_statement(path):
    """(index, stats). index[symbol] = (ordinals, statuses, values), sorted by ordinal, one entry per date."""
    stats = Counter()
    cells = {}
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        absent = [name for name in CSV_COLUMNS if name not in header]
        if absent:
            raise ProbeError(f"{path}: missing columns {absent}")
        for row in reader:
            stats["rowsRead"] += 1
            if row["finance_type"] != FINANCE_TYPE or row["period_type"] != PERIOD_TYPE:
                stats["rowsOtherStatement"] += 1
                continue
            if row["item_name"].strip().lower() != ITEM_NAME:
                stats["rowsOtherItem"] += 1
                continue
            stats["rowsKept"] += 1
            parsed = parse_date(row["report_date"])
            if parsed is None:
                stats["rowsSkippedDate"] += 1
                continue
            value = parse_value(row["item_value"], stats)
            cells.setdefault((row["symbol"].strip(), parsed.toordinal()), set()).add(value)
    if stats["rowsKept"] == 0:
        raise ProbeError(f"{path}: no {ITEM_NAME} rows in {FINANCE_TYPE}/{PERIOD_TYPE}; "
                         "the item name or the layout is not what the probe expects")
    grouped = {}
    for (symbol, ordinal), values in cells.items():
        if len(values) == 1:
            (value,) = values
            status = NULL_VALUE if value is None else MATCHED
        else:
            status, value = CONFLICT, None
            stats["conflictingDates"] += 1
        grouped.setdefault(symbol, []).append((ordinal, status, value))
    index = {}
    for symbol, items in grouped.items():
        items.sort(key=lambda item: item[0])
        index[symbol] = ([item[0] for item in items], [item[1] for item in items], [item[2] for item in items])
    if not index:
        raise ProbeError(f"{path}: no usable {ITEM_NAME} rows with a YYYY-MM-DD report_date")
    stats["symbols"] = len(index)
    stats["dates"] = len(cells)
    return index, stats


def match(index, symbol, end_ordinal):
    """(status, dataset value or None, signed day offset or None) for one repo period."""
    entry = index.get(symbol)
    if entry is None:
        return NO_ROW, None, None
    ordinals, statuses, values = entry
    low = bisect.bisect_left(ordinals, end_ordinal - MATCH_DAYS)
    high = bisect.bisect_right(ordinals, end_ordinal + MATCH_DAYS)
    best = None
    best_distance = None
    tie = False
    for position in range(low, high):
        distance = abs(ordinals[position] - end_ordinal)
        if best_distance is None or distance < best_distance:
            best, best_distance, tie = position, distance, False
        elif distance == best_distance:
            tie = True
    if best is None:
        return NO_ROW, None, None
    if tie:
        return TIE, None, None
    return statuses[best], values[best], ordinals[best] - end_ordinal


def empty_tally():
    return {key: 0 for key in COVERAGE_KEYS}


def tally_into(tally, status, offset):
    tally["missing"] += 1
    if status == MATCHED:
        tally["covered"] += 1
        tally["exactDate" if offset == 0 else "within7Days"] += 1
    else:
        tally[UNMATCHED_KEY[status]] += 1


WITHIN, OUTSIDE, NOT_TESTABLE = "within", "outside", "notTestable"
SUBTRACTION_KEYS = ("covered", "testable", "withinSubtraction", "outsideSubtraction", "notTestable")
SUBTRACTION_FIELD = {WITHIN: "withinSubtraction", OUTSIDE: "outsideSubtraction", NOT_TESTABLE: "notTestable"}


def subtraction_value(scan, symbol, fy):
    """Annual diluted EPS minus (Q1 + Q2 + Q3) diluted EPS of one fiscal year, or None when a required input is not
    testable: exactly one annual row holding a value, and exactly one row holding a value for each of Q1, Q2, Q3."""
    annual = scan["annual"].get((symbol, fy), [])
    if len(annual) != 1 or annual[0] is None:
        return None
    quarters = []
    for fq in (1, 2, 3):
        rows = scan["quarterly"].get((symbol, fy, fq), [])
        if len(rows) != 1 or rows[0] is None:
            return None
        quarters.append(rows[0])
    return annual[0] - (quarters[0] + quarters[1] + quarters[2])


def subtraction_outcome(scan, symbol, fy, value):
    """WITHIN or OUTSIDE when the dataset value is compared with the subtraction; NOT_TESTABLE otherwise."""
    expected = subtraction_value(scan, symbol, fy)
    if expected is None:
        return NOT_TESTABLE
    return WITHIN if abs(value - expected) <= tolerance(expected) else OUTSIDE


def empty_subtraction():
    return {key: 0 for key in SUBTRACTION_KEYS}


def tally_subtraction(tally, outcome):
    tally["covered"] += 1
    if outcome != NOT_TESTABLE:
        tally["testable"] += 1
    tally[SUBTRACTION_FIELD[outcome]] += 1


def compare(entries, index, scan=None):
    """Agreement of one set against the dataset. entries: (symbol, end, end ordinal, SEC value, fy). With scan, the
    compared rows also get the subtraction test."""
    counts = Counter()
    disagreements = []
    signs = []
    subtraction = empty_subtraction()
    for symbol, end, ordinal, sec_value, fy in entries:
        status, value, offset = match(index, symbol, ordinal)
        if status != MATCHED:
            counts[UNMATCHED_KEY[status]] += 1
            continue
        counts["compared"] += 1
        counts["exactDate" if offset == 0 else "within7Days"] += 1
        if sign_error(sec_value, value):
            counts["signErrors"] += 1
            signs.append((symbol, end))
        if agrees(sec_value, value):
            counts["agreements"] += 1
        else:
            disagreements.append((symbol, end))
        if scan is not None:
            tally_subtraction(subtraction, subtraction_outcome(scan, symbol, fy, value))
    compared = counts["compared"]
    agreements = counts["agreements"]
    result = {
        "population": len(entries),
        "compared": compared,
        "agreements": agreements,
        "disagreements": compared - agreements,
        "signErrors": counts["signErrors"],
        "agreementRate": round(agreements / compared, 6) if compared else None,
        "wilsonLower95": wilson_lower(agreements, compared),
        "exactDate": counts["exactDate"],
        "within7Days": counts["within7Days"],
        "notCompared": {UNMATCHED_KEY[status]: counts[UNMATCHED_KEY[status]] for status in (NO_ROW, NULL_VALUE,
                                                                                            CONFLICT, TIE)},
        "disagreementKeys": [{"symbol": symbol, "end": end} for symbol, end in sorted(disagreements)],
        "signErrorKeys": [{"symbol": symbol, "end": end} for symbol, end in sorted(signs)],
    }
    if scan is not None:
        result["subtraction"] = {
            "testable": subtraction["testable"],
            "withinSubtraction": subtraction["withinSubtraction"],
            "outsideSubtraction": subtraction["outsideSubtraction"],
            "notTestable": subtraction["notTestable"],
        }
    return result


def card_license(text):
    """The card's license text, quoted verbatim: every line that mentions a license, and every section headed by one."""
    lines = [line.rstrip("\r") for line in text.split("\n")]
    quotes = [{"line": number + 1, "text": line} for number, line in enumerate(lines) if LICENSE_WORD.search(line)]
    sections = []
    for number, line in enumerate(lines):
        if HEADING.match(line) and LICENSE_WORD.search(line):
            body = []
            for follow in lines[number + 1:]:
                if HEADING.match(follow):
                    break
                body.append(follow)
            sections.append({"line": number + 1, "heading": line, "body": "\n".join(body).strip("\n")})
    if not quotes:
        return {"status": NO_LICENSE, "text": NO_LICENSE, "quotes": [], "sections": []}
    return {"status": "STATED", "text": "\n".join(quote["text"] for quote in quotes), "quotes": quotes,
            "sections": sections}


def scan_financials(directory):
    """One pass over the US financials files: the missing Q4 keys; set a (split by quarterly.derived), set b; and the
    annual and Q1..Q3 diluted EPS the subtraction test reads. Set entries are (symbol, end, end ordinal, value, fy)."""
    scan = {"usFiles": 0, "usQ4Rows": 0, "filesWithoutEpsDilutedList": 0, "missing": set(), "setA": [],
            "setADerived": [], "setB": [], "annual": {}, "quarterly": {}}
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "index.json":
            continue
        document = read_json(path)
        if document.get("market") != "US":
            continue
        symbol = path.stem
        scan["usFiles"] += 1
        annual = document.get("annual") or {}
        annual_years = annual.get("fy") or []
        annual_eps = (annual.get("values") or {}).get("epsDiluted")
        if not isinstance(annual_eps, list):
            annual_eps = []
        for position, fy in enumerate(annual_years):
            value = annual_eps[position] if position < len(annual_eps) and is_number(annual_eps[position]) else None
            scan["annual"].setdefault((symbol, fy), []).append(value)
        quarterly = document.get("quarterly") or {}
        ends = quarterly.get("end") or []
        years = quarterly.get("fy") or []
        fqs = quarterly.get("fq") or []
        sources = quarterly.get("src") or []
        derived = quarterly.get("derived") or []
        quality = quarterly.get("epsQuality") or []
        eps = (quarterly.get("values") or {}).get("epsDiluted")
        if not isinstance(eps, list):
            scan["filesWithoutEpsDilutedList"] += 1
            eps = []
        for position, fq in enumerate(fqs):
            if fq != 4 and fq not in (1, 2, 3):
                continue
            value = eps[position] if position < len(eps) and is_number(eps[position]) else None
            end = ends[position] if position < len(ends) else None
            fy = years[position] if position < len(years) else None
            if fq in (1, 2, 3):
                scan["quarterly"].setdefault((symbol, fy, fq), []).append(value)
            if fq == 4:
                scan["usQ4Rows"] += 1
                if value is None:
                    scan["missing"].add((symbol, end))
                    continue
                grade = quality[position] if position < len(quality) else None
                if isinstance(grade, dict) and grade.get("epsDiluted") == "A":
                    scan["setB"].append((symbol, end, require_ordinal(end, f"{path.name} end[{position}]"), value, fy))
            elif value is not None and position < len(sources) and sources[position] == "sec":
                is_derived = position < len(derived) and derived[position] == 1
                entry = (symbol, end, require_ordinal(end, f"{path.name} end[{position}]"), value, fy)
                scan["setADerived" if is_derived else "setA"].append(entry)
    return scan


def check_downloads(downloads, card_bytes):
    if not isinstance(downloads, list):
        raise ProbeError("the download sizes file must hold a JSON list")
    by_file = {}
    for entry in downloads:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str) or not is_int(entry.get("bytes")) \
                or entry["bytes"] <= 0 or not isinstance(entry.get("sha256"), str):
            raise ProbeError(f"malformed download entry: {entry!r}")
        by_file[entry["file"]] = entry
    for name in (CARD_PATH, STATEMENT_PATH):
        if name not in by_file:
            raise ProbeError(f"the download sizes do not list {name}")
    card = by_file[CARD_PATH]
    if card["bytes"] != len(card_bytes) or card["sha256"] != hashlib.sha256(card_bytes).hexdigest():
        raise ProbeError("the card file does not match the downloaded README.md")


def build_report(card, statement_csv, download_sizes, repo_root):
    card_bytes = Path(card).read_bytes()
    card_text = card_bytes.decode("utf-8")
    downloads = read_json(download_sizes)
    check_downloads(downloads, card_bytes)
    index, statement_stats = read_statement(statement_csv)
    scan = scan_financials(Path(repo_root) / FINANCIALS)
    classification = read_json(Path(repo_root) / CLASSIFICATION)
    rows = classification["rows"]
    keys = [(row["symbol"], row["end"]) for row in rows]
    if len(set(keys)) != len(keys):
        raise ProbeError("the classification has duplicate (symbol, end) rows")
    only_classification = set(keys) - scan["missing"]
    only_financials = scan["missing"] - set(keys)
    if only_classification or only_financials:
        raise ProbeError(f"the classification does not match the missing Q4 rows of the financials: "
                         f"{len(only_classification)} only in the classification, "
                         f"{len(only_financials)} only in the financials")
    class_order = classification["rowDefinition"]["classOrder"]
    by_class = {name: empty_tally() for name in class_order}
    sub_by_class = {name: empty_subtraction() for name in class_order}
    by_year = {}
    for row in rows:
        status, value, offset = match(index, row["symbol"], require_ordinal(row["end"], f"{row['symbol']} end"))
        tally_into(by_class[row["class"]], status, offset)
        tally_into(by_year.setdefault(str(row["fy"]), empty_tally()), status, offset)
        if status == MATCHED:
            tally_subtraction(sub_by_class[row["class"]], subtraction_outcome(scan, row["symbol"], row["fy"], value))
    total = empty_tally()
    sub_total = empty_subtraction()
    for name in class_order:
        for key in COVERAGE_KEYS:
            total[key] += by_class[name][key]
        for key in SUBTRACTION_KEYS:
            sub_total[key] += sub_by_class[name][key]
    return {
        "schema": SCHEMA,
        "dataset": DATASET,
        "readOnly": True,
        "fillsValues": False,
        "writesEpsValues": False,
        "nameSource": NAME_SOURCE,
        "downloads": downloads,
        "card": {
            "path": CARD_PATH,
            "bytes": len(card_bytes),
            "sha256": hashlib.sha256(card_bytes).hexdigest(),
            "license": card_license(card_text),
        },
        "statement": {
            "path": STATEMENT_PATH,
            "csvBytes": Path(statement_csv).stat().st_size,
            "csvSha256": sha256_of(statement_csv),
            "stats": dict(sorted(statement_stats.items())),
        },
        "rules": RULES,
        "repo": {
            "usFiles": scan["usFiles"],
            "usQ4Rows": scan["usQ4Rows"],
            "filesWithoutEpsDilutedList": scan["filesWithoutEpsDilutedList"],
            "missingQ4Rows": len(scan["missing"]),
            "reconciledWithClassification": True,
            "classificationCounts": classification["counts"],
            "setAPopulation": len(scan["setA"]),
            "setADerivedPopulation": len(scan["setADerived"]),
            "setBPopulation": len(scan["setB"]),
        },
        "coverage": {
            "denominator": len(rows),
            "total": total,
            "byClass": {name: by_class[name] for name in sorted(by_class)},
            "byFiscalYear": {year: by_year[year] for year in sorted(by_year, key=int)},
        },
        "subtraction": {
            "coveredMissing": {
                **sub_total,
                "coveredNotWithinSubtraction": sub_total["covered"] - sub_total["withinSubtraction"],
                "byClass": {name: sub_by_class[name] for name in sorted(sub_by_class)},
            },
        },
        "agreement": {
            "setA": compare(scan["setA"], index),
            "setADerived": compare(scan["setADerived"], index),
            "setB": compare(scan["setB"], index, scan),
        },
    }


def write_report(path, report):
    body = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    os.replace(temporary, path)
    return len(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--card", required=True, help="the dataset card (README.md), a local copy")
    parser.add_argument("--statement-csv", required=True,
                        help="CSV of the dataset's quarterly income-statement diluted EPS rows")
    parser.add_argument("--download-sizes", required=True, help="JSON list of the downloaded files, sizes and sha256")
    parser.add_argument("--repo-root", default=str(ROOT), help="the repo checkout to read; never written")
    parser.add_argument("--out", required=True, help="the report path; must lie outside --repo-root")
    args = parser.parse_args(argv)
    repo = Path(args.repo_root).resolve()
    out = Path(args.out).resolve()
    if out == repo or repo in out.parents:
        parser.error("--out must be outside --repo-root: the probe never writes into the repo")
    try:
        report = build_report(args.card, args.statement_csv, args.download_sizes, repo)
    except ProbeError as error:
        sys.exit(f"probe stopped: {error}")
    size = write_report(out, report)
    coverage = report["coverage"]["total"]
    set_a = report["agreement"]["setA"]
    set_b = report["agreement"]["setB"]
    print(f"missing {coverage['missing']}; covered {coverage['covered']}; "
          f"set a compared {set_a['compared']} agreements {set_a['agreements']} sign errors {set_a['signErrors']}; "
          f"set b compared {set_b['compared']} agreements {set_b['agreements']} sign errors {set_b['signErrors']}")
    print(f"wrote {size} bytes")
    return report


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Offline classification of every US Q4 row whose diluted EPS is missing: one class per row, no network.

Reads docs/report/financials/*.json (market == "US" only) and docs/report/q4-eps-acceptance-ledger.json, and writes
docs/report/q4-eps-missing-classification.json. Nothing is fetched, and nothing under docs/report/financials is written.

Row definition (stored in the output as rowDefinition): a US Q4 row is index i of a US file with quarterly.fq[i] == 4.
It is missing when quarterly.values.epsDiluted[i] is null. A file whose quarterly.values has no epsDiluted list at all
counts each of its Q4 rows as missing, because an absent list has no value at i.

Each missing row gets one class, the first that applies:
- no-q4-net-income: quarterly.values.netIncome[i] is null;
- no-annual-diluted-eps: the derivation's annual lookup (grade_q4_eps_provenance._derivation: the annual rows with the
  same fy, exactly one) finds no diluted EPS;
- q1-q3-incomplete: the fiscal year does not have exactly one Q1, one Q2 and one Q3 row, each with a diluted EPS;
- derivation-rejected: every input is present and the existing derivation leaves the value null. The detail is the check
  name audit_q4_eps_backfill.method_a_rejection reports. build_financials.repair_q4_document, run on a copy of the file,
  must leave the value null too, or the run stops;
- ledger-blocked: the row is in the ledger's blocked list (symbol, end, epsDiluted); the detail is its reason code.
A missing row that the derivation would fill and that the ledger does not list stops the run: no class fits it.
"""
import argparse
import copy
import json
import os
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = str(Path(__file__).resolve().parent)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import audit_q4_eps_backfill as audit  # noqa: E402
import build_financials as fin  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "docs" / "report" / "financials"
DEFAULT_LEDGER = ROOT / "docs" / "report" / "q4-eps-acceptance-ledger.json"
DEFAULT_OUT = ROOT / "docs" / "report" / "q4-eps-missing-classification.json"
SCHEMA = "q4-eps-missing-classification-v1"
FIELD = "epsDiluted"
CLASSES = ("no-q4-net-income", "no-annual-diluted-eps", "q1-q3-incomplete", "derivation-rejected", "ledger-blocked")
# The text audit_q4_eps_backfill.method_a_rejection returns when none of its checks fails (its last return).
NO_FAILED_CHECK = "方法 A 通過檢查但仍未填值（需檢查資料形狀）"
# English reading of the check names. The names themselves are the ones the existing code reports.
CHECK_GLOSS = {
    "年度／季度期別缺漏、重複或不唯一": "annual or quarterly period missing, duplicated or not unique",
    "年度或季度來源非 SEC": "annual or quarterly source is not SEC",
    "年度 diluted EPS 缺漏": "annual diluted EPS missing",
    "Q1–Q3 diluted EPS 缺漏": "Q1 to Q3 diluted EPS missing",
    "年度 EPS 與淨利／股數不一致": "annual EPS disagrees with its net income over diluted shares (more than 15%)",
    "Q4 淨利缺漏": "Q4 net income missing",
    "缺股數且缺淨利，無法推算股數": "a period has neither diluted shares nor net income, so its share basis is unknown",
    "缺股數且 EPS 無效，無法推算股數": "a period has no diluted shares and no usable EPS, so its share basis is unknown",
    "缺少可用股數": "no usable diluted share count",
    "股數非正值": "diluted share count not positive",
    "方法 A 股數基準差超過 25%": "annual and quarterly share bases differ by more than 25%",
    "方法 A 缺少可計算輸入": "a derivation input is missing",
    "方法 A 推算 EPS 與 Q4 淨利符號不一致": "derived Q4 EPS and Q4 net income have different signs",
    "方法 A 推算值未通過 Q4 淨利／股數檢查": "derived Q4 EPS fails the check against Q4 net income over shares",
    NO_FAILED_CHECK: "no check fails (the derivation accepts the value)",
}
ROW_DEFINITION = {
    "usRow": "quarterly.fq[i] == 4 in a file with market == \"US\"; index.json and other markets are skipped",
    "missing": "quarterly.values.epsDiluted[i] is null; a file with no epsDiluted list at all counts every Q4 row "
               "as missing (an absent list has no value at i)",
    "unit": "one record per missing US Q4 row, keyed by symbol (file name) and end",
    "classRule": "first matching class in classOrder wins",
}


def _rows_with_fy(block, fy):
    return [index for index, year in enumerate(block.get("fy") or []) if year == fy]


def _annual_eps(annual, fy):
    """The derivation's annual lookup (grade_q4_eps_provenance._derivation): exactly one annual row of fy, else None."""
    rows = _rows_with_fy(annual, fy)
    if len(rows) != 1:
        return None, f"{len(rows)} annual rows for fy {fy}"
    eps = audit.value(annual, FIELD, rows[0])
    return eps, None if eps is not None else f"annual {FIELD} null for fy {fy}"


def _quarter_gaps(quarterly, fy):
    """Q1..Q3 of fy that are not exactly one row with a diluted EPS (the derivation needs one of each)."""
    years = quarterly.get("fy") or []
    numbers = quarterly.get("fq") or []
    gaps = []
    for number in (1, 2, 3):
        rows = [index for index, (year, fq) in enumerate(zip(years, numbers)) if year == fy and fq == number]
        if len(rows) != 1:
            gaps.append(f"Q{number} rows={len(rows)}")
        elif audit.value(quarterly, FIELD, rows[0]) is None:
            gaps.append(f"Q{number} null")
    return gaps


def _derivation_fills(document):
    """(end, field) pairs that build_financials.repair_q4_document fills, run on a copy of the document."""
    return set(fin.repair_q4_document(copy.deepcopy(document)))


def classify_document(document, symbol, blocked):
    """Records for the missing US Q4 rows of one file. blocked maps (symbol, end, field) to the ledger reason."""
    quarterly = document.get("quarterly") or {}
    annual = document.get("annual") or {}
    ends = quarterly.get("end") or []
    years = quarterly.get("fy") or []
    records = []
    fills = None
    for index, fq in enumerate(quarterly.get("fq") or []):
        if fq != 4 or audit.value(quarterly, FIELD, index) is not None:
            continue
        end = ends[index] if index < len(ends) else None
        fy = years[index] if index < len(years) else None
        if audit.value(quarterly, "netIncome", index) is None:
            cls, detail = "no-q4-net-income", "netIncome null"
        else:
            eps, problem = _annual_eps(annual, fy)
            gaps = _quarter_gaps(quarterly, fy)
            if eps is None:
                cls, detail = "no-annual-diluted-eps", problem
            elif gaps:
                cls, detail = "q1-q3-incomplete", ", ".join(gaps)
            else:
                if fills is None:
                    fills = _derivation_fills(document)
                check = audit.method_a_rejection(document, fy)
                accepted = (end, FIELD) in fills
                if accepted != (check == NO_FAILED_CHECK):
                    raise RuntimeError(f"{symbol} {end}: the derivation (accepted={accepted}) and "
                                       f"audit_q4_eps_backfill.method_a_rejection ({check!r}) disagree")
                if not accepted:
                    cls, detail = "derivation-rejected", check
                elif (symbol, end, FIELD) in blocked:
                    cls, detail = "ledger-blocked", blocked[(symbol, end, FIELD)]
                else:
                    raise RuntimeError(f"{symbol} {end}: the derivation fills this missing {FIELD} and the ledger "
                                       "does not list it; no class applies")
        records.append({"class": cls, "detail": detail, "end": end, "fy": fy, "symbol": symbol})
    return records


def load_blocked(ledger):
    """(symbol, end, field) → reason for the ledger's blocked rows of epsDiluted."""
    blocked = {}
    for row in ledger.get("blocked") or []:
        if row.get("field") != FIELD:
            continue
        key = (row["symbol"], row["end"], FIELD)
        if key in blocked:
            raise ValueError(f"duplicate ledger row {key}")
        blocked[key] = row["reason"]
    return blocked


def build(directory, ledger):
    """The classification report for the US files in directory. Reads only."""
    blocked = load_blocked(ledger)
    records = []
    stats = Counter()
    files_without_list = 0
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "index.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("market") != "US":
            continue
        quarterly = document.get("quarterly") or {}
        has_list = isinstance((quarterly.get("values") or {}).get(FIELD), list)
        q4_indices = [index for index, fq in enumerate(quarterly.get("fq") or []) if fq == 4]
        missing = sum(1 for index in q4_indices if audit.value(quarterly, FIELD, index) is None)
        stats["usFiles"] += 1
        stats["usQ4Rows"] += len(q4_indices)
        stats["missing"] += missing
        group = "WithList" if has_list else "WithoutList"
        stats[f"q4Rows{group}"] += len(q4_indices)
        stats[f"missing{group}"] += missing
        if not has_list:
            files_without_list += 1
        records.extend(classify_document(document, path.stem, blocked))
    if len(records) != stats["missing"]:
        raise RuntimeError(f"{len(records)} classified rows for {stats['missing']} missing rows")
    counts = {name: 0 for name in CLASSES}
    counts.update(Counter(record["class"] for record in records))
    if sum(counts.values()) != stats["missing"]:
        raise RuntimeError("class counts do not sum to the missing total")
    records.sort(key=lambda record: (CLASSES.index(record["class"]), record["symbol"], str(record["end"])))
    return {
        "schema": SCHEMA,
        "rowDefinition": {**ROW_DEFINITION, "classOrder": list(CLASSES)},
        "reconciliation": {
            "usFiles": stats["usFiles"],
            "usQ4Rows": stats["usQ4Rows"],
            "missing": stats["missing"],
            "q4RowsInFilesWithEpsDilutedList": stats["q4RowsWithList"],
            "missingInFilesWithEpsDilutedList": stats["missingWithList"],
            "q4RowsInFilesWithoutEpsDilutedList": stats["q4RowsWithoutList"],
            "missingInFilesWithoutEpsDilutedList": stats["missingWithoutList"],
            "filesWithoutEpsDilutedList": files_without_list,
            "note": "Counting only files that carry an epsDiluted list leaves out the Q4 rows of the files without "
                    "one. Every such row is missing, so this report counts them as null and classifies them.",
        },
        "counts": counts,
        "detailCounts": {
            "derivation-rejected": dict(sorted(Counter(record["detail"] for record in records
                                                       if record["class"] == "derivation-rejected").items())),
        },
        "checkNames": {
            "source": "audit_q4_eps_backfill.method_a_rejection, the check names the existing derivation reports",
            "reconstructed": [],
            "confirmedBy": "build_financials.repair_q4_document on a copy leaves each derivation-rejected value null",
            "gloss": CHECK_GLOSS,
        },
        "rows": records,
    }


def _relative(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def write_report(path, report):
    body = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, path)
    return len(body.encode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default=str(DEFAULT_DIR))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--dry-run", action="store_true", help="print the counts without writing the report")
    args = parser.parse_args(argv)
    if Path(args.out).resolve().is_relative_to(Path(args.dir).resolve()):
        parser.error("--out must not be inside the financials directory")
    ledger = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
    report = build(args.dir, ledger)
    reconciliation = report["reconciliation"]
    print(f"US files {reconciliation['usFiles']}; US Q4 rows {reconciliation['usQ4Rows']}; "
          f"missing {reconciliation['missing']}")
    print("classes: " + ", ".join(f"{name} {count}" for name, count in report["counts"].items()))
    print(f"in files with an epsDiluted list: {reconciliation['q4RowsInFilesWithEpsDilutedList']} Q4 rows, "
          f"{reconciliation['missingInFilesWithEpsDilutedList']} missing; in "
          f"{reconciliation['filesWithoutEpsDilutedList']} files without one: "
          f"{reconciliation['q4RowsInFilesWithoutEpsDilutedList']} Q4 rows, "
          f"{reconciliation['missingInFilesWithoutEpsDilutedList']} missing")
    if not args.dry_run:
        size = write_report(args.out, report)
        print(f"wrote {_relative(args.out)} ({size} bytes)")
    return report


if __name__ == "__main__":
    main()

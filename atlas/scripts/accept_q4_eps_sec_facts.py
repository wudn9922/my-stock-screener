#!/usr/bin/env python3
"""Opus steps 3–4 (offline): accept SEC-verified US Q4 EPS and apply the approved direct corrections.

Source: a snapshot of stored financial files that carry secQ4EpsFacts entries from SEC companyfacts
(grade A, method direct-sec-companyfacts), and the numeric-corrections ledger that classifies each difference.
The target is docs/report/financials on the current branch.

Per Q4 EPS field that has a grade-A SEC fact in the source (|EPS| < 1000):
- verify-existing: the stored value agrees with the SEC value within 0.01. The value is kept, the SEC fact is
  copied to secQ4EpsFacts and the field is graded A.
- null → A: only the five rows approved by Opus (APAM 2017, LYV 2015 and 2017, JAKK 2019 and 2020).
- differs: a stored value that disagrees by more than 0.01 is overwritten only when the source class is
  "direct-sec-existing-value-correction" and all four conditions pass:
    1. the filing is later than every stock split, or the new/old ratio is not the split product (±3%);
    2. the new value has the sign of, and is closer to, Q4 net income ÷ Q3 diluted shares;
    3. the new value has the same sign as the other EPS field;
    4. the difference is larger than 0.01.
  The old value is written to epsOverrides[end][field]. Every other difference stays as it is and is listed
  in the ledger as blocked.
Entries with |EPS| ≥ 1000 are deferred (BRK-A, PTCT) for a manual decision.

Default mode is a dry run that prints counts. --apply writes the files and the ledger.
"""
import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

SCRIPTS = str(Path(__file__).resolve().parent)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_financials as fin  # noqa: E402
from build_valuation_us import write_atomic  # noqa: E402

FIELDS = ("epsDiluted", "epsBasic")
VERIFY_TOLERANCE = 0.01
DEFERRED_ABS_EPS = 1000
SPLIT_RATIO_TOLERANCE = 0.03
DIRECT_CLASS = "direct-sec-existing-value-correction"
# Opus-approved null → A rows (symbol, period end, field); every one is grade A in the source.
NULL_TO_A = {
    ("APAM", "2017-12-31", "epsBasic"),
    ("LYV", "2015-12-31", "epsBasic"),
    ("LYV", "2017-12-31", "epsBasic"),
    ("JAKK", "2019-12-31", "epsBasic"),
    ("JAKK", "2020-12-31", "epsBasic"),
}
# Opus: the first batch excludes JAKK 2018.
EXCLUDED_DIRECT = {("JAKK", "2018-12-31", "epsBasic")}


def _sign(value):
    return (value > 0) - (value < 0)


def correction_conditions(document, end, index, field, old, new, filed):
    """Return (ok, reasons) for one direct correction; ok only when all four conditions hold."""
    quarterly = document["quarterly"]
    values = quarterly["values"]
    reasons = []
    events = [(date.fromisoformat(day), float(factor)) for day, factor in (document.get("split") or {}).get("events", [])]
    period_end = date.fromisoformat(end)
    product = 1.0
    for day, factor in events:
        if day > period_end:
            product *= factor
    last_split = max((day for day, _ in events), default=None)
    filed_day = date.fromisoformat(filed) if filed else None
    later_than_splits = filed_day is not None and last_split is not None and filed_day > last_split
    ratio = new / old if old else None
    ratio_off_split = product == 1.0 or (ratio is not None and abs(ratio / product - 1) > SPLIT_RATIO_TOLERANCE)
    if not (later_than_splits or ratio_off_split):
        reasons.append("basis-split")

    net_income = (values.get("netIncome") or [None] * len(quarterly["end"]))[index]
    fys, fqs = quarterly["fy"], quarterly["fq"]
    q3 = [k for k in range(len(fqs)) if fys[k] == fys[index] and fqs[k] == 3]
    periods = [{"values": {"dilutedShares": (values.get("dilutedShares") or [None] * len(fqs))[k]
                if k < len(values.get("dilutedShares") or []) else None}} for k in range(len(fqs))]
    q3_shares = fin.reference_shares(periods, q3[0]) if len(q3) == 1 else None
    if net_income is None or not q3_shares:
        reasons.append("no-NI-or-Q3-shares")
    else:
        reference = net_income / q3_shares
        if _sign(new) != _sign(reference):
            reasons.append("sign-vs-NI-over-shares")
        elif not abs(new - reference) < abs(old - reference):
            reasons.append("not-closer-to-NI-over-shares")

    other_field = "epsBasic" if field == "epsDiluted" else "epsDiluted"
    other = (values.get(other_field) or [None] * len(fqs))[index]
    if other is not None and _sign(other) != _sign(new):
        reasons.append("diluted-basic-sign")
    if abs(new - old) <= VERIFY_TOLERANCE:
        reasons.append("not-a-difference")
    return not reasons, reasons


def _grades_for(document):
    quarterly = document["quarterly"]
    if not isinstance(quarterly.get("epsQuality"), list):
        quarterly["epsQuality"] = [None] * len(quarterly["end"])
    return quarterly["epsQuality"]


def _set_grade(document, index, field, grade="A"):
    grades = _grades_for(document)
    entry = dict(grades[index] or {})
    entry[field] = grade
    grades[index] = entry


def _stored_source(document, index):
    quarterly = document["quarterly"]
    derived = (quarterly.get("derived") or [None] * len(quarterly["end"]))
    sources = quarterly.get("src") or []
    base = sources[index] if index < len(sources) and isinstance(sources[index], str) else "yahoo"
    return f"{base}-derived" if index < len(derived) and derived[index] == 1 else base


def process_file(path, source_path, direct_keys, apply, report):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    source = json.loads(Path(source_path).read_text(encoding="utf-8"))
    if document.get("market") != "US":
        return
    quarterly = document["quarterly"]
    ends = quarterly["end"]
    changed = False
    for end, fields in (source.get("secQ4EpsFacts") or {}).items():
        if end not in ends:
            continue
        index = ends.index(end)
        for field, fact in fields.items():
            if field not in FIELDS or fact.get("grade") != "A" or fact.get("value") is None:
                continue
            symbol = document["symbol"]
            key = (symbol, end, field)
            new = float(fact["value"])
            if abs(new) >= DEFERRED_ABS_EPS:
                report["deferred"].append({"symbol": symbol, "end": end, "field": field, "value": new})
                continue
            values = quarterly["values"].setdefault(field, [None] * len(ends))
            old = values[index]
            if old is None:
                if key in NULL_TO_A:
                    values[index] = new
                    document.setdefault("secQ4EpsFacts", {}).setdefault(end, {})[field] = fact
                    _set_grade(document, index, field)
                    report["nullToA"] += 1
                    changed = True
                continue
            if abs(old - new) <= VERIFY_TOLERANCE:
                trace = document.setdefault("secQ4EpsFacts", {}).setdefault(end, {})
                trace.setdefault(field, fact)
                _set_grade(document, index, field)
                report["verifiedA"] += 1
                changed = True
                continue
            if key in EXCLUDED_DIRECT or (symbol, end, field, new) not in direct_keys:
                report["blocked"].append({"symbol": symbol, "end": end, "field": field, "old": old, "new": new,
                                          "reason": "not-in-first-batch"})
                continue
            ok, reasons = correction_conditions(document, end, index, field, old, new, fact.get("filed"))
            if not ok:
                report["blocked"].append({"symbol": symbol, "end": end, "field": field, "old": old, "new": new,
                                          "reason": ",".join(reasons)})
                continue
            overrides = document.setdefault("epsOverrides", {}).setdefault(end, {})
            overrides[field] = {"old": old, "oldSource": _stored_source(document, index), "new": new,
                                "accession": fact.get("accession"), "filed": fact.get("filed"),
                                "reason": "direct SEC existing-value correction; four conditions passed"}
            values[index] = new
            document.setdefault("secQ4EpsFacts", {}).setdefault(end, {})[field] = fact
            _set_grade(document, index, field)
            report["overridden"].append({"symbol": symbol, "end": end, "field": field, "old": old, "new": new})
            changed = True
    if changed and apply:
        write_atomic(str(path), document)


def load_direct_keys(ledger_path):
    keys = set()
    for line in Path(ledger_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("correctionClass") == DIRECT_CLASS:
            keys.add((row["symbol"], row["period"]["end"], row["field"], row["correctedValue"]))
    return keys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default="docs/report/financials")
    parser.add_argument("--source-dir", required=True, help="snapshot of financial files with secQ4EpsFacts")
    parser.add_argument("--ledger", required=True, help="q4-eps-numeric-corrections.jsonl from the source")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    direct_keys = load_direct_keys(args.ledger)
    report = {"verifiedA": 0, "nullToA": 0, "overridden": [], "blocked": [], "deferred": []}
    for path in sorted(Path(args.dir).glob("*.json")):
        if path.name == "index.json" or path.name.startswith("q4-eps-"):
            continue
        source_path = Path(args.source_dir) / path.name
        if not source_path.exists():
            continue
        process_file(path, source_path, direct_keys, args.apply, report)
    reasons = Counter(item["reason"] for item in report["blocked"])
    mode = "applied" if args.apply else "dry run"
    print(f"{mode}: verified A {report['verifiedA']}; null→A {report['nullToA']}; "
          f"overridden {len(report['overridden'])}; blocked {len(report['blocked'])}; "
          f"deferred (|EPS| ≥ 1000) {len(report['deferred'])}")
    print("blocked reasons: " + ", ".join(f"{reason} {count}" for reason, count in reasons.most_common()))
    if args.apply:
        ledger = {
            "schema": "q4-eps-acceptance-ledger-v1",
            "source": str(args.source_dir),
            "verifiedA": report["verifiedA"],
            "nullToA": report["nullToA"],
            "overridden": report["overridden"],
            "deferred": report["deferred"],
            "blockedReasons": dict(reasons),
            "blocked": report["blocked"],
        }
        # outside financials/: build_growth reads every JSON file in that directory as a company
        write_atomic(str(Path(args.dir).parent / "q4-eps-acceptance-ledger.json"), ledger)
    return report


if __name__ == "__main__":
    main()

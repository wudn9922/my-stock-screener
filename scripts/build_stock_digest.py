"""Offline digest of public financial and institutional observations."""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from research_io import write
from research_store import MarketStore

MARGIN_PP_THRESHOLD = Decimal("3")
STREAK_DAYS = 3
DIGEST_MAX_AGE_DAYS = 30
ROOT = Path(__file__).resolve().parents[1]
SYMBOL = re.compile(r"^(?:[0-9]{4,6}[A-Z]?\.(?:TW|TWO)|[A-Z][A-Z0-9.-]{0,19})$")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def day(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
    except ValueError:
        return None


def number(value):
    if type(value) not in (int, float):
        return None
    result = Decimal(str(value))
    return result if result.is_finite() else None


def recent(stamp, today):
    d = day(stamp)
    return d is not None and today - timedelta(days=DIGEST_MAX_AGE_DAYS) <= d <= today


def latest_quarter(document):
    q = document.get("quarterly") or {}
    ends, years, quarters = (q.get(k, []) for k in ("end", "fy", "fq"))
    if not (len(ends) == len(years) == len(quarters)):
        raise ValueError("Quarter arrays differ")
    if not ends:
        return None
    i = max(range(len(ends)), key=lambda j: ends[j])
    if not day(ends[i]) or type(years[i]) is not int or quarters[i] not in (1, 2, 3, 4):
        return None
    return (years[i] * 4 + quarters[i], i, ends[i])


def margin_events(document, previous, today):
    latest = latest_quarter(document)
    if latest is None or previous is None or latest[0] <= previous:
        return []
    _, i, end = latest
    q = document["quarterly"]
    if (i == 0 or type(q["fy"][i - 1]) is not int
            or q["fq"][i - 1] not in (1, 2, 3, 4)
            or q["fy"][i - 1] * 4 + q["fq"][i - 1] != latest[0] - 1):
        return []
    prev_end = day(q["end"][i - 1])
    if not prev_end or not 70 <= (day(end) - prev_end).days <= 110:
        return []
    if "quarterly-ytd-suspected" in document.get("quality", {}).get("issues", []):
        return []
    values = q.get("values", {})

    def margin(field, index):
        rev = values.get("revenue", [])
        val = values.get(field, [])
        if index >= len(rev) or index >= len(val):
            return None
        r, v = number(rev[index]), number(val[index])
        return v * 100 / r if r is not None and r > 0 and v is not None else None

    rows = []
    for field, kind in (("grossProfit", "gross-margin"), ("operatingIncome", "operating-margin")):
        old, new = margin(field, i - 1), margin(field, i)
        if old is not None and new is not None and abs(new - old) >= MARGIN_PP_THRESHOLD:
            rows.append(dict(kind=kind, date=today.isoformat(), period=f'{q["fy"][i]}Q{q["fq"][i]}',
                             before=float(old), after=float(new)))
    return rows


def restatement_events(document, previous, today):
    if document.get("market") != "US":
        return [], []
    rows, undated = [], set()
    for revision in document.get("quality", {}).get("revisions", []):
        if revision.get("kind") != "restated" or not revision.get("accn") or not revision.get("originalAccn"):
            continue  # Yahoo differences and retained fields are not SEC restatements.
        end = revision.get("end")
        if not day(end):
            continue
        filed = revision.get("filed")
        if day(filed):
            if recent(filed, today):
                rows.append(dict(kind="sec-restatement", date=filed, period=end))
        else:
            undated.add(end)
    for frequency in ("quarterly", "annual"):
        section = document.get(frequency) or {}
        for i, flags in enumerate(section.get("secRestatementIssues") or []):
            if "restated" not in (flags or []):
                continue
            end = section["end"][i]
            basis = (section.get("secFilingBasis") or [])
            filed = (basis[i] or {}).get("filed") if i < len(basis) else None
            if not day(end):
                continue
            if day(filed):
                if recent(filed, today):
                    rows.append(dict(kind="sec-restatement", date=filed, period=end))
            else:
                undated.add(end)
    if previous is not None:
        for end in undated - set(previous):
            rows.append(dict(kind="sec-restatement", date=today.isoformat(), period=end))
    return rows, sorted(undated)


def reversal_events(record, sessions, today):
    rows = sorted(record.get("rows", []), key=lambda r: r["date"])
    sample = rows[-(STREAK_DAYS + 1):]
    if len(sample) != STREAK_DAYS + 1:
        return []
    stamps = [r["date"] for r in sample]
    # Missing stock observations are not consecutive exchange sessions.
    if stamps != sorted(set(sessions))[-(STREAK_DAYS + 1):] or not recent(stamps[-1], today):
        return []
    result = []
    for role in ("foreign", "trust"):
        nets = [number(r.get(role, {}).get("net")) for r in sample]
        if any(v is None or v == 0 for v in nets):
            continue
        signs = [v > 0 for v in nets]
        if all(s == signs[0] for s in signs[:-1]) and signs[-1] != signs[0]:
            result.append(dict(kind=role + "-reversal", date=stamps[-1],
                               direction="buy" if signs[-1] else "sell"))
    return result


def build(report, today, previous):
    if previous and previous.get("version") != 1:
        raise ValueError("Unsupported previous digest")
    entries = {symbol: [r for r in rows if recent(r["date"], today)]
               for symbol, rows in previous.get("stocks", {}).items()}
    quarters = dict(previous.get("latestQuarters", {}))
    undated = dict(previous.get("undatedRestatements", {}))
    paths = sorted((report / "financials").glob("*.json"))
    if not paths:
        raise ValueError("Financial inputs missing")
    for path in paths:
        symbol = path.stem
        if not SYMBOL.fullmatch(symbol):
            continue
        doc = read(path)
        if doc.get("symbol") != symbol:
            raise ValueError("Financial symbol mismatch")
        rows = entries.setdefault(symbol, [])
        rows.extend(margin_events(doc, quarters.get(symbol), today))
        latest = latest_quarter(doc)
        if latest:
            quarters[symbol] = max(latest[0], quarters.get(symbol, 0))
        restated, seen = restatement_events(doc, undated.get(symbol), today)
        rows.extend(restated)
        undated[symbol] = seen
    research = report / "research"
    index = read(research / "institutions.json")
    if index.get("version") != 1:
        raise ValueError("Invalid institution index")
    for market, records in MarketStore(research, "institutions").records.items():
        sessions = index.get("sources", {}).get(market, {}).get("dates", [])
        for symbol, record in records.items():
            if not SYMBOL.fullmatch(symbol) or record.get("symbol") != symbol:
                raise ValueError("Institution symbol mismatch")
            entries.setdefault(symbol, []).extend(reversal_events(record, sessions, today))
    stocks = {}
    for symbol, rows in sorted(entries.items()):
        unique = {json.dumps(r, sort_keys=True): r for r in rows}
        if unique:
            stocks[symbol] = sorted(unique.values(), key=lambda r: (r["date"], r["kind"], r.get("period", "")), reverse=True)
    return dict(version=1, generatedAt=datetime.now(timezone.utc).isoformat(),
                stocks=stocks, latestQuarters=quarters, undatedRestatements=undated)


def run(report, today, dry_run=False):
    output = report / "stock-digest.json"
    try:
        previous = read(output) if output.exists() else {}
        result = build(report, today, previous)
        counts = Counter(kind for rows in result["stocks"].values() for kind in {r["kind"] for r in rows})
        for kind in ("sec-restatement", "gross-margin", "operating-margin", "foreign-reversal", "trust-reversal"):
            counts.setdefault(kind, 0)
        print("Digest stocks by condition:", json.dumps(dict(sorted(counts.items()))))
        if not previous:
            print("First run: baseline quarter periods, no unproven new-quarter alerts.")
        if not dry_run:
            write(output, result)
        return True
    except (OSError, ValueError, KeyError, TypeError, IndexError, ArithmeticError, AttributeError):
        print("::warning::Stock digest unavailable; previous output retained")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "docs/report")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Fail only offline verification on invalid input")
    args = parser.parse_args()
    ok = run(args.report_dir, datetime.now(ZoneInfo("Asia/Taipei")).date(), args.dry_run)
    if args.strict and not ok:
        raise SystemExit(1)

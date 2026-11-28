"""Offline digest of public financial and institutional observations."""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from research_io import semantic
from market_sessions import read_policy, verified_closures
import os
import tempfile
from research_store import MarketStore

MARGIN_PP_THRESHOLD = Decimal("3")
STREAK_DAYS = 3
FLOW_MAX_AGE_SESSIONS = 5
STATE_PATH = Path('.cache/stock-digest-state.json')
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


def recent_flow_sessions(calendar_sessions, today):
    """Exactly the last five exchange sessions through the Taipei calendar date."""
    return set(sorted(d for d in calendar_sessions if d <= today.isoformat())[-FLOW_MAX_AGE_SESSIONS:])


def row_recent(row, today, flow_sessions):
    if row["kind"] in ("foreign-reversal", "trust-reversal"):
        return row["date"] in flow_sessions
    return recent(row["date"], today)


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


def reversal_events(record, sessions, today, calendar_sessions=None):
    rows = sorted(record.get("rows", []), key=lambda r: r["date"])
    sample = rows[-(STREAK_DAYS + 1):]
    if len(sample) != STREAK_DAYS + 1:
        return []
    stamps = [r["date"] for r in sample]
    # Missing stock observations are not consecutive exchange sessions.
    if stamps != sorted(set(sessions))[-(STREAK_DAYS + 1):] or not recent(stamps[-1], today):
        return []
    # The exchange calendar also catches a missing day in the entire market source.
    if calendar_sessions is not None and stamps != sorted(d for d in calendar_sessions if stamps[0] <= d <= stamps[-1]):
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


def build(report, today, previous, state):
    if previous and previous.get("version") != 1:
        raise ValueError("Unsupported previous digest")
    if state and state.get("version") != 1:
        raise ValueError("Unsupported digest state")
    calendar = read(report / "market-calendar.json")
    if calendar.get("version") != 1:
        raise ValueError("Invalid trading calendar")
    if not calendar.get("validFrom", today.isoformat()) <= today.isoformat() <= calendar.get("validTo", today.isoformat()):
        raise ValueError("Trading calendar outside coverage")
    closed = set(verified_closures("tw")) | set(read_policy(report / "market-closures.json").get("tw", {}))
    calendar_sessions = set(calendar["markets"]["TW"]["sessions"]) - closed
    flow_sessions = recent_flow_sessions(calendar_sessions, today)
    entries = {symbol: [r for r in rows if row_recent(r, today, flow_sessions)]
               for symbol, rows in previous.get("stocks", {}).items()}
    quarters = dict(state.get("latestQuarters", {}))
    undated = {k: v for k, v in state.get("undatedRestatements", {}).items() if v}
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
        known = symbol in quarters or symbol in undated
        rows.extend(margin_events(doc, quarters.get(symbol), today))
        latest = latest_quarter(doc)
        if latest:
            quarters[symbol] = max(latest[0], quarters.get(symbol) or 0)
        else:
            quarters.setdefault(symbol, None)
        restated, seen = restatement_events(doc, undated.get(symbol, []) if known else None, today)
        rows.extend(restated)
        if seen:
            undated[symbol] = seen
        else:
            undated.pop(symbol, None)
    research = report / "research"
    index = read(research / "institutions.json")
    if index.get("version") != 1:
        raise ValueError("Invalid institution index")
    for market, records in MarketStore(research, "institutions").records.items():
        sessions = index.get("sources", {}).get(market, {}).get("dates", [])
        for symbol, record in records.items():
            if not SYMBOL.fullmatch(symbol) or record.get("symbol") != symbol:
                raise ValueError("Institution symbol mismatch")
            entries.setdefault(symbol, []).extend(reversal_events(record, sessions, today, calendar_sessions))
    stocks = {}
    for symbol, rows in sorted(entries.items()):
        unique = {json.dumps(r, sort_keys=True): r for r in rows if row_recent(r, today, flow_sessions)}
        if unique:
            stocks[symbol] = sorted(unique.values(), key=lambda r: (r["date"], r["kind"], r.get("period", "")), reverse=True)
    public = dict(version=1, generatedAt=datetime.now(timezone.utc).isoformat(), stocks=stocks)
    state = dict(version=1, latestQuarters=quarters)
    if undated:
        state["undatedRestatements"] = undated
    return public, state


def serialized(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def publish_pair(outputs):
    """Stage both files before replacing; restore the first if the second fails."""
    staged = []
    replaced = []
    try:
        for path, body in outputs:
            old = path.read_bytes() if path.exists() else None
            if old == body:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                staged.append((path, Path(stream.name), old))
                stream.write(body)
        for path, temp, old in staged:
            os.replace(temp, path)
            replaced.append((path, old))
    except OSError:
        for path, old in reversed(replaced):
            if old is None:
                path.unlink(missing_ok=True)
            else:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    stream.write(old)
                os.replace(stream.name, path)
        raise
    finally:
        for _, temp, _ in staged:
            temp.unlink(missing_ok=True)


def run(report, today, dry_run=False, state_path=None):
    output = report / "stock-digest.json"
    state_path = state_path or report.parent.parent / STATE_PATH
    try:
        previous = read(output) if output.exists() else {}
        state = read(state_path) if state_path.exists() else {
            "version": 1, "latestQuarters": previous.get("latestQuarters", {}),
            "undatedRestatements": previous.get("undatedRestatements", {}),
        }
        result, new_state = build(report, today, previous, state)
        if set(previous) == set(result) and semantic(previous) == semantic(result):
            result["generatedAt"] = previous["generatedAt"]
        public_bytes, state_bytes = serialized(result), serialized(new_state)
        counts = Counter(kind for rows in result["stocks"].values() for kind in {r["kind"] for r in rows})
        for kind in ("sec-restatement", "gross-margin", "operating-margin", "foreign-reversal", "trust-reversal"):
            counts.setdefault(kind, 0)
        print("Digest stocks by condition:", json.dumps(dict(sorted(counts.items()))))
        print("Digest bytes:", json.dumps({"stock-digest.json": len(public_bytes), "stock-digest-state.json": len(state_bytes)}))
        if not state.get("latestQuarters"):
            print("First run: baseline quarter periods, no unproven new-quarter alerts.")
        if not dry_run:
            publish_pair([(output, public_bytes), (state_path, state_bytes)])
        return True
    except (OSError, ValueError, KeyError, TypeError, IndexError, ArithmeticError, AttributeError):
        print("::warning::Stock digest unavailable; previous output retained")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "docs/report")
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Fail only offline verification on invalid input")
    args = parser.parse_args()
    ok = run(args.report_dir, datetime.now(ZoneInfo("Asia/Taipei")).date(), args.dry_run, args.state_path)
    if args.strict and not ok:
        raise SystemExit(1)

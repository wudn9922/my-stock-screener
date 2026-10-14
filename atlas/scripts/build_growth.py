"""
Builds docs/report/growth.json (screener fundamentals: EPS / revenue YoY, quarterly and annual, Taiwan
and US) from the statement history in docs/report/financials/<KEY>.json. Offline: it only reads files
that build_financials.py / seed_financials_sec.py already wrote, so it runs right after them.

  python scripts/build_growth.py          (working directory: atlas)
  Env: ATLAS_REPORT_DIR (default ../docs/report),
       ATLAS_FINANCIALS_DIR (default <ATLAS_REPORT_DIR>/financials),
       ATLAS_GROWTH_OUT (default <ATLAS_REPORT_DIR>/growth.json).

YoY always compares the SAME period of the year before, never the neighbouring period:
- quarters: (fy, fq) against (fy - 1, fq); annual: fy against fy - 1. The period-end dates of a
  pair must be 335-395 days apart (52/53-week years fit; a changed fiscal year end does not and
  gives null). A duplicated (fy, fq) / fy label is ambiguous and gives null.
- slots are consecutive calendar periods counted back from the newest period that has EPS or
  revenue: slot 0 = latest, slot 1 = the quarter before, ... A quarter missing from the file (Yahoo
  often has no Taiwan Q4 EPS, or no revenue for the oldest quarter) leaves a null slot; nothing is
  derived from other fields and nothing shifts into the gap.
- EPS: diluted when both sides of the pair have it, otherwise basic when both do; never mixed.
  YoY % = (EPS / EPS a year ago - 1) * 100, only when the year-ago EPS is > 0. Year-ago EPS <= 0 and
  EPS now > 0 is a turnaround ("epsTurn", YoY stays null); a loss that narrows or widens is null.
  Revenue needs a year-ago revenue > 0.
- A split of >= 1.5x between the two period ends whose share counts still differ by about that
  ratio means the pair is not on one share basis: its EPS YoY is null instead of a fake number.
- Staleness: a newest quarter older than 270 days (annual: 560) gives no quarterly (annual) values.
Nothing is estimated; whatever cannot be computed stays null.

Output (compact, rows sorted by symbol; keys as in the financials file names):
  {"version": 1, "generatedAt": "...", "source": "financials", "fields": [...], "rows": [[...], ...]}
  fields: symbol, lv (financials quality level good/partial/limited), src (sec|yahoo, of the newest
  quarter), qEnd, yEnd (period end of slot 0 for quarterly / annual), epsQ (4), revQ (4), epsY (3),
  revY (3), tQ, tY (bit i set = EPS turnaround in slot i of quarterly / annual). Trailing nulls of
  the arrays are cut. A symbol without any usable value gets no row. The market is the symbol's
  suffix (.TW / .TWO = Taiwan). The file is only rewritten when its content changes, so generatedAt
  is the last change.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from build_valuation_us import finite_number, read_json, write_atomic

VERSION = 1
SOURCE = "financials"
FIELDS = ["symbol", "lv", "src", "qEnd", "yEnd", "epsQ", "revQ", "epsY", "revY", "tQ", "tY"]
QUARTER_SLOTS = 4
YEAR_SLOTS = 3
SAME_PERIOD_MIN_DAYS = 335
SAME_PERIOD_MAX_DAYS = 395
STALE_QUARTER_DAYS = 270
STALE_ANNUAL_DAYS = 560
MIN_SPLIT_RATIO = 1.5
SPLIT_MATCH = 0.25
TW_SUFFIXES = (".TW", ".TWO")


def parse_day(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def market_of(key):
    return "TW" if key.endswith(TW_SUFFIXES) else "US"


# ---------------------------------------------------------------------------------------------
# Reading a financials document
# ---------------------------------------------------------------------------------------------
def column(values, name, count):
    """values[name] as a list of `count` finite numbers / None (a missing or short column → None)."""
    raw = values.get(name) if isinstance(values, dict) else None
    raw = raw if isinstance(raw, list) else []
    return [finite_number(raw[i]) if i < len(raw) else None for i in range(count)]


def read_periods(section, quarterly):
    """→ [{end, fy, fq, epsD, epsB, rev, shares, src}] for the periods with a valid end date and fy."""
    if not isinstance(section, dict) or not isinstance(section.get("end"), list):
        return []
    ends = section["end"]
    count = len(ends)
    values = section.get("values")
    fys = section.get("fy") if isinstance(section.get("fy"), list) else []
    fqs = section.get("fq") if isinstance(section.get("fq"), list) else []
    srcs = section.get("src") if isinstance(section.get("src"), list) else []
    eps_d, eps_b = column(values, "epsDiluted", count), column(values, "epsBasic", count)
    revenue, shares = column(values, "revenue", count), column(values, "dilutedShares", count)
    periods = []
    for i, end in enumerate(ends):
        day = parse_day(end)
        fy = fys[i] if i < len(fys) else None
        fq = fqs[i] if quarterly and i < len(fqs) else None
        if day is None or isinstance(fy, bool) or not isinstance(fy, int):
            continue
        if quarterly and (isinstance(fq, bool) or not isinstance(fq, int) or not 1 <= fq <= 4):
            continue
        periods.append({
            "end": end, "day": day, "fy": fy, "fq": fq,
            "epsD": eps_d[i], "epsB": eps_b[i], "rev": revenue[i], "shares": shares[i],
            "src": srcs[i] if i < len(srcs) and isinstance(srcs[i], str) else "yahoo",
        })
    return periods


def usable(period):
    return any(period[name] is not None for name in ("epsD", "epsB", "rev"))


def split_events(document):
    events = ((document.get("split") or {}).get("events")) if isinstance(document.get("split"), dict) else None
    result = []
    for event in events if isinstance(events, list) else []:
        if isinstance(event, list) and len(event) >= 2 and parse_day(event[0]) and finite_number(event[1]):
            if event[1] > 0:
                result.append((parse_day(event[0]), float(event[1])))
    return result


# ---------------------------------------------------------------------------------------------
# YoY of one pair
# ---------------------------------------------------------------------------------------------
def growth_pct(current, base):
    if current is None or base is None or base <= 0:
        return None
    return round((current / base - 1) * 100, 1)


def share_basis_broken(current, base, events):
    """True when a >= 1.5x split fell between the pair and the share counts still differ by that ratio."""
    if current["shares"] is None or base["shares"] is None or base["shares"] <= 0:
        return False
    actual = current["shares"] / base["shares"]
    for day, ratio in events:
        if not (base["day"] < day <= current["day"] + timedelta(days=45)):
            continue
        if not (ratio >= MIN_SPLIT_RATIO or ratio <= 1 / MIN_SPLIT_RATIO):
            continue
        if abs(actual / ratio - 1) <= SPLIT_MATCH:
            return True
    return False


def eps_pair(current, base, events):
    """→ (yoy %, turnaround) of EPS for one pair; (None, False) when it cannot be compared."""
    if share_basis_broken(current, base, events):
        return None, False
    for name in ("epsD", "epsB"):
        now_value, before = current[name], base[name]
        if now_value is None or before is None:
            continue
        if before > 0:
            return growth_pct(now_value, before), False
        return None, now_value > 0
    return None, False


def same_period(current, base):
    gap = (current["day"] - base["day"]).days
    return SAME_PERIOD_MIN_DAYS <= gap <= SAME_PERIOD_MAX_DAYS


def index_periods(periods, quarterly):
    """{period number: period}; a label that occurs twice is ambiguous and maps to None."""
    found = {}
    for period in periods:
        number = period["fy"] * 4 + period["fq"] - 1 if quarterly else period["fy"]
        found[number] = None if number in found else period
    return found


def yoy_series(periods, quarterly, slots, events, today, stale_days):
    """→ (epsYoY, revYoY, turn mask, latest end or None, its source) over `slots` periods, newest first."""
    empty = ([None] * slots, [None] * slots, 0, None, "yahoo")
    candidates = [period for period in periods if usable(period)]
    if not candidates:
        return empty
    latest = max(candidates, key=lambda p: (p["fy"] * 4 + p["fq"] - 1) if quarterly else p["fy"])
    if (today - latest["day"]).days > stale_days:
        return empty
    by_number = index_periods(periods, quarterly)
    top = latest["fy"] * 4 + latest["fq"] - 1 if quarterly else latest["fy"]
    step = 4 if quarterly else 1
    eps, rev, mask = [], [], 0
    for slot in range(slots):
        current, base = by_number.get(top - slot), by_number.get(top - slot - step)
        if current is None or base is None or not same_period(current, base):
            eps.append(None)
            rev.append(None)
            continue
        value, turn = eps_pair(current, base, events)
        eps.append(value)
        rev.append(growth_pct(current["rev"], base["rev"]))
        if turn:
            mask |= 1 << slot
    return eps, rev, mask, latest["end"], latest["src"]


def trim(values):
    values = list(values)
    while values and values[-1] is None:
        values.pop()
    return values


# ---------------------------------------------------------------------------------------------
# One symbol / whole directory
# ---------------------------------------------------------------------------------------------
def build_row(key, document, today):
    """→ row for growth.json, or None when the document has nothing usable."""
    if not isinstance(document, dict):
        return None
    events = split_events(document)
    quarterly = read_periods(document.get("quarterly"), True)
    annual = read_periods(document.get("annual"), False)
    q = yoy_series(quarterly, True, QUARTER_SLOTS, events, today, STALE_QUARTER_DAYS)
    y = yoy_series(annual, False, YEAR_SLOTS, events, today, STALE_ANNUAL_DAYS)
    eps_q, rev_q, mask_q, q_end = trim(q[0]), trim(q[1]), q[2], q[3]
    eps_y, rev_y, mask_y, y_end = trim(y[0]), trim(y[1]), y[2], y[3]
    if not (eps_q or rev_q or eps_y or rev_y or mask_q or mask_y):
        return None
    quality = document.get("quality") if isinstance(document.get("quality"), dict) else {}
    level = quality.get("level") if quality.get("level") in ("good", "partial", "limited") else "limited"
    source = q[4] if q_end else y[4]
    return [
        key, level, "sec" if source == "sec" else "yahoo", q_end if (eps_q or rev_q or mask_q) else None,
        y_end if (eps_y or rev_y or mask_y) else None,
        eps_q, rev_q, eps_y, rev_y, mask_q, mask_y,
    ]


def read_documents(directory):
    """→ ([(key, document)], unreadable file names). index.json and non-symbol files are skipped."""
    documents, bad = [], []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return [], []
    for name in names:
        if not name.endswith(".json") or name == "index.json":
            continue
        key = name[:-5]
        document = read_json(os.path.join(directory, name))
        if not isinstance(document, dict) or not isinstance(document.get("quarterly"), dict):
            bad.append(name)
            continue
        documents.append((key, document))
    return documents, bad


def leading_count(values, mask):
    """Consecutive slots from the newest with a YoY value or an EPS turnaround."""
    count = 0
    for slot in range(len(values)):
        if values[slot] is None and not (mask >> slot) & 1:
            break
        count += 1
    return count


def coverage(rows, files):
    """Per market: files read, rows written, and how many have >= n periods of EPS / revenue YoY."""
    stats = {}
    for key, _ in files:
        stats.setdefault(market_of(key), {"files": 0, "rows": 0})["files"] += 1
    for row in rows:
        entry = stats.setdefault(market_of(row[0]), {"files": 0, "rows": 0})
        entry["rows"] += 1
        _, _, _, _, _, eps_q, rev_q, eps_y, rev_y, mask_q, mask_y = row
        for name, n in (("epsQ", leading_count(eps_q, mask_q)), ("epsY", leading_count(eps_y, mask_y)),
                        ("revQ", leading_count(rev_q, 0)), ("revY", leading_count(rev_y, 0))):
            for need in range(1, (QUARTER_SLOTS if name.endswith("Q") else YEAR_SLOTS) + 1):
                if n >= need:
                    entry[f"{name}>={need}"] = entry.get(f"{name}>={need}", 0) + 1
        if mask_q & 1:
            entry["epsTurnLatestQ"] = entry.get("epsTurnLatestQ", 0) + 1
    return stats


def build_document(files, now):
    today = now.date()
    rows = []
    for key, document in files:
        row = build_row(key, document, today)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda row: row[0])
    return {
        "version": VERSION,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": SOURCE,
        "fields": FIELDS,
        "rows": rows,
    }


def same_content(old, new):
    keys = ("version", "source", "fields", "rows")
    return isinstance(old, dict) and all(old.get(key) == new.get(key) for key in keys)


def run(financials_dir, out_path, now=None):
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    files, bad = read_documents(financials_dir)
    if not files:
        raise RuntimeError(f"no readable financials files in {financials_dir}")
    document = build_document(files, now)
    previous = read_json(out_path)
    written = False
    if not same_content(previous, document):
        size = write_atomic(out_path, document)
        written = True
    else:
        size = os.path.getsize(out_path)
    return {
        "files": len(files),
        "unreadable": bad,
        "rows": len(document["rows"]),
        "written": written,
        "bytes": size,
        "coverage": coverage(document["rows"], files),
    }


def main():
    report_dir = os.environ.get("ATLAS_REPORT_DIR") or "../docs/report"
    financials_dir = os.environ.get("ATLAS_FINANCIALS_DIR") or os.path.join(report_dir, "financials")
    out_path = os.environ.get("ATLAS_GROWTH_OUT") or os.path.join(report_dir, "growth.json")
    print(json.dumps(run(financials_dir, out_path), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"::warning::growth.json not rebuilt (the previous file stays): {error}")

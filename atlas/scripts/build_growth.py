"""
Builds docs/report/growth.json (screener fundamentals: EPS / revenue YoY, quarterly and annual, Taiwan
and US). Offline: it only reads files other steps already wrote, so it runs right after them.

  python scripts/build_growth.py          (working directory: atlas)
  Env: ATLAS_REPORT_DIR (default ../docs/report): also universe.json (Taiwan code → .TW / .TWO),
       ATLAS_FINANCIALS_DIR (default <ATLAS_REPORT_DIR>/financials),
       ATLAS_FUNDAMENTALS_DIR (default <ATLAS_REPORT_DIR>/fundamentals),
       ATLAS_GROWTH_OUT (default <ATLAS_REPORT_DIR>/growth.json).

Sources. Every row says where each block (quarterly / annual) comes from (src / srcY):
- "sec" / "yahoo" / "finmind": the statement history in docs/report/financials/<KEY>.json (build_financials.py /
  seed_financials_sec.py / build_financials_tw_finmind.py); the src of the block's newest period.
- "mops": Taiwan official income statements, docs/report/fundamentals/tw.json (build_fundamentals_tw.py:
  公開資訊觀測站 綜合損益表彙總報表 / 證交所・櫃買中心 OpenAPI; year-to-date figures turned into quarters).
- "frames": US SEC XBRL frames, docs/report/fundamentals/us-sec.json (build_fundamentals_sec.py; its
  rows are computed with the rules below by that script, their staleness is checked again here).
- "finmind": Taiwan history from FinMind (data from 公開資訊觀測站), docs/report/fundamentals/tw-finmind.json
  (build_fundamentals_tw_finmind.py, which also picks between it and "mops" per block: the newer period,
  for the same period "mops" unless FinMind has more slots; its annual series is as long as its data).
Per symbol and block the newest period wins: candidates whose slot-0 period ends within FRESHER_DAYS
of the newest one count as the same period, and among those the better source wins (SOURCE_TIERS: SEC
companyfacts files and the official Taiwan statements, then SEC frames, then Yahoo). A block is never
mixed: all its slots come from one source.

YoY always compares the SAME period of the year before, never the neighbouring period:
- quarters: (fy, fq) against (fy - 1, fq); annual: fy against fy - 1. The period-end dates of a
  pair must be 335-395 days apart (52/53-week years fit; a changed fiscal year end does not and
  gives null). A duplicated (fy, fq) / fy label is ambiguous and gives null.
- slots are consecutive calendar periods counted back from the newest period that has EPS or
  revenue: slot 0 = latest, slot 1 = the quarter before, ... A quarter missing from the file (Yahoo
  often has no Taiwan Q4 EPS, or no revenue for the oldest quarter) leaves a null slot; nothing
  shifts into the gap.
- EPS: diluted when both sides of the pair have it, otherwise basic when both do; never mixed.
  YoY % = (EPS / EPS a year ago - 1) * 100, only when the year-ago EPS is > 0. Year-ago EPS <= 0 and
  EPS now > 0 is a turnaround ("epsTurn", YoY stays null); a loss that narrows or widens is null.
  Revenue needs a year-ago revenue > 0.
- A split of >= 1.5x between the two period ends whose share counts still differ by about that
  ratio means the pair is not on one share basis: its EPS YoY is null instead of a fake number. The
  frames / Taiwan sources have no split events: there a share count change of >= 1.5x is enough.
- Staleness: a newest quarter older than 270 days (annual: 560) gives no quarterly (annual) values.
Nothing is estimated; whatever cannot be computed stays null. The only derived periods are the Taiwan
quarters (differences of year-to-date figures) and the SEC frames Q4s (fiscal year minus its first
three quarters); dQ marks the quarterly slots whose pair contains such a derived fourth quarter.

Output (compact, rows sorted by symbol; keys as in the financials file names):
  {"version": 1, "generatedAt": "...", "source": "...", "fields": [...], "rows": [[...], ...]}
  fields: symbol, lv (financials quality level good/partial/limited; null for the other sources),
  src (sec|yahoo|mops|frames|finmind: of the quarterly block, else of the annual one), qEnd, yEnd (period end
  of slot 0 for quarterly / annual), epsQ (up to 12), revQ (up to 12), epsY (up to 10), revY (up to 10),
  tQ, tY (bit i set = EPS turnaround in slot i of quarterly / annual), srcY (source of the annual block,
  null without one), dQ (bit i set = quarterly slot i compares a derived Q4). Trailing nulls of the arrays
  are cut, so a row carries as many quarters as its source has (a file written with the old 4-slot horizon
  is still valid: its arrays are just shorter). A symbol without any usable value gets no row. The market
  is the symbol's suffix (.TW / .TWO = Taiwan). The file is only rewritten when its content changes, so
  generatedAt is the last change.
Quarterly horizon: up to QUARTER_SLOTS (12) YoY values, i.e. 16 quarters of history for the newest one.
The financials files, FinMind's Taiwan history and the SEC companyfacts files have that much; the Taiwan
statements (MOPS, RECENT_SEASONS) and the SEC frames (QUARTER_FRAMES) fetch fewer seasons and give fewer.
Annual horizon: up to YEAR_SLOTS (10) YoY values, computed from the financials files (annual history of
up to 19 years). The Taiwan statements (MOPS) and the SEC frames have only a few annual periods and keep
RECENT_YEAR_SLOTS (3) values. A source's annual block only competes when it has a YoY value within its
newest RECENT_YEAR_SLOTS years (the choice is the same as with the 3-year horizon); its older years come
with it when it wins.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from build_valuation_us import finite_number, read_json, write_atomic

VERSION = 1
SOURCE = "financials+official"
FIELDS = ["symbol", "lv", "src", "qEnd", "yEnd", "epsQ", "revQ", "epsY", "revY", "tQ", "tY", "srcY", "dQ"]
# Quarterly YoY values per row: the newest 12 quarters (each needs the same quarter a year earlier, so 16 quarters of history).
QUARTER_SLOTS = 12
# Annual YoY values per row from the financials files (each needs the year before it: 11 fiscal years for 10 values).
YEAR_SLOTS = 10
# The newest annual values: the whole horizon of the official sources (Taiwan MOPS, SEC frames), and the window
# that decides whether a source's annual block competes (choose_block).
RECENT_YEAR_SLOTS = 3
SAME_PERIOD_MIN_DAYS = 335
SAME_PERIOD_MAX_DAYS = 395
STALE_QUARTER_DAYS = 270
STALE_ANNUAL_DAYS = 560
MIN_SPLIT_RATIO = 1.5
SPLIT_MATCH = 0.25
TW_SUFFIXES = (".TW", ".TWO")
# Merge: lower tier = better source; slot-0 periods this close together are the same period
SOURCE_TIERS = {"sec": 0, "mops": 0, "frames": 1, "finmind": 1, "yahoo": 2}
FRESHER_DAYS = 45
# A pair whose ratio is this close to 10^3 / 10^6 (or the inverse) is a value filed in another scale
SCALE_FACTORS = (1e3, 1e6)
SCALE_TOLERANCE = 0.03
TW_FILE = "tw.json"
US_SEC_FILE = "us-sec.json"


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
        src = srcs[i] if i < len(srcs) and isinstance(srcs[i], str) else "yahoo"
        periods.append({
            "end": end, "day": day, "fy": fy, "fq": fq,
            "epsD": eps_d[i], "epsB": eps_b[i], "rev": revenue[i], "shares": shares[i],
            "src": src if src in ("sec", "finmind") else "yahoo",
        })
    return periods


def usable(period):
    return any(period.get(name) is not None for name in ("epsD", "epsB", "rev"))


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


def scale_suspect(current, base):
    """True when current / base is about 10^3 or 10^6 (or the inverse): one side filed in another scale."""
    if not current or not base:
        return False
    ratio = abs(current / base)
    for factor in SCALE_FACTORS:
        for target in (factor, 1 / factor):
            if abs(ratio / target - 1) <= SCALE_TOLERANCE:
                return True
    return False


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


def share_count_jump(current, base):
    """Sources without split events: share counts (weighted, or implied) >= 1.5x apart → not one basis."""
    now_shares, before = current.get("shares"), base.get("shares")
    if not now_shares or not before or now_shares <= 0 or before <= 0:
        return False
    ratio = now_shares / before
    return ratio >= MIN_SPLIT_RATIO or ratio <= 1 / MIN_SPLIT_RATIO


def eps_values(current, base):
    """→ (yoy %, turnaround) of EPS for one pair already on one share basis (diluted first, never mixed)."""
    for name in ("epsD", "epsB"):
        now_value, before = current.get(name), base.get(name)
        if now_value is None or before is None:
            continue
        if before > 0:
            return growth_pct(now_value, before), False
        return None, now_value > 0
    return None, False


def eps_pair(current, base, events):
    """→ (yoy %, turnaround) of EPS for one pair; (None, False) when it cannot be compared."""
    if share_basis_broken(current, base, events):
        return None, False
    return eps_values(current, base)


def default_pair(current, base, events):
    """Financials files: → (EPS yoy, turnaround, revenue yoy) of one pair."""
    value, turn = eps_pair(current, base, events)
    return value, turn, growth_pct(current["rev"], base["rev"])


def same_period(current, base):
    gap = (current["day"] - base["day"]).days
    return SAME_PERIOD_MIN_DAYS <= gap <= SAME_PERIOD_MAX_DAYS


def period_number(period, quarterly):
    return period["fy"] * 4 + period["fq"] - 1 if quarterly else period["fy"]


def index_periods(periods, quarterly):
    """{period number: period}; a label that occurs twice is ambiguous and maps to None."""
    found = {}
    for period in periods:
        number = period_number(period, quarterly)
        found[number] = None if number in found else period
    return found


def trim(values):
    values = list(values)
    while values and values[-1] is None:
        values.pop()
    return values


def yoy_series(periods, quarterly, slots, events, today, stale_days, pair=None):
    """
    → block {eps, rev, mask, end, src, derived} over `slots` periods (newest first, trailing nulls cut),
    or None when nothing is computable or the newest period is stale. `pair(current, base)` →
    (EPS yoy, turnaround, revenue yoy) (default: default_pair with the split events). A period dict
    with "derived": True sets the slot's bit in `derived`.
    """
    candidates = [period for period in periods if usable(period)]
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: period_number(p, quarterly))
    if (today - latest["day"]).days > stale_days:
        return None
    by_number = index_periods(periods, quarterly)
    top = period_number(latest, quarterly)
    step = 4 if quarterly else 1
    eps, rev, mask, derived = [], [], 0, 0
    for slot in range(slots):
        current, base = by_number.get(top - slot), by_number.get(top - slot - step)
        if current is None or base is None or not same_period(current, base):
            eps.append(None)
            rev.append(None)
            continue
        if pair is None:
            value, turn, revenue = default_pair(current, base, events)
        else:
            value, turn, revenue = pair(current, base)
        eps.append(value)
        rev.append(revenue)
        if turn:
            mask |= 1 << slot
        if (value is not None or revenue is not None or turn) and (current.get("derived") or base.get("derived")):
            derived |= 1 << slot
    eps, rev = trim(eps), trim(rev)
    if not (eps or rev or mask):
        return None
    return {"eps": eps, "rev": rev, "mask": mask, "end": latest["end"], "src": latest["src"], "derived": derived}


# ---------------------------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------------------------
def make_row(key, level, quarterly, annual):
    """growth.json row from a quarterly and an annual block (either may be None); None without both."""
    if quarterly is None and annual is None:
        return None
    q, y = quarterly or {}, annual or {}
    return [
        key, level, (quarterly or annual)["src"], q.get("end"), y.get("end"),
        q.get("eps", []), q.get("rev", []), y.get("eps", []), y.get("rev", []),
        q.get("mask", 0), y.get("mask", 0), y.get("src"), q.get("derived", 0),
    ]


def financials_blocks(document, today):
    """→ (quality level, quarterly block, annual block) of one financials document, or None."""
    if not isinstance(document, dict):
        return None
    events = split_events(document)
    quarterly = read_periods(document.get("quarterly"), True)
    annual = read_periods(document.get("annual"), False)
    q = yoy_series(quarterly, True, QUARTER_SLOTS, events, today, STALE_QUARTER_DAYS)
    y = yoy_series(annual, False, YEAR_SLOTS, events, today, STALE_ANNUAL_DAYS)
    if q is None and y is None:
        return None
    quality = document.get("quality") if isinstance(document.get("quality"), dict) else {}
    level = quality.get("level") if quality.get("level") in ("good", "partial", "limited") else "limited"
    return level, q, y


def build_row(key, document, today):
    """→ row for growth.json from a financials document, or None when the document has nothing usable."""
    blocks = financials_blocks(document, today)
    return None if blocks is None else make_row(key, *blocks)


def has_value(block, slots):
    """A YoY value or an EPS turnaround within the newest `slots` slots of a block."""
    values = block["eps"][:slots] + block["rev"][:slots]
    return any(value is not None for value in values) or bool(block["mask"] & ((1 << slots) - 1))


def choose_block(blocks, slots):
    """The newest block with a value in its newest `slots` slots; within FRESHER_DAYS of it the best source
    (SOURCE_TIERS), then input order."""
    blocks = [block for block in blocks if block is not None and parse_day(block.get("end")) and has_value(block, slots)]
    if not blocks:
        return None
    newest = max(parse_day(block["end"]) for block in blocks)
    near = [block for block in blocks if (newest - parse_day(block["end"])).days <= FRESHER_DAYS]
    return min(near, key=lambda block: SOURCE_TIERS.get(block["src"], len(SOURCE_TIERS)))


def merge_candidates(key, candidates):
    """candidates: [(quality level or None, quarterly block, annual block)] → one row (or None)."""
    q = choose_block([candidate[1] for candidate in candidates], QUARTER_SLOTS)
    y = choose_block([candidate[2] for candidate in candidates], RECENT_YEAR_SLOTS)
    level = None
    for candidate in candidates:
        if candidate[0] and ((q is not None and candidate[1] is q) or (y is not None and candidate[2] is y)):
            level = candidate[0]
            break
    return make_row(key, level, q, y)


def block_from_row(fields, row, prefix, today, src):
    """us-sec.json row → quarterly ("Q") or annual ("Y") block, None when empty or stale."""
    get = dict(zip(fields, row)).get
    end = get(f"{prefix.lower()}End")
    day = parse_day(end)
    if day is None:
        return None
    if (today - day).days > (STALE_QUARTER_DAYS if prefix == "Q" else STALE_ANNUAL_DAYS):
        return None
    slots = QUARTER_SLOTS if prefix == "Q" else RECENT_YEAR_SLOTS

    def values(name):
        raw = get(name)
        return trim([finite_number(value) for value in raw[:slots]]) if isinstance(raw, list) else []

    def bits(name):
        raw = get(name)
        return raw & ((1 << slots) - 1) if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else 0

    eps, rev, mask = values(f"eps{prefix}"), values(f"rev{prefix}"), bits(f"t{prefix}")
    if not (eps or rev or mask):
        return None
    return {"eps": eps, "rev": rev, "mask": mask, "end": end, "src": src,
            "derived": bits("dQ") if prefix == "Q" else 0}


def sec_frame_candidates(document, today):
    """us-sec.json (build_fundamentals_sec.py) → {key: (None, quarterly block, annual block)}."""
    result = {}
    if not isinstance(document, dict) or document.get("version") != 1:
        return result
    fields, rows = document.get("fields"), document.get("rows")
    if not isinstance(fields, list) or not isinstance(rows, list) or "symbol" not in fields:
        return result
    at = fields.index("symbol")
    for row in rows:
        if not isinstance(row, list) or len(row) <= at or not isinstance(row[at], str) or not row[at]:
            continue
        q = block_from_row(fields, row, "Q", today, "frames")
        y = block_from_row(fields, row, "Y", today, "frames")
        if q is not None or y is not None:
            result[row[at]] = (None, q, y)
    return result


# ---------------------------------------------------------------------------------------------
# Whole directory
# ---------------------------------------------------------------------------------------------
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


def leading_count(values, mask, slots):
    """Consecutive slots from the newest (of `slots`) with a YoY value or an EPS turnaround."""
    count = 0
    for slot in range(slots):
        value = values[slot] if slot < len(values) else None
        if value is None and not (mask >> slot) & 1:
            break
        count += 1
    return count


def coverage(rows, files):
    """Per market: files read, rows written, sources and how many have >= n periods of EPS / revenue YoY."""
    stats = {}
    for key, _ in files:
        stats.setdefault(market_of(key), {"files": 0, "rows": 0})["files"] += 1
    for row in rows:
        entry = stats.setdefault(market_of(row[0]), {"files": 0, "rows": 0})
        entry["rows"] += 1
        r = dict(zip(FIELDS, row))
        counts = (
            ("epsQ", leading_count(r["epsQ"], r["tQ"], QUARTER_SLOTS)),
            ("epsY", leading_count(r["epsY"], r["tY"], YEAR_SLOTS)),
            ("revQ", leading_count(r["revQ"], 0, QUARTER_SLOTS)),
            ("revY", leading_count(r["revY"], 0, YEAR_SLOTS)),
        )
        for name, n in counts:
            for need in range(1, (QUARTER_SLOTS if name.endswith("Q") else YEAR_SLOTS) + 1):
                if n >= need:
                    entry[f"{name}>={need}"] = entry.get(f"{name}>={need}", 0) + 1
        if r["tQ"] & 1:
            entry["epsTurnLatestQ"] = entry.get("epsTurnLatestQ", 0) + 1
        if r["qEnd"]:
            entry[f"srcQ:{r['src']}"] = entry.get(f"srcQ:{r['src']}", 0) + 1
        if r["srcY"]:
            entry[f"srcY:{r['srcY']}"] = entry.get(f"srcY:{r['srcY']}", 0) + 1
        if r["dQ"]:
            entry["derivedQ4"] = entry.get("derivedQ4", 0) + 1
    return stats


def build_document(files, now, extra=None):
    """files: [(key, financials document)]; extra: {key: [(level, q block, y block)]} of the other sources."""
    today = now.date()
    candidates = {}
    # The other sources first: on a tie of source tier (FinMind's growth history and the FinMind statement file
    # both say "finmind") the first candidate wins, so the growth values stay those of the growth history
    for key, entries in (extra or {}).items():
        candidates.setdefault(key, []).extend(entries)
    for key, document in files:
        blocks = financials_blocks(document, today)
        if blocks is not None:
            candidates.setdefault(key, []).append(blocks)
    rows = []
    for key, entries in candidates.items():
        row = merge_candidates(key, entries)
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


def official_candidates(fundamentals_dir, universe, today, tw_keys=()):
    """{key: [(level, q, y)]} from fundamentals/tw.json and fundamentals/us-sec.json, plus a summary."""
    extra, summary = {}, {}
    if not fundamentals_dir:
        return extra, summary
    import build_fundamentals_tw as tw  # noqa: PLC0415 - that module imports this one

    store = read_json(os.path.join(fundamentals_dir, TW_FILE))
    tw_rows = {}
    if store is not None:
        try:
            tw_rows = tw.growth_candidates(store, tw.symbol_map(universe, tw_keys), today)
        except tw.StoreError as error:
            print(f"::warning::{TW_FILE} ignored: {error}")
        summary["tw"] = len(tw_rows)
    tw_rows = finmind_candidates(fundamentals_dir, tw.symbol_map(universe, tw_keys), today, store, tw_rows, summary)
    for key, blocks in tw_rows.items():
        extra.setdefault(key, []).append(blocks)
    frames = sec_frame_candidates(read_json(os.path.join(fundamentals_dir, US_SEC_FILE)), today)
    summary["frames"] = len(frames)
    for key, blocks in frames.items():
        extra.setdefault(key, []).append(blocks)
    return extra, summary


def finmind_candidates(fundamentals_dir, symbols, today, official, tw_rows, summary):
    """tw.json candidates combined with fundamentals/tw-finmind.json (when present) → one per Taiwan symbol."""
    import build_fundamentals_tw_finmind as finmind  # noqa: PLC0415 - that module imports this one

    document = read_json(os.path.join(fundamentals_dir, finmind.FILE_NAME))
    if document is None:
        return tw_rows
    try:
        rows, summary["finmind"] = finmind.growth_candidates(document, symbols, today, official)
    except finmind.StoreError as error:
        print(f"::warning::{finmind.FILE_NAME} ignored: {error}")
        summary["finmind"] = {"rows": 0, "error": str(error)}
        return tw_rows
    return finmind.combine(tw_rows, rows)


def run(financials_dir, out_path, now=None, fundamentals_dir=None, universe_path=None):
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    files, bad = read_documents(financials_dir)
    universe = read_json(universe_path) if universe_path else None
    tw_keys = [key for key, _ in files if market_of(key) == "TW"]
    extra, official = official_candidates(fundamentals_dir, universe, now.date(), tw_keys)
    if not files and not extra:
        raise RuntimeError(f"no readable financials files in {financials_dir} and no official fundamentals")
    document = build_document(files, now, extra)
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
        "official": official,
        "rows": len(document["rows"]),
        "written": written,
        "bytes": size,
        "coverage": coverage(document["rows"], files),
    }


def main():
    report_dir = os.environ.get("ATLAS_REPORT_DIR") or "../docs/report"
    financials_dir = os.environ.get("ATLAS_FINANCIALS_DIR") or os.path.join(report_dir, "financials")
    fundamentals_dir = os.environ.get("ATLAS_FUNDAMENTALS_DIR") or os.path.join(report_dir, "fundamentals")
    out_path = os.environ.get("ATLAS_GROWTH_OUT") or os.path.join(report_dir, "growth.json")
    summary = run(financials_dir, out_path, fundamentals_dir=fundamentals_dir,
                  universe_path=os.path.join(report_dir, "universe.json"))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"::warning::growth.json not rebuilt (the previous file stays): {error}")

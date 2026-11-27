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
  Taiwan financials files (FinMind EPS as published): periods still on a pre-split basis by their
  implied share count (net income / EPS) are restated first (restate_splits, the epsBasis.ts rule);
  a pair a split separates whose implied counts still jump >= 1.5x, or that lacks that evidence,
  has no EPS YoY (tw_pair). Split events: the Yahoo-era "split" block, FinMind splits / par-value changes
  ("twSplits", tw_splits.py) and, without either, splits inferred from a clean step of the implied share
  counts (tw_splits.infer_splits).
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
  null without one), dQ (bit i set = quarterly slot i compares a derived Q4), gQ (bit i set = the EPS YoY
  in quarter i is missing because low-confidence EPS was excluded). Trailing nulls of the arrays
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
import math
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

from build_valuation_us import finite_number, read_json, write_atomic
import tw_capital_reductions as capital_reductions
import tw_splits
import tw_stock_dividends as stock_dividends

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.output_guard import finite, require, retain_keys, unique_keys

VERSION = 1
SOURCE = "financials+official"
FIELDS = ["symbol", "lv", "src", "qEnd", "yEnd", "epsQ", "revQ", "epsY", "revY", "tQ", "tY", "srcY", "dQ", "gQ"]
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


# Q4_EPS_STRICT=1: a US Q4 EPS counts only when graded A (published, confirmed); the default also accepts Y
# (derived, annual minus Q1-Q3) and ungraded values. C, D and B never count for US filers.
STRICT_Q4_EPS = os.environ.get("Q4_EPS_STRICT") == "1"


def eps_usable(grade, us, q4, strict=STRICT_Q4_EPS):
    if not us:
        return True
    if grade in ("B", "C", "D"):
        return False
    if not q4:
        return True
    return grade == "A" if strict else grade in (None, "A", "Y")


def read_periods(section, quarterly, market=None):
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
    eps_quality = section.get("epsQuality") if isinstance(section.get("epsQuality"), list) else []
    revenue, shares = column(values, "revenue", count), column(values, "dilutedShares", count)
    income = column(values, "netIncome", count)
    periods = []
    for i, end in enumerate(ends):
        day = parse_day(end)
        fy = fys[i] if i < len(fys) else None
        fq = fqs[i] if quarterly and i < len(fqs) else None
        if day is None or isinstance(fy, bool) or not isinstance(fy, int):
            continue
        if quarterly and (isinstance(fq, bool) or not isinstance(fq, int) or not 1 <= fq <= 4):
            continue
        grade = eps_quality[i] if i < len(eps_quality) and isinstance(eps_quality[i], dict) else {}
        us, q4 = market != "TW", bool(quarterly and fq == 4)
        gated = {}
        if us and not eps_usable(grade.get("epsDiluted"), us, q4):
            if eps_d[i] is not None:
                gated["epsD"] = eps_d[i]
            eps_d[i] = None
        if us and not eps_usable(grade.get("epsBasic"), us, q4):
            if eps_b[i] is not None:
                gated["epsB"] = eps_b[i]
            eps_b[i] = None
        src = srcs[i] if i < len(srcs) and isinstance(srcs[i], str) else "yahoo"
        periods.append({
            "end": end, "day": day, "fy": fy, "fq": fq,
            "epsD": eps_d[i], "epsB": eps_b[i], "rev": revenue[i], "shares": shares[i], "ni": income[i],
            # a gated Q4 still anchors this filing block's recency (dropping it could switch to a shorter block)
            "gatedQ4Eps": bool(gated and q4),
            "gatedEps": gated,
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
    → block {eps, rev, mask, end, src, derived, excluded} over `slots` periods (newest first, trailing nulls cut),
    or None when nothing is computable or the newest period is stale. `pair(current, base)` →
    (EPS yoy, turnaround, revenue yoy) (default: default_pair with the split events). A period dict
    with "derived": True sets the slot's bit in `derived`.
    """
    candidates = [period for period in periods if usable(period) or (quarterly and period.get("gatedQ4Eps"))]
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: period_number(p, quarterly))
    if (today - latest["day"]).days > stale_days:
        return None
    by_number = index_periods(periods, quarterly)
    top = period_number(latest, quarterly)
    step = 4 if quarterly else 1
    eps, rev, mask, derived, excluded = [], [], 0, 0, 0
    for slot in range(slots):
        current, base = by_number.get(top - slot), by_number.get(top - slot - step)
        if current is None or base is None or not same_period(current, base):
            eps.append(None)
            rev.append(None)
            continue
        if pair is None:
            value, turn, revenue = default_pair(current, base, events)
            if quarterly and value is None and not turn and (current.get("gatedEps") or base.get("gatedEps")):
                # Restore excluded values only for attribution. The published numbers and block choice stay gated.
                raw_current = {**current, **current.get("gatedEps", {})}
                raw_base = {**base, **base.get("gatedEps", {})}
                raw_value, _ = eps_pair(raw_current, raw_base, events)
                if raw_value is not None:
                    excluded |= 1 << slot
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
    return {"eps": eps, "rev": rev, "mask": mask, "end": latest["end"], "src": latest["src"],
            "derived": derived, "excluded": excluded}


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
        q.get("mask", 0), y.get("mask", 0), y.get("src"), q.get("derived", 0), q.get("excluded", 0),
    ]


def restate_periods(periods, events):
    """EPS (and the share count) of every period on the latest share basis (追溯調整) for the given Taiwan stock
    dividend events, in place, so a pair spanning an ex-rights date is comparable. events None → untouched."""
    if events is None:
        return
    for period in periods:
        info = stock_dividends.factor(period["day"], events, annual=period.get("fq") is None)
        if info is not None:
            period["shareFactor"] = info["factor"]
        if info is None or info["factor"] == 1:
            continue
        for name in ("epsD", "epsB"):
            if period.get(name) is not None:
                period[name] = round(period[name] / info["factor"], 4) + 0.0
        if period.get("shares"):
            period["shares"] = period["shares"] * info["factor"]


def restate_for_stock_dividends(document, periods):
    """Taiwan files with stock dividend events: EPS of every period on the latest share basis (追溯調整), so a pair
    spanning an ex-rights date is comparable. No events block → EPS left as reported (never guessed)."""
    if document.get("market") != "TW":
        return
    restate_periods(periods, stock_dividends.events_of(document))


MIN_EVIDENCE_EPS = 0.05     # |EPS| below this: cent rounding alone moves the implied share count by > 10%


def tw_split_events(document, events, quarterly=None):
    """The split events of tw_split_plan, without the dated days."""
    return tw_split_plan(document, events, quarterly)[0]


def tw_split_plan(document, events, quarterly=None):
    """Usable split events (>= 1.5x or <= 1/1.5) of a Taiwan file, oldest first: the FinMind split / par-value change
    events of its "twSplits" block (tw_splits.py), the Yahoo events of its "split" block without those on a 配股 / 減資
    date (Yahoo records them as splits: that event, not a second one) or within 7 days of a FinMind event (the same
    split), and, given the quarterly periods (配股／減資-restated, before restate_splits) of a file with a
    stockDividends block, the splits inferred from their implied share counts (tw_splits.infer_splits).
    events: stock_dividends.events_of(document). → (splits, dated days: the FinMind and inferred events, which
    restate_splits also applies by date)."""
    known = [day for day in (stock_dividends.day_of(event.get("exDate")) for event in events or []) if day is not None]
    reductions = [event for event in capital_reductions.events_of(document) or [] if isinstance(event, dict)]
    known += [day for day in (stock_dividends.day_of(event.get("date")) for event in reductions) if day is not None]
    finmind = tw_splits.split_events(document)
    yahoo = [(day, ratio) for day, ratio in split_events(document)
             if (ratio >= MIN_SPLIT_RATIO or ratio <= 1 / MIN_SPLIT_RATIO) and all(abs((day - other).days) > 7 for other in known)
             and all(abs((day - other).days) > 7 for other, _ in finmind)]
    recorded = sorted(finmind + yahoo)
    dated = {day for day, _ in finmind}
    if quarterly is None or events is None:
        return recorded, dated
    inferred = [(event["day"], event["ratio"]) for event in inferred_splits(document, quarterly, recorded, events)]
    return sorted(recorded + inferred), dated | {day for day, _ in inferred}


def inferred_splits(document, quarterly, recorded, events):
    """tw_splits.infer_splits on the quarterly periods (EPS already 配股／減資-restated in place); blocked by the recorded
    splits (any ratio), 配股 of >= 5 NT$ and capital reductions that are not restated (cash / mixed / other)."""
    blockers = [day for day, _ in recorded] + [day for day, _ in split_events(document)]
    blockers += [stock_dividends.day_of(event.get("exDate")) for event in events
                 if "shareRatio" not in event and stock_dividends.number(event.get("perShare")) >= tw_splits.BIG_STOCK_DIVIDEND]
    blockers += [stock_dividends.day_of(event.get("date")) for event in capital_reductions.events_of(document) or []
                 if isinstance(event, dict) and not capital_reductions.applies(event)]
    points = [{"day": period["day"], "eps": period["epsD"] if period.get("epsD") is not None else period.get("epsB"),
               "ni": period.get("ni"), "rev": period.get("rev"),
               "due": period["day"] + timedelta(days=stock_dividends.delay_days(period["day"]))} for period in quarterly]
    return tw_splits.infer_splits(points, [day for day in blockers if day is not None])


def implied_shares(period):
    """Net income ÷ EPS (diluted, else basic) when both are usable (same sign, |EPS| >= MIN_EVIDENCE_EPS), else None."""
    eps = period.get("epsD") if period.get("epsD") is not None else period.get("epsB")
    income = period.get("ni")
    if eps is None or income is None or income == 0 or abs(eps) < MIN_EVIDENCE_EPS:
        return None
    shares = income / eps
    return shares if shares > 0 else None


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def report_due(period):
    """Report due date of a period: annual (fq None) end + 90 days, else the quarter's delay."""
    if period.get("fq") is None:
        return period["day"] + timedelta(days=stock_dividends.DELAY_DAYS["yearEnd"])
    return period["day"] + timedelta(days=stock_dividends.delay_days(period["day"]))


def restate_splits(periods, splits, anchors=(), dated=()):
    """
    Taiwan FinMind EPS are as published, so periods printed before a split (8422.TW 1 拆 10 on 2025-11-06) sit on the
    old share basis. Mirror of src/fundamentals/epsBasis.ts splitFactors, in place, on one block (after the 配股 / 減資
    restatement): walking from the newest period back, a period's implied share count (net income ÷ EPS) × the
    product of a suffix of the splits after its end must land within ±SPLIT_MATCH of the median of the next (up to)
    three newer adjusted counts and of the next newer one, beat leaving it alone, and the splits must fall before
    that newer period's report (+90 days) unless the period continues its factor. Then EPS ÷ factor. Every period gets
    "splitFactor" and "splitApplied" (days); periods without an implied count keep factor 1. anchors: copies of newer
    periods of the other block, walked for evidence only (the annual block takes the quarters after its newest year,
    so a year before a split the annual block does not reach yet, 5904.TWO 2025, is still confirmed and restated).
    dated: days of the FinMind / inferred events. A period whose report was due before such an event and that the
    evidence above did not settle takes their product unless its implied count already matches the newer basis
    without them; a period without an implied count takes it too (epsBasis.ts splitFactors `dues`).
    """
    anchors = [dict(anchor) for anchor in anchors]
    for period in list(periods) + anchors:
        period["splitFactor"], period["splitApplied"] = 1.0, ()
    if not splits:
        return
    dated = set(dated)

    def dated_after(period):
        due = report_due(period)
        return [(day, ratio) for day, ratio in splits if day in dated and day > due]

    order = sorted((p for p in list(periods) + anchors if implied_shares(p) is not None), key=lambda p: p["day"], reverse=True)
    newer, previous = [], None
    for period in order:
        shares = implied_shares(period)
        if newer and previous is not None:
            reference = _median(newer[-3:])
            after = [split for split in splits if split[0] > period["day"]]
            best, best_count, best_distance, product = 1.0, 0, abs(math.log(shares / reference)), 1.0
            for k in range(len(after) - 1, -1, -1):
                product *= after[k][1]
                distance = abs(math.log(shares * product / reference))
                if distance < best_distance:
                    best, best_count, best_distance = product, len(after) - k, distance
            applied = after[len(after) - best_count:] if best_count else []
            due_of_newer = previous["day"] + timedelta(days=90)
            known = set(previous["splitApplied"])
            transition = all(day in known or day <= due_of_newer for day, _ in applied)
            continuation = previous["splitFactor"] == best
            neighbour = newer[-1]

            def matches(count, best=best, shares=shares):
                return abs(shares * best / count - 1) <= SPLIT_MATCH

            if best != 1 and matches(reference) and matches(neighbour) and (transition or continuation):
                period["splitFactor"], period["splitApplied"] = best, tuple(day for day, _ in applied)
        forced = dated_after(period) if period["splitFactor"] == 1 else []
        if forced:
            product = math.prod(ratio for _, ratio in forced)
            reference = _median(newer[-3:]) if newer else None
            already = (reference is not None and abs(shares / reference - 1) <= SPLIT_MATCH
                       and abs(math.log(shares / reference)) < abs(math.log(shares * product / reference)))
            if not already:
                period["splitFactor"], period["splitApplied"] = round(product, 6), tuple(day for day, _ in forced)
        newer.append(shares * period["splitFactor"])
        previous = period
    for period in periods:
        if implied_shares(period) is None and (forced := dated_after(period)):
            period["splitFactor"] = round(math.prod(ratio for _, ratio in forced), 6)
            period["splitApplied"] = tuple(day for day, _ in forced)
    for period in periods:
        if period["splitFactor"] != 1:
            for name in ("epsD", "epsB"):
                if period.get(name) is not None:
                    period[name] = round(period[name] / period["splitFactor"], 4) + 0.0


def tw_pair(splits, quarterly):
    """
    Taiwan financials files (after restate_splits): a pair with a split between the earlier period's end and the
    later period's report is compared only when it is on one basis: both implied share counts (net income ÷ EPS)
    known and not >= 1.5x apart, or, without them, the earlier period restated for every such split and the later
    one not possibly left on the old basis. Otherwise the EPS YoY is dropped (never guessed); revenue YoY is
    unaffected. Without a recorded split nothing is dropped: FinMind net income can include minority interests
    (2006.TW 2026Q3), so implied counts alone are not evidence.
    """
    def pair(current, base):
        revenue = growth_pct(current["rev"], base["rev"])
        due = current["day"] + timedelta(days=stock_dividends.delay_days(current["day"]) if quarterly
                                         else stock_dividends.DELAY_DAYS["yearEnd"])
        relevant = [day for day, _ in splits if base["day"] < day <= due]
        if relevant:
            now_shares, base_shares = implied_shares(current), implied_shares(base)
            if now_shares and base_shares:
                if share_count_jump({"shares": now_shares}, {"shares": base_shares}):
                    return None, False, revenue
            elif not all(day in base.get("splitApplied", ()) and (day <= current["day"] or day in current.get("splitApplied", ()))
                         for day in relevant):
                return None, False, revenue
        value, turn = eps_values(current, base)
        return value, turn, revenue
    return pair


def recompute_derived_q4(document, quarterly, annual, splits=None):
    """
    Taiwan derived fourth quarters (annual − Q1..Q3, quality issue "q4-derived") across a share-basis change, in place,
    after restate_for_stock_dividends and restate_splits (mirror of atlas/src/fundamentals/epsBasis.ts). The published
    Q4 is the annual less the Q3 report's year-to-date figure, so a 配股 / 減資彌補虧損 / split between the Q3 and the
    annual report mixes two bases: when Q3's factor differs from the annual's, Q4 = restated annual − Σ restated
    Q1..Q3 (a piece missing → the Q4 EPS is dropped). A split in that window the implied share counts could not
    confirm leaves the Q4 EPS dropped instead of compared. The builder leaves Q4 as published
    (build_financials_tw_finmind.py).
    """
    if document.get("market") != "TW" or "q4-derived" not in ((document.get("quality") or {}).get("issues") or []):
        return
    if splits is None:
        splits = tw_split_events(document, stock_dividends.events_of(document))
    years = {period["day"]: period for period in annual}

    def factor(period):
        return period.get("shareFactor", 1.0) * period.get("splitFactor", 1.0)

    for q4 in quarterly:
        if q4.get("fq") != 4:
            continue
        year = years.get(q4["day"])
        pieces = {period["fq"]: period for period in quarterly
                  if period.get("fy") == q4["fy"] and period.get("fq") in (1, 2, 3) and period["day"] < q4["day"]}
        q3 = pieces.get(3)
        if year is None or q3 is None:
            continue
        crossing = abs(factor(q3) / factor(year) - 1) > 1e-6 or abs(factor(q4) / factor(year) - 1) > 1e-6
        if not crossing:
            due_q3 = q3["day"] + timedelta(days=stock_dividends.delay_days(q3["day"]))
            due_year = year["day"] + timedelta(days=stock_dividends.DELAY_DAYS["yearEnd"])
            if any(due_q3 < day <= due_year for day, _ in splits):
                q4["epsD"] = q4["epsB"] = None          # an unconfirmed split in the window: no basis to trust
            continue
        for name in ("epsD", "epsB"):
            values = [year.get(name)] + [pieces.get(fq, {}).get(name) for fq in (1, 2, 3)]
            q4[name] = (round(values[0] - sum(values[1:]), 4) + 0.0
                        if q4.get(name) is not None and len(pieces) == 3 and None not in values else None)


def dividend_events(files):
    """{Taiwan key: stock dividend events} of the financials documents that carry the block (for the official
    Taiwan sources, whose per-period EPS are as reported)."""
    return {key: events for key, document in files
            if market_of(key) == "TW" and (events := stock_dividends.events_of(document)) is not None}


def financials_blocks(document, today):
    """→ (quality level, quarterly block, annual block) of one financials document, or None."""
    if not isinstance(document, dict):
        return None
    events = split_events(document)
    quarterly = read_periods(document.get("quarterly"), True, document.get("market"))
    annual = read_periods(document.get("annual"), False, document.get("market"))
    restate_for_stock_dividends(document, quarterly + annual)
    q_pair = y_pair = splits = None
    if document.get("market") == "TW":
        # FinMind EPS are never split-adjusted: restate on implied-share evidence, drop what stays ambiguous
        splits, dated = tw_split_plan(document, stock_dividends.events_of(document), quarterly)
        newest_year = max((period["day"] for period in annual), default=None)
        anchors = [dict(period) for period in quarterly if newest_year is not None and period["day"] > newest_year]
        restate_splits(quarterly, splits, (), dated)
        restate_splits(annual, splits, anchors, dated)
        q_pair, y_pair = tw_pair(splits, True), tw_pair(splits, False)
    recompute_derived_q4(document, quarterly, annual, splits)
    q = yoy_series(quarterly, True, QUARTER_SLOTS, events, today, STALE_QUARTER_DAYS, q_pair)
    y = yoy_series(annual, False, YEAR_SLOTS, events, today, STALE_ANNUAL_DAYS, y_pair)
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


def official_candidates(fundamentals_dir, universe, today, tw_keys=(), dividends=None):
    """{key: [(level, q, y)]} from fundamentals/tw.json and fundamentals/us-sec.json, plus a summary. dividends:
    {Taiwan key: stock dividend events} → the Taiwan EPS are restated before their YoY (restate_periods)."""
    extra, summary = {}, {}
    if not fundamentals_dir:
        return extra, summary
    import build_fundamentals_tw as tw  # noqa: PLC0415 - that module imports this one

    store = read_json(os.path.join(fundamentals_dir, TW_FILE))
    tw_rows = {}
    if store is not None:
        try:
            tw_rows = tw.growth_candidates(store, tw.symbol_map(universe, tw_keys), today, dividends)
        except tw.StoreError as error:
            print(f"::warning::{TW_FILE} ignored: {error}")
        summary["tw"] = len(tw_rows)
    tw_rows = finmind_candidates(fundamentals_dir, tw.symbol_map(universe, tw_keys), today, store, tw_rows, summary,
                                 dividends)
    for key, blocks in tw_rows.items():
        extra.setdefault(key, []).append(blocks)
    frames = sec_frame_candidates(read_json(os.path.join(fundamentals_dir, US_SEC_FILE)), today)
    summary["frames"] = len(frames)
    for key, blocks in frames.items():
        extra.setdefault(key, []).append(blocks)
    return extra, summary


def finmind_candidates(fundamentals_dir, symbols, today, official, tw_rows, summary, dividends=None):
    """tw.json candidates combined with fundamentals/tw-finmind.json (when present) → one per Taiwan symbol."""
    import build_fundamentals_tw_finmind as finmind  # noqa: PLC0415 - that module imports this one

    document = read_json(os.path.join(fundamentals_dir, finmind.FILE_NAME))
    if document is None:
        return tw_rows
    try:
        rows, summary["finmind"] = finmind.growth_candidates(document, symbols, today, official, dividends=dividends)
    except finmind.StoreError as error:
        print(f"::warning::{finmind.FILE_NAME} ignored: {error}")
        summary["finmind"] = {"rows": 0, "error": str(error)}
        return tw_rows
    return finmind.combine(tw_rows, rows)


GROWTH_RETENTION = .85
GROWTH_ANCHORS = ("2330.TW", "AAPL", "MSFT")


def usable_growth(document):
    """Count usable symbols and quarterly/annual values, not placeholder rows."""
    if not isinstance(document, dict) or not isinstance(document.get("fields"), list) or not isinstance(document.get("rows"), list):
        return {}
    fields = document["fields"]
    if "symbol" not in fields:
        return {}
    groups = {}
    keys = []
    for row in document["rows"]:
        if not isinstance(row, list) or len(row) <= fields.index("symbol"):
            continue
        item = dict(zip(fields, row))
        key = item["symbol"]
        keys.append(key)
        if not isinstance(key, str):
            continue
        for period in ("Q", "Y"):
            mask = item.get("t" + period)
            eps = item.get("eps" + period)
            rev = item.get("rev" + period)
            valid_eps = isinstance(eps, list) and any(finite(v) for v in eps)
            valid_eps = valid_eps or (isinstance(mask, int) and not isinstance(mask, bool) and mask > 0)
            valid_rev = isinstance(rev, list) and any(finite(v) for v in rev)
            if (valid_eps or valid_rev) and parse_day(item.get(period.lower() + "End")):
                market = market_of(key)
                groups.setdefault(market, set()).add(key)
                if valid_eps:
                    groups.setdefault(market + ":eps" + period, set()).add(key)
                if valid_rev:
                    groups.setdefault(market + ":rev" + period, set()).add(key)
    unique_keys(keys, "growth.json")
    return groups


def validate_output(document, previous):
    current = usable_growth(document)
    old = usable_growth(previous)
    require(any(current.get(m) for m in ("TW", "US")), "growth.json", "no usable growth rows")
    for market in ("TW", "US"):
        if market in old:
            retain_keys(current.get(market, set()), old[market], f"growth.json {market}",
                        min_share=GROWTH_RETENTION, anchors=GROWTH_ANCHORS)
    # Sparse metrics/young companies naturally change availability. Guard only
    # established cohorts; a row count alone misses a widespread null field.
    for cohort, keys in old.items():
        if ":" in cohort and len(keys) >= 100:
            retain_keys(current.get(cohort, set()), keys, f"growth.json {cohort}",
                        min_share=GROWTH_RETENTION)


def run(financials_dir, out_path, now=None, fundamentals_dir=None, universe_path=None):
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    files, bad = read_documents(financials_dir)
    universe = read_json(universe_path) if universe_path else None
    tw_keys = [key for key, _ in files if market_of(key) == "TW"]
    extra, official = official_candidates(fundamentals_dir, universe, now.date(), tw_keys, dividend_events(files))
    if not files and not extra:
        raise RuntimeError(f"no readable financials files in {financials_dir} and no official fundamentals")
    document = build_document(files, now, extra)
    previous = read_json(out_path)
    validate_output(document, previous)
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
        sys.exit(1)

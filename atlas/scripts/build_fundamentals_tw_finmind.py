"""
Builds docs/report/fundamentals/tw-finmind.json: about ten years of quarterly revenue, basic EPS and net
income attributable to the parent of every Taiwan listed / OTC common stock (universe.json), from FinMind
(TaiwanStockFinancialStatements, data republished from 公開資訊觀測站). build_growth.py turns it into the
quarterly / annual EPS and revenue YoY of growth.json (src "finmind").

  python atlas/scripts/build_fundamentals_tw_finmind.py --budget 3000      fetch (resumable) for ~50 minutes
  python atlas/scripts/build_fundamentals_tw_finmind.py --verify           offline checks of the stored file
  python atlas/scripts/build_fundamentals_tw_finmind.py --summary          offline counts of the stored file (no network)
  Options: --codes 2330,2881 (fetch these again, full history), --limit N (at most N requests),
           --only-when-complete (fetch only once the first backfill is done; for the daily workflow),
           --report-dir docs/report, --out <file>.
  Env: FINMIND_TOKEN (optional): FinMind API token, sent as "Authorization: Bearer"; raises the hourly limit.
  Standard library only.

Fetching. One request per stock: /api/v4/data?dataset=TaiwanStockFinancialStatements&data_id=<code>&
start_date=<date> answers a long table [{date, stock_id, type, value, origin_name}]. The free tier allows
about 300 requests an hour (600 with a token): requests are spaced 3600 / (limit x 0.95) s apart. An answer
saying the limit was reached (HTTP 402 / 429, or a msg about the limit) or refusing the token ends the run;
transport errors / HTTP 5xx are retried twice, then the stock is left for the next run (5 failures in a row
end the run). Progress is saved every SAVE_EVERY stocks and when the run ends (budget, limit, error, Ctrl-C),
so the same command can simply be run again until the summary says every stock is done.
Plan per run (each stock at most once):
  1. stocks never fetched: full history from START_DATE;
  2. stocks whose newest stored quarter is older than the quarter whose filing deadline has passed
     (DUE_DATES): from their newest stored quarter on (that quarter is fetched again, every older stored
     quarter is never fetched again), once after the deadline, then every RECHECK_DAYS (two or more quarters
     behind, e.g. suspended: every SLOW_RECHECK_DAYS);
  3. during a filing window (quarter ended >= OPEN_AFTER_DAYS ago, deadline not yet passed): stocks without
     the new quarter, every RECHECK_DAYS;
  4. stocks FinMind had nothing for: every SLOW_RECHECK_DAYS.
Inside each group the stocks checked longest ago come first.

Rows → one entry per quarter (the date is the quarter end): eps = 基本每股盈餘 (type EPS); ni = 淨利（淨損）
歸屬於母公司業主 (type EquityAttributableToOwnersOfParent), else 本期淨利（淨損） (IncomeAfterTaxes); never a
綜合損益 line. Revenue by statement format, chosen by origin_name first (type names as fallback), the kind
is stored like tw.json (build_fundamentals_tw.REVENUE_KINDS): 營業收入 (一般業, 保險業) > 淨收益 (金控業,
銀行業) > 收益 (證券業) > 收入 > 利息淨收益 + 利息以外淨損益. One kind per stock where it has it (the kind
most of its quarters have); YoY pairs and derived quarters never combine two kinds.

Single quarter or year-to-date: FinMind does not say, so it is decided from the stored numbers, separately
for rev / eps / ni (detect_modes), over every company-year with four positive values:
  share of years with Q1 < Q2 < Q3 and Q3 >= 2 x Q1 (year-to-date-like Q1-Q3) >= 80%  → "ytd"
    (and Q4 > Q3 in >= 80%, else "ambiguous");
  that share <= 20% and Q4 >= Q1 + Q2 + Q3 in <= 20%  → "quarter" (Q4 = full year - Q1..Q3, done by FinMind);
  that share <= 20% and Q4 >= Q1 + Q2 + Q3 in >= 80%  → "q4annual" (Q1-Q3 single quarters, Q4 = full year);
  fewer than MIN_EVIDENCE years → "unknown" (eps / ni then follow rev), otherwise "ambiguous" (not used).
Revenue that is not decisive means FinMind is not used at all. The verdict and its evidence are logged by
every run, --verify and build_growth.py. Stocks whose own revenue years contradict the verdict are left out.
Quarters / years from the verdict: year-to-date → Qn = YTD(n) - YTD(n-1) (EPS on the share basis of the
later report: (NI(n) - NI(n-1)) x EPS(n) / NI(n), as build_fundamentals_tw), year = Q4; single quarters →
year = sum of the four quarters (EPS too: FinMind's Q4 is full year - Q1..Q3). Every Q4 is flagged derived
(dQ). A quarter's revenue must be >= 0. The YoY rules are build_growth's (tw_pair: one revenue kind, share
basis from NI / EPS, scale errors). The annual series is as long as the data (not YEAR_SLOTS).
Cross-check (growth_candidates, --verify): the year-to-date revenue of the official tw.json seasons
(MOPS / OpenAPI, NT$ thousand) against the FinMind figures read with the verdict: the median ratio must be
within 2% of 1 or 1000 (FinMind may publish NT$), else build_growth does not use FinMind.

Store (compact): {"version": 1, "source", "dataset", "units", "fields": ["rev", "eps", "ni"], "kinds",
  "start", "seasons": [...ascending], "types": {type: origin_name}, "fetch": {code: [day, newest season, n]},
  "data": {code: [entry | null per season]}}; entry = [rev, eps, ni] or [rev, eps, ni, kind] (values as
  published by FinMind). Rewritten only when it changed; never when the stored file cannot be read.
Exit codes: 0 done (also: stopped by budget / rate limit with progress saved), 1 nothing could be done
(unreadable store, no universe, refused token at any point, every request failed), --verify failed, or
--summary without a stored file.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone

import build_fundamentals_tw as tw
import build_growth as growth
from build_valuation_us import read_json, write_atomic
from finmind_client import (  # noqa: F401 - re-exported: the client is shared with build_financials_tw_finmind
    API_URL, AUTH_MARKERS, HOURLY_LIMIT, LIMIT_MARKERS, RATE_SAFETY, RETRIES, RETRY_WAIT, USER_AGENT,
    AuthError, FinMindError, Http, LimitReached, RateLimited, fetch_rows, limit_used, parse_response,
    request_interval, request_url as client_request_url,
)

VERSION = 1
FILE_NAME = "tw-finmind.json"
SRC = "finmind"
DATASET = "TaiwanStockFinancialStatements"
START_DATE = "2015-01-01"
SOURCE = "FinMind TaiwanStockFinancialStatements（資料源自公開資訊觀測站）"
UNITS = "as published by FinMind (rev / ni: NT$ or NT$ thousand, checked by --verify; eps: NT$)"
FIELDS = ["rev", "eps", "ni"]
FIELD_AT = {"rev": 0, "eps": 1, "ni": 2}
DEFAULT_BUDGET_SECONDS = 600
SAVE_EVERY = 25
MAX_CONSECUTIVE_ERRORS = 5
RECHECK_DAYS = 7
SLOW_RECHECK_DAYS = 30
OPEN_AFTER_DAYS = 30
# Filing deadline of a quarter (banks / financial holdings included) + about a week for FinMind (Q4: next year)
DUE_DATES = {1: (5, 22), 2: (9, 7), 3: (11, 21), 4: (4, 7)}
MIN_EVIDENCE = 20
# The newest quarters a Taiwan quarterly block is judged on when MOPS and FinMind both have the period (see pick_block):
# the four the screener always showed, so the longer history never costs one of them.
RECENT_QUARTERS = 4
DECISIVE = 0.8
MODES = ("ytd", "quarter", "q4annual")
CROSS_CHECK_MIN = 20
CROSS_CHECK_TOLERANCE = 0.02
COMPLETE_SHARE = 0.95
SPOT_CODES = ("2330", "2317", "1101", "2881", "2882", "2891", "2801", "2834", "2823", "2867", "6005", "2855")


EPS_TYPES = ("EPS", "BasicEPS", "BasicEarningsPerShare", "BasicEarningsLossPerShare")
PARENT_NI_TYPES = ("EquityAttributableToOwnersOfParent", "NetIncomeAttributableToOwnersOfParent",
                   "ProfitLossAttributableToOwnersOfParent", "NetIncomeLossAttributableToOwnersOfParent")
TOTAL_NI_TYPES = ("IncomeAfterTaxes", "NetIncome", "NetIncomeLoss", "ProfitLoss")
TOTAL_NI_NAMES = ("本期淨利(淨損)", "本期稅後淨利(淨損)", "本期淨利(損)", "本期稅後淨利(損)", "本期淨利", "本期稅後淨利",
                  "本期淨損益", "本期損益")
REVENUE_TYPES = ("Revenue", "Revenues", "OperatingRevenue", "OperatingRevenues", "TotalRevenue", "TotalOperatingRevenue")
REVENUE_NAMES = {"淨收益": 1, "淨收益合計": 1, "收益": 2, "收益合計": 2, "收入": 3, "收入合計": 3}
INTEREST_NAMES = ("利息淨收益", "利息以外淨損益")
INTEREST_TYPES = (("NetInterestIncome", "InterestNetIncome", "NetIncomeOfInterest"),
                  ("NetNonInterestIncome", "NonInterestNetIncome", "NetIncomeOtherThanInterest",
                   "NetRevenueOtherThanInterest", "NonInterestIncomeNet"))
INTEREST_KIND = tw.REVENUE_KINDS.index("利息淨收益+利息以外淨損益")


class StoreError(ValueError):
    """tw-finmind.json does not have the expected layout."""


# ---------------------------------------------------------------------------------------------
# Seasons
# ---------------------------------------------------------------------------------------------
def season_number(key):
    parsed = tw.parse_season(key)
    return tw.season_number(*parsed) if parsed else None


def season_of(text):
    """'2024-03-31' (a quarter end; any day of the quarter's last month) → '2024Q1'; None otherwise."""
    try:
        day = datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    if day.month % 3:
        return None
    return tw.season_key(day.year, day.month // 3)


def due_date(key):
    year, quarter = tw.parse_season(key)
    month, day = DUE_DATES[quarter]
    return date(year + 1 if quarter == 4 else year, month, day)


def due_season(today):
    """Newest quarter whose filing deadline (+ margin) has passed: every company should have it."""
    number = tw.season_number(*tw.last_ended_season(today))
    while today < due_date(tw.season_key(*tw.from_number(number))):
        number -= 1
    return tw.season_key(*tw.from_number(number))


def open_season(today):
    """Newest quarter that ended at least OPEN_AFTER_DAYS ago (early filers may have it)."""
    number = tw.season_number(*tw.last_ended_season(today))
    while (today - tw.quarter_end(*tw.from_number(number))).days < OPEN_AFTER_DAYS:
        number -= 1
    return tw.season_key(*tw.from_number(number))


# ---------------------------------------------------------------------------------------------
# FinMind answers
# ---------------------------------------------------------------------------------------------
def roles_of(type_name, origin_name):
    """One row → [(role, rank, kind)], role in eps / ni / rev / interest / noninterest; lower rank = better."""
    name, kind_type = tw.normalise(origin_name), str(type_name or "")
    roles = []
    if name.startswith("基本每股盈餘"):
        roles.append(("eps", 0, 0))
    elif kind_type in EPS_TYPES and "稀釋" not in name:
        roles.append(("eps", 1, 0))
    if "綜合" not in name and "非控制" not in name:
        if "歸屬於母公司業主" in name and any(word in name for word in ("淨利", "淨損", "損益")):
            roles.append(("ni", 0, 0))
        elif kind_type in PARENT_NI_TYPES:
            roles.append(("ni", 1, 0))
        elif name in TOTAL_NI_NAMES:
            roles.append(("ni", 2, 0))
        elif kind_type in TOTAL_NI_TYPES and "繼續" not in name and "停業" not in name:
            roles.append(("ni", 3, 0))
    if name.startswith("營業收入") and "成本" not in name:
        roles.append(("rev", 0, 0))
    elif name in REVENUE_NAMES:
        roles.append(("rev", 0, REVENUE_NAMES[name]))
    elif name == INTEREST_NAMES[0] or (not name and kind_type in INTEREST_TYPES[0]):
        roles.append(("interest", 0, INTEREST_KIND))
    elif name == INTEREST_NAMES[1] or (not name and kind_type in INTEREST_TYPES[1]):
        roles.append(("noninterest", 0, INTEREST_KIND))
    elif kind_type in REVENUE_TYPES and not name.startswith(("營業外", "其他")):
        roles.append(("rev", 1, 0))
    return roles


def parse_rows(rows, code):
    """
    FinMind rows of one stock → {"entries": {season: [rev, eps, ni, kind]}, "types": Counter(type),
    "names": {type: origin_name}, "skipped": Counter(reason), "conflicts": n}. A season without revenue and
    EPS has no entry.
    """
    seasons, types, names, skipped, conflicts = {}, Counter(), {}, Counter(), 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            skipped["not an object"] += 1
            continue
        stock = str(row.get("stock_id") or "").strip().upper()
        if stock and stock != code:
            skipped["other stock_id"] += 1
            continue
        key = season_of(row.get("date"))
        type_name = str(row.get("type") or "")
        types[type_name] += 1
        names.setdefault(type_name, str(row.get("origin_name") or ""))
        if key is None:
            skipped["date not a quarter end"] += 1
            continue
        value = tw.number(row.get("value"))
        if value is None:
            skipped["no value"] += 1
            continue
        slot = seasons.setdefault(key, {})
        for role, rank, kind in roles_of(type_name, row.get("origin_name")):
            role_key = (role, kind)
            known = slot.get(role_key)
            if known is None or rank < known[0]:
                slot[role_key] = (rank, value)
            elif rank == known[0] and value != known[1]:
                conflicts += 1                       # the first one is kept
    revenue_by_season = {}
    for key, slot in seasons.items():
        options = {kind: value for (role, kind), (_, value) in slot.items() if role == "rev"}
        if ("interest", INTEREST_KIND) in slot and ("noninterest", INTEREST_KIND) in slot:
            options.setdefault(INTEREST_KIND, slot[("interest", INTEREST_KIND)][1] + slot[("noninterest", INTEREST_KIND)][1])
        revenue_by_season[key] = options
    # One kind per stock where it has it: the kind most seasons have (ties: the better kind)
    counts = Counter(kind for options in revenue_by_season.values() for kind in options)
    dominant = min(counts, key=lambda kind: (-counts[kind], kind)) if counts else None
    entries = {}
    for key, slot in seasons.items():
        options = revenue_by_season[key]
        kind = dominant if dominant in options else (min(options) if options else 0)
        rev = options.get(kind)
        eps = slot.get(("eps", 0), (None, None))[1]
        ni = slot.get(("ni", 0), (None, None))[1]
        if rev is None and eps is None:
            continue
        entries[key] = [tw.compact(rev), tw.compact(eps), tw.compact(ni), kind if rev is not None else 0]
    return {"entries": entries, "types": types, "names": names, "skipped": skipped, "conflicts": conflicts}


# ---------------------------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------------------------
def empty_store():
    return {"fetch": {}, "data": {}, "types": {}}


def load_store(document):
    """tw-finmind.json → {"fetch": {code: [day, last, n]}, "data": {code: {season: [rev, eps, ni, kind]}}, "types"}."""
    if document is None:
        return empty_store()
    if not isinstance(document, dict) or document.get("version") != VERSION:
        raise StoreError("not a version-1 tw-finmind.json")
    seasons, data, fetch = document.get("seasons"), document.get("data"), document.get("fetch")
    if not isinstance(seasons, list) or not all(tw.parse_season(key) for key in seasons):
        raise StoreError("bad 'seasons' list")
    if not isinstance(data, dict) or not isinstance(fetch, dict):
        raise StoreError("bad 'data' / 'fetch'")
    loaded = {}
    for code, values in data.items():
        if not isinstance(values, list) or len(values) > len(seasons):
            raise StoreError(f"bad row for {code!r}")
        row = {}
        for key, entry in zip(seasons, values):
            if entry is None:
                continue
            if not isinstance(entry, list) or len(entry) not in (3, 4):
                raise StoreError(f"bad entry {entry!r} for {code} {key}")
            rev, eps, ni = (growth.finite_number(value) for value in entry[:3])
            kind = entry[3] if len(entry) == 4 and isinstance(entry[3], int) and 0 <= entry[3] < len(tw.REVENUE_KINDS) else 0
            row[key] = [rev, eps, ni, kind]
        if row:
            loaded[code] = row
    checked = {}
    for code, info in fetch.items():
        if isinstance(info, list) and info and isinstance(info[0], str) and growth.parse_day(info[0]):
            checked[code] = [info[0], info[1] if len(info) > 1 and tw.parse_season(info[1]) else None,
                             info[2] if len(info) > 2 and isinstance(info[2], int) else 0]
    types = document.get("types") if isinstance(document.get("types"), dict) else {}
    return {"fetch": checked, "data": loaded, "types": {str(k): str(v) for k, v in types.items()}}


def dump_store(store):
    """In-memory store → tw-finmind.json document (seasons ascending, rows aligned, trailing nulls cut)."""
    present = {key for row in store["data"].values() for key in row}
    seasons = sorted(present, key=season_number)
    data = {}
    for code in sorted(store["data"]):
        row = store["data"][code]
        values = []
        for key in seasons:
            entry = row.get(key)
            if entry is None:
                values.append(None)
            else:
                rev, eps, ni, kind = entry
                values.append([tw.compact(rev), tw.compact(eps), tw.compact(ni)] + ([kind] if kind else []))
        while values and values[-1] is None:
            values.pop()
        if values:
            data[code] = values
    return {
        "version": VERSION, "source": SOURCE, "dataset": DATASET, "units": UNITS, "fields": FIELDS,
        "kinds": list(tw.REVENUE_KINDS), "start": START_DATE, "seasons": seasons,
        "types": {key: store["types"][key] for key in sorted(store["types"])},
        "fetch": {code: store["fetch"][code] for code in sorted(store["fetch"])},
        "data": data,
    }


def newest_season(row):
    return max(row, key=season_number) if row else None


def merge_stock(store, code, entries, today):
    """One answer into the store: the seasons it has replace the stored ones (the others stay)."""
    row = store["data"].setdefault(code, {})
    for key, entry in entries.items():
        row[key] = entry
    if not row:
        store["data"].pop(code, None)
    store["fetch"][code] = [today.isoformat(), newest_season(row), len(entries)]


def plan_fetches(store, codes, today, force=()):
    """[(code, start_date, reason)] for this run, most needed first (see the module notes)."""
    target, window = due_season(today), open_season(today)
    missing, behind, waiting, empty = [], [], [], []
    for code in codes:
        info = store["fetch"].get(code)
        if code in force or info is None:
            missing.append((code, START_DATE, "new" if info is None else "forced"))
            continue
        checked = growth.parse_day(info[0]) or date(1970, 1, 1)
        age = (today - checked).days
        last = newest_season(store["data"].get(code, {}))
        if last is None:
            if age >= SLOW_RECHECK_DAYS:
                empty.append((info[0], code, START_DATE, "empty"))
            continue
        start = tw.quarter_end(*tw.parse_season(last)).isoformat()
        gap = season_number(target) - season_number(last)
        if gap > 0:
            if checked < due_date(target) or age >= (RECHECK_DAYS if gap == 1 else SLOW_RECHECK_DAYS):
                behind.append((info[0], code, start, f"behind {target}"))
        elif season_number(window) > season_number(last) and age >= RECHECK_DAYS:
            waiting.append((info[0], code, start, f"filing window {window}"))
    ordered = [tuple(entry[1:]) for group in (behind, waiting, empty) for entry in sorted(group)]
    return missing + ordered


# ---------------------------------------------------------------------------------------------
# Single quarter or year-to-date (decided from the data)
# ---------------------------------------------------------------------------------------------
def year_quads(data, field):
    """[(code, year, (v1, v2, v3, v4))] of the company-years with four values > 0 (revenue: one kind)."""
    at = FIELD_AT[field]
    result = []
    for code, row in data.items():
        years = {}
        for key, entry in row.items():
            year, quarter = tw.parse_season(key)
            years.setdefault(year, {})[quarter] = entry
        for year, quarters in years.items():
            if len(quarters) < 4:
                continue
            entries = [quarters[q] for q in (1, 2, 3, 4)]
            values = [entry[at] for entry in entries]
            if any(value is None or value <= 0 for value in values):
                continue
            if field == "rev" and len({entry[3] for entry in entries}) > 1:
                continue
            result.append((code, year, tuple(values)))
    return result


def median(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def ytd_like(quad):
    q1, q2, q3, _ = quad
    return q1 < q2 < q3 and q3 >= 2 * q1


def evidence(quads):
    """Shares of company-years that look year-to-date etc. (see the module notes) + an example."""
    n = len(quads)
    share = (lambda count: round(count / n, 3)) if n else (lambda count: None)
    values = [quad for _, _, quad in quads]
    example = max(quads, key=lambda item: (item[1], item[0] == "2330", item[0]))[:3] if quads else None
    return {
        "n": n,
        "ytdQ1Q3": share(sum(1 for quad in values if ytd_like(quad))),
        "q4AboveQ3": share(sum(1 for quad in values if quad[3] > quad[2])),
        "q4AboveQ1toQ3": share(sum(1 for quad in values if quad[3] >= quad[0] + quad[1] + quad[2])),
        "medianQ4overQ1": round(median([quad[3] / quad[0] for quad in values]), 3) if n else None,
        "example": [example[0], example[1], list(example[2])] if example else None,
    }


def verdict_of(found, min_evidence=MIN_EVIDENCE):
    if found["n"] < min_evidence:
        return "unknown"
    if found["ytdQ1Q3"] >= DECISIVE:
        return "ytd" if found["q4AboveQ3"] >= DECISIVE else "ambiguous"
    if found["ytdQ1Q3"] <= 1 - DECISIVE:
        if found["q4AboveQ1toQ3"] >= DECISIVE:
            return "q4annual"
        if found["q4AboveQ1toQ3"] <= 1 - DECISIVE:
            return "quarter"
    return "ambiguous"


def detect_modes(data, min_evidence=MIN_EVIDENCE):
    """→ ({field: mode or None}, {field: evidence + verdict}); eps / ni without evidence follow revenue."""
    report = {}
    for field in FIELDS:
        found = evidence(year_quads(data, field))
        found["verdict"] = verdict_of(found, min_evidence)
        report[field] = found
    revenue = report["rev"]["verdict"]
    modes = {}
    for field in FIELDS:
        verdict = report[field]["verdict"]
        if field != "rev" and verdict == "unknown" and revenue in MODES:
            verdict = revenue
            report[field]["assumed"] = revenue
        modes[field] = verdict if verdict in MODES and revenue in MODES else None
    return modes, report


def contradicting(data, mode, min_years=3):
    """Codes whose own revenue years clearly contradict the dataset's verdict (they are left out)."""
    by_code = {}
    for code, _, quad in year_quads(data, "rev"):
        by_code.setdefault(code, []).append(ytd_like(quad))
    bad = set()
    for code, flags in by_code.items():
        if len(flags) < min_years:
            continue
        share = sum(flags) / len(flags)
        if (mode == "ytd" and share <= 1 - DECISIVE) or (mode in ("quarter", "q4annual") and share >= DECISIVE):
            bad.add(code)
    return bad


def describe_modes(report):
    """Log lines with the verdict and its evidence per field."""
    lines = []
    for field in FIELDS:
        found = report[field]
        assumed = f" (no own evidence: follows revenue → {found['assumed']})" if found.get("assumed") else ""
        lines.append(
            f"FinMind {field}: verdict {found['verdict']}{assumed} over {found['n']} company-years with four positive "
            f"quarters: Q1<Q2<Q3 & Q3>=2xQ1 in {found['ytdQ1Q3']}, Q4>Q3 in {found['q4AboveQ3']}, "
            f"Q4>=Q1+Q2+Q3 in {found['q4AboveQ1toQ3']}, median Q4/Q1 {found['medianQ4overQ1']}; "
            f"example {found['example']}")
    return lines


# ---------------------------------------------------------------------------------------------
# Quarters and years
# ---------------------------------------------------------------------------------------------
def by_year(row):
    years = {}
    for key, entry in row.items():
        parsed = tw.parse_season(key)
        if parsed:
            years.setdefault(parsed[0], {})[parsed[1]] = entry
    return years


def can_be_single_quarter(quarter, quarters_of_year):
    """A published value is a single quarter only when Q1 or its same-year predecessor is present."""
    return quarter == 1 or quarter - 1 in quarters_of_year


def field_views(quarters_of_year, at, mode):
    """One field of one year → ({q: single-quarter value}, {q: year-to-date value}), None where not derivable."""
    raw = {q: entry[at] for q, entry in quarters_of_year.items() if entry[at] is not None}
    kinds = {q: entry[3] for q, entry in quarters_of_year.items()}
    single, ytd = {}, {}

    def same_kind(qs):
        return at != 0 or len({kinds.get(q) for q in qs}) == 1

    def cumulate(upto):
        qs = range(1, upto + 1)
        if all(q in single for q in qs) and same_kind(qs):
            return sum(single[q] for q in qs)
        return None

    if mode == "ytd":
        ytd = dict(raw)
        for q in raw:
            if q == 1:
                single[q] = raw[q]
            elif q - 1 in raw and same_kind((q - 1, q)):
                single[q] = raw[q] - raw[q - 1]
    elif mode in ("quarter", "q4annual"):
        for q in raw:
            # A quarter after a gap may be cumulative (half-year / nine-month before listing).
            if (q < 4 or mode == "quarter") and can_be_single_quarter(q, quarters_of_year):
                single[q] = raw[q]
        for q in (1, 2, 3):
            if q in raw:
                ytd[q] = cumulate(q)
        if mode == "quarter":
            ytd[4] = cumulate(4) if 4 in raw else None
        elif 4 in raw:
            ytd[4] = raw[4]
            before = cumulate(3)
            single[4] = raw[4] - before if before is not None and same_kind((1, 2, 3, 4)) else None
    if at == 0:
        single = {q: value if value is None or value >= 0 or kinds.get(q) in (1, 2, INTEREST_KIND) else None
                  for q, value in single.items()}
    return single, {q: value for q, value in ytd.items() if value is not None}


SHARE_STABLE_MAX = 0.02  # Q1..Q3 implied share counts within 2% of each other: their EPS sum is the full-year EPS


def official_annual_eps(official):
    """tw.json → {code: {year: full-year EPS}} (MOPS, the Q4 season's year-to-date EPS); {} when missing or malformed."""
    if not isinstance(official, dict):
        return {}
    try:
        data = tw.load_store(official)["data"]
    except tw.StoreError:
        return {}
    result = {}
    for code, seasons in data.items():
        for key, values in seasons.items():
            season = tw.parse_season(key)
            if season and season[1] == 4 and values[1] is not None:
                result.setdefault(code, {})[season[0]] = values[1]
    return result


def stable_share_count(eps_q, ni_q):
    """Q1..Q3 each have an implied share count and the three agree within SHARE_STABLE_MAX."""
    shares = [tw.implied_shares([None, eps_q.get(q), ni_q.get(q)]) for q in (1, 2, 3)]
    if any(share is None for share in shares):
        return False
    return max(shares) / min(shares) - 1 < SHARE_STABLE_MAX


def company_periods(row, modes, official_annual=None):
    """{season: [rev, eps, ni, kind]} as published → (quarterly periods, annual periods) for yoy_series.

    official_annual: {year: full-year EPS} from tw.json. Under the quarter style FinMind's annual EPS is a sum of
    four quarters, which is wrong when the share count moved in the year: the official figure wins, and the sum is
    kept only on a stable share count (stable_share_count); otherwise the annual EPS is None.
    """
    official_annual = official_annual or {}
    quarters, annual = [], []
    for year, entries in by_year(row).items():
        rev_q, rev_y = field_views(entries, 0, modes["rev"])
        eps_q, eps_y = field_views(entries, 1, modes["eps"])
        ni_q, ni_y = field_views(entries, 2, modes["ni"])
        for quarter in sorted(entries):
            eps = eps_q.get(quarter)
            derived_eps = modes["eps"] == "ytd" or (modes["eps"] == "q4annual" and quarter == 4)
            if derived_eps and quarter > 1:
                # Share basis of the later report (as build_fundamentals_tw.single_quarter)
                now_eps, now_ni = eps_y.get(quarter), ni_y.get(quarter)
                before_ni = ni_y.get(quarter - 1)
                if now_eps is not None and tw.implied_shares([None, now_eps, now_ni]) is not None and before_ni is not None:
                    eps = round((now_ni - before_ni) * now_eps / now_ni, 4)
            if eps is not None:
                eps = round(eps, 4)
            if modes["eps"] == "quarter" or (modes["eps"] == "q4annual" and quarter < 4):
                shares = tw.implied_shares([None, eps_q.get(quarter), ni_q.get(quarter)])
            else:
                shares = tw.implied_shares([None, eps_y.get(quarter), ni_y.get(quarter)])
            end = tw.quarter_end(year, quarter)
            quarters.append({
                "end": end.isoformat(), "day": end, "fy": year, "fq": quarter, "epsD": None, "epsB": eps,
                "rev": rev_q.get(quarter), "shares": shares, "src": SRC, "derived": quarter == 4,
                "kind": entries[quarter][3],
            })
        if 4 in entries and (eps_y.get(4) is not None or rev_y.get(4) is not None):
            end = tw.quarter_end(year, 4)
            kinds = {entries[q][3] for q in entries} if modes["rev"] == "quarter" else {entries[4][3]}
            eps_year = round(eps_y[4], 4) if eps_y.get(4) is not None else None
            shares = tw.implied_shares([None, eps_y.get(4), ni_y.get(4)])
            if modes["eps"] == "quarter":
                official = official_annual.get(year)
                if official is not None:
                    # FinMind net income over the official EPS: the share basis of the full year, in NT$ shares
                    eps_year, shares = round(official, 4), tw.implied_shares([None, official, ni_y.get(4)])
                elif not stable_share_count(eps_q, ni_q):
                    eps_year = None
            annual.append({
                "end": end.isoformat(), "day": end, "fy": year, "fq": None, "epsD": None, "epsB": eps_year,
                "rev": rev_y.get(4), "shares": shares, "src": SRC,
                "derived": False, "kind": entries[4][3] if len(kinds) == 1 else -1,
            })
    return quarters, annual


def annual_slots(annual):
    """As many annual YoY slots as the stored years allow (never fewer than build_growth's YEAR_SLOTS)."""
    years = [period["fy"] for period in annual if growth.usable(period)]
    return max(growth.YEAR_SLOTS, (max(years) - min(years)) if years else 0)


def ytd_revenue(row, modes, key):
    """Year-to-date revenue and EPS of one season read with the verdict → (rev, eps, kind) or None."""
    year, quarter = tw.parse_season(key)
    entries = by_year(row).get(year, {})
    if quarter not in entries:
        return None
    _, rev_y = field_views(entries, 0, modes["rev"])
    _, eps_y = field_views(entries, 1, modes["eps"])
    return rev_y.get(quarter), eps_y.get(quarter), entries[quarter][3]


def cross_check(data, modes, official_document):
    """
    FinMind year-to-date revenue (read with the verdict) / the official tw.json one, general-format companies
    (revenue kind 營業收入 on both sides) → {"count", "median", "scale", "ok", "within2pct", "eps"}.
    """
    result = {"count": 0, "median": None, "scale": None, "ok": None, "within2pct": None, "epsWithin2c": None,
              "seasons": []}
    if not isinstance(official_document, dict) or modes.get("rev") is None:
        return result
    try:
        official = tw.load_store(official_document)["data"]
    except tw.StoreError:
        return result
    ratios, eps_close, eps_total, seasons = [], 0, 0, set()
    for code, row in official.items():
        mine = data.get(code)
        if not mine:
            continue
        for key, (rev, eps, _, flags) in row.items():
            if tw.kind_of(flags) != 0 or not rev or rev <= 0:
                continue
            found = ytd_revenue(mine, modes, key)
            if found is None or found[2] != 0 or not found[0]:
                continue
            ratios.append(found[0] / rev)
            seasons.add(key)
            if eps is not None and found[1] is not None:
                eps_total += 1
                eps_close += abs(found[1] - eps) <= 0.021      # two rounded quarters may add up 1-2 cents apart
    if not ratios:
        return result
    middle = median(ratios)
    scale = min((1.0, 1000.0, 0.001), key=lambda factor: abs(middle / factor - 1))
    within = sum(1 for ratio in ratios if abs(ratio / scale - 1) <= CROSS_CHECK_TOLERANCE)
    result.update({
        "count": len(ratios), "median": round(middle, 6), "scale": scale,
        "ok": abs(middle / scale - 1) <= CROSS_CHECK_TOLERANCE, "within2pct": round(within / len(ratios), 3),
        "epsWithin2c": round(eps_close / eps_total, 3) if eps_total else None, "seasons": sorted(seasons),
    })
    return result


def growth_candidates(document, symbols, today, official=None, log=print):
    """
    tw-finmind.json + {code: symbol} → ({symbol: (None, quarterly block, annual block)}, info). Nothing when
    the revenue verdict is not decisive or the cross-check with the official file fails.
    """
    store = load_store(document)
    modes, report = detect_modes(store["data"])
    for line in describe_modes(report):
        log(line)
    info = {"modes": modes, "rows": 0}
    if modes["rev"] is None:
        log("::warning::FinMind not used: single quarter / year-to-date could not be decided from the data")
        return {}, info
    check = cross_check(store["data"], modes, official)
    info["check"] = {key: check[key] for key in ("count", "median", "scale", "within2pct", "epsWithin2c")}
    if check["count"] >= CROSS_CHECK_MIN and not check["ok"]:
        log(f"::warning::FinMind not used: its year-to-date revenue differs from the official tw.json one "
            f"(median ratio {check['median']} over {check['count']} companies)")
        return {}, info
    bad = contradicting(store["data"], modes["rev"])
    if bad:
        log(f"::warning::FinMind: {len(bad)} stocks contradict the {modes['rev']} verdict and are left out: {sorted(bad)[:12]}")
    info["excluded"] = len(bad)
    result = {}
    official_annual = official_annual_eps(official)
    for code, row in store["data"].items():
        symbol = symbols.get(code)
        if symbol is None or code in bad:
            continue
        quarters, annual = company_periods(row, modes, official_annual.get(code))
        q = growth.yoy_series(quarters, True, growth.QUARTER_SLOTS, [], today, growth.STALE_QUARTER_DAYS, tw.tw_pair)
        y = growth.yoy_series(annual, False, annual_slots(annual), [], today, growth.STALE_ANNUAL_DAYS, tw.tw_pair)
        if q is not None or y is not None:
            result[symbol] = (None, q, y)
    info["rows"] = len(result)
    return result, info


def filled(block):
    """Slots of a block with a YoY value or a turnaround."""
    eps, rev, mask = block.get("eps") or [], block.get("rev") or [], block.get("mask") or 0
    return sum(1 for slot in range(max(len(eps), len(rev), mask.bit_length()))
               if (slot < len(eps) and eps[slot] is not None) or (slot < len(rev) and rev[slot] is not None)
               or (mask >> slot) & 1)


def leading_run(values, mask, slots):
    """Consecutive slots from the newest (of the first `slots`) with a YoY value or a turnaround: the run a gate can use."""
    count = 0
    for slot in range(slots):
        if (slot < len(values) and values[slot] is not None) or (mask >> slot) & 1:
            count += 1
        else:
            break
    return count


def recent_key(block):
    """Quarterly order of two blocks for the same period: the longer EPS run in the newest RECENT_QUARTERS, then revenue."""
    eps, rev, mask = block.get("eps") or [], block.get("rev") or [], block.get("mask") or 0
    return leading_run(eps, mask, RECENT_QUARTERS), leading_run(rev, 0, RECENT_QUARTERS)


def pick_block(official, finmind, quarterly=False):
    """The newer period wins; for the same period the official (MOPS) block unless FinMind is better. Quarterly blocks:
    better means the longer run of EPS YoY in the newest RECENT_QUARTERS (the four quarters the screener always showed,
    so a longer FinMind history never empties one of them), then revenue; the official block wins a tie, and only then
    the block with more slots overall decides (the longer history, for the quarters beyond those four)."""
    if official is None or finmind is None:
        return official if finmind is None else finmind
    official_end, finmind_end = growth.parse_day(official["end"]), growth.parse_day(finmind["end"])
    if official_end != finmind_end:
        return official if official_end > finmind_end else finmind
    if quarterly and recent_key(official) != recent_key(finmind):
        return official if recent_key(official) > recent_key(finmind) else finmind
    return official if filled(official) >= filled(finmind) else finmind


def combine(official_rows, finmind_rows):
    """{symbol: (level, q, y)} of tw.json and of FinMind → one Taiwan official candidate per symbol."""
    result = {}
    for symbol in set(official_rows) | set(finmind_rows):
        mine, theirs = official_rows.get(symbol, (None, None, None)), finmind_rows.get(symbol, (None, None, None))
        result[symbol] = (None, pick_block(mine[1], theirs[1], quarterly=True), pick_block(mine[2], theirs[2]))
    return result


# =============================================================================================
# I/O
# =============================================================================================
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def request_url(code, start_date):
    return client_request_url(DATASET, code, start_date)


def fetch_stock(http, code, start_date, token, sleep, log, counter):
    """One stock → FinMind rows of the statements dataset (see finmind_client.fetch_rows)."""
    return fetch_rows(http, DATASET, code, start_date, token, sleep, log, counter)


def universe_codes(universe):
    """{code: industry or None} of the Taiwan rows of universe.json (.TW / .TWO)."""
    from build_financials import universe_rows  # noqa: PLC0415 - keep this module's import light

    industries = {}
    if isinstance(universe, dict) and isinstance(universe.get("fields"), list) and "industry" in universe["fields"]:
        fields = universe["fields"]
        symbol_at, industry_at = (fields.index("symbol") if "symbol" in fields else 0), fields.index("industry")
        rows = ((universe.get("markets") or {}).get("TW") or {}).get("rows")
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, list) and len(row) > max(symbol_at, industry_at) and isinstance(row[symbol_at], str):
                industries[row[symbol_at].upper()] = row[industry_at]
    result = {}
    for symbol, _ in universe_rows(universe, "TW"):
        if isinstance(symbol, str) and symbol.upper().endswith(growth.TW_SUFFIXES):
            result.setdefault(symbol.upper().rsplit(".", 1)[0], industries.get(symbol.upper()))
    return result


def store_summary(store, codes, today):
    """Counts for the end-of-run summary and --verify."""
    data = store["data"]
    seasons = sorted({key for row in data.values() for key in row}, key=season_number)
    fields = {field: sum(1 for row in data.values() for entry in row.values() if entry[FIELD_AT[field]] is not None)
              for field in FIELDS}
    kinds = Counter(tw.REVENUE_KINDS[entry[3]] for row in data.values() for entry in row.values() if entry[0] is not None)
    target = due_season(today)
    with_data = [code for code in codes if data.get(code)]
    current = [code for code in with_data if season_number(newest_season(data[code])) >= season_number(target)]
    return {
        "universe": len(codes), "fetched": sum(1 for code in codes if code in store["fetch"]),
        "withData": len(with_data), "current": len(current), "dueSeason": target,
        "seasons": f"{seasons[0]}–{seasons[-1]} ({len(seasons)})" if seasons else None,
        "entries": sum(len(row) for row in data.values()), "fields": fields, "kinds": dict(kinds),
        "types": len(store["types"]),
    }


def status_line(summary):
    """Last log line of a run / --summary: DONE once every universe stock has been fetched at least once."""
    remaining = summary["universe"] - summary["fetched"]
    return (f"FinMind {'DONE' if not remaining else 'NOT DONE'}: {summary['fetched']}/{summary['universe']} stocks fetched"
            + (f", {remaining} left: run the same command again" if remaining else ""))


def kinds_by_industry(store, codes):
    """{industry: Counter(revenue kind of the newest quarter)} of the financial / insurance groups, + stocks without revenue."""
    result = {}
    for code, industry in codes.items():
        row = store["data"].get(code)
        if not row:
            continue
        entry = row[newest_season(row)]
        label = tw.REVENUE_KINDS[entry[3]] if entry[0] is not None else "（無營收）"
        if entry[3] or entry[0] is None or (industry and ("金融" in industry or "保險" in industry)):
            result.setdefault(industry or "（未分類）", Counter())[label] += 1
    return {industry: dict(counter) for industry, counter in sorted(result.items())}


def spot_lines(store, modes, codes):
    lines = []
    for code in SPOT_CODES:
        row = store["data"].get(code)
        if not row or code not in codes:
            continue
        keys = sorted(row, key=season_number)[-5:]
        raw = ", ".join(f"{key} {row[key][:3]}" for key in keys)
        line = f"  {code} kind {tw.REVENUE_KINDS[row[keys[-1]][3]]}: published {raw}"
        if all(modes.get(field) for field in FIELDS):
            quarters, annual = company_periods(row, modes)
            quarters = sorted(quarters, key=lambda period: period["end"])[-4:]
            annual = sorted(annual, key=lambda period: period["end"])[-3:]
            line += ("; quarters " + ", ".join(f"{p['fy']}Q{p['fq']} rev {p['rev']} eps {p['epsB']}" for p in quarters)
                     + "; years " + ", ".join(f"{p['fy']} rev {p['rev']} eps {p['epsB']}" for p in annual))
        lines.append(line)
    return lines


def yahoo_annual_check(store, modes, financials_dir):
    """Annual revenue read with the verdict / the Yahoo financials file's (NT$) → (count, median, share within 2%)."""
    ratios = []
    if not financials_dir or not os.path.isdir(financials_dir) or not all(modes.get(field) for field in FIELDS):
        return 0, None, None
    for name in sorted(os.listdir(financials_dir)):
        if not name.endswith((".TW.json", ".TWO.json")):
            continue
        row = store["data"].get(name.split(".")[0])
        document = read_json(os.path.join(financials_dir, name)) if row else None
        annual = document.get("annual") if isinstance(document, dict) else None
        if not isinstance(annual, dict) or not isinstance(annual.get("end"), list):
            continue
        revenue = growth.column(annual.get("values"), "revenue", len(annual["end"]))
        mine = {period["fy"]: period["rev"] for period in company_periods(row, modes)[1] if period["rev"]}
        for end, value in zip(annual["end"], revenue):
            day = growth.parse_day(end)
            if day and day.month == 12 and value and day.year in mine:
                ratios.append(mine[day.year] / value)
    if not ratios:
        return 0, None, None
    middle = median(ratios)
    scale = min((1.0, 0.001, 1000.0), key=lambda factor: abs(middle / factor - 1))
    within = sum(1 for ratio in ratios if abs(ratio / scale - 1) <= CROSS_CHECK_TOLERANCE) / len(ratios)
    return len(ratios), round(middle, 6), round(within, 3)


def verify(store, codes, today, official, financials_dir, log=print):
    """Offline checks of the stored file (no network) → 0 when every criterion passes, else 1."""
    summary = store_summary(store, codes, today)
    log(f"FinMind store: {json.dumps(summary, ensure_ascii=False)}")
    modes, report = detect_modes(store["data"])
    for line in describe_modes(report):
        log(line)
    check = cross_check(store["data"], modes, official)
    log(f"Cross-check with the official tw.json (year-to-date revenue, general format): {json.dumps(check)}")
    count, middle, within = yahoo_annual_check(store, modes, financials_dir)
    log(f"Cross-check with the Yahoo financials files (annual revenue, informational): {count} company-years, "
        f"median ratio {middle}, within 2% {within}")
    log(f"Revenue kinds by industry (financial formats and stocks without revenue): "
        f"{json.dumps(kinds_by_industry(store, codes), ensure_ascii=False)}")
    bad = contradicting(store["data"], modes["rev"]) if modes["rev"] else set()
    log(f"Stocks contradicting the revenue verdict (left out of growth.json): {len(bad)} {sorted(bad)[:20]}")
    log("Spot checks (newest five published quarters; derived quarters / years with the verdict):")
    for line in spot_lines(store, modes, codes):
        log(line)
    total = summary["universe"] or 1
    criteria = [
        ("every universe stock fetched at least once (>= 99%)", summary["fetched"] / total >= 0.99,
         f"{summary['fetched']} / {summary['universe']}"),
        (f"stocks with data >= {COMPLETE_SHARE:.0%}", summary["withData"] / total >= COMPLETE_SHARE,
         f"{summary['withData']} / {summary['universe']}"),
        (f"stocks up to {summary['dueSeason']} >= 90% of those with data",
         summary["withData"] and summary["current"] / summary["withData"] >= 0.9,
         f"{summary['current']} / {summary['withData']}"),
        ("revenue and EPS: single quarter / year-to-date decided", modes["rev"] is not None and modes["eps"] is not None,
         f"rev {report['rev']['verdict']}, eps {report['eps']['verdict']}, ni {report['ni']['verdict']}"),
        ("year-to-date revenue matches the official tw.json (>= 500 companies, median within 2%, >= 90% within 2%)",
         check["count"] >= 500 and bool(check["ok"]) and (check["within2pct"] or 0) >= 0.9,
         f"{check['count']} companies, median {check['median']}, scale {check['scale']}, within 2% {check['within2pct']}"),
    ]
    for name, passed, detail in criteria:
        log(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")
    passed = all(entry[1] for entry in criteria)
    log("VERIFY " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


def save(store, out, previous_body):
    """Writes the store when it changed → (written, bytes, new body)."""
    document = dump_store(store)
    body = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    if body == previous_body or (not document["data"] and not document["fetch"]):
        return False, len(body.encode("utf-8")) + 1, previous_body
    size = write_atomic(out, document)
    return True, size, body


def run(args, http=None, now=None, sleep=time.sleep, clock=time.monotonic, log=print, token=None):
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    today = now.date()
    out = args.out or os.path.join(args.report_dir, "fundamentals", FILE_NAME)
    previous = read_json(out)
    if previous is None and os.path.exists(out):
        log(f"::error::{out} exists but cannot be read; not touched (fix or delete it)")
        return 1
    try:
        store = load_store(previous)
    except StoreError as error:
        log(f"::error::{out}: {error}; not touched (fix or delete it)")
        return 1
    previous_body = json.dumps(dump_store(store), ensure_ascii=False, separators=(",", ":")) if previous else None
    universe = read_json(os.path.join(args.report_dir, "universe.json"))
    codes = universe_codes(universe)
    if args.codes:
        codes = {**{code: None for code in args.codes}, **{c: i for c, i in codes.items() if c in args.codes}}
    if not codes:
        log("::error::no Taiwan stocks: universe.json missing or empty (and no --codes)")
        return 1
    if args.summary:
        if previous is None:
            log(f"::error::{out} not found: nothing fetched yet (run without --summary first)")
            return 1
        summary = store_summary(store, codes, today)
        log(f"FinMind store: {json.dumps(summary, ensure_ascii=False)}")
        for line in describe_modes(detect_modes(store["data"])[1]):
            log(line)
        log(status_line(summary))
        return 0
    if args.verify:
        official = read_json(os.path.join(args.report_dir, "fundamentals", growth.TW_FILE))
        return verify(store, codes, today, official, os.path.join(args.report_dir, "financials"), log)
    if args.only_when_complete:
        summary = store_summary(store, codes, today)
        if summary["fetched"] < COMPLETE_SHARE * summary["universe"]:
            log(f"FinMind first backfill not finished ({summary['fetched']} / {summary['universe']} stocks): "
                f"nothing fetched here (the backfill runs elsewhere, e.g. Codex)")
            return 0
    token = token if token is not None else (os.environ.get("FINMIND_TOKEN") or "").strip() or None
    interval = request_interval(token)
    plan = plan_fetches(store, sorted(codes), today, set(args.codes or ()))
    log(f"FinMind plan: {len(plan)} stocks ({Counter(reason.split(' ')[0] for _, _, reason in plan)}), "
        f"{'with' if token else 'without'} token, one request every {interval:.1f}s, budget {args.budget:.0f}s")
    http = http or Http()
    counter = {"requests": 0, "limit": args.limit}
    deadline = clock() + args.budget
    done, errors, consecutive, stopped, next_at, halted = 0, 0, 0, None, clock(), False
    types_seen, skipped, conflicts = Counter(), Counter(), 0
    written, size, refused = False, None, False
    try:
        for index, (code, start_date, reason) in enumerate(plan):
            if args.limit is not None and counter["requests"] >= args.limit:
                stopped = f"--limit {args.limit} requests ({len(plan) - index} stocks left)"
                break
            wait = max(0.0, next_at - clock())
            if clock() + wait > deadline:
                stopped = f"time budget ({len(plan) - index} stocks left for the next run)"
                break
            if wait:
                sleep(wait)
            before = counter["requests"]
            try:
                rows = fetch_stock(http, code, start_date, token, sleep, log, counter)
            except LimitReached:
                stopped = f"--limit {args.limit} requests ({len(plan) - index} stocks left)"
                break
            except RateLimited as error:
                stopped, halted = f"{error} ({len(plan) - index} stocks left)", True
                break
            except AuthError as error:
                stopped, halted, refused = f"{error}", True, True
                errors += 1
                break
            except FinMindError as error:
                errors += 1
                consecutive += 1
                log(f"::warning::FinMind {code}: {error}")
                if consecutive >= MAX_CONSECUTIVE_ERRORS:
                    stopped = f"{consecutive} failures in a row"
                    break
                continue
            finally:
                next_at = clock() + interval * max(1, counter["requests"] - before)
            consecutive = 0
            parsed = parse_rows(rows, code)
            merge_stock(store, code, parsed["entries"], today)
            for type_name, name in parsed["names"].items():
                store["types"].setdefault(type_name, name)
            types_seen.update(parsed["types"])
            skipped.update(parsed["skipped"])
            conflicts += parsed["conflicts"]
            done += 1
            if done % SAVE_EVERY == 0:
                written_now, size, previous_body = save(store, out, previous_body)
                written = written or written_now
                log(f"FinMind progress: {done} stocks this run, {counter['requests']} requests, last {code} ({reason})")
    except KeyboardInterrupt:
        stopped = "interrupted"
    finally:
        written_now, size, previous_body = save(store, out, previous_body)
        written = written or written_now
    summary = store_summary(store, codes, today)
    log(f"FinMind run: {done} stocks fetched, {counter['requests']} requests, {errors} errors"
        + (f", stopped: {stopped}" if stopped else ", plan finished"))
    log(f"FinMind store: {summary['fetched']} / {summary['universe']} stocks fetched, {summary['withData']} with data, "
        f"{summary['current']} up to {summary['dueSeason']}; seasons {summary['seasons']}; entries {summary['entries']} "
        f"(rev {summary['fields']['rev']}, eps {summary['fields']['eps']}, ni {summary['fields']['ni']}); "
        f"revenue kinds {json.dumps(summary['kinds'], ensure_ascii=False)}; "
        f"file {size:,} B{' written' if written else ' unchanged'}")
    if types_seen:
        log(f"FinMind types this run (rows): {json.dumps(dict(types_seen.most_common()), ensure_ascii=False)}")
        log(f"FinMind type → origin_name: {json.dumps(store['types'], ensure_ascii=False)}")
    if skipped or conflicts:
        log(f"FinMind rows skipped: {dict(skipped)}; same-rank conflicts (first kept): {conflicts}")
    if store["data"]:
        _, report = detect_modes(store["data"])
        for line in describe_modes(report):
            log(line)
    if halted:
        log(f"::warning::FinMind stopped: {stopped}; progress saved, run the same command again later")
    log(status_line(summary))
    return 1 if refused or (not done and plan and errors) else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="FinMind Taiwan income statements → docs/report/fundamentals/tw-finmind.json")
    parser.add_argument("--report-dir", default=os.path.join(REPO_ROOT, "docs", "report"))
    parser.add_argument("--out", default=None)
    parser.add_argument("--budget", type=float, default=float(os.environ.get("FINMIND_BUDGET_SECONDS") or DEFAULT_BUDGET_SECONDS),
                        help="seconds of fetching this run (default 600)")
    parser.add_argument("--limit", type=int, default=None, help="at most this many requests")
    parser.add_argument("--codes", type=lambda text: [part.strip().upper() for part in text.split(",") if part.strip()],
                        default=None, help="only these stocks, full history again (e.g. 2330,2881)")
    parser.add_argument("--only-when-complete", action="store_true",
                        help="do nothing until the first backfill covers 95%% of the stocks (daily workflow)")
    parser.add_argument("--verify", action="store_true", help="offline checks of the stored file; exit 1 on FAIL")
    parser.add_argument("--summary", action="store_true", help="offline counts of the stored file (no network)")
    return parser.parse_args(argv)


def main(argv=None):
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())

"""
Builds docs/report/financials/<KEY>.json (財報分析中心: statement history per stock) from Yahoo Finance
fundamentals-timeseries, plus docs/report/financials/index.json (this script's state; the frontend
does not load it). Runs in the daily schedule workflow; the files are committed with docs/report.

  python scripts/build_financials.py          (working directory: atlas)
  Env: ATLAS_REPORT_DIR (default ../docs/report): latest.json / themes.json / universe.json,
       ATLAS_DATA_DIR (default ../data): CSV fallback for the symbol list,
       ATLAS_FINANCIALS_DIR (default <ATLAS_REPORT_DIR>/financials).

- KEY: Taiwan 2330.TW / 6488.TWO; US upper case with '.' → '-' (BRK-B, as growthKey and Yahoo).
- Symbols, in priority order (duplicates dropped): latest.json groups, themes.json constituents,
  universe.json US rows, universe.json TW rows by turnover20 (top 300, ETFs left out); a market
  missing from universe.json falls back to data/*.csv (TW ranked by 20-day turnover from the CSV).
- Foreign issuers (ADR, 20-F / 40-F) of the full US list that are not above: a stored file with
  foreignReporter true or the "foreign-filer" code (universe.json has no country column), or
  FOREIGN_MANUAL. They refresh in a lane of their own, at most FOREIGN_SHARE of the requests while the
  list above is due, and are logged as "Foreign issuers outside the core list: …".
- Yahoo values in another currency than the file's statement currency (e.g. a USD conversion of a TWD
  statement) are never mixed into the file: left out, counted, and noted in the log.
- Foreign reporters (foreignReporter true): Yahoo's EPS and share count are per ADR (1 ADR = k ordinary
  shares), while SEC IFRS EPS is per ordinary share. Until the file carries an adrRatio {"epsFactor": k},
  the same-period EPS ratios Yahoo ÷ stored (basic and diluted) give k: all within ±2% of their median
  (snapped to a whole number or its reciprocal; 1 when within 2% of 1), or, looser, a median within 6%
  of a whole number with at least half of the pairs that close; without an overlapping period, the
  implied share counts (net income ÷ EPS) give it. With k ≠ 1 the stored EPS are multiplied by k
  (quality issue "adr-eps-rescaled"). With no k the stored EPS and share counts are dropped and Yahoo's
  kept, one basis (adrRatio basis "Yahoo", quality issue "eps-yahoo-only"). A file with no EPS yet has
  no second basis: Yahoo's values are merged and judged once it has EPS.
- One timeseries request per stock (annual + quarterly types together). Yahoo only returns ~4 years
  and ~5–8 quarters, so every run merges into the stored file and history accumulates:
  * period ends within ±5 days are the same period (the newer date wins); a new non-null value
    replaces the stored one, a missing one keeps it; revenue / net income moving > 2% is recorded
    as a restatement (quality.revisions, issue "restated");
  * annual keeps only 12M points, quarterly only 3M points (TTM / 6M / 9M are dropped); points in
    another currency than the statement currency are dropped (share counts excepted);
  * official periods (src "sec": seed_financials_sec.py; src "finmind": build_financials_tw_finmind.py)
    are never replaced or removed: Yahoo only fills their null fields, a > 2% revenue / net income
    difference is recorded as "yahoo-differs", and Yahoo periods are only added after the newest official
    period;
  * up to 20 annual and 60 quarterly periods (oldest Yahoo periods dropped first, SEC never).
- Splits: when adjacent diluted share counts jump (×1.8 / ×0.55), one chart request (events=split)
  fetches the split events; periods whose share count × the split ratio lines up with the next newer
  period (±25%) were not restated by Yahoo and are adjusted (shares × k, EPS ÷ k). Ratios under 1.5
  (Taiwan stock dividends) are ignored.
- Refetch: no file (a miss is retried after 7 days); latest period over 100 days old and checked more
  than 3 days ago; checked more than 45 days ago. Never-fetched stocks first, then the oldest check;
  stocks whose request or payload failed on an earlier run (index.json "failures") go last.
- Budget 600 s, 0.15 s between requests; 8 consecutive failed requests stop the run: files already
  written are kept, index.json is written, and the script exits 1. Each file is written atomically,
  and only when its data changed (a check that finds nothing new only updates index.json, so the
  file's checkedAt / generatedAt are the last run that changed it; index.json has the last check).
  A stored file that cannot be read back is never fetched or overwritten (fix or delete it by hand).
- Files of stocks no longer listed anywhere are deleted 180 days after they were last listed, unless
  they hold SEC periods.
"""
import json
import math
import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from build_valuation_us import Yahoo, finite_number, read_json, write_atomic

TIMESERIES_URL = (
    "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"
)
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
VERSION = 1
SOURCE = "Yahoo Finance fundamentals-timeseries"
SOURCE_MIXED = "SEC EDGAR companyfacts + Yahoo Finance"
SOURCE_FINMIND = "FinMind（公開資訊觀測站）"
SOURCE_FINMIND_MIXED = "FinMind（公開資訊觀測站）+ Yahoo Finance"
# Official periods: src "sec" (seed_financials_sec.py) and src "finmind" (build_financials_tw_finmind.py).
# Yahoo never replaces or removes them (it only fills their null values), and they are never trimmed.
OFFICIAL_SOURCES = ("sec", "finmind")
SOURCE_VALUES = ("sec", "yahoo", "finmind")
# index.json item: date of the last SEC companyfacts check (seed_financials_sec.py --due). Kept apart from
# checkedAt, which is the Yahoo check date; run_refresh and seed_financials_sec.update_index carry it over
SEC_CHECKED_KEY = "secCheckedAt"
REQUEST_DELAY = 0.15
DEFAULT_BUDGET_SECONDS = 600
MAX_CONSECUTIVE_FAILURES = 8
TW_LIMIT = 300
HISTORY_YEARS = 12
KEEP_ANNUAL = 20
KEEP_QUARTERLY = 60
SAME_PERIOD_DAYS = 5
RESTATE_TOLERANCE = 0.02
MISMATCH_TOLERANCE = 0.05
KEEP_REVISIONS = 50
# Latest period older than this → a new report is due, re-check every RECHECK_DAYS
REPORT_DUE_DAYS = 100
RECHECK_DAYS = 3
MAX_CHECK_AGE_DAYS = 45
MISS_RETRY_DAYS = 7
KEEP_UNLISTED_DAYS = 180
# Splits: adjacent share counts this far apart trigger the chart request; ratios closer to 1 than
# MIN_SPLIT_RATIO are ignored; an adjusted share count must land within SPLIT_MATCH of the next one
SHARE_JUMP_UP = 1.8
SHARE_JUMP_DOWN = 0.55
MIN_SPLIT_RATIO = 1.5
SPLIT_MATCH = 0.25
# repair_split_eps: EPS must be this close to a split-factor multiple of net income / shares to be rewritten
REPAIR_MATCH = 0.2
# Foreign issuers (ADR, 20-F / 40-F) of the full US universe that are not in the core list form their own
# lane: while core stocks are still due they take at most this share of the requests, so the core list
# is refreshed on schedule even during the first pass over the (several hundred) foreign issuers
FOREIGN_SHARE = 0.3
# Foreign issuers that may have no stored file yet (universe.json has no country column). Only counted
# when they are in the full US universe.
FOREIGN_MANUAL = ("TSM", "UMC", "ASX", "CHT")
# EPS basis of a foreign reporter (adrRatio, see resolve_eps_basis): Yahoo's EPS is per ADR, a stored SEC
# EPS per ordinary share. The pair ratios must agree within ADR_RATIO_SPREAD of their median; the median
# is k unless it is within ADR_RATIO_UNITY of 1, and k snaps to a whole number (or its reciprocal) within
# ADR_RATIO_SNAP
ADR_RATIO_SPREAD = 0.02
ADR_RATIO_UNITY = 0.02
ADR_RATIO_SNAP = 0.02
# When the pairs do not all agree (a restated year, local GAAP against IFRS EPS, a stale Yahoo value), k is
# still taken when the median lies within ADR_RATIO_LOOSE of a whole number (or its reciprocal) and at
# least half of the pairs lie that close to it. Without an overlapping period, k comes from the implied
# share counts (net income ÷ EPS) of the nearest stored and Yahoo periods, with the same snap
ADR_RATIO_LOOSE = 0.06
SHARE_GAP_DAYS = 800                     # implied share counts are compared at most ~2 years apart
TW_SUFFIXES = (".TW", ".TWO")
HOME_CURRENCY = {"US": "USD", "TW": "TWD"}
PERIOD_TYPES = {"annual": "12M", "quarterly": "3M"}

# canonical field ← Yahoo types (first with a value wins), in file order
FIELDS = {
    "revenue": ("TotalRevenue",),
    "costOfRevenue": ("CostOfRevenue",),
    "grossProfit": ("GrossProfit",),
    "operatingIncome": ("OperatingIncome",),
    "operatingIncomeReported": ("TotalOperatingIncomeAsReported",),
    "pretaxIncome": ("PretaxIncome",),
    "taxProvision": ("TaxProvision",),
    "netIncome": ("NetIncomeCommonStockholders", "NetIncome"),
    "epsDiluted": ("DilutedEPS",),
    "epsBasic": ("BasicEPS",),
    "dilutedShares": ("DilutedAverageShares",),
    "interestExpense": ("InterestExpense",),
    "operatingCashFlow": ("OperatingCashFlow",),
    "capex": ("CapitalExpenditure",),
    "fcfYahoo": ("FreeCashFlow",),
    "equity": ("StockholdersEquity",),
    "totalDebt": ("TotalDebt",),
    "cash": ("CashCashEquivalentsAndShortTermInvestments", "CashAndCashEquivalents"),
    "totalAssets": ("TotalAssets",),
    "dividendsPaid": ("CashDividendsPaid",),
    "buybacks": ("RepurchaseOfCapitalStock",),
}
YAHOO_TYPES = tuple(kind for kinds in FIELDS.values() for kind in kinds)
REQUEST_TYPES = ",".join(prefix + kind for prefix in PERIOD_TYPES for kind in YAHOO_TYPES)
PER_SHARE_FIELDS = ("epsDiluted", "epsBasic", "dilutedShares")
# Not amounts of money: their currency code says nothing about the statement currency
NON_MONEY_TYPES = ("DilutedEPS", "BasicEPS", "DilutedAverageShares")
# Outflows stored as positive numbers
OUTFLOW_FIELDS = ("capex", "dividendsPaid", "buybacks")
ISSUES = (
    "short-history", "financial-sector-format", "missing-cashflow", "missing-balance",
    "split-adjusted", "foreign-currency", "restated", "yahoo-differs", "quarter-annual-mismatch",
    "quarterly-ytd-suspected", "negative-equity", "capex-sign", "currency-changed", "adr-eps-rescaled",
    "eps-yahoo-only", "q4-eps-from-shares", "mixed-basis", "quarterly-as-reported",
)
# Quality codes that only sec_ifrs.py writes (its IFRS and US GAAP foreign-filer periods); update_document keeps them
SEC_FOREIGN_ISSUES = ("ifrs", "ifrs-fallback", "foreign-gaap", "foreign-gaap-fallback")
# Quality codes that only build_financials_tw_finmind.py writes (appended like SEC_ISSUES, not in ISSUES): the
# shared "q4-derived" / "debt-approx" codes and FinMind's "eps-annual-summed" / "debt-missing"; update_document
# keeps them while the file holds FinMind periods
FINMIND_ISSUES = ("q4-derived", "eps-annual-summed", "debt-approx", "debt-missing")
DOCUMENT_KEYS = (
    "version", "symbol", "market", "currency", "foreignReporter", "generatedAt", "checkedAt", "source",
    "fyEndMonth", "annual", "quarterly", "split", "quality",
)
SECTION_KEYS = ("end", "fy", "fq", "src", "values")


def parse_day(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def days_between(a, b):
    return (parse_day(b) - parse_day(a)).days


def clean_value(value):
    value = finite_number(value)
    if value is None:
        return None
    if value.is_integer() and abs(value) < 2 ** 53:
        return int(value)
    # + 0.0 turns a rounded -0.0 into 0.0
    return round(value, 4) + 0.0


def clean_ratio(value):
    return int(value) if float(value).is_integer() else round(float(value), 6)


def stored_number(value):
    """A value read back from a stored file: None or a finite number, else the file is broken."""
    if value is None:
        return None
    if finite_number(value) is None:
        raise ValueError(f"bad value {value!r}")
    return value


def financials_key(symbol):
    if not isinstance(symbol, str):
        return None
    symbol = symbol.strip().upper()
    if not symbol or symbol.startswith("^") or symbol == "INDEX":
        return None
    if symbol.endswith(TW_SUFFIXES):
        code = symbol.rsplit(".", 1)[0]
        return symbol if code and code.isalnum() and len(code) <= 8 else None
    # Class shares: Yahoo and the screener's lookup (growthKey) both use BRK-B, not BRK.B
    symbol = symbol.replace(".", "-")
    if len(symbol) > 12 or not all(ch.isalnum() or ch == "-" for ch in symbol):
        return None
    return symbol


def market_of(key):
    return "TW" if key.endswith(TW_SUFFIXES) else "US"


def period(end, values, src="yahoo", extra=None):
    return {"end": end, "src": src, "values": dict(values), "extra": dict(extra or {})}


# ---------------------------------------------------------------------------------------------
# Yahoo payloads
# ---------------------------------------------------------------------------------------------
def detect_currency(raw, market):
    """Most common currency code among money points (ties: the market's home currency first)."""
    counts = Counter()
    for frequency in raw.values():
        for kinds in frequency.values():
            for kind, by_currency in kinds.items():
                if kind in NON_MONEY_TYPES:
                    continue
                for currency in by_currency:
                    if currency:
                        counts[currency] += 1
    if not counts:
        return None
    home = HOME_CURRENCY.get(market)
    return sorted(counts, key=lambda c: (-counts[c], c != home, c))[0]


def pick(by_currency, kind, currency):
    """The point value in the statement currency (or without a currency code)."""
    if currency in by_currency:
        return by_currency[currency]
    for blank in (None, ""):
        if blank in by_currency:
            return by_currency[blank]
    if kind == "DilutedAverageShares" and by_currency:
        # A share count is a count, whatever currency code Yahoo attaches to it
        return by_currency[sorted(by_currency, key=str)[0]]
    return None


def other_currency(by_currency, currency):
    """True when some point carries a currency code that is neither `currency` nor blank."""
    return any(code and code != currency for code in by_currency)


def parse_timeseries(payload, today, currency=None, market="US"):
    """
    timeseries payload → {"currency", "detected", "annual": {end: {field: value}}, "quarterly": …,
    "capexSign"}. currency: the stored statement currency; None detects it from the payload.
    """
    raw = {"annual": {}, "quarterly": {}}
    timeseries = payload.get("timeseries") if isinstance(payload, dict) else None
    results = timeseries.get("result") if isinstance(timeseries, dict) else None
    for series in results if isinstance(results, list) else []:
        if not isinstance(series, dict) or not isinstance(series.get("meta"), dict):
            continue
        types = series["meta"].get("type")
        if not isinstance(types, list) or not types or not isinstance(types[0], str):
            continue
        name = types[0]
        frequency = next((prefix for prefix in PERIOD_TYPES if name.startswith(prefix)), None)
        if frequency is None or name[len(frequency):] not in YAHOO_TYPES:
            continue
        kind = name[len(frequency):]
        points = series.get(name)
        for point in points if isinstance(points, list) else []:
            if not isinstance(point, dict) or point.get("periodType") != PERIOD_TYPES[frequency]:
                continue
            end = point.get("asOfDate")
            day = parse_day(end)
            reported = point.get("reportedValue")
            value = finite_number(reported.get("raw")) if isinstance(reported, dict) else None
            if day is None or day > today or value is None:
                continue
            code = point.get("currencyCode")
            code = code if isinstance(code, str) else None
            raw[frequency].setdefault(end, {}).setdefault(kind, {})[code] = value

    detected = detect_currency(raw, market)
    chosen = currency or detected or HOME_CURRENCY.get(market, "USD")
    parsed = {
        "currency": chosen, "detected": detected, "annual": {}, "quarterly": {}, "capexSign": False,
        "skipped": 0,       # fields left out because Yahoo only gave them in another currency
    }
    for frequency, ends in raw.items():
        for end, kinds in ends.items():
            row = {}
            for field, sources in FIELDS.items():
                for kind in sources:
                    value = pick(kinds.get(kind) or {}, kind, chosen)
                    if value is None:
                        continue
                    if field in OUTFLOW_FIELDS:
                        if field == "capex" and value > 0:
                            parsed["capexSign"] = True
                        value = abs(value)
                    row[field] = clean_value(value)
                    break
                else:
                    # Never mixed into a series of another currency (e.g. a USD conversion of a TWD statement)
                    if any(other_currency(kinds.get(kind) or {}, chosen) for kind in sources):
                        parsed["skipped"] += 1
            # Share counts alone (e.g. every other point in a foreign currency) do not make a period
            if any(field != "dilutedShares" for field in row):
                parsed[frequency][end] = row
    return parsed


def parse_splits(payload):
    """chart payload (events=split) → [["YYYY-MM-DD", ratio], ...] ascending, small ratios dropped."""
    chart = payload.get("chart") if isinstance(payload, dict) else None
    results = chart.get("result") if isinstance(chart, dict) else None
    events = {}
    for result in results if isinstance(results, list) else []:
        splits = ((result or {}).get("events") or {}).get("splits") if isinstance(result, dict) else None
        if not isinstance(splits, dict):
            continue
        for event in splits.values():
            if not isinstance(event, dict):
                continue
            stamp = finite_number(event.get("date"))
            numerator = finite_number(event.get("numerator"))
            denominator = finite_number(event.get("denominator"))
            if stamp is None or not numerator or not denominator or numerator <= 0 or denominator <= 0:
                continue
            ratio = numerator / denominator
            if 1 / MIN_SPLIT_RATIO < ratio < MIN_SPLIT_RATIO:
                continue
            day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
            events[day] = clean_ratio(ratio)
    return [[day, events[day]] for day in sorted(events)]


# ---------------------------------------------------------------------------------------------
# Stored documents
# ---------------------------------------------------------------------------------------------
def read_section(section):
    """Stored annual / quarterly section → (periods, section-level extras). ValueError if broken."""
    if section is None:
        return [], {}
    if not isinstance(section, dict):
        raise ValueError("section is not an object")
    ends = section.get("end", [])
    if not isinstance(ends, list) or any(parse_day(end) is None for end in ends):
        raise ValueError("bad period ends")
    count = len(ends)
    columns = section.get("values", {})
    if not isinstance(columns, dict):
        raise ValueError("values is not an object")
    for field, column in columns.items():
        if not isinstance(column, list) or len(column) != count:
            raise ValueError(f"values.{field} does not match end")
        for value in column:
            stored_number(value)
    sources = section.get("src")
    if sources is None:
        sources = ["yahoo"] * count
    elif not isinstance(sources, list) or len(sources) != count or any(s not in SOURCE_VALUES for s in sources):
        raise ValueError("bad src")
    per_period, extras = {}, {}
    for key, value in section.items():
        if key in SECTION_KEYS:
            continue
        if isinstance(value, list) and len(value) == count:
            per_period[key] = value
        else:
            extras[key] = value
    periods = []
    for index, end in enumerate(ends):
        values = {field: column[index] for field, column in columns.items() if column[index] is not None}
        extra = {key: column[index] for key, column in per_period.items()}
        periods.append(period(end, values, sources[index], extra))
    periods.sort(key=lambda item: item["end"])
    return periods, extras


def read_sections(document):
    """Validated content of a stored file; ValueError when it cannot be merged into safely."""
    if not isinstance(document, dict) or document.get("version") != VERSION:
        raise ValueError("not a version 1 document")
    annual, annual_extras = read_section(document.get("annual"))
    quarterly, quarterly_extras = read_section(document.get("quarterly"))
    split = document.get("split") if isinstance(document.get("split"), dict) else {}
    events = {}
    for event in split.get("events") or []:
        if isinstance(event, list) and len(event) == 2 and parse_day(event[0]) and finite_number(event[1]):
            if event[1] > 0:
                events[event[0]] = event[1]
    quality = document.get("quality") if isinstance(document.get("quality"), dict) else {}
    revisions = [
        item for item in quality.get("revisions") or []
        if isinstance(item, dict) and isinstance(item.get("end"), str) and isinstance(item.get("kind"), str)
    ]
    currency = document.get("currency")
    month = document.get("fyEndMonth")
    return {
        "annual": annual,
        "quarterly": quarterly,
        "extras": {"annual": annual_extras, "quarterly": quarterly_extras},
        "currency": currency if isinstance(currency, str) and currency else None,
        "fyEndMonth": month if isinstance(month, int) and 1 <= month <= 12 else None,
        "events": [[day, events[day]] for day in sorted(events)],
        "adjusted": split.get("adjusted") is True,
        "revisions": revisions,
        "other": {key: value for key, value in document.items() if key not in DOCUMENT_KEYS},
    }


def load_document(path, key):
    """→ ("missing", None) | ("ok", document) | ("corrupt", reason)."""
    if not os.path.exists(path):
        return "missing", None
    try:
        with open(path, encoding="utf-8") as file:
            document = json.load(file)
        read_sections(document)
    except (OSError, ValueError) as error:
        return "corrupt", str(error)
    if document.get("symbol") not in (None, key):
        return "corrupt", f"symbol {document.get('symbol')!r}"
    return "ok", document


def has_sec(document):
    try:
        sections = read_sections(document)
    except ValueError:
        return True     # cannot tell: treat as precious
    return any(p["src"] == "sec" for p in sections["annual"] + sections["quarterly"])


def official_period(item):
    """An explicitly reviewed SEC basis outranks a legacy Yahoo row label."""
    basis = item.get("extra", {}).get("secFilingBasis") or {}
    return item["src"] in OFFICIAL_SOURCES or basis.get("restated") is True


def has_official(document):
    """A stored file that holds any official period (SEC or FinMind): never deleted as unlisted."""
    try:
        sections = read_sections(document)
    except ValueError:
        return True     # cannot tell: treat as precious
    return any(official_period(p) for p in sections["annual"] + sections["quarterly"])


def source_label(periods, filled=()):
    """The file's source line: SEC / FinMind (+ Yahoo when Yahoo periods or values are in the file)."""
    if any(p["src"] == "sec" for p in periods):
        return SOURCE_MIXED
    if any(p["src"] == "finmind" for p in periods):
        yahoo = bool(filled) or any(p["src"] == "yahoo" for p in periods)
        return SOURCE_FINMIND_MIXED if yahoo else SOURCE_FINMIND
    return SOURCE


# ---------------------------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------------------------
def relative_change(old, new):
    if old == new:
        return 0.0
    if old == 0:
        return math.inf
    return abs(new - old) / abs(old)


def dedupe(periods):
    """New points within SAME_PERIOD_DAYS of each other are one period (the later date)."""
    result = []
    for item in sorted(periods, key=lambda p: p["end"]):
        if result and days_between(result[-1]["end"], item["end"]) <= SAME_PERIOD_DAYS:
            merged = dict(result[-1]["values"])
            merged.update(item["values"])
            result[-1] = period(item["end"], merged, item["src"])
        else:
            result.append(period(item["end"], item["values"], item["src"]))
    return result


def same_period_matches(periods, new):
    """{index in new: index in periods}: periods within SAME_PERIOD_DAYS, nearest first, one to one."""
    pairs = sorted(
        (abs(days_between(p["end"], item["end"])), i, j)
        for i, p in enumerate(periods)
        for j, item in enumerate(new)
        if abs(days_between(p["end"], item["end"])) <= SAME_PERIOD_DAYS
    )
    match, used = {}, set()
    for _, i, j in pairs:
        if i not in used and j not in match:
            match[j] = i
            used.add(i)
    return match


def merge_section(old, new, frequency, today_iso, renamed=None, filled=None):
    """
    Merge new Yahoo periods into stored ones → (periods ascending, revisions).
    renamed (dict, optional) receives {old end: new end} for periods whose end date moved.
    filled (list, optional) receives (end, field) for every null official value that Yahoo filled.
    """
    periods = [period(p["end"], p["values"], p["src"], p["extra"]) for p in old]
    new = dedupe(new)
    official_ends = [p["end"] for p in periods if official_period(p)]
    latest_official = max(official_ends) if official_ends else None
    match = same_period_matches(periods, new)
    revisions = []
    original = len(periods)
    for j, item in enumerate(new):
        i = match.get(j)
        if i is None:
            near = any(
                abs(days_between(periods[k]["end"], item["end"])) <= SAME_PERIOD_DAYS for k in range(original)
            )
            # Never squeeze a Yahoo period into the SEC history
            if not near and (latest_official is None or item["end"] > latest_official):
                periods.append(period(item["end"], item["values"]))
            continue
        target = periods[i]
        if official_period(target):
            for field in ("revenue", "netIncome"):
                old_value, new_value = target["values"].get(field), item["values"].get(field)
                if old_value is not None and new_value is not None:
                    if relative_change(old_value, new_value) > RESTATE_TOLERANCE:
                        revisions.append(revision("yahoo-differs", frequency, target["end"], field,
                                                  old_value, new_value, today_iso))
            # Only fill gaps; per-share fields only as a set, so EPS and share count share a basis
            per_share_free = all(target["values"].get(f) is None for f in PER_SHARE_FIELDS)
            for field, value in item["values"].items():
                if field in PER_SHARE_FIELDS and not per_share_free:
                    continue
                if target["values"].get(field) is None:
                    target["values"][field] = value
                    if filled is not None:
                        filled.append((target["end"], field))
            continue
        for field in ("revenue", "netIncome"):
            old_value, new_value = target["values"].get(field), item["values"].get(field)
            if old_value is not None and new_value is not None:
                if relative_change(old_value, new_value) > RESTATE_TOLERANCE:
                    revisions.append(revision("restated", frequency, item["end"], field,
                                              old_value, new_value, today_iso))
        target["values"].update(item["values"])
        if target["end"] != item["end"] and renamed is not None:
            renamed[target["end"]] = item["end"]
        target["end"] = item["end"]
    periods.sort(key=lambda p: p["end"])
    # Every previously stored financial period is a permanent record. Display
    # windows may be bounded; storage is never trimmed by those window sizes.
    return periods, revisions


def revision(kind, frequency, end, field, old, new, seen):
    return {"kind": kind, "period": frequency, "end": end, "field": field, "old": old, "new": new, "seen": seen}


def merge_revisions(previous, new, annual, quarterly, renamed):
    """Stored + new revisions of periods still in the file (one entry per value seen)."""
    ends = {"annual": {p["end"] for p in annual}, "quarterly": {p["end"] for p in quarterly}}
    result, seen = [], set()
    for item in list(previous) + list(new):
        item = dict(item)
        frequency = item.get("period")
        item["end"] = (renamed.get(frequency) or {}).get(item["end"], item["end"])
        if item["end"] not in ends.get(frequency, ()):
            continue
        identity = (item.get("kind"), frequency, item["end"], item.get("field"), item.get("new"))
        if item.get("kind") in ("restated", "basis-unrestated") and item.get("accn") and item.get("originalAccn"):
            identity += (item.get("old"), item["accn"], item.get("filed"),
                         item["originalAccn"], item.get("originalFiled"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    result.sort(key=lambda r: (str(r.get("seen")), r["period"], r["end"], str(r.get("field")), r["kind"]))
    # New opt-in SEC audit records must retain every original filed value on later
    # ordinary refreshes too. Legacy revisions (without originalAccn) keep their cap.
    protected = [r for r in result if r.get("kind") in ("restated", "basis-unrestated") and r.get("accn") and r.get("originalAccn")]
    if not protected:
        return result[-KEEP_REVISIONS:]
    ordinary = [r for r in result if not (r.get("kind") in ("restated", "basis-unrestated") and r.get("accn") and r.get("originalAccn"))]
    return sorted(ordinary[-KEEP_REVISIONS:] + protected,
                  key=lambda r: (str(r.get("seen")), r["period"], r["end"], str(r.get("field")), r["kind"]))


# ---------------------------------------------------------------------------------------------
# EPS basis of foreign reporters
# ---------------------------------------------------------------------------------------------
def adr_eps_factor(document):
    """k of a stored adrRatio (1 ADR = k ordinary shares, EPS per ADR = k × EPS per share), else None."""
    ratio = document.get("adrRatio") if isinstance(document, dict) else None
    factor = finite_number(ratio.get("epsFactor")) if isinstance(ratio, dict) else None
    return factor if factor is not None and factor > 0 else None


def has_eps(periods):
    return any(item["values"].get(field) is not None for item in periods for field in ("epsBasic", "epsDiluted"))


def eps_ratios(stored, new):
    """Yahoo ÷ stored, for every EPS field (basic, diluted) that both sides hold in a same period; zeros skipped."""
    ratios = []
    for j, i in same_period_matches(stored, new).items():
        old, fetched = stored[i]["values"], new[j]["values"]
        for field in ("epsBasic", "epsDiluted"):
            if old.get(field) and fetched.get(field):
                ratios.append(fetched[field] / old[field])
    return ratios


def snap_factor(value):
    """value, or the whole number / reciprocal whole number it lies within ADR_RATIO_SNAP of (relative)."""
    whole = round(value)
    if whole >= 1 and abs(value - whole) <= ADR_RATIO_SNAP * whole:
        return float(whole)
    inverse = round(1 / value)
    if inverse >= 1 and abs(1 / value - inverse) <= ADR_RATIO_SNAP * inverse:
        return 1 / inverse
    return value


def whole_factor(value, tolerance=ADR_RATIO_LOOSE):
    """The whole number (or reciprocal whole number) within `tolerance` (relative) of value, else None."""
    if value is None or value <= 0:
        return None
    whole = round(value)
    if whole >= 1 and abs(value - whole) <= tolerance * whole:
        return whole
    inverse = round(1 / value)
    if inverse >= 1 and abs(1 / value - inverse) <= tolerance * inverse:
        return 1 / inverse
    return None


def eps_factor(ratios):
    """
    Pair ratios → (k, None), else (None, reason).
    Strict: every pair within ADR_RATIO_SPREAD of the median; the median is 1 within ADR_RATIO_UNITY → k = 1,
    otherwise k = median, snapped. Loose (two pairs or more): the median within ADR_RATIO_LOOSE of a whole
    number (or reciprocal) k, and at least half of the pairs within ADR_RATIO_LOOSE of k.
    """
    if not ratios:
        return None, "no overlapping EPS period"
    median = statistics.median(ratios)
    low, high = min(ratios), max(ratios)
    if low > 0 and all(abs(ratio - median) <= ADR_RATIO_SPREAD * median for ratio in ratios):
        if abs(median - 1) <= ADR_RATIO_UNITY:
            return 1, None
        return snap_factor(median), None
    whole = whole_factor(median) if low > 0 and len(ratios) >= 2 else None
    if whole is not None:
        close = sum(1 for ratio in ratios if abs(ratio - whole) <= ADR_RATIO_LOOSE * whole)
        if 2 * close >= len(ratios):
            return whole, None
    return None, f"pair ratios {low:.4f} to {high:.4f} over {len(ratios)} EPS values"


def implied_shares(item):
    """Net income ÷ basic (else diluted) EPS of one period, or None."""
    values = item["values"]
    income, eps = values.get("netIncome"), values.get("epsBasic") or values.get("epsDiluted")
    if not income or not eps or income * eps <= 0:
        return None
    return income / eps


def share_count_factor(stored, new):
    """
    No overlapping EPS period: k from the implied share counts of the newest stored period and the oldest
    Yahoo period (annual first, then quarterly) when they are at most SHARE_GAP_DAYS apart, snapped within
    ADR_RATIO_LOOSE; None when that fails.
    """
    for name in ("annual", "quarterly"):
        old = [item for item in stored[name] if implied_shares(item)]
        fresh = [item for item in new[name] if implied_shares(item)]
        if old and fresh:
            older = max(old, key=lambda item: item["end"])
            newer = min(fresh, key=lambda item: item["end"])
            gap = (parse_day(newer["end"]) - parse_day(older["end"])).days
            if gap > SHARE_GAP_DAYS:
                return None        # share counts drift (issuance, buybacks, mergers): too far apart to compare
            return whole_factor(implied_shares(older) / implied_shares(newer))
    return None


def drop_per_share(periods):
    """Stored EPS and share count removed, in place (they cannot be put on Yahoo's basis)."""
    for item in periods:
        for field in PER_SHARE_FIELDS:
            item["values"].pop(field, None)


def scale_eps(periods, factor):
    """Stored EPS × factor and share count ÷ factor, in place (per ordinary share → per ADR, as Yahoo)."""
    for item in periods:
        for field in ("epsBasic", "epsDiluted"):
            if item["values"].get(field) is not None:
                item["values"][field] = clean_value(item["values"][field] * factor)
        if item["values"].get("dilutedShares") is not None:
            item["values"]["dilutedShares"] = clean_value(item["values"]["dilutedShares"] / factor)


def without_per_share(periods):
    """Yahoo periods without their per-share values (EPS, share count); a period left empty is dropped."""
    kept = []
    for item in periods:
        values = {field: value for field, value in item["values"].items() if field not in PER_SHARE_FIELDS}
        if values:
            kept.append(period(item["end"], values))
    return kept


def resolve_eps_basis(stored, new, today_iso):
    """
    Foreign reporter without a valid adrRatio: Yahoo's EPS (per ADR) against the stored EPS of the same
    periods (SEC: per ordinary share). stored / new: {"annual": [periods], "quarterly": [periods]}.
    → (state, adrRatio or None, reason or None, new periods to merge)
      None: nothing to compare (no Yahoo EPS, or no EPS stored yet: nothing is on a second basis); merged
            as usual, judged on a later run once both sides carry EPS.
      "unity": k = 1, adrRatio written, merged as usual.
      "rescaled": stored EPS × k (in place), adrRatio written, merged as usual.
      "replaced": no k (the pairs disagree, or none overlap and the implied share counts give none): the
            stored EPS and share counts are dropped (in place) and Yahoo's are merged, so the file keeps one
            basis, Yahoo's per-ADR EPS that matches the quoted price; adrRatio {"epsFactor": 1, "basis":
            "Yahoo"} settles it (reason says why).
    """
    if not any(has_eps(new[name]) for name in new) or not any(has_eps(stored[name]) for name in stored):
        return None, None, None, new
    ratios = eps_ratios(stored["annual"], new["annual"]) + eps_ratios(stored["quarterly"], new["quarterly"])
    factor, reason = eps_factor(ratios)
    if factor is None and not ratios:
        factor = share_count_factor(stored, new)
        reason = None if factor is not None else "no overlapping EPS period; implied share counts give no ADR ratio"
    if factor is None:
        for items in stored.values():
            drop_per_share(items)
        ratio = {"epsFactor": 1, "basis": "Yahoo", "derivedAt": today_iso, "pairs": len(ratios)}
        return "replaced", ratio, reason, new
    if factor != 1:
        for items in stored.values():
            scale_eps(items, factor)
    ratio = {"epsFactor": clean_ratio(factor), "basis": "ADR", "derivedAt": today_iso, "pairs": len(ratios)}
    return ("unity" if factor == 1 else "rescaled"), ratio, None, new


# ---------------------------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------------------------
def shares_of(item):
    value = item["values"].get("dilutedShares")
    return value if value is not None and value > 0 else None


def share_jump(sections):
    for periods in sections:
        counts = [shares_of(p) for p in sorted(periods, key=lambda p: p["end"]) if shares_of(p)]
        for older, newer in zip(counts, counts[1:]):
            ratio = newer / older
            if ratio >= SHARE_JUMP_UP or ratio <= SHARE_JUMP_DOWN:
                return True
    return False


def merge_events(stored, fetched):
    events = {day: ratio for day, ratio in stored}
    events.update({day: ratio for day, ratio in fetched})
    return [[day, events[day]] for day in sorted(events)]


def adjust_splits(sections, events):
    """
    Put share counts / EPS of periods Yahoo has not restated on the latest basis, in place.

    Walks every period with a share count from the newest back. A period's value was captured at
    some time after its end, so it misses some suffix of the split events after its end: the factor
    (product of such a suffix) that brings its share count closest to the next newer period's count
    is applied when that lands within ±25% and beats leaving it alone. Returns True if anything
    changed.
    """
    usable = sorted(
        (day, ratio) for day, ratio in events
        if ratio >= MIN_SPLIT_RATIO or ratio <= 1 / MIN_SPLIT_RATIO
    )
    if not usable:
        return False
    entries = [p for periods in sections for p in periods if shares_of(p)]
    entries.sort(key=lambda p: p["end"], reverse=True)
    changed = False
    reference = None
    for item in entries:
        shares = shares_of(item)
        if reference is not None:
            after = [ratio for day, ratio in usable if day > item["end"]]
            best, best_distance = 1, abs(math.log(shares / reference))
            factor = 1
            for ratio in reversed(after):
                factor *= ratio
                distance = abs(math.log(shares * factor / reference))
                if distance < best_distance:
                    best, best_distance = factor, distance
            if best != 1 and abs(shares * best / reference - 1) <= SPLIT_MATCH:
                values = item["values"]
                values["dilutedShares"] = clean_value(shares * best)
                for field in ("epsDiluted", "epsBasic"):
                    if values.get(field) is not None:
                        values[field] = round(values[field] / best, 4) + 0.0
                changed = True
        reference = shares_of(item)
    return changed


def split_factors(events, end):
    """Every product of a subset of the split ratios after `end` (without 1), largest first."""
    ratios = [
        ratio for day, ratio in events
        if day > end and (ratio >= MIN_SPLIT_RATIO or ratio <= 1 / MIN_SPLIT_RATIO)
    ]
    products = {1.0}
    for ratio in ratios:
        products |= {factor * ratio for factor in products}
    products.discard(1.0)
    return sorted(products, reverse=True)


def reference_shares(periods, index):
    """Own diluted share count, else the mean of the nearest counts before and after (Q4 has none)."""
    own = shares_of(periods[index])
    if own:
        return own
    near = []
    for step in (-1, 1):
        j = index + step
        if 0 <= j < len(periods) and shares_of(periods[j]):
            near.append(shares_of(periods[j]))
    return sum(near) / len(near) if near else None


def repair_split_eps(sections, events):
    """
    Rewrite SEC EPS that sits on the wrong side of a split, in place. Returns the number of periods.

    companyfacts EPS is "as reported in the latest filing of that period", so neighbouring periods can
    carry different split bases (AAPL 2012-12: 0.0704 next to 0.4931 implied by net income / shares;
    Q4 = annual − Q1..Q3 mixes bases the same way). When EPS differs from net income / shares by a
    factor that matches a product of the split ratios after the period (within REPAIR_MATCH), EPS is
    replaced by net income / shares (basic EPS scaled by the same factor). Anything else (preferred
    dividends, minority interest, share classes) is left alone.
    """
    fixed = 0
    for periods in sections:
        ordered = sorted(periods, key=lambda p: p["end"])
        for index, item in enumerate(ordered):
            if item.get("src") != "sec":
                continue
            values = item["values"]
            eps, income = values.get("epsDiluted"), values.get("netIncome")
            if eps is None or income is None or eps <= 0 or income <= 0:
                continue
            shares = reference_shares(ordered, index)
            if not shares:
                continue
            implied = income / shares
            ratio = eps / implied
            if 1 - SPLIT_MATCH / 2 <= ratio <= 1 + SPLIT_MATCH / 2:
                continue
            for factor in split_factors(events, item["end"]):
                if abs(ratio * factor - 1) <= REPAIR_MATCH or abs(ratio / factor - 1) <= REPAIR_MATCH:
                    new_eps = round(implied, 4) + 0.0
                    basic = values.get("epsBasic")
                    if basic is not None:
                        values["epsBasic"] = round(basic * new_eps / eps, 4) + 0.0
                    values["epsDiluted"] = new_eps
                    fixed += 1
                    break
    return fixed


def repair_document(document):
    """Apply repair_split_eps to a stored document (dict) in place. Returns the number of periods."""
    events = (document.get("split") or {}).get("events") or []
    if not events:
        return 0
    sections = []
    for name in ("annual", "quarterly"):
        block = document.get(name) or {}
        ends = block.get("end") or []
        values = block.get("values") or {}
        sources = block.get("src") or []
        periods = [
            {
                "end": end,
                "src": sources[i] if i < len(sources) else "yahoo",
                "values": {field: (column[i] if i < len(column) else None) for field, column in values.items()},
            }
            for i, end in enumerate(ends)
        ]
        sections.append((block, periods))
    fixed = repair_split_eps([periods for _, periods in sections], events)
    if fixed:
        for block, periods in sections:
            for field in ("epsDiluted", "epsBasic"):
                column = (block.get("values") or {}).get(field)
                if column is None:
                    continue
                for i, item in enumerate(periods):
                    column[i] = item["values"].get(field)
    return fixed


# ---------------------------------------------------------------------------------------------
# Q4 EPS = annual EPS − (Q1 + Q2 + Q3 EPS), shared by seed_financials_sec.py and repair_q4_eps.py
# ---------------------------------------------------------------------------------------------
Q4_EPS_FIELDS = ("epsDiluted", "epsBasic")
Q4_EPS_SOURCE = "sec"           # only SEC periods take a derived Q4 EPS (and only SEC periods are its pieces)
Q4_ISSUE = "q4-derived"
DERIVED_COLUMN = "derived"      # per-period quarterly column: 1 when a value of the period was subtracted
EPS_TOLERANCE = 0.15            # |EPS − net income / diluted shares| / |EPS| above this: the EPS disagrees with its figures
Q4_EPS_ROUNDING = 0.02          # a derived Q4 EPS (four cent-rounded pieces) may also stray this far, absolute


def share_basis(shares, income, eps):
    """A period's share basis for Q4 EPS: its diluted share count, else |net income / EPS|, else None."""
    if shares is not None and shares > 0:
        return shares
    return abs(income / eps) if income and eps else None


def q4_eps(total, pieces, bases):
    """
    Annual EPS − (Q1 + Q2 + Q3 EPS) when all four are reported on one share basis, else None.

    total: the annual EPS; pieces: the Q1..Q3 EPS; bases: the four share bases (share_basis, annual first).
    Every basis must lie within SPLIT_MATCH of the others: a split between the filings mixes bases (an annual
    EPS restated after a split next to Q1..Q3 that are still pre-split).
    """
    if total is None or any(value is None for value in pieces):
        return None
    if any(not basis for basis in bases) or max(bases) / min(bases) > 1 + SPLIT_MATCH:
        return None
    return total - sum(pieces)


def _single(items):
    return items[0] if items and len(items) == 1 else None


def eps_inconsistent(values):
    """True when |epsDiluted − netIncome / dilutedShares| / |epsDiluted| > EPS_TOLERANCE (False if anything is missing)."""
    eps, income, shares = (values.get(name) for name in ("epsDiluted", "netIncome", "dilutedShares"))
    if eps is None or income is None or not shares or eps == 0:
        return False
    return abs(eps - income / shares) / abs(eps) > EPS_TOLERANCE


def fill_q4_eps(annual, quarterly):
    """
    Fill the null Q4 EPS (epsBasic, epsDiluted) of SEC periods, in place, on the basis the periods hold now
    (after any split adjustment). Items are dicts with end, fy, fq, src, values and extra.

    Q4 (fq 4) = the annual period of its fiscal year − its Q1..Q3 (q4_eps), each field on its own. A field is
    filled only when it is null, all four pieces are SEC periods (exactly one per fiscal quarter), and:
    - the annual EPS agrees with its own net income and share count (eps_inconsistent is False): a wrong
      annual EPS (AMST 2022: −56.04 next to −4.67 implied) would carry its error into Q4;
    - the Q4 net income is known and the derived value has its sign;
    - the derived value agrees with the Q4 net income over the annual share basis (within EPS_TOLERANCE, plus
      Q4_EPS_ROUNDING because the pieces are cent-rounded EPS): otherwise the basis or the version is wrong.
    A filled period gets extra["derived"] = 1. Nothing else is derived here: net income, share counts and
    balance-sheet values of a Q4 are never changed.
    → [(end, field)] of the values filled.
    """
    years, quarters = {}, {}
    for item in annual:
        if item.get("src") == Q4_EPS_SOURCE:
            years.setdefault(item.get("fy"), []).append(item)
    for item in quarterly:
        if item.get("src") == Q4_EPS_SOURCE and item.get("fq") in (1, 2, 3, 4):
            quarters.setdefault((item.get("fy"), item["fq"]), []).append(item)
    filled = []
    for (fy, fq), found in quarters.items():
        item = _single(found) if fq == 4 else None
        year = _single(years.get(fy, []))
        pieces = [_single(quarters.get((fy, number), [])) for number in (1, 2, 3)]
        if item is None or year is None:
            continue
        from sec_restatements import BLOCKED, REASON, same_basis
        if any(piece is None for piece in pieces):
            if year.get("extra", {}).get("secFilingBasis", {}).get("restated"):
                item.setdefault("extra", {})[BLOCKED] = "restated-quarterly-eps-inputs-unavailable"
            continue
        if not same_basis(year, [item, *pieces]):
            item.setdefault("extra", {})[BLOCKED] = REASON
            continue
        if eps_inconsistent(year["values"]):
            continue
        income = item["values"].get("netIncome")
        if income is None:
            continue
        for field in Q4_EPS_FIELDS:
            if item["values"].get(field) is not None:
                continue
            bases = [
                share_basis(period["values"].get("dilutedShares"), period["values"].get("netIncome"),
                            period["values"].get(field))
                for period in (year, *pieces)
            ]
            value = q4_eps(year["values"].get(field), [piece["values"].get(field) for piece in pieces], bases)
            if value is None or (value and (value > 0) != (income > 0)):
                continue
            basis = bases[0]
            if abs(value - income / basis) > EPS_TOLERANCE * abs(value) + Q4_EPS_ROUNDING:
                continue
            item["values"][field] = clean_value(value)
            item.setdefault("extra", {})[DERIVED_COLUMN] = 1
            filled.append((item["end"], field))
    return filled


def repair_q4_document(document):
    """
    fill_q4_eps on a stored document (dict) in place: the filled quarterly columns and their derived flag are
    written, and "q4-derived" joins quality.issues (ISSUES order, then the SEC codes sorted, as
    seed_financials_sec.merge_into_document writes them). Only null values change.
    → [(end, field)] filled; the document is untouched when there is nothing to fill.
    """
    sections = {}
    for name in ("annual", "quarterly"):
        block = document.get(name)
        ends = block.get("end") if isinstance(block, dict) else None
        if not isinstance(block, dict) or not isinstance(ends, list):
            return []
        count = len(ends)
        sources = block.get("src") or ["yahoo"] * count
        fys = block.get("fy") or [None] * count
        fqs = block.get("fq") or [None] * count
        columns = block.get("values") or {}
        items = [
            {
                "end": end, "fy": fys[index], "fq": fqs[index], "src": sources[index],
                "extra": {key: block[key][index] for key in ("secFilingBasis", "secQ4EpsBlockedReason")
                          if isinstance(block.get(key), list) and len(block[key]) == count},
                "values": {field: column[index] for field, column in columns.items()},
            }
            for index, end in enumerate(ends)
        ]
        sections[name] = (block, items)
    filled = fill_q4_eps(sections["annual"][1], sections["quarterly"][1])
    if not filled:
        return []
    for name, (block, items) in sections.items():
        index = {item["end"]: position for position, item in enumerate(items)}
        touched = [(index[end], field) for end, field in filled if end in index]
        if name == "quarterly" and touched:
            count = len(items)
            flags = block.get(DERIVED_COLUMN) or [None] * count
            for position in {position for position, _ in touched}:
                flags[position] = 1
            block[DERIVED_COLUMN] = flags
        for position, field in touched:
            columns = block.setdefault("values", {})
            column = columns.get(field) or [None] * len(items)
            column[position] = items[position]["values"][field]
            columns[field] = column
    quality = document.get("quality")
    if not isinstance(quality, dict):
        quality = document["quality"] = {}
    codes = set(quality.get("issues") or []) | {Q4_ISSUE}
    quality["issues"] = [code for code in ISSUES if code in codes] + sorted(
        code for code in codes if code not in ISSUES)
    return filled


# ---------------------------------------------------------------------------------------------
# Fiscal labels, reconciliation, quality
# ---------------------------------------------------------------------------------------------
def effective(end):
    """52/53-week years: a period ending in the first week of a month belongs to the month before."""
    return parse_day(end) - timedelta(days=7)


def fiscal_year(end):
    """FY = year of the period end; ends on Jan 1–7 (52/53-week years) belong to the year before."""
    return effective(end).year


def fy_end_month(ends):
    """Most common fiscal-year-end month (the latest wins ties), or None without annual periods."""
    months = [effective(end).month for end in sorted(ends)]
    if not months:
        return None
    counts = Counter(months)
    return max(reversed(months), key=lambda month: counts[month])


def fiscal_quarter(end, end_month):
    """(fiscal year, quarter 1–4) of a quarter ending on `end` for a fiscal year ending in end_month."""
    day = effective(end)
    offset = (day.month - end_month) % 12
    quarter = round(offset / 3) % 4 or 4
    # Months from this quarter's end to its fiscal year's end, nearest to the nominal distance
    target = (4 - quarter) * 3
    ahead = (end_month - day.month) % 12
    to_year_end = min((ahead, ahead - 12), key=lambda months: abs(months - target))
    index = day.year * 12 + (day.month - 1) + to_year_end
    return index // 12, quarter


def fiscal_label_eligible_ends(annual, quarterly):
    """Quarter ends flagged by the offline fiscal-period detector, plus rows without labels.

    Keeping existing, unflagged rows stable avoids changing fiscal labels in files where the
    detector found no evidence of a fiscal-period mismatch. New rows have no labels yet and are
    included so each ingestion path can label them.
    """
    from detect_fiscal_period_mismatch import analyze

    document = {
        "annual": {
            "end": [item.get("end") for item in annual],
            "fy": [
                item.get("fy") if isinstance(item.get("fy"), int) and not isinstance(item.get("fy"), bool)
                else fiscal_year(item.get("end"))
                for item in annual
            ],
        },
        "quarterly": {
            "end": [item.get("end") for item in quarterly],
            "fy": [item.get("fy") for item in quarterly],
            "fq": [item.get("fq") for item in quarterly],
        },
    }
    result = analyze(document)
    eligible = {
        row["end"]
        for row in result["affectedQ4"] + result["otherQuarterFlags"]
    }
    eligible.update(
        item.get("end") for item in quarterly
        if not isinstance(item.get("fy"), int)
        or isinstance(item.get("fy"), bool)
        or item.get("fq") not in (1, 2, 3, 4)
    )
    return eligible


def assign_fiscal_labels(annual, quarterly, fallback_end_month=None, eligible_quarter_ends=None):
    """Assign annual FYs and quarterly FY/FQs, using each actual annual end as the anchor.

    A quarter belongs to the first annual end on or after it, provided that end is within 371 days.
    Its quarter number comes from the effective month distance to that annual end. Periods after
    the latest annual end continue from that end's month. When there are no annual periods, retain
    the caller's existing month-based (or year-only) behavior.
    """
    anchors = []
    for item in annual:
        item["fy"] = fiscal_year(item["end"])
        day = parse_day(item["end"])
        if day is not None:
            anchors.append((day, item["end"], item["fy"]))
    anchors.sort(key=lambda anchor: anchor[0])

    if not anchors:
        for item in quarterly:
            if fallback_end_month is None:
                item["fy"], item["fq"] = effective(item["end"]).year, None
            else:
                item["fy"], item["fq"] = fiscal_quarter(item["end"], fallback_end_month)
        return

    def month_index(day):
        return day.year * 12 + day.month - 1

    eligible = None if eligible_quarter_ends is None else set(eligible_quarter_ends)
    for item in quarterly:
        if eligible is not None and item.get("end") not in eligible:
            continue
        quarter_day = parse_day(item["end"])
        effective_quarter = effective(item["end"])
        owner = next(
            (anchor for anchor in anchors
             if 0 <= (anchor[0] - quarter_day).days <= 371),
            None,
        )
        if owner is not None:
            annual_day, annual_end, fy = owner
            effective_annual = effective(annual_end)
            months_before = month_index(effective_annual) - month_index(effective_quarter)
            quarter_steps = 0 if months_before <= 0 else max(1, round(months_before / 3))
            fq = max(1, 4 - quarter_steps)
        elif quarter_day > anchors[0][0]:
            # No annual end owns this quarter within 371 days, but an earlier annual does.
            # Continue the nearest prior fiscal calendar forward rather than rounding a short
            # transition period back into that prior year's Q4.
            prior = [anchor for anchor in anchors if anchor[0] < quarter_day]
            _annual_day, annual_end, prior_fy = prior[-1]
            effective_annual = effective(annual_end)
            months_after = month_index(effective_quarter) - month_index(effective_annual)
            months_after = max(1, months_after)
            months_into_year = (months_after - 1) % 12 + 1
            quarter_steps = max(1, round(months_into_year / 3))
            fq = (quarter_steps - 1) % 4 + 1
            fy = prior_fy + (months_after + 11) // 12
        else:
            # Sparse historical annuals can leave old quarters outside the 371-day window.
            # Use the nearest real annual end as the month anchor for those periods.
            prior = [anchor for anchor in anchors if anchor[0] < quarter_day]
            reference = prior[-1] if prior else anchors[0]
            fy, fq = fiscal_quarter(item["end"], effective(reference[1]).month)

        item["fy"], item["fq"] = fy, fq


def reconcile(annual, quarterly):
    """→ issues from comparing the four quarters of a fiscal year with the annual figure (revenue)."""
    issues = set()
    by_year = {}
    for item in quarterly:
        if item["fq"] is None or item["values"].get("revenue") is None:
            continue
        quarters = by_year.setdefault(item["fy"], {})
        quarters[item["fq"]] = None if item["fq"] in quarters else item["values"]["revenue"]
    for item in annual:
        total = item["values"].get("revenue")
        quarters = by_year.get(item["fy"]) or {}
        values = [quarters.get(q) for q in (1, 2, 3, 4)]
        if not total or any(value is None for value in values):
            continue
        if abs(sum(values) - total) > MISMATCH_TOLERANCE * abs(total):
            issues.add("quarter-annual-mismatch")
            increasing = all(a < b for a, b in zip(values, values[1:]))
            if increasing and abs(values[3] - total) <= MISMATCH_TOLERANCE * abs(total):
                issues.add("quarterly-ytd-suspected")
    return issues


def quality(annual, quarterly, flags, foreign):
    issues = set(flags) | reconcile(annual, quarterly)
    # Reviewed restatement metadata survives ordinary Yahoo/SEC refreshes. These
    # describe retained historical periods, not a transient fetch verdict.
    for item in annual + quarterly:
        issues.update(code for code in (item.get("extra", {}).get("secRestatementIssues") or [])
                      if code in {"restated", "mixed-basis", "quarterly-as-reported"})
    recent = annual[-4:] or quarterly[-4:]
    latest = annual[-1] if annual else (quarterly[-1] if quarterly else None)

    def has(item, field):
        return item["values"].get(field) is not None

    if len(annual) < 4:
        issues.add("short-history")
    if recent:
        gross = any(has(p, "grossProfit") or (has(p, "revenue") and has(p, "costOfRevenue")) for p in recent)
        operating = any(has(p, "operatingIncome") or has(p, "operatingIncomeReported") for p in recent)
        if not gross or not operating:
            issues.add("financial-sector-format")
    if latest is not None:
        if not has(latest, "operatingCashFlow"):
            issues.add("missing-cashflow")
        if not has(latest, "equity"):
            issues.add("missing-balance")
    for item in (annual[-1:] + quarterly[-1:]):
        if has(item, "equity") and item["values"]["equity"] <= 0:
            issues.add("negative-equity")
    if foreign:
        issues.add("foreign-currency")
    mismatch = "quarter-annual-mismatch" in issues
    complete = bool(annual) and all(
        has(annual[-1], field) for field in ("revenue", "netIncome", "operatingCashFlow", "equity")
    )
    if len(annual) < 3 or mismatch:
        level = "limited"
    elif len(annual) >= 4 and complete:
        level = "good"
    else:
        level = "partial"
    return {
        "level": level,
        "annualYears": len(annual),
        "quarters": len(quarterly),
        "issues": [code for code in ISSUES if code in issues],
    }


# ---------------------------------------------------------------------------------------------
# One document
# ---------------------------------------------------------------------------------------------
def write_section(periods, extras, quarterly):
    section = {"end": [p["end"] for p in periods], "fy": [p["fy"] for p in periods]}
    if quarterly:
        section["fq"] = [p["fq"] for p in periods]
    if any(p["src"] != "yahoo" for p in periods):
        section["src"] = [p["src"] for p in periods]
    present = {field for p in periods for field, value in p["values"].items() if value is not None}
    order = [field for field in FIELDS if field in present] + sorted(present - set(FIELDS))
    section["values"] = {field: [p["values"].get(field) for p in periods] for field in order}
    for key in sorted({key for p in periods for key in p["extra"]}):
        section[key] = [p["extra"].get(key) for p in periods]
    for key, value in extras.items():
        section.setdefault(key, value)
    return section


def update_document(previous, payload, key, now, fetch_splits=None):
    """
    Merge one Yahoo timeseries payload into the stored document (None for a new stock).
    fetch_splits() → chart payload, called at most once, only when share counts jump.
    Returns (document or None when there is nothing to write, info).
    """
    today = now.date()
    today_iso = today.isoformat()
    market = market_of(key)
    info = {"status": "updated", "restated": 0, "chart": None, "currencyChanged": False, "skipped": 0,
            "epsBasis": None}
    stored = read_sections(previous) if previous is not None else None
    currency = stored["currency"] if stored else None
    parsed = parse_timeseries(payload, today, currency=currency, market=market)
    currency = parsed["currency"]
    changed = bool(stored and stored["currency"] and parsed["detected"] not in (None, currency))
    info["currencyChanged"] = changed
    info["skipped"] = parsed["skipped"]
    if parsed["skipped"] and currency != HOME_CURRENCY[market]:
        # Foreign reporter: Yahoo's values in other currencies (often USD conversions) are left out
        print(f"::notice::{key}: {parsed['skipped']} Yahoo values in another currency skipped "
              f"(statement currency {currency})")
    new_annual = [period(end, values) for end, values in sorted(parsed["annual"].items())]
    new_quarterly = [period(end, values) for end, values in sorted(parsed["quarterly"].items())]
    if not new_annual and not new_quarterly and not changed:
        info["status"] = "empty"
        return None, info
    if stored is None:
        stored = read_sections({"version": VERSION})
    # A foreign reporter stays one: an IFRS 20-F filer may report in USD, which the currency alone does not show
    foreign = currency != HOME_CURRENCY[market] or (isinstance(previous, dict) and previous.get("foreignReporter") is True)
    adr_ratio = None
    if foreign and adr_eps_factor(previous) is None:
        # Yahoo EPS is per ADR; a stored SEC EPS is per ordinary share. Settled once, before the merge
        state, adr_ratio, reason, merged = resolve_eps_basis(
            {"annual": stored["annual"], "quarterly": stored["quarterly"]},
            {"annual": dedupe(new_annual), "quarterly": dedupe(new_quarterly)},
            today_iso,
        )
        info["epsBasis"] = state
        if reason:
            print(f"::notice::{key}: EPS basis: SEC EPS dropped, Yahoo EPS kept ({reason})")
        new_annual, new_quarterly = merged["annual"], merged["quarterly"]

    renamed = {"annual": {}, "quarterly": {}}
    filled = []
    annual, revised_a = merge_section(stored["annual"], new_annual, "annual", today_iso, renamed["annual"], filled)
    quarterly, revised_q = merge_section(
        stored["quarterly"], new_quarterly, "quarterly", today_iso, renamed["quarterly"], filled
    )
    info["restated"] = sum(1 for r in revised_a + revised_q if r["kind"] == "restated")

    events = stored["events"]
    adjusted = adjust_splits([annual, quarterly], events)
    if fetch_splits is not None and share_jump([annual, quarterly]):
        try:
            fetched = parse_splits(fetch_splits())
            info["chart"] = "fetched"
        except Exception as error:  # noqa: BLE001 - leave the data as Yahoo sent it
            info["chart"] = "failed"
            print(f"{key}: split events unavailable: {error}", file=sys.stderr)
        else:
            events = merge_events(events, fetched)
            adjusted = adjust_splits([annual, quarterly], events) or adjusted
    adjusted = repair_split_eps([annual, quarterly], events) > 0 or adjusted
    adjusted = adjusted or stored["adjusted"]

    end_month = fy_end_month([p["end"] for p in annual])
    if end_month is None and market == "TW":
        end_month = 12     # Taiwan companies report on the calendar year
    eligible_ends = fiscal_label_eligible_ends(annual, quarterly) if market == "US" else None
    assign_fiscal_labels(
        annual, quarterly, fallback_end_month=end_month, eligible_quarter_ends=eligible_ends,
    )

    revisions = merge_revisions(stored["revisions"], revised_a + revised_q, annual, quarterly, renamed)
    flags = {r["kind"] for r in revisions}
    if parsed["capexSign"]:
        flags.add("capex-sign")
    if changed:
        flags.add("currency-changed")
    if adjusted:
        flags.add("split-adjusted")
    # The stored adrRatio stays in the file (carried below); its rescale is a quality note while it is there
    factor = adr_ratio["epsFactor"] if adr_ratio is not None else adr_eps_factor(previous)
    if factor not in (None, 1):
        flags.add("adr-eps-rescaled")
    settled = adr_ratio if adr_ratio is not None else (previous or {}).get("adrRatio")
    if isinstance(settled, dict) and settled.get("basis") == "Yahoo":
        flags.add("eps-yahoo-only")
    grade = quality(annual, quarterly, flags, foreign)
    # IFRS / US GAAP codes of the SEC periods (sec_ifrs.py) stay while those periods are in the file
    if isinstance(previous, dict) and isinstance(previous.get("quality"), dict):
        carried = previous["quality"].get("issues")
        if isinstance(carried, list):
            codes = SEC_FOREIGN_ISSUES + (FINMIND_ISSUES if any(p["src"] == "finmind" for p in annual + quarterly) else ())
            grade["issues"] += sorted(code for code in codes if code in carried and code not in grade["issues"])
    if revisions:
        grade["revisions"] = revisions
    document = {
        "version": VERSION,
        "symbol": key,
        "market": market,
        "currency": currency,
        "foreignReporter": foreign,
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "checkedAt": today_iso,
        "source": source_label(annual + quarterly, filled),
        "fyEndMonth": end_month or 12,
        "annual": write_section(annual, stored["extras"]["annual"], False),
        "quarterly": write_section(quarterly, stored["extras"]["quarterly"], True),
        "split": {"adjusted": adjusted, "events": events},
        "quality": grade,
    }
    for name, value in stored["other"].items():
        document.setdefault(name, value)
    if adr_ratio is not None:
        document["adrRatio"] = adr_ratio
    if changed:
        info["status"] = "currency-changed"
        print(f"::warning::{key}: Yahoo now reports in {parsed['detected']}, file kept in {currency}")
    return document, info


# ---------------------------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------------------------
def is_tw_etf(key):
    # Taiwan ETF / ETN codes start with 00 and have no financial statements
    return key.endswith(TW_SUFFIXES) and key.startswith("00")


def nested_symbols(document, outer, inner):
    """latest.json groups[].items[].symbol / themes.json themes[].constituents[].symbol."""
    entries = document.get(outer) if isinstance(document, dict) else None
    symbols = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get(inner), list):
            # latest.json rows with status "failed" have no price and maybe a wrong suffix: skip them
            symbols += [
                item.get("symbol") for item in entry[inner] if isinstance(item, dict) and item.get("status") != "failed"
            ]
    return symbols


def universe_rows(document, market):
    """[(symbol, turnover20 or None)] of one market of universe.json."""
    if not isinstance(document, dict):
        return []
    fields = document.get("fields") if isinstance(document.get("fields"), list) else []
    symbol_at = fields.index("symbol") if "symbol" in fields else 0
    turnover_at = fields.index("turnover20") if "turnover20" in fields else None
    rows = ((document.get("markets") or {}).get(market) or {}).get("rows") or []
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, list) or len(row) <= symbol_at:
            continue
        turnover = None
        if turnover_at is not None and len(row) > turnover_at:
            turnover = finite_number(row[turnover_at])
        result.append((row[symbol_at], turnover))
    return result


def csv_turnover(path, bars=20):
    """Average close × volume of the last `bars` rows of a daily CSV, or None."""
    try:
        with open(path, encoding="utf-8") as file:
            lines = file.read().strip().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if len(lines) < 2:
        return None
    header = [name.strip().lower() for name in lines[0].split(",")]
    if "close" not in header or "volume" not in header:
        return None
    close_at, volume_at = header.index("close"), header.index("volume")
    values = []
    for line in lines[1:][-bars:]:
        cells = line.split(",")
        try:
            values.append(float(cells[close_at]) * float(cells[volume_at]))
        except (IndexError, ValueError):
            continue
    values = [value for value in values if math.isfinite(value)]
    return sum(values) / len(values) if values else None


def csv_symbols(data_dir):
    try:
        names = sorted(os.listdir(data_dir))
    except OSError:
        return [], []
    us, tw = [], []
    for name in names:
        if not name.endswith(".csv"):
            continue
        key = financials_key(name[:-4])
        if key is None:
            continue
        if market_of(key) == "TW":
            tw.append((key, csv_turnover(os.path.join(data_dir, name))))
        else:
            us.append(key)
    return us, tw


def top_turnover(rows, limit):
    rows = [(key, turnover) for key, turnover in rows if key and not is_tw_etf(key)]
    rows.sort(key=lambda row: -(row[1] if row[1] is not None else -math.inf))
    return [key for key, _ in rows[:limit]]


def collect_symbols(report_dir, data_dir, tw_limit=TW_LIMIT):
    """Keys in priority order: report groups, themes, universe US, universe TW by turnover, CSVs."""
    universe = read_json(os.path.join(report_dir, "universe.json"))
    us_rows = [financials_key(symbol) for symbol, _ in universe_rows(universe, "US")]
    tw_rows = [(financials_key(symbol), turnover) for symbol, turnover in universe_rows(universe, "TW")]
    us_rows = [key for key in us_rows if key and market_of(key) == "US"]
    # The full-market list (scope "full", every US common stock) is not this script's coverage: the US
    # CSVs in data/ (S&P 500 + custom stocks) stand in for it. Taiwan keeps the top TW_LIMIT by turnover.
    if (((universe or {}).get("markets") or {}).get("US") or {}).get("scope") == "full":
        us_rows = []
    tw_rows = [(key, turnover) for key, turnover in tw_rows if key and market_of(key) == "TW"]
    candidates = (
        nested_symbols(read_json(os.path.join(report_dir, "latest.json")), "groups", "items")
        + nested_symbols(read_json(os.path.join(report_dir, "themes.json")), "themes", "constituents")
        + us_rows
    )
    if not us_rows or not tw_rows:
        csv_us, csv_tw = csv_symbols(data_dir)
        if not us_rows:
            candidates += csv_us
        if not tw_rows:
            tw_rows = csv_tw
    candidates += top_turnover(tw_rows, tw_limit)
    result, seen = [], set()
    for symbol in candidates:
        key = financials_key(symbol)
        if key and key not in seen and not is_tw_etf(key):
            seen.add(key)
            result.append(key)
    return result


def universe_us_keys(report_dir):
    """Keys of the US rows of universe.json (the full US list when its scope is "full"), in file order."""
    universe = read_json(os.path.join(report_dir, "universe.json"))
    keys = [financials_key(symbol) for symbol, _ in universe_rows(universe, "US")]
    return [key for key in dict.fromkeys(keys) if key and market_of(key) == "US"]


def foreign_filer(document):
    """Foreign issuer (ADR, 20-F / 40-F) by its stored file: foreignReporter, or the SEC "foreign-filer" code."""
    if not isinstance(document, dict):
        return False
    quality = document.get("quality") if isinstance(document.get("quality"), dict) else {}
    issues = quality.get("issues")
    return document.get("foreignReporter") is True or (isinstance(issues, list) and "foreign-filer" in issues)


def foreign_issuers(universe, documents):
    """US keys of the full universe that are foreign issuers: a stored file says so, or FOREIGN_MANUAL."""
    flagged = {key for key, document in documents.items() if foreign_filer(document)}
    return [key for key in universe if key in flagged or key in FOREIGN_MANUAL]


def interleave(core, extra, share):
    """
    Order two queues: `extra` takes about `share` of the slots while `core` still has entries, the rest
    of its slots go to `core` in order, and once one queue is empty the other fills the remaining slots.
    """
    order, taken = [], 0
    i = j = 0
    while i < len(core) or j < len(extra):
        if j < len(extra) and (i >= len(core) or taken + 1 <= share * (len(order) + 1)):
            order.append(extra[j])
            j += 1
            taken += 1
        else:
            order.append(core[i])
            i += 1
    return order


# ---------------------------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------------------------
def needs_refresh(state, today, miss=None):
    """state: index item ({latestQuarter, latestAnnual, checkedAt}) or None when there is no file."""
    if not state:
        if miss is None:
            return True
        missed = parse_day(miss)
        return missed is None or (today - missed).days >= MISS_RETRY_DAYS
    checked = parse_day(state.get("checkedAt"))
    if checked is None:
        return True
    age = (today - checked).days
    if age > MAX_CHECK_AGE_DAYS:
        return True
    latest = parse_day(state.get("latestQuarter")) or parse_day(state.get("latestAnnual"))
    due = latest is None or (today - latest).days > REPORT_DUE_DAYS
    return due and age > RECHECK_DAYS


def index_item(document, checked=None, last_listed=None):
    annual = (document.get("annual") or {}).get("end") or []
    quarterly = (document.get("quarterly") or {}).get("end") or []
    checked_at = document.get("checkedAt") if isinstance(document.get("checkedAt"), str) else None
    if isinstance(checked, str) and (checked_at is None or checked > checked_at):
        checked_at = checked
    return {
        "market": document.get("market") or None,
        "currency": document.get("currency") or None,
        "latestAnnual": annual[-1] if annual else None,
        "latestQuarter": quarterly[-1] if quarterly else None,
        "checkedAt": checked_at,
        "level": (document.get("quality") or {}).get("level"),
        "lastListed": last_listed,
    }


def same_content(stored, document):
    """Equal apart from the run's timestamps."""
    def strip(value):
        return {key: item for key, item in value.items() if key not in ("generatedAt", "checkedAt")}
    return json.dumps(strip(stored), sort_keys=True) == json.dumps(strip(document), sort_keys=True)


def stored_keys(out_dir):
    try:
        names = os.listdir(out_dir)
    except OSError:
        return []
    keys = []
    for name in names:
        if name.endswith(".json.tmp"):
            # Left over by an interrupted write; the .json beside it is intact
            try:
                os.remove(os.path.join(out_dir, name))
            except OSError:
                pass
        elif name.endswith(".json") and name != "index.json":
            keys.append(name[:-5])
    return sorted(keys)


def run_refresh(symbols, out_dir, fetch, now, budget_seconds, fetch_chart=None,
                delay=REQUEST_DELAY, sleep=time.sleep, clock=time.monotonic, universe=()):
    """
    symbols: keys in priority order; fetch(key) → timeseries payload, fetch_chart(key) → chart payload
    (both raise on failure). Writes <out_dir>/<KEY>.json as it goes and index.json at the end.
    universe: US keys of the full-market list. Its foreign issuers that are not in symbols are added
    after them, in their own lane (FOREIGN_SHARE of the requests while core keys are due).
    Returns stats; stats["failed"] is True when consecutive failures stopped the run.
    """
    today = now.date()
    today_iso = today.isoformat()
    index = read_json(os.path.join(out_dir, "index.json"))
    index = index if isinstance(index, dict) and index.get("version") == VERSION else {}
    previous_items = {
        key: item for key, item in (index.get("items") or {}).items() if isinstance(item, dict)
    } if isinstance(index.get("items"), dict) else {}
    previous_misses = index.get("misses") if isinstance(index.get("misses"), dict) else {}
    previous_failures = index.get("failures") if isinstance(index.get("failures"), dict) else {}

    documents, corrupt = {}, []
    for key in stored_keys(out_dir):
        status, value = load_document(os.path.join(out_dir, key + ".json"), key)
        if status == "ok":
            documents[key] = value
        elif status == "corrupt":
            corrupt.append(key)
            print(f"::warning::{key}.json cannot be merged into ({value}); left untouched")

    listed = []
    for symbol in symbols:
        if symbol not in listed:
            listed.append(symbol)
    # Foreign issuers outside the core list come after it (priority) and in a lane of their own
    core_set = set(listed)
    foreign = [key for key in foreign_issuers(universe, documents) if key not in core_set]
    foreign_set = set(foreign)
    listed += foreign
    priority = {key: position for position, key in enumerate(listed)}
    listed_set = set(listed)

    items = {
        key: index_item(document, (previous_items.get(key) or {}).get("checkedAt"))
        for key, document in documents.items()
    }
    misses = {
        key: day for key, day in previous_misses.items()
        if key in listed_set and key not in documents and isinstance(day, str)
    }
    # Stocks whose request or payload failed on an earlier run (HTTP 404, a payload that does not parse…)
    # go last, so a handful of them can never use up the consecutive-failure limit before anything else
    failed = {
        key: day for key, day in previous_failures.items()
        if key in listed_set and key not in corrupt and isinstance(day, str)
    }
    pending = [
        key for key in listed
        if key not in corrupt and needs_refresh(items.get(key), today, misses.get(key))
    ]
    # Never fetched first (priority order), then the oldest check; earlier failures after everything else
    pending.sort(key=lambda key: (
        key in failed,
        key in items or key in misses,
        (items.get(key) or {}).get("checkedAt") or misses.get(key) or "",
        priority[key],
    ))
    # Core keys keep their order; foreign issuers take at most FOREIGN_SHARE of the slots while core is due
    foreign_due = [key for key in pending if key in foreign_set]
    order = interleave([key for key in pending if key not in foreign_set], foreign_due, FOREIGN_SHARE)

    stats = {
        "symbols": len(listed), "pending": len(pending), "requested": 0, "updated": 0, "unchanged": 0,
        "restated": 0,
        "splitChecks": 0, "misses": 0, "stopped": None, "failed": False, "corrupt": corrupt,
        "errors": [], "deleted": [],
        "foreign": {"listed": len(foreign), "due": len(foreign_due), "requested": 0, "updated": 0, "errors": 0},
        "epsBasis": {"unity": 0, "rescaled": 0, "replaced": 0},
    }
    foreign_stats = stats["foreign"]
    deadline = clock() + budget_seconds
    failures = 0
    for key in order:
        if clock() > deadline:
            stats["stopped"] = "time budget"
            break
        try:
            payload = fetch(key)
            failures = 0
        except Exception as error:  # noqa: BLE001 - keep the stored file
            failed[key] = today_iso
            failures += 1
            if key in foreign_set:
                foreign_stats["errors"] += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                stats["stopped"] = f"{failures} consecutive failures ({error})"
                stats["failed"] = True
                break
            continue
        finally:
            stats["requested"] += 1
            if key in foreign_set:
                foreign_stats["requested"] += 1
            sleep(delay)

        def fetch_splits(key=key):
            stats["splitChecks"] += 1
            try:
                return fetch_chart(key)
            finally:
                stats["requested"] += 1
                sleep(delay)

        try:
            document, info = update_document(
                documents.get(key), payload, key, now, fetch_splits if fetch_chart else None
            )
        except Exception as error:  # noqa: BLE001 - one bad payload must not stop the run
            failed[key] = today_iso
            stats["errors"].append(key)
            if key in foreign_set:
                foreign_stats["errors"] += 1
            print(f"::warning::{key}: not updated ({type(error).__name__}: {error})")
            continue
        failed.pop(key, None)
        if info["epsBasis"]:
            stats["epsBasis"][info["epsBasis"]] += 1
        if document is None:
            if key in documents:
                items[key]["checkedAt"] = today_iso
            else:
                misses[key] = today_iso
            continue
        misses.pop(key, None)
        if key in documents and same_content(documents[key], document):
            # Nothing new: keep the file (and git history) as is; index.json records the check
            items[key]["checkedAt"] = today_iso
            stats["unchanged"] += 1
            continue
        write_atomic(os.path.join(out_dir, key + ".json"), document)
        documents[key] = document
        items[key] = index_item(document)
        stats["updated"] += 1
        stats["restated"] += info["restated"]
        if key in foreign_set:
            foreign_stats["updated"] += 1

    # Foreign-lane totals for the Actions log
    print(
        f"Foreign issuers outside the core list: {foreign_stats['listed']} listed, {foreign_stats['due']} due, "
        f"{foreign_stats['requested']} requests, {foreign_stats['updated']} files written, "
        f"{foreign_stats['errors']} errors",
        flush=True,
    )
    # Index and retention of stocks that are no longer listed
    final = {}
    for key in sorted(set(documents) | set(corrupt)):
        previous = previous_items.get(key) or {}
        if key in listed_set:
            last_listed = today_iso
        else:
            last_listed = previous.get("lastListed") if parse_day(previous.get("lastListed")) else today_iso
            # Delisting changes refresh eligibility, never permanent retention.
        if key in documents:
            item = dict(items[key])
            item["lastListed"] = last_listed
            if isinstance(previous.get(SEC_CHECKED_KEY), str):
                item[SEC_CHECKED_KEY] = previous[SEC_CHECKED_KEY]
            final[key] = item
        elif previous:
            final[key] = dict(previous, lastListed=last_listed)
    stats["misses"] = len(misses)
    write_atomic(os.path.join(out_dir, "index.json"), {
        "version": VERSION,
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "items": final,
        "misses": dict(sorted(misses.items())),
        "failures": dict(sorted(failed.items())),
    })
    return stats


CONNECT_DELAYS = (30, 60, 120)


def connect_yahoo(factory=None, sleep=None, delays=CONNECT_DELAYS):
    """Yahoo() with a few patient retries: the crumb request is the first one rate limited (HTTP 429)."""
    factory = factory or Yahoo
    sleep = sleep or time.sleep
    for number in range(len(delays) + 1):
        try:
            return factory()
        except Exception as error:  # noqa: BLE001 - the message is a short status line, never a URL
            if number == len(delays):
                raise
            print(f"Yahoo connect attempt {number + 1} failed ({error}); retrying in {delays[number]} s", flush=True)
            sleep(delays[number])


def repair_all(out_dir):
    """Offline: repair_document on every stored file, no network. Returns (files changed, periods fixed)."""
    files = periods = 0
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith(".json") or name == "index.json":
            continue
        path = os.path.join(out_dir, name)
        document = read_json(path)
        if not isinstance(document, dict):
            continue
        fixed = repair_document(document)
        if fixed:
            write_atomic(path, document)
            files += 1
            periods += fixed
            print(f"{name}: {fixed} EPS values rewritten")
    return files, periods


def main():
    if "--repair" in sys.argv[1:]:
        report_dir = os.environ.get("ATLAS_REPORT_DIR") or "../docs/report"
        out_dir = os.environ.get("ATLAS_FINANCIALS_DIR") or os.path.join(report_dir, "financials")
        files, periods = repair_all(out_dir)
        print(json.dumps({"repairedFiles": files, "repairedPeriods": periods}))
        return
    report_dir = os.environ.get("ATLAS_REPORT_DIR") or "../docs/report"
    data_dir = os.environ.get("ATLAS_DATA_DIR") or "../data"
    out_dir = os.environ.get("ATLAS_FINANCIALS_DIR") or os.path.join(report_dir, "financials")

    symbols = collect_symbols(report_dir, data_dir)
    if not symbols:
        raise RuntimeError("no symbols found")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    period2 = int(now.timestamp())
    period1 = int((now - timedelta(days=HISTORY_YEARS * 366)).timestamp())
    yahoo = connect_yahoo()

    def fetch(key):
        return yahoo.get_json(
            TIMESERIES_URL.format(symbol=key),
            {"symbol": key, "type": REQUEST_TYPES, "period1": period1, "period2": period2},
        )

    def fetch_chart(key):
        return yahoo.get_json(CHART_URL.format(symbol=key), {"events": "split", "range": "max", "interval": "1mo"})

    stats = run_refresh(
        symbols, out_dir, fetch, now, DEFAULT_BUDGET_SECONDS, fetch_chart=fetch_chart, sleep=time.sleep,
        universe=universe_us_keys(report_dir),
    )
    print(json.dumps(stats, ensure_ascii=False))
    if stats["failed"]:
        raise RuntimeError(f"financials refresh stopped early: {stats['stopped']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"::warning::Financial statement history not fully rebuilt (files already written kept): {error}")
        sys.exit(1)

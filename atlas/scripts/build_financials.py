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
- One timeseries request per stock (annual + quarterly types together). Yahoo only returns ~4 years
  and ~5–8 quarters, so every run merges into the stored file and history accumulates:
  * period ends within ±5 days are the same period (the newer date wins); a new non-null value
    replaces the stored one, a missing one keeps it; revenue / net income moving > 2% is recorded
    as a restatement (quality.revisions, issue "restated");
  * annual keeps only 12M points, quarterly only 3M points (TTM / 6M / 9M are dropped); points in
    another currency than the statement currency are dropped (share counts excepted);
  * periods written by seed_financials_sec.py (src "sec") are never replaced or removed: Yahoo only
    fills their null fields, a > 2% revenue / net income difference is recorded as "yahoo-differs",
    and Yahoo periods are only added after the newest SEC period;
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
    "quarterly-ytd-suspected", "negative-equity", "capex-sign", "currency-changed",
)
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
    parsed = {"currency": chosen, "detected": detected, "annual": {}, "quarterly": {}, "capexSign": False}
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
    elif not isinstance(sources, list) or len(sources) != count or any(s not in ("sec", "yahoo") for s in sources):
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


def merge_section(old, new, frequency, today_iso, renamed=None):
    """
    Merge new Yahoo periods into stored ones → (periods ascending, revisions).
    renamed (dict, optional) receives {old end: new end} for periods whose end date moved.
    """
    keep = KEEP_ANNUAL if frequency == "annual" else KEEP_QUARTERLY
    periods = [period(p["end"], p["values"], p["src"], p["extra"]) for p in old]
    new = dedupe(new)
    sec_ends = [p["end"] for p in periods if p["src"] == "sec"]
    latest_sec = max(sec_ends) if sec_ends else None
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
    revisions = []
    original = len(periods)
    for j, item in enumerate(new):
        i = match.get(j)
        if i is None:
            near = any(
                abs(days_between(periods[k]["end"], item["end"])) <= SAME_PERIOD_DAYS for k in range(original)
            )
            # Never squeeze a Yahoo period into the SEC history
            if not near and (latest_sec is None or item["end"] > latest_sec):
                periods.append(period(item["end"], item["values"]))
            continue
        target = periods[i]
        if target["src"] == "sec":
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
    # Drop the oldest periods beyond the cap, but never SEC ones (nor anything newer than them)
    while len(periods) > keep and periods[0]["src"] != "sec":
        del periods[0]
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
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    result.sort(key=lambda r: (str(r.get("seen")), r["period"], r["end"], str(r.get("field")), r["kind"]))
    return result[-KEEP_REVISIONS:]


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
    if any(p["src"] == "sec" for p in periods):
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
    info = {"status": "updated", "restated": 0, "chart": None, "currencyChanged": False}
    stored = read_sections(previous) if previous is not None else None
    currency = stored["currency"] if stored else None
    parsed = parse_timeseries(payload, today, currency=currency, market=market)
    currency = parsed["currency"]
    changed = bool(stored and stored["currency"] and parsed["detected"] not in (None, currency))
    info["currencyChanged"] = changed
    new_annual = [period(end, values) for end, values in sorted(parsed["annual"].items())]
    new_quarterly = [period(end, values) for end, values in sorted(parsed["quarterly"].items())]
    if not new_annual and not new_quarterly and not changed:
        info["status"] = "empty"
        return None, info
    if stored is None:
        stored = read_sections({"version": VERSION})

    renamed = {"annual": {}, "quarterly": {}}
    annual, revised_a = merge_section(stored["annual"], new_annual, "annual", today_iso, renamed["annual"])
    quarterly, revised_q = merge_section(
        stored["quarterly"], new_quarterly, "quarterly", today_iso, renamed["quarterly"]
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
    adjusted = adjusted or stored["adjusted"]

    end_month = fy_end_month([p["end"] for p in annual])
    if end_month is None and market == "TW":
        end_month = 12     # Taiwan companies report on the calendar year
    for item in annual:
        item["fy"] = fiscal_year(item["end"])
    for item in quarterly:
        if end_month is None:
            item["fy"], item["fq"] = effective(item["end"]).year, None
        else:
            item["fy"], item["fq"] = fiscal_quarter(item["end"], end_month)

    revisions = merge_revisions(stored["revisions"], revised_a + revised_q, annual, quarterly, renamed)
    flags = {r["kind"] for r in revisions}
    if parsed["capexSign"]:
        flags.add("capex-sign")
    if changed:
        flags.add("currency-changed")
    if adjusted:
        flags.add("split-adjusted")
    foreign = currency != HOME_CURRENCY[market]
    grade = quality(annual, quarterly, flags, foreign)
    if revisions:
        grade["revisions"] = revisions
    mixed = any(p["src"] == "sec" for p in annual + quarterly)
    document = {
        "version": VERSION,
        "symbol": key,
        "market": market,
        "currency": currency,
        "foreignReporter": foreign,
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "checkedAt": today_iso,
        "source": SOURCE_MIXED if mixed else SOURCE,
        "fyEndMonth": end_month or 12,
        "annual": write_section(annual, stored["extras"]["annual"], False),
        "quarterly": write_section(quarterly, stored["extras"]["quarterly"], True),
        "split": {"adjusted": adjusted, "events": events},
        "quality": grade,
    }
    for name, value in stored["other"].items():
        document.setdefault(name, value)
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
            symbols += [item.get("symbol") for item in entry[inner] if isinstance(item, dict)]
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
                delay=REQUEST_DELAY, sleep=time.sleep, clock=time.monotonic):
    """
    symbols: keys in priority order; fetch(key) → timeseries payload, fetch_chart(key) → chart payload
    (both raise on failure). Writes <out_dir>/<KEY>.json as it goes and index.json at the end.
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

    listed = []
    for symbol in symbols:
        if symbol not in listed:
            listed.append(symbol)
    priority = {key: position for position, key in enumerate(listed)}
    listed_set = set(listed)

    documents, corrupt = {}, []
    for key in stored_keys(out_dir):
        status, value = load_document(os.path.join(out_dir, key + ".json"), key)
        if status == "ok":
            documents[key] = value
        elif status == "corrupt":
            corrupt.append(key)
            print(f"::warning::{key}.json cannot be merged into ({value}); left untouched")

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

    stats = {
        "symbols": len(listed), "pending": len(pending), "requested": 0, "updated": 0, "unchanged": 0,
        "restated": 0,
        "splitChecks": 0, "misses": 0, "stopped": None, "failed": False, "corrupt": corrupt,
        "errors": [], "deleted": [],
    }
    deadline = clock() + budget_seconds
    failures = 0
    for key in pending:
        if clock() > deadline:
            stats["stopped"] = "time budget"
            break
        try:
            payload = fetch(key)
            failures = 0
        except Exception as error:  # noqa: BLE001 - keep the stored file
            failed[key] = today_iso
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                stats["stopped"] = f"{failures} consecutive failures ({error})"
                stats["failed"] = True
                break
            continue
        finally:
            stats["requested"] += 1
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
            print(f"::warning::{key}: not updated ({type(error).__name__}: {error})")
            continue
        failed.pop(key, None)
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

    # Index and retention of stocks that are no longer listed
    final = {}
    for key in sorted(set(documents) | set(corrupt)):
        previous = previous_items.get(key) or {}
        if key in listed_set:
            last_listed = today_iso
        else:
            last_listed = previous.get("lastListed") if parse_day(previous.get("lastListed")) else today_iso
            gone = (today - parse_day(last_listed)).days > KEEP_UNLISTED_DAYS
            if gone and key in documents and not has_sec(documents[key]):
                try:
                    os.remove(os.path.join(out_dir, key + ".json"))
                    stats["deleted"].append(key)
                    continue
                except OSError as error:
                    print(f"::warning::{key}.json not deleted: {error}")
        if key in documents:
            item = dict(items[key])
            item["lastListed"] = last_listed
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


def main():
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
        symbols, out_dir, fetch, now, DEFAULT_BUDGET_SECONDS, fetch_chart=fetch_chart, sleep=time.sleep
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

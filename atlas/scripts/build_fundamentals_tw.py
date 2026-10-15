"""
Builds docs/report/fundamentals/tw.json: year-to-date revenue, basic EPS and net income attributable to
the parent of every Taiwan listed / OTC company per quarter (official statements), from which
build_growth.py derives the quarterly and annual EPS / revenue YoY (src "mops").

  python atlas/scripts/build_fundamentals_tw.py [--budget 600] [--no-mops] [--no-openapi]
         [--seasons 2025Q4,2026Q1] [--report-dir docs/report]
  Env: TW_FUNDAMENTALS_BUDGET_SECONDS (default 600): time allowed for the MOPS requests of one run.

Sources (both official; numbers are year-to-date (累計), revenue / net income in NT$ thousand, EPS in NT$):
- 公開資訊觀測站 綜合損益表彙總報表 (MOPS t163sb04): one POST per market (TYPEK sii 上市 / otc 上櫃 /
  rotc 興櫃) and season returns every company, in one table per statement format (一般業, 銀行業,
  證券業, 金控業, 保險業, 異業). History only has to be fetched once: a season counts as final once it
  was fetched after FINAL_DATES (the filing deadline + about a month); seasons that are not final are
  re-fetched every run (at most once a day). rotc (興櫃) is fetched for final seasons only: it gives the
  pre-listing history of companies listed since. MOPS may refuse frequent requests or answer with a
  security page: requests are MOPS_DELAY s apart (+ jitter), a refusal waits BLOCK_WAIT s and retries
  once, a second one ends the MOPS part of the run; unfinished seasons are fetched by the next run.
- 證交所 / 櫃買中心 OpenAPI t187ap06_L_* / mopsfin_t187ap06_O_* (latest season only, per format): fills
  the companies MOPS has no row for in that season (flagged). When both sources have a season the
  revenues of the overlapping companies must agree (median within 2%), else OpenAPI is not used.
Columns (header names normalised: no spaces, half-width brackets): code 公司代號; EPS 基本每股盈餘(元);
revenue by format: 營業收入 > 淨收益 > 收益 > 收入 > 利息淨收益 + 利息以外淨損益 (the kind is stored;
quarters and YoY pairs only combine one kind); net income 淨利(淨損)歸屬於母公司業主 (or another
「…歸屬於母公司業主」 net income column), else 本期淨利(淨損) / 本期稅後淨利(淨損).

Quarters (growth_candidates): Q1 = Q1 YTD; Qn = YTD(n) - YTD(n-1) (Q4 = full year - first three
quarters, flagged derived). EPS of a derived quarter is put on the share basis of the later YTD figure:
(NI(n) - NI(n-1)) x EPS(n) / NI(n), so a stock dividend between the two reports (the later EPS is
restated for it, the earlier one is not) does not distort it; without usable net income (|EPS| < 0.1, a
different sign or no column) it is EPS(n) - EPS(n-1). Revenue of a quarter must be >= 0. Annual = the
Q4 YTD figures. The share basis of a pair is the implied share count (NI / EPS of the YTD figures): a
change >= 1.5x (capital reduction, large stock dividend) nulls the EPS YoY. Smaller stock dividends
are NOT restated: the EPS YoY of such a pair is lower than the company's own restated comparison.

Store (compact): {"version": 1, "source", "units", "fields": ["rev", "eps", "ni"], "seasons": [...],
  "fetch": {season: {typek|"openapi": {"src", "at", "n", "final"}}}, "data": {code: [entry|null per season]}}
  entry = [rev, eps, ni] or [rev, eps, ni, flags]: flags bit 0 = from OpenAPI, bits 1-3 = revenue kind.
Kept: the last RECENT_SEASONS seasons and the Q4 of the last ANNUAL_YEARS years, every code (興櫃 history
included: the year-ago figures of companies listed since). The file is only rewritten when it changed,
and never when the stored one cannot be read (fix or delete it by hand).
Exit codes: 0 done (also when a source was skipped), 1 the stored file is unreadable or nothing worked.
"""
import argparse
import json
import math
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from html.parser import HTMLParser

import build_growth as growth
from build_valuation_us import read_json, write_atomic

VERSION = 1
SOURCE = "公開資訊觀測站 綜合損益表彙總報表 (t163sb04) + 證交所/櫃買中心 OpenAPI t187ap06"
UNITS = "rev, ni: NT$ thousand; eps: NT$; year-to-date"
STORE_FIELDS = ["rev", "eps", "ni"]
MOPS_URLS = (
    "https://mopsov.twse.com.tw/mops/web/ajax_t163sb04",
    "https://mops.twse.com.tw/mops/web/ajax_t163sb04",
)
MOPS_REFERER = "https://mopsov.twse.com.tw/mops/web/t163sb04"
LISTED_TYPEKS = ("sii", "otc")
HISTORY_TYPEK = "rotc"
OPENAPI = (
    ("sii", "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"),
    ("sii", "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_basi"),
    ("sii", "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_bd"),
    ("sii", "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_fh"),
    ("sii", "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ins"),
    ("sii", "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_mim"),
    ("otc", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci"),
    ("otc", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_basi"),
    ("otc", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_bd"),
    ("otc", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_fh"),
    ("otc", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ins"),
    ("otc", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_mim"),
)
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
MOPS_DELAY = 8.0
MOPS_JITTER = 4.0
BLOCK_WAIT = 90.0
MAX_MOPS_ERRORS = 3
DEFAULT_BUDGET_SECONDS = 600
RECENT_SEASONS = 11
ANNUAL_YEARS = 5
# Final once fetched on / after this date (statutory deadline, financial companies included, + margin)
FINAL_DATES = {1: (6, 30), 2: (9, 30), 3: (12, 31), 4: (4, 30)}
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
IMPLIED_MIN_EPS = 0.1
CROSS_CHECK_MIN = 20
CROSS_CHECK_TOLERANCE = 0.02
FLAG_OPENAPI = 1
REVENUE_KINDS = ("營業收入", "淨收益", "收益", "收入", "利息淨收益+利息以外淨損益")

# Pages MOPS answers instead of data when it refuses a request
BLOCK_MARKERS = (
    "FOR SECURITY REASONS", "CAN NOT BE ACCESSED", "CANNOT BE ACCESSED", "因為安全性考量", "安全性考量",
    "查詢過於頻繁", "過於頻繁", "請稍後再查詢", "OVERRUN", "頁面無法執行", "系統忙碌", "TOO MANY REQUESTS",
    "ACCESS DENIED", "REQUEST REJECTED",
)
EMPTY_MARKERS = ("查無所需資料", "查無資料", "查詢無資料", "資料庫中查無需求資料", "無符合條件之資料")


class StoreError(ValueError):
    """tw.json does not have the expected layout."""


class MopsBlocked(RuntimeError):
    """MOPS answered with a refusal / security page instead of data."""


class MopsFormatError(ValueError):
    """A MOPS page or OpenAPI payload this parser does not recognise (details for the Actions log)."""


# ---------------------------------------------------------------------------------------------
# Seasons
# ---------------------------------------------------------------------------------------------
def season_key(year, quarter):
    return f"{year}Q{quarter}"


def parse_season(key):
    match = re.fullmatch(r"(\d{4})Q([1-4])", key) if isinstance(key, str) else None
    return (int(match.group(1)), int(match.group(2))) if match else None


def season_number(year, quarter):
    return year * 4 + quarter - 1


def from_number(number):
    return number // 4, number % 4 + 1


def roc_year(year):
    """Western year → 民國 year (2026 → 115)."""
    return year - 1911


def western_year(roc):
    """民國 year (115, '115') → western year; None for anything else."""
    try:
        value = int(str(roc).strip())
    except (TypeError, ValueError):
        return None
    return value + 1911 if 1 <= value < 300 else None


def quarter_end(year, quarter):
    month, day = QUARTER_END[quarter]
    return date(year, month, day)


def final_date(year, quarter):
    month, day = FINAL_DATES[quarter]
    return date(year + 1 if quarter == 4 else year, month, day)


def last_ended_season(today):
    """(year, quarter) of the newest quarter that has ended before today."""
    number = season_number(today.year, (today.month - 1) // 3 + 1) - 1
    return from_number(number)


def target_seasons(today):
    """Seasons kept and fetched, newest first: the last RECENT_SEASONS + the Q4 of the last ANNUAL_YEARS years."""
    newest = season_number(*last_ended_season(today))
    numbers = {newest - k for k in range(RECENT_SEASONS)}
    numbers |= {season_number(year, 4) for year in range(today.year - ANNUAL_YEARS, today.year)}
    return [season_key(*from_number(number)) for number in sorted(numbers, reverse=True)]


# ---------------------------------------------------------------------------------------------
# Values and headers
# ---------------------------------------------------------------------------------------------
_FULL_WIDTH = str.maketrans({"（": "(", "）": ")", "：": ":", "，": ",", "－": "-", "＋": "+", "　": ""})


def normalise(text):
    """Header / cell text without whitespace and with half-width brackets: '基本每股盈餘（元）' → '基本每股盈餘(元)'."""
    return re.sub(r"\s+", "", str(text or "").translate(_FULL_WIDTH)).replace(" ", "")


def number(value):
    """'1,234' / '-1,234' / '(1,234)' / 12.5 → float; '', '--', '-', 'N/A' → None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = normalise(value).replace(",", "")
    if text in ("", "-", "--", "---", "N/A", "NA", "null", "None"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        result = float(text)
    except ValueError:
        return None
    if not math.isfinite(result):
        return None
    return -result if negative else result


def compact(value):
    """Whole numbers as int (no '.0' in the file), others rounded to 4 decimals."""
    if value is None:
        return None
    return int(value) if float(value).is_integer() else round(value, 4)


CODE_KEYS = ("公司代號", "SecuritiesCompanyCode", "CompanyCode", "Code")
YEAR_KEYS = ("年度", "Year")
SEASON_KEYS = ("季別", "Season", "Quarter")
EPS_KEYS = ("基本每股盈餘(元)", "基本每股盈餘", "BasicEarningsPerShare", "BasicEarningsLossPerShare", "EPS")
REVENUE_KEYS = (
    ("營業收入", "OperatingRevenue", "Revenue", "TotalRevenue", "Revenues", "營業收入合計"),
    ("淨收益", "NetRevenue", "NetRevenues"),
    ("收益",),
    ("收入",),
)
INTEREST_KEYS = ("利息淨收益", "利息以外淨損益")
PARENT_NI_KEYS = (
    "淨利(淨損)歸屬於母公司業主", "淨利(損)歸屬於母公司業主", "本期淨利(淨損)歸屬於母公司業主",
    "歸屬於母公司業主之淨利(損)", "歸屬於母公司業主之本期淨利(淨損)",
    "NetIncomeAttributableToOwnersOfParent", "ProfitLossAttributableToOwnersOfParent",
    "NetIncomeLossAttributableToOwnersOfParent",
)
TOTAL_NI_KEYS = ("本期淨利(淨損)", "本期稅後淨利(淨損)", "本期淨利(損)", "本期稅後淨利(損)", "NetIncome", "ProfitLoss")


def column_map(headers):
    """
    Normalised headers → {role: index} (code, eps, rev | interest pair, ni). MopsFormatError without a
    code column or without both EPS and revenue.
    """
    names = [normalise(header) for header in headers]
    lookup = {name: index for index, name in reversed(list(enumerate(names)))}

    def find(keys):
        return next((lookup[key] for key in keys if key in lookup), None)

    roles = {"code": find(CODE_KEYS), "eps": find(EPS_KEYS)}
    if roles["eps"] is None:
        roles["eps"] = next((i for i, name in enumerate(names) if name.startswith("基本每股盈餘")), None)
    roles["rev"], roles["kind"] = None, None
    for kind, keys in enumerate(REVENUE_KEYS):
        index = find(keys)
        if index is not None:
            roles["rev"], roles["kind"] = index, kind
            break
    if roles["rev"] is None and all(key in lookup for key in INTEREST_KEYS):
        roles["interest"] = tuple(lookup[key] for key in INTEREST_KEYS)
        roles["kind"] = REVENUE_KINDS.index("利息淨收益+利息以外淨損益")
    roles["ni"] = find(PARENT_NI_KEYS)
    if roles["ni"] is None:
        roles["ni"] = next((i for i, name in enumerate(names)
                            if "歸屬於母公司業主" in name and ("淨利" in name or "淨損" in name) and "綜合" not in name), None)
    if roles["ni"] is None:
        roles["ni"] = find(TOTAL_NI_KEYS)
    if roles["code"] is None:
        raise MopsFormatError(f"no company code column (公司代號) among {names[:12]}")
    if roles["eps"] is None and roles["rev"] is None and "interest" not in roles:
        raise MopsFormatError(f"no EPS (基本每股盈餘) or revenue (營業收入/淨收益/收益/收入) column among {names[:20]}")
    return roles


def entry_from(values, roles):
    """Cells / record values (indexable by the roles) → (code, [rev, eps, ni, kind]) or None."""
    def cell(index):
        return values[index] if index is not None and index < len(values) else None

    code = normalise(cell(roles["code"])).upper()
    if not re.fullmatch(r"[0-9A-Z]{4,6}", code):
        return None
    if "interest" in roles:
        parts = [number(cell(index)) for index in roles["interest"]]
        revenue = None if None in parts else parts[0] + parts[1]
    else:
        revenue = number(cell(roles["rev"]))
    eps, ni = number(cell(roles["eps"])), number(cell(roles["ni"]))
    if revenue is None and eps is None:
        return None
    return code, [compact(revenue), compact(eps), compact(ni), roles["kind"] or 0]


# ---------------------------------------------------------------------------------------------
# MOPS pages
# ---------------------------------------------------------------------------------------------
class _TableParser(HTMLParser):
    """Every <table> as a list of rows, every row a list of cell texts (nested tables kept apart)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._stack, self._row, self._cell = [], [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            if self._row:
                self._stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def detect_block(text):
    """The refusal marker found in a MOPS answer, or None."""
    upper = (text or "")[:20000].upper()
    return next((marker for marker in BLOCK_MARKERS if marker in upper), None)


def decode(body, content_type=""):
    """Response bytes → text: the declared charset, else UTF-8, else Big5 (cp950)."""
    if isinstance(body, str):
        return body
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    for encoding in ([match.group(1)] if match else []) + ["utf-8", "cp950"]:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("cp950", errors="replace")


def parse_mops(text):
    """
    A t163sb04 answer → {"status": "ok" | "empty", "entries": {code: entry}, "tables": n, "kinds": Counter}.
    MopsBlocked for a refusal page, MopsFormatError for anything else without a company table.
    """
    marker = detect_block(text)
    if marker:
        raise MopsBlocked(f"MOPS refused the request ('{marker}')")
    parser = _TableParser()
    parser.feed(text or "")
    parser.close()
    entries, kinds, tables, duplicates = {}, Counter(), 0, 0
    for table in parser.tables:
        header_at = next((i for i, row in enumerate(table) if any(normalise(cell) == "公司代號" for cell in row)), None)
        if header_at is None:
            continue
        header = table[header_at]
        roles = column_map(header)
        tables += 1
        for row in table[header_at + 1:]:
            if len(row) < len(header) or any(normalise(cell) == "公司代號" for cell in row):
                continue
            parsed = entry_from(row, roles)
            if parsed is None:
                continue
            code, entry = parsed
            if code in entries:
                duplicates += 1
                continue
            entries[code] = entry
            kinds[REVENUE_KINDS[entry[3]]] += 1
    if not tables:
        compact_text = normalise(re.sub(r"<[^>]+>", " ", text or ""))
        if any(marker in compact_text for marker in EMPTY_MARKERS):
            return {"status": "empty", "entries": {}, "tables": 0, "kinds": kinds, "duplicates": 0}
        raise MopsFormatError(f"no table with 公司代號 (page starts: {compact_text[:160]!r})")
    return {"status": "ok", "entries": entries, "tables": tables, "kinds": kinds, "duplicates": duplicates}


def parse_openapi(records, label):
    """
    OpenAPI t187ap06 records → {season key: {code: entry}}. MopsFormatError when the records have no
    code / year / season / value columns (the first record's keys are in the message).
    """
    if not isinstance(records, list):
        raise MopsFormatError(f"{label}: not a JSON list ({type(records).__name__})")
    result = {}
    if not records:
        return result
    first = next((record for record in records if isinstance(record, dict)), None)
    if first is None:
        raise MopsFormatError(f"{label}: no JSON object among {len(records)} records")
    keys = list(first)
    try:
        roles = column_map(keys)
    except MopsFormatError as error:
        raise MopsFormatError(f"{label}: {error}") from error
    names = [normalise(key) for key in keys]
    year_at = next((names.index(key) for key in YEAR_KEYS if key in names), None)
    season_at = next((names.index(key) for key in SEASON_KEYS if key in names), None)
    if year_at is None or season_at is None:
        raise MopsFormatError(f"{label}: no 年度 / 季別 columns among {names[:12]}")
    for record in records:
        if not isinstance(record, dict):
            continue
        values = [record.get(key) for key in keys]
        year, quarter = western_year(values[year_at]), number(values[season_at])
        if year is None or quarter not in (1, 2, 3, 4):
            continue
        parsed = entry_from(values, roles)
        if parsed is not None:
            result.setdefault(season_key(year, int(quarter)), {})[parsed[0]] = parsed[1]
    return result


# ---------------------------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------------------------
def empty_store():
    return {"version": VERSION, "source": SOURCE, "units": UNITS, "fields": STORE_FIELDS,
            "seasons": [], "fetch": {}, "data": {}}


def load_store(document):
    """tw.json → {"fetch": {...}, "data": {code: {season: [rev, eps, ni, flags]}}}; StoreError when malformed."""
    if document is None:
        return {"fetch": {}, "data": {}}
    if not isinstance(document, dict) or document.get("version") != VERSION:
        raise StoreError("not a version-1 tw.json")
    seasons, data, fetch = document.get("seasons"), document.get("data"), document.get("fetch")
    if not isinstance(seasons, list) or not all(parse_season(key) for key in seasons):
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
            flags = entry[3] if len(entry) == 4 and isinstance(entry[3], int) else 0
            row[key] = [rev, eps, ni, flags]
        if row:
            loaded[code] = row
    return {"fetch": {key: dict(value) for key, value in fetch.items() if parse_season(key) and isinstance(value, dict)},
            "data": loaded}


def dump_store(store, keep_seasons):
    """In-memory store → tw.json document (seasons ascending, rows aligned, trailing nulls cut)."""
    present = {key for row in store["data"].values() for key in row}
    seasons = sorted(set(keep_seasons) & present, key=lambda key: season_number(*parse_season(key)))
    data = {}
    for code in sorted(store["data"]):
        row = store["data"][code]
        values = []
        for key in seasons:
            entry = row.get(key)
            if entry is None:
                values.append(None)
            else:
                rev, eps, ni, flags = entry
                values.append([compact(rev), compact(eps), compact(ni)] + ([flags] if flags else []))
        while values and values[-1] is None:
            values.pop()
        if values:
            data[code] = values
    fetch = {key: store["fetch"][key] for key in sorted(store["fetch"]) if key in keep_seasons}
    document = empty_store()
    document.update({"seasons": seasons, "fetch": fetch, "data": data})
    return document


def flags_of(kind, openapi):
    return (kind << 1) | (FLAG_OPENAPI if openapi else 0)


def kind_of(flags):
    return (flags >> 1) & 0b111


def merge_mops(store, key, typek, page, today):
    """Puts one MOPS page into the store (rotc never replaces a listed-market entry) → entries stored."""
    year, quarter = parse_season(key)
    stored = 0
    for code, (rev, eps, ni, kind) in page["entries"].items():
        row = store["data"].setdefault(code, {})
        if typek == HISTORY_TYPEK and key in row and not row[key][3] & FLAG_OPENAPI:
            continue
        row[key] = [rev, eps, ni, flags_of(kind, False)]
        stored += 1
    store["fetch"].setdefault(key, {})[typek] = {
        "src": "mops", "at": today.isoformat(), "n": len(page["entries"]),
        "final": today >= final_date(year, quarter),
    }
    return stored


def cross_check(store, key, entries):
    """Median OpenAPI / MOPS revenue ratio over the companies both have for `key` → (ratio, count)."""
    ratios = []
    for code, (rev, _, _, _) in entries.items():
        stored = store["data"].get(code, {}).get(key)
        if stored and not stored[3] & FLAG_OPENAPI and stored[0] and rev:
            ratios.append(rev / stored[0])
    if not ratios:
        return None, 0
    ratios.sort()
    return ratios[len(ratios) // 2], len(ratios)


def merge_openapi(store, by_season, today, log=print):
    """OpenAPI rows fill the (code, season) pairs MOPS has not; refused when the units disagree."""
    filled = {}
    for key, entries in by_season.items():
        if parse_season(key) is None:
            continue
        ratio, count = cross_check(store, key, entries)
        if count >= CROSS_CHECK_MIN and abs(ratio - 1) > CROSS_CHECK_TOLERANCE:
            log(f"::warning::OpenAPI {key}: revenue differs from MOPS (median ratio {ratio:.4g} over {count} "
                f"companies); OpenAPI rows not used")
            continue
        added = 0
        for code, (rev, eps, ni, kind) in entries.items():
            row = store["data"].setdefault(code, {})
            if key in row and not row[key][3] & FLAG_OPENAPI:
                continue
            row[key] = [rev, eps, ni, flags_of(kind, True)]
            added += 1
        store["fetch"].setdefault(key, {})["openapi"] = {"src": "openapi", "at": today.isoformat(),
                                                         "n": len(entries), "checked": count}
        filled[key] = added
    return filled


def mops_plan(store, today, force=()):
    """[(season, typek)] to fetch this run, most needed first (see the module notes)."""
    missing, refresh, history = [], [], []
    for key in target_seasons(today):
        year, quarter = parse_season(key)
        if today <= quarter_end(year, quarter):
            continue
        fetched = store["fetch"].get(key, {})
        for typek in LISTED_TYPEKS:
            info = fetched.get(typek)
            if info is None:
                missing.append((key, typek))
            elif key in force or (not info.get("final") and info.get("at") != today.isoformat()):
                refresh.append((key, typek))
        if today >= final_date(year, quarter) and (HISTORY_TYPEK not in fetched or key in force):
            history.append((key, HISTORY_TYPEK))
    return missing + refresh + history


# ---------------------------------------------------------------------------------------------
# Quarters, YoY blocks
# ---------------------------------------------------------------------------------------------
def implied_shares(rev_eps_ni):
    """NI / EPS of a YTD figure (thousand shares) when cent rounding cannot move it much; else None."""
    _, eps, ni = rev_eps_ni[:3]
    if eps is None or ni is None or abs(eps) < IMPLIED_MIN_EPS or ni == 0 or (ni > 0) != (eps > 0):
        return None
    return ni / eps


def single_quarter(now, before):
    """YTD(n), YTD(n-1) → [rev, eps] of quarter n (see the module notes); None values when not derivable."""
    rev = None
    if now[0] is not None and before[0] is not None and kind_of(now[3]) == kind_of(before[3]):
        rev = now[0] - before[0]
        if rev < 0:
            rev = None
    eps = None
    if now[1] is not None:
        if implied_shares(now) is not None and before[2] is not None:
            eps = round((now[2] - before[2]) * now[1] / now[2], 4)
        elif before[1] is not None:
            eps = round(now[1] - before[1], 4)
    return rev, eps


def company_periods(row):
    """{season: [rev, eps, ni, flags]} → (quarterly periods, annual periods) for build_growth.yoy_series."""
    years = {}
    for key, entry in row.items():
        parsed = parse_season(key)
        if parsed:
            years.setdefault(parsed[0], {})[parsed[1]] = entry
    quarters, annual = [], []
    for year, ytd in years.items():
        for quarter in (1, 2, 3, 4):
            entry = ytd.get(quarter)
            if entry is None:
                continue
            if quarter == 1:
                rev, eps = entry[0], entry[1]
            elif ytd.get(quarter - 1) is None:
                continue
            else:
                rev, eps = single_quarter(entry, ytd[quarter - 1])
            end = quarter_end(year, quarter)
            quarters.append({
                "end": end.isoformat(), "day": end, "fy": year, "fq": quarter, "epsD": None, "epsB": eps,
                "rev": rev, "shares": implied_shares(entry), "src": "mops", "derived": quarter == 4,
                "kind": kind_of(entry[3]),
            })
        if 4 in ytd:
            entry, end = ytd[4], quarter_end(year, 4)
            annual.append({
                "end": end.isoformat(), "day": end, "fy": year, "fq": None, "epsD": None, "epsB": entry[1],
                "rev": entry[0], "shares": implied_shares(entry), "src": "mops", "derived": False,
                "kind": kind_of(entry[3]),
            })
    return quarters, annual


def tw_pair(current, base):
    """build_growth pair function: one share basis (implied counts) for EPS, one revenue kind for revenue."""
    value, turn = (None, False) if growth.share_count_jump(current, base) else growth.eps_values(current, base)
    revenue = None
    if current["kind"] == base["kind"] and not growth.scale_suspect(current["rev"], base["rev"]):
        revenue = growth.growth_pct(current["rev"], base["rev"])
    return value, turn, revenue


def symbol_map(universe, extra_keys=()):
    """{code: '2330.TW' / '6488.TWO'} from the Taiwan rows of universe.json and other Taiwan keys."""
    from build_financials import universe_rows  # noqa: PLC0415 - keep this module's import light

    mapping = {}
    for symbol in [symbol for symbol, _ in universe_rows(universe, "TW")] + list(extra_keys):
        if isinstance(symbol, str) and symbol.upper().endswith(growth.TW_SUFFIXES):
            mapping.setdefault(symbol.upper().rsplit(".", 1)[0], symbol.upper())
    return mapping


def growth_candidates(document, symbols, today):
    """tw.json + {code: symbol} → {symbol: (None, quarterly block, annual block)} (src "mops")."""
    store = load_store(document)
    result = {}
    for code, row in store["data"].items():
        symbol = symbols.get(code)
        if symbol is None:
            continue
        quarters, annual = company_periods(row)
        q = growth.yoy_series(quarters, True, growth.QUARTER_SLOTS, [], today, growth.STALE_QUARTER_DAYS, tw_pair)
        y = growth.yoy_series(annual, False, growth.RECENT_YEAR_SLOTS, [], today, growth.STALE_ANNUAL_DAYS, tw_pair)
        if q is not None or y is not None:
            result[symbol] = (None, q, y)
    return result


# =============================================================================================
# I/O
# =============================================================================================
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Http:
    """curl_cffi (Chrome TLS) when installed, else requests; → (status, bytes, content type)."""

    def __init__(self):
        try:
            from curl_cffi import requests as curl_requests

            self.session = curl_requests.Session(impersonate="chrome")
        except ImportError:
            import requests

            self.session = requests.Session()
            self.session.headers["User-Agent"] = BROWSER_UA

    def post(self, url, data, headers=None, timeout=60):
        response = self.session.post(url, data=data, headers=headers or {}, timeout=timeout)
        return response.status_code, response.content, response.headers.get("content-type", "")

    def get(self, url, headers=None, timeout=60):
        response = self.session.get(url, headers=headers or {}, timeout=timeout)
        return response.status_code, response.content, response.headers.get("content-type", "")


def mops_form(year, quarter, typek):
    return {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "isQuery": "Y",
            "TYPEK": typek, "year": str(roc_year(year)), "season": f"{quarter:02d}"}


def fetch_mops(http, key, typek):
    """One season / market from MOPS (MOPS_URLS in turn) → parsed page; MopsBlocked / MopsFormatError / OSError."""
    year, quarter = parse_season(key)
    problems = []
    for url in MOPS_URLS:
        try:
            status, body, content_type = http.post(url, mops_form(year, quarter, typek), {
                "Referer": MOPS_REFERER, "Origin": "https://mopsov.twse.com.tw",
                "Content-Type": "application/x-www-form-urlencoded",
            })
        except Exception as error:  # noqa: BLE001 - transport errors differ between curl_cffi and requests
            problems.append(f"{url}: {type(error).__name__}: {error}")
            continue
        text = decode(body, content_type)
        if status != 200:
            if detect_block(text):
                raise MopsBlocked(f"MOPS HTTP {status} ('{detect_block(text)}')")
            problems.append(f"{url}: HTTP {status}")
            continue
        try:
            return parse_mops(text)
        except MopsFormatError as error:
            problems.append(f"{url}: {error}")
    raise MopsFormatError(f"{key} {typek}: " + " | ".join(problems))


def run_mops(store, http, today, budget, force, sleep, clock, log):
    """MOPS part of a run → summary dict."""
    plan = mops_plan(store, today, force)
    deadline = clock() + budget
    done, errors, blocked, stopped = [], 0, 0, None
    for index, (key, typek) in enumerate(plan):
        if clock() > deadline:
            stopped = f"time budget ({len(plan) - index} left for the next run)"
            break
        if index:
            sleep(MOPS_DELAY + random.random() * MOPS_JITTER)
        try:
            page = fetch_mops(http, key, typek)
        except MopsBlocked as error:
            blocked += 1
            if blocked >= 2:
                stopped = f"refused twice: {error}"
                break
            log(f"MOPS {key} {typek}: {error}; waiting {BLOCK_WAIT:.0f}s")
            sleep(BLOCK_WAIT)
            try:
                page = fetch_mops(http, key, typek)
            except (MopsBlocked, MopsFormatError) as again:
                stopped = f"refused again: {again}"
                break
        except MopsFormatError as error:
            errors += 1
            log(f"::warning::MOPS {key} {typek}: {error}")
            if errors >= MAX_MOPS_ERRORS:
                stopped = f"{errors} unreadable answers"
                break
            continue
        stored = merge_mops(store, key, typek, page, today)
        done.append(f"{key}:{typek}:{page['status']}:{stored}")
        log(f"MOPS {key} {typek}: {page['status']}, {len(page['entries'])} companies "
            f"({dict(page['kinds'])}), {page['tables']} tables")
    if stopped:
        log(f"::warning::MOPS part stopped: {stopped}")
    return {"planned": len(plan), "done": done, "errors": errors, "blocked": blocked, "stopped": stopped}


def run_openapi(store, http, today, log):
    """OpenAPI part of a run → {endpoint: result}."""
    results, by_season = {}, {}
    for _, url in OPENAPI:
        name = url.rsplit("/", 1)[-1]
        try:
            status, body, content_type = http.get(url, {"Accept": "application/json"})
            if status == 404:
                results[name] = "not found"
                continue
            if status != 200:
                raise MopsFormatError(f"HTTP {status}")
            parsed = parse_openapi(json.loads(decode(body, content_type) or "[]"), name)
        except (MopsFormatError, ValueError, OSError) as error:
            results[name] = f"error: {error}"
            log(f"::warning::OpenAPI {name}: {error}")
            continue
        except Exception as error:  # noqa: BLE001 - transport errors differ between curl_cffi and requests
            results[name] = f"error: {type(error).__name__}: {error}"
            log(f"::warning::OpenAPI {name}: {type(error).__name__}: {error}")
            continue
        for key, entries in parsed.items():
            by_season.setdefault(key, {}).update(entries)
        results[name] = {key: len(entries) for key, entries in parsed.items()}
    filled = merge_openapi(store, by_season, today, log)
    return {"endpoints": results, "filled": filled}


def run(args, http=None, now=None, sleep=time.sleep, clock=time.monotonic, log=print):
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    today = now.date()
    out = args.out or os.path.join(args.report_dir, "fundamentals", "tw.json")
    previous = read_json(out)
    if previous is None and os.path.exists(out):
        log(f"::error::{out} exists but cannot be read; not touched (fix or delete it)")
        return 1
    try:
        store = load_store(previous)
    except StoreError as error:
        log(f"::error::{out}: {error}; not touched (fix or delete it)")
        return 1
    http = http or Http()
    force = set(args.seasons or ())
    summary = {}
    if not args.no_mops:
        summary["mops"] = run_mops(store, http, today, args.budget, force, sleep, clock, log)
    if not args.no_openapi:
        summary["openapi"] = run_openapi(store, http, today, log)
    # Every code is kept (興櫃 history included: it is the year-ago data of companies listed since)
    document = dump_store(store, target_seasons(today))
    unchanged = isinstance(previous, dict) and all(previous.get(k) == document[k] for k in ("seasons", "fetch", "data"))
    if unchanged or (not document["data"] and not isinstance(previous, dict)):
        summary["written"] = False          # nothing new (or nothing at all yet: no empty file)
    else:
        summary["written"] = True
        summary["bytes"] = write_atomic(out, document)
    summary["companies"] = len(document["data"])
    summary["seasons"] = {key: sum(1 for row in document["data"].values() if i < len(row) and row[i] is not None)
                          for i, key in enumerate(document["seasons"])}
    log(json.dumps(summary, ensure_ascii=False, default=str))
    mops, openapi = summary.get("mops") or {}, summary.get("openapi") or {}
    answered = any(isinstance(result, dict) for result in (openapi.get("endpoints") or {}).values())
    # Exit 1 (a warning in the workflow) when no source answered at all; a quiet day (nothing due) is fine
    if mops.get("done") or answered or (mops and not mops.get("stopped") and not mops.get("errors")):
        return 0
    return 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Taiwan official income statements → docs/report/fundamentals/tw.json")
    parser.add_argument("--report-dir", default=os.path.join(REPO_ROOT, "docs", "report"))
    parser.add_argument("--out", default=None)
    parser.add_argument("--budget", type=float,
                        default=float(os.environ.get("TW_FUNDAMENTALS_BUDGET_SECONDS") or DEFAULT_BUDGET_SECONDS))
    parser.add_argument("--no-mops", action="store_true")
    parser.add_argument("--no-openapi", action="store_true")
    parser.add_argument("--seasons", type=lambda text: [part for part in text.split(",") if parse_season(part)],
                        default=None, help="re-fetch these seasons from MOPS even when final (e.g. 2025Q4,2026Q1)")
    return parser.parse_args(argv)


def main(argv=None):
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())

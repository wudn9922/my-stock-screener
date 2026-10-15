"""
Builds docs/report/fundamentals/us-sec.json: EPS / revenue YoY (quarterly and annual) of the whole US
list (docs/report/universe.json) from SEC XBRL frames. One frames request returns every company's value
of one concept for one calendar period
(https://data.sec.gov/api/xbrl/frames/<taxonomy>/<concept>/<unit>/CY<yyyy>[Q<q>].json), so the whole
market costs a few hundred requests instead of one per company. build_growth.py merges the rows into
docs/report/growth.json (src "frames").

  python atlas/scripts/build_fundamentals_sec.py [--no-foreign] [--foreign-max-age-days N]
         [--report-dir docs/report] [--out docs/report/fundamentals/us-sec.json] [--history-dir DIR]
  python atlas/scripts/build_fundamentals_sec.py --summary     (offline: summary of the stored files)
  Env: SEC_USER_AGENT (default: seed_financials_sec.DEFAULT_USER_AGENT, a contact address as SEC asks).
  Standard library only. Network: data.sec.gov (frames) and www.sec.gov (company_tickers.json).

Where it runs: SEC has answered 403 to GitHub-hosted runners at times (see atlas.yml). The workflow
still tries every day: the first request (company_tickers.json) decides within seconds, and a refusal
keeps the stored files (exit 0 with a warning). A manual run from the repository (local or Codex Cloud)
followed by a commit of docs/report/fundamentals/us-sec.json and docs/report/fundamentals/us-history/
refreshes them by hand. Requests (~10 years): 13 core concept-units x (44 quarters + 12 years) = 728,
plus 206 foreign concept-units x 11 years = 2,266 (most answer 404); ~3,000 in all, about 15 minutes
at the observed ~0.3 s per request (~4 minutes without the foreign part). SEC fair access: User-Agent
with contact, <= ~7.5 requests per second, backoff on 403 / 429 / 5xx (seed_financials_sec.SecClient).
The run ends with a "SUMMARY {...}" line (rows, period ranges, coverage) to check a commit against.

Frames (all us-gaap, USD; "BasicAndDiluted" EPS counts as both diluted and basic):
- quarterly CY frames (duration ~3 months) of the last QUARTER_FRAMES calendar quarters and annual CY
  frames (~12 months) of the last ANNUAL_FRAMES years: EPS (EarningsPerShareDiluted > -BasicAndDiluted;
  EarningsPerShareBasic > -BasicAndDiluted), revenue (REVENUE concepts, see revenue_pair), weighted
  average share counts (SHARES, for the share-basis check);
- foreign issuers (20-F / 40-F, annual only, in the reporting currency): us-gaap EPS / revenue in
  FOREIGN_CURRENCIES and ifrs-full EPS / revenue in USD and FOREIGN_CURRENCIES for the last FOREIGN_YEARS
  years. Their YoY is in the reporting currency (a ratio: no FX conversion, no ADR ratio). Skipped with
  --no-foreign, or while the stored foreign part is younger than --foreign-max-age-days: the stored
  foreign rows are then kept.
A frame holds one fact per company, the last filed one that best fits the calendar period. Companies
with another fiscal year map to the nearest calendar quarter / year; YoY only compares one frame label
with the same label a year earlier and still needs the period ends to be 335-395 days apart.

Per company (CIK):
- one period per frame label; values whose dates are more than 7 days off the label's common dates
  are dropped.
- Q4: a fiscal year (annual frame) whose first three quarters are in the quarterly frames and whose
  fourth quarter is not gets Q4 = year - (Q1 + Q2 + Q3), flagged derived (dQ), per concept and only
  when all four pieces have it in one currency; revenue only when >= 0; EPS only when the four share
  counts are within Q4_SHARE_MATCH of each other (no split inside the year). The derived quarter takes
  the calendar quarter of its midpoint, unless that label is already taken.
- YoY / turnaround / staleness: build_growth.yoy_series with frames_pair: EPS diluted when both sides
  have it, else basic, the same concept and currency on both sides; a share count change >= 1.5x
  between the pair nulls EPS (no split events here). Revenue: the first REVENUE concept (in order)
  that both sides have in one currency and that is not a partial line (current value >= half of the
  largest revenue concept of that period). A pair whose ratio is ~10^3 / 10^6 is a scale error: null.
- tickers: company_tickers.json (CIK → every ticker of that CIK, so all share classes get the company's
  YoY; ratios do not depend on the class), restricted to the US rows of universe.json when it exists.

Output (compact): {"version": 1, "generatedAt", "asOf", "source", "foreignAt", "frames": {...},
  "fields": FIELDS, "rows": [[symbol, cik, qEnd, yEnd, epsQ, revQ, epsY, revY, tQ, tY, dQ, f, cur]]}
  f = 1 when the annual block comes from the foreign frames, cur = its currency when not USD.
  epsY / revY / tY have up to 10 annual slots (growth.YEAR_SLOTS), epsQ / revQ 4.

Statement history (us-history/NNN.json, HISTORY_SHARDS shards; a symbol's shard is FNV-1a 32-bit of
its key mod HISTORY_SHARDS, computed the same way by atlas/src/fundamentals/framesHistory.ts). The
financials page reads it for US stocks without docs/report/financials/<KEY>.json, or to add older
periods to a shorter one. One entry per ticker (share classes repeat their company's entry):
  {"cik", "cur", "f" (foreign issuer: annual only, reporting currency), "fyEnd" (fiscal-year-end
   month), "split": [[date, ratio]] (inferred, see adjust_splits), "issues": [...], "c": {field:
   [concepts used]}, "q": [[end, fy, fq, d, revenue, netIncome, epsDiluted, epsBasic, dilutedShares]],
   "y": [[end, fy, revenue, netIncome, epsDiluted, epsBasic, dilutedShares]]} (oldest first).
- Only what the frames have: revenue (the first non-partial REVENUE concept), net income
  (NetIncomeLoss, US filers), EPS diluted / basic, weighted diluted shares; everything else of a
  statement (gross profit, cash flow, balance sheet) is absent and the page shows it as missing.
- d = 1: fiscal Q4 derived as year - (Q1 + Q2 + Q3) (rules above; EPS only on one share basis).
- Every value is on the newest share basis: inferred splits adjust older EPS / shares, an odd share
  jump nulls older EPS ("eps-basis-break"); scale errors (10^3 / 10^6 off the neighbours) are dropped.
- Written with one symbol per line, only the shards whose content changed; a run without the
  foreign part keeps the stored foreign entries. The YoY rows above are computed from the frames
  values as filed (their own split / scale rules), not from these adjusted series.
Safety: the file is not written when a frame failed (other than 404 = no data), when one of the key
frames (diluted EPS of the four calendar quarters before the newest one: thousands of filers each) is
empty or missing, or when the row (history symbol) count drops below MIN_ROW_SHARE of the stored
one; only written when the rows changed. The same gate holds for the history shards. Exit codes: 0 written / unchanged / SEC refused (warning), 1 failed.
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import build_growth as growth
from build_financials import (
    financials_key, finite_number, fiscal_quarter, fiscal_year, fy_end_month, market_of, universe_rows,
)
from build_valuation_us import read_json, write_atomic

VERSION = 1
SOURCE = "SEC EDGAR XBRL frames (data.sec.gov/api/xbrl/frames)"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/{unit}/{period}.json"
FIELDS = ["symbol", "cik", "qEnd", "yEnd", "epsQ", "revQ", "epsY", "revY", "tQ", "tY", "dQ", "f", "cur"]
# ~10 years: 44 calendar quarters (10 years of TTM EPS for the P/E river plus the year before), 12
# calendar years (a calendar-year filer's current year has no frame yet, so 12 labels give it 11 fiscal
# years = 10 annual YoY), foreign issuers 11 years back from last year (10 annual YoY)
QUARTER_FRAMES = 44
ANNUAL_FRAMES = 12
FOREIGN_YEARS = 11
QUARTER_DAYS = (80, 120)
ANNUAL_DAYS = (350, 380)
ALIGN_DAYS = 7
Q4_SHARE_MATCH = 1.25
PARTIAL_REVENUE = 0.5
MIN_ROW_SHARE = 0.5
KEY_QUARTERS = 4

# Statement history (docs/report/fundamentals/us-history/NNN.json, see the module notes)
HISTORY_VERSION = 1
HISTORY_SHARDS = 128
HISTORY_VALUES = ["revenue", "netIncome", "epsDiluted", "epsBasic", "dilutedShares"]
HISTORY_FIELDS = {"q": ["end", "fy", "fq", "d"] + HISTORY_VALUES, "y": ["end", "fy"] + HISTORY_VALUES}
# Inferred splits: neighbouring share counts >= SPLIT_JUMP apart, within SPLIT_TOLERANCE of one of
# these ratios (or the reverse), revenue ratio inside SPLIT_REVENUE_BAND
SPLIT_JUMP = 1.4
NICE_SPLITS = (1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100)
SPLIT_TOLERANCE = 0.08
SPLIT_REVENUE_BAND = (0.5, 2.0)

US = "us-gaap"
IFRS = "ifrs-full"
# role → concepts in preference order (taxonomy-qualified keys)
CORE_CONCEPTS = {
    "epsD": [f"{US}:EarningsPerShareDiluted", f"{US}:EarningsPerShareBasicAndDiluted"],
    "epsB": [f"{US}:EarningsPerShareBasic", f"{US}:EarningsPerShareBasicAndDiluted"],
    "rev": [
        f"{US}:Revenues",
        f"{US}:RevenueFromContractWithCustomerExcludingAssessedTax",
        f"{US}:RevenueFromContractWithCustomerIncludingAssessedTax",
        f"{US}:SalesRevenueNet",
        f"{US}:RevenuesNetOfInterestExpense",
        f"{US}:RegulatedAndUnregulatedOperatingRevenue",
    ],
    # net income: only for the statement history (us-history shards), not part of the YoY rows
    "ni": [f"{US}:NetIncomeLoss"],
    "shares": [
        f"{US}:WeightedAverageNumberOfDilutedSharesOutstanding",
        f"{US}:WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
        f"{US}:WeightedAverageNumberOfSharesOutstandingBasic",
    ],
}
FOREIGN_CONCEPTS = {
    "epsD": [f"{US}:EarningsPerShareDiluted", f"{IFRS}:DilutedEarningsLossPerShare"],
    "epsB": [f"{US}:EarningsPerShareBasic", f"{IFRS}:BasicEarningsLossPerShare"],
    "rev": [f"{US}:Revenues", f"{US}:RevenueFromContractWithCustomerExcludingAssessedTax",
            f"{IFRS}:Revenue", f"{IFRS}:RevenueFromContractsWithCustomers"],
    "shares": [f"{IFRS}:AdjustedWeightedAverageShares", f"{IFRS}:WeightedAverageShares"],
}
# Reporting currencies of the foreign issuers listed in the US (ADRs and 40-F filers)
FOREIGN_CURRENCIES = (
    "CNY", "EUR", "GBP", "JPY", "HKD", "TWD", "INR", "BRL", "CHF", "DKK", "CAD", "AUD", "ILS", "MXN",
    "KRW", "SEK", "NOK", "ZAR", "CLP", "ARS", "COP", "PEN", "SGD", "IDR", "PHP",
)
ROLE_ORDER = {}
for _concepts in (CORE_CONCEPTS, FOREIGN_CONCEPTS):
    for _role, _keys in _concepts.items():
        ROLE_ORDER.setdefault(_role, [])
        ROLE_ORDER[_role] += [key for key in _keys if key not in ROLE_ORDER[_role]]


class FrameFormatError(ValueError):
    """A frames payload that is not what the request asked for (shown in the Actions log)."""


# ---------------------------------------------------------------------------------------------
# Calendar labels and the request plan
# ---------------------------------------------------------------------------------------------
def quarter_number(year, quarter):
    return year * 4 + quarter - 1


def quarter_label(number):
    return f"CY{number // 4}Q{number % 4 + 1}"


def last_ended_quarter(today):
    """Number of the newest calendar quarter that has ended before `today`."""
    return quarter_number(today.year, (today.month - 1) // 3 + 1) - 1


def unit_of(role, currency):
    if role == "shares":
        return "shares"
    return f"{currency}-per-shares" if role in ("epsD", "epsB") else currency


class Frame:
    """One frames request: taxonomy:concept in `unit` for `period` (CY2025Q3 / CY2025)."""

    __slots__ = ("key", "taxonomy", "concept", "unit", "currency", "period", "label", "kind", "foreign")

    def __init__(self, key, currency, unit, period, label, kind, foreign):
        self.key = key
        self.taxonomy, self.concept = key.split(":", 1)
        self.currency, self.unit, self.period = currency, unit, period
        self.label, self.kind, self.foreign = label, kind, foreign

    @property
    def url(self):
        return FRAMES_URL.format(taxonomy=self.taxonomy, concept=self.concept, unit=self.unit, period=self.period)

    def __repr__(self):
        return f"{self.key} {self.unit} {self.period}"


def _concept_units(concepts, currencies):
    """[(key, currency or None, unit)] without duplicates (BasicAndDiluted appears under two roles)."""
    seen, result = set(), []
    for role, keys in concepts.items():
        for key in keys:
            for currency in currencies:
                unit = unit_of(role, currency)
                if (key, unit) not in seen:
                    seen.add((key, unit))
                    result.append((key, None if role == "shares" else currency, unit))
    return result


def frame_plan(today, foreign=True):
    """Every frames request of a run (core first: US filers, quarterly + annual; then foreign, annual)."""
    newest_q = last_ended_quarter(today)
    quarters = [newest_q - k for k in range(QUARTER_FRAMES)]
    years = [today.year - k for k in range(ANNUAL_FRAMES)]
    plan = []
    for key, currency, unit in _concept_units(CORE_CONCEPTS, ("USD",)):
        for number in quarters:
            plan.append(Frame(key, currency, unit, quarter_label(number), number, "q", False))
        for year in years:
            plan.append(Frame(key, currency, unit, f"CY{year}", year, "y", False))
    if foreign:
        foreign_years = [today.year - 1 - k for k in range(FOREIGN_YEARS)]
        units = [
            entry for entry in _concept_units(FOREIGN_CONCEPTS, ("USD",) + FOREIGN_CURRENCIES)
            # us-gaap in USD is already in the core frames
            if not (entry[0].startswith(US + ":") and entry[2] in ("USD", "USD-per-shares"))
        ]
        for key, currency, unit in units:
            for year in foreign_years:
                plan.append(Frame(key, currency, unit, f"CY{year}", year, "y", True))
    return plan


# ---------------------------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------------------------
def parse_day(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_frame(payload, frame):
    """
    frames payload → ([(cik, start, end, value)], dropped count). FrameFormatError when the payload is
    not a frames document for this request (wrong tag / unit / period, no data list).
    """
    if not isinstance(payload, dict):
        raise FrameFormatError(f"{frame}: response is {type(payload).__name__}, not a frames object")
    checks = (("taxonomy", frame.taxonomy), ("tag", frame.concept), ("uom", frame.unit), ("ccp", frame.period))
    for name, expected in checks:
        actual = payload.get(name)
        if actual is not None and str(actual).replace("/", "-per-") != expected:
            raise FrameFormatError(f"{frame}: field {name!r} is {actual!r}, expected {expected!r}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise FrameFormatError(f"{frame}: no 'data' list (keys: {sorted(payload)[:10]})")
    low, high = QUARTER_DAYS if frame.kind == "q" else ANNUAL_DAYS
    points, dropped, seen = [], 0, Counter()
    for item in data:
        if not isinstance(item, dict):
            dropped += 1
            continue
        cik = item.get("cik")
        if isinstance(cik, str) and cik.strip().isdigit():
            cik = int(cik)
        start, end, value = parse_day(item.get("start")), parse_day(item.get("end")), finite_number(item.get("val"))
        if isinstance(cik, bool) or not isinstance(cik, int) or cik <= 0 or start is None or end is None or value is None:
            dropped += 1
            continue
        if not low <= (end - start).days <= high:
            dropped += 1
            continue
        seen[cik] += 1
        points.append((cik, start, end, value))
    if data and not points:
        raise FrameFormatError(f"{frame}: {len(data)} entries but none with cik / start / end / val "
                               f"(first entry keys: {sorted(data[0])[:10] if isinstance(data[0], dict) else data[0]!r})")
    duplicated = {cik for cik, count in seen.items() if count > 1}
    if duplicated:
        dropped += sum(seen[cik] for cik in duplicated)
        points = [point for point in points if point[0] not in duplicated]
    return points, dropped


def parse_tickers(payload):
    """
    company_tickers.json → ({cik: [keys, in file order]}, {key: cik}). Several tickers of one CIK are
    its share classes (GOOGL / GOOG); a ticker listed under two CIKs keeps the first.
    """
    entries = payload.values() if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    by_cik, by_key = {}, {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cik = entry.get("cik_str")
        if isinstance(cik, str) and cik.strip().isdigit():
            cik = int(cik)
        key = financials_key(entry.get("ticker"))
        if key is None or market_of(key) != "US" or isinstance(cik, bool) or not isinstance(cik, int) or cik <= 0:
            continue
        if key in by_key:
            continue
        by_key[key] = cik
        by_cik.setdefault(cik, []).append(key)
    if entries and not by_key:
        raise FrameFormatError("company_tickers.json: no entry with cik_str / ticker")
    return by_cik, by_key


# ---------------------------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------------------------
def roles_of(key, foreign):
    concepts = FOREIGN_CONCEPTS if foreign else CORE_CONCEPTS
    return [role for role, keys in concepts.items() if key in keys]


def collect(frame_points):
    """[(Frame, points)] → {cik: {"q": {label: [entry]}, "y": {label: [entry]}}}; entry = (role, key, cur, start, end, value)."""
    companies = {}
    for frame, points in frame_points:
        for role in roles_of(frame.key, frame.foreign):
            for cik, start, end, value in points:
                kind = companies.setdefault(cik, {"q": {}, "y": {}})[frame.kind]
                kind.setdefault(frame.label, []).append((role, frame.key, frame.currency, start, end, value))
    return companies


def canonical_period(entries):
    """
    One label's values → {start, end, values: {role: {key: (value, currency)}}} on the dates most
    values share (EPS first on ties); values more than ALIGN_DAYS off those dates are dropped.
    """
    dates = Counter((start, end) for _, _, _, start, end, _ in entries)
    eps_dates = {(start, end) for role, _, _, start, end, _ in entries if role in ("epsD", "epsB")}
    start, end = max(dates, key=lambda pair: (dates[pair], pair in eps_dates, pair[1]))
    values = {}
    for role, key, currency, s, e, value in entries:
        if abs((s - start).days) > ALIGN_DAYS or abs((e - end).days) > ALIGN_DAYS:
            continue
        values.setdefault(role, {}).setdefault(key, (value, currency))
    return {"start": start, "end": end, "values": values, "derived": False}


def first_shares(period):
    shares = period["values"].get("shares") or {}
    for key in ROLE_ORDER["shares"]:
        if key in shares and shares[key][0] > 0:
            return shares[key][0]
    return None


def calendar_quarter(start, end):
    middle = start + (end - start) / 2
    return quarter_number(middle.year, (middle.month - 1) // 3 + 1)


def derive_q4(quarters, years):
    """
    Adds Q4 = year - (Q1 + Q2 + Q3) to `quarters` ({number: period}) for the fiscal years of `years`
    whose first three quarters are there and the fourth is not (see the module notes). → labels added.
    """
    added = []
    for year in years.values():
        inside = sorted(
            (p for p in quarters.values()
             if p["start"] >= year["start"] - timedelta(days=ALIGN_DAYS) and p["end"] <= year["end"] + timedelta(days=ALIGN_DAYS)),
            key=lambda p: p["start"],
        )
        if len(inside) != 3:
            continue
        q1, q2, q3 = inside
        if abs((q1["start"] - year["start"]).days) > ALIGN_DAYS:
            continue
        if any(abs((b["start"] - (a["end"] + timedelta(days=1))).days) > ALIGN_DAYS for a, b in ((q1, q2), (q2, q3))):
            continue
        start, end = q3["end"] + timedelta(days=1), year["end"]
        if not QUARTER_DAYS[0] <= (end - start).days <= QUARTER_DAYS[1]:
            continue
        label = calendar_quarter(start, end)
        if label in quarters:
            continue
        values = {}
        for role in ("rev", "ni", "epsD", "epsB"):
            if role in ("epsD", "epsB"):
                shares = [first_shares(p) for p in (year, q1, q2, q3)]
                if None in shares or max(shares) / min(shares) > Q4_SHARE_MATCH:
                    continue
            for key, (total, currency) in (year["values"].get(role) or {}).items():
                pieces = [(p["values"].get(role) or {}).get(key) for p in inside]
                if any(piece is None or piece[1] != currency for piece in pieces):
                    continue
                value = total - sum(piece[0] for piece in pieces)
                if role == "rev" and value < 0:
                    continue
                values.setdefault(role, {})[key] = (round(value, 4), currency)
        if not values:
            continue
        shares = (q3["values"].get("shares") or {})
        if shares:
            values["shares"] = dict(shares)
        quarters[label] = {"start": start, "end": end, "values": values, "derived": True}
        added.append(label)
    return added


def as_growth_period(number, period, quarterly):
    """→ the period dict build_growth.yoy_series reads (epsD / epsB / rev are {key: (value, currency)})."""
    values = period["values"]
    return {
        "end": period["end"].isoformat(), "day": period["end"],
        "fy": number // 4 if quarterly else number, "fq": number % 4 + 1 if quarterly else None,
        "epsD": values.get("epsD") or None, "epsB": values.get("epsB") or None, "rev": values.get("rev") or None,
        "shares": first_shares(period), "src": "frames", "derived": period["derived"],
    }


def pick_pair(current, base, order):
    """The first concept (in `order`) both sides have in one currency → (now, before) or None."""
    if not current or not base:
        return None
    for key in order:
        if key in current and key in base and current[key][1] == base[key][1]:
            return current[key][0], base[key][0]
    return None


def revenue_pair(current, base):
    """Revenue YoY of one pair: first concept both sides have that is not a partial line (see notes)."""
    if not current or not base:
        return None
    for key in ROLE_ORDER["rev"]:
        if key not in current or key not in base or current[key][1] != base[key][1]:
            continue
        value, currency = current[key]
        largest = max(v for v, c in current.values() if c == currency)
        if largest > 0 and value < PARTIAL_REVENUE * largest:
            continue
        before = base[key][0]
        return None if growth.scale_suspect(value, before) else growth.growth_pct(value, before)
    return None


def frames_pair(current, base):
    """build_growth pair function for frames periods → (EPS yoy, turnaround, revenue yoy)."""
    value, turn = None, False
    if not growth.share_count_jump(current, base):
        for role in ("epsD", "epsB"):
            pair = pick_pair(current.get(role), base.get(role), ROLE_ORDER[role])
            if pair is None:
                continue
            now_value, before = pair
            if growth.scale_suspect(now_value, before):
                break
            if before > 0:
                value = growth.growth_pct(now_value, before)
            else:
                turn = now_value > 0
            break
    return value, turn, revenue_pair(current.get("rev"), base.get("rev"))


def company_periods(company):
    """One CIK's collected entries → ({quarter number: period}, {year: period}) with the derived Q4s."""
    quarters = {label: canonical_period(entries) for label, entries in company["q"].items()}
    years = {label: canonical_period(entries) for label, entries in company["y"].items()}
    derive_q4(quarters, {label: year for label, year in years.items() if not year_is_foreign(year)})
    return quarters, years


def company_blocks(company, today, periods=None):
    """One CIK's collected entries → (quarterly block, annual block, foreign flag, currency)."""
    quarters, years = periods or company_periods(company)
    q_periods = [as_growth_period(number, period, True) for number, period in quarters.items()]
    y_periods = [as_growth_period(number, period, False) for number, period in years.items()]
    q = growth.yoy_series(q_periods, True, growth.QUARTER_SLOTS, [], today, growth.STALE_QUARTER_DAYS, frames_pair)
    y = growth.yoy_series(y_periods, False, growth.YEAR_SLOTS, [], today, growth.STALE_ANNUAL_DAYS, frames_pair)
    foreign, currency = False, None
    if y is not None:
        latest = years[max(number for number, period in years.items() if period["end"].isoformat() == y["end"])]
        foreign = year_is_foreign(latest)
        currencies = {c for role in ("epsD", "epsB", "rev") for _, c in (latest["values"].get(role) or {}).values()}
        currency = next(iter(currencies)) if len(currencies) == 1 and currencies != {"USD"} else None
    return q, y, foreign, currency


def year_is_foreign(period):
    """An annual period without any us-gaap USD EPS / revenue value (only foreign-frame values)."""
    for role in ("epsD", "epsB", "rev"):
        for key, (_, currency) in (period["values"].get(role) or {}).items():
            if key.startswith(US + ":") and currency == "USD":
                return False
    return True


# ---------------------------------------------------------------------------------------------
# Statement history (us-history shards: the financials page / P/E river of stocks without a
# financials/<KEY>.json file, or with a shorter one)
# ---------------------------------------------------------------------------------------------
def short_concept(key):
    return key[len(US) + 1:] if key.startswith(US + ":") else key


def pick_value(values, role, currency):
    """First concept of `role` (ROLE_ORDER) in `currency` → (value, concept) or (None, None)."""
    found = values.get(role) or {}
    for key in ROLE_ORDER.get(role, []):
        if key in found and found[key][1] == currency:
            return found[key][0], key
    return None, None


def pick_revenue(values, currency):
    """Revenue of one period: the first REVENUE concept that is not a partial line (as revenue_pair)."""
    found = {key: value for key, (value, cur) in (values.get("rev") or {}).items() if cur == currency}
    if not found:
        return None, None
    largest = max(found.values())
    for key in ROLE_ORDER["rev"]:
        if key in found and not (largest > 0 and found[key] < PARTIAL_REVENUE * largest):
            return found[key], key
    return None, None


def period_currencies(period):
    return {currency for role in ("epsD", "epsB", "rev") for _, currency in (period["values"].get(role) or {}).values()}


def history_point(period, currency, concepts):
    """A canonical period → {end, derived, values: HISTORY_VALUES} in `currency`; None when it has none."""
    values = period["values"]
    revenue, rev_key = pick_revenue(values, currency)
    eps_d, d_key = pick_value(values, "epsD", currency)
    eps_b, b_key = pick_value(values, "epsB", currency)
    net, ni_key = pick_value(values, "ni", currency)
    point = {"revenue": revenue, "netIncome": net, "epsDiluted": eps_d, "epsBasic": eps_b,
             "dilutedShares": first_shares(period)}
    if all(point[name] is None for name in HISTORY_VALUES if name != "dilutedShares"):
        return None
    for name, key in (("revenue", rev_key), ("netIncome", ni_key), ("epsDiluted", d_key), ("epsBasic", b_key)):
        if key and short_concept(key) not in concepts.setdefault(name, []):
            concepts[name].append(short_concept(key))
    return {"end": period["end"], "derived": period["derived"], "values": point}


def median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def drop_scale_errors(points, issues):
    """
    A revenue / net income / share count about 10^3 or 10^6 times the median of its (up to four)
    nearest neighbours in the series was filed in another scale: it becomes None ("scale-dropped").
    """
    for name in ("revenue", "netIncome", "dilutedShares"):
        indices = [i for i, point in enumerate(points) if point["values"][name]]
        bad = []
        for position, i in enumerate(indices):
            around = indices[max(0, position - 2):position] + indices[position + 1:position + 3]
            if around and growth.scale_suspect(points[i]["values"][name],
                                               median(abs(points[j]["values"][name]) for j in around)):
                bad.append(i)
        for i in bad:
            points[i]["values"][name] = None
            issues.add("scale-dropped")


def nice_split(ratio):
    """The usual split (or reverse split) ratio that `ratio` is within SPLIT_TOLERANCE of, else None."""
    for nice in NICE_SPLITS:
        for target in (nice, 1 / nice):
            if abs(ratio / target - 1) <= SPLIT_TOLERANCE:
                return target
    return None


def same_business(newer, older):
    """Revenue continuity across a share-count jump (a merger moves revenue too); True without revenue."""
    now_rev, old_rev = newer["values"]["revenue"], older["values"]["revenue"]
    if not now_rev or not old_rev or now_rev <= 0 or old_rev <= 0:
        return True
    return SPLIT_REVENUE_BAND[0] <= now_rev / old_rev <= SPLIT_REVENUE_BAND[1]


def adjust_splits(points, issues):
    """
    Frames hold the last filed value of each period: a split restates only the periods that later
    filings repeat as comparatives (about a year of quarters, three years of annual figures), so a long
    series jumps from one share basis to another somewhere before the split. Walking from the newest
    period back, a weighted share count that changes >= SPLIT_JUMP between two neighbouring periods
    that have one is
    - an inferred split when the ratio is within SPLIT_TOLERANCE of a usual split ratio (NICE_SPLITS or
      the reverse) and revenue stays within SPLIT_REVENUE_BAND: every older period is put on the newest
      basis (EPS / k, shares x k); periods between the two without a share count lose their EPS (their
      basis is unknown). Event = (end of the first period on the newer basis, k), issue "split-inferred";
    - otherwise a basis break (merger, spin-off, an odd ratio): EPS and share counts of that period and
      every older one become None, issue "eps-basis-break". Nothing is guessed.
    → events [(end date, ratio)], newest first. Modifies `points`.
    """
    events, factor, newer, raw = [], 1.0, None, {}
    for i in range(len(points) - 1, -1, -1):
        values = points[i]["values"]
        shares = values["dilutedShares"]
        if shares and shares > 0:
            if newer is not None:
                ratio = raw[newer] / shares
                if ratio >= SPLIT_JUMP or ratio <= 1 / SPLIT_JUMP:
                    k = nice_split(ratio)
                    gap = range(i + 1, newer)
                    if k is None or not same_business(points[newer], points[i]):
                        for j in list(range(i + 1)) + list(gap):
                            values_j = points[j]["values"]
                            values_j["epsDiluted"] = values_j["epsBasic"] = None
                            if j <= i:
                                values_j["dilutedShares"] = None
                        issues.add("eps-basis-break")
                        return events
                    factor *= k
                    events.append((points[newer]["end"], round(k, 4)))
                    issues.add("split-inferred")
                    for j in gap:
                        points[j]["values"]["epsDiluted"] = points[j]["values"]["epsBasic"] = None
            raw[i] = shares
            newer = i
        if factor != 1.0:
            for name in ("epsDiluted", "epsBasic"):
                if values[name] is not None:
                    values[name] = values[name] / factor
            if values["dilutedShares"]:
                values["dilutedShares"] = values["dilutedShares"] * factor
    return events


def clean_number(value, digits):
    """Rounded for the file: amounts / share counts as integers, EPS to `digits` decimals."""
    if value is None:
        return None
    if digits == 0:
        return int(round(value))
    value = round(value, digits)
    return 0.0 if value == 0 else value


def history_series(periods, currency, quarterly, end_month, concepts, issues):
    """Canonical periods (one kind) → (rows in HISTORY_FIELDS order, split events), oldest first."""
    points = []
    for number in sorted(periods):
        period = periods[number]
        currencies = period_currencies(period)
        if currencies and currency not in currencies:
            issues.add("currency-changed")
            continue
        point = history_point(period, currency, concepts)
        if point is not None:
            point["number"] = number
            points.append(point)
    drop_scale_errors(points, issues)
    events = adjust_splits(points, issues)
    rows = []
    for point in points:
        end = point["end"].isoformat()
        values = point["values"]
        numbers = [clean_number(values["revenue"], 0), clean_number(values["netIncome"], 0),
                   clean_number(values["epsDiluted"], 4), clean_number(values["epsBasic"], 4),
                   clean_number(values["dilutedShares"], 0)]
        if quarterly:
            fy, fq = fiscal_quarter(end, end_month) if end_month else (point["number"] // 4, point["number"] % 4 + 1)
            if point["derived"]:
                issues.add("q4-derived")
            rows.append([end, fy, fq, 1 if point["derived"] else 0] + numbers)
        else:
            rows.append([end, fiscal_year(end)] + numbers)
    return rows, events


def company_history(periods, cik, foreign):
    """→ one company's history entry (see the module notes), or None without any usable period."""
    quarters, years = periods
    currencies = period_currencies(years[max(years)]) if years else set()
    currency = "USD" if not currencies or "USD" in currencies else sorted(currencies)[0]
    issues, concepts = set(), {}
    annual_ends = [period["end"].isoformat() for period in years.values() if currency in period_currencies(period)]
    end_month = fy_end_month(annual_ends)
    y_rows, y_events = history_series(years, currency, False, end_month, concepts, issues)
    q_rows, q_events = history_series({} if foreign else quarters, currency, True, end_month, concepts, issues)
    if not y_rows and not q_rows:
        return None
    events = []
    for end, ratio in sorted(q_events + y_events):
        # One split seen in both kinds (at different restatement boundaries) is listed once
        if not any(ratio == known and abs((end - day).days) <= 3 * 366 for day, known in events):
            events.append((end, ratio))
    return {
        "cik": cik, "cur": currency, "f": 1 if foreign else 0, "fyEnd": end_month,
        "split": [[day.isoformat(), ratio] for day, ratio in events],
        "issues": sorted(issues), "c": concepts, "q": q_rows, "y": y_rows,
    }


def build_rows(companies, tickers_by_cik, allowed, today, history=None):
    """
    → rows (FIELDS) for every ticker of every company with a usable block, sorted by symbol. A
    `history` dict is filled with {symbol: history entry} for the same tickers.
    """
    rows = []
    for cik, company in companies.items():
        keys = [key for key in tickers_by_cik.get(cik, []) if allowed is None or key in allowed]
        if not keys:
            continue
        periods = company_periods(company)
        q, y, foreign, currency = company_blocks(company, today, periods)
        if history is not None:
            entry = company_history(periods, cik, foreign)
            if entry is not None:
                for key in keys:
                    history[key] = entry
        if q is None and y is None:
            continue
        qb, yb = q or {}, y or {}
        for key in keys:
            rows.append([
                key, cik, qb.get("end"), yb.get("end"), qb.get("eps", []), qb.get("rev", []),
                yb.get("eps", []), yb.get("rev", []), qb.get("mask", 0), yb.get("mask", 0),
                qb.get("derived", 0), 1 if foreign else 0, currency,
            ])
    rows.sort(key=lambda row: row[0])
    return rows


def universe_keys(universe):
    """US keys of universe.json (BRK-B style), or None without a US list."""
    keys = {financials_key(symbol) for symbol, _ in universe_rows(universe, "US")}
    keys.discard(None)
    return keys or None


def keep_foreign_rows(rows, previous):
    """Rows of the stored file with a foreign annual block whose symbol this run did not produce."""
    if not isinstance(previous, dict) or previous.get("fields") != FIELDS or not isinstance(previous.get("rows"), list):
        return rows
    have = {row[0] for row in rows}
    kept = [row for row in previous["rows"] if isinstance(row, list) and len(row) == len(FIELDS)
            and row[FIELDS.index("f")] == 1 and row[0] not in have]
    return sorted(rows + kept, key=lambda row: row[0])


def keep_foreign_history(history, previous_history):
    """Stored foreign entries (annual blocks from the foreign frames) whose symbol this run did not produce."""
    for key, entry in (previous_history or {}).items():
        if key not in history and isinstance(entry, dict) and entry.get("f") == 1:
            history[key] = entry
    return history


def build_outputs(frame_points, tickers_payload, universe, today, now, previous=None, foreign=True, stats=None,
                  previous_history=None):
    """Pure core of a run → (the us-sec.json document, {symbol: history entry})."""
    tickers_by_cik, _ = parse_tickers(tickers_payload)
    companies = collect(frame_points)
    history = {}
    rows = build_rows(companies, tickers_by_cik, universe_keys(universe), today, history)
    foreign_at = today.isoformat() if foreign else (previous or {}).get("foreignAt")
    if not foreign:
        rows = keep_foreign_rows(rows, previous)
        keep_foreign_history(history, previous_history)
    return us_sec_document(rows, today, now, foreign_at, stats), history


def build_document(frame_points, tickers_payload, universe, today, now, previous=None, foreign=True, stats=None):
    """Pure core of a run → the us-sec.json document."""
    return build_outputs(frame_points, tickers_payload, universe, today, now, previous, foreign, stats)[0]


def us_sec_document(rows, today, now, foreign_at, stats):
    return {
        "version": VERSION,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asOf": today.isoformat(),
        "source": SOURCE,
        "foreignAt": foreign_at,
        "frames": stats or {},
        "fields": FIELDS,
        "rows": rows,
    }


def same_rows(old, new):
    return isinstance(old, dict) and old.get("fields") == new["fields"] and old.get("rows") == new["rows"]


# =============================================================================================
# I/O
# =============================================================================================
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def foreign_due(previous, today, max_age_days):
    if max_age_days <= 0:
        return True
    stamp = parse_day((previous or {}).get("foreignAt"))
    return stamp is None or (today - stamp).days >= max_age_days


def fetch_frames(client, plan, log=print):
    """→ ([(Frame, points)], stats). FetchError / TooManyFailures propagate (nothing is written)."""
    results, with_data, missing, dropped = [], 0, 0, 0
    for index, frame in enumerate(plan, 1):
        payload = client.get_json(frame.url, allow_404=True)
        if payload is None:
            missing += 1
            continue
        points, bad = parse_frame(payload, frame)
        dropped += bad
        if points:
            with_data += 1
        results.append((frame, points))
        if index % 50 == 0:
            log(f"frames {index}/{len(plan)} ({with_data} with data, {missing} not found)")
    stats = {"requested": len(plan), "withData": with_data, "notFound": missing, "droppedPoints": dropped,
             "keyFrames": sum(1 for frame, points in results if is_key_frame(frame, plan) and points)}
    return results, stats


def history_shard(key):
    """Shard of a symbol key: FNV-1a 32-bit of its ASCII text mod HISTORY_SHARDS (same as the frontend)."""
    value = 0x811C9DC5
    for byte in key.encode("ascii", "replace"):
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value % HISTORY_SHARDS


def shard_path(directory, index):
    return os.path.join(directory, f"{index:03d}.json")


def read_history(directory):
    """{symbol: entry} of every readable shard (unreadable / foreign-layout shards are skipped)."""
    entries = {}
    for index in range(HISTORY_SHARDS):
        document = read_json(shard_path(directory, index))
        if isinstance(document, dict) and document.get("version") == HISTORY_VERSION and isinstance(document.get("symbols"), dict):
            entries.update({key: entry for key, entry in document["symbols"].items() if isinstance(entry, dict)})
    return entries


def shard_text(index, entries, today, now):
    """One shard as text: valid JSON with one symbol per line (small diffs when one company changes)."""
    head = {
        "version": HISTORY_VERSION, "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "asOf": today.isoformat(),
        "source": SOURCE, "shard": index, "shards": HISTORY_SHARDS, "fields": HISTORY_FIELDS,
    }
    head_text = json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1]
    lines = [f"{json.dumps(key)}:{json.dumps(entries[key], ensure_ascii=False, separators=(',', ':'))}" for key in sorted(entries)]
    return head_text + ',"symbols":{\n' + ",\n".join(lines) + ("\n" if lines else "") + "}}\n"


def write_history(directory, history, today, now):
    """Writes the shards whose symbols changed → {"written": n, "unchanged": n, "bytes": total size}."""
    by_shard = {index: {} for index in range(HISTORY_SHARDS)}
    for key, entry in history.items():
        by_shard[history_shard(key)][key] = entry
    os.makedirs(directory, exist_ok=True)
    written = unchanged = size = 0
    for index, entries in by_shard.items():
        path = shard_path(directory, index)
        stored = read_json(path)
        if isinstance(stored, dict) and stored.get("version") == HISTORY_VERSION and stored.get("fields") == HISTORY_FIELDS \
                and stored.get("symbols") == json.loads(json.dumps(entries)):
            unchanged += 1
            size += os.path.getsize(path)
            continue
        text = shard_text(index, entries, today, now)
        with open(path + ".tmp", "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(path + ".tmp", path)
        written += 1
        size += len(text.encode("utf-8"))
    return {"written": written, "unchanged": unchanged, "bytes": size}


def summarize(document, history):
    """Checkable summary of the outputs (printed after a run, and by --summary for a review)."""
    rows = (document or {}).get("rows") or []
    col = {name: index for index, name in enumerate(FIELDS)}
    leading = Counter()
    for row in rows:
        if len(row) != len(FIELDS):
            continue
        for name, mask in (("epsY", "tY"), ("revY", "tY")):
            values = row[col[name]] or []
            count = 0
            for slot in range(len(values)):
                if values[slot] is None and not (name == "epsY" and (row[col[mask]] >> slot) & 1):
                    break
                count += 1
            for need in (1, 3, 5, 10):
                if count >= need:
                    leading[f"{name}>={need}"] += 1
    q_ends, y_ends, issues = [], [], Counter()
    deep = Counter()
    for entry in history.values():
        q_ends += [row[0] for row in entry.get("q") or []]
        y_ends += [row[0] for row in entry.get("y") or []]
        issues.update(entry.get("issues") or [])
        deep["quarters>=40"] += len(entry.get("q") or []) >= 40
        deep["years>=10"] += len(entry.get("y") or []) >= 10
        deep["years>=11"] += len(entry.get("y") or []) >= 11
        deep["foreign"] += entry.get("f") == 1
    return {
        "usSec": {"asOf": (document or {}).get("asOf"), "foreignAt": (document or {}).get("foreignAt"),
                  "rows": len(rows), "frames": (document or {}).get("frames"), "coverage": dict(sorted(leading.items()))},
        "history": {"symbols": len(history), "companies": len({entry.get("cik") for entry in history.values()}),
                    "quarterEnds": [min(q_ends), max(q_ends)] if q_ends else None,
                    "annualEnds": [min(y_ends), max(y_ends)] if y_ends else None,
                    **{key: deep[key] for key in ("quarters>=40", "years>=10", "years>=11", "foreign")},
                    "issues": dict(sorted(issues.items()))},
    }


def is_key_frame(frame, plan):
    """Diluted EPS of the KEY_QUARTERS calendar quarters before the newest planned one."""
    newest = max(request.label for request in plan if request.kind == "q")
    return (frame.key == CORE_CONCEPTS["epsD"][0] and frame.kind == "q" and not frame.foreign
            and newest - KEY_QUARTERS <= frame.label < newest)


def run(args, client=None, now=None, log=print):
    from seed_financials_sec import TICKERS_URL, FetchError, SecClient, TooManyFailures, resolve_user_agent

    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    today = now.date()
    report_dir = args.report_dir
    out = args.out or os.path.join(report_dir, "fundamentals", "us-sec.json")
    history_dir = getattr(args, "history_dir", None) or os.path.join(os.path.dirname(out), "us-history")
    previous = read_json(out)
    if getattr(args, "summary", False):
        log(json.dumps(summarize(previous, read_history(history_dir)), ensure_ascii=False))
        return 0
    universe = read_json(os.path.join(report_dir, "universe.json"))
    foreign = not args.no_foreign and foreign_due(previous, today, args.foreign_max_age_days)
    user_agent = resolve_user_agent(os.environ)
    probe = client or SecClient(user_agent, backoff=(5,), max_failures=3)
    try:
        tickers = probe.get_json(TICKERS_URL)
    except (FetchError, TooManyFailures) as error:
        if "HTTP 403" in str(error):
            log(f"::warning::SEC refused the request (403; GitHub-hosted runners are blocked by SEC). "
                f"us-sec.json kept as it is; refresh it with a local run. ({error})")
            return 0
        log(f"::warning::SEC company_tickers.json failed, us-sec.json kept: {error}")
        return 1
    client = client or SecClient(user_agent)
    plan = frame_plan(today, foreign)
    log(f"SEC frames: {len(plan)} requests ({'with' if foreign else 'without'} the foreign part)")
    try:
        frame_points, stats = fetch_frames(client, plan, log)
    except (FetchError, TooManyFailures, FrameFormatError) as error:
        log(f"::warning::SEC frames stopped, us-sec.json kept: {error}")
        return 1
    if stats["keyFrames"] < KEY_QUARTERS:
        log(f"::warning::only {stats['keyFrames']} of the {KEY_QUARTERS} key diluted-EPS frames had data, us-sec.json kept")
        return 1
    previous_history = read_history(history_dir)
    document, history = build_outputs(frame_points, tickers, universe, today, now, previous, foreign, stats,
                                      previous_history)
    old_rows = len(previous.get("rows") or []) if isinstance(previous, dict) else 0
    if old_rows and len(document["rows"]) < MIN_ROW_SHARE * old_rows and not args.force:
        log(f"::warning::{len(document['rows'])} rows against {old_rows} stored: not written (use --force)")
        return 1
    if previous_history and len(history) < MIN_ROW_SHARE * len(previous_history) and not args.force:
        log(f"::warning::{len(history)} history symbols against {len(previous_history)} stored: not written (use --force)")
        return 1
    if same_rows(previous, document):
        result = {"rows": len(document["rows"]), "written": False}
        document = previous
    else:
        size = write_atomic(out, document)
        by_part = Counter("foreign" if row[FIELDS.index("f")] else "us" for row in document["rows"])
        result = {"rows": len(document["rows"]), "written": True, "bytes": size, "parts": by_part}
    result["history"] = write_history(history_dir, history, today, now)
    log(json.dumps({**result, **stats}))
    log("SUMMARY " + json.dumps(summarize(document, history), ensure_ascii=False))
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="SEC XBRL frames → docs/report/fundamentals/us-sec.json")
    parser.add_argument("--report-dir", default=os.path.join(REPO_ROOT, "docs", "report"))
    parser.add_argument("--out", default=None)
    parser.add_argument("--no-foreign", action="store_true", help="skip the foreign-issuer annual frames")
    parser.add_argument("--foreign-max-age-days", type=int, default=0,
                        help="re-fetch the foreign part only when the stored one is at least this old (0 = always)")
    parser.add_argument("--history-dir", default=None,
                        help="statement history shards (default: us-history/ next to --out)")
    parser.add_argument("--force", action="store_true", help="write even when the row count dropped a lot")
    parser.add_argument("--summary", action="store_true",
                        help="only print the summary of the stored files (offline, for reviewing a commit)")
    return parser.parse_args(argv)


def main(argv=None):
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())

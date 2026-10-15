"""
Builds docs/report/fundamentals/us-sec.json: EPS / revenue YoY (quarterly and annual) of the whole US
list (docs/report/universe.json) from SEC XBRL frames. One frames request returns every company's value
of one concept for one calendar period
(https://data.sec.gov/api/xbrl/frames/<taxonomy>/<concept>/<unit>/CY<yyyy>[Q<q>].json), so the whole
market costs a few hundred requests instead of one per company. build_growth.py merges the rows into
docs/report/growth.json (src "frames").

  python atlas/scripts/build_fundamentals_sec.py [--no-foreign] [--foreign-max-age-days N]
         [--report-dir docs/report] [--out docs/report/fundamentals/us-sec.json]
  Env: SEC_USER_AGENT (default: seed_financials_sec.DEFAULT_USER_AGENT, a contact address as SEC asks).

Where it runs: SEC has answered 403 to GitHub-hosted runners whatever the User-Agent (see atlas.yml).
The workflow still tries: the first request (company_tickers.json) decides within seconds, and a refusal
keeps the stored file (exit 0 with a warning). A local run (`python atlas/scripts/build_fundamentals_sec.py`
from the repository, ~1-2 minutes, ~5 more with the foreign part) followed by a commit of
docs/report/fundamentals/us-sec.json refreshes it by hand. SEC fair access: User-Agent with contact,
<= ~7.5 requests per second, backoff on 403 / 429 / 5xx (seed_financials_sec.SecClient).

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
Safety: the file is not written when a frame failed (other than 404 = no data), when one of the key
frames (diluted EPS of the four calendar quarters before the newest one: thousands of filers each) is
empty or missing, or when the row count drops below MIN_ROW_SHARE of the stored one; only
written when the rows changed. Exit codes: 0 written / unchanged / SEC refused (warning), 1 failed.
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import build_growth as growth
from build_financials import financials_key, finite_number, market_of, universe_rows
from build_valuation_us import read_json, write_atomic

VERSION = 1
SOURCE = "SEC EDGAR XBRL frames (data.sec.gov/api/xbrl/frames)"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/{unit}/{period}.json"
FIELDS = ["symbol", "cik", "qEnd", "yEnd", "epsQ", "revQ", "epsY", "revY", "tQ", "tY", "dQ", "f", "cur"]
QUARTER_FRAMES = 11
ANNUAL_FRAMES = 6
FOREIGN_YEARS = 4
QUARTER_DAYS = (80, 120)
ANNUAL_DAYS = (350, 380)
ALIGN_DAYS = 7
Q4_SHARE_MATCH = 1.25
PARTIAL_REVENUE = 0.5
MIN_ROW_SHARE = 0.5
KEY_QUARTERS = 4

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
        for role in ("rev", "epsD", "epsB"):
            if role != "rev":
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


def company_blocks(company, today):
    """One CIK's collected entries → (quarterly block, annual block, foreign flag, currency)."""
    quarters = {label: canonical_period(entries) for label, entries in company["q"].items()}
    years = {label: canonical_period(entries) for label, entries in company["y"].items()}
    derive_q4(quarters, {label: year for label, year in years.items() if not year_is_foreign(year)})
    q_periods = [as_growth_period(number, period, True) for number, period in quarters.items()]
    y_periods = [as_growth_period(number, period, False) for number, period in years.items()]
    q = growth.yoy_series(q_periods, True, growth.QUARTER_SLOTS, [], today, growth.STALE_QUARTER_DAYS, frames_pair)
    y = growth.yoy_series(y_periods, False, growth.RECENT_YEAR_SLOTS, [], today, growth.STALE_ANNUAL_DAYS, frames_pair)
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


def build_rows(companies, tickers_by_cik, allowed, today):
    """→ rows (FIELDS) for every ticker of every company with a usable block, sorted by symbol."""
    rows = []
    for cik, company in companies.items():
        keys = [key for key in tickers_by_cik.get(cik, []) if allowed is None or key in allowed]
        if not keys:
            continue
        q, y, foreign, currency = company_blocks(company, today)
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


def build_document(frame_points, tickers_payload, universe, today, now, previous=None, foreign=True, stats=None):
    """Pure core of a run → the us-sec.json document."""
    tickers_by_cik, _ = parse_tickers(tickers_payload)
    companies = collect(frame_points)
    rows = build_rows(companies, tickers_by_cik, universe_keys(universe), today)
    foreign_at = today.isoformat() if foreign else (previous or {}).get("foreignAt")
    if not foreign:
        rows = keep_foreign_rows(rows, previous)
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
    previous = read_json(out)
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
    document = build_document(frame_points, tickers, universe, today, now, previous, foreign, stats)
    old_rows = len(previous.get("rows") or []) if isinstance(previous, dict) else 0
    if old_rows and len(document["rows"]) < MIN_ROW_SHARE * old_rows and not args.force:
        log(f"::warning::{len(document['rows'])} rows against {old_rows} stored: not written (use --force)")
        return 1
    if same_rows(previous, document):
        log(json.dumps({"rows": len(document["rows"]), "written": False, **stats}))
        return 0
    size = write_atomic(out, document)
    by_part = Counter("foreign" if row[FIELDS.index("f")] else "us" for row in document["rows"])
    log(json.dumps({"rows": len(document["rows"]), "written": True, "bytes": size, "parts": by_part, **stats}))
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="SEC XBRL frames → docs/report/fundamentals/us-sec.json")
    parser.add_argument("--report-dir", default=os.path.join(REPO_ROOT, "docs", "report"))
    parser.add_argument("--out", default=None)
    parser.add_argument("--no-foreign", action="store_true", help="skip the foreign-issuer annual frames")
    parser.add_argument("--foreign-max-age-days", type=int, default=0,
                        help="re-fetch the foreign part only when the stored one is at least this old (0 = always)")
    parser.add_argument("--force", action="store_true", help="write even when the row count dropped a lot")
    return parser.parse_args(argv)


def main(argv=None):
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())

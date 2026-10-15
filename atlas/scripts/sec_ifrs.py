"""
Foreign private issuers (20-F / 40-F filers that report IFRS) → docs/report/financials/<KEY>.json, from
SEC EDGAR companyfacts (taxonomy "ifrs-full"). seed_financials_sec.py --foreign calls build_document for
the tickers its us-gaap extraction skips as "foreign-filer", "no-us-gaap" or "non-usd" (see seed_ticker);
a file that already exists is never touched by this module.

The first part is the pure core (no network): build_document(companyfacts, key, now) → (document, reason).
The second part plans a --foreign run from the local files only (universe, the US backfill progress file
and the check state), so a dry run needs no network.

Concept table IFRS_CONCEPTS (canonical field ← ifrs-full concepts). For each period the FIRST candidate
with a value wins; a "fallback" candidate only fills what the earlier ones lack, and such a period is
flagged (quality issue "ifrs-fallback"):
  revenue            Revenue, RevenueFromContractsWithCustomers
  costOfRevenue      CostOfSales
  grossProfit        GrossProfit
  operatingIncome    ProfitLossFromOperatingActivities
  pretaxIncome       ProfitLossBeforeTax
  taxProvision       IncomeTaxExpenseContinuingOperations
  netIncome          ProfitLossAttributableToOwnersOfParent, fallback ProfitLoss (incl. non-controlling interests)
  epsBasic           BasicEarningsLossPerShare          (unit <CUR>/shares)
  epsDiluted         DilutedEarningsLossPerShare        (unit <CUR>/shares)
  interestExpense    InterestExpense
  operatingCashFlow  CashFlowsFromUsedInOperatingActivities
  capex              PurchaseOfPropertyPlantAndEquipment, PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities
  dividendsPaid      DividendsPaidClassifiedAsFinancingActivities
  buybacks           PaymentsToAcquireOrRedeemEntitysShares
  equity             EquityAttributableToOwnersOfParent, fallback Equity (incl. non-controlling interests)
  totalAssets        Assets
  cash               CashAndCashEquivalents
Outflows (capex, dividendsPaid, buybacks) are stored as positive amounts whatever sign the filer used.
Balance-sheet fields (equity, totalAssets, cash) are instants; the others are durations.
Not mapped, therefore always null: dilutedShares, totalDebt (no single ifrs-full total), and the
Yahoo-only operatingIncomeReported / fcfYahoo. Nothing is derived to fill a gap (no Q4 subtraction,
no gross profit = revenue − cost of sales).

Periods and currency:
- Annual: facts of 20-F / 40-F forms lasting 350–380 days; instants (balance sheet) of the same forms at
  the fiscal year end. A period exists where revenue or net income does.
- Quarterly: 6-K (and 6-K/A) facts lasting 80–120 days only; half-year (≈180 days) and year-to-date
  values are never split into quarters. Instants of 6-K forms at quarter ends. Quarter ends within
  SAME_PERIOD_DAYS of an annual end are dropped (the annual period covers them).
- Several facts for one period: the latest filed wins (restatements in later filings), ties by accession.
- Reporting currency: the money unit (a three-letter code) that the most annual period ends are reported
  in, ties → the unit filed most recently. Only that currency is used, so one field is never mixed
  across currencies; the other units and their counts are recorded in document["ifrsCurrencies"] when
  there is more than one. EPS units are "<currency>/shares".
- Quality: issue codes "ifrs" (always) and "ifrs-fallback"; the usual gates of seed_financials_sec apply
  (fewer than GATE_YEARS annual points of revenue / net income / operating cash flow / equity → partial).
  Splits are not checked (no Yahoo here): per-share values are as the filer reported them.

Skip reasons (build_document → reason): "not-us-key", "no-ifrs" (no ifrs-full facts at all),
"ifrs-no-usable-facts" (ifrs-full facts, but no annual money fact of a mapped concept: a concept name to
check against the taxonomy), "short-history" (fewer than MIN_ANNUAL_PERIODS annual periods).
"""
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timezone
from typing import NamedTuple

from build_financials import (
    ISSUES, KEEP_ANNUAL, KEEP_QUARTERLY, SAME_PERIOD_DAYS, VERSION, clean_value, financials_key, fiscal_quarter,
    fiscal_year, fy_end_month, market_of, parse_day, period, quality, write_section,
)
from build_valuation_us import finite_number, read_json, write_atomic

SOURCE = "SEC EDGAR companyfacts (IFRS)"
SRC = "sec"
TAXONOMY = "ifrs-full"
ANNUAL_FORMS = frozenset({"20-F", "20-F/A", "40-F", "40-F/A"})
INTERIM_FORMS = frozenset({"6-K", "6-K/A"})
ANNUAL_DAYS = (350, 380)
QUARTER_DAYS = (80, 120)
MIN_ANNUAL_PERIODS = 2
GATE_YEARS = 4
GATE_FIELDS = ("revenue", "netIncome", "operatingCashFlow", "equity")
LEVEL_RANK = {"limited": 0, "partial": 1, "good": 2}
# Statuses of fundamentals/us-market-progress.json issuers that the --foreign run looks at
FOREIGN_SKIPS = frozenset({"foreign-filer", "no-us-gaap", "non-usd"})
EXISTENCE_FIELDS = ("revenue", "netIncome")
PER_SHARE_FIELDS = frozenset({"epsBasic", "epsDiluted"})
INSTANT_FIELDS = frozenset({"equity", "totalAssets", "cash"})
OUTFLOW_FIELDS = frozenset({"capex", "dividendsPaid", "buybacks"})
CURRENCY_UNIT = re.compile(r"[A-Z]{3}")

# canonical field → ifrs-full concepts: (concept, "primary" | "fallback"), in priority order (see docstring)
IFRS_CONCEPTS = {
    "revenue": (("Revenue", "primary"), ("RevenueFromContractsWithCustomers", "primary")),
    "costOfRevenue": (("CostOfSales", "primary"),),
    "grossProfit": (("GrossProfit", "primary"),),
    "operatingIncome": (("ProfitLossFromOperatingActivities", "primary"),),
    "pretaxIncome": (("ProfitLossBeforeTax", "primary"),),
    "taxProvision": (("IncomeTaxExpenseContinuingOperations", "primary"),),
    "netIncome": (("ProfitLossAttributableToOwnersOfParent", "primary"), ("ProfitLoss", "fallback")),
    "epsBasic": (("BasicEarningsLossPerShare", "primary"),),
    "epsDiluted": (("DilutedEarningsLossPerShare", "primary"),),
    "interestExpense": (("InterestExpense", "primary"),),
    "operatingCashFlow": (("CashFlowsFromUsedInOperatingActivities", "primary"),),
    "capex": (
        ("PurchaseOfPropertyPlantAndEquipment", "primary"),
        ("PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "primary"),
    ),
    "dividendsPaid": (("DividendsPaidClassifiedAsFinancingActivities", "primary"),),
    "buybacks": (("PaymentsToAcquireOrRedeemEntitysShares", "primary"),),
    "equity": (("EquityAttributableToOwnersOfParent", "primary"), ("Equity", "fallback")),
    "totalAssets": (("Assets", "primary"),),
    "cash": (("CashAndCashEquivalents", "primary"),),
}
_CONCEPTS = frozenset(concept for candidates in IFRS_CONCEPTS.values() for concept, _ in candidates)


class Fact(NamedTuple):
    start: object      # datetime.date, or None for an instant
    end: object        # datetime.date
    value: object      # int or float as filed
    filed: str
    accn: str
    form: str

    def rank(self):
        """Larger is preferred: the latest filing, then the accession number (a total order)."""
        return (self.filed, self.accn, self.form, float(self.value), self.end.isoformat())


@dataclass
class IfrsResult:
    """
    currency: the reporting currency (None without money facts); currencies: {unit: annual period ends}
    of every money unit seen; annual / quarterly: [{"end", "values", "concepts": {field: concept},
    "fallback": bool}] ascending (quarterly already without the annual-overlap ends); concepts:
    {field: sorted concepts used}; fallback: any period took a fallback concept; skip: None or the reason.
    """
    currency: str | None = None
    currencies: dict = field(default_factory=dict)
    annual: list = field(default_factory=list)
    quarterly: list = field(default_factory=list)
    concepts: dict = field(default_factory=dict)
    fallback: bool = False
    skip: str | None = None


def _fact(point):
    if not isinstance(point, dict) or point.get("segment") is not None:
        return None
    end = parse_day(point.get("end"))
    if end is None or finite_number(point.get("val")) is None:
        return None
    start = None
    if point.get("start") is not None:
        start = parse_day(point.get("start"))
        if start is None or start >= end:
            return None
    text = [point.get(key) if isinstance(point.get(key), str) else "" for key in ("filed", "accn", "form")]
    return Fact(start, end, point["val"], text[0], text[1], text[2].strip().upper())


def _index(companyfacts):
    """{concept: {unit: [Fact]}} of the concepts of IFRS_CONCEPTS (empty without the ifrs-full taxonomy)."""
    facts = companyfacts.get("facts") if isinstance(companyfacts, dict) else None
    block = facts.get(TAXONOMY) if isinstance(facts, dict) else None
    if not isinstance(block, dict):
        return {}
    index = {}
    for concept in sorted(_CONCEPTS):
        entry = block.get(concept)
        units = entry.get("units") if isinstance(entry, dict) else None
        if not isinstance(units, dict):
            continue
        index[concept] = {
            unit: [fact for fact in map(_fact, points) if fact is not None]
            for unit, points in units.items() if isinstance(points, list)
        }
    return index


def _unit(field_name, currency):
    return f"{currency}/shares" if field_name in PER_SHARE_FIELDS else currency


def _in_days(fact, bounds):
    return bounds[0] <= (fact.end - fact.start).days <= bounds[1]


def _best_by_end(facts, keep):
    best = {}
    for fact in facts:
        if keep(fact) and (fact.end not in best or fact.rank() > best[fact.end].rank()):
            best[fact.end] = fact
    return best


def _table(index, cache, concept, unit):
    """The latest filed fact per period end, by kind of period, of one concept and unit (cached)."""
    key = (concept, unit)
    if key not in cache:
        facts = index.get(concept, {}).get(unit, [])
        cache[key] = {
            "annual": _best_by_end(facts, lambda f: f.form in ANNUAL_FORMS and f.start is not None
                                   and _in_days(f, ANNUAL_DAYS)),
            "quarterly": _best_by_end(facts, lambda f: f.form in INTERIM_FORMS and f.start is not None
                                      and _in_days(f, QUARTER_DAYS)),
            "annual_instant": _best_by_end(facts, lambda f: f.form in ANNUAL_FORMS and f.start is None),
            "quarterly_instant": _best_by_end(facts, lambda f: f.form in INTERIM_FORMS and f.start is None),
        }
    return cache[key]


def currency_counts(index):
    """({money unit: number of annual period ends}, {unit: latest filing date}) over the money concepts."""
    ends, latest = {}, {}
    for field_name, candidates in IFRS_CONCEPTS.items():
        if field_name in PER_SHARE_FIELDS:
            continue
        for concept, _ in candidates:
            for unit, facts in index.get(concept, {}).items():
                if not CURRENCY_UNIT.fullmatch(unit):
                    continue
                for fact in facts:
                    if fact.form not in ANNUAL_FORMS:
                        continue
                    if fact.start is not None and not _in_days(fact, ANNUAL_DAYS):
                        continue
                    ends.setdefault(unit, set()).add(fact.end)
                    latest[unit] = max(latest.get(unit, ""), fact.filed)
    return {unit: len(days) for unit, days in ends.items()}, latest


def choose_currency(counts, latest):
    """The unit with the most annual period ends; ties → the most recently filed, then the name."""
    best = max(counts.values())
    tied = [unit for unit, count in counts.items() if count == best]
    return max(tied, key=lambda unit: (latest.get(unit, ""), unit))


def _periods(index, cache, currency, frequency):
    """Period dicts of one frequency ("annual" / "quarterly"), ascending, in the chosen currency."""
    ends = set()
    for field_name, candidates in IFRS_CONCEPTS.items():
        if field_name in INSTANT_FIELDS:
            continue
        for concept, _ in candidates:
            ends |= set(_table(index, cache, concept, _unit(field_name, currency))[frequency])
    items = []
    for end in sorted(ends):
        values, concepts, fallback = {}, {}, False
        for field_name, candidates in IFRS_CONCEPTS.items():
            series = frequency + "_instant" if field_name in INSTANT_FIELDS else frequency
            for concept, role in candidates:
                fact = _table(index, cache, concept, _unit(field_name, currency))[series].get(end)
                if fact is None:
                    continue
                value = clean_value(abs(fact.value) if field_name in OUTFLOW_FIELDS else fact.value)
                if value is None:
                    continue
                values[field_name] = value
                concepts[field_name] = concept
                fallback = fallback or role == "fallback"
                break
        if any(name in values for name in EXISTENCE_FIELDS):
            items.append({"end": end.isoformat(), "values": values, "concepts": concepts, "fallback": fallback})
    return items


def extract(companyfacts):
    """companyfacts payload → IfrsResult (pure and deterministic; see the module docstring)."""
    result = IfrsResult()
    index = _index(companyfacts)
    if not index:
        result.skip = "no-ifrs"
        return result
    counts, latest = currency_counts(index)
    if not counts:
        result.skip = "ifrs-no-usable-facts"      # the taxonomy is there, none of the mapped money concepts is
        return result
    result.currencies = dict(sorted(counts.items()))
    result.currency = choose_currency(counts, latest)
    cache = {}
    result.annual = _periods(index, cache, result.currency, "annual")
    if len(result.annual) < MIN_ANNUAL_PERIODS:
        result.skip = "short-history"
    annual_ends = [parse_day(item["end"]) for item in result.annual]
    result.quarterly = [
        item for item in _periods(index, cache, result.currency, "quarterly")
        if not any(abs((parse_day(item["end"]) - day).days) <= SAME_PERIOD_DAYS for day in annual_ends)
    ]
    everything = result.annual + result.quarterly
    for field_name in IFRS_CONCEPTS:
        used = sorted({item["concepts"][field_name] for item in everything if field_name in item["concepts"]})
        if used:
            result.concepts[field_name] = used
    result.fallback = any(item["fallback"] for item in everything)
    return result


def _document(result, key, now):
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    annual = [period(p["end"], p["values"], SRC) for p in result.annual[-KEEP_ANNUAL:]]
    quarterly = [period(p["end"], p["values"], SRC) for p in result.quarterly[-KEEP_QUARTERLY:]]
    end_month = fy_end_month([p["end"] for p in annual])
    for item in annual:
        item["fy"] = fiscal_year(item["end"])
    for item in quarterly:
        item["fy"], item["fq"] = fiscal_quarter(item["end"], end_month or 12)
    grade = quality(annual, quarterly, set(), True)
    codes = {"ifrs"} | ({"ifrs-fallback"} if result.fallback else set())
    grade["issues"] += sorted(code for code in codes if code not in ISSUES)
    short = any(sum(1 for item in annual if item["values"].get(name) is not None) < GATE_YEARS for name in GATE_FIELDS)
    if short:
        gate = "limited" if len(annual) < 2 or "quarter-annual-mismatch" in grade["issues"] else "partial"
        if LEVEL_RANK[gate] < LEVEL_RANK[grade["level"]]:
            grade["level"] = gate
    document = {
        "version": VERSION,
        "symbol": key,
        "market": "US",
        "currency": result.currency,
        "foreignReporter": True,
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "checkedAt": now.date().isoformat(),
        "source": SOURCE,
        "fyEndMonth": end_month or 12,
        "annual": write_section(annual, {}, False),
        "quarterly": write_section(quarterly, {}, True),
        "split": {"adjusted": False, "events": []},
        "quality": grade,
        "secConcepts": {name: list(concepts) for name, concepts in result.concepts.items()},
    }
    if len(result.currencies) > 1:
        document["ifrsCurrencies"] = dict(result.currencies)
    return document


def build_document(companyfacts, key, now):
    """
    One ticker's document from its companyfacts payload → (document, None) or (None, skip reason).
    key: the financials key (US); now: the run's aware datetime (generatedAt / checkedAt).
    """
    if financials_key(key) != key or market_of(key) != "US":
        return None, "not-us-key"
    result = extract(companyfacts)
    if result.skip:
        return None, result.skip
    return _document(result, key, now), None


# ---------------------------------------------------------------------------------------------
# --foreign planning from local files (no network)
# ---------------------------------------------------------------------------------------------
STATE_VERSION = 1
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def default_state_path():
    """Where the check state lives: .cache/ (git-ignored), so it never reaches a commit."""
    return os.path.join(REPO_ROOT, ".cache", "sec-ifrs-state.json")


def load_progress(path):
    """The US market backfill's progress file (issuers, symbols, tickerMap); ValueError when unusable."""
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("issuers"), dict) or not isinstance(
            data.get("symbols"), dict):
        raise ValueError(f"{path} is not a US market progress file")
    return data


def progress_cik_map(progress):
    """{financials key: 10-digit CIK} from the progress file's SEC ticker map."""
    out = {}
    for symbol, cik in (progress.get("tickerMap") or {}).items():
        key = financials_key(symbol)
        if key and isinstance(cik, str) and cik.isdigit():
            out.setdefault(key, cik.zfill(10))
    return out


def universe_us_keys(universe):
    """[(financials key, raw symbol)] of the US universe, largest market cap first (ties by symbol)."""
    market = ((universe or {}).get("markets") or {}).get("US") or {}
    rows = market.get("rows") or []
    fields = market.get("fields") or []
    cap_index = fields.index("marketCap") if "marketCap" in fields else None
    entries = []
    for row in rows:
        if not isinstance(row, list) or not row or not isinstance(row[0], str):
            continue
        key = financials_key(row[0])
        if key is None:
            continue
        cap = row[cap_index] if cap_index is not None and cap_index < len(row) else None
        cap = finite_number(cap) or 0.0
        entries.append((key, row[0], cap))
    entries.sort(key=lambda entry: (-entry[2], entry[0]))
    seen, out = set(), []
    for key, raw, _ in entries:
        if key not in seen:
            seen.add(key)
            out.append((key, raw))
    return out


def load_state(path):
    data = read_json(path)
    if isinstance(data, dict) and data.get("version") == STATE_VERSION and isinstance(data.get("checked"), dict):
        return data
    return {"version": STATE_VERSION, "checked": {}}


def save_state(path, state):
    return write_atomic(path, state)


def plan_foreign(universe, progress, out_dir, state, keys=None):
    """
    Which tickers a --foreign run still has to check.

    keys None: the US universe ordered by market cap, restricted to the issuers the US backfill judged
    "foreign-filer", "no-us-gaap" or "non-usd". keys given (--tickers): exactly those, any status.
    → {"candidates": [(key, cik, status)], "hasFile": [key], "checked": [key], "todo": [(key, cik)],
       "byStatus": {status: count}} (candidates: every ticker considered, before the file / state filters).
    """
    issuers = progress["issuers"]
    symbols = progress["symbols"]
    cik_of = progress_cik_map(progress)
    if keys is None:
        candidates = []
        for key, raw in universe_us_keys(universe):
            record = symbols.get(raw) or symbols.get(key) or {}
            cik = record.get("cik") or cik_of.get(key)
            status = (issuers.get(cik) or {}).get("status") if cik else None
            if status in FOREIGN_SKIPS:
                candidates.append((key, cik, status))
    else:
        candidates = []
        for key in keys:
            cik = cik_of.get(key)
            candidates.append((key, cik, (issuers.get(cik) or {}).get("status") if cik else None))
    has_file, checked, todo = [], [], []
    for key, cik, _ in candidates:
        if os.path.exists(os.path.join(out_dir, key + ".json")):
            has_file.append(key)
        elif key in state["checked"]:
            checked.append(key)
        else:
            todo.append((key, cik))
    return {
        "candidates": candidates,
        "hasFile": has_file,
        "checked": checked,
        "todo": todo,
        "byStatus": dict(Counter(status for _, _, status in candidates)),
    }


def record_checked(state, key, result, detail, today_iso):
    """Remember a ticker whose answer is final (written, skipped, no companyfacts, no CIK)."""
    state["checked"][key] = {"result": result, "detail": detail, "checkedAt": today_iso}

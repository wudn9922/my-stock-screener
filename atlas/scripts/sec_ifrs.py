"""
Foreign private issuers (20-F / 40-F filers) → docs/report/financials/<KEY>.json, from SEC EDGAR
companyfacts. seed_financials_sec.py --foreign calls build_document for the tickers its us-gaap extraction
skips as "foreign-filer", "no-us-gaap" or "non-usd" (see seed_ticker). A ticker whose payload has no
ifrs-full facts at all ("no-ifrs") is then read with build_us_gaap_document (foreign filers that report
US GAAP: BABA, ASML, PDD, JD, BIDU, NTES, TCOM, BEKE, ...). A file that already exists is never touched by
--foreign. --foreign --upgrade merges the same documents into the Yahoo-only foreign files instead
(seed_financials_sec.upgrade_document); plan_upgrade / is_yahoo_only and the UPGRADE_FINAL answers are here.

Both reporting bases run through one extraction (extract, parameterised by a Basis):
- IFRS (taxonomy "ifrs-full", IFRS_BASIS): annual = 20-F / 40-F facts, quarterly = 6-K facts.
- US GAAP (taxonomy "us-gaap", US_GAAP_FOREIGN_BASIS): annual = 20-F / 40-F and 10-K facts, quarterly =
  6-K and 10-Q facts; the concepts are US_GAAP_CONCEPTS.

The first part is the pure core (no network): build_document / build_us_gaap_document(companyfacts, key,
now) → (document, reason). The second part plans a --foreign run from the local files only (universe, the US
backfill progress file and the check state), so a dry run needs no network.

Concept table IFRS_CONCEPTS (canonical field ← ifrs-full concepts; US_GAAP_CONCEPTS has the same fields
with us-gaap concepts). For each period the FIRST candidate with a value wins; a "fallback" candidate only
fills what the earlier ones lack, and such a period is flagged (quality issue "ifrs-fallback", or
"foreign-gaap-fallback" on the US GAAP basis):
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

Periods and currency (both bases):
- Annual: facts of the basis's annual forms lasting 350–380 days; instants (balance sheet) of the same
  forms at the fiscal year end. A period exists where revenue or net income does.
- Quarterly: the basis's interim forms (6-K / 6-K/A; US GAAP also 10-Q / 10-Q/A) with facts lasting
  80–120 days only; half-year (≈180 days) and year-to-date values are never split into quarters.
  Instants of the interim forms at quarter ends. Quarter ends within SAME_PERIOD_DAYS of an annual end
  are dropped (the annual period covers them).
- Several facts for one period: the latest filed wins (restatements in later filings), ties by accession.
- Reporting currency: the money unit (a three-letter code) that the most annual period ends are reported
  in, ties → the unit filed most recently. US GAAP counts the annual ends of revenue and net income only.
  Only that currency is used, so one field is never mixed across currencies; the other units and their
  counts are recorded in document["ifrsCurrencies"] / document["usGaapCurrencies"] when there is more
  than one. EPS units are "<currency>/shares" (per ordinary share, never per ADR).
- Quality: issue codes "ifrs" / "foreign-gaap" (always) and "ifrs-fallback" / "foreign-gaap-fallback";
  the usual gates of seed_financials_sec apply (fewer than GATE_YEARS annual points of revenue / net
  income / operating cash flow / equity → partial). Splits are not checked (no Yahoo here): per-share
  values are as the filer reported them.

Skip reasons (build_document / build_us_gaap_document → reason): "not-us-key"; "no-ifrs" / "no-us-gaap" (no
facts of the taxonomy at all); "ifrs-no-usable-facts" / "us-gaap-no-usable-facts" (the taxonomy is there, but
no annual money fact of a mapped concept: a concept name to check against the taxonomy); "short-history"
(fewer than MIN_ANNUAL_PERIODS annual periods).
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
US_GAAP_SOURCE = "SEC EDGAR companyfacts (US GAAP, foreign filer)"
SRC = "sec"
TAXONOMY = "ifrs-full"
US_GAAP_TAXONOMY = "us-gaap"
ANNUAL_FORMS = frozenset({"20-F", "20-F/A", "40-F", "40-F/A"})
INTERIM_FORMS = frozenset({"6-K", "6-K/A"})
US_GAAP_ANNUAL_FORMS = ANNUAL_FORMS | frozenset({"10-K", "10-K/A"})
US_GAAP_INTERIM_FORMS = INTERIM_FORMS | frozenset({"10-Q", "10-Q/A"})
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
# canonical field → us-gaap concepts of a foreign filer that reports US GAAP (same fields and roles)
US_GAAP_CONCEPTS = {
    "revenue": (
        ("Revenues", "primary"),
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "primary"),
        ("SalesRevenueNet", "primary"),
    ),
    "costOfRevenue": (("CostOfRevenue", "primary"), ("CostOfGoodsAndServicesSold", "primary")),
    "grossProfit": (("GrossProfit", "primary"),),
    "operatingIncome": (("OperatingIncomeLoss", "primary"),),
    "pretaxIncome": (
        ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "primary"),
        ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
         "primary"),
    ),
    "taxProvision": (("IncomeTaxExpenseBenefit", "primary"),),
    "netIncome": (("NetIncomeLoss", "primary"), ("ProfitLoss", "fallback")),
    "epsBasic": (("EarningsPerShareBasic", "primary"),),
    "epsDiluted": (("EarningsPerShareDiluted", "primary"),),
    "interestExpense": (("InterestExpense", "primary"),),
    "operatingCashFlow": (("NetCashProvidedByUsedInOperatingActivities", "primary"),),
    "capex": (("PaymentsToAcquirePropertyPlantAndEquipment", "primary"),),
    "dividendsPaid": (("PaymentsOfDividends", "primary"), ("PaymentsOfDividendsCommonStock", "primary")),
    "buybacks": (("PaymentsForRepurchaseOfCommonStock", "primary"),),
    "equity": (
        ("StockholdersEquity", "primary"),
        ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "fallback"),
    ),
    "totalAssets": (("Assets", "primary"),),
    "cash": (("CashAndCashEquivalentsAtCarryingValue", "primary"),),
}


@dataclass(frozen=True)
class Basis:
    """
    One reporting basis of the extraction: taxonomy, concept table, annual / interim forms, the source name
    and quality code of the document, the currency rule and the skip reasons of the taxonomy.

    code: the quality issue of every document ("ifrs", "foreign-gaap"); "<code>-fallback" flags a fallback.
    currency_fields: the money fields whose annual period ends pick the reporting currency (None: all of them).
    currency_key: the document key listing the other money units ("ifrsCurrencies", "usGaapCurrencies").
    no_facts / unusable: the skip reasons for a payload without the taxonomy / without a usable money fact.
    """
    taxonomy: str
    concepts: dict
    annual_forms: frozenset
    interim_forms: frozenset
    source: str
    code: str
    currency_fields: frozenset | None
    currency_key: str
    no_facts: str
    unusable: str

    @property
    def concept_names(self):
        return frozenset(concept for candidates in self.concepts.values() for concept, _ in candidates)


IFRS_BASIS = Basis(
    taxonomy=TAXONOMY, concepts=IFRS_CONCEPTS, annual_forms=ANNUAL_FORMS, interim_forms=INTERIM_FORMS,
    source=SOURCE, code="ifrs", currency_fields=None, currency_key="ifrsCurrencies", no_facts="no-ifrs",
    unusable="ifrs-no-usable-facts",
)
US_GAAP_FOREIGN_BASIS = Basis(
    taxonomy=US_GAAP_TAXONOMY, concepts=US_GAAP_CONCEPTS, annual_forms=US_GAAP_ANNUAL_FORMS,
    interim_forms=US_GAAP_INTERIM_FORMS, source=US_GAAP_SOURCE, code="foreign-gaap",
    currency_fields=frozenset({"revenue", "netIncome"}), currency_key="usGaapCurrencies", no_facts="no-us-gaap",
    unusable="us-gaap-no-usable-facts",
)


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


def _index(companyfacts, basis=IFRS_BASIS):
    """{concept: {unit: [Fact]}} of the concepts of the basis (empty without its taxonomy)."""
    facts = companyfacts.get("facts") if isinstance(companyfacts, dict) else None
    block = facts.get(basis.taxonomy) if isinstance(facts, dict) else None
    if not isinstance(block, dict):
        return {}
    index = {}
    for concept in sorted(basis.concept_names):
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


def _table(index, cache, concept, unit, basis=IFRS_BASIS):
    """The latest filed fact per period end, by kind of period, of one concept and unit (cached)."""
    key = (concept, unit)
    if key not in cache:
        facts = index.get(concept, {}).get(unit, [])
        cache[key] = {
            "annual": _best_by_end(facts, lambda f: f.form in basis.annual_forms and f.start is not None
                                   and _in_days(f, ANNUAL_DAYS)),
            "quarterly": _best_by_end(facts, lambda f: f.form in basis.interim_forms and f.start is not None
                                      and _in_days(f, QUARTER_DAYS)),
            "annual_instant": _best_by_end(facts, lambda f: f.form in basis.annual_forms and f.start is None),
            "quarterly_instant": _best_by_end(facts, lambda f: f.form in basis.interim_forms and f.start is None),
        }
    return cache[key]


def currency_counts(index, basis=IFRS_BASIS):
    """({money unit: number of annual period ends}, {unit: latest filing date}) over the basis's money concepts."""
    ends, latest = {}, {}
    for field_name, candidates in basis.concepts.items():
        if field_name in PER_SHARE_FIELDS:
            continue
        if basis.currency_fields is not None and field_name not in basis.currency_fields:
            continue
        for concept, _ in candidates:
            for unit, facts in index.get(concept, {}).items():
                if not CURRENCY_UNIT.fullmatch(unit):
                    continue
                for fact in facts:
                    if fact.form not in basis.annual_forms:
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


def _periods(index, cache, currency, frequency, basis=IFRS_BASIS):
    """Period dicts of one frequency ("annual" / "quarterly"), ascending, in the chosen currency."""
    ends = set()
    for field_name, candidates in basis.concepts.items():
        if field_name in INSTANT_FIELDS:
            continue
        for concept, _ in candidates:
            ends |= set(_table(index, cache, concept, _unit(field_name, currency), basis)[frequency])
    items = []
    for end in sorted(ends):
        values, concepts, fallback = {}, {}, False
        for field_name, candidates in basis.concepts.items():
            series = frequency + "_instant" if field_name in INSTANT_FIELDS else frequency
            for concept, role in candidates:
                fact = _table(index, cache, concept, _unit(field_name, currency), basis)[series].get(end)
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


def extract(companyfacts, basis=IFRS_BASIS):
    """companyfacts payload → IfrsResult (pure and deterministic; see the module docstring)."""
    result = IfrsResult()
    index = _index(companyfacts, basis)
    if not index:
        result.skip = basis.no_facts
        return result
    counts, latest = currency_counts(index, basis)
    if not counts:
        result.skip = basis.unusable              # the taxonomy is there, none of the mapped money concepts is
        return result
    result.currencies = dict(sorted(counts.items()))
    result.currency = choose_currency(counts, latest)
    cache = {}
    result.annual = _periods(index, cache, result.currency, "annual", basis)
    if len(result.annual) < MIN_ANNUAL_PERIODS:
        result.skip = "short-history"
    annual_ends = [parse_day(item["end"]) for item in result.annual]
    result.quarterly = [
        item for item in _periods(index, cache, result.currency, "quarterly", basis)
        if not any(abs((parse_day(item["end"]) - day).days) <= SAME_PERIOD_DAYS for day in annual_ends)
    ]
    everything = result.annual + result.quarterly
    for field_name in basis.concepts:
        used = sorted({item["concepts"][field_name] for item in everything if field_name in item["concepts"]})
        if used:
            result.concepts[field_name] = used
    result.fallback = any(item["fallback"] for item in everything)
    return result


def apply_gate(grade, annual):
    """
    The coverage gate of the foreign documents, in place: fewer than GATE_YEARS annual points of a GATE_FIELDS
    field caps the level at partial (limited with fewer than 2 years or a quarter-annual mismatch). Also used by
    seed_financials_sec's --upgrade on the merged periods.
    """
    short = any(sum(1 for item in annual if item["values"].get(name) is not None) < GATE_YEARS for name in GATE_FIELDS)
    if short:
        gate = "limited" if len(annual) < 2 or "quarter-annual-mismatch" in grade["issues"] else "partial"
        if LEVEL_RANK[gate] < LEVEL_RANK[grade["level"]]:
            grade["level"] = gate


def _document(result, key, now, basis=IFRS_BASIS):
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    annual = [period(p["end"], p["values"], SRC) for p in result.annual[-KEEP_ANNUAL:]]
    quarterly = [period(p["end"], p["values"], SRC) for p in result.quarterly[-KEEP_QUARTERLY:]]
    end_month = fy_end_month([p["end"] for p in annual])
    for item in annual:
        item["fy"] = fiscal_year(item["end"])
    for item in quarterly:
        item["fy"], item["fq"] = fiscal_quarter(item["end"], end_month or 12)
    grade = quality(annual, quarterly, set(), True)
    codes = {basis.code} | ({basis.code + "-fallback"} if result.fallback else set())
    grade["issues"] += sorted(code for code in codes if code not in ISSUES)
    apply_gate(grade, annual)
    document = {
        "version": VERSION,
        "symbol": key,
        "market": "US",
        "currency": result.currency,
        "foreignReporter": True,
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "checkedAt": now.date().isoformat(),
        "source": basis.source,
        "fyEndMonth": end_month or 12,
        "annual": write_section(annual, {}, False),
        "quarterly": write_section(quarterly, {}, True),
        "split": {"adjusted": False, "events": []},
        "quality": grade,
        "secConcepts": {name: list(concepts) for name, concepts in result.concepts.items()},
    }
    if len(result.currencies) > 1:
        document[basis.currency_key] = dict(result.currencies)
    return document


def _build(companyfacts, key, now, basis):
    if financials_key(key) != key or market_of(key) != "US":
        return None, "not-us-key"
    result = extract(companyfacts, basis)
    if result.skip:
        return None, result.skip
    return _document(result, key, now, basis), None


def build_document(companyfacts, key, now):
    """
    One ticker's IFRS document from its companyfacts payload → (document, None) or (None, skip reason).
    key: the financials key (US); now: the run's aware datetime (generatedAt / checkedAt).
    """
    return _build(companyfacts, key, now, IFRS_BASIS)


def build_us_gaap_document(companyfacts, key, now):
    """
    One ticker's US GAAP foreign-filer document (the us-gaap taxonomy, see US_GAAP_FOREIGN_BASIS) →
    (document, None) or (None, skip reason). Same arguments as build_document.
    """
    return _build(companyfacts, key, now, US_GAAP_FOREIGN_BASIS)


def build_foreign_document(companyfacts, key, now):
    """
    The foreign document of one ticker (--foreign and --upgrade): the IFRS document, or the US GAAP one when the
    payload has no ifrs-full facts at all ("no-ifrs") → (document, None) or (None, reason of the last basis tried).
    """
    document, reason = build_document(companyfacts, key, now)
    if document is None and reason == IFRS_BASIS.no_facts:
        document, reason = build_us_gaap_document(companyfacts, key, now)
    return document, reason


# ---------------------------------------------------------------------------------------------
# --foreign planning from local files (no network)
# ---------------------------------------------------------------------------------------------
STATE_VERSION = 1
# Rules of the recorded answers (record_checked stores them per ticker; an entry without one is rules 1).
# Rules 2: a ticker without ifrs-full facts is read with the US GAAP basis, so the "no-ifrs" answers of
# rules 1 are asked again (is_stale); every other answer keeps its meaning.
FOREIGN_RULES = 2
RETRY_UNDER_OLDER_RULES = frozenset({"no-ifrs"})
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
        if not isinstance(data.get("upgraded"), dict):
            data["upgraded"] = {}
        return data
    return {"version": STATE_VERSION, "checked": {}, "upgraded": {}}


def save_state(path, state):
    return write_atomic(path, state)


def is_stale(entry):
    """A recorded answer the current rules may change: a "no-ifrs" answer recorded under older rules."""
    return (isinstance(entry, dict) and entry.get("rules", 1) < FOREIGN_RULES
            and entry.get("detail") in RETRY_UNDER_OLDER_RULES)


def foreign_candidates(universe, progress, keys=None):
    """
    The tickers the --foreign family considers → [(key, cik, status)].

    keys None: the US universe ordered by market cap, restricted to the issuers the US backfill judged
    "foreign-filer", "no-us-gaap" or "non-usd". keys given (--tickers): exactly those, any status.
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
    return candidates


def plan_foreign(universe, progress, out_dir, state, keys=None):
    """
    Which tickers a --foreign run still has to check.

    keys: see foreign_candidates. A state entry recorded under older rules and still answering "no-ifrs"
    (is_stale) is checked again (its ticker is in "retry" too), the others stay checked.
    → {"candidates": [(key, cik, status)], "hasFile": [key], "checked": [key], "todo": [(key, cik)],
       "retry": [key], "byStatus": {status: count}} (candidates: every ticker considered, before the file /
       state filters).
    """
    candidates = foreign_candidates(universe, progress, keys)
    has_file, checked, todo, retry = [], [], [], []
    for key, cik, _ in candidates:
        if os.path.exists(os.path.join(out_dir, key + ".json")):
            has_file.append(key)
        elif key in state["checked"] and not is_stale(state["checked"][key]):
            checked.append(key)
        else:
            todo.append((key, cik))
            if key in state["checked"]:
                retry.append(key)
    return {
        "candidates": candidates,
        "hasFile": has_file,
        "checked": checked,
        "todo": todo,
        "retry": retry,
        "byStatus": dict(Counter(status for _, _, status in candidates)),
    }


def record_checked(state, key, result, detail, today_iso):
    """Remember a ticker whose answer is final (written, skipped, no companyfacts, no CIK), under FOREIGN_RULES."""
    state["checked"][key] = {"result": result, "detail": detail, "checkedAt": today_iso, "rules": FOREIGN_RULES}


# --upgrade: a Yahoo-only foreign file is merged with its SEC document (seed_financials_sec.upgrade_document).
# Its answers live in state["upgraded"], apart from the --foreign ones. A file that is merged (or got a currency
# note) is no longer Yahoo-only, so the file itself decides whether it is still to do; only the answers that leave
# the file untouched are recorded (UPGRADE_FINAL).
UPGRADE_FINAL = ("no-sec-data", "no-facts", "no-cik")


def is_yahoo_only(document):
    """A stored file with no SEC source: its source names no SEC and no annual period came from SEC."""
    source = document.get("source")
    annual = document.get("annual") if isinstance(document.get("annual"), dict) else {}
    sources = annual.get("src") or []
    return "SEC" not in str(source or "") and "sec" not in sources


def plan_upgrade(universe, progress, out_dir, state, keys=None):
    """
    Which foreign tickers --upgrade still has to merge (local files and state only, no network).

    candidates: foreign_candidates. noFile: no stored file (left to --foreign). upgraded: the stored file already
    holds SEC periods. checked: Yahoo-only, with a final answer in state["upgraded"]. unreadable: the stored file
    cannot be read. todo: Yahoo-only, still to merge → [(key, cik)]. byStatus: counts of the candidates' statuses.
    """
    candidates = foreign_candidates(universe, progress, keys)
    recorded = state.get("upgraded") or {}
    no_file, upgraded, checked, unreadable, todo = [], [], [], [], []
    for key, cik, _ in candidates:
        path = os.path.join(out_dir, key + ".json")
        if not os.path.exists(path):
            no_file.append(key)
            continue
        document = read_json(path)
        if not isinstance(document, dict):
            unreadable.append(key)
        elif not is_yahoo_only(document):
            upgraded.append(key)
        elif (recorded.get(key) or {}).get("result") in UPGRADE_FINAL:
            checked.append(key)
        else:
            todo.append((key, cik))
    return {
        "candidates": candidates,
        "noFile": no_file,
        "upgraded": upgraded,
        "checked": checked,
        "unreadable": unreadable,
        "todo": todo,
        "byStatus": dict(Counter(status for _, _, status in candidates)),
    }


def record_upgrade(state, key, result, detail, today_iso):
    """Remember a --upgrade answer that leaves the file untouched (UPGRADE_FINAL)."""
    state.setdefault("upgraded", {})[key] = {"result": result, "detail": detail, "checkedAt": today_iso}

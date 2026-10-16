"""
Seeds docs/report/financials/<KEY>.json (財報分析中心) with long statement history from SEC EDGAR
companyfacts (https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json).

The first part of this module is the pure core (no network, no files, no CLI; the SEC access / CLI
part starts at "I/O" near the end). It turns one companyfacts payload into
annual / quarterly periods (extract_periods) and merges them into the stored per-ticker document
(merge_into_document) with exactly the schema and helpers of build_financials.py, so the daily
Yahoo run (build_financials.update_document) keeps working on the result:

- Periods are written with src "sec". build_financials never replaces or removes SEC periods; it
  only fills their null fields and appends newer Yahoo periods.
- Values are raw reporting-currency units (USD), capex / dividendsPaid / buybacks positive
  outflows, EPS per share, dilutedShares a share count. operatingIncomeReported and fcfYahoo
  (Yahoo-only fields) are never written.

Extraction rules (see CONCEPTS for the us-gaap concepts per field):
- Units: USD for money, USD/shares for EPS, shares for dilutedShares; anything else is ignored.
- Several facts for the same period: the latest `filed` wins (restatements in later filings),
  ties broken by accession number. Facts whose `val` is not a finite number are ignored. Revenue
  concepts only: a later value below 15% of the earlier filed one is a narrow line item re-using
  the tag (CPT after ASC 842) and is not adopted (annual, 3-month and YTD values alike).
- Scale errors (every concept): a filed version 10^(3k) times another version of the same period is
  the number filed in thousands / millions as units; the version consistent with the neighbouring
  periods wins (_resolve_versions). Single filed values in another scale are left null, never
  rescaled (_Facts._clean_per_share): share counts below 100,000 or 10^2.5 away from the local share
  level (net income / EPS of the nearest periods, else the nearest counts), EPS 20x away from net
  income / shares. Real splits (<= 50:1) never reach these limits. Counts: SecResult.dropped.
- Concept candidates (series-level primary, see _concept_orders): for a field with several candidate
  concepts ONE primary concept is chosen for the whole series; the other candidates only fill the
  periods the primary lacks, in CONCEPTS order. The primary is the candidate that (1) for
  CROSS_CHECK_FIELDS minimises the fiscal years whose four quarters do not add up to the annual value
  (|Σ quarters − annual| > 2% for every filed version of it), (2) covers clearly more periods itself
  (annual + quarterly; > max(4, 10%) more than an earlier candidate), (3) comes first in CONCEPTS.
  This keeps a partial line item (e.g. a segment's "Revenues") from replacing the total in the
  years both exist, and keeps one scope over the series where the data allows it.
  Revenue also has total-revenue concepts of banks / brokers / utilities / lessors after the original
  four, and an optional Yahoo arbiter (extract_periods' `reference`, see _arbitrate): when the stored
  file's Yahoo revenue contradicts the series these rules give by more than 5% and another concept
  matches it (within 25%), that concept leads the order. Yahoo only picks the concept, every value is
  SEC's; the decision is stored (secConcepts.arbiter) so re-seeding repeats it.
- Annual: 10-K / 10-K/A / 10-KT / 10-KT/A facts lasting 350–380 days (52/53-week years included);
  an annual period exists where revenue or net income does. Balance-sheet (instant) fields at the
  fiscal-year end come from the 10-K (falling back to the same date in a 10-Q's comparative column).
- Quarterly: 3-month values (80–120 days: 12/13/14- and 16/17-week quarters) of 10-Q / 10-Q/A AND
  of 10-K-type forms (restated quarters and Q4 values that only a 10-K reports) form one pool, the
  latest filed per period wins. Otherwise the quarter is the difference of two year-to-date values
  (10-Q) with the same fiscal-year start (cash-flow items are only reported year-to-date), or the
  year-to-date value minus the earlier quarters of that fiscal year; a first quarter may also be a
  restated 6-month YTD − Q2. When a reported 3-month value and such a difference both exist, the
  more recently filed one wins (a difference is as recent as its oldest piece; one within 0.5% of
  the reported value keeps it).
  10-K 3-month values count only when the same filing reports the year containing the quarter, the
  value is not that year's own (a mis-tagged context, see _confirmed_quarter) and a Q4 is in the
  year's scope (within 25% of the annual value minus the first nine months, _q4_in_scope).
  Q4 = annual (10-K) − 9-month YTD (or − Q1..Q3) and is flagged "q4-derived", but only when the
  pieces are one version: Q1+Q2+Q3 within 1% of the 9-month YTD, and the annual value is the one the
  first 10-K filed after the YTD reported (within 1%); otherwise Q4 stays null. A derived value of a
  NON_NEGATIVE field below zero is never stored (outflows are subtracted as positive amounts).
  Per-share fields are never subtracted except Q4 EPS = annual EPS − (Q1+Q2+Q3 EPS) when all four
  are reported on one share basis (diluted shares, else net income / EPS, within SPLIT_MATCH) and the
  result has the sign of the Q4 net income; dilutedShares has no derived Q4. Nothing is derived unless
  every piece exists. After the split adjustment (below) a Q4 EPS still missing is derived once more, on the
  basis the periods now hold (build_financials.fill_q4_eps: the same rule, also used by repair_q4_eps.py on
  stored files), so a split restated in a later filing no longer blocks it.
- EPS fallbacks only fill periods without EarningsPerShareDiluted / -Basic: the combined
  EarningsPerShareBasicAndDiluted tag, then continuing-operations EPS where it equals net income /
  diluted shares (within 3%, i.e. no discontinued operations; never a derived Q4).
- Period ends within SAME_PERIOD_DAYS (5) of each other are one period. Quarter ends 6–79 days apart
  are two reporting calendars under one CIK (a merger's predecessor and the registrant): only the
  one filed later is kept (_drop_colliding_quarters). Annual periods are never dropped.
- totalDebt / cash: see _total_debt / _cash (judgement calls documented there). A current portion
  alone is never a total debt (null), nor is a partial balance next to full ones (_suspect_debt).
- Revenue scope: with a stored Yahoo revenue the SEC series must lie within a median 50% of it, else
  the file is not written at all (skip "revenue-scope-mismatch", _revenue_scope_error).

Merge rules (merge_into_document): SEC periods are capped at the newest 20 annual / 60 quarterly;
a SEC period replaces every stored period (SEC or Yahoo) labelled for it (±10 days annual, ±25 days
quarterly, nearest SEC period only, see _match_section; a Yahoo value > 2% off on revenue / net income
is recorded as a "yahoo-differs" revision). The latest SEC period takes the later Yahoo label it
replaced when that lies more than 5 days away (_aligned_end), so that the next Yahoo run does not add
it again. Stored Yahoo periods overlapping a SEC period (closer than 340 / 80 days: another calendar
after a fiscal-year change) are dropped, and after the fiscal labels a Yahoo period carrying a SEC
period's (fy, fq) goes too (_dedupe_labels). Every other stored period is kept, except stored SEC
quarters at the ends this extraction dropped as another calendar's quarters.
Fiscal labels, splits, revisions and quality are computed with build_financials' own helpers, then
the SEC-only issue codes and the quality gates are applied.

Splits: companyfacts per-share values are as originally reported, and only periods a later filing
restated (~3 years back) carry the post-split basis. Like build_financials.update_document, the
stored split events are applied first and, when diluted share counts still jump, the Yahoo chart
events are fetched once (fetch_splits) and applied (adjust_splits; Q4 quarters, which have no share
count, follow their annual period). SEC periods without a diluted share count (GOOGL / GOOG tag none
without a class dimension) take an implied one (net income / EPS) for this step only, never written
(_implied_shares), so their EPS is checked and adjusted too. A failed fetch leaves the values as
reported and flags the ticker split-check-pending. Q4 EPS is not derived when its four pieces are on
different share bases.
"""
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from typing import NamedTuple

from build_financials import (
    FIELDS,
    ISSUES,
    KEEP_ANNUAL,
    KEEP_QUARTERLY,
    MAX_CHECK_AGE_DAYS,
    MIN_SPLIT_RATIO,
    PER_SHARE_FIELDS,
    REPORT_DUE_DAYS,
    RESTATE_TOLERANCE,
    SAME_PERIOD_DAYS,
    SEC_CHECKED_KEY,
    SOURCE_MIXED,
    SPLIT_MATCH,
    VERSION,
    adjust_splits,
    adr_eps_factor,
    SEC_FOREIGN_ISSUES,
    Q4_ISSUE,
    clean_value,
    dedupe,
    days_between,
    effective,
    eps_inconsistent,
    financials_key,
    finite_number,
    fill_q4_eps,
    fiscal_quarter,
    fiscal_year,
    fy_end_month,
    market_of,
    merge_events,
    merge_revisions,
    merge_section,
    needs_refresh,
    parse_day,
    parse_splits,
    period,
    q4_eps,
    quality,
    read_sections,
    relative_change,
    repair_split_eps,
    resolve_eps_basis,
    revision,
    share_basis,
    share_jump,
    shares_of,
    write_section,
)

SRC = "sec"
CURRENCY = "USD"
ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT", "10-KT/A"})
QUARTERLY_FORMS = frozenset({"10-Q", "10-Q/A"})
# Annual / current reports of foreign private issuers (no 10-K / 10-Q)
FOREIGN_FORMS = frozenset({"20-F", "20-F/A", "40-F", "40-F/A", "6-K", "6-K/A"})
# Durations in days (end - start): 52/53-week years are 364/371, 13/14-week quarters 91/98
ANNUAL_DAYS = (350, 380)
# 12/13/14-week quarters (84/91/98 days) and the 16/17-week quarters of 4-4-4-4(5) calendars (112/119
# days: AZO, COST, DPZ, KR, PEP, HST). The upper bound stays far below YTD_DAYS[0], so a quarter can
# never be read as a 6-month YTD value (or the reverse).
QUARTER_DAYS = (80, 120)
YTD_DAYS = (160, 295)          # 6- and 9-month year-to-date values (24-28 and 36-40 weeks)
MIN_ANNUAL_PERIODS = 2         # fewer annual 10-K periods → no document
GATE_YEARS = 4                 # quality gate: annual points needed of each GATE_FIELDS field
GATE_FIELDS = ("revenue", "netIncome", "operatingCashFlow", "equity")
CONTINUING_TOLERANCE = 0.03    # the same, for a continuing-operations EPS standing in for the EPS
# SEC-only quality codes, appended (sorted) after build_financials' ISSUES codes
SEC_ISSUES = ("debt-approx", "eps-inconsistent", "foreign-filer", "q4-derived")
DERIVED = "derived"            # per-period quarterly column: 1 when a value was subtracted
CROSS_CHECK_TOLERANCE = 0.02   # concept choice: |Σ four quarters − annual| / |annual| of a consistent year
# Concept choice: a later candidate covers MORE periods only when it covers more than
# max(COVERAGE_GAIN_PERIODS, COVERAGE_GAIN_RATIO × the earlier one's periods) more (a period or two
# of difference must not switch e.g. net income to ProfitLoss, which includes noncontrolling interests)
# Fields whose fallback concept has a different definition (ProfitLoss and the equity total include the
# non-controlling interest; the EPS fallbacks see CONCEPTS): they keep the CONCEPTS order, the fallback
# only fills periods the primary lacks.
PRIORITY_ONLY_FIELDS = frozenset({"netIncome", "equity", "epsDiluted", "epsBasic"})
COVERAGE_GAIN_PERIODS = 4
COVERAGE_GAIN_RATIO = 0.10
Q4_TOLERANCE = 0.01            # Q4 derivation: Q1+Q2+Q3 vs 9M YTD, annual version vs the one the YTD saw
RESTATED_MIN_CHANGE = 0.005    # a newer difference replaces a reported 3-month value only if it differs more
# Revenue concepts: a later filing whose non-negative value is below this share of the earlier filed
# positive value of the same period re-uses the tag for a narrow line item (CPT: after ASC 842 the
# "revenue" tag of the old years holds 8.2M of management fees next to a 900M total) and is not adopted
NARROW_REFILE_RATIO = 0.15
# A 10-K's 3-month Q4 value must lie within this of (that 10-K's annual value − Q1..Q3 / 9-month YTD),
# else it is another scope (GE 2012: a segment's 11.8B quarter next to the 147B consolidated year)
Q4_SCOPE_TOLERANCE = 0.25
# Yahoo arbiter of the revenue concept (_arbitrate): years of overlap needed (1 when the reference
# only overlaps one SEC year), how much worse than the best candidate the default primary may be and
# still stay, and the median error above which no candidate is trusted (Yahoo defines it otherwise)
ARBITER_MIN_YEARS = 2
ARBITER_MARGIN = 0.05
ARBITER_MAX_ERROR = 0.25
# Revenue scope check (extract_periods): when the stored file's Yahoo revenue exists and the chosen SEC
# revenue series still misses it by a median relative error above this over the overlapping years (a
# bank whose only revenue tag is the ASC 606 fee line: 15-25% of the total), no document is written
REVENUE_SCOPE_MAX_ERROR = 0.50
# XBRL scale errors (a value filed in thousands / millions as units, e.g. 59,711,000,000 shares for 59.7M):
# a filed version whose value is 10^(3k) × another version's (k ≠ 0) within this tolerance of log10
UNIT_SHIFT_TOLERANCE = 0.05
NEIGHBOURS = 8                  # nearest other periods forming a local reference level
MIN_SHARE_COUNT = 100_000       # a diluted share count below this is a unit error (reported in thousands / millions)
# A share count this many powers of ten away from the local median of the other counts is a unit error;
# real splits (≤ 50:1) and share issues stay far below it
SHARE_OUTLIER_LOG = 2.5
EPS_OUTLIER_RATIO = 20          # EPS vs net income / diluted shares: beyond this factor one of them is wrong
EPS_MATERIAL_SHARE = 0.25       # an EPS "too small" for its net income only counts for a material net income
# Split detection on implied share counts (net income / EPS) of SEC periods without a diluted share count:
# only where cent rounding cannot move the implied count by more than ~5%
IMPLIED_MIN_EPS = 0.10
# Total debt: a quarter below this share of its neighbouring quarters, with another composition, is a
# partial balance (one line of the debt) rather than the total (_suspect_debt)
DEBT_DROP_RATIO = 0.5
# Stored Yahoo periods this close to a SEC period of the new extraction overlap it (another calendar after a
# fiscal-year change, FERG): never kept next to it
OVERLAP_DAYS = {"annual": 340, "quarterly": 80}

# field → [(us-gaap concept, kind)], in FIELDS order. kind is the concept's role in the field's rule:
# "value": candidates of one field; a series-level primary is chosen (_concept_orders), the others fill
# the periods it lacks in this order; totalDebt / cash are composites (see _total_debt / _cash) whose
# roles name the parts.
CONCEPTS = {
    "revenue": [
        ("Revenues", "value"),
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "value"),
        ("RevenueFromContractWithCustomerIncludingAssessedTax", "value"),
        ("SalesRevenueNet", "value"),
        # Total-revenue concepts of financial / utility filers (after the original four, so they only
        # change a series where those have no data, win the concept choice, or the Yahoo arbiter picks
        # them): brokers / card issuers / custody banks (MS, AXP, RJF, NTRS: total net revenues),
        # utilities (DTE: total operating revenues)
        ("RevenuesNetOfInterestExpense", "value"),
        ("RegulatedAndUnregulatedOperatingRevenue", "value"),
        # Lessors (CPT: total property revenue of a REIT). Elsewhere a narrow line item (GE, DTE: a
        # few % of revenue), so it is ARBITER_ONLY: used only as the primary the Yahoo arbiter chose
        ("OperatingLeaseLeaseIncome", "value"),
    ],
    "costOfRevenue": [("CostOfRevenue", "value"), ("CostOfGoodsAndServicesSold", "value")],
    "grossProfit": [("GrossProfit", "value")],
    "operatingIncome": [("OperatingIncomeLoss", "value")],
    "pretaxIncome": [
        ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "value"),
        ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
         "value"),
    ],
    "taxProvision": [("IncomeTaxExpenseBenefit", "value")],
    "netIncome": [("NetIncomeLoss", "value"), ("ProfitLoss", "value")],
    # EPS fallbacks (only for the periods the first concept lacks, see PRIORITY_ONLY_FIELDS): the combined
    # tag of filers whose basic and diluted EPS are equal (ASTS, ENPH, PPL), and continuing-operations EPS
    # of filers that tag the income-statement EPS line so (VRSN 2023+, MNST): "continuing" values are used
    # only when they equal net income / diluted shares (EPS_TOLERANCE), i.e. without discontinued operations
    "epsDiluted": [
        ("EarningsPerShareDiluted", "value"),
        ("EarningsPerShareBasicAndDiluted", "value"),
        ("IncomeLossFromContinuingOperationsPerDilutedShare", "continuing"),
    ],
    "epsBasic": [
        ("EarningsPerShareBasic", "value"),
        ("EarningsPerShareBasicAndDiluted", "value"),
        ("IncomeLossFromContinuingOperationsPerBasicShare", "continuing"),
    ],
    "dilutedShares": [("WeightedAverageNumberOfDilutedSharesOutstanding", "value")],
    "interestExpense": [("InterestExpense", "value")],
    "operatingCashFlow": [("NetCashProvidedByUsedInOperatingActivities", "value")],
    "capex": [
        ("PaymentsToAcquirePropertyPlantAndEquipment", "value"),
        ("PaymentsToAcquireProductiveAssets", "value"),
    ],
    "equity": [
        ("StockholdersEquity", "value"),
        ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "value"),
    ],
    "totalDebt": [
        ("LongTermDebtNoncurrent", "noncurrent"),
        ("LongTermDebt", "total-long-term"),
        ("DebtCurrent", "current"),
        ("LongTermDebtCurrent", "current-part"),
        ("ShortTermBorrowings", "current-part"),
        # the long-term debt lines of filers that tag them with finance leases (ACN, FE, CINF, TMO)
        ("LongTermDebtAndCapitalLeaseObligations", "noncurrent-with-leases"),
        ("LongTermDebtAndCapitalLeaseObligationsCurrent", "current-part-with-leases"),
    ],
    "cash": [
        ("CashAndCashEquivalentsAtCarryingValue", "base"),
        ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "base"),
        ("ShortTermInvestments", "investments"),
        ("MarketableSecuritiesCurrent", "investments"),
    ],
    "totalAssets": [("Assets", "value")],
    "dividendsPaid": [("PaymentsOfDividends", "value"), ("PaymentsOfDividendsCommonStock", "value")],
    "buybacks": [("PaymentsForRepurchaseOfCommonStock", "value")],
}
assert list(CONCEPTS) == [name for name in FIELDS if name in CONCEPTS]

FIELD_UNITS = {
    name: "USD/shares" if name in ("epsDiluted", "epsBasic") else "shares" if name == "dilutedShares" else "USD"
    for name in CONCEPTS
}
INSTANT_FIELDS = frozenset({"equity", "totalDebt", "cash", "totalAssets"})
# Duration (income / cash-flow statement) fields, per-share ones included
FLOW_FIELDS = frozenset(name for name in CONCEPTS if name not in INSTANT_FIELDS)
# Duration fields whose quarters may be obtained by subtracting year-to-date values
ADDITIVE_FIELDS = frozenset(name for name in FLOW_FIELDS if name not in PER_SHARE_FIELDS)
OUTFLOW_FIELDS = frozenset({"capex", "dividendsPaid", "buybacks"})
# Fields that are never negative: a negative DERIVED value (a subtraction) is not stored
NON_NEGATIVE_FIELDS = frozenset({"revenue", "costOfRevenue", "capex", "dividendsPaid", "buybacks", "totalAssets"})
# Fields whose concept choice checks that four quarters add up to the annual value (non-negative
# income-statement totals, where partial line items are tagged with the total's candidates)
CROSS_CHECK_FIELDS = frozenset({"revenue", "costOfRevenue"})
# Per-share fields whose Q4 may be annual − (Q1 + Q2 + Q3)
Q4_PER_SHARE_FIELDS = ("epsDiluted", "epsBasic")
EXISTENCE_FIELDS = ("revenue", "netIncome")
SHARES_CONCEPT = CONCEPTS["dilutedShares"][0][0]
# Candidates that are a total only for some filers: never chosen by the concept rules, never a
# fallback, never a period's existence evidence; only a primary the Yahoo arbiter chose (_arbitrate)
ARBITER_ONLY_CONCEPTS = frozenset({"OperatingLeaseLeaseIncome"})
_REVENUE_CONCEPTS = frozenset(concept for concept, _ in CONCEPTS["revenue"])
# Candidate kinds that make a field's value (the others are parts of the composites)
VALUE_KINDS = ("value", "continuing")
CONTINUING_CONCEPTS = frozenset(concept for name in Q4_PER_SHARE_FIELDS for concept, kind in CONCEPTS[name]
                                if kind == "continuing")
_EPS_CONCEPTS = tuple(dict.fromkeys(concept for name in Q4_PER_SHARE_FIELDS for concept, _ in CONCEPTS[name]))
# concept → the first field naming it (EarningsPerShareBasicAndDiluted counts as epsDiluted)
_FIELD_OF_CONCEPT = {concept: name for name, pairs in reversed(CONCEPTS.items()) for concept, _ in pairs}

_UNIT_OF_CONCEPT = {concept: FIELD_UNITS[name] for name, pairs in CONCEPTS.items() for concept, _ in pairs}
_INSTANT_CONCEPTS = frozenset(concept for name in INSTANT_FIELDS for concept, _ in CONCEPTS[name])
# Payments concepts: reported as positive outflows (a negative fact is the filer's sign flip, see _store)
_OUTFLOW_CONCEPTS = frozenset(concept for name in OUTFLOW_FIELDS for concept, _ in CONCEPTS[name])
_NON_NEGATIVE_CONCEPTS = frozenset(
    concept for name in NON_NEGATIVE_FIELDS & FLOW_FIELDS for concept, _ in CONCEPTS[name]
)
_OFFSETS = [0] + [sign * days for days in range(1, SAME_PERIOD_DAYS + 1) for sign in (-1, 1)]


@dataclass
class SecResult:
    """
    Periods extracted from one companyfacts payload (extract_periods).

    annual / quarterly: build_financials.period(end, values, "sec", extra) dicts, ascending; values
      only hold non-null numbers; quarterly extra is {"derived": 1 or None}.
    concepts: {field: sorted us-gaap concepts used by any period}.
    issues: SEC issue codes of the whole payload ("q4-derived", "debt-approx", "eps-inconsistent",
      "capex-sign", "foreign-filer": a filer that still files foreign-filer forms, see _Facts.foreign_now).
    skip: None, or why no document should be produced: "no-us-gaap" (no us-gaap facts at all),
      "foreign-filer" (IFRS facts or 20-F / 40-F / 6-K filings — not older than its latest 10-K / 10-Q
      fact, i.e. still a foreign filer — and < 2 annual 10-K periods),
      "non-usd" (money facts only in other currencies), "short-history" (< 2 annual 10-K periods),
      "revenue-scope-mismatch" (the revenue series misses the stored Yahoo revenue by a median > 50%).
    period_issues: {("annual" | "quarterly", end): set of issue codes of that period}.
    period_concepts: {("annual" | "quarterly", end): {field: [concepts used]}}.
    dropped_quarters: quarter ends (dates) left out as another reporting calendar's quarters
      (_drop_colliding_quarters); merge_into_document removes stored SEC quarters at those ends.
    arbiter: {"revenue": [concepts in order]} when the Yahoo arbiter (or a stored decision) chose the
      revenue concept order (_arbitrate), else {}; merge_into_document stores it as
      secConcepts["arbiter"].
    dropped: {field: count} of values left out as implausible (null, never replaced by a guess):
      "dilutedShares" / "epsDiluted" / "epsBasic" unit errors (_Facts._clean_per_share) and derived Q4
      EPS of the wrong sign, partial "totalDebt" balances (_suspect_debt), and "unitRefiled": filed
      versions passed over as another version's 10^(3k) scale error (_resolve_versions).
    """
    annual: list = field(default_factory=list)
    quarterly: list = field(default_factory=list)
    concepts: dict = field(default_factory=dict)
    issues: set = field(default_factory=set)
    skip: str | None = None
    period_issues: dict = field(default_factory=dict)
    period_concepts: dict = field(default_factory=dict)
    cik: str | None = None
    entity: str | None = None
    dropped_quarters: list = field(default_factory=list)
    arbiter: dict = field(default_factory=dict)
    dropped: dict = field(default_factory=dict)


class _Fact(NamedTuple):
    start: object      # datetime.date or None (instant)
    end: object        # datetime.date
    value: object      # int or float as filed
    filed: str
    accn: str
    form: str

    def rank(self):
        """Larger is preferred: the latest filing, then the accession number (then a total order, so that
        the choice never depends on the order of the facts: one filing may tag a period with two starts)."""
        return (self.filed, self.accn, self.form, float(self.value),
                self.start.isoformat() if self.start else "", self.end.isoformat())

    def days(self):
        return (self.end - self.start).days


def _within(days, bounds):
    return bounds[0] <= days <= bounds[1]


def _close(a, b, tolerance):
    """|a − b| ≤ tolerance × max(|a|, |b|) (two zeros are close)."""
    return abs(a - b) <= tolerance * max(abs(a), abs(b))


def _norm(concept, value):
    """Value of a fact for arithmetic: payments concepts as positive outflows, everything else as filed."""
    return abs(value) if concept in _OUTFLOW_CONCEPTS else value


def _narrow(fact, full):
    """`fact` is a narrow line item next to `full` (same period, same concept): ≥ 0 and < 15% of it."""
    return full.value > 0 and 0 <= fact.value < NARROW_REFILE_RATIO * full.value


def _best(table, key, fact, guard=False):
    """
    Keep the preferred fact per key: the latest filed (rank). guard (revenue concepts): a later filed
    value that is a narrow line item next to the earlier filed one (_narrow) is not adopted, whichever
    order the facts arrive in (an earlier full value replaces a narrow later one already stored).
    """
    known = table.get(key)
    if known is None:
        table[key] = fact
    elif fact.rank() > known.rank():
        if not (guard and _narrow(fact, known)):
            table[key] = fact
    elif guard and _narrow(known, fact):
        table[key] = fact


def _pick(items, guard=False):
    """The preferred fact of several versions of one period (_best, whatever order they come in)."""
    table = {}
    for fact in sorted(items, key=_Fact.rank):
        _best(table, None, fact, guard)
    return table[None]


def _unit_class(value, pivot):
    """k ≠ 0 when value ≈ 10^(3k) × pivot (same sign, within UNIT_SHIFT_TOLERANCE of log10), else 0."""
    if not value or not pivot or (value > 0) != (pivot > 0):
        return 0
    exponent = math.log10(abs(value) / abs(pivot))
    k = round(exponent / 3)
    return k if k and abs(exponent - 3 * k) <= UNIT_SHIFT_TOLERANCE else 0


def _local_median(points, day, minimum=2):
    """Median of the values of the NEIGHBOURS points [(day, value)] nearest to `day`, or None (< minimum)."""
    near = sorted(points, key=lambda point: (abs((point[0] - day).days), point[0], point[1]))[:NEIGHBOURS]
    return _median([value for _, value in near]) if len(near) >= minimum else None


def _resolve_versions(groups, guard=False):
    """
    The magnitude refile guard (every concept): groups {table: {key: [every filed version]}} (key: an end
    date, or a (start, end) pair) → ({table: {key: [the versions to choose from]}}, {facts passed over}).

    The versions of one period are split into scale classes relative to the earliest filed one: a value
    10^(3k) times another (_unit_class, k ≠ 0) is the same number filed in thousands / millions as units
    (WAT 2025 Q1 shares 59,711,000 in the 10-Q, 59,711,000,000 in the next year's comparative column; NTRS
    2008 224,053,430 and 224,053,430,000,000). Where a period has several classes, the class whose value
    lies nearest (log scale) to the median of the NEIGHBOURS nearest unambiguous periods of the same concept
    (all tables: 3-month, YTD and annual values differ by far less than 10^3) is kept, without neighbours the
    class of the earliest filed version. The latest filed version of the kept class then wins as usual.
    """
    def day_of(key):
        return key[1] if isinstance(key, tuple) else key

    points, ambiguous = [], []
    chosen = {table: dict(entries) for table, entries in groups.items()}
    for table, entries in groups.items():
        for key, items in entries.items():
            pivot = min(items, key=_Fact.rank)
            classes = {}
            for fact in items:
                classes.setdefault(_unit_class(fact.value, pivot.value), []).append(fact)
            if len(classes) == 1:
                value = abs(_pick(items, guard).value)
                if value:
                    points.append((day_of(key), value))
            else:
                ambiguous.append((table, key, classes))
    passed = set()
    for table, key, classes in ambiguous:
        reference = _local_median(points, day_of(key), minimum=1)

        def distance(k, classes=classes, reference=reference):
            value = abs(_pick(classes[k], guard).value)
            far = abs(math.log10(value / reference)) if reference and value else math.inf
            return far, k != 0, k
        best = min(classes, key=distance) if reference else 0
        chosen[table][key] = classes[best]
        passed.update(fact for k, items in classes.items() if k != best for fact in items)
    return chosen, passed


def _parse_fact(raw):
    if not isinstance(raw, dict):
        return None
    end = parse_day(raw.get("end"))
    value = raw.get("val")
    if end is None or finite_number(value) is None:
        return None
    start = None
    if raw.get("start") is not None:
        start = parse_day(raw.get("start"))
        if start is None or start >= end:
            return None
    text = [raw.get(key) if isinstance(raw.get(key), str) else "" for key in ("filed", "accn", "form")]
    return _Fact(start, end, value, text[0], text[1], text[2].strip().upper())


def _confirmed_quarter(fact, annual_facts, concept, filing_quarters=(), ytds=None):
    """
    A 3-month fact of a 10-K-type filing is used only when the same filing (annual_facts: its
    annual-length facts of the same concept) reports the fiscal year containing the quarter, and the
    quarter is not that year's own value with a 3-month context (a mis-tagged annual figure: the same
    non-zero value, whatever quarter of the year the context names, HSIC / AVAV; for share counts only
    a quarter ending with the year). A lone 3-month value in a 10-K is such a mis-tag far more often
    than a real quarter.
    ytds (money concepts; None skips the check): the concept's 10-Q year-to-date facts. A Q4 value
    (ending with the year) must also be in the year's scope (_q4_in_scope).
    """
    for year in annual_facts:
        if year.start - timedelta(days=SAME_PERIOD_DAYS) <= fact.start and fact.end <= year.end + timedelta(
                days=SAME_PERIOD_DAYS):
            # (a share count may equal the year's average for real: only the year's own end counts there)
            if fact.value != 0 and fact.value == year.value and (
                    _UNIT_OF_CONCEPT[concept] != "shares" or abs((year.end - fact.end).days) <= SAME_PERIOD_DAYS):
                return False
            if ytds is not None and abs((year.end - fact.end).days) <= SAME_PERIOD_DAYS:
                return _q4_in_scope(fact, year, concept, filing_quarters, ytds)
            return True
    return False


def _q4_in_scope(fact, year, concept, filing_quarters, ytds):
    """
    A 10-K's 3-month Q4 value lies within Q4_SCOPE_TOLERANCE of what that 10-K's annual value leaves
    after the first nine months: annual − (the same filing's three other 3-month values of the year),
    or annual − any filed 9-month YTD value of the year (10-Q; any version, so a recast in between does
    not reject a real Q4). No such evidence → accepted. GE 2012: the 10-K tagged a segment's quarters
    (11.8B) next to the consolidated year (147B, 9-month YTD 108B) → rejected.
    """
    slack = timedelta(days=SAME_PERIOD_DAYS)
    nine_months_end = fact.start - timedelta(days=1)
    expected = []
    earlier = {
        q.end: q for q in sorted(filing_quarters, key=_Fact.rank)
        if q.start >= year.start - slack and q.end <= fact.start + slack and q.end < fact.end - slack
    }
    if len(earlier) == 3:
        expected.append(_norm(concept, year.value) - sum(_norm(concept, q.value) for q in earlier.values()))
    for ytd in ytds:
        if abs((ytd.start - year.start).days) <= SAME_PERIOD_DAYS and abs(
                (ytd.end - nine_months_end).days) <= SAME_PERIOD_DAYS:
            expected.append(_norm(concept, year.value) - _norm(concept, ytd.value))
    # A Q4 near zero (tax, net income) may miss by more than 25% of itself and still be immaterial:
    # a gap within Q4_TOLERANCE of the year is accepted too
    value, slack_value = _norm(concept, fact.value), Q4_TOLERANCE * abs(_norm(concept, year.value))
    return not expected or any(_close(value, total, Q4_SCOPE_TOLERANCE) or abs(value - total) <= slack_value
                               for total in expected)


def _material(eps, profit, count, scale):
    """
    An EPS off net income / `count` shares by more than EPS_OUTLIER_RATIO in a way that matters: too large
    against both the period's and the series' typical (`scale`, median |net income|) net income — a tiny
    net income with a normal EPS (preferred dividends) is not a unit error — or too small for a net income
    of at least EPS_MATERIAL_SHARE of the typical one.
    """
    money = abs(eps) * count
    if money > abs(profit):
        return money > EPS_OUTLIER_RATIO * max(abs(profit), scale)
    return abs(profit) > EPS_OUTLIER_RATIO * money and abs(profit) >= EPS_MATERIAL_SHARE * scale


def _cluster(counts):
    """{day: count} → {day: canonical day}; days within SAME_PERIOD_DAYS of a group's first day are one."""
    mapping, group = {}, []

    def close():
        if group:
            canonical = max(group, key=lambda day: (counts[day], day))
            for day in group:
                mapping[day] = canonical

    for day in sorted(counts):
        if group and (day - group[0]).days > SAME_PERIOD_DAYS:
            close()
            group = []
        group.append(day)
    close()
    return mapping


class _Facts:
    """Indexed facts of the CONCEPTS of one companyfacts payload."""

    def __init__(self, companyfacts):
        facts = companyfacts.get("facts") if isinstance(companyfacts, dict) else None
        facts = facts if isinstance(facts, dict) else {}
        gaap = facts.get("us-gaap") if isinstance(facts.get("us-gaap"), dict) else {}
        self.has_gaap = bool(gaap)
        self.has_ifrs = bool(facts.get("ifrs-full"))
        self.foreign_forms = False
        # Latest `filed` of a foreign-filer fact (20-F / 40-F / 6-K form, or any IFRS fact) and of a 10-K /
        # 10-Q fact: a filer that moved to 10-K reporting (CRH since 2024) is no foreign filer any more
        self.foreign_filed, self.domestic_filed = "", ""
        self.other_currency = False     # money facts of our concepts exist in another unit
        self.usd = False                # … and in USD
        raw = {}                        # concept → [_Fact] in the concept's unit
        for concept in sorted(_UNIT_OF_CONCEPT):
            entry = gaap.get(concept)
            units = entry.get("units") if isinstance(entry, dict) else None
            if not isinstance(units, dict):
                continue
            unit = _UNIT_OF_CONCEPT[concept]
            for name, points in units.items():
                if not isinstance(points, list):
                    continue
                parsed = [fact for fact in map(_parse_fact, points) if fact is not None]
                self._note_forms((fact.form, fact.filed) for fact in parsed)
                if name == unit:
                    raw[concept] = parsed
                    if unit == "USD" and parsed:
                        self.usd = True
                elif unit == "USD" and parsed and "/" not in name and name != "shares":
                    self.other_currency = True
        for namespace in ("dei", "ifrs-full"):
            entries = facts.get(namespace) if isinstance(facts.get(namespace), dict) else {}
            for entry in entries.values():
                units = entry.get("units") if isinstance(entry, dict) else None
                for points in (units or {}).values() if isinstance(units, dict) else ():
                    for point in points if isinstance(points, list) else ():
                        if not isinstance(point, dict):
                            continue
                        form, filed = point.get("form"), point.get("filed")
                        form = form.strip().upper() if isinstance(form, str) else ""
                        filed = filed if isinstance(filed, str) else ""
                        if namespace == "ifrs-full":
                            self.foreign_filed = max(self.foreign_filed, filed)
                        else:
                            self._note_forms([(form, filed)])
        # Foreign filer now: its latest foreign-filer fact is not older than its latest 10-K / 10-Q fact
        self.foreign_now = (self.foreign_forms or self.has_ifrs) and self.foreign_filed >= self.domestic_filed

        # Canonical period ends / starts: dates of usable duration facts within ±5 days are one
        end_counts, start_counts = Counter(), Counter()
        usable = {}
        for concept, items in raw.items():
            if concept in _INSTANT_CONCEPTS:
                continue
            kept = []
            spans = {}              # accession → annual-length 10-K facts of this concept
            filing_quarters = {}    # accession → 3-month 10-K facts of this concept
            ytds = [] if _UNIT_OF_CONCEPT[concept] == "USD" else None     # 10-Q YTD facts (Q4 scope check)
            for fact in items:
                if fact.start is None:
                    continue
                if fact.form in ANNUAL_FORMS and _within(fact.days(), ANNUAL_DAYS):
                    spans.setdefault(fact.accn, []).append(fact)
                elif fact.form in ANNUAL_FORMS and _within(fact.days(), QUARTER_DAYS):
                    filing_quarters.setdefault(fact.accn, []).append(fact)
                elif ytds is not None and fact.form in QUARTERLY_FORMS and _within(fact.days(), YTD_DAYS):
                    ytds.append(fact)
            for fact in items:
                if fact.start is None:
                    continue
                days = fact.days()
                # 10-K-type forms: annual values, and 3-month values (restated quarters, Q4) for the
                # quarterly pool when the same filing confirms them (_confirmed_quarter); their
                # 6/9-month values are not used
                annual = fact.form in ANNUAL_FORMS and (
                    _within(days, ANNUAL_DAYS)
                    or (_within(days, QUARTER_DAYS) and _confirmed_quarter(
                        fact, spans.get(fact.accn, ()), concept, filing_quarters.get(fact.accn, ()), ytds))
                )
                quarterly = fact.form in QUARTERLY_FORMS and (
                    _within(days, QUARTER_DAYS) or _within(days, YTD_DAYS)
                )
                if annual or quarterly:
                    kept.append(fact)
                    end_counts[fact.end] += 1
                    start_counts[fact.start] += 1
            usable[concept] = kept
        self.end_of = _cluster(end_counts)
        self.start_of = _cluster(start_counts)

        # Duration facts: annual[c][end], quarter[c][end] (3 months, 10-Q and 10-K-type forms),
        # cumulative[c][(start, end)] (10-Q only), annual_versions[c][end] (every annual fact)
        self.annual, self.quarter, self.cumulative, self.annual_versions = {}, {}, {}, {}
        self.restated_ytd = set()       # (concept, (start, end)) of YTD values filed with different values
        self.dropped = Counter()        # field (or "unitRefiled") → values left out as implausible
        interim_ends = set()
        for concept in sorted(usable):
            groups = {"annual": {}, "quarter": {}, "cumulative": {}}
            guard = concept in _REVENUE_CONCEPTS
            for fact in usable[concept]:
                end = self.end_of[fact.end]
                if fact.form in ANNUAL_FORMS:
                    if _within(fact.days(), ANNUAL_DAYS):
                        groups["annual"].setdefault(end, []).append(fact)
                    else:
                        interim_ends.add(end)
                        groups["quarter"].setdefault(end, []).append(fact)
                    continue
                interim_ends.add(end)
                if _within(fact.days(), QUARTER_DAYS):
                    groups["quarter"].setdefault(end, []).append(fact)
                groups["cumulative"].setdefault((self.start_of[fact.start], end), []).append(fact)
            groups, passed = _resolve_versions(groups, guard)
            self.dropped["unitRefiled"] += len(passed)
            tables = {name: {key: _pick(items, guard) for key, items in entries.items()}
                      for name, entries in groups.items()}
            self.restated_ytd |= {
                (concept, key) for key, items in groups["cumulative"].items()
                if len({fact.value for fact in items if _within(fact.days(), YTD_DAYS)}) > 1
            }
            self.annual[concept], self.quarter[concept] = tables["annual"], tables["quarter"]
            self.cumulative[concept] = tables["cumulative"]
            self.annual_versions[concept] = groups["annual"]

        # Instant facts per filing kind: instants[c]["annual" | "quarterly"][end as filed]
        self.instants = {}
        for concept in sorted(_INSTANT_CONCEPTS & set(raw)):
            groups = {"annual": {}, "quarterly": {}}
            for fact in raw[concept]:
                if fact.start is not None:
                    continue
                if fact.form in ANNUAL_FORMS:
                    groups["annual"].setdefault(fact.end, []).append(fact)
                elif fact.form in QUARTERLY_FORMS:
                    groups["quarterly"].setdefault(fact.end, []).append(fact)
            groups, passed = _resolve_versions(groups)
            self.dropped["unitRefiled"] += len(passed)
            self.instants[concept] = {pool: {end: _pick(items) for end, items in entries.items()}
                                      for pool, entries in groups.items()}

        existence = [c for name in EXISTENCE_FIELDS for c, _ in CONCEPTS[name] if c not in ARBITER_ONLY_CONCEPTS]
        self.annual_ends = sorted({end for c in existence for end in self.annual.get(c, {})})
        self.annual_set = set(self.annual_ends)
        self.quarter_ends = sorted(interim_ends | self.annual_set)
        self._previous, self._next = {}, {}
        for index, end in enumerate(self.quarter_ends):
            earlier = [p for p in self.quarter_ends[:index] if _within((end - p).days, QUARTER_DAYS)]
            self._previous[end] = earlier[-1] if earlier else None
            if earlier:
                self._next.setdefault(earlier[-1], end)
        self._memo = {}
        self._clean_per_share()

    def _note_forms(self, pairs):
        for form, filed in pairs:
            if form in FOREIGN_FORMS:
                self.foreign_forms = True
                self.foreign_filed = max(self.foreign_filed, filed)
            elif form in ANNUAL_FORMS or form in QUARTERLY_FORMS:
                self.domestic_filed = max(self.domestic_filed, filed)

    # ------------------------------------------------------------------------------- per share
    def net_income(self, table, end):
        """Net income of the annual ("annual") or 3-month ("quarter") period ending on `end`, or None."""
        for concept, _ in CONCEPTS["netIncome"]:
            if table == "annual":
                fact = self.annual.get(concept, {}).get(end)
                if fact is not None:
                    return fact.value
            else:
                found = self.quarter_value(concept, end)
                if found is not None:
                    return found[0]
        return None

    def _clean_per_share(self):
        """
        Per-series plausibility of the annual and 3-month share counts and EPS (single filed versions in
        another scale, which _resolve_versions cannot see). Dropped values become null (never rescaled),
        counted in self.dropped:
        1. diluted share counts below MIN_SHARE_COUNT (HST 2017-2020: 739.1, i.e. millions), or
           10^SHARE_OUTLIER_LOG away from the local share level: the median of the counts implied by the
           NEIGHBOURS nearest periods' net income / EPS (|EPS| ≥ IMPLIED_MIN_EPS; independent of how the
           counts are tagged: ECHO tags two years of 10-Q counts in thousands next to the 10-K's units),
           else of the NEIGHBOURS nearest other counts (BWXT 2011 Q3 118.6B among 118M; WAT 2026 82.1B
           after 59.7M). Real splits move the level by ≤ 50x and only shift a median gradually;
        2. EPS against net income / diluted shares of its own period: beyond EPS_OUTLIER_RATIO one of them
           is wrong. When the EPS agrees with the level its neighbours imply (median net income / EPS of
           the NEIGHBOURS nearest periods), the share count is the outlier (dropped if 10^SHARE_OUTLIER_LOG
           off that level, else both are kept: a constant ratio, e.g. EPS per ADS); otherwise the EPS is
           dropped (HAL 2021 Q3 260000 for 0.26), if material (_material). Without a share count of its own
           the EPS is compared with the nearest share counts (else the neighbours' implied level), at
           10^SHARE_OUTLIER_LOG (ASTS 2019-2020: 17,600,000 USD/share).
        """
        shares = {table: getattr(self, table).setdefault(SHARES_CONCEPT, {}) for table in ("annual", "quarter")}
        income = {table: {} for table in shares}
        for table in shares:
            for concept in _EPS_CONCEPTS:
                for end in getattr(self, table).get(concept, {}):
                    if end not in income[table]:
                        income[table][end] = self.net_income(table, end)
        implied_level = []
        for table in shares:
            seen = set()
            for concept in _EPS_CONCEPTS:
                for end, fact in getattr(self, table).get(concept, {}).items():
                    profit = income[table].get(end)
                    if end not in seen and profit and abs(fact.value) >= IMPLIED_MIN_EPS:
                        seen.add(end)
                        implied_level.append((end, abs(profit / fact.value)))
        entries = [(end, table, fact.value) for table, items in shares.items() for end, fact in items.items()]
        drop = {(table, end) for end, table, value in entries if value < MIN_SHARE_COUNT}
        points = [(end, table, value) for end, table, value in entries if (table, end) not in drop]
        for end, table, value in points:
            level = _local_median(implied_level, end) or _local_median(
                [(day, v) for day, kind, v in points if (day, kind) != (end, table)], end)
            if level and abs(math.log10(value / level)) >= SHARE_OUTLIER_LOG:
                drop.add((table, end))
        for table, end in drop:
            del shares[table][end]
        self.dropped["dilutedShares"] += len(drop)

        counts = [(end, fact.value) for items in shares.values() for end, fact in items.items()]
        limit = math.log10(EPS_OUTLIER_RATIO)
        drop_shares, drop_eps = set(), set()
        for table in ("annual", "quarter"):
            tables = getattr(self, table)
            sizes = [abs(value) for value in income[table].values() if value]
            scale = _median(sizes) if sizes else 0
            for concept in _EPS_CONCEPTS:
                items = tables.get(concept, {})
                implied = {end: abs(income[table][end] / fact.value) for end, fact in items.items()
                           if fact.value and income[table].get(end)}
                for end, own in implied.items():
                    eps, profit = items[end].value, income[table][end]
                    level = _local_median([(day, v) for day, v in implied.items() if day != end], end)
                    count = shares[table].get(end)
                    if count is not None:
                        if abs(math.log10(own / count.value)) < limit:
                            continue
                        if level is not None and abs(math.log10(own / level)) < limit:
                            if abs(math.log10(count.value / level)) >= SHARE_OUTLIER_LOG:
                                drop_shares.add((table, end))
                            continue
                        if _material(eps, profit, count.value, scale):
                            drop_eps.add((table, concept, end))
                        continue
                    reference = _local_median(counts, end, minimum=1) or level
                    if reference and abs(math.log10(own / reference)) >= SHARE_OUTLIER_LOG and _material(
                            eps, profit, reference, scale):
                        drop_eps.add((table, concept, end))
        for table, end in drop_shares:
            del shares[table][end]
        self.dropped["dilutedShares"] += len(drop_shares)
        for table, concept, end in drop_eps:
            del getattr(self, table)[concept][end]
            self.dropped[_FIELD_OF_CONCEPT[concept]] += 1

    def basis(self, table, end, eps):
        """Share basis of one EPS piece: its diluted share count, else net income / EPS, else None."""
        count = getattr(self, table).get(SHARES_CONCEPT, {}).get(end)
        return share_basis(count.value if count is not None else None, self.net_income(table, end), eps)

    # ------------------------------------------------------------------------------- durations
    def previous_quarter(self, end):
        return self._previous.get(end)

    def chain(self, last, start):
        """Quarter ends from Q1 to `last` of the fiscal year starting on `start` (as filed), or None."""
        ends, current = [], last
        while current is not None and len(ends) < 3:
            ends.append(current)
            if _within((current - start).days, QUARTER_DAYS):
                return ends[::-1]
            current = self.previous_quarter(current)
        return None

    def quarter_value(self, concept, end):
        """(value, derived) of one additive concept for the quarter ending on `end`, or None."""
        found = self._quarter(concept, end)
        return None if found is None else found[:2]

    def _quarter(self, concept, end):
        """
        (value, derived, filed) or None (memoized). A reported 3-month value is returned as filed;
        a derived value is computed on _norm values (payments as positive outflows) and carries the
        `filed` of its oldest piece.
        """
        key = (concept, end)
        if key not in self._memo:
            self._memo[key] = None          # guards against cycles
            self._memo[key] = self._quarter_value(concept, end)
        return self._memo[key]

    def _quarter_value(self, concept, end):
        direct = self.quarter.get(concept, {}).get(end)
        options = [self._derived_quarter(concept, end)]
        if direct is not None:
            options.append(self._first_quarter_from_ytd(concept, end, direct))
        options = [option for option in options if option is not None]
        if direct is None:
            return (options[0][0], True, options[0][1]) if options else None
        # A difference filed after the reported 3-month value (a restated version) replaces it, unless
        # it is the same number
        best = (direct.value, False, direct.filed)
        for value, filed in options:
            if filed > best[2] and not _close(value, _norm(concept, direct.value), RESTATED_MIN_CHANGE):
                best = (value, True, filed)
        return best

    def _checked(self, concept, value, filed):
        """A derived (value, filed), or None when a never-negative concept came out negative."""
        if concept in _NON_NEGATIVE_CONCEPTS and value < 0:
            return None
        return value, filed

    def _derived_quarter(self, concept, end):
        """(value, filed) of the quarter as a difference of year-to-date / annual values, or None."""
        is_q4 = end in self.annual_set
        totals = []
        if is_q4:
            fact = self.annual.get(concept, {}).get(end)
            if fact is not None:
                totals.append(fact)
        else:
            cumulative = self.cumulative.get(concept, {})
            totals = [
                cumulative[key] for key in sorted(cumulative)
                if key[1] == end and _within(cumulative[key].days(), YTD_DAYS)
            ]
        previous = self.previous_quarter(end)
        if previous is None:
            return None
        for total in totals:
            start = self.start_of[total.start]
            # Year-to-date difference: both values must start on the same day
            prior = self.cumulative.get(concept, {}).get((start, previous))
            if prior is not None and prior.start == total.start:
                if is_q4 and not self._q4_consistent(concept, total, prior, previous):
                    continue            # mixed versions: no Q4 rather than a fabricated one
                value = _norm(concept, total.value) - _norm(concept, prior.value)
                return self._checked(concept, value, min(total.filed, prior.filed))
            # Year-to-date minus the earlier quarters of the same fiscal year (Q1 as reported, starting
            # on the same day as the year-to-date value)
            ends = self.chain(previous, total.start)
            if ends is None or not self._starts_with(concept, ends[0], total.start):
                continue
            parts = [self._quarter(concept, day) for day in ends]
            if all(part is not None for part in parts):
                filed = min([total.filed] + [part[2] for part in parts])
                if is_q4 and not self._annual_consistent(concept, total, filed):
                    continue
                value = _norm(concept, total.value) - sum(_norm(concept, part[0]) for part in parts)
                return self._checked(concept, value, filed)
        return None

    def _first_quarter_from_ytd(self, concept, end, direct):
        """
        (value, filed) of a first quarter as 6-month YTD − Q2 (both reported, the YTD starting on the
        reported quarter's start day and filed with more than one value, i.e. restated), or None.
        Lets a restated YTD / Q2 pair (e.g. recast for a discontinued operation in a later 10-Q)
        replace an older reported Q1; an unrestated YTD never replaces it (rounding noise).
        """
        following = self._next.get(end)
        if following is None:
            return None
        key = (self.start_of[direct.start], following)
        ytd = self.cumulative.get(concept, {}).get(key)
        second = self.quarter.get(concept, {}).get(following)
        if ytd is None or second is None or ytd.start != direct.start or not _within(ytd.days(), YTD_DAYS):
            return None
        if (concept, key) not in self.restated_ytd:
            return None             # the 6-month value was never restated: the reported Q1 stands
        if second.start <= direct.end or (second.start - direct.end).days > SAME_PERIOD_DAYS:
            return None
        value = _norm(concept, ytd.value) - _norm(concept, second.value)
        return self._checked(concept, value, min(ytd.filed, second.filed))

    def _q4_consistent(self, concept, total, ytd, previous):
        """
        Annual − 9-month YTD is one version: the stored Q1..Q3 of the concept (where all three exist)
        add up to the YTD within Q4_TOLERANCE, and the annual value is the version the YTD saw.
        """
        ends = self.chain(previous, total.start)
        if ends is not None and len(ends) == 3:
            parts = [self._quarter(concept, day) for day in ends]
            if all(part is not None for part in parts):
                values = [_norm(concept, part[0]) for part in parts]
                scale = max(abs(_norm(concept, ytd.value)), sum(abs(value) for value in values))
                if abs(sum(values) - _norm(concept, ytd.value)) > Q4_TOLERANCE * scale:
                    return False
        return self._annual_consistent(concept, total, ytd.filed)

    def _annual_consistent(self, concept, total, filed):
        """
        The annual value `total` (latest filed) equals, within Q4_TOLERANCE, what the first 10-K filed
        on or after `filed` reported for that year: a 9-month YTD (or Q1..Q3) filed before a
        restatement / recast of the annual value is another version and must not be subtracted.
        """
        start = self.start_of[total.start]
        versions = [
            fact for fact in self.annual_versions.get(concept, {}).get(self.end_of[total.end], [])
            if self.start_of[fact.start] == start and fact.filed >= filed
        ]
        if not versions:
            return True
        first = min(fact.filed for fact in versions)
        return any(_close(_norm(concept, fact.value), _norm(concept, total.value), Q4_TOLERANCE)
                   for fact in versions if fact.filed == first)

    def q4_per_share(self, concept, end):
        """
        Annual EPS − (Q1 + Q2 + Q3 EPS) when all four are reported on one share basis, else None.

        Each piece is the latest filed value of its period, so a split between the filings mixes bases
        (e.g. annual EPS restated after a split by a later 10-K, Q1..Q3 EPS still pre-split; GOOGL 2020:
        annual 2.93 after the 20:1 split, Q1-Q3 26.29 / 1.36 / ...): every piece needs a share basis
        (_Facts.basis: its diluted share count, else the implied net income / EPS) and all four must lie
        within SPLIT_MATCH of each other, otherwise no Q4 per-share value is derived.
        """
        total = self.annual.get(concept, {}).get(end)
        previous = self.previous_quarter(end)
        if total is None or previous is None:
            return None
        ends = self.chain(previous, total.start)
        if ends is None or len(ends) != 3:
            return None
        parts = [self.quarter.get(concept, {}).get(day) for day in ends]
        if any(part is None for part in parts) or parts[0].start != total.start:
            return None
        bases = [self.basis("annual", end, total.value)] + [
            self.basis("quarter", day, part.value) for day, part in zip(ends, parts)
        ]
        return q4_eps(total.value, [part.value for part in parts], bases)

    def _starts_with(self, concept, end, start):
        """The reported 3-month value of the quarter ending on `end` starts on `start` (as filed)."""
        fact = self.quarter.get(concept, {}).get(end)
        return fact is not None and fact.start == start

    # ------------------------------------------------------------------------------- instants
    def instant(self, concept, end, pools):
        """Best fact of an instant concept within ±5 days of `end` (pool order first, then distance)."""
        tables = self.instants.get(concept)
        if not tables:
            return None
        for pool in pools:
            table = tables[pool]
            for offset in _OFFSETS:
                fact = table.get(end + timedelta(days=offset))
                if fact is not None:
                    return fact
        return None

    def instant_at(self, concept, day, pools):
        """Fact of an instant concept dated exactly `day`."""
        tables = self.instants.get(concept)
        for pool in pools if tables else ():
            fact = tables[pool].get(day)
            if fact is not None:
                return fact
        return None


# ---------------------------------------------------------------------------------------------
# Composite balance-sheet fields
# ---------------------------------------------------------------------------------------------
def _total_debt(facts, end, pools):
    """
    → (value, concepts, approximate) or None. Judgement call (us-gaap: LongTermDebt is the total
    INCLUDING current maturities = LongTermDebtCurrent + LongTermDebtNoncurrent; DebtCurrent =
    current maturities + short-term borrowings), so no part is ever counted twice:
    1. LongTermDebtNoncurrent + current part, where the current part is DebtCurrent, else
       LongTermDebtCurrent + ShortTermBorrowings (whichever exist; disjoint by definition), all at
       the identical date. No current part at all → noncurrent alone, "debt-approx".
    2. Else LongTermDebt (+ ShortTermBorrowings at the identical date). DebtCurrent is NOT added (it
       would count current maturities twice). Always "debt-approx": some filers tag only the
       noncurrent balance-sheet line as LongTermDebt, so its scope cannot be verified.
    3. Else LongTermDebtAndCapitalLeaseObligations (the noncurrent line including finance leases) +
       the current part as in 1 (LongTermDebtAndCapitalLeaseObligationsCurrent standing in for
       LongTermDebtCurrent); "debt-approx" without a current part.
    4. Else None: a current part alone (DebtCurrent / LongTermDebtCurrent / ShortTermBorrowings) is not
       the total debt (ACN 10-Q: 113M of current debt next to 8.4B in all, ABBV: 0), so the field stays
       null (a later Yahoo run may fill it). Annual, Q4 and other quarters follow the same rules; a
       quarter that is still a partial balance next to its neighbours is nulled by _suspect_debt.
    """
    def current_part(day, maturities=("LongTermDebtCurrent",)):
        fact = facts.instant_at("DebtCurrent", day, pools)
        if fact is not None:
            return [("DebtCurrent", fact)]
        found = next(((concept, fact) for concept in maturities
                      for fact in [facts.instant_at(concept, day, pools)] if fact is not None), None)
        short = facts.instant_at("ShortTermBorrowings", day, pools)
        return [part for part in (found, ("ShortTermBorrowings", short) if short is not None else None) if part]

    base = facts.instant("LongTermDebtNoncurrent", end, pools)
    if base is not None:
        parts = [("LongTermDebtNoncurrent", base)] + current_part(base.end)
        approximate = len(parts) == 1
    elif facts.instant("LongTermDebt", end, pools) is not None:
        base = facts.instant("LongTermDebt", end, pools)
        parts = [("LongTermDebt", base)]
        short = facts.instant_at("ShortTermBorrowings", base.end, pools)
        if short is not None:
            parts.append(("ShortTermBorrowings", short))
        approximate = True
    elif facts.instant("LongTermDebtAndCapitalLeaseObligations", end, pools) is not None:
        base = facts.instant("LongTermDebtAndCapitalLeaseObligations", end, pools)
        parts = [("LongTermDebtAndCapitalLeaseObligations", base)] + current_part(
            base.end, ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"))
        approximate = len(parts) == 1
    else:
        return None
    return sum(fact.value for _, fact in parts), [concept for concept, _ in parts], approximate


def _suspect_debt(series):
    """
    [(end, totalDebt value, concepts)] of one section, ascending → ends whose value is a partial balance:
    below DEBT_DROP_RATIO of every neighbouring value (previous and next period with a value, whichever
    exist) AND built from other concepts than each of them (APD / RTX: a 10-Q tagging only one line of
    the debt next to the 10-K's full balance). A fall with the same composition is a real repayment.
    """
    suspect = []
    for index, (end, value, concepts) in enumerate(series):
        neighbours = [series[i] for i in (index - 1, index + 1) if 0 <= i < len(series)]
        if neighbours and all(value < DEBT_DROP_RATIO * other and concepts != used
                              for _, other, used in neighbours):
            suspect.append(end)
    return suspect


def _cash(facts, end, pools):
    """
    → (value, concepts) or None. Same definition as Yahoo's CashCashEquivalentsAndShortTermInvestments:
    CashAndCashEquivalentsAtCarryingValue (fallback CashCashEquivalentsRestrictedCashAnd…) plus ONE
    short-term investments concept dated identically (ShortTermInvestments, else
    MarketableSecuritiesCurrent; never both, they usually describe the same line).
    """
    for concept in ("CashAndCashEquivalentsAtCarryingValue",
                    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"):
        base = facts.instant(concept, end, pools)
        if base is None:
            continue
        for extra in ("ShortTermInvestments", "MarketableSecuritiesCurrent"):
            fact = facts.instant_at(extra, base.end, pools)
            if fact is not None:
                return base.value + fact.value, [concept, extra]
        return base.value, [concept]
    return None


def _instant_fields(facts, end, pools, values, used, issues, orders):
    for name in ("equity", "totalAssets"):
        for concept in orders[name]:
            fact = facts.instant(concept, end, pools)
            if fact is not None:
                values[name] = clean_value(fact.value)
                used[name] = [concept]
                break
    debt = _total_debt(facts, end, pools)
    if debt is not None:
        values["totalDebt"], used["totalDebt"] = clean_value(debt[0]), debt[1]
        if debt[2]:
            issues.add("debt-approx")
    cash = _cash(facts, end, pools)
    if cash is not None:
        values["cash"], used["cash"] = clean_value(cash[0]), cash[1]


def _store(name, value, values, issues, derived=False):
    """
    Store one field value. A directly reported payments fact is stored as a positive outflow (abs:
    the XBRL payments tags are positive, a negative one is the filer's sign flip, flagged capex-sign
    for capex). A derived value (a subtraction, already on positive-outflow values) is stored as it
    is, except that a negative one of a NON_NEGATIVE_FIELDS field is not stored at all.
    """
    if derived:
        if name in NON_NEGATIVE_FIELDS and value < 0:
            return
    elif name in OUTFLOW_FIELDS:
        if name == "capex" and value < 0:
            issues.add("capex-sign")
        value = abs(value)
    value = clean_value(value)
    if value is not None:
        values[name] = value


# ---------------------------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------------------------
def _annual_flow(facts, concept, end):
    """The annual fact of one flow concept, or None."""
    return facts.annual.get(concept, {}).get(end)


def _quarter_flow(facts, name, concept, end, is_q4):
    """(value, derived) of one flow concept for the quarter ending on `end`, or None."""
    if name in ADDITIVE_FIELDS:
        return facts.quarter_value(concept, end)
    if name in PER_SHARE_FIELDS:
        fact = facts.quarter.get(concept, {}).get(end)
        if fact is not None:
            return fact.value, False
        if is_q4 and name in Q4_PER_SHARE_FIELDS and concept not in CONTINUING_CONCEPTS:
            value = facts.q4_per_share(concept, end)
            return (value, True) if value is not None else None
    return None


def _continuing_ok(value, values):
    """A continuing-operations EPS is the period's EPS: within CONTINUING_TOLERANCE of net income / diluted
    shares (no material discontinued operation; AIG 2007: 43.17 next to 47.7 is not)."""
    income, shares = values.get("netIncome"), values.get("dilutedShares")
    return bool(value and income is not None and shares) and abs(
        value - income / shares) <= CONTINUING_TOLERANCE * abs(value)


# Flow fields in extraction order: net income and the share count before EPS (the continuing-operations
# fallback of EPS is checked against them)
_FLOW_ORDER = sorted(FLOW_FIELDS, key=lambda name: (name in Q4_PER_SHARE_FIELDS, list(CONCEPTS).index(name)))


def _first(items):
    return next((item for item in items if item is not None), None)


def _concept_orders(facts, reference=None):
    """
    → ({field: [concepts]}, decision). orders: the order in which a field's "value" candidates are
    tried for every period: the series-level primary first, the other candidates behind it in CONCEPTS
    order (ARBITER_ONLY_CONCEPTS only as a primary). The primary is, among the candidates having any
    value:
    0. (revenue only) the concept the Yahoo arbiter picks, or the stored decision (_arbitrate);
    1. (CROSS_CHECK_FIELDS only) one whose ordering has the fewest cross-check mismatches: fiscal
       years whose annual value and four quarters all exist and the quarters add up to no filed
       version of the annual value within CROSS_CHECK_TOLERANCE (a partial line item next to the
       total, or another scope);
    2. then the one covering the most periods itself (annual + quarterly ends), a later candidate
       winning over an earlier one only by a clear margin (COVERAGE_GAIN_PERIODS / _RATIO);
    3. then CONCEPTS order.
    decision: {"revenue": order} when step 0 decided (to be persisted, see merge_into_document),
    else {}. Deterministic; the cost is a few passes over the periods per multi-candidate field.
    """
    annual_ends, quarter_ends = facts.annual_ends, facts.quarter_ends
    orders, decision = {}, {}
    for name, pairs in CONCEPTS.items():
        every = [concept for concept, kind in pairs if kind in VALUE_KINDS]
        concepts = [concept for concept in every if concept not in ARBITER_ONLY_CONCEPTS]
        orders[name] = concepts
        if len(every) < 2 or name in PRIORITY_ONLY_FIELDS:
            continue
        if name in INSTANT_FIELDS:
            def annual_of(concept, end):
                return facts.instant(concept, end, ("annual", "quarterly"))

            def quarter_of(concept, end):
                pools = ("annual", "quarterly") if end in facts.annual_set else ("quarterly",)
                return facts.instant(concept, end, pools)
        else:
            def annual_of(concept, end):
                return _annual_flow(facts, concept, end)

            def quarter_of(concept, end, name=name):
                return _quarter_flow(facts, name, concept, end, end in facts.annual_set)
        coverage = {
            concept: sum(1 for end in annual_ends if annual_of(concept, end) is not None)
            + sum(1 for end in quarter_ends if quarter_of(concept, end) is not None)
            for concept in every
        }
        candidates = [concept for concept in concepts if coverage[concept]]
        primary = concepts[0]
        if len(candidates) >= 2:
            if name in CROSS_CHECK_FIELDS:
                mismatches = {
                    primary: _cross_check(facts, [primary] + [c for c in concepts if c != primary],
                                          annual_of, quarter_of)
                    for primary in candidates
                }
                fewest = min(mismatches.values())
                candidates = [concept for concept in candidates if mismatches[concept] == fewest]
            primary = candidates[0]
            for concept in candidates[1:]:
                if coverage[concept] > coverage[primary] + max(COVERAGE_GAIN_PERIODS,
                                                               COVERAGE_GAIN_RATIO * coverage[primary]):
                    primary = concept
        orders[name] = [primary] + [concept for concept in concepts if concept != primary]
        if name == "revenue":
            chosen = _arbitrate(facts, [c for c in every if coverage[c]], orders[name], reference or {})
            if chosen is not None:
                orders[name] = decision[name] = chosen
    return orders, decision


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _yahoo_pairs(facts, values):
    """
    [(SEC annual end, Yahoo revenue)]: values {"YYYY-MM-DD": Yahoo annual revenue}; a Yahoo year
    matches the nearest SEC annual end within YAHOO_MATCH_DAYS (Yahoo labels 52/53-week years with
    month ends); zero / non-numeric values are ignored.
    """
    reference = sorted(
        (day, value) for text, value in (values.items() if isinstance(values, dict) else ())
        for day in [parse_day(text)] for value in [finite_number(value)]
        if day is not None and value is not None and value != 0
    )
    pairs = []
    for end in facts.annual_ends:
        near = [(abs((day - end).days), day, value) for day, value in reference
                if abs((day - end).days) <= YAHOO_MATCH_DAYS]
        if near:
            pairs.append((end, min(near)[2]))
    return pairs


def _series_errors(facts, order, pairs):
    """|SEC / Yahoo − 1| of the annual revenue the concept order `order` yields, per Yahoo year it covers."""
    errors = []
    for end, yahoo in pairs:
        fact = _first(facts.annual.get(concept, {}).get(end) for concept in order)
        if fact is not None:
            errors.append(abs(fact.value / yahoo - 1))
    return errors


def _arbiter_choice(facts, candidates, default, values, need):
    """
    The revenue order the Yahoo values point to, or None when they cannot tell. default: the concept
    rules' order; candidates: the revenue concepts having data. need: Yahoo years a candidate's own
    annual values must cover (None: ARBITER_MIN_YEARS, or 1 when only one SEC year has a Yahoo value).

    Each candidate X gets its own median relative error against Yahoo, and the order it would give:
    X first, then the default order without the concepts Yahoo contradicts (median error more than
    ARBITER_MARGIN above X's: the narrow / other-scope ones), then those. That order's SERIES is judged
    in the Yahoo years (fallbacks included). The best series (smallest median error; ties: CONCEPTS
    order) replaces the default only when the default's series is more than ARBITER_MARGIN worse (or
    has no value in the Yahoo years) and the best is within ARBITER_MAX_ERROR (else Yahoo defines
    revenue otherwise, e.g. net of interest expense / provisions for a bank, and the "closest" concept
    could be any partial line item). Years Yahoo does not cover keep the default's preferences, except
    that contradicted concepts move behind the others (a concept that ended before the Yahoo years is
    never judged, so it is never demoted). Returns the default order when it was judged and kept.
    """
    pairs = _yahoo_pairs(facts, values)
    need = min(ARBITER_MIN_YEARS, len(pairs)) if need is None else need
    if need < 1:
        return None
    own = {}
    for concept in candidates:
        errors = _series_errors(facts, [concept], pairs)
        if errors:
            own[concept] = _median(errors)
    judged = {}
    for concept in candidates:
        if len(_series_errors(facts, [concept], pairs)) < need:
            continue
        rest = [c for c in default if c != concept]
        bad = [c for c in rest if c in own and own[c] > own[concept] + ARBITER_MARGIN]
        order = [concept] + [c for c in rest if c not in bad] + bad
        errors = _series_errors(facts, order, pairs)
        judged[concept] = (_median(errors), order)
    if not judged:
        return None
    best = min(judged, key=lambda concept: (judged[concept][0], candidates.index(concept)))
    base = _series_errors(facts, default, pairs)
    base = _median(base) if len(base) >= need else None
    if base is not None and (base <= judged[best][0] + ARBITER_MARGIN or judged[best][0] > ARBITER_MAX_ERROR):
        return list(default)
    if judged[best][0] > ARBITER_MAX_ERROR:
        return None
    return judged[best][1]


def _stored_order(stored, candidates, default):
    """A stored arbiter order still usable with this payload (its first concept has data), completed with
    the default order's other concepts; else None. ARBITER_ONLY_CONCEPTS only count in first place."""
    if not isinstance(stored, list) or not stored or stored[0] not in candidates:
        return None
    order = [stored[0]]
    for concept in list(stored[1:]) + list(default):
        if isinstance(concept, str) and concept in default and concept not in order:
            order.append(concept)
    return order


def _arbitrate(facts, candidates, default, reference):
    """
    Revenue order chosen with the Yahoo reference (extract_periods' `reference`), or None (the concept
    rules decide). candidates: the revenue concepts having data (ARBITER_ONLY_CONCEPTS included);
    default: the concept rules' order. Yahoo only arbitrates between SEC concepts: every value still
    comes from SEC. In this order:
    1. reference["annual"]: Yahoo's own annual periods (fresh values of a Yahoo-only file);
       ARBITER_MIN_YEARS years of overlap needed, 1 when only one SEC year overlaps at all — but
       always ARBITER_MIN_YEARS when a stored decision exists (one new Yahoo year does not overturn it);
    2. reference["arbiter"]["revenue"]: the order stored by an earlier seed (secConcepts.arbiter), if its
       first concept still has data. After a seed the Yahoo periods it overlapped are replaced by SEC
       periods, so a re-seed only finds the leftovers below; reusing the decision keeps re-seeding
       idempotent;
    3. reference["differs"]: Yahoo values of "yahoo-differs" revisions (files seeded before the arbiter
       existed). Only years where the stored SEC value was > 2% off are there, so this is used only
       without a stored decision.
    Grey zone, by design: when Yahoo's revenue has another definition (gross vs net: HAS / MO / RSG /
    BX file 12-20% above Yahoo; banks / brokers where Yahoo nets interest expense or provisions), the
    concept closest to Yahoo wins only within ARBITER_MAX_ERROR, and a single-concept series (BX) is
    never second-guessed.
    A decision is returned whenever the reference could judge between at least two candidates (even
    "keep the default"), so that it can be stored and a re-seed without the same reference repeats it.
    """
    if len(candidates) < 2 and not ARBITER_ONLY_CONCEPTS & set(candidates):
        return None             # one concept the rules use anyway: nothing to choose
    stored = reference.get("arbiter")
    stored = _stored_order(stored.get("revenue") if isinstance(stored, dict) else None, candidates, default)
    chosen = _arbiter_choice(facts, candidates, default, reference.get("annual"),
                             ARBITER_MIN_YEARS if stored else None)
    if chosen is None and stored:
        chosen = stored
    if chosen is None:
        chosen = _arbiter_choice(facts, candidates, default, reference.get("differs"), None)
    return chosen


def _cross_check(facts, order, annual_of, quarter_of):
    """Fiscal years where the series built with `order` has four quarters not adding up to the annual value."""
    count = 0
    for end in facts.annual_ends:
        found = _first((concept, fact) for concept in order for fact in [annual_of(concept, end)] if fact is not None)
        previous = facts.previous_quarter(end)
        if found is None or previous is None:
            continue
        annual_concept, annual = found
        ends = facts.chain(previous, annual.start)
        if ends is None or len(ends) != 3:
            continue
        quarters = []
        for day in ends + [end]:
            found = next(((concept, value) for concept in order
                          for value in [quarter_of(concept, day)] if value is not None), None)
            if found is None:
                break
            concept, (value, derived) = found
            quarters.append(value if derived else _norm(concept, value))
        else:
            # Any filed version of the annual value counts (a restatement / recast of the annual
            # value alone is a version question, not a scope one)
            start = facts.start_of[annual.start]
            versions = [fact for fact in facts.annual_versions.get(annual_concept, {}).get(end, [annual])
                        if facts.start_of[fact.start] == start] or [annual]
            if all(abs(sum(quarters) - _norm(annual_concept, fact.value))
                   > CROSS_CHECK_TOLERANCE * abs(_norm(annual_concept, fact.value)) for fact in versions):
                count += 1
    return count


def _annual_period(facts, end, orders):
    values, used, issues = {}, {}, set()
    for name in _FLOW_ORDER:
        for concept in orders[name]:
            fact = _annual_flow(facts, concept, end)
            if fact is not None and (concept not in CONTINUING_CONCEPTS or _continuing_ok(fact.value, values)):
                _store(name, fact.value, values, issues)
                used[name] = [concept]
                break
    _instant_fields(facts, end, ("annual", "quarterly"), values, used, issues, orders)
    if eps_inconsistent(values):
        issues.add("eps-inconsistent")
    return period(end.isoformat(), values, SRC), used, issues


def _quarter_period(facts, end, orders):
    """→ (period, used concepts, issues, latest `filed` of its revenue / net income) or None."""
    values, used, issues = {}, {}, set()
    derived = set()
    filed = ""
    is_q4 = end in facts.annual_set
    for name in _FLOW_ORDER:
        for concept in orders[name]:
            result = _quarter_flow(facts, name, concept, end, is_q4)
            if result is not None and (concept not in CONTINUING_CONCEPTS or _continuing_ok(result[0], values)):
                _store(name, result[0], values, issues, result[1])
                used[name] = [concept]
                if result[1]:
                    derived.add(name)
                if name in EXISTENCE_FIELDS:
                    filed = max(filed, facts._quarter(concept, end)[2])
                break
    if not any(name in values for name in EXISTENCE_FIELDS):
        return None
    # A derived Q4 EPS of the opposite sign to the quarter's net income is another basis / version mix
    # (ADI, AMD, HPQ: −33.47 for a profitable quarter): null, never stored
    income = values.get("netIncome")
    for name in Q4_PER_SHARE_FIELDS:
        if name in derived and income and values.get(name) and (values[name] > 0) != (income > 0):
            del values[name]
            used.pop(name, None)
            derived.discard(name)
            facts.dropped[name] += 1
    pools = ("annual", "quarterly") if is_q4 else ("quarterly",)
    _instant_fields(facts, end, pools, values, used, issues, orders)
    if derived and is_q4:
        issues.add("q4-derived")
    extra = {DERIVED: 1 if derived else None}
    return period(end.isoformat(), values, SRC, extra), used, issues, filed


def _drop_colliding_quarters(built):
    """
    [(end, built quarter)] ascending → the same without the quarters of another reporting calendar:
    two quarter ends less than QUARTER_DAYS[0] apart (but more than SAME_PERIOD_DAYS, which are one
    period) cannot both be quarters of one calendar; this happens when one CIK carries two accounting
    entities (a merger's predecessor and the legal registrant, e.g. 2015-12-25 / 2015-12-31). The
    quarter whose revenue / net income was filed later (the registrant's current history) is kept,
    on a tie the later end. → (kept, dropped ends).
    """
    kept, dropped = [], []
    for end, item in built:
        while kept and (end - kept[-1][0]).days < QUARTER_DAYS[0]:
            previous = kept[-1]
            if (previous[1][3], previous[0]) > (item[3], end):
                dropped.append(end)
                break
            dropped.append(previous[0])
            kept.pop()
        else:
            kept.append((end, item))
    return kept, sorted(dropped)


def extract_periods(companyfacts, reference=None):
    """
    SEC companyfacts payload → SecResult (pure, deterministic; see the module docstring for the
    rules). When result.skip is set the payload must not produce a document; annual / quarterly may
    still hold whatever could be read (useful for diagnostics).

    reference (optional; reference_from_document builds it from the stored file): {"annual":
    {"YYYY-MM-DD": Yahoo annual revenue}, "differs": {SEC end: Yahoo revenue of a "yahoo-differs"
    revision}, "arbiter": {"revenue": stored order}}, every key optional. It only chooses WHICH SEC
    concepts make the revenue series (_arbitrate); no Yahoo value is ever copied. None → the concept
    rules alone, exactly as without the arbiter.
    """
    result = SecResult()
    if isinstance(companyfacts, dict):
        cik = companyfacts.get("cik")
        result.cik = "%010d" % cik if isinstance(cik, int) and not isinstance(cik, bool) else (
            cik.strip().zfill(10) if isinstance(cik, str) and cik.strip().isdigit() else None
        )
        name = companyfacts.get("entityName")
        result.entity = name.strip() if isinstance(name, str) and name.strip() else None
    facts = _Facts(companyfacts)
    orders, result.arbiter = _concept_orders(facts, reference if isinstance(reference, dict) else None)

    for end in facts.annual_ends:
        item, used, issues = _annual_period(facts, end, orders)
        if not any(name in item["values"] for name in EXISTENCE_FIELDS):
            continue
        result.annual.append(item)
        result.period_concepts[("annual", item["end"])] = used
        result.period_issues[("annual", item["end"])] = issues
    built = [(end, _quarter_period(facts, end, orders)) for end in facts.quarter_ends]
    built, result.dropped_quarters = _drop_colliding_quarters([pair for pair in built if pair[1] is not None])
    for end, (item, used, issues, _) in built:
        result.quarterly.append(item)
        result.period_concepts[("quarterly", item["end"])] = used
        result.period_issues[("quarterly", item["end"])] = issues
    # Partial debt balances next to full ones (_suspect_debt): null
    for frequency, items in (("annual", result.annual), ("quarterly", result.quarterly)):
        series = [(item["end"], item["values"]["totalDebt"], result.period_concepts[(frequency, item["end"])]["totalDebt"])
                  for item in items if item["values"].get("totalDebt") is not None]
        for end in _suspect_debt(series):
            identity = (frequency, end)
            item = next(p for p in items if p["end"] == end)
            del item["values"]["totalDebt"]
            del result.period_concepts[identity]["totalDebt"]
            result.period_issues[identity].discard("debt-approx")
            facts.dropped["totalDebt"] += 1

    concepts = {}
    for used in result.period_concepts.values():
        for name, names in used.items():
            concepts.setdefault(name, set()).update(names)
    result.concepts = {name: sorted(concepts[name]) for name in CONCEPTS if name in concepts}
    for issues in result.period_issues.values():
        result.issues |= issues
    foreign = facts.foreign_now
    if foreign:
        result.issues.add("foreign-filer")
    result.dropped = {name: count for name, count in sorted(facts.dropped.items()) if count}

    if not facts.has_gaap:
        result.skip = "foreign-filer" if facts.has_ifrs or facts.foreign_forms else "no-us-gaap"
    elif not facts.usd and facts.other_currency:
        result.skip = "non-usd"
    elif len(result.annual) < MIN_ANNUAL_PERIODS:
        result.skip = "foreign-filer" if foreign else "short-history"
    elif isinstance(reference, dict):
        error = _revenue_scope_error(facts, orders["revenue"], reference)
        if error is not None and error > REVENUE_SCOPE_MAX_ERROR:
            result.skip = "revenue-scope-mismatch"
    return result


def _revenue_scope_error(facts, order, reference):
    """
    Median |SEC / Yahoo − 1| of the annual revenue series `order` gives (the arbitrated one) over the
    years the stored file's Yahoo revenue overlaps (reference["annual"]; for a file seeded earlier, whose
    Yahoo years were replaced, the "yahoo-differs" values, at least ARBITER_MIN_YEARS of them), or None.
    Above REVENUE_SCOPE_MAX_ERROR the SEC series is another scope (HBAN / FITB / MTB / IBKR: the ASC 606
    fee line only, 15-25% of a bank's revenue) and the file is not written at all: the next Yahoo
    quarter would otherwise follow it as a fake 5x jump.
    """
    errors = _series_errors(facts, order, _yahoo_pairs(facts, reference.get("annual")))
    if not errors:
        errors = _series_errors(facts, order, _yahoo_pairs(facts, reference.get("differs")))
        if len(errors) < ARBITER_MIN_YEARS:
            return None
    # a concept switch late in the history (MTB: Revenues until 2023, then the ASC 606 fee line only)
    # leaves the overall median small while the latest years, the ones Yahoo will extend, are off
    return max(_median(errors), _median(errors[-3:]))


# ---------------------------------------------------------------------------------------------
# Merge into the stored document
# ---------------------------------------------------------------------------------------------
# Yahoo labels 52/53-week year ends with month-end dates (AAPL: SEC 2022-09-24, Yahoo 2022-09-30), up to
# ~7 days away from the SEC period end. Stored periods within this window of a SEC period are the same
# period (Yahoo's label, or a stored SEC period an earlier seed aligned to it, see _aligned_end).
YAHOO_MATCH_DAYS = 10
# Quarters of 52/53-week calendars: Yahoo's month-end label is 11-24 days from the SEC quarter end
# (COST, AZO, DPZ, KR, PEP, FERG). Safe because quarters are ≥ 80 days apart (and see _match_section).
YAHOO_QUARTER_MATCH_DAYS = 25


def _window(frequency):
    return YAHOO_MATCH_DAYS if frequency == "annual" else YAHOO_QUARTER_MATCH_DAYS


def _match_section(periods, new, frequency):
    """
    {index of a SEC period in `new`: [indexes of the stored periods it replaces]}. Every stored period
    (SEC or Yahoo) within YAHOO_MATCH_DAYS (annual) / YAHOO_QUARTER_MATCH_DAYS (quarterly) of a SEC period
    goes to its NEAREST SEC period (ties: the earlier one), and a SEC period takes all of them: two
    periods of one calendar are ≥ 80 days apart, so whatever lies this close is the same fiscal period
    (Yahoo's month-end re-label, an empty duplicate of it — AZO 2026-04-30 next to 2026-05-31 — or a
    stored SEC period whose end an earlier seed aligned to Yahoo's label).
    """
    window = _window(frequency)
    pairs = sorted(
        (distance, index, j)
        for j, item in enumerate(new)
        for index, p in enumerate(periods)
        for distance in [abs(days_between(p["end"], item["end"]))]
        if distance <= window
    )
    decided, matches = set(), {}
    for distance, index, j in pairs:
        if index in decided:
            continue
        decided.add(index)          # its nearest SEC period (ties: the earlier one) decides
        matches.setdefault(j, []).append((distance, periods[index]["end"], index))
    return {j: [index for _, _, index in sorted(hits)] for j, hits in matches.items()}


def _has_values(item):
    return any(value is not None for value in item["values"].values())


def _aligned_end(end, targets, latest):
    """
    The end date a SEC period is written with. build_financials.merge_section treats only ±5 days as one
    period and appends a Yahoo period dated after the latest SEC period: a SEC period that absorbed a
    LATER label more than SAME_PERIOD_DAYS away (NVDA 2026-01-25 / Yahoo 2026-01-31, CSCO 07-25 / 07-31,
    AZO 05-09 / 05-31) would get that label back as a duplicate on the next Yahoo run when the label lies
    after the latest SEC end `latest`. Such a period keeps its SEC values but takes the label: a non-empty
    Yahoo period first, else a stored SEC period already aligned by an earlier seed (re-seeding repeats
    the alignment), else an empty Yahoo period; the nearest of them. Every other period keeps its SEC end.
    """
    later = [t for t in targets if days_between(end, t["end"]) > SAME_PERIOD_DAYS]
    if not later or latest is None:
        return end

    def preference(target):
        rank = 0 if target["src"] != SRC and _has_values(target) else 1 if target["src"] == SRC else 2
        return rank, abs(days_between(end, target["end"])), target["end"]
    label = min(later, key=preference)["end"]
    return label if label > latest else end


def _merge_section(old, new, frequency, today_iso):
    """
    Stored periods + SEC periods → (periods ascending, new revisions, renamed {old end: new end}, counts,
    aligned {written end: SEC end}). Every SEC period replaces the stored periods _match_section assigns
    to it (written at _aligned_end); stored Yahoo periods left that overlap a SEC period of the new
    extraction (closer than OVERLAP_DAYS: another calendar after a fiscal-year change, FERG 2025-04-30
    next to the recast 2025-03-31) are dropped too.
    """
    keep = KEEP_ANNUAL if frequency == "annual" else KEEP_QUARTERLY
    periods = [period(p["end"], p["values"], p["src"], p["extra"]) for p in old]
    renamed, revisions, aligned = {}, [], {}
    counts = {"replacedYahoo": 0, "replacedSec": 0, "keptYahoo": 0, "keptSec": 0, "droppedYahoo": 0,
              "droppedOverlap": 0}
    result = []
    assigned = _match_section(periods, new, frequency)
    matched = {index for indexes in assigned.values() for index in indexes}
    sec_ends = [item["end"] for item in new] + [
        p["end"] for index, p in enumerate(periods) if p["src"] == SRC and index not in matched
    ]
    latest = max(sec_ends) if sec_ends else None
    for j, item in enumerate(new):
        targets = [periods[index] for index in assigned.get(j, ())]
        end = _aligned_end(item["end"], targets, latest)
        if end != item["end"]:
            aligned[end] = item["end"]
        extra = {}
        for target in targets:
            counts["replacedSec" if target["src"] == SRC else "replacedYahoo"] += 1
            if target["src"] != SRC:
                for name in EXISTENCE_FIELDS:
                    old_value, new_value = target["values"].get(name), item["values"].get(name)
                    if old_value is not None and new_value is not None:
                        if relative_change(old_value, new_value) > RESTATE_TOLERANCE:
                            # Same orientation as build_financials.merge_section: old = SEC, new = Yahoo,
                            # so a later Yahoo run records the identical revision and it de-duplicates.
                            revisions.append(revision("yahoo-differs", frequency, end, name,
                                                      new_value, old_value, today_iso))
            # Unknown per-period columns of the stored period survive; SEC's own columns win
            extra.update(target["extra"])
            if target["end"] != end:
                renamed[target["end"]] = end
        extra.update(item["extra"])
        result.append(period(end, item["values"], SRC, extra))
    gap = OVERLAP_DAYS[frequency]
    for index, item in enumerate(periods):
        if index in matched:
            continue
        if item["src"] != SRC and any(abs(days_between(item["end"], other["end"])) < gap for other in new):
            counts["droppedOverlap"] += 1
            continue
        counts["keptSec" if item["src"] == SRC else "keptYahoo"] += 1
        result.append(item)
    result.sort(key=lambda p: p["end"])
    # Same cap as build_financials.merge_section: oldest non-SEC periods go first, SEC never
    while len(result) > keep and result[0]["src"] != SRC:
        counts["keptYahoo"] -= 1
        counts["droppedYahoo"] += 1
        del result[0]
    return result, revisions, renamed, counts, aligned


def _dedupe_labels(annual, quarterly):
    """
    Final pass after the fiscal labels: a Yahoo period carrying the fiscal year (annual) or (fiscal year,
    quarter) of a SEC period is a duplicate of it and goes (SEC is kept) → number dropped. Two SEC periods
    with one label (a fiscal-year change) are left alone.
    """
    dropped = 0
    for periods, label in ((annual, lambda p: p["fy"]),
                           (quarterly, lambda p: None if p["fq"] is None else (p["fy"], p["fq"]))):
        taken = {label(p) for p in periods if p["src"] == SRC}
        kept = [p for p in periods if p["src"] == SRC or label(p) is None or label(p) not in taken]
        dropped += len(periods) - len(kept)
        periods[:] = kept
    return dropped


def _level_rank(level):
    return {"limited": 0, "partial": 1, "good": 2}.get(level, 0)


def _suffix_factor(usable, end, ratio):
    """The product of a suffix of the split events after `end` closest to `ratio` (what adjust_splits applied)."""
    best, factor = 1, 1
    for _, split in reversed([event for event in usable if event[0] > end]):
        factor *= split
        if abs(math.log(factor / ratio)) < abs(math.log(best / ratio)):
            best = factor
    return best


def _adjust_splits(annual, quarterly, events):
    """
    build_financials.adjust_splits([annual, quarterly], events), in place, plus the SEC quarters that
    hold EPS but no share count (Q4 = annual − Q1..Q3, which has no diluted share count): adjust_splits
    cannot see them, so they take the split factor just applied to the annual period ending within
    SAME_PERIOD_DAYS (the Q4 value is on the annual figure's basis, see _Facts.q4_per_share).
    Returns True if anything changed.
    """
    before = {id(item): shares_of(item) for item in annual}
    if not adjust_splits([annual, quarterly], events):
        return False
    usable = sorted(
        (day, ratio) for day, ratio in events if ratio >= MIN_SPLIT_RATIO or ratio <= 1 / MIN_SPLIT_RATIO
    )
    factors = []
    for item in annual:
        old, new = before.get(id(item)), shares_of(item)
        if old and new and new != old:
            factor = _suffix_factor(usable, item["end"], new / old)
            if factor != 1:
                factors.append((item["end"], factor))
    for item in quarterly:
        if item["src"] != SRC or shares_of(item) is not None:
            continue
        factor = next((f for end, f in factors if abs(days_between(end, item["end"])) <= SAME_PERIOD_DAYS), None)
        if factor is None:
            continue
        for name in Q4_PER_SHARE_FIELDS:
            if item["values"].get(name) is not None:
                item["values"][name] = round(item["values"][name] / factor, 4) + 0.0
    return True


def _implied_shares(annual, quarterly):
    """
    Gives every SEC period that has no diluted share count but an EPS (diluted, else basic; |EPS| ≥
    IMPLIED_MIN_EPS) and a net income of the same sign the implied count |net income / EPS|, IN PLACE,
    → the periods changed (the caller deletes the count again after the split step, it is never written).
    GOOGL / GOOG tag no share count without a class dimension: without this the pre-split EPS (2019:
    49.16 next to 2020: 2.93) would never trigger a split check (share_jump) nor be adjusted
    (adjust_splits only sees periods with a count). Q4 quarters (ending within SAME_PERIOD_DAYS of an
    annual period) are left out: _adjust_splits gives them their annual period's factor.
    """
    year_ends = [p["end"] for p in annual]
    changed = []
    for periods, frequency in ((annual, "annual"), (quarterly, "quarterly")):
        for item in periods:
            values = item["values"]
            if item["src"] != SRC or shares_of(item) is not None:
                continue
            if frequency == "quarterly" and any(abs(days_between(end, item["end"])) <= SAME_PERIOD_DAYS
                                                for end in year_ends):
                continue
            eps = values.get("epsDiluted") if values.get("epsDiluted") is not None else values.get("epsBasic")
            income = values.get("netIncome")
            if not eps or not income or (eps > 0) != (income > 0) or abs(eps) < IMPLIED_MIN_EPS:
                continue
            values["dilutedShares"] = abs(income / eps)
            changed.append(item)
    return changed


def scale_sec_to_adr(values, factor):
    """
    SEC per-ordinary-share values → the basis of a foreign reporter's file (1 ADR = factor ordinary shares):
    EPS × factor and the diluted share count ÷ factor, so EPS and count stay consistent (NI ÷ count = EPS).
    In place.
    """
    for field in ("epsBasic", "epsDiluted"):
        if values.get(field) is not None:
            values[field] = clean_value(values[field] * factor)
    if values.get("dilutedShares") is not None:
        values["dilutedShares"] = clean_value(values["dilutedShares"] / factor)


def merge_into_document(previous, sec, key, now, fetch_splits=None):
    """
    Merge one SecResult into the stored document (None for a new file) → (document or None, info).

    previous: the stored file's JSON (not modified). If build_financials.read_sections rejects it
      (or its symbol is another stock's) the document is built from SEC alone and info["status"] is
      "corrupt-previous": the caller should not overwrite such a file without an explicit decision.
    key: the file key (build_financials.financials_key form, US market).
    now: aware datetime of the run (naive = UTC); generatedAt / checkedAt / revision dates.
    fetch_splits: None, or fetch_splits() → Yahoo chart payload (events=split), as
      build_financials.update_document takes it. Called at most once, only when diluted share counts
      still jump after the stored split events were applied; any exception it raises is caught.

    Splits: companyfacts EPS / share counts are as reported, so periods before a split are on the
    old basis unless a later filing restated them. The stored events (+ the fetched ones) adjust
    them with build_financials.adjust_splits; split.adjusted (and the "split-adjusted" issue) stays
    true forever once set.

    info: {"status": "ok" | "skipped" | "corrupt-previous", "reason", "previous": "missing" | "ok" |
      "corrupt", "counts": {...}, "issues": [...], "level", "shareJump", "chart", "chartError",
      "splitAdjusted", "splitEvents"}. shareJump: adjacent diluted share counts still jump and no
      split check succeeded (the chart fetch failed, or without fetch_splits the file has no split
      events): the values are left as reported (split-check-pending). chart: None (not requested) |
      "fetched" | "failed" (chartError: the reason). splitAdjusted: values were adjusted in this
      merge. splitEvents: number of split events in the document. dropped: SecResult.dropped (values
      left null as implausible). counts also hold droppedOverlap / droppedDuplicate (stored Yahoo
      periods overlapping a SEC period / carrying its fiscal label).
    A stored adrRatio (foreign reporter, EPS per ADR): the SEC values are put on that basis first (EPS × k,
    diluted share count ÷ k), so the file keeps one EPS basis across reruns.
    Returns (None, info) when sec.skip is set, the key is not a US key, or the stored file is in
    another currency than USD.
    """
    info = {"status": "ok", "reason": None, "previous": "missing", "counts": {}, "issues": [],
            "level": None, "shareJump": False, "chart": None, "chartError": None,
            "splitAdjusted": False, "splitEvents": 0, "dropped": {}}
    if sec.skip:
        info.update(status="skipped", reason=sec.skip)
        return None, info
    if financials_key(key) != key or market_of(key) != "US":
        info.update(status="skipped", reason="not-a-us-key")
        return None, info
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    today_iso = now.date().isoformat()

    stored = None
    if previous is not None:
        try:
            stored = read_sections(previous)
            if previous.get("symbol") not in (None, key):
                raise ValueError(f"symbol {previous.get('symbol')!r}")
            info["previous"] = "ok"
        except ValueError as error:
            stored = None
            info.update(status="corrupt-previous", reason=str(error), previous="corrupt")
    if stored is not None and stored["currency"] not in (None, CURRENCY):
        info.update(status="skipped", reason=f"stored currency {stored['currency']}")
        return None, info
    if stored is None:
        stored = read_sections({"version": VERSION})

    # The newest 20 annual / 60 quarterly SEC periods (copies: the SecResult is never modified)
    new = {
        "annual": sorted(sec.annual, key=lambda p: p["end"])[-KEEP_ANNUAL:],
        "quarterly": sorted(sec.quarterly, key=lambda p: p["end"])[-KEEP_QUARTERLY:],
    }
    new = {
        frequency: [period(p["end"], p["values"], SRC, p["extra"]) for p in items]
        for frequency, items in new.items()
    }
    # A foreign reporter's file holds EPS per ADR (adrRatio, see build_financials.resolve_eps_basis): SEC's
    # per-ordinary-share values go onto that basis too, or the next Yahoo run and this one would mix them
    adr_factor = adr_eps_factor(previous) if info["previous"] == "ok" else None
    if adr_factor not in (None, 1):
        for items in new.values():
            for item in items:
                scale_sec_to_adr(item["values"], adr_factor)
    counts = {"secAnnual": len(new["annual"]), "secQuarterly": len(new["quarterly"]),
              "trimmedSec": len(sec.annual) + len(sec.quarterly) - len(new["annual"]) - len(new["quarterly"])}
    # Stored SEC quarters of a calendar this extraction left out (_drop_colliding_quarters) go too,
    # otherwise an earlier seed would keep them forever
    dropped = [day.isoformat() for day in sec.dropped_quarters]
    old_quarters = [
        p for p in stored["quarterly"]
        if p["src"] != SRC or not any(abs(days_between(p["end"], day)) <= SAME_PERIOD_DAYS for day in dropped)
    ]
    counts["droppedSec"] = len(stored["quarterly"]) - len(old_quarters)
    sections, revised, renamed, aligned = {}, [], {}, {}
    for frequency in ("annual", "quarterly"):
        merged, revisions, moved, section_counts, aligned[frequency] = _merge_section(
            stored[frequency] if frequency == "annual" else old_quarters, new[frequency], frequency, today_iso
        )
        sections[frequency], renamed[frequency] = merged, moved
        revised += revisions
        for name, value in section_counts.items():
            counts[name] = counts.get(name, 0) + value
    annual, quarterly = sections["annual"], sections["quarterly"]

    # Splits, exactly as build_financials.update_document: the stored events put pre-split SEC values
    # (companyfacts are as originally reported) on today's basis; if share counts still jump, the
    # chart events are fetched once (fetch_splits) and applied too. A failed fetch leaves the values
    # as reported (never raises, prints nothing: info["chart"] / info["chartError"] tell the caller).
    # SEC periods without a diluted share count take an implied one for this step only (_implied_shares).
    implied = _implied_shares(annual, quarterly)
    events = stored["events"]
    adjusted = _adjust_splits(annual, quarterly, events)
    if fetch_splits is not None and share_jump([annual, quarterly]):
        try:
            fetched = parse_splits(fetch_splits())
        except Exception as error:  # noqa: BLE001 - keep the SEC values as reported
            info.update(chart="failed", chartError=f"{type(error).__name__}: {error}")
        else:
            info["chart"] = "fetched"
            events = merge_events(events, fetched)
            adjusted = _adjust_splits(annual, quarterly, events) or adjusted
    info["splitAdjusted"], info["splitEvents"] = adjusted, len(events)
    adjusted = adjusted or stored["adjusted"]
    # Still unexplained share-count jump: no split check succeeded (fetch failed, or none was possible
    # and no events are stored). A jump left after a successful check is a real share-count change.
    if info["chart"] == "failed":
        info["shareJump"] = share_jump([annual, quarterly])
    elif info["chart"] is None:
        info["shareJump"] = not events and share_jump([annual, quarterly])
    for item in implied:
        del item["values"]["dilutedShares"]

    # Fiscal labels exactly as build_financials.update_document
    end_month = fy_end_month([p["end"] for p in annual])
    for item in annual:
        item["fy"] = fiscal_year(item["end"])
    for item in quarterly:
        if end_month is None:
            item["fy"], item["fq"] = effective(item["end"]).year, None
        else:
            item["fy"], item["fq"] = fiscal_quarter(item["end"], end_month)
    counts["droppedDuplicate"] = _dedupe_labels(annual, quarterly)
    # Q4 EPS on the basis the periods hold now (split adjusted, fiscal labels set): extraction could not derive
    # it across a split (an annual EPS restated after the split next to Q1..Q3 still pre-split, NFLX 2023-2024),
    # and the same rule as repair_q4_eps.py applies to the stored file (build_financials.fill_q4_eps)
    q4_filled = bool(fill_q4_eps(annual, quarterly))

    revisions = merge_revisions(stored["revisions"], revised, annual, quarterly, renamed)
    # (frequency, SEC end) of the SEC periods written: an aligned period counts under its SEC end
    kept = {
        (frequency, aligned[frequency].get(p["end"], p["end"]))
        for frequency, items in (("annual", annual), ("quarterly", quarterly)) for p in items if p["src"] == SRC
    }
    fresh = {(frequency, p["end"]) for frequency, items in new.items() for p in items} & kept
    sec_issues = set()
    for identity in sorted(fresh):
        sec_issues |= sec.period_issues.get(identity, set())
    if "foreign-filer" in sec.issues:
        sec_issues.add("foreign-filer")
    if q4_filled:
        sec_issues.add(Q4_ISSUE)
    # Stored SEC periods this run did not produce keep the SEC codes the stored file reported
    retained = bool(kept - fresh)
    previous_quality = previous.get("quality") if info["previous"] == "ok" else None
    if retained and isinstance(previous_quality, dict) and isinstance(previous_quality.get("issues"), list):
        sec_issues |= {code for code in previous_quality["issues"] if code in SEC_ISSUES}
    sec_issues.discard("eps-inconsistent")
    if any(eps_inconsistent(item["values"]) for item in annual):
        sec_issues.add("eps-inconsistent")

    flags = {r["kind"] for r in revisions}
    if adr_factor not in (None, 1):
        flags.add("adr-eps-rescaled")
    if "capex-sign" in sec_issues:
        flags.add("capex-sign")
    if adjusted:
        flags.add("split-adjusted")
    grade = quality(annual, quarterly, flags, False)
    grade["issues"] += sorted(code for code in sec_issues if code not in ISSUES)
    # Quality gates: ≥ 4 annual points of each GATE_FIELDS field, else partial / limited
    short = any(
        sum(1 for item in annual if item["values"].get(name) is not None) < GATE_YEARS for name in GATE_FIELDS
    )
    if short:
        mismatch = "quarter-annual-mismatch" in grade["issues"]
        gate = "limited" if len(annual) < 2 or mismatch else "partial"
        if _level_rank(gate) < _level_rank(grade["level"]):
            grade["level"] = gate
    if revisions:
        grade["revisions"] = revisions

    document = {
        "version": VERSION,
        "symbol": key,
        "market": "US",
        "currency": CURRENCY,
        "foreignReporter": False,
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "checkedAt": today_iso,
        "source": SOURCE_MIXED,
        "fyEndMonth": end_month or 12,
        "annual": write_section(annual, stored["extras"]["annual"], False),
        "quarterly": write_section(quarterly, stored["extras"]["quarterly"], True),
        "split": {"adjusted": adjusted, "events": events},
        "quality": grade,
    }
    for name, value in stored["other"].items():
        document.setdefault(name, value)
    document["secConcepts"] = _sec_concepts(sec, fresh, stored["other"].get("secConcepts") if retained else None)
    # The revenue order chosen by the Yahoo arbiter (or reused from the stored file), for the next
    # seed (reference_from_document); describes this extraction, so a stored one is never carried over
    arbiter = {name: list(order) for name, order in sorted((sec.arbiter or {}).items())
               if name in CONCEPTS and isinstance(order, list)}
    if arbiter:
        document["secConcepts"]["arbiter"] = arbiter

    counts.update(annual=len(annual), quarterly=len(quarterly), yahooDiffers=len(revised))
    info.update(counts=counts, issues=list(grade["issues"]), level=grade["level"], dropped=dict(sec.dropped))
    return document, info


def reference_from_document(document):
    """
    The stored file (None, Yahoo-only or already seeded) → extract_periods' `reference`, or None:
    "annual": revenue of its Yahoo annual periods; "differs": the Yahoo revenue of its annual
    "yahoo-differs" revisions (the newest seen per end); "arbiter": secConcepts["arbiter"] (the stored
    arbiter decision). Read-only; an unreadable file gives None.
    """
    if not isinstance(document, dict):
        return None
    try:
        sections = read_sections(document)
    except ValueError:
        return None
    annual = {
        p["end"]: p["values"]["revenue"] for p in sections["annual"]
        if p["src"] != SRC and finite_number(p["values"].get("revenue")) is not None
    }
    differs = {}
    revisions = [
        r for r in sections["revisions"]
        if r.get("kind") == "yahoo-differs" and r.get("period") == "annual" and r.get("field") == "revenue"
        and finite_number(r.get("new")) is not None
    ]
    for item in sorted(revisions, key=lambda r: (str(r.get("seen")), r["end"], float(r["new"]))):
        differs[item["end"]] = item["new"]
    concepts = document.get("secConcepts")
    stored = concepts.get("arbiter") if isinstance(concepts, dict) else None
    arbiter = {name: [c for c in order if isinstance(c, str)] for name, order in stored.items()
               if name in CONCEPTS and isinstance(order, list)} if isinstance(stored, dict) else {}
    return {"annual": annual, "differs": differs, "arbiter": arbiter}


def _sec_concepts(sec, identities, stored):
    """{field: sorted concepts} used by the SEC periods written now (+ the stored map, if given)."""
    concepts = {}
    for identity in sorted(identities):
        for name, names in (sec.period_concepts.get(identity) or {}).items():
            concepts.setdefault(name, set()).update(names)
    if isinstance(stored, dict):
        for name, names in stored.items():
            if name in CONCEPTS and isinstance(names, list):
                concepts.setdefault(name, set()).update(n for n in names if isinstance(n, str))
    return {name: sorted(concepts[name]) for name in CONCEPTS if concepts.get(name)}


__all__ = [
    "ANNUAL_FORMS", "CONCEPTS", "FIELD_UNITS", "FLOW_FIELDS", "INSTANT_FIELDS", "QUARTERLY_FORMS",
    "SEC_ISSUES", "SecResult", "eps_inconsistent", "extract_periods", "merge_into_document",
    "reference_from_document",
]


# =============================================================================================
# I/O: SEC access rules, client, CLI.  Local manual use only (never in CI).
#
#   python atlas/scripts/seed_financials_sec.py [--limit N] [--tickers AAPL,MSFT] [--force] [--no-splits]
#          [--out docs/report/financials] [--report-dir docs/report] [--data-dir data] [--budget SECONDS]
#   python atlas/scripts/seed_financials_sec.py --due [--budget SECONDS]     (quarterly re-check, see below)
#   python atlas/scripts/seed_financials_sec.py --due --dry-run               (offline: lists what is due)
#   python atlas/scripts/seed_financials_sec.py --foreign [--upgrade] [--dry-run]   (foreign filers, see sec_ifrs.py;
#          --upgrade merges the SEC document into the Yahoo-only foreign files, see upgrade_document)
#
# --due re-checks only the stored US files that already carry SEC periods and are due (select_due; the
# --foreign IFRS / US GAAP files, i.e. foreign reporters, are left out). due_reason: never checked, last check
# older than MAX_CHECK_AGE_DAYS, or latest period older than REPORT_DUE_DAYS and
# last checked more than RECHECK_DAYS ago). The SEC check date of each file is kept in index.json
# (items[KEY].secCheckedAt, SEC_CHECKED_KEY), never in the file and never in checkedAt (the Yahoo date).
# Oldest check first; every checked file is recorded, unchanged ones are not rewritten. --budget stops
# before the next ticker once SECONDS have passed (exit 0, "PAUSED"); a re-run continues with what is left.
# The first HTTP 403 of a --due run stops it at once (SecClient.stop_on_forbidden, SecForbidden): nothing is
# retried or worked around, and the file in progress is not recorded as checked.
#
# Split events come from Yahoo's chart endpoint exactly as in build_financials.main() (connect_yahoo,
# CHART_URL, events=split / range=max / interval=1mo, REQUEST_DELAY after every chart request). The
# Yahoo session is opened lazily, at most once per run, only when the first ticker whose share counts
# jump needs it; if it cannot be opened (or MAX_CHART_FAILURES chart requests fail in a row) the run
# goes on without split checks and those tickers are reported split-check-pending. --no-splits never
# contacts Yahoo.
#
# SEC fair-access rules followed here: a descriptive User-Agent with a contact address on every
# request (env SEC_USER_AGENT, default DEFAULT_USER_AGENT), at most ~7 requests per second
# (MIN_INTERVAL after EVERY request, failed ones included), exponential backoff on 429 / 403 / 5xx /
# network errors, and a hard stop after MAX_CONSECUTIVE_FAILURES failed requests in a row.
# Exit codes: 0 done (also PAUSED by --budget), 1 stopped early (too many failures / HTTP 403 / ticker map
# unavailable), 2 refused to run.
# =============================================================================================
import argparse  # noqa: E402
import gzip  # noqa: E402
import http.client  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import textwrap  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
import zlib  # noqa: E402
from datetime import datetime  # noqa: E402

import importlib.util  # noqa: E402

from build_financials import (  # noqa: E402
    CHART_URL, REQUEST_DELAY, collect_symbols, connect_yahoo, has_sec, index_item, load_document, same_content,
)
from build_valuation_us import read_json, write_atomic  # noqa: E402
import sec_ifrs  # noqa: E402

DEFAULT_USER_AGENT = "SecSeed w556087587@gmail.com"
USER_AGENT_ENV = "SEC_USER_AGENT"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
MIN_INTERVAL = 0.13                     # seconds slept after every request (SEC allows 10/s; this is ~7.5/s)
BACKOFF_DELAYS = (2, 4, 8, 16, 32)      # seconds before retry 1..5 of one URL (6 attempts in all)
MAX_CONSECUTIVE_FAILURES = 10           # failed attempts in a row (across URLs) → TooManyFailures
REQUEST_TIMEOUT = 60
ESTIMATE_SECONDS_PER_REQUEST = 0.2
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXIT_DONE, EXIT_STOPPED, EXIT_REFUSED = 0, 1, 2
# Yahoo chart request of build_financials.main()'s fetch_chart
CHART_PARAMS = {"events": "split", "range": "max", "interval": "1mo"}
MAX_CHART_FAILURES = 3                  # chart requests failing in a row → no more split checks this run
PENDING = "split-check-pending"
# Result labels of one ticker, in summary order
RESULTS = (
    "written", "unchanged", "already-sec", "has-file", "skipped", "no-cik", "no-facts", "not-us", "bad-ticker",
    "corrupt-previous", "fetch-error", "extract-error", "upgraded", "currency-changed", "no-sec-data",
    "not-yahoo-only", "not-processed",
)
# --foreign: outcomes that are final for a ticker (recorded in the check state, not asked again)
FOREIGN_FINAL = ("written", "skipped", "no-cik", "no-facts")
# --upgrade: the outcomes that wrote a merged file (no state record: the file itself is the answer) ...
UPGRADE_RESULTS = ("upgraded", "currency-changed")
# ... and the answers that leave the file untouched, recorded in state["upgraded"] (sec_ifrs.UPGRADE_FINAL)
UPGRADE_FINAL = sec_ifrs.UPGRADE_FINAL


class UserAgentError(ValueError):
    """SEC_USER_AGENT is set but unusable."""


class FetchError(Exception):
    """One URL could not be fetched (attempts exhausted, or a non-retryable status)."""


class TooManyFailures(Exception):
    """MAX_CONSECUTIVE_FAILURES failed requests in a row: stop the run, keep the files already written."""


class SecForbidden(RuntimeError):
    """SEC answered HTTP 403 to a request of a --due run (SecClient.stop_on_forbidden): the run stops at once."""

    def __init__(self, url):
        super().__init__(f"SEC returned HTTP 403 for {url}")
        self.url = url


def resolve_user_agent(environ):
    """User-Agent for SEC requests: env SEC_USER_AGENT, default DEFAULT_USER_AGENT; set-but-blank is an error."""
    if USER_AGENT_ENV not in environ:
        return DEFAULT_USER_AGENT
    value = environ[USER_AGENT_ENV]
    value = value.strip() if isinstance(value, str) else ""
    if not value:
        raise UserAgentError(f"{USER_AGENT_ENV} is set but empty: SEC requires a User-Agent with contact details")
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in value):
        raise UserAgentError(f"{USER_AGENT_ENV} must be printable ASCII on one line")
    return value


def refusal_reason(environ):
    """Why this run must not start (CI / GitHub Actions), or None."""
    for name in ("CI", "GITHUB_ACTIONS"):
        value = environ.get(name)
        if isinstance(value, str) and value.strip().lower() not in ("", "0", "false", "no", "off"):
            return f"{name} is set: seed_financials_sec.py is for local manual use only, never in CI"
    return None


def cik10(cik):
    """10-digit zero-padded CIK string (the form data.sec.gov URLs use)."""
    return "%010d" % int(cik)


def decode_json(body):
    """Response bytes (possibly gzip) → JSON object; ValueError when it is not one."""
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError, zlib.error) as error:
            raise ValueError(f"bad gzip body: {error}") from error
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, (dict, list)):
        raise ValueError("JSON is not an object")
    return payload


def urllib_opener(request):
    """The real opener: urllib only → (status, body bytes); network errors propagate."""
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        try:
            body = error.read()
        except (OSError, http.client.HTTPException):
            body = b""
        finally:
            error.close()
        return error.code, body


class SecClient:
    """
    Rate-limited, polite SEC client. opener(request) → (status, bytes) (raises OSError on network
    failure) and sleeper(seconds) are injectable for tests.

    - Every request is followed by sleeper(MIN_INTERVAL), whatever its outcome.
    - 429 / 403 / 5xx / network errors / unparsable bodies are retried after sleeper(backoff[n]);
      other statuses fail at once. A URL gives up with FetchError after len(backoff) + 1 attempts.
    - Every failed attempt counts towards consecutive_failures (across URLs); a good answer (and a
      404 where allowed) resets it. At max_failures → TooManyFailures.
    - stop_on_forbidden (--due runs): an HTTP 403 is not retried; it raises SecForbidden at once.
    """

    def __init__(self, user_agent, opener=None, sleeper=None,
                 backoff=BACKOFF_DELAYS, max_failures=MAX_CONSECUTIVE_FAILURES, stop_on_forbidden=False):
        self.user_agent = user_agent
        self.opener = opener or urllib_opener
        self.sleeper = sleeper or time.sleep
        self.backoff = tuple(backoff)
        self.max_failures = max_failures
        self.stop_on_forbidden = stop_on_forbidden
        self.consecutive_failures = 0
        self.requests = 0
        self.failed_attempts = 0
        self.bytes_received = 0

    def _request(self, url):
        request = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip",
            "Accept": "application/json",
        })
        self.requests += 1
        try:
            return self.opener(request)
        finally:
            self.sleeper(MIN_INTERVAL)

    def _failed(self, url, problem):
        self.failed_attempts += 1
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_failures:
            raise TooManyFailures(f"{self.consecutive_failures} consecutive failed requests (last: {problem} for {url})")

    def get_json(self, url, allow_404=False):
        """Decoded JSON of `url`; None for a 404 when allow_404. FetchError / TooManyFailures otherwise."""
        for attempt in range(len(self.backoff) + 1):
            retry, problem = True, None
            try:
                status, body = self._request(url)
            except (OSError, http.client.HTTPException) as error:
                problem = f"{type(error).__name__}: {error}"
            else:
                self.bytes_received += len(body or b"")
                if status == 200:
                    try:
                        payload = decode_json(body)
                    except ValueError as error:
                        problem = f"unreadable response ({error})"
                    else:
                        self.consecutive_failures = 0
                        return payload
                elif status == 404 and allow_404:
                    self.consecutive_failures = 0
                    return None
                elif status == 403 and self.stop_on_forbidden:
                    raise SecForbidden(url)
                else:
                    problem = f"HTTP {status}"
                    retry = status in (403, 429) or status >= 500
            self._failed(url, problem)
            if not retry or attempt == len(self.backoff):
                raise FetchError(f"{problem} for {url}")
            self.sleeper(self.backoff[attempt])
        raise FetchError(f"no attempt for {url}")       # unreachable: the loop returns or raises

    def ticker_map(self):
        """{financials key: 10-digit CIK} from company_tickers.json."""
        return parse_ticker_map(self.get_json(TICKERS_URL))

    def company_facts(self, cik):
        """companyfacts payload of one CIK, or None when SEC has none (404)."""
        return self.get_json(FACTS_URL.format(cik=cik10(cik)), allow_404=True)


def parse_ticker_map(payload):
    """company_tickers.json ({"0": {"cik_str": 320193, "ticker": "AAPL", ...}, ...}) → {key: 10-digit CIK}."""
    entries = payload.values() if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    mapping = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cik = entry.get("cik_str")
        if isinstance(cik, str) and cik.strip().isdigit():
            cik = int(cik)
        key = financials_key(entry.get("ticker"))
        if key is None or market_of(key) != "US" or isinstance(cik, bool) or not isinstance(cik, int) or cik <= 0:
            continue
        mapping.setdefault(key, cik10(cik))        # the first listing of a ticker wins
    return mapping


class SplitUnavailable(RuntimeError):
    """No split check is possible in this run (Yahoo could not be reached)."""


def _default_yahoo():
    """build_valuation_us.Yahoo(), the session build_financials uses (needs curl_cffi, see SplitChecker)."""
    from build_valuation_us import Yahoo
    return Yahoo()


def _missing_curl_cffi():
    """Why the default Yahoo session cannot work at all (curl_cffi not importable), or None."""
    if importlib.util.find_spec("curl_cffi") is None:
        return "curl_cffi is not installed (pip install curl_cffi)"
    return None


def drop_pre_epoch_splits(payload):
    """
    Remove split events dated before 1970 from a Yahoo chart payload (in place). build_financials.parse_splits
    converts timestamps with datetime.fromtimestamp, which raises OSError for negative values on Windows
    (KO has 1920s/30s splits); those events predate every SEC XBRL period, so they are irrelevant here.
    """
    chart = payload.get("chart") if isinstance(payload, dict) else None
    results = chart.get("result") if isinstance(chart, dict) else None
    for result in results if isinstance(results, list) else []:
        events = result.get("events") if isinstance(result, dict) else None
        splits = events.get("splits") if isinstance(events, dict) else None
        if isinstance(splits, dict):
            for name in [n for n, e in splits.items()
                         if isinstance(e, dict) and isinstance(e.get("date"), (int, float)) and e["date"] < 0]:
                del splits[name]
    return payload


class SplitChecker:
    """
    Lazily connected Yahoo chart client for split events (build_financials.main()'s fetch_chart).

    The session is created by the first fetch_chart() call (build_financials.connect_yahoo, with
    `factory` / `sleep`), at most once: a failed connection is remembered and every later call raises
    SplitUnavailable at once. With the default factory a missing curl_cffi fails at once, without
    connect_yahoo's patient retries (30 + 60 + 120 s for an error no retry can fix). Every chart request
    is followed by sleep(REQUEST_DELAY); MAX_CHART_FAILURES failed chart requests in a row also end split
    checking for the run.
    """

    def __init__(self, factory=None, sleep=None, log=None):
        self.default = factory is None
        self.factory = factory or _default_yahoo
        self.sleep = sleep or time.sleep
        self.log = log or (lambda text: print(text, flush=True))
        self.yahoo = None
        self.requests = 0
        self.failures = 0
        self.failed = None          # why split checks stopped, once they did

    def state(self):
        if self.failed:
            return f"unavailable ({self.failed})"
        return "connected" if self.yahoo is not None else "not needed"

    def fetch_chart(self, key):
        if self.failed:
            raise SplitUnavailable(self.failed)
        if self.yahoo is None:
            try:
                missing = _missing_curl_cffi() if self.default else None
                if missing:
                    raise SplitUnavailable(missing)
                self.yahoo = connect_yahoo(self.factory, self.sleep)
            except Exception as error:  # noqa: BLE001 - seeding goes on without split checks
                self.failed = f"Yahoo connection failed: {type(error).__name__}: {error}"
                self.log(f"{self.failed}; continuing without split checks (tickers flagged {PENDING})")
                raise SplitUnavailable(self.failed) from error
        self.requests += 1
        try:
            payload = self.yahoo.get_json(CHART_URL.format(symbol=key), dict(CHART_PARAMS))
        except Exception as error:
            self.failures += 1
            if self.failures >= MAX_CHART_FAILURES:
                self.failed = f"{self.failures} chart requests failed in a row (last: {type(error).__name__}: {error})"
                self.log(f"Yahoo: {self.failed}; continuing without split checks")
            raise
        finally:
            self.sleep(REQUEST_DELAY)
        self.failures = 0
        return drop_pre_epoch_splits(payload)


def read_index(out_dir):
    """<out_dir>/index.json → (state, index): "ok" (version 1, items an object), "absent" or "unreadable"."""
    path = os.path.join(out_dir, "index.json")
    if not os.path.exists(path):
        return "absent", None
    index = read_json(path)
    if not isinstance(index, dict) or index.get("version") != VERSION or not isinstance(index.get("items", {}), dict):
        return "unreadable", None
    return "ok", index


def update_index(out_dir, documents, today_iso, checks=None):
    """
    Update items[KEY] of an EXISTING <out_dir>/index.json for the documents written by this run
    (build_financials.index_item with the entry's own checkedAt / lastListed) and for the --due checks
    (checks: {KEY: date of the SEC check}, stored as SEC_CHECKED_KEY). Everything else in the file is kept,
    and an item keeps its SEC check date unless this run sets a new one.
    → (state, count, bytes): state "absent" (never created), "unreadable" (left untouched), "updated" or
    "unchanged".
    """
    state, index = read_index(out_dir)
    if state != "ok":
        return state, 0, 0
    checks = checks or {}
    if not documents and not checks:
        return "unchanged", 0, 0
    items = dict(index.get("items") or {})
    touched = sorted(set(documents) | set(checks))
    for key in touched:
        known = items.get(key) if isinstance(items.get(key), dict) else {}
        if key in documents:
            listed = known.get("lastListed") if parse_day(known.get("lastListed")) else today_iso
            item = index_item(documents[key], known.get("checkedAt"), listed)
        elif known:
            item = dict(known)
        else:
            # checked without an item yet (the Yahoo run gives every file one): built from the file itself
            status, document = load_document(os.path.join(out_dir, key + ".json"), key)
            if status != "ok":
                continue
            item = index_item(document, None, today_iso)
        if key in checks:
            item[SEC_CHECKED_KEY] = checks[key]
        elif SEC_CHECKED_KEY in known:
            item[SEC_CHECKED_KEY] = known[SEC_CHECKED_KEY]
        items[key] = item
    index["items"] = dict(sorted(items.items()))
    return "updated", len(touched), write_atomic(os.path.join(out_dir, "index.json"), index)


def _outcome(result, detail=None, counts=None, level=None, document=None, size=0, fresh=False, note=None,
             split=None, dropped=None):
    return {"result": result, "detail": detail, "counts": counts or {}, "level": level,
            "document": document, "bytes": size, "new": fresh, "note": note, "split": split,
            "dropped": dropped or {}}


# Tickers whose SEC ticker map entry now points at a successor registrant that carries no XBRL history
# (XOM -> ExxonMobil Holdings Corp, CIK 2115436, while the history stays under Exxon Mobil Corp). Used only
# when the mapped CIK yields "short-history".
PREDECESSOR_CIKS = {"XOM": "0000034088"}


def _foreign_outcome(path, key, facts, now):
    """
    --foreign: the ifrs-full document of a foreign private issuer (sec_ifrs.build_document); a payload without
    ifrs-full facts at all ("no-ifrs") is read with the US GAAP basis (sec_ifrs.build_us_gaap_document) → outcome.
    The outcome's detail is the reason of the last basis tried.
    """
    document, reason = sec_ifrs.build_foreign_document(facts, key, now)
    if document is None:
        return _outcome("skipped", reason)
    size = write_atomic(path, document)
    counts = {"annual": len(document["annual"]["end"]), "quarterly": len(document["quarterly"]["end"])}
    return _outcome("written", counts=counts, level=document["quality"]["level"], document=document, size=size,
                    fresh=True)


def seed_ticker(key, cik_map, client, out_dir, force, now, splits=None, foreign=False):
    """
    One ticker end to end → outcome dict (see _outcome). TooManyFailures propagates.
    splits: None (no split checks) or a SplitChecker; outcome["split"] is merge_into_document's
    {"chart", "chartError", "adjusted", "events"} for merged documents.
    foreign (--foreign): a ticker that already has a file is "has-file" (never touched); a missing file whose
    us-gaap extraction skips as a foreign filer (FOREIGN_SKIPS) gets the ifrs-full document instead (or the
    US GAAP foreign-filer document when it has no ifrs-full facts, see _foreign_outcome).
    """
    path = os.path.join(out_dir, key + ".json")
    status, stored = load_document(path, key)
    if status == "corrupt":
        return _outcome("corrupt-previous", f"{key}.json left untouched: {stored}")
    previous = stored if status == "ok" else None
    if foreign and previous is not None:
        return _outcome("has-file")
    if previous is not None and not force and has_sec(previous):
        return _outcome("already-sec")
    cik = cik_map.get(key)
    if cik is None:
        return _outcome("no-cik")
    try:
        facts = client.company_facts(cik)
    except FetchError as error:
        return _outcome("fetch-error", str(error))
    if facts is None:
        return _outcome("no-facts", "SEC has no companyfacts for this CIK (404)")
    fetch_splits = (lambda: splits.fetch_chart(key)) if splits is not None else None
    # the stored file (Yahoo periods, revisions, the stored decision) arbitrates the revenue concept
    reference = reference_from_document(previous)
    try:
        sec = extract_periods(facts, reference)
    except Exception as error:  # noqa: BLE001 - one bad payload must not stop the run
        return _outcome("extract-error", f"{type(error).__name__}: {error}")
    old_cik = PREDECESSOR_CIKS.get(key)
    if sec.skip == "short-history" and old_cik and old_cik != cik:
        # the ticker moved to a successor registrant without history: take the predecessor's filings.
        # Request failures are what they are for the mapped CIK: FetchError → fetch-error, and
        # TooManyFailures stops the run (it propagates)
        try:
            older = client.company_facts(old_cik)
        except FetchError as error:
            return _outcome("fetch-error", str(error))
        if older is not None:
            try:
                sec = extract_periods(older, reference)
            except Exception as error:  # noqa: BLE001
                return _outcome("extract-error", f"{type(error).__name__}: {error}")
    try:
        document, info = merge_into_document(previous, sec, key, now, fetch_splits)
    except Exception as error:  # noqa: BLE001 - one bad payload must not stop the run
        return _outcome("extract-error", f"{type(error).__name__}: {error}")
    if document is None:
        if foreign and info["reason"] in sec_ifrs.FOREIGN_SKIPS:
            return _foreign_outcome(path, key, facts, now)
        return _outcome("skipped", info["reason"])
    if info["status"] != "ok":
        return _outcome("corrupt-previous", f"{key}.json left untouched: {info['reason']}")
    # Keep the Yahoo run's cadence: index.json / build_financials.needs_refresh read checkedAt as "last
    # Yahoo check", so an existing file keeps its own value (generatedAt records this change).
    if previous is not None and parse_day(previous.get("checkedAt")):
        document["checkedAt"] = previous["checkedAt"]
    counts = info["counts"]
    note = PENDING if info["shareJump"] else None
    split = {"chart": info["chart"], "chartError": info["chartError"], "adjusted": info["splitAdjusted"],
             "events": info["splitEvents"]}
    dropped = info["dropped"]
    if previous is not None and same_content(previous, document):
        return _outcome("unchanged", counts=counts, level=info["level"], note=note, split=split, dropped=dropped)
    size = write_atomic(path, document)
    return _outcome("written", counts=counts, level=info["level"], document=document, size=size,
                    fresh=previous is None, note=note, split=split, dropped=dropped)


def _eps_detail(state, ratio, reason):
    """The EPS basis of an --upgrade merge, as the log says it."""
    if state == "rescaled":
        text = f"EPS basis: SEC EPS x {ratio['epsFactor']:g} (per ADR)"
    elif state == "unity":
        text = "EPS basis: ADR ratio 1, SEC and Yahoo EPS agree"
    elif state == "replaced":
        text = "EPS basis: no ADR ratio, SEC EPS and share counts dropped, Yahoo's kept"
    else:
        text = "EPS: nothing to compare"
    return text + (f" ({reason})" if reason else "")


def upgrade_document(previous, sec, key, now, fetch_splits=None):
    """
    --upgrade: the stored Yahoo-only file (previous) merged with the SEC document of the same ticker (sec: the
    sec_ifrs.build_foreign_document result) → (document, info). Nothing is written here.

    info: status ("upgraded" or "currency-changed"), epsBasis (resolve_eps_basis state, None: nothing to compare),
    adrRatio, detail (the log text), counts, level, splitAdjusted, shareJump, chart, chartError.

    - Currency differs from the Yahoo file's: the SEC document alone, with quality "currency-changed"; Yahoo's
      amounts are not merged (the detail says so).
    - EPS basis: build_financials.resolve_eps_basis on the SEC periods (stored) against the Yahoo periods (new).
      k != 1 rescales the SEC EPS (x k) and share counts (/ k) and writes adrRatio. Unresolved, or the SEC side has
      no EPS to compare with, leaves Yahoo's EPS and share counts out. The Yahoo file's own adrRatio is not carried:
      until SEC EPS are there it only compares Yahoo with Yahoo, which says nothing about the ADR ratio.
    - Periods: build_financials.merge_section (SEC non-null values first, Yahoo fills nulls and adds newer
      periods). Split events, revisions and the Yahoo-only keys (SEC has none) are carried from the Yahoo file;
      checkedAt stays the Yahoo check date, since build_financials schedules its refresh by it.
    - Quality: the merge's flags (revision kinds, capex-sign, split-adjusted, adr-eps-rescaled), the SEC codes of
      both sides, then build_financials.quality(), which recomputes the codes derived from the periods (short
      history, missing fields, quarter/annual mismatch) from the merged file instead of copying either side's,
      and the coverage gate of sec_ifrs.
    """
    today_iso = now.date().isoformat()
    yahoo = read_sections(previous)
    sec_stored = read_sections(sec)
    checked = previous["checkedAt"] if parse_day(previous.get("checkedAt")) else today_iso
    sec_grade = sec.get("quality") if isinstance(sec.get("quality"), dict) else {}
    previous_grade = previous.get("quality") if isinstance(previous.get("quality"), dict) else {}
    carried_codes = set(previous_grade.get("issues") or []) | set(sec_grade.get("issues") or [])
    counts = {"annual": len(sec["annual"]["end"]), "quarterly": len(sec["quarterly"]["end"])}
    info = {"status": "upgraded", "epsBasis": None, "adrRatio": None, "detail": None, "counts": counts,
            "level": None, "splitAdjusted": False, "shareJump": False, "chart": None, "chartError": None}
    if yahoo["currency"] and sec["currency"] != yahoo["currency"]:
        document = dict(sec)
        grade = dict(sec_grade)
        grade["issues"] = list(sec_grade.get("issues") or [])
        if "currency-changed" not in grade["issues"]:
            grade["issues"].append("currency-changed")
        document["quality"] = grade
        document["checkedAt"] = checked
        info.update(status="currency-changed", level=grade.get("level"),
                    detail=f"SEC reports in {sec['currency']}, the Yahoo file in {yahoo['currency']}: Yahoo amounts "
                           "not merged, SEC document kept")
        return document, info

    stored = {"annual": sec_stored["annual"], "quarterly": sec_stored["quarterly"]}
    new = {"annual": dedupe(yahoo["annual"]), "quarterly": dedupe(yahoo["quarterly"])}
    # None (no SEC EPS at all): every EPS of the result comes from Yahoo, one basis, so they are merged
    eps_state, ratio, reason, new = resolve_eps_basis(stored, new, today_iso)
    info.update(epsBasis=eps_state, adrRatio=ratio, detail=_eps_detail(eps_state, ratio, reason))

    renamed = {"annual": {}, "quarterly": {}}
    annual, revised_a = merge_section(stored["annual"], new["annual"], "annual", today_iso, renamed["annual"])
    quarterly, revised_q = merge_section(
        stored["quarterly"], new["quarterly"], "quarterly", today_iso, renamed["quarterly"]
    )

    events = yahoo["events"]
    adjusted = adjust_splits([annual, quarterly], events)
    if fetch_splits is not None and share_jump([annual, quarterly]):
        try:
            fetched = parse_splits(fetch_splits())
        except Exception as error:  # noqa: BLE001 - leave the merged data as it is
            info["chart"], info["chartError"] = "failed", str(error)
        else:
            info["chart"] = "fetched"
            events = merge_events(events, fetched)
            adjusted = adjust_splits([annual, quarterly], events) or adjusted
    adjusted = repair_split_eps([annual, quarterly], events) > 0 or adjusted
    adjusted = adjusted or yahoo["adjusted"]
    info["shareJump"] = share_jump([annual, quarterly])
    info["splitAdjusted"] = adjusted

    end_month = fy_end_month([p["end"] for p in annual]) or 12
    for item in annual:
        item["fy"] = fiscal_year(item["end"])
    for item in quarterly:
        item["fy"], item["fq"] = fiscal_quarter(item["end"], end_month)

    revisions = merge_revisions(yahoo["revisions"], revised_a + revised_q, annual, quarterly, renamed)
    flags = {item["kind"] for item in revisions}
    if "capex-sign" in carried_codes:
        flags.add("capex-sign")
    if adjusted:
        flags.add("split-adjusted")
    factor = ratio["epsFactor"] if ratio is not None else None
    if factor not in (None, 1):
        flags.add("adr-eps-rescaled")
    if ratio is not None and ratio.get("basis") == "Yahoo":
        flags.add("eps-yahoo-only")
    grade = quality(annual, quarterly, flags, True)
    grade["issues"] += sorted(
        code for code in SEC_FOREIGN_ISSUES if code in carried_codes and code not in grade["issues"])
    sec_ifrs.apply_gate(grade, annual)
    if revisions:
        grade["revisions"] = revisions

    def extras(name):
        merged = dict(sec_stored["extras"][name])
        for key_name, value in yahoo["extras"][name].items():
            merged.setdefault(key_name, value)
        return merged

    document = {
        "version": VERSION,
        "symbol": key,
        "market": "US",
        "currency": sec["currency"],
        "foreignReporter": True,
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "checkedAt": checked,
        "source": sec["source"],
        "fyEndMonth": end_month,
        "annual": write_section(annual, extras("annual"), False),
        "quarterly": write_section(quarterly, extras("quarterly"), True),
        "split": {"adjusted": adjusted, "events": events},
        "quality": grade,
    }
    for name, value in sec_stored["other"].items():
        document.setdefault(name, value)
    for name, value in yahoo["other"].items():
        if name != "adrRatio":
            document.setdefault(name, value)
    if ratio is not None:
        document["adrRatio"] = ratio
    info.update(counts={"annual": len(annual), "quarterly": len(quarterly)}, level=grade["level"])
    return document, info


def seed_upgrade(key, cik_map, client, out_dir, now, splits=None):
    """
    --upgrade, one Yahoo-only foreign ticker → outcome dict (see _outcome). The stored file is merged with its SEC
    document (sec_ifrs.build_foreign_document: IFRS, else US GAAP) and written; a ticker without SEC periods keeps
    its file untouched. TooManyFailures and SecForbidden propagate (see main).
    """
    path = os.path.join(out_dir, key + ".json")
    status, previous = load_document(path, key)
    if status == "corrupt":
        return _outcome("corrupt-previous", f"{key}.json left untouched: {previous}")
    if status == "missing":
        return _outcome("not-yahoo-only", "no stored file")
    if not sec_ifrs.is_yahoo_only(previous):
        return _outcome("not-yahoo-only", "the file already holds SEC periods")
    cik = cik_map.get(key)
    if cik is None:
        return _outcome("no-cik")
    try:
        facts = client.company_facts(cik)
    except FetchError as error:
        return _outcome("fetch-error", str(error))
    if facts is None:
        return _outcome("no-facts", "SEC has no companyfacts for this CIK (404)")
    sec, reason = sec_ifrs.build_foreign_document(facts, key, now)
    if sec is None:
        return _outcome("no-sec-data", reason)
    fetch_splits = (lambda: splits.fetch_chart(key)) if splits is not None else None
    try:
        document, info = upgrade_document(previous, sec, key, now, fetch_splits)
    except Exception as error:  # noqa: BLE001 - one bad payload must not stop the run
        return _outcome("extract-error", f"{type(error).__name__}: {error}")
    size = write_atomic(path, document)
    split = {"chart": info["chart"], "chartError": info["chartError"], "adjusted": info["splitAdjusted"],
             "events": document["split"]["events"]}
    return _outcome(info["status"], info["detail"], counts=info["counts"], level=info["level"], document=document,
                    size=size, note=PENDING if info["shareJump"] else None, split=split)


def parse_args(argv):
    def positive(text):
        try:
            number = int(text)
        except ValueError:
            number = 0
        if number < 1:
            raise argparse.ArgumentTypeError("must be a positive integer")
        return number

    parser = argparse.ArgumentParser(
        prog="seed_financials_sec.py",
        description="Seed docs/report/financials/<KEY>.json with SEC EDGAR companyfacts history (US stocks; "
                    "local manual use only).",
    )
    parser.add_argument("--limit", type=positive, help="process only the first N symbols of the list")
    parser.add_argument("--tickers", help="comma separated tickers (replaces the symbol list), e.g. AAPL,MSFT,BRK.B")
    parser.add_argument("--force", action="store_true", help="re-seed files that already hold SEC periods")
    parser.add_argument("--no-splits", action="store_true",
                        help="never contact Yahoo for split events (tickers whose share counts jump stay "
                             "as reported and are flagged split-check-pending)")
    parser.add_argument("--out", help="output directory (default <repo>/docs/report/financials)")
    parser.add_argument("--report-dir", help="directory of latest.json / themes.json / universe.json "
                                             "(default <repo>/docs/report)")
    parser.add_argument("--data-dir", help="directory of the CSV fallback (default <repo>/data)")
    parser.add_argument("--budget", type=positive, metavar="SECONDS",
                        help="stop before the next ticker once SECONDS have passed (exit 0, PAUSED); "
                             "re-run the same command to continue")
    parser.add_argument("--due", action="store_true",
                        help="quarterly re-check: only the stored US files with SEC periods that are due, oldest "
                             "check first (needs index.json; SEC check dates are kept there)")
    parser.add_argument("--foreign", action="store_true",
                        help="foreign filers (20-F / 40-F): tickers that have no file and that the US backfill judged "
                             "foreign-filer / no-us-gaap / non-usd get an ifrs-full document, or a US GAAP one when "
                             "they have no ifrs-full facts (sec_ifrs.py); "
                             "without --tickers the list comes from fundamentals/us-market-progress.json")
    parser.add_argument("--upgrade", action="store_true",
                        help="with --foreign: the foreign files that hold only Yahoo periods (no SEC source) are "
                             "merged with their SEC document (IFRS, else US GAAP): SEC values first, Yahoo fills the "
                             "gaps and adds newer periods; currency or EPS basis mismatches are handled as "
                             "described in upgrade_document")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --due or --foreign: print the plan from the local files, offline (no network, "
                             "nothing written)")
    parser.add_argument("--state", help="--foreign check state, git-ignored "
                                        "(default <repo>/.cache/sec-ifrs-state.json)")
    args = parser.parse_args(argv)
    if args.due and args.foreign:
        parser.error("--due and --foreign cannot be combined")
    if args.upgrade and not args.foreign:
        parser.error("--upgrade needs --foreign")
    if args.dry_run and not (args.due or args.foreign):
        parser.error("--dry-run is only meaningful with --due or --foreign")
    return args


def choose_symbols(args, report_dir, data_dir):
    """→ (US keys in order, [(text, result) for --tickers entries that are not usable US tickers])."""
    rejected = []
    if args.tickers is not None:
        raw = [part for part in args.tickers.replace(";", ",").replace(" ", ",").split(",") if part.strip()]
        candidates = []
        for text in raw:
            key = financials_key(text)
            if key is None:
                rejected.append((text.strip(), "bad-ticker"))
            elif market_of(key) != "US":
                rejected.append((key, "not-us"))
            else:
                candidates.append(key)
    else:
        candidates = [key for key in collect_symbols(report_dir, data_dir) if market_of(key) == "US"]
    keys, seen = [], set()
    for key in candidates:
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys, rejected


# --due: quarterly re-check of the stored US files that carry SEC periods (see the I/O header above)
DUE_SAVE_EVERY = 100                    # index.json is saved after this many checks, and at the end of the run


def due_state(document, checked):
    """needs_refresh state of a stored file: checkedAt = its SEC check date (None: never checked)."""
    sections = read_sections(document)
    return {
        "checkedAt": checked,
        "latestQuarter": sections["quarterly"][-1]["end"] if sections["quarterly"] else None,
        "latestAnnual": sections["annual"][-1]["end"] if sections["annual"] else None,
    }


def due_reason(state, today):
    """
    Why a stored US file is due for a re-check, or None. The rules are build_financials.needs_refresh's with
    the SEC check date in place of the Yahoo one: never checked; last check older than MAX_CHECK_AGE_DAYS
    (45); or latest reported period older than REPORT_DUE_DAYS (100, a new 10-Q / 10-K may be out) and last
    checked more than RECHECK_DAYS (3) ago, so that a finished run leaves nothing due.
    """
    if not needs_refresh(state, today):
        return None
    checked = parse_day(state.get("checkedAt"))
    if checked is None:
        return "never checked"
    if (today - checked).days > MAX_CHECK_AGE_DAYS:
        return f"last check older than {MAX_CHECK_AGE_DAYS} days"
    return f"latest period older than {REPORT_DUE_DAYS} days"


def select_due(out_dir, items, today, only=None):
    """
    The --due candidates → ([(KEY, reason, SEC check date or None)], oldest check first, Counter of the stored
    files left out). Candidates are US files with SEC periods. Taiwan and other non-US files, Yahoo-only files
    and unreadable ones are left out and counted. Foreign reporters (foreignReporter: the IFRS / US GAAP files
    of sec_ifrs.py, "foreign reporter") are left out too: the US extraction above answers "skipped" for them,
    and the Yahoo daily run keeps them. Nothing is fetched or written. only: set of keys or None.
    """
    due, left = [], Counter()
    names = sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []
    for name in names:
        if not name.endswith(".json") or name == "index.json":
            continue
        key = name[:-len(".json")]
        if only is not None and key not in only:
            continue
        if financials_key(key) != key or market_of(key) != "US":
            left["not US"] += 1
            continue
        status, document = load_document(os.path.join(out_dir, name), key)
        if status != "ok":
            left["unreadable"] += 1
            continue
        if not has_sec(document):
            left["no SEC periods"] += 1
            continue
        if document.get("foreignReporter") is True:
            left["foreign reporter"] += 1
            continue
        known = items.get(key) if isinstance(items.get(key), dict) else {}
        checked = known.get(SEC_CHECKED_KEY) if parse_day(known.get(SEC_CHECKED_KEY)) else None
        reason = due_reason(due_state(document, checked), today)
        if reason is None:
            left["not due"] += 1
        else:
            due.append((key, reason, checked))
    due.sort(key=lambda item: (item[2] or "", item[0]))
    return due, left


def plan_due(args, out_dir, report_dir, data_dir, today):
    """
    The --due plan → (keys in check order, rejected --tickers entries, scan). scan = {"index": state of
    index.json, "due": select_due's list, "left": Counter}. --tickers narrows the stored files considered.
    """
    only, rejected = None, []
    if args.tickers is not None:
        keys, rejected = choose_symbols(args, report_dir, data_dir)
        only = set(keys)
    state, index = read_index(out_dir)
    items = (index or {}).get("items") or {}
    due, left = select_due(out_dir, items, today, only)
    return [key for key, _, _ in due], rejected, {"index": state, "due": due, "left": left}


def print_due_scan(scan, out_dir):
    due, left = scan["due"], scan["left"]
    reasons = Counter(reason for _, reason, _ in due)
    detail = "; ".join(f"{reason}: {count}" for reason, count in sorted(reasons.items()))
    print(f"Stored files in {out_dir}: index.json {scan['index']}; due now {len(due)}"
          + (f" ({detail})" if due else ""), flush=True)
    print(f"  not due {left['not due']}; left out: not US {left['not US']}, foreign reporter "
          f"{left['foreign reporter']}, no SEC periods {left['no SEC periods']}, unreadable {left['unreadable']}",
          flush=True)


def format_duration(seconds):
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def main(argv=None, *, client=None, now=None, environ=None, yahoo_factory=None, sleep=None, clock=None):
    """
    CLI entry → exit code. client / now / environ / clock are injectable for tests, and so are the Yahoo
    session factory (yahoo_factory() → object with get_json(url, params); default
    build_valuation_us.Yahoo) and the sleep used by the split checks (connect retries, REQUEST_DELAY).
    clock: monotonic seconds for --budget (default time.monotonic). A --dry-run reads local files only.
    """
    environ = os.environ if environ is None else environ
    args = parse_args(argv)
    user_agent = None
    if not args.dry_run:
        reason = refusal_reason(environ)
        if reason:
            print(f"Refusing to run: {reason}", flush=True)
            return EXIT_REFUSED
        try:
            user_agent = resolve_user_agent(environ)
        except UserAgentError as error:
            print(f"Refusing to run: {error}", flush=True)
            return EXIT_REFUSED
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    today_iso = (now.astimezone(timezone.utc) if now.tzinfo else now).date().isoformat()
    report_dir = args.report_dir or os.path.join(REPO_ROOT, "docs", "report")
    data_dir = args.data_dir or os.path.join(REPO_ROOT, "data")
    out_dir = args.out or os.path.join(REPO_ROOT, "docs", "report", "financials")
    clock = clock or time.monotonic
    progress, state, state_path = None, None, None
    if args.foreign:
        try:
            progress = sec_ifrs.load_progress(os.path.join(report_dir, "fundamentals", "us-market-progress.json"))
        except ValueError as error:
            print(f"Refusing to run: {error}", flush=True)
            return EXIT_REFUSED
        state_path = args.state or sec_ifrs.default_state_path()
        state = sec_ifrs.load_state(state_path)

    scan = None
    if args.due:
        keys, rejected, scan = plan_due(args, out_dir, report_dir, data_dir, parse_day(today_iso))
        print_due_scan(scan, out_dir)
        if args.dry_run:
            for text, result in rejected:
                print(f"[-] {text} {result}", flush=True)
            if args.limit:
                keys = keys[:args.limit]
            print("Dry run: nothing fetched or written. Due files, oldest check first:", flush=True)
            print(textwrap.fill(" ".join(keys), width=100) if keys else "  (none)", flush=True)
            return EXIT_DONE
        if scan["index"] != "ok":
            print(f"Refusing to run: {os.path.join(out_dir, 'index.json')} is {scan['index']}; the SEC check "
                  "dates are kept there (run the Yahoo refresh once, or restore the file)", flush=True)
            return EXIT_REFUSED
    elif args.foreign and args.upgrade:
        plan_keys, rejected = None, []
        if args.tickers is not None:
            plan_keys, rejected = choose_symbols(args, report_dir, data_dir)
        plan = sec_ifrs.plan_upgrade(read_json(os.path.join(report_dir, "universe.json")), progress, out_dir, state,
                                     keys=plan_keys)
        keys = [key for key, _ in plan["todo"]]
        by_status = ", ".join(f"{name} {count}" for name, count in sorted(plan["byStatus"].items())) or "none"
        print(f"--foreign --upgrade: {len(plan['candidates'])} candidate ticker(s) ({by_status}); "
              f"{len(plan['noFile'])} without a file (run --foreign first); {len(plan['upgraded'])} already hold SEC "
              f"periods; {len(plan['checked'])} answered earlier with no SEC data (state {state_path}); "
              f"{len(plan['unreadable'])} unreadable; {len(keys)} Yahoo-only to merge with SEC", flush=True)
        if plan["unreadable"]:
            print(f"  unreadable: {', '.join(plan['unreadable'])}", flush=True)
        if args.dry_run:
            print(f"Dry run: {len(keys)} ticker(s) would be merged with their SEC document (about {len(keys)} "
                  f"requests, one per ticker); no network, nothing written. Tickers:", flush=True)
            print(textwrap.fill(" ".join(keys), width=100) if keys else "  (none)", flush=True)
            return EXIT_DONE
    elif args.foreign:
        plan_keys, rejected = None, []
        if args.tickers is not None:
            plan_keys, rejected = choose_symbols(args, report_dir, data_dir)
        plan = sec_ifrs.plan_foreign(read_json(os.path.join(report_dir, "universe.json")), progress, out_dir,
                                     state, keys=plan_keys)
        keys = [key for key, _ in plan["todo"]]
        by_status = ", ".join(f"{name} {count}" for name, count in sorted(plan["byStatus"].items())) or "none"
        retry = (f" ({len(plan['retry'])} of them asked again, rules {sec_ifrs.FOREIGN_RULES})"
                 if plan["retry"] else "")
        print(f"--foreign: {len(plan['candidates'])} candidate ticker(s) ({by_status}); "
              f"{len(plan['hasFile'])} already have a file; {len(plan['checked'])} checked before "
              f"(state {state_path}); {len(keys)} to check{retry}", flush=True)
        if args.dry_run:
            print(f"Dry run: {len(keys)} ticker(s) would be checked against SEC (about {len(keys)} requests, "
                  f"one per ticker); no network, nothing written.", flush=True)
            return EXIT_DONE
    else:
        keys, rejected = choose_symbols(args, report_dir, data_dir)
    due_total = len(keys)
    if args.limit:
        keys = keys[:args.limit]
    total = len(keys)
    client = client or SecClient(user_agent)
    # --due / --foreign: the first HTTP 403 stops the run at once (SecForbidden)
    client.stop_on_forbidden = bool(args.due or args.foreign)
    splits = None if args.no_splits else SplitChecker(yahoo_factory, sleep)

    counts = {name: 0 for name in RESULTS}
    reasons = Counter()
    for text, result in rejected:
        counts[result] += 1
        print(f"[-] {text} {result}", flush=True)
    if not keys:
        print("Nothing is due for a re-check." if args.due else
              ("Nothing to upgrade." if args.upgrade else "No US symbols to process."), flush=True)
        return EXIT_DONE
    if args.due:
        print(f"{total} due file(s) -> {out_dir}; oldest check first; index.json and written files are saved "
              f"as the run goes", flush=True)
    else:
        estimate = total * 2 * ESTIMATE_SECONDS_PER_REQUEST
        print(f"{total} tickers -> {out_dir}", flush=True)
        print(f"Reminder: estimated duration about {format_duration(estimate)} "
              f"({total} tickers x 2 requests x ~{ESTIMATE_SECONDS_PER_REQUEST} s); local manual use only; "
              f"stop with Ctrl+C, files already written are kept.", flush=True)

    written, bytes_written, exit_code, processed = {}, 0, EXIT_DONE, 0
    checks, paused, started = {}, None, clock()
    fetched, with_events, adjusted, pending, nulled = [], [], [], [], Counter()
    dirty = False
    try:
        try:
            # --foreign: the CIKs of the progress file's SEC ticker map (no extra request, works offline)
            cik_map = sec_ifrs.progress_cik_map(progress) if args.foreign else client.ticker_map()
        except FetchError as error:
            raise TooManyFailures(f"company_tickers.json unavailable: {error}") from error
        print(f"SEC ticker map: {len(cik_map)} US tickers", flush=True)
        for number, key in enumerate(keys, 1):
            if args.budget is not None and clock() - started >= args.budget:
                paused = f"time budget of {args.budget} s used up"
                break
            if args.upgrade:
                outcome = seed_upgrade(key, cik_map, client, out_dir, now, splits)
            else:
                outcome = seed_ticker(key, cik_map, client, out_dir, args.force or args.due, now, splits,
                                      foreign=args.foreign)
            processed += 1
            result = outcome["result"]
            counts[result] += 1
            recorded = UPGRADE_FINAL if args.upgrade else FOREIGN_FINAL
            if state is not None and result in recorded:
                if args.upgrade:
                    sec_ifrs.record_upgrade(state, key, result, outcome["detail"], today_iso)
                else:
                    sec_ifrs.record_checked(state, key, result, outcome["detail"], today_iso)
                dirty = True
                if processed % 20 == 0:
                    sec_ifrs.save_state(state_path, state)
                    dirty = False
            line = f"[{number}/{total}] {key} {result}"
            if result in ("written", "unchanged") + UPGRADE_RESULTS:
                line += (f" annual={outcome['counts'].get('annual')} quarters={outcome['counts'].get('quarterly')}"
                         f" level={outcome['level']}")
                if result == "written":
                    line += " (new file)" if outcome["new"] else " (merged into existing file)"
                if result in UPGRADE_RESULTS:
                    line += f" ({outcome['detail']})"
                implausible = sum(count for name, count in outcome["dropped"].items() if name != "unitRefiled")
                if implausible:
                    nulled[key] = implausible
                    line += f" [{implausible} implausible value(s) left null]"
                split = outcome["split"] or {}
                if split.get("chart") == "fetched":
                    fetched.append(key)
                    if split.get("events"):
                        with_events.append(key)
                if split.get("adjusted"):
                    adjusted.append(key)
                    line += f" [split-adjusted, {split.get('events')} event(s)]"
                if outcome["note"]:
                    pending.append(key)
                    line += f" [{outcome['note']}]"
                    if split.get("chartError"):
                        line += f" (split events unavailable: {split['chartError']})"
            elif result == "skipped":
                reasons[outcome["detail"]] += 1
                line += f" ({outcome['detail']})"
            elif outcome["detail"]:
                line += f" ({outcome['detail']})"
            print(line, flush=True)
            if outcome["document"] is not None:
                written[key] = outcome["document"]
                bytes_written += outcome["bytes"]
            if args.due:
                # every answer counts as a check (a 404, an unchanged file, a skipped one, a failed fetch):
                # only a 403 stop and a failure of the whole run leave a file unrecorded
                checks[key] = today_iso
                if len(checks) % DUE_SAVE_EVERY == 0:
                    update_index(out_dir, written, today_iso, checks)
    except SecForbidden as error:
        exit_code = EXIT_STOPPED
        print(f"STOPPED: {error}. Stopped at once: no further SEC request in this run, and the file in progress "
              "was not recorded as checked. Files already written and checked are kept; re-run later.", flush=True)
    except TooManyFailures as error:
        exit_code = EXIT_STOPPED
        print(f"STOPPED: {error}. Files already written are kept; re-run later (finished tickers are skipped).",
              flush=True)
    except KeyboardInterrupt:
        exit_code = EXIT_STOPPED
        print("STOPPED: interrupted. Files already written are kept.", flush=True)
    finally:
        index_state, index_count, index_bytes = update_index(out_dir, written, today_iso, checks)
        bytes_written += index_bytes
        if state is not None and dirty:
            sec_ifrs.save_state(state_path, state)

    counts["not-processed"] += total - processed
    if paused:
        print(f"PAUSED: {paused}; {total - processed} not processed yet, re-run the same command to continue.",
              flush=True)
    print("Summary", flush=True)
    print(f"  processed: {processed} of {total} ticker(s)", flush=True)
    if args.due:
        print(f"  due at start: {due_total}", flush=True)
    for name in RESULTS:
        if counts[name]:
            print(f"  {name}: {counts[name]}", flush=True)
    for reason_text, count in sorted(reasons.items()):
        print(f"  skip reason {reason_text}: {count}", flush=True)
    print(f"  index.json: {index_state}" + (f" ({index_count} entries)" if index_count else ""), flush=True)
    if args.due:
        print(f"  remaining due: {due_total - processed}", flush=True)
    print(f"  requests: {getattr(client, 'requests', '?')}, received "
          f"{getattr(client, 'bytes_received', 0)} bytes, written {bytes_written} bytes", flush=True)
    if splits is None:
        print("  split checks: disabled (--no-splits)", flush=True)
    else:
        print(f"  split checks: Yahoo {splits.state()}, {splits.requests} chart request(s)", flush=True)
    print(f"  split events fetched: {len(fetched)} ticker(s), {len(with_events)} with split events"
          + (f" ({', '.join(with_events)})" if with_events else ""), flush=True)
    print(f"  split-adjusted: {len(adjusted)} ticker(s)" + (f" ({', '.join(adjusted)})" if adjusted else ""),
          flush=True)
    print(f"  {PENDING}: {len(pending)} ticker(s)" + (f" ({', '.join(pending)})" if pending else ""), flush=True)
    print(f"  implausible values left null: {sum(nulled.values())} in {len(nulled)} ticker(s)", flush=True)
    return exit_code


__all__ += [
    "DEFAULT_USER_AGENT", "FetchError", "MIN_INTERVAL", "SecClient", "SecForbidden", "SplitChecker",
    "SplitUnavailable", "TooManyFailures", "UserAgentError",
    "due_reason", "main", "parse_ticker_map", "read_index", "resolve_user_agent", "seed_ticker", "select_due",
    "update_index",
]

if __name__ == "__main__":
    sys.exit(main())

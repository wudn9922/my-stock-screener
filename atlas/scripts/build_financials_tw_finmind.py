"""
Taiwan statements from FinMind → docs/report/financials/<code>.TW.json / <code>.TWO.json: the income statement,
balance sheet and cash flow statement of every Taiwan listed / OTC common stock (universe.json), from 2015Q1, as
"finmind" periods (official, see build_financials.py). Yahoo's daily run then fills their null values and adds
newer periods; it never replaces a FinMind value. The growth figures of growth.json read the same files.

  python atlas/scripts/build_financials_tw_finmind.py --budget 1500          fetch and write (resumable)
  python atlas/scripts/build_financials_tw_finmind.py --probe 2330,2881,2317  print every type / origin_name of
                                                  the three datasets and the field each maps to; writes nothing
  python atlas/scripts/build_financials_tw_finmind.py --dry-run             offline: what the next run would fetch
  Options: --limit N (at most N requests), --tickers 2330,2881 (fetch these again, full history),
           --report-dir docs/report, --out-dir (default <report-dir>/financials).
  Env: FINMIND_TOKEN (optional; raises the hourly limit to 600). Standard library + the build_* modules.

Fetching: three requests per stock, one per dataset (TaiwanStockFinancialStatements = pl,
TaiwanStockBalanceSheet = bs, TaiwanStockCashFlowsStatement = cf), start_date 2015-01-01. Pacing, retries and
refusals are finmind_client.py's. A stock is written only when its three datasets were answered (an empty
answer is a real "no data"); each file is written as soon as its stock is done, so an interrupted run loses at
most the stock in progress. Exit codes: 0 (also when paused or stopped with the progress saved), 1 when FinMind
refused the token or the verdicts could not be decided.

Field rules (RULES): the lines are matched by origin_name first (MOPS wording, normalised) and by FinMind's type
code second; a line matching nothing is counted and logged, never guessed. Income statement revenue / EPS /
net income / revenue kind: build_fundamentals_tw_finmind.parse_rows (shared with the growth file). Total
debt = short-term borrowings + long-term borrowings + bonds payable, each with its current maturities, and
short-term notes payable, when one of them is present (leases excluded; flag "debt-approx"); none present →
null (flag "debt-missing").

Single quarter or cumulative (year-to-date): decided once from the data, with build_fundamentals_tw_finmind's
verdicts (ytd / quarter / q4annual over company-years with four positive quarters): the income statement follows
its revenue verdict, the cash flow statement its operating cash flow verdict. Until both are known, stocks are
held in memory (warm-up: WARMUP_STOCKS stocks, at most WARMUP_LIMIT), then written. The verdicts are kept in
the state file. Singles: the cumulative differences of build_fundamentals_tw (EPS on the share basis of the
later report); a missing earlier cumulative figure leaves the single quarter empty (never inferred), and a
quarter after a missing quarter is not treated as a single. Annual: income statement and cash flow are the sum
of four singles; annual EPS uses official MOPS EPS when available, otherwise it is summed only on a stable Q1–Q3
share basis (flag "eps-annual-summed"). The balance sheet is Q4. Q4 singles are the full year less Q1..Q3
(flag "q4-derived").

State: <report-dir>/fundamentals/tw-financials-finmind.json: the verdicts (with evidence), per stock
[checked day, newest quarter, "ok" | "empty"], and the type / origin_name pairs seen per dataset.
Status lines: "FinMind statements DONE" (every listed stock fetched once), "PAUSED" (time budget, --limit, or
FinMind's request limit: run the same command again), "STOPPED" (token refused, or failures in a row).
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone

import build_financials as fin
import build_fundamentals_tw as tw
import build_fundamentals_tw_finmind as fmo
import build_growth as growth
from build_valuation_us import read_json, write_atomic
from finmind_client import (HOURLY_LIMIT, AuthError, FinMindError, Http, LimitReached, RateLimited, fetch_rows,
                            request_interval)

VERSION = 1
STATE_FILE = "tw-financials-finmind.json"
SRC = "finmind"
START_DATE = "2015-01-01"
DATASETS = {"pl": "TaiwanStockFinancialStatements", "bs": "TaiwanStockBalanceSheet",
            "cf": "TaiwanStockCashFlowsStatement"}
DEFAULT_BUDGET_SECONDS = 600
SAVE_EVERY = 25
MAX_CONSECUTIVE_ERRORS = 5
# FinMind's balance sheet repeats every line as "<type>_per": the same origin_name, as % of total assets
PERCENT_SUFFIX = "_per"
# Taiwanese cash flow statements are published year to date (Q2 = first half, …) by regulation; the
# operating cash flow can change sign within a year, so the cumulative test of the income statement
# cannot decide it from the data (it came out "ambiguous" on the first 15 real stocks)
CF_MODE = "ytd"
WARMUP_STOCKS = 15          # the verdicts are decided from this many stocks (held in memory until then)
WARMUP_LIMIT = 200          # still undecided after this many: stop, the verdicts need a look (see the probe)
MAX_TYPES_KEPT = 400        # type / origin_name pairs kept in the state file per dataset
PL_LINES = ("costOfRevenue", "grossProfit", "operatingIncome", "pretaxIncome", "taxProvision", "nonOperating")
CF_LINES = ("operatingCashFlow", "capex", "dividendsPaid")
CF_ABSOLUTE = ("capex", "dividendsPaid")       # outflows: stored as positive numbers
BS_LINES = ("totalAssets", "equity", "cash")
DEBT_LINES = ("debt.short", "debt.long", "debt.longCurrent", "debt.bonds", "debt.bondsCurrent", "debt.cp")


def canon(text):
    """Origin name as matched: normalised (full-width brackets, no spaces) and without the 、 separator."""
    return tw.normalise(text).replace("、", "")


def rule(field, rank, names=(), types=(), must=(), excludes=()):
    """One statement line: canonical field, rank (lower wins), exact origin names, FinMind type codes, and
    fallback words: every group in `must` needs one of its words in the name, no word of `excludes` may occur."""
    return (field, rank, tuple(canon(name) for name in names), tuple(types), tuple(must), tuple(excludes))


RULES = {
    "pl": (
        rule("costOfRevenue", 0, ("營業成本", "營業成本合計"), ("CostOfGoodsSold", "CostOfRevenue", "CostOfSales")),
        rule("costOfRevenue", 1, must=(("營業成本",),), excludes=("以外", "其他", "營業外", "率")),
        rule("grossProfit", 0, ("營業毛利(毛損)", "營業毛利", "毛利(毛損)"), ("GrossProfit",)),
        rule("grossProfit", 1, must=(("毛利",),), excludes=("率", "營業外", "其他", "每股")),
        rule("operatingIncome", 0, ("營業利益(損失)", "營業利益", "營業利益(淨損)"), ("OperatingIncome",)),
        rule("pretaxIncome", 0, ("稅前淨利(淨損)", "稅前淨利(損)", "稅前淨利"), ("PreTaxIncome", "IncomeBeforeTax")),
        rule("pretaxIncome", 1, ("繼續營業單位稅前淨利(淨損)", "繼續營業單位稅前淨利(損)"), ("PreTaxIncomeFromContinuingOperations",)),
        rule("taxProvision", 0, ("所得稅費用(利益)", "所得稅費用", "所得稅費用(利益)合計"), ("TAX", "IncomeTaxExpense")),
        rule("nonOperating", 0, ("營業外收入及支出", "營業外損益"), ("TotalNonoperatingIncomeAndExpense",)),
    ),
    "bs": (
        rule("totalAssets", 0, ("資產總額", "資產總計"), ("TotalAssets",)),
        rule("equity", 0, ("歸屬於母公司業主之權益合計", "歸屬於母公司業主權益合計", "歸屬於母公司業主之權益",
                           "歸屬母公司業主權益合計"), ("EquityAttributableToOwnersOfParent",)),
        rule("equity", 1, ("權益總額", "權益總計", "權益合計"), ("Equity", "TotalEquity")),
        rule("cash", 0, ("現金及約當現金",), ("CashAndCashEquivalents",)),
        rule("debt.short", 0, ("短期借款",), ("ShortTermBorrowings",)),
        rule("debt.long", 0, ("長期借款",), ("LongTermBorrowings",)),
        rule("debt.longCurrent", 0, ("一年或一營業週期內到期長期借款", "一年內到期長期借款", "一年內到期之長期借款",
                                     "一年或一營業週期內到期長期負債", "一年內到期長期負債"),
             ("CurrentPortionOfLongTermBorrowings",)),
        rule("debt.bonds", 0, ("應付公司債",), ("BondsPayable",)),
        rule("debt.bondsCurrent", 0, ("一年或一營業週期內到期應付公司債", "一年內到期應付公司債"),
             ("CurrentPortionOfBondsPayable",)),
        rule("debt.cp", 0, ("應付短期票券",), ("ShortTermNotesAndBillsPayable",)),
    ),
    "cf": (
        rule("operatingCashFlow", 0, ("營業活動之淨現金流入(流出)", "營業活動之淨現金流入(流出)合計", "營業活動之現金流量",
                                      "營業活動之淨現金流入"),
             ("NetCashInflowFromOperatingActivities", "NetCashFlowsFromUsedInOperatingActivities")),
        rule("operatingCashFlow", 1, must=(("營業活動",), ("現金",)), excludes=("調整", "以外", "資產", "負債", "其他")),
        rule("capex", 0, ("取得不動產廠房及設備", "購置不動產廠房及設備"),
             ("AcquisitionOfPropertyPlantAndEquipment", "PurchaseOfPropertyPlantAndEquipment")),
        rule("capex", 1, must=(("取得", "購置"), ("不動產",), ("廠房",)), excludes=("處分", "出售", "折舊", "租賃")),
        rule("dividendsPaid", 0, ("發放現金股利", "發放現金股息", "支付現金股利", "分配現金股利", "發放股利", "現金股利"),
             ("CashDividendsPaid", "DividendsPaid", "PaymentOfCashDividends")),
        rule("dividendsPaid", 1, must=(("發放", "支付", "分配"), ("股利", "股息")),
             excludes=("收取", "收到", "收回", "非控制", "子公司", "特別股")),
    ),
}


def hits_of(dataset, type_name, origin):
    """[(field, rank)] of one row by the RULES of its dataset (exact names and types, then the fallbacks)."""
    name = canon(origin)
    found = []
    for field, rank, names, types, must, excludes in RULES[dataset]:
        if (name and name in names) or (type_name and type_name in types):
            found.append((field, rank))
        elif must and name and all(any(word in name for word in group) for group in must) \
                and not any(word in name for word in excludes):
            found.append((field, rank))
    return found


def core_of(dataset, type_name, origin):
    """Revenue / EPS / net income / revenue kind lines: the income statement's parse_rows roles, as fields."""
    if dataset != "pl":
        return []
    names = {"rev": "revenue", "eps": "epsBasic", "ni": "netIncome", "interest": "revenue", "noninterest": "revenue"}
    return [names[role] for role, _, _ in fmo.roles_of(type_name, origin)]


class StoreError(ValueError):
    """The state file does not have the expected layout."""


# ---------------------------------------------------------------------------------------------
# One stock's rows → published values per season
# ---------------------------------------------------------------------------------------------
def parse_dataset(dataset, rows, code):
    """
    FinMind rows of one stock and statement → {"fields": {season: {field: value}}, "types": Counter,
    "names": {type: origin_name}, "unmatched": Counter((type, origin_name)), "skipped": Counter, "conflicts": n}.
    """
    seasons, types, names, unmatched, skipped, conflicts = {}, Counter(), {}, Counter(), Counter(), 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            skipped["not an object"] += 1
            continue
        stock = str(row.get("stock_id") or "").strip().upper()
        if stock and stock != code:
            skipped["other stock_id"] += 1
            continue
        type_name, origin = str(row.get("type") or ""), str(row.get("origin_name") or "")
        if type_name.endswith(PERCENT_SUFFIX):
            skipped["percent of total column"] += 1     # common-size % (e.g. CashAndCashEquivalents_per)
            continue
        types[type_name] += 1
        names.setdefault(type_name, origin)
        hits = hits_of(dataset, type_name, origin)
        if not hits and not core_of(dataset, type_name, origin):
            unmatched[(type_name, origin)] += 1
        key = fmo.season_of(row.get("date"))
        if key is None:
            skipped["date not a quarter end"] += 1
            continue
        value = tw.number(row.get("value"))
        if value is None:
            skipped["no value"] += 1
            continue
        slot = seasons.setdefault(key, {})
        for field, rank in hits:
            known = slot.get(field)
            if known is None or rank < known[0]:
                slot[field] = (rank, value)
            elif rank == known[0] and value != known[1]:
                conflicts += 1                          # the first one is kept
    fields = {}
    for key, slot in seasons.items():
        values = {field: value for field, (_, value) in slot.items()}
        if dataset == "cf":
            for field in CF_ABSOLUTE:
                if field in values:
                    values[field] = abs(values[field])
        if values:
            fields[key] = values
    return {"fields": fields, "types": types, "names": names, "unmatched": unmatched, "skipped": skipped,
            "conflicts": conflicts}


def parse_stock(code, rows_by_dataset):
    """Three datasets of one stock → {"pl": {season: {...}}, "bs": {...}, "cf": {...}, "parsed": {dataset: parse}}.
    The income statement keeps build_fundamentals_tw_finmind's revenue / EPS / net income / kind per season."""
    parsed = {name: parse_dataset(name, rows_by_dataset.get(name) or [], code) for name in DATASETS}
    core = fmo.parse_rows(rows_by_dataset.get("pl") or [], code)["entries"]
    pl = {}
    for key, (rev, eps, ni, kind) in core.items():
        pl[key] = {"rev": rev, "eps": eps, "ni": ni, "kind": kind}
    for key, values in parsed["pl"]["fields"].items():
        if key in pl:
            pl[key].update(values)
    return {"pl": pl, "bs": parsed["bs"]["fields"], "cf": parsed["cf"]["fields"], "parsed": parsed}


# ---------------------------------------------------------------------------------------------
# Verdicts (single quarter or cumulative), decided once from the held stocks
# ---------------------------------------------------------------------------------------------
def cf_quads(cf_data):
    """Company-years with four positive cumulative operating cash flows → [(code, year, quad)]."""
    quads = []
    for code, row in cf_data.items():
        years = {}
        for key, values in row.items():
            year, quarter = tw.parse_season(key)
            years.setdefault(year, {})[quarter] = values.get("operatingCashFlow")
        for year, quarters in years.items():
            if len(quarters) < 4:
                continue
            quad = tuple(quarters.get(q) for q in (1, 2, 3, 4))
            if any(value is None or value <= 0 for value in quad):
                continue
            quads.append((code, year, quad))
    return quads


def decide(held, modes, official, log):
    """Fills the unknown verdicts of `modes` ({"pl", "cf"}) from the held stocks → (modes, evidence)."""
    evidence = dict((modes.get("evidence") or {}))
    pl_data = {code: {key: [row.get("rev"), row.get("eps"), row.get("ni"), row.get("kind")]
                      for key, row in parsed["pl"].items()} for code, _, parsed in held}
    if modes.get("pl") is None:
        found_modes, report = fmo.detect_modes(pl_data)
        for line in fmo.describe_modes(report):
            log(line.replace("FinMind ", "FinMind income statement ", 1))
        verdict = found_modes["rev"]
        if verdict and official is not None:
            check = fmo.cross_check(pl_data, {"rev": verdict, "eps": verdict, "ni": verdict}, official)
            log(f"Cross-check with the official tw.json (year-to-date revenue): {json.dumps(check, ensure_ascii=False)}")
            if check["count"] >= fmo.CROSS_CHECK_MIN and not check["ok"]:
                log("::warning::income statement verdict not used: FinMind's year-to-date revenue differs from tw.json")
                verdict = None
        modes["pl"] = verdict if verdict in fmo.MODES else None
        evidence["pl"] = {key: report["rev"][key] for key in ("n", "ytdQ1Q3", "q4AboveQ3", "q4AboveQ1toQ3")}
    if modes.get("cf") is None:
        cf_data = {code: {key: {"operatingCashFlow": row.get("operatingCashFlow")} for key, row in parsed["cf"].items()}
                   for code, _, parsed in held}
        found = fmo.evidence(cf_quads(cf_data))
        verdict = fmo.verdict_of(found)
        log(f"FinMind cash flow: {CF_MODE} (published year to date); the data test says {verdict} over {found['n']} "
            f"company-years with four positive quarters: Q1<Q2<Q3 & Q3>=2xQ1 in {found['ytdQ1Q3']}, "
            f"Q4>Q3 in {found['q4AboveQ3']}, Q4>=Q1+Q2+Q3 in {found['q4AboveQ1toQ3']}")
        modes["cf"] = CF_MODE
        evidence["cf"] = {key: found[key] for key in ("n", "ytdQ1Q3", "q4AboveQ3", "q4AboveQ1toQ3")}
    return modes, evidence


# ---------------------------------------------------------------------------------------------
# Singles, annual values and periods of one stock
# ---------------------------------------------------------------------------------------------
def views(raw, mode, kinds=None, quarters_of_year=None):
    """{quarter: published value} of one year → ({quarter: single}, {quarter: cumulative}); None when unknown.
    kinds (revenue only): a difference is taken only between quarters of the same revenue kind. For quarter and
    q4annual modes, a raw value is a single only if Q1 or its same-year predecessor is present."""
    raw = {q: value for q, value in raw.items() if value is not None}
    single, cumulative = {}, {}
    present = quarters_of_year if quarters_of_year is not None else raw

    def same(quarters):
        return kinds is None or len({kinds.get(q) for q in quarters}) == 1

    if mode == "ytd":
        cumulative = dict(raw)
        for q in raw:
            if q == 1:
                single[1] = raw[1]
            elif q - 1 in raw and same((q - 1, q)):
                single[q] = raw[q] - raw[q - 1]
    elif mode == "quarter":
        single = {q: raw[q] for q in raw if fmo.can_be_single_quarter(q, present)}
        for q in (1, 2, 3, 4):
            if all(p in single for p in range(1, q + 1)) and same(range(1, q + 1)):
                cumulative[q] = sum(single[p] for p in range(1, q + 1))
    elif mode == "q4annual":
        for q in (1, 2, 3):
            if q in raw and fmo.can_be_single_quarter(q, present):
                single[q] = raw[q]
        for q in (1, 2, 3):
            if all(p in single for p in range(1, q + 1)) and same(range(1, q + 1)):
                cumulative[q] = sum(single[p] for p in range(1, q + 1))
        if 4 in raw:
            cumulative[4] = raw[4]
            if all(p in single for p in (1, 2, 3)) and same((1, 2, 3, 4)):
                single[4] = raw[4] - sum(single[p] for p in (1, 2, 3))
    else:
        return {}, {}
    if kinds is not None:
        single = {q: value for q, value in single.items() if value >= 0}
    return single, cumulative


def eps_singles(eps_raw, ni_raw, mode, quarters_of_year=None):
    """Basic EPS singles as build_fundamentals_tw_finmind.company_periods (the share basis of the later report)."""
    eps_q, eps_y = views(eps_raw, mode, quarters_of_year=quarters_of_year)
    _, ni_y = views(ni_raw, mode, quarters_of_year=quarters_of_year)
    result = {}
    for q in (1, 2, 3, 4):
        eps = eps_q.get(q)
        derived = mode == "ytd" or (mode == "q4annual" and q == 4)
        if derived and q > 1:
            now_eps, now_ni, before_ni = eps_y.get(q), ni_y.get(q), ni_y.get(q - 1)
            if now_eps is not None and now_ni and tw.implied_shares([None, now_eps, now_ni]) is not None \
                    and before_ni is not None:
                eps = (now_ni - before_ni) * now_eps / now_ni
        if eps is not None:
            result[q] = round(eps, 4)
    return result


def by_year(seasons):
    """{season: values} → {year: {quarter: values}}."""
    years = {}
    for key, values in seasons.items():
        parsed = tw.parse_season(key)
        if parsed:
            years.setdefault(parsed[0], {})[parsed[1]] = values
    return years


def derive(stock, modes, official_annual=None):
    """One stock's published values → (annual {year: {field: value}}, quarterly {(year, q): {field: value}}, flags)."""
    official_annual = official_annual or {}
    pl_years, bs_years, cf_years = by_year(stock["pl"]), by_year(stock["bs"]), by_year(stock["cf"])
    annual, quarterly, flags = {}, {}, set()
    for year in sorted(set(pl_years) | set(bs_years) | set(cf_years)):
        pl, bs, cf = pl_years.get(year, {}), bs_years.get(year, {}), cf_years.get(year, {})
        singles = {}
        if modes["pl"] and pl:
            kinds = {q: row.get("kind") for q, row in pl.items()}
            singles["revenue"], _ = views({q: row.get("rev") for q, row in pl.items()}, modes["pl"], kinds, pl)
            singles["netIncome"], _ = views({q: row.get("ni") for q, row in pl.items()}, modes["pl"],
                                             quarters_of_year=pl)
            eps = eps_singles({q: row.get("eps") for q, row in pl.items()}, {q: row.get("ni") for q, row in pl.items()},
                              modes["pl"], pl)
            singles["epsBasic"] = eps
            for line in PL_LINES:
                singles[line], _ = views({q: row.get(line) for q, row in pl.items()}, modes["pl"],
                                         quarters_of_year=pl)
        if modes["cf"] and cf:
            for line in CF_LINES:
                singles[line], _ = views({q: row.get(line) for q, row in cf.items()}, modes["cf"],
                                         quarters_of_year=cf)
        for field, per_quarter in singles.items():
            for q, value in per_quarter.items():
                quarterly.setdefault((year, q), {})[field] = value
        if singles.get("revenue", {}).get(4) is not None or singles.get("netIncome", {}).get(4) is not None:
            flags.add("q4-derived")
        for q, row in bs.items():
            values = {field: row[field] for field in BS_LINES if row.get(field) is not None}
            debt = [row[name] for name in DEBT_LINES if row.get(name) is not None]
            if debt:
                values["totalDebt"] = sum(debt)
                flags.add("debt-approx")
            elif any(field in values for field in BS_LINES):
                flags.add("debt-missing")
            if values:
                quarterly.setdefault((year, q), {}).update(values)
        totals = {}
        for field, per_quarter in singles.items():
            if field == "epsBasic":
                continue
            if all(per_quarter.get(q) is not None for q in (1, 2, 3, 4)):
                totals[field] = sum(per_quarter[q] for q in (1, 2, 3, 4))
        official_eps = official_annual.get(year)
        if pl and official_eps is not None:
            totals["epsBasic"] = round(official_eps, 4)
        elif (all(singles.get("epsBasic", {}).get(q) is not None for q in (1, 2, 3, 4))
              and fmo.stable_share_count(singles.get("epsBasic", {}), singles.get("netIncome", {}))):
            totals["epsBasic"] = round(sum(singles["epsBasic"][q] for q in (1, 2, 3, 4)), 4)
            flags.add("eps-annual-summed")
        q4 = quarterly.get((year, 4), {})
        for field in BS_LINES + ("totalDebt",):
            if q4.get(field) is not None:
                totals[field] = q4[field]
        if totals:
            annual[year] = totals
    return annual, quarterly, flags


def periods_of(annual, quarterly):
    """Derived values → (annual, quarterly) period dicts of build_financials (src finmind), ascending."""
    def values_of(values):
        return {field: fin.clean_value(value) for field, value in values.items() if value is not None}
    annual_periods = [fin.period(date(year, 12, 31).isoformat(), values_of(values), SRC)
                      for year, values in sorted(annual.items()) if values_of(values)]
    quarterly_periods = [fin.period(tw.quarter_end(year, q).isoformat(), values_of(values), SRC)
                         for (year, q), values in sorted(quarterly.items()) if values_of(values)]
    return annual_periods, quarterly_periods


def write_stock(out_dir, symbol, annual, quarterly, flags, now, today):
    """Merges the FinMind periods into the stock's file (Yahoo periods fill nulls and newer quarters) and writes it
    when the content changed → 'written' | 'unchanged' | 'corrupt' (a file that cannot be read is never touched)."""
    today_iso = today.isoformat()
    path = os.path.join(out_dir, symbol + ".json")
    status, stored = fin.load_document(path, symbol)
    if status == "corrupt":
        return "corrupt"
    previous = stored if status == "ok" else None
    sections = fin.read_sections(previous) if previous is not None else None
    others_a = [p for p in sections["annual"] if p["src"] != SRC] if sections else []
    others_q = [p for p in sections["quarterly"] if p["src"] != SRC] if sections else []
    # Stored FinMind periods the new answer does not contain stay (a period is never dropped for lack of an answer)
    new_a_ends, new_q_ends = {p["end"] for p in annual}, {p["end"] for p in quarterly}
    kept_a = [p for p in sections["annual"] if p["src"] == SRC and p["end"] not in new_a_ends] if sections else []
    kept_q = [p for p in sections["quarterly"] if p["src"] == SRC and p["end"] not in new_q_ends] if sections else []
    # Null values of a new period keep what the file already holds for that period (Yahoo values filled in an
    # earlier run stay, so the rewrite is the same content and nothing flips back and forth)
    for items, stored_periods in ((annual, sections["annual"] if sections else []),
                                  (quarterly, sections["quarterly"] if sections else [])):
        stored_by_end = {p["end"]: p for p in stored_periods if p["src"] == SRC}
        for item in items:
            stored = stored_by_end.get(item["end"])
            if stored is not None:
                for field, value in stored["values"].items():
                    if item["values"].get(field) is None and value is not None:
                        item["values"][field] = value
    base_a = sorted(annual + kept_a, key=lambda item: item["end"])
    base_q = sorted(quarterly + kept_q, key=lambda item: item["end"])
    renamed = {"annual": {}, "quarterly": {}}
    filled = []
    annual_m, revised_a = fin.merge_section(base_a, others_a, "annual", today_iso, renamed["annual"], filled)
    quarterly_m, revised_q = fin.merge_section(base_q, others_q, "quarterly", today_iso, renamed["quarterly"], filled)
    for item in annual_m:
        item["fy"] = fin.fiscal_year(item["end"])
    for item in quarterly_m:
        item["fy"], item["fq"] = fin.fiscal_quarter(item["end"], 12)
    revisions = fin.merge_revisions(sections["revisions"] if sections else [], revised_a + revised_q,
                                    annual_m, quarterly_m, renamed)
    flags = set(flags)
    flags.update(revision["kind"] for revision in revisions)
    if sections and sections["adjusted"]:
        flags.add("split-adjusted")
    label = fin.source_label(annual_m + quarterly_m, filled)
    if previous is not None and previous.get("source") == fin.SOURCE_FINMIND_MIXED and label == fin.SOURCE_FINMIND:
        label = fin.SOURCE_FINMIND_MIXED                 # Yahoo values filled in an earlier run are still in the file
    grade = fin.quality(annual_m, quarterly_m, flags, foreign=False)
    grade["issues"] += [code for code in fin.FINMIND_ISSUES if code in flags and code not in grade["issues"]]
    if revisions:
        grade["revisions"] = revisions
    document = {
        "version": fin.VERSION, "symbol": symbol, "market": "TW", "currency": "TWD", "foreignReporter": False,
        "generatedAt": now.isoformat().replace("+00:00", "Z"), "checkedAt": today_iso,
        "source": label, "fyEndMonth": 12,
        "annual": fin.write_section(annual_m, sections["extras"]["annual"] if sections else {}, False),
        "quarterly": fin.write_section(quarterly_m, sections["extras"]["quarterly"] if sections else {}, True),
        "split": {"adjusted": bool(sections and sections["adjusted"]), "events": sections["events"] if sections else []},
        "quality": grade,
    }
    if sections:
        for name, value in sections["other"].items():
            document.setdefault(name, value)
    if previous is not None and fin.same_content(previous, document):
        return "unchanged"                       # checkedAt keeps the last day the content changed
    write_atomic(path, document)
    return "written"


# ---------------------------------------------------------------------------------------------
# State and plan
# ---------------------------------------------------------------------------------------------
def empty_state():
    return {"modes": {"pl": None, "cf": None, "evidence": {}}, "fetch": {}, "types": {}}


def load_state(document):
    if document is None:
        return empty_state()
    if not isinstance(document, dict) or document.get("version") != VERSION:
        raise StoreError("not a version-1 tw-financials-finmind.json")
    modes = document.get("modes") if isinstance(document.get("modes"), dict) else {}
    fetch = document.get("fetch") if isinstance(document.get("fetch"), dict) else {}
    types = document.get("types") if isinstance(document.get("types"), dict) else {}
    checked = {}
    for code, info in fetch.items():
        if isinstance(info, list) and len(info) == 3 and growth.parse_day(info[0]) and info[2] in ("ok", "empty"):
            checked[code] = [info[0], info[1] if tw.parse_season(info[1]) else None, info[2]]
    return {
        "modes": {"pl": modes.get("pl") if modes.get("pl") in fmo.MODES else None,
                  "cf": modes.get("cf") if modes.get("cf") in fmo.MODES else None,
                  "evidence": modes.get("evidence") if isinstance(modes.get("evidence"), dict) else {}},
        "fetch": checked,
        "types": {str(dataset): dict(pairs) for dataset, pairs in types.items() if isinstance(pairs, dict)},
    }


def dump_state(state, now):
    return {
        "version": VERSION, "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "source": "FinMind TaiwanStockFinancialStatements / TaiwanStockBalanceSheet / TaiwanStockCashFlowsStatement "
                  "（資料源自公開資訊觀測站）",
        "start": START_DATE, "modes": state["modes"],
        "fetch": {code: state["fetch"][code] for code in sorted(state["fetch"])},
        "types": {dataset: state["types"][dataset] for dataset in sorted(state["types"])},
    }


def plan(state, codes, today, forced=()):
    """[(code, reason)] for this run, most needed first: never fetched, behind the due quarter, filing window, empty."""
    if forced:
        # --tickers: exactly those stocks (full history again), nothing else
        return [(code, "forced" if code in state["fetch"] else "new") for code in codes if code in forced]
    target, window = fmo.due_season(today), fmo.open_season(today)
    missing, behind, waiting, empty = [], [], [], []
    for code in codes:
        info = state["fetch"].get(code)
        if code in forced or info is None:
            missing.append((code, "forced" if info is not None else "new"))
            continue
        checked_day, last, status = info
        checked = growth.parse_day(checked_day) or date(1970, 1, 1)
        age = (today - checked).days
        if status != "ok" or last is None:
            if age >= fmo.SLOW_RECHECK_DAYS:
                empty.append((checked_day, code, "empty"))
            continue
        gap = fmo.season_number(target) - fmo.season_number(last)
        if gap > 0:
            if checked < fmo.due_date(target) or age >= (fmo.RECHECK_DAYS if gap == 1 else fmo.SLOW_RECHECK_DAYS):
                behind.append((checked_day, code, f"behind {target}"))
        elif fmo.season_number(window) > fmo.season_number(last) and age >= fmo.RECHECK_DAYS:
            waiting.append((checked_day, code, f"filing window {window}"))
    ordered = [(code, reason) for group in (behind, waiting, empty) for _, code, reason in sorted(group)]
    return missing + ordered


# --only-after-backfill: the daily run starts once this share of the listed stocks has been fetched
BACKFILL_SHARE = 0.95


def tw_listing(universe):
    """{code: symbol} of the Taiwan rows of universe.json (.TW / .TWO)."""
    result = {}
    for symbol, _ in fin.universe_rows(universe, "TW"):
        if isinstance(symbol, str) and symbol.upper().endswith(fin.TW_SUFFIXES):
            symbol = symbol.upper()
            if not fin.is_tw_etf(symbol):
                result.setdefault(symbol.rsplit(".", 1)[0], symbol)
    return result


def status_line(state, listing):
    fetched = sum(1 for code in listing if code in state["fetch"])
    remaining = len(listing) - fetched
    return (f"FinMind statements {'DONE' if not remaining else 'NOT DONE'}: {fetched}/{len(listing)} stocks fetched"
            + (f", {remaining} left: run the same command again" if remaining else ""))


# ---------------------------------------------------------------------------------------------
# Probe / dry run
# ---------------------------------------------------------------------------------------------
def probe(codes, http, token, sleep, clock, log):
    """Every type / origin_name of the three datasets for these stocks, with the field each maps to. Writes nothing."""
    counter = {"requests": 0, "limit": None}
    interval = request_interval(token)
    rows_of = {name: {} for name in DATASETS}
    for code in codes:
        for name, dataset in DATASETS.items():
            if counter["requests"]:
                sleep(interval)
            rows_of[name][code] = fetch_rows(http, dataset, code, START_DATE, token, sleep, log, counter)
    for name, dataset in DATASETS.items():
        pairs, rows_total = Counter(), 0
        for rows in rows_of[name].values():
            rows_total += len(rows)
            for row in rows:
                if isinstance(row, dict):
                    pairs[(str(row.get("type") or ""), str(row.get("origin_name") or ""))] += 1
        log(f"[{name}] {dataset}: {rows_total} rows for {', '.join(codes)}, {len(pairs)} type / origin_name pairs")
        log("  type | origin_name | rows | maps to (field: rule rank)")
        for (type_name, origin), count in sorted(pairs.items(), key=lambda item: (-item[1], item[0])):
            mapped = [f"{field}({rank})" for field, rank in hits_of(name, type_name, origin)]
            mapped += [f"{field}(core)" for field in core_of(name, type_name, origin)]
            log(f"  {type_name} | {origin} | {count} | {', '.join(mapped) or '— (not used)'}")
    first = codes[0]
    stock = parse_stock(first, {name: rows_of[name][first] for name in DATASETS})
    for key in sorted(stock["pl"], key=tw.parse_season)[-2:]:
        log(f"sample {first} {key} income statement: " + json.dumps(stock["pl"][key], ensure_ascii=False))
    for key in sorted(stock["bs"], key=tw.parse_season)[-1:]:
        log(f"sample {first} {key} balance sheet: " + json.dumps(stock["bs"][key], ensure_ascii=False))
    for key in sorted(stock["cf"], key=tw.parse_season)[-1:]:
        log(f"sample {first} {key} cash flow (cumulative as published): " + json.dumps(stock["cf"][key], ensure_ascii=False))
    log(f"probe requests: {counter['requests']} (3 per stock); nothing written")
    return 0


def dry_run(state, listing, codes, today, token, log, forced=()):
    chosen = plan(state, codes, today, forced)
    requests = 3 * len(chosen)
    rate = HOURLY_LIMIT[bool(token)]
    log(f"dry run (offline): {len(listing)} listed stocks, {len(chosen)} to fetch now "
        f"({Counter(reason.split(' ')[0] for _, reason in chosen)}), {requests} requests; "
        f"about {requests / rate:.1f} h at {rate} requests per hour ({'with' if token else 'without'} token)")
    full = 3 * len(listing)
    log(f"full backfill: {len(listing)} stocks x 3 = {full} requests, about {full / HOURLY_LIMIT[True]:.1f} h at "
        f"{HOURLY_LIMIT[True]} per hour with a token, {full / HOURLY_LIMIT[False]:.1f} h at {HOURLY_LIMIT[False]} per hour without")
    log(f"verdicts: income statement {state['modes']['pl']}, cash flow {state['modes']['cf']} "
        f"({'known' if state['modes']['pl'] and state['modes']['cf'] else 'undecided: the first run decides them'})")
    log("first stocks: " + ", ".join(code for code, _ in chosen[:12]))
    log(status_line(state, listing))
    return 0


# ---------------------------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------------------------
class BudgetUsedUp(RuntimeError):
    """The --budget seconds are used up (checked before every request)."""


def run(args, http=None, now=None, sleep=time.sleep, clock=time.monotonic, log=print, token=None):
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    today = now.date()
    report_dir = args.report_dir
    out_dir = args.out_dir or os.path.join(report_dir, "financials")
    state_path = os.path.join(report_dir, "fundamentals", STATE_FILE)
    listing = tw_listing(read_json(os.path.join(report_dir, "universe.json")))
    if not listing:
        log("::error::no Taiwan stocks: universe.json missing or empty")
        return 1
    previous = read_json(state_path)
    if previous is None and os.path.exists(state_path):
        log(f"::error::{state_path} exists but cannot be read; not touched (fix or delete it)")
        return 1
    try:
        state = load_state(previous)
    except StoreError as error:
        log(f"::error::{state_path}: {error}; not touched (fix or delete it)")
        return 1
    token = token if token is not None else (os.environ.get("FINMIND_TOKEN") or "").strip() or None
    if args.dry_run:
        forced = set(args.tickers or ())
        unknown = sorted(forced - set(listing))
        if unknown:
            log(f"::warning::not in universe.json, skipped: {unknown}")
        return dry_run(state, listing, sorted(listing), today, token, log, forced & set(listing))
    http = http or Http()
    if args.probe:
        codes = [code for code in args.probe if code]
        try:
            return probe(codes, http, token, sleep, clock, log)
        except (AuthError, RateLimited, LimitReached, FinMindError) as error:
            log(f"::error::probe stopped: {error}")
            return 1

    if getattr(args, "only_after_backfill", False):
        fetched = sum(1 for code in listing if code in state["fetch"])
        if fetched < BACKFILL_SHARE * len(listing):
            log(f"::notice::FinMind statements: backfill not finished ({fetched}/{len(listing)} stocks fetched); "
                "the daily run waits for it (run the script by hand without --only-after-backfill)")
            return 0
    codes = sorted(listing)
    forced = set(args.tickers or ())
    unknown = [code for code in sorted(forced) if code not in listing]
    if unknown:
        log(f"::warning::not in universe.json, skipped: {unknown}")
    chosen = plan(state, codes, today, forced & set(listing))
    interval = request_interval(token)
    log(f"FinMind statements plan: {len(chosen)} stocks ({Counter(reason.split(' ')[0] for _, reason in chosen)}), "
        f"3 requests each, {'with' if token else 'without'} token, one request every {interval:.1f}s, "
        f"budget {args.budget:.0f}s; verdicts: income statement {state['modes']['pl']}, cash flow {state['modes']['cf']}")
    official = read_json(os.path.join(report_dir, "fundamentals", growth.TW_FILE))
    official_annual = fmo.official_annual_eps(official)
    counter = {"requests": 0, "limit": args.limit}
    deadline = clock() + args.budget
    next_at = clock()
    outcomes, held, unmatched = Counter(), [], Counter()
    types_seen = {name: Counter() for name in DATASETS}
    done = errors = consecutive = 0
    stopped, paused, refused = None, False, False
    saved_body = json.dumps({key: value for key, value in dump_state(state, now).items() if key != "generatedAt"},
                            ensure_ascii=False, sort_keys=True)

    def save():
        nonlocal saved_body
        document = dump_state(state, now)
        body = json.dumps({key: value for key, value in document.items() if key != "generatedAt"},
                          ensure_ascii=False, sort_keys=True)
        if body != saved_body:
            write_atomic(state_path, document)
            saved_body = body

    def turn():
        nonlocal next_at
        wait = max(0.0, next_at - clock())
        if clock() + wait > deadline:
            raise BudgetUsedUp()
        if wait:
            sleep(wait)

    def finish(code, symbol, parsed, reason):
        """Writes one stock (verdicts known) and records it in the state."""
        annual, quarterly, flags = derive(parsed, state["modes"], official_annual.get(code))
        annual_p, quarterly_p = periods_of(annual, quarterly)
        if not annual_p and not quarterly_p:
            state["fetch"][code] = [today.isoformat(), None, "empty"]
            outcomes["empty"] += 1
            return
        outcome = write_stock(out_dir, symbol, annual_p, quarterly_p, flags, now, today)
        if outcome == "corrupt":
            log(f"::warning::{symbol}.json cannot be merged into (unreadable); left untouched")
            outcomes["corrupt"] += 1
            return
        newest = max((key for key in quarterly), default=None)
        state["fetch"][code] = [today.isoformat(), tw.season_key(*newest) if newest else None, "ok"]
        outcomes[outcome] += 1

    try:
        for position, (code, reason) in enumerate(chosen):
            symbol = listing[code]
            rows = {}
            try:
                for name, dataset in DATASETS.items():
                    before = counter["requests"]
                    turn()
                    rows[name] = fetch_rows(http, dataset, code, START_DATE, token, sleep, log, counter)
                    next_at = clock() + interval * max(1, counter["requests"] - before)
            except BudgetUsedUp:
                stopped = f"time budget ({len(chosen) - position} stocks left for the next run)"
                paused = True
                break
            except LimitReached:
                stopped = f"--limit {args.limit} requests"
                paused = True
                break
            except RateLimited as error:
                stopped, paused = f"{error}", True
                break
            except AuthError as error:
                stopped, refused = f"{error}", True
                errors += 1
                break
            except FinMindError as error:
                errors += 1
                consecutive += 1
                log(f"::warning::FinMind statements {code}: {error}")
                if consecutive >= MAX_CONSECUTIVE_ERRORS:
                    stopped = f"{consecutive} failures in a row"
                    break
                continue
            consecutive = 0
            parsed = parse_stock(code, rows)
            for name in DATASETS:
                types_seen[name].update(parsed["parsed"][name]["types"])
                unmatched.update({f"{name} {type_name} | {origin}": n for (type_name, origin), n in
                                  parsed["parsed"][name]["unmatched"].items()})
                for type_name, origin in parsed["parsed"][name]["names"].items():
                    state["types"].setdefault(name, {})
                    if len(state["types"][name]) < MAX_TYPES_KEPT:
                        state["types"][name].setdefault(type_name, origin)
            done += 1
            if not (state["modes"]["pl"] and state["modes"]["cf"]):
                held.append((code, symbol, parsed))
                count = len(held)
                if count >= WARMUP_STOCKS and (count == WARMUP_STOCKS or count % 10 == 0 or count >= WARMUP_LIMIT):
                    modes, evidence = decide(held, dict(state["modes"]), official, log)
                    state["modes"] = {"pl": modes["pl"], "cf": modes["cf"], "evidence": evidence}
                    if state["modes"]["pl"] and state["modes"]["cf"]:
                        for item in held:
                            finish(item[0], item[1], item[2], reason)
                        held = []
                    elif len(held) >= WARMUP_LIMIT:
                        log("::error::FinMind verdicts (single quarter or cumulative) not decided from "
                            f"{len(held)} stocks: run build_financials_tw_finmind.py --probe and look at the rows")
                        stopped, refused = "verdicts undecided", True
                        break
                continue
            finish(code, symbol, parsed, reason)
            if done % SAVE_EVERY == 0:
                save()
                log(f"FinMind statements progress: {done} stocks this run, {counter['requests']} requests, "
                    f"last {code} ({reason})")
    except KeyboardInterrupt:
        stopped, paused = "interrupted", True
    finally:
        if held:
            log(f"{len(held)} stocks held without decided verdicts: they are fetched again next run")
        save()

    for name in DATASETS:
        if types_seen[name]:
            log(f"FinMind {name} types (rows this run): {json.dumps(dict(types_seen[name].most_common(60)), ensure_ascii=False)}")
    if unmatched:
        log("FinMind lines not used (counted): " + json.dumps(dict(unmatched.most_common(40)), ensure_ascii=False))
    log(f"FinMind statements run: {done} stocks, {counter['requests']} requests, {errors} errors, "
        f"outcomes {json.dumps(dict(outcomes), ensure_ascii=False)}")
    log(status_line(state, listing))
    if refused:
        log(f"STOPPED: {stopped}; progress saved")
    elif stopped and paused:
        log(f"PAUSED: {stopped}; progress saved, run the same command again later")
    elif stopped:
        log(f"STOPPED: {stopped}; progress saved, run the same command again later")
    return 1 if refused else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="FinMind statements → docs/report/financials/<code>.TW(O).json")
    parser.add_argument("--report-dir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "report"))
    parser.add_argument("--out-dir", default=None, help="default <report-dir>/financials")
    parser.add_argument("--budget", type=float, default=float(os.environ.get("FINMIND_BUDGET_SECONDS") or DEFAULT_BUDGET_SECONDS),
                        help="seconds of fetching this run (default 600)")
    parser.add_argument("--limit", type=int, default=None, help="at most this many requests")
    parser.add_argument("--tickers", type=lambda text: [part.strip().upper() for part in text.split(",") if part.strip()],
                        default=None, help="only these stocks, full history again (e.g. 2330,2881)")
    parser.add_argument("--probe", type=lambda text: [part.strip().upper() for part in text.split(",") if part.strip()],
                        default=None, help="print every type / origin_name of the three datasets for these stocks")
    parser.add_argument("--dry-run", action="store_true", help="offline: stocks and requests the next run would make")
    parser.add_argument("--only-after-backfill", action="store_true",
                        help="do nothing until BACKFILL_SHARE of the listed stocks are fetched (the daily workflow: "
                             "the first full backfill is a manual run and must not race it for the quota or files)")
    return parser.parse_args(argv)


def main(argv=None):
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())

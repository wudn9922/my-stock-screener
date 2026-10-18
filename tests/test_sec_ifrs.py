"""
IFRS foreign-filer documents: atlas/scripts/sec_ifrs.py and the --foreign wiring of seed_financials_sec.py.

Offline: every companyfacts payload below is a hand-written SYNTHETIC "Synthetic Taiwan Holdings" (round
made-up numbers, TWD) or "Synthetic Euro N.V." (EUR); none of it comes from SEC. The SEC client is injected
(a fake, or the real SecClient with an opener that answers 403), every file lives in a temporary directory,
and --no-splits keeps Yahoo out. Run: python -m unittest discover -s tests -p "test_sec_ifrs.py" -v
"""
import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_financials as fin  # noqa: E402
import seed_financials_sec as sec  # noqa: E402
import sec_ifrs as ifrs  # noqa: E402

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
TWD = "TWD"


def load_json(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def read_bytes(path):
    with open(path, "rb") as file:
        return file.read()


def point(end, value, filed, accn, form, start=None):
    """One companyfacts point (SYNTHETIC)."""
    out = {"end": end, "val": value, "filed": filed, "accn": accn, "form": form}
    if start is not None:
        out["start"] = start
    return out


def payload(cik, entity, concepts):
    """companyfacts shape: {concept: {unit: [points]}} under facts['ifrs-full'] (SYNTHETIC)."""
    block = {concept: {"units": {unit: points for unit, points in units.items()}}
             for concept, units in concepts.items()}
    return {"cik": int(cik), "entityName": entity, "facts": {"ifrs-full": block}}


def twd_concepts():
    """
    Synthetic Taiwan Holdings, TWD. 20-F for FY2020-FY2024 (each filing repeats the prior year), restated
    FY2022 revenue in the FY2023 filing (+1%), no attributable profit for FY2024 (ProfitLoss fallback), no
    cash concept at all, EUR facts for FY2018-FY2019 (a currency switch), 6-K for Q1-Q2 2025 (3-month
    values, half-year and nine-month values that must be ignored, a Q4 2024 3-month value that overlaps the
    FY2024 annual period).
    """
    concepts = defaultdict(lambda: defaultdict(list))

    def add(concept, unit, row):
        concepts[concept][unit].append(row)

    def year_values(year):
        rev = (year - 2019) * 10_000_000_000
        attributable = rev * 20 // 100
        eps = (year - 2019) * 2.5
        return {
            "Revenue": rev,
            "CostOfSales": rev * 6 // 10,
            "GrossProfit": rev - rev * 6 // 10,
            "ProfitLossFromOperatingActivities": rev * 25 // 100,
            "ProfitLossBeforeTax": rev * 24 // 100,
            "IncomeTaxExpenseContinuingOperations": rev * 4 // 100,
            "ProfitLossAttributableToOwnersOfParent": attributable,
            "ProfitLoss": attributable + rev // 100,
            "InterestExpense": rev // 100,
            "CashFlowsFromUsedInOperatingActivities": rev * 3 // 10,
            "PurchaseOfPropertyPlantAndEquipment": -(rev * 8 // 100),
            "DividendsPaidClassifiedAsFinancingActivities": -(rev * 5 // 100),
            "EquityAttributableToOwnersOfParent": rev * 4 // 10,
            "Assets": rev * 2,
            "BasicEarningsLossPerShare": eps,
            "DilutedEarningsLossPerShare": eps - 0.1,
        }

    instants = {"EquityAttributableToOwnersOfParent", "Assets"}
    for filing_year in range(2020, 2025):
        filed, accn = f"{filing_year + 1}-03-31", f"0001-{filing_year}"
        for year in (filing_year, filing_year - 1):
            if year < 2020:
                continue
            values = year_values(year)
            for concept, value in values.items():
                if concept == "ProfitLossAttributableToOwnersOfParent" and year == 2024:
                    continue      # FY2024 only has ProfitLoss (the fallback)
                if concept == "Revenue" and year == 2022 and filing_year == 2023:
                    value = value * 101 // 100     # restated in the next 20-F
                unit = f"{TWD}/shares" if concept.endswith("PerShare") else TWD
                if concept in instants:
                    add(concept, unit, point(f"{year}-12-31", value, filed, accn, "20-F"))
                else:
                    add(concept, unit, point(f"{year}-12-31", value, filed, accn, "20-F",
                                             start=f"{year}-01-01"))

    # 6-K 2025: Q1 and Q2 3-month values; half-year, nine-month and an overlapping Q4 2024 value
    add("Revenue", TWD, point("2025-03-31", 14_000_000_000, "2025-05-14", "0002-2025Q1", "6-K", "2025-01-01"))
    add("Revenue", TWD, point("2025-06-30", 15_000_000_000, "2025-08-13", "0002-2025Q2", "6-K", "2025-04-01"))
    add("Revenue", TWD, point("2025-06-30", 29_000_000_000, "2025-08-13", "0002-2025Q2", "6-K", "2025-01-01"))
    add("Revenue", TWD, point("2025-09-30", 44_000_000_000, "2025-11-13", "0003-2025Q3", "6-K", "2025-01-01"))
    add("Revenue", TWD, point("2024-12-31", 17_000_000_000, "2025-02-12", "0002-2024Q4", "6-K", "2024-10-01"))
    add("ProfitLossAttributableToOwnersOfParent", TWD,
        point("2025-03-31", 2_800_000_000, "2025-05-14", "0002-2025Q1", "6-K", "2025-01-01"))
    add("ProfitLossAttributableToOwnersOfParent", TWD,
        point("2025-06-30", 3_000_000_000, "2025-08-13", "0002-2025Q2", "6-K", "2025-04-01"))
    add("Assets", TWD, point("2025-06-30", 130_000_000_000, "2025-08-13", "0002-2025Q2", "6-K"))

    # Currency switch: EUR for FY2018-FY2019 (fewer period ends than the TWD years)
    for year, filed in ((2018, "2019-03-29"), (2019, "2020-03-30")):
        add("Revenue", "EUR", point(f"{year}-12-31", 1_000_000 * (year - 2017), filed, f"0009-{year}", "20-F",
                                    start=f"{year}-01-01"))
        add("ProfitLossAttributableToOwnersOfParent", "EUR",
            point(f"{year}-12-31", 200_000 * (year - 2017), filed, f"0009-{year}", "20-F", start=f"{year}-01-01"))
    return payload("1001", "Synthetic Taiwan Holdings Ltd.", concepts)


def eur_only_concepts():
    """Synthetic Euro N.V.: EUR only, FY2021-FY2024 (20-F), with a non-currency unit that must be ignored."""
    concepts = defaultdict(lambda: defaultdict(list))
    for year in range(2021, 2025):
        filed, accn = f"{year + 1}-03-30", f"0005-{year}"
        rev = (year - 2020) * 500_000_000
        concepts["Revenue"]["EUR"].append(point(f"{year}-12-31", rev, filed, accn, "20-F", start=f"{year}-01-01"))
        concepts["ProfitLossAttributableToOwnersOfParent"]["EUR"].append(
            point(f"{year}-12-31", rev // 10, filed, accn, "20-F", start=f"{year}-01-01"))
        concepts["Equity"]["EUR"].append(point(f"{year}-12-31", rev, filed, accn, "20-F"))
        concepts["Revenue"]["shares"].append(point(f"{year}-12-31", 1, filed, accn, "20-F", start=f"{year}-01-01"))
    return payload("1002", "Synthetic Euro N.V.", concepts)


def us_gaap_payload():
    """A US-GAAP filer (USD, two 10-K years): the normal path must still own it."""
    def row(end, value, start, filed):
        return {"start": start, "end": end, "val": value, "accn": f"0006-{end[:4]}", "fy": int(end[:4]),
                "fp": "FY", "form": "10-K", "filed": filed}
    return {"cik": 1003, "entityName": "Synthetic US Corp", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [row("2023-12-31", 900, "2023-01-01", "2024-02-15"),
                                       row("2024-12-31", 1000, "2024-01-01", "2025-02-14")]}},
        "NetIncomeLoss": {"units": {"USD": [row("2023-12-31", 90, "2023-01-01", "2024-02-15"),
                                            row("2024-12-31", 100, "2024-01-01", "2025-02-14")]}},
    }}}


CNY = "CNY"
EUR = "EUR"
RC = "RevenueFromContractWithCustomerExcludingAssessedTax"


def foreign_gaap_payload(cik, entity, concepts):
    """companyfacts shape: {concept: {unit: [points]}} under facts['us-gaap'] (SYNTHETIC)."""
    block = {concept: {"units": {unit: points for unit, points in units.items()}}
             for concept, units in concepts.items()}
    return {"cik": int(cik), "entityName": entity, "facts": {"us-gaap": block}}


def cny_us_gaap_concepts():
    """
    Synthetic Shenzhen Holdings (BABA-like ADR issuer), US GAAP, CNY; every number is made up. 20-F for
    FY2020-FY2024, each filing repeating the prior year; FY2022 revenue restated +1% in the FY2023 filing.
    "Revenues" up to FY2021, the ASC 606 tag from FY2022. NetIncomeLoss except FY2024 (ProfitLoss fallback).
    USD convenience values of FY2023-FY2024 revenue (another unit, never mixed). 6-K 2025 Q1 and Q2 3-month
    values next to a 6-month and a nine-month value (both ignored) and a Q4 2024 3-month value that overlaps
    the FY2024 annual period (dropped).
    """
    concepts = defaultdict(lambda: defaultdict(list))

    def add(concept, unit, row):
        concepts[concept][unit].append(row)

    def base(year):
        return (year - 2019) * 100_000_000_000

    for filing_year in range(2020, 2025):
        filed, accn = f"{filing_year + 1}-03-31", f"0009-{filing_year}"
        for year in (filing_year, filing_year - 1):
            if year < 2020:
                continue
            revenue = base(year)
            if year == 2022 and filing_year == 2023:
                revenue = revenue * 101 // 100       # restated in the next 20-F
            add("Revenues" if year <= 2021 else RC, CNY,
                point(f"{year}-12-31", revenue, filed, accn, "20-F", start=f"{year}-01-01"))
            flows = {
                "CostOfRevenue": base(year) * 6 // 10,
                "GrossProfit": base(year) * 4 // 10,
                "OperatingIncomeLoss": base(year) * 25 // 100,
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest":
                    base(year) * 24 // 100,
                "IncomeTaxExpenseBenefit": base(year) * 4 // 100,
                "InterestExpense": base(year) // 100,
                "NetCashProvidedByUsedInOperatingActivities": base(year) * 3 // 10,
                "PaymentsToAcquirePropertyPlantAndEquipment": base(year) * 8 // 100,
                "PaymentsOfDividends": base(year) * 5 // 100,
                "ProfitLoss": base(year) * 16 // 100,
            }
            if year != 2024:
                flows["NetIncomeLoss"] = base(year) * 15 // 100
            for concept, value in flows.items():
                add(concept, CNY, point(f"{year}-12-31", value, filed, accn, "20-F", start=f"{year}-01-01"))
            for concept, sign in (("EarningsPerShareBasic", 0.0), ("EarningsPerShareDiluted", -0.2)):
                add(concept, f"{CNY}/shares",
                    point(f"{year}-12-31", (year - 2019) * 3.0 + sign, filed, accn, "20-F", start=f"{year}-01-01"))
            add("StockholdersEquity", CNY, point(f"{year}-12-31", base(year) * 4 // 10, filed, accn, "20-F"))
            add("Assets", CNY, point(f"{year}-12-31", base(year) * 2, filed, accn, "20-F"))
            add("CashAndCashEquivalentsAtCarryingValue", CNY,
                point(f"{year}-12-31", base(year) // 10, filed, accn, "20-F"))
    for year in (2023, 2024):
        add(RC, "USD", point(f"{year}-12-31", base(year) // 7, f"{year + 1}-03-31", f"0009-{year}", "20-F",
                             start=f"{year}-01-01"))
    add(RC, CNY, point("2025-03-31", 130_000_000_000, "2025-05-14", "0010-2025Q1", "6-K", start="2025-01-01"))
    add(RC, CNY, point("2025-06-30", 140_000_000_000, "2025-08-13", "0010-2025Q2", "6-K", start="2025-04-01"))
    add(RC, CNY, point("2025-06-30", 290_000_000_000, "2025-08-13", "0010-2025Q2", "6-K", start="2025-01-01"))
    add(RC, CNY, point("2025-09-30", 440_000_000_000, "2025-11-13", "0011-2025Q3", "6-K", start="2025-01-01"))
    add(RC, CNY, point("2024-12-31", 170_000_000_000, "2025-02-12", "0010-2024Q4", "6-K", start="2024-10-01"))
    add("NetIncomeLoss", CNY, point("2025-03-31", 20_000_000_000, "2025-05-14", "0010-2025Q1", "6-K",
                                    start="2025-01-01"))
    add("StockholdersEquity", CNY, point("2025-03-31", 220_000_000_000, "2025-05-14", "0010-2025Q1", "6-K"))
    return foreign_gaap_payload("1010", "Synthetic Shenzhen Holdings", concepts)


def eur_us_gaap_concepts():
    """
    Synthetic Amsterdam Chips N.V. (ASML-like), US GAAP, EUR; every number is made up. 20-F for FY2021-FY2024,
    a "shares" unit next to the EUR net income (ignored), a 6-K Q1 2025 and a 10-Q Q2 2025 3-month value
    (both quarterly forms of the US GAAP basis).
    """
    concepts = defaultdict(lambda: defaultdict(list))
    for year in range(2021, 2025):
        filed, accn = f"{year + 1}-02-10", f"0011-{year}"
        rev = (year - 2020) * 500_000_000
        start = f"{year}-01-01"
        concepts["Revenues"][EUR].append(point(f"{year}-12-31", rev, filed, accn, "20-F", start=start))
        concepts["NetIncomeLoss"][EUR].append(point(f"{year}-12-31", rev // 5, filed, accn, "20-F", start=start))
        concepts["NetIncomeLoss"]["shares"].append(point(f"{year}-12-31", 1, filed, accn, "20-F", start=start))
        concepts["EarningsPerShareBasic"]["EUR/shares"].append(
            point(f"{year}-12-31", (year - 2020) * 1.5, filed, accn, "20-F", start=start))
        concepts["StockholdersEquity"][EUR].append(point(f"{year}-12-31", rev, filed, accn, "20-F"))
    concepts["Revenues"][EUR].append(point("2025-03-31", 1_300_000_000, "2025-04-29", "0012-2025Q1", "6-K",
                                           start="2025-01-01"))
    concepts["Revenues"][EUR].append(point("2025-06-30", 1_400_000_000, "2025-07-29", "0013-2025Q2", "10-Q",
                                           start="2025-04-01"))
    return foreign_gaap_payload("1011", "Synthetic Amsterdam Chips", concepts)


def revenue_rule_concepts():
    """
    Synthetic Rule Holdings, US GAAP: CNY revenue for FY2022-FY2024 (20-F), and USD assets and equity for
    FY2020-FY2024 only. The currency is picked by the annual revenue / net income periods, so CNY wins.
    """
    concepts = defaultdict(lambda: defaultdict(list))
    for year in range(2020, 2025):
        filed = f"{year + 1}-03-31"
        concepts["Assets"]["USD"].append(point(f"{year}-12-31", 1_000, filed, f"0014-{year}", "20-F"))
        concepts["StockholdersEquity"]["USD"].append(point(f"{year}-12-31", 400, filed, f"0014-{year}", "20-F"))
    for year in range(2022, 2025):
        concepts["Revenues"][CNY].append(point(f"{year}-12-31", 10_000, f"{year + 1}-03-31", f"0015-{year}", "20-F",
                                               start=f"{year}-01-01"))
    return foreign_gaap_payload("1014", "Synthetic Rule Holdings", concepts)


class FakeClient:
    """companyfacts by CIK (SYNTHETIC payloads, None = 404); ticker_map is SYNTHETIC too."""

    TICKERS = {"SYNA": "0000001001", "SYNB": "0000001002", "SYNC": "0000001004"}

    def __init__(self, payloads):
        self.payloads = payloads
        self.requests = 0
        self.asked = []
        self.ticker_calls = 0

    def company_facts(self, cik):
        self.requests += 1
        self.asked.append(cik)
        return self.payloads.get(cik)

    def ticker_map(self):
        self.ticker_calls += 1
        return dict(self.TICKERS)


class NoNetworkClient(FakeClient):
    """Fails on any SEC request: the dry run and the has-file path must not make one."""

    def company_facts(self, cik):
        raise AssertionError("no SEC request expected")

    def ticker_map(self):
        raise AssertionError("no SEC request expected")


class ExtractionTests(unittest.TestCase):
    def test_concept_mapping_and_units(self):
        result = ifrs.extract(twd_concepts())
        self.assertIsNone(result.skip)
        self.assertEqual(result.currency, TWD)
        fy2021 = next(p for p in result.annual if p["end"] == "2021-12-31")
        values = fy2021["values"]
        self.assertEqual(values["revenue"], 20_000_000_000)
        self.assertEqual(values["costOfRevenue"], 12_000_000_000)
        self.assertEqual(values["grossProfit"], 8_000_000_000)
        self.assertEqual(values["operatingIncome"], 5_000_000_000)
        self.assertEqual(values["pretaxIncome"], 4_800_000_000)
        self.assertEqual(values["taxProvision"], 800_000_000)
        self.assertEqual(values["netIncome"], 4_000_000_000)
        self.assertAlmostEqual(values["epsBasic"], 5.0)
        self.assertAlmostEqual(values["epsDiluted"], 4.9)
        self.assertEqual(values["operatingCashFlow"], 6_000_000_000)
        self.assertEqual(values["equity"], 8_000_000_000)
        self.assertEqual(values["totalAssets"], 40_000_000_000)
        self.assertEqual(values["interestExpense"], 200_000_000)
        self.assertEqual(fy2021["concepts"]["netIncome"], "ProfitLossAttributableToOwnersOfParent")
        self.assertFalse(fy2021["fallback"])

    def test_outflows_are_positive_whatever_the_sign(self):
        values = ifrs.extract(twd_concepts()).annual[0]["values"]     # FY2020: revenue 10bn
        self.assertEqual(values["capex"], 800_000_000)               # 8%, filed as a negative cash flow
        self.assertEqual(values["dividendsPaid"], 500_000_000)       # 5%

    def test_profit_loss_is_a_flagged_fallback_only_where_attributable_is_missing(self):
        result = ifrs.extract(twd_concepts())
        fy2024 = next(p for p in result.annual if p["end"] == "2024-12-31")   # revenue 50bn
        self.assertTrue(fy2024["fallback"])
        self.assertEqual(fy2024["concepts"]["netIncome"], "ProfitLoss")
        self.assertEqual(fy2024["values"]["netIncome"], 10_000_000_000 + 500_000_000)
        fy2023 = next(p for p in result.annual if p["end"] == "2023-12-31")
        self.assertFalse(fy2023["fallback"])
        self.assertEqual(fy2023["concepts"]["netIncome"], "ProfitLossAttributableToOwnersOfParent")
        self.assertTrue(result.fallback)

    def test_latest_filing_wins_for_restated_values(self):
        result = ifrs.extract(twd_concepts())
        fy2022 = next(p for p in result.annual if p["end"] == "2022-12-31")   # 30bn, restated +1% in FY2023 20-F
        self.assertEqual(fy2022["values"]["revenue"], 30_000_000_000 * 101 // 100)

    def test_missing_cash_stays_null_and_is_never_derived(self):
        document, reason = ifrs.build_document(twd_concepts(), "SYNA", NOW)
        self.assertIsNone(reason)
        self.assertNotIn("cash", document["annual"]["values"])
        for absent in ("dilutedShares", "totalDebt", "operatingIncomeReported", "fcfYahoo"):
            self.assertNotIn(absent, document["annual"]["values"])

    def test_annual_periods_only_from_annual_forms_and_durations(self):
        result = ifrs.extract(twd_concepts())
        self.assertEqual([p["end"] for p in result.annual], [f"{y}-12-31" for y in range(2020, 2025)])

    def test_quarterly_only_three_month_values_from_interim_forms(self):
        result = ifrs.extract(twd_concepts())
        self.assertEqual([p["end"] for p in result.quarterly], ["2025-03-31", "2025-06-30"])
        q2 = result.quarterly[1]
        self.assertEqual(q2["values"]["revenue"], 15_000_000_000)     # not the 29bn half-year value
        self.assertNotIn("2025-09-30", [p["end"] for p in result.quarterly])

    def test_quarter_overlapping_an_annual_end_is_dropped(self):
        result = ifrs.extract(twd_concepts())
        self.assertNotIn("2024-12-31", [p["end"] for p in result.quarterly])

    def test_one_currency_only_and_the_others_are_recorded(self):
        result = ifrs.extract(twd_concepts())
        self.assertEqual(result.currencies, {"EUR": 2, "TWD": 5})
        self.assertEqual(result.currency, TWD)
        self.assertNotIn("2018-12-31", [p["end"] for p in result.annual])
        self.assertTrue(all(p["values"]["revenue"] > 1_000_000_000 for p in result.annual))

    def test_single_other_currency_filer_uses_its_own_unit(self):
        result = ifrs.extract(eur_only_concepts())
        self.assertEqual(result.currency, "EUR")
        self.assertEqual(len(result.annual), 4)
        self.assertEqual(result.annual[0]["values"]["revenue"], 500_000_000)
        self.assertEqual(result.annual[0]["values"]["equity"], 500_000_000)
        document, _ = ifrs.build_document(eur_only_concepts(), "SYNB", NOW)
        self.assertEqual(document["currency"], "EUR")
        self.assertNotIn("ifrsCurrencies", document)

    def test_tie_on_period_count_goes_to_the_latest_filed_unit(self):
        counts, latest = {"EUR": 2, "USD": 2}, {"EUR": "2020-03-30", "USD": "2021-03-30"}
        self.assertEqual(ifrs.choose_currency(counts, latest), "USD")

    def test_skip_reasons(self):
        empty = payload("1004", "Synthetic Nothing", {})
        self.assertEqual(ifrs.build_document(empty, "SYNC", NOW), (None, "no-ifrs"))
        self.assertEqual(ifrs.build_document(us_gaap_payload(), "SYND", NOW), (None, "no-ifrs"))
        self.assertEqual(ifrs.build_document(twd_concepts(), "2330.TW", NOW), (None, "not-us-key"))
        short = payload("1005", "Synthetic Short", {"Revenue": {TWD: [
            point("2024-12-31", 5, "2025-03-31", "0007", "20-F", start="2024-01-01")]}})
        self.assertEqual(ifrs.build_document(short, "SYNE", NOW), (None, "short-history"))
        self.assertEqual(ifrs.build_document(None, "SYNF", NOW), (None, "no-ifrs"))

    def test_bad_points_are_ignored(self):
        concepts = {"Revenue": {TWD: [
            point("2024-12-31", "n/a", "2025-03-31", "0008", "20-F", start="2024-01-01"),
            point("2024-12-31", 9, "2025-03-31", "0008", "20-F", start="2024-12-31"),
        ]}}
        self.assertEqual(ifrs.extract(payload("1006", "Synthetic Junk", concepts)).skip, "ifrs-no-usable-facts")
        self.assertEqual(ifrs.build_document(payload("1006", "Synthetic Junk", concepts), "SYNJ", NOW),
                         (None, "ifrs-no-usable-facts"))


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.document, self.reason = ifrs.build_document(twd_concepts(), "SYNA", NOW)
        self.assertIsNone(self.reason)

    def test_schema_matches_the_stored_format(self):
        doc = self.document
        self.assertEqual(doc["symbol"], "SYNA")
        self.assertEqual(doc["market"], "US")
        self.assertEqual(doc["currency"], TWD)
        self.assertIs(doc["foreignReporter"], True)
        self.assertEqual(doc["source"], "SEC EDGAR companyfacts (IFRS)")
        self.assertEqual(doc["generatedAt"], "2026-10-15T09:00:00Z")
        self.assertEqual(doc["checkedAt"], "2026-10-15")
        self.assertEqual(doc["fyEndMonth"], 12)
        self.assertEqual(doc["split"], {"adjusted": False, "events": []})
        self.assertEqual(doc["version"], fin.VERSION)
        self.assertEqual(doc["ifrsCurrencies"], {"EUR": 2, "TWD": 5})
        self.assertTrue(set(fin.DOCUMENT_KEYS) <= set(doc))
        self.assertEqual(set(doc) - set(fin.DOCUMENT_KEYS), {"secConcepts", "ifrsCurrencies"})

    def test_sections_are_columnar_with_sec_source_and_fiscal_labels(self):
        annual, quarterly = self.document["annual"], self.document["quarterly"]
        self.assertEqual(annual["end"], [f"{y}-12-31" for y in range(2020, 2025)])
        self.assertEqual(annual["fy"], [2020, 2021, 2022, 2023, 2024])
        self.assertEqual(set(annual["src"]), {"sec"})
        self.assertEqual(annual["values"]["revenue"], [10_000_000_000, 20_000_000_000, 30_300_000_000,
                                                       40_000_000_000, 50_000_000_000])
        self.assertEqual(quarterly["end"], ["2025-03-31", "2025-06-30"])
        self.assertEqual(quarterly["fq"], [1, 2])
        self.assertEqual(quarterly["fy"], [2025, 2025])
        self.assertNotIn("cash", annual["values"])
        self.assertNotIn("capex", quarterly["values"])        # 6-K cash flows are year-to-date: not split

    def test_transition_year_quarters_use_their_own_annual_end(self):
        concepts = defaultdict(lambda: defaultdict(list))
        annual_rows = (
            ("2022-03-31", "2021-04-01", "2023-04-01"),
            ("2023-03-31", "2022-04-01", "2024-04-01"),
            ("2023-12-31", "2023-01-01", "2024-04-02"),
        )
        for index, (end, start, filed) in enumerate(annual_rows):
            concepts["Revenue"][TWD].append(
                point(end, 100 + index, filed, f"0010-A{index}", "20-F", start=start)
            )
        for index, (end, start, filed) in enumerate((
            ("2023-06-30", "2023-04-01", "2023-08-01"),
            ("2023-09-30", "2023-07-01", "2023-11-01"),
        )):
            concepts["Revenue"][TWD].append(
                point(end, 20 + index, filed, f"0010-Q{index}", "6-K", start=start)
            )

        document, reason = ifrs.build_document(
            payload("1010", "Synthetic Transition Ltd.", concepts), "SYNTRANS", NOW,
        )
        self.assertIsNone(reason)
        quarterly = document["quarterly"]
        self.assertEqual(quarterly["end"], ["2023-06-30", "2023-09-30"])
        self.assertEqual(quarterly["fy"], [2023, 2023])
        self.assertEqual(quarterly["fq"], [2, 3])

    def test_quality_issues_carry_ifrs_codes(self):
        quality = self.document["quality"]
        self.assertEqual(quality["annualYears"], 5)
        self.assertEqual(quality["quarters"], 2)
        self.assertEqual(quality["level"], "good")
        self.assertEqual(quality["issues"], ["foreign-currency", "ifrs", "ifrs-fallback"])

    def test_thin_filer_is_limited_and_flagged_short(self):
        thin = payload("1007", "Synthetic Thin", {
            "Revenue": {TWD: [point("2023-12-31", 9, "2024-03-29", "0010", "20-F", start="2023-01-01"),
                              point("2024-12-31", 10, "2025-03-28", "0011", "20-F", start="2024-01-01")]},
        })
        document, _ = ifrs.build_document(thin, "SYNG", NOW)
        self.assertEqual(document["quality"]["level"], "limited")       # two annual years
        self.assertIn("short-history", document["quality"]["issues"])

    def test_four_years_without_net_income_are_partial(self):
        concepts = {"Revenue": {TWD: [point(f"{y}-12-31", 10 * y, "2026-03-31", "0012", "20-F",
                                            start=f"{y}-01-01") for y in range(2021, 2025)]},
                    "Assets": {TWD: [point(f"{y}-12-31", 5, "2026-03-31", "0012", "20-F") for y in range(2021, 2025)]}}
        document, reason = ifrs.build_document(payload("1008", "Synthetic Partial", concepts), "SYNH", NOW)
        self.assertIsNone(reason)
        self.assertEqual(document["quality"]["level"], "partial")
        self.assertNotIn("netIncome", document["annual"]["values"])

    def test_round_trip_through_the_stored_reader(self):
        """The Yahoo daily job reads the file with the same reader it uses for every stored document."""
        stored = fin.read_sections(json.loads(json.dumps(self.document)))
        self.assertEqual(stored["currency"], TWD)
        self.assertEqual(stored["annual"][0]["end"], "2020-12-31")


class PlanTests(unittest.TestCase):
    def test_universe_order_is_by_market_cap_and_unique(self):
        universe = {"markets": {"US": {"fields": ["symbol", "name", "marketCap"], "rows": [
            ["SMALL", "s", 1e9], ["BIG", "b", 9e9], ["BRK.B", "x", 5e9], ["BRK-B", "y", 5e9], ["", "z", 1e9],
        ]}}}
        self.assertEqual(ifrs.universe_us_keys(universe), [("BIG", "BIG"), ("BRK-B", "BRK.B"), ("SMALL", "SMALL")])

    def test_plan_keeps_foreign_statuses_and_drops_files_and_checked(self):
        progress = {
            "issuers": {"0000000001": {"status": "foreign-filer"}, "0000000002": {"status": "no-us-gaap"},
                        "0000000003": {"status": "short-history"}, "0000000004": {"status": "non-usd"}},
            "symbols": {"AAA": {"cik": "0000000001"}, "BBB": {"cik": "0000000002"},
                        "CCC": {"cik": "0000000003"}, "DDD": {"cik": "0000000004"}},
            "tickerMap": {},
        }
        universe = {"markets": {"US": {"fields": ["symbol", "marketCap"], "rows": [
            ["AAA", 3], ["BBB", 2], ["CCC", 5], ["DDD", 1]]}}}
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        open(os.path.join(out, "DDD.json"), "w").close()
        state = {"version": ifrs.STATE_VERSION, "checked": {"BBB": {"result": "skipped"}}}
        plan = ifrs.plan_foreign(universe, progress, out, state)
        self.assertEqual([c[0] for c in plan["candidates"]], ["AAA", "BBB", "DDD"])
        self.assertEqual(plan["hasFile"], ["DDD"])
        self.assertEqual(plan["checked"], ["BBB"])
        self.assertEqual(plan["todo"], [("AAA", "0000000001")])
        self.assertEqual(plan["byStatus"], {"foreign-filer": 1, "no-us-gaap": 1, "non-usd": 1})


class SeedWiringTests(unittest.TestCase):
    """seed_financials_sec.seed_ticker / main with --foreign, through the injected fake client."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = os.path.join(self.tmp, "financials")
        self.report = os.path.join(self.tmp, "report")
        os.makedirs(os.path.join(self.report, "fundamentals"))
        os.makedirs(self.out)
        universe = {"markets": {"US": {"fields": ["symbol", "name", "marketCap"], "rows": [
            ["SYNA", "Synthetic A", 5e9], ["SYNB", "Synthetic B", 1e9], ["USAA", "US A", 7e9], ["SYNC", "C", 2e9],
        ]}}}
        progress = {
            "issuers": {"0000001001": {"status": "foreign-filer"}, "0000001002": {"status": "no-us-gaap"},
                        "0000001003": {"status": "short-history"}, "0000001004": {"status": "foreign-filer"},
                        "0000001005": {"status": "foreign-filer"}},
            "symbols": {"SYNA": {"cik": "0000001001"}, "SYNB": {"cik": "0000001002"},
                        "USAA": {"cik": "0000001003"}, "SYNC": {"cik": "0000001004"}},
            "tickerMap": {"SYNA": "0000001001", "SYNB": "0000001002", "USAA": "0000001003",
                          "SYNC": "0000001004", "SYND": "0000001005"},
        }
        with open(os.path.join(self.report, "universe.json"), "w") as file:
            json.dump(universe, file)
        with open(os.path.join(self.report, "fundamentals", "us-market-progress.json"), "w") as file:
            json.dump(progress, file)
        # SYNC already has a file (a stand-in for a Yahoo-only one): --foreign must never touch it
        existing, _ = ifrs.build_document(twd_concepts(), "SYNC", NOW)
        existing["source"] = "Yahoo Finance fundamentals-timeseries"
        with open(os.path.join(self.out, "SYNC.json"), "w") as file:
            json.dump(existing, file)
        with open(os.path.join(self.out, "SYNC.json"), "rb") as file:
            self.before_sync = file.read()
        self.state_path = os.path.join(self.tmp, "state", "sec-ifrs-state.json")

    def run_main(self, client, extra=(), clock=None):
        args = ["--foreign", "--no-splits", "--report-dir", self.report, "--out", self.out,
                "--state", self.state_path, *extra]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = sec.main(args, client=client, now=NOW, environ={}, clock=clock)
        return code, buffer.getvalue()

    def test_dry_run_is_offline_and_writes_nothing(self):
        code, text = self.run_main(NoNetworkClient({}), extra=("--dry-run",))
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertIn("--foreign: 3 candidate ticker(s)", text)
        self.assertIn("1 already have a file", text)
        self.assertIn("2 to check", text)
        self.assertIn("Dry run: 2 ticker(s) would be checked", text)
        self.assertEqual(sorted(os.listdir(self.out)), ["SYNC.json"])
        self.assertFalse(os.path.exists(self.state_path))

    def test_dry_run_needs_foreign_or_due(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            sec.parse_args(["--dry-run", "--no-splits"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            sec.parse_args(["--foreign", "--due"])

    def test_run_writes_ifrs_files_and_leaves_existing_ones(self):
        client = FakeClient({"0000001001": twd_concepts(), "0000001002": eur_only_concepts()})
        code, text = self.run_main(client)
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertEqual(sorted(client.asked), ["0000001001", "0000001002"])
        self.assertEqual(client.requests, 2)
        doc = load_json(os.path.join(self.out, "SYNA.json"))
        self.assertEqual(doc["currency"], TWD)
        self.assertEqual(doc["source"], "SEC EDGAR companyfacts (IFRS)")
        self.assertEqual(load_json(os.path.join(self.out, "SYNB.json"))["currency"], "EUR")
        self.assertEqual(read_bytes(os.path.join(self.out, "SYNC.json")), self.before_sync)
        self.assertIn("written", text)
        state = load_json(self.state_path)
        self.assertEqual(sorted(state["checked"]), ["SYNA", "SYNB"])

    def test_budget_stops_between_tickers_and_a_rerun_finishes(self):
        client = FakeClient({"0000001001": twd_concepts(), "0000001002": eur_only_concepts()})
        ticks = iter([0, 0, 5])                          # start, before SYNA, before SYNB (budget used up)
        code, text = self.run_main(client, extra=("--budget", "1"), clock=lambda: next(ticks))
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertEqual(client.requests, 1)
        self.assertIn("PAUSED: time budget of 1 s used up", text)
        self.assertEqual(sorted(os.listdir(self.out)), ["SYNA.json", "SYNC.json"])
        code, text = self.run_main(client)
        self.assertEqual(client.requests, 2)                 # only SYNB left
        self.assertIn("2 already have a file", text)         # SYNA (written) and SYNC (Yahoo-only)
        code, text = self.run_main(client)               # nothing left: no request at all
        self.assertEqual(client.requests, 2)
        self.assertIn("0 to check", text)

    def test_missing_companyfacts_is_recorded_and_not_asked_again(self):
        client = FakeClient({"0000001001": twd_concepts()})  # SYNB answers 404
        self.run_main(client)
        self.assertEqual(client.requests, 2)
        state = load_json(self.state_path)
        self.assertEqual(state["checked"]["SYNB"]["result"], "no-facts")
        client2 = FakeClient({})
        self.run_main(client2)
        self.assertEqual(client2.requests, 0)

    def test_first_403_stops_the_run_at_once(self):
        forbidden = sec.SecClient(sec.DEFAULT_USER_AGENT, opener=lambda request: (403, b""),
                                  sleeper=lambda seconds: None)
        code, text = self.run_main(forbidden)
        self.assertEqual(code, sec.EXIT_STOPPED)
        self.assertEqual(forbidden.requests, 1)
        self.assertIn("STOPPED", text)
        self.assertEqual(sorted(os.listdir(self.out)), ["SYNC.json"])
        self.assertFalse(os.path.exists(self.state_path))

    def test_us_gaap_filer_keeps_the_normal_path_under_foreign(self):
        client = FakeClient({"0000001001": us_gaap_payload()})
        code, _ = self.run_main(client, extra=("--tickers", "SYNA"))
        self.assertEqual(code, sec.EXIT_DONE)
        doc = load_json(os.path.join(self.out, "SYNA.json"))
        self.assertEqual(doc["currency"], "USD")
        self.assertIs(doc["foreignReporter"], False)
        self.assertNotIn("ifrs", doc["quality"]["issues"])

    def test_without_foreign_an_ifrs_filer_is_still_skipped(self):
        client = FakeClient({"0000001001": twd_concepts()})
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = sec.main(["--tickers", "SYNA", "--no-splits", "--report-dir", self.report, "--out", self.out,
                             "--state", self.state_path], client=client, now=NOW, environ={})
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertIn("skip reason foreign-filer: 1", buffer.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.out, "SYNA.json")))

    def test_seed_ticker_has_file_outcome(self):
        client = NoNetworkClient({})
        outcome = sec.seed_ticker("SYNC", {"SYNC": "0000001004"}, client, self.out, False, NOW, None, foreign=True)
        self.assertEqual(outcome["result"], "has-file")

    def test_seed_ticker_without_foreign_keeps_the_old_skip(self):
        client = FakeClient({"0000001001": twd_concepts()})
        outcome = sec.seed_ticker("SYNA", {"SYNA": "0000001001"}, client, self.out, False, NOW, None)
        self.assertEqual((outcome["result"], outcome["detail"]), ("skipped", "foreign-filer"))
        self.assertFalse(os.path.exists(os.path.join(self.out, "SYNA.json")))



class DailyJobTests(unittest.TestCase):
    """The Yahoo daily run (build_financials.update_document) rebuilds the file from its own rules."""

    def test_daily_run_keeps_the_foreign_flag_and_the_ifrs_codes(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "financials", "yahoo_us_full.json"), encoding="utf-8") as file:
            payload = json.load(file)
        doc, _ = fin.update_document(None, payload, "AAPL", NOW)
        self.assertIs(doc["foreignReporter"], False)               # a US-dollar file of a domestic filer
        doc["foreignReporter"] = True                              # what sec_ifrs.py writes for an IFRS filer
        doc["quality"]["issues"] = sorted(set(doc["quality"]["issues"]) | {"ifrs", "ifrs-fallback"})
        updated, _ = fin.update_document(copy.deepcopy(doc), payload, "AAPL", NOW + timedelta(days=1))
        self.assertIs(updated["foreignReporter"], True)
        self.assertIn("ifrs", updated["quality"]["issues"])
        self.assertIn("ifrs-fallback", updated["quality"]["issues"])
        self.assertIn("foreign-currency", updated["quality"]["issues"])


class UsGaapForeignTests(unittest.TestCase):
    """The US GAAP basis of sec_ifrs.py: a foreign filer's us-gaap facts, one currency, 20-F / 10-K and 6-K / 10-Q."""

    def setUp(self):
        self.document, self.reason = ifrs.build_us_gaap_document(cny_us_gaap_concepts(), "BABA", NOW)
        self.assertIsNone(self.reason)

    def test_one_currency_from_annual_revenue_and_net_income_and_the_others_recorded(self):
        result = ifrs.extract(cny_us_gaap_concepts(), ifrs.US_GAAP_FOREIGN_BASIS)
        self.assertIsNone(result.skip)
        self.assertEqual(result.currency, CNY)
        self.assertEqual(result.currencies, {CNY: 5, "USD": 2})
        self.assertEqual([p["end"] for p in result.annual], [f"{y}-12-31" for y in range(2020, 2025)])
        values = {p["end"]: p["values"] for p in result.annual}
        self.assertEqual(values["2020-12-31"]["revenue"], 100_000_000_000)          # Revenues
        self.assertEqual(values["2022-12-31"]["revenue"], 303_000_000_000)          # restated, latest filed wins
        self.assertEqual(values["2023-12-31"]["revenue"], 400_000_000_000)          # not the USD convenience value
        self.assertEqual(values["2024-12-31"]["netIncome"], 80_000_000_000)         # ProfitLoss fallback
        self.assertEqual(values["2024-12-31"]["epsBasic"], 15.0)                     # per ordinary share, CNY
        self.assertTrue(result.fallback)
        self.assertEqual(result.concepts["revenue"], sorted(["Revenues", RC]))
        self.assertEqual(result.concepts["netIncome"], ["NetIncomeLoss", "ProfitLoss"])

    def test_quarters_are_three_month_values_of_the_interim_forms_only(self):
        result = ifrs.extract(cny_us_gaap_concepts(), ifrs.US_GAAP_FOREIGN_BASIS)
        self.assertEqual([p["end"] for p in result.quarterly], ["2025-03-31", "2025-06-30"])
        q1, q2 = result.quarterly
        self.assertEqual(q1["values"]["revenue"], 130_000_000_000)
        self.assertEqual(q1["values"]["netIncome"], 20_000_000_000)
        self.assertEqual(q2["values"]["revenue"], 140_000_000_000)        # not the six-month 290bn
        self.assertNotIn("netIncome", q2["values"])
        self.assertEqual(q1["values"]["equity"], 220_000_000_000)        # quarter-end instant of a 6-K

    def test_document_schema_and_codes(self):
        doc = self.document
        self.assertEqual(doc["symbol"], "BABA")
        self.assertEqual(doc["currency"], CNY)
        self.assertIs(doc["foreignReporter"], True)
        self.assertEqual(doc["source"], "SEC EDGAR companyfacts (US GAAP, foreign filer)")
        self.assertEqual(doc["usGaapCurrencies"], {CNY: 5, "USD": 2})
        self.assertNotIn("ifrsCurrencies", doc)
        self.assertEqual(set(doc) - set(fin.DOCUMENT_KEYS), {"secConcepts", "usGaapCurrencies"})
        self.assertEqual(doc["quality"]["issues"], ["foreign-currency", "foreign-gaap", "foreign-gaap-fallback"])
        self.assertEqual(doc["secConcepts"]["revenue"], sorted(["Revenues", RC]))
        self.assertEqual(doc["annual"]["values"]["epsDiluted"][-1], 14.8)
        self.assertEqual(doc["annual"]["src"], ["sec"] * 5)
        self.assertNotIn("dilutedShares", doc["annual"]["values"])          # not mapped on this basis

    def test_eur_filer_keeps_its_unit_and_takes_10_q_quarters(self):
        result = ifrs.extract(eur_us_gaap_concepts(), ifrs.US_GAAP_FOREIGN_BASIS)
        self.assertEqual(result.currency, EUR)
        self.assertEqual(result.currencies, {EUR: 4})
        self.assertEqual(len(result.annual), 4)
        self.assertEqual(result.annual[-1]["values"]["netIncome"], 400_000_000)
        self.assertEqual(result.annual[-1]["values"]["epsBasic"], 6.0)          # EUR/shares, not converted
        self.assertEqual([p["end"] for p in result.quarterly], ["2025-03-31", "2025-06-30"])
        document, reason = ifrs.build_us_gaap_document(eur_us_gaap_concepts(), "ASML", NOW)
        self.assertIsNone(reason)
        self.assertEqual(document["currency"], EUR)
        self.assertNotIn("usGaapCurrencies", document)

    def test_the_currency_rule_counts_revenue_and_net_income_only(self):
        result = ifrs.extract(revenue_rule_concepts(), ifrs.US_GAAP_FOREIGN_BASIS)
        self.assertEqual(result.currencies, {CNY: 3})            # the USD balance sheet is not counted
        self.assertEqual(result.currency, CNY)

    def test_annual_10_k_values_count_on_this_basis(self):
        ten_k = foreign_gaap_payload("1015", "Synthetic Ten K", {"Revenues": {CNY: [
            point("2023-12-31", 5, "2024-03-29", "0016", "10-K", start="2023-01-01"),
            point("2024-12-31", 6, "2025-03-28", "0017", "10-K", start="2024-01-01")]}})
        document, reason = ifrs.build_us_gaap_document(ten_k, "SYNK", NOW)
        self.assertIsNone(reason)
        self.assertEqual(document["annual"]["end"], ["2023-12-31", "2024-12-31"])
        self.assertEqual(ifrs.build_document(ten_k, "SYNK", NOW), (None, "no-ifrs"))

    def test_skip_reasons_of_the_us_gaap_basis(self):
        self.assertEqual(ifrs.build_us_gaap_document(twd_concepts(), "SYNA", NOW), (None, "no-us-gaap"))
        self.assertEqual(ifrs.build_us_gaap_document(None, "SYNA", NOW), (None, "no-us-gaap"))
        self.assertEqual(ifrs.build_us_gaap_document(cny_us_gaap_concepts(), "2330.TW", NOW), (None, "not-us-key"))
        short = foreign_gaap_payload("1016", "Synthetic Short", {"Revenues": {CNY: [
            point("2024-12-31", 5, "2025-03-31", "0018", "20-F", start="2024-01-01")]}})
        self.assertEqual(ifrs.build_us_gaap_document(short, "SYNE", NOW), (None, "short-history"))
        junk = foreign_gaap_payload("1017", "Synthetic Junk", {"Revenues": {CNY: [
            point("2024-12-31", 9, "2025-03-31", "0019", "20-F", start="2024-12-31")]}})
        self.assertEqual(ifrs.build_us_gaap_document(junk, "SYNJ", NOW), (None, "us-gaap-no-usable-facts"))

    def test_ifrs_basis_still_ignores_us_gaap_facts(self):
        self.assertEqual(ifrs.build_document(cny_us_gaap_concepts(), "BABA", NOW), (None, "no-ifrs"))

    def test_round_trip_through_the_stored_reader(self):
        stored = fin.read_sections(json.loads(json.dumps(self.document)))
        self.assertEqual(stored["currency"], CNY)
        self.assertEqual(stored["annual"][0]["end"], "2020-12-31")


class UsGaapStateTests(unittest.TestCase):
    """The --foreign check state: answers recorded under older rules and still "no-ifrs" are asked again."""

    def test_only_a_no_ifrs_answer_from_older_rules_is_stale(self):
        self.assertTrue(ifrs.is_stale({"result": "skipped", "detail": "no-ifrs", "checkedAt": "2026-09-01"}))
        self.assertFalse(ifrs.is_stale({"result": "skipped", "detail": "no-ifrs", "rules": ifrs.FOREIGN_RULES}))
        self.assertFalse(ifrs.is_stale({"result": "skipped", "detail": "no-us-gaap"}))
        self.assertFalse(ifrs.is_stale({"result": "skipped", "detail": "ifrs-no-usable-facts"}))
        self.assertFalse(ifrs.is_stale({"result": "no-facts", "detail": "404"}))
        self.assertFalse(ifrs.is_stale("skipped"))

    def test_recorded_answers_carry_the_rules(self):
        state = {"version": ifrs.STATE_VERSION, "checked": {}}
        ifrs.record_checked(state, "SYNA", "skipped", "no-us-gaap", "2026-10-15")
        self.assertEqual(state["checked"]["SYNA"], {"result": "skipped", "detail": "no-us-gaap",
                                                    "checkedAt": "2026-10-15", "rules": ifrs.FOREIGN_RULES})

    def test_plan_splits_stale_answers_out_as_retry(self):
        universe = {"markets": {"US": {"fields": ["symbol", "marketCap"], "rows": [
            ["AAA", 3], ["BBB", 2], ["CCC", 1]]}}}
        progress = {"issuers": {"0000000001": {"status": "foreign-filer"}, "0000000002": {"status": "foreign-filer"},
                                "0000000003": {"status": "non-usd"}},
                    "symbols": {"AAA": {"cik": "0000000001"}, "BBB": {"cik": "0000000002"},
                                "CCC": {"cik": "0000000003"}}, "tickerMap": {}}
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        state = {"version": ifrs.STATE_VERSION, "checked": {
            "AAA": {"result": "skipped", "detail": "no-ifrs", "checkedAt": "2026-09-01"},
            "BBB": {"result": "skipped", "detail": "no-ifrs", "checkedAt": "2026-10-01", "rules": ifrs.FOREIGN_RULES},
            "CCC": {"result": "no-facts", "detail": "404", "checkedAt": "2026-09-01"}}}
        plan = ifrs.plan_foreign(universe, progress, out, state)
        self.assertEqual(plan["checked"], ["BBB", "CCC"])
        self.assertEqual(plan["todo"], [("AAA", "0000000001")])
        self.assertEqual(plan["retry"], ["AAA"])


class ForeignGaapWiringTests(unittest.TestCase):
    """seed_financials_sec --foreign with the US GAAP fallback, its state, and --due leaving foreign reporters out."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = os.path.join(self.tmp, "financials")
        self.report = os.path.join(self.tmp, "report")
        self.state_path = os.path.join(self.tmp, "state", "sec-ifrs-state.json")
        os.makedirs(os.path.join(self.report, "fundamentals"))
        os.makedirs(os.path.dirname(self.state_path))
        os.makedirs(self.out)
        universe = {"markets": {"US": {"fields": ["symbol", "name", "marketCap"], "rows": [
            ["SYNA", "Synthetic A", 5e9], ["SYNB", "Synthetic B", 1e9], ["SYNC", "C", 2e9], ["USAA", "US A", 7e9]]}}}
        progress = {
            "issuers": {"0000001001": {"status": "foreign-filer"}, "0000001002": {"status": "non-usd"},
                        "0000001003": {"status": "foreign-filer"}, "0000001004": {"status": "short-history"}},
            "symbols": {"SYNA": {"cik": "0000001001"}, "SYNB": {"cik": "0000001002"},
                        "SYNC": {"cik": "0000001003"}, "USAA": {"cik": "0000001004"}},
            "tickerMap": {"SYNA": "0000001001", "SYNB": "0000001002", "SYNC": "0000001003", "USAA": "0000001004"},
        }
        with open(os.path.join(self.report, "universe.json"), "w") as file:
            json.dump(universe, file)
        with open(os.path.join(self.report, "fundamentals", "us-market-progress.json"), "w") as file:
            json.dump(progress, file)

    def write_state(self, checked):
        with open(self.state_path, "w") as file:
            json.dump({"version": ifrs.STATE_VERSION, "checked": checked}, file)

    def run_main(self, client, args=()):
        argv = ["--no-splits", "--report-dir", self.report, "--out", self.out, "--state", self.state_path, *args]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = sec.main(argv, client=client, now=NOW, environ={})
        return code, buffer.getvalue()

    def test_foreign_run_writes_the_us_gaap_document_when_there_is_no_ifrs(self):
        client = FakeClient({"0000001001": cny_us_gaap_concepts(), "0000001002": eur_us_gaap_concepts()})
        code, text = self.run_main(client, ("--foreign", "--tickers", "SYNA,SYNB"))
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertEqual(client.requests, 2)
        doc = load_json(os.path.join(self.out, "SYNA.json"))
        self.assertEqual((doc["currency"], doc["foreignReporter"]), (CNY, True))
        self.assertEqual(doc["source"], "SEC EDGAR companyfacts (US GAAP, foreign filer)")
        self.assertIn("foreign-gaap", doc["quality"]["issues"])
        self.assertEqual(load_json(os.path.join(self.out, "SYNB.json"))["currency"], EUR)
        state = load_json(self.state_path)
        self.assertEqual((state["checked"]["SYNA"]["result"], state["checked"]["SYNA"]["rules"]), ("written", 2))
        self.assertIn("SYNA written", text)

    def test_ifrs_facts_win_and_unusable_ifrs_is_not_read_as_us_gaap(self):
        concepts = cny_us_gaap_concepts()
        concepts["facts"]["ifrs-full"] = {"Revenue": {"units": {CNY: [
            point("2024-12-31", 9, "2025-03-31", "0008", "20-F", start="2024-12-31")]}}}
        outcome = sec.seed_ticker("SYNA", {"SYNA": "0000001001"}, FakeClient({"0000001001": concepts}),
                                  self.out, False, NOW, None, foreign=True)
        self.assertEqual((outcome["result"], outcome["detail"]), ("skipped", "ifrs-no-usable-facts"))
        self.assertFalse(os.path.exists(os.path.join(self.out, "SYNA.json")))

    def test_dry_run_counts_the_stale_answers_asked_again(self):
        self.write_state({"SYNA": {"result": "skipped", "detail": "no-ifrs", "checkedAt": "2026-09-01"},
                          "SYNB": {"result": "skipped", "detail": "no-us-gaap", "checkedAt": "2026-09-01"}})
        code, text = self.run_main(NoNetworkClient({}), ("--foreign", "--dry-run"))
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertIn("--foreign: 3 candidate ticker(s)", text)
        self.assertIn("0 already have a file; 1 checked before", text)
        self.assertIn("2 to check (1 of them asked again, rules 2)", text)
        self.assertIn("Dry run: 2 ticker(s) would be checked", text)

    def test_stale_answer_is_asked_once_and_then_recorded_under_rules_two(self):
        self.write_state({"SYNA": {"result": "skipped", "detail": "no-ifrs", "checkedAt": "2026-09-01"},
                          "SYNB": {"result": "skipped", "detail": "no-ifrs", "checkedAt": "2026-09-01",
                                   "rules": ifrs.FOREIGN_RULES}})
        client = FakeClient({"0000001001": cny_us_gaap_concepts()})
        code, text = self.run_main(client, ("--foreign",))
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertEqual(client.asked, ["0000001001", "0000001003"])      # SYNB is final; SYNC has no companyfacts
        state = load_json(self.state_path)
        self.assertEqual((state["checked"]["SYNA"]["result"], state["checked"]["SYNA"]["rules"]), ("written", 2))
        self.assertEqual(state["checked"]["SYNB"]["detail"], "no-ifrs")
        self.assertEqual(state["checked"]["SYNC"]["result"], "no-facts")

    def test_due_leaves_the_foreign_reporters_out_and_counts_them(self):
        us_doc, _ = sec.merge_into_document(None, sec.extract_periods(us_gaap_payload()), "USAA", NOW)
        for key, doc in (("SYNA", ifrs.build_document(twd_concepts(), "SYNA", NOW)[0]), ("USAA", us_doc)):
            with open(os.path.join(self.out, key + ".json"), "w") as file:
                json.dump(doc, file)
        with open(os.path.join(self.out, "index.json"), "w") as file:
            json.dump({"version": fin.VERSION, "items": {}}, file)
        code, text = self.run_main(NoNetworkClient({}), ("--due", "--dry-run"))
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertIn("due now 1 (never checked: 1)", text)
        self.assertIn("left out: not US 0, foreign reporter 1, no SEC periods 0, unreadable 0", text)
        self.assertIn("USAA", text)
        self.assertNotIn("SYNA", text.split("Dry run:")[1])

    def test_due_run_makes_no_request_for_foreign_reporters_only(self):
        with open(os.path.join(self.out, "SYNA.json"), "w") as file:
            json.dump(ifrs.build_document(twd_concepts(), "SYNA", NOW)[0], file)
        with open(os.path.join(self.out, "index.json"), "w") as file:
            json.dump({"version": fin.VERSION, "items": {}}, file)
        code, text = self.run_main(NoNetworkClient({}), ("--due",))
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertIn("Nothing is due for a re-check.", text)


class DailyForeignCodesTests(unittest.TestCase):
    """The Yahoo daily run keeps the US GAAP foreign-filer codes like the IFRS ones."""

    def test_daily_run_keeps_the_foreign_flag_and_the_us_gaap_codes(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "financials", "yahoo_us_full.json"), encoding="utf-8") as file:
            payload = json.load(file)
        doc, _ = fin.update_document(None, payload, "AAPL", NOW)
        doc["foreignReporter"] = True
        doc["quality"]["issues"] = sorted(set(doc["quality"]["issues"]) | {"foreign-gaap", "foreign-gaap-fallback"})
        updated, _ = fin.update_document(copy.deepcopy(doc), payload, "AAPL", NOW + timedelta(days=1))
        self.assertIs(updated["foreignReporter"], True)
        self.assertIn("foreign-gaap", updated["quality"]["issues"])
        self.assertIn("foreign-gaap-fallback", updated["quality"]["issues"])


if __name__ == "__main__":
    unittest.main()

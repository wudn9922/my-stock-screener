"""
Financial statement history (財報分析中心): atlas/scripts/build_financials.py.

Offline; every Yahoo payload is synthetic (tests/financials_synth.py writes them to
tests/fixtures/financials/). Run: python3 -m unittest discover -s tests
"""
import copy
import itertools
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
TESTS = os.path.join(ROOT, "tests")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "financials")
for path in (SCRIPTS, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import build_financials as fin  # noqa: E402
import financials_synth as synth  # noqa: E402

NOW = synth.SAMPLE_NOW                      # 2026-10-11 09:00 UTC
TODAY = NOW.date()
BN = synth.BN


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as file:
        return json.load(file)


def at(day, hour=9):
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def values(section, field):
    return dict(zip(section["end"], section["values"].get(field) or [None] * len(section["end"])))


def build(payload, key, previous=None, now=NOW, fetch_splits=None):
    document, info = fin.update_document(previous, payload, key, now, fetch_splits=fetch_splits)
    return document, info


def statement(rows, end, ratios):
    for row_end, revenue, shares in rows:
        if row_end == end:
            return synth.statement(revenue, shares, ratios)
    raise KeyError(end)


def canonical(document):
    return json.dumps(document, sort_keys=False, separators=(",", ":"))


# ---------------------------------------------------------------------------------------------
# Keys and parsing
# ---------------------------------------------------------------------------------------------
class KeyTest(unittest.TestCase):
    def test_financials_key(self):
        self.assertEqual(fin.financials_key("brk.b"), "BRK-B")
        self.assertEqual(fin.financials_key(" aapl "), "AAPL")
        self.assertEqual(fin.financials_key("2330.TW"), "2330.TW")
        self.assertEqual(fin.financials_key("6488.two"), "6488.TWO")
        self.assertIsNone(fin.financials_key("^TWII"))
        self.assertIsNone(fin.financials_key("index"))       # would clash with index.json
        self.assertIsNone(fin.financials_key("A/B"))
        self.assertIsNone(fin.financials_key(None))
        self.assertEqual(fin.market_of("2330.TW"), "TW")
        self.assertEqual(fin.market_of("6488.TWO"), "TW")
        self.assertEqual(fin.market_of("BRK-B"), "US")

    def test_clean_value_never_negative_zero(self):
        for raw in (-0.00001, -0.0, 0.00001):
            value = fin.clean_value(raw)
            self.assertEqual(value, 0)
            self.assertNotIn("-", json.dumps(value))
        self.assertEqual(fin.clean_value(-1.23456), -1.2346)


class ParseTest(unittest.TestCase):
    def test_us_full_filters_period_types_currency_and_garbage(self):
        parsed = fin.parse_timeseries(fixture("yahoo_us_full.json"), TODAY)
        self.assertEqual(parsed["currency"], "USD")
        annual, quarterly = parsed["annual"], parsed["quarterly"]
        self.assertEqual(sorted(annual), [end for end, _, _ in synth.US_ANNUAL])
        self.assertEqual(sorted(quarterly), [end for end, _, _ in synth.US_QUARTERLY])
        # TTM point inside annualTotalRevenue and 12M / 6M points inside quarterly series dropped
        self.assertEqual(quarterly["2025-09-27"]["revenue"], synth.money(103 * BN))
        self.assertEqual(quarterly["2025-12-27"]["revenue"], synth.money(140 * BN))
        # EUR operating cash flow dropped; the rest of that quarter is kept
        self.assertIsNone(quarterly["2025-03-29"].get("operatingCashFlow"))
        self.assertEqual(quarterly["2025-03-29"]["revenue"], synth.money(95 * BN))
        # Net income to common holders missing → NetIncome
        fy22 = statement(synth.US_ANNUAL, "2022-09-24", synth.US_RATIOS)
        self.assertEqual(annual["2022-09-24"]["netIncome"], fy22["NetIncome"])
        # Cash + short-term investments missing → cash and equivalents
        q = statement(synth.US_QUARTERLY, "2024-12-28", synth.US_RATIOS)
        self.assertEqual(quarterly["2024-12-28"]["cash"], q["CashAndCashEquivalents"])
        q = statement(synth.US_QUARTERLY, "2025-03-29", synth.US_RATIOS)
        self.assertEqual(quarterly["2025-03-29"]["cash"], q["CashCashEquivalentsAndShortTermInvestments"])
        # Outflows stored positive
        self.assertEqual(quarterly["2025-03-29"]["capex"], -q["CapitalExpenditure"])
        self.assertEqual(quarterly["2025-03-29"]["dividendsPaid"], -q["CashDividendsPaid"])
        self.assertEqual(quarterly["2025-03-29"]["buybacks"], -q["RepurchaseOfCapitalStock"])
        self.assertFalse(parsed["capexSign"])
        # Bad date / string value / null point ignored, nothing in the future
        self.assertNotIn("2025-13-45", quarterly)
        self.assertEqual(
            quarterly["2026-06-27"]["netIncome"],
            statement(synth.US_QUARTERLY, "2026-06-27", synth.US_RATIOS)["NetIncomeCommonStockholders"],
        )
        # Meta-only annualInterestExpense → no annual interest expense at all
        self.assertTrue(all("interestExpense" not in row for row in annual.values()))
        self.assertIn("interestExpense", quarterly["2025-03-29"])

    def test_tw_currency_guard(self):
        parsed = fin.parse_timeseries(fixture("yahoo_tw_2330.json"), TODAY)
        self.assertEqual(parsed["currency"], "TWD")
        fy25 = statement(synth.TW_ANNUAL, "2025-12-31", synth.TW_RATIOS)
        # The duplicate USD point (9.99) is dropped
        self.assertEqual(parsed["annual"]["2025-12-31"]["epsDiluted"], fy25["DilutedEPS"])

    def test_required_currency(self):
        parsed = fin.parse_timeseries(fixture("yahoo_tw_2330.json"), TODAY, currency="USD")
        self.assertEqual(parsed["currency"], "USD")
        self.assertEqual(parsed["detected"], "TWD")
        # Only the stray USD EPS point survives (share counts alone do not make a period)
        self.assertEqual(list(parsed["annual"]), ["2025-12-31"])
        self.assertEqual(parsed["annual"]["2025-12-31"], {"epsDiluted": 9.99, "dilutedShares": 25930000000})

    def test_foreign_reporter(self):
        parsed = fin.parse_timeseries(fixture("yahoo_foreign_tsm.json"), TODAY)
        self.assertEqual(parsed["currency"], "TWD")
        self.assertIn("epsDiluted", parsed["annual"]["2025-12-31"])
        self.assertNotIn("epsDiluted", parsed["quarterly"]["2025-12-31"])
        # Share counts are kept whatever currency code they carry
        self.assertEqual(parsed["quarterly"]["2025-12-31"]["dilutedShares"], 25930000000)

    def test_empty_and_garbled(self):
        for payload in (
            fixture("yahoo_empty.json"), fixture("yahoo_garbled.json"), None, [], "x", {"timeseries": 5},
            {"timeseries": {"result": None, "error": {"code": "Not Found"}}},
        ):
            parsed = fin.parse_timeseries(payload, TODAY)
            self.assertEqual((parsed["annual"], parsed["quarterly"]), ({}, {}), payload)
            document, info = build(payload, "XYZ")
            self.assertIsNone(document)
            self.assertEqual(info["status"], "empty")

    def test_parse_splits(self):
        self.assertEqual(fin.parse_splits(fixture("yahoo_chart_split.json")),
                         [["2019-06-10", 1.1], ["2026-06-10", 10]])
        self.assertEqual(fin.parse_splits({"chart": {"result": None}}), [])
        self.assertEqual(fin.parse_splits(None), [])
        self.assertTrue(fin.split_chart_is_complete(fixture("yahoo_chart_split.json"), "SPLT", "2020-01-01", "2026-01-01"))
        self.assertFalse(fin.split_chart_is_complete(fixture("yahoo_chart_split.json"), "OTHER", "2020-01-01", "2026-01-01"))
        self.assertFalse(fin.split_chart_is_complete(fixture("yahoo_chart_split.json"), "SPLT", "1999-01-01", "2026-01-01"))
        self.assertFalse(fin.split_chart_is_complete({"junk": 1}, "SPLT", "2020-01-01", "2026-01-01"))
        self.assertFalse(fin.split_chart_is_complete({"chart": {"result": None}}, "SPLT", "2020-01-01", "2026-01-01"))
        chart = fixture("yahoo_chart_split.json")
        result = chart["chart"]["result"][0]
        result["timestamp"] = []
        self.assertFalse(fin.split_chart_is_complete(chart, "SPLT", "2020-01-01", "2026-01-01"))
        result["timestamp"] = [946684800, 1792108800]
        result["events"] = {}
        self.assertFalse(fin.split_chart_is_complete(chart, "SPLT", "2020-01-01", "2026-01-01"))
        result["events"] = {"splits": {}}
        self.assertTrue(fin.split_chart_is_complete(chart, "SPLT", "2020-01-01", "2026-01-01"))
        reverse = synth.split_chart([(1781098200, 1.0, 20.0)])
        self.assertEqual(fin.parse_splits(reverse), [["2026-06-10", 0.05]])
        small_split = synth.split_chart([(1781098200, 5.0, 4.0)])
        self.assertEqual(fin.parse_splits(small_split), [["2026-06-10", 1.25]])
        small_reverse_split = synth.split_chart([(1781098200, 4.0, 5.0)])
        self.assertEqual(fin.parse_splits(small_reverse_split), [["2026-06-10", 0.8]])
        malformed = synth.split_chart([(1781098200, 5.0, 4.0)])
        split_event = next(iter(malformed["chart"]["result"][0]["events"]["splits"].values()))
        split_event["numerator"] = None
        self.assertFalse(fin.split_chart_is_complete(
            malformed, "SPLT", "2020-01-01", "2026-01-01", as_of="2026-10-17"))
        with self.assertRaisesRegex(ValueError, "invalid date or ratio"):
            fin.parse_splits(malformed)
        self.assertFalse(fin.split_chart_is_complete(
            fixture("yahoo_chart_split.json"), "SPLT", "2020-01-01", "2026-01-01",
            as_of="2026-11-01"))

    def test_parse_splits_is_bound_to_the_expected_chart_result_symbol(self):
        chart = fixture("yahoo_chart_split.json")
        other = copy.deepcopy(chart["chart"]["result"][0])
        other["meta"]["symbol"] = "OTHER"
        other["events"]["splits"] = {"other": {"date": 1781098200, "numerator": 3, "denominator": 1}}
        chart["chart"]["result"].append(other)
        self.assertEqual(fin.parse_splits(chart, expected_symbol="SPLT"),
                         [["2019-06-10", 1.1], ["2026-06-10", 10]])
        self.assertTrue(fin.split_chart_is_complete(chart, "SPLT", "2020-01-01", "2026-01-01"))
        duplicate = copy.deepcopy(chart["chart"]["result"][0])
        chart["chart"]["result"].append(duplicate)
        self.assertFalse(fin.split_chart_is_complete(chart, "SPLT", "2020-01-01", "2026-01-01"))
        with self.assertRaisesRegex(ValueError, "exactly one result"):
            fin.parse_splits(chart, expected_symbol="SPLT")


# ---------------------------------------------------------------------------------------------
# One document
# ---------------------------------------------------------------------------------------------
class DocumentTest(unittest.TestCase):
    def test_us_full_contract(self):
        document, info = build(fixture("yahoo_us_full.json"), "AAPL")
        self.assertEqual(info["status"], "updated")
        self.assertEqual(list(document)[:9], [
            "version", "symbol", "market", "currency", "foreignReporter", "generatedAt", "checkedAt",
            "source", "fyEndMonth",
        ])
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["symbol"], "AAPL")
        self.assertEqual(document["market"], "US")
        self.assertEqual(document["currency"], "USD")
        self.assertFalse(document["foreignReporter"])
        self.assertEqual(document["generatedAt"], "2026-10-11T09:00:00Z")
        self.assertEqual(document["checkedAt"], "2026-10-11")
        self.assertEqual(document["source"], "Yahoo Finance fundamentals-timeseries")
        self.assertEqual(document["fyEndMonth"], 9)
        annual, quarterly = document["annual"], document["quarterly"]
        self.assertEqual(annual["end"], ["2022-09-24", "2023-09-30", "2024-09-28", "2025-09-27"])
        self.assertEqual(annual["fy"], [2022, 2023, 2024, 2025])
        self.assertNotIn("fq", annual)
        self.assertNotIn("src", annual)
        self.assertEqual(quarterly["end"][0], "2024-12-28")
        self.assertEqual(quarterly["fy"], [2025, 2025, 2025, 2025, 2026, 2026, 2026])
        self.assertEqual(quarterly["fq"], [1, 2, 3, 4, 1, 2, 3])
        for section in (annual, quarterly):
            for field, column in section["values"].items():
                self.assertIn(field, fin.FIELDS)
                self.assertEqual(len(column), len(section["end"]), field)
        # Fields without any value are left out
        self.assertNotIn("interestExpense", annual["values"])
        self.assertEqual(values(annual, "revenue")["2025-09-27"], synth.money(416 * BN))
        self.assertEqual(document["split"], {"adjusted": False, "events": [], "eventsComplete": False,
                                             "eventsSource": None, "eventsCheckedAt": None})
        self.assertEqual(document["quality"]["level"], "good")
        self.assertEqual(document["quality"]["annualYears"], 4)
        self.assertEqual(document["quality"]["quarters"], 7)
        self.assertEqual(document["quality"]["issues"], [])
        # Strict JSON
        json.loads(json.dumps(document, allow_nan=False))

    def test_tw(self):
        document, _ = build(fixture("yahoo_tw_2330.json"), "2330.TW")
        self.assertEqual((document["market"], document["currency"]), ("TW", "TWD"))
        self.assertFalse(document["foreignReporter"])
        self.assertEqual(document["fyEndMonth"], 12)
        self.assertEqual(document["annual"]["fy"], [2022, 2023, 2024, 2025])
        self.assertEqual(document["quarterly"]["fq"], [1, 2, 3, 4, 1, 2])
        self.assertEqual(document["quarterly"]["fy"], [2025, 2025, 2025, 2025, 2026, 2026])
        self.assertEqual(document["quality"]["level"], "good")
        self.assertEqual(document["quality"]["issues"], [])

    def test_bank_without_gross_profit(self):
        document, _ = build(fixture("yahoo_bank.json"), "JPM")
        self.assertNotIn("grossProfit", document["annual"]["values"])
        self.assertNotIn("capex", document["annual"]["values"])
        self.assertIn("financial-sector-format", document["quality"]["issues"])
        self.assertEqual(document["quality"]["level"], "good")

    def test_foreign_reporter(self):
        document, _ = build(fixture("yahoo_foreign_tsm.json"), "TSM")
        self.assertEqual((document["market"], document["currency"]), ("US", "TWD"))
        self.assertTrue(document["foreignReporter"])
        self.assertIn("foreign-currency", document["quality"]["issues"])
        self.assertIn("epsDiluted", document["annual"]["values"])
        self.assertNotIn("epsDiluted", document["quarterly"]["values"])

    def test_capex_sign_and_negative_equity(self):
        payload = fixture("yahoo_us_full.json")
        for series in payload["timeseries"]["result"]:
            kind = series["meta"]["type"][0]
            if kind == "annualCapitalExpenditure":
                series[kind][-1]["reportedValue"]["raw"] = 5000000000
            if kind == "annualStockholdersEquity":
                series[kind][-1]["reportedValue"]["raw"] = -2000000000
        document, _ = build(payload, "AAPL")
        self.assertEqual(values(document["annual"], "capex")["2025-09-27"], 5000000000)
        self.assertIn("capex-sign", document["quality"]["issues"])
        self.assertIn("negative-equity", document["quality"]["issues"])

    def test_short_history_levels(self):
        payload = fixture("yahoo_tw_2330.json")
        for series in payload["timeseries"]["result"]:
            kind = series["meta"]["type"][0]
            if kind.startswith("annual") and series.get(kind):
                series[kind] = [p for p in series[kind] if p["asOfDate"] >= "2023-12-31"]
        document, _ = build(payload, "2330.TW")
        self.assertEqual(document["quality"]["annualYears"], 3)
        self.assertEqual(document["quality"]["level"], "partial")
        self.assertIn("short-history", document["quality"]["issues"])
        for series in payload["timeseries"]["result"]:
            kind = series["meta"]["type"][0]
            if kind.startswith("annual") and series.get(kind):
                series[kind] = [p for p in series[kind] if p["asOfDate"] >= "2024-12-31"]
        document, _ = build(payload, "2330.TW")
        self.assertEqual(document["quality"]["level"], "limited")

    def test_missing_cashflow_and_balance(self):
        payload = fixture("yahoo_tw_2330.json")
        payload["timeseries"]["result"] = [
            series for series in payload["timeseries"]["result"]
            if series["meta"]["type"][0] not in (
                "annualOperatingCashFlow", "annualStockholdersEquity", "annualTotalAssets"
            )
        ]
        document, _ = build(payload, "2330.TW")
        self.assertIn("missing-cashflow", document["quality"]["issues"])
        self.assertIn("missing-balance", document["quality"]["issues"])
        self.assertEqual(document["quality"]["level"], "partial")


# ---------------------------------------------------------------------------------------------
# Labels and reconciliation
# ---------------------------------------------------------------------------------------------
class LabelTest(unittest.TestCase):
    def test_fiscal_labels(self):
        self.assertEqual(fin.fiscal_year("2026-01-03"), 2025)      # 52/53-week year
        self.assertEqual(fin.fiscal_year("2026-01-25"), 2026)      # late-January fiscal year end
        self.assertEqual(fin.fiscal_year("2025-12-31"), 2025)
        self.assertEqual(fin.fy_end_month(["2023-12-30", "2025-01-03", "2025-12-27"]), 12)
        self.assertEqual(fin.fy_end_month(["2024-01-28", "2025-01-26", "2026-01-25"]), 1)
        self.assertIsNone(fin.fy_end_month([]))
        self.assertEqual(fin.fiscal_quarter("2025-04-27", 1), (2026, 1))
        self.assertEqual(fin.fiscal_quarter("2026-01-25", 1), (2026, 4))
        self.assertEqual(fin.fiscal_quarter("2024-12-28", 9), (2025, 1))
        self.assertEqual(fin.fiscal_quarter("2025-09-27", 9), (2025, 4))
        self.assertEqual(fin.fiscal_quarter("2026-01-02", 12), (2025, 4))
        self.assertEqual(fin.fiscal_quarter("2025-03-29", 12), (2025, 1))
        self.assertEqual(fin.fiscal_quarter("2025-06-30", 6), (2025, 4))
        self.assertEqual(fin.fiscal_quarter("2025-09-30", 6), (2026, 1))

    def test_quarter_labels_without_annual_data(self):
        payload = fixture("yahoo_us_full.json")
        payload["timeseries"]["result"] = [
            s for s in payload["timeseries"]["result"] if not s["meta"]["type"][0].startswith("annual")
        ]
        document, _ = build(payload, "AAPL")
        self.assertEqual(document["annual"]["end"], [])
        self.assertEqual(document["fyEndMonth"], 12)
        self.assertEqual(document["quarterly"]["fq"], [None] * 7)
        self.assertEqual(document["quarterly"]["fy"][0], 2024)
        self.assertEqual(document["quality"]["level"], "limited")

    def test_ytd_suspected(self):
        document, _ = build(fixture("yahoo_ytd.json"), "2454.TW")
        issues = document["quality"]["issues"]
        self.assertIn("quarter-annual-mismatch", issues)
        self.assertIn("quarterly-ytd-suspected", issues)
        self.assertEqual(document["quality"]["level"], "limited")
        # Flagged only; the quarters are not split apart
        self.assertEqual(values(document["quarterly"], "revenue")["2025-06-30"], synth.money(290 * BN))

    def test_mismatch_without_ytd(self):
        payload = fixture("yahoo_tw_2330.json")
        for series in payload["timeseries"]["result"]:
            if series["meta"]["type"][0] == "quarterlyTotalRevenue":
                for point in series["quarterlyTotalRevenue"]:
                    if point["asOfDate"] == "2025-06-30":
                        point["reportedValue"]["raw"] = 700 * BN
        document, _ = build(payload, "2330.TW")
        self.assertIn("quarter-annual-mismatch", document["quality"]["issues"])
        self.assertNotIn("quarterly-ytd-suspected", document["quality"]["issues"])
        # Within 5%: no mismatch
        for series in payload["timeseries"]["result"]:
            if series["meta"]["type"][0] == "quarterlyTotalRevenue":
                for point in series["quarterlyTotalRevenue"]:
                    if point["asOfDate"] == "2025-06-30":
                        point["reportedValue"]["raw"] = 860 * BN
        document, _ = build(payload, "2330.TW")
        self.assertNotIn("quarter-annual-mismatch", document["quality"]["issues"])


# ---------------------------------------------------------------------------------------------
# Merging across runs
# ---------------------------------------------------------------------------------------------
class MergeTest(unittest.TestCase):
    def setUp(self):
        self.first, _ = build(fixture("yahoo_us_prev.json"), "AAPL", now=at(date(2025, 8, 1)))

    def test_accumulates_and_adopts_new_period_end(self):
        document, _ = build(fixture("yahoo_us_full.json"), "AAPL", previous=self.first)
        self.assertEqual(
            document["annual"]["end"],
            ["2021-09-25", "2022-09-24", "2023-09-30", "2024-09-28", "2025-09-27"],
        )
        self.assertEqual(document["annual"]["fy"], [2021, 2022, 2023, 2024, 2025])
        self.assertEqual(document["quarterly"]["end"][:3], ["2024-06-29", "2024-09-28", "2024-12-28"])
        self.assertEqual(len(document["quarterly"]["end"]), 9)
        # Values Yahoo no longer returns are kept (annual interest expense, the FY2021 year)
        interest = values(document["annual"], "interestExpense")
        self.assertIsNotNone(interest["2023-09-30"])
        self.assertIsNone(interest["2025-09-27"])
        self.assertEqual(values(document["annual"], "revenue")["2021-09-25"], synth.money(366 * BN))
        self.assertEqual(document["quality"]["annualYears"], 5)
        self.assertNotIn("restated", document["quality"]["issues"])

    def test_same_payload_twice_is_identical(self):
        payload = fixture("yahoo_us_full.json")
        once, _ = build(payload, "AAPL", previous=self.first)
        twice, info = build(payload, "AAPL", previous=copy.deepcopy(once))
        self.assertEqual(canonical(once), canonical(twice))
        self.assertEqual(info["restated"], 0)

    def test_restated(self):
        current, _ = build(fixture("yahoo_us_full.json"), "AAPL", previous=self.first)
        restated, info = build(fixture("yahoo_us_restated.json"), "AAPL", previous=current)
        self.assertEqual(info["restated"], 1)
        self.assertIn("restated", restated["quality"]["issues"])
        revenue = values(restated["annual"], "revenue")
        self.assertEqual(revenue["2024-09-28"], synth.money(391 * BN * 1.03))
        self.assertEqual(revenue["2023-09-30"], synth.money(383 * BN * 1.01))     # +1%: not flagged
        self.assertEqual(restated["quality"]["revisions"], [{
            "kind": "restated", "period": "annual", "end": "2024-09-28", "field": "revenue",
            "old": synth.money(391 * BN), "new": synth.money(391 * BN * 1.03), "seen": "2026-10-11",
        }])
        # The flag survives a later run with the same data
        again, info = build(fixture("yahoo_us_restated.json"), "AAPL", previous=copy.deepcopy(restated),
                            now=NOW + timedelta(days=4))
        self.assertEqual(info["restated"], 0)
        self.assertIn("restated", again["quality"]["issues"])
        self.assertEqual(again["quality"]["revisions"], restated["quality"]["revisions"])

    def test_merge_section_limits_and_matching(self):
        old = [fin.period("20%02d-12-31" % year, {"revenue": year}) for year in range(0, 25)]
        new = [fin.period("2025-01-03", {"revenue": 25.0, "netIncome": 1})]
        merged, revisions = fin.merge_section(old, new, "annual", "2026-10-11")
        self.assertEqual(len(merged), fin.KEEP_ANNUAL)
        self.assertEqual(merged[-1]["end"], "2025-01-03")           # ±5 days: same period, new date
        self.assertEqual(merged[-1]["values"], {"revenue": 25, "netIncome": 1})
        self.assertEqual(len(revisions), 1)                          # 24 → 25 is +4%
        # Two new points within 5 days of each other: one period, the later date
        merged, _ = fin.merge_section([], [
            fin.period("2025-12-28", {"revenue": 1}), fin.period("2025-12-31", {"netIncome": 2}),
        ], "quarterly", "2026-10-11")
        self.assertEqual([(p["end"], p["values"]) for p in merged],
                         [("2025-12-31", {"revenue": 1, "netIncome": 2})])

    def test_currency_change_keeps_history(self):
        current, _ = build(fixture("yahoo_tw_2330.json"), "2330.TW")
        changed = fixture("yahoo_tw_2330.json")
        for series in changed["timeseries"]["result"]:
            for point in series.get(series["meta"]["type"][0]) or []:
                point["currencyCode"] = "USD"
        document, info = build(changed, "2330.TW", previous=copy.deepcopy(current),
                               now=NOW + timedelta(days=50))
        self.assertEqual(document["currency"], "TWD")
        self.assertEqual(document["annual"], current["annual"])
        self.assertIn("currency-changed", document["quality"]["issues"])
        self.assertTrue(info["currencyChanged"])


# ---------------------------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------------------------
class SplitTest(unittest.TestCase):
    def setUp(self):
        self.before, _ = build(fixture("yahoo_split_before.json"), "SPLT", now=at(date(2026, 6, 1)))
        self.chart_calls = 0

    def chart(self, payload=None):
        def fetch():
            self.chart_calls += 1
            return payload if payload is not None else fixture("yahoo_chart_split.json")
        return fetch

    def assert_on_post_split_basis(self, document):
        for section in (document["annual"], document["quarterly"]):
            for end, shares in values(section, "dilutedShares").items():
                self.assertGreater(shares, 20e9, end)

    def test_unadjusted_history_is_adjusted(self):
        document, info = build(fixture("yahoo_split_after_unadjusted.json"), "SPLT",
                               previous=copy.deepcopy(self.before), fetch_splits=self.chart())
        self.assertEqual(self.chart_calls, 1)
        self.assertEqual(document["split"], {"adjusted": True,
                                             "events": [["2019-06-10", 1.1], ["2026-06-10", 10]],
                                             "eventsComplete": True,
                                             "eventsSource": "Yahoo Finance chart range=max split events",
                                             "eventsCheckedAt": TODAY.isoformat(), "eventsSymbol": "SPLT",
                                             "eventsCoverageStart": "2000-01-01", "eventsCoverageEnd": "2026-10-16",
                                             "eventsParserVersion": 4})
        self.assertIn("split-adjusted", document["quality"]["issues"])
        self.assert_on_post_split_basis(document)
        old = statement(synth.SPLIT_QUARTERLY, "2025-04-27", synth.SPLIT_RATIOS)
        self.assertAlmostEqual(values(document["quarterly"], "epsDiluted")["2025-04-27"],
                               old["DilutedEPS"] / 10, places=4)
        self.assertEqual(values(document["quarterly"], "dilutedShares")["2025-04-27"],
                         old["DilutedAverageShares"] * 10)
        new = statement([synth.SPLIT_NEW_QUARTER], "2026-07-26", synth.SPLIT_RATIOS)
        self.assertEqual(values(document["quarterly"], "epsDiluted")["2026-07-26"], new["DilutedEPS"])
        # Next run with the same (still unadjusted) payload: same file, no chart request needed
        again, _ = build(fixture("yahoo_split_after_unadjusted.json"), "SPLT",
                         previous=copy.deepcopy(document), fetch_splits=self.chart())
        self.assertEqual(self.chart_calls, 1)
        self.assertEqual(canonical(again), canonical(document))

    def test_already_adjusted_periods_are_left_alone(self):
        document, _ = build(fixture("yahoo_split_after_adjusted.json"), "SPLT",
                            previous=copy.deepcopy(self.before), fetch_splits=self.chart())
        self.assertEqual(self.chart_calls, 1)
        self.assert_on_post_split_basis(document)
        # Periods only in the stored file (Yahoo dropped them) are adjusted ...
        old = statement(synth.SPLIT_ANNUAL, "2023-01-29", synth.SPLIT_RATIOS)
        self.assertAlmostEqual(values(document["annual"], "epsDiluted")["2023-01-29"],
                               old["DilutedEPS"] / 10, places=4)
        # ... the ones Yahoo restated keep Yahoo's values exactly
        restated = statement(synth.times_ten(synth.SPLIT_ANNUAL), "2024-01-28", synth.SPLIT_RATIOS)
        self.assertEqual(values(document["annual"], "epsDiluted")["2024-01-28"], restated["DilutedEPS"])
        self.assertEqual(values(document["annual"], "dilutedShares")["2024-01-28"],
                         restated["DilutedAverageShares"])
        self.assertTrue(document["split"]["adjusted"])

    def test_no_chart_request_without_a_share_jump(self):
        document, info = build(fixture("yahoo_tw_2330.json"), "2330.TW", fetch_splits=self.chart())
        self.assertEqual(self.chart_calls, 0)
        self.assertFalse(document["split"]["adjusted"])

    def test_chart_failure_leaves_data_unadjusted(self):
        def broken():
            self.chart_calls += 1
            raise RuntimeError("HTTP 429")
        document, info = build(fixture("yahoo_split_after_unadjusted.json"), "SPLT",
                               previous=copy.deepcopy(self.before), fetch_splits=broken)
        self.assertEqual(self.chart_calls, 1)
        self.assertEqual(info["chart"], "failed")
        self.assertEqual(document["split"], {"adjusted": False, "events": [], "eventsComplete": False,
                                             "eventsSource": "Yahoo Finance chart range=max split events",
                                             "eventsCheckedAt": TODAY.isoformat()})
        self.assertEqual(values(document["quarterly"], "dilutedShares")["2026-04-26"], 2440000000)

    def test_small_stock_dividend_is_ignored(self):
        chart = synth.split_chart([(1781098200, 11.0, 10.0)])
        document, _ = build(fixture("yahoo_split_after_unadjusted.json"), "SPLT",
                            previous=copy.deepcopy(self.before), fetch_splits=self.chart(chart))
        self.assertEqual(document["split"], {"adjusted": False, "events": [["2026-06-10", 1.1]], "eventsComplete": True,
                                             "eventsSource": "Yahoo Finance chart range=max split events",
                                             "eventsCheckedAt": TODAY.isoformat(), "eventsSymbol": "SPLT",
                                             "eventsCoverageStart": "2000-01-01", "eventsCoverageEnd": "2026-10-16",
                                             "eventsParserVersion": 4})

    def test_two_splits_cascade(self):
        rows = [
            fin.period("2019-12-31", {"dilutedShares": 100, "epsDiluted": 40.0}),
            fin.period("2021-12-31", {"dilutedShares": 400, "epsDiluted": 10.0}),
            fin.period("2025-12-31", {"dilutedShares": 4000, "epsDiluted": 1.0}),
        ]
        adjusted = fin.adjust_splits([rows], [["2020-08-31", 4], ["2024-06-10", 10]])
        self.assertTrue(adjusted)
        self.assertEqual([p["values"]["dilutedShares"] for p in rows], [4000, 4000, 4000])
        self.assertEqual([p["values"]["epsDiluted"] for p in rows], [1.0, 1.0, 1.0])


# ---------------------------------------------------------------------------------------------
# SEC provenance (periods written by seed_financials_sec.py)
# ---------------------------------------------------------------------------------------------
def sec_document():
    """AAPL file as the SEC seeder would write it: FY2015–FY2024, quarters to 2025-06-28."""
    years = list(range(2015, 2025))
    ends = ["%d-09-%02d" % (year, 24 + year % 5) for year in years]
    ends[-3:] = ["2022-09-24", "2023-09-30", "2024-09-28"]
    revenue = [200e9 + 10e9 * i for i in range(len(years))]
    revenue[-3] = 394e9 * 1.05            # FY2022: Yahoo differs by ~5%
    revenue[-2] = 383e9
    revenue[-1] = 391e9
    annual = {
        "end": ends,
        "fy": years,
        "src": ["sec"] * len(years),
        "values": {
            "revenue": revenue,
            "netIncome": [50e9] * len(years),
            "buybacks": [70e9] * (len(years) - 1) + [None],
            "secOnlyField": [1] * len(years),
        },
        "form": ["10-K"] * len(years),
    }
    quarter_ends = ["2024-06-29", "2024-09-28", "2024-12-28", "2025-03-29", "2025-06-28"]
    quarterly = {
        "end": quarter_ends,
        "fy": [2024, 2024, 2025, 2025, 2025],
        "fq": [3, 4, 1, 2, 3],
        "src": ["sec"] * 5,
        "values": {"revenue": [86e9, 95e9, 124e9, 95e9, 94e9], "netIncome": [21e9, 15e9, 36e9, 25e9, 23e9]},
    }
    return {
        "version": 1, "symbol": "AAPL", "market": "US", "currency": "USD", "foreignReporter": False,
        "generatedAt": "2026-09-01T00:00:00Z", "checkedAt": "2026-09-01",
        "source": "SEC EDGAR companyfacts + Yahoo Finance", "fyEndMonth": 9,
        "annual": annual, "quarterly": quarterly,
        "split": {"adjusted": False, "events": []},
        "quality": {"level": "good", "annualYears": 10, "quarters": 5, "issues": []},
        "secCik": "0000320193",
    }


class SecProvenanceTest(unittest.TestCase):
    def test_yahoo_never_overwrites_sec_periods(self):
        seeded = sec_document()
        document, info = build(fixture("yahoo_us_full.json"), "AAPL", previous=copy.deepcopy(seeded))
        annual, quarterly = document["annual"], document["quarterly"]
        self.assertEqual(document["source"], "SEC EDGAR companyfacts + Yahoo Finance")
        self.assertEqual(document["secCik"], "0000320193")                 # unknown keys kept
        self.assertEqual(annual["end"], seeded["annual"]["end"] + ["2025-09-27"])
        self.assertEqual(annual["src"], ["sec"] * 10 + ["yahoo"])
        self.assertEqual(annual["form"], ["10-K"] * 10 + [None])            # per-period extras kept
        self.assertEqual(values(annual, "secOnlyField")["2015-09-%02d" % (24 + 2015 % 5)], 1)
        revenue = values(annual, "revenue")
        self.assertEqual(revenue["2022-09-24"], 394e9 * 1.05)                # SEC value kept
        self.assertEqual(revenue["2025-09-27"], synth.money(416 * BN))       # newer Yahoo year added
        # A null SEC field is filled from Yahoo
        fy24 = statement(synth.US_ANNUAL, "2024-09-28", synth.US_RATIOS)
        self.assertEqual(values(annual, "buybacks")["2024-09-28"], -fy24["RepurchaseOfCapitalStock"])
        self.assertEqual(values(annual, "buybacks")["2023-09-30"], 70e9)
        # Net income differs too, but only revenue / net income > 2% are reported, once per field
        self.assertIn("yahoo-differs", document["quality"]["issues"])
        self.assertNotIn("restated", document["quality"]["issues"])
        kinds = {(r["kind"], r["end"], r["field"]) for r in document["quality"]["revisions"]}
        self.assertIn(("yahoo-differs", "2022-09-24", "revenue"), kinds)
        self.assertEqual(info["restated"], 0)
        # Quarters: SEC ones kept, newer Yahoo quarters appended
        self.assertEqual(quarterly["src"], ["sec"] * 5 + ["yahoo"] * 4)
        self.assertEqual(values(quarterly, "revenue")["2024-12-28"], 124e9)
        self.assertEqual(document["quality"]["annualYears"], 11)
        self.assertEqual(document["quality"]["quarters"], 9)

    def test_yahoo_period_inside_sec_history_is_not_inserted(self):
        seeded = sec_document()
        index = seeded["annual"]["end"].index("2023-09-30")
        for key in ("end", "fy", "src", "form"):
            del seeded["annual"][key][index]
        for column in seeded["annual"]["values"].values():
            del column[index]
        document, _ = build(fixture("yahoo_us_full.json"), "AAPL", previous=seeded)
        self.assertNotIn("2023-09-30", document["annual"]["end"])

    def test_mixed_sources_rerun_is_identical(self):
        once, _ = build(fixture("yahoo_us_full.json"), "AAPL", previous=sec_document())
        twice, _ = build(fixture("yahoo_us_full.json"), "AAPL", previous=copy.deepcopy(once))
        self.assertEqual(canonical(once), canonical(twice))

    def test_sec_periods_beyond_the_cap_are_kept(self):
        seeded = sec_document()
        years = list(range(1995, 2025))
        seeded["annual"] = {
            "end": ["%d-09-28" % year for year in years], "fy": years, "src": ["sec"] * len(years),
            "values": {"revenue": [float(year) * 1e8 for year in years]},
        }
        document, _ = build(fixture("yahoo_us_full.json"), "AAPL", previous=seeded)
        self.assertEqual(len(document["annual"]["end"]), len(years) + 1)
        self.assertEqual(document["quality"]["level"], "good")

    def test_bad_src_makes_the_file_untouchable(self):
        seeded = sec_document()
        seeded["annual"]["src"] = ["sec"] * 3
        with self.assertRaises(ValueError):
            fin.read_sections(seeded)


# ---------------------------------------------------------------------------------------------
# Symbols, schedule, run
# ---------------------------------------------------------------------------------------------
def write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        if isinstance(value, str):
            file.write(value)
        else:
            json.dump(value, file)


class SymbolTest(unittest.TestCase):
    def test_priority_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, data = os.path.join(tmp, "report"), os.path.join(tmp, "data")
            write(os.path.join(report, "latest.json"), {"groups": [
                {"market": "TW", "items": [{"symbol": "6770.TW"}, {"symbol": "2303.TW"}]},
                {"market": "US", "items": [{"symbol": "NFLX"}, {"symbol": "brk.b"}, {"symbol": "^GSPC"}]},
                {"market": "US", "items": None},
                "junk",
            ]})
            write(os.path.join(report, "themes.json"), {"themes": [
                {"constituents": [{"symbol": "ZS"}, {"symbol": "NFLX"}]}, None,
            ]})
            fields = ["symbol", "name", "turnover20"]
            write(os.path.join(report, "universe.json"), {"fields": fields, "markets": {
                "US": {"rows": [["A", "", 1], ["AAPL", "", 2], ["ZS", "", 3]]},
                "TW": {"rows": [
                    ["1101.TW", "台泥", 5], ["2330.TW", "台積電", 100], ["2303.TW", "聯電", 50],
                    ["6488.TWO", "環球晶", None], ["8069.TWO", "元太", 70], ["bad"],
                ]},
            }})
            symbols = fin.collect_symbols(report, data, tw_limit=2)
        self.assertEqual(symbols, [
            "6770.TW", "2303.TW", "NFLX", "BRK-B", "ZS", "A", "AAPL", "2330.TW", "8069.TWO",
        ])

    def test_full_market_universe_keeps_liquid_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, data = os.path.join(tmp, "report"), os.path.join(tmp, "data")
            write(os.path.join(data, "AAPL.csv"), "Date,Open,High,Low,Close,Volume\n2026-10-08,1,1,1,10,5\n")
            fields = ["symbol", "name", "turnover20"]
            write(os.path.join(report, "universe.json"), {"fields": fields, "markets": {
                "US": {"scope": "full", "rows": [["ZZZZ", "", 1], ["YYYY", "", 2]]},
                "TW": {"scope": "full", "rows": [["1101.TW", "台泥", 5], ["2330.TW", "台積電", 100], ["9999.TW", "x", 1]]},
            }})
            symbols = fin.collect_symbols(report, data, tw_limit=2)
        # US from the CSVs (S&P 500 + custom), TW still the top tw_limit by turnover
        self.assertEqual(symbols, ["AAPL", "2330.TW", "1101.TW"])

    def test_csv_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, data = os.path.join(tmp, "report"), os.path.join(tmp, "data")
            header = "Date,Open,High,Low,Close,Volume\n"
            write(os.path.join(data, "AAPL.csv"), header + "2026-10-08,1,1,1,10,5\n")
            write(os.path.join(data, "^TWII.csv"), header + "2026-10-08,1,1,1,10,5\n")
            write(os.path.join(data, "1101.TW.csv"), header + "2026-10-08,1,1,1,10,5\n")
            write(os.path.join(data, "2330.TW.csv"), header + "2026-10-07,1,1,1,1000,9\n2026-10-08,1,1,1,1000,9\n")
            write(os.path.join(data, "6488.TWO.csv"), header + "garbage\n")
            write(os.path.join(data, "notes.txt"), "x")
            symbols = fin.collect_symbols(report, data, tw_limit=2)
        self.assertEqual(symbols, ["AAPL", "2330.TW", "1101.TW"])


class RefreshPolicyTest(unittest.TestCase):
    def state(self, latest, checked):
        return {"latestQuarter": latest, "checkedAt": checked}

    def test_needs_refresh(self):
        day = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731
        self.assertTrue(fin.needs_refresh(None, TODAY))
        self.assertFalse(fin.needs_refresh(None, TODAY, miss=day(6)))
        self.assertTrue(fin.needs_refresh(None, TODAY, miss=day(7)))
        self.assertFalse(fin.needs_refresh(self.state(day(120), day(3)), TODAY))
        self.assertTrue(fin.needs_refresh(self.state(day(120), day(4)), TODAY))
        self.assertFalse(fin.needs_refresh(self.state(day(50), day(45)), TODAY))
        self.assertTrue(fin.needs_refresh(self.state(day(50), day(46)), TODAY))
        self.assertFalse(fin.needs_refresh(self.state(None, day(1)), TODAY))
        self.assertTrue(fin.needs_refresh({"latestAnnual": day(200), "checkedAt": day(5)}, TODAY))
        self.assertTrue(fin.needs_refresh({"checkedAt": "garbage"}, TODAY))


class Fetcher:
    """fetch(symbol) stub: payload per symbol (name of a fixture, a dict, or an exception)."""

    def __init__(self, table, default="yahoo_empty.json"):
        self.table = table
        self.default = default
        self.calls = []

    def __call__(self, symbol):
        self.calls.append(symbol)
        value = self.table.get(symbol, self.default)
        if isinstance(value, Exception):
            raise value
        return fixture(value) if isinstance(value, str) else copy.deepcopy(value)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "financials")

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, symbols, fetch, now=NOW, budget=600, chart=None, clock=None):
        return fin.run_refresh(
            symbols, self.out, fetch, now, budget, fetch_chart=chart, delay=0,
            sleep=lambda seconds: None, clock=clock or (lambda: 0.0),
        )

    def files(self):
        result = {}
        for name in sorted(os.listdir(self.out)):
            with open(os.path.join(self.out, name), "rb") as file:
                result[name] = file.read()
        return result

    def index(self):
        with open(os.path.join(self.out, "index.json"), encoding="utf-8") as file:
            return json.load(file)

    def test_writes_files_and_index(self):
        fetch = Fetcher({"AAPL": "yahoo_us_full.json", "2330.TW": "yahoo_tw_2330.json"})
        stats = self.run_once(["AAPL", "2330.TW", "XYZ"], fetch)
        self.assertFalse(stats["failed"])
        self.assertEqual(stats["updated"], 2)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(sorted(self.files()), ["2330.TW.json", "AAPL.json", "index.json"])
        index = self.index()
        self.assertEqual(index["version"], 1)
        self.assertEqual(index["misses"], {"XYZ": "2026-10-11"})
        self.assertEqual(index["items"]["AAPL"], {
            "market": "US", "currency": "USD", "latestAnnual": "2025-09-27",
            "latestQuarter": "2026-06-27", "checkedAt": "2026-10-11", "level": "good",
            "lastListed": "2026-10-11",
        })
        self.assertEqual(index["items"]["2330.TW"]["currency"], "TWD")
        with open(os.path.join(self.out, "AAPL.json"), encoding="utf-8") as file:
            document = json.load(file)
        self.assertEqual(document["symbol"], "AAPL")

    def test_rerun_is_byte_identical(self):
        fetch = Fetcher({"AAPL": "yahoo_us_full.json", "2330.TW": "yahoo_tw_2330.json"})
        self.run_once(["AAPL", "2330.TW"], fetch)
        first = self.files()
        # Same day, index lost: the files' own checkedAt says nothing is due; index rebuilt identically
        os.remove(os.path.join(self.out, "index.json"))
        stats = self.run_once(["AAPL", "2330.TW"], fetch)
        self.assertEqual(stats["pending"], 0)
        self.assertEqual(self.files(), first)
        # 46 days later the same answers do not rewrite the files; only index.json records the check
        later = NOW + timedelta(days=46)
        stats = self.run_once(["AAPL", "2330.TW"], fetch, now=later)
        self.assertEqual(fetch.calls, ["AAPL", "2330.TW", "AAPL", "2330.TW"])
        self.assertEqual((stats["updated"], stats["unchanged"]), (0, 2))
        files = self.files()
        for name in ("AAPL.json", "2330.TW.json"):
            self.assertEqual(files[name], first[name])
        self.assertEqual(self.index()["items"]["AAPL"]["checkedAt"], later.date().isoformat())
        # A changed stored value is re-merged: the rewrite equals the first file but for timestamps
        path = os.path.join(self.out, "AAPL.json")
        with open(path, encoding="utf-8") as file:
            document = json.load(file)
        document["quarterly"]["values"]["cash"][-1] = 1
        fin.write_atomic(path, document)
        os.remove(os.path.join(self.out, "index.json"))
        stats = self.run_once(["AAPL"], fetch, now=NOW + timedelta(days=50))
        self.assertEqual(stats["updated"], 1)
        with open(path, encoding="utf-8") as file:
            rebuilt = json.load(file)
        expected = json.loads(first["AAPL.json"])
        expected.update(generatedAt="2026-11-30T09:00:00Z", checkedAt="2026-11-30")
        self.assertEqual(rebuilt, expected)

    def test_ordering_and_skipping(self):
        fin.write_atomic(os.path.join(self.out, "index.json"), {
            "version": 1, "items": {
                "OLD": {"latestQuarter": "2026-06-30", "checkedAt": "2026-08-01", "lastListed": "2026-10-10"},
                "OLDER": {"latestQuarter": "2026-06-30", "checkedAt": "2026-07-01", "lastListed": "2026-10-10"},
                "FRESH": {"latestQuarter": "2026-06-30", "checkedAt": "2026-10-09", "lastListed": "2026-10-10"},
            }, "misses": {"GONE": "2026-10-08", "RETRY": "2026-10-01"},
        })
        for key in ("OLD", "OLDER", "FRESH"):
            fin.write_atomic(os.path.join(self.out, key + ".json"), {"version": 1, "symbol": key})
        fetch = Fetcher({})
        self.run_once(["OLD", "NEW2", "FRESH", "GONE", "OLDER", "RETRY", "NEW1"], fetch)
        # Never fetched first (priority order), then the oldest check / miss
        self.assertEqual(fetch.calls, ["NEW2", "NEW1", "OLDER", "OLD", "RETRY"])

    def test_sec_check_date_of_the_index_survives_the_yahoo_run(self):
        # seed_financials_sec.py --due keeps its SEC check date (SEC_CHECKED_KEY) in index.json; the Yahoo run
        # rebuilds every item, so it must carry the date over, whether the file is unchanged or re-merged
        fetch = Fetcher({"AAPL": "yahoo_us_full.json"})
        self.run_once(["AAPL"], fetch)
        index = self.index()
        index["items"]["AAPL"][fin.SEC_CHECKED_KEY] = "2026-10-01"
        fin.write_atomic(os.path.join(self.out, "index.json"), index)
        self.run_once(["AAPL"], fetch, now=NOW + timedelta(days=46))            # Yahoo check 46 days old: due
        self.assertEqual(self.index()["items"]["AAPL"][fin.SEC_CHECKED_KEY], "2026-10-01")
        path = os.path.join(self.out, "AAPL.json")
        with open(path, encoding="utf-8") as file:
            document = json.load(file)
        document["quarterly"]["values"]["cash"][-1] = 1
        fin.write_atomic(path, document)
        stats = self.run_once(["AAPL"], fetch, now=NOW + timedelta(days=50))
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(self.index()["items"]["AAPL"][fin.SEC_CHECKED_KEY], "2026-10-01")

    def test_budget_stops_and_keeps_written_files(self):
        ticks = iter([0.0, 0.0, 5.0, 11.0, 12.0, 13.0])
        fetch = Fetcher({"AAPL": "yahoo_us_full.json", "2330.TW": "yahoo_tw_2330.json"})
        stats = self.run_once(["AAPL", "2330.TW", "JPM"], fetch, budget=10, clock=lambda: next(ticks))
        self.assertEqual(fetch.calls, ["AAPL", "2330.TW"])
        self.assertEqual(stats["stopped"], "time budget")
        self.assertFalse(stats["failed"])
        self.assertIn("2330.TW", self.index()["items"])

    def test_consecutive_failures_stop(self):
        table = {"AAPL": "yahoo_us_full.json"}
        table.update({"F%d" % n: RuntimeError("HTTP 429") for n in range(20)})
        fetch = Fetcher(table)
        symbols = ["AAPL"] + ["F%d" % n for n in range(20)]
        stats = self.run_once(symbols, fetch)
        self.assertTrue(stats["failed"])
        self.assertEqual(len(fetch.calls), 1 + fin.MAX_CONSECUTIVE_FAILURES)
        self.assertIn("AAPL.json", self.files())
        self.assertIn("AAPL", self.index()["items"])
        self.assertEqual(self.index()["misses"], {})

    def test_failing_symbols_do_not_block_the_queue(self):
        # Eight never-fetched symbols that always fail (e.g. HTTP 404) sit first in priority order:
        # without remembering them they would use up the failure limit again on every run.
        table = {"F%d" % n: RuntimeError("HTTP 404") for n in range(fin.MAX_CONSECUTIVE_FAILURES)}
        table["AAPL"] = "yahoo_us_full.json"
        fetch = Fetcher(table)
        symbols = ["F%d" % n for n in range(fin.MAX_CONSECUTIVE_FAILURES)] + ["AAPL"]
        stats = self.run_once(symbols, fetch)
        self.assertTrue(stats["failed"])
        self.assertNotIn("AAPL", fetch.calls)
        self.assertEqual(sorted(self.index()["failures"]), symbols[:-1])
        fetch.calls.clear()
        stats = self.run_once(symbols, fetch, now=NOW + timedelta(days=1))
        self.assertEqual(fetch.calls[0], "AAPL")
        self.assertIn("AAPL.json", self.files())
        self.assertNotIn("AAPL", self.index()["failures"])
        # A symbol that recovers leaves the failure list
        fetch.table["F0"] = "yahoo_bank.json"
        self.run_once(symbols, fetch, now=NOW + timedelta(days=2))
        self.assertNotIn("F0", self.index()["failures"])
        self.assertIn("F1", self.index()["failures"])

    def test_failure_counter_resets(self):
        table = {"A%d" % n: RuntimeError("x") for n in range(7)}
        table["OK"] = "yahoo_tw_2330.json"
        table.update({"B%d" % n: RuntimeError("x") for n in range(7)})
        fetch = Fetcher(table)
        symbols = ["A%d" % n for n in range(7)] + ["OK"] + ["B%d" % n for n in range(7)]
        stats = self.run_once(symbols, fetch)
        self.assertFalse(stats["failed"])
        self.assertEqual(len(fetch.calls), 15)

    def test_miss_retry_after_seven_days(self):
        fetch = Fetcher({})
        self.run_once(["XYZ"], fetch)
        self.run_once(["XYZ"], fetch, now=NOW + timedelta(days=6))
        self.assertEqual(fetch.calls, ["XYZ"])
        fetch.table["XYZ"] = "yahoo_bank.json"
        self.run_once(["XYZ"], fetch, now=NOW + timedelta(days=7))
        self.assertEqual(fetch.calls, ["XYZ", "XYZ"])
        self.assertEqual(self.index()["misses"], {})
        self.assertIn("XYZ.json", self.files())

    def test_empty_answer_keeps_existing_file(self):
        fetch = Fetcher({"AAPL": "yahoo_us_full.json"})
        self.run_once(["AAPL"], fetch)
        before = self.files()["AAPL.json"]
        fetch.table["AAPL"] = "yahoo_empty.json"
        later = NOW + timedelta(days=50)
        stats = self.run_once(["AAPL"], fetch, now=later)
        self.assertEqual(stats["misses"], 0)
        self.assertEqual(self.files()["AAPL.json"], before)
        self.assertEqual(self.index()["items"]["AAPL"]["checkedAt"], later.date().isoformat())
        self.assertEqual(self.index()["misses"], {})

    def test_corrupt_file_is_never_touched(self):
        os.makedirs(self.out)
        path = os.path.join(self.out, "AAPL.json")
        with open(path, "w", encoding="utf-8") as file:
            file.write('{"version": 1, "symbol": "AAPL", "annual": {"end": ["2025-09')
        fetch = Fetcher({"AAPL": "yahoo_us_full.json"})
        stats = self.run_once(["AAPL"], fetch)
        self.assertEqual(fetch.calls, [])
        self.assertEqual(stats["corrupt"], ["AAPL"])
        with open(path, encoding="utf-8") as file:
            self.assertTrue(file.read().endswith("2025-09"))

    def test_retention_of_unlisted_tickers(self):
        fetch = Fetcher({"AAPL": "yahoo_us_full.json", "OLD": "yahoo_bank.json", "KEEP": "yahoo_bank.json"})
        self.run_once(["AAPL", "OLD", "KEEP"], fetch)
        seeded = sec_document()
        seeded["symbol"] = "SEC"
        fin.write_atomic(os.path.join(self.out, "SEC.json"), seeded)
        fin.write_atomic(os.path.join(self.out, "STRAY.json"), dict(seeded, symbol="STRAY"))
        index = self.index()
        index["items"]["OLD"]["lastListed"] = (TODAY - timedelta(days=181)).isoformat()
        index["items"]["KEEP"]["lastListed"] = (TODAY - timedelta(days=179)).isoformat()
        index["items"]["SEC"] = dict(index["items"]["OLD"])
        index["items"]["GHOST"] = dict(index["items"]["OLD"])         # entry without a file
        fin.write_atomic(os.path.join(self.out, "index.json"), index)
        stats = self.run_once(["AAPL"], Fetcher({}))
        self.assertEqual(stats["deleted"], ["OLD"])
        self.assertEqual(sorted(self.files()), ["AAPL.json", "KEEP.json", "SEC.json", "STRAY.json", "index.json"])
        items = self.index()["items"]
        self.assertEqual(sorted(items), ["AAPL", "KEEP", "SEC", "STRAY"])
        # Unlisted file found without an index entry: the 180 days start now
        self.assertEqual(items["STRAY"]["lastListed"], "2026-10-11")
        self.assertEqual(items["KEEP"]["lastListed"], (TODAY - timedelta(days=179)).isoformat())
        # Files holding SEC periods are never deleted by this script
        self.assertEqual(items["SEC"]["level"], "good")

    def test_split_chart_request_counts_toward_requests(self):
        before, _ = build(fixture("yahoo_split_before.json"), "SPLT", now=at(date(2026, 6, 1)))
        fin.write_atomic(os.path.join(self.out, "SPLT.json"), before)
        chart_calls = []

        def chart(symbol):
            chart_calls.append(symbol)
            return fixture("yahoo_chart_split.json")

        stats = self.run_once(["SPLT"], Fetcher({"SPLT": "yahoo_split_after_unadjusted.json"}), chart=chart)
        self.assertEqual(chart_calls, ["SPLT"])
        self.assertEqual(stats["requested"], 2)
        with open(os.path.join(self.out, "SPLT.json"), encoding="utf-8") as file:
            self.assertTrue(json.load(file)["split"]["adjusted"])


class MainTest(unittest.TestCase):
    def test_main_exits_with_error_after_failure_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "report")
            write(os.path.join(report, "universe.json"), {"fields": ["symbol"], "markets": {
                "US": {"rows": [["S%d" % n] for n in range(10)]},
            }})

            class BrokenYahoo:
                def get_json(self, url, params):
                    raise RuntimeError("HTTP 429")

            env = {"ATLAS_REPORT_DIR": report, "ATLAS_DATA_DIR": os.path.join(tmp, "data")}
            with mock.patch.dict(os.environ, env), mock.patch.object(fin, "Yahoo", BrokenYahoo), \
                    mock.patch.object(fin.time, "sleep"):
                with self.assertRaises(RuntimeError):
                    fin.main()
            self.assertTrue(os.path.exists(os.path.join(report, "financials", "index.json")))


class SampleTest(unittest.TestCase):
    def test_committed_samples_match_the_builder(self):
        for name, value in synth.samples().items():
            with open(os.path.join(FIXTURES, name), encoding="utf-8") as file:
                self.assertEqual(file.read(), synth.dump_sample(value), name)
            self.assertIn("_fixture", value)


class ConnectYahooTest(unittest.TestCase):
    def factory(self, failures):
        calls = []

        def make():
            calls.append(1)
            if len(calls) <= failures:
                raise RuntimeError("Yahoo crumb HTTP 429: 'Too Many Requests'")
            return "session"

        return make, calls

    def test_retries_then_succeeds(self):
        make, calls = self.factory(2)
        waits = []
        self.assertEqual(fin.connect_yahoo(make, sleep=waits.append, delays=(1, 2, 3)), "session")
        self.assertEqual(len(calls), 3)
        self.assertEqual(waits, [1, 2])

    def test_gives_up_after_the_last_delay(self):
        make, calls = self.factory(99)
        waits = []
        with self.assertRaises(RuntimeError):
            fin.connect_yahoo(make, sleep=waits.append, delays=(1, 2, 3))
        self.assertEqual(len(calls), 4)
        self.assertEqual(waits, [1, 2, 3])


class RepairSplitEpsTest(unittest.TestCase):
    """synthetic: 4:1 split on 2020-08-31, 7:1 on 2014-06-09 (numbers invented, shaped like a real mixed-basis file)"""
    EVENTS = [["2014-06-09", 7], ["2020-08-31", 4]]

    def doc(self, quarters, src="sec"):
        # quarters: (end, netIncome, shares, epsDiluted, epsBasic)
        return {
            "split": {"adjusted": True, "events": self.EVENTS},
            "annual": {"end": [], "fy": [], "values": {}},
            "quarterly": {
                "end": [q[0] for q in quarters],
                "fy": [2012] * len(quarters),
                "fq": [1] * len(quarters),
                "src": [src] * len(quarters),
                "values": {
                    "netIncome": [q[1] for q in quarters],
                    "dilutedShares": [q[2] for q in quarters],
                    "epsDiluted": [q[3] for q in quarters],
                    "epsBasic": [q[4] for q in quarters],
                },
            },
        }

    def test_formula_like_split_mismatch_never_overwrites_reported_eps(self):
        document = self.doc([
            ("2012-09-29", 8_000_000_000, 26_000_000_000, 0.3077, 0.31),
            ("2012-12-29", 13_000_000_000, 26_000_000_000, 0.0714, 0.0714),   # 1/7 too small
            ("2013-03-30", 9_500_000_000, 26_000_000_000, 0.3654, 0.3654),
        ])
        self.assertEqual(fin.repair_document(document), 0)
        values = document["quarterly"]["values"]
        self.assertEqual(values["epsDiluted"], [0.3077, 0.0714, 0.3654])
        self.assertEqual(values["epsBasic"][1], 0.0714)

    def test_q4_without_shares_is_not_rewritten_from_neighbouring_eps(self):
        document = self.doc([
            ("2012-06-30", 8_000_000_000, 26_000_000_000, 0.3077, 0.3077),
            ("2012-09-29", 8_000_000_000, None, 2.1538, 2.1538),               # 7x too large, no share count
            ("2012-12-29", 8_000_000_000, 26_000_000_000, 0.3077, 0.3077),
        ])
        self.assertEqual(fin.repair_document(document), 0)
        self.assertEqual(document["quarterly"]["values"]["epsDiluted"][1], 2.1538)

    def test_non_split_mismatch_is_left_alone(self):
        # preferred dividends / minority interest: off by 1.6x, not a split multiple
        document = self.doc([("2012-09-29", 8_000_000_000, 26_000_000_000, 0.19, 0.19)])
        self.assertEqual(fin.repair_document(document), 0)
        self.assertEqual(document["quarterly"]["values"]["epsDiluted"], [0.19])

    def test_yahoo_periods_and_files_without_splits_are_untouched(self):
        rows = [("2012-12-29", 13_000_000_000, 26_000_000_000, 0.0714, 0.0714)]
        yahoo = self.doc(rows, src="yahoo")
        self.assertEqual(fin.repair_document(yahoo), 0)
        plain = self.doc(rows)
        plain["split"]["events"] = []
        self.assertEqual(fin.repair_document(plain), 0)

    def test_later_split_only_counts(self):
        # period after both splits cannot be off by a split factor
        document = self.doc([("2021-03-27", 20_000_000_000, 16_900_000_000, 0.2958, 0.2958)])
        self.assertEqual(fin.repair_document(document), 0)

    def test_deprecated_repair_is_idempotent_and_preserves_reported_values(self):
        document = self.doc([
            ("2012-09-29", 8_000_000_000, 26_000_000_000, 0.3077, 0.31),
            ("2012-12-29", 13_000_000_000, 26_000_000_000, 0.0714, 0.0714),
        ])
        self.assertEqual(fin.repair_document(document), 0)
        before = json.dumps(document, sort_keys=True)
        self.assertEqual(fin.repair_document(document), 0)
        self.assertEqual(json.dumps(document, sort_keys=True), before)

    def test_repair_all_does_not_rewrite_files_from_eps_ratios(self):
        with tempfile.TemporaryDirectory() as directory:
            document = self.doc([("2012-12-29", 13_000_000_000, 26_000_000_000, 0.0714, 0.0714)])
            with open(os.path.join(directory, "AAPL.json"), "w", encoding="utf-8") as file:
                json.dump(document, file)
            with open(os.path.join(directory, "index.json"), "w", encoding="utf-8") as file:
                json.dump({"items": {}}, file)
            with mock.patch("builtins.print"):
                self.assertEqual(fin.repair_all(directory), (0, 0))
                self.assertEqual(fin.repair_all(directory), (0, 0))
            with open(os.path.join(directory, "AAPL.json"), encoding="utf-8") as file:
                self.assertEqual(json.load(file)["quarterly"]["values"]["epsDiluted"], [0.0714])


class NestedSymbolsTest(unittest.TestCase):
    def test_failed_report_rows_are_skipped(self):
        document = {"groups": [{"items": [
            {"symbol": "AAA"},
            {"symbol": "BBB", "status": "unmatched"},
            {"symbol": "6488.TW", "status": "failed"},
        ]}]}
        self.assertEqual(fin.nested_symbols(document, "groups", "items"), ["AAA", "BBB"])


# ---------------------------------------------------------------------------------------------
# Foreign issuers (ADR / 20-F): identification, own lane, currency guard, SEC protection
# ---------------------------------------------------------------------------------------------
def twd_sec_document():
    """An ADR file as the IFRS seeder would write it: TWD statements, SEC annual FY2022–FY2024 only."""
    annual = {
        "end": ["2022-12-31", "2023-12-31", "2024-12-31"],
        "fy": [2022, 2023, 2024],
        "src": ["sec", "sec", "sec"],
        "values": {
            # 2023 revenue is ~3% under Yahoo's; 2023 net income is a gap SEC leaves; 2023 EPS is SEC's
            "revenue": [2264 * BN, 2100 * BN, 2894 * BN],
            "netIncome": [600 * BN, None, 700 * BN],
            "epsDiluted": [23.1, 22.0, 27.0],
            "dilutedShares": [25.93e9, 25.93e9, 25.93e9],
        },
    }
    return {
        "version": 1, "symbol": "ASX", "market": "US", "currency": "TWD", "foreignReporter": True,
        "generatedAt": "2026-09-01T00:00:00Z", "checkedAt": "2026-09-01",
        "source": "SEC EDGAR companyfacts + Yahoo Finance", "fyEndMonth": 12,
        "annual": annual, "quarterly": {"end": [], "fy": [], "fq": [], "values": {}},
        "split": {"adjusted": False, "events": []},
        "quality": {"level": "partial", "annualYears": 3, "quarters": 0,
                    "issues": ["foreign-filer", "foreign-currency"]},
    }


class ForeignIssuerTest(unittest.TestCase):
    def test_foreign_filer_by_file_flags(self):
        self.assertTrue(fin.foreign_filer({"foreignReporter": True}))
        self.assertTrue(fin.foreign_filer({"quality": {"issues": ["foreign-filer"]}}))
        self.assertFalse(fin.foreign_filer({"foreignReporter": False, "quality": {"issues": []}}))
        self.assertFalse(fin.foreign_filer({"quality": {"issues": "foreign-filer"}}))
        self.assertFalse(fin.foreign_filer(None))

    def test_foreign_issuers_come_from_the_full_universe_only(self):
        documents = {
            "BABA": {"foreignReporter": True},
            "AAPL": {"foreignReporter": False},
            "NOTLISTED": {"foreignReporter": True},
        }
        universe = ["AAPL", "TSM", "BABA", "ASX"]
        self.assertEqual(fin.foreign_issuers(universe, documents), ["TSM", "BABA", "ASX"])

    def test_universe_us_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "universe.json"), {"fields": ["symbol"], "markets": {
                "US": {"rows": [["brk.b"], ["AAPL"], ["AAPL"], ["2330.TW"], ["^GSPC"], ["NVO"]]},
            }})
            self.assertEqual(fin.universe_us_keys(tmp), ["BRK-B", "AAPL", "NVO"])
            self.assertEqual(fin.universe_us_keys(os.path.join(tmp, "missing")), [])

    def test_interleave_shares_slots(self):
        self.assertEqual(fin.interleave(["a", "b", "c", "d"], ["x", "y"], 0.5), ["a", "x", "b", "y", "c", "d"])
        self.assertEqual(fin.interleave(["a"], [], 0.3), ["a"])
        self.assertEqual(fin.interleave([], ["x"], 0.3), ["x"])
        core, extra = list(range(100)), list(range(-100, 0))
        order = fin.interleave(core, extra, 0.3)
        self.assertEqual(sorted(order), sorted(core + extra))
        self.assertEqual([item for item in order if item >= 0], core)      # core keeps its order
        self.assertAlmostEqual(sum(1 for item in order[:100] if item < 0), 30, delta=1)


class ForeignLaneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "financials")

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, symbols, fetch, universe, budget=600, clock=None, now=NOW):
        return fin.run_refresh(
            symbols, self.out, fetch, now, budget, delay=0, sleep=lambda seconds: None,
            clock=clock or (lambda: 0.0), universe=universe,
        )

    def test_core_list_keeps_its_share_while_foreign_issuers_are_due(self):
        core = ["C%d" % n for n in range(10)]
        foreign = ["F%d" % n for n in range(40)]
        fetch = Fetcher({})
        ticks = itertools.count()
        with mock.patch.object(fin, "FOREIGN_MANUAL", tuple(foreign)), \
                mock.patch("builtins.print") as printed:
            # 14 requests fit the budget: the 10 core keys must all come before the budget runs out
            stats = self.run_once(core, fetch, foreign, budget=14, clock=lambda: float(next(ticks)))
        self.assertEqual([key for key in fetch.calls if key.startswith("C")], core)
        self.assertEqual(stats["stopped"], "time budget")
        self.assertEqual(len(fetch.calls), 14)
        self.assertEqual(stats["foreign"], {"listed": 40, "due": 40, "requested": 4, "updated": 0, "errors": 0})
        printed.assert_any_call(
            "Foreign issuers outside the core list: 40 listed, 40 due, 4 requests, 0 files written, 0 errors",
            flush=True,
        )

    def test_foreign_issuers_take_the_rest_once_core_is_done(self):
        foreign = ["F%d" % n for n in range(5)]
        fetch = Fetcher({})
        with mock.patch.object(fin, "FOREIGN_MANUAL", tuple(foreign)), mock.patch("builtins.print"):
            stats = self.run_once(["C0"], fetch, foreign)
        self.assertEqual(fetch.calls, ["C0"] + foreign)
        self.assertEqual(stats["foreign"]["requested"], 5)

    def test_core_issuer_that_is_also_foreign_stays_in_the_core_lane(self):
        fetch = Fetcher({})
        with mock.patch.object(fin, "FOREIGN_MANUAL", ("TSM", "F0")), mock.patch("builtins.print"):
            stats = self.run_once(["TSM", "C0"], fetch, ["TSM", "F0"])
        self.assertEqual(fetch.calls, ["TSM", "C0", "F0"])
        self.assertEqual(stats["foreign"]["listed"], 1)

    def test_flagged_file_joins_the_lane_and_is_listed(self):
        write(os.path.join(self.out, "ASX.json"), {"version": 1, "symbol": "ASX", "foreignReporter": True})
        write(os.path.join(self.out, "AAPL.json"), {"version": 1, "symbol": "AAPL", "foreignReporter": False})
        fetch = Fetcher({})
        with mock.patch("builtins.print"):
            stats = self.run_once(["C0"], fetch, ["AAPL", "ASX", "NVO"])
        self.assertEqual(fetch.calls, ["C0", "ASX"])     # NVO has no file and no flag: not in the lane
        self.assertEqual(stats["foreign"]["listed"], 1)
        with open(os.path.join(self.out, "index.json"), encoding="utf-8") as file:
            index = json.load(file)
        self.assertEqual(index["items"]["ASX"]["lastListed"], "2026-10-11")


class ForeignCurrencyTest(unittest.TestCase):
    def test_sec_values_are_not_overwritten_by_yahoo(self):
        yahoo = synth.build("ASX", synth.TW_ANNUAL, [], synth.TW_RATIOS, "TWD")
        with mock.patch("builtins.print"):
            document, info = build(yahoo, "ASX", previous=twd_sec_document())
        annual = document["annual"]
        self.assertEqual(document["currency"], "TWD")
        self.assertTrue(document["foreignReporter"])
        self.assertEqual(annual["src"], ["sec", "sec", "sec", "yahoo"])        # only the newer year is added
        self.assertEqual(values(annual, "revenue")["2023-12-31"], 2100 * BN)  # SEC value kept
        # the synthetic SEC EPS give no ADR ratio against Yahoo's: one basis kept, Yahoo's (per ADR)
        self.assertEqual(info["epsBasis"], "replaced")
        self.assertEqual(document["adrRatio"]["basis"], "Yahoo")
        self.assertIn("eps-yahoo-only", document["quality"]["issues"])
        self.assertEqual(values(annual, "revenue")["2025-12-31"], 3600 * BN)
        # The SEC gap is filled from the same-currency Yahoo value
        fy23 = statement(synth.TW_ANNUAL, "2023-12-31", synth.TW_RATIOS)
        self.assertEqual(values(annual, "netIncome")["2023-12-31"], fy23["NetIncomeCommonStockholders"])
        self.assertEqual(values(annual, "netIncome")["2022-12-31"], 600 * BN)
        self.assertIn("yahoo-differs", document["quality"]["issues"])
        kinds = {(r["kind"], r["end"], r["field"]) for r in document["quality"]["revisions"]}
        self.assertIn(("yahoo-differs", "2023-12-31", "revenue"), kinds)
        self.assertEqual(info["restated"], 0)

    def test_usd_conversion_is_not_mixed_into_a_twd_file(self):
        usd = synth.build("ASX", synth.TW_ANNUAL, synth.TW_QUARTERLY, synth.TW_RATIOS, "USD")
        with mock.patch("builtins.print") as printed:
            document, info = build(usd, "ASX", previous=twd_sec_document(), now=NOW + timedelta(days=1))
        self.assertEqual(document["currency"], "TWD")
        self.assertEqual(document["annual"]["end"], ["2022-12-31", "2023-12-31", "2024-12-31"])
        self.assertEqual(document["annual"]["values"]["revenue"], [2264 * BN, 2100 * BN, 2894 * BN])
        self.assertIsNone(values(document["annual"], "netIncome")["2023-12-31"])
        self.assertEqual(document["quarterly"]["end"], [])
        self.assertTrue(info["currencyChanged"])
        self.assertGreater(info["skipped"], 0)
        self.assertIn("currency-changed", document["quality"]["issues"])
        notices = [call.args[0] for call in printed.call_args_list if call.args and "::notice::" in str(call.args[0])]
        self.assertTrue(any("ASX" in text and "skipped" in text and "TWD" in text for text in notices), notices)

    def test_only_the_statement_currency_survives_per_field(self):
        payload = synth.build("ASX", synth.TW_ANNUAL, [], synth.TW_RATIOS, "TWD")
        converted = synth.build("ASX", [], synth.TW_QUARTERLY, synth.TW_RATIOS, "USD")
        payload["timeseries"]["result"] += converted["timeseries"]["result"]
        parsed = fin.parse_timeseries(payload, TODAY, currency="TWD", market="US")
        self.assertEqual(parsed["currency"], "TWD")
        self.assertEqual(sorted(parsed["annual"]), ["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"])
        self.assertEqual(parsed["quarterly"], {})
        self.assertGreater(parsed["skipped"], 0)
        # Without a pinned currency the statement currency is the majority, and the other points are left out
        detected = fin.parse_timeseries(payload, TODAY, market="US")
        self.assertEqual(detected["currency"], "USD")
        self.assertEqual(sorted(detected["quarterly"]), [end for end, _, _ in synth.TW_QUARTERLY])
        self.assertEqual(detected["annual"], {})


# ---------------------------------------------------------------------------------------------
# EPS basis of foreign reporters (adrRatio): SEC IFRS EPS is per ordinary share, Yahoo's per ADR
# ---------------------------------------------------------------------------------------------
ORDINARY = 25.93e9          # ordinary shares of the made-up issuer
PER_ADR = 5                 # 1 ADR = 5 ordinary shares (the TSM type)


def adr_eps(revenue, shares=ORDINARY / PER_ADR):
    """Diluted EPS Yahoo would report per ADR for a period with this revenue (TW_RATIOS)."""
    return synth.statement(revenue, shares, synth.TW_RATIOS)["DilutedEPS"]


def sec_file(rows, shares, adr_ratio=None, quarterly=(), eps_scale=1.0):
    """
    SYNTHETIC SEC-seeded file of a foreign reporter: revenue, net income and EPS per ORDINARY share (as
    sec_ifrs writes them; no share count). rows / quarterly: synth rows (end, revenue, shares);
    eps_scale multiplies the stored EPS, to store a deliberately different basis.
    """
    def section(items):
        columns = {"revenue": [], "netIncome": [], "epsBasic": [], "epsDiluted": []}
        for _, revenue, _ in items:
            values = synth.statement(revenue, shares, synth.TW_RATIOS)
            columns["revenue"].append(values["TotalRevenue"])
            columns["netIncome"].append(values["NetIncomeCommonStockholders"])
            columns["epsBasic"].append(round(values["BasicEPS"] * eps_scale, 2))
            columns["epsDiluted"].append(round(values["DilutedEPS"] * eps_scale, 2))
        return {"end": [end for end, _, _ in items], "fy": [int(end[:4]) for end, _, _ in items],
                "src": ["sec"] * len(items), "values": columns}
    empty = {"end": [], "fy": [], "fq": [], "values": {}}
    document = {
        "version": 1, "symbol": "TSM", "market": "US", "currency": "TWD", "foreignReporter": True,
        "generatedAt": "2026-09-01T00:00:00Z", "checkedAt": "2026-09-01",
        "source": "SEC EDGAR companyfacts (IFRS)", "fyEndMonth": 12,
        "annual": section(rows), "quarterly": section(quarterly) if quarterly else empty,
        "split": {"adjusted": False, "events": []},
        "quality": {"level": "good", "annualYears": len(rows), "quarters": len(quarterly), "issues": ["ifrs"]},
    }
    if adr_ratio is not None:
        document["adrRatio"] = adr_ratio
    return document


def merge_quietly(previous, payload, now=NOW):
    """update_document for TSM with the notices it printed → (document, info, notices)."""
    with mock.patch("builtins.print") as printed:
        document, info = build(payload, "TSM", previous=previous, now=now)
    notices = [call.args[0] for call in printed.call_args_list if call.args and "::notice::" in str(call.args[0])]
    return document, info, notices


class EpsFactorTest(unittest.TestCase):
    def test_median_snaps_to_a_whole_number_or_its_reciprocal(self):
        self.assertEqual(fin.eps_factor([4.99, 5.0, 5.02]), (5.0, None))
        self.assertEqual(fin.snap_factor(5.03), 5.0)
        self.assertEqual(fin.snap_factor(0.2), 0.2)           # 1 / 0.2 = 5
        self.assertEqual(fin.snap_factor(0.5), 0.5)           # 1 / 0.5 = 2
        self.assertEqual(fin.snap_factor(2.5), 2.5)           # neither is within 2%: kept as measured

    def test_within_two_percent_of_one_is_unity(self):
        self.assertEqual(fin.eps_factor([1.0, 1.01, 0.995]), (1, None))

    def test_disagreeing_or_missing_pairs_give_a_reason(self):
        factor, reason = fin.eps_factor([5.0, 3.5, 7.0])
        self.assertIsNone(factor)
        self.assertEqual(reason, "pair ratios 3.5000 to 7.0000 over 3 EPS values")
        self.assertEqual(fin.eps_factor([2.5, 2.5, 1.0]), (None, "pair ratios 1.0000 to 2.5000 over 3 EPS values"))

    def test_loose_ratio_snaps_to_a_whole_number_with_half_the_pairs_close(self):
        # TSM: local GAAP against IFRS EPS; UMC; ASML with two stale Yahoo years
        self.assertEqual(fin.eps_factor([5.119, 4.922, 5.064]), (5, None))
        self.assertEqual(fin.eps_factor([4.791, 5.01, 4.738]), (5, None))
        self.assertEqual(fin.eps_factor([1.137, 1.137, 1.035, 1.035, 1.0, 1.0, 1.0, 1.0]), (1, None))
        self.assertEqual(fin.eps_factor([5.0, 4.5, 5.0]), (5, None))
        self.assertEqual(fin.eps_factor([0.5, 0.52, 0.8]), (0.5, None))
        self.assertIsNone(fin.eps_factor([5.0, 4.4, 4.3])[0])           # only one pair close to 5 of three
        self.assertEqual(fin.eps_factor([]), (None, "no overlapping EPS period"))
        self.assertIsNone(fin.eps_factor([-5.0, 5.0])[0])     # opposite signs

    def test_stored_adr_factor_must_be_a_positive_number(self):
        self.assertEqual(fin.adr_eps_factor({"adrRatio": {"epsFactor": 5, "basis": "ADR"}}), 5)
        self.assertIsNone(fin.adr_eps_factor({"adrRatio": {"epsFactor": 0}}))
        self.assertIsNone(fin.adr_eps_factor({"adrRatio": {"epsFactor": "5"}}))
        self.assertIsNone(fin.adr_eps_factor({}))
        self.assertIsNone(fin.adr_eps_factor(None))


class AdrEpsBasisTest(unittest.TestCase):
    """Foreign reporter files: the EPS basis is settled once (adrRatio), then Yahoo merges as usual."""

    def adr_payload(self, annual=None, quarterly=()):
        """Yahoo's view of the issuer: the same periods on the ADR share count, TWD."""
        annual = synth.TW_ANNUAL if annual is None else annual
        adr = [(end, revenue, ORDINARY / PER_ADR) for end, revenue, _ in annual]
        adr_q = [(end, revenue, ORDINARY / PER_ADR) for end, revenue, _ in quarterly]
        return synth.build("TSM", adr, adr_q, synth.TW_RATIOS, "TWD")

    def test_tsm_type_rescales_sec_eps_and_merges_newer_adr_periods(self):
        stored = sec_file(synth.TW_ANNUAL[:3], ORDINARY, quarterly=synth.TW_QUARTERLY[:4])
        document, info, notices = merge_quietly(stored, self.adr_payload(quarterly=synth.TW_QUARTERLY[:4]))
        self.assertEqual(notices, [])
        self.assertEqual(info["epsBasis"], "rescaled")
        self.assertEqual(document["adrRatio"], {"epsFactor": 5, "basis": "ADR", "derivedAt": TODAY.isoformat(), "pairs": 14})
        self.assertIn("adr-eps-rescaled", document["quality"]["issues"])
        eps = values(document["annual"], "epsDiluted")
        for end, revenue, _ in synth.TW_ANNUAL[:3]:
            self.assertAlmostEqual(eps[end], adr_eps(revenue), delta=0.05)     # 45.25 per share → 226.25 per ADR
        self.assertEqual(eps["2025-12-31"], adr_eps(3600 * BN))                  # newer period: Yahoo's ADR EPS
        quarterly = values(document["quarterly"], "epsDiluted")
        for end, revenue, _ in synth.TW_QUARTERLY[:4]:
            self.assertAlmostEqual(quarterly[end], adr_eps(revenue), delta=0.05)
        self.assertEqual(document["annual"]["src"], ["sec", "sec", "sec", "yahoo"])

    def test_rescale_divides_a_stored_share_count(self):
        periods = [fin.period("2024-12-31", {"epsBasic": 45.25, "epsDiluted": 45.2, "dilutedShares": 25.93e9})]
        fin.scale_eps(periods, 5)
        self.assertEqual(periods[0]["values"]["epsBasic"], 226.25)
        self.assertEqual(periods[0]["values"]["dilutedShares"], 5.186e9)

    def test_rescaling_is_not_repeated_on_the_next_run(self):
        first, _, _ = merge_quietly(sec_file(synth.TW_ANNUAL[:3], ORDINARY), self.adr_payload())
        second, info, notices = merge_quietly(first, self.adr_payload(), now=NOW + timedelta(days=1))
        self.assertEqual(notices, [])
        self.assertIsNone(info["epsBasis"])
        self.assertEqual(second["adrRatio"], first["adrRatio"])
        self.assertEqual(values(second["annual"], "epsDiluted"), values(first["annual"], "epsDiluted"))
        self.assertIn("adr-eps-rescaled", second["quality"]["issues"])

    def test_asml_type_is_unity_and_nothing_is_rescaled(self):
        stored = sec_file(synth.TW_ANNUAL[:3], ORDINARY)
        payload = synth.build("TSM", synth.TW_ANNUAL, [], synth.TW_RATIOS, "TWD")   # one ADR = one share
        document, info, notices = merge_quietly(stored, payload)
        self.assertEqual(notices, [])
        self.assertEqual(info["epsBasis"], "unity")
        self.assertEqual(document["adrRatio"]["epsFactor"], 1)
        self.assertNotIn("adr-eps-rescaled", document["quality"]["issues"])
        self.assertEqual(values(document["annual"], "epsDiluted"), values(stored["annual"], "epsDiluted") | {
            "2025-12-31": adr_eps(3600 * BN, ORDINARY)})

    def test_disagreeing_ratios_drop_the_sec_eps_and_keep_yahoos(self):
        stored = sec_file(synth.TW_ANNUAL[:3], ORDINARY)
        for field in ("epsBasic", "epsDiluted"):
            column = stored["annual"]["values"][field]
            column[1] = round(column[1] * 1.6, 2)
            column[2] = round(column[2] * 0.6, 2)
        payload = self.adr_payload()
        document, info, notices = merge_quietly(stored, payload)
        self.assertEqual(info["epsBasis"], "replaced")
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0].startswith("::notice::TSM: EPS basis: SEC EPS dropped, Yahoo EPS kept (pair "),
                        notices[0])
        self.assertEqual(document["adrRatio"]["basis"], "Yahoo")
        self.assertEqual(document["adrRatio"]["epsFactor"], 1)
        self.assertIn("eps-yahoo-only", document["quality"]["issues"])
        self.assertNotIn("adr-eps-rescaled", document["quality"]["issues"])
        eps = values(document["annual"], "epsDiluted")
        for end, revenue, _ in synth.TW_ANNUAL[:3]:                              # every EPS is Yahoo's, per ADR
            self.assertAlmostEqual(eps[end], adr_eps(revenue), delta=0.05)
        self.assertEqual(eps["2025-12-31"], adr_eps(3600 * BN))
        self.assertEqual(values(document["annual"], "revenue")["2025-12-31"], 3600 * BN)   # other fields merged

    def test_no_overlapping_period_takes_k_from_the_implied_share_counts(self):
        old = sec_file([("2019-12-31", 1900 * BN, ORDINARY), ("2020-12-31", 1950 * BN, ORDINARY)], ORDINARY)
        sec_2020 = values(old["annual"], "epsDiluted")["2020-12-31"]
        document, info, notices = merge_quietly(old, self.adr_payload())
        self.assertEqual(notices, [])
        self.assertEqual(info["epsBasis"], "rescaled")
        self.assertEqual(document["adrRatio"]["epsFactor"], 5)
        self.assertEqual(document["annual"]["end"], ["2019-12-31", "2020-12-31", "2022-12-31", "2023-12-31",
                                                     "2024-12-31", "2025-12-31"])
        self.assertAlmostEqual(values(document["annual"], "epsDiluted")["2020-12-31"], sec_2020 * 5, delta=0.05)
        self.assertEqual(values(document["annual"], "revenue")["2023-12-31"], 2162 * BN)

    def test_no_overlap_and_no_share_count_ratio_drops_the_sec_eps(self):
        old = sec_file([("2019-12-31", 1900 * BN, ORDINARY), ("2020-12-31", 1950 * BN, ORDINARY)], ORDINARY,
                       eps_scale=3.7)                                       # implied shares off by 3.7: no k
        document, info, notices = merge_quietly(old, self.adr_payload())
        self.assertEqual(info["epsBasis"], "replaced")
        self.assertIn("implied share counts give no ADR ratio", notices[0])
        self.assertIsNone(values(document["annual"], "epsDiluted")["2020-12-31"])
        self.assertIsNotNone(values(document["annual"], "epsDiluted")["2023-12-31"])

    def test_file_without_eps_is_merged_and_judged_on_the_next_run(self):
        first, info, notices = merge_quietly(None, self.adr_payload())
        self.assertEqual((info["epsBasis"], notices), (None, []))
        self.assertEqual(values(first["annual"], "epsDiluted")["2024-12-31"], adr_eps(2894 * BN))
        self.assertNotIn("adrRatio", first)
        second, info, _ = merge_quietly(first, self.adr_payload(), now=NOW + timedelta(days=1))
        self.assertEqual(info["epsBasis"], "unity")
        self.assertEqual(second["adrRatio"]["epsFactor"], 1)

    def test_stored_adr_ratio_is_kept_and_not_judged_again(self):
        # EPS already on the ADR basis, and off by 30% from Yahoo: a new judgement would not pass
        adr_stored = sec_file(synth.TW_ANNUAL[:3], ORDINARY, eps_scale=PER_ADR * 1.3)
        adr_stored["adrRatio"] = {"epsFactor": 5, "basis": "ADR", "derivedAt": "2026-01-02", "pairs": 3}
        before = copy.deepcopy(adr_stored)
        document, info, notices = merge_quietly(adr_stored, self.adr_payload())
        self.assertEqual((info["epsBasis"], notices), (None, []))
        self.assertEqual(document["adrRatio"], before["adrRatio"])
        for end in before["annual"]["end"]:
            self.assertEqual(values(document["annual"], "epsDiluted")[end], values(before["annual"], "epsDiluted")[end])
        self.assertIn("adr-eps-rescaled", document["quality"]["issues"])

    def test_non_foreign_file_is_not_touched(self):
        payload = synth.build("AAPL", synth.US_ANNUAL, [], synth.US_RATIOS, "USD")
        document, info = build(payload, "AAPL")
        self.assertFalse(document["foreignReporter"])
        self.assertNotIn("adrRatio", document)
        self.assertIsNone(info["epsBasis"])
        self.assertNotIn("adr-eps-rescaled", document["quality"]["issues"])

    def test_unresolved_periods_keep_only_their_other_values(self):
        periods = [
            fin.period("2024-12-31", {"revenue": 5, "epsDiluted": 2.0, "dilutedShares": 3}),
            fin.period("2025-12-31", {"epsBasic": 1.0, "dilutedShares": 3}),
        ]
        kept = fin.without_per_share(periods)
        self.assertEqual([(p["end"], p["values"]) for p in kept], [("2024-12-31", {"revenue": 5})])


if __name__ == "__main__":
    unittest.main()

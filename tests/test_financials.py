"""
Financial statement history (財報分析中心): atlas/scripts/build_financials.py.

Offline; every Yahoo payload is synthetic (tests/financials_synth.py writes them to
tests/fixtures/financials/). Run: python3 -m unittest discover -s tests
"""
import copy
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
        self.assertEqual(fin.parse_splits(fixture("yahoo_chart_split.json")), [["2026-06-10", 10]])
        self.assertEqual(fin.parse_splits({"chart": {"result": None}}), [])
        self.assertEqual(fin.parse_splits(None), [])
        reverse = synth.split_chart([(1781098200, 1.0, 20.0)])
        self.assertEqual(fin.parse_splits(reverse), [["2026-06-10", 0.05]])


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
        self.assertEqual(document["split"], {"adjusted": False, "events": []})
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
        self.assertEqual(document["split"], {"adjusted": True, "events": [["2026-06-10", 10]]})
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
        self.assertEqual(document["split"], {"adjusted": False, "events": []})
        self.assertEqual(values(document["quarterly"], "dilutedShares")["2026-04-26"], 2440000000)

    def test_small_stock_dividend_is_ignored(self):
        chart = synth.split_chart([(1781098200, 11.0, 10.0)])
        document, _ = build(fixture("yahoo_split_after_unadjusted.json"), "SPLT",
                            previous=copy.deepcopy(self.before), fetch_splits=self.chart(chart))
        self.assertEqual(document["split"], {"adjusted": False, "events": []})

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


if __name__ == "__main__":
    unittest.main()

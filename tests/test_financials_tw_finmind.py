"""
Taiwan statements from FinMind: atlas/scripts/build_financials_tw_finmind.py (income statement, balance sheet and
cash flow → docs/report/financials/<code>.TW(O).json as "finmind" periods) and its merge with Yahoo's files,
build_financials' official-source rules, and build_growth's tie-break for the FinMind growth history.

Offline: the FinMind answers are synthetic (tests/financials_tw_synth.py, long-table layout); the Yahoo payloads
are the synthetic ones of tests/financials_synth.py. Run: python3 -m unittest discover -s tests
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
TESTS = os.path.join(ROOT, "tests")
for path in (SCRIPTS, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import build_financials as fin  # noqa: E402
import build_financials_tw_finmind as ftw  # noqa: E402
import build_growth as growth  # noqa: E402
import financials_synth as synth  # noqa: E402
import financials_tw_synth as tsynth  # noqa: E402

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
QUIET = lambda *_: None  # noqa: E731


def statements_of(code, mode, seed, years=range(2015, 2026), extra=(), finance=False):
    """Synthetic three datasets (rows by name) and the truth quarters of one stock."""
    quarters = tsynth.truth(seed, years=years, extra=extra, finance=finance)
    rows = tsynth.rows_of(code, tsynth.publish(quarters, mode))
    if finance:
        rows = {name: [row for row in items if row["origin_name"] not in ("營業成本", "營業毛利（毛損）")]
                for name, items in rows.items()}
    return rows, quarters


def parsed_of(code, mode, seed, **kwargs):
    rows, quarters = statements_of(code, mode, seed, **kwargs)
    return ftw.parse_stock(code, rows), quarters


def verdicts_of(codes_seeds, mode):
    held = []
    for code, seed in codes_seeds:
        parsed, _ = parsed_of(code, mode, seed)
        held.append((code, code + ".TW", parsed))
    modes, _ = ftw.decide(held, {"pl": None, "cf": None, "evidence": {}}, None, QUIET)
    return {"pl": modes["pl"], "cf": modes["cf"]}


class ParseTest(unittest.TestCase):
    def test_three_statements_map_to_fields_with_outflows_positive(self):
        parsed, quarters = parsed_of("2330", "ytd", 7)
        pl = parsed["pl"]["2018Q2"]
        self.assertEqual(pl["rev"], quarters[(2018, 1)]["rev"] + quarters[(2018, 2)]["rev"])   # published cumulative
        self.assertEqual(pl["kind"], 0)
        bs = parsed["bs"]["2018Q2"]
        self.assertEqual((bs["totalAssets"], bs["cash"]), (quarters[(2018, 2)]["assets"], quarters[(2018, 2)]["cash"]))
        truth = quarters[(2018, 2)]
        self.assertEqual(bs["debt.short"] + bs["debt.long"] + bs["debt.bonds"],
                         truth["short"] + truth["long"] + truth["bonds"])
        cf = parsed["cf"]["2018Q2"]
        self.assertEqual(cf["capex"], quarters[(2018, 1)]["capex"] + quarters[(2018, 2)]["capex"])
        self.assertEqual(cf["dividendsPaid"], quarters[(2018, 2)]["div"])          # -div in FinMind, stored positive

    def test_unknown_lines_are_counted_and_bad_rows_skipped(self):
        rows, _ = statements_of("2330", "quarter", 3, years=range(2019, 2020), extra=())
        rows["pl"] += [
            {"date": "2019-03-31", "stock_id": "2317", "type": "EPS", "value": 9.0, "origin_name": "基本每股盈餘（元）"},
            {"date": "2019-02-14", "stock_id": "2330", "type": "Revenue", "value": 9, "origin_name": "營業收入"},
            {"date": "2019-06-30", "stock_id": "2330", "type": "OperatingIncome", "value": None,
             "origin_name": "營業利益（損失）"},
            "garbage",
        ]
        parsed = ftw.parse_stock("2330", rows)
        self.assertEqual(dict(parsed["parsed"]["pl"]["skipped"]),
                         {"other stock_id": 1, "date not a quarter end": 1, "no value": 1, "not an object": 1})
        self.assertIn(("NoncontrollingInterests", "淨利（淨損）歸屬於非控制權益"), parsed["parsed"]["pl"]["unmatched"])
        self.assertIn(("Inventories", "存貨"), parsed["parsed"]["bs"]["unmatched"])
        self.assertIn(("InterestReceived", "收取之利息"), parsed["parsed"]["cf"]["unmatched"])

    def test_a_dividend_received_is_not_a_dividend_paid(self):
        rows = {"pl": [], "bs": [], "cf": [
            {"date": "2019-03-31", "stock_id": "2330", "type": "CashDividendsReceived", "value": 500,
             "origin_name": "收取之股利"},
            {"date": "2019-03-31", "stock_id": "2330", "type": "CashDividendsPaid", "value": -300,
             "origin_name": "發放現金股利"},
        ]}
        self.assertEqual(ftw.parse_stock("2330", rows)["cf"]["2019Q1"], {"dividendsPaid": 300})


class DerivationTest(unittest.TestCase):
    def test_each_publication_style_gives_the_single_quarters(self):
        for mode in ("ytd", "quarter", "q4annual"):
            with self.subTest(mode=mode):
                # the income statement verdict follows the data; the cash flow is always year to date (CF_MODE)
                self.assertEqual(verdicts_of([(str(1100 + i), i) for i in range(30)], mode), {"pl": mode, "cf": "ytd"})
                parsed, quarters = parsed_of("2330", mode, 7)
                annual, quarterly, flags = ftw.derive(parsed, {"pl": mode, "cf": mode})
                for (year, quarter), truth in quarters.items():
                    if year > 2025:
                        continue
                    got = quarterly[(year, quarter)]
                    self.assertEqual(got["revenue"], truth["rev"], (year, quarter))
                    self.assertEqual(got["operatingIncome"], truth["oi"])
                    self.assertEqual(got["netIncome"], truth["ni"])
                    self.assertEqual(got["operatingCashFlow"], truth["ocf"])
                    self.assertEqual(got["capex"], truth["capex"])
                    self.assertAlmostEqual(got["epsBasic"], truth["ni"] / tsynth.SHARES, delta=0.001)
                self.assertTrue({"q4-derived", "eps-annual-summed"} <= flags)

    def test_annual_is_the_sum_of_four_singles_and_the_balance_sheet_is_q4(self):
        parsed, quarters = parsed_of("2330", "ytd", 9)
        annual, _, _ = ftw.derive(parsed, {"pl": "ytd", "cf": "ytd"})
        year = 2020
        quarter_rows = [quarters[(year, q)] for q in (1, 2, 3, 4)]
        self.assertEqual(annual[year]["revenue"], sum(row["rev"] for row in quarter_rows))
        self.assertEqual(annual[year]["netIncome"], sum(row["ni"] for row in quarter_rows))
        self.assertEqual(annual[year]["capex"], sum(row["capex"] for row in quarter_rows))
        self.assertEqual(annual[year]["totalAssets"], quarters[(year, 4)]["assets"])
        self.assertAlmostEqual(annual[year]["epsBasic"], sum(row["ni"] for row in quarter_rows) / tsynth.SHARES, delta=0.001)

    def test_missing_first_quarter_leaves_no_single_second_quarter(self):
        rows, quarters = statements_of("2330", "ytd", 5, years=range(2015, 2026), extra=())
        # Q1 2019 cumulative revenue is missing: Q2 2019 cannot be derived (nothing is inferred); Q3 still is (Q3 - Q2)
        rows["pl"] = [row for row in rows["pl"] if not (row["date"] == "2019-03-31" and row["origin_name"] == "營業收入")]
        annual, quarterly, _ = ftw.derive(ftw.parse_stock("2330", rows), {"pl": "ytd", "cf": "ytd"})
        self.assertNotIn("revenue", quarterly[(2019, 1)])
        self.assertNotIn("revenue", quarterly[(2019, 2)])
        self.assertEqual(quarterly[(2019, 3)]["revenue"], quarters[(2019, 3)]["rev"])
        self.assertNotIn("revenue", annual[2019])                  # four singles needed for the year

    def test_too_little_evidence_decides_nothing(self):
        # 11 company-years < 20: no income statement verdict; the cash flow is year to date by regulation
        self.assertEqual(verdicts_of([("1101", 1)], "ytd"), {"pl": None, "cf": "ytd"})

    def test_percent_of_total_columns_are_skipped(self):
        rows = [{"stock_id": "2330", "date": "2024-12-31", "type": "TotalAssets", "origin_name": "資產總額",
                 "value": 6691764700000},
                {"stock_id": "2330", "date": "2024-12-31", "type": "TotalAssets_per", "origin_name": "資產總額",
                 "value": 100}]
        for order in (rows, rows[::-1]):
            parsed = ftw.parse_dataset("bs", order, "2330")
            self.assertEqual(parsed["fields"]["2024Q4"]["totalAssets"], 6691764700000)
            self.assertEqual(parsed["skipped"]["percent of total column"], 1)
            self.assertEqual(parsed["conflicts"], 0)

    def test_tickers_plan_is_exactly_those_stocks(self):
        state = {"fetch": {"2330": ["2026-10-01", "2026Q2", "ok"]}}
        chosen = ftw.plan(state, ["1101", "2317", "2330"], TODAY, {"2330", "2317"})
        self.assertEqual(chosen, [("2317", "new"), ("2330", "forced")])

    def test_eps_of_a_quarter_without_its_cumulative_eps_stays_empty(self):
        self.assertEqual(ftw.eps_singles({1: 1.0, 2: None}, {1: 10.0, 2: 20.0}, "ytd"), {1: 1.0})


class CumulativeAndAnnualEpsTest(unittest.TestCase):
    """Reuse the growth builder's F1/F2 helpers when producing full financial statements."""

    MODES = {"pl": "quarter", "cf": "ytd"}

    @staticmethod
    def stock(quarters):
        return {
            "pl": {f"{year}Q{quarter}": {"rev": rev, "eps": eps, "ni": ni, "kind": 0,
                                           "operatingIncome": oi}
                   for year, quarter, rev, eps, ni, oi in quarters},
            "bs": {}, "cf": {},
        }

    def test_single_quarters_after_a_gap_are_not_emitted(self):
        stock = self.stock([
            (2024, 2, 200, 2.0, 20, 20), (2024, 4, 400, 4.0, 40, 40),  # 0101 before listing
            (2025, 1, 100, 1.0, 10, 10), (2025, 2, 110, 1.1, 11, 11),
            (2025, 3, 120, 1.2, 12, 12), (2025, 4, 130, 1.3, 13, 13),
        ])
        annual, quarterly, _ = ftw.derive(stock, self.MODES)
        for period in ((2024, 2), (2024, 4)):
            self.assertFalse(quarterly.get(period, {}))
        self.assertEqual(quarterly[(2025, 2)]["revenue"], 110)
        self.assertEqual(quarterly[(2025, 4)]["revenue"], 130)

        # 0111 drops the first half in Q2 but retains Q3/Q4; 0001 drops the full-year Q4.
        partial = self.stock([(2024, 2, 200, 2.0, 20, 20), (2024, 3, 120, 1.2, 12, 12),
                              (2024, 4, 130, 1.3, 13, 13)])
        _, quarterly, _ = ftw.derive(partial, self.MODES)
        self.assertFalse(quarterly.get((2024, 2), {}))
        self.assertEqual(quarterly[(2024, 3)]["revenue"], 120)
        self.assertEqual(quarterly[(2024, 4)]["revenue"], 130)
        annual_only = self.stock([(2024, 4, 400, 4.0, 40, 40)])
        _, quarterly, _ = ftw.derive(annual_only, self.MODES)
        self.assertFalse(quarterly.get((2024, 4), {}))

    def test_field_views_share_the_growth_builders_gap_rule(self):
        for mode in ("quarter", "q4annual"):
            with self.subTest(mode=mode):
                single, _ = ftw.views({2: 200, 4: 400}, mode, quarters_of_year={2: {}, 4: {}})
                self.assertNotIn(2, single)
                self.assertNotIn(4, single)
                single, _ = ftw.views({2: 200, 3: 120, 4: 130}, mode,
                                      quarters_of_year={2: {}, 3: {}, 4: {}})
                self.assertNotIn(2, single)
                self.assertEqual(single.get(3), 120)
                if mode == "quarter":
                    self.assertEqual(single.get(4), 130)

    def test_annual_eps_uses_the_official_full_year_value(self):
        stock = self.stock([(2025, 1, 100, 1.0, 10, 10), (2025, 2, 100, 1.0, 10, 10),
                            (2025, 3, 100, 1.0, 20, 20), (2025, 4, 100, 1.0, 20, 20)])
        official_store = ftw.tw.empty_store()
        official_store["data"] = {"2330": {"2025Q4": [400, 3.5, 60, 0]}}
        official = ftw.tw.dump_store(official_store, ["2025Q4"])
        by_code = ftw.fmo.official_annual_eps(official)

        annual, _, flags = ftw.derive(stock, self.MODES, by_code["2330"])
        self.assertEqual(annual[2025]["epsBasic"], 3.5)
        self.assertEqual(annual[2025]["revenue"], 400)
        self.assertNotIn("eps-annual-summed", flags)

    def test_annual_eps_sum_requires_stable_q1_to_q3_share_counts(self):
        stable = self.stock([(2025, 1, 100, 1.0, 10.0, 10), (2025, 2, 100, 1.0, 10.0, 10),
                             (2025, 3, 100, 1.0, 10.05, 10), (2025, 4, 100, 1.0, 10.0, 10)])
        annual, _, flags = ftw.derive(stable, self.MODES)
        self.assertEqual(annual[2025]["epsBasic"], 4.0)
        self.assertIn("eps-annual-summed", flags)

        unstable = self.stock([(2025, 1, 100, 1.0, 10.0, 10), (2025, 2, 100, 1.0, 10.0, 10),
                               (2025, 3, 100, 1.0, 10.5, 10), (2025, 4, 100, 1.0, 10.0, 10)])
        annual, _, flags = ftw.derive(unstable, self.MODES)
        self.assertIsNone(annual[2025].get("epsBasic"))
        self.assertEqual(annual[2025]["revenue"], 400)
        self.assertNotIn("eps-annual-summed", flags)


class FinanceAndDebtTest(unittest.TestCase):
    def test_financial_company_has_no_gross_profit_and_is_marked_so(self):
        rows, _ = statements_of("2881", "quarter", 11, finance=True)
        annual, quarterly, flags = ftw.derive(ftw.parse_stock("2881", rows), {"pl": "quarter", "cf": "quarter"})
        annual_p, quarterly_p = ftw.periods_of(annual, quarterly)
        out = tempfile.mkdtemp()
        self.assertEqual(ftw.write_stock(out, "2881.TW", annual_p, quarterly_p, flags, NOW, TODAY), "written")
        with open(os.path.join(out, "2881.TW.json"), encoding="utf-8") as file:
            document = json.load(file)
        self.assertIn("financial-sector-format", document["quality"]["issues"])
        self.assertNotIn("grossProfit", document["quarterly"]["values"])

    def test_debt_is_approximate_when_lines_exist_and_missing_when_not(self):
        rows, _ = statements_of("2330", "quarter", 4, years=range(2019, 2020), extra=())
        annual, quarterly, flags = ftw.derive(ftw.parse_stock("2330", rows), {"pl": "quarter", "cf": "quarter"})
        self.assertIn("debt-approx", flags)
        self.assertNotIn("debt-missing", flags)
        self.assertIn("totalDebt", quarterly[(2019, 1)])
        rows["bs"] = [row for row in rows["bs"] if row["origin_name"] not in ("短期借款", "長期借款", "應付公司債")]
        annual, quarterly, flags = ftw.derive(ftw.parse_stock("2330", rows), {"pl": "quarter", "cf": "quarter"})
        self.assertIn("debt-missing", flags)
        self.assertNotIn("totalDebt", quarterly[(2019, 1)])


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name
        self.yahoo_payload = synth.build("2330.TW", synth.TW_ANNUAL, synth.TW_QUARTERLY, synth.TW_RATIOS, "TWD")

    def tearDown(self):
        self.tmp.cleanup()

    def path(self):
        return os.path.join(self.out, "2330.TW.json")

    def read(self):
        with open(self.path(), encoding="utf-8") as file:
            return json.load(file)

    def write_yahoo_file(self, split=None):
        document, _ = fin.update_document(None, copy.deepcopy(self.yahoo_payload), "2330.TW", NOW)
        document["checkedAt"] = "2026-01-05"
        if split is not None:
            document["split"] = split
        fin.write_atomic(self.path(), document)
        return document

    def finmind_periods(self):
        parsed, _ = parsed_of("2330", "ytd", 13, years=range(2015, 2026), extra=())
        annual, quarterly, flags = ftw.derive(parsed, {"pl": "ytd", "cf": "ytd"})
        annual_p, quarterly_p = ftw.periods_of(annual, quarterly)
        return annual_p, quarterly_p, flags

    def test_finmind_wins_yahoo_fills_gaps_and_newer_quarters_are_added(self):
        self.write_yahoo_file(split={"adjusted": True, "events": [["2025-06-01", 10.0]]})
        annual, quarterly, flags = self.finmind_periods()
        self.assertEqual(ftw.write_stock(self.out, "2330.TW", annual, quarterly, flags, NOW, TODAY), "written")
        document = self.read()
        self.assertEqual(document["source"], fin.SOURCE_FINMIND_MIXED)
        revenue = dict(zip(document["annual"]["end"], document["annual"]["values"]["revenue"]))
        truth = tsynth.truth(13, years=range(2015, 2026), extra=())
        self.assertEqual(revenue["2025-12-31"], sum(truth[(2025, q)]["rev"] for q in (1, 2, 3, 4)))
        self.assertNotEqual(revenue["2025-12-31"], 3600 * synth.BN)                 # Yahoo's FY2025 value
        src = dict(zip(document["quarterly"]["end"], document["quarterly"]["src"]))
        self.assertEqual(src["2025-12-31"], "finmind")
        self.assertEqual(src["2026-03-31"], "yahoo")                              # newer than the official periods
        self.assertEqual(document["split"], {"adjusted": True, "events": [["2025-06-01", 10.0]]})
        self.assertEqual(document["checkedAt"], TODAY.isoformat())                # content changed: today
        self.assertIn("q4-derived", document["quality"]["issues"])
        self.assertIn("eps-annual-summed", document["quality"]["issues"])

    def test_same_content_again_is_unchanged_and_keeps_the_checked_day(self):
        self.write_yahoo_file()
        annual, quarterly, flags = self.finmind_periods()
        ftw.write_stock(self.out, "2330.TW", annual, quarterly, flags, NOW, TODAY)
        status = ftw.write_stock(self.out, "2330.TW", annual, quarterly, flags, NOW + timedelta(days=1),
                                 TODAY + timedelta(days=1))
        self.assertEqual(status, "unchanged")
        self.assertEqual(self.read()["checkedAt"], TODAY.isoformat())

    def test_a_file_without_yahoo_is_finmind_only_and_a_corrupt_file_is_never_touched(self):
        annual, quarterly, flags = self.finmind_periods()
        self.assertEqual(ftw.write_stock(self.out, "2330.TW", annual, quarterly, flags, NOW, TODAY), "written")
        self.assertEqual(self.read()["source"], fin.SOURCE_FINMIND)
        corrupt = os.path.join(self.out, "2881.TW.json")
        with open(corrupt, "w", encoding="utf-8") as file:
            file.write("{not json")
        self.assertEqual(ftw.write_stock(self.out, "2881.TW", annual, quarterly, flags, NOW, TODAY), "corrupt")
        with open(corrupt, encoding="utf-8") as file:
            self.assertEqual(file.read(), "{not json")

    def test_yahoo_daily_update_never_overwrites_finmind_values(self):
        annual, quarterly, flags = self.finmind_periods()
        ftw.write_stock(self.out, "2330.TW", annual, quarterly, flags, NOW, TODAY)
        stored = self.read()
        changed = copy.deepcopy(self.yahoo_payload)
        for entry in changed["timeseries"]["result"]:
            if entry["meta"]["type"][0] == "annualTotalRevenue":
                for item in entry["annualTotalRevenue"]:
                    item["reportedValue"]["raw"] = item["reportedValue"]["raw"] * 2
        document, _ = fin.update_document(stored, changed, "2330.TW", NOW)
        before = dict(zip(stored["annual"]["end"], stored["annual"]["values"]["revenue"]))
        after = dict(zip(document["annual"]["end"], document["annual"]["values"]["revenue"]))
        self.assertEqual(after["2025-12-31"], before["2025-12-31"])
        self.assertEqual(document["source"], fin.SOURCE_FINMIND_MIXED)
        self.assertIn("eps-annual-summed", document["quality"]["issues"])          # carried across the Yahoo rewrite
        self.assertIn("q4-derived", document["quality"]["issues"])

    def test_yahoo_fills_null_official_values_only(self):
        old = [fin.period("2025-03-31", {"revenue": 5, "netIncome": None}, "finmind")]
        new = [fin.period("2025-03-31", {"revenue": 7, "netIncome": 3}, "yahoo")]
        filled = []
        merged, revisions = fin.merge_section(old, new, "quarterly", TODAY.isoformat(), None, filled)
        self.assertEqual(merged[0]["values"], {"revenue": 5, "netIncome": 3})
        self.assertEqual(filled, [("2025-03-31", "netIncome")])
        self.assertEqual([item["kind"] for item in revisions], ["yahoo-differs"])
        self.assertEqual(fin.source_label(merged, filled), fin.SOURCE_FINMIND_MIXED)
        self.assertEqual(fin.source_label(merged, []), fin.SOURCE_FINMIND)
        self.assertEqual(fin.source_label([fin.period("2025-03-31", {}, "finmind")], []), fin.SOURCE_FINMIND)

    def test_only_files_with_official_periods_are_protected_from_the_unlisted_rule(self):
        annual, quarterly, flags = self.finmind_periods()
        ftw.write_stock(self.out, "2330.TW", annual, quarterly, flags, NOW, TODAY)
        self.assertTrue(fin.has_official(self.read()))
        yahoo_only = fin.update_document(None, copy.deepcopy(self.yahoo_payload), "2330.TW", NOW)[0]
        self.assertFalse(fin.has_official(yahoo_only))


class FakeHttp:
    """FinMind stand-in: {(dataset, code): rows | (status, body) | callable(query)}; records requests."""

    def __init__(self, answers, clock):
        self.answers, self.clock, self.requests = answers, clock, []

    def get(self, url, headers):
        query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
        self.requests.append((query["dataset"], query["data_id"], headers))
        self.clock.now += 0.5
        answer = self.answers.get((query["dataset"], query["data_id"]), [])
        if callable(answer):
            answer = answer(query)
        if isinstance(answer, tuple):
            if isinstance(answer[0], Exception):
                raise answer[0]
            return answer
        return 200, json.dumps({"msg": "success", "status": 200, "data": answer}).encode()


class Clock:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class RunTest(unittest.TestCase):
    CODES = ["1101", "1102", "1103", "1104", "1105", "1106", "1107", "2881", "6488"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.report = self.tmp.name
        os.makedirs(os.path.join(self.report, "fundamentals"))
        os.makedirs(os.path.join(self.report, "financials"))
        rows = [[f"{code}.TWO" if code == "6488" else f"{code}.TW", "industry"] for code in self.CODES]
        rows.append(["00631L.TW", "ETF"])                       # ETFs have no statements: never requested
        with open(os.path.join(self.report, "universe.json"), "w", encoding="utf-8") as file:
            json.dump({"fields": ["symbol", "industry"], "markets": {"TW": {"rows": rows}}}, file)
        self.answers = {}
        for index, code in enumerate(self.CODES):
            statements, _ = statements_of(code, "ytd", 100 + index, finance=(code == "2881"))
            for name, dataset in ftw.DATASETS.items():
                self.answers[(dataset, code)] = statements[name]
        self.saved = (ftw.WARMUP_STOCKS, ftw.WARMUP_LIMIT)
        ftw.WARMUP_STOCKS, ftw.WARMUP_LIMIT = 5, 20            # five stocks decide the verdicts in these tests

    def tearDown(self):
        ftw.WARMUP_STOCKS, ftw.WARMUP_LIMIT = self.saved
        self.tmp.cleanup()

    def args(self, **kwargs):
        values = {"report_dir": self.report, "out_dir": None, "budget": 3600.0, "limit": None, "tickers": None,
                  "probe": None, "dry_run": False}
        values.update(kwargs)
        return SimpleNamespace(**values)

    def go(self, answers=None, **kwargs):
        clock, logs = Clock(), []
        http = FakeHttp(self.answers if answers is None else answers, clock)
        code = ftw.run(self.args(**kwargs), http=http, now=NOW, sleep=clock.sleep, clock=clock, log=logs.append,
                       token="")
        return code, http, logs

    def state(self):
        with open(os.path.join(self.report, "fundamentals", ftw.STATE_FILE), encoding="utf-8") as file:
            return json.load(file)

    def file_of(self, symbol):
        return os.path.join(self.report, "financials", symbol + ".json")

    def test_full_run_writes_every_stock_then_the_second_run_asks_nothing(self):
        code, http, logs = self.go()
        self.assertEqual(code, 0)
        self.assertEqual(len(http.requests), 3 * len(self.CODES))                # 9 stocks, the ETF skipped
        self.assertNotIn("00631L", {req[1] for req in http.requests})
        self.assertTrue(any(line.startswith("FinMind statements DONE") for line in logs), logs[-2:])
        self.assertTrue(os.path.exists(self.file_of("1101.TW")))
        self.assertTrue(os.path.exists(self.file_of("6488.TWO")))
        state = self.state()
        self.assertEqual((state["modes"]["pl"], state["modes"]["cf"]), ("ytd", "ytd"))
        self.assertIn("EPS", state["types"]["pl"])
        code, http, _ = self.go()
        self.assertEqual((code, http.requests), (0, []))

    def test_budget_pauses_after_the_first_decided_stocks_and_the_next_run_finishes(self):
        code, http, logs = self.go(budget=200.0)
        self.assertEqual(code, 0)
        self.assertLess(len(http.requests), 3 * len(self.CODES))
        self.assertTrue(any(line.startswith("PAUSED: time budget") for line in logs), logs[-3:])
        self.assertEqual(len(self.state()["fetch"]), 5)                           # the first five decided the verdicts
        code, http, logs = self.go()
        self.assertEqual(len(http.requests), 3 * (len(self.CODES) - 5))          # the partly fetched stock is asked again
        self.assertTrue(any(line.startswith("FinMind statements DONE") for line in logs), logs[-2:])

    def test_token_refused_stops_the_run_with_status_one(self):
        answers = dict(self.answers)
        answers[(ftw.DATASETS["bs"], self.CODES[2])] = (403, b"Forbidden")
        code, _, logs = self.go(answers=answers)
        self.assertEqual(code, 1)
        self.assertTrue(any(line.startswith("STOPPED: FinMind refused") for line in logs), logs[-3:])

    def test_request_limit_is_a_pause_and_keeps_the_stocks_already_written(self):
        answers = dict(self.answers)
        answers[(ftw.DATASETS["pl"], "1107")] = (402, json.dumps(
            {"msg": "Requests reach the upper limit.", "status": 402}).encode())
        code, _, logs = self.go(answers=answers)
        self.assertEqual(code, 0)
        self.assertTrue(any(line.startswith("PAUSED: FinMind request limit") for line in logs), logs[-3:])
        self.assertTrue(os.path.exists(self.file_of("1101.TW")))
        self.assertNotIn("1107", self.state()["fetch"])

    def test_limit_counts_requests(self):
        code, http, logs = self.go(limit=4)
        self.assertEqual(len(http.requests), 4)
        self.assertTrue(any(line.startswith("PAUSED: --limit 4") for line in logs), logs[-3:])

    def test_probe_prints_every_type_and_writes_nothing(self):
        code, http, logs = self.go(probe=["1101", "2881"])
        self.assertEqual((code, len(http.requests)), (0, 6))
        text = "\n".join(logs)
        self.assertIn("CashDividendsPaid | 發放現金股利", text)
        self.assertIn("dividendsPaid(0)", text)
        self.assertIn("NoncontrollingInterests | 淨利（淨損）歸屬於非控制權益 | ", text)
        self.assertIn("— (not used)", text)
        self.assertIn("nothing written", text)
        self.assertFalse(os.path.exists(self.file_of("1101.TW")))
        self.assertFalse(os.path.exists(os.path.join(self.report, "fundamentals", ftw.STATE_FILE)))

    def test_dry_run_is_offline_and_states_the_estimate(self):
        code, http, logs = self.go(dry_run=True)
        self.assertEqual((code, http.requests), (0, []))
        text = "\n".join(logs)
        self.assertIn("dry run (offline): 9 listed stocks, 9 to fetch now", text)
        self.assertIn("full backfill: 9 stocks x 3 = 27 requests", text)
        self.assertIn("300 per hour without", text)
        self.assertFalse(os.path.exists(os.path.join(self.report, "fundamentals", ftw.STATE_FILE)))

    def test_dry_run_limits_the_plan_to_the_requested_tickers(self):
        code, http, logs = self.go(dry_run=True, tickers=["1102", "2881"])
        self.assertEqual((code, http.requests), (0, []))
        text = "\n".join(logs)
        self.assertIn("2 to fetch now", text)
        self.assertIn("6 requests", text)
        self.assertIn("first stocks: 1102, 2881", text)
        self.assertFalse(os.path.exists(os.path.join(self.report, "fundamentals", ftw.STATE_FILE)))

    def test_only_after_backfill_waits_for_the_manual_backfill(self):
        code, http, logs = self.go(only_after_backfill=True)
        self.assertEqual((code, http.requests), (0, []))
        self.assertTrue(any("backfill not finished (0/9 stocks fetched)" in line for line in logs), logs)
        code, _, _ = self.go()                                                  # the manual backfill
        self.assertEqual(code, 0)
        code, http, logs = self.go(only_after_backfill=True)                    # now the daily run goes ahead
        self.assertEqual(code, 0)
        self.assertFalse(any("backfill not finished" in line for line in logs), logs)

    def test_undecided_verdicts_stop_the_run_and_write_nothing(self):
        ftw.WARMUP_LIMIT = 5
        answers = dict(self.answers)
        for code in self.CODES:                                                  # one EPS line per stock: no evidence
            answers[(ftw.DATASETS["pl"], code)] = [row for row in self.answers[(ftw.DATASETS["pl"], code)]
                                                   if row["origin_name"] == "基本每股盈餘（元）"][:1]
        code, _, logs = self.go(answers=answers)
        self.assertEqual(code, 1)
        self.assertTrue(any("not decided" in line for line in logs), logs[-3:])
        self.assertFalse(os.path.exists(self.file_of("1101.TW")))


class PlanTest(unittest.TestCase):
    def test_never_fetched_first_then_behind_then_empty_and_current_skipped(self):
        target = ftw.fmo.due_season(TODAY)
        state = ftw.load_state(None)
        state["fetch"] = {
            "1101": [TODAY.isoformat(), target, "ok"],                                   # current: skipped
            "1102": [(TODAY - timedelta(days=10)).isoformat(), "2026Q1", "ok"],          # a quarter behind
            "1103": [(TODAY - timedelta(days=40)).isoformat(), None, "empty"],           # rechecked after 30 days
            "1104": [(TODAY - timedelta(days=2)).isoformat(), None, "empty"],            # not yet
        }
        chosen = ftw.plan(state, ["1101", "1102", "1103", "1104", "1105"], TODAY)
        self.assertEqual(chosen, [("1105", "new"), ("1102", f"behind {target}"), ("1103", "empty")])

    def test_a_state_file_of_another_version_is_refused(self):
        with self.assertRaises(ftw.StoreError):
            ftw.load_state({"version": 9})


class GrowthTieTest(unittest.TestCase):
    def finmind_document(self, eps):
        ends = [ftw.tw.quarter_end(year, quarter).isoformat() for year, quarter in
                [(2024, 1), (2024, 2), (2024, 3), (2024, 4), (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2)]]
        return {"version": 1, "quality": {"level": "good"}, "annual": {"end": [], "fy": []},
                "quarterly": {"end": ends, "fy": [2024] * 4 + [2025] * 4 + [2026] * 2,
                              "fq": [1, 2, 3, 4, 1, 2, 3, 4, 1, 2], "src": ["finmind"] * 10,
                              "values": {"epsBasic": eps, "revenue": [100 + i for i in range(10)]}}}

    def test_statement_files_with_finmind_src_are_finmind_candidates(self):
        document = self.finmind_document([1.0 + i * 0.1 for i in range(10)])
        level, quarterly, _ = growth.financials_blocks(document, date(2026, 10, 15))
        self.assertEqual(quarterly["src"], "finmind")
        self.assertEqual(level, "good")

    def test_on_a_tie_of_source_tier_the_growth_history_candidate_wins(self):
        document = self.finmind_document([1.0 + i * 0.1 for i in range(10)])
        growth_history = (None, {"eps": [9.0], "rev": [], "mask": 0, "end": "2026-06-30", "src": "finmind",
                                 "derived": 0}, None)
        result = growth.build_document([("2330.TW", document)], NOW, {"2330.TW": [growth_history]})
        row = dict(zip(growth.FIELDS, result["rows"][0]))
        self.assertEqual(row["src"], "finmind")
        self.assertEqual(row["epsQ"], [9.0])                                     # not the statement file's YoY
        without = growth.build_document([("2330.TW", document)], NOW, {})
        self.assertNotEqual(dict(zip(growth.FIELDS, without["rows"][0]))["epsQ"], [9.0])


if __name__ == "__main__":
    unittest.main()

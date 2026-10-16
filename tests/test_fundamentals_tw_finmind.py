"""
FinMind Taiwan history: atlas/scripts/build_fundamentals_tw_finmind.py (TaiwanStockFinancialStatements →
docs/report/fundamentals/tw-finmind.json) and its quarters / YoY for growth.json (src "finmind").

Offline: the FinMind answers are synthetic fixtures in tests/fixtures/fundamentals/finmind_*.json written in
the documented long-table layout (date / stock_id / type / value / origin_name); the mode tests publish one
synthetic truth as single quarters, as year-to-date figures and as "Q1-Q3 single, Q4 full year".
Run: python3 -m unittest discover -s tests
"""
import json
import os
import random
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_fundamentals_tw as tw  # noqa: E402
import build_fundamentals_tw_finmind as fm  # noqa: E402
import build_growth as growth  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "fundamentals")
TODAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
ENDS = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
QUIET = lambda *_: None  # noqa: E731


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as file:
        return json.load(file)


def truth(seed, years=range(2016, 2026), extra=((2026, 1), (2026, 2)), shares=1000.0):
    """Synthetic single-quarter truth {(y, q): (rev, eps, ni)}: growing, seasonal, always profitable."""
    rng = random.Random(seed)
    level, margin = rng.uniform(1e6, 5e7), rng.uniform(0.05, 0.2)
    result = {}
    for year, quarter in [(y, q) for y in years for q in (1, 2, 3, 4)] + list(extra):
        level *= rng.uniform(0.97, 1.08)
        rev = round(level * (0.9 + 0.1 * quarter))
        ni = round(rev * margin * rng.uniform(0.9, 1.1))
        result[(year, quarter)] = (rev, round(ni / shares, 2), ni)
    return result


def publish(quarters, mode):
    """Single-quarter truth → {(y, q): (rev, eps, ni)} as published in `mode` (quarter / ytd / q4annual)."""
    if mode == "quarter":
        return dict(quarters)
    result = {}
    for (year, quarter), values in quarters.items():
        if mode == "q4annual" and quarter < 4:
            result[(year, quarter)] = values
            continue
        parts = [quarters.get((year, q)) for q in range(1, quarter + 1)]
        if None in parts:
            continue
        result[(year, quarter)] = (sum(p[0] for p in parts), round(sum(p[1] for p in parts), 2), sum(p[2] for p in parts))
    return result


def finmind_rows(code, published, revenue_name="營業收入"):
    rows = []
    for (year, quarter), (rev, eps, ni) in sorted(published.items()):
        day = f"{year}-{ENDS[quarter]}"
        rows += [
            {"date": day, "stock_id": code, "type": "Revenue", "value": rev, "origin_name": revenue_name},
            {"date": day, "stock_id": code, "type": "EPS", "value": eps, "origin_name": "基本每股盈餘（元）"},
            {"date": day, "stock_id": code, "type": "EquityAttributableToOwnersOfParent", "value": ni,
             "origin_name": "淨利（淨損）歸屬於母公司業主"},
        ]
    return rows


def store_of(published_by_code):
    """{code: published} → in-memory store (kind 0)."""
    return {"fetch": {}, "types": {}, "data": {
        code: {tw.season_key(*key): [rev, eps, ni, 0] for key, (rev, eps, ni) in published.items()}
        for code, published in published_by_code.items()}}


def market(mode, count=30, **kwargs):
    truths = {f"{1100 + i}": truth(i, **kwargs) for i in range(count)}
    return truths, store_of({code: publish(values, mode) for code, values in truths.items()})


def official_of(truths, keys=("2026Q2",), scale=1000.0):
    """tw.json document with the year-to-date revenue / EPS / NI of `keys` (NT$ thousand when scale = 1000)."""
    store = {"fetch": {}, "data": {}}
    for code, quarters in truths.items():
        for key in keys:
            year, quarter = tw.parse_season(key)
            parts = [quarters[(year, q)] for q in range(1, quarter + 1)]
            store["data"].setdefault(code, {})[key] = [
                round(sum(p[0] for p in parts) / scale), round(sum(p[1] for p in parts), 2),
                round(sum(p[2] for p in parts) / scale), 0]
    return tw.dump_store(store, tw.target_seasons(TODAY))


class ResponseTest(unittest.TestCase):
    def test_success_and_every_kind_of_refusal(self):
        self.assertEqual(fm.parse_response(200, json.dumps({"msg": "success", "status": 200, "data": [{"a": 1}]}).encode()),
                         [{"a": 1}])
        self.assertEqual(fm.parse_response(200, b'{"msg": "success", "status": 200, "data": []}'), [])
        limit = json.dumps(fixture("finmind_limit.json")).encode()
        for status in (402, 200):
            with self.assertRaises(fm.RateLimited) as caught:
                fm.parse_response(status, limit)
            self.assertIn("upper limit", str(caught.exception))
        with self.assertRaises(fm.RateLimited):
            fm.parse_response(429, b"<html>Too Many Requests</html>")
        for status, body in ((401, b'{"msg": "Token is invalid", "status": 401}'),
                             (403, b"Forbidden"), (400, b'{"msg": "your token is expired", "status": 400}')):
            with self.assertRaises(fm.AuthError):
                fm.parse_response(status, body)
        with self.assertRaises(fm.FinMindError) as caught:
            fm.parse_response(400, b'{"msg": "parameter error: dataset", "status": 400}')
        self.assertFalse(caught.exception.transient)
        self.assertIn("parameter error", str(caught.exception))
        for status, body in ((500, b"Internal Server Error"), (502, b""), (200, b"<html>maintenance</html>")):
            with self.assertRaises(fm.FinMindError) as caught:
                fm.parse_response(status, body)
            self.assertTrue(caught.exception.transient, status)
        with self.assertRaises(fm.FinMindError) as caught:
            fm.parse_response(200, b'{"msg": "success", "status": 200}')
        self.assertIn("no 'data' list", str(caught.exception))
        # HTTP 200 with an empty list but a msg: a refusal, never "fetched, nothing published"
        with self.assertRaises(fm.RateLimited):
            fm.parse_response(200, b'{"msg": "Upper limit reached", "status": 200, "data": []}')
        with self.assertRaises(fm.AuthError):
            fm.parse_response(200, b'{"msg": "Token expired", "status": 200, "data": []}')
        with self.assertRaises(fm.FinMindError) as caught:
            fm.parse_response(200, b'{"msg": "unknown notice", "status": 200, "data": []}')
        self.assertFalse(caught.exception.transient)


class ParseTest(unittest.TestCase):
    def test_general_format_picks_eps_parent_net_income_and_revenue(self):
        parsed = fm.parse_rows(fixture("finmind_2330.json")["data"], "2330")
        self.assertEqual(sorted(parsed["entries"]), ["2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"])
        # parent net income (not 本期淨利 / 綜合損益 / 非控制權益), basic EPS, 營業收入 (kind 0)
        self.assertEqual(parsed["entries"]["2026Q2"], [1270380250000, 27.25, 706600000000, 0])
        self.assertEqual(parsed["types"]["EPS"], 6)
        self.assertEqual(parsed["names"]["Revenue"], "營業收入")
        self.assertEqual(parsed["conflicts"], 0)
        self.assertFalse(parsed["skipped"])

    def test_financial_formats(self):
        holding = fm.parse_rows(fixture("finmind_2881.json")["data"], "2881")["entries"]
        # 淨收益 (kind 1) wins over the 利息淨收益 + 利息以外淨損益 pair; parent NI by name
        self.assertEqual(holding["2025Q1"][3], 1)
        self.assertEqual(holding["2025Q1"][0], 70000000000)
        self.assertEqual(holding["2025Q1"][2], 25000000000)
        bank = fm.parse_rows(fixture("finmind_2801.json")["data"], "2801")["entries"]
        self.assertEqual(bank["2025Q2"][3], fm.INTEREST_KIND)
        self.assertEqual(bank["2025Q2"][0], 8200000000 + 2000000000)
        self.assertEqual(bank["2025Q2"][2], 3520000000)          # 本期稅後淨利 when there is no parent line
        broker = fm.parse_rows(fixture("finmind_6005.json")["data"], "6005")["entries"]
        self.assertEqual((broker["2026Q1"][3], broker["2026Q1"][2]), (2, 1550000000))
        self.assertEqual(tw.REVENUE_KINDS[2], "收益")

    def test_bad_rows_are_skipped_and_counted(self):
        rows = finmind_rows("2330", {(2025, 1): (100, 1.0, 10)}) + [
            {"date": "2025-03-31", "stock_id": "2317", "type": "EPS", "value": 9.0, "origin_name": "基本每股盈餘（元）"},
            {"date": "2025-02-14", "stock_id": "2330", "type": "EPS", "value": 9.0, "origin_name": "基本每股盈餘（元）"},
            {"date": "2025-06-30", "stock_id": "2330", "type": "EPS", "value": None, "origin_name": "基本每股盈餘（元）"},
            {"date": "2025-03-31", "stock_id": "2330", "type": "EPS", "value": 0.9, "origin_name": "稀釋每股盈餘（元）"},
            {"date": "2025-03-31", "stock_id": "2330", "type": "X", "value": 77, "origin_name": "綜合損益總額歸屬於母公司業主"},
            "garbage",
        ]
        parsed = fm.parse_rows(rows, "2330")
        self.assertEqual(parsed["entries"], {"2025Q1": [100, 1.0, 10, 0]})
        self.assertEqual(dict(parsed["skipped"]), {"other stock_id": 1, "date not a quarter end": 1, "no value": 1,
                                                   "not an object": 1})

    def test_one_revenue_kind_per_stock(self):
        rows = finmind_rows("2882", {(2025, q): (100 + q, 1.0, 10) for q in (1, 2, 3)}, revenue_name="淨收益")
        rows.append({"date": "2025-06-30", "stock_id": "2882", "type": "OperatingRevenue", "value": 5,
                     "origin_name": "營業收入合計"})
        entries = fm.parse_rows(rows, "2882")["entries"]
        self.assertEqual([entries[key][3] for key in sorted(entries)], [1, 1, 1])
        self.assertEqual(entries["2025Q2"][0], 102)


class ModeTest(unittest.TestCase):
    def test_each_publication_style_is_recognised_with_evidence(self):
        for mode in fm.MODES:
            with self.subTest(mode=mode):
                _, store = market(mode)
                modes, report = fm.detect_modes(store["data"])
                self.assertEqual(modes, {"rev": mode, "eps": mode, "ni": mode})
                self.assertGreaterEqual(report["rev"]["n"], 250)
                lines = fm.describe_modes(report)
                self.assertIn(f"verdict {mode}", lines[0])
                self.assertIn("median Q4/Q1", lines[0])

    def test_too_little_or_mixed_evidence_is_not_used(self):
        _, small = market("ytd", count=1, years=range(2023, 2026))
        modes, report = fm.detect_modes(small["data"])
        self.assertEqual(report["rev"]["verdict"], "unknown")
        self.assertEqual(modes, {"rev": None, "eps": None, "ni": None})
        _, ytd = market("ytd", count=15)
        _, single = market("quarter", count=15)
        mixed = {"data": {**ytd["data"], **{str(int(code) + 100): row for code, row in single["data"].items()}}}
        modes, report = fm.detect_modes(mixed["data"])
        self.assertEqual(report["rev"]["verdict"], "ambiguous")
        self.assertIsNone(modes["rev"])
        result, info = fm.growth_candidates(fm.dump_store({**mixed, "fetch": {}, "types": {}}), {}, TODAY, log=QUIET)
        self.assertEqual((result, info["modes"]["rev"]), ({}, None))

    def test_eps_without_own_evidence_follows_revenue(self):
        _, store = market("quarter")
        for row in store["data"].values():
            for key, entry in row.items():
                entry[1] = -abs(entry[1]) if key.endswith("Q2") else entry[1]   # no year with four positive EPS
        modes, report = fm.detect_modes(store["data"])
        self.assertEqual(modes["eps"], "quarter")
        self.assertEqual(report["eps"]["assumed"], "quarter")

    def test_stocks_contradicting_the_verdict_are_listed(self):
        truths, store = market("quarter")
        store["data"]["9999"] = store_of({"9999": publish(truths["1100"], "ytd")})["data"]["9999"]
        self.assertEqual(fm.contradicting(store["data"], "quarter"), {"9999"})


class ConversionTest(unittest.TestCase):
    def test_all_three_styles_give_the_same_quarters_and_years(self):
        quarters = truth(3, years=range(2023, 2026))
        expected = None
        for mode in fm.MODES:
            row = store_of({"1": publish(quarters, mode)})["data"]["1"]
            periods, annual = fm.company_periods(row, {"rev": mode, "eps": mode, "ni": mode})
            got = ({(p["fy"], p["fq"]): (p["rev"], p["derived"]) for p in periods},
                   {p["fy"]: p["rev"] for p in annual})
            if expected is None:
                expected = got
            self.assertEqual(got, expected, mode)
        self.assertEqual(expected[0][(2024, 4)], (quarters[(2024, 4)][0], True))    # every Q4 flagged derived
        self.assertEqual(expected[0][(2024, 3)][1], False)
        self.assertEqual(expected[1][2024], sum(quarters[(2024, q)][0] for q in (1, 2, 3, 4)))
        self.assertNotIn(2026, expected[1])                                          # no full year yet

    def test_year_to_date_eps_uses_the_share_basis_of_the_later_report(self):
        # 10% stock dividend between Q1 and Q2: the H1 EPS is restated, the Q1 one is not
        row = {"2025Q1": [100, 1.0, 1000, 0], "2025Q2": [200, 1.82, 2000, 0]}
        periods, _ = fm.company_periods(row, {"rev": "ytd", "eps": "ytd", "ni": "ytd"})
        q2 = next(p for p in periods if p["fq"] == 2)
        self.assertEqual(q2["epsB"], 0.91)                       # (2000 - 1000) x 1.82 / 2000, not 1.82 - 1.0
        self.assertEqual(q2["rev"], 100)

    def test_q4_from_the_full_year_and_annual_eps_as_the_sum_of_quarters(self):
        row = {"2025Q1": [100, 1.0, 10, 0], "2025Q2": [110, 1.1, 11, 0], "2025Q3": [120, 1.2, 12, 0],
               "2025Q4": [500, 4.6, 46, 0]}
        periods, annual = fm.company_periods(row, {"rev": "q4annual", "eps": "q4annual", "ni": "q4annual"})
        q4 = next(p for p in periods if p["fq"] == 4)
        self.assertEqual((q4["rev"], q4["epsB"], q4["derived"]), (170, 1.3, True))
        self.assertEqual((annual[0]["rev"], annual[0]["epsB"]), (500, 4.6))
        single = {key: list(value) for key, value in row.items()}
        single["2025Q4"] = [170, 1.3, 13, 0]
        _, annual = fm.company_periods(single, {"rev": "quarter", "eps": "quarter", "ni": "quarter"})
        self.assertEqual((annual[0]["rev"], annual[0]["epsB"]), (500, 4.6))

    def test_negative_derived_revenue_and_mixed_kinds_give_nothing(self):
        row = {"2025Q1": [100, 1.0, 10, 0], "2025Q2": [90, 1.5, 15, 0], "2025Q3": [150, 2.0, 20, 1]}
        periods, _ = fm.company_periods(row, {"rev": "ytd", "eps": "ytd", "ni": "ytd"})
        by_q = {p["fq"]: p for p in periods}
        self.assertIsNone(by_q[2]["rev"])           # YTD went down: not a quarter
        self.assertIsNone(by_q[3]["rev"])           # another revenue kind
        self.assertEqual(by_q[3]["epsB"], 0.5)
        _, annual = fm.company_periods({**{f"2025Q{q}": [100, 1.0, 10, q % 2] for q in (1, 2, 3, 4)}},
                                       {"rev": "quarter", "eps": "quarter", "ni": "quarter"})
        self.assertIsNone(annual[0]["rev"])
        self.assertEqual(annual[0]["epsB"], 4.0)


class GrowthTest(unittest.TestCase):
    def blocks(self, row, mode="quarter", today=TODAY):
        truths, store = market(mode)
        store["data"]["2330"] = row
        result, info = fm.growth_candidates(fm.dump_store(store), {"2330": "2330.TW", "1100": "1100.TW"}, today, log=QUIET)
        return result.get("2330.TW", (None, None, None))[1:], info, result, truths

    def test_yoy_turnaround_and_loss_rules_follow_build_growth(self):
        row = {}
        for year, values in ((2025, ((100, -0.5), (100, 0.4), (100, 0.5), (100, -0.2))),
                             (2026, ((150, 0.5), (130, 0.3), None, None))):
            for quarter, entry in enumerate(values, start=1):
                if entry:
                    row[f"{year}Q{quarter}"] = [entry[0], entry[1], entry[1] * 1000, 0]
        (q, _), info, _, _ = self.blocks(row)
        self.assertEqual(info["modes"]["rev"], "quarter")
        self.assertEqual(q["end"], "2026-06-30")
        self.assertEqual(q["src"], "finmind")
        self.assertEqual(q["rev"], [30.0, 50.0])
        self.assertEqual(q["eps"], [-25.0])          # 0.4 → 0.3; Q1 is a turnaround (null + mask)
        self.assertEqual(q["mask"], 0b10)

    def test_annual_series_as_long_as_the_data_and_staleness(self):
        (q, y), info, result, truths = self.blocks({})
        self.assertIsNone(q)
        block = result["1100.TW"][2]
        self.assertEqual(len(block["rev"]), 9)        # 2016..2025 → nine YoY years: the whole history, not cut
        self.assertGreater(len(block["rev"]), growth.RECENT_YEAR_SLOTS)
        years = [sum(truths["1100"][(year, q)][0] for q in (1, 2, 3, 4)) for year in (2025, 2024)]
        self.assertEqual(block["rev"][0], growth.growth_pct(*years))
        self.assertEqual(result["1100.TW"][1]["derived"] & 0b1, 0)        # 2026Q2 vs 2025Q2: no Q4
        later = date(2027, 6, 1)                                           # newest quarter 2026Q2 → stale
        stale, _ = fm.growth_candidates(fm.dump_store(market("quarter")[1]), {"1100": "1100.TW"}, later, log=QUIET)
        self.assertIsNone(stale["1100.TW"][1])

    def test_cross_check_with_the_official_file(self):
        truths, store = market("quarter")
        document = fm.dump_store(store)
        symbols = {code: f"{code}.TW" for code in truths}
        result, info = fm.growth_candidates(document, symbols, TODAY, official_of(truths), log=QUIET)
        self.assertEqual(info["check"]["scale"], 1000.0)            # FinMind in NT$, official in NT$ thousand
        self.assertEqual(info["check"]["count"], 30)
        self.assertEqual(info["check"]["within2pct"], 1.0)
        self.assertEqual(info["check"]["epsWithin2c"], 1.0)
        self.assertEqual(len(result), 30)
        # Numbers that disagree with the official file (here: off by 2x) are refused
        logs = []
        result, info = fm.growth_candidates(document, symbols, TODAY, official_of(truths, scale=500.0), log=logs.append)
        self.assertEqual(result, {})
        self.assertAlmostEqual(info["check"]["median"], 500.0, places=2)
        self.assertTrue(any("differs from the official" in line for line in logs))


class CombineTest(unittest.TestCase):
    def test_newer_period_then_official_unless_finmind_has_more(self):
        mops = {"eps": [10.0], "rev": [5.0], "mask": 0, "end": "2026-06-30", "src": "mops", "derived": 0}
        longer = {"eps": [11.0, 12.0], "rev": [5.0, 6.0], "mask": 0, "end": "2026-06-30", "src": "finmind", "derived": 0}
        same = {"eps": [11.0], "rev": [None], "mask": 1, "end": "2026-06-30", "src": "finmind", "derived": 0}
        older = {"eps": [1.0, 2.0, 3.0, 4.0], "rev": [], "mask": 0, "end": "2026-03-31", "src": "finmind", "derived": 0}
        self.assertIs(fm.pick_block(mops, longer), longer)
        self.assertIs(fm.pick_block(mops, same), mops)
        self.assertIs(fm.pick_block(mops, older), mops)
        self.assertIs(fm.pick_block(None, older), older)
        self.assertEqual(fm.filled(same), 1)
        combined = fm.combine({"A.TW": (None, mops, None)}, {"A.TW": (None, same, older), "B.TW": (None, None, older)})
        self.assertEqual(combined, {"A.TW": (None, mops, older), "B.TW": (None, None, older)})

    def test_build_growth_merges_finmind_over_yahoo_and_one_season_mops(self):
        truths, store = market("quarter", count=25)
        store["data"]["2330"] = store_of({"2330": publish(truth(99), "quarter")})["data"]["2330"]
        with tempfile.TemporaryDirectory() as tmp:
            fin, fundamentals = os.path.join(tmp, "financials"), os.path.join(tmp, "fundamentals")
            os.makedirs(fin)
            os.makedirs(fundamentals)
            files = {"values": {"epsBasic": [1.0, 1.5], "revenue": [100, 150]}, "end": ["2025-06-30", "2026-06-30"],
                     "fy": [2025, 2026], "fq": [2, 2]}
            with open(os.path.join(fin, "2330.TW.json"), "w", encoding="utf-8") as file:
                json.dump({"quarterly": files, "annual": {"end": []}, "quality": {"level": "good"}}, file)
            with open(os.path.join(fin, "9999.TW.json"), "w", encoding="utf-8") as file:
                json.dump({"quarterly": files, "annual": {"end": []}}, file)
            # tw.json today: one season (2026Q2) from the OpenAPI only → no MOPS YoY
            with open(os.path.join(fundamentals, "tw.json"), "w", encoding="utf-8") as file:
                json.dump(official_of({**truths, "2330": truth(99)}), file)
            with open(os.path.join(fundamentals, fm.FILE_NAME), "w", encoding="utf-8") as file:
                json.dump(fm.dump_store(store), file)
            universe = os.path.join(tmp, "universe.json")
            with open(universe, "w", encoding="utf-8") as file:
                json.dump({"fields": ["symbol"], "markets": {"TW": {"rows": [[f"{c}.TW"] for c in [*truths, "2330"]]}}}, file)
            out = os.path.join(tmp, "growth.json")
            summary = growth.run(fin, out, NOW, fundamentals, universe)
            with open(out, encoding="utf-8") as file:
                data = json.load(file)
        rows = {row[0]: dict(zip(data["fields"], row)) for row in data["rows"]}
        self.assertEqual((rows["2330.TW"]["src"], rows["2330.TW"]["srcY"]), ("finmind", "finmind"))
        self.assertIsNone(rows["2330.TW"]["lv"])                # the Yahoo file's level only when its block is used
        self.assertEqual(len(rows["2330.TW"]["epsQ"]), 4)
        self.assertEqual(len(rows["2330.TW"]["revY"]), 9)
        self.assertEqual(rows["9999.TW"]["src"], "yahoo")       # not in FinMind: the Yahoo file stays
        self.assertEqual(summary["official"]["finmind"]["rows"], 26)
        self.assertEqual(summary["official"]["tw"], 0)
        self.assertEqual(summary["official"]["finmind"]["check"]["within2pct"], 1.0)
        self.assertEqual(summary["coverage"]["TW"]["srcQ:finmind"], 26)

    def test_broken_finmind_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            fin, fundamentals = os.path.join(tmp, "financials"), os.path.join(tmp, "fundamentals")
            os.makedirs(fin)
            os.makedirs(fundamentals)
            with open(os.path.join(fin, "AAPL.json"), "w", encoding="utf-8") as file:
                json.dump({"quarterly": {"end": ["2025-06-30", "2026-06-30"], "fy": [2025, 2026], "fq": [2, 2],
                                         "values": {"epsDiluted": [1.0, 1.5]}}}, file)
            with open(os.path.join(fundamentals, fm.FILE_NAME), "w", encoding="utf-8") as file:
                json.dump({"version": 1, "seasons": "bad"}, file)
            summary = growth.run(fin, os.path.join(tmp, "growth.json"), NOW, fundamentals, None)
        self.assertEqual(summary["rows"], 1)
        self.assertEqual(summary["official"]["finmind"]["rows"], 0)


class StoreAndPlanTest(unittest.TestCase):
    def test_round_trip(self):
        store = {"fetch": {"2330": ["2026-10-01", "2026Q2", 6]}, "types": {"EPS": "基本每股盈餘（元）"},
                 "data": {"2330": {"2025Q4": [1.5, 2.0, 3.0, 0], "2026Q2": [1, None, 2, 4]}, "2331": {}}}
        document = fm.dump_store(store)
        self.assertEqual(document["seasons"], ["2025Q4", "2026Q2"])
        self.assertEqual(document["data"], {"2330": [[1.5, 2, 3], [1, None, 2, 4]]})
        self.assertEqual(fm.load_store(json.loads(json.dumps(document)))["data"]["2330"]["2026Q2"], [1, None, 2, 4])
        with self.assertRaises(fm.StoreError):
            fm.load_store({"version": 1, "seasons": ["2026Q2"], "fetch": {}, "data": {"1": [[1, 2]]}})
        with self.assertRaises(fm.StoreError):
            fm.load_store({"version": 2})

    def test_due_and_open_seasons(self):
        self.assertEqual(fm.due_season(TODAY), "2026Q2")
        self.assertEqual(fm.due_season(date(2026, 9, 6)), "2026Q1")
        self.assertEqual(fm.due_season(date(2026, 4, 7)), "2025Q4")
        self.assertEqual(fm.due_season(date(2026, 4, 6)), "2025Q3")
        self.assertEqual(fm.open_season(TODAY), "2026Q2")
        self.assertEqual(fm.open_season(date(2026, 11, 1)), "2026Q3")

    def test_plan_new_first_then_behind_window_and_empty(self):
        rows = {key: [1, 1, 1, 0] for key in ("2026Q1", "2026Q2")}
        store = {"types": {}, "data": {
            "A": dict(rows), "B": {"2026Q1": [1, 1, 1, 0]}, "C": {"2025Q3": [1, 1, 1, 0]}, "D": dict(rows),
            "F": {"2026Q1": [1, 1, 1, 0]},
        }, "fetch": {
            "A": ["2026-10-14", "2026Q2", 2],     # current
            "B": ["2026-09-01", "2026Q1", 1],     # behind 2026Q2, not checked since its deadline (09-07)
            "C": ["2026-10-01", "2025Q3", 1],     # three quarters behind, checked 14 days ago → wait 30
            "E": ["2026-09-01", None, 0],         # FinMind had nothing: every 30 days
            "D": ["2026-10-14", "2026Q2", 2],
            "F": ["2026-10-12", "2026Q1", 1],     # behind, checked after the deadline 3 days ago → wait 7
        }}
        plan = fm.plan_fetches(store, ["A", "B", "C", "D", "E", "F", "G"], TODAY, force={"D"})
        self.assertEqual(plan, [("D", fm.START_DATE, "forced"), ("G", fm.START_DATE, "new"),
                                ("B", "2026-03-31", "behind 2026Q2"), ("E", fm.START_DATE, "empty")])
        # In the 2026Q3 filing window (ended 30+ days ago): A is waited for, re-checked every 7 days
        plan = fm.plan_fetches(store, ["A"], date(2026, 11, 1))
        self.assertEqual(plan, [("A", "2026-06-30", "filing window 2026Q3")])
        store["fetch"]["A"][0] = "2026-10-28"
        self.assertEqual(fm.plan_fetches(store, ["A"], date(2026, 11, 3)), [])
        self.assertEqual(len(fm.plan_fetches(store, ["A"], date(2026, 11, 4))), 1)
        self.assertEqual(fm.plan_fetches(store, ["C"], date(2026, 10, 31)), [("C", "2025-09-30", "behind 2026Q2")])


class FakeHttp:
    """FinMind stand-in: {code: rows or (status, body)}; records requests; each answer takes 0.5 s."""

    def __init__(self, answers, clock):
        self.answers, self.clock, self.requests = answers, clock, []

    def get(self, url, headers):
        query ={key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
        self.requests.append((query, headers))
        self.clock.now += 0.5
        answer = self.answers.get(query["data_id"], [])
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
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.report = self.tmp.name
        self.codes = [str(1101 + i) for i in range(8)]
        with open(os.path.join(self.report, "universe.json"), "w", encoding="utf-8") as file:
            json.dump({"fields": ["symbol", "industry"], "markets": {"TW": {"rows": [
                [f"{code}.TW", "水泥工業"] for code in self.codes] + [["2881.TW", "金融保險業"]]}}}, file)
        self.answers = {code: finmind_rows(code, truth(int(code))) for code in self.codes}
        self.answers["2881"] = fixture("finmind_2881.json")["data"]
        self.out = os.path.join(self.report, "fundamentals", fm.FILE_NAME)

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, **kwargs):
        values = {"report_dir": self.report, "out": None, "budget": 3600.0, "limit": None, "codes": None,
                  "only_when_complete": False, "verify": False, "summary": False}
        values.update(kwargs)
        return SimpleNamespace(**values)

    def go(self, now=NOW, token="", **kwargs):
        clock, logs = Clock(), []
        http = FakeHttp(self.answers, clock)
        code = fm.run(self.args(**kwargs), http=http, now=now, sleep=clock.sleep, clock=clock, log=logs.append,
                      token=token)
        return code, http, clock, logs

    def stored(self):
        with open(self.out, encoding="utf-8") as file:
            return json.load(file)

    def test_budget_throttle_resume_and_no_refetch(self):
        code, http, clock, logs = self.go(budget=30.0)
        self.assertEqual(code, 0)
        # one request every 3600 / (300 x 0.95) = 12.6 s without a token: 3 fit into 30 s
        self.assertEqual(len(http.requests), 3)
        self.assertTrue(all(abs(wait - 12.63) < 0.05 for wait in clock.slept), clock.slept)
        self.assertNotIn("Authorization", http.requests[0][1])
        self.assertEqual(http.requests[0][0], {"dataset": "TaiwanStockFinancialStatements", "data_id": "1101",
                                               "start_date": "2015-01-01"})
        self.assertTrue(any("NOT DONE: 3/9" in line for line in logs), logs[-3:])
        document = self.stored()
        self.assertEqual(sorted(document["fetch"]), ["1101", "1102", "1103"])
        self.assertEqual(document["seasons"][0], "2016Q1")
        code, http, _, logs = self.go()
        self.assertEqual([query["data_id"] for query, _ in http.requests], self.codes[3:] + ["2881"])
        self.assertTrue(any("DONE: 9/9" in line for line in logs))
        self.assertTrue(any("verdict quarter" in line for line in logs))
        self.assertTrue(any('"淨收益": 6' in line for line in logs))
        code, http, _, _ = self.go()                   # same day: nothing due
        self.assertEqual((code, http.requests), (0, []))

    def test_new_season_is_fetched_from_the_newest_stored_one(self):
        self.go()
        later = datetime(2026, 11, 25, tzinfo=timezone.utc)
        self.answers["1101"] = lambda query: [row for row in finmind_rows(
            "1101", {**truth(1101), (2026, 3): (999, 9.99, 9990)}) if row["date"] >= query["start_date"]]
        code, http, _, _ = self.go(now=later, limit=1)
        self.assertEqual(http.requests[0][0]["start_date"], "2026-06-30")
        row = fm.load_store(self.stored())["data"]["1101"]
        self.assertEqual(row["2026Q3"], [999, 9.99, 9990, 0])
        self.assertEqual(row["2016Q1"][0], truth(1101)[(2016, 1)][0])        # history kept, not re-fetched
        self.assertEqual(self.stored()["fetch"]["1101"], ["2026-11-25", "2026Q3", 2])      # 2026Q2 again + 2026Q3

    def test_rate_limit_stops_and_keeps_progress(self):
        self.answers["1103"] = (402, json.dumps(fixture("finmind_limit.json")).encode())
        code, http, _, logs = self.go()
        self.assertEqual(code, 0)
        self.assertEqual(len(http.requests), 3)
        self.assertEqual(sorted(self.stored()["fetch"]), ["1101", "1102"])
        self.assertTrue(any("::warning::FinMind stopped" in line and "upper limit" in line for line in logs))

    def test_token_header_faster_pace_and_refused_token(self):
        code, http, clock, _ = self.go(token="abc", limit=3)
        self.assertEqual(http.requests[0][1]["Authorization"], "Bearer abc")
        self.assertTrue(all(abs(wait - 6.32) < 0.05 for wait in clock.slept), clock.slept)
        self.answers["1104"] = (401, b'{"msg": "Token is invalid", "status": 401}')
        code, http, _, logs = self.go(token="bad")
        self.assertEqual(code, 1)                       # refused before any stock of this run was fetched
        self.assertEqual(len(http.requests), 1)
        self.assertTrue(any("refused" in line for line in logs))

    def test_refused_token_on_the_first_request_fails_the_run(self):
        self.answers["1101"] = (403, b"Forbidden")
        code, http, _, _ = self.go(token="bad")
        self.assertEqual((code, len(http.requests)), (1, 1))
        self.assertFalse(os.path.exists(self.out))

    def test_limit_counts_retries_and_stops_without_a_wait(self):
        self.answers["1101"] = (503, b"busy")
        code, http, _, logs = self.go(limit=2)
        self.assertEqual((code, len(http.requests)), (0, 2))              # the retry is the second request
        self.assertTrue(any("stopped: --limit 2 requests" in line for line in logs), logs)
        self.assertFalse(os.path.exists(self.out))                         # nothing fetched, nothing written

    def test_refused_token_after_some_stocks_still_fails_the_run(self):
        self.answers["1103"] = (401, b'{"msg": "Token is invalid", "status": 401}')
        code, http, _, logs = self.go(token="abc")
        self.assertEqual((code, len(http.requests)), (1, 3))
        self.assertEqual(sorted(self.stored()["fetch"]), ["1101", "1102"])     # progress kept
        self.assertTrue(any("::warning::FinMind stopped" in line and "refused" in line for line in logs))

    def test_summary_reads_the_stored_file_offline(self):
        clock, logs = Clock(), []
        code = fm.run(self.args(summary=True), http=None, now=NOW, sleep=clock.sleep, clock=clock, log=logs.append)
        self.assertEqual(code, 1)                                              # no file yet
        self.assertTrue(any("not found" in line for line in logs))
        self.go(limit=3)
        clock, logs = Clock(), []
        code = fm.run(self.args(summary=True), http=None, now=NOW, sleep=clock.sleep, clock=clock, log=logs.append)
        self.assertEqual((code, clock.slept), (0, []))                         # no request, no wait
        self.assertTrue(any(line.startswith("FinMind store: ") and '"fetched": 3' in line for line in logs), logs)
        self.assertTrue(any("NOT DONE: 3/9 stocks fetched, 6 left" in line for line in logs), logs)

    def test_transient_errors_are_retried_and_failures_in_a_row_stop(self):
        attempts = []

        def flaky(query):
            attempts.append(query)
            return (503, b"busy") if len(attempts) < 3 else finmind_rows("1101", truth(1101))

        self.answers["1101"] = flaky
        code, http, clock, logs = self.go(limit=3)
        self.assertEqual(len(attempts), 3)
        self.assertIn(fm.RETRY_WAIT, clock.slept)
        self.assertEqual(sorted(self.stored()["fetch"]), ["1101"])
        for code_ in self.codes:
            self.answers[code_] = (OSError("connection reset"), None)
        self.answers["2881"] = (OSError("connection reset"), None)
        code, http, _, logs = self.go(codes=self.codes)
        self.assertEqual(code, 1)
        self.assertEqual(len(http.requests), fm.MAX_CONSECUTIVE_ERRORS * (fm.RETRIES + 1))
        self.assertTrue(any("failures in a row" in line for line in logs))

    def test_unreadable_store_is_never_overwritten_and_only_when_complete(self):
        os.makedirs(os.path.dirname(self.out))
        with open(self.out, "w", encoding="utf-8") as file:
            file.write("{broken")
        code, http, _, _ = self.go()
        self.assertEqual((code, http.requests), (1, []))
        with open(self.out, encoding="utf-8") as file:
            self.assertEqual(file.read(), "{broken")
        os.remove(self.out)
        code, http, _, logs = self.go(only_when_complete=True)
        self.assertEqual((code, http.requests), (0, []))
        self.assertTrue(any("first backfill not finished" in line for line in logs))
        self.go()
        code, http, _, _ = self.go(only_when_complete=True, now=datetime(2026, 11, 25, tzinfo=timezone.utc), limit=2)
        self.assertEqual(len(http.requests), 2)

    def test_verify_pass_and_fail(self):
        self.go()
        _, _, _, logs = self.go(verify=True)
        self.assertTrue(any(line.startswith("FAIL") and "official tw.json" in line for line in logs))   # no tw.json yet
        truths = {code: truth(int(code)) for code in self.codes}
        with open(os.path.join(self.report, "fundamentals", "tw.json"), "w", encoding="utf-8") as file:
            json.dump(official_of(truths), file)
        clock, logs = Clock(), []
        result = fm.run(self.args(verify=True), http=None, now=NOW, sleep=clock.sleep, clock=clock, log=logs.append)
        self.assertEqual(result, 1)                     # 8 companies < 500 for the cross-check
        self.assertTrue(any(line.startswith("PASS  every universe stock") for line in logs))
        self.assertTrue(any("2881 kind 淨收益" in line for line in logs))
        self.assertTrue(any('"金融保險業": {"淨收益": 1}' in line for line in logs))


class SizeTest(unittest.TestCase):
    def test_full_market_file_size(self):
        """1,976 stocks x ten and a half years of quarters with realistic magnitudes (NT$): the file size."""
        rng = random.Random(5)
        store = {"fetch": {}, "types": {}, "data": {}}
        for index in range(1976):
            code = str(1101 + index)
            row = store["data"][code] = {}
            level = rng.uniform(5e7, 5e10)
            shares = rng.uniform(2e7, 5e9)
            for year in range(2015, 2027):
                for quarter in (1, 2, 3, 4):
                    if year == 2026 and quarter > 2:
                        continue
                    rev = round(level * rng.uniform(0.8, 1.2) / 1000) * 1000
                    ni = round(rev * rng.uniform(-0.1, 0.3) / 1000) * 1000
                    row[tw.season_key(year, quarter)] = [rev, round(ni / shares, 2), ni, 0]
            store["fetch"][code] = ["2026-10-15", "2026Q2", 46]
        size = len(json.dumps(fm.dump_store(store), ensure_ascii=False, separators=(",", ":")).encode())
        print(f"\n[full-market size] tw-finmind.json about {size:,} B for 1,976 stocks x 46 quarters")
        self.assertLess(size, 4_000_000)


if __name__ == "__main__":
    unittest.main()

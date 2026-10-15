"""
Taiwan official statements: atlas/scripts/build_fundamentals_tw.py (MOPS t163sb04 / TWSE・TPEx OpenAPI
t187ap06 → docs/report/fundamentals/tw.json) and its quarters / YoY for growth.json.

Offline: the MOPS pages and OpenAPI payloads are synthetic fixtures in tests/fixtures/fundamentals
written in the official layouts; the store tests use small hand-written stores.
Run: python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_fundamentals_tw as tw  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "fundamentals")
TODAY = date(2026, 10, 15)


def fixture(name, mode="r"):
    with open(os.path.join(FIXTURES, name), mode, **({} if "b" in mode else {"encoding": "utf-8"})) as file:
        return file.read()


def store_with(rows, fetch=None):
    """rows: {code: {season: [rev, eps, ni] or [rev, eps, ni, flags]}} → an in-memory store."""
    data = {code: {key: (list(entry) + [0])[:4] for key, entry in seasons.items()} for code, seasons in rows.items()}
    return {"fetch": fetch or {}, "data": data}


def ytd(year, quarters, flags=0):
    """quarters: {q: (rev, eps, ni)} YTD figures → {season: entry}."""
    return {tw.season_key(year, q): [rev, eps, ni, flags] for q, (rev, eps, ni) in quarters.items()}


def blocks(row, today=TODAY):
    candidates = tw.growth_candidates(tw.dump_store(store_with({"1101": row}), tw.target_seasons(today)),
                                      {"1101": "1101.TW"}, today)
    return candidates.get("1101.TW", (None, None, None))[1:]


class SeasonTest(unittest.TestCase):
    def test_roc_years_and_season_keys(self):
        self.assertEqual(tw.roc_year(2026), 115)
        self.assertEqual(tw.western_year("115"), 2026)
        self.assertEqual(tw.western_year(" 99 "), 2010)
        self.assertIsNone(tw.western_year("abc"))
        self.assertIsNone(tw.western_year(2026))       # already western: not a 民國 year
        self.assertEqual(tw.parse_season("2026Q2"), (2026, 2))
        self.assertIsNone(tw.parse_season("2026Q5"))
        self.assertEqual(tw.mops_form(2026, 2, "otc"), {
            "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "isQuery": "Y",
            "TYPEK": "otc", "year": "115", "season": "02"})

    def test_targets_and_final_dates(self):
        targets = tw.target_seasons(TODAY)
        self.assertEqual(targets[0], "2026Q3")
        self.assertEqual(targets[:11][-1], "2024Q1")
        self.assertEqual(targets[11:], ["2023Q4", "2022Q4", "2021Q4"])
        self.assertEqual(tw.final_date(2025, 4), date(2026, 4, 30))
        self.assertEqual(tw.final_date(2026, 2), date(2026, 9, 30))
        self.assertEqual(tw.last_ended_season(date(2026, 4, 1)), (2026, 1))
        self.assertEqual(tw.last_ended_season(date(2026, 3, 31)), (2025, 4))

    def test_plan_fetches_missing_first_refreshes_open_seasons_daily_and_history_last(self):
        empty = {"fetch": {}, "data": {}}
        plan = tw.mops_plan(empty, TODAY)
        self.assertEqual(plan[:2], [("2026Q3", "sii"), ("2026Q3", "otc")])
        self.assertEqual(len([entry for entry in plan if entry[1] != "rotc"]), 28)
        self.assertNotIn(("2026Q3", "rotc"), plan)      # 興櫃 only for final seasons
        self.assertIn(("2026Q2", "rotc"), plan)         # final since 2026-09-30
        done = {"fetch": {key: {typek: {"at": "2026-10-14", "final": key != "2026Q3"} for typek in ("sii", "otc", "rotc")}
                          for key in tw.target_seasons(TODAY)}, "data": {}}
        self.assertEqual(tw.mops_plan(done, TODAY), [("2026Q3", "sii"), ("2026Q3", "otc")])
        done["fetch"]["2026Q3"]["sii"]["at"] = TODAY.isoformat()
        self.assertEqual(tw.mops_plan(done, TODAY), [("2026Q3", "otc")])
        self.assertIn(("2025Q4", "sii"), tw.mops_plan(done, TODAY, force={"2025Q4"}))
        # A quarter that has not ended yet is never requested
        self.assertNotIn("2026Q4", {key for key, _ in tw.mops_plan(empty, date(2026, 12, 31))})


class MopsPageTest(unittest.TestCase):
    def test_every_statement_format(self):
        page = tw.parse_mops(fixture("mops_t163sb04_sii.html"))
        self.assertEqual(page["status"], "ok")
        self.assertEqual(page["tables"], 6)
        entries = page["entries"]
        # 一般業: 營業收入, 淨利（淨損）歸屬於母公司業主, 基本每股盈餘（元）
        self.assertEqual(entries["2330"], [1270380250, 21.55, 559000000, 0])
        # 銀行業: no 營業收入 / 淨收益 → 利息淨收益 + 利息以外淨損益; 淨利（損）歸屬於母公司業主
        self.assertEqual(entries["2801"], [10000000, 0.75, 3800000, tw.REVENUE_KINDS.index("利息淨收益+利息以外淨損益")])
        self.assertEqual(entries["2881"][:3], [70000000, 2.4, 32500000])              # 金控業: 淨收益
        self.assertEqual(entries["2881"][3], tw.REVENUE_KINDS.index("淨收益"))
        self.assertEqual(entries["6005"][:3], [9000000, 1.4, 3050000])                # 證券業: 收益
        self.assertEqual(entries["2823"][:3], [120000000, 1.2, 14000000])             # 保險業: 營業收入
        self.assertEqual(entries["5871"][:3], [20000000, 7.8, 3900000])               # 異業: 收入
        self.assertEqual(entries["5871"][3], tw.REVENUE_KINDS.index("收入"))
        # (480) is negative, '--' is missing, a repeated header row is skipped, a row without revenue and EPS too
        self.assertEqual(entries["1234"], [1000, -0.05, -480, 0])
        self.assertNotIn("4141", entries)
        self.assertEqual(page["duplicates"], 0)

    def test_refusals_empty_seasons_and_unknown_pages(self):
        with self.assertRaisesRegex(tw.MopsBlocked, "SECURITY"):
            tw.parse_mops(fixture("mops_blocked.html"))
        with self.assertRaisesRegex(tw.MopsBlocked, "頻繁"):
            tw.parse_mops(fixture("mops_overrun.html"))
        self.assertEqual(tw.parse_mops(fixture("mops_empty.html"))["status"], "empty")
        with self.assertRaisesRegex(tw.MopsFormatError, "page starts"):
            tw.parse_mops("<html><body><p>維護中</p></body></html>")
        with self.assertRaisesRegex(tw.MopsFormatError, "no EPS"):
            tw.parse_mops("<table><tr><th>公司代號</th><th>公司名稱</th><th>其他</th></tr><tr><td>1101</td><td>x</td><td>1</td></tr></table>")

    def test_big5_pages_and_numbers(self):
        body = fixture("mops_t163sb04_sii.html").replace("charset=utf-8", "charset=big5").encode("cp950")
        self.assertEqual(tw.parse_mops(tw.decode(body, ""))["entries"]["2330"][1], 21.55)
        self.assertEqual(tw.parse_mops(tw.decode(body, "text/html; charset=Big5"))["entries"]["2801"][0], 10000000)
        self.assertEqual(tw.number("（1,234）"), -1234)
        self.assertEqual(tw.number("－0.12"), -0.12)
        self.assertIsNone(tw.number("--"))
        self.assertIsNone(tw.number("N/A"))
        self.assertEqual(tw.normalise("基本每股盈餘（元）"), "基本每股盈餘(元)")


class OpenApiTest(unittest.TestCase):
    def test_chinese_and_english_keys(self):
        parsed = tw.parse_openapi(json.loads(fixture("openapi_t187ap06_L_ci.json")), "t187ap06_L_ci")
        self.assertEqual(parsed, {"2026Q2": {"1101": [52000000, 0.63, 4700000, 0], "9999": [1000, 0.1, 100, 0]}})
        parsed = tw.parse_openapi(json.loads(fixture("openapi_t187ap06_O_ci_en.json")), "mopsfin_t187ap06_O_ci")
        self.assertEqual(parsed, {"2026Q2": {"6488": [30000000, 14, 6000000, 0]}})
        self.assertEqual(tw.parse_openapi([], "x"), {})

    def test_unknown_layouts_name_the_keys(self):
        with self.assertRaisesRegex(tw.MopsFormatError, "x: no company code column"):
            tw.parse_openapi([{"foo": 1, "bar": 2}], "x")
        with self.assertRaisesRegex(tw.MopsFormatError, "年度 / 季別"):
            tw.parse_openapi([{"公司代號": "1101", "基本每股盈餘（元）": "1"}], "x")
        with self.assertRaisesRegex(tw.MopsFormatError, "not a JSON list"):
            tw.parse_openapi({"a": 1}, "x")


class QuarterTest(unittest.TestCase):
    def test_year_to_date_differences_and_q4_from_the_full_year(self):
        row = {}
        row.update(ytd(2024, {2: (200, 2.0, 2000), 3: (330, 3.2, 3200), 4: (480, 4.6, 4600)}))
        row.update(ytd(2025, {1: (110, 1.1, 1100), 2: (230, 2.4, 2400), 3: (380, 3.9, 3900), 4: (560, 5.6, 5600)}))
        row.update(ytd(2026, {1: (130, 1.4, 1400), 2: (280, 3.0, 3000)}))
        row.update(ytd(2023, {4: (400, 4.0, 4000)}))
        row.update(ytd(2022, {4: (380, 3.6, 3600)}))
        row.update(ytd(2021, {4: (350, 3.0, 3000)}))
        q, y = blocks(row)
        # 2026Q2 = 150 / 1.6 vs 2025Q2 = 120 / 1.3; 2026Q1 130 / 1.4 vs 110 / 1.1;
        # 2025Q4 = 180 / 1.7 vs 2024Q4 = 150 / 1.4; 2025Q3 = 150 / 1.5 vs 2024Q3 = 130 / 1.2
        self.assertEqual(q["rev"], [25.0, 18.2, 20.0, 15.4])
        self.assertEqual(q["eps"], [23.1, 27.3, 21.4, 25.0])
        self.assertEqual(q["end"], "2026-06-30")
        self.assertEqual(q["src"], "mops")
        self.assertEqual(q["derived"], 0b100)          # slot 2 compares the derived Q4s
        # Annual: 2025 5.6 / 560 vs 2024 4.6 / 480, 2024 vs 2023, 2023 vs 2022, 2022 vs 2021 (up to 10 years)
        self.assertEqual(y["eps"], [21.7, 15.0, 11.1, 20.0])
        self.assertEqual(y["rev"], [16.7, 20.0, 5.3, 8.6])
        self.assertEqual(y["end"], "2025-12-31")

    def test_a_stock_dividend_between_two_reports_does_not_distort_the_quarter(self):
        # H1 EPS 2.0 on 1,000 shares; a 20% stock dividend, then the 9M EPS is restated on 1,200 shares
        before, after = [200, 2.0, 2000, 0], [300, 2.5, 3000, 0]      # 9M NI 3,000 / 1,200 shares = 2.5
        rev, eps = tw.single_quarter(after, before)
        self.assertEqual(rev, 100)
        self.assertEqual(eps, 0.8333)                                 # 1,000 / 1,200, not 2.5 - 2.0 = 0.5
        # Tiny EPS (cent rounding): plain difference
        self.assertEqual(tw.single_quarter([300, 0.05, 3000, 0], [200, 0.02, 2000, 0])[1], 0.03)
        # No net income column: plain difference
        self.assertEqual(tw.single_quarter([300, 2.5, None, 0], [200, 2.0, None, 0])[1], 0.5)
        # Revenue may not shrink, nor mix revenue kinds
        self.assertIsNone(tw.single_quarter([100, 1.0, 100, 0], [200, 2.0, 200, 0])[0])
        self.assertIsNone(tw.single_quarter([300, 1.0, 100, 2], [200, 2.0, 200, 0])[0])

    def test_missing_previous_report_leaves_the_quarter_out(self):
        row = {}
        row.update(ytd(2025, {1: (100, 1.0, 1000), 3: (330, 3.3, 3300)}))   # no H1: no Q2, no Q3
        row.update(ytd(2026, {1: (120, 1.2, 1200), 2: (250, 2.5, 2500), 3: (390, 3.9, 3900)}))
        q, _ = blocks(row)
        self.assertEqual(q["end"], "2026-09-30")
        self.assertEqual(q["rev"], [None, None, 20.0])

    def test_turnaround_capital_reduction_and_staleness(self):
        row = {}
        row.update(ytd(2025, {1: (100, -0.5, -500)}))
        row.update(ytd(2026, {1: (120, 0.3, 300)}))
        q, _ = blocks(row, today=date(2026, 6, 1))
        self.assertEqual((q["eps"], q["mask"], q["rev"]), ([], 0b1, [20.0]))
        # 50% capital reduction: implied shares 1,000 → 500, EPS doubles: not one share basis
        row = {}
        row.update(ytd(2025, {1: (100, 1.0, 1000)}))
        row.update(ytd(2026, {1: (100, 2.0, 1000)}))
        q, _ = blocks(row, today=date(2026, 6, 1))
        self.assertEqual((q["eps"], q["rev"]), ([], [0.0]))
        self.assertEqual(blocks(row, today=date(2027, 1, 30)), (None, None))

    def test_scale_errors_are_not_growth(self):
        row = {}
        row.update(ytd(2025, {1: (100, 1.0, 1000)}))
        row.update(ytd(2026, {1: (100_000, 1.1, 1100)}))
        q, _ = blocks(row, today=date(2026, 6, 1))
        self.assertEqual((q["eps"], q["rev"]), ([10.0], []))


class StoreTest(unittest.TestCase):
    def page(self, entries):
        return {"status": "ok", "entries": entries, "tables": 1, "kinds": {}}

    def test_round_trip_alignment_and_pruning(self):
        store = store_with({"2330": ytd(2026, {1: (1, 1.5, 1)}) | ytd(2019, {4: (9, 9, 9)}),
                            "1101": ytd(2025, {4: (2, 2, 2)}, flags=tw.FLAG_OPENAPI)})
        document = tw.dump_store(store, tw.target_seasons(TODAY))
        self.assertEqual(document["seasons"], ["2025Q4", "2026Q1"])          # 2019Q4 pruned, sorted
        self.assertEqual(document["data"], {"1101": [[2, 2, 2, 1]], "2330": [None, [1, 1.5, 1]]})
        self.assertEqual(tw.load_store(document)["data"]["2330"], {"2026Q1": [1, 1.5, 1, 0]})
        for bad in ({"version": 2}, {"version": 1, "seasons": ["x"], "data": {}, "fetch": {}},
                    {"version": 1, "seasons": ["2026Q1"], "data": {"1": [[1]]}, "fetch": {}}):
            with self.assertRaises(tw.StoreError):
                tw.load_store(bad)

    def test_mops_pages_and_history(self):
        store = {"fetch": {}, "data": {}}
        tw.merge_mops(store, "2025Q4", "otc", self.page({"6488": [10, 1.0, 5, 0]}), TODAY)
        self.assertEqual(store["fetch"]["2025Q4"]["otc"], {"src": "mops", "at": "2026-10-15", "n": 1, "final": True})
        # 興櫃 history never replaces a listed-market row, but fills the others
        tw.merge_mops(store, "2025Q4", "rotc", self.page({"6488": [99, 9.0, 9, 0], "7777": [5, 0.5, 2, 0]}), TODAY)
        self.assertEqual(store["data"]["6488"]["2025Q4"][:3], [10, 1.0, 5])
        self.assertEqual(store["data"]["7777"]["2025Q4"][:3], [5, 0.5, 2])
        tw.merge_mops(store, "2025Q2", "sii", self.page({}), TODAY)
        self.assertTrue(store["fetch"]["2025Q2"]["sii"]["final"])

    def test_openapi_fills_gaps_and_is_refused_when_the_units_disagree(self):
        store = {"fetch": {}, "data": {}}
        listed = {f"{1000 + i}": [1000 + i, 1.0, 100, 0] for i in range(25)}
        tw.merge_mops(store, "2026Q2", "sii", self.page(listed), TODAY)
        same = {code: [entry[0], 1.0, 100, 0] for code, entry in listed.items()} | {"9999": [5, 0.1, 1, 0]}
        logs = []
        self.assertEqual(tw.merge_openapi(store, {"2026Q2": same}, TODAY, logs.append), {"2026Q2": 1})
        self.assertEqual(store["data"]["9999"]["2026Q2"][3] & tw.FLAG_OPENAPI, tw.FLAG_OPENAPI)
        self.assertEqual(store["data"]["1000"]["2026Q2"][3], 0)                # MOPS rows are kept
        self.assertEqual(store["fetch"]["2026Q2"]["openapi"]["checked"], 25)
        thousandfold = {code: [entry[0] * 1000, 1.0, 100, 0] for code, entry in listed.items()} | {"8888": [5, 0.1, 1, 0]}
        self.assertEqual(tw.merge_openapi(store, {"2026Q2": thousandfold}, TODAY, logs.append), {})
        self.assertNotIn("8888", store["data"])
        self.assertIn("median ratio 1000", logs[-1])


class FakeHttp:
    """MOPS / OpenAPI answers by (method, url, season, typek); records the calls."""

    def __init__(self, mops=None, openapi=None):
        self.mops, self.openapi, self.calls = mops or {}, openapi or {}, []

    def post(self, url, data, headers=None, timeout=60):
        self.calls.append(("POST", url, data["year"], data["season"], data["TYPEK"]))
        answer = self.mops.get((data["year"], data["season"], data["TYPEK"]), self.mops.get("*"))
        if callable(answer):
            answer = answer()
        return answer if answer else (404, b"not found", "text/html")

    def get(self, url, headers=None, timeout=60):
        self.calls.append(("GET", url))
        name = url.rsplit("/", 1)[-1]
        return self.openapi.get(name, (404, b"", "application/json"))


class RunTest(unittest.TestCase):
    def args(self, tmp, **extra):
        values = {"report_dir": tmp, "out": os.path.join(tmp, "fundamentals", "tw.json"), "budget": 600.0,
                  "no_mops": False, "no_openapi": False, "seasons": None}
        values.update(extra)
        return SimpleNamespace(**values)

    def test_backfill_then_a_refusal_stops_mops_and_keeps_what_was_fetched(self):
        page = (200, fixture("mops_t163sb04_sii.html").encode("utf-8"), "text/html; charset=utf-8")
        blocked = (200, fixture("mops_blocked.html").encode("utf-8"), "text/html")
        answers = iter([page, page, page, blocked, blocked])
        http = FakeHttp(mops={"*": lambda: next(answers)},
                        openapi={"t187ap06_L_ci": (200, fixture("openapi_t187ap06_L_ci.json").encode("utf-8"), "application/json")})
        sleeps, logs = [], []
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(tmp)
            now = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
            self.assertEqual(tw.run(args, http, now, sleeps.append, lambda: 0.0, logs.append), 0)
            with open(args.out, encoding="utf-8") as file:
                document = json.load(file)
            self.assertIn(tw.BLOCK_WAIT, sleeps)
            self.assertTrue(any("refused" in line for line in logs))
            self.assertEqual(sorted(document["fetch"]), ["2026Q2", "2026Q3"])
            self.assertEqual(sorted(document["fetch"]["2026Q3"]), ["otc", "sii"])
            # OpenAPI 2026Q2: 1101 is already there from MOPS (kept), 9999 is new
            self.assertEqual(document["data"]["9999"][document["seasons"].index("2026Q2")][3], tw.FLAG_OPENAPI)
            mops_calls = [call for call in http.calls if call[0] == "POST"]
            self.assertEqual(mops_calls[0][2:], ("115", "03", "sii"))
            self.assertTrue(all(call[1] == tw.MOPS_URLS[0] for call in mops_calls))

    def test_unreadable_store_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(tmp)
            os.makedirs(os.path.dirname(args.out))
            with open(args.out, "w", encoding="utf-8") as file:
                file.write("{broken")
            logs = []
            self.assertEqual(tw.run(args, FakeHttp(), log=logs.append), 1)
            with open(args.out, encoding="utf-8") as file:
                self.assertEqual(file.read(), "{broken")

    def test_format_errors_fall_back_to_the_second_host_then_give_up(self):
        calls = []

        class Http(FakeHttp):
            def post(self, url, data, headers=None, timeout=60):
                calls.append(url)
                if url == tw.MOPS_URLS[0]:
                    return 200, "<html>新版網站</html>".encode("utf-8"), "text/html"
                return 200, fixture("mops_t163sb04_sii.html").encode("utf-8"), "text/html"

        page = tw.fetch_mops(Http(), "2026Q2", "sii")
        self.assertEqual(calls, list(tw.MOPS_URLS))
        self.assertIn("2330", page["entries"])

        class Broken(FakeHttp):
            def post(self, url, data, headers=None, timeout=60):
                return 500, b"oops", "text/html"

        with self.assertRaisesRegex(tw.MopsFormatError, "HTTP 500"):
            tw.fetch_mops(Broken(), "2026Q2", "sii")
        with tempfile.TemporaryDirectory() as tmp:
            logs = []
            self.assertEqual(tw.run(self.args(tmp, no_openapi=True), Broken(), sleep=lambda s: None, log=logs.append), 1)
            self.assertTrue(any("3 unreadable answers" in line for line in logs))
            self.assertFalse(os.path.exists(os.path.join(tmp, "fundamentals", "tw.json")))


if __name__ == "__main__":
    unittest.main()

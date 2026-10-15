"""
~10-year SEC XBRL frames: atlas/scripts/build_fundamentals_sec.py → us-sec.json (10 annual YoY slots) and the
statement history shards docs/report/fundamentals/us-history/NNN.json (financials page / P/E river).

Offline: a synthetic calendar-year company over 2015–2026 in the frames layout (the helpers of
test_fundamentals_sec), with a 4-for-1 split whose restated comparatives reach back one year of quarters
and three fiscal years, as frames really hold them (the last filed value of each period).
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
for path in (SCRIPTS, os.path.dirname(os.path.abspath(__file__))):
    if path not in sys.path:
        sys.path.insert(0, path)

import build_fundamentals_sec as sec  # noqa: E402
import build_growth as growth  # noqa: E402
from test_fundamentals_sec import FakeSec, frame, quarter_dates  # noqa: E402

TODAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)
DILUTED = "us-gaap:EarningsPerShareDiluted"
BASIC = "us-gaap:EarningsPerShareBasic"
REVENUES = "us-gaap:Revenues"
NET = "us-gaap:NetIncomeLoss"
SHARES = "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"
CIK = 77
QF, YF = sec.HISTORY_FIELDS["q"], sec.HISTORY_FIELDS["y"]


def money_frame(key, period, points):
    """frame() for net income (the helper of test_fundamentals_sec treats unknown concepts as EPS)."""
    request, parsed = frame(REVENUES, period, points)
    request = sec.Frame(key, "USD", "USD", period, request.label, request.kind, False)
    return request, parsed


EPS_BASE = 0.25


def true_eps(year, quarter, base=None):
    """Post-split (newest basis) quarterly EPS: grows 10% a year."""
    return round((base or EPS_BASE) * (1 + 0.1 * (year - 2015)) * (1 + 0.02 * quarter), 4)


def restated(year, quarter):
    """Quarters from 2019Q3 on carry the post-split basis (the split was mid-2020; 10-Q comparatives go back one year)."""
    return (year, quarter) >= (2019, 3)


def ten_year_company(cik=CIK, split=True, base=None, scale=1):
    """Q1–Q3 frames 2016Q1–2026Q2 (fiscal Q4 only in the annual frames), annual frames 2015–2025."""
    frames = []
    for year in range(2016, 2027):
        for quarter in (1, 2, 3):
            if (year, quarter) > (2026, 2):
                continue
            start, end = quarter_dates(year, quarter)
            k = 1 if not split or restated(year, quarter) else 4
            eps = true_eps(year, quarter, base)
            period = f"CY{year}Q{quarter}"
            frames.append(frame(DILUTED, period, [(cik, start, end, round(eps * k, 4))]))
            frames.append(frame(BASIC, period, [(cik, start, end, round(eps * k + 0.01, 4))]))
            frames.append(frame(SHARES, period, [(cik, start, end, 400 * scale / k)]))
            frames.append(frame(REVENUES, period, [(cik, start, end, (1000 + 50 * (year - 2015) + quarter) * scale)]))
            frames.append(money_frame(NET, period, [(cik, start, end, round(eps * 400 * scale, 2))]))
    for year in range(2015, 2026):
        k = 1 if not split or year >= 2017 else 4     # 10-K comparatives: three fiscal years
        eps = sum(true_eps(year, quarter, base) for quarter in (1, 2, 3, 4))
        start, end = f"{year}-01-01", f"{year}-12-31"
        frames.append(frame(DILUTED, f"CY{year}", [(cik, start, end, round(eps * k, 4))]))
        frames.append(frame(BASIC, f"CY{year}", [(cik, start, end, round(eps * k + 0.04, 4))]))
        frames.append(frame(SHARES, f"CY{year}", [(cik, start, end, 400 * scale / k)]))
        frames.append(frame(REVENUES, f"CY{year}", [(cik, start, end, (4 * (1000 + 50 * (year - 2015)) + 10) * scale)]))
        frames.append(money_frame(NET, f"CY{year}", [(cik, start, end, round(eps * 400 * scale, 2))]))
    return frames


def history_of(frames, cik=CIK):
    company = sec.collect(frames)[cik]
    periods = sec.company_periods(company)
    q, y, foreign, _ = sec.company_blocks(company, TODAY, periods)
    return sec.company_history(periods, cik, foreign), q, y


def column(rows, fields, name):
    return [row[fields.index(name)] for row in rows]


class PlanTest(unittest.TestCase):
    def test_ten_years_of_frames_and_the_request_count(self):
        plan = sec.frame_plan(TODAY)
        core = [request for request in plan if not request.foreign]
        quarters = sorted({request.label for request in core if request.kind == "q"})
        self.assertEqual(len(quarters), 44)
        self.assertEqual(sec.quarter_label(quarters[0]), "CY2015Q4")
        self.assertEqual(sec.quarter_label(quarters[-1]), "CY2026Q3")
        self.assertEqual(sorted({request.label for request in core if request.kind == "y"}), list(range(2015, 2027)))
        foreign_years = sorted({request.label for request in plan if request.foreign})
        self.assertEqual(foreign_years, list(range(2015, 2026)))
        self.assertIn("https://data.sec.gov/api/xbrl/frames/us-gaap/NetIncomeLoss/USD/CY2016Q2.json", {r.url for r in plan})
        # 13 core concept-units x (44 + 12) + 206 foreign concept-units x 11 years (README / workflow estimate)
        self.assertEqual(len(core), 13 * 56)
        self.assertEqual(len(plan) - len(core), 206 * 11)


class LongSeriesTest(unittest.TestCase):
    def test_q4_derived_over_ten_years_and_ten_annual_yoy(self):
        entry, q, y = history_of(ten_year_company(split=False))
        quarters = entry["q"]
        self.assertEqual(len(quarters), 42)                               # 2016Q1–2026Q2, Q4s derived
        self.assertEqual(quarters[0][0], "2016-03-31")
        self.assertEqual(quarters[-1][0], "2026-06-30")
        derived = [row for row in quarters if row[QF.index("d")]]
        self.assertEqual([row[QF.index("fq")] for row in derived], [4] * 10)
        q4_2020 = next(row for row in derived if row[0] == "2020-12-31")
        self.assertAlmostEqual(q4_2020[QF.index("epsDiluted")], true_eps(2020, 4), places=3)
        self.assertEqual(q4_2020[QF.index("revenue")], 4 * 1250 + 10 - (3 * 1250 + 6))
        self.assertIn("q4-derived", entry["issues"])
        self.assertEqual(entry["fyEnd"], 12)
        self.assertEqual(len(entry["y"]), 11)
        self.assertEqual(entry["c"]["revenue"], ["Revenues"])
        # growth: ten annual YoY slots (2025 vs 2024 ... 2016 vs 2015), four quarterly ones
        self.assertEqual(len(y["eps"]), growth.YEAR_SLOTS)
        self.assertEqual(len(y["rev"]), 10)
        self.assertTrue(all(value > 0 for value in y["eps"]))
        self.assertEqual(len(q["eps"]), growth.QUARTER_SLOTS)

    def test_inferred_split_puts_every_period_on_the_newest_basis(self):
        entry, q, y = history_of(ten_year_company(split=True))
        # one 4-for-1 event: the quarterly boundary (2019Q3) and the annual one (FY2017) are the same split
        self.assertEqual([ratio for _, ratio in entry["split"]], [4])
        self.assertIn("split-inferred", entry["issues"])
        rows = {row[0]: row for row in entry["q"]}
        for year, quarter in ((2016, 1), (2018, 2), (2019, 2), (2019, 3), (2025, 1)):
            row = rows[quarter_dates(year, quarter)[1]]
            self.assertAlmostEqual(row[QF.index("epsDiluted")], true_eps(year, quarter), places=4)
            self.assertEqual(row[QF.index("dilutedShares")], 400)
        # FY2019 Q4: Q1/Q2 on the old basis, Q3 and the year on the new one → its EPS is not derived (no guess)
        q4_2019 = rows["2019-12-31"]
        self.assertIsNone(q4_2019[QF.index("epsDiluted")])
        self.assertIsNotNone(q4_2019[QF.index("revenue")])
        annual = {row[0]: row for row in entry["y"]}
        self.assertAlmostEqual(annual["2015-12-31"][YF.index("epsDiluted")],
                               sum(true_eps(2015, quarter) for quarter in (1, 2, 3, 4)), places=4)
        # growth.json keeps its rule: the annual pair across the basis change (2017 vs 2016) is null, not fake
        self.assertEqual(len(y["eps"]), 10)
        self.assertIsNone(y["eps"][8])
        self.assertTrue(all(value is not None for slot, value in enumerate(y["eps"]) if slot != 8))
        self.assertTrue(all(value is not None for value in y["rev"]))

    def test_an_odd_share_jump_is_a_basis_break_not_a_split(self):
        frames = ten_year_company(split=False)
        # 1.8x share count from 2021Q1 on (a merger): EPS of every older period is dropped, revenue kept
        bumped = []
        for request, points in frames:
            if request.key == SHARES and request.kind == "q" and request.label >= sec.quarter_number(2021, 1):
                points = [(cik, start, end, value * 1.8) for cik, start, end, value in points]
            bumped.append((request, points))
        entry, _, _ = history_of(bumped)
        rows = {row[0]: row for row in entry["q"]}
        self.assertIn("eps-basis-break", entry["issues"])
        self.assertIsNone(rows["2020-09-30"][QF.index("epsDiluted")])
        self.assertIsNone(rows["2016-03-31"][QF.index("dilutedShares")])
        self.assertIsNotNone(rows["2016-03-31"][QF.index("revenue")])
        self.assertIsNotNone(rows["2021-03-31"][QF.index("epsDiluted")])
        self.assertEqual(entry["split"], [])

    def test_a_split_with_revenue_doubling_is_a_merger(self):
        points = [{"end": date(2020, 3, 31), "derived": False,
                   "values": {"revenue": 100, "netIncome": 10, "epsDiluted": 1.0, "epsBasic": 1.0, "dilutedShares": 10}},
                  {"end": date(2020, 6, 30), "derived": False,
                   "values": {"revenue": 250, "netIncome": 20, "epsDiluted": 1.0, "epsBasic": 1.0, "dilutedShares": 20}}]
        issues = set()
        self.assertEqual(sec.adjust_splits(points, issues), [])
        self.assertEqual(issues, {"eps-basis-break"})
        self.assertIsNone(points[0]["values"]["epsDiluted"])

    def test_reverse_split_and_ratios(self):
        self.assertEqual(sec.nice_split(0.1), 0.1)
        self.assertEqual(sec.nice_split(2.04), 2)
        self.assertEqual(sec.nice_split(1.52), 1.5)
        self.assertIsNone(sec.nice_split(1.8))
        points = [{"end": date(2020, 3, 31), "derived": False,
                   "values": {"revenue": 100, "netIncome": -10, "epsDiluted": -0.1, "epsBasic": -0.1, "dilutedShares": 100}},
                  {"end": date(2020, 6, 30), "derived": False,
                   "values": {"revenue": 110, "netIncome": -10, "epsDiluted": -1.0, "epsBasic": -1.0, "dilutedShares": 10}}]
        issues = set()
        events = sec.adjust_splits(points, issues)
        self.assertEqual(events, [(date(2020, 6, 30), 0.1)])
        self.assertAlmostEqual(points[0]["values"]["epsDiluted"], -1.0)
        self.assertAlmostEqual(points[0]["values"]["dilutedShares"], 10)

    def test_scale_errors_are_dropped_from_the_series(self):
        frames = []
        for request, points in ten_year_company(split=False):
            if request.key == REVENUES and request.period == "CY2018Q2":
                points = [(cik, start, end, value * 1000) for cik, start, end, value in points]
            frames.append((request, points))
        entry, _, _ = history_of(frames)
        rows = {row[0]: row for row in entry["q"]}
        self.assertIsNone(rows["2018-06-30"][QF.index("revenue")])
        self.assertIsNotNone(rows["2018-03-31"][QF.index("revenue")])
        self.assertIn("scale-dropped", entry["issues"])

    def test_foreign_issuer_history_is_annual_in_its_currency(self):
        frames = []
        for year in range(2015, 2026):
            start, end = f"{year}-01-01", f"{year}-12-31"
            frames.append(frame("ifrs-full:DilutedEarningsLossPerShare", f"CY{year}", [(9, start, end, 10 + year - 2015)],
                                foreign=True, currency="TWD"))
            frames.append(frame("ifrs-full:Revenue", f"CY{year}", [(9, start, end, 1e9 * (year - 2010))],
                                foreign=True, currency="TWD"))
        entry, q, y = history_of(frames, cik=9)
        self.assertEqual(entry["cur"], "TWD")
        self.assertEqual(entry["f"], 1)
        self.assertEqual(entry["q"], [])
        self.assertEqual(len(entry["y"]), 11)
        self.assertIsNone(entry["y"][0][YF.index("netIncome")])
        self.assertEqual(len(y["eps"]), 10)


class ShardTest(unittest.TestCase):
    def test_shard_hash_matches_the_frontend(self):
        # Same values are asserted in atlas/tests/frames-history.test.ts
        self.assertEqual([sec.history_shard(key) for key in ("AAPL", "BRK-B", "GOOGL", "ZZZZ", "A")], [11, 117, 39, 37, 76])

    def test_shards_are_json_one_symbol_per_line_and_only_rewritten_on_change(self):
        entry, _, _ = history_of(ten_year_company())
        history = {"AAA": entry, "BBB": {**entry, "cik": 78}}
        with tempfile.TemporaryDirectory() as tmp:
            stats = sec.write_history(tmp, history, TODAY, NOW)
            self.assertEqual(stats["written"], sec.HISTORY_SHARDS)
            path = sec.shard_path(tmp, sec.history_shard("AAA"))
            with open(path, encoding="utf-8") as file:
                text = file.read()
            document = json.loads(text)
            self.assertEqual(document["fields"], sec.HISTORY_FIELDS)
            self.assertEqual(document["shards"], sec.HISTORY_SHARDS)
            self.assertTrue(any(line.startswith('"AAA":') for line in text.splitlines()))
            self.assertEqual(sec.read_history(tmp)["BBB"]["cik"], 78)
            mtime = os.path.getmtime(path)
            again = sec.write_history(tmp, history, TODAY, datetime(2026, 10, 16, tzinfo=timezone.utc))
            self.assertEqual(again["written"], 0)
            self.assertEqual(os.path.getmtime(path), mtime)
            changed = sec.write_history(tmp, {"AAA": entry}, TODAY, NOW)
            self.assertEqual(changed["written"], 1)     # only the shard that lost BBB

    def test_a_run_without_the_foreign_part_keeps_stored_foreign_histories(self):
        stored = {"TSM": {"cik": 9, "f": 1, "cur": "TWD", "q": [], "y": [["2025-12-31", 2025, 1, 2, 3, 4, 5]]},
                  "OLD": {"cik": 5, "f": 0, "cur": "USD", "q": [], "y": []}}
        history = sec.keep_foreign_history({"AAPL": {"cik": 1, "f": 0}}, stored)
        self.assertEqual(sorted(history), ["AAPL", "TSM"])

    def test_summary_counts(self):
        entry, q, y = history_of(ten_year_company())
        document = {"asOf": "2026-10-15", "rows": [["AAA", 77, q["end"], y["end"], q["eps"], q["rev"], y["eps"], y["rev"],
                                                     q["mask"], y["mask"], q["derived"], 0, None]]}
        summary = sec.summarize(document, {"AAA": entry})
        self.assertEqual(summary["history"]["quarterEnds"], ["2016-03-31", "2026-06-30"])
        self.assertEqual(summary["history"]["years>=10"], 1)
        self.assertEqual(summary["usSec"]["coverage"]["revY>=10"], 1)
        self.assertNotIn("epsY>=10", summary["usSec"]["coverage"])     # the split pair is null


class RunHistoryTest(unittest.TestCase):
    def test_run_writes_shards_and_summary_mode_reads_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "fundamentals", "us-sec.json")
            args = SimpleNamespace(report_dir=tmp, out=out, no_foreign=False, foreign_max_age_days=0, force=False,
                                   history_dir=None, summary=False)
            logs = []
            self.assertEqual(sec.run(args, client=FakeSec(), now=datetime(2026, 8, 20, tzinfo=timezone.utc), log=logs.append), 0)
            history = sec.read_history(os.path.join(tmp, "fundamentals", "us-history"))
            self.assertIn("NVDA", history)
            self.assertTrue(any(line.startswith("SUMMARY ") for line in logs))
            logs.clear()
            args.summary = True
            self.assertEqual(sec.run(args, client=None, log=logs.append), 0)
            summary = json.loads(logs[-1])
            self.assertEqual(summary["history"]["symbols"], len(history))


FIXTURE_DIR = os.path.join(ROOT, "atlas", "tests", "fixtures", "frames")
FIXTURE_SYMBOL = "ZFRM"


def fixture_shard():
    """(path, text) of the frontend fixture: one frames-only stock with ~10 years and an inferred 4-for-1 split."""
    entry, _, _ = history_of(ten_year_company(cik=900, base=0.5, scale=1e6), cik=900)
    index = sec.history_shard(FIXTURE_SYMBOL)
    path = os.path.join(FIXTURE_DIR, f"us-history-{index:03d}.json")
    return path, sec.shard_text(index, {FIXTURE_SYMBOL: entry}, TODAY, NOW)


class FrontendFixtureTest(unittest.TestCase):
    """atlas/tests/fixtures/frames is the builder's real output (vitest and the e2e tests read it)."""

    def test_fixture_is_current(self):
        path, text = fixture_shard()
        with open(path, encoding="utf-8") as file:
            self.assertEqual(file.read(), text, "regenerate: python tests/test_fundamentals_sec_history.py --write-fixture")


if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        fixture_path, fixture_text = fixture_shard()
        os.makedirs(os.path.dirname(fixture_path), exist_ok=True)
        with open(fixture_path, "w", encoding="utf-8") as fixture_file:
            fixture_file.write(fixture_text)
        print(fixture_path)
    else:
        unittest.main()

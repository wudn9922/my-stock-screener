"""
Screener growth data: atlas/scripts/build_growth.py (docs/report/financials/*.json -> docs/report/growth.json).

Offline; documents are small hand-written dicts in the financials file layout.
Run: python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_growth as growth  # noqa: E402

NOW = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
SLOT = {"epsQ": 5, "revQ": 6, "epsY": 7, "revY": 8, "tQ": 9, "tY": 10, "qEnd": 3, "yEnd": 4, "lv": 1, "src": 2}


def quarter_end(fy, fq):
    return {1: f"{fy}-03-31", 2: f"{fy}-06-30", 3: f"{fy}-09-30", 4: f"{fy}-12-31"}[fq]


def section(rows, quarterly, src=None):
    """rows: (end, fy, fq|None, epsD, epsB, revenue, shares)."""
    result = {
        "end": [r[0] for r in rows],
        "fy": [r[1] for r in rows],
        "values": {
            "revenue": [r[5] for r in rows],
            "epsDiluted": [r[3] for r in rows],
            "epsBasic": [r[4] for r in rows],
            "dilutedShares": [r[6] for r in rows],
        },
    }
    if quarterly:
        result["fq"] = [r[2] for r in rows]
    if src:
        result["src"] = [src] * len(rows)
    return result


def document(quarters=(), annual=(), events=(), level="good", src=None):
    return {
        "version": 1,
        "symbol": "X",
        "quarterly": section(list(quarters), True, src),
        "annual": section(list(annual), False, src),
        "split": {"adjusted": bool(events), "events": [list(e) for e in events]},
        "quality": {"level": level, "annualYears": len(annual), "quarters": len(quarters), "issues": []},
    }


def q(fy, fq, eps=None, revenue=None, basic=None, shares=None):
    return (quarter_end(fy, fq), fy, fq, eps, basic if basic is not None else eps, revenue, shares)


def y(fy, eps=None, revenue=None, shares=None):
    return (f"{fy}-12-31", fy, None, eps, eps, revenue, shares)


def row_of(doc, key="X", today=TODAY):
    row = growth.build_row(key, doc, today)
    return None if row is None else dict(zip(growth.FIELDS, row))


class QuarterlyAlignmentTest(unittest.TestCase):
    def test_same_quarter_a_year_earlier_not_the_neighbour(self):
        # Seasonal company: Q4 is always big. Neighbour division would show -75%; the same quarter is +50%.
        rows = [
            q(2025, 1, 1.0, 100), q(2025, 2, 1.0, 100), q(2025, 3, 1.0, 100), q(2025, 4, 4.0, 400),
            q(2026, 1, 1.5, 150), q(2026, 2, 1.2, 120),
        ]
        result = row_of(document(rows))
        self.assertEqual(result["epsQ"], [20.0, 50.0])
        self.assertEqual(result["revQ"], [20.0, 50.0])
        self.assertEqual(result["qEnd"], "2026-06-30")

    def test_missing_quarter_leaves_a_null_slot_and_nothing_shifts(self):
        rows = [
            q(2024, 1, 1.0), q(2024, 2, 1.0), q(2024, 3, 2.0), q(2024, 4, 1.0),
            q(2025, 1, 1.1), q(2025, 2, 1.2), q(2025, 3, 2.4),  # 2025Q4 missing
            q(2026, 1, 1.5), q(2026, 2, 1.8),
        ]
        result = row_of(document(rows))
        # slots: 2026Q2 (+50), 2026Q1 (+36.4), 2025Q4 (no period), 2025Q3 (+20 vs 2024Q3 = 2.0 -> 2.4)
        self.assertEqual(result["epsQ"], [50.0, 36.4, None, 20.0])

    def test_pairs_by_fiscal_labels_for_a_non_calendar_year(self):
        rows = [
            ("2025-09-27", 2025, 4, 2.0, 2.0, 200, None), ("2025-12-27", 2026, 1, 2.2, 2.2, 220, None),
            ("2026-03-28", 2026, 2, 1.0, 1.0, 100, None), ("2026-06-27", 2026, 3, 1.5, 1.5, 150, None),
            ("2026-09-26", 2026, 4, 3.0, 3.0, 300, None),
            ("2024-09-28", 2024, 4, 1.0, 1.0, 100, None), ("2024-12-28", 2025, 1, 2.0, 2.0, 200, None),
            ("2025-03-29", 2025, 2, 1.0, 1.0, 100, None), ("2025-06-28", 2025, 3, 1.0, 1.0, 100, None),
        ]
        result = row_of(document(rows))
        self.assertEqual(result["epsQ"], [50.0, 50.0, 0.0, 10.0])
        self.assertEqual(result["revQ"], [50.0, 50.0, 0.0, 10.0])

    def test_pair_with_dates_not_a_year_apart_is_null(self):
        # A fiscal year-end change: the "same" labelled quarter is 8 months earlier, so no YoY
        rows = [q(2025, 2, 1.0), ("2026-02-28", 2026, 2, 2.0, 2.0, 10, None)]
        result = row_of(document(rows))
        self.assertIsNone(result)

    def test_duplicate_label_is_ambiguous(self):
        rows = [q(2025, 2, 1.0), q(2025, 2, 5.0), q(2026, 2, 2.0)]
        self.assertIsNone(row_of(document(rows)))

    def test_stale_latest_quarter_gives_nothing(self):
        rows = [q(2024, 2, 1.0), q(2025, 2, 2.0)]
        self.assertEqual(row_of(document(rows), today=date(2026, 3, 1))["epsQ"], [100.0])
        self.assertIsNone(row_of(document(rows), today=date(2026, 6, 1)))

    def test_leading_period_without_any_value_is_skipped(self):
        # The newest period has only nulls (Yahoo placeholder): slot 0 is the newest period WITH data
        rows = [q(2025, 1, 1.0), q(2026, 1, 2.0), q(2026, 2)]
        self.assertEqual(row_of(document(rows))["epsQ"], [100.0])


class EpsRulesTest(unittest.TestCase):
    def test_turnaround_is_flagged_and_yoy_stays_null(self):
        rows = [q(2025, 1, -0.5), q(2025, 2, 0.0), q(2026, 1, 0.4), q(2026, 2, 0.3)]
        result = row_of(document(rows))
        self.assertEqual(result["epsQ"], [])
        self.assertEqual(result["tQ"], 0b11)

    def test_loss_that_narrows_or_widens_is_not_growth(self):
        rows = [q(2025, 1, -1.0), q(2025, 2, -1.0), q(2026, 1, -0.5), q(2026, 2, -2.0)]
        self.assertIsNone(row_of(document(rows)))  # nothing computable at all: no row
        with_revenue = [q(2025, 1, -1.0, 100), q(2025, 2, -1.0, 100), q(2026, 1, -0.5, 110), q(2026, 2, -2.0, 120)]
        result = row_of(document(with_revenue))
        self.assertEqual(result["epsQ"], [])
        self.assertEqual(result["tQ"], 0)
        self.assertEqual(result["revQ"], [20.0, 10.0])

    def test_profit_to_loss_is_a_negative_yoy(self):
        result = row_of(document([q(2025, 2, 1.0), q(2026, 2, -0.5)]))
        self.assertEqual(result["epsQ"], [-150.0])

    def test_never_mixes_diluted_with_basic(self):
        rows = [
            (quarter_end(2025, 1), 2025, 1, None, 1.0, None, None),
            (quarter_end(2026, 1), 2026, 1, 1.5, None, None, None),
        ]
        self.assertIsNone(row_of(document(rows)))
        rows = [
            (quarter_end(2025, 2), 2025, 2, None, 2.0, None, None),
            (quarter_end(2026, 2), 2026, 2, None, 3.0, None, None),
        ]
        self.assertEqual(row_of(document(rows))["epsQ"], [50.0])

    def test_revenue_needs_a_positive_year_ago_value_and_null_columns_are_tolerated(self):
        rows = [q(2025, 1, 1.0, None), q(2025, 2, 1.0, 0), q(2026, 1, 2.0, 50), q(2026, 2, 2.0, 50)]
        result = row_of(document(rows))
        self.assertEqual(result["epsQ"], [100.0, 100.0])
        self.assertEqual(result["revQ"], [])

    def test_split_without_matching_adjustment_is_not_trusted(self):
        events = [("2026-03-10", 4.0)]
        raw = [q(2025, 2, 4.0, shares=1_000), q(2026, 2, 1.2, shares=4_000)]  # old period not restated
        self.assertIsNone(row_of(document(raw, events=events)))
        restated = [q(2025, 2, 1.0, shares=4_000), q(2026, 2, 1.2, shares=4_000)]
        self.assertEqual(row_of(document(restated, events=events))["epsQ"], [20.0])
        # A big issuance without a split event keeps its (real) EPS change
        issued = [q(2025, 2, 4.0, shares=1_000), q(2026, 2, 1.2, shares=4_000)]
        self.assertEqual(row_of(document(issued))["epsQ"], [-70.0])


class AnnualTest(unittest.TestCase):
    def test_three_years_need_four_annual_periods(self):
        four = [y(2022, 1.0, 100), y(2023, 1.5, 120), y(2024, 3.0, 150), y(2025, 3.6, 300)]
        result = row_of(document(annual=four))
        self.assertEqual(result["epsY"], [20.0, 100.0, 50.0])
        self.assertEqual(result["revY"], [100.0, 25.0, 20.0])
        self.assertEqual(result["yEnd"], "2025-12-31")
        self.assertIsNone(result["qEnd"])
        three = row_of(document(annual=four[1:]))
        self.assertEqual(three["epsY"], [20.0, 100.0])

    def test_gap_year_breaks_the_chain_and_loss_year_is_a_turn(self):
        rows = [y(2021, 1.0), y(2023, 2.0), y(2024, -1.0), y(2025, 0.5)]
        result = row_of(document(annual=rows))
        # 2025 vs 2024 loss -> turn; 2024 vs 2023 = -150; 2023 vs 2022 missing -> null
        self.assertEqual(result["epsY"], [None, -150.0])
        self.assertEqual(result["tY"], 0b1)

    def test_stale_annual(self):
        rows = [y(2023, 1.0), y(2024, 2.0)]
        self.assertIsNone(row_of(document(annual=rows), today=date(2026, 10, 1)))


class DocumentTest(unittest.TestCase):
    def test_taiwan_short_history(self):
        # Like a real Yahoo Taiwan file: six quarters, Q4 EPS null, oldest revenue null, four years annual
        quarters = [
            q(2025, 1, 1.0, None), q(2025, 2, 1.0, 100), q(2025, 3, 1.0, 100), q(2025, 4, None, 100),
            q(2026, 1, 1.3, 110), q(2026, 2, 1.5, 130),
        ]
        annual = [y(2022, 3.0, 800), y(2023, 3.3, 880), y(2024, 3.96, 900), y(2025, 4.0, 990)]
        result = row_of(document(quarters, annual, level="partial"))
        self.assertEqual(result["epsQ"], [50.0, 30.0])
        self.assertEqual(result["revQ"], [30.0])  # 2026Q1 revenue has no year-ago value (null, cut)
        self.assertEqual(result["epsY"], [1.0, 20.0, 10.0])
        self.assertEqual(result["lv"], "partial")
        self.assertEqual(result["src"], "yahoo")

    def test_sec_source_and_unusable_documents(self):
        result = row_of(document([q(2025, 1, 1.0), q(2026, 1, 2.0)], src="sec"))
        self.assertEqual(result["src"], "sec")
        self.assertEqual(result["epsQ"], [100.0])
        self.assertIsNone(row_of(document()))
        self.assertIsNone(growth.build_row("X", None, TODAY))
        self.assertIsNone(growth.build_row("X", {"quarterly": "bad", "annual": 5}, TODAY))
        self.assertEqual(row_of(document([q(2025, 1, 1.0), q(2026, 1, 2.0)], level="weird"))["lv"], "limited")

    def test_garbage_values_are_nulls(self):
        doc = document([q(2025, 1, 1.0, 10), q(2026, 1, 2.0, 20)])
        doc["quarterly"]["values"]["revenue"][1] = "n/a"
        doc["quarterly"]["values"]["epsDiluted"].pop()  # short column
        doc["quarterly"]["fq"][0] = 9  # bad label drops that period
        self.assertIsNone(row_of(doc))

    def test_market_from_the_suffix(self):
        self.assertEqual(growth.market_of("2330.TW"), "TW")
        self.assertEqual(growth.market_of("6488.TWO"), "TW")
        self.assertEqual(growth.market_of("BRK-B"), "US")


class RunTest(unittest.TestCase):
    def write_all(self, directory, documents, extra=()):
        os.makedirs(directory, exist_ok=True)
        for key, doc in documents.items():
            with open(os.path.join(directory, f"{key}.json"), "w", encoding="utf-8") as file:
                json.dump(doc, file)
        for name, body in extra:
            with open(os.path.join(directory, name), "w", encoding="utf-8") as file:
                file.write(body)

    def test_run_writes_compact_sorted_rows_and_skips_rewrite_when_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            fin = os.path.join(tmp, "financials")
            out = os.path.join(tmp, "growth.json")
            good = document([q(2025, 2, 1.0, 100), q(2026, 2, 1.5, 120)], [y(2024, 1.0), y(2025, 2.0)])
            self.write_all(
                fin,
                {"2330.TW": good, "AAPL": good, "EMPTY": document()},
                extra=[("index.json", "{}"), ("broken.json", "{not json"), ("notes.txt", "x")],
            )
            summary = growth.run(fin, out, NOW)
            self.assertTrue(summary["written"])
            self.assertEqual(summary["unreadable"], ["broken.json"])
            self.assertEqual(summary["files"], 3)
            self.assertEqual(summary["rows"], 2)
            self.assertEqual(summary["coverage"]["TW"]["epsQ>=1"], 1)
            with open(out, encoding="utf-8") as file:
                data = json.load(file)
            self.assertEqual(data["version"], 1)
            self.assertEqual(data["generatedAt"], "2026-10-14T06:00:00Z")
            self.assertEqual(data["fields"], growth.FIELDS)
            self.assertEqual([r[0] for r in data["rows"]], ["2330.TW", "AAPL"])
            self.assertEqual(data["rows"][0][5], [50.0])
            # Unchanged content: the file (and its generatedAt) stays
            later = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)
            self.assertFalse(growth.run(fin, out, later)["written"])
            with open(out, encoding="utf-8") as file:
                self.assertEqual(json.load(file)["generatedAt"], "2026-10-14T06:00:00Z")
            # Changed content: rewritten with the new time
            self.write_all(fin, {"2330.TW": document([q(2025, 2, 1.0), q(2026, 2, 3.0)])})
            self.assertTrue(growth.run(fin, out, later)["written"])
            with open(out, encoding="utf-8") as file:
                data = json.load(file)
            self.assertEqual(data["generatedAt"], "2026-10-15T06:00:00Z")
            self.assertEqual(data["rows"][0][5], [200.0])

    def test_run_without_files_fails_and_keeps_the_old_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "growth.json")
            with open(out, "w", encoding="utf-8") as file:
                file.write("keep")
            with self.assertRaises(RuntimeError):
                growth.run(os.path.join(tmp, "nothing"), out, NOW)
            with open(out, encoding="utf-8") as file:
                self.assertEqual(file.read(), "keep")


def block(end, src, eps=(10.0,), rev=(), mask=0, derived=0):
    return {"eps": list(eps), "rev": list(rev), "mask": mask, "end": end, "src": src, "derived": derived}


class MergeTest(unittest.TestCase):
    def test_newest_period_first_then_the_better_source(self):
        yahoo = block("2026-06-30", "yahoo", eps=(1.0,))
        mops = block("2026-06-30", "mops", eps=(2.0, 3.0, 4.0, 5.0))
        self.assertIs(growth.choose_block([yahoo, mops]), mops)
        # Yahoo already has the next quarter (> FRESHER_DAYS newer): the newest period wins
        newer = block("2026-09-30", "yahoo", eps=(9.0,))
        self.assertIs(growth.choose_block([newer, mops]), newer)
        # US: SEC companyfacts files > frames > Yahoo files; 52/53-week ends a few days apart are one period
        sec_file, frames, yahoo_file = block("2026-06-27", "sec"), block("2026-06-28", "frames"), block("2026-06-30", "yahoo")
        self.assertIs(growth.choose_block([yahoo_file, frames, sec_file]), sec_file)
        self.assertIs(growth.choose_block([yahoo_file, frames]), frames)
        self.assertIsNone(growth.choose_block([None, {"end": "bad", "src": "sec"}]))

    def test_blocks_never_mix_and_lv_only_from_a_used_financials_file(self):
        financials = ("partial", block("2026-06-30", "yahoo", eps=(1.0, 2.0)), block("2025-12-31", "yahoo", eps=(5.0,)))
        official = (None, block("2026-06-30", "mops", eps=(3.0, 4.0, 5.0, 6.0), derived=0b100), None)
        row = dict(zip(growth.FIELDS, growth.merge_candidates("2330.TW", [financials, official])))
        self.assertEqual(row["epsQ"], [3.0, 4.0, 5.0, 6.0])
        self.assertEqual((row["src"], row["dQ"]), ("mops", 0b100))
        self.assertEqual((row["epsY"], row["srcY"], row["yEnd"]), ([5.0], "yahoo", "2025-12-31"))
        self.assertEqual(row["lv"], "partial")                       # the annual block is the file's
        only_official = dict(zip(growth.FIELDS, growth.merge_candidates("2330.TW", [official])))
        self.assertIsNone(only_official["lv"])
        self.assertIsNone(only_official["srcY"])
        self.assertIsNone(growth.merge_candidates("X", [(None, None, None)]))

    def test_sec_frame_rows_are_checked_again(self):
        fields = ["symbol", "cik", "qEnd", "yEnd", "epsQ", "revQ", "epsY", "revY", "tQ", "tY", "dQ", "f", "cur"]
        document = {"version": 1, "fields": fields, "rows": [
            ["AAA", 1, "2026-06-30", "2025-12-31", [10.0, None, "x"], [5.0], [1.0], [], 0, 0, 0b10, 0, None],
            ["OLD", 2, "2025-06-30", "2025-12-31", [10.0], [], [], [], 0, 0, 0, 0, None],     # stale quarter, no annual
            ["TRN", 3, "2026-06-30", None, [], [], [], [], 0b1, 0, 0, 0, None],
            ["BAD", 4], "junk", [None],
        ]}
        candidates = growth.sec_frame_candidates(document, TODAY)
        self.assertEqual(sorted(candidates), ["AAA", "TRN"])
        _, q, y = candidates["AAA"]
        self.assertEqual((q["eps"], q["rev"], q["derived"], q["src"]), ([10.0], [5.0], 0b10, "frames"))
        self.assertEqual((y["eps"], y["end"]), ([1.0], "2025-12-31"))
        self.assertEqual(candidates["TRN"][1]["mask"], 1)
        self.assertEqual(growth.sec_frame_candidates({"version": 2}, TODAY), {})
        self.assertEqual(growth.sec_frame_candidates(None, TODAY), {})

    def test_run_merges_the_official_files(self):
        import build_fundamentals_tw as tw

        with tempfile.TemporaryDirectory() as tmp:
            fin, fundamentals = os.path.join(tmp, "financials"), os.path.join(tmp, "fundamentals")
            os.makedirs(fin)
            os.makedirs(fundamentals)
            # Yahoo file: two quarters a year apart only
            with open(os.path.join(fin, "2330.TW.json"), "w", encoding="utf-8") as file:
                json.dump(document([q(2025, 2, 1.0, 100), q(2026, 2, 1.1, 110)]), file)
            store = {"fetch": {}, "data": {"2330": {}, "6488": {}}}
            for code in ("2330", "6488"):
                for year, base in ((2025, 100), (2026, 130)):
                    for quarter in (1, 2):
                        store["data"][code][tw.season_key(year, quarter)] = [base * quarter, base * quarter / 100, base * quarter * 10, 0]
            with open(os.path.join(fundamentals, "tw.json"), "w", encoding="utf-8") as file:
                json.dump(tw.dump_store(store, tw.target_seasons(TODAY)), file)
            fields = ["symbol", "cik", "qEnd", "yEnd", "epsQ", "revQ", "epsY", "revY", "tQ", "tY", "dQ", "f", "cur"]
            with open(os.path.join(fundamentals, "us-sec.json"), "w", encoding="utf-8") as file:
                json.dump({"version": 1, "fields": fields, "rows": [
                    ["RIVN", 1, "2026-06-30", "2025-12-31", [45.0], [60.0], [], [12.0], 0, 0, 0, 0, None]]}, file)
            universe = os.path.join(tmp, "universe.json")
            with open(universe, "w", encoding="utf-8") as file:
                json.dump({"fields": ["symbol"], "markets": {"TW": {"rows": [["2330.TW"], ["6488.TWO"]]}}}, file)
            out = os.path.join(tmp, "growth.json")
            summary = growth.run(fin, out, NOW, fundamentals, universe)
            with open(out, encoding="utf-8") as file:
                data = json.load(file)
        rows = {row[0]: dict(zip(data["fields"], row)) for row in data["rows"]}
        self.assertEqual(sorted(rows), ["2330.TW", "6488.TWO", "RIVN"])
        self.assertEqual(rows["2330.TW"]["src"], "mops")                # official beats the Yahoo file
        self.assertEqual(rows["2330.TW"]["epsQ"], [30.0, 30.0])
        self.assertEqual(rows["6488.TWO"]["revQ"], [30.0, 30.0])
        self.assertEqual((rows["RIVN"]["src"], rows["RIVN"]["srcY"], rows["RIVN"]["epsQ"]), ("frames", "frames", [45.0]))
        self.assertEqual(summary["official"], {"tw": 2, "frames": 1})
        self.assertEqual(summary["coverage"]["TW"]["srcQ:mops"], 2)
        self.assertEqual(data["fields"], growth.FIELDS)

    def test_a_broken_official_file_is_skipped_with_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            fin, fundamentals = os.path.join(tmp, "financials"), os.path.join(tmp, "fundamentals")
            os.makedirs(fin)
            os.makedirs(fundamentals)
            with open(os.path.join(fin, "AAPL.json"), "w", encoding="utf-8") as file:
                json.dump(document([q(2025, 2, 1.0), q(2026, 2, 1.5)]), file)
            with open(os.path.join(fundamentals, "tw.json"), "w", encoding="utf-8") as file:
                json.dump({"version": 1, "seasons": "bad"}, file)
            summary = growth.run(fin, os.path.join(tmp, "growth.json"), NOW, fundamentals, None)
        self.assertEqual(summary["rows"], 1)
        self.assertEqual(summary["official"]["tw"], 0)


class RepoDataTest(unittest.TestCase):
    """The checked-in docs/report/financials (skipped when absent)."""

    def test_full_market_size_with_official_sources(self):
        """Real financials + synthetic official files for every universe symbol: the size of a full growth.json."""
        import random

        import build_fundamentals_tw as tw

        directory = os.path.join(ROOT, "docs", "report", "financials")
        universe_path = os.path.join(ROOT, "docs", "report", "universe.json")
        if not os.path.isdir(directory) or not os.path.exists(universe_path):
            self.skipTest("no financials / universe")
        with open(universe_path, encoding="utf-8") as file:
            universe = json.load(file)
        tw_symbols = [row[0] for row in universe["markets"]["TW"]["rows"]]
        us_symbols = [row[0] for row in universe["markets"]["US"]["rows"]]
        rng = random.Random(7)
        store = {"fetch": {}, "data": {}}
        for symbol in tw_symbols:
            code = symbol.split(".")[0]
            row = store["data"][code] = {}
            for key in tw.target_seasons(TODAY):
                year, quarter = tw.parse_season(key)
                if key == "2026Q3":
                    continue
                revenue = rng.randint(100_000, 50_000_000) * quarter
                eps = round(rng.uniform(-1, 8) * quarter, 2)
                row[key] = [revenue, eps, round(eps * rng.randint(10_000, 2_000_000)), 0]
        fields = ["symbol", "cik", "qEnd", "yEnd", "epsQ", "revQ", "epsY", "revY", "tQ", "tY", "dQ", "f", "cur"]
        us_rows = []
        for index, symbol in enumerate(us_symbols):
            pct = lambda: round(rng.uniform(-80, 300), 1)  # noqa: E731
            us_rows.append([symbol.replace(".", "-"), index, "2026-06-30", "2025-12-31", [pct() for _ in range(4)],
                            [pct() for _ in range(4)], [pct() for _ in range(3)], [pct() for _ in range(3)], 0, 0, 0, 0, None])
        with tempfile.TemporaryDirectory() as tmp:
            fundamentals = os.path.join(tmp, "fundamentals")
            os.makedirs(fundamentals)
            tw_path = os.path.join(fundamentals, "tw.json")
            us_path = os.path.join(fundamentals, "us-sec.json")
            tw_bytes = growth.write_atomic(tw_path, tw.dump_store(store, tw.target_seasons(TODAY)))
            us_bytes = growth.write_atomic(us_path, {"version": 1, "fields": fields, "rows": sorted(us_rows)})
            out = os.path.join(tmp, "growth.json")
            summary = growth.run(directory, out, NOW, fundamentals, universe_path)
            with open(out, encoding="utf-8") as file:
                data = json.load(file)
        keys = {row[0] for row in data["rows"]}
        self.assertTrue(all(symbol in keys for symbol in tw_symbols))
        self.assertTrue(all(symbol.replace(".", "-") in keys for symbol in us_symbols))
        # Official data replaced the Yahoo quarterly block of every listed Taiwan stock (a TDR file outside the list keeps it)
        listed = set(tw_symbols)
        self.assertEqual([row[0] for row in data["rows"] if row[0] in listed and row[3] and row[2] != "mops"], [])
        # Sizes of a full market (synthetic values with realistic lengths): reported, and kept in check
        self.assertLess(tw_bytes, 1_400_000)
        self.assertLess(us_bytes, 900_000)
        self.assertLess(summary["bytes"], 1_300_000)
        print(f"\n[full-market sizes] tw.json {tw_bytes:,} B, us-sec.json {us_bytes:,} B, "
              f"growth.json {summary['bytes']:,} B for {summary['rows']:,} rows")

    def test_real_files_produce_small_valid_output_for_both_markets(self):
        directory = os.path.join(ROOT, "docs", "report", "financials")
        if not os.path.isdir(directory):
            self.skipTest("no financials directory")
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "growth.json")
            summary = growth.run(directory, out, NOW)
            self.assertEqual(summary["unreadable"], [])
            # The completed US whole-market backfill adds more real history rows;
            # keep the real-file output compact while allowing that larger cohort.
            self.assertLess(summary["bytes"], 600_000)
            with open(out, encoding="utf-8") as file:
                data = json.load(file)
        markets = {growth.market_of(row[0]) for row in data["rows"]}
        self.assertEqual(markets, {"TW", "US"})
        for row in data["rows"]:
            self.assertEqual(len(row), len(growth.FIELDS))
            for value in row[SLOT["epsQ"]:SLOT["revY"] + 1]:
                self.assertTrue(all(v is None or isinstance(v, (int, float)) for v in value))
                self.assertLessEqual(len(value), 4)


if __name__ == "__main__":
    unittest.main()

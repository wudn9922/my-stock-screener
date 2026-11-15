import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_industry_rotation import MIN_MEMBERS, PARAMS, build, market_method, summarize
from build_data_status import calendar_for
from universe_cache import UNKNOWN_VOLUME_DATES


class IndustryRotationTests(unittest.TestCase):
    def test_equal_returns_common_volume_cohort_and_unknowns(self):
        dates = ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-27"]
        frame = pd.DataFrame({"Close": [100, 105, 110, 115, 121], "Volume": [100, 100, 100, 200, 200]}, index=pd.to_datetime(dates))
        unknown = frame.copy()
        unknown.attrs[UNKNOWN_VOLUME_DATES] = ("2026-10-21T00:00:00",)
        result = summarize([{"return": 10, "frame": frame}, {"return": -5, "frame": unknown}, {"return": None}], 2, dates, min_members=1)
        self.assertAlmostEqual(result["returnPct"], 2.5)
        self.assertEqual(result["upShare"], 50)
        self.assertEqual(result["volumeRatio"], 2)
        self.assertEqual(result["volumeCount"], 1)
        self.assertEqual(result["pairedCount"], 2)
        self.assertAlmostEqual(result["momentumChange"], 0)

    def test_missing_session_not_filled_and_stale_stock_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / "docs/report"
            report.mkdir(parents=True)
            path = report / "universe.json"
            path.write_text(json.dumps({"fields": ["symbol", "asOf", "industry", "changePct", "r5", "r21", "r63", "close", "turnover20"], "markets": {"TW": {"asOf": "2026-10-28", "scope": "full", "rows": [["2330.TW", "2026-10-28", "半導體", 2, 5, 10, 20, 500, 5e9], ["2317.TW", "2026-10-27", "其他", 3, 5, 10, 20, 100, 5e9]]}}}))
            original = path.read_bytes()
            result = build(root)
            rows = result["markets"]["TW"]["periods"]["m1"]["rows"]
            self.assertEqual([r["name"] for r in rows], ["半導體"])
            row = rows[0]
            self.assertEqual(row["count"], 1)
            self.assertIsNone(row["returnPct"])  # below the 3-member minimum: still listed, unranked
            self.assertIsNone(row["rank"])
            self.assertIsNone(row["volumeRatio"])
            self.assertEqual(path.read_bytes(), original)

    def test_full_build_reads_cache_without_writing_it_and_aligns_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / "docs/report"
            bars = root / ".cache/universe-full/bars"
            report.mkdir(parents=True)
            bars.mkdir(parents=True)
            dates = calendar_for("XTAI", 2026).sessions_in_range("2026-01-01", "2026-10-28")[-127:]
            frame = pd.DataFrame({"Open": 100., "High": 100., "Low": 100., "Close": 100., "Volume": [100]*106 + [200]*21}, index=dates)
            frame.index.name = "Date"
            for symbol in ("2330.TW", "2454.TW", "3034.TW"):
                frame.to_csv(bars / f"{symbol}.csv")
            cache = bars / "2330.TW.csv"
            source = {"fields": ["symbol", "asOf", "industry", "changePct", "r5", "r21", "r63", "close", "turnover20"], "markets": {"TW": {"asOf": "2026-10-28", "rows": [[symbol, "2026-10-28", "半導體", 0, 0, 0, 0, 100, 5e9] for symbol in ("2330.TW", "2454.TW", "3034.TW")]}}}
            (report / "universe.json").write_text(json.dumps(source))
            before = cache.read_bytes()
            result = build(root)
            row = result["markets"]["TW"]["periods"]["m1"]["rows"][0]
            self.assertEqual(row["volumeRatio"], 2)
            self.assertEqual(row["volumeCount"], 3)
            self.assertEqual(row["pairedCount"], 3)
            self.assertEqual(cache.read_bytes(), before)


AMOUNT, PRICE = 5_000_000, 5


def stock(ret, cap=None, close=50, turnover=10_000_000):
    return {"return": ret, "close": close, "turnover20": turnover, "marketCap": cap}


def stats(rows, **kw):
    return summarize(rows, 2, [], AMOUNT, PRICE, **kw)


class RobustStatisticsTests(unittest.TestCase):
    def test_median_resists_one_outlier(self):
        result = stats([stock(r) for r in (2, 3, 4, 191.7)])
        self.assertAlmostEqual(result["medianReturnPct"], 3.5)
        self.assertGreater(result["returnPct"], 50)

    def test_illiquid_and_missing_members_are_excluded_everywhere(self):
        rows = [stock(1), stock(2), stock(3),
                stock(500, turnover=1_000),       # below minAmount
                stock(500, close=1),              # below minPrice
                stock(500, turnover=None),        # missing turnover
                {"return": 500, "turnover20": 1e9}]  # missing close
        result = stats(rows)
        self.assertEqual((result["count"], result["liquidCount"], result["validCount"]), (7, 3, 3))
        self.assertEqual(result["returnPct"], 2)
        self.assertEqual(result["medianReturnPct"], 2)
        self.assertEqual(result["upShare"], 100)

    def test_thresholds_are_inclusive(self):
        result = stats([stock(1, turnover=AMOUNT, close=PRICE)] * 3)
        self.assertEqual(result["liquidCount"], 3)

    def test_liquid_member_with_invalid_return_counts_as_liquid_not_valid(self):
        result = stats([stock(1), stock(2), stock(3), stock(None)])
        self.assertEqual((result["liquidCount"], result["validCount"]), (4, 3))

    def test_cap_weighted_mean_and_null_rule(self):
        weighted = stats([stock(10, cap=300), stock(0, cap=100), stock(-10, cap=0)])
        # the zero-cap member is ignored (2 of 3 valid members have a usable cap)
        self.assertAlmostEqual(weighted["capWeightedReturnPct"], 7.5)
        self.assertAlmostEqual(weighted["returnPct"], 0)
        exactly_half = stats([stock(10, cap=300), stock(0, cap=100), stock(1), stock(2)])
        self.assertAlmostEqual(exactly_half["capWeightedReturnPct"], 7.5)
        too_few = stats([stock(10, cap=300), stock(0), stock(1), stock(2)])
        self.assertIsNone(too_few["capWeightedReturnPct"])
        nan_cap = stats([stock(1, cap=float("nan")), stock(2, cap=None), stock(3, cap=-5)])
        self.assertIsNone(nan_cap["capWeightedReturnPct"])

    def test_minimum_group_size_nulls_statistics(self):
        result = stats([stock(5), stock(7), stock(9, turnover=1)])
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["validCount"], 2)
        for key in ("returnPct", "medianReturnPct", "capWeightedReturnPct", "upShare", "volumeRatio",
                    "previousReturnPct", "momentumChange"):
            self.assertIsNone(result[key], key)
        self.assertIsNotNone(stats([stock(5), stock(7), stock(9)])["returnPct"])
        self.assertEqual(MIN_MEMBERS, 3)

    def test_method_block_reads_single_source_params(self):
        tw, us = market_method("TW"), market_method("US")
        self.assertEqual(tw, {"rankBy": "median", "minAmount": 20_000_000, "minPrice": 10, "minMembers": 3})
        self.assertEqual(us, {"rankBy": "median", "minAmount": 5_000_000, "minPrice": 5, "minMembers": 3})
        self.assertEqual(tw["minAmount"], PARAMS["markets"]["tw"]["minAmount"])


class BuildOutputTests(unittest.TestCase):
    def build_us(self, rows):
        fields = ["symbol", "asOf", "sector", "changePct", "r5", "r21", "r63", "close", "turnover20", "marketCap"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "docs/report").mkdir(parents=True)
            source = {"fields": fields, "markets": {"US": {"asOf": "2026-11-12", "scope": "full", "rows": rows}}}
            (root / "docs/report/universe.json").write_text(json.dumps(source))
            return build(root, now="2026-11-13T00:00:00+00:00")

    def test_ranks_by_median_and_keeps_unranked_groups(self):
        def member(i, sector, r21, close=50, turnover=9e6):
            return [f"{sector[:3].upper()}{i}", "2026-11-12", sector, r21, r21, r21, r21, close, turnover, 1e9]
        rows = [member(i, "Outlier", r) for i, r in enumerate((1, 1, 2, 300))]       # mean ~76, median 1.5
        rows += [member(i, "Steady", r) for i, r in enumerate((3, 4, 5))]            # mean = median 4
        rows += [member(i, "Thin", r) for i, r in enumerate((50, 60))]               # < 3 members
        rows += [member(i, "Penny", 80, close=1) for i in range(5)]                  # all illiquid
        result = self.build_us(rows)
        self.assertEqual(result["version"], 1)
        market = result["markets"]["US"]
        self.assertEqual(market["method"], {"rankBy": "median", "minAmount": 5_000_000, "minPrice": 5, "minMembers": 3})
        by_name = {r["name"]: r for r in market["periods"]["m1"]["rows"]}
        self.assertEqual(set(by_name), {"Outlier", "Steady", "Thin", "Penny"})
        order = [r["name"] for r in market["periods"]["m1"]["rows"]]
        self.assertEqual(order[:2], ["Steady", "Outlier"])
        self.assertEqual((by_name["Steady"]["rank"], by_name["Outlier"]["rank"]), (1, 2))
        self.assertGreater(by_name["Outlier"]["returnPct"], by_name["Steady"]["returnPct"])
        for name in ("Thin", "Penny"):
            self.assertIsNone(by_name[name]["rank"])
            self.assertIsNone(by_name[name]["medianReturnPct"])
        self.assertEqual((by_name["Penny"]["count"], by_name["Penny"]["liquidCount"]), (5, 0))

    def test_output_matches_frontend_schema_expectations(self):
        row = ["AAA", "2026-11-12", "Tech", 1, 1, 1, 1, 50, 9e6, 1e9]
        market = self.build_us([row, row[:], row[:]])["markets"]["US"]
        self.assertEqual(set(market), {"asOf", "scope", "method", "periods"})
        self.assertEqual(set(market["periods"]), {"d1", "w1", "m1", "m3"})
        expected = {"name", "count", "liquidCount", "validCount", "returnPct", "medianReturnPct", "capWeightedReturnPct",
                    "upShare", "volumeRatio", "volumeCount", "pairedCount", "previousReturnPct", "momentumChange", "rank"}
        for period in market["periods"].values():
            self.assertEqual(set(period), {"startDate", "endDate", "volumePreviousStart", "rows"})
            for entry in period["rows"]:
                self.assertEqual(set(entry), expected)
        json.dumps(market, allow_nan=False)


if __name__ == "__main__":
    unittest.main()

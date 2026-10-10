"""
F2 screener metrics: report_metrics.py and the main.py report plumbing.

Run: python3 -m unittest discover -s tests
Synthetic prices only; yfinance is stubbed before main is imported.
"""
import json
import os
import sys
import tempfile
import types
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import report_metrics  # noqa: E402

UNIVERSE_FIELDS = [
    "symbol", "name", "close", "changePct", "volume", "r5", "r21", "r63",
    "ma20", "ma50", "ma200", "ma20Slope5", "trend", "hi52", "fromHi52", "hiBars",
    "rs21", "rs63", "rsRank", "volRatio", "turnover20",
]


def make_prices(closes, volumes=None, highs=None, end="2026-10-09"):
    closes = [float(value) for value in closes]
    index = pd.bdate_range(end=end, periods=len(closes))
    volumes = volumes if volumes is not None else [1000.0] * len(closes)
    highs = highs if highs is not None else closes
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [float(value) for value in highs],
            "Low": closes,
            "Close": closes,
            "Volume": [float(value) for value in volumes],
        },
        index=index,
    )


def geometric(start, ratio, count):
    return [start * ratio ** i for i in range(count)]


def bench_series(closes, end="2026-10-09"):
    return pd.Series(
        [float(value) for value in closes],
        index=pd.bdate_range(end=end, periods=len(closes)),
    )


def write_csv(directory, symbol, df):
    df = df.copy()
    df.index.name = "Date"
    df.to_csv(os.path.join(directory, f"{symbol}.csv"))


def strict_json(value):
    return json.loads(json.dumps(value, allow_nan=False))


class ComputeStockMetricsTest(unittest.TestCase):
    def test_rising_stock(self):
        df = make_prices(geometric(100, 1.01, 260))
        metrics = report_metrics.compute_stock_metrics(df, None, "SPY")

        self.assertEqual(metrics["bars"], 260)
        self.assertAlmostEqual(metrics["r5"], round((1.01 ** 5 - 1) * 100, 2))
        self.assertAlmostEqual(metrics["r21"], round((1.01 ** 21 - 1) * 100, 2))
        self.assertAlmostEqual(metrics["r63"], round((1.01 ** 63 - 1) * 100, 2))

        close = df["Close"]
        for window in (20, 50, 200):
            expected = round((close.iloc[-1] / close.tail(window).mean() - 1) * 100, 2)
            self.assertAlmostEqual(metrics["ma"][str(window)], expected)
        self.assertTrue(0 < metrics["ma"]["20"] < metrics["ma"]["50"] < metrics["ma"]["200"])

        ma20_now = close.tail(20).mean()
        ma20_prev = close.iloc[-25:-5].mean()
        self.assertAlmostEqual(metrics["ma20Slope5"], round((ma20_now / ma20_prev - 1) * 100, 2))
        self.assertEqual(metrics["trend"], "up")
        self.assertEqual(metrics["hiBars"], 252)
        self.assertAlmostEqual(metrics["hi52"], round(close.iloc[-1], 2))
        self.assertEqual(metrics["fromHi52"], 0)
        self.assertEqual(metrics["rsBench"], "SPY")
        self.assertIsNone(metrics["rs21"])
        self.assertIsNone(metrics["rsRank"])
        self.assertEqual(metrics["volRatio"], 1.0)
        self.assertEqual(
            metrics["turnover20"],
            round(float((df["Close"] * df["Volume"]).tail(20).mean())),
        )

    def test_flat_stock(self):
        metrics = report_metrics.compute_stock_metrics(make_prices([50] * 120), None, "^TWII")

        self.assertEqual(metrics["r5"], 0)
        self.assertEqual(metrics["ma"]["20"], 0)
        self.assertEqual(metrics["ma"]["50"], 0)
        self.assertIsNone(metrics["ma"]["200"])
        self.assertEqual(metrics["ma20Slope5"], 0)
        self.assertEqual(metrics["trend"], "mixed")
        self.assertEqual(metrics["fromHi52"], 0)
        self.assertEqual(metrics["hiBars"], 120)
        self.assertEqual(metrics["rsBench"], "^TWII")

    def test_falling_stock(self):
        closes = geometric(200, 0.99, 300)
        df = make_prices(closes)
        metrics = report_metrics.compute_stock_metrics(df, None, "SPY")

        self.assertEqual(metrics["trend"], "down")
        self.assertLess(metrics["ma20Slope5"], 0)
        # 52-week window = last 252 bars; the high is the first close in that window
        window_high = closes[-252]
        self.assertAlmostEqual(metrics["hi52"], round(window_high, 2))
        self.assertAlmostEqual(
            metrics["fromHi52"], round((closes[-1] / window_high - 1) * 100, 2)
        )
        self.assertLess(metrics["fromHi52"], 0)

    def test_hi52_uses_high_column(self):
        closes = [100.0] * 60
        highs = [101.0] * 60
        highs[10] = 130.0
        metrics = report_metrics.compute_stock_metrics(
            make_prices(closes, highs=highs), None, "SPY"
        )
        self.assertEqual(metrics["hi52"], 130.0)
        self.assertAlmostEqual(metrics["fromHi52"], round((100 / 130 - 1) * 100, 2))

    def test_vol_ratio_excludes_today(self):
        volumes = [1000.0] * 40 + [3000.0]
        metrics = report_metrics.compute_stock_metrics(
            make_prices([10] * 41, volumes=volumes), None, "SPY"
        )
        # Previous 20 sessions average 1000 → 3.0 (2.73 if today were included)
        self.assertEqual(metrics["volRatio"], 3.0)

    def test_low_volume(self):
        volumes = [2000.0] * 30 + [500.0]
        metrics = report_metrics.compute_stock_metrics(
            make_prices([10] * 31, volumes=volumes), None, "SPY"
        )
        self.assertEqual(metrics["volRatio"], 0.25)

    def test_zero_previous_volume_gives_none(self):
        volumes = [0.0] * 30 + [500.0]
        metrics = report_metrics.compute_stock_metrics(
            make_prices([10] * 31, volumes=volumes), None, "SPY"
        )
        self.assertIsNone(metrics["volRatio"])

    def test_short_history(self):
        metrics = report_metrics.compute_stock_metrics(
            make_prices(geometric(10, 1.01, 30)), bench_series([100] * 30), "SPY"
        )
        self.assertEqual(metrics["bars"], 30)
        self.assertIsNotNone(metrics["r21"])
        self.assertIsNone(metrics["r63"])
        self.assertIsNotNone(metrics["ma"]["20"])
        self.assertIsNone(metrics["ma"]["50"])
        self.assertIsNone(metrics["ma"]["200"])
        self.assertIsNotNone(metrics["ma20Slope5"])
        self.assertIsNone(metrics["trend"])  # MA50 not available
        self.assertEqual(metrics["hiBars"], 30)
        self.assertIsNotNone(metrics["rs21"])
        self.assertIsNone(metrics["rs63"])
        strict_json(metrics)

    def test_single_bar_and_empty(self):
        metrics = report_metrics.compute_stock_metrics(make_prices([10]), None, "SPY")
        self.assertEqual(metrics["bars"], 1)
        self.assertEqual(metrics["hiBars"], 1)
        for key in ("r5", "r21", "r63", "ma20Slope5", "trend", "volRatio", "turnover20"):
            self.assertIsNone(metrics[key], key)
        self.assertEqual(metrics["ma"], {"20": None, "50": None, "200": None})
        strict_json(metrics)

        self.assertIsNone(report_metrics.compute_stock_metrics(pd.DataFrame(), None, "SPY"))
        self.assertIsNone(report_metrics.compute_stock_metrics(None, None, "SPY"))

    def test_nan_and_bad_rows_never_leak(self):
        df = make_prices(geometric(10, 1.01, 80))
        df.iloc[5, df.columns.get_loc("Close")] = float("nan")
        df.iloc[6, df.columns.get_loc("Volume")] = float("nan")
        df.iloc[7, df.columns.get_loc("High")] = float("inf")
        metrics = report_metrics.compute_stock_metrics(df, None, "SPY")
        self.assertEqual(metrics["bars"], 78)  # NaN close row and inf high row dropped
        strict_json(metrics)

    def test_zero_close_is_safe(self):
        closes = [10.0] * 70
        closes[-64] = 0.0
        metrics = report_metrics.compute_stock_metrics(make_prices(closes), None, "SPY")
        strict_json(metrics)
        # A non-positive close is bad data: the row is dropped instead of dividing by zero
        self.assertEqual(metrics["bars"], 69)
        self.assertEqual(metrics["r63"], 0)

    def test_relative_strength(self):
        stock = [100.0] * 100
        stock[-1] = 110.0
        bench = [400.0] * 100
        bench[-1] = 420.0
        metrics = report_metrics.compute_stock_metrics(
            make_prices(stock), bench_series(bench), "SPY"
        )
        self.assertAlmostEqual(metrics["rs21"], round((1.10 / 1.05 - 1) * 100, 2))
        self.assertAlmostEqual(metrics["rs63"], round((1.10 / 1.05 - 1) * 100, 2))

    def test_relative_strength_aligns_by_date(self):
        # Benchmark misses the stock's last session: use the latest benchmark close on/before it
        stock = make_prices([100.0] * 99 + [120.0])
        bench = bench_series([200.0] * 98 + [210.0], end=stock.index[-2])
        metrics = report_metrics.compute_stock_metrics(stock, bench, "^TWII")
        self.assertAlmostEqual(metrics["rs21"], round((1.20 / 1.05 - 1) * 100, 2))

    def test_stale_benchmark_gives_none(self):
        stock = make_prices([100.0] * 100)
        bench = bench_series([200.0] * 100, end="2026-08-01")
        metrics = report_metrics.compute_stock_metrics(stock, bench, "SPY")
        self.assertIsNone(metrics["rs21"])
        self.assertIsNone(metrics["rs63"])


class RsRankTest(unittest.TestCase):
    def test_percentile_ranks(self):
        rows = [{"rs63": float(value)} for value in range(11)] + [{"rs63": None}]
        distribution = report_metrics.assign_rs_rank(rows)

        self.assertEqual(distribution, [float(value) for value in range(11)])
        ranks = [row["rsRank"] for row in rows]
        self.assertIsNone(ranks[-1])
        self.assertEqual(ranks[0], 5)   # (0 + 0.5) / 11
        self.assertEqual(ranks[5], 50)
        self.assertEqual(ranks[10], 95)
        self.assertEqual(ranks[:-1], sorted(ranks[:-1]))
        for rank in ranks[:-1]:
            self.assertTrue(1 <= rank <= 99)

    def test_ties_share_rank(self):
        rows = [{"rs63": 1.0} for _ in range(12)]
        report_metrics.assign_rs_rank(rows)
        self.assertEqual({row["rsRank"] for row in rows}, {50})

    def test_small_population_gets_no_rank(self):
        rows = [{"rs63": 1.0}, {"rs63": 2.0}]
        report_metrics.assign_rs_rank(rows)
        self.assertEqual([row["rsRank"] for row in rows], [None, None])

    def test_rank_from_distribution(self):
        distribution = [float(value) for value in range(20)]
        self.assertEqual(report_metrics.rs_rank_from_distribution(100.0, distribution), 99)
        self.assertEqual(report_metrics.rs_rank_from_distribution(-100.0, distribution), 1)
        self.assertEqual(report_metrics.rs_rank_from_distribution(9.5, distribution), 50)
        self.assertIsNone(report_metrics.rs_rank_from_distribution(None, distribution))
        self.assertIsNone(report_metrics.rs_rank_from_distribution(1.0, [1.0]))


class LoadBenchmarkTest(unittest.TestCase):
    def test_uses_cache_when_long_enough(self):
        cache = make_prices(geometric(100, 1.001, 300))

        def downloader(symbol):
            raise AssertionError("should not download")

        close = report_metrics.load_benchmark("^TWII", cache_df=cache, downloader=downloader)
        self.assertEqual(len(close), 300)
        self.assertAlmostEqual(close.iloc[-1], cache["Close"].iloc[-1])

    def test_downloads_when_cache_missing_or_short(self):
        calls = []

        def downloader(symbol):
            calls.append(symbol)
            frame = make_prices(geometric(400, 1.001, 200))
            frame.columns = pd.MultiIndex.from_product([frame.columns, [symbol]])
            return frame

        close = report_metrics.load_benchmark(
            "SPY", cache_df=make_prices([1] * 10), downloader=downloader
        )
        self.assertEqual(calls, ["SPY"])
        self.assertEqual(len(close), 200)
        self.assertIsInstance(close, pd.Series)

    def test_download_failure_returns_none(self):
        def downloader(symbol):
            raise RuntimeError("offline")

        self.assertIsNone(report_metrics.load_benchmark("SPY", downloader=downloader))

        def empty(symbol):
            return pd.DataFrame()

        self.assertIsNone(report_metrics.load_benchmark("SPY", downloader=empty))


class UniverseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        # 12 TW stocks with increasing momentum, one stale CSV, one listed without CSV
        for i in range(12):
            write_csv(
                self.dir, f"{1101 + i}.TW",
                make_prices(geometric(50, 1 + i * 0.001, 260), volumes=[2e6] * 260),
            )
        write_csv(self.dir, "9999.TW", make_prices([10] * 100, end="2026-08-01"))
        write_csv(self.dir, "AAPL", make_prices(geometric(200, 1.002, 260)))
        write_csv(self.dir, "CUSTOM.TWO", make_prices(geometric(30, 1.02, 120)))
        self.tw_bench = {"symbol": "^TWII", "close": bench_series([100.0] * 300)}
        self.us_bench = {"symbol": "SPY", "close": None}
        self.tw_tickers = [f"{1101 + i}.TW" for i in range(12)] + ["9999.TW", "0000.TW"]

    def tearDown(self):
        self.tmp.cleanup()

    def build(self):
        tw = report_metrics.build_universe(
            self.tw_tickers, "TW", self.dir, self.tw_bench,
            name_fn=lambda symbol: f"N{symbol[:4]}",
        )
        us = report_metrics.build_universe(["AAPL", "MSFT"], "US", self.dir, self.us_bench)
        return {"TW": tw, "US": us}

    def test_build_universe(self):
        universes = self.build()
        tw = universes["TW"]
        self.assertEqual(tw["benchmark"], "^TWII")
        self.assertEqual(tw["asOf"], "2026-10-09")
        symbols = [record["symbol"] for record in tw["records"]]
        self.assertEqual(len(symbols), 12)
        self.assertNotIn("9999.TW", symbols)  # stale
        self.assertNotIn("0000.TW", symbols)  # no CSV
        first = tw["records"][0]
        self.assertEqual(first["name"], "N1101")
        self.assertEqual(first["volume"], 2000000)
        self.assertIsNotNone(first["metrics"]["rsRank"])
        ranks = [record["metrics"]["rsRank"] for record in tw["records"]]
        self.assertEqual(ranks, sorted(ranks))
        self.assertGreater(ranks[-1], ranks[0])

        us = universes["US"]
        self.assertEqual(us["benchmark"], "SPY")
        self.assertEqual([record["symbol"] for record in us["records"]], ["AAPL"])
        self.assertEqual(us["records"][0]["name"], "")
        self.assertIsNone(us["records"][0]["metrics"]["rs63"])

    def test_payload_fields_and_strict_json(self):
        payload = report_metrics.build_universe_payload(
            self.build(), generated_at="2026-10-09T10:00:00+00:00", report_date="2026-10-09"
        )
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["fields"], UNIVERSE_FIELDS)
        self.assertEqual(payload["reportDate"], "2026-10-09")
        self.assertEqual(set(payload["markets"]), {"TW", "US"})
        tw = payload["markets"]["TW"]
        self.assertEqual(tw["benchmark"], "^TWII")
        self.assertEqual(tw["universe"], "台股流動性名單（當日成交≥100萬股）")
        self.assertEqual(payload["markets"]["US"]["universe"], "S&P 500")
        self.assertEqual(payload["markets"]["US"]["benchmark"], "SPY")
        for row in tw["rows"]:
            self.assertEqual(len(row), len(UNIVERSE_FIELDS))
        row = dict(zip(UNIVERSE_FIELDS, tw["rows"][0]))
        self.assertEqual(row["symbol"], "1101.TW")
        self.assertEqual(row["hiBars"], 252)
        self.assertIn(row["trend"], ("up", "down", "mixed"))
        strict_json(payload)

        with tempfile.TemporaryDirectory() as out:
            path = report_metrics.write_universe_json(payload, out)
            self.assertEqual(os.path.basename(path), "universe.json")
            with open(path, encoding="utf-8") as file:
                self.assertEqual(json.load(file), strict_json(payload))

    def test_payload_with_missing_market(self):
        universes = self.build()
        payload = report_metrics.build_universe_payload({"TW": universes["TW"]})
        self.assertEqual(payload["markets"]["US"]["rows"], [])
        self.assertIsNone(payload["markets"]["US"]["asOf"])
        strict_json(payload)

    def test_write_rejects_nan(self):
        payload = {"value": float("nan"), "nested": [float("inf")]}
        with tempfile.TemporaryDirectory() as out:
            path = report_metrics.write_universe_json(payload, out)
            with open(path, encoding="utf-8") as file:
                self.assertEqual(json.load(file), {"value": None, "nested": [None]})

    def test_lookup(self):
        universes = self.build()
        lookup = report_metrics.make_lookup(
            universes, self.dir, {"TW": self.tw_bench, "US": self.us_bench}
        )
        in_universe = lookup("1112.tw")
        self.assertEqual(
            in_universe, universes["TW"]["records"][-1]["metrics"]
        )
        custom = lookup("CUSTOM.TWO")
        self.assertEqual(custom["rsBench"], "^TWII")
        self.assertEqual(custom["bars"], 120)
        self.assertEqual(custom["rsRank"], 99)  # far stronger than the universe
        self.assertEqual(lookup("AAPL")["rsBench"], "SPY")
        self.assertIsNone(lookup("NOPE"))
        self.assertIsNone(lookup(""))
        strict_json(custom)


def import_main():
    if "main" in sys.modules:
        return sys.modules["main"]
    sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
    import main  # noqa: E402

    return main


class MainPlumbingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = import_main()

    def item(self):
        candles = [
            {"time": "2026-10-08", "open": 9, "high": 10, "low": 9, "close": 10},
            {"time": "2026-10-09", "open": 10, "high": 11, "low": 10, "close": 11},
        ]
        return {
            "ticker": "2330.TW",
            "name": "台積電",
            "volume": 1234567,
            "ma_list": [20],
            "chart_data": {
                "candles": candles,
                "moving_averages": [
                    {"window": 20, "data": [{"time": "2026-10-09", "value": 10.5}]}
                ],
                "title_suffix": "(現價:11.00)",
            },
        }

    def test_constants(self):
        self.assertEqual(self.main.MAX_DAYS, 260)
        self.assertIs(self.main.BACKFILL_ENABLED, False)

    def test_group_item_without_lookup_is_unchanged(self):
        result = self.main.build_report_group_item(self.item(), "TW")
        self.assertEqual(
            result,
            {
                "symbol": "2330.TW",
                "name": "台積電",
                "maList": [20],
                "close": 11.0,
                "changePct": 10.0,
                "maValues": {"20": 10.5},
                "note": "現價:11.00",
                "volume": 1234567,
                "asOf": "2026-10-09",
            },
        )

    def test_group_item_with_lookup(self):
        seen = []

        def lookup(symbol):
            seen.append(symbol)
            return {"bars": 5}

        result = self.main.build_report_group_item(self.item(), "TW", metrics_lookup=lookup)
        self.assertEqual(seen, ["2330.TW"])
        self.assertEqual(result["metrics"], {"bars": 5})

        def broken(symbol):
            raise ValueError("boom")

        result = self.main.build_report_group_item(self.item(), "TW", metrics_lookup=broken)
        self.assertIn("metrics", result)
        self.assertIsNone(result["metrics"])

    def test_payload_passes_lookup(self):
        def analyze(*args, **kwargs):
            return None

        args = (
            "2026-10-09",
            {"tw_all": [self.item()]},
            {"tw_all": {"name": "台股", "market": "tw"}},
            ["tw_all"],
            {},
            {},
            {},
            {},
            {},
        )
        plain = self.main.build_report_payload(*args, analyze=analyze)
        self.assertNotIn("metrics", plain["groups"][0]["items"][0])

        payload = self.main.build_report_payload(
            *args, analyze=analyze, metrics_lookup=lambda symbol: {"bars": 1}
        )
        self.assertEqual(payload["groups"][0]["items"][0]["metrics"], {"bars": 1})

    def test_report_universe_end_to_end(self):
        with tempfile.TemporaryDirectory() as data_dir, tempfile.TemporaryDirectory() as out:
            for i in range(12):
                write_csv(data_dir, f"{2301 + i}.TW", make_prices(geometric(50, 1.001 + i / 1000, 260)))
            write_csv(data_dir, "AAPL", make_prices(geometric(100, 1.001, 260)))
            tw_index = make_prices(geometric(20000, 1.0005, 500))
            original_cache = dict(self.main.INDEX_HISTORY_CACHE)
            self.main.INDEX_HISTORY_CACHE["^TWII"] = {"df": tw_index, "source": "Yahoo"}
            try:
                universes, lookup = self.main.build_report_universe(
                    [f"{2301 + i}.TW" for i in range(12)],
                    ["AAPL"],
                    data_dir=data_dir,
                    downloader=lambda symbol: make_prices(geometric(500, 1.0004, 500)),
                )
            finally:
                self.main.INDEX_HISTORY_CACHE.clear()
                self.main.INDEX_HISTORY_CACHE.update(original_cache)

            self.assertEqual(len(universes["TW"]["records"]), 12)
            self.assertIsNotNone(lookup("2301.TW")["rs63"])
            self.assertIsNotNone(lookup("AAPL")["rs63"])
            path = self.main.write_report_universe(universes, "2026-10-09", report_dir=out)
            with open(path, encoding="utf-8") as file:
                document = json.load(file)
            self.assertEqual(document["reportDate"], "2026-10-09")
            self.assertEqual(len(document["markets"]["TW"]["rows"]), 12)
            self.assertEqual(len(document["markets"]["US"]["rows"]), 1)

    def test_backfill_short_csvs(self):
        main = self.main
        with tempfile.TemporaryDirectory() as data_dir:
            write_csv(data_dir, "SHORT", make_prices([10.0] * 50))
            write_csv(data_dir, "SHORTER", make_prices([10.0] * 20))
            write_csv(data_dir, "FULL", make_prices([10.0] * 250))
            requested = []

            def fake_download(tickers, period, max_attempts=3):
                requested.append((list(tickers), period))
                frames = {}
                for ticker in tickers:
                    frames[ticker] = make_prices([9.0] * 300, end="2026-10-08")
                return pd.concat(frames, axis=1).swaplevel(0, 1, axis=1)

            original = main.download_market_data
            main.download_market_data = fake_download
            try:
                updated = main.backfill_short_csvs(
                    ["SHORT", "SHORTER", "FULL", "MISSING"], max_per_run=1, data_dir=data_dir
                )
            finally:
                main.download_market_data = original

            # Shortest first, limited by max_per_run, full and missing CSVs ignored
            self.assertEqual(requested, [(["SHORTER"], "1y")])
            self.assertEqual(updated, ["SHORTER"])
            df = pd.read_csv(os.path.join(data_dir, "SHORTER.csv"), index_col=0, parse_dates=True)
            self.assertEqual(len(df), main.MAX_DAYS)
            self.assertFalse(df.index.duplicated().any())
            # Local rows win over downloaded rows for the same date
            self.assertEqual(df["Close"].iloc[-1], 10.0)
            self.assertEqual(
                len(pd.read_csv(os.path.join(data_dir, "SHORT.csv"))), 50
            )


if __name__ == "__main__":
    unittest.main()

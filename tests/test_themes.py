import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import build_themes  # noqa: E402
import themes_synth  # noqa: E402
import themes_us  # noqa: E402

TODAY = datetime(2026, 10, 9, 22, 30, tzinfo=timezone.utc)


def mini_config(tickers_by_theme, etfs=None):
    etfs = etfs or {}
    return {
        "benchmark": "SPY",
        "parents": [{"key": "p", "name": "P", "order": 1}],
        "themes": [
            {
                "key": key,
                "name": key.upper(),
                "parent": "p",
                "description": "d",
                "etf": etfs.get(key),
                "tickers": [(t, t, "n") for t in tickers],
            }
            for key, tickers in tickers_by_theme.items()
        ],
    }


def geometric(calendar, start, daily):
    out, price = [], start
    for index, day in enumerate(calendar):
        out.append((day, price))
        price *= 1 + (daily(index) if callable(daily) else daily)
    return out


class ConfigTest(unittest.TestCase):
    def test_theme_list_is_well_formed(self):
        themes = themes_us.THEMES
        self.assertGreaterEqual(len(themes), 20)
        self.assertLessEqual(len(themes), 30)
        keys = [t["key"] for t in themes]
        self.assertEqual(len(keys), len(set(keys)))
        parents = {p["key"] for p in themes_us.PARENTS}
        self.assertEqual(len(parents), len(themes_us.PARENTS))
        used = set()
        for theme in themes:
            self.assertRegex(theme["key"], r"^[a-z0-9-]{1,40}$")
            self.assertIn(theme["parent"], parents)
            used.add(theme["parent"])
            self.assertTrue(theme["name"].strip())
            self.assertGreater(len(theme["description"].strip()), 30, theme["key"])
            self.assertGreaterEqual(len(theme["tickers"]), 5, theme["key"])
            self.assertLessEqual(len(theme["tickers"]), 12, theme["key"])
            symbols = [t[0] for t in theme["tickers"]]
            self.assertEqual(len(symbols), len(set(symbols)), theme["key"])
            for entry in theme["tickers"]:
                self.assertEqual(len(entry), 3)
                self.assertRegex(entry[0], r"^[A-Z]{1,5}$")
                self.assertTrue(entry[1].strip() and entry[2].strip(), entry)
            if theme["etf"] is not None:
                self.assertRegex(theme["etf"], r"^[A-Z]{2,5}$")
        self.assertEqual(used, parents)

    def test_same_ticker_keeps_one_name_everywhere_it_repeats(self):
        names = {}
        for theme in themes_us.THEMES:
            for symbol, name, _note in theme["tickers"]:
                names.setdefault(symbol, set()).add(name)
        # Repeated tickers may be named slightly differently, but never by more than two spellings.
        for symbol, spellings in names.items():
            self.assertLessEqual(len(spellings), 2, symbol)


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.calendar = themes_synth.business_days("2026-10-09", 260)

    def prices(self, **series):
        out = {"SPY": geometric(self.calendar, 400.0, 0.0)}
        out.update(series)
        return out

    def test_returns_relative_strength_ma_and_high(self):
        config = mini_config({"t": ["AAA", "BBB", "CCC"]})
        prices = self.prices(
            AAA=geometric(self.calendar, 100.0, 0.01),
            BBB=geometric(self.calendar, 100.0, 0.0),
            CCC=geometric(self.calendar, 100.0, -0.005),
        )
        payload = build_themes.build_payload(prices, config, TODAY)
        theme = payload["themes"][0]
        by_symbol = {c["symbol"]: c for c in theme["constituents"]}
        aaa = by_symbol["AAA"]
        self.assertAlmostEqual(aaa["returns"]["d1"], 1.0, places=2)
        self.assertAlmostEqual(aaa["returns"]["w1"], (1.01**5 - 1) * 100, places=2)
        self.assertAlmostEqual(aaa["returns"]["m3"], (1.01**63 - 1) * 100, places=2)
        self.assertAlmostEqual(aaa["returns"]["m6"], (1.01**126 - 1) * 100, places=2)
        self.assertAlmostEqual(aaa["rs3m"], aaa["returns"]["m3"], places=2)  # SPY is flat
        self.assertTrue(aaa["aboveMa50"])
        self.assertEqual(aaa["fromHi52"], 0.0)
        self.assertFalse(by_symbol["CCC"]["aboveMa50"])
        self.assertLess(by_symbol["CCC"]["fromHi52"], 0)
        self.assertEqual(by_symbol["BBB"]["returns"]["m1"], 0.0)
        # YTD uses the last close of the previous year as base.
        index = max(i for i, day in enumerate(self.calendar) if day < "2026-01-01")
        expected = (prices["AAA"][-1][1] / prices["AAA"][index][1] - 1) * 100
        self.assertAlmostEqual(aaa["returns"]["ytd"], expected, places=2)
        self.assertEqual(payload["asOf"], "2026-10-09")
        self.assertEqual(len(payload["dates"]), 126)
        self.assertEqual(payload["generatedAt"], "2026-10-09T22:30:00Z")
        self.assertEqual(payload["benchmark"]["returns"]["m3"], 0.0)

    def test_theme_stats_and_rank(self):
        config = mini_config({"hot": ["A1", "A2", "A3"], "cold": ["B1", "B2", "B3"]})
        prices = self.prices(
            A1=geometric(self.calendar, 50, 0.004),
            A2=geometric(self.calendar, 50, 0.004),
            A3=geometric(self.calendar, 50, 0.004),
            B1=geometric(self.calendar, 50, -0.004),
            B2=geometric(self.calendar, 50, -0.004),
            B3=geometric(self.calendar, 50, 0.001),
        )
        payload = build_themes.build_payload(prices, config, TODAY)
        self.assertEqual([t["key"] for t in payload["themes"]], ["hot", "cold"])
        hot, cold = payload["themes"]
        self.assertEqual((hot["rank"], cold["rank"]), (1, 2))
        self.assertEqual(hot["breadth50"], 100.0)
        self.assertAlmostEqual(cold["breadth50"], 33.33, places=2)
        w = build_themes.MOMENTUM_WEIGHTS
        expected = (
            w["w1"] * hot["returns"]["w1"]
            + w["m1"] * hot["returns"]["m1"]
            + w["m3"] * hot["returns"]["m3"]
            + w["rs3m"] * hot["rs"]["m3"]
        )
        self.assertAlmostEqual(hot["momentum"], expected, delta=0.05)
        self.assertEqual(hot["count"], 3)
        self.assertEqual(hot["validCount"], 3)
        # Median ignores the one rising name; the mean is pulled up by it.
        self.assertLess(cold["median"]["m3"], cold["returns"]["m3"])
        self.assertAlmostEqual(cold["median"]["d1"], -0.4, places=2)

    def test_equal_weight_index_is_rebalanced_daily(self):
        config = mini_config({"t": ["UP", "FLAT", "FLAT2"]})
        prices = self.prices(
            UP=geometric(self.calendar, 100, 0.03),
            FLAT=geometric(self.calendar, 100, 0.0),
            FLAT2=geometric(self.calendar, 100, 0.0),
        )
        series = build_themes.build_payload(prices, config, TODAY)["themes"][0]["series"]
        self.assertEqual(len(series), 126)
        self.assertEqual(series[0], 100.0)
        self.assertAlmostEqual(series[-1], 100 * 1.01**125, places=1)  # (3% + 0 + 0) / 3 every day
        bench = build_themes.build_payload(prices, config, TODAY)["benchmark"]["series"]
        self.assertEqual(bench, [100.0] * 126)

    def test_constituents_carry_turnover20_only_when_known(self):
        config = mini_config({"t": ["AAA", "BBB", "CCC"]})
        prices = self.prices(
            AAA=geometric(self.calendar, 100.0, 0.01),
            BBB=geometric(self.calendar, 100.0, 0.0),
            CCC=geometric(self.calendar, 100.0, -0.005),
        )
        turnover = {"AAA": [(day, 1000.0) for day in self.calendar], "BBB": [(day, 5.0) for day in self.calendar[-3:]]}
        theme = build_themes.build_payload(prices, config, TODAY, turnover)["themes"][0]
        by_symbol = {c["symbol"]: c for c in theme["constituents"]}
        self.assertEqual(by_symbol["AAA"]["turnover20"], 1000)
        self.assertNotIn("turnover20", by_symbol["BBB"])  # fewer than TURNOVER_MIN_DAYS sessions
        self.assertNotIn("turnover20", by_symbol["CCC"])
        plain = build_themes.build_payload(prices, config, TODAY)["themes"][0]["constituents"]
        self.assertTrue(all("turnover20" not in c for c in plain))

    def test_stale_constituents_are_excluded_after_three_days(self):
        config = mini_config({"t": ["OK", "OK2", "OK3", "EDGE", "OLD", "GONE"]})
        prices = self.prices(
            OK=geometric(self.calendar, 100, 0.001),
            OK2=geometric(self.calendar, 100, 0.001),
            OK3=geometric(self.calendar, 100, 0.001),
            EDGE=geometric(self.calendar, 100, 0.001)[:-1],  # ends Thu: 1 day behind
            OLD=geometric(self.calendar, 100, 0.001)[:-3],  # ends Tue: 3 calendar days -> kept
            GONE=geometric(self.calendar, 100, 0.001)[:-4],  # ends Mon: 4 calendar days -> stale
        )
        payload = build_themes.build_payload(prices, config, TODAY)
        theme = payload["themes"][0]
        self.assertEqual(sorted(c["symbol"] for c in theme["constituents"]), ["EDGE", "OK", "OK2", "OK3", "OLD"])
        self.assertEqual(payload["skipped"]["stale"], ["GONE"])
        self.assertEqual(theme["validCount"], 5)
        self.assertEqual(theme["count"], 6)

    def test_benchmark_one_session_ahead_of_all_stocks(self):
        """Yahoo served ^TWII a session ahead of every stock: the data date is the stocks', and 1-day moves are real."""
        config = mini_config({"t": ["A", "B", "C"]})
        stock = geometric(self.calendar, 100, 0.01)
        ahead = self.prices(A=stock[:-1], B=stock[:-1], C=stock[:-1])
        payload = build_themes.build_payload(ahead, config, TODAY)
        self.assertEqual(payload["asOf"], self.calendar[-2])
        self.assertAlmostEqual(payload["themes"][0]["returns"]["d1"], 1.0, places=1)  # not 0.0 from a forward-fill
        self.assertEqual(payload["skipped"], {"missing": [], "stale": []})
        self.assertEqual(payload["dates"][-1], self.calendar[-2])

    def test_coverage_threshold(self):
        symbols = [f"T{i}" for i in range(10)]
        config = mini_config({"t": symbols})
        series = geometric(self.calendar, 100, 0.001)
        ok = self.prices(**{s: series for s in symbols[:6]})
        self.assertEqual(build_themes.build_payload(ok, config, TODAY)["coverage"], 0.6)
        few = self.prices(**{s: series for s in symbols[:5]})
        with self.assertRaises(build_themes.InsufficientData):
            build_themes.build_payload(few, config, TODAY)
        with self.assertRaises(build_themes.InsufficientData):
            build_themes.build_payload({"SPY": series[:50]}, config, TODAY)

    def test_mostly_stale_data_does_not_pass_coverage(self):
        symbols = [f"T{i}" for i in range(10)]
        config = mini_config({"t": symbols})
        fresh = geometric(self.calendar, 100, 0.001)
        old = fresh[:-6]
        prices = self.prices(**{s: (fresh if i < 5 else old) for i, s in enumerate(symbols)})
        with self.assertRaises(build_themes.InsufficientData):
            build_themes.build_payload(prices, config, TODAY)

    def test_open_session_bar_is_dropped_until_after_the_close(self):
        bars = [("2026-10-08", 1.0), ("2026-10-09", 2.0)]
        during = datetime(2026, 10, 9, 16, 47, tzinfo=timezone.utc)  # 12:47 New York
        after = datetime(2026, 10, 9, 22, 30, tzinfo=timezone.utc)  # 18:30 New York
        self.assertEqual(build_themes.drop_open_session({"A": bars}, during), {"A": bars[:1]})
        self.assertEqual(build_themes.drop_open_session({"A": bars}, after), {"A": bars})
        weekend = datetime(2026, 10, 10, 16, 47, tzinfo=timezone.utc)
        self.assertEqual(build_themes.drop_open_session({"A": bars}, weekend), {"A": bars})

    def test_theme_with_too_few_valid_names_is_unranked_and_last(self):
        config = mini_config({"thin": ["X1", "X2", "X3"], "full": ["Y1", "Y2", "Y3"]})
        prices = self.prices(
            X1=geometric(self.calendar, 100, 0.01),
            X2=geometric(self.calendar, 100, 0.01),
            Y1=geometric(self.calendar, 100, -0.001),
            Y2=geometric(self.calendar, 100, -0.001),
            Y3=geometric(self.calendar, 100, -0.001),
        )
        payload = build_themes.build_payload(prices, config, TODAY)
        self.assertEqual([t["key"] for t in payload["themes"]], ["full", "thin"])
        thin = payload["themes"][1]
        self.assertEqual(thin["validCount"], 2)
        self.assertIsNone(thin["rank"])
        self.assertIsNone(thin["momentum"])
        self.assertEqual(payload["skipped"]["missing"], ["X3"])

    def test_new_listing_has_partial_history(self):
        config = mini_config({"t": ["OLD1", "OLD2", "NEW"]})
        prices = self.prices(
            OLD1=geometric(self.calendar, 100, 0.001),
            OLD2=geometric(self.calendar, 100, 0.001),
            NEW=geometric(self.calendar[-30:], 20, 0.01),
        )
        theme = build_themes.build_payload(prices, config, TODAY)["themes"][0]
        new = next(c for c in theme["constituents"] if c["symbol"] == "NEW")
        self.assertIsNotNone(new["returns"]["m1"])
        self.assertIsNone(new["returns"]["m3"])
        self.assertIsNone(new["returns"]["ytd"])
        self.assertIsNone(new["aboveMa50"])
        self.assertIsNone(new["fromHi52"])
        self.assertIsNotNone(theme["returns"]["m3"])  # mean over the two names that have it
        self.assertEqual(len(theme["series"]), 126)

    def test_etf_reference(self):
        config = mini_config({"t": ["A", "B", "C"]}, etfs={"t": "ETFX"})
        series = geometric(self.calendar, 100, 0.002)
        prices = self.prices(A=series, B=series, C=series, ETFX=series)
        theme = build_themes.build_payload(prices, config, TODAY)["themes"][0]
        self.assertEqual(theme["etf"]["symbol"], "ETFX")
        self.assertAlmostEqual(theme["etf"]["returns"]["w1"], (1.002**5 - 1) * 100, places=2)
        no_etf = self.prices(A=series, B=series, C=series)
        self.assertIsNone(build_themes.build_payload(no_etf, config, TODAY)["themes"][0]["etf"])

    def test_synthetic_run_over_the_real_config(self):
        config = build_themes.default_config()
        prices = themes_synth.synthetic_prices(config, drop={"CRCL", "SNDK", "USAR"}, stale={"AAOI"})
        payload = build_themes.build_payload(prices, config, TODAY)
        self.assertEqual(len(payload["themes"]), len(themes_us.THEMES))
        self.assertEqual(payload["skipped"], {"missing": ["CRCL", "SNDK", "USAR"], "stale": ["AAOI"]})
        ranks = [t["rank"] for t in payload["themes"] if t["rank"] is not None]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
        json.dumps(payload, allow_nan=False)


class DownloadTest(unittest.TestCase):
    def frame(self, symbols):
        import pandas as pd

        index = pd.to_datetime(["2026-10-07", "2026-10-08", "2026-10-09"])
        columns = pd.MultiIndex.from_product([symbols, ["Open", "Close", "Volume"]])
        frame = pd.DataFrame(index=index, columns=columns, dtype=float)
        for n, symbol in enumerate(symbols):
            frame[(symbol, "Open")] = 1.0
            frame[(symbol, "Close")] = [10.0 + n, 11.0 + n, float("nan") if symbol == "NAN" else 12.0 + n]
            frame[(symbol, "Volume")] = 5.0
        return frame

    def test_extract_bars_handles_multi_and_single_layouts(self):
        if importlib.util.find_spec("pandas") is None:
            self.skipTest("pandas not installed")
        frame = self.frame(["AAA", "NAN"])
        self.assertEqual(
            build_themes.extract_bars(frame, "AAA", single=False),
            [("2026-10-07", 10.0), ("2026-10-08", 11.0), ("2026-10-09", 12.0)],
        )
        self.assertEqual(len(build_themes.extract_bars(frame, "NAN", single=False)), 2)
        self.assertEqual(build_themes.extract_bars(frame, "ZZZ", single=False), [])
        flat = frame["AAA"]
        self.assertEqual(len(build_themes.extract_bars(flat, "AAA", single=True)), 3)
        self.assertEqual(build_themes.extract_bars(flat, "AAA", single=False), [])
        self.assertEqual(build_themes.extract_bars(None, "AAA", single=True), [])

    def test_extract_turnover_is_close_times_volume_and_skips_bad_rows(self):
        if importlib.util.find_spec("pandas") is None:
            self.skipTest("pandas not installed")
        frame = self.frame(["AAA", "NAN"])
        self.assertEqual(
            build_themes.extract_turnover(frame, "AAA", single=False),
            [("2026-10-07", 50.0), ("2026-10-08", 55.0), ("2026-10-09", 60.0)],
        )
        self.assertEqual(len(build_themes.extract_turnover(frame, "NAN", single=False)), 2)
        self.assertEqual(build_themes.extract_turnover(frame, "ZZZ", single=False), [])
        self.assertEqual(build_themes.extract_turnover(None, "AAA", single=True), [])
        frame[("AAA", "Volume")] = [0.0, 5.0, float("nan")]
        self.assertEqual(build_themes.extract_turnover(frame, "AAA", single=False), [("2026-10-08", 55.0)])

    def test_turnover_average_uses_latest_window_up_to_the_date(self):
        rows = [(f"2026-09-{day:02d}", float(day)) for day in range(1, 29)]
        window = build_themes.TURNOVER_WINDOW
        self.assertEqual(build_themes.turnover_average(rows, "2026-09-28"), sum(range(29 - window, 29)) / window)
        # nothing after last_date leaks in
        self.assertEqual(build_themes.turnover_average(rows, "2026-09-20"), sum(range(1, 21)) / 20)
        self.assertIsNone(build_themes.turnover_average(rows[: build_themes.TURNOVER_MIN_DAYS - 1], "2026-09-28"))
        self.assertIsNone(build_themes.turnover_average([], "2026-09-28"))

    def test_download_prices_collects_turnover_when_asked(self):
        if importlib.util.find_spec("pandas") is None:
            self.skipTest("pandas not installed")
        turnover = {}
        prices = build_themes.download_prices(
            ["AAA", "BBB"], downloader=lambda batch: self.frame(list(batch)), sleep=lambda _s: None, turnover=turnover
        )
        self.assertEqual(sorted(prices), ["AAA", "BBB"])
        self.assertEqual(sorted(turnover), ["AAA", "BBB"])
        self.assertEqual(len(turnover["AAA"]), 3)

    def test_download_prices_batches_retries_and_collects(self):
        if importlib.util.find_spec("pandas") is None:
            self.skipTest("pandas not installed")
        calls = []
        failed_once = []

        def downloader(batch):
            calls.append(list(batch))
            if len(calls) == 1 and not failed_once:
                failed_once.append(True)
                raise RuntimeError("temporary")
            return self.frame([s for s in batch if s != "MISSING"])

        symbols = [f"S{i}" for i in range(45)] + ["MISSING"]
        prices = build_themes.download_prices(symbols, downloader=downloader, sleep=lambda _s: None)
        self.assertNotIn("MISSING", prices)
        self.assertEqual(len(prices), 45)
        self.assertEqual(len(calls[0]), build_themes.BATCH_SIZE)
        # first batch retried after the error, the missing symbol retried once in a final round
        self.assertEqual(calls[1], calls[0])
        self.assertEqual(calls[-1], ["MISSING"])


class MainTest(unittest.TestCase):
    def run_main(self, prices):
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, "docs", "report", "themes.json")
            os.makedirs(os.path.dirname(out))
            with open(out, "w", encoding="utf-8") as file:
                file.write('{"old":true}')
            with mock.patch.dict(os.environ, {"THEMES_OUT_PATH": out}), mock.patch.object(
                build_themes, "download_prices", return_value=prices
            ):
                code = build_themes.main()
            with open(out, encoding="utf-8") as file:
                return code, json.load(file), os.path.exists(out + ".tmp")

    def test_writes_new_file_on_success(self):
        config = build_themes.default_config()
        prices = themes_synth.synthetic_prices(config)
        code, written, tmp_left = self.run_main(prices)
        self.assertEqual(code, 0)
        self.assertEqual(written["version"], 1)
        self.assertFalse(tmp_left)

    def test_keeps_old_file_below_coverage_threshold(self):
        config = build_themes.default_config()
        prices = themes_synth.synthetic_prices(config)
        keep = {"SPY": prices["SPY"]}
        keep.update({s: prices[s] for s in list(prices)[: len(prices) // 4] if s != "SPY"})
        code, written, _ = self.run_main(keep)
        self.assertEqual(code, 1)
        self.assertEqual(written, {"old": True})


def fake_themes_tw(config=None):
    """A stand-in `themes_tw` module (scripts/themes_tw.py is delivered separately)."""
    config = config or themes_synth.tw_config()
    module = types.ModuleType("themes_tw")
    module.PARENTS = config["parents"]
    module.THEMES = config["themes"]
    module.BENCHMARK = config["benchmark"]
    return module


def tw_prices(**kwargs):
    config = themes_synth.tw_config()
    options = dict(seed=7, drift=themes_synth.TW_THEME_DRIFT, speculative=themes_synth.TW_SPECULATIVE, bench_level=22000.0)
    options.update(kwargs)
    return config, themes_synth.synthetic_prices(config, **options)


class UsRegressionTest(unittest.TestCase):
    """The US file must stay exactly what it was before the Taiwan market was added."""

    def test_us_sample_payload_is_byte_identical_to_the_committed_fixture(self):
        body = json.dumps(themes_synth.us_fixture_payload(), ensure_ascii=False, separators=(",", ":")) + "\n"
        path = os.path.join(ROOT, "atlas", "src", "report", "fixtures", "themes.sample.json")
        with open(path, encoding="utf-8") as file:
            self.assertEqual(body, file.read())

    def test_us_config_and_payload_carry_no_market_keys(self):
        config = build_themes.load_config("us")
        self.assertEqual(config, build_themes.default_config())
        self.assertEqual(set(config), {"parents", "themes", "benchmark"})
        payload = themes_synth.us_fixture_payload()
        self.assertNotIn("market", payload)
        self.assertEqual(set(payload["benchmark"]), {"symbol", "returns", "series"})
        self.assertEqual(payload["benchmark"]["symbol"], "SPY")

    def test_us_session_cutoff_is_unchanged_by_default(self):
        bars = [("2026-10-08", 1.0), ("2026-10-09", 2.0)]
        during = datetime(2026, 10, 9, 16, 47, tzinfo=timezone.utc)
        self.assertEqual(build_themes.drop_open_session({"A": bars}, during), {"A": bars[:1]})
        self.assertEqual(build_themes.drop_open_session({"A": bars}, during, "us"), {"A": bars[:1]})

    def test_us_main_still_writes_themes_json_without_arguments(self):
        prices = themes_synth.synthetic_prices(build_themes.default_config())
        with tempfile.TemporaryDirectory() as folder:
            default_out = os.path.join(folder, "docs", "report", "themes.json")
            with mock.patch.dict(os.environ), mock.patch.object(build_themes, "DEFAULT_OUT", default_out), mock.patch.object(
                build_themes, "download_prices", return_value=prices
            ):
                os.environ.pop("THEMES_OUT_PATH", None)
                os.environ.pop("THEMES_MARKET", None)
                self.assertEqual(build_themes.main(), 0)
            self.assertEqual(sorted(os.listdir(os.path.dirname(default_out))), ["themes.json"])
            with open(default_out, encoding="utf-8") as file:
                self.assertEqual(json.load(file)["benchmark"]["symbol"], "SPY")


class TaiwanPayloadTest(unittest.TestCase):
    def test_payload_uses_the_taiwan_benchmark_and_keeps_the_structure(self):
        config, prices = tw_prices(drop={"2605.TW", "2606.TW", "2609.TW"}, stale={"8150.TW"})
        payload = build_themes.build_payload(prices, config, TODAY)
        self.assertEqual(payload["market"], "tw")
        self.assertEqual(payload["benchmark"]["symbol"], "^TWII")
        self.assertEqual(payload["benchmark"]["name"], "加權指數")
        self.assertEqual(payload["asOf"], "2026-10-09")
        self.assertEqual(len(payload["dates"]), 126)
        self.assertEqual(len(payload["benchmark"]["series"]), 126)
        self.assertEqual(payload["skipped"], {"missing": ["2605.TW", "2606.TW", "2609.TW"], "stale": ["8150.TW"]})
        self.assertEqual(len(payload["themes"]), len(config["themes"]))
        symbols = {c["symbol"] for t in payload["themes"] for c in t["constituents"]}
        self.assertTrue(all(s.endswith((".TW", ".TWO")) for s in symbols))
        self.assertIn("5347.TWO", symbols)
        ranks = [t["rank"] for t in payload["themes"] if t["rank"] is not None]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
        momentum = [t["momentum"] for t in payload["themes"] if t["rank"] is not None]
        self.assertEqual(momentum, sorted(momentum, reverse=True))
        thin = payload["themes"][-1]
        self.assertEqual((thin["key"], thin["validCount"], thin["rank"], thin["momentum"]), ("shipping", 2, None, None))
        finance = next(t for t in payload["themes"] if t["key"] == "finance")
        self.assertEqual(finance["etf"]["symbol"], "0055.TW")
        json.dumps(payload, allow_nan=False)
        self.assertEqual(set(payload) - {"market"}, set(themes_synth.us_fixture_payload()))

    def test_relative_strength_is_measured_against_the_taiwan_benchmark(self):
        calendar = themes_synth.business_days("2026-10-09", 260)
        config = mini_config({"t": ["1101.TW", "1102.TW", "1216.TW"]})
        config["benchmark"] = "^TWII"
        index = geometric(calendar, 22000.0, 0.001)
        flat = geometric(calendar, 50.0, 0.0)
        payload = build_themes.build_payload({"^TWII": index, "1101.TW": flat, "1102.TW": flat, "1216.TW": flat}, config, TODAY)
        theme = payload["themes"][0]
        self.assertAlmostEqual(payload["benchmark"]["returns"]["m3"], (1.001**63 - 1) * 100, places=2)
        expected = ((1 / 1.001**63) - 1) * 100
        self.assertAlmostEqual(theme["rs"]["m3"], expected, places=2)
        self.assertNotIn("market", payload)  # only configs that name a market add the key

    def test_bars_follow_the_benchmark_calendar(self):
        # ^TWII has no bar on the 2026-10-06 holiday; a stray bar on that day in a stock is ignored,
        # and the stock is forward-filled on days it did not trade (a halt).
        calendar = [d for d in themes_synth.business_days("2026-10-09", 260) if d != "2026-10-06"]
        config = mini_config({"t": ["2330.TW", "2317.TW", "2454.TW"]})
        config["benchmark"] = "^TWII"
        index = geometric(calendar, 22000.0, 0.0)
        stock = geometric(calendar, 100.0, 0.001)
        halted = [bar for bar in stock if bar[0] != "2026-10-08"]
        stray = sorted(stock + [("2026-10-06", 999.0)])
        prices = {"^TWII": index, "2330.TW": stock, "2317.TW": halted, "2454.TW": stray}
        payload = build_themes.build_payload(prices, config, TODAY)
        self.assertNotIn("2026-10-06", payload["dates"])
        by_symbol = {c["symbol"]: c for c in payload["themes"][0]["constituents"]}
        self.assertAlmostEqual(by_symbol["2330.TW"]["returns"]["d1"], 0.1, places=2)
        # No bar on 10-08: forward-filled, so the last day's move is measured from the 10-07 close.
        self.assertAlmostEqual(by_symbol["2317.TW"]["returns"]["d1"], (1.001**2 - 1) * 100, places=2)
        self.assertAlmostEqual(by_symbol["2454.TW"]["returns"]["d1"], 0.1, places=2)
        self.assertLess(by_symbol["2454.TW"]["close"], 200)

    def test_coverage_threshold_applies_to_taiwan_too(self):
        config, prices = tw_prices()
        symbols = sorted({t[0] for theme in config["themes"] for t in theme["tickers"]})
        keep = {"^TWII": prices["^TWII"]}
        keep.update({s: prices[s] for s in symbols[: int(len(symbols) * 0.5)]})
        with self.assertRaises(build_themes.InsufficientData):
            build_themes.build_payload(keep, config, TODAY)

    def test_taiwan_session_cutoff(self):
        bars = [("2026-10-08", 1.0), ("2026-10-09", 2.0)]
        during = datetime(2026, 10, 9, 4, 0, tzinfo=timezone.utc)  # 12:00 Taipei
        after = datetime(2026, 10, 9, 6, 30, tzinfo=timezone.utc)  # 14:30 Taipei
        weekend = datetime(2026, 10, 10, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(build_themes.drop_open_session({"A": bars}, during, "tw"), {"A": bars[:1]})
        self.assertEqual(build_themes.drop_open_session({"A": bars}, after, "tw"), {"A": bars})
        self.assertEqual(build_themes.drop_open_session({"A": bars}, weekend, "tw"), {"A": bars})
        # The same instant is still the middle of the US previous session's evening: nothing to drop there.
        self.assertEqual(build_themes.drop_open_session({"A": bars}, after), {"A": bars[:1]})

    def test_all_symbols_include_the_benchmark_and_etfs(self):
        symbols = build_themes.all_symbols(themes_synth.tw_config())
        self.assertIn("^TWII", symbols)
        self.assertIn("0055.TW", symbols)
        self.assertIn("5347.TWO", symbols)


class MarketSelectionTest(unittest.TestCase):
    def run_main(self, argv, prices, env=None, module=None):
        with tempfile.TemporaryDirectory() as folder:
            report = os.path.join(folder, "docs", "report")
            os.makedirs(report)
            for name in ("themes.json", "themes-tw.json"):
                with open(os.path.join(report, name), "w", encoding="utf-8") as file:
                    file.write('{"old":"%s"}' % name)
            with mock.patch.dict(os.environ), mock.patch.object(
                build_themes, "DEFAULT_OUT", os.path.join(report, "themes.json")
            ), mock.patch.object(build_themes, "download_prices", return_value=prices) as download, mock.patch.dict(
                sys.modules, {"themes_tw": None if module == "missing" else module} if module is not None else {}
            ):
                os.environ.pop("THEMES_OUT_PATH", None)
                os.environ.pop("THEMES_MARKET", None)
                os.environ.update(env or {})
                code = build_themes.main(argv)
            written = {}
            for name in os.listdir(report):
                with open(os.path.join(report, name), encoding="utf-8") as file:
                    written[name] = json.load(file)
            return code, written, download

    def test_market_tw_writes_themes_tw_json_and_leaves_the_us_file_alone(self):
        _config, prices = tw_prices()
        code, written, download = self.run_main(["--market", "tw"], prices, module=fake_themes_tw())
        self.assertEqual(code, 0)
        self.assertEqual(written["themes.json"], {"old": "themes.json"})
        self.assertEqual(written["themes-tw.json"]["benchmark"]["symbol"], "^TWII")
        self.assertEqual(written["themes-tw.json"]["benchmark"]["name"], "加權指數")
        self.assertIn("^TWII", download.call_args[0][0])

    def test_environment_variable_selects_the_market_and_the_flag_wins(self):
        _config, prices = tw_prices()
        code, written, _ = self.run_main([], prices, env={"THEMES_MARKET": "tw"}, module=fake_themes_tw())
        self.assertEqual(code, 0)
        self.assertEqual(written["themes-tw.json"]["market"], "tw")
        us_prices = themes_synth.synthetic_prices(build_themes.default_config())
        code, written, _ = self.run_main(["--market", "us"], us_prices, env={"THEMES_MARKET": "tw"})
        self.assertEqual(code, 0)
        self.assertEqual(written["themes-tw.json"], {"old": "themes-tw.json"})
        self.assertEqual(written["themes.json"]["benchmark"]["symbol"], "SPY")

    def test_taiwan_failure_keeps_the_old_file(self):
        _config, prices = tw_prices()
        thin = {"^TWII": prices["^TWII"]}
        code, written, _ = self.run_main(["--market", "tw"], thin, module=fake_themes_tw())
        self.assertEqual(code, 1)
        self.assertEqual(written["themes-tw.json"], {"old": "themes-tw.json"})
        self.assertEqual(written["themes.json"], {"old": "themes.json"})

    def test_missing_themes_tw_gives_a_clear_error_and_downloads_nothing(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code, written, download = self.run_main(["--market", "tw"], {}, module="missing")
        self.assertEqual(code, 1)
        self.assertIn("themes_tw.py not found", stdout.getvalue())
        download.assert_not_called()
        self.assertEqual(written["themes-tw.json"], {"old": "themes-tw.json"})

    def test_load_config_reports_a_missing_or_incomplete_module(self):
        with mock.patch.dict(sys.modules, {"themes_tw": None}):
            with self.assertRaisesRegex(build_themes.ConfigError, "themes_tw.py not found"):
                build_themes.load_config("tw")
        incomplete = types.ModuleType("themes_tw")
        incomplete.THEMES = []
        with mock.patch.dict(sys.modules, {"themes_tw": incomplete}):
            with self.assertRaisesRegex(build_themes.ConfigError, "PARENTS, THEMES and BENCHMARK"):
                build_themes.load_config("tw")
        with self.assertRaises(build_themes.ConfigError):
            build_themes.load_config("jp")

    def test_load_config_reads_the_module_and_names_the_default_benchmark(self):
        with mock.patch.dict(sys.modules, {"themes_tw": fake_themes_tw()}):
            config = build_themes.load_config("tw")
        self.assertEqual(config["benchmark"], "^TWII")
        self.assertEqual(config["benchmark_name"], "加權指數")
        self.assertEqual(config["market"], "tw")
        named = fake_themes_tw()
        named.BENCHMARK_NAME = "自訂名稱"
        with mock.patch.dict(sys.modules, {"themes_tw": named}):
            self.assertEqual(build_themes.load_config("tw")["benchmark_name"], "自訂名稱")

    def test_load_config_accepts_a_benchmark_pair_or_mapping(self):
        pair = fake_themes_tw()
        pair.BENCHMARK = ("^TWII", "台灣加權")
        with mock.patch.dict(sys.modules, {"themes_tw": pair}):
            config = build_themes.load_config("tw")
        self.assertEqual((config["benchmark"], config["benchmark_name"]), ("^TWII", "台灣加權"))
        mapping = fake_themes_tw()
        mapping.BENCHMARK = {"symbol": "^TWII", "name": "加權"}
        with mock.patch.dict(sys.modules, {"themes_tw": mapping}):
            self.assertEqual(build_themes.load_config("tw")["benchmark_name"], "加權")
        broken = fake_themes_tw()
        broken.BENCHMARK = None
        with mock.patch.dict(sys.modules, {"themes_tw": broken}):
            with self.assertRaisesRegex(build_themes.ConfigError, "BENCHMARK must be"):
                build_themes.load_config("tw")

    def test_unknown_market_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            build_themes.main(["--market", "jp"])
        self.assertEqual(raised.exception.code, 2)
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"THEMES_MARKET": "jp"}), contextlib.redirect_stdout(stdout):
            self.assertEqual(build_themes.main([]), 1)
        self.assertIn("unknown market", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

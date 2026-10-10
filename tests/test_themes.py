import importlib.util
import json
import os
import sys
import tempfile
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

    def test_stale_constituents_are_excluded_after_three_days(self):
        config = mini_config({"t": ["OK", "EDGE", "OLD", "GONE"]})
        prices = self.prices(
            OK=geometric(self.calendar, 100, 0.001),
            EDGE=geometric(self.calendar, 100, 0.001)[:-1],  # ends Thu: 1 day behind
            OLD=geometric(self.calendar, 100, 0.001)[:-3],  # ends Tue: 3 calendar days -> kept
            GONE=geometric(self.calendar, 100, 0.001)[:-4],  # ends Mon: 4 calendar days -> stale
        )
        payload = build_themes.build_payload(prices, config, TODAY)
        theme = payload["themes"][0]
        self.assertEqual(sorted(c["symbol"] for c in theme["constituents"]), ["EDGE", "OK", "OLD"])
        self.assertEqual(payload["skipped"]["stale"], ["GONE"])
        self.assertEqual(theme["validCount"], 3)
        self.assertEqual(theme["count"], 4)

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


if __name__ == "__main__":
    unittest.main()

import json
import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import build_themes  # noqa: E402
import themes_synth  # noqa: E402
import themes_tw  # noqa: E402

UNIVERSE_PATH = os.path.join(ROOT, "docs", "report", "universe.json")
TODAY = datetime(2026, 10, 9, 22, 30, tzinfo=timezone.utc)
SYMBOL_RE = r"^\d{4}\.TWO?$"
ETF_RE = r"^\d{4,6}[A-Z]?\.TW$"


def clean_name(name):
    # TWSE/TPEx short names carry a "*" marker that is not part of the company name.
    return name.replace("*", "").strip()


def tw_universe():
    """{symbol: name} from the TW liquidity list (row[0] symbol, row[1] name)."""
    with open(UNIVERSE_PATH, encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload["markets"]["TW"]["rows"]
    return {row[0]: clean_name(row[1]) for row in rows}


def all_entries():
    for theme in themes_tw.THEMES:
        for entry in theme["tickers"]:
            yield theme["key"], entry


class ConfigTest(unittest.TestCase):
    def test_theme_list_is_well_formed(self):
        themes = themes_tw.THEMES
        self.assertGreaterEqual(len(themes), 18)
        self.assertLessEqual(len(themes), 24)
        keys = [t["key"] for t in themes]
        self.assertEqual(len(keys), len(set(keys)))
        parents = {p["key"] for p in themes_tw.PARENTS}
        self.assertEqual(len(parents), len(themes_tw.PARENTS))
        self.assertGreaterEqual(len(parents), 6)
        self.assertLessEqual(len(parents), 8)
        orders = [p["order"] for p in themes_tw.PARENTS]
        self.assertEqual(len(orders), len(set(orders)))
        used = set()
        for theme in themes:
            self.assertEqual(
                set(theme), {"key", "name", "parent", "description", "etf", "tickers"}, theme["key"]
            )
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
                self.assertRegex(entry[0], SYMBOL_RE)
                self.assertTrue(entry[1].strip() and entry[2].strip(), entry)
                self.assertNotIn("*", entry[1], entry)
            if theme["etf"] is not None:
                self.assertRegex(theme["etf"], ETF_RE)
        self.assertEqual(used, parents)

    def test_benchmark_is_taiex(self):
        self.assertEqual(themes_tw.BENCHMARK, "^TWII")
        self.assertEqual(themes_tw.BENCHMARK_NAME, "加權指數")

    def test_same_ticker_keeps_one_name_everywhere_it_repeats(self):
        names = {}
        for _key, (symbol, name, _note) in all_entries():
            names.setdefault(symbol, set()).add(name)
        for symbol, spellings in names.items():
            self.assertEqual(len(spellings), 1, (symbol, spellings))


@unittest.skipUnless(os.path.exists(UNIVERSE_PATH), "docs/report/universe.json not present")
class UniverseTest(unittest.TestCase):
    """Constituents must come from the TW liquidity list (stocks with prices the daily report already fetches).

    universe.json is rewritten by the daily report; a few names may drop off on a quiet day, so only a
    large gap (typos) or a theme left with fewer than 5 valid names fails.
    """

    @classmethod
    def setUpClass(cls):
        cls.universe = tw_universe()

    def test_universe_is_loaded(self):
        self.assertGreater(len(self.universe), 100)

    def test_most_constituents_are_in_the_universe(self):
        # The list is rebuilt daily from that day's turnover, so a quiet day can drop a few names; a typo in a
        # ticker shows up as a larger gap, which is what this guards against.
        symbols = {symbol for _key, (symbol, _n, _d) in all_entries()}
        missing = sorted(symbol for symbol in symbols if symbol not in self.universe)
        self.assertLessEqual(len(missing), max(5, len(symbols) // 10), missing)

    def test_names_match_the_universe(self):
        mismatched = [
            (symbol, name, self.universe[symbol])
            for _key, (symbol, name, _note) in all_entries()
            if symbol in self.universe and name != self.universe[symbol]
        ]
        self.assertEqual(mismatched, [])

    def test_each_theme_keeps_enough_valid_constituents(self):
        for theme in themes_tw.THEMES:
            inside = [t[0] for t in theme["tickers"] if t[0] in self.universe]
            self.assertGreaterEqual(len(inside), 5, theme["key"])
            self.assertGreaterEqual(len(inside), build_themes.MIN_VALID, theme["key"])


class PayloadSmokeTest(unittest.TestCase):
    def test_build_payload_accepts_the_tw_config(self):
        config = {
            "parents": themes_tw.PARENTS,
            "themes": themes_tw.THEMES,
            "benchmark": themes_tw.BENCHMARK,
        }
        calendar = themes_synth.business_days("2026-10-09", 260)
        symbols = {t[0] for theme in themes_tw.THEMES for t in theme["tickers"]}
        symbols.add(themes_tw.BENCHMARK)
        prices = {
            symbol: [(day, 100.0 * (1 + 0.0005 * (index % 7))) for index, day in enumerate(calendar)]
            for symbol in symbols
        }
        payload = build_themes.build_payload(prices, config, TODAY)
        self.assertEqual(payload["benchmark"]["symbol"], "^TWII")
        self.assertEqual(len(payload["themes"]), len(themes_tw.THEMES))
        self.assertEqual(payload["coverage"], 1.0)
        for theme in payload["themes"]:
            self.assertEqual(theme["validCount"], theme["count"], theme["key"])


if __name__ == "__main__":
    unittest.main()

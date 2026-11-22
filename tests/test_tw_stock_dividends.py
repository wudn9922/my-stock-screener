"""Stock dividend (配股) EPS restatement: atlas/scripts/tw_stock_dividends.py, its FinMind fetch/builder
build_stock_dividends_tw_finmind.py and build_growth's use of it. Offline, synthetic answers.
Run: python3 -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "atlas", "scripts"))

import build_growth as growth  # noqa: E402
import build_stock_dividends_tw_finmind as builder  # noqa: E402
import tw_stock_dividends as div  # noqa: E402

ROWS = [
    {"stock_id": "1773", "year": "113", "StockEarningsDistribution": 1.0, "StockStatutorySurplus": 0.0,
     "StockExDividendTradingDate": "2025-08-20", "CashEarningsDistribution": 2.0},
    {"stock_id": "1773", "year": "114", "StockEarningsDistribution": 1.5, "StockStatutorySurplus": 0.5,
     "StockExDividendTradingDate": "2026-08-19"},
    {"stock_id": "1773", "year": "112", "StockEarningsDistribution": 0.0, "StockStatutorySurplus": 0.0,
     "CashEarningsDistribution": 3.0, "StockExDividendTradingDate": ""},
]


class Factor(unittest.TestCase):
    def test_parse_keeps_stock_dividends_only(self):
        events = div.parse_events(ROWS)
        self.assertEqual([(e["exDate"], e["perShare"]) for e in events], [("2025-08-20", 1.0), ("2026-08-19", 2.0)])
        self.assertEqual(div.parse_events([]), [])
        pending = div.parse_events([{"StockEarningsDistribution": 1, "year": "115"}])
        self.assertIsNone(pending[0]["exDate"])

    def test_single_and_multiple(self):
        events = div.parse_events(ROWS)
        self.assertAlmostEqual(div.factor("2025-03-31", events[:1])["factor"], 1.1)
        self.assertAlmostEqual(div.factor("2024-12-31", events)["factor"], 1.32)
        self.assertAlmostEqual(div.factor("2025-12-31", events)["factor"], 1.2)
        self.assertEqual(div.factor("2026-12-31", events)["factor"], 1.0)

    def test_already_in_report_and_pending_not_applied(self):
        events = div.parse_events(ROWS)[:1]
        self.assertEqual(div.factor("2025-06-30", events)["factor"], 1.0)  # due 2025-08-29 > ex 2025-08-20
        self.assertEqual(div.factor("2024-12-31", div.parse_events([{"StockEarningsDistribution": 1}]))["factor"], 1.0)

    def test_cash_only_is_one_and_missing_is_none(self):
        self.assertEqual(div.factor("2024-12-31", div.parse_events([ROWS[2]]))["factor"], 1.0)
        self.assertIsNone(div.factor("2024-12-31", None))
        self.assertEqual(div.adjust(6.0, "2024-12-31", None), (None, None))
        self.assertEqual(div.adjust(None, "2024-12-31", [])[0], None)
        value, info = div.adjust(6.0, "2024-12-31", div.parse_events(ROWS))
        self.assertEqual(value, 4.5455)
        self.assertEqual(len(info["events"]), 2)

    def test_annual_period_is_due_after_90_days(self):
        # a June fiscal year end would be a 60-day quarter; as an annual period its report is due after 90 days
        events = [{"exDate": "2025-09-15", "perShare": 1.0}]
        self.assertEqual(div.factor("2025-06-30", events)["factor"], 1.1)            # Q2 due 2025-08-29
        self.assertEqual(div.factor("2025-06-30", events, annual=True)["factor"], 1.0)  # annual due 2025-09-28

    def test_2755_restated_quarters(self):
        events = [{"exDate": "2023-07-04", "perShare": 3.7}, {"exDate": "2025-09-04", "perShare": 1.973802},
                  {"exDate": "2026-07-16", "perShare": 2.464591}]
        for end, eps, restated in (("2024-06-30", 1.47, 0.985), ("2025-06-30", 1.78, 1.193),
                                   ("2025-09-30", 1.80, 1.444), ("2026-03-31", 1.65, 1.324), ("2026-06-30", 1.58, 1.58)):
            self.assertAlmostEqual(div.adjust(eps, end, events)[0], restated, places=3)
        self.assertAlmostEqual(div.adjust(6.44, "2025-12-31", events, annual=True)[0], 5.1666, places=4)

    def test_as_of(self):
        self.assertAlmostEqual(div.factor("2024-12-31", div.parse_events(ROWS), as_of="2025-12-01")["factor"], 1.1)


def document():
    q = {"end": ["2025-12-31", "2026-12-31"], "fy": [2025, 2026], "fq": [4, 4], "src": ["finmind"] * 2,
         "values": {"epsBasic": [6.0, 6.6], "revenue": [100, 110]}}
    a = {"end": ["2024-12-31", "2025-12-31"], "fy": [2024, 2025], "fq": [None, None], "src": ["finmind"] * 2,
         "values": {"epsBasic": [6.0, 6.6], "revenue": [100, 110]}}
    return {"version": 1, "symbol": "1773.TW", "market": "TW", "currency": "TWD", "annual": a, "quarterly": q,
            "split": {"adjusted": False, "events": []}, "quality": {"level": "good", "issues": []}}


class Builder(unittest.TestCase):
    def test_writes_block_without_touching_statements(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "financials")
            os.makedirs(out)
            path = os.path.join(out, "1773.TW.json")
            doc = document()
            json.dump(doc, open(path, "w"))
            class Http:
                def get(self, url, headers):
                    assert "TaiwanStockDividend" in url and "data_id=1773" in url
                    return 200, json.dumps({"msg": "success", "status": 200, "data": ROWS}).encode()
            args = SimpleNamespace(report_dir=tmp, out_dir=out, budget=60, limit=None, tickers=None)
            rc = builder.run(args, http=Http(), sleep=lambda *_: None, log=lambda *_: None, today=date(2026, 11, 20))
            self.assertEqual(rc, 0)
            written = json.load(open(path))
            self.assertEqual(written["annual"], doc["annual"])
            block = written["stockDividends"]
            self.assertEqual(block["source"], div.SOURCE)
            self.assertEqual(block["checked"], "2026-11-20")
            self.assertEqual([e["perShare"] for e in block["events"]], [1.0, 2.0])
            # fresh: nothing to fetch the next day
            self.assertFalse(builder.due(written, date(2026, 11, 21)))
            self.assertTrue(builder.due(written, date(2026, 12, 31)))

    def test_growth_compares_restated_eps(self):
        doc = document()
        doc["stockDividends"] = div.block(div.parse_events(ROWS), "2026-11-20")
        # annual 2024 EPS 6.0 → ÷1.32 = 4.5455; 2025 EPS 6.6 → ÷1.2 = 5.5; reported YoY +10.0, restated +21.0
        _, _, y = growth.financials_blocks(doc, date(2026, 3, 1))
        self.assertEqual(y["eps"][0], 21.0)
        plain = document()
        _, _, y = growth.financials_blocks(plain, date(2026, 3, 1))
        self.assertEqual(y["eps"][0], 10.0)

    def test_official_taiwan_periods_are_restated_with_the_file_events(self):
        events = div.parse_events(ROWS)[:1]   # ex 2025-08-20, +10% shares
        periods = [
            {"end": "2024-06-30", "day": date(2024, 6, 30), "fy": 2024, "fq": 2, "epsD": None, "epsB": 1.1,
             "rev": 100, "shares": 100.0, "src": "mops", "derived": False, "kind": "normal"},
            {"end": "2025-06-30", "day": date(2025, 6, 30), "fy": 2025, "fq": 2, "epsD": None, "epsB": 1.0,
             "rev": 100, "shares": 110.0, "src": "mops", "derived": False, "kind": "normal"},
        ]
        growth.restate_periods(periods, events)
        # 2024 Q2 was reported before the ex date: 1.1 ÷ 1.1 = 1.0 on 110 shares; 2025 Q2 (due 08-29) already new basis
        self.assertEqual((periods[0]["epsB"], round(periods[0]["shares"], 6)), (1.0, 110.0))
        self.assertEqual(periods[1]["epsB"], 1.0)
        import build_fundamentals_tw as tw
        today = date(2026, 6, 1)
        store = {"fetch": {}, "data": {"1773": {tw.season_key(2025, 1): [100, 1.1, 110, 0], tw.season_key(2026, 1): [100, 1.0, 110, 0]}}}
        document_ = tw.dump_store(store, tw.target_seasons(today))
        plain = tw.growth_candidates(document_, {"1773": "1773.TW"}, today)["1773.TW"][1]
        restated = tw.growth_candidates(document_, {"1773": "1773.TW"}, today, {"1773.TW": events})["1773.TW"][1]
        self.assertEqual((plain["eps"], restated["eps"]), ([-9.1], [0.0]))
        self.assertEqual(growth.dividend_events([("1773.TW", {"stockDividends": {"events": events}}), ("AAPL", {})]),
                         {"1773.TW": events})


if __name__ == "__main__":
    unittest.main()

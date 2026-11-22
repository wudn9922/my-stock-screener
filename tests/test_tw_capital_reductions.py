"""Capital reduction (減資) EPS restatement: atlas/scripts/tw_capital_reductions.py, its fetch in
build_stock_dividends_tw_finmind.py, tw_stock_dividends.factor / events_of and build_growth's derived-Q4 rule.
Offline, synthetic answers. Run: python3 -m unittest discover -s tests -p test_tw_capital_reductions.py"""
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
import tw_capital_reductions as cap  # noqa: E402
import tw_stock_dividends as div  # noqa: E402


def row(stock, day, close, reference, reason, **extra):
    return {"date": day, "stock_id": stock, "ClosingPriceonTheLastTradingDay": close,
            "PostReductionReferencePrice": reference, "LimitUp": round(reference * 1.1, 2),
            "LimitDown": round(reference * 0.9, 2), "OpeningReferencePrice": reference, "ExrightReferencePrice": 0,
            "ReasonforCapitalReduction": reason, **extra}


# 100M → 50M shares: reference = close ÷ 0.5. Cash return of 40%: reference = (close − cash) ÷ 0.6.
LOSS = row("9999", "2025-09-01", 8.0, 16.0, "彌補虧損")
CASH = row("9999", "2026-03-02", 30.0, 33.33, "退還股款")


class Parse(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(cap.classify("彌補虧損"), "loss")
        self.assertEqual(cap.classify("退還股款"), "cash")
        self.assertEqual(cap.classify("現金減資"), "cash")
        self.assertEqual(cap.classify("彌補虧損及退還股款"), "mixed")
        self.assertEqual(cap.classify("註銷庫藏股"), "other")
        self.assertEqual(cap.classify(None), "other")
        self.assertEqual(cap.classify("Offset losses"), "loss")

    def test_ratio_from_reference_prices_or_an_explicit_field(self):
        logs = []
        events = cap.parse_events([LOSS, CASH, row("9999", "2024-01-02", 5, 5, "註銷庫藏股"), {"date": "bad"}], logs.append)
        self.assertEqual([(e["date"], e["kind"], e["ratio"], e["applied"]) for e in events],
                         [("2024-01-02", "other", None, False), ("2025-09-01", "loss", 0.5, True),
                          ("2026-03-02", "cash", None, False)])
        self.assertEqual(events[1]["raw"]["PostReductionReferencePrice"], 16.0)
        self.assertEqual(len(logs), 1)
        self.assertIn("註銷庫藏股", logs[0])
        explicit = cap.parse_events([row("1", "2025-01-02", 8.0, 16.1, "彌補虧損", ReductionRatio=0.48)])
        self.assertEqual(explicit[0]["ratio"], 0.48)
        # a ratio >= 1 (bad prices) is kept unapplied and logged
        logs = []
        bad = cap.parse_events([row("1", "2025-01-02", 16.0, 8.0, "彌補虧損")], logs.append)
        self.assertFalse(bad[0]["applied"])
        self.assertIn("without a usable ratio", logs[0])


def document(reductions):
    q = {"end": ["2025-03-31", "2025-06-30", "2026-03-31", "2026-06-30"], "fy": [2025, 2025, 2026, 2026],
         "fq": [1, 2, 1, 2], "src": ["finmind"] * 4,
         "values": {"epsBasic": [-1.0, 0.5, -1.6, 1.2], "netIncome": [-100, 50, -80, 60], "revenue": [10] * 4}}
    a = {"end": [], "fy": [], "fq": [], "src": [], "values": {}}
    doc = {"version": 1, "symbol": "9999.TW", "market": "TW", "currency": "TWD", "annual": a, "quarterly": q,
           "split": {"adjusted": False, "events": []}, "quality": {"level": "good", "issues": []},
           "stockDividends": div.block([], "2026-11-20")}
    if reductions is not None:
        doc["capitalReductions"] = cap.block(reductions, "2026-11-20")
    return doc


class Restate(unittest.TestCase):
    def test_loss_offset_doubles_the_loss_per_share_and_cash_is_untouched(self):
        loss = document(cap.parse_events([LOSS]))
        events = div.events_of(loss)
        self.assertEqual(div.adjust(-1.0, "2025-03-31", events)[0], -2.0)   # Q1 due 05-15, resumption 09-01
        self.assertEqual(div.adjust(0.5, "2025-06-30", events)[0], 1.0)     # Q2 due 08-29
        self.assertEqual(div.adjust(-1.6, "2025-09-30", events)[0], -1.6)   # Q3 due 11-14: already 50M shares
        cash = document(cap.parse_events([CASH]))
        self.assertEqual(div.events_of(cash), [])
        self.assertEqual(div.adjust(-1.0, "2025-03-31", div.events_of(cash))[0], -1.0)
        # no capitalReductions block: the dividend events as before
        self.assertEqual(div.events_of(document(None)), [])

    def test_growth_compares_on_the_reduced_share_basis(self):
        # Q1 2026 −1.6 vs Q1 2025 −1.0 restated −2.0: still a loss (null YoY); Q2 2026 1.2 vs Q2 2025 0.5 → 1.0: +20%
        q, _ = growth.financials_blocks(document(cap.parse_events([LOSS])), date(2026, 9, 1))[1:]
        self.assertEqual(q["eps"][0], 20.0)
        plain, _ = growth.financials_blocks(document(cap.parse_events([CASH])), date(2026, 9, 1))[1:]
        self.assertEqual(plain["eps"][0], 140.0)                               # cash reduction: not restated
        self.assertEqual(div.events_of(document(cap.parse_events([CASH]))), [])


class DerivedQ4(unittest.TestCase):
    def doc(self, events, splits=()):
        q = {"end": ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"], "fy": [2025] * 4, "fq": [1, 2, 3, 4],
             "src": ["finmind"] * 4, "values": {"epsBasic": [1.1, 1.1, 1.1, 0.7], "revenue": [10] * 4}}
        a = {"end": ["2025-12-31"], "fy": [2025], "fq": [None], "src": ["finmind"], "values": {"epsBasic": [4.0]}}
        return {"version": 1, "symbol": "1234.TW", "market": "TW", "annual": a, "quarterly": q,
                "split": {"adjusted": False, "events": [list(s) for s in splits]},
                "quality": {"issues": ["q4-derived"]}, "stockDividends": div.block(events, "2026-11-20")}

    def periods(self, document):
        quarterly = growth.read_periods(document["quarterly"], True, "TW")
        annual = growth.read_periods(document["annual"], False, "TW")
        growth.restate_for_stock_dividends(document, quarterly + annual)
        growth.recompute_derived_q4(document, quarterly, annual)
        return quarterly

    def test_dividend_between_q3_and_annual_report(self):
        q4 = self.periods(self.doc([{"exDate": "2025-12-10", "perShare": 1.0}]))[3]
        self.assertEqual(q4["epsB"], 1.0)        # 4.0 − 3 × (1.1 ÷ 1.1)
        same = self.periods(self.doc([{"exDate": "2025-10-01", "perShare": 1.0}]))[3]
        self.assertEqual(same["epsB"], 0.7)      # Q3 report already on the new basis: unchanged

    def test_split_between_q3_and_annual_report_drops_q4(self):
        q4 = self.periods(self.doc([], splits=[("2025-12-01", 10)]))[3]
        self.assertIsNone(q4["epsB"])


class Builder(unittest.TestCase):
    def run_builder(self, answer_all, answer_one=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = os.path.join(tmp.name, "financials")
        os.makedirs(out)
        for code in ("9999", "2330"):
            with open(os.path.join(out, f"{code}.TW.json"), "w", encoding="utf-8") as file:
                json.dump(document(None) | {"symbol": f"{code}.TW"}, file)
        urls = []

        class Http:
            def get(self, url, headers):
                urls.append(url)
                if cap.DATASET in url:
                    rows = answer_all if "data_id" not in url else (answer_one or {}).get(url.split("data_id=")[1].split("&")[0], [])
                    return 200, json.dumps({"msg": "success", "status": 200, "data": rows}).encode()
                return 200, json.dumps({"msg": "success", "status": 200, "data": []}).encode()

        args = SimpleNamespace(report_dir=tmp.name, out_dir=out, budget=600, limit=None, tickers=None)
        rc = builder.run(args, http=Http(), sleep=lambda *_: None, log=lambda *_: None, today=date(2026, 11, 22))
        self.assertEqual(rc, 0)

        def read(code):
            with open(os.path.join(out, f"{code}.TW.json"), encoding="utf-8") as file:
                return json.load(file)
        return urls, read

    def test_one_all_market_request(self):
        rows = [LOSS, CASH] + [row("1101", f"2020-0{m}-01", 10, 12, "彌補虧損") for m in range(1, 6)]
        urls, read = self.run_builder(rows)
        self.assertEqual(sum(cap.DATASET in url for url in urls), 1)
        self.assertNotIn("data_id", next(url for url in urls if cap.DATASET in url))
        block = read("9999")["capitalReductions"]
        self.assertEqual(block["source"], cap.SOURCE)
        self.assertEqual(block["checked"], "2026-11-22")
        self.assertEqual([(e["kind"], e["applied"]) for e in block["events"]], [("loss", True), ("cash", False)])
        self.assertEqual(read("2330")["capitalReductions"]["events"], [])
        self.assertIn("stockDividends", read("9999"))
        self.assertFalse(builder.due(read("9999"), date(2026, 11, 23), "capitalReductions"))

    def test_untrusted_all_market_answer_falls_back_to_one_request_per_stock(self):
        urls, read = self.run_builder([LOSS], {"9999": [LOSS]})     # a single day cannot be the whole market
        self.assertEqual(sum(cap.DATASET in url and "data_id" in url for url in urls), 2)
        self.assertEqual(read("9999")["capitalReductions"]["events"][0]["ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()

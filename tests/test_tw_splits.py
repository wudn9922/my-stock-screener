"""Taiwan splits / par-value changes: atlas/scripts/tw_splits.py (FinMind events, inferred-split fallback), their fetch
in build_stock_dividends_tw_finmind.py and the restatement in build_growth.py. Offline, synthetic answers.
Run: python3 -m unittest discover -s tests -p test_tw_splits.py"""
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from datetime import date
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "atlas", "scripts"))

import build_growth as growth  # noqa: E402
import build_stock_dividends_tw_finmind as builder  # noqa: E402
import tw_splits as splits  # noqa: E402
import tw_stock_dividends as div  # noqa: E402

# FinMind docs samples (finmind.github.io tutor/TaiwanMarket/Fundamental)
SPLIT_0050 = {"date": "2025-06-18", "stock_id": "0050", "type": "分割", "before_price": 188.65, "after_price": 47.16,
              "max_price": 51.85, "min_price": 42.45, "open_price": 47.16}
REVERSE_00632R = {"date": "2024-12-11", "stock_id": "00632R", "type": "反分割", "before_price": 3.28, "after_price": 22.96,
                  "max_price": 25.25, "min_price": 20.67, "open_price": 22.96}
PAR_8070 = {"date": "2020-08-17", "stock_id": "8070", "stock_name": "長華", "before_close": 190, "after_ref_close": 19,
            "after_ref_max": 20.9, "after_ref_min": 17.1, "after_ref_open": 19}
PAR_6415 = {"date": "2022-07-13", "stock_id": "6415", "stock_name": "矽力-K", "before_close": 2485, "after_ref_close": 621.25,
            "after_ref_max": 683, "after_ref_min": 560, "after_ref_open": 621}


class Parse(unittest.TestCase):
    def test_split_reverse_and_par_change(self):
        split = splits.parse_split_rows([SPLIT_0050])[0]
        self.assertEqual((split["kind"], split["ratio"], split["applied"]), ("split", 4.0, True))
        self.assertEqual(split["raw"]["before_price"], 188.65)
        reverse = splits.parse_split_rows([REVERSE_00632R])[0]
        self.assertEqual((reverse["kind"], reverse["ratio"], reverse["applied"]), ("reverse", round(1 / 7, 6), True))
        par = splits.parse_par_rows([PAR_8070, PAR_6415])
        self.assertEqual([(e["kind"], e["ratio"], e["applied"]) for e in par], [("par", 10.0, True), ("par", 4.0, True)])
        self.assertEqual(par[0]["raw"]["stock_name"], "長華")

    def test_ratio_snaps_within_one_percent_only(self):
        self.assertEqual(splits.snap_ratio(2.04 / 12.23), round(1 / 6, 6))   # 00676R 反分割
        self.assertEqual(splits.snap_ratio(2.5), 2.5)
        self.assertEqual(splits.snap_ratio(1.9), 1.9)                         # 5% from 2: kept raw
        self.assertIsNone(splits.snap_ratio(None))

    def test_tolerant_rows(self):
        logs = []
        events = splits.parse_events(
            [dict(SPLIT_0050, stock_id="9999"), {"date": "bad"}, "x",
             dict(SPLIT_0050, stock_id="9999", date="2025-01-02", type="反分割")],                # type contradicts the prices
            [dict(PAR_8070, stock_id="9999"), dict(PAR_8070, stock_id="1111", date="2021-01-04"),
             dict(PAR_8070, stock_id="9999", date="2023-03-01", after_ref_close=None)], "9999", logs.append)
        self.assertEqual([(e["date"], e["kind"], e["applied"]) for e in events],
                         [("2020-08-17", "par", True), ("2023-03-01", "par", False), ("2025-01-02", "split", False),
                          ("2025-06-18", "split", True)])
        self.assertEqual(len(logs), 2)
        both = splits.parse_events([dict(SPLIT_0050, date="2020-08-17")], [PAR_8070])
        self.assertEqual(len(both), 1)
        self.assertEqual(both[0]["raw"]["split"]["type"], "分割")


# 寶雅 5904.TWO as in docs/report/financials (FinMind): par 10 → 1 between the Q2 and the Q3 2026 reports.
Q_END = ["2023-09-30", "2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30",
         "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
Q_EPS = [6.89, 6.57, 6.78, 5.61, 7.27, 7.15, 7.48, 5.58, 7.59, 8.98, 9.3, 8.15, 0.95]
Q_NI = [712928000, 679732000, 701515000, 589084000, 762984000, 750810000, 784586000, 594048000, 808340000, 955557000,
        990042000, 867577000, 1015678000]
Q_REV = [5869731000, 5634862000, 5873699000, 5627850000, 6124615000, 6002058000, 6295928000, 6044385000, 6400814000,
         6578714000, 7094656000, 6932323000, 7359183000]


def poya(twsplits=None, eps=Q_EPS, ni=Q_NI, rev=Q_REV):
    q = {"end": Q_END, "fy": [int(e[:4]) for e in Q_END], "fq": [3, 4] + [1, 2, 3, 4] * 2 + [1, 2, 3],
         "src": ["finmind"] * len(Q_END), "values": {"epsBasic": list(eps), "netIncome": list(ni), "revenue": list(rev)}}
    a = {"end": ["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"], "fy": [2022, 2023, 2024, 2025],
         "src": ["finmind"] * 4, "values": {"epsBasic": [20.26, 24.41, 26.74, 29.54],
                                            "netIncome": [2068075000, 2525392000, 2804393000, 3142531000],
                                            "revenue": [19476169000, 22078695000, 23628222000, 25319841000]}}
    doc = {"version": 1, "symbol": "5904.TWO", "market": "TW", "currency": "TWD", "annual": a, "quarterly": q,
           "split": {"adjusted": False, "events": []}, "quality": {"level": "good", "issues": ["q4-derived"]},
           "stockDividends": div.block([{"exDate": "2024-06-21", "perShare": 0.1}, {"exDate": "2025-07-15", "perShare": 0.1}],
                                       "2026-11-20")}
    if twsplits is not None:
        doc["twSplits"] = splits.block(twsplits, "2026-11-23")
    return doc


def restated(document):
    quarterly = growth.read_periods(document["quarterly"], True, "TW")
    annual = growth.read_periods(document["annual"], False, "TW")
    growth.restate_for_stock_dividends(document, quarterly + annual)
    events, dated = growth.tw_split_plan(document, div.events_of(document), quarterly)
    newest = max(p["day"] for p in annual)
    anchors = [dict(p) for p in quarterly if p["day"] > newest]
    growth.restate_splits(quarterly, events, (), dated)
    growth.restate_splits(annual, events, anchors, dated)
    return events, {p["end"]: p["epsB"] for p in quarterly}, {p["end"]: p["epsB"] for p in annual}


class Poya(unittest.TestCase):
    def check(self, document):
        events, q, a = restated(document)
        self.assertEqual(len(events), 1)
        self.assertEqual(q["2026-06-30"], 0.815)
        self.assertEqual(q["2026-03-31"], 0.93)
        self.assertEqual(q["2025-09-30"], 0.759)
        self.assertEqual(q["2026-09-30"], 0.95)                    # already on the new basis: not divided again
        self.assertEqual(a["2025-12-31"], 2.954)
        self.assertAlmostEqual(q["2025-12-31"] + q["2026-03-31"] + q["2026-06-30"] + q["2026-09-30"], 3.593, 4)
        _, qb, yb = growth.financials_blocks(document, date(2026, 11, 23))
        self.assertEqual(qb["eps"][0], round((0.95 / 0.759 - 1) * 100, 1))    # +25.2%, not −87.5%
        self.assertEqual(yb["eps"][0], round((29.54 / (26.74 / 1.01) - 1) * 100, 1))
        return events

    def test_finmind_par_value_change(self):
        par = splits.parse_par_rows([{"date": "2026-08-10", "stock_id": "5904", "before_close": 720,
                                      "after_ref_close": 72}], "5904")
        self.assertEqual(self.check(poya(par)), [(date(2026, 8, 10), 10.0)])

    def test_inferred_without_an_event(self):
        events = self.check(poya([]))
        self.assertEqual(events, [(date(2026, 7, 1), 10.0)])
        found = splits.infer_splits(points(poya()), [])
        self.assertEqual(found[0]["evidence"]["before"], "2026-06-30")
        self.assertEqual(found[0]["evidence"]["periodsAfter"], 1)

    def test_a_recorded_event_blocks_the_inference(self):
        document = poya()
        document["split"]["events"] = [["2026-08-10", 10]]
        events, q, _ = restated(document)
        self.assertEqual(events, [(date(2026, 8, 10), 10.0)])
        self.assertEqual(q["2026-06-30"], 0.815)


def points(document):
    quarterly = growth.read_periods(document["quarterly"], True, "TW")
    growth.restate_for_stock_dividends(document, quarterly)
    return [{"day": p["day"], "eps": p["epsB"], "ni": p["ni"], "rev": p["rev"],
             "due": p["day"]} for p in quarterly]


class Detector(unittest.TestCase):
    def test_constant_minority_offset_is_not_a_jump(self):
        # 2006.TW style: net income includes minority interests, NI ÷ EPS ≈ 2.7× the real share count every quarter
        eps = [1.0, 1.1, 0.9, 1.2, 1.0, 1.05, 0.95, 1.1, 1.0, 0.9, 1.1, 1.0, 1.05]
        ni = [round(e * 2.7 * 100e6) for e in eps]
        self.assertEqual(splits.infer_splits(points(poya(eps=eps, ni=ni)), []), [])
        self.assertEqual(restated(poya(eps=eps, ni=ni))[0], [])

    def test_issuance_is_not_a_split(self):
        # a 10% cash issuance: shares 106.5M → 117M
        eps = Q_EPS[:-1] + [round(1015678000 / 117e6, 2)]
        self.assertEqual(splits.infer_splits(points(poya(eps=eps)), []), [])
        # 5314.TWO style: 20× shares from a deal that brings 5× revenue with it
        rev = Q_REV[:9] + [r * 5 for r in Q_REV[9:]]
        eps = Q_EPS[:9] + [round(n / (106.5e6 * 20), 2) for n in Q_NI[9:]]
        self.assertEqual(splits.infer_splits(points(poya(eps=eps, rev=rev)), []), [])

    def test_one_quarter_blip_and_newest_loss_do_not_fire(self):
        # one odd quarter (a derived Q4 or a typo) that returns to the old level
        eps = Q_EPS[:9] + [0.9] + Q_EPS[10:12] + [9.5]
        self.assertEqual(splits.infer_splits(points(poya(eps=eps)), []), [])
        # a halving shown only by the newest quarter, a loss (8291.TWO 2026Q3): no persistence, no profit → nothing
        ni = Q_NI[:-1] + [-500000000]
        eps = Q_EPS[:-1] + [-9.39]
        self.assertEqual(splits.infer_splits(points(poya(eps=eps, ni=ni)), []), [])

    def test_blocked_by_a_recorded_share_event(self):
        self.assertEqual(splits.infer_splits(points(poya()), [date(2026, 8, 10)]), [])
        self.assertEqual(len(splits.infer_splits(points(poya()), [date(2026, 6, 1)])), 1)


PAR_ALL = ({"date": "2026-08-10", "stock_id": "5904", "stock_name": "寶雅", "before_close": 720, "after_ref_close": 72},
           PAR_8070)


class Builder(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.out = os.path.join(tmp.name, "financials")
        os.makedirs(self.out)
        for code in ("5904.TWO", "8070.TW"):
            with open(os.path.join(self.out, code + ".json"), "w", encoding="utf-8") as file:
                json.dump(poya() | {"capitalReductions": {"checked": "2026-11-22", "events": []},
                                    "stockDividends": {"checked": "2026-11-22", "events": []}}, file)

    def run_builder(self, answer):
        """answer(dataset, data_id) → (status, rows); returns the requested URLs."""
        urls = []

        class Http:
            def get(self, url, headers):
                urls.append(url)
                query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                status, rows = answer(query["dataset"][0], query.get("data_id", [None])[0])
                return status, json.dumps({"msg": "success" if status == 200 else "refused", "status": status,
                                           "data": rows}).encode()

        args = SimpleNamespace(report_dir=self.root, out_dir=self.out, budget=600, limit=None, tickers=None)
        rc = builder.run(args, http=Http(), sleep=lambda *_: None, log=lambda *_: None, today=date(2026, 11, 23))
        self.assertEqual(rc, 0)
        return urls

    def document(self, code):
        with open(os.path.join(self.out, code + ".json"), encoding="utf-8") as file:
            return json.load(file)

    def test_two_all_market_requests_cover_every_stock(self):
        def answer(dataset, data_id):
            self.assertIsNone(data_id)
            return 200, list(PAR_ALL) if dataset == splits.DATASET_PAR else [{"date": "2015-01-05", "stock_id": "9999"}]

        urls = self.run_builder(answer)
        self.assertEqual(len(urls), 2)
        block = self.document("5904.TWO")["twSplits"]
        self.assertEqual((block["source"], block["checked"]), (splits.SOURCE, "2026-11-23"))
        self.assertEqual([(e["date"], e["ratio"], e["kind"]) for e in block["events"]], [("2026-08-10", 10.0, "par")])
        self.assertEqual([e["date"] for e in self.document("8070.TW")["twSplits"]["events"]], ["2020-08-17"])
        self.assertFalse(builder.due(self.document("5904.TWO"), date(2026, 11, 24), "twSplits"))
        self.assertTrue(builder.due(self.document("5904.TWO"), date(2026, 12, 23), "twSplits"))

    def test_no_par_answer_skips_every_twsplits_block(self):
        urls = self.run_builder(lambda dataset, data_id: (400, []))
        self.assertEqual(len(urls), 1)
        self.assertNotIn("twSplits", self.document("5904.TWO"))

    def test_empty_split_answer_falls_back_to_one_split_request_per_stock(self):
        def answer(dataset, data_id):
            if dataset == splits.DATASET_PAR:
                self.assertIsNone(data_id)
                return 200, list(PAR_ALL)
            return 200, []

        urls = self.run_builder(answer)
        per_stock = [url for url in urls if "data_id=" in url]
        self.assertEqual(len(per_stock), 2)
        self.assertTrue(all(splits.DATASET_SPLIT in url for url in per_stock))
        self.assertEqual([e["kind"] for e in self.document("5904.TWO")["twSplits"]["events"]], ["par"])


if __name__ == "__main__":
    unittest.main()

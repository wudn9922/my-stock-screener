import json
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_stock_digest as digest
from research_store import MarketStore

TODAY = date(2026, 11, 28)
SESSIONS = ["2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"]


def financial(new=39):
    return {"symbol": "AAPL", "market": "US", "quarterly": {
        "end": ["2026-06-30", "2026-09-30"], "fy": [2026, 2026], "fq": [2, 3],
        "values": {"revenue": [100, 100], "grossProfit": [42, new], "operatingIncome": [10, 13]},
    }}


def institutional(nets):
    return {"symbol": "2330.TW", "rows": [
        {"date": day, "foreign": {"net": net}, "trust": {"net": -net}}
        for day, net in zip(SESSIONS, nets)
    ]}


class DigestTests(unittest.TestCase):
    def test_margin_boundary_and_only_new_quarter(self):
        prev = 2026 * 4 + 2
        rows = digest.margin_events(financial(), prev, TODAY)
        self.assertEqual([r["kind"] for r in rows], ["gross-margin", "operating-margin"])
        self.assertEqual((rows[0]["before"], rows[0]["after"]), (42, 39))
        self.assertEqual([r["kind"] for r in digest.margin_events(financial(39.01), prev, TODAY)], ["operating-margin"])
        self.assertEqual(digest.margin_events(financial(), prev + 1, TODAY), [])
        self.assertEqual(digest.margin_events(financial(), None, TODAY), [])
        doc = financial()
        doc["quarterly"]["fq"][0] = 1
        self.assertEqual(digest.margin_events(doc, prev, TODAY), [])
        doc = financial()
        doc["quarterly"]["values"]["grossProfit"][0] = None
        self.assertEqual([r["kind"] for r in digest.margin_events(doc, prev, TODAY)], ["operating-margin"])

    def test_sec_only_actual_restatement_and_effective_date(self):
        doc = financial()
        doc["quality"] = {"revisions": [
            {"kind": "restated", "accn": "new", "originalAccn": "old", "filed": "2026-11-01", "end": "2025-12-31"},
            {"kind": "restated", "seen": "2026-11-28", "end": "2024-12-31"},
            {"kind": "basis-unrestated", "accn": "new", "originalAccn": "old", "filed": "2026-11-01", "end": "2025-09-30"},
        ]}
        rows, _ = digest.restatement_events(doc, None, TODAY)
        self.assertEqual(rows, [{"kind": "sec-restatement", "date": "2026-11-01", "period": "2025-12-31"}])
        doc["quality"]["revisions"][0].pop("filed")
        self.assertEqual(digest.restatement_events(doc, None, TODAY)[0], [])
        rows, state = digest.restatement_events(doc, [], TODAY)
        self.assertEqual(rows[0]["date"], TODAY.isoformat())
        self.assertEqual(digest.restatement_events(doc, state, TODAY)[0], [])

    def test_reversal_and_missing_session(self):
        self.assertEqual([r["direction"] for r in digest.reversal_events(institutional([1, 2, 3, -1]), SESSIONS, TODAY)], ["sell", "buy"])
        for nets in ([1, 0, 3, -1], [1, 2, 3, 0], [1, -2, 3, -1]):
            self.assertEqual(digest.reversal_events(institutional(nets), SESSIONS, TODAY), [])
        self.assertEqual(digest.reversal_events(institutional([1, 2, 3, -1]), SESSIONS + ["2026-11-28"], TODAY), [])

    def test_build_and_keep_output_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            (report / "financials").mkdir()
            file = report / "financials/AAPL.json"
            file.write_text(json.dumps(financial()))
            research = report / "research"
            store = MarketStore(research, "institutions")
            store.put("2330.TW", institutional([1, 2, 3, -1]))
            (research / "institutions.json").write_text(json.dumps({
                "version": 1, "storage": store.save(), "sources": {"TW": {"dates": SESSIONS}}
            }))
            self.assertTrue(digest.run(report, TODAY))
            output = report / "stock-digest.json"
            old = output.read_bytes()
            doc = json.loads(old)
            self.assertEqual(doc["version"], 1)
            self.assertIn("generatedAt", doc)
            self.assertEqual(set(doc["stocks"]), {"2330.TW"})
            self.assertTrue(digest.run(report, TODAY, dry_run=True))
            self.assertEqual(output.read_bytes(), old)
            file.write_text("{")
            self.assertFalse(digest.run(report, TODAY))
            self.assertEqual(output.read_bytes(), old)

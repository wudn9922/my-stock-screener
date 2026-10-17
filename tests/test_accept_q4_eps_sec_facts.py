"""
Opus steps 3–4 (atlas/scripts/accept_q4_eps_sec_facts.py): accept grade-A SEC Q4 EPS facts and the first batch of
direct corrections, which need all four conditions.

Offline: every document is a hand-written SYNTHETIC stored file ("Synthetic Corp."), in temporary directories.
Run: python -m unittest discover -s tests -p "test_accept_q4_eps_sec_facts.py" -v
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import accept_q4_eps_sec_facts as accept  # noqa: E402


def synthetic_document(symbol="SYN", old_basic=0.02, events=None, net_income=8.0, q3_shares=100.0):
    """FY2018: Q1–Q3 with 100 diluted shares, Q4 net income 8 (so NI ÷ Q3 shares = 0.08). Stored Q4 basic EPS
    is old_basic; diluted is 0.08 so the signs agree."""
    return {
        "symbol": symbol,
        "market": "US",
        "split": {"adjusted": True, "events": events or []},
        "quarterly": {
            "end": ["2018-03-31", "2018-06-30", "2018-09-30", "2018-12-31"],
            "fy": [2018, 2018, 2018, 2018],
            "fq": [1, 2, 3, 4],
            "src": ["sec"] * 4,
            "derived": [None, None, None, 1],
            "values": {
                "netIncome": [2.0, 2.0, 2.0, net_income],
                "dilutedShares": [100.0, 100.0, q3_shares, None],
                "epsDiluted": [0.02, 0.02, 0.02, 0.08],
                "epsBasic": [0.02, 0.02, 0.02, old_basic],
            },
        },
    }


def sec_fact(value, filed="2020-02-28", grade="A"):
    return {"concept": "EarningsPerShareBasic", "value": value, "filed": filed, "accession": "0000000000-20-000001",
            "grade": grade, "method": "direct-sec-companyfacts"}


class CorrectionConditionTests(unittest.TestCase):
    def test_all_four_conditions_pass(self):
        document = synthetic_document(old_basic=0.02)
        ok, reasons = accept.correction_conditions(document, "2018-12-31", 3, "epsBasic", 0.02, 0.08, "2020-02-28")
        self.assertTrue(ok, reasons)

    def test_missing_q3_shares_blocks(self):
        # Q3 shares fall back to the nearest quarter's count, so drop every count to leave nothing to use
        document = synthetic_document(q3_shares=None)
        document["quarterly"]["values"]["dilutedShares"] = [None, None, None, None]
        ok, reasons = accept.correction_conditions(document, "2018-12-31", 3, "epsBasic", 0.02, 0.08, "2020-02-28")
        self.assertFalse(ok)
        self.assertIn("no-NI-or-Q3-shares", reasons)

    def test_value_on_the_wrong_side_of_NI_over_shares_blocks(self):
        document = synthetic_document(old_basic=0.02)
        ok, reasons = accept.correction_conditions(document, "2018-12-31", 3, "epsBasic", 0.02, -0.08, "2020-02-28")
        self.assertFalse(ok)
        self.assertIn("sign-vs-NI-over-shares", reasons)

    def test_split_ratio_that_matches_a_later_split_blocks_when_filed_before_it(self):
        # old 0.09 → new 0.03 is a ratio of 1/3, the product of a 2021 split of 1/3, and the filing is before it
        document = synthetic_document(old_basic=0.09, events=[["2021-02-17", 0.333333]], net_income=8.0)
        ok, reasons = accept.correction_conditions(document, "2018-12-31", 3, "epsBasic", 0.09, 0.03, "2020-02-28")
        self.assertFalse(ok)
        self.assertIn("basis-split", reasons)

    def test_filing_after_every_split_passes_the_basis_check(self):
        document = synthetic_document(old_basic=0.09, events=[["2019-01-01", 0.333333]])
        ok, reasons = accept.correction_conditions(document, "2018-12-31", 3, "epsBasic", 0.09, 0.08, "2020-02-28")
        self.assertNotIn("basis-split", reasons)

    def test_diluted_and_basic_must_share_a_sign(self):
        document = synthetic_document(old_basic=0.02)
        document["quarterly"]["values"]["epsDiluted"][3] = -0.08
        ok, reasons = accept.correction_conditions(document, "2018-12-31", 3, "epsBasic", 0.02, 0.08, "2020-02-28")
        self.assertIn("diluted-basic-sign", reasons)


class ProcessFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="accept-q4-")
        self.target = os.path.join(self.dir, "target")
        self.source = os.path.join(self.dir, "source")
        os.makedirs(self.target)
        os.makedirs(self.source)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, directory, name, document):
        with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
            json.dump(document, handle)

    def ledger(self, rows):
        path = os.path.join(self.dir, "ledger.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        return path

    def run_one(self, target_document, source_facts, ledger_rows, name="SYN.json"):
        source_document = copy.deepcopy(target_document)
        source_document["secQ4EpsFacts"] = source_facts
        self.write(self.target, name, target_document)
        self.write(self.source, name, source_document)
        report = {"verifiedA": 0, "nullToA": 0, "overridden": [], "blocked": [], "deferred": []}
        accept.process_file(os.path.join(self.target, name), os.path.join(self.source, name),
                            accept.load_direct_keys(self.ledger(ledger_rows)), True, report)
        with open(os.path.join(self.target, name), encoding="utf-8") as handle:
            return json.load(handle), report

    def test_verify_existing_value_is_kept_graded_A_and_traced(self):
        document = synthetic_document(old_basic=0.08)
        document["symbol"] = "SYN"
        out, report = self.run_one(document, {"2018-12-31": {"epsBasic": sec_fact(0.08)}}, [])
        self.assertEqual(out["quarterly"]["values"]["epsBasic"][3], 0.08)
        self.assertEqual(out["quarterly"]["epsQuality"][3]["epsBasic"], "A")
        self.assertEqual(out["secQ4EpsFacts"]["2018-12-31"]["epsBasic"]["value"], 0.08)
        self.assertEqual(report["verifiedA"], 1)

    def test_grade_B_source_fact_is_ignored(self):
        document = synthetic_document(old_basic=0.08)
        out, report = self.run_one(document, {"2018-12-31": {"epsBasic": sec_fact(0.08, grade="B")}}, [])
        self.assertNotIn("epsQuality", out["quarterly"])
        self.assertEqual(report["verifiedA"], 0)

    def test_large_eps_is_deferred(self):
        document = synthetic_document(old_basic=1500.0)
        _, report = self.run_one(document, {"2018-12-31": {"epsBasic": sec_fact(1500.0)}}, [])
        self.assertEqual(len(report["deferred"]), 1)
        self.assertEqual(report["verifiedA"], 0)

    def test_direct_correction_needs_the_ledger_row_and_all_conditions(self):
        document = synthetic_document(symbol="ADAM", old_basic=0.02)
        ledger = [{"symbol": "ADAM", "correctionClass": accept.DIRECT_CLASS, "field": "epsBasic",
                   "correctedValue": 0.08, "period": {"end": "2018-12-31"}}]
        out, report = self.run_one(document, {"2018-12-31": {"epsBasic": sec_fact(0.08)}}, ledger, name="ADAM.json")
        self.assertEqual(out["quarterly"]["values"]["epsBasic"][3], 0.08)
        self.assertEqual(out["epsOverrides"]["2018-12-31"]["epsBasic"]["old"], 0.02)
        self.assertEqual(out["epsOverrides"]["2018-12-31"]["epsBasic"]["oldSource"], "sec-derived")
        self.assertEqual(len(report["overridden"]), 1)

    def test_direct_correction_without_ledger_row_is_blocked(self):
        document = synthetic_document(symbol="ADAM", old_basic=0.02)
        out, report = self.run_one(document, {"2018-12-31": {"epsBasic": sec_fact(0.08)}}, [], name="ADAM.json")
        self.assertEqual(out["quarterly"]["values"]["epsBasic"][3], 0.02)
        self.assertNotIn("epsOverrides", out)
        self.assertEqual(report["blocked"][0]["reason"], "not-in-first-batch")

    def test_null_to_A_only_for_the_approved_rows(self):
        self.assertIn(("APAM", "2017-12-31", "epsBasic"), accept.NULL_TO_A)
        document = synthetic_document(symbol="APAM", old_basic=None)
        out, report = self.run_one(document, {"2018-12-31": {"epsBasic": sec_fact(0.08)}}, [], name="APAM.json")
        self.assertIsNone(out["quarterly"]["values"]["epsBasic"][3])  # 2018 is not an approved row
        self.assertEqual(report["nullToA"], 0)


if __name__ == "__main__":
    unittest.main()

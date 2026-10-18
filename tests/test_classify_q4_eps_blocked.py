"""
Read-only classification of the US Q4 EPS rows that the acceptance step left blocked
(atlas/scripts/classify_q4_eps_blocked.py). Synthetic ledger rows only.
Run: python -m unittest discover -s tests -p "test_classify_q4_eps_blocked.py" -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import classify_q4_eps_blocked as classify  # noqa: E402


def row(symbol, end, old, new, reason, field="epsBasic"):
    return {"symbol": symbol, "end": end, "field": field, "old": old, "new": new, "reason": reason}


class ClassifyTests(unittest.TestCase):
    def test_each_blocked_row_gets_one_class_in_priority_order(self):
        ledger = {"blocked": [
            row("JAKK", "2018-12-31", -1.4, -0.14, "not-in-first-batch"),
            row("AAA", "2015-12-31", 0.24, 0.25, "no-NI-or-Q3-shares"),        # one cent, even with a condition failure
            row("BBB", "2016-12-31", 0.5, 0.2, "no-NI-or-Q3-shares"),
            row("CCC", "2017-12-31", 0.5, 0.2, "not-closer-to-NI-over-shares"),
            row("DDD", "2018-12-31", 0.5, 0.2, "not-in-first-batch"),
        ], "deferred": []}
        report = classify.build(ledger)
        by_symbol = {item["symbol"]: item["class"] for item in report["rows"]}
        self.assertEqual(by_symbol, {"JAKK": "excluded-first-batch", "AAA": "one-cent-boundary",
                                     "BBB": "condition-no-NI-or-Q3-shares",
                                     "CCC": "condition-not-closer-to-NI-over-shares",
                                     "DDD": "not-direct-class"})
        self.assertEqual(report["blocked"], 5)
        self.assertEqual(sum(report["counts"].values()), 5)

    def test_the_real_ledger_splits_154_as_expected(self):
        path = os.path.join(ROOT, "docs", "report", "q4-eps-acceptance-ledger.json")
        if not os.path.exists(path):
            self.skipTest("ledger not present")
        import json
        with open(path, encoding="utf-8") as handle:
            report = classify.build(json.load(handle))
        self.assertEqual(report["blocked"], 154)
        self.assertEqual(report["counts"], {"condition-diluted-basic-sign": 1, "condition-no-NI-or-Q3-shares": 7,
                                            "condition-not-closer-to-NI-over-shares": 4,
                                            "excluded-first-batch": 1, "not-direct-class": 111,
                                            "one-cent-boundary": 30})


if __name__ == "__main__":
    unittest.main()

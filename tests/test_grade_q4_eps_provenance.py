"""
Opus steps 1–2 (atlas/scripts/grade_q4_eps_provenance.py): move the share-count estimates out of epsDiluted and
grade the US Q4 EPS by provenance (A = SEC fact, Y = derived, none otherwise).

Offline: every document is a hand-written SYNTHETIC stored file ("Synthetic Corp."). Run:
python -m unittest discover -s tests -p "test_grade_q4_eps_provenance.py" -v
"""
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import grade_q4_eps_provenance as grade  # noqa: E402


def synthetic_document(q4_eps=0.4, annual_eps=1.6, flag=True, derived=None, trace=None, quality_issues=None):
    """FY2020 with Q1–Q3 EPS 0.3 each and 100 diluted shares everywhere. The Q4 net income of 40 over the
    estimated Q4 share count of 100 gives the method-B estimate 0.4. The annual EPS 1.6 gives a derivation of
    0.7, so the default stored 0.4 is a share estimate; annual 1.3 gives 0.4 (a derived value)."""
    quarterly = {
        "end": ["2020-03-31", "2020-06-30", "2020-09-30", "2020-12-31"],
        "fy": [2020, 2020, 2020, 2020],
        "fq": [1, 2, 3, 4],
        "src": ["sec"] * 4,
        "values": {
            "netIncome": [10, 10, 10, 40],
            "dilutedShares": [100, 100, 100, None],
            "epsDiluted": [0.3, 0.3, 0.3, q4_eps],
        },
        "derived": derived if derived is not None else [None, None, None, 1],
    }
    annual = {
        "end": ["2020-12-31"],
        "fy": [2020],
        "src": ["sec"],
        "values": {"dilutedShares": [100], "epsDiluted": [annual_eps]},
    }
    document = {
        "symbol": "SYN",
        "market": "US",
        "annual": annual,
        "quarterly": quarterly,
        "quality": {"issues": quality_issues if quality_issues is not None else (["q4-eps-from-shares"] if flag else [])},
    }
    if trace is not None:
        document["secQ4EpsFacts"] = trace
    return document


class MoveShareEstimatesTests(unittest.TestCase):
    def test_flagged_method_b_value_moves_to_estimates_with_inputs(self):
        document = synthetic_document(q4_eps=0.4)
        self.assertEqual(grade.move_share_estimates(document), 1)
        self.assertIsNone(document["quarterly"]["values"]["epsDiluted"][3])
        self.assertIsNone(document["quarterly"]["derived"][3])
        estimate = document["q4EpsEstimates"]["2020-12-31"]
        self.assertEqual(estimate["epsDiluted"], 0.4)
        self.assertEqual(estimate["grade"], "C")
        self.assertEqual(estimate["netIncome"], 40.0)
        self.assertEqual(estimate["estimatedQ4Shares"], 100.0)
        self.assertEqual(estimate["q123DilutedShares"], [100.0, 100.0, 100.0])

    def test_unflagged_value_stays(self):
        document = synthetic_document(flag=False)
        self.assertEqual(grade.move_share_estimates(document), 0)
        self.assertEqual(document["quarterly"]["values"]["epsDiluted"][3], 0.4)
        self.assertNotIn("q4EpsEstimates", document)

    def test_value_equal_to_the_derivation_stays(self):
        document = synthetic_document(q4_eps=0.4, annual_eps=1.3)
        self.assertEqual(grade.move_share_estimates(document), 0)
        self.assertEqual(document["quarterly"]["values"]["epsDiluted"][3], 0.4)

    def test_sec_fact_value_stays(self):
        trace = {"2020-12-31": {"epsDiluted": {"concept": "EarningsPerShareDiluted"}}}
        document = synthetic_document(trace=trace)
        self.assertEqual(grade.move_share_estimates(document), 0)
        self.assertEqual(document["quarterly"]["values"]["epsDiluted"][3], 0.4)

    def test_value_that_is_not_the_method_b_estimate_stays(self):
        document = synthetic_document(q4_eps=0.5)
        self.assertEqual(grade.move_share_estimates(document), 0)
        self.assertEqual(document["quarterly"]["values"]["epsDiluted"][3], 0.5)


class GradeQ4EpsTests(unittest.TestCase):
    def test_sec_fact_is_A_and_derived_is_Y(self):
        trace = {"2020-12-31": {"epsDiluted": {"concept": "EarningsPerShareDiluted"}}}
        document = synthetic_document(flag=False, derived=[None, None, None, None], trace=trace)
        self.assertEqual(grade.grade_q4_eps(document), 1)
        self.assertEqual(document["quarterly"]["epsQuality"][3], {"epsDiluted": "A"})

    def test_derived_value_without_sec_fact_is_Y_per_field(self):
        document = synthetic_document(flag=False)
        self.assertEqual(grade.grade_q4_eps(document), 1)
        self.assertEqual(document["quarterly"]["epsQuality"][3], {"epsDiluted": "Y"})

    def test_undated_value_is_left_ungraded(self):
        document = synthetic_document(flag=False, derived=[None, None, None, None])
        self.assertEqual(grade.grade_q4_eps(document), 0)
        self.assertNotIn("epsQuality", document["quarterly"])

    def test_non_q4_and_null_values_get_no_grade(self):
        document = synthetic_document(flag=False)
        grade.grade_q4_eps(document)
        for index in (0, 1, 2):
            self.assertIsNone(document["quarterly"]["epsQuality"][index])

    def test_existing_epsQuality_is_never_overwritten(self):
        document = synthetic_document(flag=False)
        document["quarterly"]["epsQuality"] = [None, None, None, {"epsDiluted": "C"}]
        before = copy.deepcopy(document["quarterly"]["epsQuality"])
        self.assertEqual(grade.grade_q4_eps(document), 0)
        self.assertEqual(document["quarterly"]["epsQuality"], before)


class ProcessFileTests(unittest.TestCase):
    def test_move_then_grade_leaves_moved_value_ungraded(self):
        document = synthetic_document(q4_eps=0.4)
        self.assertEqual(grade.move_share_estimates(document), 1)
        self.assertEqual(grade.grade_q4_eps(document), 0)
        self.assertNotIn("epsQuality", document["quarterly"])


if __name__ == "__main__":
    unittest.main()

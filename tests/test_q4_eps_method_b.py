"""Synthetic strict-subgroup validation and grade-C Method B quarantine tests."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import audit_q4_eps_backfill as audit  # noqa: E402


def synthetic_document():
    years = list(range(2018, 2025))
    annual = {
        "end": [f"{year}-12-31" for year in years],
        "fy": years,
        "src": ["sec"] * len(years),
        "values": {
            "netIncome": [100] * len(years),
            "epsDiluted": [0.1] * len(years),
            "dilutedShares": [1000] * len(years),
        },
    }
    quarter_ends, quarter_fy, quarter_fq = [], [], []
    incomes, eps, shares = [], [], []
    for year in years:
        q4_income, actual = 100, 0.1
        if year == 2021:
            q4_income, actual = 1, 0.0  # denominator floor makes this a 1% error
        elif year == 2022:
            actual = None  # a qualifying null is staged in C quarantine only
        elif year == 2023:
            shares_for_year = [1021, 1000, 1000, 1000]
            for fq, (month, day) in enumerate(((3, 31), (6, 30), (9, 30), (12, 31)), start=1):
                quarter_ends.append(f"{year}-{month:02d}-{day:02d}")
                quarter_fy.append(year)
                quarter_fq.append(fq)
                incomes.append((1, 2, 3, q4_income)[fq - 1])
                eps.append((0.001, 0.002, 0.003, None)[fq - 1])
                shares.append(shares_for_year[fq - 1])
            continue
        elif year == 2024:
            q4_income, actual = -10, -0.01  # negative Q4 income is outside the subgroup

        for fq, (month, day) in enumerate(((3, 31), (6, 30), (9, 30), (12, 31)), start=1):
            quarter_ends.append(f"{year}-{month:02d}-{day:02d}")
            quarter_fy.append(year)
            quarter_fq.append(fq)
            incomes.append((1, 2, 3, q4_income)[fq - 1])
            eps.append((0.001, 0.002, 0.003, actual)[fq - 1])
            shares.append(1000)

    return {
        "version": 1,
        "symbol": "SYNTH",
        "market": "US",
        "currency": "USD",
        "annual": annual,
        "quarterly": {
            "end": quarter_ends,
            "fy": quarter_fy,
            "fq": quarter_fq,
            "src": ["sec"] * len(quarter_ends),
            "values": {"netIncome": incomes, "epsDiluted": eps, "dilutedShares": shares},
        },
        "split": {"events": []},
        "quality": {"issues": []},
    }


class MethodBStrictSubgroupTests(unittest.TestCase):
    def test_strict_conditions_and_four_share_range(self):
        document = synthetic_document()
        q4_index = document["quarterly"]["end"].index("2020-12-31")
        observation, reason = audit.method_b_observation(document, q4_index)
        self.assertIsNone(reason)
        self.assertEqual(observation["q4Shares"], 1000)
        self.assertAlmostEqual(float(observation["estimate"]), 0.1)

        q4_2023 = document["quarterly"]["end"].index("2023-12-31")
        observation, reason = audit.method_b_observation(document, q4_2023)
        self.assertIsNone(observation)
        self.assertEqual(reason, "share_range_over_2pct")

        q4_2024 = document["quarterly"]["end"].index("2024-12-31")
        observation, reason = audit.method_b_observation(document, q4_2024)
        self.assertIsNone(observation)
        self.assertEqual(reason, "q4_income_not_positive")

    def test_floor_normalized_error_passes_and_estimate_stays_out_of_formal_eps(self):
        document = synthetic_document()
        q4_2021 = document["quarterly"]["end"].index("2021-12-31")
        counts, errors = audit.validate_method_b(document)
        self.assertEqual(counts["eligible_compared"], 4)
        self.assertAlmostEqual(float(errors[3]), 0.01)

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "SYNTH.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(document, handle)

            subgroup_errors, skipped = audit.method_b_subgroup_backtest(directory)
            report = audit.method_b_subgroup_report(subgroup_errors)
            item = report[audit.METHOD_B_GROUP]
            self.assertTrue(item["passed"])
            self.assertEqual(item["n"], 4)
            self.assertEqual(skipped["q4_income_not_positive"], 1)

            result = audit.apply_method_b(directory, report)
            self.assertEqual(result["quarantinedPeriods"], 1)
            self.assertEqual(result["fills"][0]["end"], "2022-12-31")
            self.assertEqual(result["fills"][0]["value"], 0.1)
            self.assertEqual(result["fills"][0]["grade"], "C")

            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            ends = saved["quarterly"]["end"]
            values = saved["quarterly"]["values"]["epsDiluted"]
            self.assertIsNone(values[ends.index("2022-12-31")])
            self.assertEqual(values[ends.index("2020-12-31")], 0.1)
            self.assertEqual(values[ends.index("2021-12-31")], 0.0)
            self.assertIsNone(values[ends.index("2023-12-31")])
            self.assertEqual(values[ends.index("2024-12-31")], -0.01)
            self.assertEqual(saved["quality"]["issues"], [])
            quarantine = os.path.join(directory, "q4-eps-method-b-quarantine-C.jsonl")
            with open(quarantine, encoding="utf-8") as handle:
                record = json.loads(handle.readline())
            self.assertEqual(record["grade"], "C")
            self.assertEqual(record["status"], "quarantined")


if __name__ == "__main__":
    unittest.main()

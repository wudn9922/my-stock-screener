"""Offline fiscal-period label and dry-run report regressions."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atlas" / "scripts"
FINANCIALS = ROOT / "docs" / "report" / "financials"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_financials as fin  # noqa: E402
import repair_fiscal_periods as repair  # noqa: E402


def stored_items(document, name):
    return repair.section_items(document, name)


def proposed(document):
    annual = stored_items(document, "annual")
    quarterly = stored_items(document, "quarterly")
    fallback = document.get("fyEndMonth")
    fin.assign_fiscal_labels(annual, quarterly, fallback_end_month=fallback)
    return annual, quarterly


def document(symbol):
    with (FINANCIALS / f"{symbol}.json").open(encoding="utf-8") as file:
        return json.load(file)


def labels(periods):
    return {item["end"]: (item.get("fy"), item.get("fq")) for item in periods}


def q4_eps_document(*, derived, q4_eps=2.5):
    ends = ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"]
    return {
        "annual": {"end": ["2023-12-31"], "fy": [2023], "src": ["sec"], "values": {
            "epsBasic": [4.0], "epsDiluted": [4.0], "netIncome": [400], "dilutedShares": [100],
        }},
        "quarterly": {
            "end": ends, "fy": [2023] * 4, "fq": [1, 2, 3, 4], "src": ["sec"] * 4,
            "derived": [None, None, None, 1 if derived else None],
            "values": {
                "epsBasic": [0.5, 0.5, 0.5, q4_eps],
                "epsDiluted": [0.5, 0.5, 0.5, q4_eps],
                "netIncome": [50, 50, 50, 250], "dilutedShares": [100] * 4,
            },
        },
    }


class FiscalPeriodRepairTests(unittest.TestCase):
    def test_tdw_march_to_december_change_uses_each_annual_end(self):
        doc = document("TDW")
        annual, quarterly = proposed(doc)
        by_end = labels(quarterly)
        # The old dominant March month mislabeled calendar quarters after the transition.
        self.assertEqual(by_end["2018-03-31"], (2018, 1))
        self.assertEqual(by_end["2018-06-30"], (2018, 2))
        self.assertEqual(by_end["2018-09-30"], (2018, 3))
        self.assertEqual(by_end["2018-12-31"], (2018, 4))
        self.assertEqual({item["end"] for item in annual if item["fy"] == 2018}, {"2018-12-31"})

    def test_nmtc_september_annual_owns_quarter_labels(self):
        doc = document("NMTC")
        _annual, quarterly = proposed(doc)
        by_end = labels(quarterly)
        self.assertEqual(by_end["2022-12-31"], (2023, 1))
        self.assertEqual(by_end["2023-03-31"], (2023, 2))
        self.assertEqual(by_end["2023-06-30"], (2023, 3))
        self.assertEqual(by_end["2023-09-30"], (2023, 4))
        self.assertEqual(by_end["2023-12-31"], (2024, 1))

    def test_bnc_quarters_after_latest_annual_continue_from_its_month(self):
        doc = document("BNC")
        _annual, quarterly = proposed(doc)
        by_end = labels(quarterly)
        self.assertEqual(by_end["2025-01-31"], (2025, 1))
        self.assertEqual(by_end["2025-10-31"], (2025, 3))
        self.assertEqual(by_end["2026-01-31"], (2026, 1))

    def test_cost_52_53_week_periods_use_effective_months(self):
        doc = document("COST")
        annual, quarterly = proposed(doc)
        q_by_end = labels(quarterly)
        self.assertEqual(q_by_end["2023-09-03"], (2023, 4))
        self.assertEqual(q_by_end["2024-09-01"], (2024, 4))
        self.assertEqual(fin.effective("2023-09-03").month, 8)
        self.assertIn({"end": "2023-09-03", "fy": 2023},
                      [{"end": item["end"], "fy": item["fy"]} for item in annual])

    def test_calendar_year_filer_has_no_label_changes(self):
        doc = document("AAPL")
        before_annual = stored_items(doc, "annual")
        before_quarterly = stored_items(doc, "quarterly")
        annual, quarterly = proposed(doc)
        self.assertEqual(labels(before_annual), labels(annual))
        self.assertEqual(labels(before_quarterly), labels(quarterly))

    def test_unflagged_file_has_no_existing_quarter_label_targets(self):
        doc = document("AAPL")
        annual = stored_items(doc, "annual")
        quarterly = stored_items(doc, "quarterly")
        targets = fin.fiscal_label_eligible_ends(annual, quarterly)
        self.assertEqual(targets, set())
        before = labels(quarterly)
        fin.assign_fiscal_labels(annual, quarterly, eligible_quarter_ends=targets)
        self.assertEqual(labels(quarterly), before)

    def test_no_annual_periods_keep_the_callers_current_behavior(self):
        quarter = [{"end": "2023-01-07", "fy": None, "fq": None, "values": {}}]
        fin.assign_fiscal_labels([], quarter)
        self.assertEqual((quarter[0]["fy"], quarter[0]["fq"]), (2022, None))

        quarter = [{"end": "2023-01-07", "fy": None, "fq": None, "values": {}}]
        fin.assign_fiscal_labels([], quarter, fallback_end_month=12)
        self.assertEqual((quarter[0]["fy"], quarter[0]["fq"]), (2022, 4))

    def test_quarter_outside_owner_window_continues_from_nearest_prior_annual(self):
        annual = [
            {"end": "2016-02-28", "fy": None, "values": {}},
            {"end": "2018-04-30", "fy": None, "values": {}},
        ]
        quarter = [{"end": "2017-03-31", "fy": None, "fq": None, "values": {}}]
        fin.assign_fiscal_labels(annual, quarter)
        self.assertEqual((quarter[0]["fy"], quarter[0]["fq"]), (2018, 1))

    def test_owner_month_distance_is_bounded_at_first_quarter(self):
        annual = [{"end": "2025-01-31", "fy": None, "values": {}}]
        quarter = [{"end": "2024-01-31", "fy": None, "fq": None, "values": {}}]
        fin.assign_fiscal_labels(annual, quarter)
        self.assertEqual((quarter[0]["fy"], quarter[0]["fq"]), (2025, 1))

    def test_q4_eps_only_nulls_derived_values_when_new_labels_break_the_group(self):
        doc = q4_eps_document(derived=True)
        annual = stored_items(doc, "annual")
        quarterly = stored_items(doc, "quarterly")
        derived = repair.infer_existing_derived_q4_eps(doc, annual, quarterly)
        self.assertEqual(derived, {
            ("2023-12-31", "epsBasic"): 2.5,
            ("2023-12-31", "epsDiluted"): 2.5,
        })
        new_quarterly = copy.deepcopy(quarterly)
        new_quarterly[-1]["fy"], new_quarterly[-1]["fq"] = 2024, 1
        changes = repair.q4_eps_differences(annual, quarterly, annual, new_quarterly, derived)
        self.assertEqual([row["action"] for row in changes], ["nulled", "nulled"])

    def test_q4_eps_report_does_not_null_a_non_derived_value(self):
        doc = q4_eps_document(derived=False)
        annual = stored_items(doc, "annual")
        quarterly = stored_items(doc, "quarterly")
        self.assertEqual(repair.infer_existing_derived_q4_eps(doc, annual, quarterly), {})
        new_quarterly = copy.deepcopy(quarterly)
        new_quarterly[-1]["fy"], new_quarterly[-1]["fq"] = 2024, 1
        changes = repair.q4_eps_differences(annual, quarterly, annual, new_quarterly, {})
        self.assertEqual(changes, [])
        self.assertEqual(quarterly[-1]["values"]["epsDiluted"], 2.5)

    def test_q4_eps_report_marks_a_new_fill_only_when_new_labels_allow_it(self):
        doc = q4_eps_document(derived=False, q4_eps=None)
        annual = stored_items(doc, "annual")
        old_quarterly = stored_items(doc, "quarterly")
        new_quarterly = copy.deepcopy(old_quarterly)
        old_quarterly[-1]["fq"] = 3
        new_quarterly[-1]["fq"] = 4
        changes = repair.q4_eps_differences(annual, old_quarterly, annual, new_quarterly, {})
        self.assertEqual({row["action"] for row in changes}, {"newly-derived"})
        self.assertEqual(len(changes), 2)
        self.assertTrue(all(row["new"] == 2.5 for row in changes))

    def test_write_report_is_byte_stable(self):
        report = {"version": 1, "counts": {"quarterlyRowsChanged": 2}, "files": []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            repair.write_report(path, report)
            first = path.read_bytes()
            repair.write_report(path, copy.deepcopy(report))
            self.assertEqual(path.read_bytes(), first)


class FixpointConvergenceTests(unittest.TestCase):
    """Convergence logic and the pass cap for --fixpoint. Nothing here writes to docs/report/financials/."""

    def test_converge_stops_at_the_first_pass_that_makes_no_change(self):
        calls = []

        def run_pass(number):
            calls.append(number)
            return [] if number == 1 else ["unexpected"]

        results, converged = repair.converge(run_pass)
        self.assertTrue(converged)
        self.assertEqual(calls, [1])
        self.assertEqual(results, [[]])

    def test_converge_runs_changing_passes_until_a_quiet_pass(self):
        changes = {1: ["a", "b"], 2: ["c"], 3: []}
        results, converged = repair.converge(lambda number: changes[number])
        self.assertTrue(converged)
        self.assertEqual(results, [["a", "b"], ["c"], []])

    def test_converge_is_capped_at_five_passes_and_reports_no_convergence(self):
        calls = []

        def run_pass(number):
            calls.append(number)
            return ["still changing"]

        results, converged = repair.converge(run_pass)
        self.assertEqual(repair.FIXPOINT_MAX_PASSES, 5)
        self.assertFalse(converged)
        self.assertEqual(calls, [1, 2, 3, 4, 5])
        self.assertEqual(len(results), 5)

    def test_converge_honours_a_smaller_cap(self):
        results, converged = repair.converge(lambda number: ["x"], max_passes=2)
        self.assertFalse(converged)
        self.assertEqual(len(results), 2)

    def test_converge_rejects_a_cap_below_one(self):
        with self.assertRaises(ValueError):
            repair.converge(lambda number: [], max_passes=0)


def chain_document(symbol):
    """Five rows whose labels need two changing passes (the third pass makes no change)."""
    rows = [("2022-06-30", 2023, 1), ("2022-09-30", 2023, 2), ("2023-03-31", 2023, 4),
            ("2023-09-30", 2024, 2), ("2023-12-31", 2023, 3)]
    return {
        "version": 4, "symbol": symbol, "market": "US", "currency": "USD", "foreignReporter": False,
        "fyEndMonth": 12,
        "annual": {"end": ["2023-12-31"], "fy": [2023], "src": ["sec"],
                   "values": {"netIncome": [400], "dilutedShares": [100]}},
        "quarterly": {
            "end": [end for end, _, _ in rows], "fy": [fy for _, fy, _ in rows],
            "fq": [fq for _, _, fq in rows], "src": ["sec"] * len(rows),
            "values": {"netIncome": [50] * len(rows), "dilutedShares": [100] * len(rows)},
        },
    }


def q4_eps_fixture(symbol):
    """A flagged file whose derived Q4 EPS can no longer be derived after one pass."""
    values = {"epsDiluted": [0.5, 0.5, 0.5, 2.5], "epsBasic": [0.5, 0.5, 0.5, 2.5],
              "netIncome": [50, 50, 50, 250], "dilutedShares": [100, 100, 100, 100]}
    return {
        "version": 4, "symbol": symbol, "market": "US", "currency": "USD", "foreignReporter": False,
        "fyEndMonth": 12,
        "annual": {"end": ["2023-12-31"], "fy": [2023], "src": ["sec"],
                   "values": {"epsDiluted": [4.0], "epsBasic": [4.0], "netIncome": [400], "dilutedShares": [100]}},
        "quarterly": {"end": ["2023-03-31", "2023-06-30", "2023-09-30", "2024-01-31"],
                      "fy": [2023, 2023, 2023, 2023], "fq": [1, 2, 3, 4], "src": ["sec"] * 4,
                      "derived": [None, None, None, 1], "values": values},
    }


class FixpointDriverTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name) / "financials"
        self.directory.mkdir()

    def write(self, name, document):
        (self.directory / name).write_text(
            json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.directory.glob("*.json")}

    def test_chain_converges_in_three_passes_with_the_last_pass_making_no_change(self):
        self.write("CHAIN.json", chain_document("CHAIN"))
        before = self.snapshot()

        report = repair.run_fixpoint(self.directory)

        self.assertTrue(report["converged"])
        self.assertEqual(report["passesRun"], 3)
        self.assertEqual([entry["filesChanged"] for entry in report["passes"]], [1, 1, 0])
        self.assertEqual([entry["quarterlyLabelRows"] for entry in report["passes"]], [4, 1, 0])
        self.assertEqual(report["cumulativeVsStartingState"]["quarterlyLabelRows"], 5)
        self.assertEqual(self.snapshot(), before)

    def test_cap_stops_a_chain_that_has_not_settled(self):
        self.write("CHAIN.json", chain_document("CHAIN"))

        report = repair.run_fixpoint(self.directory, max_passes=2)

        self.assertFalse(report["converged"])
        self.assertEqual(report["passesRun"], 2)
        self.assertEqual([entry["filesChanged"] for entry in report["passes"]], [1, 1])

    def test_default_cap_is_five_and_a_single_change_converges_in_two_passes(self):
        self.write("CHAIN.json", chain_document("CHAIN"))
        self.write("CLEAN.json", {**chain_document("CLEAN"), "quarterly": {
            "end": ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"], "fy": [2023] * 4, "fq": [1, 2, 3, 4],
            "src": ["sec"] * 4, "values": {"netIncome": [50] * 4, "dilutedShares": [100] * 4}}})
        report = repair.run_fixpoint(self.directory)
        self.assertEqual(report["scope"]["maxPasses"], 5)
        self.assertTrue(report["converged"])

        other = Path(self._tmp.name) / "single"
        other.mkdir()
        (other / "FLAG.json").write_text(
            json.dumps(q4_eps_fixture("FLAG"), ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        single = repair.run_fixpoint(other)
        self.assertTrue(single["converged"])
        self.assertEqual([entry["filesChanged"] for entry in single["passes"]], [1, 0])
        self.assertEqual(single["checks"]["nonDerivedEpsChanges"], 0)
        self.assertEqual(single["checks"]["guardedFieldChanges"], 0)
        nulled = [row for row in single["epsActionsByPass"] if row["action"] == "nulled"]
        self.assertEqual(sorted(row["field"] for row in nulled), ["epsBasic", "epsDiluted"])

    def test_excluded_symbol_is_planned_and_reported_but_never_changed(self):
        self.write("UA.json", q4_eps_fixture("UA"))
        before = self.snapshot()

        report = repair.run_fixpoint(self.directory)

        self.assertEqual(report["passesRun"], 1)
        self.assertEqual(report["excluded"]["bySymbol"]["UA"]["quarterlyLabelRows"], 1)
        self.assertEqual(report["cumulativeVsStartingState"]["files"], 0)
        self.assertEqual(report["checks"]["excludedFilesChanged"], 0)
        self.assertEqual(self.snapshot(), before)

    def test_fixpoint_writes_nothing_under_the_financials_directory(self):
        self.write("CHAIN.json", chain_document("CHAIN"))
        self.write("FLAG.json", q4_eps_fixture("FLAG"))
        before = self.snapshot()
        names_before = sorted(path.name for path in self.directory.iterdir())

        repair.run_fixpoint(self.directory)

        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), names_before)


if __name__ == "__main__":
    unittest.main()

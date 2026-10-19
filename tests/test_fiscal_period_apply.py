"""Synthetic-document tests for repair_fiscal_periods.py --apply."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atlas" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import detect_fiscal_period_mismatch as detector  # noqa: E402
import repair_fiscal_periods as repair  # noqa: E402


Q4_END = "2024-01-31"


def synthetic_document(symbol, *, derived=True, q4_eps=2.5, extra=None):
    """A US file whose fq 4 row (dated 31 January 2024) belongs to the 2024 fiscal year.

    The annual end is 31 December 2023, so the detector flags the Q4 row and the repair relabels
    it as fiscal 2024 Q1. Its EPS was derived from the annual figure less the first three quarters.
    """
    values = {
        "epsDiluted": [0.5, 0.5, 0.5, q4_eps],
        "epsBasic": [0.5, 0.5, 0.5, q4_eps],
        "netIncome": [50, 50, 50, 250],
        "dilutedShares": [100, 100, 100, 100],
    }
    document = {
        "version": 4, "symbol": symbol, "market": "US", "currency": "USD", "foreignReporter": False,
        "fyEndMonth": 12,
        "annual": {
            "end": ["2023-12-31"], "fy": [2023], "src": ["sec"],
            "values": {"epsDiluted": [4.0], "epsBasic": [4.0], "netIncome": [400], "dilutedShares": [100]},
        },
        "quarterly": {
            "end": ["2023-03-31", "2023-06-30", "2023-09-30", Q4_END],
            "fy": [2023, 2023, 2023, 2023],
            "fq": [1, 2, 3, 4],
            "src": ["sec"] * 4,
            "derived": [None, None, None, 1 if derived else None],
            "values": values,
        },
    }
    if extra:
        document.update(extra)
    return document


def clean_document(symbol):
    """A calendar-year US file whose labels already agree with its annual end: nothing to repair."""
    return {
        "version": 4, "symbol": symbol, "market": "US", "currency": "USD", "foreignReporter": False,
        "fyEndMonth": 12,
        "annual": {"end": ["2023-12-31"], "fy": [2023], "src": ["sec"], "values": {"revenue": [1000]}},
        "quarterly": {
            "end": ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"],
            "fy": [2023, 2023, 2023, 2023], "fq": [1, 2, 3, 4], "src": ["sec"] * 4,
            "values": {"revenue": [240, 250, 260, 250]},
        },
    }


def write_document(directory, name, document):
    (directory / name).write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
                                  encoding="utf-8")


def read_document(directory, name):
    return json.loads((directory / name).read_text(encoding="utf-8"))


def dry_run_report(directory):
    """The dry-run file records for a folder, built with the same per-file planner the dry run uses."""
    flagged = {entry["file"]: entry for entry in detector.build_report(directory)["flaggedFiles"]}
    files = []
    for path in sorted(directory.glob("*.json")):
        plan = repair.plan_path(path, flagged)
        if plan is not None:
            files.append(plan["record"])
    return {"files": files}


def snapshot(directory):
    return {path.name: path.read_bytes() for path in directory.glob("*.json")}


class ApplyTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name) / "financials"
        self.directory.mkdir()

    def write_standard_folder(self):
        write_document(self.directory, "SYNQ.json", synthetic_document("SYNQ"))
        write_document(self.directory, "SYNCLEAN.json", clean_document("SYNCLEAN"))
        write_document(self.directory, "SYNND.json", synthetic_document("SYNND", derived=False))
        # Same shape as SYNQ, but the symbol is on the exclusion list.
        write_document(self.directory, "UA.json", synthetic_document("UA"))

    def apply(self, report=None):
        report = report if report is not None else dry_run_report(self.directory)
        with mock.patch("socket.socket", side_effect=AssertionError("network access during --apply")):
            return repair.apply_repairs(self.directory, report)


class FlaggedAndUnflaggedFilesTests(ApplyTestCase):
    def test_flagged_file_is_changed_to_the_dry_run_labels_and_nulls_its_underivable_eps(self):
        self.write_standard_folder()
        before = read_document(self.directory, "SYNQ.json")
        self.apply()
        after = read_document(self.directory, "SYNQ.json")

        expected = copy.deepcopy(before)
        expected["quarterly"]["fy"][3] = 2024
        expected["quarterly"]["fq"][3] = 1
        expected["quarterly"]["values"]["epsDiluted"][3] = None
        expected["quarterly"]["values"]["epsBasic"][3] = None
        self.assertEqual(after, expected)

    def test_unflagged_file_is_not_changed(self):
        self.write_standard_folder()
        before = snapshot(self.directory)
        summary = self.apply()

        self.assertEqual(snapshot(self.directory)["SYNCLEAN.json"], before["SYNCLEAN.json"])
        self.assertNotIn("SYNCLEAN", [row["symbol"] for row in summary["written"]])

    def test_excluded_symbol_is_not_written_and_is_reported_separately(self):
        self.write_standard_folder()
        before = snapshot(self.directory)
        summary = self.apply()

        self.assertEqual(snapshot(self.directory)["UA.json"], before["UA.json"])
        self.assertEqual([row["symbol"] for row in summary["excluded"]], ["UA"])
        self.assertEqual(summary["excluded"][0]["quarterlyLabelRows"], 1)
        self.assertNotIn("UA", [row["symbol"] for row in summary["written"]])

    def test_non_derived_eps_value_is_not_changed_while_its_label_is(self):
        self.write_standard_folder()
        self.apply()
        after = read_document(self.directory, "SYNND.json")

        self.assertEqual((after["quarterly"]["fy"][3], after["quarterly"]["fq"][3]), (2024, 1))
        self.assertEqual(after["quarterly"]["values"]["epsDiluted"][3], 2.5)
        self.assertEqual(after["quarterly"]["values"]["epsBasic"][3], 2.5)

    def test_apply_writes_only_the_files_it_changes(self):
        self.write_standard_folder()
        names_before = sorted(path.name for path in self.directory.iterdir())
        self.apply()
        names_after = sorted(path.name for path in self.directory.iterdir())

        self.assertEqual(names_before, names_after)


class ProtectedMetadataTests(ApplyTestCase):
    def test_eps_quality_and_q4_eps_metadata_are_never_changed(self):
        protected = {
            "secQ4EpsFacts": {Q4_END: {"epsDiluted": 2.5}},
            "epsOverrides": {Q4_END: {"epsDiluted": 2.5}},
            "q4EpsEstimates": {Q4_END: {"epsBasic": 2.5}},
        }
        document = synthetic_document("SYNP", extra=protected)
        document["quarterly"]["epsQuality"] = [None, None, None, {"epsDiluted": "Y", "epsBasic": "Y"}]
        write_document(self.directory, "SYNP.json", document)
        before = read_document(self.directory, "SYNP.json")

        self.apply()
        after = read_document(self.directory, "SYNP.json")

        for name in ("epsQuality",):
            self.assertEqual(after["quarterly"][name], before["quarterly"][name])
        for name in ("secQ4EpsFacts", "epsOverrides", "q4EpsEstimates"):
            self.assertEqual(after[name], before[name])
        # The label still moves; the EPS guarded by secQ4EpsFacts and epsOverrides stays put.
        self.assertEqual((after["quarterly"]["fy"][3], after["quarterly"]["fq"][3]), (2024, 1))
        self.assertEqual(after["quarterly"]["values"]["epsDiluted"][3], 2.5)


class SafetyTests(ApplyTestCase):
    def test_second_apply_writes_nothing_and_leaves_identical_bytes(self):
        self.write_standard_folder()
        report = dry_run_report(self.directory)  # the committed dry run is computed once, before any apply
        self.apply(report)
        first = snapshot(self.directory)

        summary = self.apply(report)

        self.assertEqual(snapshot(self.directory), first)
        self.assertEqual(summary["written"], [])
        self.assertIn("SYNQ", summary["alreadyApplied"])

    def test_mismatched_dry_run_stops_before_any_file_is_written(self):
        self.write_standard_folder()
        write_document(self.directory, "SYNQ2.json", synthetic_document("SYNQ2"))
        before = snapshot(self.directory)
        report = dry_run_report(self.directory)
        for entry in report["files"]:
            if entry["symbol"] == "SYNQ2":
                entry["quarterlyLabelChanges"] = []

        with self.assertRaises(repair.ApplyError):
            self.apply(report)
        self.assertEqual(snapshot(self.directory), before)

    def test_failed_write_leaves_the_untouched_file_intact(self):
        self.write_standard_folder()
        write_document(self.directory, "SYNQ2.json", synthetic_document("SYNQ2"))
        before = snapshot(self.directory)
        real_write = repair.write_atomic
        calls = []

        def failing_second_write(path, value):
            calls.append(path)
            if len(calls) == 2:
                raise RuntimeError("interrupted")
            return real_write(path, value)

        with mock.patch.object(repair, "write_atomic", side_effect=failing_second_write):
            with self.assertRaises(RuntimeError):
                self.apply()

        after = snapshot(self.directory)
        written = [name for name in before if after[name] != before[name]]
        self.assertEqual(len(written), 1)
        for name in before:
            if name not in written:
                self.assertEqual(after[name], before[name])
        json.loads(after[written[0]])  # the file that was written is complete JSON

    def test_verifier_rejects_a_change_outside_the_dry_run(self):
        document = synthetic_document("SYNQ")
        diff = frozenset({
            ("quarterly", Q4_END, "fy", "2023", "2024"),
            ("quarterly", Q4_END, "fq", "4", "1"),
        })
        updated = repair.apply_diff(document, diff)
        updated["quarterly"]["values"]["netIncome"][0] = 999
        with self.assertRaises(repair.ApplyError):
            repair.verify_written_change(document, updated, diff)


if __name__ == "__main__":
    unittest.main()

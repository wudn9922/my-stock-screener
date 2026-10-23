"""Reviewed-manifest apply and independent whole-tree proof regressions."""
import copy
import json
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "atlas/scripts"))
import apply_sec_restatements as apply
import build_growth as growth
import build_financials as fin
import report_sec_restatements as preview
import seed_financials_sec as sec
from sec_restatements import _merge_filed_row

NOW = datetime(2025, 3, 1, tzinfo=timezone.utc)


class ReviewedApplyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.before, self.after, self.cache = [self.root / name for name in ("before", "after", "cache")]
        self.cache.mkdir()
        payload = json.loads((ROOT / "tests/fixtures/sec-restatements/synthetic-quarterly.json").read_text())
        original = copy.deepcopy(payload)
        for concept in original["facts"]["us-gaap"].values():
            for unit, rows in concept["units"].items():
                concept["units"][unit] = [r for r in rows if r["accn"] != "restated-2024"]
        extracted = sec.extract_periods(original)
        extracted.skip = None
        self.old, _ = sec.merge_into_document(None, extracted, "TEST", NOW)
        self.old["quality"]["issues"].append("keep-existing-flag")
        self.new, reason = preview.preview_document(self.old, payload, NOW, True)
        self.assertIsNone(reason)
        raw = apply.encode(self.old)
        self.entry = {"symbol": "TEST", "cik": str(payload["cik"]).zfill(10), "sha256": apply.digest(raw)}
        preview.record_comparison(self.entry, self.old, self.new)
        self.report = {"files": [self.entry]}
        for root in (self.before, self.after):
            (root / "financials").mkdir(parents=True)
            (root / "financials/TEST.json").write_bytes(raw)
            (root / "q4-8k").mkdir()
            (root / "q4-8k/answers.json").write_text('{"answer": 0.36}\n')
            for name in ("q4-eps-missing-classification.json", "q4-eps-acceptance-ledger.json"):
                (root / name).write_text('{"unchanged":true}\n')
            (root / "universe.json").write_text('{"markets":{}}\n')
            (root / "growth.json").write_bytes(apply.encode(growth.build_document([("TEST", self.old)], NOW)))
        cache = self.cache / f"{self.entry['cik']}.json"
        cache.write_bytes(apply.encode(payload))
        cache.with_suffix(".receipt.json").write_bytes(apply.encode({"cik": self.entry["cik"],
            "sha256": apply.digest(cache.read_bytes()), "sourceUrl": "https://data.sec.gov/fixture", "fetchedAt": NOW.isoformat()}))

    def test_reviewed_manifest_is_pinned_and_has_the_exact_approved_counts(self):
        report = apply.approved_report(ROOT / "docs/report/sec-restated-comparatives.json")
        self.assertEqual(report["summary"]["fields"], 2151)
        self.assertEqual(report["summary"]["switchedPeriods"], 1172)
        altered = self.root / "report.json"
        altered.write_bytes((ROOT / "docs/report/sec-restated-comparatives.json").read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            apply.approved_report(altered)

    def test_preflight_does_not_write_and_apply_matches_manifest_and_rebuilds_growth(self):
        before = apply.hashes(self.after)
        check = apply.apply_batch(self.report, self.after, self.cache, NOW)
        self.assertFalse(check["written"])
        self.assertEqual(apply.hashes(self.after), before)
        receipt = apply.apply_batch(self.report, self.after, self.cache, NOW, write=True)
        self.assertTrue(receipt["written"])
        self.assertEqual(receipt["changedValues"], len(self.entry["changes"]))
        proof = apply.verify_tree(self.before, self.after, self.report)
        self.assertTrue(proof["valueDiffEqualsDryRun"])
        self.assertTrue(proof["protectedFilesUnchanged"])
        written = apply.read(self.after / "financials/TEST.json")
        self.assertIn("keep-existing-flag", written["quality"]["issues"])
        self.assertIn("mixed-basis", written["quality"]["issues"])
        changed = apply.cells(self.entry["changes"])
        revisions = written["quality"]["revisions"]
        for row in changed:
            self.assertTrue(any(r["kind"] == "restated" and
                tuple(r.get(k) for k in apply.CELL_KEYS[1:]) == row[1:] for r in revisions))
        expected = growth.build_document([("TEST", written)], NOW)
        self.assertEqual(apply.read(self.after / "growth.json")["rows"], expected["rows"])
        with self.assertRaisesRegex(ValueError, "changed since reviewed"):
            apply.apply_batch(self.report, self.after, self.cache, NOW, write=True)

    def test_baseline_drift_rejects_entire_batch_before_any_write(self):
        entry = copy.deepcopy(self.entry)
        entry["symbol"], entry["sha256"] = "OTHER", "stale"
        (self.after / "financials/OTHER.json").write_bytes(apply.encode({**self.old, "symbol": "OTHER"}))
        before = apply.hashes(self.after)
        with self.assertRaisesRegex(ValueError, "changed since reviewed"):
            apply.apply_batch({"files": [self.entry, entry]}, self.after, self.cache, NOW, write=True)
        self.assertEqual(apply.hashes(self.after), before)

    def test_new_source_numeric_diff_is_not_silently_added_to_approved_changes(self):
        modified = copy.deepcopy(self.new)
        modified["annual"]["values"]["revenue"][0] += 1
        before = apply.hashes(self.after)
        with mock.patch.object(preview, "preview_document", return_value=(modified, None)):
            with self.assertRaisesRegex(ValueError, "differs from reviewed"):
                apply.apply_batch(self.report, self.after, self.cache, NOW, write=True)
        self.assertEqual(apply.hashes(self.after), before)

    def test_tampered_source_receipt_fails_closed(self):
        path = self.cache / f"{self.entry['cik']}.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "receipt mismatch"):
            apply.apply_batch(self.report, self.after, self.cache, NOW, write=True)

    def test_atomic_batch_rolls_back_on_growth_write_failure(self):
        before = apply.hashes(self.after)
        atomic = apply.atomic
        failed = False
        def fail_once(path, raw):
            nonlocal failed
            if pathlib.Path(path).name == "growth.json" and not failed:
                failed = True
                raise OSError("disk failure")
            atomic(path, raw)
        rebuilt = apply.read(self.after / "growth.json")
        with mock.patch.object(apply, "atomic", side_effect=fail_once), mock.patch.object(
                apply, "rebuild_growth", return_value=(rebuilt, [{"symbol": "TEST"}])):
            with self.assertRaisesRegex(OSError, "disk failure"):
                apply.apply_batch(self.report, self.after, self.cache, NOW, write=True)
        self.assertTrue(failed)
        self.assertEqual(apply.hashes(self.after), before)

    def test_independent_verifier_rejects_an_extra_value_change(self):
        altered = copy.deepcopy(self.new)
        altered["quarterly"]["values"]["revenue"][0] += 1
        (self.after / "financials/TEST.json").write_bytes(apply.encode(altered))
        with self.assertRaisesRegex(ValueError, "differs from reviewed"):
            apply.verify_tree(self.before, self.after, self.report)

    def test_extra_numeric_cell_beyond_the_period_columns_is_rejected(self):
        altered = copy.deepcopy(self.new)
        altered["annual"]["values"]["revenue"].append(1234)
        with self.assertRaisesRegex(ValueError, "unaligned financial value column"):
            apply.validate_candidate(self.old, altered, self.entry)

    def test_independent_verifier_rejects_quality_flag_loss(self):
        altered = copy.deepcopy(self.new)
        altered["quality"]["issues"].remove("keep-existing-flag")
        (self.after / "financials/TEST.json").write_bytes(apply.encode(altered))
        with self.assertRaisesRegex(ValueError, "flag removed"):
            apply.verify_tree(self.before, self.after, self.report)

    def test_independent_verifier_rejects_unrelated_growth_value_change(self):
        (self.after / "financials/TEST.json").write_bytes(apply.encode(self.new))
        output = apply.read(self.after / "growth.json")
        output["rows"].append(["UNAPPROVED"])
        (self.after / "growth.json").write_bytes(apply.encode(output))
        with self.assertRaisesRegex(ValueError, "independent recomputation"):
            apply.verify_tree(self.before, self.after, self.report)

    def test_independent_verifier_rejects_modified_q4_answer_or_ledgers(self):
        (self.after / "financials/TEST.json").write_bytes(apply.encode(self.new))
        for name in ("q4-8k/answers.json", "q4-eps-missing-classification.json", "q4-eps-acceptance-ledger.json"):
            with self.subTest(path=name):
                path = self.after / name
                old = path.read_bytes()
                path.write_text('{"modified":true}')
                with self.assertRaisesRegex(ValueError, "answer set / classification / acceptance ledger"):
                    apply.verify_tree(self.before, self.after, self.report)
                path.write_bytes(old)

    def test_independent_verifier_rejects_period_relabel_or_eps_grade_change(self):
        for field in ("end", "fy", "fq", "epsQuality"):
            with self.subTest(field=field):
                altered = copy.deepcopy(self.new)
                altered["quarterly"][field] = [None]
                with self.assertRaisesRegex(ValueError, "period alignment / EPS grade"):
                    apply.preserve_invariants(self.old, altered)

    def test_quarter_sec_end_is_its_own_date_not_the_annual_basis_date(self):
        old = {"end": "2023-03-31", "values": {"revenue": 1}, "extra": {}}
        filed = {"end": "2023-04-01", "values": {"revenue": 2}}
        basis = {"accn": "restating", "filed": "2025-02-01", "end": "2023-12-31"}
        revisions = []
        result = _merge_filed_row(old, filed, basis, revisions, "quarterly")
        self.assertEqual(result["end"], "2023-03-31")
        self.assertEqual(result["extra"]["secEnd"], "2023-04-01")
        self.assertEqual(revisions[0]["secEnd"], "2023-04-01")

    def test_ordinary_refresh_retains_restated_history_and_persistent_quality_flags(self):
        payload = apply.read(self.cache / f"{self.entry['cik']}.json")
        for concept in payload["facts"]["us-gaap"].values():
            for unit, rows in concept["units"].items():
                concept["units"][unit] = [r for r in rows if not (
                    r["accn"] == "restated-2024" and r.get("start") and
                    80 <= abs(sec.days_between(r["end"], r["start"])) <= 120)]
        restated, _ = preview.preview_document(self.old, payload, NOW, True)
        restated["quarterly"]["secRestatementIssues"][0] = None  # Unaffected rows have null metadata slots.
        unchanged_quote = {"timeseries": {"result": [{"meta": {"type": ["annualTotalRevenue"]},
            "annualTotalRevenue": [{"asOfDate": "2023-12-31", "periodType": "12M", "currencyCode": "USD",
                                    "reportedValue": {"raw": restated["annual"]["values"]["revenue"][-1]}}]}]}}
        refreshed, _ = fin.update_document(restated, unchanged_quote, "TEST", NOW,
                                           fetch_splits=lambda: {"chart": {"result": [{"events": {}}]}})
        self.assertIsNotNone(refreshed)
        for flag in ("restated", "mixed-basis", "quarterly-as-reported"):
            self.assertIn(flag, restated["quality"]["issues"])
            self.assertIn(flag, refreshed["quality"]["issues"])
        for revision in restated["quality"]["revisions"]:
            self.assertIn(revision, refreshed["quality"]["revisions"])
        for frequency in apply.FREQUENCIES:
            self.assertEqual(restated[frequency].get("secEnd"), refreshed[frequency].get("secEnd"))

    def test_reviewed_sec_values_with_legacy_yahoo_label_cannot_be_reverted_by_yahoo(self):
        # Five reviewed annual rows still carry their historical Yahoo source label.
        for reviewed in (True, False):
            with self.subTest(reviewed=reviewed):
                old = fin.period("2024-12-31", {"revenue": 100, "netIncome": 10, "epsBasic": 1}, "yahoo",
                                 {"secFilingBasis": {"restated": reviewed}, "secEnd": "2024-12-29"})
                incoming = fin.period("2024-12-31", {"revenue": 3906, "netIncome": 54, "epsBasic": 5})
                result, _ = fin.merge_section([old], [incoming], "annual", "2026-10-23")
                self.assertEqual(result[0]["values"]["revenue"], 100 if reviewed else 3906)
                self.assertEqual(result[0]["values"]["epsBasic"], 1 if reviewed else 5)
                self.assertEqual(result[0]["extra"]["secEnd"], "2024-12-29")

    def test_metadata_only_annotation_does_not_change_values_or_labels(self):
        before = copy.deepcopy(self.old)
        raw_end = before["annual"]["end"][-1]
        before["annual"]["end"][-1] = (datetime.fromisoformat(raw_end).date().replace(day=30)).isoformat()
        payload = apply.read(self.cache / f"{self.entry['cik']}.json")
        annotated = apply.annotate_metadata_only(before, payload)
        self.assertEqual(annotated["annual"]["secEnd"][-1], raw_end)
        self.assertEqual(preview.document_changes(before, annotated)[0], [])
        apply.preserve_invariants(before, annotated)


if __name__ == "__main__":
    unittest.main()

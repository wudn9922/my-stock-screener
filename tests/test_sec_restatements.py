"""Offline fixture regressions for accession-coherent annual and quarterly comparatives."""
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
import build_financials as fin
import report_sec_restatements as report
import sec_ifrs
import seed_financials_sec as sec
from sec_restatements import BASIS, BLOCKED

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def fixture(name):
    return json.loads((ROOT / "tests/fixtures/sec-restatements" / name).read_text())


def original_only(payload):
    payload = copy.deepcopy(payload)
    for concept in payload["facts"]["us-gaap"].values():
        for unit, rows in concept["units"].items():
            concept["units"][unit] = [r for r in rows if r["accn"] != "restated-2024"]
    return payload


def row(result, frequency, end):
    return next(p for p in getattr(result, frequency) if p["end"] == end)


class RestatedComparativesTests(unittest.TestCase):
    def test_ad_latest_comparative_bypasses_narrow_revenue_guard_only_when_enabled(self):
        payload = fixture("ad-comparatives.json")
        before = copy.deepcopy(payload)
        legacy = sec.extract_periods(payload)
        self.assertEqual(legacy, sec.extract_periods(payload, prefer_latest_filing=False))
        self.assertEqual(row(legacy, "annual", "2023-12-31")["values"]["revenue"], 3_906_000_000)
        latest = sec.extract_periods(payload, prefer_latest_filing=True)
        annual = row(latest, "annual", "2023-12-31")
        self.assertEqual(annual["values"]["revenue"], 100_469_000)
        self.assertEqual(annual["extra"][BASIS]["accn"], "0000821130-26-000012")
        self.assertEqual(annual["extra"][BASIS]["filed"], "2026-02-20")
        self.assertEqual(payload, before)
        revision = next(r for r in latest.restatement_revisions if r["end"] == "2023-12-31")
        self.assertEqual(revision["old"], 3_906_000_000)
        self.assertEqual(revision["kind"], "restated")
        # fy=2025/fp=FY labels the later filing, not this comparative's fiscal year.
        self.assertEqual(annual["end"], "2023-12-31")

    def test_missing_balance_is_retained_and_flagged_instead_of_nulled(self):
        payload = fixture("synthetic-quarterly.json")
        selected = sec.extract_periods(payload, prefer_latest_filing=True)
        annual = row(selected, "annual", "2023-12-31")
        self.assertEqual(annual["values"]["revenue"], 2000)
        self.assertEqual(annual["values"]["netIncome"], 200)
        self.assertEqual(annual["values"]["totalAssets"], 9000)
        self.assertIn("mixed-basis", annual["extra"]["secRestatementIssues"])
        audit = next(r for r in selected.restatement_revisions
                     if r["kind"] == "basis-unrestated" and r["field"] == "totalAssets")
        self.assertEqual((audit["old"], audit["new"]), (9000, 9000))

    def test_unchanged_later_comparative_does_not_switch_or_normalize_anything(self):
        from sec_restatements import material_fields
        payload = fixture("synthetic-quarterly.json")
        original = sec.extract_periods(original_only(payload))
        original.skip = None
        previous, _ = sec.merge_into_document(None, original, "TEST", NOW)
        for concept in payload["facts"]["us-gaap"].values():
            for facts in concept["units"].values():
                for fact in facts:
                    if fact["accn"] == "restated-2024" and fact["end"].startswith("2023"):
                        fact["val"] *= 2 if fact["val"] != 100 else 1
        # Deliberately noncanonical dates and old flags must survive byte-for-byte.
        previous["annual"]["end"][-1] = "2024-01-06"
        previous["quality"]["issues"].append("existing-flag")
        proposed, reason = report.preview_document(previous, payload, NOW, True)
        self.assertIsNone(reason)
        self.assertEqual(proposed, previous)
        self.assertEqual(material_fields({"epsBasic": 1}, {"epsBasic": 1.02}), [])
        self.assertEqual(material_fields({"netIncome": -100}, {"netIncome": -102}), [])
        self.assertEqual(material_fields({"netIncome": 0}, {"netIncome": 1}), ["netIncome"])

    def test_q4_nulling_is_limited_to_switched_years_and_other_cells_are_unchanged(self):
        from sec_restatements import overlay_document
        years, quarters = [], []
        for year in (2022, 2023, 2024):
            years.append(fin.period(f"{year}-12-31", {"revenue": 100, "netIncome": 10, "totalAssets": 9000}, "sec"))
            for end in ("03-31", "06-30", "09-30", "12-31"):
                quarters.append(fin.period(f"{year}-{end}", {"netIncome": 2.5, "epsBasic": 1, "epsDiluted": 1}, "sec"))
        fin.assign_fiscal_labels(years, quarters, fallback_end_month=12)
        previous = {"version": fin.VERSION, "symbol": "TEST", "market": "US", "currency": "USD",
                    "annual": fin.write_section(years, {}, False), "quarterly": fin.write_section(quarters, {}, True)}
        candidate = copy.deepcopy(years[1])
        candidate["values"] = {"revenue": 50, "netIncome": 5}
        candidate["extra"][BASIS] = {"accn": "restated", "filed": "2025-02-20", "restated": True}
        proposed = overlay_document(previous, [candidate], [], [], NOW)
        self.assertEqual(proposed["annual"]["end"], previous["annual"]["end"])
        self.assertEqual(proposed["quarterly"]["end"], previous["quarterly"]["end"])
        self.assertEqual(proposed["annual"]["values"]["totalAssets"], [9000] * 3)
        for field in ("epsBasic", "epsDiluted"):
            self.assertEqual(proposed["quarterly"]["values"][field][3], 1)
            self.assertIsNone(proposed["quarterly"]["values"][field][7])
            self.assertEqual(proposed["quarterly"]["values"][field][11], 1)
        changes, q4 = report.document_changes(previous, proposed)
        self.assertTrue(all(c["end"].startswith("2023") for c in changes))
        self.assertEqual(len(q4), 2)
        self.assertTrue(all(c["field"] in ("epsBasic", "epsDiluted") for c in changes if c["new"] is None))

    def test_current_missing_field_is_preserved_with_period_mixed_basis_audit(self):
        payload = fixture("synthetic-quarterly.json")
        original = sec.extract_periods(original_only(payload))
        original.skip = None
        previous, _ = sec.merge_into_document(None, original, "TEST", NOW)
        previous["annual"]["values"]["totalAssets"][-1] = 123456
        proposed, reason = report.preview_document(previous, payload, NOW, True)
        self.assertIsNone(reason)
        self.assertEqual(proposed["annual"]["values"]["totalAssets"][-1], 123456)
        self.assertIn("mixed-basis", proposed["quality"]["issues"])
        self.assertIn("mixed-basis", proposed["annual"]["secRestatementIssues"][-1])
        entry = next(r for r in proposed["quality"]["revisions"] if r["kind"] == "basis-unrestated"
                     and r["period"] == "annual" and r["field"] == "totalAssets")
        self.assertEqual((entry["old"], entry["new"]), (123456, 123456))
        self.assertEqual(proposed["annual"]["end"], previous["annual"]["end"])

    def test_existing_split_adjustment_is_not_an_eps_restatement(self):
        payload = fixture("synthetic-quarterly.json")
        original = sec.extract_periods(original_only(payload))
        original.skip = None
        previous, _ = sec.merge_into_document(None, original, "TEST", NOW)
        for frequency in ("annual", "quarterly"):
            for field in ("epsBasic", "epsDiluted"):
                previous[frequency]["values"][field] = [value / 4 if value is not None else None
                                                       for value in previous[frequency]["values"][field]]
            if "dilutedShares" in previous[frequency]["values"]:
                previous[frequency]["values"]["dilutedShares"] = [value * 4 if value is not None else None
                    for value in previous[frequency]["values"]["dilutedShares"]]
        previous["split"] = {"adjusted": True, "events": [["2025-06-01", 4]]}
        for concept in payload["facts"]["us-gaap"].values():
            for facts in concept["units"].values():
                for fact in facts:
                    if fact["accn"] == "restated-2024" and fact["end"].startswith("2023") and fact["val"] != 100:
                        fact["val"] *= 2
        proposed, _ = report.preview_document(previous, payload, NOW, True)
        self.assertEqual(proposed, previous)

    def test_global_split_flag_does_not_rebase_a_row_already_in_reported_units(self):
        payload = fixture("synthetic-quarterly.json")
        original = sec.extract_periods(original_only(payload))
        original.skip = None
        previous, _ = sec.merge_into_document(None, original, "TEST", NOW)
        previous["split"] = {"adjusted": True, "events": [["2025-06-01", 4]]}
        for concept in payload["facts"]["us-gaap"].values():
            for facts in concept["units"].values():
                for fact in facts:
                    if fact["accn"] == "restated-2024" and fact["end"].startswith("2023") and fact["val"] != 100:
                        fact["val"] *= 2
        proposed, _ = report.preview_document(previous, payload, NOW, True)
        self.assertEqual(proposed, previous)

    def test_switched_quarters_use_the_annual_rows_existing_share_basis(self):
        payload = fixture("synthetic-quarterly.json")
        original = sec.extract_periods(original_only(payload))
        original.skip = None
        previous, _ = sec.merge_into_document(None, original, "TEST", NOW)
        # Historical annual was adjusted, while the stored quarters were not.
        for field in ("epsBasic", "epsDiluted"):
            previous["annual"]["values"][field][-1] /= 4
        previous["split"] = {"adjusted": True, "events": [["2025-06-01", 4]]}
        proposed, _ = report.preview_document(previous, payload, NOW, True)
        self.assertEqual(proposed["annual"]["values"]["epsDiluted"][-1], .5)
        self.assertEqual(proposed["quarterly"]["values"]["epsDiluted"], [.125] * 4)
        self.assertEqual(sum(proposed["quarterly"]["values"]["epsDiluted"]), .5)

    def test_restated_quarters_allow_q4_eps_on_one_accession(self):
        selected = sec.extract_periods(fixture("synthetic-quarterly.json"), prefer_latest_filing=True)
        annual = row(selected, "annual", "2023-12-31")
        quarters = [p for p in selected.quarterly if p["end"].startswith("2023")]
        fin.assign_fiscal_labels([annual], quarters, fallback_end_month=12)
        q4 = next(p for p in quarters if p["fq"] == 4)
        for field in ("epsBasic", "epsDiluted"):
            q4["values"].pop(field, None)
        self.assertEqual(set(fin.fill_q4_eps([annual], quarters)),
                         {("2023-12-31", "epsBasic"), ("2023-12-31", "epsDiluted")})
        self.assertEqual(q4["values"]["epsDiluted"], .5)
        self.assertTrue(all(p["extra"][BASIS]["accn"] == "restated-2024" for p in quarters))
        revision = next(r for r in selected.restatement_revisions if r["period"] == "quarterly"
                        and r["end"] == "2023-03-31" and r["field"] == "epsDiluted")
        self.assertEqual((revision["old"], revision["new"], revision["originalAccn"]), (1, .5, "quarter-1"))

    def test_missing_restated_quarters_keep_as_reported_and_null_q4(self):
        payload = fixture("synthetic-quarterly.json")
        for concept in payload["facts"]["us-gaap"].values():
            for unit, rows in concept["units"].items():
                concept["units"][unit] = [r for r in rows if not
                                          (r["accn"] == "restated-2024" and r.get("fp") == "FY"
                                           and r.get("start") and r["end"] != "2023-12-31"
                                           and r["end"].startswith("2023"))]
        selected = sec.extract_periods(payload, prefer_latest_filing=True)
        annual = row(selected, "annual", "2023-12-31")
        quarters = [p for p in selected.quarterly if p["end"].startswith("2023")]
        fin.assign_fiscal_labels([annual], quarters, fallback_end_month=12)
        self.assertEqual(row(selected, "quarterly", "2023-03-31")["values"]["epsDiluted"], 1)
        q4 = next(p for p in quarters if p["fq"] == 4)
        self.assertIsNone(q4["values"].get("epsDiluted"))
        self.assertIn("bases-differ", q4["extra"][BLOCKED])
        self.assertEqual(fin.fill_q4_eps([annual], quarters), [])
        self.assertIsNone(q4["values"].get("epsBasic"))
        original = sec.extract_periods(original_only(payload))
        original.skip = None  # a single-year fixture is sufficient for this merge regression
        previous, _ = sec.merge_into_document(None, original, "TEST", NOW)
        proposed, _ = sec.merge_into_document(previous, selected, "TEST", NOW)
        old_q4 = fin.read_sections(previous)["quarterly"][-1]
        new_q4 = next(p for p in fin.read_sections(proposed)["quarterly"] if p["end"] == "2023-12-31")
        self.assertEqual(new_q4["values"].get("netIncome"), old_q4["values"].get("netIncome"))
        self.assertIsNone(new_q4["values"].get("epsDiluted"))
        _, changes = report.document_changes(previous, proposed)
        affected = next(c for c in changes if c["field"] == "epsDiluted")
        self.assertEqual((affected["old"], affected["new"], affected["accn"]), (1, None, "restated-2024"))

    def test_quality_preserves_existing_flags_and_original_values(self):
        payload = fixture("synthetic-quarterly.json")
        previous, _ = sec.merge_into_document(None, sec.extract_periods(original_only(payload)), "TEST", NOW)
        # One-year fixtures are diagnostic; construct the original stored document directly.
        if previous is None:
            original = sec.extract_periods(original_only(payload))
            original.skip = None
            previous, _ = sec.merge_into_document(None, original, "TEST", NOW)
        previous["quality"]["issues"] += ["eps-inconsistent", "custom-existing-flag"]
        before = copy.deepcopy(previous)
        selected = sec.extract_periods(payload, prefer_latest_filing=True)
        proposed, _ = sec.merge_into_document(previous, selected, "TEST", NOW)
        self.assertEqual(previous, before)
        self.assertEqual(proposed["quality"]["issues"][:len(previous["quality"]["issues"])],
                         previous["quality"]["issues"])
        self.assertIn("restated", proposed["quality"]["issues"])
        revisions = proposed["quality"]["revisions"]
        revision = next(r for r in revisions if r.get("field") == "revenue" and r.get("accn"))
        self.assertEqual((revision["old"], revision["new"], revision["filed"]), (4000, 2000, "2025-02-20"))
        changes, q4 = report.document_changes(previous, proposed)
        summary = report.summarize([{"status": "reviewed", "changes": changes, "q4EpsChanges": q4}])
        self.assertEqual(summary["fields"], len(changes))
        self.assertEqual(summary["q4EpsAffected"], len(q4))

    def test_report_client_stops_first_403_without_retry_or_further_request(self):
        opener = mock.Mock(return_value=(403, b"forbidden"))
        sleep = mock.Mock()
        client = report.ReportClient("Tests test@example.org", opener=opener, sleeper=sleep)
        for _ in range(2):
            with self.assertRaises(sec.SecForbidden):
                client.company_facts("12345")
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(client.requests, 1)
        self.assertGreaterEqual(sleep.call_args.args[0], .25)
        self.assertEqual(opener.call_args.args[0].get_header("User-agent"), "Tests test@example.org")

    def test_report_client_enforces_budget_before_request(self):
        opener = mock.Mock()
        client = report.ReportClient("Tests test@example.org", opener=opener, sleeper=lambda _: None)
        client.requests = 12000
        with self.assertRaises(report.RequestBudgetExceeded):
            client.company_facts("12345")
        opener.assert_not_called()

    def test_apply_option_is_refused_without_report(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            sec.parse_args(["--prefer-latest-filing"])

    def test_foreign_latest_row_and_reported_quarters_use_one_accession(self):
        payload = fixture("synthetic-quarterly.json")
        names = {"Revenues": "Revenue", "NetIncomeLoss": "ProfitLossAttributableToOwnersOfParent",
                 "EarningsPerShareBasic": "BasicEarningsLossPerShare",
                 "EarningsPerShareDiluted": "DilutedEarningsLossPerShare", "Assets": "Assets"}
        block = {names[name]: copy.deepcopy(value) for name, value in payload["facts"]["us-gaap"].items() if name in names}
        for concept in block.values():
            for facts in concept["units"].values():
                for fact in facts:
                    fact["form"] = "20-F" if fact["form"] == "10-K" else "6-K"
        payload["facts"] = {"ifrs-full": block}
        selected = sec_ifrs.extract(payload, prefer_latest_filing=True)
        annual = row(selected, "annual", "2023-12-31")
        quarter = row(selected, "quarterly", "2023-03-31")
        self.assertEqual(annual["values"]["revenue"], 2000)
        self.assertEqual(annual["values"]["totalAssets"], 9000)
        self.assertIn("mixed-basis", annual["extra"]["secRestatementIssues"])
        self.assertEqual(quarter["values"]["epsDiluted"], .5)
        self.assertEqual(annual["extra"][BASIS]["accn"], quarter["extra"][BASIS]["accn"])

    def test_q4_basis_guard_survives_document_serialization_and_repair(self):
        annual = fin.period("2023-12-31", {"epsBasic": 4, "epsDiluted": 4, "netIncome": 400,
                                                 "dilutedShares": 100}, "sec", {BASIS: {"accn": "new", "restated": True}})
        quarters = [fin.period(end, {"epsBasic": 1, "epsDiluted": 1, "netIncome": 100,
                                    "dilutedShares": 100}, "sec") for end in ("2023-03-31", "2023-06-30", "2023-09-30")]
        quarters.append(fin.period("2023-12-31", {"netIncome": 100}, "sec"))
        fin.assign_fiscal_labels([annual], quarters, fallback_end_month=12)
        document = {"annual": fin.write_section([annual], {}, False),
                    "quarterly": fin.write_section(quarters, {}, True)}
        before = copy.deepcopy(document)
        self.assertEqual(fin.repair_q4_document(document), [])
        self.assertEqual(document, before)

    def test_offline_report_records_unreviewed_files_and_never_writes_financials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            financials = root / "financials"
            financials.mkdir()
            (root / "fundamentals").mkdir()
            (root / "fundamentals/us-market-progress.json").write_text(json.dumps({"tickerMap": {"TEST": "0000012345"}}))
            original = json.dumps({"symbol": "TEST", "market": "US"})
            source = financials / "TEST.json"
            source.write_text(original)
            output = root / "restatements.json"
            with mock.patch("builtins.print"), mock.patch.object(report, "ReportClient", side_effect=AssertionError("network")):
                code = report.main(["--offline", "--prefer-latest-filing", "--report-dir", str(root),
                                    "--report", str(output), "--cache-dir", str(root / "cache")])
            self.assertEqual(code, 1)
            self.assertEqual(source.read_text(), original)
            result = json.loads(output.read_text())
            self.assertEqual(result["summary"]["files"], 1)
            self.assertEqual(result["files"][0]["status"], "offline-cache-missing")
            self.assertEqual(result["financialFilesWritten"], 0)

    def test_restatement_audit_is_not_truncated_to_fifty_entries(self):
        from sec_restatements import preserve_quality
        originals = [{"kind": "restated", "period": "annual", "end": "2023-12-31", "field": str(i),
                      "old": i, "new": i + 1, "accn": "new", "originalAccn": "original",
                      "filed": "2025-02-20"} for i in range(60)]
        document = {"quality": {"issues": []}}
        preserve_quality(document, None, originals, "2026-10-22")
        self.assertEqual(len(document["quality"]["revisions"]), 60)
        self.assertEqual(len(fin.merge_revisions(document["quality"]["revisions"], [],
                                                [fin.period("2023-12-31", {})], [], {})), 60)
        distinct_filings = [originals[0], {**originals[0], "accn": "later", "filed": "2026-02-20"}]
        self.assertEqual(len(fin.merge_revisions(distinct_filings, [],
                                                [fin.period("2023-12-31", {})], [], {})), 2)

    def test_original_audit_follows_aligned_stored_period_end(self):
        payload = fixture("ad-comparatives.json")
        previous, _ = sec.merge_into_document(None, sec.extract_periods(payload), "AD", NOW)
        previous["annual"]["end"][-1] = "2024-01-06"
        selected = sec.extract_periods(payload, prefer_latest_filing=True)
        proposed, _ = sec.merge_into_document(previous, selected, "AD", NOW)
        entry = next(r for r in proposed["quality"]["revisions"] if r.get("accn") == "0000821130-26-000012")
        self.assertEqual(entry["end"], "2024-01-06")
        self.assertEqual(entry["secEnd"], "2023-12-31")

    def test_domestic_revenue_scope_rejection_cannot_fall_through_to_foreign_builder(self):
        result = sec.SecResult(skip="revenue-scope-mismatch")
        with mock.patch.object(sec, "extract_periods", return_value=result), mock.patch.object(
                sec_ifrs, "build_foreign_document", side_effect=AssertionError("bypassed scope guard")):
            proposed, reason = report.preview_document({"symbol": "TEST"}, {}, NOW, True)
        self.assertIsNone(proposed)
        self.assertEqual(reason, "revenue-scope-mismatch")

    def test_old_stored_q4_outside_refresh_cap_still_gets_basis_check(self):
        annual = fin.period("2000-12-31", {"revenue": 100, "netIncome": 100}, "sec")
        q4 = fin.period("2000-12-31", {"revenue": 25, "netIncome": 25, "epsDiluted": 1}, "sec")
        fin.assign_fiscal_labels([annual], [q4], fallback_end_month=12)
        previous = {"version": fin.VERSION, "symbol": "TEST", "market": "US", "currency": "USD",
                    "annual": fin.write_section([annual], {}, False),
                    "quarterly": fin.write_section([q4], {}, True)}
        annual["values"]["netIncome"] = 50
        annual["extra"][BASIS] = {"accn": "restated", "filed": "2016-02-20", "restated": True}
        protected = fin.period("2000-12-31", {}, "sec", {
            "secKeepAsReported": True, BLOCKED: "restated-quarterly-values-unavailable"})
        newer = [fin.period(f"{year}-{end}", {"revenue": 1}, "sec")
                 for year in range(2001, 2016) for end in ("03-31", "06-30", "09-30", "12-31")]
        selected = sec.SecResult(annual=[annual], quarterly=[protected, *newer], prefer_latest_filing=True)
        proposed, _ = sec.merge_into_document(previous, selected, "TEST", NOW)
        retained = next(p for p in fin.read_sections(proposed)["quarterly"] if p["end"] == "2000-12-31")
        self.assertIsNone(retained["values"].get("epsDiluted"))
        self.assertEqual(retained["values"]["netIncome"], 25)
        self.assertIsNotNone(retained["extra"].get(BLOCKED))


if __name__ == "__main__":
    unittest.main()

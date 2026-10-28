import json
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_data_status import SOURCES, Source, build, evaluate, expected_date, workflow_status


def now(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class DataStatusTests(unittest.TestCase):
    tw = Source("test", "測試", "TW", "x.json", "asOf")
    us = Source("test", "測試", "US", "x.json", "asOf", calendar="XNYS", publish_minutes=360)

    def test_weekend_is_not_late(self):
        row = evaluate(self.tw, {"asOf": "2026-10-23", "generatedAt": "2026-10-23T10:00:00Z"}, now("2026-10-25T12:00:00Z"))
        self.assertEqual((row["status"], row["lagTradingDays"], row["expectedDate"]), ("ok", 0, "2026-10-23"))

    def test_taiwan_national_holiday(self):
        self.assertEqual(expected_date(self.tw, now("2026-10-09T15:00:00Z")), "2026-10-08")

    def test_us_independence_holiday(self):
        self.assertEqual(expected_date(self.us, now("2026-07-04T14:00:00Z")), "2026-07-02")

    def test_lag_one_and_three_sessions(self):
        for day, status, lag in [("2026-10-27", "late", 1), ("2026-10-22", "stale", 3)]:
            row = evaluate(self.tw, {"asOf": day, "generatedAt": "2026-10-28T09:00:00Z"}, now("2026-10-28T10:00:00Z"))
            self.assertEqual((row["status"], row["lagTradingDays"]), (status, lag))

    def test_publication_grace_and_dst(self):
        # US Oct 27 close at 20Z, +6h availability at Taiwan Oct 28 10:00.
        self.assertEqual(expected_date(self.us, now("2026-10-28T01:59:00Z")), "2026-10-26")
        self.assertEqual(expected_date(self.us, now("2026-10-28T02:00:00Z")), "2026-10-27")
        self.assertEqual(expected_date(self.us, now("2026-11-04T02:59:00Z")), "2026-11-02")
        self.assertEqual(expected_date(self.us, now("2026-11-04T03:00:00Z")), "2026-11-03")
        self.assertEqual(expected_date(self.tw, now("2026-10-28T07:29:00Z")), "2026-10-27")
        self.assertEqual(expected_date(self.tw, now("2026-10-28T07:30:00Z")), "2026-10-28")

    def test_early_close_uses_calendar(self):
        self.assertEqual(expected_date(self.us, now("2026-11-28T00:01:00Z")), "2026-11-27")

    def test_fred_grace(self):
        source = next(s for s in SOURCES if s.id == "rates")
        row = evaluate(source, {"asOf": "2026-10-23", "generatedAt": "2026-10-28T09:00:00Z"}, now("2026-10-28T12:00:00Z"))
        self.assertEqual(row["status"], "ok")

    def test_missing_field_invalid_date_and_unreadable_file(self):
        for doc in [{}, {"asOf": "2026-02-30"}, {"asOf": "20261027"}, {"asOf": "2027-01-01"}]:
            row = evaluate(self.tw, doc, now("2026-10-28T12:00:00Z"))
            self.assertEqual(row["status"], "missing")
            self.assertTrue(row["reason"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "docs/report"
            path.mkdir(parents=True)
            (path / "latest.json").mkdir()  # EISDIR, not just ENOENT
            (path / "growth.json").write_text("broken JSON")
            report = build(folder, now("2026-10-28T12:00:00Z"))
            self.assertEqual(report["summary"]["missing"], len(SOURCES))
            self.assertIn("latest.json", report["sources"][0]["reason"])

    def test_real_shapes_and_conservative_fetch_coverage(self):
        at = now("2026-10-28T12:00:00Z")
        docs = {
            "universe-tw": {"generatedAt": at.isoformat(), "markets": {"TW": {"asOf": "2026-10-27", "counts": {"stale": 2, "missing": 3}}}},
            "breadth-us": {"generatedAt": at.isoformat(), "markets": {"us": {"asOf": "2026-10-26"}}},
            "tw-financials": {"fetch": {"2025Q4": {"sii": {"at": "2026-01-01"}}, "2026Q2": {"sii": {"at": "2026-10-26"}, "otc": {"at": "2026-10-27"}}}},
            "tw-finmind": {"fetch": {"2330": ["2026-10-20", "2026Q2", 40], "2317": ["2026-10-27", "2026Q2", 40]}},
        }
        expected = {"universe-tw": "2026-10-27", "breadth-us": "2026-10-26", "tw-financials": "2026-10-26", "tw-finmind": "2026-10-20"}
        for source in SOURCES:
            if source.id in docs:
                row = evaluate(source, docs[source.id], at)
                self.assertEqual(row["dataDate"], expected[source.id])
                self.assertNotEqual(row["status"], "missing", row)
                if source.id == "universe-tw":
                    self.assertEqual(row["counts"], {"stale": 2, "missing": 3})
                if source.id == "tw-financials":
                    self.assertEqual(row["generatedAt"], "2026-10-26")

    def test_missing_archive_isolated_and_inputs_not_written(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "docs/report"
            path.mkdir(parents=True)
            body = '{"reportDate":"2026-10-28","generatedAt":"2026-10-28T09:00:00Z"}'
            (path / "latest.json").write_text(body)
            report = build(folder, now("2026-10-28T12:00:00Z"))
            self.assertEqual(report["sources"][0]["status"], "ok")
            self.assertEqual((path / "latest.json").read_text(), body)
            self.assertTrue(all(r["status"] == "missing" for r in report["sources"] if r["path"] == "@archive"))
            self.assertEqual(sum(report["summary"].values()), len(SOURCES))

    def test_workflow_errors_do_not_break_inventory_or_expose_secrets(self):
        def request(path, repository, token):
            if "schedule.yml" in path:
                return {"workflow_runs": [{"status": "completed", "conclusion": "failure", "html_url": "https://github.com/a/b/actions/runs/1", "updated_at": "2026-10-28T01:00:00Z"}]}
            raise OSError("secret-token")
        rows = workflow_status("a/b", "secret-token", request)
        self.assertEqual(rows[0]["conclusion"], "failure")
        self.assertEqual(len(rows), 8)
        self.assertNotIn("secret-token", json.dumps(rows))


if __name__ == "__main__":
    unittest.main()

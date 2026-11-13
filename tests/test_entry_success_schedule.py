"""Offline tests for the weekly entry-success gate; no market data or GitHub access."""
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import entry_success_schedule as schedule  # noqa: E402


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class EntrySuccessScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.report = self.root / "breakouts.json"
        self.save("2026-11-13", "2026-11-13")

    def save(self, tw, us, generated_at="2026-11-13T10:00:00Z"):
        self.report.write_text(json.dumps({
            "markets": {"tw": {"asOf": tw}, "us": {"asOf": us}},
            "generatedAt": generated_at,
        }), encoding="utf-8")

    def decision(self, when="2026-11-16T03:00:00Z", event="schedule", mode="auto"):
        return schedule.decide(self.report, utc(when), event, mode)

    def test_taipei_saturday_scheduled_and_workflow_run(self):
        self.assertTrue(self.decision("2026-11-13T16:00:00Z", "schedule").run)
        self.assertTrue(self.decision("2026-11-13T16:00:00Z", "workflow_run").run)

    def test_weekday_and_manual_auto_with_fresh_data_skip(self):
        self.assertFalse(self.decision().run)
        self.assertFalse(self.decision("2026-11-13T16:00:00Z", "workflow_dispatch").run)
        self.assertEqual(self.decision().next_date.isoformat(), "2026-11-20")

    def test_seven_calendar_days_catches_up_even_for_one_market(self):
        self.save("2026-11-09", "2026-11-12")
        self.assertTrue(self.decision("2026-11-16T01:00:00Z").run)
        self.save("2026-11-12", "2026-11-09")
        self.assertTrue(self.decision("2026-11-16T01:00:00Z").run)
        self.save("2026-11-10", "2026-11-12")
        self.assertFalse(self.decision("2026-11-16T01:00:00Z").run)

    def test_unreadable_missing_or_invalid_file_runs_without_erasing_it(self):
        self.report.unlink()
        self.assertTrue(self.decision().run)
        self.report.write_text("{invalid")
        self.assertTrue(self.decision().run)
        self.save("2026-11-13", "invalid")
        self.assertTrue(self.decision().run)
        self.save("2026-11-13", "2026-11-30")
        self.assertTrue(self.decision().run)

    def test_force_and_skip_override_everything(self):
        self.assertTrue(self.decision(mode="force").run)
        self.assertFalse(self.decision("2026-11-13T16:00:00Z", "schedule", "skip").run)
        self.report.unlink()
        self.assertFalse(self.decision(mode="skip").run)

    def test_utc_friday_1600_is_taipei_saturday(self):
        self.assertFalse(self.decision("2026-11-13T15:59:59Z").run)
        self.assertTrue(self.decision("2026-11-13T16:00:00Z").run)

    def test_second_saturday_run_uses_existing_saturday_generation(self):
        self.save("2026-11-13", "2026-11-13", "2026-11-13T17:00:00Z")
        self.assertFalse(self.decision("2026-11-13T17:30:00Z", "workflow_run").run)
        self.assertTrue(self.decision("2026-11-13T17:30:00Z", "workflow_run", "force").run)

    def test_skip_writes_actions_summary_but_never_touches_report(self):
        original = self.report.read_bytes()
        output = self.root / "output"
        summary = self.root / "summary"
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary),
                                     "ENTRY_SUCCESS_MODE": "auto"}):
            self.assertEqual(schedule.main(["--report", str(self.report), "--now", "2026-11-16T03:00:00Z",
                                            "--event", "workflow_run"]), 0)
        self.assertEqual(output.read_text(), "run=false\n")
        self.assertIn("進場成功率本次略過（上次 asOf TW 2026-11-13／US 2026-11-13，", summary.read_text())
        self.assertIn("下次預計 2026-11-20", summary.read_text())
        self.assertEqual(self.report.read_bytes(), original)

    def test_workflow_conditions_keep_risk_warnings_and_publish_running(self):
        workflow = (ROOT / ".github/workflows/universe-full.yml").read_text()
        entry = workflow.split("      - name: Build full-market entry success rates\n", 1)[1].split("      - id: notice_soft_06", 1)[0]
        warnings = workflow.split("        name: Build market risk warnings\n", 1)[1].split("      - name: Publish owned outputs", 1)[0]
        publish = workflow.split("      - name: Publish owned outputs to generated and durable records to main\n", 1)[1].split("      - name: Commit and Push changes", 1)[0]
        self.assertIn("steps.entry_success_gate.outputs.run == 'true'", entry)
        self.assertIn("if: always() && !cancelled()", warnings)
        self.assertNotIn("entry_success_gate", warnings)
        self.assertIn("always()", publish)
        self.assertNotIn("entry_success_gate", publish)
        self.assertIn("entry_success:", workflow)


if __name__ == "__main__":
    unittest.main()

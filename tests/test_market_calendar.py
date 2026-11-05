import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_market_calendar import build, is_session, utc_now, write_calendar


class MarketCalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = build(utc_now("2026-10-08T12:00:00Z"))

    def test_one_year_and_lookback(self):
        self.assertEqual(self.doc["validFrom"], "2026-09-08")
        self.assertGreaterEqual(self.doc["validTo"], "2027-10-08")
        for market in ("TW", "US"):
            self.assertGreater(len(self.doc["markets"][market]["sessions"]), 240)

    def test_tw_holidays_and_weekend(self):
        tw = self.doc["markets"]["TW"]
        for day in ("2026-10-09", "2027-02-08"):
            self.assertIn(day, tw["holidays"])
            self.assertNotIn(day, tw["sessions"])
        self.assertNotIn("2026-10-10", tw["sessions"])
        self.assertIn("2026-10-08", tw["sessions"])

    def test_us_holiday_and_early_close(self):
        us = self.doc["markets"]["US"]
        self.assertIn("2026-11-26", us["holidays"])
        self.assertNotIn("2026-11-26", us["sessions"])
        session = us["sessions"]["2026-11-27"]
        self.assertTrue(session["earlyClose"])
        self.assertIn("2026-11-27", us["earlyCloses"])
        self.assertEqual(session["close"], int(utc_now("2026-11-27T18:00:00Z").timestamp()))

    def test_dst_and_regular_close(self):
        us = self.doc["markets"]["US"]["sessions"]
        for day, hour in [("2026-10-08", 20), ("2026-11-04", 21)]:
            closed = utc_now(f"{day}T{hour}:00:00Z")
            self.assertEqual(us[day]["close"], int(closed.timestamp()))
            self.assertEqual(closed.astimezone(ZoneInfo("America/New_York")).hour, 16)
            self.assertFalse(us[day]["earlyClose"])

    def test_gate_uses_market_date_including_after_hours(self):
        self.assertFalse(is_session("US", utc_now("2026-11-26T22:00:00Z")))
        self.assertTrue(is_session("US", utc_now("2026-11-28T01:41:00Z")))  # Friday in NY
        self.assertFalse(is_session("US", utc_now("2026-11-28T22:00:00Z")))
        self.assertFalse(is_session("TW", utc_now("2026-10-09T02:00:00Z")))

    def test_atomic_derived_file_and_cli_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            permanent = folder / "untouched.csv"
            permanent.write_text("original")
            output = folder / "report/calendar.json"
            write_calendar(output, self.doc)
            self.assertEqual(json.loads(output.read_text()), self.doc)
            self.assertEqual(permanent.read_text(), "original")
            self.assertEqual(list(output.parent.iterdir()), [output])
            github_output = folder / "github-output"
            subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/build_market_calendar.py"),
                            "--now", "2026-11-26T22:00:00Z", "--is-session", "US", "--github-output", str(github_output)], check=True)
            self.assertEqual(github_output.read_text(), "is_session=false\n")

    def test_only_generated_owner_and_workflow_gates(self):
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / ".github/generated-paths.json").read_text())
        self.assertEqual(config["owners"]["calendar"], ["docs/report/market-calendar.json"])
        self.assertNotIn("calendar", config["records"])
        workflow = (root / ".github/workflows/market-extras.yml").read_text()
        self.assertIn("--is-session US --github-output", workflow)
        self.assertEqual(workflow.count("if: steps.session.outputs.is_session == 'true'"), 3)
        daily = (root / ".github/workflows/schedule.yml").read_text()
        self.assertIn("publish calendar", daily)
        self.assertIn("group: market-calendar-generated", daily)


if __name__ == "__main__":
    unittest.main()

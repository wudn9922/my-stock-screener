"""Offline reporting tests for the US market backfill checkpoint."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "atlas" / "scripts"))

import report_financials_us_market_coverage as market_report  # noqa: E402


class SplitCheckRecheckReportTests(unittest.TestCase):
    def test_summary_keeps_failed_attempt_history_and_reports_successful_retry(self):
        summaries = market_report.summarize_split_check_rechecks([
            {
                "batchId": 2,
                "startedAt": "2026-10-15T07:48:00Z",
                "status": "stopped_split_unavailable",
                "symbols": ["BCDA", "BCHT"],
                "outcomes": {
                    "BCDA": {
                        "note": "split-check-pending",
                        "split": {"chart": "failed", "events": 0, "adjusted": False},
                    },
                },
                "remainingSymbols": ["BCHT"],
                "secWireRequests": 0,
                "stoppedReason": "curl_cffi unavailable",
            },
            {
                "batchId": 2,
                "startedAt": "2026-10-15T07:50:00Z",
                "finishedAt": "2026-10-15T07:52:00Z",
                "status": "completed",
                "symbols": ["BCDA", "BCHT"],
                "outcomes": {
                    "BCDA": {"split": {"chart": "fetched", "events": 2, "adjusted": True}},
                    "BCHT": {"split": {"chart": "fetched", "events": 0, "adjusted": False}},
                },
                "remainingSymbols": [],
                "secWireRequests": 0,
                "interpreter": "/tmp/example-venv/bin/python",
            },
        ])

        self.assertEqual(len(summaries), 2)
        self.assertEqual(summaries[0]["status"], "stopped_split_unavailable")
        self.assertEqual(summaries[0]["pendingAfterCheck"], 2)
        self.assertEqual(summaries[0]["chartFailures"], 1)
        self.assertEqual(summaries[0]["secWireRequests"], 0)
        self.assertEqual(summaries[1]["status"], "completed")
        self.assertEqual(summaries[1]["chartFetches"], 2)
        self.assertEqual(summaries[1]["splitEvents"], 2)
        self.assertEqual(summaries[1]["adjustedFiles"], 1)
        self.assertEqual(summaries[1]["pendingAfterCheck"], 0)


if __name__ == "__main__":
    unittest.main()

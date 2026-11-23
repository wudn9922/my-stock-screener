"""Fail-closed authentication tests for the Cloud Run /run endpoint."""

import os
import unittest
from importlib import import_module
from unittest.mock import patch

monitor_app = import_module("breakout_alert.app")


class RunAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.client = monitor_app.app.test_client()

    def test_missing_token_configuration_returns_503_without_running(self):
        with patch.dict(os.environ):
            os.environ.pop("RUN_TOKEN", None)
            with patch.object(monitor_app, "run_breakout_monitor") as runner:
                result = self.client.post("/run")
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.get_json()["status"], "unavailable")
                self.assertIn("RUN_TOKEN", result.get_json()["message"])
                runner.assert_not_called()

    def test_blank_configuration_returns_503_even_if_header_present(self):
        with patch.dict(os.environ, {"RUN_TOKEN": "   "}):
            with patch.object(monitor_app, "run_breakout_monitor") as runner:
                result = self.client.post("/run", headers={"X-Run-Token": "anything"})
                self.assertEqual(result.status_code, 503)
                runner.assert_not_called()

    def test_missing_or_wrong_header_returns_401_with_token_configured(self):
        with patch.dict(os.environ, {"RUN_TOKEN": "test-run-token"}):
            with patch.object(monitor_app, "run_breakout_monitor") as runner:
                for headers in ({}, {"X-Run-Token": "wrong"}):
                    with self.subTest(headers=headers):
                        result = self.client.post("/run", headers=headers)
                        self.assertEqual(result.status_code, 401)
                runner.assert_not_called()

    def test_matching_header_is_allowed(self):
        with patch.dict(os.environ, {"RUN_TOKEN": "test-run-token"}):
            with patch.object(monitor_app, "run_breakout_monitor") as runner:
                result = self.client.post("/run", headers={"X-Run-Token": "test-run-token"})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.get_json()["status"], "success")
                runner.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

"""Offline consistency checks for the workflow catalog and documented upstreams."""

from pathlib import Path
import re
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CATALOG = ROOT / "docs" / "WORKFLOWS.md"
ROW = re.compile(r"^\| \[\x60([^\x60]+\.yml)\x60\]", re.MULTILINE)


class WorkflowCatalogTest(unittest.TestCase):
    def setUp(self):
        self.document = CATALOG.read_text(encoding="utf-8")

    def assert_same_names(self, expected, actual, label):
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        self.assertFalse(
            missing or extra,
            f"{label}: missing={missing}; extra={extra}",
        )

    def test_catalog_rows_match_workflow_files(self):
        expected = {path.name for path in WORKFLOWS.glob("*.yml")}
        rows = ROW.findall(self.document)
        self.assert_same_names(expected, set(rows), "Workflow catalog filenames")
        duplicates = sorted(name for name in set(rows) if rows.count(name) > 1)
        self.assertFalse(duplicates, f"Duplicate workflow catalog rows: {duplicates}")

    def test_removed_row_fails_with_filename(self):
        # Mutate a disposable copy, never the tracked catalog.
        match = ROW.search(self.document)
        self.assertIsNotNone(match, "Catalog must contain a workflow row")
        removed = match.group(1)
        line = self.document[match.start():].splitlines()[0]
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "WORKFLOWS.md"
            catalog.write_text(self.document.replace(line + "\n", "", 1), encoding="utf-8")
            check = WorkflowCatalogTest("test_catalog_rows_match_workflow_files")
            with patch(__name__ + ".CATALOG", catalog):
                check.setUp()
                with self.assertRaisesRegex(AssertionError, re.escape(f"missing={[removed]}")):
                    check.test_catalog_rows_match_workflow_files()

    def assert_upstream_matches(self, heading, filename):
        match = re.search(
            rf"^### {re.escape(heading)}[ \t]*\n(.*?)(?=^#{{1,6}} |\Z)",
            self.document,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, f"Missing catalog heading: {heading}")
        documented = {
            name.strip()
            for name in match.group(1).strip().removesuffix("。").split("、")
            if name.strip()
        }
        config = yaml.load(
            (WORKFLOWS / filename).read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        expected = set(config["on"]["workflow_run"]["workflows"])
        self.assert_same_names(expected, documented, heading)

    def test_failure_fallback_upstreams(self):
        self.assert_upstream_matches(
            "故障通知備援的上游", "failure-notice-fallback.yml"
        )

    def test_pages_upstreams(self):
        self.assert_upstream_matches("Pages 組裝的上游", "generated-pages.yml")


if __name__ == "__main__":
    unittest.main()

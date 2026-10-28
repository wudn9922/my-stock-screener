import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_screener_history import capture


class ScreenerHistoryTests(unittest.TestCase):
    def test_append_only_market_dates_growth_and_source_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / "docs/report"
            report.mkdir(parents=True)
            source = {"version": 1, "fields": ["symbol"], "markets": {"TW": {"asOf": "2026-10-27", "rows": [["2330.TW"]]}, "US": {"asOf": "2026-10-26", "rows": [["AAPL"]]}}}
            (report / "universe.json").write_text(json.dumps(source))
            (report / "growth.json").write_text(json.dumps({"version": 1, "fields": ["symbol"], "rows": [["2330-TW"], ["AAPL"]]}))
            original = (report / "universe.json").read_bytes()
            self.assertEqual(capture(root, "2026-10-28T01:00:00Z"), 2)
            path = report / "screener-history/TW-2026-10-27.json"
            first = path.read_bytes()
            self.assertEqual(json.loads(first)["growth"]["rows"], [["2330-TW"]])
            self.assertEqual(capture(root, "2026-10-28T02:00:00Z"), 0)
            self.assertEqual(path.read_bytes(), first)
            self.assertEqual((report / "universe.json").read_bytes(), original)
            source["markets"]["TW"]["asOf"] = "2026-10-28"
            (report / "universe.json").write_text(json.dumps(source))
            self.assertEqual(capture(root, "2026-10-28T12:00:00Z"), 1)
            self.assertEqual(path.read_bytes(), first)
            index = json.loads((report / "screener-history/index.json").read_text())
            self.assertEqual(index["markets"]["TW"], ["2026-10-27", "2026-10-28"])

    def test_missing_or_malformed_input_does_not_create_fabricated_history(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(capture(folder), 0)
            self.assertFalse((Path(folder) / "docs/report/screener-history/index.json").exists())


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_industry_rotation import build, summarize
from build_data_status import calendar_for
from universe_cache import UNKNOWN_VOLUME_DATES


class IndustryRotationTests(unittest.TestCase):
    def test_equal_returns_common_volume_cohort_and_unknowns(self):
        dates = ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-27"]
        frame = pd.DataFrame({"Close": [100, 105, 110, 115, 121], "Volume": [100, 100, 100, 200, 200]}, index=pd.to_datetime(dates))
        unknown = frame.copy()
        unknown.attrs[UNKNOWN_VOLUME_DATES] = ("2026-10-21T00:00:00",)
        result = summarize([{"return": 10, "frame": frame}, {"return": -5, "frame": unknown}, {"return": None}], 2, dates)
        self.assertAlmostEqual(result["returnPct"], 2.5)
        self.assertEqual(result["upShare"], 50)
        self.assertEqual(result["volumeRatio"], 2)
        self.assertEqual(result["volumeCount"], 1)
        self.assertEqual(result["pairedCount"], 2)
        self.assertAlmostEqual(result["momentumChange"], 0)

    def test_missing_session_not_filled_and_stale_stock_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / "docs/report"
            report.mkdir(parents=True)
            path = report / "universe.json"
            path.write_text(json.dumps({"fields": ["symbol", "asOf", "industry", "changePct", "r5", "r21", "r63"], "markets": {"TW": {"asOf": "2026-10-28", "scope": "full", "rows": [["2330.TW", "2026-10-28", "半導體", 2, 5, 10, 20], ["2317.TW", "2026-10-27", "其他", 3, 5, 10, 20]]}}}))
            original = path.read_bytes()
            result = build(root)
            row = result["markets"]["TW"]["periods"]["m1"]["rows"][0]
            self.assertEqual(row["returnPct"], 10)
            self.assertEqual(row["count"], 1)
            self.assertIsNone(row["volumeRatio"])
            self.assertEqual(path.read_bytes(), original)

    def test_full_build_reads_cache_without_writing_it_and_aligns_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / "docs/report"
            bars = root / ".cache/universe-full/bars"
            report.mkdir(parents=True)
            bars.mkdir(parents=True)
            dates = calendar_for("XTAI", 2026).sessions_in_range("2026-01-01", "2026-10-28")[-127:]
            frame = pd.DataFrame({"Open": 100., "High": 100., "Low": 100., "Close": 100., "Volume": [100]*106 + [200]*21}, index=dates)
            frame.index.name = "Date"
            cache = bars / "2330.TW.csv"
            frame.to_csv(cache)
            source = {"fields": ["symbol", "asOf", "industry", "changePct", "r5", "r21", "r63"], "markets": {"TW": {"asOf": "2026-10-28", "rows": [["2330.TW", "2026-10-28", "半導體", 0, 0, 0, 0]]}}}
            (report / "universe.json").write_text(json.dumps(source))
            before = cache.read_bytes()
            result = build(root)
            row = result["markets"]["TW"]["periods"]["m1"]["rows"][0]
            self.assertEqual(row["volumeRatio"], 2)
            self.assertEqual(row["volumeCount"], 1)
            self.assertEqual(row["pairedCount"], 1)
            self.assertEqual(cache.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

"""Offline publication gates. All inputs are fixtures or mocked upstream calls."""
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "atlas/scripts"))
sys.path.insert(0, str(ROOT))

from scripts.output_guard import OutputRejected, finite, retain_count, retain_keys, unique_keys
import build_growth as growth
import build_valuation_us as valuation
import build_industry_rotation as rotation
from test_growth import document, y


class GuardTests(unittest.TestCase):
    def test_finite_and_unique(self):
        self.assertTrue(finite(-1))
        self.assertTrue(finite(0))
        for value in (None, True, float("nan"), float("inf"), "1"):
            self.assertFalse(finite(value))
        self.assertEqual(unique_keys(["A", "B"], "test"), {"A", "B"})
        for keys in (["A", "A"], [None], [""]):
            with self.assertRaises(OutputRejected):
                unique_keys(keys, "test")

    def test_counts_and_anchor(self):
        retain_count(85, 100, "test", min_share=.85)
        with self.assertRaises(OutputRejected):
            retain_count(84, 100, "test", min_share=.85)
        with self.assertRaises(OutputRejected):
            retain_count(0, 0, "test", min_share=.85)
        prior = set("ABCDEFGHIJ")
        retain_keys(prior, prior, "test", min_share=.85, anchors=["A"])
        with self.assertRaisesRegex(OutputRejected, "anchors"):
            retain_keys(prior - {"A"} | {"Z"}, prior, "test", min_share=.85, anchors=["A"])
        with self.assertRaises(OutputRejected):
            retain_keys(set("KLMNOPQRST"), prior, "test", min_share=.85)
        # Cold start need not invent a key absent from the old snapshot.
        retain_keys({"B"}, set(), "test", min_share=.85, anchors=["A"])


class GrowthPublicationTests(unittest.TestCase):
    def test_cli_normal_empty_anchor_and_shrink(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            fin = report / "financials"
            fin.mkdir()
            out = report / "growth.json"
            year = datetime.now(timezone.utc).year - 1
            baseline = ["AAPL", "MSFT"] + [f"TEST{i}" for i in range(18)]
            def inputs(symbols, eps=2):
                for path in fin.glob("*.json"):
                    path.unlink()
                for symbol in symbols:
                    value = document(annual=[y(year - 1, 1, 100), y(year, eps, 120)])
                    (fin / f"{symbol}.json").write_text(json.dumps(value))
            def cli():
                with patch.dict(os.environ, {"ATLAS_REPORT_DIR": tmp, "ATLAS_FINANCIALS_DIR": str(fin),
                                             "ATLAS_FUNDAMENTALS_DIR": str(report / "fundamentals"),
                                             "ATLAS_GROWTH_OUT": str(out)}):
                    runpy.run_path(str(ROOT / "atlas/scripts/build_growth.py"), run_name="__main__")
            inputs(baseline)
            cli()
            original = out.read_bytes()
            self.assertEqual(len(json.loads(original)["rows"]), 20)
            inputs(baseline, eps=3)
            cli()
            good = out.read_bytes()
            self.assertNotEqual(good, original)
            for bad in ([], baseline[1:] + ["NEW"], baseline[:10]):
                with self.subTest(bad=len(bad)):
                    inputs(bad)
                    with self.assertRaises(SystemExit) as exc:
                        cli()
                    self.assertEqual(exc.exception.code, 1)
                    self.assertEqual(out.read_bytes(), good)
            # Readable financial documents, but all values are absent.
            inputs(baseline, eps=None)
            for path in fin.glob("*.json"):
                path.write_text(json.dumps(document()))
            with self.assertRaises(SystemExit) as exc:
                cli()
            self.assertEqual(exc.exception.code, 1)
            self.assertEqual(out.read_bytes(), good)


class AnnualPublicationTests(unittest.TestCase):
    def test_cli_normal_empty_anchor_and_shrink(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            directory = folder / "directory.json"
            out = folder / "tw-annual.json"
            symbols = ["2330.TW", "2317.TW", "2454.TW"] + [f"TEST{i}.TW" for i in range(597)]
            prior = {s: {"epsAnnual": 1.0, "fiscalYear": 2025, "annualPeriodEnd": "2025-12-31"} for s in symbols}
            out.write_text(json.dumps({"version": 1, "items": prior}))
            original = out.read_bytes()

            def cli(listed, annual=(2.0, "2025-12-31")):
                directory.write_text(json.dumps({"items": [[s, s, "TWSE"] for s in listed]}))
                with patch.dict(os.environ, {"ATLAS_DIRECTORY_PATH": str(directory),
                                             "ATLAS_VALUATION_OUT_DIR": tmp,
                                             "YAHOO_TW_ANNUAL_BUDGET_SECONDS": "600"}), \
                     patch.object(valuation, "Yahoo") as yahoo, \
                     patch.object(valuation, "needs_annual", side_effect=lambda item, today: item is None or item.get("epsAnnual") == 1.0), \
                     patch.object(valuation, "parse_annual", return_value=annual), \
                     patch("time.sleep"):
                    yahoo.return_value.get_json.return_value = {}
                    runpy.run_path(str(ROOT / "atlas/scripts/build_valuation_tw_annual.py"), run_name="__main__")

            cli(symbols)
            good = out.read_bytes()
            self.assertNotEqual(original, good)
            self.assertEqual(json.loads(good)["items"]["2330.TW"]["epsAnnual"], 2.0)
            for bad in ([], symbols[1:] + ["NEW.TW"], symbols[:500]):
                with self.subTest(bad=len(bad)):
                    with self.assertRaises(SystemExit) as exc:
                        cli(bad)
                    self.assertEqual(exc.exception.code, 1)
                    self.assertEqual(out.read_bytes(), good)
            # A successful-looking directory with all identities replaced is not a safe rebuild.
            with self.assertRaises(SystemExit) as exc:
                cli([f"OTHER{i}.TW" for i in range(600)])
            self.assertEqual(exc.exception.code, 1)
            self.assertEqual(out.read_bytes(), good)
            # The directory itself is large, but all upstream EPS values are absent.
            with self.assertRaises(SystemExit) as exc:
                cli([f"OTHER{i}.TW" for i in range(600)], annual=None)
            self.assertEqual(exc.exception.code, 1)
            self.assertEqual(out.read_bytes(), good)


class RotationPublicationTests(unittest.TestCase):
    def test_cli_normal_empty_anchor_and_shrink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "docs/report"
            report.mkdir(parents=True)
            out = report / "industry-rotation.json"
            sectors = ["半導體業", "金融保險業", "食品工業", "鋼鐵工業"]
            day = "2026-10-28"

            def inputs(names, members, ret=1):
                rows = [[f"{1000 + i*100+j}.TW", day, name, ret, ret, ret, ret, 100, 5e9]
                        for i, name in enumerate(names) for j in range(members)]
                (report / "universe.json").write_text(json.dumps({
                    "fields": ["symbol", "asOf", "industry", "changePct", "r5", "r21", "r63", "close", "turnover20"],
                    "markets": {"TW": {"asOf": day, "scope": "full", "rows": rows}} if names else {}
                }))
            def cli():
                with patch.object(rotation, "read_bars", return_value=None):
                    return rotation.main(["--root", str(root)])
            inputs(sectors, 10)
            self.assertEqual(cli(), 0)
            original = out.read_bytes()
            inputs(sectors, 10, 2)
            self.assertEqual(cli(), 0)
            good = out.read_bytes()
            self.assertNotEqual(original, good)
            for names, members, ret in (([], 0, 1), (sectors[1:] + ["汽車工業"], 10, 1),
                                        (sectors, 3, 1), (sectors, 10, None)):
                with self.subTest(names=names, members=members, ret=ret):
                    inputs(names, members, ret)
                    self.assertEqual(cli(), 1)
                    self.assertEqual(out.read_bytes(), good)


if __name__ == "__main__":
    unittest.main()

"""Publication/storage regression tests; no external data or credentials required."""

import io
import os
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from research_io import read, write, semantic
from research_store import MarketStore, decode, encode, migrate
from research_publish import stage, overlay
from build_research import volume
from build_sec_radar import SecHttp, build as sec_build


class StablePublication(unittest.TestCase):
    def test_clocks_ignored_recursively_but_publication_dates_are_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            write(
                path,
                {
                    "generatedAt": "old",
                    "sources": [{"fetchedAt": "old", "publishedAt": "2026-10-21"}],
                },
            )
            original = path.read_bytes(), path.stat().st_mtime_ns
            write(
                path,
                {
                    "generatedAt": "new",
                    "sources": [{"fetchedAt": "new", "publishedAt": "2026-10-21"}],
                },
            )
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)
            write(path, {"sources": [{"publishedAt": "2026-10-22"}]})
            self.assertNotEqual(path.read_bytes(), original[0])

    def test_codec_round_trip_and_stable_shape_ids(self):
        shapes = {}
        value = {
            "空值": None,
            "negative": -123,
            "nested": [[], {}, {"date": "2026-10-21", "shares": 0}],
        }
        packed = encode(value, shapes)
        self.assertEqual(decode(packed, shapes), value)
        encode({"newShape": 10}, shapes)
        self.assertEqual(encode(value, shapes), packed)
        with self.assertRaises(ValueError):
            decode([packed[0]], shapes)

    def test_us_ticker_tw_is_not_confused_with_the_taiwan_market_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root / "volume/TW.json", {"symbol": "TW", "market": "US", "close": 100}
            )
            migrate(root)
            store = MarketStore(root, "volume")
            self.assertEqual(store.get("TW")["market"], "US")
            self.assertEqual(len(store.records["US"]), 1)
            self.assertEqual(store.records["TW"], {})

    def test_migration_preserves_every_value_and_byte_range_including_unicode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = {
                "symbol": "2330.TW",
                "name": "台積電",
                "generatedAt": "old",
                "rows": [
                    {"net": -1000, "fetchedAt": "old", "publishedAt": "2026-10-21"}
                ],
            }
            write(root / "institutions/2330.TW.json", old)
            write(root / "institutions.json", {"version": 1, "generatedAt": "old"})
            migrate(root)
            self.assertFalse((root / "institutions/2330.TW.json").exists())
            self.assertEqual(
                MarketStore(root, "institutions").get("2330.TW"), semantic(old)
            )
            spec = read(root / "institutions.json")["storage"]["markets"]["TW"]
            start, size, _ = spec["records"]["2330.TW"]
            body = (root / spec["path"]).read_bytes()
            import json

            self.assertEqual(
                decode(json.loads(body[start : start + size]), spec["shapes"]),
                semantic(old),
            )
            self.assertNotIn(b"generatedAt", body)
            self.assertNotIn(b"fetchedAt", body)

    def test_real_volume_builder_rerun_has_no_git_or_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv = root / "TEST.TW.csv"
            csv.write_text(
                "Date,Open,High,Low,Close,Volume\n"
                + "".join(f"2026-09-{i:02},10,12,9,11,{100+i}\n" for i in range(1, 29))
            )
            args = SimpleNamespace(
                out=root / "report",
                cache=root / "cache",
                as_of=date(2026, 10, 22),
                market="all",
            )

            def run():
                with patch(
                    "build_research.universe",
                    return_value={"TW": {"TEST.TW"}, "US": set()},
                ), patch("build_research.market_paths", return_value={"TEST.TW": csv}):
                    volume(args)

            with patch("research_io.stamp", return_value="first-run"):
                run()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            before = {
                p: (p.read_bytes(), p.stat().st_mtime_ns)
                for p in args.out.rglob("*.json")
            }
            with patch("research_io.stamp", return_value="second-run"), patch(
                "build_research.stamp", return_value="second-run"
            ):
                run()
            self.assertEqual(
                before,
                {
                    p: (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in args.out.rglob("*.json")
                },
            )
            subprocess.run(["git", "-C", str(root), "diff", "--exit-code"], check=True)
            self.assertEqual(
                subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(root),
                        "ls-files",
                        "--others",
                        "--exclude-standard",
                    ]
                ),
                b"",
            )

    def test_no_secret_sec_skips_without_requests_or_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sec-radar.json"
            write(path, {"version": 1, "events": [], "status": "stale"})
            original = path.read_bytes(), path.stat().st_mtime_ns
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), patch(
                "build_sec_radar.SecHttp"
            ) as transport, redirect_stdout(output):
                sec_build(SimpleNamespace(out=root))
                transport.assert_not_called()
            self.assertIn(
                "SEC module skipped: secrets.SEC_USER_AGENT is not set",
                output.getvalue(),
            )
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)

    def test_sec_requires_explicit_agent(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "no fallback"):
                SecHttp({})
        with patch.dict(os.environ, {"SEC_USER_AGENT": "Research test@example.com"}):
            self.assertEqual(SecHttp({}).agent, "Research test@example.com")

    def test_sec_rebuild_preserves_index_receipts_without_archiving_clocks(self):
        from build_sec_radar import event
        import time

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = event(
                "AAPL",
                "320193",
                "Apple",
                {
                    "form": "8-K",
                    "accessionNumber": "0000320193-26-000001",
                    "filingDate": "2026-10-21",
                },
                "original-receipt",
            )
            original.update(
                summaryStatus="parsed", summary="Actual document test excerpt"
            )
            store = MarketStore(root, "sec")
            store.put(
                "AAPL", {"symbol": "AAPL", "asOf": "2026-10-21", "events": [original]}
            )
            write(
                root / "sec-radar.json",
                {"version": 1, "storage": store.save(), "events": [original]},
            )
            write(root / "cache/sec-tickers.json", {"AAPL": "0000320193"})
            write(root / "cache/sec-radar-state.json", {"tickerMapAt": time.time()})
            args = SimpleNamespace(
                out=root,
                cache=root / "cache",
                days=90,
                limit=0,
                budget=0,
                symbols="",
                as_of=date(2026, 10, 22),
            )
            with patch.dict(
                os.environ, {"SEC_USER_AGENT": "Research test@example.com"}
            ), patch(
                "build_sec_radar.universe", return_value={"US": {"AAPL"}}
            ), patch.object(
                SecHttp, "get", side_effect=AssertionError("Unexpected request")
            ):
                sec_build(args)
            self.assertEqual(
                read(root / "sec-radar.json")["events"][0]["fetchedAt"],
                "original-receipt",
            )
            self.assertNotIn(
                "fetchedAt", MarketStore(root, "sec").get("AAPL")["events"][0]
            )
            # Re-querying a parsed event must not index a removed per-stock clock.
            args.limit, args.budget, args.days, args.symbols = 1, 100, 1, "AAPL"
            state = read(root / "cache/sec-radar-state.json")
            state["indexedDates"] = ["2026-10-21"]
            write(root / "cache/sec-radar-state.json", state)
            filing = {
                "cik": 320193,
                "name": "Apple",
                "entityType": "operating",
                "filings": {
                    "recent": {
                        "form": ["8-K"],
                        "accessionNumber": [original["accession"]],
                        "filingDate": [original["date"]],
                    }
                },
            }
            with patch.dict(
                os.environ, {"SEC_USER_AGENT": "Research test@example.com"}
            ), patch(
                "build_sec_radar.universe", return_value={"US": {"AAPL"}}
            ), patch.object(
                SecHttp, "json", return_value=filing
            ), patch.object(
                SecHttp, "get", side_effect=AssertionError("Unexpected request")
            ):
                sec_build(args)
            self.assertEqual(
                read(root / "sec-radar.json")["events"][0]["fetchedAt"],
                "original-receipt",
            )
            self.assertEqual(
                read(root / "sec-radar.json")["events"][0]["summary"],
                original["summary"],
            )

    def test_artifact_overlay_unchanged_and_checksum_failure_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, artifacts, dest = root / "data", root / "artifacts", root / "dest"
            store = MarketStore(data, "volume")
            store.put("AAPL", {"symbol": "AAPL", "value": 1})
            write(data / "volume.json", {"version": 1, "storage": store.save()})
            stage(data, artifacts, "volume")
            overlay(artifacts, dest)
            before = {
                p: (p.read_bytes(), p.stat().st_mtime_ns) for p in dest.rglob("*.json")
            }
            overlay(artifacts, dest)
            self.assertEqual(
                before,
                {
                    p: (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in dest.rglob("*.json")
                },
            )
            (artifacts / "volume/volume/US.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "checksum"):
                overlay(artifacts, dest)
            self.assertEqual(
                before,
                {
                    p: (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in dest.rglob("*.json")
                },
            )

    def test_artifact_rejects_unapproved_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                overlay(root, root / "out")

    def test_schedule_and_permission_boundaries(self):
        import yaml

        wf = yaml.safe_load(
            (
                Path(__file__).resolve().parents[1]
                / ".github/workflows/research-data.yml"
            ).read_text()
        )
        trigger = wf.get("on", wf.get(True))
        self.assertEqual(
            [v["cron"] for v in trigger["schedule"]], ["23 11 * * 1-5", "23 5 * * 2-6"]
        )
        self.assertEqual(wf["permissions"], {"contents": "read"})
        self.assertEqual(
            [
                k
                for k, job in wf["jobs"].items()
                if job.get("permissions", wf["permissions"]).get("contents") == "write"
            ],
            ["publish"],
        )
        steps = wf["jobs"]["refresh"]["steps"]
        run = next(
            s
            for s in steps
            if s.get("name") == "Refresh SEC with operator-provided contact only"
        )
        self.assertEqual(run["env"]["SEC_USER_AGENT"], "${{ secrets.SEC_USER_AGENT }}")
        self.assertTrue(any("upload-artifact" in s.get("uses", "") for s in steps))
        self.assertFalse(any("git push" in s.get("run", "") for s in steps))


if __name__ == "__main__":
    unittest.main()

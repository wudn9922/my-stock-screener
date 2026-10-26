"""Runner-scale synthetic benchmark; never fetches or publishes production data.

KLINE_ARCHIVE_PERFORMANCE=1 python -m unittest discover -s tests \
    -p test_kline_archive_performance.py -v
"""
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import kline_archive as ka
import kline_archive_git as kg


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


def sessions_ending(day, count=174):
    result = []
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day -= timedelta(days=1)
    return list(reversed(result))


@unittest.skipUnless(os.environ.get("KLINE_ARCHIVE_PERFORMANCE") == "1", "explicit runner-scale benchmark")
class ArchivePerformanceTests(unittest.TestCase):
    def test_daily_append_for_7000_symbols_and_21000_packed_year_files(self):
        with tempfile.TemporaryDirectory(prefix="kline-scale-") as temp:
            root = Path(temp)
            archive, remote, payload = root / "archive", root / "origin.git", root / "payload"
            remote.mkdir()
            git(remote, "init", "--bare", "--initial-branch=kline-archive")
            kg.initialize(archive, str(remote))
            git(archive, "config", "user.name", "Synthetic benchmark")
            git(archive, "config", "user.email", "benchmark@example.invalid")
            # Control fixture packing: commit's detached auto-gc can otherwise
            # race our explicit gc, and worker core counts must not multiply RAM.
            git(archive, "config", "gc.auto", "0")
            git(archive, "config", "pack.threads", "2")
            dates = {year: sessions_ending(date(year, 10, 23) if year == 2026 else date(year, 12, 31))
                     for year in (2024, 2025, 2026)}
            operations = []
            for number in range(7000):
                symbol = f"S{number:04d}"
                market = "tw" if number < 2000 else "us"
                if market == "tw":
                    symbol += ".TW"
                folder = archive / market / symbol
                folder.mkdir(parents=True)
                for year, days in dates.items():
                    rows = []
                    for index, day in enumerate(days):
                        # Distinct, canonical data creates real packed/delta blobs;
                        # identical per-symbol templates would hide Git read costs.
                        price = 100 + ((number * 17 + index * 13 + year) % 100000) / 1000
                        volume = 1000 + (number * 7919 + index * 104729) % 100000000
                        rows.append(f"{day},{price:.4f},{price + 1:.4f},{price - 1:.4f},{price:.4f},{volume}\n")
                    (folder / f"{year}.csv").write_text(",".join(ka.FIELDS) + "\n" + "".join(rows))
                relative = f"{market}/{symbol}.csv"
                target = payload / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                content = ka.csv_text([ka.normalize(["2026-10-26", 100, 101, 99, 100, 1000 + number])])
                target.write_text(content)
                operations.append(dict(symbol=symbol, file=relative, sha256=ka.digest(content.encode()), correction=None))
            ka.update_manifest(archive)
            git(archive, "add", ".")
            git(archive, "commit", "-m", "Synthetic 7000-symbol baseline")
            git(archive, "push", "origin", "HEAD:refs/heads/kline-archive")
            git(archive, "gc", "--quiet")
            # Exercise both pre-rollover and final transport snapshots without
            # applying the same accepted daily operation twice.
            plan = dict(version=1, mode="daily", symbols=operations, skipped=[])
            ka.json_write(payload / "baseline/operations.json", plan)
            for item in operations:
                target = payload / "baseline" / item["file"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((payload / item["file"]).read_bytes())
            ka.json_write(payload / "operations.json", dict(plan, baseline=True))
            original_verify, original_run = ka.verify, subprocess.run
            verifications, batches = [], []

            def measured(*args, **kwargs):
                started = time.perf_counter()
                result = original_verify(*args, **kwargs)
                verifications.append((time.perf_counter() - started, result))
                return result

            def inspect(command, **kwargs):
                if "cat-file" in command and "--batch" in command:
                    batches.append(command)
                if "show" in command and str(command[-1]).endswith(".csv"):
                    self.fail("Per-file git show regressed into daily verification")
                return original_run(command, **kwargs)

            started = time.perf_counter()
            with patch.object(ka, "verify", side_effect=measured), patch.object(ka.subprocess, "run", side_effect=inspect), patch.object(ka, "datetime", wraps=datetime) as clock:
                clock.now.return_value = datetime(2026, 10, 27, 2, tzinfo=timezone.utc)
                self.assertTrue(kg.publish(archive, payload, sleep=lambda _: None))
            publish_seconds = time.perf_counter() - started
            self.assertEqual(len(verifications), 1)
            verification_seconds, result = verifications[0]
            self.assertEqual(result["files"], 21000)
            self.assertEqual(result["rows"], 3661000)
            self.assertEqual(len(batches), 1)
            manifest = ka.json_read(archive / "manifest.json", {})
            self.assertEqual(sum(part["symbolCount"] for part in manifest["markets"].values()), 7000)
            for part in manifest["markets"].values():
                self.assertEqual(part["lastDate"], "2026-10-26")
            self.assertEqual(git(remote, "rev-parse", "refs/heads/kline-archive"), git(archive, "rev-parse", "HEAD"))
            report = dict(symbols=7000, files=21000, rows=result["rows"],
                          verificationSeconds=round(verification_seconds, 3),
                          dailyPublishSeconds=round(publish_seconds, 3), baselineBatchProcesses=len(batches))
            print("Synthetic archive performance: " + json.dumps(report), flush=True)
            if os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
                    stream.write("### Synthetic daily archive benchmark\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n")
            self.assertLess(verification_seconds, 300, "Daily verification must take under five minutes")


if __name__ == "__main__":
    unittest.main()

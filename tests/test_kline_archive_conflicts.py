"""Bounded conflict storage, real-artifact replay and overlapping-source contracts."""
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import kline_archive as ka
import analyze_kline_archive_payload as analysis

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


def bar(day, bump=0):
    return ka.normalize([day, 100 + bump, 101 + bump, 99 + bump, 100 + bump, 1000])


def transport(folder, rows, baseline=True):
    operations = []
    for symbol, bars in rows.items():
        relative = f"{ka.market_of(symbol)}/{symbol}.csv"
        body = ka.csv_text(bars)
        ka.atomic(folder / relative, body)
        operations.append(dict(symbol=symbol, file=relative, sha256=ka.digest(body.encode()), correction=None))
    plan = dict(version=1, mode="daily", symbols=operations)
    ka.json_write(folder / "operations.json", dict(plan, baseline=True) if baseline else plan)
    if baseline:
        transport(folder / "baseline", rows, False)


class ConflictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive, self.payload = self.root / "archive", self.root / "payload"
        ka.initialize(self.archive)

    def test_most_of_320_baseline_rows_disagree_but_one_batch_write_and_bounded_size(self):
        days = [(datetime(2025, 1, 1) + timedelta(days=i)).date().isoformat() for i in range(320)]
        old, incoming = [bar(d) for d in days], [bar(d, .0001) for d in days]
        symbols = [f"S{i:03d}" + (".TW" if i % 2 else "") for i in range(80)]
        for symbol in symbols:
            ka.put_symbol(self.archive, symbol, old)
        transport(self.payload, {s: incoming + [bar("2026-10-26")] for s in symbols})
        original_write, original_read = ka.json_write, ka.read_rows
        writes, reads = [], []

        def write(path, value):
            if str(path).endswith(ka.CONFLICTS):
                writes.append(path)
            return original_write(path, value)

        def read(path, **kwargs):
            if self.archive in Path(path).parents:
                reads.append(Path(path))
            return original_read(path, **kwargs)

        started = time.perf_counter()
        progress = io.StringIO()
        with patch.object(ka, "json_write", side_effect=write), patch.object(ka, "read_rows", side_effect=read), patch("sys.stdout", progress):
            result = ka.apply_operations(self.archive, self.payload, now=NOW, rebuild_manifest=False)
        self.assertLess(time.perf_counter() - started, 20)
        self.assertEqual(result, dict(added=80, corrected=0, conflicts=25600, blocked=0))
        self.assertEqual(len(writes), 1)
        self.assertEqual(len(reads), 80)  # No repeated archive read for identical final snapshots.
        self.assertIn("validate start", progress.getvalue())
        self.assertIn("merge 80/80", progress.getvalue())
        self.assertIn("duplicateOperations=80", progress.getvalue())
        self.assertIn("K-line timing: apply.conflicts_flush", progress.getvalue())
        state = ka.json_read(self.archive / ka.CONFLICTS, {})
        self.assertEqual(state["version"], 2)
        self.assertEqual(len(state["conflicts"]), 80)
        self.assertTrue(all(e["count"] == 320 and len(e["samples"]) == 3 for e in state["conflicts"].values()))
        self.assertLess((self.archive / ka.CONFLICTS).stat().st_size, 100000)
        for symbol in symbols:
            self.assertEqual(ka.symbol_rows(self.archive, symbol), old + [bar("2026-10-26")])
        before = (self.archive / ka.CONFLICTS).read_bytes()
        with patch.object(ka, "json_write", side_effect=write):
            ka.apply_operations(self.archive, self.payload, now=NOW, rebuild_manifest=False)
        self.assertEqual(len(writes), 1)
        self.assertEqual((self.archive / ka.CONFLICTS).read_bytes(), before)

    def test_v1_migration_exactly_deduplicates_dates_across_leap_year_and_revisions(self):
        entries = []
        for day, bump in (("2024-02-29", .0001), ("2024-02-29", .0002), ("2024-12-31", .0001), ("2025-01-01", .0001)):
            entries.append(dict(symbol="AAPL", date=day, old=list(bar(day)), incoming=list(bar(day, bump)),
                                reason="existing-row-disagrees", firstObservedAt="2026-10-23T00:00:00Z"))
        ka.json_write(self.archive / ka.CONFLICTS, dict(version=1, conflicts={str(i): e for i, e in enumerate(entries)}))
        batch = ka._ConflictBatch(self.archive)
        batch.commit()
        state = ka.json_read(self.archive / ka.CONFLICTS, {})
        self.assertEqual(len(state["conflicts"]), 1)
        entry = next(iter(state["conflicts"].values()))
        self.assertEqual(entry["count"], 3)
        self.assertEqual(entry["firstDate"], "2024-02-29")
        self.assertEqual(entry["lastDate"], "2025-01-01")
        self.assertEqual(entry["firstObservedAt"], "2026-10-23T00:00:00Z")
        self.assertEqual(sum(int(bits, 16).bit_count() for bits in entry["dateBits"].values()), 3)
        self.assertEqual(len(entry["samples"]), 3)
        before = (self.archive / ka.CONFLICTS).read_bytes()
        for e in entries:
            ka.conflict(self.archive, e["symbol"], e["incoming"], e["old"], e["reason"])
        self.assertEqual((self.archive / ka.CONFLICTS).read_bytes(), before)
        ka.conflict(self.archive, "AAPL", bar("2025-01-01"), None, "adjustment-source-does-not-cover-retained-history")
        self.assertEqual(len(ka.json_read(self.archive / ka.CONFLICTS, {})["conflicts"]), 2)

    def test_conflicts_flush_on_a_later_operation_error_without_overwriting_rows(self):
        ka.put_symbol(self.archive, "AAPL", [bar("2026-10-23")])
        transport(self.payload, {"AAPL": [bar("2026-10-23", .0001)], "MSFT": [bar("2026-10-23")]}, baseline=False)
        plan = ka.json_read(self.payload / "operations.json", {})
        plan["symbols"][1]["correction"] = {"id": "invalid"}
        ka.json_write(self.payload / "operations.json", plan)
        with self.assertRaisesRegex(ValueError, "cache comparison witness"):
            ka.apply_operations(self.archive, self.payload, now=NOW)
        self.assertEqual(next(iter(ka.json_read(self.archive / ka.CONFLICTS, {})["conflicts"].values()))["count"], 1)
        self.assertEqual(ka.symbol_rows(self.archive, "AAPL"), [bar("2026-10-23")])

    def test_identical_snapshot_still_requires_digest_validation(self):
        transport(self.payload, {"AAPL": [bar("2026-10-23")]})
        (self.payload / "baseline/us/AAPL.csv").write_text(ka.csv_text([bar("2026-10-23", .0001)]))
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            ka.apply_operations(self.archive, self.payload, now=NOW)
        self.assertEqual(ka.symbol_rows(self.archive, "AAPL"), [])

    def test_duplicate_final_file_mutated_after_prevalidation_is_rejected(self):
        transport(self.payload, {"AAPL": [bar("2026-10-23")]})
        original = ka.put_symbol

        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.payload / "us/AAPL.csv").write_text(ka.csv_text([bar("2026-10-23", .0001)]))
            return result

        with patch.object(ka, "put_symbol", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "changed during apply"):
                ka.apply_operations(self.archive, self.payload, now=NOW)
        self.assertEqual(ka.symbol_rows(self.archive, "AAPL"), [bar("2026-10-23")])

    def test_sample_selection_is_order_independent_and_unknown_schema_fails_closed(self):
        observations = [(bar("2024-02-29", .0002), bar("2024-02-29")),
                        (bar("2025-01-01", .0001), None),
                        (bar("2024-02-29", .0001), bar("2024-02-29")),
                        (bar("2024-12-31", .0001), bar("2024-12-31")),
                        (bar("2026-10-23", .0001), bar("2026-10-23"))]
        batches = []
        for order in (observations, list(reversed(observations))):
            batch = ka._ConflictBatch(self.archive)
            for incoming, old in order:
                batch.add("AAPL", incoming, old, "existing-row-disagrees", "2026-10-23T00:00:00Z")
            batches.append(batch.entries)
        self.assertEqual(batches[0], batches[1])
        self.assertEqual(next(iter(batches[0].values()))["count"], 4)
        ka.json_write(self.archive / ka.CONFLICTS, dict(version=999, conflicts={}))
        with self.assertRaisesRegex(ValueError, "Unknown archive conflict schema"):
            ka._ConflictBatch(self.archive)

    def test_real_artifact_subset_preserves_history_and_replays_without_growth(self):
        with zipfile.ZipFile(ROOT / "tests/fixtures/kline-daily-40317163328-subset.zip") as fixture:
            fixture.extractall(self.root)
        metadata = ka.json_read(self.root / "provenance.json", {})
        before = {p.relative_to(self.archive): ka.read_rows(p, strict=True) for p in ka.archive_files(self.archive)}
        comparison = analysis.analyze(self.archive, self.payload)
        self.assertEqual(comparison, metadata["comparison"])
        expected = dict(added=sum(g["appendable"] for g in comparison["markets"].values()), corrected=0,
                        conflicts=sum(g["differ"] for g in comparison["markets"].values()), blocked=0)
        started = time.perf_counter()
        result = ka.apply_operations(self.archive, self.payload, now=NOW)
        self.assertLess(time.perf_counter() - started, 10)
        self.assertEqual(result, expected)
        self.assertEqual(result["conflicts"], 71)
        for name, rows in before.items():
            after = {r[0]: r for r in ka.read_rows(self.archive / name, strict=True)}
            self.assertTrue(all(after.get(r[0]) == r for r in rows))
        ka.verify(self.archive)
        snapshot = {p.relative_to(self.archive): p.read_bytes() for p in self.archive.rglob("*") if p.is_file()}
        ka.apply_operations(self.archive, self.payload, now=NOW)
        self.assertEqual(snapshot, {p.relative_to(self.archive): p.read_bytes() for p in self.archive.rglob("*") if p.is_file()})
        self.assertLess((self.archive / ka.CONFLICTS).stat().st_size, 12000)


if __name__ == "__main__":
    unittest.main()

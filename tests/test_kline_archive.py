"""Offline permanent-archive contracts. Synthetic prices never ship as market data."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests")]
import kline_archive as ka
import kline_archive_git as kg
import backfill_kline_archive as bf
import kline_integrity as ki
import universe_cache as uc
import build_universe_full as bu
import universe_sources as sources

NOW = datetime(2026, 10, 24, 1, tzinfo=timezone.utc)


def row(day, close=100, volume=1000):
    return ka.normalize([day, close, close + 1, close - 1, close, volume])


def frame(rows):
    return pd.DataFrame([[float(v) if v != "" else float("nan") for v in r[1:]] for r in rows],
                        index=pd.to_datetime([r[0] for r in rows]), columns=uc.PRICE_COLUMNS)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL).decode().strip()


class _ArchiveCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = self.root / "archive"
        self.cache = self.root / "cache"
        self.payload = self.root / "payload"
        self.log = self.root / "main-log.json"
        self.patch = patch.object(ki, "LOG_PATH", self.log)
        self.patch.start()
        ka.initialize(self.archive)

    def tearDown(self):
        self.patch.stop()
        ki._load_log.cache_clear()
        self.temp.cleanup()

    def seed(self, symbol="AAPL", rows=None):
        ka.put_symbol(self.archive, symbol, rows or [row("2025-12-31"), row("2026-01-02")])
        ka.update_manifest(self.archive)

    def cache_rows(self, symbol, rows):
        uc.write_bars(uc.bars_path(str(self.cache), symbol), frame(rows), now=NOW)
        state = uc.load_state(str(self.cache))
        uc.record_frame(state, symbol, frame(rows), NOW.date().isoformat(), full=True)
        uc.save_state(str(self.cache), state)

    def checkpoint(self):
        if not (self.archive / ".git").exists():
            git(self.archive, "init", "--initial-branch=kline-archive")
            git(self.archive, "config", "user.name", "Test")
            git(self.archive, "config", "user.email", "test@example.invalid")
        git(self.archive, "add", ".")
        git(self.archive, "commit", "-m", "Test baseline")
        return git(self.archive, "rev-parse", "HEAD")


class ArchiveTests(_ArchiveCase):
    def test_append_only_touches_current_year_and_rerun_changes_no_files(self):
        self.seed()
        old = self.archive / "us/AAPL/2025.csv"
        before = old.read_bytes(), old.stat().st_mtime_ns
        result = ka.put_symbol(self.archive, "AAPL", [row("2026-01-02"), row("2026-01-05")])
        self.assertEqual(result["added"], 1)
        self.assertEqual((old.read_bytes(), old.stat().st_mtime_ns), before)
        ka.update_manifest(self.archive)
        snapshot = {p.relative_to(self.archive): p.read_bytes() for p in self.archive.rglob("*") if p.is_file()}
        ka.put_symbol(self.archive, "AAPL", [row("2026-01-02"), row("2026-01-05")])
        ka.update_manifest(self.archive)
        self.assertEqual(snapshot, {p.relative_to(self.archive): p.read_bytes() for p in self.archive.rglob("*") if p.is_file()})
        ka.verify(self.archive)

    def test_removed_or_renamed_symbols_never_lose_their_files(self):
        self.seed("OLD")
        old = (self.archive / "us/OLD/2025.csv").read_bytes()
        self.cache_rows("NEW", [row("2026-10-23")])
        ka.stage_cache(self.cache, self.archive, self.payload, NOW)
        ka.apply_operations(self.archive, self.payload)
        self.assertEqual((self.archive / "us/OLD/2025.csv").read_bytes(), old)
        self.assertTrue((self.archive / "us/NEW/2026.csv").exists())
        self.assertEqual(ka.json_read(self.archive / "manifest.json", {})["markets"]["us"]["symbols"], ["NEW", "OLD"])

    def test_first_run_preserves_unlisted_bars_pruned_before_daily_save(self):
        self.cache_rows("DELISTED", [row("2026-01-02")])
        baseline = self.root / "baseline"
        ka.stage_cache(self.cache, self.archive, baseline, NOW)
        Path(uc.bars_path(str(self.cache), "DELISTED")).unlink()
        uc.save_state(str(self.cache), {"version": uc.STATE_VERSION, "symbols": {}})
        ka.stage_cache(self.cache, self.archive, self.payload, NOW, previous=baseline)
        ka.apply_operations(self.archive, self.payload)
        self.assertEqual(ka.symbol_rows(self.archive, "DELISTED"), [row("2026-01-02")])

    def test_first_run_preserves_a_bar_dropped_by_the_rolling_window(self):
        days = pd.bdate_range("2024-01-01", periods=uc.KEEP_BARS + 1)
        rows = [row(str(day.date())) for day in days]
        self.cache_rows("AAPL", rows[:-1])
        baseline = self.root / "baseline"
        ka.stage_cache(self.cache, self.archive, baseline, NOW)
        self.cache_rows("AAPL", rows[1:])
        ka.stage_cache(self.cache, self.archive, self.payload, NOW, previous=baseline)
        ka.apply_operations(self.archive, self.payload)
        self.assertEqual(ka.symbol_rows(self.archive, "AAPL"), rows)

    def test_unknown_volume_round_trips_empty_and_known_zero_stays_zero(self):
        rows = [row("2026-10-22", volume=None), row("2026-10-23", volume=0)]
        self.cache_rows("AAPL", rows)
        cache_text = Path(uc.bars_path(str(self.cache), "AAPL")).read_text()
        self.assertIn("2026-10-22,100.0,101.0,99.0,100.0,\n", cache_text)
        ka.stage_cache(self.cache, self.archive, self.payload, NOW)
        ka.apply_operations(self.archive, self.payload)
        self.assertEqual(ka.symbol_rows(self.archive, "AAPL"), rows)
        self.assertEqual(rows[0][-1], "")
        self.assertEqual(rows[1][-1], "0")

    def test_four_decimal_rounding_matches_the_existing_cache(self):
        value = 1.23445
        result = ka.normalize(["2026-01-02", value, 2, 1, value, None])
        self.assertEqual(float(result[4]), float(uc._price(value)))

    def test_attrs_volume_lineage_survives_merges_adjustment_staging_and_archive(self):
        rows = [row("2026-10-21", volume=None), row("2026-10-22", volume=0)]
        compatible = uc.clean(frame(rows))
        self.assertEqual(compatible["Volume"].tolist(), [0, 0])
        self.assertEqual(compatible.attrs[uc.UNKNOWN_VOLUME_DATES], ("2026-10-21T00:00:00",))
        merged = uc.merge_incremental(compatible, uc.clean(frame([row("2026-10-23")])))
        merged = uc.merge_full(merged, compatible)
        expected = rows + [row("2026-10-23")]
        self.assertEqual(ka.frame_rows(uc.clean(merged)), expected)
        ka.stage_adjustment(self.cache, "AAPL", merged, {}, NOW)
        staged = self.cache / "archive-adjustments/AAPL.csv"
        self.assertEqual(ka.read_rows(staged, strict=True), expected)
        # The cache and the archive serialize the same source cells, while all
        # existing calculation consumers keep their attrs-based zero fallback.
        path = uc.bars_path(str(self.cache), "AAPL")
        uc.write_bars(path, merged, now=NOW)
        self.assertEqual(ka.read_rows(path), expected)
        ka.put_symbol(self.archive, "AAPL", ka.frame_rows(uc.read_bars(path)))
        self.assertEqual(ka.symbol_rows(self.archive, "AAPL"), expected)

    def test_partial_or_unfinished_cache_entries_are_not_archived(self):
        self.cache_rows("AAPL", [row("2026-10-23")])
        state = uc.load_state(str(self.cache))
        state["symbols"]["AAPL"]["last"] = "2026-10-22"
        uc.save_state(str(self.cache), state)
        plan = ka.stage_cache(self.cache, self.archive, self.payload, NOW)
        self.assertEqual(len(plan["skipped"]), 1)
        self.assertFalse(plan["symbols"])
        self.cache_rows("AAPL", [row("2026-10-23")])
        before_us_close = datetime(2026, 10, 23, 18, tzinfo=timezone.utc)
        self.assertFalse(ka.stage_cache(self.cache, self.archive, self.payload, before_us_close)["symbols"])

    def test_disagreement_is_logged_once_and_never_overwrites(self):
        self.seed()
        result = ka.put_symbol(self.archive, "AAPL", [row("2026-01-02", close=999)])
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(ka.symbol_rows(self.archive, "AAPL")[-1][4], "100.0000")
        original = (self.archive / ka.CONFLICTS).read_bytes()
        ka.put_symbol(self.archive, "AAPL", [row("2026-01-02", close=999)])
        self.assertEqual((self.archive / ka.CONFLICTS).read_bytes(), original)

    def test_daily_import_never_inserts_old_missing_dates(self):
        self.seed(rows=[row("2026-01-02"), row("2026-01-06")])
        ka.put_symbol(self.archive, "AAPL", [row("2026-01-05")])
        self.assertEqual([r[0] for r in ka.symbol_rows(self.archive, "AAPL")], ["2026-01-02", "2026-01-06"])

    def test_split_three_years_emits_three_hash_audits_and_main_grows_by_at_most_three(self):
        days = ["2023-01-03", "2023-12-29", "2024-01-02", "2024-12-31", "2025-01-02", "2025-12-31"]
        self.seed(rows=[row(day) for day in days])
        self.seed("MSFT")
        untouched = (self.archive / "us/MSFT/2025.csv").read_bytes()
        base = self.checkpoint()
        old_hashes = {p.stem: ka.digest(p.read_bytes()) for p in (self.archive / "us/AAPL").glob("*.csv")}
        source = [row(day, close=50) for day in days] + [row("2026-10-23", close=55)]
        proof = {"id": "cache-split", "comparison": {"date": "2025-12-31", "oldClose": 100, "newClose": 50, "worst": 0.5}}
        result = ka.put_symbol(self.archive, "AAPL", source, correction=proof, fetched_at=NOW.isoformat())
        self.assertEqual(result["corrected"], 6)
        self.assertEqual(result["added"], 1)
        events = ki.read_log(self.archive / ka.LOG)["events"]
        self.assertEqual(len(events), 3)
        for event in events:
            year = str(event["year"])
            self.assertEqual(event["new"]["path"], f"us/AAPL/{year}.csv")
            self.assertEqual(event["old"]["sha256"], old_hashes[year])
            self.assertEqual(event["new"]["sha256"], ka.digest((self.archive / event["new"]["path"]).read_bytes()))
            self.assertEqual(event["new"]["rowsChanged"], 2)
            self.assertEqual(event["comparison"], proof["comparison"])
            self.assertEqual(event["adjustmentId"], proof["id"])
            self.assertEqual(event["reason"], "kline-archive/adjustment-year:cache-split")
            self.assertEqual(event["fetchedAt"], NOW.isoformat())
            self.assertTrue(event["sourceUrl"].endswith("/AAPL"))
            self.assertNotIn("Close", event["old"])
        self.assertEqual((self.archive / "us/MSFT/2025.csv").read_bytes(), untouched)
        ka.update_manifest(self.archive)
        ka.verify(self.archive, base)
        legacy = ka.event("MSFT", row("2026-01-02", close=30), row("2026-01-02"), "corrected", "independent-data-correction")
        ka.json_write(self.log, ki.merge_logs(ki.read_log(self.log), {"version": 1, "events": [legacy]}))
        ka.merge_main_log(self.archive, self.log)
        self.assertEqual(len(ki.read_log(self.log)["events"]), 4)
        self.assertFalse(ka.merge_main_log(self.archive, self.log))
        self.assertEqual(ka.put_symbol(self.archive, "AAPL", source, correction=proof)["corrected"], 0)
        self.assertEqual(len(ki.read_log(self.archive / ka.LOG)["events"]), 3)

    def test_logged_split_does_not_authorize_one_extra_unlogged_old_row_edit(self):
        self.seed(rows=[row("2025-01-02"), row("2025-12-31")])
        base = self.checkpoint()
        proof = {"id": "cache-split", "comparison": {"date": "2025-12-31", "oldClose": 100, "newClose": 50}}
        ka.put_symbol(self.archive, "AAPL", [row("2025-01-02", close=50), row("2025-12-31", close=50)], correction=proof)
        ka.update_manifest(self.archive)
        ka.verify(self.archive, base)
        path = self.archive / "us/AAPL/2025.csv"
        path.write_text(ka.csv_text([row("2025-01-02", close=49), row("2025-12-31", close=50)]))
        ka.update_manifest(self.archive)
        with self.assertRaisesRegex(ValueError, "Unlogged historical"):
            ka.verify(self.archive, base)

    def test_transaction_audit_spans_baseline_append_adjustment_and_later_daily_append(self):
        days = ["2025-12-31", "2026-10-21"]
        self.seed(rows=[row(day) for day in days])
        base = self.checkpoint()
        # The old cache has one accepted session the archive has not seen yet.
        self.cache_rows("AAPL", [row("2026-10-21"), row("2026-10-22")])
        baseline = self.root / "baseline"
        ka.stage_cache(self.cache, self.archive, baseline, NOW)
        source = [row(day, close=50) for day in days + ["2026-10-22"]]
        proof = {"date": "2026-10-21", "oldClose": 100, "newClose": 50, "worst": 0.5}
        ka.stage_adjustment(self.cache, "AAPL", uc.clean(frame(source)), proof, NOW)
        self.cache_rows("AAPL", source + [row("2026-10-23", close=55)])
        ka.stage_cache(self.cache, self.archive, self.payload, NOW, previous=baseline)
        ka.apply_operations(self.archive, self.payload, self.log)
        ka.verify(self.archive, base)
        events = ki.read_log(self.archive / ka.LOG)["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(sorted(e["new"]["rowsChanged"] for e in events), [1, 1])
        self.assertEqual(len(ki.read_log(self.log)["events"]), 2)

        # A baseline can also contain the correction itself. The final same-id
        # payload appends another session after it without re-correcting old rows.
        base = self.checkpoint()
        source = [row(day, close=25) for day in days + ["2026-10-22", "2026-10-23"]]
        proof = dict(proof, oldClose=50, newClose=25)
        ka.stage_adjustment(self.cache, "AAPL", uc.clean(frame(source)), proof, NOW)
        self.cache_rows("AAPL", source)
        baseline2, payload2 = self.root / "baseline2", self.root / "payload2"
        ka.stage_cache(self.cache, self.archive, baseline2, NOW)
        later = NOW + timedelta(days=4)
        # Use a later completed session for the transaction's appended suffix.
        path = uc.bars_path(str(self.cache), "AAPL")
        uc.write_bars(path, frame(source + [row("2026-10-26", close=27)]), now=later)
        state = uc.load_state(str(self.cache))
        uc.record_frame(state, "AAPL", uc.read_bars(path), later.date().isoformat(), full=True)
        uc.save_state(str(self.cache), state)
        ka.stage_cache(self.cache, self.archive, payload2, later, previous=baseline2)
        ka.apply_operations(self.archive, payload2, self.log, now=later)
        ka.verify(self.archive, base)
        self.assertEqual(len(ki.read_log(self.archive / ka.LOG)["events"]), 4)

    def test_year_hash_receipt_never_allows_a_deleted_date_or_file(self):
        self.seed(rows=[row("2025-01-02"), row("2025-12-31")])
        base = self.checkpoint()
        proof = {"id": "split", "comparison": {"date": "2025-12-31", "oldClose": 100, "newClose": 50}}
        ka.put_symbol(self.archive, "AAPL", [row("2025-01-02", close=50), row("2025-12-31", close=50)], correction=proof)
        path = self.archive / "us/AAPL/2025.csv"
        path.write_text(ka.csv_text([row("2025-12-31", close=50)]))
        ka.update_manifest(self.archive)
        with self.assertRaisesRegex(ValueError, "dates deleted"):
            ka.verify(self.archive, base)
        path.unlink()
        ka.update_manifest(self.archive)
        with self.assertRaisesRegex(ValueError, "file deleted"):
            ka.verify(self.archive, base)

    def test_multiple_adjustment_hash_edges_accept_a_chain_independent_of_log_order(self):
        days = ["2025-01-02", "2025-12-31"]
        self.seed(rows=[row(day) for day in days])
        base = self.checkpoint()
        for adjustment, old, new in (("first", 100, 50), ("second", 50, 25)):
            proof = {"id": adjustment, "comparison": {"date": days[-1], "oldClose": old, "newClose": new}}
            ka.put_symbol(self.archive, "AAPL", [row(day, close=new) for day in days], correction=proof)
        log = ki.read_log(self.archive / ka.LOG)
        log["events"].reverse()
        ka.json_write(self.archive / ka.LOG, log)
        ka.update_manifest(self.archive)
        ka.verify(self.archive, base)
        # Breaking one hash edge cannot be hidden by the other valid adjustment.
        log["events"].pop()
        ka.json_write(self.archive / ka.LOG, log)
        ki._load_log.cache_clear()
        ka.update_manifest(self.archive)
        with self.assertRaisesRegex(ValueError, "Unlogged historical"):
            ka.verify(self.archive, base)

    def test_short_adjustment_download_cannot_truncate_or_mix_old_years(self):
        self.seed(rows=[row("2023-12-29"), row("2024-01-02")])
        before = ka.symbol_rows(self.archive, "AAPL")
        proof = {"id": "cache-split", "comparison": {"date": "2024-01-02", "oldClose": 100, "newClose": 50}}
        result = ka.put_symbol(self.archive, "AAPL", [row("2024-01-02", close=50)], correction=proof)
        self.assertTrue(result["blocked"])
        self.assertEqual(ka.symbol_rows(self.archive, "AAPL"), before)

    def test_unproven_adjustment_is_rejected(self):
        self.seed()
        with self.assertRaisesRegex(ValueError, "witness"):
            ka.put_symbol(self.archive, "AAPL", [row("2026-01-02", close=50)], correction={"id": "invented"})

    def test_bootstrap_latest_keep_bars_preserves_unknown_and_archive_bytes(self):
        days = pd.bdate_range("2024-01-01", periods=400)
        rows = [row(str(day.date()), volume=None if i == 399 else 100) for i, day in enumerate(days)]
        self.seed("2330.TW", rows)
        before = {p: p.read_bytes() for p in (self.archive / "tw").rglob("*.csv")}
        self.assertEqual(ka.bootstrap(self.archive, self.cache, NOW), 1)
        restored = ka.read_rows(uc.bars_path(str(self.cache), "2330.TW"))
        self.assertEqual(restored, rows[-uc.KEEP_BARS:])
        self.assertTrue(uc.is_complete(uc.load_state(str(self.cache))["symbols"]["2330.TW"]))
        self.assertEqual(ka.bootstrap(self.archive, self.cache, NOW), 0)
        self.assertEqual(before, {p: p.read_bytes() for p in (self.archive / "tw").rglob("*.csv")})

    def test_backfill_inserts_audited_history_and_keeps_conflicting_account_rows(self):
        self.seed(rows=[row("2026-01-02")])
        base = self.checkpoint()
        result = ka.put_symbol(self.archive, "AAPL", [row("2024-01-02"), row("2026-01-02", close=9)], mode="backfill")
        self.assertEqual((result["added"], result["conflicts"]), (1, 1))
        ka.update_manifest(self.archive)
        ka.verify(self.archive, base)
        self.assertEqual(ka.symbol_rows(self.archive, "AAPL")[-1][4], "100.0000")
        # One compact before/after file receipt, not hundreds of per-bar backfill logs.
        audit = ki.read_log(self.archive / ka.LOG)["events"]
        self.assertEqual(audit[0]["status"], "backfilled")
        self.assertEqual(audit[0]["new"]["path"], "us/AAPL/2024.csv")

    def test_payload_integrity_failure_writes_nothing(self):
        self.cache_rows("AAPL", [row("2026-10-23")])
        ka.stage_cache(self.cache, self.archive, self.payload, NOW)
        (self.payload / "us/AAPL.csv").write_text("corrupted")
        before = (self.archive / "manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "digest"):
            ka.apply_operations(self.archive, self.payload)
        self.assertEqual((self.archive / "manifest.json").read_bytes(), before)
        self.assertFalse((self.archive / "us").exists())

    def test_empty_archive_journal_does_not_reformat_or_touch_main(self):
        content = json.dumps({"version": 1, "quarantined": [], "events": []}, indent=2) + "\n"
        self.log.write_text(content)
        before = self.log.stat().st_mtime_ns
        self.assertFalse(ka.merge_main_log(self.archive, self.log))
        self.assertEqual(self.log.read_text(), content)
        self.assertEqual(self.log.stat().st_mtime_ns, before)

    def test_main_journal_only_copies_compact_adjustments_not_legacy_rows_or_backfills(self):
        self.seed()
        ka.put_symbol(self.archive, "AAPL", [row("2024-01-02")], mode="backfill")
        legacy = ka.event("AAPL", row("2025-12-31", close=50), row("2025-12-31"),
                          "corrected", "kline-archive/adjustment:legacy-row")
        ka.add_events(self.archive, [legacy])
        self.assertFalse(ka.merge_main_log(self.archive, self.log))
        self.assertFalse(self.log.exists())

    def test_verifier_rejects_deletion_and_unlogged_past_edits(self):
        self.seed()
        base = self.checkpoint()
        path = self.archive / "us/AAPL/2026.csv"
        original = path.read_text()
        for content in (ka.csv_text([row("2026-01-02", close=90)]), ka.csv_text([row("2026-01-02"), row("2026-01-01")])):
            path.write_text(content)
            with self.assertRaises(ValueError):
                ka.verify(self.archive, base)
        path.write_text(original)
        path.unlink()
        ka.update_manifest(self.archive)
        with self.assertRaisesRegex(ValueError, "deleted"):
            ka.verify(self.archive, base)

    def test_validator_checks_year_sorted_unique_ohlc_precision_and_volume(self):
        self.seed(rows=[row("2026-01-02")])
        path = self.archive / "us/AAPL/2026.csv"
        for bad in (
            "2025-01-02,100.0000,101.0000,99.0000,100.0000,1\n",
            "2026-01-02,100.0000,90.0000,99.0000,100.0000,1\n",
            "2026-01-02,100.0000,101.0000,99.0000,0.0000,1\n",
            "2026-01-02,100.0000,101.0000,99.0000,100.0000,-1\n",
            "2026-01-02,100.0000,101.0000,99.0000,100.0000,1.5\n",
            "2026-01-02,100,101,99,100,1\n",
        ):
            with self.subTest(bad=bad):
                path.write_text(",".join(ka.FIELDS) + "\n" + bad)
                with self.assertRaises(ValueError):
                    ka.verify(self.archive)

    def test_manifest_totals_and_job_summary_are_exact(self):
        self.seed("2330.TW")
        self.seed("AAPL")
        summary = self.root / "summary.md"
        result = ka.verify(self.archive, summary=summary)
        size = sum(p.stat().st_size for p in self.archive.rglob("*") if p.is_file())
        self.assertEqual(result["totalBytes"], size)
        manifest = ka.json_read(self.archive / "manifest.json", {})
        self.assertEqual(manifest["markets"]["tw"]["firstDate"], "2025-12-31")
        self.assertIn("totalBytes", summary.read_text())

    def test_daily_verification_does_not_materialize_unchanged_old_git_history(self):
        self.seed(rows=[row("2024-01-02"), row("2025-12-31"), row("2026-01-02")])
        base = self.checkpoint()
        ka.put_symbol(self.archive, "AAPL", [row("2026-01-05")])
        ka.update_manifest(self.archive)
        original = subprocess.check_output
        old_reads = []

        def inspect(args, **kwargs):
            if "show" in args and str(args[-1]).endswith(".csv"):
                old_reads.append(args[-1])
            return original(args, **kwargs)

        with patch.object(ka.subprocess, "check_output", side_effect=inspect):
            ka.verify(self.archive, base)
        self.assertEqual(old_reads, [f"{base}:us/AAPL/2026.csv"])

    def test_daily_append_does_not_read_unaffected_old_years(self):
        self.seed(rows=[row("2024-01-02"), row("2025-12-31"), row("2026-01-02")])
        original = ka.read_rows
        reads = []

        def inspect(path, **kwargs):
            reads.append(Path(path).name)
            return original(path, **kwargs)

        with patch.object(ka, "read_rows", side_effect=inspect):
            ka.put_symbol(self.archive, "AAPL", [row("2026-01-05")])
        self.assertEqual(reads, ["2026.csv"])

    def test_an_unlogged_new_old_year_is_rejected(self):
        self.seed()
        base = self.checkpoint()
        ka.atomic(self.archive / "us/AAPL/2024.csv", ka.csv_text([row("2024-01-02")]))
        ka.update_manifest(self.archive)
        with self.assertRaisesRegex(ValueError, "Unlogged historical"):
            ka.verify(self.archive, base)


class BackfillTests(_ArchiveCase):
    def test_empty_or_invalid_chart_payload_records_no_source_data_without_rows(self):
        self.seed("SVA")
        before = {p.relative_to(self.archive): p.read_bytes() for p in self.archive.rglob("*") if p.is_file()}

        def chart(quote, day="2026-10-23"):
            stamp = int(datetime.fromisoformat(day + "T15:00:00+00:00").timestamp())
            return {"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"},
                "timestamp": [stamp], "indicators": {"quote": [quote]}}], "error": None}}

        valid = {"open": [100], "high": [101], "low": [99], "close": [100], "volume": [1000]}
        cases = {
            "empty-result": {"chart": {"result": [], "error": None}},
            "null-result": {"chart": {"result": None, "error": None}},
            "empty-timestamps": {"chart": {"result": [{"timestamp": [], "indicators": {"quote": [{}]}}]}},
            "all-null": chart({key: [None] for key in valid}),
            # SVA's live response: one zero-open bar is rejected by OHLC validation.
            "zero-open": chart(dict(valid, open=[0])),
            "outside-period": chart(valid, "2024-10-23"),
        }
        for name, payload in cases.items():
            with self.subTest(payload=name):
                class Net:
                    def connect_yahoo(self):
                        pass

                    def chart(self, symbol, _range):
                        return payload

                output = self.payload / name
                plan = bf.backfill(self.archive, self.cache, output, budget=60, markets="US", now=NOW,
                                   net=Net(), sleep=lambda _: None, listing={"US": {"SVA": {}}})
                entry = plan["progress"]["symbols"]["SVA"]
                self.assertEqual(entry["status"], "retry")
                self.assertIn("no data from source", entry["error"])
                self.assertIn("SVA", entry["error"])
                self.assertIn("no valid completed sessions", entry["error"])
                self.assertEqual(entry["failReason"], entry["error"])
                self.assertEqual(entry["fails"], 1)
                self.assertEqual(entry["retryAfter"], "2026-10-25")
                self.assertFalse(plan["symbols"])
                self.assertFalse(list(output.rglob("*.csv")))
                self.assertFalse(list(self.cache.rglob("*.csv")))
                self.assertEqual(ka.json_read(output / "operations.json", {}), plan)
                self.assertEqual(before, {p.relative_to(self.archive): p.read_bytes()
                                          for p in self.archive.rglob("*") if p.is_file()})

        # The publisher persists only the retry checkpoint, with no data conflict.
        self.assertEqual(ka.apply_operations(self.archive, output),
                         {"added": 0, "corrected": 0, "conflicts": 0, "blocked": 0})
        self.assertEqual(ka.json_read(self.archive / ka.PROGRESS, {})["symbols"]["SVA"], entry)
        after = {p.relative_to(self.archive): p.read_bytes() for p in self.archive.rglob("*")
                 if p.is_file() and p.relative_to(self.archive).as_posix() not in (ka.PROGRESS, "manifest.json")}
        self.assertEqual(after, {p: content for p, content in before.items() if p.as_posix() != "manifest.json"})

    def test_backfill_stages_attrs_unknown_volume_as_empty_and_explicit_zero_as_zero(self):
        compatible = uc.clean(frame([row("2026-10-22", volume=None), row("2026-10-23", volume=0)]))
        with patch.object(bu.Run, "connect_yahoo", return_value=True), patch.object(bu.Run, "fetch_chart", return_value=compatible):
            bf.backfill(self.archive, self.cache, self.payload, budget=60, markets="US", now=NOW,
                        sleep=lambda _: None, listing={"US": {"AAPL": {}}})
        ka.apply_operations(self.archive, self.payload)
        self.assertEqual([r[-1] for r in ka.symbol_rows(self.archive, "AAPL")], ["", "0"])

    def test_resume_skips_completed_symbols_across_new_runs(self):
        ticks = [0]

        class Net:
            calls = []

            def connect_yahoo(self):
                pass

            def chart(self, symbol, _range):
                self.calls.append(symbol)
                ticks[0] += 50
                return {"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"},
                    "timestamp": [int(datetime(2025, 1, 2, 15, tzinfo=timezone.utc).timestamp())],
                    "indicators": {"quote": [{"open": [100], "high": [101], "low": [99], "close": [100], "volume": [None]}]}}]}}

        net = Net()
        listing = {"US": {"AAPL": {}, "MSFT": {}}}
        bf.backfill(self.archive, self.cache, self.payload, budget=60, markets="US", now=NOW,
                    net=net, clock=lambda: ticks[0], sleep=lambda _: None, listing=listing)
        self.assertEqual(net.calls, ["AAPL"])
        ka.apply_operations(self.archive, self.payload)
        self.assertEqual(ka.symbol_rows(self.archive, "AAPL")[0][-1], "")
        ticks[0] = 0
        second = self.root / "second"
        bf.backfill(self.archive, self.cache, second, budget=60, markets="US", now=NOW,
                    net=net, clock=lambda: ticks[0], sleep=lambda _: None, listing=listing)
        self.assertEqual(net.calls, ["AAPL", "MSFT"])
        ka.apply_operations(self.archive, second)
        self.assertEqual(set(ka.json_read(self.archive / ka.PROGRESS, {})["symbols"]), {"AAPL", "MSFT"})
        # A later-day rerun retains the original backfill start and needs no Yahoo history session.
        ticks[0] = 0
        third = self.root / "third"
        with patch.object(net, "connect_yahoo", side_effect=AssertionError("Completed stocks must not connect to Yahoo")):
            plan = bf.backfill(self.archive, self.cache, third, budget=60, markets="US", now=NOW + timedelta(days=1),
                               net=net, clock=lambda: ticks[0], sleep=lambda _: None, listing=listing)
        self.assertFalse(plan["symbols"])
        self.assertEqual(plan["progress"]["targetStart"], "2024-10-24")

    def test_leap_year_and_budget_validation(self):
        self.assertEqual(str(bf.start_date(datetime(2024, 2, 29).date(), 1)), "2023-02-28")
        with self.assertRaises(ValueError):
            bf.backfill(self.archive, self.cache, self.payload, budget=0)

    def test_429_backoff_is_budgeted_and_resume_checkpoint_is_kept(self):
        ticks = [0]

        class Limited:
            calls = 0

            def connect_yahoo(self):
                pass

            def chart(self, symbol, _range):
                self.calls += 1
                raise bu.RateLimited(429, symbol)

        def sleep(seconds):
            ticks[0] += seconds

        net = Limited()
        plan = bf.backfill(self.archive, self.cache, self.payload, budget=60, markets="US", now=NOW,
                           net=net, clock=lambda: ticks[0], sleep=sleep, listing={"US": {"AAPL": {}, "MSFT": {}}})
        self.assertEqual(net.calls, 2)
        self.assertFalse(plan["symbols"])
        self.assertEqual(plan["progress"]["symbols"]["AAPL"]["status"], "retry")
        self.assertLessEqual(ticks[0], 60)


class CollectorAdjustmentTests(_ArchiveCase):
    def test_twse_tpex_snapshot_volume_lineage_distinguishes_missing_from_zero(self):
        samples = (
            (sources.parse_twse_day_all, "2330.TW", "TradeVolume",
             {"Date": "20261023", "Code": "2330", "OpeningPrice": "100", "HighestPrice": "101", "LowestPrice": "99", "ClosingPrice": "100"}),
            (sources.parse_tpex_daily_quotes, "6488.TWO", "TradingShares",
             {"Date": "20261023", "SecuritiesCompanyCode": "6488", "Open": "100", "High": "101", "Low": "99", "Close": "100"}),
        )
        for parser, symbol, field, original in samples:
            for source, expected in ((None, ""), ("--", ""), (0, "0"), ("0", "0"), ("100", "100")):
                with self.subTest(symbol=symbol, volume=source):
                    bar = parser([dict(original, **{field: source})])["bars"][symbol]
                    parsed = uc.frame_from_rows([uc.snapshot_bar(bar, 100)])
                    self.assertEqual(ka.frame_rows(parsed)[0][-1], expected)
                    path = uc.bars_path(str(self.cache), symbol)
                    uc.write_bars(path, parsed, now=NOW)
                    self.assertEqual(ka.read_rows(path)[0][-1], expected)
        for source, expected in ((None, ""), (0, "0")):
            no_price = uc.snapshot_bar({"date": "2026-10-23", "close": None, "volume": source}, 100)
            self.assertEqual(ka.frame_rows(uc.frame_from_rows([no_price]))[0][-1], expected)

    def test_cache_pruning_waits_for_permanent_retention_and_never_prunes_archive(self):
        rows = [row("2026-01-02")]
        self.cache_rows("OLD", rows)
        state = uc.load_state(str(self.cache))
        state["symbols"]["OLD"]["seen"] = "2020-01-01"
        uc.save_state(str(self.cache), state)
        args = bu.parse_args(["--cache-dir", str(self.cache), "--archive-dir", str(self.archive), "--markets", "US"])
        job = bu.Run(args, now=NOW, log=lambda _: None)
        job.listing = {"US": {"NEW": {}}}
        job.prune()
        self.assertTrue(Path(uc.bars_path(str(self.cache), "OLD")).exists())
        self.assertEqual(job.stats["pruneDeferredUntilArchived"], 1)
        self.seed("OLD", rows)
        before = (self.archive / "us/OLD/2026.csv").read_bytes()
        job.prune()
        self.assertFalse(Path(uc.bars_path(str(self.cache), "OLD")).exists())
        self.assertEqual((self.archive / "us/OLD/2026.csv").read_bytes(), before)

    def test_cache_adjustment_fetches_retained_years_with_the_existing_budget(self):
        self.seed(rows=[row("2020-01-02"), row("2026-10-22")])
        self.cache_rows("AAPL", [row("2026-10-22")])
        args = bu.parse_args(["--cache-dir", str(self.cache), "--archive-dir", str(self.archive), "--budget", "120"])
        job = bu.Run(args, now=NOW, sleep=lambda _: None, log=lambda _: None)
        recent = frame([row("2026-10-22", close=50), row("2026-10-23", close=50)])
        complete = frame([row("2020-01-02", close=50), row("2026-10-22", close=50), row("2026-10-23", close=50)])
        with patch.object(job, "fetch_chart", side_effect=[recent, recent, complete]) as fetch:
            job.execute([("AAPL", "recent", "verify")])
        self.assertEqual([call.args[1] for call in fetch.call_args_list], ["3mo", "2y", "max"])
        receipt = ka.json_read(self.cache / "archive-adjustments/AAPL.json", {})
        self.assertEqual(receipt["comparison"]["oldClose"], 100)
        self.assertEqual(receipt["comparison"]["newClose"], 50)
        ka.stage_cache(self.cache, self.archive, self.payload, NOW)
        result = ka.apply_operations(self.archive, self.payload)
        self.assertEqual(result["corrected"], 2)
        self.assertFalse(ka.pending_adjustment(self.archive, self.cache, "AAPL"))

    def test_failed_extended_adjustment_keeps_old_cache_and_archive(self):
        self.seed(rows=[row("2020-01-02"), row("2026-10-22")])
        self.cache_rows("AAPL", [row("2026-10-22")])
        original = Path(uc.bars_path(str(self.cache), "AAPL")).read_bytes()
        args = bu.parse_args(["--cache-dir", str(self.cache), "--archive-dir", str(self.archive), "--budget", "120"])
        job = bu.Run(args, now=NOW, sleep=lambda _: None, log=lambda _: None)
        fresh = frame([row("2026-10-22", close=50)])
        with patch.object(job, "fetch_chart", side_effect=[fresh, RuntimeError("Yahoo unavailable")]):
            job.execute([("AAPL", "full", "adjusted")])
        self.assertEqual(Path(uc.bars_path(str(self.cache), "AAPL")).read_bytes(), original)
        self.assertEqual(ka.symbol_rows(self.archive, "AAPL")[0][4], "100.0000")
        self.assertFalse((self.cache / "archive-adjustments/AAPL.json").exists())

    def test_max_history_429_stops_the_remaining_daily_request_queue(self):
        self.seed(rows=[row("2020-01-02"), row("2026-10-22")])
        self.cache_rows("AAPL", [row("2026-10-22")])
        original = Path(uc.bars_path(str(self.cache), "AAPL")).read_bytes()
        args = bu.parse_args(["--cache-dir", str(self.cache), "--archive-dir", str(self.archive), "--budget", "120"])
        job = bu.Run(args, now=NOW, sleep=lambda _: None, log=lambda _: None)
        fresh = frame([row("2026-10-22", close=50)])
        with patch.object(job, "fetch_chart", side_effect=[fresh, bu.RateLimited(429, "AAPL")]) as fetch:
            job.execute([("AAPL", "full", "adjusted"), ("MSFT", "full", "new")])
        self.assertEqual(fetch.call_count, 2)
        self.assertIsNotNone(job.chart_stopped)
        self.assertEqual(Path(uc.bars_path(str(self.cache), "AAPL")).read_bytes(), original)

    def test_yahoo_missing_volume_never_becomes_zero_during_parsing(self):
        payload = {"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"},
            "timestamp": [int(datetime(2026, 10, 23, 15, tzinfo=timezone.utc).timestamp())],
            "indicators": {"quote": [{"open": [100], "high": [101], "low": [99], "close": [100], "volume": [None]}]}}]}}
        self.assertIsNone(sources.parse_yahoo_chart(payload, "AAPL")[0][0][-1])


class ArchiveGitTests(_ArchiveCase):
    def setUp(self):
        super().setUp()
        self.remote = self.root / "remote.git"
        self.remote.mkdir()
        git(self.remote, "init", "--bare", "--initial-branch=main")
        self.writer = self.root / "writer"
        kg.initialize(self.writer, str(self.remote))
        kg.publish(self.writer, create_only=True, sleep=lambda _: None)

    def operations(self):
        self.cache_rows("AAPL", [row("2026-10-23")])
        ka.stage_cache(self.cache, self.writer, self.payload, NOW)

    def test_initialized_branch_is_orphan_and_has_no_pages_or_code(self):
        head = git(self.writer, "rev-list", "--parents", "-1", "HEAD").split()
        self.assertEqual(len(head), 1)
        paths = git(self.writer, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        self.assertEqual(set(paths), {"README.md", "manifest.json", ka.LOG})
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)"), "refs/heads/kline-archive")

    def test_noop_publish_adds_no_commit(self):
        before = git(self.writer, "rev-parse", "HEAD")
        self.assertFalse(kg.publish(self.writer))
        self.assertEqual(git(self.writer, "rev-parse", "HEAD"), before)

    def test_shallow_checkout_and_nonforce_push_retry_rebase_only_archive(self):
        other = self.root / "other"
        subprocess.run(["git", "clone", "--quiet", "--depth=1", "--branch", ka.BRANCH, self.remote.as_uri(), str(other)], check=True)
        self.assertEqual(git(other, "rev-parse", "--is-shallow-repository"), "true")
        git(other, "config", "user.name", "Concurrent")
        git(other, "config", "user.email", "test@example.invalid")
        self.operations()
        original = kg.git
        pushes = []

        def racing(path, *args, **kwargs):
            if args[0] == "push":
                pushes.append(args)
                if len(pushes) == 1:
                    git(other, "commit", "--allow-empty", "-m", "Concurrent metadata checkpoint")
                    git(other, "push", "origin", ka.BRANCH)
            return original(path, *args, **kwargs)

        with patch.object(kg, "git", side_effect=racing):
            self.assertTrue(kg.publish(self.writer, self.payload, sleep=lambda _: None))
        self.assertEqual(len(pushes), 2)
        self.assertFalse(any("force" in arg for args in pushes for arg in args))
        self.assertEqual(git(self.remote, "show", "kline-archive:us/AAPL/2026.csv").splitlines()[-1], ",".join(row("2026-10-23")))

    def test_second_initialization_cannot_replace_existing_remote_records(self):
        self.operations()
        kg.publish(self.writer, self.payload)
        before = git(self.remote, "rev-parse", ka.BRANCH)
        second = self.root / "second-init"
        kg.initialize(second, str(self.remote))
        with self.assertRaisesRegex(RuntimeError, "Create-only"):
            kg.publish(second, create_only=True)
        self.assertEqual(git(self.remote, "rev-parse", ka.BRANCH), before)

    def test_wrong_checkout_branch_cannot_be_published(self):
        git(self.writer, "checkout", "-b", "main")
        with self.assertRaisesRegex(ValueError, "dedicated"):
            kg.publish(self.writer)

    def test_rebase_conflict_never_overwrites_the_remote_writer(self):
        other = self.root / "concurrent"
        subprocess.run(["git", "clone", "--quiet", "--branch", ka.BRANCH, str(self.remote), str(other)], check=True)
        git(other, "config", "user.name", "Concurrent")
        git(other, "config", "user.email", "test@example.invalid")
        self.operations()
        original = kg.git
        remote_head = []

        def racing(path, *args, **kwargs):
            if args[0] == "push" and not remote_head:
                ka.put_symbol(other, "AAPL", [row("2026-10-23", close=200)])
                ka.update_manifest(other)
                git(other, "add", ".")
                git(other, "commit", "-m", "Independent accepted data")
                git(other, "push", "origin", ka.BRANCH)
                remote_head.append(git(other, "rev-parse", "HEAD"))
            return original(path, *args, **kwargs)

        with patch.object(kg, "git", side_effect=racing):
            with self.assertRaisesRegex(RuntimeError, "rebase conflict"):
                kg.publish(self.writer, self.payload, sleep=lambda _: None)
        self.assertEqual(git(self.remote, "rev-parse", ka.BRANCH), remote_head[0])
        self.assertIn("200.0000", git(self.remote, "show", "kline-archive:us/AAPL/2026.csv"))


class JournalTests(_ArchiveCase):
    def test_main_journal_publication_unions_a_racing_writer_and_only_touches_the_log(self):
        import generated_branch
        self.seed(rows=[row("2026-01-02")])
        proof = {"id": "cache-split", "comparison": {"date": "2026-01-02", "oldClose": 100, "newClose": 50}}
        ka.put_symbol(self.archive, "AAPL", [row("2026-01-02", close=50)], correction=proof)
        main = self.root / "main"
        snapshots = [{"version": 1, "quarantined": [], "events": []}]
        other = ka.event("MSFT", row("2026-01-02", close=30), row("2026-01-02"), "corrected", "independent-data-correction")
        snapshots.append({"version": 1, "quarantined": [], "events": [other]})
        counts = [0]

        class Store:
            def fetch(self, branch):
                self.assert_branch = branch
                return str(counts[0])

            def publish_one(self, branch, base, patterns, message):
                if branch != "main" or patterns != [ka.LOG]:
                    raise AssertionError("Publisher escaped the journal allowlist")
                counts[0] += 1
                if counts[0] == 1:
                    raise ValueError("Concurrent same-path change")
                return True

        def read(*args):
            return json.dumps(snapshots[int(args[-1].split(":")[0])]).encode()

        with patch.object(generated_branch, "Storage", return_value=Store()), patch.object(generated_branch, "git", side_effect=read):
            self.assertTrue(kg.publish_main_journal(self.archive, sleep=lambda _: None, main_root=main))
        events = ki.read_log(main / ka.LOG)["events"]
        self.assertEqual({e["symbol"] for e in events}, {"AAPL", "MSFT"})


class WorkflowTests(unittest.TestCase):
    def test_workflow_token_resets_existing_git_auth_headers_without_persisting_it(self):
        import os
        with patch.dict(os.environ, {"GITHUB_TOKEN": "unit-test-token", "GIT_CONFIG_COUNT": "0", "GITHUB_SERVER_URL": "https://github.com"}):
            env = kg.auth_env()
        self.assertEqual(env["GIT_CONFIG_COUNT"], "2")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_KEY_1"])
        self.assertTrue(env["GIT_CONFIG_VALUE_1"].startswith("AUTHORIZATION: basic "))

    def test_archive_is_not_a_generated_owner_and_daily_stage_follows_cache_save(self):
        import yaml
        config = json.loads((ROOT / ".github/generated-paths.json").read_text())
        self.assertNotEqual(config["branch"], ka.BRANCH)
        self.assertFalse(any(p.startswith(("tw/", "us/", "kline-archive")) for paths in config["owners"].values() for p in paths))
        workflow = yaml.safe_load((ROOT / ".github/workflows/universe-full.yml").read_text())
        steps = workflow["jobs"]["build"]["steps"]
        names = [step.get("name", "") for step in steps]
        self.assertLess(names.index("Save daily-bar cache"), names.index("Stage accepted completed sessions for permanent retention"))
        checkout = next(step for step in steps if step.get("id") == "archive_checkout")
        self.assertEqual(checkout["with"]["ref"], ka.BRANCH)
        self.assertEqual(checkout["with"]["fetch-depth"], 1)
        build = next(step for step in steps if step.get("name") == "Build full-market universe")
        self.assertIn("archive_checkout.outcome == 'success'", build["if"])
        self.assertIn("archive_bootstrap.outcome == 'success'", build["if"])
        backfill = yaml.safe_load((ROOT / ".github/workflows/kline-archive-backfill.yml").read_text())
        self.assertEqual(backfill["jobs"]["fetch"]["permissions"]["contents"], "read")
        self.assertEqual(backfill["jobs"]["publish"]["permissions"]["contents"], "write")


if __name__ == "__main__":
    unittest.main()

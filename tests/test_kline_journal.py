"""Main correction-journal retention, dedup, format and batching; the archive journal is permanent."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import kline_integrity as k
import kline_archive
import kline_journal as journal

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
BAR = dict(Open=None, High=None, Low=None, Close=None, Volume=None)


def event(symbol, day, status, logged, reason="r", old=None, new=None, fetched="f"):
    return dict(symbol=symbol, date=day, status=status, old=old, new=new, sourceUrl="u",
                fetchedAt=fetched, reason=reason, id=f"{symbol}-{day}-{status}-{logged}-{fetched}",
                loggedAt=(NOW - timedelta(days=logged)).isoformat())


class PruneTests(unittest.TestCase):
    def test_retention_dedup_and_permanent_entries(self):
        events = [
            event("A", "2026-10-28", "deferred", 0, reason="unfinished-session"),
            # The same bad bar seen by three later fetches is one event.
            event("B", "2026-10-02", "rejected", 3, fetched="1"), event("B", "2026-10-02", "rejected", 2, fetched="2"),
            event("B", "2026-10-02", "rejected", 1, fetched="3"),
            # A source flipping back and forth keeps each change.
            event("C", "2026-10-22", "corrected", 3, old={"v": 1}, new={"v": 2}),
            event("C", "2026-10-22", "corrected", 2, old={"v": 2}, new={"v": 1}),
            event("C", "2026-10-22", "corrected", 1, old={"v": 1}, new={"v": 2}),
            event("D", "2026-06-01", "corrected", 120, reason="source-refresh"),
            event("E", "2026-06-01", "quarantined", 120, reason="invalid-OHLC-bounds"),
            event("F", "2026-06-01", "corrected", 120, reason="kline-archive/adjustment-year:abc"),
            # The first retained repeat of an aged-out rejection stands in for it.
            event("G", "2026-06-01", "rejected", 120, fetched="old"), event("G", "2026-06-01", "rejected", 5, fetched="new"),
        ]
        data = dict(version=1, quarantined=[dict(symbol="E", date="2026-06-01", reason="invalid-OHLC-bounds")], events=events)
        kept = journal.prune_main_journal(data, now=NOW)
        self.assertEqual([(e["symbol"], e["fetchedAt"]) for e in kept["events"]],
                         [("E", "f"), ("F", "f"), ("G", "new"), ("B", "1"), ("C", "f"), ("C", "f"), ("C", "f")])
        self.assertEqual(kept["quarantined"], data["quarantined"])

    def test_z_suffixed_timestamps_parse_on_python_310(self):
        recent = event("Z", "2026-10-28", "rejected", 1)
        recent["loggedAt"] = (NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        aged = event("Y", "2026-06-01", "rejected", 120)
        aged["loggedAt"] = (NOW - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
        kept = journal.prune_main_journal(dict(version=1, quarantined=[], events=[recent, aged]), now=NOW)
        self.assertEqual([e["symbol"] for e in kept["events"]], ["Z"])

    def test_cap_keeps_permanent_and_newest(self):
        events = [event("Q", "2026-01-01", "quarantined", 200)] + [event(f"S{i}", "2026-10-01", "rejected", 1 - i / 1e6) for i in range(5)]
        with patch.object(journal, "MAX_EVENTS", 3):
            kept = journal.prune_main_journal(dict(version=1, quarantined=[], events=events), now=NOW)
        self.assertEqual([e["symbol"] for e in kept["events"]], ["Q", "S3", "S4"])

    def test_dump_is_valid_json_with_one_event_per_line(self):
        data = dict(version=1, quarantined=[dict(symbol="E", date="2026-06-01", reason="x")],
                    events=[event("中", "2026-10-01", "rejected", 1), event("B", "2026-10-02", "rejected", 1)])
        text = journal.dump_journal(data)
        self.assertEqual(json.loads(text), data)
        self.assertEqual(sum(1 for line in text.splitlines() if '"loggedAt"' in line), 2)
        self.assertIn("中", text)
        self.assertEqual(json.loads(journal.dump_journal(dict(version=1, quarantined=[], events=[]))),
                         dict(version=1, quarantined=[], events=[]))


class LogEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "corrections.json"
        patcher = patch.object(k, "LOG_PATH", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unfinished_sessions_and_repeated_sightings_are_not_logged(self):
        k.log_event("A", "2026-10-28", "deferred", old=BAR, reason="unfinished-session")
        self.assertFalse(self.log.exists())
        for fetched in ("t1", "t2", "t3"):
            k.log_event("B", "2026-10-02", "rejected", old=BAR, fetched_at=fetched, reason="nonpositive-or-nonfinite-price")
        k.log_event("B", "2026-10-02", "rejected", old=BAR, fetched_at="t4", reason="invalid-OHLC-bounds")
        self.assertEqual([e["fetchedAt"] for e in k.read_log()["events"]], ["t1", "t4"])
        self.assertEqual(json.loads(self.log.read_text()), k.read_log())

    def test_batched_run_writes_once_and_reads_its_own_quarantine(self):
        with patch.object(k, "write_log", wraps=k.write_log) as writes:
            with k.batched_journal():
                k.log_event("A", "2026-10-02", "quarantined", old=BAR, reason="invalid-OHLC-bounds")
                for day in ("2026-10-03", "2026-10-04"):
                    k.log_event("B", day, "rejected", old=BAR, reason="r")
                self.assertFalse(self.log.exists())
                self.assertEqual(k.quarantined_dates("A"), {"2026-10-02"})
                writes.assert_not_called()
            writes.assert_called_once()
        self.assertEqual(len(json.loads(self.log.read_text())["events"]), 3)
        self.assertEqual(k.quarantined_dates("A"), {"2026-10-02"})

    def test_flush_inside_a_batch_makes_the_file_current(self):
        with k.batched_journal():
            k.log_event("B", "2026-10-03", "rejected", old=BAR, reason="r")
            k.flush_journal()
            self.assertEqual(len(json.loads(self.log.read_text())["events"]), 1)
            k.log_event("B", "2026-10-04", "rejected", old=BAR, reason="r")
        self.assertEqual(len(json.loads(self.log.read_text())["events"]), 2)

    def test_batch_is_written_even_when_the_run_fails(self):
        with self.assertRaises(RuntimeError):
            with k.batched_journal():
                k.log_event("B", "2026-10-03", "rejected", old=BAR, reason="r")
                raise RuntimeError
        self.assertEqual(len(json.loads(self.log.read_text())["events"]), 1)
        self.assertIsNone(k._batch)


class ArchiveJournalTests(unittest.TestCase):
    def test_archive_journal_keeps_every_event(self):
        with tempfile.TemporaryDirectory() as root:
            old = [event("A", "2025-01-02", "removed", 400, reason="kline-archive/closed-session"),
                   event("B", "2025-01-02", "corrected", 400, reason="kline-archive/adjustment-year:x")]
            kline_archive.add_events(root, old)
            kline_archive.add_events(root, [event("C", "2026-10-02", "corrected", 1)])
            self.assertEqual(len(k.read_log(Path(root) / kline_archive.LOG)["events"]), 3)


if __name__ == "__main__":
    unittest.main()

"""Offline ^TWII share-volume basis, official validation and scoped repair tests."""
import copy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import twii_volume as tv
import repair_twii_volume as repair
import kline_archive as archive
from test_kline_archive import _ArchiveCase


def trading_days(end, count):
    current = date.fromisoformat(end)
    result = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current -= timedelta(days=1)
    return list(reversed(result))


def fixture():
    days = trading_days("2026-11-04", 25)
    official = {}
    archive = []
    for index, day in enumerate(days):
        shares = (8_000_000_000 + index * 90_000_000)
        index_price = Decimal("42000")
        official[day] = (shares, index_price)
        archive.append((day, 42000, 42000, 42000, 42000, int(shares / 3000)))
    for day in ("2026-11-05", "2026-11-06"):
        official[day] = (10_000_000_000, Decimal("42000"))
        archive.append((day, 42000, 42000, 42000, 42000, 0))
    return archive, official


def fmtqik(month="2026-11-01"):
    month = date.fromisoformat(month)
    return {
        "stat": "OK", "date": f"{month:%Y%m}13",
        "fields": ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"],
        "data": [["115/11/05", "10,000,000,000", "900,000,000,000", "3,100,000", "42,000.00", "+2.1"]],
    }


class FMTQIKValidationTest(unittest.TestCase):
    def test_monthly_fields_and_unit_are_strict(self):
        parsed = tv.parse_fmtqik(fmtqik(), date(2026, 11, 1))
        self.assertEqual(parsed["2026-11-05"], (10_000_000_000, Decimal("42000.00")))
        self.assertEqual(tv.fmtqik_url(date(2026, 11, 1)),
                         "https://www.twse.com.tw/exchangeReport/FMTQIK?response=json&date=20261101")
        for mutate in (
            lambda doc: doc.update(date="20261013"),
            lambda doc: doc.update(stat="ERROR"),
            lambda doc: doc["fields"].remove("成交股數"),
            lambda doc: doc["data"][0].pop(2),
            lambda doc: doc["data"][0].__setitem__(0, "115/10/05"),
            lambda doc: doc["data"].append(list(doc["data"][0])),
            lambda doc: doc["data"][0].__setitem__(1, "--"),
        ):
            doc = copy.deepcopy(fmtqik())
            mutate(doc)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                tv.parse_fmtqik(doc, date(2026, 11, 1))
        with self.assertRaises(ValueError):
            tv.parse_fmtqik("<html>challenge</html>", date(2026, 11, 1))

    def test_wrong_month_and_non_json_discard_entire_month(self):
        messages = []
        data, failures = tv.fetch_official(lambda url: fmtqik("2026-10-01"), "2026-11-05", messages.append)
        self.assertNotIn("2026-11-05", data)
        self.assertTrue(failures)
        self.assertTrue(messages)


class CalibrationTest(unittest.TestCase):
    def test_at_least_twenty_overlap_and_ratio_in_correct_direction(self):
        rows, official = fixture()
        factor, stats = tv.calibrated_ratio({r[0]: r[5] for r in rows}, official)
        self.assertEqual(stats["reason"], "stable")
        self.assertEqual(stats["overlap"], 25)
        self.assertAlmostEqual(factor, 1 / 3000, places=8)
        proposed, changes = tv.fill_rows(rows, official, factor)
        self.assertEqual([r["date"] for r in changes], ["2026-11-05", "2026-11-06"])
        self.assertEqual(proposed[-2][-1], 3333333)
        self.assertEqual(proposed[-1][-1], 3333333)
        self.assertEqual(proposed[-2][:5], rows[-2][:5])
        self.assertEqual(proposed[:25], rows[:25])

    def test_fewer_than_twenty_or_unstable_ratio_never_fills(self):
        rows, official = fixture()
        eligible = {r[0]: r[5] for r in rows[:19]}
        self.assertEqual(tv.calibrated_ratio(eligible, official)[1]["reason"], "insufficient-overlap")
        yahoo = {r[0]: (r[5] * (2 if i % 2 else 1)) for i, r in enumerate(rows[:25])}
        factor, stats = tv.calibrated_ratio(yahoo, official)
        self.assertIsNone(factor)
        self.assertEqual(stats["reason"], "unstable-ratio")
        self.assertEqual(tv.fill_rows(rows, official, factor)[1], [])

    def test_close_mismatch_or_missing_official_preserves_source_volume(self):
        rows, official = fixture()
        wrong = dict(official)
        wrong["2026-11-05"] = (10_000_000_000, Decimal("40000"))
        del wrong["2026-11-06"]
        patched, changes = tv.fill_rows(rows, wrong, 1 / 3000)
        self.assertEqual(changes, [])
        self.assertEqual(patched, rows)
        missing = list(rows)
        missing[-1] = (*missing[-1][:5], None)
        patched, changes = tv.fill_rows(missing, official, 1 / 3000)
        self.assertEqual([c["date"] for c in changes], ["2026-11-05", "2026-11-06"])

    def test_only_twii_benchmark_is_allowed_through_adapter(self):
        rows, official = fixture()
        called = []
        def fetch(url):
            called.append(url)
            raise ValueError("no access")
        for symbol in ("^TWOII", "2330.TW", "AAPL"):
            self.assertIs(tv.fill_yahoo_rows(symbol, rows, fetch), rows)
        self.assertFalse(called)
        self.assertEqual(tv.fill_yahoo_rows("^TWII", rows, fetch), rows)
        self.assertTrue(called)

    def test_chart_nan_missing_volume_uses_calibrated_official_data(self):
        rows, official = fixture()
        rows[-1] = (*rows[-1][:5], float('nan'))  # pandas chart frame
        with patch.object(tv, 'fetch_official', return_value=(official, [])) as fetch:
            result = tv.fill_yahoo_rows('^TWII', rows, lambda url: None)
        fetch.assert_called_once()
        self.assertEqual(result[-1][-1], 3333333)
        self.assertEqual(result[-2][-1], 3333333)

    def test_repair_plan_scopes_to_zero_volumes_only(self):
        rows, official = fixture()
        proposed, changes, skipped, stats = repair.plan(
            [(*r[:5], str(r[5])) for r in rows], official)
        self.assertEqual(stats["reason"], "stable")
        self.assertEqual(len(changes), 2)
        self.assertEqual(skipped, {})
        self.assertEqual(proposed[0][5], str(rows[0][5]))
        self.assertEqual(proposed[-1][5], 3333333)



class YahooV8PriorityTest(unittest.TestCase):
    def test_yahoo_direct_overrides_unstable_fmtqik_ratio(self):
        rows, official = fixture()
        unstable = dict(official)
        for i, row in enumerate(rows[:25]):
            if i % 2:
                unstable[row[0]] = (official[row[0]][0] * 2, official[row[0]][1])
        self.assertEqual(tv.calibrated_ratio({r[0]: r[5] for r in rows}, unstable)[1]["reason"],
                         "unstable-ratio")
        yahoo = [("2026-11-05", 42000, 42000, 42000, 42000, 4239400),
                 ("2026-11-06", 42000, 42000, 42000, 42000, 5813000)]
        proposed, changes, skipped, stats = repair.plan(rows, unstable, yahoo)
        self.assertEqual(stats["reason"], "unstable-ratio")
        self.assertEqual([c["source"] for c in changes], ["yahoo-v8", "yahoo-v8"])
        self.assertEqual([r[5] for r in proposed[-2:]], [4239400, 5813000])
        self.assertEqual(skipped, {})

    def test_close_mismatch_rejects_yahoo_without_using_fmtqik(self):
        rows, official = fixture()
        yahoo = [("2026-11-05", 42000, 42000, 42000, 43200, 4239400),
                 ("2026-11-06", 42000, 42000, 42000, 42000, 5813000)]
        proposed, changes, skipped, _ = repair.plan(rows, official, yahoo)
        self.assertEqual([c["date"] for c in changes], ["2026-11-06"])
        self.assertEqual(proposed[-2][5], 0)
        self.assertEqual(proposed[-1][:5], rows[-1][:5])
        self.assertIn("2026-11-05", skipped)
        self.assertEqual(changes[0]["source"], "yahoo-v8")

    def test_fmtqik_only_for_missing_yahoo_and_stable_ratio(self):
        rows, official = fixture()
        raw = [("2026-11-05", 42000, 42000, 42000, 42000, 4239400),
               ("2026-11-06", 42000, 42000, 42000, 42000, 0)]
        proposed, changes, skipped, stats = repair.plan(rows, official, raw)
        self.assertEqual(stats["reason"], "stable")
        self.assertEqual([c["source"] for c in changes], ["yahoo-v8", "fmtqik"])
        self.assertEqual([r[5] for r in proposed[-2:]], [4239400, 3333333])
        self.assertEqual(skipped, {})
        # Source failure or missing official data never manufactures a value.
        proposed, changes, skipped, stats = repair.plan(rows, {}, [])
        self.assertEqual(changes, [])
        self.assertEqual(proposed, rows)
        self.assertIn("2026-11-05", skipped)



class ArchiveTWIIVolumeRepairTest(_ArchiveCase):
    def test_backup_audit_verification_and_unrelated_rows_unchanged(self):
        rows, official = fixture()
        source = [archive.normalize(r) for r in rows]
        self.seed("^TWII", source)
        self.seed("2330.TW", [archive.normalize(
            ("2026-11-05", 100, 101, 99, 100, 456789))])
        self.checkpoint()
        other_path = self.archive / "tw/2330.TW/2026.csv"
        other = other_path.read_bytes()
        before = self.archive / "tw/^TWII/2026.csv"
        before_sha = archive.digest(before.read_bytes())
        proposed, changes, skipped, stats = repair.plan(
            archive.symbol_rows(self.archive, "^TWII", {"2026"}), official)
        backup = self.root / repair.BACKUP_NAME
        self.assertTrue(repair.apply(self.archive, source, proposed, changes, stats, backup))
        self.assertTrue(backup.exists())
        self.assertEqual(archive.digest(before.read_bytes()) != before_sha, True)
        self.assertEqual(other_path.read_bytes(), other)
        evidence = archive.json_read(backup, {})
        self.assertEqual(evidence["operation"], "twii-volume")
        self.assertEqual(evidence["archiveCommit"], repair.git(self.archive, "rev-parse", "HEAD"))
        self.assertEqual(len(evidence["files"]), 4)
        self.assertEqual(archive.symbol_rows(self.archive, "^TWII", {"2026"})[-1][-1], "3333333")
        archive.verify(self.archive, base="HEAD")
        self.checkpoint()
        archive.verify(self.archive, base="HEAD^")
        with self.assertRaisesRegex(ValueError, "already exists"):
            repair.make_backup(self.archive, backup, repair.git(self.archive, "rev-parse", "HEAD"), [])



class RecentTWIIBackfillTest(_ArchiveCase):
    def test_daily_fills_only_recent_ten_sessions_with_exact_yahoo_and_keeps_ohlc(self):
        dates = trading_days("2026-11-30", 16)
        source = [archive.normalize((day, 42000, 42100, 41900, 42000,
                        0 if i in (0, 13, 15) else 1000000))
                  for i, day in enumerate(dates)]
        self.seed("^TWII", source)
        self.seed("2330.TW", [archive.normalize(("2026-11-27", 100, 101, 99, 100, 5))])
        base = self.checkpoint()
        other = (self.archive / "tw/2330.TW/2026.csv").read_bytes()
        yahoo = [(day, 1, 2, 1, 42000, 7777777) for day in (dates[0], dates[13], dates[15])]
        # One recent Yahoo close mismatch must remain an archival zero.
        yahoo[-1] = (dates[15], 1, 2, 1, 43000, 8888888)
        changes = repair.recent_yahoo_backfill(
            self.archive, base, now=datetime(2026, 12, 1, 8, tzinfo=timezone.utc),
            yahoo_v8=yahoo, log=lambda _: None)
        self.assertEqual([c["date"] for c in changes], [dates[13]])
        actual = archive.symbol_rows(self.archive, "^TWII", {"2026"})
        by_day = {r[0]: r for r in actual}
        self.assertEqual(by_day[dates[0]][5], "0")  # Outside latest ten sessions.
        self.assertEqual(by_day[dates[15]][5], "0")  # Close mismatch.
        self.assertEqual(by_day[dates[13]][5], "7777777")
        self.assertEqual(by_day[dates[13]][:5], source[13][:5])
        self.assertEqual((self.archive / "tw/2330.TW/2026.csv").read_bytes(), other)
        archive.verify(self.archive, base=base, refresh_manifest=True)
        self.checkpoint()
        archive.verify(self.archive, base=base)

    def test_daily_audit_allows_append_plus_prior_zero_fix_in_one_year(self):
        rows = [archive.normalize(("2026-11-04", 42000, 42100, 41900, 42000, 4641300)),
                archive.normalize(("2026-11-05", 42000, 42100, 41900, 42000, 0))]
        self.seed("^TWII", rows)
        base = self.checkpoint()
        archive.put_symbol(self.archive, "^TWII", [
            archive.normalize(("2026-11-06", 42000, 42100, 41900, 42000, 5813000))])
        changes = repair.recent_yahoo_backfill(
            self.archive, base, now=datetime(2026, 11, 9, 8, tzinfo=timezone.utc),
            yahoo_v8=[("2026-11-05", 42000, 42100, 41900, 42000, 4239400)],
            log=lambda _: None)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["new"], 4239400)
        archive.verify(self.archive, base=base, refresh_manifest=True)
        self.checkpoint()
        archive.verify(self.archive, base=base)
        before = archive.symbol_rows(self.archive, "^TWII", {"2026"})
        self.assertEqual(before[-2][5], "4239400")
        self.assertEqual(before[-1][5], "5813000")
        self.assertEqual(repair.recent_yahoo_backfill(
            self.archive, "HEAD", now=datetime(2026, 11, 9, 8, tzinfo=timezone.utc),
            yahoo_v8=[("2026-11-05", 42000, 42100, 41900, 42000, 4239400)],
            log=lambda _: None), [])



if __name__ == "__main__":
    unittest.main()

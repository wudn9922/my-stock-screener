"""Offline ^TWII share-volume basis, official validation and scoped repair tests."""
import copy
from datetime import date, timedelta
from decimal import Decimal
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import twii_volume as tv
import repair_twii_volume as repair


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

    def test_repair_plan_scopes_to_zero_volumes_only(self):
        rows, official = fixture()
        proposed, changes, skipped, stats = repair.plan(
            [(*r[:5], str(r[5])) for r in rows], official)
        self.assertEqual(stats["reason"], "stable")
        self.assertEqual(len(changes), 2)
        self.assertEqual(skipped, {})
        self.assertEqual(proposed[0][5], str(rows[0][5]))
        self.assertEqual(proposed[-1][5], 3333333)


if __name__ == "__main__":
    unittest.main()

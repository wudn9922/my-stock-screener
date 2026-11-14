"""Run: python -m unittest discover -s tests -p 'test_margin_temperature.py' -v"""
import csv
import json
import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from margin_temperature import DATA, OUTPUT, TwseClient, build_rows, csv_rows, hot_episodes, make_report, parse_index, parse_margin, to_report


def weekly(closes, amounts):
    """Synthetic weekly series (one Friday per ISO week from 2007-01-05) as CSV-style dict rows."""
    days = [(date(2007, 1, 5) + timedelta(weeks=i)).isoformat() for i in range(len(closes))]
    index = [{'date': d, 'close': str(c)} for d, c in zip(days, closes)]
    margin = [{'date': d, 'mrg_amt': str(a)} for d, a in zip(days, amounts)]
    return index, margin


class MarginTemperatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = build_rows(csv_rows(DATA / 'taiex.csv'), csv_rows(DATA / 'margin_daily.csv'))

    def test_golden_all_weeks(self):
        with (ROOT / 'tests/fixtures/margin_temperature/gauge_golden.csv').open(newline='') as fp:
            golden = list(csv.DictReader(fp))
        historical = [row for row in self.rows if row['date'] <= golden[-1]['date']]
        self.assertEqual(len(historical), len(golden))
        differences = {'pct': [], 'mdd': []}
        for item, expected in zip(historical, golden):
            self.assertEqual(item['date'], expected['date'])
            self.assertEqual(item['close'], float(expected['close']))
            for key in differences:
                if expected[key] == '':
                    self.assertIsNone(item[key], f"{item['date']}: expected NULL {key}")
                else:
                    self.assertIsNotNone(item[key])
                    differences[key].append(abs(item[key] - float(expected[key])))
        for name, values in differences.items():
            maximum = max(values)
            print(f'{name} 逐週比對 {len(values)} 筆，最大誤差 {maximum:.15f}')
            self.assertLess(maximum, .01)

    def test_latest_and_status(self):
        historical = [row for row in self.rows if row['date'] <= '2026-11-12']
        r = to_report(historical, date(2026, 11, 13))
        self.assertEqual(r['latest']['date'], '2026-11-12')
        self.assertTrue(r['provisional'])
        self.assertAlmostEqual(r['latest']['pct'], 85.16129032258064, places=9)
        self.assertAlmostEqual(r['latest']['mdd'], -17.47062661957971, places=9)
        self.assertEqual((r['latest']['hot'], r['latest']['cold']), (False, False))
        self.assertAlmostEqual(r['latest']['ratioPct'], 40.645161290322584, places=9)
        self.assertEqual((r['latest']['relHot'], r['latest']['hotWeeks']), (False, 0))
        self.assertEqual((r['firstDate'], r['hotEpisodes']), ('2007-01-05', 10))
        self.assertFalse(to_report(historical, date(2026, 11, 20))['provisional'])

    def test_dual_flags_independent(self):
        # If both threshold flags are true, neither must override the other.
        output = to_report([{'date':'2026-11-12','close':45000,'pct':99,'mdd':-39,'hot':True,'cold':True,'ratioPct':None,'relHot':False,'hotWeeks':1}], date(2026,11,13))
        self.assertTrue(output['latest']['hot'])
        self.assertTrue(output['latest']['cold'])

    def test_ratio_pct_minimum_history_and_hand_value(self):
        # margin = close * ratio, so log(margin / close) ranks exactly like ratio.  Ratios rise 1..109 and the
        # last week drops to 50.5: 50 of the 109 previous weeks are lower -> 100 * 50 / 109.
        n = 110
        closes = [100 if i % 2 == 0 else 200 for i in range(n)]
        ratios = [i + 1 for i in range(n - 1)] + [50.5]
        rows = build_rows(*weekly(closes, [c * r for c, r in zip(closes, ratios)]))
        self.assertTrue(all(row['ratioPct'] is None for row in rows[:103]))  # < 104 observations
        self.assertAlmostEqual(rows[103]['ratioPct'], 100.0, places=9)       # 104 observations, all previous lower
        self.assertAlmostEqual(rows[-1]['ratioPct'], 100 * 50 / 109, places=9)
        self.assertFalse(rows[-1]['relHot'])
        self.assertTrue(rows[103]['relHot'])
        self.assertFalse(rows[102]['relHot'])

    def test_ratio_pct_uses_156_week_window(self):
        # Rows 0..13 fall outside the window of the final row; ratios 1..14 there must not be counted.
        n = 170
        ratios = [i + 1 for i in range(n - 1)] + [5.5]
        rows = build_rows(*weekly([100] * n, [100 * r for r in ratios]))
        self.assertEqual(rows[-1]['ratioPct'], 0.0)

    def test_ratio_pct_differs_from_amount_pct_when_index_grows(self):
        # Amount grows 1%/week but the index grows faster: amount looks hot, the ratio does not.
        n = 130
        rows = build_rows(*weekly([100 * 1.02 ** i for i in range(n)], [1000 * 1.01 ** i for i in range(n)]))
        self.assertTrue(rows[-1]['hot'])
        self.assertEqual(rows[-1]['ratioPct'], 0.0)
        self.assertFalse(rows[-1]['relHot'])

    def test_hot_weeks_streak_and_reset(self):
        amounts = [1000 * 1.01 ** i for i in range(110)] + [1000, 1000 * 1.01 ** 111, 1000 * 1.01 ** 112]
        rows = build_rows(*weekly([1000] * len(amounts), amounts))
        self.assertEqual([row['hotWeeks'] for row in rows[100:]], [0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 0, 1, 2])
        self.assertTrue(all(row['hot'] == (row['hotWeeks'] > 0) for row in rows))
        report = to_report(rows, date(2030, 1, 1))
        self.assertEqual(report['hotEpisodes'], 2)
        self.assertEqual(report['latest']['hotWeeks'], 2)
        self.assertEqual(report['firstDate'], rows[0]['date'])
        self.assertEqual([row['hotWeeks'] for row in report['rows']], [row['hotWeeks'] for row in rows])

    def test_hot_episodes_counting(self):
        def episodes(flags):
            return hot_episodes([{'hot': flag} for flag in flags])
        self.assertEqual(episodes([]), 0)
        self.assertEqual(episodes([False, False]), 0)
        self.assertEqual(episodes([True, True, True]), 1)
        self.assertEqual(episodes([True, False, True, True, False, False, True]), 3)
        self.assertEqual(episodes([False, True, True, False]), 1)

    def test_existing_fields_unchanged_in_published_file(self):
        report = make_report(csv_rows(DATA / 'taiex.csv'), csv_rows(DATA / 'margin_daily.csv'), date(2026, 11, 14))
        published = json.loads(OUTPUT.read_text(encoding='utf-8'))
        legacy = ('date', 'close', 'pct', 'mdd', 'hot', 'cold')
        self.assertEqual(list(report['rows'][0]), list(legacy) + ['ratioPct', 'relHot', 'hotWeeks'])
        self.assertEqual(report['rows'], published['rows'])
        self.assertEqual(report['latest'], published['latest'])
        self.assertEqual(report['rows'][-1], report['latest'])
        self.assertEqual((report['version'], report['asOf'], report['scope']),
                         (published['version'], published['asOf'], published['scope']))
        for key in legacy:  # still match the independent legacy rows
            self.assertEqual([row[key] for row in report['rows']], [row[key] for row in self.rows])

    def test_margin_parser_and_holiday(self):
        day = {'stat':'OK','tables':[{'data':[
            ['融資股數(仟股)','1','2','3','4','9,016,207'],
            ['融券股數(仟股)','1','2','3','4','210,522'],
            ['融資金額(仟元)','1','2','3','4','533,474,629'],
        ]}]}
        self.assertEqual(parse_margin(day), {'mrg_amt':533474629.0,'mrg_lots':9016207.0,'short_lots':210522.0})
        self.assertIsNone(parse_margin({'stat':'很抱歉，沒有符合條件的資料!'}))
        with self.assertRaises(ValueError):
            parse_margin({'stat':'OK','tables':[{'data':[]}]})

    def test_index_parser_and_holiday(self):
        day = {'stat':'OK','fields':['日期','開盤指數','最高指數','最低指數','收盤指數'],
               'data':[['115/11/12','44,180.46','44,525.24','42,943.50','43,091.87']]}
        self.assertEqual(parse_index(day,date(2026,11,12)),{'open':44180.46,'high':44525.24,'low':42943.5,'close':43091.87})
        self.assertIsNone(parse_index(day,date(2026,11,13)))
        self.assertIsNone(parse_index({'stat':'No data'},date(2026,11,14)))

    def test_retry_html_then_success(self):
        calls = []
        class Response:
            headers = {'Content-Type':'application/json'}
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return self.data
            def getcode(self): return 200
        data = iter([Response(b'<html>error</html>'),Response(json.dumps({'stat':'OK','tables':[]}).encode())])
        client = TwseClient(opener=lambda *args,**kwargs:next(data),sleeper=lambda seconds:calls.append(seconds),clock=lambda:100.0)
        self.assertEqual(client.fetch('marginTrading/MI_MARGN',{'date':'20261112'}),{'stat':'OK','tables':[]})
        self.assertTrue(any(1 <= x <= 3 for x in calls))
        self.assertGreaterEqual(sum(calls), 1)


if __name__ == '__main__':
    unittest.main()

"""Run: python -m unittest discover -s tests -p 'test_margin_temperature.py' -v"""
import csv
import json
import math
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from margin_temperature import DATA, TwseClient, build_rows, csv_rows, make_report, parse_index, parse_margin, to_report


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
        self.assertFalse(to_report(historical, date(2026, 11, 20))['provisional'])

    def test_dual_flags_independent(self):
        # If both threshold flags are true, neither must override the other.
        output = to_report([{'date':'2026-11-12','close':45000,'pct':99,'mdd':-39,'hot':True,'cold':True}], date(2026,11,13))
        self.assertTrue(output['latest']['hot'])
        self.assertTrue(output['latest']['cold'])

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

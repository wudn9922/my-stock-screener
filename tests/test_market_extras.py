import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_market_extras import collect, extended_quote, ipo_companies

NOW = datetime(2026, 11, 4, 2, tzinfo=timezone.utc)
def payload(mode='post'):
    return {'data': {'lastUpdateInfo': ['Data last updated Nov 3, 2026 08:00 PM ET.'], 'tradeDetailTable': {'rows': [{'time': '19:59:53' if mode == 'post' else '09:20:00', 'price': '$123.45'}]}}}
class MarketExtrasTests(unittest.TestCase):
    def test_actual_trade_time_not_collection_or_regular_close(self):
        value = extended_quote(payload(), 'AAPL', 'post', NOW)
        self.assertEqual(value['price'], 123.45)
        self.assertEqual(value['asOf'], '2026-11-04T00:59:53+00:00')
        self.assertEqual(extended_quote(payload('pre'), 'AAPL', 'post', NOW)['status'], 'unavailable')
    def test_empty_invalid_and_future_quotes_never_fabricate(self):
        for raw in [{'data': None}, {'data': {}}, {'data': {'previousInfo': 'Market Close: $100'}}]:
            self.assertIsNone(extended_quote(raw, 'AAPL', 'pre', NOW)['price'])
        value = extended_quote(payload(), 'AAPL', 'post', datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertIsNone(value['price'])
    def test_calendar_mapping_excludes_priced_and_missing_company(self):
        rows = ipo_companies({'data': {'upcoming': {'upcomingTable': {'rows': [{'companyName': 'Example', 'proposedTickerSymbol': 'EX', 'expectedPriceDate': '11/12/2026', 'proposedExchange': 'NASDAQ'}]}}, 'priced': {'rows': [{'companyName': 'Already listed'}]}}}, NOW.isoformat())
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['price'])
        self.assertIsNone(rows[0]['listingDate'])
        self.assertEqual(rows[0]['expectedPricingDate'], '11/12/2026')
    def test_bounded_collection_writes_only_new_snapshots_and_preserves_failed_quote_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); report = root / 'docs/report'; report.mkdir(parents=True)
            permanent = root / 'data/AAPL.csv'; permanent.parent.mkdir(); permanent.write_text('do not change')
            (report / 'latest.json').write_text(json.dumps({'groups': [{'items': [{'symbol': 'AAPL'}, {'symbol': '2330.TW'}]}]}))
            def get(url):
                return {'data': {'upcoming': {'rows': []}}} if '/ipo/' in url else payload('pre' if url.endswith('=pre') else 'post')
            collect(root, get, NOW, limit=1)
            path = report / 'extended-quotes/AAPL.json'; before = json.loads(path.read_text())
            def fail(url): raise requests.ConnectionError('sensitive raw response')
            collect(root, fail, NOW, limit=1)
            after = json.loads(path.read_text())
            collect(root, lambda url: {'data': {}}, NOW, limit=1)
            empty = json.loads(path.read_text())
            self.assertEqual(empty['post']['asOf'], before['post']['asOf'])
            self.assertEqual(empty['post']['status'], 'stale')
            self.assertEqual(before['post']['asOf'], after['post']['asOf'])
            self.assertEqual(after['post']['status'], 'stale')
            self.assertNotIn('sensitive', path.read_text())
            self.assertEqual(len(list((report / 'extended-quotes').glob('*.json'))), 1)
            self.assertEqual(permanent.read_text(), 'do not change')
    def test_priced_issuer_keeps_pricing_and_listing_dates_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'config').mkdir()
            seed = {'id': 'example', 'name': 'Example', 'symbol': 'EX', 'aliases': ['Brand'], 'status': '申請上市', 'price': None, 'listingDate': None, 'checkedAt': '2026-11-01T00:00:00Z'}
            (root / 'config/ipo-watch.json').write_text(json.dumps([seed]))
            def get(url):
                if '/ipo/' in url:
                    return {'data': {'priced': {'rows': [{'companyName': 'Example Inc', 'proposedTickerSymbol': 'EX', 'pricedDate': '11/03/2026', 'proposedSharePrice': '20.00'}]}}}
                return {'data': None}
            collect(root, get, NOW, limit=1)
            company = json.loads((root / 'docs/report/ipo-watch.json').read_text())['companies'][0]
            self.assertIn('已定價', company['status'])
            self.assertEqual(company['price'], '20.00')
            self.assertIsNone(company['listingDate'])
            self.assertEqual(company['pricingDate'], '11/03/2026')
            self.assertEqual(company['aliases'], ['Brand'])
if __name__ == '__main__': unittest.main()

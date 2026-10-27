import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# The report tests do not open a browser or send LINE messages.
if 'playwright.sync_api' not in sys.modules:
    api = types.ModuleType('playwright.sync_api')
    api.sync_playwright = mock.Mock()
    sys.modules['playwright'] = types.ModuleType('playwright')
    sys.modules['playwright.sync_api'] = api

import dify_report


class TrendingStocksTest(unittest.TestCase):
    def test_same_line_order_and_atomic_last_good_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report' / 'trending-stocks.json'
            stamp = '2026-10-27T09:00:00+00:00'
            self.assertTrue(dify_report.save_trending_stocks('NVDA, AAPL, AMD, TSLA, MSFT', stamp, path))
            data = json.loads(path.read_text())
            self.assertEqual(data['symbols'], ['NVDA', 'AAPL', 'AMD', 'TSLA', 'MSFT'])
            self.assertEqual(data['fetchedAt'], stamp)
            self.assertEqual(data['version'], 1)
            self.assertFalse(dify_report.save_trending_stocks('', stamp, path))
            self.assertEqual(json.loads(path.read_text()), data)
            self.assertFalse(path.with_suffix('.tmp').exists())

    def test_deduplicates_and_limits_to_five(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trending.json'
            dify_report.save_trending_stocks('nvda, NVDA, AAPL, AMD, TSLA, MSFT, META', '2026-10-27T09:00:00Z', path)
            self.assertEqual(json.loads(path.read_text())['symbols'], ['NVDA', 'AAPL', 'AMD', 'TSLA', 'MSFT'])

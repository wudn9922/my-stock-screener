"""Daily collection must not skip a stock because of its traded share count."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class MarketVolumeScopeTest(unittest.TestCase):
    def test_official_list_includes_low_zero_and_missing_volume(self):
        twse = Mock(status_code=200, text=(
            '證券代號,證券名稱,成交股數\n'
            '1101,低量,1\n1102,零量,0\n1103,缺量,\n0050,ETF,9999999\n'
        ))
        tpex = Mock(status_code=200)
        tpex.json.return_value = [
            {'SecuritiesCompanyCode': '6488', 'CompanyName': '低量', 'TradingShares': '1'},
            {'SecuritiesCompanyCode': '8069', 'CompanyName': '零量', 'TradingShares': '0'},
            {'SecuritiesCompanyCode': '6104', 'CompanyName': '缺量'},
        ]
        with patch.object(main.requests, 'get', side_effect=[twse, tpex]), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(main.get_tw_tickers(), ['1101.TW', '1102.TW', '1103.TW', '6104.TWO', '6488.TWO', '8069.TWO'])

    def test_existing_low_volume_csvs_keep_updating_and_can_match_ma20(self):
        dates = pd.bdate_range(end='2026-10-20', periods=30)
        frames = {
            symbol: pd.DataFrame({'Open': 10., 'High': 11., 'Low': 9., 'Close': 10., 'Volume': volume}, index=dates)
            for symbol, volume in [('1101.TW', 0), ('LOW', 1)]
        }
        with tempfile.TemporaryDirectory() as directory:
            for symbol, frame in frames.items():
                frame.iloc[:-2].to_csv(Path(directory) / f'{symbol}.csv')
            with patch.object(main, 'DATA_DIR', directory), \
                 patch.object(main, 'download_market_data', return_value=frames['LOW']), \
                 patch.object(main, 'extract_yfinance_data', side_effect=lambda _data, symbol: frames[symbol]), \
                 patch.object(main, 'build_stock_data', return_value={}), \
                 patch('sys.stdout', new_callable=io.StringIO):
                matches = main.scan_market(list(frames))
            self.assertEqual({item['ticker'] for item in matches}, set(frames))
            for symbol in frames:
                saved = pd.read_csv(Path(directory) / f'{symbol}.csv', index_col=0)
                self.assertEqual(len(saved), 30)
                self.assertEqual(saved.index[-1], '2026-10-20')


if __name__ == '__main__':
    unittest.main()

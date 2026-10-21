"""Daily collection must not skip a stock because of its traded share count."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

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

    def test_existing_low_volume_csvs_update_but_tw_all_uses_latest_volume(self):
        dates = pd.bdate_range(end='2026-10-20', periods=30)
        latest_volumes = {
            '1101.TW': 999_999,
            '6488.TWO': 1,
            '6104.TWO': 0,
            '2330.TW': 1_000_000,
            '8069.TWO': 1_000_000,
            'LOW': 1,
            'ZERO': 0,
        }
        frames = {
            symbol: pd.DataFrame({'Open': 10., 'High': 11., 'Low': 9., 'Close': 10., 'Volume': 2_000_000}, index=dates)
            for symbol in latest_volumes
        }
        for symbol, frame in frames.items():
            if symbol in ('2330.TW', '8069.TWO'):
                frame['Volume'] = 0
            frame.loc[dates[-1], 'Volume'] = latest_volumes[symbol]
        tw_symbols = [symbol for symbol in frames if symbol.endswith(('.TW', '.TWO'))]
        us_symbols = [symbol for symbol in frames if symbol not in tw_symbols]
        with tempfile.TemporaryDirectory() as directory:
            for symbol, frame in frames.items():
                frame.iloc[:-2].to_csv(Path(directory) / f'{symbol}.csv')
            with patch.object(main, 'DATA_DIR', directory), \
                 patch.object(main, 'download_market_data', return_value=frames['LOW']) as download, \
                 patch.object(main, 'extract_yfinance_data', side_effect=lambda _data, symbol: frames[symbol]), \
                 patch.object(main, 'build_stock_data', return_value={}), \
                 patch('sys.stdout', new_callable=io.StringIO):
                matches = main.scan_market(tw_symbols, report_min_volume=main.TW_MIN_VOLUME)
                matches += main.scan_market(us_symbols)
            self.assertEqual(download.call_args_list, [call(tw_symbols, '15d'), call(us_symbols, '15d')])
            self.assertEqual({item['ticker'] for item in matches}, {'2330.TW', '8069.TWO', 'LOW', 'ZERO'})
            for symbol in frames:
                saved = pd.read_csv(Path(directory) / f'{symbol}.csv', index_col=0)
                self.assertEqual(len(saved), 30)
                self.assertEqual(saved.index[-1], '2026-10-20')
                self.assertEqual(saved['Volume'].iloc[-1], latest_volumes[symbol])

    def test_new_low_volume_taiwan_csv_is_downloaded_but_not_in_tw_all(self):
        frame = pd.DataFrame(
            {'Open': 10., 'High': 11., 'Low': 9., 'Close': 10., 'Volume': 1},
            index=pd.bdate_range(end='2026-10-20', periods=30),
        )
        symbols = ['1101.TW', '6488.TWO']
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(main, 'DATA_DIR', directory), \
                 patch.object(main, 'download_market_data', return_value=frame) as download, \
                 patch.object(main, 'extract_yfinance_data', return_value=frame), \
                 patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main.scan_market(symbols, report_min_volume=main.TW_MIN_VOLUME), [])
            download.assert_called_once_with(symbols, '250d')
            for symbol in symbols:
                saved = pd.read_csv(Path(directory) / f'{symbol}.csv', index_col=0)
                self.assertEqual(len(saved), 30)
                self.assertEqual(saved.index[-1], '2026-10-20')


    def test_report_floor_filters_list_but_still_updates_low_volume_csv(self):
        dates = pd.bdate_range(end='2026-10-20', periods=30)
        frames = {
            symbol: pd.DataFrame({'Open': 10., 'High': 11., 'Low': 9., 'Close': 10., 'Volume': volume}, index=dates)
            for symbol, volume in [('1101.TW', 1_000), ('2330.TW', 2_000_000)]
        }
        with tempfile.TemporaryDirectory() as directory:
            for symbol, frame in frames.items():
                frame.iloc[:-2].to_csv(Path(directory) / f'{symbol}.csv')
            with patch.object(main, 'DATA_DIR', directory), \
                 patch.object(main, 'download_market_data', return_value=frames['2330.TW']), \
                 patch.object(main, 'extract_yfinance_data', side_effect=lambda _data, symbol: frames[symbol]), \
                 patch.object(main, 'build_stock_data', return_value={}), \
                 patch('sys.stdout', new_callable=io.StringIO):
                matches = main.scan_market(list(frames), report_min_volume=main.TW_MIN_VOLUME)
            self.assertEqual({item['ticker'] for item in matches}, {'2330.TW'})
            for symbol in frames:
                saved = pd.read_csv(Path(directory) / f'{symbol}.csv', index_col=0)
                self.assertEqual(saved.index[-1], '2026-10-20')

if __name__ == '__main__':
    unittest.main()

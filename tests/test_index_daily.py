"""Offline transport/archive contracts; never calls Yahoo or writes real records."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import index_daily as daily
import kline_archive as ka

NOW = datetime(2026, 11, 5, 4, tzinfo=timezone.utc)


def bars(symbol='^IXIC', count=440):
    cal = daily._calendar('tw' if symbol == '^TWII' else 'us', 2023, 2027)
    dates = cal.sessions_in_range('2023-01-03', '2026-11-04')[-count:]
    return pd.DataFrame({'Open':100., 'High':101., 'Low':99., 'Close':100., 'Volume':10000.}, index=dates)


def archive_for(root, symbol, frame):
    stats = {}
    for year, part in frame.groupby(frame.index.year):
        path = root / daily.bucket(symbol) / symbol / f'{year}.csv'
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = ka.frame_rows(part)
        body = ka.csv_text(rows).encode()
        path.write_bytes(body)
        stats[path.relative_to(root).as_posix()] = ka._row_stats(rows, len(body), ka.digest(body))
    for name, document in ka._index_documents(stats).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document))


class IndexDailyTests(unittest.TestCase):
    def read(self, symbols, download, root, **kwargs):
        sleep, logs = Mock(), []
        result = daily.read_index_daily(symbols, period='2y', min_bars=400, now=NOW,
                                        archive_dir=root, download=download, sleep=sleep,
                                        log=logs.append, **kwargs)
        return result, sleep, logs

    def test_empty_batch_member_retries_only_that_symbol_and_succeeds(self):
        tw, us = bars('^TWII'), bars()
        raw = pd.concat({'^TWII':tw, '^IXIC':us * np.nan}, axis=1, sort=True).swaplevel(axis=1)
        download = Mock(side_effect=[raw, us])
        with tempfile.TemporaryDirectory() as folder:
            result, sleep, _ = self.read(['^TWII','^IXIC'], download, folder)
        self.assertFalse(result.errors)
        self.assertEqual([c.args[0] for c in download.call_args_list], [['^TWII','^IXIC'],['^IXIC']])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [5])
        self.assertEqual(result.sources['^IXIC']['attempts'], 2)
        self.assertEqual(result.sources['^TWII']['attempts'], 1)
        pd.testing.assert_frame_equal(result['^IXIC'], us, check_freq=False)
        for call in download.call_args_list:
            self.assertFalse(call.kwargs['auto_adjust'])

    def test_two_failed_retries_use_sha_checked_archive_and_do_not_write_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            frame = bars()
            frame.iloc[0, frame.columns.get_loc('Volume')] = np.nan
            archive_for(root, '^IXIC', frame)
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            result, sleep, logs = self.read(['^IXIC'], Mock(return_value=pd.DataFrame()), root)
            self.assertEqual(result.sources['^IXIC']['source'], 'kline-archive')
            self.assertEqual(result.sources['^IXIC']['attempts'], 3)
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [5,10])
            self.assertTrue(any('^IXIC' in line and '備援' in line for line in logs))
            self.assertTrue(pd.isna(result['^IXIC'].Volume.iloc[0]))
            self.assertEqual(result['^IXIC'].Volume.iloc[-1],10000)
            self.assertEqual(before, {p:p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_invalid_ohlc_count_not_just_nonnull_close_triggers_fallback(self):
        frame = bars()
        bad = frame.copy()
        bad.iloc[:100,bad.columns.get_loc('Open')] = np.nan
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive_for(root, '^IXIC', frame)
            result, _, _ = self.read(['^IXIC'], Mock(return_value=bad), root)
            self.assertEqual(result.sources['^IXIC']['source'], 'kline-archive')
            self.assertEqual(daily.valid_count(result['^IXIC']), 440)

    def test_both_sources_fail_returns_symbol_named_error(self):
        with tempfile.TemporaryDirectory() as folder:
            result, sleep, _ = self.read(['^IXIC'], Mock(side_effect=RuntimeError('offline')), folder)
        self.assertFalse(result)
        self.assertIn('^IXIC', result.errors['^IXIC'])
        self.assertIn('offline', result.errors['^IXIC'])
        self.assertEqual(sleep.call_count, 2)

    def test_rate_limit_uses_existing_cooldowns_and_stops_yahoo_for_remaining_symbols(self):
        download = Mock(side_effect=daily.RateLimited(429, 'Yahoo'))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for symbol in ['^IXIC','^GSPC']:
                archive_for(root, symbol, bars())
            result, sleep, _ = self.read(['^IXIC','^GSPC'], download, root)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [30,60])
        self.assertEqual(download.call_count, 3) # batch + two bounded retries
        self.assertEqual(set(result), {'^IXIC','^GSPC'})
        self.assertEqual(result.sources['^GSPC']['attempts'], 1)

    def test_corrupt_hash_or_wrong_identity_cannot_be_used_as_fallback(self):
        for mutation in ['hash','identity','years-list','document-list']:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                archive_for(root, '^IXIC', bars())
                path = root / 'index/us/%5EIXIC.json'
                doc = json.loads(path.read_text())
                if mutation == 'hash':
                    doc['years']['2026']['sha256'] = '0'*64
                elif mutation == 'identity':
                    doc['symbol'] = 'QQQ'
                elif mutation == 'years-list':
                    doc['years'] = []
                else:
                    doc = []
                path.write_text(json.dumps(doc))
                result, _, _ = self.read(['^IXIC'], Mock(return_value=pd.DataFrame()), root)
                self.assertFalse(result)
                self.assertIn('不符', result.errors['^IXIC'])

    def test_stale_archive_cannot_satisfy_breadth_date_alignment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive_for(root, '^IXIC', bars().iloc[:-1])
            result, _, _ = self.read(['^IXIC'], Mock(return_value=pd.DataFrame()), root, as_of='2026-11-04')
            self.assertFalse(result)
            self.assertIn('資料日', result.errors['^IXIC'])

    def test_null_row_on_expected_day_is_incomplete_and_retried(self):
        frame = bars()
        partial = frame.copy()
        partial.iloc[-1] = np.nan
        download = Mock(side_effect=[partial, frame])
        with tempfile.TemporaryDirectory() as folder:
            result, sleep, _ = self.read(['^IXIC'], download, folder, as_of='2026-11-04')
        self.assertFalse(result.errors)
        self.assertEqual(result.sources['^IXIC']['attempts'], 2)
        self.assertEqual(sleep.call_count, 1)

    def test_completed_only_tw_noon_and_us_early_close(self):
        frame = bars('^TWII')
        frame.loc[pd.Timestamp('2026-11-05')] = [100,101,99,100,10000]
        frame.loc[pd.Timestamp('2026-11-07')] = [100,101,99,100,10000]
        self.assertEqual(daily.completed_bars(frame, '^TWII', NOW).index[-1], pd.Timestamp('2026-11-04'))
        frame = bars().tail(1).copy()
        frame.index = pd.to_datetime(['2026-11-27'])
        self.assertTrue(daily.completed_bars(frame, '^IXIC', datetime(2026,11,27,17,59,tzinfo=timezone.utc)).empty)
        self.assertEqual(len(daily.completed_bars(frame, '^IXIC', datetime(2026,11,27,18,1,tzinfo=timezone.utc))),1)

    def test_dividend_adjusted_column_is_ignored_and_invalid_sessions_not_bridged(self):
        frame = bars('SOXX')
        frame['Adj Close'] = 50
        frame.loc[frame.index[-2], 'High'] = 0
        with tempfile.TemporaryDirectory() as folder:
            result, _, _ = self.read(['SOXX'], Mock(return_value=frame), folder)
        self.assertEqual(result['SOXX'].Close.iloc[-1],100)
        self.assertEqual(result['SOXX'].High.iloc[-2],0)
        self.assertEqual(result.sources['SOXX']['priceBasis'],'split-adjusted')
        self.assertFalse(result.sources['SOXX']['dividendAdjusted'])


if __name__ == '__main__':
    unittest.main()

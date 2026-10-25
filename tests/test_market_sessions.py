"""Market closures must preserve the last real scan, breadth and filter metrics."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import main
import report_metrics as rm
import build_breadth as bb
from scripts.market_sessions import analysis_prices, market_prices, non_trading_dates
from scripts.restore_closed_market_snapshot import restore


def prices(volume=2_000_000):
    dates = pd.bdate_range(end='2026-10-22', periods=80)
    values = [100.0] * 79 + [100.5]
    return pd.DataFrame({'Open': values, 'High': [x + 1 for x in values],
                         'Low': [x - 1 for x in values], 'Close': values,
                         'Volume': [float(volume)] * 79 + [float(volume) * 2]}, index=dates)


def closure(frame, days=('2026-10-23', '2026-10-24', '2026-10-25')):
    result = frame.copy()
    for day in days:
        result.loc[pd.Timestamp(day)] = [frame['Close'].iloc[-1]] * 4 + [0]
    return result


class MarketSessionTest(unittest.TestCase):
    def test_weekend_and_weekday_closures_remove_only_the_shared_placeholders(self):
        original = {'2330.TW': prices(), '6488.TWO': prices(3_000_000)}
        raw = {s: closure(f) for s, f in original.items()}
        before = {s: f.copy(deep=True) for s, f in raw.items()}
        projected, excluded = market_prices(raw)
        self.assertEqual(excluded, frozenset(['2026-10-23', '2026-10-24', '2026-10-25']))
        for symbol in raw:
            pd.testing.assert_frame_equal(projected[symbol], original[symbol], check_freq=False)
            pd.testing.assert_frame_equal(raw[symbol], before[symbol])

    def test_long_closure_does_not_age_a_complete_market_into_empty_data(self):
        days = pd.date_range('2026-10-23', periods=14).strftime('%Y-%m-%d')
        projected, _ = market_prices({'A': closure(prices(), days), 'B': closure(prices(), days)})
        self.assertEqual({f.index[-1].strftime('%Y-%m-%d') for f in projected.values()}, {'2026-10-22'})

    def test_an_individual_halt_on_an_active_market_day_is_never_rewound(self):
        frames = {'A': closure(prices(), ['2026-10-23']), 'B': closure(prices(), ['2026-10-23'])}
        frames['B'].loc[pd.Timestamp('2026-10-23'), 'Volume'] = 1_000
        projected, excluded = market_prices(frames)
        self.assertNotIn('2026-10-23', excluded)
        self.assertEqual(projected['A'].index[-1], pd.Timestamp('2026-10-23'))
        self.assertEqual(projected['A']['Volume'].iloc[-1], 0)

    def test_sparse_zero_data_cannot_declare_a_whole_market_closed(self):
        frames = {str(i): prices() for i in range(8)}
        frames['0'] = closure(frames['0'], ['2026-10-23'])
        self.assertNotIn('2026-10-23', non_trading_dates(frames))

    def test_unknown_volume_is_not_evidence_of_a_closure_and_lineage_survives(self):
        a = closure(prices(), ['2026-10-23'])
        b = a.copy()
        b.attrs[rm.UNKNOWN_VOLUME_DATES] = ('2026-10-23T00:00:00',)
        self.assertNotIn('2026-10-23', non_trading_dates({'A': a, 'B': b}))
        b.attrs[rm.UNKNOWN_VOLUME_DATES] += ('2026-10-21T00:00:00',)
        projected = analysis_prices(b, ['2026-10-23'])
        self.assertEqual(projected.attrs, b.attrs)
        self.assertIsNone(rm.compute_stock_metrics(projected)['volumeAvg20'])

    def test_breadth_matches_the_last_traded_session_instead_of_a_zero_snapshot(self):
        base = {'2330.TW': prices(), '6488.TWO': prices()}
        expected = bb.build_market(base, 'test', {'2330.TW': 'twse', '6488.TWO': 'tpex'})
        actual = bb.build_market({s: closure(f) for s, f in base.items()}, 'test', {'2330.TW': 'twse', '6488.TWO': 'tpex'})
        self.assertEqual(actual, expected)
        self.assertEqual(actual['asOf'], '2026-10-22')
        self.assertGreater(actual['segments']['all']['ma20']['total'], 0)

    def test_empty_volume_history_produces_no_new_market(self):
        frame = prices(0)
        self.assertIsNone(bb.build_market({'A': frame, 'B': frame.copy()}, 'empty'))

    def test_partial_refresh_preserves_the_other_markets_existing_valid_snapshot(self):
        old_tw = bb.build_market({'2330.TW': prices(), '6488.TWO': prices()}, 'saved')
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'data'
            data.mkdir()
            prices().to_csv(data / 'AAPL.csv', index_label='Date')
            output = Path(directory) / 'breadth.json'
            output.write_text(json.dumps({'version': 1, 'markets': {'tw': old_tw}}))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bb.main(['--data-dir', str(data), '--out', str(output)]), 0)
            result = json.loads(output.read_text())
            self.assertEqual(result['markets']['tw'], old_tw)
            self.assertGreater(result['markets']['us']['segments']['all']['count'], 0)

    def test_scan_and_filter_metrics_replay_prior_session_without_writing_raw_csvs(self):
        symbols = ['2330.TW', '6488.TWO']
        with tempfile.TemporaryDirectory() as directory:
            base = {s: prices() for s in symbols}
            for symbol, frame in base.items():
                frame.to_csv(Path(directory) / f'{symbol}.csv')
            with contextlib.redirect_stdout(io.StringIO()):
                expected = rm.build_universe(symbols, 'TW', directory, {'symbol': '^TWII'})
            for symbol, frame in base.items():
                closure(frame).to_csv(Path(directory) / f'{symbol}.csv')
            paths = list(Path(directory).glob('*.csv'))
            hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            with patch.object(main, 'DATA_DIR', directory), patch.dict(main.REPORT_NON_TRADING_DATES, clear=True), \
                 patch.object(main, 'get_tw_stock_name', side_effect=lambda s: s), \
                 contextlib.redirect_stdout(io.StringIO()):
                projected = main._scan_analysis_frames(symbols)
                matches = main._market_scan_report(symbols, projected, main.TW_MIN_VOLUME)
                actual = rm.build_universe(symbols, 'TW', directory, {'symbol': '^TWII'})
            self.assertEqual({item['ticker'] for item in matches}, set(symbols))
            self.assertTrue(all(item['volume'] == 4_000_000 for item in matches))
            self.assertEqual(actual['records'], expected['records'])
            self.assertEqual(actual['asOf'], '2026-10-22')
            lookup = rm.make_lookup({'TW': actual}, directory, {})
            extra = '1101.TW'
            closure(prices()).to_csv(Path(directory) / f'{extra}.csv')
            self.assertEqual(lookup(extra), rm.compute_stock_metrics(prices(), bench_symbol='^TWII'))
            self.assertEqual(hashes, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_scan_keeps_halts_excluded_and_market_calendars_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = closure(prices(), ['2026-10-23']), closure(prices(), ['2026-10-23'])
            b.loc[pd.Timestamp('2026-10-23'), 'Volume'] = 2_000_000
            a.to_csv(Path(directory) / '2330.TW.csv')
            b.to_csv(Path(directory) / '6488.TWO.csv')
            with patch.object(main, 'DATA_DIR', directory), patch.dict(main.REPORT_NON_TRADING_DATES, {'TW': frozenset(), 'US': frozenset(['2026-10-20'])}, clear=True), \
                 patch.object(main, 'get_tw_stock_name', side_effect=lambda s: s), contextlib.redirect_stdout(io.StringIO()):
                projected = main._scan_analysis_frames(['2330.TW', '6488.TWO'])
                matches = main._market_scan_report(list(projected), projected, main.TW_MIN_VOLUME)
                self.assertEqual([item['ticker'] for item in matches], ['6488.TWO'])
                self.assertEqual(main.REPORT_NON_TRADING_DATES['US'], frozenset(['2026-10-20']))


class SnapshotRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current, self.previous, self.data, self.out = [self.root / name for name in ('current', 'previous', 'data', 'review')]
        for path in (self.current, self.previous, self.data):
            path.mkdir()
        rows = []
        for symbol in ('2330.TW', '6488.TWO'):
            frame = prices()
            closure(frame).to_csv(self.data / f'{symbol}.csv', index_label='Date')
            rows.append({'symbol': symbol, 'asOf': '2026-10-22', 'close': 100.5,
                         'volume': 4_000_000, 'metrics': {'rsRank': 72}})
        self.saved = {'groups': [{'key': 'tw_all', 'maList': [20], 'items': rows}]}
        self.latest = {'reportDate': '2026-10-25', 'groups': [
            {'key': 'tw_all', 'maList': [20], 'items': [], 'count': 0},
            {'key': 'us_all', 'items': [{'symbol': 'AAPL', 'asOf': '2026-10-23'}], 'count': 1},
        ]}
        self.width = {'markets': {
            'tw': {'asOf': '2026-10-23', 'universe': 'Taiwan test', 'segments': {'all': {'count': 0}}},
            'us': {'asOf': '2026-10-23', 'universe': 'Unchanged US', 'segments': {'all': {'count': 1}}},
        }}
        self.write_inputs()

    def write_inputs(self):
        for directory, name, value in [(self.current, 'latest.json', self.latest),
                                       (self.current, 'breadth.json', self.width),
                                       (self.previous, 'latest.json', self.saved)]:
            (directory / name).write_text(json.dumps(value))

    def test_recovery_validates_saved_prices_and_adds_real_average_without_mutating_inputs(self):
        before = {p: p.read_bytes() for d in (self.current, self.previous, self.data) for p in d.iterdir()}
        proof = restore(self.current, self.previous, self.data, self.out)
        result = json.loads((self.out / 'latest.json').read_text())
        width = json.loads((self.out / 'breadth.json').read_text())
        self.assertEqual(proof['restoredScanRows'], 2)
        self.assertEqual(proof['rejectedRows'], [])
        self.assertEqual(width['markets']['tw']['asOf'], '2026-10-22')
        self.assertEqual(width['markets']['tw']['segments']['all']['count'], 2)
        self.assertEqual(width['markets']['us'], self.width['markets']['us'])
        self.assertEqual(result['groups'][1:], self.latest['groups'][1:])
        for item, saved in zip(result['groups'][0]['items'], self.saved['groups'][0]['items']):
            expected = copy.deepcopy(saved)
            expected['metrics']['volumeAvg20'] = 2_100_000
            self.assertEqual(item, expected)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_saved_row_with_mismatched_volume_is_rejected(self):
        self.saved['groups'][0]['items'][0]['volume'] = 123
        self.write_inputs()
        proof = restore(self.current, self.previous, self.data, self.out)
        self.assertEqual(proof['restoredScanRows'], 1)
        self.assertEqual([row['symbol'] for row in proof['rejectedRows']], ['2330.TW'])
        self.assertEqual(json.loads((self.out / 'latest.json').read_text())['groups'][0]['items'][0]['symbol'], '6488.TWO')

    def test_recovery_refuses_nonempty_or_wrong_session_inputs_and_source_output_paths(self):
        for output in (self.current, self.data / 'nested-review'):
            with self.assertRaisesRegex(ValueError, 'separate'):
                restore(self.current, self.previous, self.data, output)
        self.latest['groups'][0]['items'] = [{'symbol': '2330.TW'}]
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            restore(self.current, self.previous, self.data, self.out)
        self.latest['groups'][0]['items'] = []
        self.saved['groups'][0]['items'][0]['asOf'] = '2026-10-21'
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, 'latest observed trading session'):
            restore(self.current, self.previous, self.data, self.out)
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()

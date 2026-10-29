"""Acceptance and permanent-record boundaries: real readers, no live network."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import kline_integrity as k
import report_metrics
import universe_cache
from build_breadth import load_daily_bars
from research_io import price_rows

NOW = datetime(2026, 10, 22, 22, tzinfo=timezone.utc)


def frame(day="2026-10-22", **kwargs):
    return pd.DataFrame([dict(Open=10, High=12, Low=9, Close=11, Volume=100, **{}) | kwargs],
                        index=pd.to_datetime([day]))


class KlineIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "corrections.json"
        self.patch = patch.object(k, "LOG_PATH", self.log)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_bounds_and_positive_finite_prices(self):
        for values in [dict(Low=12), dict(High=9), dict(Open=0), dict(Close=-1), dict(High=float('inf'))]:
            self.assertIsNotNone(k.violation(frame(**values).iloc[0]))
        self.assertIsNone(k.violation(frame().iloc[0]))

    def test_latest_invalid_refetched_once_and_not_clamped(self):
        retry = Mock(return_value=frame(Low=8))
        official = Mock()
        result = k.accept_frame(frame(Low=11.5), "3555.TWO", now=NOW, refetch=retry, official=official)
        self.assertEqual(result.iloc[0].Low, 8)
        retry.assert_called_once()
        official.assert_not_called()
        event = k.read_log()["events"][-1]
        self.assertEqual((event['old']['Low'], event['new']['Low']), (11.5, 8))

    def test_failed_retry_uses_dated_official_taiwan_bar(self):
        values = k.bar_values(frame(Low=7).iloc[0])
        retry = Mock(return_value=frame(Low=12))
        official = Mock(return_value=(values, "https://official.example/20261022", NOW.isoformat()))
        result = k.accept_frame(frame(Low=12), "4133.TW", now=NOW, refetch=retry, official=official)
        self.assertEqual(result.iloc[0].Low, 7)
        retry.assert_called_once()
        official.assert_called_once_with("4133.TW", "2026-10-22")
        self.assertEqual(k.read_log()['events'][-1]['sourceUrl'], "https://official.example/20261022")

    def test_invalid_non_taiwan_bar_rejected_and_logged_without_official(self):
        official = Mock()
        retry = Mock(return_value=frame(Low=12))
        self.assertTrue(k.accept_frame(frame(Low=12), "AAPL", now=NOW, refetch=retry, official=official).empty)
        official.assert_not_called()
        self.assertEqual(k.read_log()['events'][-1]['status'], 'rejected')

    def test_latest_completed_bar_retries_even_when_forming_row_follows(self):
        raw = pd.concat([frame("2026-10-21", Low=12), frame("2026-10-22")])
        retry = Mock(return_value=frame("2026-10-21", Low=8))
        now = datetime(2026, 10, 22, 18, tzinfo=timezone.utc)
        result = k.accept_frame(raw, "AAPL", now=now, refetch=retry, official=None)
        self.assertEqual(list(result.index.strftime('%Y-%m-%d')), ['2026-10-21'])
        retry.assert_called_once()
        # The forming row is not stored, and no longer logged on every sighting.
        self.assertNotIn('deferred', [e['status'] for e in k.read_log()['events']])

    def test_permanent_rows_and_source_precision_survive_filtered_writes(self):
        path = Path(self.tmp.name) / '7825.TWO.csv'
        original = 'Date,Open,High,Low,Close,Volume\n2026-08-27,87.000000000001,96,85,93,100\n2026-08-28,87,105,94.7,95.8,200\n'
        path.write_text(original)
        k.log_event('7825.TWO', '2026-08-28', 'quarantined', reason='unverifiable')
        result = k.write_csv(path, frame('2026-08-31'), now=NOW, official=None)
        self.assertEqual(len(pd.read_csv(path)), 3)
        self.assertTrue(path.read_text().startswith(original))
        self.assertNotIn(pd.Timestamp('2026-08-28'), result.index)
        before = path.read_bytes()
        k.write_csv(path, frame('2026-08-31'), now=NOW, official=None)
        self.assertEqual(path.read_bytes(), before)

    def test_quarantine_applies_to_all_python_csv_computation_readers(self):
        path = Path(self.tmp.name) / '7825.TWO.csv'
        data = pd.concat([frame('2026-08-27'), frame('2026-08-28')])
        data.index.name = 'Date'
        data.to_csv(path)
        # Even a structurally valid quarantined row must never enter a computation.
        k.log_event('7825.TWO', '2026-08-28', 'quarantined', reason='unverifiable')
        for reader in [report_metrics.read_price_csv, universe_cache.read_bars, load_daily_bars]:
            with self.subTest(reader=reader.__name__):
                self.assertEqual(list(reader(path).index.strftime('%Y-%m-%d')), ['2026-08-27'])
        self.assertEqual([r['date'] for r in price_rows(path)], ['2026-08-27'])
        self.assertEqual(len(pd.read_csv(path)), 2)

    def test_full_market_writer_validates_and_refetches_without_clamping(self):
        path = Path(self.tmp.name) / 'AAPL.csv'
        retry = Mock(return_value=frame(High=13))
        result = universe_cache.write_bars(path, frame(High=8), refetch=retry, now=NOW)
        self.assertEqual(result.iloc[0].High, 13)
        retry.assert_called_once()

    def test_official_fallback_refuses_a_wrong_report_date(self):
        with patch.object(k, '_official_payload', return_value=({'date':'20261021'}, 'https://official.example', NOW.isoformat())):
            with self.assertRaisesRegex(ValueError, 'date mismatch'):
                k.official_bar('2330.TW', '2026-10-22')

    def test_per_row_observation_date_survives_json_column_round_trip(self):
        rows = []
        for day in ['2026-10-21','2026-10-22']:
            record = dict(symbol='2330.TW',name='台積電',close=10,changePct=1,volume=100,asOf=day,
                          metrics={'ma':{'20':0,'50':None,'200':None}})
            rows.append(report_metrics.record_to_row(record))
        payload = json.loads(json.dumps({'fields':report_metrics.UNIVERSE_FIELDS,'rows':rows}))
        self.assertEqual([dict(zip(payload['fields'],r))['asOf'] for r in payload['rows']], ['2026-10-21','2026-10-22'])

    def test_rs_percentiles_are_computed_within_each_observation_date(self):
        records = [dict(symbol=str(i),asOf='2026-10-21' if i<10 else '2026-10-22',
                        metrics={'rs63':i}) for i in range(20)]
        with patch.object(report_metrics,'read_price_csv',return_value=frame()), \
             patch.object(report_metrics,'build_stock_record',side_effect=records):
            result = report_metrics.build_universe([str(i) for i in range(20)], 'US', self.tmp.name, None)
        self.assertEqual(result['records'][9]['metrics']['rsRank'], result['records'][19]['metrics']['rsRank'])

    def test_final_repository_has_no_unquarantined_ohlc_violations(self):
        bad = []
        for path in (ROOT / 'data').rglob('*.csv'):
            data = pd.read_csv(path, index_col=0)
            p = data[k.FIELDS[:4]]
            mask = ~((p > 0).all(axis=1) & (p < float('inf')).all(axis=1)
                     & (p.Low <= p[['Open','Close']].min(axis=1))
                     & (p.High >= p[['Open','Close']].max(axis=1)))
            for day in data.index[mask]:
                bad.append((path.stem, day))
        self.assertEqual(bad, [('7825.TWO', '2026-08-28')])


if __name__ == '__main__':
    unittest.main()

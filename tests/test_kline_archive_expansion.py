"""Expansion stays public, append-only and separate from screener/account data."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import backfill_kline_archive as bf
import build_universe_full as bu
import kline_archive as ka
import kline_instruments as scope
import kline_integrity as integrity
import refresh_archive_instruments as daily
import universe_cache as uc
import archive_tpex_index as tpex
from test_kline_archive import _ArchiveCase, row, frame

NOW = datetime(2026, 11, 4, 23, tzinfo=timezone.utc)

def metadata(symbol):
    profile = scope.INDEX_METADATA.get(symbol, {})
    return {'kind': 'index' if symbol.startswith('^') else 'etf', 'name': symbol,
            'currency': profile.get('currency', 'TWD' if symbol.endswith(('.TW', '.TWO')) else 'USD'),
            'timezone': profile.get('timezone', 'Asia/Taipei' if symbol.endswith(('.TW', '.TWO')) else 'America/New_York'), 'source': 'synthetic public fixture'}

class ExpansionTests(_ArchiveCase):
    def test_tpex_actual_month_shape_requires_real_ohlc_and_keeps_unknown_volume(self):
        payload = {'stat': 'ok', 'tables': [{'title': '櫃買指數(月查詢)',
                   'fields': ['日期', '開市', '最高', '最低', '收市', '漲/跌'],
                   'data': [['2021/01/04', '184.29', '185.80', '184.29', '185.80', '1.70'],
                            ['110/01/05', '185.81', '186.31', '184.90', '185.95', '0.15']]}]}
        result = ka.frame_rows(tpex.parse_month(payload))
        self.assertEqual(result[0], ka.normalize(['2021-01-04', 184.29, 185.80, 184.29, 185.80, None]))
        self.assertEqual(result[1][0], '2021-01-05')
        with self.assertRaisesRegex(ValueError, 'actual daily OHLC'):
            tpex.parse_month({'tables': [{'fields': ['日期', '收市'], 'data': [['2021/01/04', '185.8']]}]})
        with self.assertRaisesRegex(ValueError, 'invalid dated OHLC'):
            bad = copy.deepcopy(payload)
            bad['tables'][0]['data'][0][2] = '1'
            tpex.parse_month(bad)

    def test_tpex_reads_existing_source_without_writing_and_budget_failure_is_retryable(self):
        source = self.root / 'permanent.csv'
        source.write_text(ka.csv_text([row('2026-11-03', volume=123), row('2026-11-04', volume=456)]))
        before = source.read_bytes()
        calls = []
        class Net:
            def get_json(self, url, params):
                calls.append((url, params))
                return {'tables': [{'title': '櫃買指數(月查詢)', 'fields': ['日期', '開市', '最高', '最低', '收市'],
                    'data': [['2026/10/30', '100', '102', '99', '101']]}]}
        result = tpex.history('2026-10-01', NOW, Net(), source_csv=source, sleep=lambda _: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]['d'], '115/10')
        self.assertEqual(ka.frame_rows(result)[-1][-1], '456')
        self.assertEqual(ka.frame_rows(result)[0][-1], '')
        self.assertEqual(source.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'budget exhausted; retry later'):
            tpex.history('2026-10-01', NOW, Net(), source_csv=source, time_left=lambda: 1)
        self.assertEqual(source.read_bytes(), before)

    def test_otc_backfill_and_daily_use_official_source_even_when_yahoo_is_unavailable(self):
        fresh = frame([row('2021-01-04'), row('2026-11-04')])
        with patch.object(tpex, 'history', return_value=fresh) as official, patch.object(bu.Run, 'connect_yahoo', side_effect=AssertionError('not a Yahoo symbol')):
            plan = bf.backfill(self.archive, self.cache, self.payload, years=5, budget=60, now=NOW,
                listing={'TW': {}}, calendar_years=True, include_instruments=True, segment='indices',
                only_symbols='^TWOII', scope_getter=lambda _: '', sleep=lambda _: None)
            self.assertEqual(plan['progress']['symbols']['^TWOII']['status'], 'done')
            self.assertIn('TPEx', plan['instruments']['^TWOII']['source'])
            official.assert_called_once()
            result = daily.refresh(self.archive, self.cache, now=NOW, selected={'^TWOII': metadata('^TWOII')}, sleep=lambda _: None)
            self.assertEqual(result['appended'], 1)

    def test_scope_includes_otc_kind_etf_and_public_flagged_us_etfs_but_no_name_guesses(self):
        root = self.root / 'website'
        (root / 'config').mkdir(parents=True)
        (root / 'config/kline-archive-instruments.json').write_bytes(scope.CONFIG.read_bytes())
        reports = root / 'reports'; reports.mkdir()
        ka.json_write(reports / 'symbol-catalog.json', {'entries': [
            {'symbol': '0050.TW', 'kind': 'etf', 'name': '上市 ETF'},
            {'symbol': '00719B.TWO', 'kind': 'etf', 'name': '上櫃 ETF'},
            {'symbol': '2330.TW', 'kind': 'stock', 'name': '普通股'}]})
        ka.json_write(reports / 'latest.json', {'groups': [{'items': [{'symbol': 'PUBLIC'}, {'symbol': 'FAKE'}, {'symbol': 'TEST'}]}]})
        flags = 'Symbol|Security Name|ETF|Test Issue\nPUBLIC|Public fund|Y|N\nFAKE|ETF in name only|N|N\nTEST|Test fund|Y|Y\n'
        prior = {'OLD': metadata('OLD')}
        selected, _ = scope.scope(root, reports, prior, getter=lambda _: flags)
        self.assertIn('00719B.TWO', selected)
        self.assertIn('PUBLIC', selected)
        self.assertIn('OLD', selected)
        self.assertNotIn('FAKE', selected)
        self.assertNotIn('TEST', selected)
        self.assertNotIn('2330.TW', selected)
        self.assertEqual(scope.bucket('^TWII'), 'tw')
        self.assertEqual(scope.bucket('^N225'), 'global')
        self.assertEqual(scope.url_path('^TWII'), 'tw/%5ETWII')

    def test_small_manifest_and_symbol_indexes_with_legacy_readonly_verification(self):
        self.seed('AAPL', [row('2024-10-24'), row('2026-01-02')])
        ka.put_symbol(self.archive, '^TWII', [row('2021-01-04')], mode='backfill')
        ka.put_symbol(self.archive, '^N225', [row('2021-01-04')], mode='backfill')
        document = ka.update_manifest(self.archive)
        self.assertEqual(document['version'], 3)
        self.assertNotIn('bySymbol', document)
        indexes = {p: ka.json_read(self.archive / p, None) for p in ka._index_documents({q.relative_to(self.archive).as_posix(): ka._row_stats(ka.read_rows(q), q.stat().st_size, ka.digest(q.read_bytes())) for q in ka.archive_files(self.archive)})}
        self.assertEqual(indexes['index/tw/%5ETWII.json']['path'], 'tw/%5ETWII')
        self.assertEqual(indexes['index/global/%5EN225.json']['currency'], 'JPY')
        for item in indexes.values():
            symbol = item['symbol']
            for year, detail in item['years'].items():
                body = (self.archive / ka.market_of(symbol) / symbol / (year + '.csv')).read_bytes()
                self.assertEqual(detail['sha256'], hashlib.sha256(body).hexdigest())
                self.assertEqual(detail['csvBytes'], len(body))
        ka.verify(self.archive)
        # An existing deployed v1 archive remains valid and is never rewritten by verify.
        stats = {p.relative_to(self.archive).as_posix(): ka._row_stats(ka.read_rows(p, strict=True), p.stat().st_size, ka.digest(p.read_bytes())) for p in ka.archive_files(self.archive)}
        total = sum(p.stat().st_size for p in self.archive.rglob('*') if p.is_file() and p.name != 'manifest.json')
        for version in (1, 2):
            ka.json_write(self.archive / 'manifest.json', ka._manifest(stats, total, version=version))
            before = (self.archive / 'manifest.json').read_bytes()
            ka.verify(self.archive)
            self.assertEqual(before, (self.archive / 'manifest.json').read_bytes())

    def test_indexes_only_rewrite_changed_symbols(self):
        self.seed('AAPL')
        self.seed('MSFT')
        paths = list((self.archive / 'index').rglob('*.json'))
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        ka.update_manifest(self.archive)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths})
        ka.put_symbol(self.archive, 'AAPL', [row('2026-01-05')])
        ka.update_manifest(self.archive)
        changed = [p.name for p in paths if p.read_bytes() != before[p][0]]
        self.assertEqual(changed, ['AAPL.json'])
        self.assertEqual((self.archive / 'index/us/MSFT.json').stat().st_mtime_ns,
                         before[self.archive / 'index/us/MSFT.json'][1])
        ka.verify(self.archive)

    def test_index_missing_extra_or_inexact_is_rejected(self):
        self.seed('AAPL')
        index = self.archive / 'index/us/AAPL.json'
        original = index.read_bytes()
        index.unlink()
        with self.assertRaisesRegex(ValueError, 'index file missing'):
            ka.verify(self.archive)
        index.write_bytes(original)
        value = ka.json_read(index, {})
        for field, wrong in [('rows', 999), ('firstDate', '2000-01-01'), ('path', 'us/MSFT')]:
            ka.json_write(index, {**value, field: wrong})
            with self.assertRaisesRegex(ValueError, 'index does not match CSV'):
                ka.verify(self.archive)
        index.write_bytes(original)
        value['years']['2026']['sha256'] = '0' * 64
        ka.json_write(index, value)
        with self.assertRaisesRegex(ValueError, 'index does not match CSV'):
            ka.verify(self.archive)
        index.write_bytes(original)
        ka.json_write(self.archive / 'index/us/UNEXPECTED.json', {})
        with self.assertRaisesRegex(ValueError, 'Unexpected archive index'):
            ka.verify(self.archive, refresh_manifest=True)

    def test_six_year_catalog_stays_small_and_has_no_monolithic_years(self):
        stats = {f'us/A{symbol:04d}/{year}.csv': {'firstDate': f'{year}-01-04', 'lastDate': f'{year}-12-30',
                 'rows': 250, 'csvBytes': 15000, 'sha256': ka.digest(f'{symbol}/{year}'.encode())}
                 for symbol in range(7000) for year in range(2021, 2027)}
        indexes = ka._index_documents(stats)
        index_bytes = sum(len(ka._json_bytes(v)) for v in indexes.values())
        manifest = ka._manifest(stats, 630000000 + index_bytes, indexes=indexes)
        self.assertLess(len(ka._json_bytes(manifest)), 100000)
        self.assertNotIn('bySymbol', manifest)
        self.assertEqual(manifest['indexFiles'], 7000)
        self.assertEqual(manifest['indexBytes'], index_bytes)
        self.assertLess(max(len(ka._json_bytes(v)) for v in indexes.values()), 2500)

    def test_five_complete_years_batch_limit_resume_and_immutable_existing_rows(self):
        self.seed('AAPL', [row('2026-01-02', close=100)])
        base = self.checkpoint()
        existing = copy.deepcopy(ka.symbol_rows(self.archive, 'AAPL'))
        fresh = frame([row('2021-01-04'), row('2026-01-02', close=101)])
        listing = {'US': {'AAPL': {}, 'MSFT': {}}}
        with patch.object(bu.Run, 'connect_yahoo', return_value=True), patch.object(bu.Run, 'fetch_chart', return_value=fresh):
            first = bf.backfill(self.archive, self.cache, self.payload, years=5, budget=60, markets='US', now=NOW, listing=listing, calendar_years=True, max_symbols=1, sleep=lambda _: None)
            self.assertEqual(first['progress']['targetStart'], '2021-01-01')
            self.assertEqual([x['symbol'] for x in first['symbols']], ['AAPL'])
            ka.apply_operations(self.archive, self.payload, now=NOW)
            self.assertEqual(ka.symbol_rows(self.archive, 'AAPL')[-1], existing[0])
            self.assertTrue(ka.json_read(self.archive / ka.CONFLICTS, {})['conflicts'])
            ka.verify(self.archive, base)
            next_payload = self.root / 'next'
            next_plan = bf.backfill(self.archive, self.cache, next_payload, years=5, budget=60, markets='US', now=NOW, listing=listing, calendar_years=True, max_symbols=1, sleep=lambda _: None)
            self.assertEqual([x['symbol'] for x in next_plan['symbols']], ['MSFT'])

    def test_daily_batch_appends_etf_tw_index_global_without_changing_stock_cache_or_main_log(self):
        selected = {s: metadata(s) for s in ['SPY', '^TWII', '^N225']}
        for symbol in selected: ka.put_symbol(self.archive, symbol, [row('2026-11-03')])
        ka.update_manifest(self.archive)
        self.log.write_text('{"version":1,"events":[],"quarantined":[],"marker":"unchanged"}')
        before_log = self.log.read_bytes()
        chart_calls = []
        class Net:
            def connect_yahoo(self): pass
            def quote(self, symbols):
                return {'quoteResponse': {'result': [{'symbol': symbol, 'quoteType': 'INDEX' if symbol.startswith('^') else 'ETF', 'exchangeTimezoneName': metadata(symbol)['timezone'], 'regularMarketTime': int(datetime(2026, 11, 4, 5 if symbol == '^TWII' else 6 if symbol == '^N225' else 21, 30, tzinfo=timezone.utc).timestamp()), 'regularMarketOpen': 100, 'regularMarketDayHigh': 102, 'regularMarketDayLow': 99, 'regularMarketPrice': 101, 'regularMarketPreviousClose': 100, 'regularMarketVolume': None} for symbol in symbols]}}
            def chart(self, symbol, range_):
                chart_calls.append(symbol)
                raise AssertionError('Consecutive completed quotes should use the batch fast path')
        result = daily.refresh(self.archive, self.cache, budget=300, now=NOW, net=Net(), selected=selected, sleep=lambda _: None)
        self.assertEqual(result['appended'], 3)
        self.assertEqual(chart_calls, [])
        self.assertEqual(before_log, self.log.read_bytes())
        daily.stage(self.archive, self.cache, self.payload, now=NOW)
        ka.apply_operations(self.archive, self.payload, now=NOW)
        for symbol in selected:
            self.assertEqual(ka.symbol_rows(self.archive, symbol)[-1][0], '2026-11-04')
            self.assertEqual(ka.symbol_rows(self.archive, symbol)[-1][-1], '')
        manifest = ka.json_read(self.archive / 'manifest.json', {})
        self.assertEqual(manifest['markets']['us']['symbols'], ['SPY'])
        self.assertEqual(manifest['markets']['global']['symbols'], ['^N225'])
        ka.verify(self.archive)

    def test_primary_stock_payload_survives_empty_supplemental_source(self):
        self.cache_rows('AAPL', [row('2026-01-02')], now=NOW)
        primary = self.root / 'primary'
        ka.stage_cache(self.cache, self.archive, primary, now=NOW)
        separate_cache = self.root / 'empty-instruments'
        daily.stage(self.archive, separate_cache, self.payload, previous_payload=primary, now=NOW)
        ka.apply_operations(self.archive, self.payload, now=NOW)
        self.assertEqual(ka.symbol_rows(self.archive, 'AAPL'), ka.read_rows(primary / 'us/AAPL.csv'))

    def test_daily_recovers_every_missing_session_and_source_failure_keeps_previous_csv(self):
        self.seed('SPY', [row('2026-10-30')])
        selected = {'SPY': metadata('SPY')}
        class Net:
            def connect_yahoo(self): pass
            def quote(self, symbols): return {'quoteResponse': {'result': []}}
        fresh = frame([row('2026-11-02'), row('2026-11-03'), row('2026-11-04')])
        with patch.object(bu.Run, 'fetch_chart', return_value=fresh):
            result = daily.refresh(self.archive, self.cache, now=NOW, net=Net(), selected=selected, sleep=lambda _: None)
        self.assertEqual(result['charts'], 1)
        daily.stage(self.archive, self.cache, self.payload, now=NOW)
        ka.apply_operations(self.archive, self.payload, now=NOW)
        self.assertEqual([r[0] for r in ka.symbol_rows(self.archive, 'SPY')], ['2026-10-30', '2026-11-02', '2026-11-03', '2026-11-04'])
        before = Path(uc.bars_path(str(self.cache), 'SPY')).read_bytes()
        with patch.object(bu.Run, 'fetch_chart', side_effect=ValueError('no data from source')):
            failure = daily.refresh(self.archive, self.cache, now=NOW, net=Net(), selected=selected, sleep=lambda _: None)
        self.assertEqual(failure['failed'], 1)
        self.assertEqual(before, Path(uc.bars_path(str(self.cache), 'SPY')).read_bytes())

    def test_unfinished_tw_index_is_not_written_and_diagnostics_stay_out_of_main(self):
        self.seed('^TWII', [row('2026-11-03')])
        now = datetime(2026, 11, 4, 4, tzinfo=timezone.utc)
        selected = {'^TWII': metadata('^TWII')}
        class Net:
            def connect_yahoo(self): pass
            def quote(self, symbols): return {'quoteResponse': {'result': []}}
            def chart(self, symbol, range_):
                return {'chart': {'result': [{'meta': {'exchangeTimezoneName': 'Asia/Taipei'}, 'timestamp': [int(now.timestamp())], 'indicators': {'quote': [{'open': [100], 'high': [101], 'low': [99], 'close': [100], 'volume': [0]}]}}]}}
        result = daily.refresh(self.archive, self.cache, now=now, net=Net(), selected=selected, sleep=lambda _: None)
        self.assertEqual(result['appended'], 0)
        plan = daily.stage(self.archive, self.cache, self.payload, now=now)
        self.assertFalse(plan['symbols'])
        self.log.write_text('{"version":1,"quarantined":[],"events":[]}')
        before = self.log.read_bytes()
        original_path = integrity.LOG_PATH
        with daily.disposable_quality_log(self.cache):
            integrity.log_event('SPY', '2026-11-04', 'rejected', reason='synthetic-invalid-source')
        self.assertEqual(self.log.read_bytes(), before)
        self.assertEqual(integrity.LOG_PATH, original_path)
        self.assertTrue((self.cache / 'quality.json').exists())
        integrity.write_log(self.log, {'version': 1, 'events': [], 'quarantined': [{'symbol': 'SPY', 'date': '2026-11-03', 'reason': 'retained evidence'}]})
        before = self.log.read_bytes()
        with daily.disposable_quality_log(self.cache):
            self.assertEqual(integrity.quarantined_dates('SPY'), {'2026-11-03'})
        self.assertEqual(self.log.read_bytes(), before)

    def test_invalid_metadata_is_rejected_before_rows_or_readme_change(self):
        body = ka.csv_text([row('2026-01-02')])
        ka.atomic(self.payload / 'us/SPY.csv', body)
        ka.json_write(self.payload / 'operations.json', {'version': 1, 'mode': 'backfill', 'symbols': [{'symbol': 'SPY', 'file': 'us/SPY.csv', 'sha256': ka.digest(body.encode())}], 'instruments': {'SPY': {'kind': 'etf', 'private-field': 'synthetic'}}})
        before = (self.archive / 'README.md').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Invalid instrument metadata'):
            ka.apply_operations(self.archive, self.payload, now=NOW)
        self.assertFalse(list((self.archive / 'us').rglob('*.csv')))
        self.assertEqual(before, (self.archive / 'README.md').read_bytes())

    def test_global_file_deletion_and_existing_date_overwrite_are_still_rejected(self):
        self.seed('^N225', [row('2021-01-04')])
        base = self.checkpoint()
        (self.archive / 'global/^N225/2021.csv').unlink()
        (self.archive / 'index/global/%5EN225.json').unlink()
        ka.update_manifest(self.archive)
        with self.assertRaisesRegex(ValueError, 'deleted'): ka.verify(self.archive, base)

class ExpansionWorkflowTests(unittest.TestCase):
    def test_dispatch_only_and_publishes_only_archive_without_main_journal(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / '.github/workflows/kline-archive-five-years.yml').read_text()
        document = yaml.safe_load(workflow)
        self.assertIn('workflow_dispatch:', workflow)
        self.assertNotIn('schedule:', workflow)
        self.assertNotIn('kline_archive_git.py journal', workflow)
        self.assertNotIn('--force', workflow)
        self.assertEqual(document['permissions'], {'contents': 'read'})
        self.assertEqual(document['jobs']['publish']['permissions'], {'contents': 'write'})
        self.assertIn('--years 5 --calendar-years', workflow)
        daily_workflow = (root / '.github/workflows/universe-full.yml').read_text()
        self.assertIn("steps.archive_instrument_stage.outcome == 'success'", daily_workflow)
        self.assertIn("|| '.cache/kline-payload/'", daily_workflow)

if __name__ == '__main__': unittest.main()

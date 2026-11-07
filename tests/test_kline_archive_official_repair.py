"""Official parser/basis contracts and destructive-operation recovery on synthetic Git only."""
import copy
from datetime import datetime, timezone
from decimal import Decimal
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts'), str(ROOT/'tests')]
import kline_archive as ka
import repair_kline_archive as repair
import official_tw_quotes as official
import market_sessions as sessions
import scan_market_closures as scan
from test_kline_archive import _ArchiveCase, row, git, frame

DAY = '2021-04-06'
NEIGHBORS = ['2021-03-31', '2021-04-01', '2021-04-07', '2021-04-08']
TWSE_FIELDS = ['證券代號','證券名稱','成交股數','開盤價','最高價','最低價','收盤價']
TPEX_FIELDS = ['代號','名稱','收盤','開盤','最高','最低','成交股數']


def daily(exchange, day=DAY):
    if exchange == 'twse':
        fields, rows = TWSE_FIELDS, [['2330','台積電','1,234,567','600.00','610.00','599.00','608.00'],
            ['0001','停牌','0','--','--','--','--'], ['0002','無成交','0','10','10','10','10']]
    else:
        fields, rows = TPEX_FIELDS, [['6488','環球晶','608.00','600.00','610.00','599.00','1,234,567']]
    table = dict(fields=list(fields), data=rows)
    if exchange == 'tpex':
        y,m,d=day.split('-');table['date']=f'{int(y)-1911}/{m}/{d}'
    return dict(stat='OK' if exchange == 'twse' else 'ok', date=day.replace('-',''), tables=[table])


class OfficialParsingTest(unittest.TestCase):
    def test_dated_fields_commas_and_uniform_no_trade_skip(self):
        for exchange, symbol in [('twse','2330.TW'),('tpex','6488.TWO')]:
            quotes, skipped, closed = official.daily_quotes(daily(exchange), exchange, DAY)
            self.assertFalse(closed)
            self.assertEqual(quotes[symbol][1:], tuple(map(Decimal, ['600','610','599','608','1234567'])))
            if exchange == 'twse':
                self.assertEqual(set(skipped), {'0001.TW','0002.TW'})
            doc = daily(exchange)
            table = doc['tables'][0]
            table['fields'].reverse()
            table['data'] = [list(reversed(r)) for r in table['data']]
            self.assertEqual(official.daily_quotes(doc, exchange, DAY)[0], quotes)

    def test_roc_dates_index_ohlc_and_unknown_index_volume(self):
        doc = dict(stat='OK',date='20210401', fields=['日期','開盤指數','最高指數','最低指數','收盤指數'],
                   data=[['110/04/06','16,711.79','16,804.45','16,711.79','16,739.87']])
        values=official.index_quotes(doc)[DAY]
        self.assertEqual(values[4],Decimal('16739.87'))
        self.assertIsNone(values[5])
        self.assertEqual(official.roc_day('110/04/06'),DAY)
        for value in ['2021-04-06','110/02/30','not-a-date']:
            with self.assertRaises(ValueError):official.roc_day(value)

    def test_errors_are_never_market_closures(self):
        for doc in [dict(stat='rate-limited'),dict(stat=official.NO_DATA,date='20210407'),dict(stat='OK',date='20210407',tables=[]),
                    dict(stat='OK',date='20210406',tables=[])]:
            with self.assertRaises(ValueError):official.daily_quotes(doc,'twse',DAY)
        self.assertTrue(official.daily_quotes(dict(stat=official.NO_DATA),'twse',DAY)[2])
        duplicate=daily('twse');duplicate['tables'][0]['data'].append(duplicate['tables'][0]['data'][0])
        with self.assertRaises(ValueError):official.daily_quotes(duplicate,'twse',DAY)
        bad_date=daily('tpex');bad_date['tables'][0]['date']='110/04/07'
        with self.assertRaises(ValueError):official.daily_quotes(bad_date,'tpex',DAY)

    def test_official_proof_requires_both_dates_hosts_empty_rows_and_hashes(self):
        seed=json.loads((ROOT/'atlas/src/market-data/verified-market-closures.json').read_text())
        row=next(r for r in seed['markets']['tw']['closedSessions'] if r['date']=='2024-10-31')
        self.assertTrue(official.valid_closure_proof(row))
        for key,value in [('url','https://example.com/'),('quoteRows',1),('sha256','broken'),('stat','error'),('responseDate','20241030')]:
            changed=copy.deepcopy(row);changed['officialEvidence'][0][key]=value
            self.assertFalse(official.valid_closure_proof(changed))
        changed=copy.deepcopy(row);changed['date']='2024-10-30'
        self.assertFalse(official.valid_closure_proof(changed))

    def test_offline_missing_source_never_contacts_network(self):
        with tempfile.TemporaryDirectory() as directory, patch('requests.get') as get:
            with self.assertRaisesRegex(ValueError,'not cached'):official.OfficialClient(directory,True).get('twse',DAY)
            get.assert_not_called()

    def test_fixed_official_equivalent_is_recorded_and_html_never_means_closed(self):
        class Response:
            def __init__(self,status,body):self.status_code,self.body=status,body
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_content(self,size):yield self.body
        with tempfile.TemporaryDirectory() as folder, patch('requests.get',side_effect=[
                Response(307,b'<html>challenge</html>'), Response(200,json.dumps(daily('twse')).encode())]) as get, \
                patch.object(official.time,'sleep'):
            client=official.OfficialClient(folder)
            self.assertIn('2330.TW',client.get('twse',DAY)[0])
            self.assertEqual(get.call_count,2)
            record=client.records[f'twse-{DAY}']
            self.assertEqual(record['url'],official.url_candidates('twse',DAY)[1])
            self.assertEqual([r['httpStatus'] for r in record['httpAttempts']],[307,200])
            cached=official.OfficialClient(folder,True)
            cached.get('twse',DAY)
            self.assertEqual(cached.records[f'twse-{DAY}']['url'],record['url'])
        with tempfile.TemporaryDirectory() as folder, patch('requests.get',return_value=Response(429,b'')):
            with self.assertRaisesRegex(ValueError,'HTTP 429'):
                official.OfficialClient(folder).get('twse',DAY)
            self.assertFalse(list(Path(folder).glob('*.json')))


class BasisTest(unittest.TestCase):
    def witnesses(self, ratios):
        return [dict(date=d,archiveClose=str(Decimal(str(r))*100),officialClose='100')
                for d,r in zip(NEIGHBORS,ratios)]

    def test_raw_within_half_percent_and_stable_nonunit_factor(self):
        self.assertEqual(repair.price_factor(self.witnesses(['.995','1','1.005','1'])),(Decimal(1),None))
        factor,reason=repair.price_factor(self.witnesses(['.25','.25','.2501','.2499']))
        self.assertEqual(factor,Decimal('.25'));self.assertIsNone(reason)

    def test_unstable_or_incomplete_is_skipped(self):
        self.assertEqual(repair.price_factor(self.witnesses([1,1,.25,.25]))[1],'unstable-price-basis')
        self.assertEqual(repair.price_factor(self.witnesses([1,1,1]))[1],'insufficient-comparison-data')


class RepairCase(_ArchiveCase):
    def placeholder_fixture(self):
        for symbol,volume,flat in [('2330.TW',0,True),('6488.TWO',0,True),('0050.TW',1,True),('1101.TW',0,False),('AAPL',0,True)]:
            target=ka.normalize(['2026-10-23',101,101 if flat else 102,101,101,volume])
            self.seed(symbol,[row('2026-10-22'),target,row('2026-10-27')])
        self.checkpoint()

    def inventory(self):
        return {p.relative_to(self.archive).as_posix():p.read_bytes() for p in self.archive.rglob('*')
                if p.is_file() and '.git' not in p.relative_to(self.archive).parts}

    def test_only_missing_rows_are_added_with_official_provenance(self):
        self.seed('2330.TW',[row('2021-04-01'),row('2021-04-07')]);self.checkpoint()
        operation=dict(symbol='2330.TW',date=DAY,row=list(row(DAY)),factor='1',sourceUrl=official.urls(DAY)['twse'])
        original={r[0]:r for r in ka.symbol_rows(self.archive,'2330.TW')}
        repair.apply_backfill(self.archive,[operation])
        ka.verify(self.archive,base='HEAD')
        current={r[0]:r for r in ka.symbol_rows(self.archive,'2330.TW')}
        self.assertTrue(all(current[d]==r for d,r in original.items()))
        log=json.loads((self.archive/ka.LOG).read_text())
        self.assertEqual(log['events'][-1]['sourceUrl'],operation['sourceUrl'])
        self.assertEqual(log['events'][-1]['officialBasis']['factor'],'1')
        before=self.inventory()
        with self.assertRaisesRegex(ValueError,'now exists'):repair.apply_backfill(self.archive,[operation])
        self.assertEqual(before,self.inventory())

    def test_plan_uses_exact_two_sessions_each_side_and_scales_volume_inversely(self):
        for symbol,factor in [('2330.TW',Decimal('.25')),('6488.TWO',Decimal(1))]:
            self.seed(symbol,[row(day,100*float(factor),1000) for day in NEIGHBORS])
        observations={d:{s:(d,Decimal(100),Decimal(101),Decimal(99),Decimal(100),Decimal(1000))
                         for s in ['2330.TW','6488.TWO']} for d in [DAY,*NEIGHBORS]}
        class Source:
            records = {f'{exchange}-{DAY}': {'url': official.urls(DAY)[exchange]} for exchange in ('twse','tpex')}
            def neighbors(self,day):return NEIGHBORS
            def observations(self,day):return observations[day],{}
        report,operations=repair.plan_backfill(self.archive,Source(),[DAY])
        adjusted=next(r for r in operations if r['symbol']=='2330.TW')
        self.assertEqual(adjusted['row'][1:5],['25.0000','25.2500','24.7500','25.0000'])
        self.assertEqual(adjusted['row'][5],'4000')
        self.assertEqual(report['days'][0]['adjustedSymbols'],1)
        observations[NEIGHBORS[0]].pop('2330.TW')
        report,operations=repair.plan_backfill(self.archive,Source(),[DAY])
        self.assertEqual(len(operations),1)
        self.assertEqual(report['days'][0]['skipped'][0]['reason'],'insufficient-comparison-data')

    def test_count_guard_and_nonmatching_rows_are_preserved(self):
        self.placeholder_fixture();before=self.inventory()
        plan=ka.placeholder_plan(self.archive,expected=2214)
        self.assertFalse(plan['guardPassed'])
        with self.assertRaises(ValueError):repair.removal_backup(self.archive,plan,self.root/'backup.json')
        self.assertEqual(before,self.inventory())
        plan=ka.placeholder_plan(self.archive,expected=2)
        self.assertEqual({r['symbol'] for r in plan['rows']},{'2330.TW','6488.TWO'})
        self.assertEqual(set(plan['preserved']),{'0050.TW','1101.TW'})

    def test_backup_precedes_promotion_and_restores_every_byte_and_history_check(self):
        self.placeholder_fixture()
        year=self.archive/'tw/2330.TW/2026.csv'
        year.write_bytes(year.read_bytes().replace(b'\n', b'\r\n'))
        ka.update_manifest(self.archive);self.checkpoint()
        before=self.inventory();backup=self.root/repair.BACKUP_NAME
        original=ka.replace_repair_images
        def checked(*args,**kwargs):
            self.assertTrue(backup.is_file())
            saved=json.loads(backup.read_text())
            self.assertEqual(len(saved['rows']),2)
            self.assertTrue(all(r['beforeSha256']==ka.digest(before[r['path']]) for r in saved['rows']))
            return original(*args,**kwargs)
        with patch.object(ka,'replace_repair_images',side_effect=checked):
            repair.removal_backup(self.archive,ka.placeholder_plan(self.archive,2),backup)
        ka.verify(self.archive,base='HEAD')
        self.assertFalse(any(r[0]=='2026-10-23' for r in ka.symbol_rows(self.archive,'2330.TW')))
        self.assertTrue(any(r[0]=='2026-10-23' for r in ka.symbol_rows(self.archive,'AAPL')))
        self.checkpoint()  # Synthetic publication, so the immutable Git parent is the undo witness.
        repair.restore_backup(self.archive,backup,apply=True)
        self.assertEqual(before,self.inventory())
        ka.verify(self.archive,base='HEAD')
        self.checkpoint()
        ka.verify(self.archive,base='HEAD^')  # Same contract the production PR verifier uses.
        clone=self.root/'shallow-restored'
        subprocess.run(['git','clone','--quiet','--depth','3',self.archive.as_uri(),str(clone)],check=True)
        ka.verify(clone,base='HEAD^')


    def test_restore_refuses_concurrent_updates_and_corrupt_backup(self):
        self.placeholder_fixture();backup=self.root/repair.BACKUP_NAME
        repair.removal_backup(self.archive,ka.placeholder_plan(self.archive,2),backup)
        with self.assertRaisesRegex(ValueError,'already exists'):
            repair.removal_backup(self.archive,ka.placeholder_plan(self.archive,2),backup)
        before=self.inventory();path=self.archive/'tw/2330.TW/2026.csv';path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'advanced'):repair.restore_backup(self.archive,backup,True)
        self.assertNotEqual(before,self.inventory())
        data=json.loads(backup.read_text());data['files'][0]['path']='../escape';backup.write_text(json.dumps(data))
        with self.assertRaises(ValueError):repair.restore_backup(self.archive,backup,True)

    def test_restore_refuses_unrelated_same_size_updates_before_any_write(self):
        self.placeholder_fixture();backup=self.root/repair.BACKUP_NAME
        repair.removal_backup(self.archive,ka.placeholder_plan(self.archive,2),backup)
        self.checkpoint()
        path=self.archive/'us/AAPL/2026.csv'
        path.write_bytes(path.read_bytes().replace(b'100.0000',b'100.1000'))
        ka.update_manifest(self.archive)
        before=self.inventory()
        with self.assertRaisesRegex(ValueError,'advanced'):
            repair.restore_backup(self.archive,backup,True)
        self.assertEqual(before,self.inventory())

    def test_successful_dry_run_only_writes_the_external_report(self):
        self.placeholder_fixture();before=self.inventory();plan=ka.placeholder_plan(self.archive,2)
        with patch.object(ka,'placeholder_plan',return_value=plan), patch.object(repair,'removal_backup') as write:
            report=repair.run(self.archive,'remove-placeholders',self.root/'report.json',self.root/'sources')
        write.assert_not_called()
        self.assertEqual(report['status'],'dry-run')
        self.assertEqual(before,self.inventory())

    def test_specific_removal_audit_cannot_authorize_a_us_deletion(self):
        values=[row('2026-10-22'),ka.normalize(['2026-10-23',100,100,100,100,0]),row('2026-10-27')]
        self.seed('AAPL',values);self.checkpoint()
        path=self.archive/'us/AAPL/2026.csv';before=path.read_bytes()
        after=ka.csv_text([r for r in values if r[0]!='2026-10-23']).encode()
        path.write_bytes(after)
        record=dict(symbol='AAPL',date='2026-10-23',status='removed',reason=ka.VERIFIED_PLACEHOLDER_REASON,
                    old={'sha256':ka.digest(before)},new={'path':'us/AAPL/2026.csv','sha256':ka.digest(after),'rowsRemoved':1})
        record.update(id='forged-removal',loggedAt='2026-11-07T00:00:00Z')
        ka.add_events(self.archive,[record]);ka.update_manifest(self.archive)
        with self.assertRaisesRegex(ValueError,'dates deleted'):ka.verify(self.archive,base='HEAD')

    def test_dry_run_never_writes_archive_and_reports_guard_stop(self):
        self.placeholder_fixture();before=self.inventory();report=self.root/'report.json'
        with self.assertRaisesRegex(ValueError,'more than 1%'):
            repair.run(self.archive,'remove-placeholders',report,self.root/'sources')
        self.assertEqual(before,self.inventory())
        self.assertEqual(json.loads(report.read_text())['status'],'stopped')
        self.assertFalse((self.root/repair.BACKUP_NAME).exists())


class OfficialClosureProjectionTest(unittest.TestCase):
    def test_four_verified_days_never_reappear_as_gaps(self):
        primary=dict(symbol='^TWII',firstDate='2022-02-01',lastDate='2026-10-27',dates={'2022-02-01','2026-10-27'})
        stats=pd.DataFrame(dict(observed=[0,0],flatZero=[0,0],unknownVolume=[0,0],positiveVolume=[0,0]),
                           index=pd.to_datetime(['2022-02-01','2026-10-27']))
        missing={};closed=sessions.classify_closures(stats,primary,market='tw',missing=missing)
        self.assertTrue(set(official.AUDIT_DAYS)<=closed.keys())
        self.assertTrue(all(d not in missing for d in official.AUDIT_DAYS))
        us=sessions.classify_closures(stats,market='us',known=sessions.verified_closures('tw'))
        self.assertNotIn('2024-10-31',us)
        result=scan.data_gaps('tw',stats,[],primary,closed,{},datetime(2026,11,7,tzinfo=timezone.utc))
        self.assertTrue(all(r['date'] not in official.AUDIT_DAYS for r in result['sessions']))

    def test_positive_trading_evidence_still_vetoes_a_reviewed_closure(self):
        stats=pd.DataFrame(dict(observed=[100],flatZero=[0],unknownVolume=[0],positiveVolume=[100]),index=pd.to_datetime(['2024-10-31']))
        self.assertNotIn('2024-10-31',sessions.classify_closures(stats,market='tw'))

    def test_analysis_is_identical_before_and_after_removing_1023(self):
        original=frame([row('2026-10-22'),ka.normalize(['2026-10-23',101,101,101,101,0]),row('2026-10-27')])
        removed=original.drop(pd.Timestamp('2026-10-23'))
        a,closed=sessions.market_prices({'2330.TW':original},market='tw')
        b,after=sessions.market_prices({'2330.TW':removed},market='tw')
        self.assertIn('2026-10-23',closed);self.assertIn('2026-10-23',after)
        pd.testing.assert_frame_equal(a['2330.TW'],b['2330.TW'])


class RepairWorkflowTest(unittest.TestCase):
    def test_default_dry_run_and_separate_write_permission_job(self):
        doc=yaml.load((ROOT/'.github/workflows/kline-archive-official-repair.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(doc['on']['workflow_dispatch']['inputs']['apply']['default'],'false')
        self.assertEqual(doc['permissions']['contents'],'read')
        self.assertIn('inputs.apply',doc['jobs']['publish']['if'])
        self.assertEqual(doc['jobs']['publish']['permissions']['contents'],'write')
        self.assertEqual(doc['concurrency']['group'],'kline-archive-publish')
        steps=doc['jobs']['prepare']['steps']
        self.assertTrue(any('upload-artifact@' in s.get('uses','') for s in steps))
        self.assertEqual(doc['jobs']['publish']['needs'],'prepare')
        publication='\n'.join(s.get('run','') for s in doc['jobs']['publish']['steps'])
        self.assertNotIn('--force',publication)
        self.assertNotIn('pull --rebase',publication)


if __name__=='__main__':unittest.main()

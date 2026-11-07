"""Offline closure policy regression; real archive fixtures are never modified."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
import market_sessions as ms
import universe_cache as uc
import build_universe_full as bu
import build_market_warnings as mw
import kline_archive as ka
import scan_market_closures as scan
import scan_market_data_gaps as gaps
import refresh_archive_instruments as instruments
import build_breadth as breadth
import build_breakouts as breakouts

NOW=datetime(2026,11,6,tzinfo=timezone.utc)


def prices():
    dates=pd.bdate_range('2026-09-01','2026-10-27')
    return pd.DataFrame(dict(Open=100.,High=101.,Low=99.,Close=100.,Volume=1000.),index=dates)


def ghost(frame, day='2026-10-23', close=102.):
    frame=frame.copy()
    frame.loc[pd.Timestamp(day)]=[close]*4+[0]
    return frame.sort_index()


class ClosureRulesTest(unittest.TestCase):
    def test_calendar_dependency_is_installed_before_the_scan_and_archive_contracts(self):
        workflow=(ROOT/'.github/workflows/universe-full.yml').read_text()
        before=workflow.split('- name: Build market closure evidence from read-only archive',1)[0]
        self.assertIn("'exchange-calendars>=4.5,<5.0'",before)
        review=(ROOT/'.github/workflows/kline-archive-review.yml').read_text()
        before=review.split('- name: Offline append, correction, retention, resume and Git retry contracts',1)[0]
        self.assertIn("'exchange-calendars>=4.5,<5.0'",before)

    def test_calendar_session_with_only_official_tpex_bar_is_missing_data_not_closed(self):
        self.assertIn('2021-04-06',ms.exchange_sessions('tw','2021-04-01','2021-04-07'))
        primary=ms.primary_reference(pd.DataFrame(dict(Open=1.,High=1.,Low=1.,Close=1.),
            index=pd.to_datetime(['2021-04-01','2021-04-07'])),'^TWII','fixture')
        official={'2021-04-06':[dict(symbol='^TWOII',source='TPEx official cash-index OHLC')]}
        empty=pd.DataFrame(columns=['observed','flatZero','unknownVolume','positiveVolume'])
        missing={}
        closed=ms.classify_closures(empty,primary,known=['2021-04-06'],market='tw',official=official,missing=missing)
        self.assertNotIn('2021-04-06',closed)
        self.assertEqual(missing['2021-04-06']['reason'],'missing-session-data')
        self.assertEqual(missing['2021-04-06']['officialSources'],official['2021-04-06'])
        self.assertTrue(missing['2021-04-06']['calendarSession'])
        # Even one ordinary-stock observation on a calendar session is a gap,
        # never holiday evidence; the existing flat-zero emergency rule is separate.
        stats=pd.DataFrame(dict(observed=[1],flatZero=[0],unknownVolume=[1],positiveVolume=[0]),
                           index=pd.to_datetime(['2021-04-06']))
        self.assertNotIn('2021-04-06',ms.classify_closures(stats,primary,market='tw'))

    def test_available_index_or_official_bar_vetoes_zero_stock_placeholders_on_a_session(self):
        stats=pd.DataFrame(dict(observed=[100],flatZero=[100],unknownVolume=[0],positiveVolume=[0]),
                           index=pd.to_datetime(['2021-04-06']))
        full=ms.primary_reference(pd.DataFrame(dict(Open=1.,High=1.,Low=1.,Close=1.),
            index=pd.to_datetime(['2021-04-01','2021-04-06','2021-04-07'])),'^TWII','fixture')
        gap={**full,'dates':full['dates']-{'2021-04-06'}}
        self.assertNotIn('2021-04-06',ms.classify_closures(stats,full,market='tw'))
        self.assertNotIn('2021-04-06',ms.classify_closures(stats,gap,market='tw',
                         official={'2021-04-06':[dict(symbol='^TWOII',source='TPEx official cash-index OHLC')]}))
        self.assertIn('2021-04-06',ms.classify_closures(stats,gap,market='tw')) # emergency exception needs both proofs

    def test_calendar_failure_does_not_create_a_new_weekday_closure(self):
        stats=pd.DataFrame(dict(observed=[100],flatZero=[100],unknownVolume=[0],positiveVolume=[0]),
                           index=pd.to_datetime(['2021-04-06']))
        with patch.object(ms,'exchange_sessions',side_effect=RuntimeError('calendar unavailable')):
            self.assertNotIn('2021-04-06',ms.classify_closures(stats,market='tw'))

    def test_95_percent_flat_zero_is_closed_even_if_price_changed_from_previous_close(self):
        frames={str(i):ghost(prices()) for i in range(100)}
        for i in range(95,100):
            frames[str(i)].attrs[ms.UNKNOWN_VOLUME_DATES]=('2026-10-23',)
        evidence=ms.classify_closures(ms.cohort_statistics(frames))
        self.assertIn('flat-zero-cohort',evidence['2026-10-23']['reasons'])
        self.assertEqual(evidence['2026-10-23']['flatZeroShare'],.95)
        self.assertEqual(evidence['2026-10-23']['flatZeroSymbols'],95)
        raw=copy.deepcopy(frames['0'].attrs)
        projected,closed=ms.market_prices(frames)
        self.assertNotIn(pd.Timestamp('2026-10-23'),projected['0'].index)
        self.assertIn(pd.Timestamp('2026-10-23'),frames['0'].index)
        self.assertEqual(frames['0'].attrs,raw)

    def test_a_few_halts_or_unknown_volume_do_not_close_the_market(self):
        frames={str(i):prices() for i in range(100)}
        for i in range(5): frames[str(i)]=ghost(frames[str(i)])
        self.assertNotIn('2026-10-23',ms.non_trading_dates(frames))
        frames={str(i):ghost(prices()) for i in range(100)}
        for i in range(6): frames[str(i)].attrs[ms.UNKNOWN_VOLUME_DATES]=('2026-10-23',)
        self.assertNotIn('2026-10-23',ms.non_trading_dates(frames))

    def test_primary_absence_requires_readable_bounded_coverage_not_a_failed_download(self):
        bars=prices().drop(pd.to_datetime(['2026-10-23','2026-10-26']))
        primary=ms.primary_reference(bars,'^TWII','offline primary',NOW)
        stock=ghost(prices()).drop(pd.Timestamp('2026-10-26'))
        dates=ms.non_trading_dates({'A':stock,'B':stock},primary)
        self.assertIn('2026-10-23',dates)
        self.assertIn('2026-10-26',dates)
        self.assertNotIn('2026-10-28',dates)
        self.assertNotIn('2026-10-23',ms.non_trading_dates({'A':prices(),'B':prices()},None))

    def test_index_gap_with_trading_vetoes_old_closure_even_at_five_percent_boundary(self):
        stats=pd.DataFrame(dict(observed=[100,2000,100],flatZero=[95,1950,100],unknownVolume=[0,0,0],
                                 positiveVolume=[5,50,0]),index=pd.to_datetime(['2026-11-03','2026-11-04','2026-10-23']))
        primary=ms.primary_reference(pd.DataFrame(dict(Open=[1,1],High=[1,1],Low=[1,1],Close=[1,1]),
                 index=pd.to_datetime(['2026-10-22','2026-11-05'])),'^TWII','fixture',NOW)
        reviews={}
        closed=ms.classify_closures(stats,primary,known={'2026-11-04':{}},reviews=reviews)
        self.assertNotIn('2026-11-03',closed)
        self.assertNotIn('2026-11-04',closed)
        self.assertEqual(reviews['2026-11-04']['reason'],'primary-index-gap-with-trading')
        self.assertIn('2026-10-23',closed)
        self.assertIn('2026-10-26',closed)

    def test_sparse_snapshot_is_not_a_95_percent_valid_listing(self):
        listing={str(i):{} for i in range(100)}
        bars={str(i):dict(date='2026-10-23',open=102,high=102,low=102,close=102,volume=0) for i in range(5)}
        self.assertFalse(ms.classify_closures(ms.snapshot_statistics(bars,listing)))

    def test_prior_trading_evidence_survives_a_later_snapshot_without_replacing_counts_with_zero(self):
        prior={'2026-11-04':dict(date='2026-11-04',reason='primary-index-gap-with-trading',observedSymbols=100,positiveVolumeSymbols=100,positiveVolumeShare=1.)}
        primary=ms.primary_reference(pd.DataFrame(dict(Open=[1,1],High=[1,1],Low=[1,1],Close=[1,1]),
            index=pd.to_datetime(['2026-11-03','2026-11-05'])),'^TWII','fixture',NOW)
        stats=pd.DataFrame(dict(observed=[100],flatZero=[0],unknownVolume=[0],positiveVolume=[100]),index=pd.to_datetime(['2026-11-05']))
        reviews=copy.deepcopy(prior)
        self.assertNotIn('2026-11-04',ms.classify_closures(stats,primary,known=['2026-11-04'],trading=prior,reviews=reviews))
        self.assertEqual(reviews,prior)

    def test_projection_keeps_legacy_weekend_counts_and_close_only_loader_eligibility(self):
        frame=ghost(prices(),day='2026-10-25')
        normal=breakouts.build_market({'A':frame},'fixture','tw')
        projected,_=ms.market_prices({'A':frame})
        after=breakouts.build_market(projected,'fixture','tw')
        self.assertEqual(normal['weekendBars'],1)
        self.assertEqual(after['weekendBars'],1)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'A.csv'
            path.write_text('Date,Open,High,Low,Close,Volume\n2026-10-22,,,,100,1000\n')
            self.assertIsNone(breadth.load_daily_bars(path)) # existing OHLC validation still rejects this
            path.write_text('Date,Open,Close,Volume\n2026-10-22,,100,1000\n')
            loaded=breadth.load_daily_bars(path)
            self.assertEqual(len(loaded),1)
            self.assertTrue(pd.isna(loaded.Open.iloc[0]))
            self.assertFalse(ms.non_trading_dates({'A':loaded,'B':loaded}))

    def test_daily_snapshot_cannot_append_the_market_closed_bar(self):
        with tempfile.TemporaryDirectory() as folder:
            args=SimpleNamespace(budget=300,cache_dir=folder,markets=['TW'],archive_dir=None)
            job=bu.Run(args,now=NOW,sleep=lambda _:None,log=lambda _:None)
            job.listing={'TW':{f'{1000+i}.TW':{} for i in range(100)}}
            old=prices().loc[:'2026-10-22']
            for symbol in job.listing['TW']:
                uc.write_bars(uc.bars_path(folder,symbol),old)
                uc.record_frame(job.state,symbol,old,'2026-10-22',full=True,verified=True)
            job.snapshots={'TW':{'date':'2026-10-23','bars':{
                s:dict(date='2026-10-23',open=102,high=102,low=102,close=102,volume=0) for s in job.listing['TW']}}}
            before={p:p.read_bytes() for p in (Path(folder)/'bars').glob('*.csv')}
            job.plan()
            self.assertIn('2026-10-23',job.closed_dates('TW'))
            self.assertEqual(before,{p:p.read_bytes() for p in before})
            with patch.object(job,'fetch_raw_chart_once',return_value=ghost(old)):
                fetched=job.fetch_chart('1000.TW','2y')
            self.assertNotIn(pd.Timestamp('2026-10-23'),fetched.index)

    def test_warning_calendar_without_fake_day_keeps_leaders_and_counts_observable(self):
        days=mw.completed_calendar('tw',NOW,'2025-01-02')
        days=days[~days.strftime('%Y-%m-%d').isin(['2026-10-23','2026-10-26'])]
        close=np.linspace(100,150,len(days))
        base=pd.DataFrame(dict(Open=close,High=close+1,Low=close-1,Close=close,Volume=1e7),index=days)
        normal={f'{1000+i}.TW':base.copy() for i in range(100)}
        fake={s:ghost(f,close=float(f.loc[:'2026-10-22'].Close.iloc[-1])) for s,f in normal.items()}
        proof={'2026-10-23':dict(date='2026-10-23',reasons=['primary-index-missing','flat-zero-cohort'],
               observedSymbols=100,eligibleSymbols=100,flatZeroSymbols=100,positiveVolumeSymbols=0)}
        spec=lambda frames:dict(frames=frames,listed=100,listingAsOf='2026-11-05',closedSessions=proof)
        def attempts(bars,calendar):
            return [dict(first=True,higher=True,qualified=True,date=calendar[-25],outcome={'status':'complete','wins':{'hold5':True}})]
        with patch.object(mw,'swing_attempts',side_effect=attempts):
            expected=mw.build_market(spec(normal),{'^TWII':base},'tw',NOW,{**mw.PARAMS,'minEvaluated':1})
            actual=mw.build_market(spec(fake),{'^TWII':base},'tw',NOW,{**mw.PARAMS,'minEvaluated':1})
        self.assertEqual(actual['series'],expected['series'])
        for point in actual['series']:
            if point['date'] >= '2026-10-27':
                self.assertIsNotNone(point['leader_success'])
                self.assertIsNotNone(point['breakout_count_per_1000'])
        self.assertNotIn('2026-10-23',{p['date'] for p in actual['series']})


class ArchiveClosureTest(unittest.TestCase):
    def make_gap_archive(self, root, official=True):
        ka.initialize(root)
        for symbol in ['^TWII',*[f'{1000+i}.TW' for i in range(50)]]:
            ka.put_symbol(root,symbol,[ka.normalize([day,100,101,99,100,1000]) for day in ['2021-04-01','2021-04-07']])
        if official:
            ka.put_symbol(root,'^TWOII',[ka.normalize(['2021-04-06',207.79,209.81,207.79,209.70,None])])
            ka.json_write(root/ka.INSTRUMENTS_STATE,dict(version=1,symbols={
                '^TWOII':dict(kind='index',source='TPEx official cash-index OHLC; synthetic test fixture')}))
        ka.update_manifest(root)

    def test_read_only_gap_scan_reports_missing_index_and_stocks_and_retains_official_row(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'archive';self.make_gap_archive(root)
            before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            report=scan.scan_archive(root,datetime(2021,4,8,tzinfo=timezone.utc))
            tw=report['markets']['tw']
            self.assertNotIn('2021-04-06',{row['date'] for row in tw['closedSessions']})
            missing=next(row for row in tw['missingSessions'] if row['date']=='2021-04-06')
            self.assertEqual(missing['reason'],'missing-session-data')
            row=next(row for row in scan.gap_report(report)['markets']['tw']['sessions'] if row['date']=='2021-04-06')
            self.assertEqual((row['observedSymbols'],row['eligibleSymbols'],row['missingSymbols']),(0,50,50))
            self.assertEqual(row['missingShare'],1.)
            self.assertEqual(row['reasons'],['primary-index-missing','majority-stock-rows-missing'])
            self.assertEqual(row['officialSources'][0]['ohlc'],dict(open=207.79,high=209.81,low=207.79,close=209.70))
            self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before})
            with self.assertRaisesRegex(ValueError,'Read-only'):
                gaps.main(['--archive-dir',str(root),'--out',str(root/'bad.json')])

    def test_stage_retains_april_6_and_revokes_a_legacy_index_only_closure(self):
        with tempfile.TemporaryDirectory() as folder:
            root,cache,payload=Path(folder)/'archive',Path(folder)/'cache',Path(folder)/'payload'
            self.make_gap_archive(root,official=False)
            false_row=dict(date='2021-04-06',reasons=['primary-index-missing'],observedSymbols=0,eligibleSymbols=0,
                           flatZeroSymbols=0,positiveVolumeSymbols=0)
            scan.write_report(cache/ms.POLICY_FILE,dict(version=1,threshold=.95,markets={'tw':{'closedSessions':[false_row]}}))
            self.assertNotIn('2021-04-06',ms.read_policy(cache/ms.POLICY_FILE)['tw'])
            frame=pd.DataFrame([[207.79,209.81,207.79,209.70,float('nan')]],columns=uc.PRICE_COLUMNS,
                               index=pd.to_datetime(['2021-04-06']))
            uc.write_bars(uc.bars_path(str(cache),'^TWOII'),frame)
            state=uc.load_state(str(cache));uc.record_frame(state,'^TWOII',frame,'2026-11-05',full=True,verified=True);uc.save_state(str(cache),state)
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            plan=ka.stage_cache(cache,root,payload,now=NOW)
            self.assertNotIn('2021-04-06',{row['date'] for row in plan['closedSessions']})
            item=next(item for item in plan['symbols'] if item['symbol']=='^TWOII')
            self.assertEqual(ka.read_rows(payload/item['file'])[0][0],'2021-04-06')
            self.assertEqual(before,{p:p.read_bytes() for p in before})

    def make_archive(self,root):
        ka.initialize(root)
        for symbol in ['2330.TW','6488.TWO',*[f'{1000+i}.TW' for i in range(48)]]:
            ka.put_symbol(root,symbol,[ka.normalize(['2026-10-22',100,101,99,100,1000]),
                                       ka.normalize(['2026-10-23',100,100,100,100,0]),
                                       ka.normalize(['2026-10-27',101,102,100,101,1000])])
        ka.put_symbol(root,'^TWII',[ka.normalize(['2026-10-22',100,101,99,100,0]),
                                   ka.normalize(['2026-10-27',101,102,100,101,0])])
        ka.update_manifest(root)

    def test_scanner_reports_rows_and_reasons_without_changing_a_byte(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'archive'
            self.make_archive(root)
            before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            report=scan.scan_archive(root,NOW)
            rows=[r for r in report['markets']['tw']['closedSessions'] if r['affectedRows']]
            self.assertEqual(len(rows),1)
            self.assertEqual((rows[0]['date'],rows[0]['affectedRows']),('2026-10-23',50))
            self.assertIn('primary-index-missing',rows[0]['reasons'])
            self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before})
            with self.assertRaisesRegex(ValueError,'Read-only'):
                scan.main(['--archive-dir',str(root),'--out',str(root/'bad-report.json')])
            index=root/'index/tw/%5ETWII.json'
            damaged=json.loads(index.read_text())
            damaged['years']['2026']['sha256']='0'*64
            index.write_text(json.dumps(damaged))
            self.assertIsNone(ms.archive_primary(root,'tw',NOW))

    def test_bootstrap_and_stage_reject_known_closures_but_keep_permanent_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root,cache,payload=Path(folder)/'archive',Path(folder)/'cache',Path(folder)/'payload'
            self.make_archive(root)
            before=(root/'tw/2330.TW/2026.csv').read_bytes()
            scan.write_report(cache/ms.POLICY_FILE,scan.scan_archive(root,NOW))
            scan.write_report(Path(folder)/ms.POLICY_FILE,scan.scan_archive(root,NOW))
            ka.bootstrap(root,cache,now=NOW)
            self.assertNotIn('2026-10-23',uc.read_bars(uc.bars_path(str(cache),'2330.TW')).index.strftime('%Y-%m-%d'))
            # Reproduce an old rolling cache with a changed legacy placeholder.
            rows=ka.symbol_rows(root,'2330.TW')+[ka.normalize(['2026-11-02',102,103,101,102,1000])]
            (cache/'bars/2330.TW.csv').write_text(ka.csv_text(rows))
            state=uc.load_state(str(cache))
            uc.record_frame(state,'2330.TW',uc.read_bars(str(cache/'bars/2330.TW.csv')),'2026-11-05',full=True,verified=True)
            uc.save_state(str(cache),state)
            plan=ka.stage_cache(cache,root,payload,now=NOW)
            staged=[r for item in plan['symbols'] for r in ka.read_rows(payload/item['file'])]
            self.assertNotIn('2026-10-23',{r[0] for r in staged})
            self.assertEqual((root/'tw/2330.TW/2026.csv').read_bytes(),before)
            supplement=Path(folder)/'instruments-cache'
            state=uc.load_state(str(supplement))
            instruments.seed_selected(root,supplement,{'2330.TW':{'kind':'etf'}},state,NOW)
            self.assertNotIn('2026-10-23',uc.read_bars(uc.bars_path(str(supplement),'2330.TW')).index.strftime('%Y-%m-%d'))

    def test_runtime_scan_keeps_a_newer_observed_closure_outside_archive_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'archive'
            self.make_archive(root)
            prior=scan.scan_archive(root,NOW)
            row=copy.deepcopy(prior['markets']['tw']['closedSessions'][0])
            row['date']='2026-11-04'
            prior['markets']['tw']['closedSessions']=[row]
            policy,out=Path(folder)/'cache-policy.json',Path(folder)/'report.json'
            scan.write_report(policy,prior)
            scan.main(['--archive-dir',str(root),'--out',str(out),'--cache-policy',str(policy)])
            saved=json.loads(policy.read_text())
            dates={r['date']:r for r in saved['markets']['tw']['closedSessions']}
            self.assertIn('2026-10-23',dates)
            self.assertIn('published-closure',dates['2026-11-04']['reasons'])
            self.assertEqual(json.loads(out.read_text()),saved)

    def test_traded_november_4_is_staged_when_primary_index_has_a_hole(self):
        with tempfile.TemporaryDirectory() as folder:
            root,cache,payload=Path(folder)/'archive',Path(folder)/'cache',Path(folder)/'payload'
            ka.initialize(root)
            ka.put_symbol(root,'^TWII',[ka.normalize([d,100,101,99,100,0]) for d in ['2026-11-03','2026-11-05']])
            ka.put_symbol(root,'9999.TW',[ka.normalize([d,100,101,99,100,1000]) for d in ['2026-11-03','2026-11-04','2026-11-05']])
            ka.update_manifest(root)
            frame=pd.DataFrame(dict(Open=100.,High=101.,Low=99.,Close=100.,Volume=1000.),
                               index=pd.to_datetime(['2026-11-03','2026-11-04','2026-11-05']))
            state=uc.load_state(str(cache))
            for i in range(100):
                symbol=f'{1000+i}.TW'
                uc.write_bars(uc.bars_path(str(cache),symbol),frame)
                uc.record_frame(state,symbol,frame,'2026-11-05',full=True,verified=True)
            uc.save_state(str(cache),state)
            plan=ka.stage_cache(cache,root,payload,now=NOW)
            self.assertEqual(len(plan['symbols']),100)
            self.assertTrue(all('2026-11-04' in {r[0] for r in ka.read_rows(payload/x['file'])} for x in plan['symbols']))
            self.assertNotIn('2026-11-04',{x['date'] for x in plan['closedSessions']})
            self.assertEqual(plan['indexGapsWithTrading'][0]['reason'],'primary-index-gap-with-trading')
            report=scan.scan_archive(root,NOW)
            self.assertNotIn('2026-11-04',{r['date'] for r in report['markets']['tw']['closedSessions']})
            self.assertEqual(report['markets']['tw']['indexGapsWithTrading'][0]['reason'],'primary-index-gap-with-trading')
            # A previously inferred gap cannot survive a full scan with trading.
            empty=pd.DataFrame(columns=['observed','flatZero','unknownVolume','positiveVolume'])
            primary=ms.archive_primary(root,'tw',NOW)
            report['markets']['tw']['closedSessions']=list(ms.classify_closures(empty,primary).values())
            report['markets']['tw']['indexGapsWithTrading']=[]
            scan.write_report(cache/ms.POLICY_FILE,report)
            scan.main(['--archive-dir',str(root),'--out',str(Path(folder)/'report.json'),'--cache-policy',str(cache/ms.POLICY_FILE)])
            self.assertNotIn('2026-11-04',ms.read_policy(cache/ms.POLICY_FILE)['tw'])
            job=bu.Run(SimpleNamespace(budget=300,cache_dir=str(cache),markets=['TW'],archive_dir=str(root)),
                       now=NOW,sleep=lambda _:None,log=lambda _:None)
            job.listing={'TW':{f'{1000+i}.TW':{} for i in range(100)}}
            job.snapshots={'TW':{'date':'2026-11-04','bars':{symbol:dict(date='2026-11-04',open=100,high=101,low=99,close=100,volume=1000) for symbol in job.listing['TW']}}}
            self.assertNotIn('2026-11-04',job.closed_dates('TW'))

    def test_a_real_adjustment_can_preserve_an_existing_closed_row_without_inserting_one(self):
        with tempfile.TemporaryDirectory() as folder:
            root,cache,payload=Path(folder)/'archive',Path(folder)/'cache',Path(folder)/'payload'
            self.make_archive(root)
            scan.write_report(cache/ms.POLICY_FILE,scan.scan_archive(root,NOW))
            old=ka.symbol_rows(root,'2330.TW')[1]
            fresh=pd.DataFrame([[50,50.5,49.5,50,1000],[50.5,51,50,50.5,1000]],
                                columns=uc.PRICE_COLUMNS,index=pd.to_datetime(['2026-10-22','2026-10-27']))
            uc.write_bars(uc.bars_path(str(cache),'2330.TW'),fresh)
            state=uc.load_state(str(cache))
            uc.record_frame(state,'2330.TW',fresh,'2026-11-05',full=True,verified=True)
            uc.save_state(str(cache),state)
            ka.stage_adjustment(cache,'2330.TW',fresh,dict(date='2026-10-22',oldClose=100,newClose=50,worst=.5),NOW)
            plan=ka.stage_cache(cache,root,payload,now=NOW)
            self.assertTrue(plan['symbols'][0]['correction'])
            ka.apply_operations(root,payload,now=NOW)
            current={r[0]:r for r in ka.symbol_rows(root,'2330.TW')}
            self.assertEqual(current['2026-10-23'],old)
            self.assertEqual(float(current['2026-10-22'][4]),50)


if __name__ == '__main__':
    unittest.main()

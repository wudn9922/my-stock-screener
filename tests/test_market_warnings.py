import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_market_warnings as mw
import market_warnings_log as log
from index_daily import IndexDailyBars
from swing_breakouts import SwingHigh, SwingHighTrace
from swing_cohort import diagnostics, included, swing_attempts


def frame(close, volume=None):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame(dict(Open=close, High=close + 1, Low=close - 1, Close=close,
                             Volume=np.asarray(volume if volume is not None else [1e7] * len(close), dtype=float)),
                        index=pd.bdate_range('2022-01-03', periods=len(close)))


def trace_for(bars, hits, previous=90, level=100):
    series = lambda x: pd.Series(x, index=bars.index)
    return SwingHighTrace(series([i in hits for i in range(len(bars))]), series([level]*len(bars)),
                          series([True]*len(bars)), series([20]*len(bars)), series([25]*len(bars)),
                          series([1]*len(bars)), (SwingHigh(10, 15, previous, 2), SwingHigh(20, 25, level, 2)))


class VolumeTest(unittest.TestCase):
    def test_separate_denominators_equal_volume_invalid_and_both_windows(self):
        bars = frame([100]*50 + [100,101,100,102,101,103,102,104,103,105], [100]*50 + [100,110,110,100,90,80,0,120,100,130])
        values = mw.volume_series(bars, bars.index).iloc[-1]
        self.assertAlmostEqual(values.up_vol_up_ratio_5, .5)
        self.assertAlmostEqual(values.down_vol_down_ratio_5, 1)
        self.assertAlmostEqual(values.up_vol_up_ratio_60, .5)
        self.assertAlmostEqual(values.down_vol_down_ratio_60, 2/3)
        # The invalid day and its next day do not bridge to the last valid volume.
        bars.loc[bars.index[-3], 'High'] = 0
        self.assertTrue(pd.isna(mw.volume_series(bars, bars.index).iloc[-1].down_vol_down_ratio_5))

    def test_zero_denominator_and_missing_session_are_not_zero_ratios(self):
        bars = frame(range(100, 170))
        values = mw.volume_series(bars, bars.index).iloc[-1]
        self.assertEqual(values.up_vol_up_ratio_60, 0)
        self.assertTrue(pd.isna(values.down_vol_down_ratio_60))
        missing = bars.drop(bars.index[-2])
        result = mw.volume_series(missing, bars.index)
        self.assertEqual(result.iloc[-1].up_vol_up_ratio_5, 0)

    def test_median_excludes_today_and_all_future_information(self):
        bars = frame(100 + np.arange(1000) % 2, 100 + (np.arange(1000)*7)%13)
        result = mw.volume_series(bars, bars.index)
        t = 800
        self.assertAlmostEqual(result.median756.iloc[t], result.avg60.iloc[max(0,t-756):t].median())
        changed = bars.copy()
        changed.iloc[t+1:, changed.columns.get_loc('Volume')] = 1e20
        pd.testing.assert_frame_equal(result.iloc[:t+1], mw.volume_series(changed, bars.index).iloc[:t+1])
        self.assertTrue(result.median756.iloc[:559].isna().all())


class SwingCountingTest(unittest.TestCase):
    def test_three_failed_intraday_attempts_are_one_first_and_two_retries(self):
        bars = frame([99]*40)
        attempts = swing_attempts(bars, list(bars.index), trace_for(bars, [30,31,32]))
        stats = diagnostics(attempts)
        self.assertEqual((stats['first_attempts'], stats['retry_failures'], stats['retry_attempts']), (1,2,2))
        self.assertEqual(stats['close_fail_share'], 1)
        self.assertFalse(any(included(a) for a in attempts))

    def test_lower_high_and_unknown_first_high_never_enter_success_denominator(self):
        bars = frame([102]*40)
        lower = swing_attempts(bars, list(bars.index), trace_for(bars, [30], previous=110))
        self.assertFalse(included(lower[0]))
        self.assertEqual(diagnostics(lower)['lower_high_share'], 1)
        trace = trace_for(bars, [30])
        trace = SwingHighTrace(trace.fresh, trace.levels, trace.known, trace.pivot_indices,
                               trace.confirmed_indices, trace.atr, trace.highs[1:])
        self.assertEqual(diagnostics(swing_attempts(bars, list(bars.index), trace))['unknown_previous_high'], 1)

    def test_qualified_close_needs_five_complete_future_sessions(self):
        bars = frame([102]*40)
        result = swing_attempts(bars, list(bars.index), trace_for(bars, [36]))[0]
        self.assertTrue(included(result))
        self.assertEqual(result['outcome']['status'], 'pending')
        result = swing_attempts(bars.drop(bars.index[33]), list(bars.index), trace_for(bars.drop(bars.index[33]), [30]))[0]
        self.assertEqual(result['outcome']['status'], 'missing')


class LeadersTest(unittest.TestCase):
    def test_known_zero_volume_counts_in_liquidity_but_unknown_does_not(self):
        bars = frame(np.linspace(100,200,180))
        bars.iloc[159,bars.columns.get_loc('Volume')] = 0
        close,volume,active = mw.stock_matrices({'X':bars},bars.index)
        self.assertFalse(active.iloc[159,0])
        self.assertEqual(volume.iloc[159,0],0)
        self.assertTrue(mw.leader_membership(close,volume,'tw').iloc[160,0])
        bars.attrs[mw.UNKNOWN_VOLUME_DATES] = (bars.index[159].isoformat(),)
        close,volume,_ = mw.stock_matrices({'X':bars},bars.index)
        self.assertTrue(pd.isna(volume.iloc[159,0]))
        self.assertFalse(mw.leader_membership(close,volume,'tw').iloc[160,0])
    def test_todays_extreme_price_cannot_change_todays_leader_selection(self):
        dates = pd.bdate_range('2022-01-03', periods=180)
        close = pd.DataFrame({'strong': np.linspace(100,200,180), 'weak': np.linspace(100,110,180)}, index=dates)
        volume = close*0 + 1e7
        membership = mw.leader_membership(close, volume, 'tw')
        self.assertTrue(membership.iloc[160]['strong'])
        close.iloc[160, 1] = 1e8
        pd.testing.assert_series_equal(membership.iloc[160], mw.leader_membership(close, volume, 'tw').iloc[160])
        self.assertFalse(membership.iloc[:127].any().any())

    def test_rates_only_include_event_days_t_minus_five_or_earlier(self):
        dates = pd.bdate_range('2022-01-03', periods=30)
        leaders = pd.DataFrame({'X': True}, index=dates)
        params = {**mw.PARAMS, 'minBreakouts': 1, 'smoothDays': 1}
        event = lambda i: ('X', dict(first=True,higher=True,qualified=True,date=dates[i],outcome={'status':'complete','wins':{'hold5':True}}))
        result = mw.matured_rates([event(24), event(25)], leaders, dates, 3, params)
        self.assertEqual(result.iloc[29].market_success_n, 1)
        self.assertEqual(result.iloc[28].market_success_n, 0)
        self.assertTrue(pd.isna(result.iloc[28].market_success))

    def test_observation_uses_its_own_t_minus_one_leader_list(self):
        dates = pd.bdate_range('2022-01-03', periods=25)
        leaders = pd.DataFrame({'X':False},index=dates)
        leaders.loc[dates[15],'X'] = True
        attempt = dict(first=True,higher=True,qualified=True,date=dates[10],
                       outcome={'status':'complete','wins':{'hold5':True}})
        result = mw.matured_rates([('X',attempt)],leaders,dates,3,
                                  {**mw.PARAMS,'minBreakouts':1,'smoothDays':1})
        self.assertEqual(result.iloc[15].leader_success_n,1)
        self.assertEqual(result.iloc[15].leader_success,1)
        self.assertEqual(result.iloc[16].leader_success_n,0)

    def test_thresholds_only_use_the_past_and_flags_handle_unknown_and_us(self):
        self.assertEqual(mw.warning_flags(dict(vol60_weak=True,leader_lag=True,breadth_divergence=True), 'tw'), {'warning_level':3,'warning_complete':True})
        self.assertEqual(mw.warning_flags(dict(vol60_weak=True,leader_lag=False,breadth_divergence=None), 'us'), {'warning_level':0,'warning_complete':False})

    def test_count_unique_stock_denominator_and_quantile_are_causal(self):
        dates = pd.bdate_range('2022-01-03', periods=30)
        valid = pd.DataFrame({'A':True,'B':True}, index=dates)
        good = dict(first=True,higher=True,qualified=True,date=dates[25])
        events = [('A', good), ('A',{**good,'first':False,'date':dates[26]}),
                  ('B',{**good,'higher':False,'date':dates[27]}), ('B',{**good,'qualified':False,'date':dates[28]})]
        self.assertEqual(mw.breakout_counts(events, valid, 2).iloc[-1], 500)
        valid.iloc[-1,1] = False
        self.assertTrue(pd.isna(mw.breakout_counts(events, valid, 2).iloc[-1]))
        values = pd.Series(np.arange(1000)%37, index=pd.bdate_range('2020-01-01',periods=1000), dtype=float)
        original = mw.past_quantile(values)
        values.iloc[801:] = 1e10
        pd.testing.assert_series_equal(original.iloc[:801], mw.past_quantile(values).iloc[:801])
        self.assertAlmostEqual(original.iloc[800], values.iloc[44:800].quantile(.3))


class SafetyTest(unittest.TestCase):
    def test_completed_sessions_exclude_taiwan_forming_bar_and_us_early_close(self):
        now = datetime(2026,11,5,4,tzinfo=timezone.utc) # TW noon
        self.assertEqual(mw.completed_calendar('tw', now, '2026-11-02')[-1], pd.Timestamp('2026-11-04'))
        now = datetime(2026,11,27,18,1,tzinfo=timezone.utc) # 13:01 New York early close
        self.assertEqual(mw.completed_calendar('us', now, '2026-11-20')[-1], pd.Timestamp('2026-11-27'))

    def test_cached_rows_older_than_the_exchange_calendar_are_clamped(self):
        now = datetime(2026, 11, 5, 13, tzinfo=timezone.utc)
        for market in ('tw', 'us'):
            days = mw.completed_calendar(market, now, '2000-05-01')
            self.assertGreaterEqual(days[0], pd.Timestamp('2001-01-01'))
            self.assertLess(days[-1], pd.Timestamp('2026-11-06'))

    def test_log_append_is_idempotent_and_never_rewrites_first_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'log.csv'
            payload = {'generatedAt':'2026-11-05T00:00:00Z','markets':{'tw':{'asOf':'2026-11-04','latest':{'warning_level':1}}}}
            log.append_observations(path, payload)
            old = path.read_bytes()
            payload['markets']['tw']['latest']['warning_level'] = 3
            log.append_observations(path, payload)
            self.assertEqual(old, path.read_bytes())
            payload['markets']['tw']['asOf'] = '2026-11-05'
            log.append_observations(path, payload)
            self.assertTrue(path.read_bytes().startswith(old))
            self.assertEqual(len(log.read_rows(path.read_bytes())), 2)
            with self.assertRaisesRegex(ValueError, 'append-only'):
                log.require_append(path.read_bytes(), old)

    def test_concurrent_logs_union_once_and_keep_first_observation(self):
        row = {field:'' for field in log.FIELDS}
        a = {**row,'asOf':'2026-11-04','market':'tw'}
        b = {**row,'asOf':'2026-11-05','market':'tw'}
        merged = log.merge(log.encode([a], True), log.encode([a,b], True))
        self.assertEqual(log.read_rows(merged), [a,b])
        self.assertEqual(merged, log.merge(merged, merged))
        self.assertEqual(merged, log.merge(merged, log.encode([{**a,'warning_level':'1'}],True)))

    def test_insufficient_coverage_or_bad_listing_preserves_output_and_log(self):
        with tempfile.TemporaryDirectory() as folder:
            out, forward = Path(folder)/'out.json', Path(folder)/'log.csv'
            out.write_bytes(b'old report')
            forward.write_bytes(b'old log')
            args = ['--out',str(out),'--log',str(forward)]
            with patch.object(mw, 'build_payload', side_effect=ValueError('覆蓋不足')):
                self.assertEqual(mw.main(args, inputs_loader=lambda *_:{}, indices_loader=lambda:{}), 1)
            self.assertEqual(out.read_bytes(), b'old report')
            bars = frame(range(100,280))
            spec = {'frames':{'A':bars,'B':bars},'listed':20,'listingAsOf':'2026-11-04'}
            self.assertEqual(mw.main(args, inputs_loader=lambda *_:{'tw':spec},
                                     indices_loader=lambda:{'^TWII':bars}), 1)
            self.assertEqual(out.read_bytes(), b'old report')
            self.assertEqual(forward.read_bytes(), b'old log')
            self.assertEqual(mw.main(args, inputs_loader=lambda *_: (_ for _ in ()).throw(ValueError('名單過期'))),1)
            self.assertEqual(out.read_bytes(), b'old report')

    def test_zigzag_is_retrospective_and_labels_depth(self):
        close = frame([100,105,110,100,98,109]).Close
        result = mw.correction_segments(close)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['start'], str(close.index[2].date()))
        self.assertEqual(result[0]['end'], str(close.index[4].date()))
        self.assertLessEqual(result[0]['drawdown'], -.07)

    def test_rolled_out_history_retains_rates_and_recalculates_its_matching_light(self):
        index = frame(range(100, 280))
        bars = index.tail(40)
        spec = {'frames': {'A':bars, 'B':bars}, 'listed':2, 'listingAsOf':'2022-09-01'}
        now = datetime(2022,10,1,tzinfo=timezone.utc)
        day = bars.index[-3].strftime('%Y-%m-%d')
        prior = {'series':[{'date':day, 'breakout_count_per_1000':10,
                           'leader_success':.2, 'market_success':.4,
                           'leader_success_n':20, 'market_success_n':50}]}
        result = mw.build_market(spec, {'^TWII':index}, 'tw', now,
                                 {**mw.PARAMS,'minEvaluated':1}, prior)
        row = next(r for r in result['series'] if r['date']==day)
        self.assertEqual((row['leader_success'],row['market_success']),(.2,.4))
        self.assertTrue(row['leader_lag'])
        self.assertEqual(row['breakout_count_per_1000'],10)


class DownloadIsolationTest(unittest.TestCase):
    def previous(self):
        value = json.loads((Path(__file__).resolve().parents[1] / 'atlas/tests/fixtures/market-warnings.sample.json').read_text())
        value['params'] = {**mw.PARAMS, 'swingCountingModel': mw.COUNTING_MODEL}
        return value

    def test_fetch_wrapper_keeps_seven_year_period_and_each_index_error(self):
        result = IndexDailyBars()
        result.errors['^IXIC'] = '^IXIC offline'
        with patch.object(mw,'read_index_daily',return_value=result) as read:
            self.assertIs(mw.fetch_indices(now=datetime(2026,11,5,tzinfo=timezone.utc)),result)
        self.assertEqual(set(read.call_args.args[0]),{'^TWII','^GSPC','^IXIC','^SOX','SOXX'})
        self.assertEqual(read.call_args.kwargs['period'],'7y')
        self.assertEqual(read.call_args.kwargs['min_bars'],60)

    def test_real_tw_calculation_completes_when_no_us_index_is_available(self):
        bars = frame(range(100,280))
        spec = {'frames':{'A':bars,'B':bars},'listed':2,'listingAsOf':'2022-09-01'}
        with patch.dict(mw.PARAMS,{'minEvaluated':1}):
            payload = mw.build_payload({'tw':spec},{'^TWII':bars},datetime(2022,10,1,tzinfo=timezone.utc))
        self.assertEqual(payload['markets']['tw']['update']['status'],'updated')
        self.assertEqual(payload['markets']['tw']['evaluated'],2)
        self.assertEqual(payload['markets']['tw']['latest']['date'],payload['markets']['tw']['asOf'])
        self.assertTrue(payload['markets']['tw']['indices']['^TWII']['series'])
        self.assertEqual(payload['markets']['us']['update']['status'],'unavailable')

    def test_only_us_download_failure_updates_tw_retains_us_and_never_relogs_us(self):
        previous = self.previous()
        indices = IndexDailyBars()
        indices['^TWII'] = frame(range(100,280))
        indices.errors['^IXIC'] = '^IXIC Yahoo 與封存都沒有完整資料'
        now = datetime(2026,10,15,9,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            out, forward = Path(folder)/'report.json', Path(folder)/'log.csv'
            out.write_text(json.dumps(previous))
            old_row = {field:'' for field in log.FIELDS}
            old_row.update(asOf='2026-10-12',market='tw')
            forward.write_bytes(log.encode([old_row],True))
            original_bytes = forward.read_bytes()
            original_us = copy.deepcopy(previous['markets']['us'])
            with patch.object(mw, 'build_market', return_value=copy.deepcopy(previous['markets']['tw'])) as build:
                self.assertEqual(mw.main(['--out',str(out),'--log',str(forward)],
                    inputs_loader=lambda *_:{'tw':{}}, indices_loader=lambda:indices, now=now),0)
                self.assertEqual(build.call_count,1)
            payload = json.loads(out.read_text())
            self.assertEqual(payload['markets']['tw']['update']['status'],'updated')
            self.assertEqual(payload['markets']['us']['update']['status'],'retained')
            self.assertIn('^IXIC',payload['markets']['us']['update']['reason'])
            self.assertEqual(payload['markets']['us']['update']['generatedAt'],previous['generatedAt'])
            retained = copy.deepcopy(payload['markets']['us'])
            retained.pop('update')
            self.assertEqual(retained,original_us)
            self.assertTrue(forward.read_bytes().startswith(original_bytes))
            self.assertEqual([r['market'] for r in log.read_rows(forward.read_bytes())],['tw','tw'])

    def test_first_publication_without_old_us_keeps_tw_and_marks_us_unavailable(self):
        prior = self.previous()
        with patch.object(mw,'build_market',return_value=copy.deepcopy(prior['markets']['tw'])):
            result = mw.build_payload({'tw':{}},{'^TWII':frame(range(100,280))},datetime(2026,10,15,tzinfo=timezone.utc))
        self.assertEqual(result['markets']['us']['update']['status'],'unavailable')
        self.assertEqual(set(result['markets']['us']),{'update'})

    def test_both_markets_missing_preserves_entire_report_and_log(self):
        with tempfile.TemporaryDirectory() as folder:
            out, forward = Path(folder)/'report.json', Path(folder)/'log.csv'
            out.write_text(json.dumps(self.previous()))
            forward.write_bytes(b'unchanged log')
            before = out.read_bytes()
            self.assertEqual(mw.main(['--out',str(out),'--log',str(forward)],
                inputs_loader=lambda *_:{},indices_loader=lambda:{},now=datetime(2026,10,15,tzinfo=timezone.utc)),1)
            self.assertEqual(out.read_bytes(),before)
            self.assertEqual(forward.read_bytes(),b'unchanged log')

    def test_volume_proxy_failure_only_retains_its_market(self):
        prior = self.previous()
        indices = {s:frame(range(100,280)) for s in ['^TWII','^GSPC','^IXIC','^SOX']}
        with patch.object(mw,'build_market',return_value=copy.deepcopy(prior['markets']['tw'])):
            result = mw.build_payload({'tw':{}},indices,datetime(2026,10,15,tzinfo=timezone.utc),prior)
        self.assertEqual(result['markets']['us']['update']['status'],'retained')
        self.assertIn('SOXX',result['markets']['us']['update']['reason'])


if __name__ == '__main__':
    unittest.main()

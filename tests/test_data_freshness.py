"""Offline, exchange-calendar-shaped fixtures. No production workflows or APIs."""
import copy
from datetime import datetime, timezone, timedelta
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('data_freshness', ROOT / 'scripts/check_data_freshness.py')
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


def clock(value='2026-11-06T01:30:00+00:00'):
    return datetime.fromisoformat(value)


def calendar():
    result = {'version': 1, 'validFrom': '2026-10-01', 'validTo': '2027-11-30', 'markets': {}}
    for market, suffix in [('TW', 'T05:30:00+00:00'), ('US', 'T21:00:00+00:00')]:
        result['markets'][market] = {'sessions': {day: {'close': int(clock(day+suffix).timestamp())}
            for day in ['2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06','2026-11-09']}}
    return result


def sources(day='2026-11-05'):
    return {name: {'markets': {market if name == 'universe' else market.lower():
            {'lastDate' if name == 'archive' else 'asOf': day}
            for market in markets}}
            for name, (_, _, markets) in health.DATASETS.items()}


class SourceDateTest(unittest.TestCase):
    def test_current_data_and_one_session_lag_are_healthy(self):
        issues, checked = health.evaluate(calendar(), sources(), clock())
        self.assertEqual(issues, {})
        self.assertEqual(len(checked), 11)  # two calendars plus nine market datasets
        self.assertEqual(health.evaluate(calendar(), sources('2026-11-04'), clock())[0], {})

    def test_three_completed_session_lag_alerts_each_market_source(self):
        issues, _ = health.evaluate(calendar(), sources('2026-11-03'), clock('2026-11-06T08:00:00+00:00'))
        self.assertEqual(len(issues), 7)
        self.assertNotIn('breakouts/TW', issues)
        self.assertNotIn('breakouts/US', issues)
        self.assertIn('3 個交易日', issues['archive/TW'])
        self.assertIn('2 個交易日', issues['archive/US'])

    def test_weekly_breakouts_allow_five_sessions_but_alert_after_six(self):
        cal = calendar()
        for market, suffix in [('TW', 'T05:30:00+00:00'), ('US', 'T21:00:00+00:00')]:
            sessions = cal['markets'][market]['sessions']
            day = datetime(2026, 11, 10)
            while day.date() <= datetime(2026, 11, 20).date():
                if day.weekday() < 5:
                    key = day.date().isoformat()
                    sessions[key] = {'close': int(clock(key + suffix).timestamp())}
                day += timedelta(days=1)
        now = clock('2026-11-20T23:00:00+00:00')
        data = sources('2026-11-20')
        data['breakouts']['markets']['tw']['asOf'] = '2026-11-13'
        data['breakouts']['markets']['us']['asOf'] = '2026-11-13'
        issues, _ = health.evaluate(cal, data, now)
        self.assertNotIn('breakouts/TW', issues)
        self.assertNotIn('breakouts/US', issues)
        data['breakouts']['markets']['tw']['asOf'] = '2026-11-09'
        data['breakouts']['markets']['us']['asOf'] = '2026-11-09'
        issues, _ = health.evaluate(cal, data, now)
        self.assertEqual(set(issues), {'breakouts/TW', 'breakouts/US'})
        self.assertIn('每週更新一次', issues['breakouts/TW'])

    def test_warning_unavailable_always_alerts_but_current_retained_does_not(self):
        data = sources()
        data['warnings']['markets']['us']['update'] = {'status': 'unavailable', 'reason': 'must not copy arbitrary source text'}
        issues, _ = health.evaluate(calendar(), data, clock())
        self.assertEqual(set(issues), {'warnings/US'})
        self.assertIn('unavailable', issues['warnings/US'])
        self.assertNotIn('arbitrary', str(issues))
        # Retained data that already covers the latest closed session is current.
        data = sources()
        data['warnings']['markets']['us']['update'] = {'status': 'retained', 'reason': 'must not copy arbitrary source text'}
        self.assertEqual(health.evaluate(calendar(), data, clock())[0], {})
        # Retained data one session behind alerts, stricter than the normal one-session grace.
        data = sources('2026-11-04')
        data['warnings']['markets']['us']['update'] = {'status': 'retained', 'reason': 'must not copy arbitrary source text'}
        issues, _ = health.evaluate(calendar(), data, clock())
        self.assertEqual(set(issues), {'warnings/US'})
        self.assertIn('retained', issues['warnings/US'])
        self.assertNotIn('arbitrary', str(issues))
        data['warnings']['markets']['us'].pop('asOf')
        issues, _ = health.evaluate(calendar(), data, clock())
        self.assertIn('缺少有效資料日', issues['warnings/US'])

    def test_weekends_holidays_and_new_york_early_close_use_published_timestamps(self):
        cal = calendar()
        cal['markets']['US']['sessions'] = {
            '2026-11-25': {'close': int(clock('2026-11-25T21:00:00+00:00').timestamp())},
            '2026-11-27': {'close': int(clock('2026-11-27T18:00:00+00:00').timestamp())},
            '2026-11-30': {'close': int(clock('2026-11-30T21:00:00+00:00').timestamp())},
        }
        self.assertEqual(str(health.completed_sessions(cal, 'US', clock('2026-11-27T17:59:59+00:00'))[-1]), '2026-11-25')
        self.assertEqual(str(health.completed_sessions(cal, 'US', clock('2026-11-27T18:00:00+00:00'))[-1]), '2026-11-27')
        self.assertEqual(str(health.completed_sessions(cal, 'US', clock('2026-11-29T12:00:00+00:00'))[-1]), '2026-11-27')
        self.assertEqual(str(health.completed_sessions(cal, 'US', clock('2026-11-30T19:00:00+00:00'))[-1]), '2026-11-27')

    def test_intraday_today_is_not_the_expected_closed_session(self):
        self.assertEqual(str(health.completed_sessions(calendar(), 'TW', clock())[-1]), '2026-11-05')
        self.assertEqual(str(health.completed_sessions(calendar(), 'TW', clock('2026-11-06T05:30:00+00:00'))[-1]), '2026-11-06')

    def test_missing_sources_dates_and_expired_calendar_fail_as_observable_issues(self):
        data = sources(); data['archive'] = None
        issues, _ = health.evaluate(calendar(), data, clock())
        self.assertEqual(set(issues), {'archive/TW','archive/US'})
        data = sources(); data['universe']['markets']['TW']['asOf'] = 'PRIVATE-MALFORMED-DATE'
        issues, _ = health.evaluate(calendar(), data, clock())
        self.assertEqual(set(issues), {'universe/TW'})
        self.assertNotIn('PRIVATE', str(issues))
        cal = calendar(); cal['validTo'] = '2026-11-04'
        issues, checked = health.evaluate(cal, sources(), clock())
        self.assertEqual(set(issues), {'calendar/TW','calendar/US'})
        self.assertEqual(checked, set(issues))



class TWIIVolumeWatchdogTest(unittest.TestCase):
    def test_latest_zero_gets_one_trading_day_grace_then_notifies_and_recovers(self):
        zero = ("Date,Open,High,Low,Close,Volume\n"
                "2026-11-04,1.0000,2.0000,1.0000,2.0000,4161800\n"
                "2026-11-05,1.0000,2.0000,1.0000,2.0000,0\n")
        issues, checked = health.twii_volume_issue(calendar(), zero, clock())
        self.assertEqual(issues, {})  # 11/05 only just closed
        self.assertEqual(checked, {"volume/^TWII"})
        # 11/06 has completed; even though its volume is positive, 11/05
        # still being zero proves the previous day was not recovered.
        next_day = zero + "2026-11-06,1.0000,2.0000,1.0000,2.0000,6000000\n"
        issues, checked = health.twii_volume_issue(
            calendar(), next_day, clock("2026-11-06T08:00:00+00:00"))
        self.assertEqual(set(issues), {"volume/^TWII"})
        self.assertIn("2026-11-05", issues["volume/^TWII"])
        state, events = health.plan(None, issues, checked, clock("2026-11-06T08:00:00+00:00"))
        self.assertEqual([e["kind"] for e in events], ["failure"])
        positive = next_day.replace("2.0000,0\n", "2.0000,4239400\n")
        issues, checked = health.twii_volume_issue(
            calendar(), positive, clock("2026-11-06T08:00:00+00:00"))
        self.assertEqual(issues, {})
        self.assertEqual(checked, {"volume/^TWII"})
        _, events = health.plan(state, issues, checked, clock("2026-11-06T08:00:00+00:00"))
        self.assertEqual([e["kind"] for e in events], ["recovered"])

    def test_weekend_does_not_count_as_extra_session(self):
        first = ("Date,Open,High,Low,Close,Volume\n"
                 "2026-11-05,1,2,1,2,4000000\n"
                 "2026-11-06,1,2,1,2,0\n")
        friday = clock("2026-11-06T08:00:00+00:00")
        self.assertEqual(health.twii_volume_issue(calendar(), first, friday)[0], {})
        self.assertEqual(health.twii_volume_issue(
            calendar(), first, clock("2026-11-08T08:00:00+00:00"))[0], {})
        monday = first + "2026-11-09,1,2,1,2,0\n"
        issues, _ = health.twii_volume_issue(
            calendar(), monday, clock("2026-11-09T08:00:00+00:00"))
        self.assertIn("2026-11-06", issues["volume/^TWII"])

    def test_older_unrepaired_zero_does_not_silently_recover_after_ten_days(self):
        older = ("Date,Open,High,Low,Close,Volume\n"
                 "2026-11-05,1,2,1,2,0\n"
                 "2026-11-06,1,2,1,2,6000000\n"
                 "2026-11-09,1,2,1,2,7000000\n")
        issues, checked = health.twii_volume_issue(
            calendar(), older, clock("2026-11-09T08:00:00+00:00"))
        self.assertIn("2026-11-05", issues["volume/^TWII"])
        self.assertEqual(checked, {"volume/^TWII"})

    def test_old_session_or_unavailable_archive_never_signals_false_recovery(self):
        prior = ("Date,Open,High,Low,Close,Volume\n"
                 "2026-11-04,1.0000,2.0000,1.0000,2.0000,0\n")
        for data in (prior, None, "<html>invalid</html>"):
            self.assertEqual(health.twii_volume_issue(calendar(), data, clock()), ({}, set()))
        self.assertEqual(health.twii_volume_issue(
            calendar(), prior, clock("2026-11-06T08:00:00+00:00")), ({}, set()))


class DedupeRecoveryTest(unittest.TestCase):
    def test_same_problem_only_once_per_taipei_day_then_notifies_next_day(self):
        issues={'archive/TW':'封存台股落後'}
        state, events = health.plan(None, issues, set(issues), clock())
        self.assertEqual(len(events), 1)
        same, events = health.plan(state, issues, set(issues), clock('2026-11-06T14:00:00+00:00'))
        self.assertEqual(events, [])
        _, events = health.plan(same, issues, set(issues), clock('2026-11-06T17:00:00+00:00'))
        self.assertEqual(len(events), 1)  # next Taipei day, not next UTC day

    def test_recovery_notifies_once_and_keeps_other_unchecked_problems(self):
        old, _ = health.plan(None, {'archive/TW':'台股落後','archive/US':'美股落後'}, {'archive/TW','archive/US'}, clock())
        state, events = health.plan(old, {}, {'archive/TW'}, clock())
        self.assertEqual([e['kind'] for e in events], ['recovered'])
        self.assertIn('已恢復', events[0]['summary'])
        self.assertTrue(state['problems']['archive/US']['active'])
        self.assertFalse(state['problems']['archive/TW']['active'])
        self.assertEqual(health.plan(state, {}, {'archive/TW'}, clock())[1], [])

    def test_calendar_failure_does_not_falsely_recover_a_stale_source(self):
        old, _ = health.plan(None, {'archive/TW':'台股落後'}, {'archive/TW'}, clock())
        issues, checked = health.evaluate(None, sources(), clock())
        state, events = health.plan(old, issues, checked, clock())
        self.assertTrue(state['problems']['archive/TW']['active'])
        self.assertFalse(any(e['kind'] == 'recovered' for e in events))

    def test_plan_does_not_commit_before_delivery_and_failed_delivery_can_retry(self):
        previous={'version':1,'problems':{}}
        before=copy.deepcopy(previous)
        proposed, first=health.plan(previous, {'warnings/US':'未更新'}, {'warnings/US'}, clock())
        self.assertEqual(previous,before)
        self.assertNotEqual(proposed,previous)
        self.assertEqual(health.plan(previous, {'warnings/US':'未更新'}, {'warnings/US'}, clock())[1],first)

    def test_damaged_cache_does_not_block_check_or_report_false_recovery(self):
        for problems in (None, ['wrong shape'], {'archive/TW': 'wrong shape'}):
            state, events = health.plan({'version': 1, 'problems': problems},
                                       {'archive/TW': '台股落後'}, {'archive/TW'}, clock())
            self.assertEqual([event['kind'] for event in events], ['failure'])
            self.assertTrue(state['problems']['archive/TW']['active'])

    def test_daily_report_keeps_success_running_missing_and_saturday_rules(self):
        now=clock('2026-11-08T17:23:00+00:00') # Taipei Monday, checks Sunday
        self.assertFalse(health.daily_report_issue([{'conclusion':'success'}],now)[0])
        self.assertIn('仍在執行',health.daily_report_issue([{'status':'in_progress'}],now)[0]['daily-report'])
        self.assertIn('完全沒有執行',health.daily_report_issue([],now)[0]['daily-report'])
        self.assertEqual(health.daily_report_issue([],clock('2026-11-07T17:23:00+00:00')),({},set()))

    def test_cli_prepares_without_persisting_until_explicit_delivery_commit(self):
        data = sources('2026-11-02')
        by_path = {path: data[name] for name, (_, path, _) in health.DATASETS.items()}
        by_path['docs/report/market-calendar.json'] = calendar()
        fetched = []
        def fetch(repo, ref, path, token):
            self.assertEqual(repo, 'example/repository')
            self.assertEqual(ref, 'kline-archive' if path == 'manifest.json' else 'generated')
            fetched.append(path)
            return by_path[path]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = ['--state-dir', str(root), '--now', '2026-11-06T01:30:00Z']
            env = {'GITHUB_REPOSITORY': 'example/repository', 'GH_TOKEN': 'PRIVATE-TEST-TOKEN',
                   'GENERATED_BRANCH_MODE': '1', 'GITHUB_OUTPUT': str(root / 'output')}
            with patch.dict(os.environ, env), patch.object(health, 'github_json', side_effect=fetch), \
                 patch.object(health, 'github_text', return_value=None), \
                 patch.object(health, 'github_daily_runs', return_value=[{'conclusion': 'success'}]):
                self.assertEqual(health.main(args), 0)
                first = health.read_json(root / 'events.json')
                self.assertEqual(len(first), 7)
                self.assertFalse((root / 'state.json').exists())
                self.assertEqual(health.main(args), 0)
                self.assertEqual(health.read_json(root / 'events.json'), first)
                self.assertEqual(health.main(args + ['--commit']), 0)
                self.assertTrue((root / 'state.json').exists())
                self.assertEqual(health.main(args), 0)
                self.assertEqual(health.read_json(root / 'events.json'), [])
            self.assertEqual(set(fetched), set(by_path))
            for path in root.iterdir():
                self.assertNotIn('PRIVATE-TEST-TOKEN', path.read_text())


if __name__ == '__main__':
    unittest.main()

"""Offline cancellation replay and actual notifier transport tests. No real POST."""
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import sys
import unittest
from urllib.error import URLError

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.github/actions/notify-failure'))
import notify
from run_policy import decide
sys.path.insert(0, str(ROOT / 'scripts'))
from replay_cancelled_notifications import replay

RUN = dict(id=12345, workflow_id=1, head_branch='main', event='workflow_run',
           conclusion='cancelled', created_at='2026-11-06T01:00:00Z')
NEXT = dict(RUN, id=12346, created_at='2026-11-06T01:01:00Z', conclusion='success')


class FakeOpener:
    def __init__(self, run=None, jobs=None, newer=None, fail=False, job_pages=None, run_pages=None):
        self.run = run or RUN
        self.jobs = [] if jobs is None else jobs
        self.newer = [NEXT] if newer is None else newer
        self.fail, self.calls = fail, []
        self.job_pages, self.run_pages = job_pages, run_pages

    def open(self, request, timeout):
        self.calls.append((request.get_method(), request.full_url))
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return self.body
        response = Response()
        if request.get_method() == 'POST':
            assert request.full_url.startswith('https://discord.com/api/webhooks/'), 'unexpected network'
            response.body = b''
            return response
        if self.fail:
            raise URLError('PRIVATE-API-TOKEN PRIVATE-FAILURE-DETAIL')
        path = request.full_url.split('/actions/', 1)[1]
        if '/jobs?' in path:
            page = int(path.rsplit('page=', 1)[1])
            body = self.job_pages[page-1] if self.job_pages else dict(total_count=len(self.jobs), jobs=self.jobs)
        elif path.startswith('workflows/'):
            page = int(path.rsplit('page=', 1)[1])
            body = self.run_pages[page-1] if self.run_pages else dict(total_count=len(self.newer), workflow_runs=self.newer)
        else:
            body = self.run
        response.body = json.dumps(body).encode()
        return response


class CancellationDecisionTest(unittest.TestCase):
    def execute(self, **kwargs):
        hint = kwargs.pop('hint', 'cancelled')
        needs = kwargs.pop('needs', {})
        opener = FakeOpener(**kwargs)
        env = dict(NOTICE_RUN_URL='https://github.com/example/repository/actions/runs/12345',
                   NOTICE_GITHUB_TOKEN='PRIVATE-API-TOKEN', NOTICE_CHECK_SUPERSEDED='true',
                   NOTICE_RUN_CONCLUSION=hint, NOTICE_NEEDS=json.dumps(needs),
                   NOTICE_DISCORD_WEBHOOK='https://discord.com/api/webhooks/123/PRIVATE-WEBHOOK',
                   NOTICE_TITLE='系統故障', NOTICE_WORKFLOW='Pages', NOTICE_EVENT='workflow_run')
        output = io.StringIO()
        with redirect_stdout(output):
            delivered = notify.notify(env, opener)
        for value in ('PRIVATE-API-TOKEN', 'PRIVATE-WEBHOOK', 'PRIVATE-FAILURE-DETAIL'):
            self.assertNotIn(value, output.getvalue())
        posts = [url for method, url in opener.calls if method == 'POST']
        return delivered, posts, output.getvalue(), opener

    def test_superseded_cancelled_never_posts_to_either_channel(self):
        delivered, posts, output, _ = self.execute()
        self.assertFalse(delivered)
        self.assertEqual(posts, [])
        self.assertIn('superseded by run 12346', output)
        self.assertNotIn('::warning::', output)

    def test_manual_cancellation_without_a_later_matching_run_notifies_once(self):
        delivered, posts, _, _ = self.execute(newer=[])
        self.assertTrue(delivered)
        self.assertEqual(len(posts), 1)

    def test_different_workflow_branch_or_equal_creation_never_suppresses(self):
        for update in (dict(workflow_id=2), dict(head_branch='other'),
                       dict(created_at=RUN['created_at']), dict(created_at='2026-11-06T00:59:59Z')):
            self.assertTrue(decide(RUN, [], [dict(NEXT, **update)])['notify'])

    def test_cross_event_successor_suppresses_without_event_query_filter(self):
        for event in ('push', 'workflow_run'):
            _, posts, _, opener = self.execute(run=dict(RUN, event=event),
                                              newer=[dict(NEXT, event='workflow_run' if event == 'push' else 'push')])
            self.assertEqual(posts, [])
            query = next(url for method, url in opener.calls if '/workflows/' in url)
            self.assertNotIn('event=', query)
            self.assertIn('branch=main', query)
            self.assertIn('created=%3E', query)

    def test_overall_failure_or_any_failed_job_still_notifies_with_a_successor(self):
        for kwargs in (dict(run=dict(RUN, conclusion='failure')), dict(jobs=[dict(conclusion='failure')])):
            self.assertEqual(len(self.execute(**kwargs)[1]), 1)

    def test_run_job_or_step_timeout_always_notifies_with_a_successor(self):
        for kwargs in (dict(run=dict(RUN, conclusion='timed_out')),
                       dict(jobs=[dict(conclusion='timed_out')]),
                       dict(jobs=[dict(conclusion='cancelled', steps=[dict(conclusion='timed_out')])])):
            self.assertEqual(len(self.execute(**kwargs)[1]), 1)

    def test_api_failure_conservatively_posts_once_without_printing_raw_error(self):
        delivered, posts, output, _ = self.execute(fail=True)
        self.assertTrue(delivered)
        self.assertEqual(len(posts), 1)
        self.assertIn('保守通知一次', output)

    def test_in_run_needs_cancellation_and_completed_fallback_make_the_same_decision(self):
        in_run = self.execute(run=dict(RUN, conclusion=None), hint='', needs={'build': {'result': 'cancelled'}})
        fallback = self.execute()
        self.assertEqual(in_run[1], fallback[1])
        self.assertEqual(in_run[1], [])
        failure = self.execute(run=dict(RUN, conclusion=None), needs={'build': {'result': 'failure'}})
        self.assertEqual(len(failure[1]), 1)

    def test_failed_job_on_page_two_prevents_suppression(self):
        pages = [dict(total_count=101, jobs=[dict(conclusion='success') for _ in range(100)]),
                 dict(total_count=101, jobs=[dict(conclusion='failure')])]
        self.assertEqual(len(self.execute(job_pages=pages)[1]), 1)

    def test_matching_successor_on_page_two_is_found(self):
        pages = [dict(total_count=101, workflow_runs=[dict(NEXT, head_branch='other') for _ in range(100)]),
                 dict(total_count=101, workflow_runs=[NEXT])]
        self.assertEqual(self.execute(run_pages=pages)[1], [])

    def test_both_workflows_enable_the_same_composite_policy(self):
        for filename in ('notify-failure.yml', 'failure-notice-fallback.yml'):
            doc = yaml.load((ROOT / '.github/workflows' / filename).read_text(), Loader=yaml.BaseLoader)
            sender = next(step for job in doc['jobs'].values() for step in job['steps']
                          if step.get('uses', '').endswith('/notify-failure'))
            self.assertEqual(sender['with']['check-superseded'], 'true')

    def test_real_week_of_21_cancelled_runs_replays_to_zero_notifications(self):
        snapshot = json.loads((ROOT / 'tests/fixtures/notifications/cancelled-week-20261106.json').read_text())
        self.assertEqual(len(snapshot['runs']), 21)
        decisions = [decide(item['run'], item['jobs'], item['newer']) for item in snapshot['runs']]
        self.assertEqual(sum(row['notify'] for row in decisions), 0)
        self.assertTrue(all(row['reason'] == 'superseded' for row in decisions))

    def test_historical_replay_does_not_use_future_runs_to_hide_cross_event_cancellations(self):
        snapshot = json.loads((ROOT / 'tests/fixtures/notifications/cancelled-week-20261106.json').read_text())
        result = replay(snapshot)
        self.assertEqual(result['summary']['notifications'], 0)
        self.assertEqual(result['summary']['atCancellation'], {'suppressed': 21, 'notifications': 0})
        notices = [row for row in result['records'] if row['atCancellation']['notify']]
        self.assertEqual(notices, [])
        for row in result['records']:
            witnesses = [candidate for candidate in row['newerRuns']
                         if candidate['id'] == row['atCancellation']['supersededBy']]
            self.assertEqual(len(witnesses), 1)
            self.assertLessEqual(witnesses[0]['created_at'], row['cancellationCompletedAt'])

    def test_future_only_candidate_cannot_suppress_at_cancellation(self):
        snapshot = json.loads((ROOT / 'tests/fixtures/notifications/cancelled-week-20261106.json').read_text())
        item = copy.deepcopy(snapshot['runs'][0])
        item['newer'] = [dict(item['run'], id=item['run']['id'] + 1,
                             event='push', created_at='2099-01-01T00:00:00Z')]
        item.pop('crossEventReplacement', None)
        snapshot['runs'] = [item]
        result = replay(snapshot)
        self.assertEqual(result['summary']['notifications'], 0)
        self.assertEqual(result['summary']['atCancellation']['notifications'], 1)


if __name__ == '__main__':
    unittest.main()

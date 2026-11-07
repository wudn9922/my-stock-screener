"""Offline subprocess tests for the actual composite-action entry point."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / '.github/actions/notify-failure'
sys.path.insert(0, str(ACTION))
spec = importlib.util.spec_from_file_location('failure_notify', ACTION / 'notify.py')
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)
WEBHOOK = 'https://discord.com/api/webhooks/111/PRIVATE-WEBHOOK-TOKEN'
TOKEN, RECIPIENT = 'PRIVATE-LINE-TOKEN', 'PRIVATE-LINE-RECIPIENT'
RUN = 'https://github.com/wudn9922/my-stock-screener/actions/runs/12345'
CHILD = r'''
import json,os,runpy,sys
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
calls=[];texts=[]
class Response:
    status=204
    def __enter__(self): return self
    def __exit__(self,*args): pass
class Fake:
    def open(self,request,timeout):
        url=request.full_url
        kind='discord' if url.startswith('https://discord.com/api/webhooks/') else 'line' if url=='https://api.line.me/v2/bot/message/push' else 'unexpected'
        assert kind!='unexpected','network forbidden'
        calls.append(kind)
        payload=json.loads(request.data)
        texts.append(json.dumps(payload['embeds'] if kind=='discord' else payload['messages'],ensure_ascii=False))
        case=os.environ['OFFLINE_CASE']
        if case=='both-fail' or kind=='discord' and case=='discord-fail':
            raise URLError('PRIVATE-LINE-TOKEN '+url)
        return Response()
sys.path.insert(0,str(Path(sys.argv[1]).parent))
with patch('urllib.request.build_opener',return_value=Fake()):
    try: runpy.run_path(sys.argv[1],run_name='__main__')
    except SystemExit as result: code=result.code
print('OFFLINE_RESULT='+json.dumps({'calls':calls,'texts':texts,'exit':code}))
'''


class CompositeDeliveryTest(unittest.TestCase):
    def execute(self, case='success', webhook=WEBHOOK, line=True, **updates):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'output'
            env = {**os.environ, 'OFFLINE_CASE': case, 'NOTICE_DISCORD_WEBHOOK': webhook,
                   'NOTICE_LINE_TOKEN': TOKEN if line else '', 'NOTICE_LINE_USER': RECIPIENT if line else '',
                   'NOTICE_GITHUB_TOKEN': '', 'NOTICE_TITLE': 'Atlas 系統失敗',
                   'NOTICE_WORKFLOW': 'Full-Market Universe', 'NOTICE_EVENT': 'schedule',
                   'NOTICE_RUN_URL': RUN, 'NOTICE_NEEDS': '{}',
                   'NOTICE_SUMMARY': f'部分失敗: Build breadth signals {TOKEN} {RECIPIENT} {WEBHOOK}',
                   'GITHUB_OUTPUT': str(output),
                   # A deliberately unusable price-alert webhook must never be selected.
                   'DISCORD_WEBHOOK_URL': 'https://forbidden.invalid/PRICE-CHANNEL', **updates}
            process = subprocess.run([sys.executable, '-c', CHILD, str(ACTION / 'notify.py')],
                                     env=env, text=True, capture_output=True, timeout=20)
            self.assertEqual(process.returncode, 0, process.stderr)
            line_out = next(line for line in process.stdout.splitlines() if line.startswith('OFFLINE_RESULT='))
            result = json.loads(line_out.split('=', 1)[1])
            self.assertEqual(result['exit'], 0)
            raw = process.stdout + process.stderr
            for secret in (WEBHOOK, TOKEN, RECIPIENT, 'PRIVATE-WEBHOOK-TOKEN', 'PRICE-CHANNEL'):
                self.assertNotIn(secret, raw)
            return result, output.read_text(), raw

    def test_discord_success_never_calls_line(self):
        result, output, raw = self.execute()
        self.assertEqual(result['calls'], ['discord'])
        self.assertIn('delivered=true', output)
        self.assertNotIn('::warning::', raw)
        embed = json.loads(result['texts'][0])[0]
        self.assertEqual(embed['fields'][0]['value'], 'Full-Market Universe')
        self.assertEqual(embed['fields'][1]['value'], 'schedule')
        self.assertIn('台北', embed['fields'][-1]['value'])
        self.assertEqual(embed['url'], RUN)

    def test_discord_failure_falls_back_to_line(self):
        result, output, _ = self.execute('discord-fail')
        self.assertEqual(result['calls'], ['discord', 'line'])
        self.assertIn('delivered=true', output)

    def test_both_failed_only_warn_and_exit_zero(self):
        result, output, raw = self.execute('both-fail')
        self.assertEqual(result['calls'], ['discord', 'line'])
        self.assertIn('delivered=false', output)
        self.assertIn('::warning::', raw)

    def test_missing_discord_uses_line_and_missing_both_warns(self):
        result, _, _ = self.execute(webhook='')
        self.assertEqual(result['calls'], ['line'])
        result, output, raw = self.execute(webhook='', line=False)
        self.assertEqual(result['calls'], [])
        self.assertIn('delivered=false', output)
        self.assertIn('::warning::', raw)

    def test_untrusted_webhook_and_secret_bearing_run_link_never_escape(self):
        result, _, raw = self.execute(webhook='https://forbidden.invalid/PRIVATE-WEBHOOK-TOKEN',
                                      NOTICE_RUN_URL=RUN+'?token='+TOKEN)
        self.assertEqual(result['calls'], ['line'])
        self.assertNotIn('?token=', raw)
        self.assertNotIn('forbidden.invalid', raw)

    def test_only_selected_job_results_are_summarized_not_outputs_or_logs(self):
        summary = notify.summaries({'build': {'result': 'failure', 'outputs': {'token': TOKEN,
                                    'notify_summary': 'Build breadth signals'}}, 'healthy': {'result': 'success'}})
        self.assertEqual(summary, ['build: failure', 'build: Build breadth signals'])
        self.assertNotIn(TOKEN, str(summary))

    def test_job_metadata_reads_names_and_conclusions_without_logs_or_private_outputs(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit):
                return json.dumps({'jobs': [
                    {'name': 'build', 'conclusion': 'failure', 'steps': [
                        {'name': 'Build breadth signals', 'conclusion': 'failure'},
                        {'name': 'Healthy step', 'conclusion': 'success'}],
                     'outputs': {'holdings': 'PRIVATE-HOLDINGS-SENTINEL'}},
                    {'name': 'healthy', 'conclusion': 'success', 'steps': []}]}).encode()
        opener = Mock()
        opener.open.return_value = Response()
        self.assertEqual(notify.job_summary(RUN, TOKEN, opener), ['build: Build breadth signals'])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertIn('/actions/runs/12345/jobs?', request.full_url)
        self.assertNotIn('/logs', request.full_url)
        self.assertEqual(request.headers['Authorization'], f'Bearer {TOKEN}')

    def test_composite_executes_the_entry_point_and_uses_only_failure_secret_input(self):
        action = yaml.load((ACTION / 'action.yml').read_text(), Loader=yaml.BaseLoader)
        step = action['runs']['steps'][0]
        self.assertEqual(step['run'], 'python "$GITHUB_ACTION_PATH/notify.py"')
        self.assertEqual(step['continue-on-error'], 'true')
        self.assertEqual(step['env']['NOTICE_DISCORD_WEBHOOK'], '${{ inputs.discord-webhook }}')


class WorkflowCoverageTest(unittest.TestCase):
    REVIEW = {'atlas-review.yml', 'research-review.yml', 'generated-verify.yml', 'kline-archive-review.yml',
              'failure-notice-review.yml'}
    HELPERS = {'notify-failure.yml', 'failure-notice-fallback.yml'}

    def test_all_producers_notify_without_altering_review_workflows(self):
        count = 0
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            doc = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            if path.name in self.REVIEW | self.HELPERS:
                self.assertNotIn('notify-system-failure', doc.get('jobs', {}), path.name)
                continue
            count += 1
            job = doc['jobs']['notify-system-failure']
            self.assertIn('failure()', job['if'], path.name)
            self.assertIn('cancelled()', job['if'], path.name)
            self.assertEqual(job['uses'], './.github/workflows/notify-failure.yml')
            self.assertEqual(job['secrets']['DISCORD_WEBHOOK_URL1'], '${{ secrets.DISCORD_WEBHOOK_URL1 }}')
            self.assertNotIn('DISCORD_WEBHOOK_URL', job['secrets'])
            self.assertEqual(set(job['needs']), set(doc['jobs']) - {'notify-system-failure'}, path.name)
            for name, producer in doc['jobs'].items():
                if name == 'notify-system-failure':
                    continue
                partial = [s for s in producer.get('steps', []) if s.get('continue-on-error') == 'true'
                           and s.get('id') != 'collect_notification_outcomes']
                if partial:
                    self.assertTrue(all(s.get('id') for s in partial), path.name)
                    self.assertIn('notify_partial', producer['outputs'], path.name)
                    collector = next(s for s in producer['steps'] if s.get('id') == 'collect_notification_outcomes')
                    names = json.loads(collector['env']['NOTIFY_STEP_NAMES'])
                    self.assertTrue({s['id'] for s in partial} <= names.keys(), path.name)
        self.assertGreaterEqual(count, 24)

    def test_normal_daily_line_report_steps_are_preserved(self):
        doc = yaml.load((ROOT / '.github/workflows/schedule.yml').read_text(), Loader=yaml.BaseLoader)
        steps = doc['jobs']['build']['steps']
        for name in ('Run Screener', 'Run Dify Wall Street Report'):
            step = next(s for s in steps if s.get('name') == name)
            self.assertEqual(step['env']['LINE_ACCESS_TOKEN'], '${{ secrets.LINE_ACCESS_TOKEN }}')
        self.assertFalse(any(s.get('name') == 'Notify LINE on failure' for s in steps))

    def test_cancelled_run_fallback_uses_trusted_main_and_skips_reviews_and_sent_receipts(self):
        path = ROOT / '.github/workflows/failure-notice-fallback.yml'
        doc = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        names = set(doc['on']['workflow_run']['workflows'])
        expected = set()
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            if path.name not in self.REVIEW | self.HELPERS:
                expected.add(yaml.load(path.read_text(), Loader=yaml.BaseLoader)['name'])
        self.assertEqual(names, expected)
        job = doc['jobs']['fallback']
        self.assertIn('head_repository.full_name == github.repository', job['if'])
        self.assertIn("event != 'pull_request'", job['if'])
        checkout = job['steps'][0]
        self.assertEqual(checkout['with']['ref'], 'main')
        self.assertEqual(checkout['with']['persist-credentials'], 'false')
        sender = job['steps'][-1]
        self.assertIn("receipt.outputs.delivered != 'true'", sender['if'])
        self.assertEqual(sender['with']['discord-webhook'], '${{ secrets.DISCORD_WEBHOOK_URL1 }}')
        self.assertEqual(job['continue-on-error'], 'true')

    def test_delivery_receipt_is_uploadable_and_contains_only_run_id(self):
        doc = yaml.load((ROOT / '.github/workflows/notify-failure.yml').read_text(), Loader=yaml.BaseLoader)
        steps = doc['jobs']['notify']['steps']
        marker = next(step for step in steps if 'NOTICE_RUN_ID' in step.get('env', {}))
        upload = next(step for step in steps if 'actions/upload-artifact' in step.get('uses', ''))
        # upload-artifact excludes dotfiles by default; use a visible run-only file.
        self.assertFalse(Path(upload['with']['path']).name.startswith('.'))
        self.assertIn(upload['with']['path'], marker['run'])
        self.assertEqual(marker['env'], {'NOTICE_RUN_ID': '${{ github.run_id }}'})
        self.assertIn("outputs.delivered == 'true'", upload['if'])
        self.assertEqual(upload['with']['retention-days'], '1')

    def test_real_partial_collector_uses_outcome_and_emits_valid_github_outputs(self):
        doc = yaml.load((ROOT / '.github/workflows/universe-full.yml').read_text(), Loader=yaml.BaseLoader)
        job = doc['jobs']['build']
        step = next(s for s in job['steps'] if s.get('id') == 'collect_notification_outcomes')
        names = json.loads(step['env']['NOTIFY_STEP_NAMES'])
        self.assertEqual(names['archive_closure_scan'], 'Build market closure evidence from read-only archive')
        for label in ('Build breadth trading signals', 'Build market closure evidence from read-only archive',
                      'Publish derived closure evidence including the daily cohort'):
            target = next(key for key, value in names.items() if value == label)
            results = {key: {'outcome': 'success', 'conclusion': 'success'} for key in names}
            results[target] = {'outcome': 'failure', 'conclusion': 'success', 'outputs': {'private': TOKEN}}
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / 'output'
                process = subprocess.run(['bash', '-euo', 'pipefail', '-c', step['run']], text=True, capture_output=True,
                    env={**os.environ, 'NOTIFY_STEP_RESULTS': json.dumps(results), 'NOTIFY_STEP_NAMES': json.dumps(names),
                         'GITHUB_OUTPUT': str(output)}, timeout=10)
                self.assertEqual(process.returncode, 0, process.stderr)
                values = dict(line.split('=', 1) for line in output.read_text().splitlines())
                self.assertEqual(values['has_partial'], 'true')
                self.assertEqual(json.loads(values['summary']), [label])
                self.assertNotIn(TOKEN, process.stdout + process.stderr + output.read_text())


if __name__ == '__main__':
    unittest.main()

"""System metadata only. No source responses, credentials or private positions.

Never use the normal-price Discord webhook. Callers pass the dedicated failure
secret through an environment variable; neither URLs nor exceptions are logged.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def clean(value, secrets=(), limit=1000):
    text = str(value or '')
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, '[redacted]')
    text = re.sub(r'https?://\S+', '[link omitted]', text)
    text = re.sub(r'(?i)(?:bearer\s+|(?:token|secret|api[_-]?key|signature)\s*[=:]\s*)\S+', '[redacted]', text)
    return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)[:limit]


def valid_run_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        return (parsed.scheme == 'https' and parsed.netloc == 'github.com' and not parsed.query and not parsed.fragment
                and re.fullmatch(r'/[\w.-]+/[\w.-]+/actions/runs/\d+', parsed.path) is not None)
    except (ValueError, TypeError):
        return False


def post(url, payload, headers, opener):
    try:
        request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                                         headers={'Content-Type': 'application/json', **headers}, method='POST')
        with opener.open(request, timeout=10) as response:
            return 200 <= response.status < 300
    except Exception:
        # HTTPError / URLError can contain a signed webhook URL or response body.
        return False


def job_summary(run_url, token, opener):
    """Read names/conclusions only. Never request logs or artifacts with user data."""
    if not token or not valid_run_url(run_url):
        return []
    owner, repo, _, _, run_id = urllib.parse.urlsplit(run_url).path.strip('/').split('/')
    found = []
    try:
        for page in range(1, 11):
            url = f'https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}'
            request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}',
                'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
            with opener.open(request, timeout=10) as response:
                payload = json.loads(response.read(2 * 1024 * 1024))
            jobs = payload.get('jobs', [])
            for job in jobs:
                if job.get('conclusion') not in ('failure', 'cancelled', 'timed_out'):
                    continue
                steps = [str(s.get('name', '')) for s in job.get('steps', [])
                         if s.get('conclusion') in ('failure', 'cancelled', 'timed_out')]
                found.append(str(job.get('name', 'job')) + ': ' + (' / '.join(steps) or str(job['conclusion'])))
            if len(jobs) < 100:
                break
    except Exception:
        # The caller's static summary still works if metadata is unavailable.
        return found
    return found


def summaries(needs):
    result = []
    if not isinstance(needs, dict):
        return result
    for job, value in needs.items():
        if not isinstance(value, dict):
            continue
        if value.get('result') in ('failure', 'cancelled'):
            result.append(f'{job}: {value["result"]}')
        outputs = value.get('outputs', {})
        if isinstance(outputs, dict) and outputs.get('notify_summary'):
            summary = outputs['notify_summary']
            try:
                labels = json.loads(summary)
                if isinstance(labels, list) and all(isinstance(label, str) for label in labels):
                    summary = ' / '.join(labels)
            except (TypeError, ValueError):
                pass
            result.append(f'{job}: {summary}')
    return result


def notify(env, opener=None, now=None):
    opener = opener or urllib.request.build_opener(NoRedirect())
    now = now or datetime.now(timezone.utc)
    webhook, line_token, line_user, github_token = (env.get(k, '') for k in (
        'NOTICE_DISCORD_WEBHOOK', 'NOTICE_LINE_TOKEN', 'NOTICE_LINE_USER', 'NOTICE_GITHUB_TOKEN'))
    hidden = (webhook, line_token, line_user, github_token)
    run_url = env.get('NOTICE_RUN_URL', '')
    run_url = run_url if valid_run_url(run_url) else ''
    title = clean(env.get('NOTICE_TITLE'), hidden, 180) or 'Atlas 系統狀態'
    workflow = clean(env.get('NOTICE_WORKFLOW'), hidden, 200) or 'unknown'
    event = clean(env.get('NOTICE_EVENT'), hidden, 100) or 'unknown'
    try:
        needs = json.loads(env.get('NOTICE_NEEDS', '{}'))
    except (TypeError, ValueError):
        needs = {}
    rows = [env.get('NOTICE_SUMMARY', ''), *summaries(needs), *job_summary(run_url, github_token, opener)]
    detail = clean('\n'.join(dict.fromkeys(str(r) for r in rows if r)), hidden, 1000) or '執行失敗／取消；請查看執行紀錄。'
    taipei = now.astimezone(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d %H:%M:%S 台北')
    embed = {'title': title, 'color': 0xD49B35, 'fields': [
        {'name': 'Workflow', 'value': workflow}, {'name': '觸發方式', 'value': event, 'inline': True},
        {'name': '失敗 job／step 或資料狀態', 'value': detail},
        {'name': '執行連結', 'value': run_url or '無可用執行連結'},
        {'name': '時間', 'value': taipei}], 'timestamp': now.astimezone(timezone.utc).isoformat()}
    if run_url:
        embed['url'] = run_url
    try:
        target = urllib.parse.urlsplit(webhook)
        discord_allowed = (target.scheme == 'https' and target.netloc in ('discord.com', 'discordapp.com',
                           'canary.discord.com', 'ptb.discord.com') and target.path.startswith('/api/webhooks/')
                           and not target.query and not target.fragment)
    except ValueError:
        discord_allowed = False
    if discord_allowed and post(webhook, {'embeds': [embed], 'allowed_mentions': {'parse': []}}, {}, opener):
        return True
    if line_token and line_user:
        text = f'{title}\nWorkflow：{workflow}\n觸發：{event}\n{detail}\n{run_url}\n{taipei}'
        if post('https://api.line.me/v2/bot/message/push', {'to': line_user, 'messages': [{'type': 'text', 'text': text}]},
                {'Authorization': f'Bearer {line_token}'}, opener):
            return True
    print('::warning::系統狀態通知未送達：Discord 與 LINE 皆不可用。')
    return False


def main():
    delivered = False
    try:
        delivered = notify(os.environ)
    except Exception:
        print('::warning::系統狀態通知無法完成；原工作結論保持不變。')
    try:
        if os.environ.get('GITHUB_OUTPUT'):
            with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
                stream.write('delivered=' + str(delivered).lower() + '\n')
    except OSError:
        print('::warning::無法記錄通知送達狀態。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

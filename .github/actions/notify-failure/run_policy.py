"""Read-only cancellation policy shared by in-run and workflow_run notices."""
from datetime import datetime
import json
import urllib.parse
import urllib.request


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Missing timestamp timezone')
    return parsed


def newer_run(run, candidates):
    """A later creation, same workflow id, branch and event; status is irrelevant."""
    if (type(run.get('id')) is not int or type(run.get('workflow_id')) is not int
            or not isinstance(run.get('head_branch'), str) or not isinstance(run.get('event'), str)):
        raise ValueError('Incomplete originating run metadata')
    created = timestamp(run['created_at'])
    matches = [candidate for candidate in candidates
               if type(candidate.get('id')) is int and candidate['id'] != run['id']
               and candidate.get('workflow_id') == run['workflow_id']
               and candidate.get('head_branch') == run['head_branch']
               and candidate.get('event') == run['event']
               and timestamp(candidate['created_at']) > created]
    return min(matches, key=lambda row: (timestamp(row['created_at']), row['id'])) if matches else None


def decide(run, jobs, candidates=(), hint='', results=()):
    """Pure decision, also used to replay public historical run metadata offline."""
    conclusions = [job.get('conclusion') for job in jobs]
    step_conclusions = [step.get('conclusion') for job in jobs for step in (job.get('steps') or [])]
    if 'timed_out' in [run.get('conclusion'), hint, *results, *conclusions, *step_conclusions]:
        return dict(notify=True, reason='timed-out')
    if 'failure' in [*results, *conclusions]:
        return dict(notify=True, reason='failed-job')
    if run.get('conclusion') == 'failure' or hint == 'failure':
        return dict(notify=True, reason='failed-run')
    if 'cancelled' not in [run.get('conclusion'), hint, *results]:
        return dict(notify=True, reason='not-cancelled')
    successor = newer_run(run, candidates)
    return (dict(notify=False, reason='superseded', supersededBy=successor['id']) if successor
            else dict(notify=True, reason='cancelled-without-successor'))


class RunMetadata:
    def __init__(self, repo, token, opener):
        self.base = f'https://api.github.com/repos/{repo}/actions/'
        self.token, self.opener = token, opener

    def get(self, path):
        request = urllib.request.Request(self.base + path, headers={
            'Authorization': f'Bearer {self.token}', 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'})
        with self.opener.open(request, timeout=5) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError('Metadata response too large')
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError('Invalid metadata response')
        return value

    def pages(self, path, field):
        separator = '&' if '?' in path else '?'
        seen = 0
        for page in range(1, 11):
            doc = self.get(f'{path}{separator}per_page=100&page={page}')
            values, total = doc.get(field), doc.get('total_count')
            if not isinstance(values, list) or type(total) is not int or total < 0:
                raise ValueError('Invalid metadata collection')
            seen += len(values)
            yield values
            if seen >= total:
                return
            if not values:
                raise ValueError('Incomplete metadata pagination')
        raise ValueError('Metadata pagination limit reached')


def cancellation_decision(run_url, token, opener, hint='', needs=None):
    """API uncertainty never suppresses a notice. Never returns raw errors/data."""
    results = [row.get('result') for row in (needs or {}).values() if isinstance(row, dict)]
    if 'failure' in results or 'timed_out' in results or hint in ('failure', 'timed_out'):
        return decide({'conclusion': hint}, [], hint=hint, results=results)
    try:
        if not token:
            raise ValueError('Missing metadata token')
        owner, repo, _, _, run_id = urllib.parse.urlsplit(run_url).path.strip('/').split('/')
        if not run_id.isdecimal():
            raise ValueError('Invalid run id')
        api = RunMetadata(f'{owner}/{repo}', token, opener)
        run = api.get(f'runs/{run_id}')
        if run.get('id') != int(run_id):
            raise ValueError('Originating run mismatch')
        if 'cancelled' not in [run.get('conclusion'), hint, *results]:
            return decide(run, [], hint=hint, results=results)
        jobs = [row for page in api.pages(f'runs/{run_id}/jobs', 'jobs') for row in page]
        initial = decide(run, jobs, hint=hint, results=results)
        if initial['reason'] in ('failed-job', 'failed-run', 'timed-out'):
            return initial
        query = urllib.parse.urlencode({'branch': run['head_branch'], 'event': run['event'],
                                        'created': '>' + run['created_at']})
        for page in api.pages(f'workflows/{int(run["workflow_id"])}/runs?{query}', 'workflow_runs'):
            decision = decide(run, jobs, page, hint=hint, results=results)
            if not decision['notify']:
                return decision
        return dict(notify=True, reason='cancelled-without-successor')
    except Exception:
        return dict(notify=True, reason='metadata-unavailable')

"""Read-only production-data watchdog. Notification delivery is a separate action.

Only session dates and producer status are inspected. Never changes prices,
archive contents, calculated metrics or the normal daily report delivery.
"""
import argparse
import csv
import io
import copy
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


DATASETS = {
    'archive': ('kline-archive', 'manifest.json', ('TW', 'US')),
    'warnings': (None, 'docs/report/market-warnings.json', ('TW', 'US')),
    'breadth-signals': (None, 'docs/report/breadth-signals.json', ('US',)),
    'breakouts': (None, 'docs/report/breakouts.json', ('TW', 'US')),
    'universe': (None, 'docs/report/universe.json', ('TW', 'US')),
}
LABELS = {'archive': 'K 線封存', 'warnings': '多頭末期警示', 'breadth-signals': '寬度訊號',
          'breakouts': '全市場進場成功率', 'universe': '全市場選股'}


def iso_day(value):
    try:
        if not isinstance(value, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is None:
            return None
        return date.fromisoformat(value)
    except ValueError:
        return None


def completed_sessions(calendar, market, now):
    if not isinstance(calendar, dict) or calendar.get('version') != 1:
        raise ValueError('交易日曆無法讀取')
    today = now.astimezone(ZoneInfo('Asia/Taipei' if market == 'TW' else 'America/New_York')).date()
    start, end = iso_day(calendar.get('validFrom')), iso_day(calendar.get('validTo'))
    if not start or not end or not start <= today <= end:
        raise ValueError('交易日曆未涵蓋今天')
    markets = calendar.get('markets', {})
    row = markets.get(market) if isinstance(markets, dict) else None
    sessions = row.get('sessions') if isinstance(row, dict) else None
    if not isinstance(sessions, dict):
        raise ValueError('交易日曆缺少市場')
    result = []
    for day, row in sessions.items():
        parsed = iso_day(day)
        close = row.get('close') if isinstance(row, dict) else None
        if parsed is None or type(close) is not int or not start <= parsed <= end:
            raise ValueError('交易日曆格式不完整')
        if close <= now.timestamp():
            result.append(parsed)
    if not result:
        raise ValueError('交易日曆缺少已收盤交易日')
    return sorted(result)


def evaluate(calendar, sources, now):
    """Issues plus checked keys, so an unavailable source cannot falsely recover."""
    issues, checked = {}, set()
    for market in ('TW', 'US'):
        calendar_key = f'calendar/{market}'
        checked.add(calendar_key)
        try:
            sessions = completed_sessions(calendar, market, now)
        except ValueError as error:
            issues[calendar_key] = f'{market}：{error}'
            continue
        expected = sessions[-1]
        for source, (_, _, markets) in DATASETS.items():
            if market not in markets:
                continue
            key = f'{source}/{market}'
            checked.add(key)
            payload = sources.get(source)
            if not isinstance(payload, dict):
                issues[key] = f'{LABELS[source]} {market}：來源無法讀取'
                continue
            markets = payload.get('markets')
            row = markets.get(market if source == 'universe' else market.lower()) if isinstance(markets, dict) else None
            if not isinstance(row, dict):
                issues[key] = f'{LABELS[source]} {market}：缺少市場資料'
                continue
            observed = iso_day(row.get('lastDate' if source == 'archive' else 'asOf'))
            status = row.get('update', {}).get('status') if isinstance(row.get('update'), dict) else None
            if source == 'warnings' and status == 'unavailable':
                issues[key] = f'{LABELS[source]} {market}：本次未更新（{status}），資料日 {observed or "未知"}'
                continue
            if observed is None:
                issues[key] = f'{LABELS[source]} {market}：缺少有效資料日'
                continue
            # The 30-day left edge can undercount a long outage, but >=2 known
            # closed sessions still proves the requested >1-session threshold.
            lag = sum(session > observed for session in sessions)
            # A retained file that already covers the latest closed session is
            # current: a later rerun failing to refetch the same day is not stale.
            if source == 'warnings' and status == 'retained' and lag >= 1:
                issues[key] = f'{LABELS[source]} {market}：本次未更新（retained），資料日 {observed}，應至 {expected}'
                continue
            if observed > expected:
                issues[key] = f'{LABELS[source]} {market}：資料日晚於最近已收盤交易日 {expected}'
            elif lag > 1:
                issues[key] = f'{LABELS[source]} {market}：資料日 {observed}，應至 {expected}，至少落後 {lag} 個交易日'
    return issues, checked


def twii_volume_issue(calendar, raw_csv, now):
    """Latest *completed* TW session only; absence cannot imply recovery."""
    key = "volume/^TWII"
    if not isinstance(raw_csv, str):
        return {}, set()
    try:
        sessions = completed_sessions(calendar, "TW", now)
        expected = sessions[-1].isoformat()
        reader = csv.DictReader(io.StringIO(raw_csv))
        if reader.fieldnames != ["Date", "Open", "High", "Low", "Close", "Volume"]:
            return {}, set()
        rows = list(reader)
        if not rows or rows[-1]["Date"] != expected:
            return {}, set()  # Delayed/unavailable archive cannot imply recovery.
        by_date = {r["Date"]: r["Volume"] for r in rows}
        # The most recently closed day gets one FULL trading-session grace.
        # Still-zero volume on a previous session is a confirmed backlog.
        aged = sorted(day for day, value in by_date.items()
                     if "2026-11-05" <= day < expected and value == "0")
        if aged:
            return {key: f"^TWII {aged[0]} 成交量隔一個交易日仍為 0（封存尚未補回）"}, {key}
        value = by_date.get(expected)
        if value is None or not re.fullmatch(r"\d+", value):
            return {}, set()
        # Fresh same-session zero: intentionally no failure; prior stale
        # problems can recover if all older inspected sessions are positive.
        return {}, {key}
    except (ValueError, KeyError, csv.Error):
        return {}, set()


def daily_report_issue(runs, now):
    """Keep the existing six-hour report-day window and Sunday-Friday policy."""
    target = (now.astimezone(ZoneInfo('Asia/Taipei')) - timedelta(hours=6)).date()
    key = 'daily-report'
    if target.weekday() == 5:  # no scheduled Saturday daily report
        return {}, set()
    if runs is None:
        return {key: f'{target} 每日報告：無法查詢執行狀態'}, {key}
    if any(run.get('conclusion') == 'success' for run in runs):
        return {}, {key}
    detail = '仍在執行，尚未完成' if any(run.get('status') != 'completed' for run in runs) else '沒有成功的執行' if runs else '完全沒有執行'
    return {key: f'{target} 每日報告：{detail}'}, {key}


def plan(previous, issues, checked, now):
    """Prepare only; commit this state after a channel accepted the notice."""
    today = now.astimezone(ZoneInfo('Asia/Taipei')).date().isoformat()
    state = copy.deepcopy(previous) if isinstance(previous, dict) and previous.get('version') == 1 else {'version': 1, 'problems': {}}
    if not isinstance(state.get('problems'), dict):
        state['problems'] = {}
    problems = state['problems']
    # A damaged cache must not prevent the next health check or invent recovery.
    for key in list(problems):
        if not isinstance(problems[key], dict):
            del problems[key]
    events = []
    for key, summary in issues.items():
        row = problems.setdefault(key, {})
        if row.get('failureDate') != today:
            events.append({'kind': 'failure', 'key': key, 'summary': summary})
            row['failureDate'] = today
        row.update(active=True, summary=summary)
    for key in checked - set(issues):
        row = problems.get(key)
        if row and row.get('active'):
            if row.get('recoveryDate') != today:
                events.append({'kind': 'recovered', 'key': key, 'summary': f'已恢復：{row.get("summary", key)}'})
                row['recoveryDate'] = today
            row['active'] = False
    return state, events


def read_json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return fallback


def github_json(repo, ref, path, token, opener=urllib.request.urlopen):
    url = f'https://api.github.com/repos/{repo}/contents/{path}?ref={urllib.parse.quote(ref, safe="")}'
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}',
                                    'Accept': 'application/vnd.github.raw+json', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with opener(request, timeout=25) as response:
            body = response.read(32 * 1024 * 1024 + 1)
        if len(body) > 32 * 1024 * 1024:
            return None
        return json.loads(body)
    except Exception:
        # No raw URLs, headers, content or exception messages enter diagnostics.
        return None


def github_text(repo, ref, path, token, opener=urllib.request.urlopen):
    """Bounded read of a dedicated archive CSV. API outage must not mark recovery."""
    safe_path = urllib.parse.quote(path, safe="/")
    url = f'https://api.github.com/repos/{repo}/contents/{safe_path}?ref={urllib.parse.quote(ref, safe="")}'
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}',
                'Accept': 'application/vnd.github.raw+json', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with opener(request, timeout=25) as response:
            body = response.read(1024 * 1024 + 1)
        return body.decode("utf-8") if len(body) <= 1024 * 1024 else None
    except Exception:
        return None


def github_daily_runs(repo, token, now, opener=urllib.request.urlopen):
    target = (now.astimezone(ZoneInfo('Asia/Taipei')) - timedelta(hours=6)).date()
    start = datetime.combine(target, datetime.min.time(), ZoneInfo('Asia/Taipei')).astimezone(timezone.utc)
    query = urllib.parse.urlencode({'created': f'{start.isoformat()}..{(start+timedelta(days=1)).isoformat()}', 'per_page': 100})
    try:
        request = urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/workflows/schedule.yml/runs?{query}',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})
        with opener(request, timeout=25) as response:
            return json.loads(response.read(2 * 1024 * 1024))['workflow_runs']
    except Exception:
        return None


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + '\n')
    temp.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('.cache/system-data-watchdog'))
    parser.add_argument('--now')
    parser.add_argument('--commit', action='store_true')
    args = parser.parse_args(argv)
    if args.commit:
        proposed = read_json(args.state_dir / 'proposed.json')
        if proposed is None:
            raise ValueError('Missing prepared notification state')
        write_json(args.state_dir / 'state.json', proposed)
        return 0
    now = datetime.fromisoformat(args.now.replace('Z', '+00:00')) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Timezone-aware clock required')
    repo, token = os.environ.get('GITHUB_REPOSITORY', ''), os.environ.get('GH_TOKEN', '')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or not token:
        raise ValueError('GitHub metadata access is not configured')
    ref = 'generated' if os.environ.get('GENERATED_BRANCH_MODE') == '1' else 'main'
    calendar = github_json(repo, ref, 'docs/report/market-calendar.json', token)
    sources = {name: github_json(repo, branch or ref, path, token) for name, (branch, path, _) in DATASETS.items()}
    issues, checked = evaluate(calendar, sources, now)
    try:
        expected_tw = completed_sessions(calendar, "TW", now)[-1]
        twii_csv = github_text(repo, "kline-archive", f"tw/^TWII/{expected_tw.year}.csv", token)
        volume_issues, volume_checked = twii_volume_issue(calendar, twii_csv, now)
        issues.update(volume_issues)
        checked.update(volume_checked)
    except ValueError:
        pass  # A broken calendar is already reported; never falsely resolve volume.
    report_issues, report_checked = daily_report_issue(github_daily_runs(repo, token, now), now)
    issues.update(report_issues)
    checked.update(report_checked)
    previous = read_json(args.state_dir / 'state.json')
    proposed, events = plan(previous, issues, checked, now)
    write_json(args.state_dir / 'proposed.json', proposed)
    write_json(args.state_dir / 'events.json', events)
    summaries = '\n'.join(('⚠️ ' if e['kind'] == 'failure' else '✅ ') + e['summary'] for e in events)
    title = 'Atlas 資料未更新／已恢復' if any(e['kind'] == 'failure' for e in events) else 'Atlas 資料已恢復'
    outputs = {'has_events': str(bool(events)).lower(), 'state_changed': str(proposed != previous).lower(),
               'title': title, 'summary': summaries}
    if os.environ.get('GITHUB_OUTPUT'):
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
            for key, value in outputs.items():
                delimiter = 'NOTICE_' + os.urandom(12).hex()
                stream.write(f'{key}<<{delimiter}\n{value}\n{delimiter}\n')
    print(f'Data watchdog: {len(issues)} active problems; {len(events)} new notices; raw sources omitted.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

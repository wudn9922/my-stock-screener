"""Standard-library correction journal union, shared by collectors and Git publishers."""
from datetime import datetime, timedelta, timezone
import json

# Main journal only (docs/report/kline-corrections.json on main). The kline-archive
# branch keeps its own permanent journal and never passes through prune_main_journal.
RETENTION_DAYS = 92
# Safety cap below GitHub's 100 MB single-file limit (~520 bytes per line, so
# ~63 MB). If more events arrive than fit, the newest are kept first.
MAX_EVENTS = 120_000
# Linked to permanent archive audits; replayed from the archive if missing.
PERMANENT_REASON_PREFIX = 'kline-archive/'


def merge_logs(current, incoming):
    """Concurrent collector artifacts retain the union of append-only events."""
    if incoming.get('version') != 1 or not isinstance(incoming.get('events'), list):
        raise ValueError('Invalid correction artifact')
    events = {e['id']: e for e in current.get('events', [])}
    events.update({e['id']: e for e in incoming['events']})
    ordered = sorted(events.values(), key=lambda e: (e['loggedAt'], e['id']))
    blocked = {(q['symbol'],q['date']):q for q in current.get('quarantined', [])}
    for event in ordered:
        key = event['symbol'], event['date']
        if event['status'] == 'quarantined':
            blocked[key] = dict(symbol=key[0], date=key[1], reason=event.get('reason'))
        elif event['status'] == 'corrected':
            blocked.pop(key, None)
    return dict(version=1, quarantined=[blocked[k] for k in sorted(blocked)], events=ordered)


def fact(event):
    """What an event says, without when/where it was fetched."""
    return event['status'], event.get('reason'), event.get('old'), event.get('new')


def permanent(event):
    return event['status'] == 'quarantined' or str(event.get('reason') or '').startswith(PERMANENT_REASON_PREFIX)


def logged_at(event):
    # Python 3.10 (the scheduled jobs) does not parse a trailing "Z".
    moment = datetime.fromisoformat(event['loggedAt'].replace('Z', '+00:00'))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def prune_main_journal(journal, now=None):
    """Main journal retention; the quarantine list itself is never pruned.

    Drops unfinished-session ("deferred") rows, which never entered storage; an
    event repeating the previous retained event for the same symbol/date (a retry
    seeing the same bad or revised bar); and events older than RETENTION_DAYS, except
    quarantines and archive-linked audits.
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
    last, kept = {}, []
    for event in sorted(journal['events'], key=lambda e: (e['loggedAt'], e['id'])):
        if event['status'] == 'deferred':
            continue
        key = event['symbol'], event['date']
        if key in last and fact(last[key]) == fact(event):
            continue
        # A repeat of an aged-out event becomes the retained first occurrence.
        if permanent(event) or logged_at(event) >= cutoff:
            last[key] = event
            kept.append(event)
    if len(kept) > MAX_EVENTS:
        keep = {id(e) for e in kept if permanent(e)}
        keep.update(id(e) for e in [e for e in kept if not permanent(e)][-(MAX_EVENTS - len(keep)):])
        kept = [e for e in kept if id(e) in keep]
    return dict(version=1, quarantined=journal['quarantined'], events=kept)


def dump_journal(journal):
    """One event per line: compact, still line-diffable and valid JSON."""
    line = lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    rows = lambda values: ',\n'.join(line(v) for v in values)
    return ('{"version":1,"quarantined":[\n' + rows(journal['quarantined'])
            + '\n],"events":[\n' + rows(journal['events']) + '\n]}\n')

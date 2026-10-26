"""Standard-library correction journal union, shared by collectors and Git publishers."""


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


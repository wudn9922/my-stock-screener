#!/usr/bin/env python3
"""Official TW missing-day repair / scoped placeholder removal / exact restoration.

Default is dry-run. Never publishes Git refs. All price writes use kline_archive.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

if __package__:
    from . import kline_archive as archive
    from .official_tw_quotes import OfficialClient, REPAIR_DAYS
else:  # python scripts/repair_kline_archive.py
    import kline_archive as archive
    from official_tw_quotes import OfficialClient, REPAIR_DAYS

BACKUP_NAME = 'removed-placeholders-20261023.json'


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()



def worktree_tree(root):
    """Hash the complete worktree through a temporary Git index, leaving the real index alone."""
    with tempfile.TemporaryDirectory(prefix='repair-tree-') as folder:
        env = {**os.environ, 'GIT_INDEX_FILE': str(Path(folder)/'index')}
        for args in [('read-tree', 'HEAD'), ('add', '-A')]:
            subprocess.run(['git', '-C', str(root), *args], env=env, check=True, capture_output=True)
        return subprocess.check_output(['git', '-C', str(root), 'write-tree'], env=env, text=True).strip()

def outside(root, path):
    path = Path(path).resolve()
    if path.is_relative_to(Path(root).resolve()):
        raise ValueError('Reports, source cache and backups must be outside the archive')
    return path


def write_json(path, doc, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise ValueError('Backup already exists; refusing to overwrite it')
    body = (json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    if exclusive:
        try:
            os.link(temp, path)  # Atomic create-only: preserve the first recovery witness.
        finally:
            temp.unlink(missing_ok=True)
    else:
        temp.replace(path)
    if os.name == 'posix':
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def price_factor(witnesses, day):
    """Three bilateral witnesses may prove raw prices; adjustments still need four."""
    if len(witnesses) not in (3, 4) or len({r['date'] for r in witnesses}) != len(witnesses):
        return None, 'insufficient-comparison-data'
    before = sum(r['date'] < day for r in witnesses)
    after = sum(r['date'] > day for r in witnesses)
    if not before or not after or before + after != len(witnesses):
        return None, 'insufficient-comparison-data'
    with localcontext() as context:
        context.prec = 50
        ratios = [Decimal(r['archiveClose']) / Decimal(r['officialClose']) for r in witnesses]
        if any(not r.is_finite() or r <= 0 for r in ratios):
            return None, 'invalid-comparison-data'
        if all(abs(r-1) <= Decimal('.005') for r in ratios):
            return Decimal(1), None
        if len(witnesses) != 4 or before != 2 or after != 2:
            return None, 'insufficient-comparison-data'
        if max(ratios)/min(ratios)-1 > Decimal('.005'):
            return None, 'unstable-price-basis'
        ordered = sorted(ratios)
        return (ordered[1]+ordered[2])/2, None


def plan_backfill(root, client, targets=REPAIR_DAYS):
    symbols = sorted(p.name for p in (Path(root)/'tw').iterdir() if p.is_dir())
    report = dict(noTradePolicy='skip all zero-volume or missing-OHLC observations',
                  indexVolumePolicy='unknown: MI_5MINS_HIST publishes OHLC only', days=[])
    operations = []
    for day in sorted(targets):
        neighbors = client.neighbors(day)
        observations, source_skips = client.observations(day)
        comparisons = {d: client.observations(d)[0] for d in neighbors}
        rows, skipped, existing = [], [], 0
        years = {d[:4] for d in [day, *neighbors]}
        for symbol in symbols:
            current = {r[0]: r for r in archive.symbol_rows(root, symbol, years)}
            if day in current:
                existing += 1
                continue
            source = observations.get(symbol)
            if source is None:
                skipped.append(dict(symbol=symbol, reason=source_skips.get(symbol, 'no-official-observation')))
                continue
            witnesses = [dict(date=d, archiveClose=current[d][4], officialClose=str(comparisons[d][symbol][4]))
                         for d in neighbors if d in current and symbol in comparisons[d]]
            factor, reason = price_factor(witnesses, day)
            if reason:
                skipped.append(dict(symbol=symbol, reason=reason, witnesses=witnesses))
                continue
            with localcontext() as context:
                context.prec = 50
                row = archive.normalize((day, *(v*factor for v in source[1:5]),
                                         source[5]/factor if source[5] is not None else None))
            exchange = 'twii' if symbol == '^TWII' else 'tpex' if symbol.endswith('.TWO') else 'twse'
            item = dict(symbol=symbol, date=day, row=list(row), factor=str(factor),
                        sourceUrl=client.records[f'{exchange}-{day[:8] + "01" if exchange == "twii" else day}']['url'],
                        witnesses=witnesses)
            rows.append(item)
            operations.append(item)
        report['days'].append(dict(date=day, addedSymbols=len(rows), adjustedSymbols=sum(Decimal(r['factor']) != 1 for r in rows),
            existingSymbols=existing, excludedOfficialSymbolsNotInArchive=len(set(observations)-set(symbols)),
            comparisonSessions=neighbors, additions=rows, skipped=skipped,
            skippedByReason=dict(Counter(s['reason'] for s in skipped))))
    return report, operations


def apply_backfill(root, operations, rebuild_manifest=True):
    """Recheck all dates before writes: an existing row is never sent for correction."""
    if len({(item['symbol'], item['date']) for item in operations}) != len(operations):
        raise ValueError('Duplicate planned official repair')
    for item in operations:
        if item['row'][0] != item['date']:
            raise ValueError('Official row date mismatch')
        current = archive.symbol_rows(root, item['symbol'], {item['date'][:4]})
        if any(r[0] == item['date'] for r in current):
            raise ValueError('A planned missing date now exists; re-run the dry-run')
        archive.normalize(item['row'])
    with archive.backfill_batch(root) as (conflicts, events):
        for item in operations:
            result = archive.put_symbol(root, item['symbol'], [item['row']], mode='backfill',
                fetched_at=datetime.now(timezone.utc).isoformat(), source_url=item['sourceUrl'],
                provenance=dict(factor=item['factor'], witnesses=item.get('witnesses', [])),
                _conflicts=conflicts, _events=events)
            if result != dict(added=1, corrected=0, conflicts=0, blocked=False):
                raise ValueError('Unexpected official repair write result')
    if rebuild_manifest:
        archive.update_manifest(root)


def removal_backup(root, plan, destination):
    """Stage and validate first; create the complete backup before promoting any byte."""
    root, destination = Path(root).resolve(), outside(root, destination)
    if destination.exists():
        raise ValueError('Backup already exists; refusing to overwrite it')
    if archive.json_read(root/'manifest.json', {}).get('version') != 3:
        raise ValueError('Repair requires the existing v3 per-symbol archive indexes')
    if not plan['guardPassed']:
        raise ValueError('Placeholder count differs from #230 by more than 1%')
    base = git(root, 'rev-parse', 'HEAD')
    before_tree = git(root, 'rev-parse', 'HEAD^{tree}')
    with tempfile.TemporaryDirectory(prefix='official-repair-') as folder:
        stage = Path(folder)/'archive'
        subprocess.run(['git', 'clone', '--quiet', '--shared', '--no-hardlinks', str(root), str(stage)], check=True)
        if git(stage, 'rev-parse', 'HEAD') != base:
            raise ValueError('Archive advanced while preparing the removal')
        archive.remove_verified_placeholders(stage, plan, rebuild_manifest=False)
        archive.verify(stage, base='HEAD', refresh_manifest=True)
        changed = git(stage, 'diff', '--name-only').splitlines()
        images, files = {}, []
        for name in changed:
            before, after = (root/name).read_bytes(), (stage/name).read_bytes()
            images[name] = after
            files.append(dict(path=name, beforeSha256=archive.digest(before), afterSha256=archive.digest(after),
                              beforeGzipBase64=base64.b64encode(gzip.compress(before, mtime=0)).decode()))
        backup = dict(version=1, operation='remove-placeholders-20261023', archiveCommit=base,
                      beforeTree=before_tree, afterTree=worktree_tree(stage),
                      rows=plan['rows'], files=files)
        if git(root, 'rev-parse', 'HEAD') != base or worktree_tree(root) != before_tree:
            raise ValueError('Archive advanced while preparing the removal')
        write_json(destination, backup, exclusive=True)
        archive.replace_repair_images(root, images, {f['path']: {f['beforeSha256']} for f in files})
        if worktree_tree(root) != backup['afterTree']:
            raise ValueError('Promoted removal does not match the validated staging tree')
    return backup


def restore_backup(root, path, apply=False):
    root, path = Path(root).resolve(), outside(root, path)
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('Backup too large')
    backup = json.loads(path.read_text())
    if backup.get('version') != 1 or backup.get('operation') != 'remove-placeholders-20261023':
        raise ValueError('Unknown restoration backup')
    current_tree = worktree_tree(root)
    if current_tree not in {backup['beforeTree'], backup['afterTree']}:
        raise ValueError('Archive advanced since removal; complete tree no longer matches backup')
    original = backup['archiveCommit']
    if len(original) not in (40, 64) or any(c not in '0123456789abcdef' for c in original):
        raise ValueError('Invalid original Git witness')
    if git(root, 'rev-parse', original + '^{tree}') != backup['beforeTree']:
        raise ValueError('Backup does not match its immutable Git witness')
    if current_tree == backup['afterTree'] and git(root, 'rev-parse', 'HEAD') != original:
        if git(root, 'rev-parse', 'HEAD^') != original:
            raise ValueError('Restoration requires the verified removal parent')
    original_blobs = {}
    for entry in git(root, 'ls-tree', '-rz', original).split('\0'):
        if entry:
            metadata, name = entry.split('\t', 1)
            original_blobs[name] = metadata.split()[2]
    object_format = git(root, 'rev-parse', '--show-object-format')
    images, expected, total = {}, {}, 0
    rows = backup['rows']
    if not rows or len({r['path'] for r in rows}) != len(rows):
        raise ValueError('Invalid backup selection')
    allowed = {archive.LOG, 'manifest.json'}
    for item in rows:
        symbol = item['symbol']
        if item['path'] != f'tw/{symbol}/2026.csv' or archive.market_of(symbol) != 'tw':
            raise ValueError('Unsafe backup symbol/path')
        allowed.update((item['path'], 'index/' + archive.instrument_url_path(symbol) + '.json'))
    for item in backup['files']:
        name = item['path']
        if name not in allowed or name in images:
            raise ValueError('Unexpected/duplicate backup path')
        # Bounded streaming decompression, including the journal.
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(item['beforeGzipBase64'], validate=True))) as stream:
            body = stream.read(64 * 1024 * 1024 + 1)
        total += len(body)
        if len(body) > 64 * 1024 * 1024 or total > 128 * 1024 * 1024 or archive.digest(body) != item['beforeSha256']:
            raise ValueError('Invalid backup size/hash')
        if archive._blob_hash(body, object_format) != original_blobs.get(name):
            raise ValueError('Backup file differs from the original Git witness: ' + name)
        if archive.digest((root/name).read_bytes()) not in {item['beforeSha256'], item['afterSha256']}:
            raise ValueError('Archive advanced since removal; refusing restoration: ' + name)
        images[name] = body
        expected[name] = {item['beforeSha256'], item['afterSha256']}
    if not {r['path'] for r in rows} <= images.keys() or not {archive.LOG, 'manifest.json'} <= images.keys():
        raise ValueError('Incomplete restoration backup')
    for item in rows:
        body = images[item['path']]
        if archive.digest(body) != item['beforeSha256']:
            raise ValueError('Backup row/file SHA mismatch')
        import io
        before = archive._read_rows(io.StringIO(body.decode()), item['path'], strict=True)
        target = archive.normalize(item['ohlcv'])
        if (target not in before or target[0] != archive.VERIFIED_PLACEHOLDER_DAY
                or target[5] != '0' or len(set(target[1:5])) != 1):
            raise ValueError('Backup contains a non-placeholder restoration')
        removed = archive.csv_text([r for r in before if r[0] != target[0]]).encode()
        file_record = next(f for f in backup['files'] if f['path'] == item['path'])
        if archive.digest(removed) != file_record['afterSha256']:
            raise ValueError('Backup changes more than the authorized placeholder')
    if apply:
        archive.replace_repair_images(root, images, expected)
    return dict(restoredRows=len(rows), restoredFiles=len(images), byteExact=True)


SAMPLE_LIMIT = 10
SAMPLE_PRIORITY = ('2330.TW', '^TWII')


def _count(value):
    return '-' if value is None else f'{value:,}' if isinstance(value, int) else str(value)


def _factor(value):
    """Display only: eight significant digits; the report keeps the exact factor."""
    return format(Decimal(value).normalize(), '.8g')


def _validation_rows(report):
    before, after = report.get('validationBefore') or {}, report.get('validationAfter') or {}
    rows = []
    for key in ('files', 'rows'):
        b, a = before.get(key), after.get(key)
        delta = f'{a - b:+,}' if isinstance(a, int) and isinstance(b, int) else '-'
        rows.append([key, _count(b), _count(a), delta])
    return rows


def _sample_additions(additions, limit=SAMPLE_LIMIT):
    """Deterministic: 2330.TW and ^TWII first, then adjusted rows (up to half), then the rest."""
    chosen = [a for name in SAMPLE_PRIORITY for a in additions if a['symbol'] == name][:limit]
    adjusted = [a for a in additions if a not in chosen and Decimal(a['factor']) != 1]
    chosen += adjusted[:max(0, (limit - len(chosen)) // 2)]
    chosen += [a for a in additions if a not in chosen][:limit - len(chosen)]
    return chosen


def summary_sections(report):
    """Pure: turn a repair report into [(title, headers, rows)] for logs and the step summary."""
    mode = report.get('mode')
    sections = [('Official archive repair', ['field', 'value'], [
        ['mode', str(mode)], ['apply', str(bool(report.get('apply'))).lower()],
        ['status', str(report.get('status'))], ['archive commit', str(report.get('archiveCommit'))],
        *([['error', str(report['error'])]] if report.get('error') else [])])]
    sections.append(('Validation', ['metric', 'before', 'after', 'delta'], _validation_rows(report)))
    if mode == 'backfill':
        days = report.get('days') or []
        reasons = sorted({r for d in days for r in (d.get('skippedByReason') or {})})
        columns = []
        for d in days:
            additions = d.get('additions')
            added = len(additions) if additions is not None else d.get('addedSymbols', 0)
            adjusted = (sum(Decimal(a['factor']) != 1 for a in additions) if additions is not None
                        else d.get('adjustedSymbols', 0))
            by_reason = d.get('skippedByReason') or {}
            columns.append([added, added - adjusted, adjusted, sum(by_reason.values()),
                            *(by_reason.get(r, 0) for r in reasons), d.get('existingSymbols') or 0])
        labels = ['rows to add', 'of which factor = 1', 'of which factor != 1', 'rows skipped',
                  *(f'skipped: {r}' for r in reasons), 'existing rows kept']
        totals = [sum(c[i] for c in columns) for i in range(len(labels))] if columns else []
        sections.append(('Backfill per date', ['', *(d['date'] for d in days), *(['total'] if days else [])],
                         [[label, *(_count(c[i]) for c in columns), *([_count(totals[i])] if totals else [])]
                          for i, label in enumerate(labels)] if days else []))
        audit = report.get('officialDayAudit')
        if audit:
            sections.append(('Official day audit', ['closed days', 'opened days'],
                             [[', '.join(c['date'] for c in audit.get('closed', [])) or '-',
                               ', '.join(audit.get('opened', [])) or '-']]))
        for d in days:
            samples = _sample_additions(d.get('additions') or [])
            if samples:
                sections.append((f"Sample additions {d['date']} ({len(samples)} of {len(d['additions'])})",
                    ['symbol', 'open', 'high', 'low', 'close', 'volume', 'factor'],
                    [[a['symbol'], *a['row'][1:5], a['row'][5] or '-', _factor(a['factor'])] for a in samples]))
    elif mode == 'remove-placeholders':
        rows = report.get('rows') or []
        groups = Counter((report.get('market', '-'),
                          'tpex' if r['symbol'].endswith('.TWO') else 'twse', r['ohlcv'][0]) for r in rows)
        expected, actual = report.get('expected'), report.get('actual', len(rows))
        sections.append(('Placeholder rows matched for removal', ['market', 'exchange', 'date', 'rows'],
                         [[*k, _count(v)] for k, v in sorted(groups.items())]
                         or [[report.get('market', '-'), '-', report.get('date', '-'), _count(actual)]]))
        diff = f'{(actual - expected) * 100 / expected:+.2f}%' if expected else '-'
        backup = report.get('backupPath')
        sections.append(('Removal totals', ['field', 'value'], [
            ['matched rows', _count(actual)], ['expected rows', _count(expected)], ['difference', diff],
            ['count guard passed', str(report.get('guardPassed')).lower()],
            ['rows preserved (not placeholders)', _count(len(report.get('preserved') or []))],
            ['year files touched', _count(len({r['path'] for r in rows})) if rows else '-'],
            *([['archive files changed (incl. indexes, journal, manifest)', _count(report['changedFiles'])]]
              if 'changedFiles' in report else []),
            ['backup written', f'yes: {backup}' if backup else 'no'],
            *([['backup sha256', str(report.get('backupSha256'))]] if backup else [])]))
    elif mode == 'restore':
        sections.append(('Restore', ['field', 'value'], [
            ['rows restored', _count(report.get('restoredRows'))],
            ['files touched', _count(report.get('restoredFiles'))],
            ['byte exact', str(report.get('byteExact')).lower() if 'byteExact' in report else '-']]))
    return sections


def render_text(sections):
    out = []
    for title, headers, rows in sections:
        widths = [max(len(str(c)) for c in col) for col in zip(headers, *rows)]
        out += ['', f'== {title} ==', '  '.join(h.ljust(w) for h, w in zip(headers, widths)).rstrip(),
                '  '.join('-' * w for w in widths)]
        out += ['  '.join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
    return '\n'.join(out[1:]) + '\n'


def render_markdown(sections):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    out = []
    for title, headers, rows in sections:
        out += [f'### {title}', '', '| ' + ' | '.join(map(cell, headers)) + ' |',
                '|' + '|'.join('---' for _ in headers) + '|',
                *('| ' + ' | '.join(map(cell, r)) + ' |' for r in rows), '']
    return '\n'.join(out) + '\n'


def emit_summary(report, stream=None, environ=None):
    """Reporting only: never changes the repair outcome, even if the summary itself fails."""
    stream, environ = stream or sys.stdout, os.environ if environ is None else environ
    try:
        sections = summary_sections(report)
        stream.write(render_text(sections))
        stream.flush()
        if environ.get('GITHUB_STEP_SUMMARY'):
            with open(environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
                summary.write(render_markdown(sections))
    except Exception as exc:  # pragma: no cover - defensive
        print(f'warning: repair summary unavailable: {exc}', file=sys.stderr)


def run(root, mode, report_path, source_cache, backup_path=None, apply=False, offline=False):
    root = Path(root).resolve()
    report_path, source_cache = outside(root, report_path), outside(root, source_cache)
    backup_path = outside(root, backup_path or report_path.parent / BACKUP_NAME)
    if report_path == backup_path:
        raise ValueError('Report must not overwrite the restoration backup')
    if git(root, 'status', '--porcelain'):
        raise ValueError('Repair requires a clean dedicated archive checkout')
    report = dict(version=1, mode=mode, apply=apply, archiveCommit=git(root, 'rev-parse', 'HEAD'),
                  generatedAt=datetime.now(timezone.utc).isoformat(), status='planning')
    report['validationBefore'] = archive.verify(root)
    try:
        if archive.json_read(root/'manifest.json', {}).get('version') != 3:
            raise ValueError('Repair requires the existing v3 per-symbol archive indexes')
        if mode == 'backfill':
            client = OfficialClient(source_cache, offline)
            audit = client.audit_days()
            detail, operations = plan_backfill(root, client, (*REPAIR_DAYS, *audit['opened']))
            report.update(detail, officialDayAudit=audit, officialSources=list(client.records.values()))
            write_json(report_path, report)
            if apply:
                apply_backfill(root, operations, rebuild_manifest=False)
        elif mode == 'remove-placeholders':
            plan = archive.placeholder_plan(root)
            report.update(plan)
            if not plan['guardPassed']:
                report['status'] = 'stopped-count-guard'
                write_json(report_path, report)
                raise ValueError('Placeholder count differs from #230 by more than 1%')
            if apply:
                backup = removal_backup(root, plan, backup_path)
                report.update(backupPath=str(backup_path), backupSha256=archive.digest(backup_path.read_bytes()),
                              changedFiles=len(backup['files']))
        elif mode == 'restore':
            report.update(restore_backup(root, backup_path, apply))
        else:
            raise ValueError('Unknown repair mode')
        report['validationAfter'] = archive.verify(root, base=report['archiveCommit'] if apply else None,
            refresh_manifest=apply and mode == 'backfill')
        report['status'] = 'applied-locally' if apply else 'dry-run'
    except Exception as exc:
        report['status'] = 'stopped'
        report['error'] = str(exc)
        write_json(report_path, report)
        emit_summary(report)
        raise
    write_json(report_path, report)
    print(json.dumps({k: report[k] for k in ('mode', 'apply', 'status', 'archiveCommit')}))
    emit_summary(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-dir', required=True, type=Path)
    parser.add_argument('--mode', choices=['backfill', 'remove-placeholders', 'restore'], default='backfill')
    parser.add_argument('--report', type=Path, default=Path('docs/research-verification/official-archive-repair.json'))
    parser.add_argument('--source-cache', type=Path, default=Path('.cache/official-repair-sources'))
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    run(args.archive_dir, args.mode, args.report, args.source_cache, args.backup, args.apply, args.offline)


if __name__ == '__main__':
    main()

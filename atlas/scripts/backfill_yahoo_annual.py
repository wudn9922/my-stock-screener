"""Conservative FY2021–FY2025 Yahoo null fills; immutable source snapshot + audit.

Fetch is explicit; default replay is offline. No new rows, EPS or share fills.
"""
import argparse
import copy
import gzip
import hashlib
import json
import math
import subprocess
import tarfile
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

import build_financials as fin

ROOT = Path(__file__).resolve().parents[2]
BLOCKED = set(fin.PER_SHARE_FIELDS)
WINDOWS = ((1609459200, 1640995200), (1640995200, 1798761600))


def encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fetch_sources(directory, output, workers=4):
    """Keep all overlapping fields, including EPS/shares used only as a basis veto."""
    local = threading.local()
    symbols = []
    for path in sorted(directory.glob('*.json')):
        if path.stem == 'index':
            continue
        doc = json.loads(path.read_bytes())
        if any(isinstance(fy, int) and 2021 <= fy <= 2025 for fy in doc.get('annual', {}).get('fy', [])):
            symbols.append((path.stem, doc))
    def fetch(entry):
        symbol, doc = entry
        if not hasattr(local, 'client'):
            local.client = fin.connect_yahoo()
        result = {'symbol': symbol, 'market': doc['market'], 'currency': doc['currency'], 'requests': [], 'annual': {}}
        for start, end in WINDOWS:
            params = {'symbol': symbol, 'type': ','.join('annual' + kind for kind in fin.YAHOO_TYPES),
                      'period1': start, 'period2': end}
            url = fin.TIMESERIES_URL.format(symbol=symbol)
            for attempt in range(3):
                try:
                    payload = local.client.get_json(url, params)
                    if (payload.get('timeseries') or {}).get('error'):
                        raise ValueError('Yahoo timeseries error')
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2 * (attempt + 1))
            parsed = fin.parse_timeseries(payload, date(2026, 10, 23), doc['currency'], doc['market'])
            result['annual'].update(parsed['annual'])
            result['requests'].append({'url': url, 'params': params, 'fetchedAt': datetime.now(timezone.utc).isoformat(),
                                       'payloadSha256': sha(encode(payload)), 'detectedCurrency': parsed['detected']})
            time.sleep(.15)
        return symbol, result
    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(fetch, entry): entry[0] for entry in symbols}
        for n, future in enumerate(as_completed(jobs), 1):
            try:
                symbol, result = future.result()
                results[symbol] = result
            except Exception as exc:
                errors[jobs[future]] = str(exc)
            if n % 100 == 0:
                print(f'fetched {n}/{len(symbols)}, errors {len(errors)}', flush=True)
    snapshot = {'version': 1, 'asOf': '2026-10-23', 'symbols': dict(sorted(results.items())), 'errors': errors}
    output.write_bytes(gzip.compress(encode(snapshot), mtime=0))
    print(json.dumps({'symbols': len(results), 'errors': errors, 'sha256': sha(output.read_bytes())}))
    if errors:
        raise RuntimeError('incomplete snapshot; errors recorded, do not apply')


def close(left, right):
    return abs(left - right) <= .02 * abs(left)


def conflicts(stored, incoming):
    return [{'field': field, 'stored': old, 'yahoo': incoming[field]}
            for field, old in stored.items() if old is not None and incoming.get(field) is not None
            and not close(old, incoming[field])]


def unit_mismatch(old, new):
    if not old or not new or old * new <= 0:
        return False
    exponent = round(math.log10(abs(new / old)))
    return exponent != 0 and abs(new / old / 10 ** exponent - 1) <= .02


def classify(differences, doc, end):
    revisions = (doc.get('quality') or {}).get('revisions') or []
    evidence = []
    for difference in differences:
        matches = [r for r in revisions if r.get('kind') == 'restated' and r.get('period') == 'annual'
                   and r.get('end') == end and r.get('field') == difference['field']
                   and r.get('accn') and r.get('originalAccn') and r.get('new') is not None
                   and close(r['new'], difference['yahoo'])
                   and r.get('filed', '') > r.get('originalFiled', '')]
        if matches:
            evidence.append(matches[-1])
    if len(evidence) == len(differences):
        return 'restated', evidence
    if any(unit_mismatch(d['stored'], d['yahoo']) for d in differences):
        return 'unit-mismatch', []
    return 'unexplained', []


def backfill_document(doc, source):
    result = copy.deepcopy(doc)
    block = result.get('annual') or {}
    fills, skipped = [], []
    ends = block.get('end') or []
    incoming_rows = source.get('annual') or {}
    # Match one-to-one within the existing builder's five-day alignment tolerance.
    matches = {i: [end for end in incoming_rows if abs(fin.days_between(stored, end)) <= fin.SAME_PERIOD_DAYS]
               for i, stored in enumerate(ends)}
    uses = Counter(end for candidates in matches.values() for end in candidates)
    for i, end in enumerate(ends):
        fy = (block.get('fy') or [None] * len(ends))[i]
        if not isinstance(fy, int) or not 2021 <= fy <= 2025:
            continue
        candidates = matches[i]
        base = {'symbol': doc['symbol'], 'market': doc['market'], 'end': end, 'fy': fy}
        if len(candidates) != 1 or uses[candidates[0]] != 1:
            skipped.append({**base, 'reason': 'no-source' if not candidates else 'ambiguous-period'})
            continue
        yahoo_end = candidates[0]
        incoming = incoming_rows[yahoo_end]
        stored = {field: column[i] for field, column in block.get('values', {}).items()}
        differences = conflicts(stored, incoming)
        if differences:
            reason, evidence = classify(differences, doc, end)
            skipped.append({**base, 'yahooEnd': yahoo_end, 'reason': reason, 'differences': differences,
                            **({'secEvidence': evidence} if evidence else {})})
            continue
        row_flags = (block.get('secRestatementIssues') or [None] * len(ends))[i] or []
        basis = (block.get('secFilingBasis') or [None] * len(ends))[i] or {}
        if {'restated', 'mixed-basis'} & set(row_flags) or basis.get('restated'):
            # Equal overlapping numbers do not establish an accession/reporting
            # basis for the missing fields of an explicitly restated row.
            skipped.append({**base, 'yahooEnd': yahoo_end, 'reason': 'basis-unverified',
                            'flags': row_flags, 'secBasis': basis})
            continue
        overlap = sum(value is not None and incoming.get(field) is not None for field, value in stored.items())
        if not overlap:
            skipped.append({**base, 'reason': 'no-overlap'})
            continue
        for field, value in incoming.items():
            if field not in fin.FIELDS or field in BLOCKED or value is None or stored.get(field) is not None:
                continue
            block.setdefault('values', {}).setdefault(field, [None] * len(ends))[i] = value
            maps = block.setdefault('fieldSrc', [None] * len(ends))
            maps[i] = {**(maps[i] or {}), field: 'yahoo'}
            locks = block.setdefault('yahooAnnualBackfill', [None] * len(ends))
            locks[i] = {**(locks[i] or {}), field: value}
            fills.append({**base, 'yahooEnd': yahoo_end, 'field': field, 'old': None, 'new': value})
    if fills:
        annual, _ = fin.read_section(result['annual'])
        quarterly, _ = fin.read_section(result.get('quarterly'))
        for row in annual + quarterly:
            row['fy'] = row['extra'].get('fy')
            row['fq'] = row['extra'].get('fq')
        old_quality = doc.get('quality') or {}
        computed = fin.quality(annual, quarterly, old_quality.get('issues') or [], doc.get('foreignReporter', False))
        # All existing flag and revision objects survive, including unknown future codes.
        result['quality'] = {**old_quality, **computed,
            'issues': list(dict.fromkeys((old_quality.get('issues') or []) + computed['issues']))}
        result['source'] = fin.source_label(annual + quarterly)
    return result, fills, skipped


def validate(before, after):
    """Independent stored-tree constraints, including untouched SEC restatement metadata."""
    if before.get('quarterly') != after.get('quarterly'):
        raise ValueError('quarterly changed')
    for key in set(before) | set(after):
        if key not in {'annual', 'quality', 'source'} and before.get(key) != after.get(key):
            raise ValueError(f'metadata changed: {key}')
    old, new = before.get('annual') or {}, after.get('annual') or {}
    for key in set(old) | set(new):
        if key not in {'values', 'fieldSrc', 'yahooAnnualBackfill'} and old.get(key) != new.get(key):
            raise ValueError(f'annual metadata changed: {key}')
    changes = []
    for field in set(old.get('values', {})) | set(new.get('values', {})):
        for i, end in enumerate(old.get('end', [])):
            previous = old.get('values', {}).get(field, [None] * len(old['end']))[i]
            current = new.get('values', {}).get(field, [None] * len(old['end']))[i]
            if previous == current:
                continue
            if previous is not None or current is None or field in BLOCKED or not 2021 <= old['fy'][i] <= 2025:
                raise ValueError('forbidden value change')
            if new.get('fieldSrc', [None] * len(old['end']))[i].get(field) != 'yahoo':
                raise ValueError('missing field provenance')
            changes.append((before['symbol'], end, field, previous, current))
    oq, nq = before.get('quality') or {}, after.get('quality') or {}
    if not set(oq.get('issues') or []) <= set(nq.get('issues') or []):
        raise ValueError('removed quality flags')
    if oq.get('revisions') != nq.get('revisions'):
        raise ValueError('revisions changed')
    return sorted(changes)


def run(directory, snapshot_path, audit_path, apply=False):
    raw = snapshot_path.read_bytes()
    snapshot = json.loads(gzip.decompress(raw))
    if snapshot.get('errors'):
        raise ValueError('source snapshot has errors')
    if audit_path.exists():
        prior = json.loads(audit_path.read_bytes())
        if prior.get('sourceSha256') == sha(raw) and prior.get('files') and all(
                sha((directory / (entry['symbol'] + '.json')).read_bytes()) == entry['afterSha256']
                for entry in prior['files']):
            print(json.dumps({**prior['summary'], 'alreadyApplied': True}))
            return prior
    plans, fills, skipped = [], [], []
    for path in sorted(directory.glob('*.json')):
        if path.stem == 'index':
            continue
        before_raw = path.read_bytes()
        doc = json.loads(before_raw)
        source = snapshot['symbols'].get(path.stem)
        if source is None:
            if not any(isinstance(fy, int) and 2021 <= fy <= 2025 for fy in doc.get('annual', {}).get('fy', [])):
                continue
            raise ValueError(f'missing source {path.stem}')
        if source['currency'] != doc['currency'] or source['market'] != doc['market']:
            raise ValueError('source currency/market mismatch')
        after, added, excluded = backfill_document(doc, source)
        actual = validate(doc, after)
        if actual != sorted((c['symbol'], c['end'], c['field'], c['old'], c['new']) for c in added):
            raise ValueError('written cells differ from audit')
        fills.extend(added)
        skipped.extend(excluded)
        if added:
            plans.append((path, before_raw, encode(after)))
    counts = Counter((row['market'], row['field']) for row in fills)
    audit = {'version': 1, 'sourceSha256': sha(raw), 'fills': fills, 'skipped': skipped,
             'summary': {'files': len(plans), 'fills': len(fills),
                         'byMarketAndField': {f'{market}.{field}': count for (market, field), count in sorted(counts.items())},
                         'skippedByReason': {reason: sum(row['reason'] == reason for row in skipped) for reason in
                             ('restated', 'unit-mismatch', 'unexplained', 'basis-unverified',
                              'no-source', 'no-overlap', 'ambiguous-period')},
                         'changedNonNull': 0, 'quarterlyChanges': 0, 'removedFlags': 0, 'revisionChanges': 0}}
    growth_plan = None
    if (directory.parent / 'growth.json').exists():
        from apply_sec_restatements import rebuild_growth
        growth_before = (directory.parent / 'growth.json').read_bytes()
        growth_after, growth_changes = rebuild_growth(directory.parent,
            {path.stem: json.loads(after) for path, _, after in plans},
            datetime(2026, 10, 23, tzinfo=timezone.utc))
        audit['growthChanges'] = growth_changes
        if growth_changes:
            growth_plan = (directory.parent / 'growth.json', growth_before, encode(growth_after))
    audit['files'] = [{'symbol': path.stem, 'beforeSha256': sha(before), 'afterSha256': sha(after)}
                      for path, before, after in plans]
    # Preflight the entire batch before the first permanent write; roll back on error.
    if apply:
        audit_before = audit_path.read_bytes() if audit_path.exists() else None
        if growth_plan:
            plans.append(growth_plan)
        try:
            for path, before, after in plans:
                if path.read_bytes() != before:
                    raise ValueError('financials changed during preflight')
                fin.write_atomic(str(path), json.loads(after))
            fin.write_atomic(str(audit_path), audit)
        except BaseException:
            for path, before, _ in plans:
                path.write_bytes(before)
            if audit_before is None:
                audit_path.unlink(missing_ok=True)
            else:
                audit_path.write_bytes(audit_before)
            raise
    else:
        fin.write_atomic(str(audit_path), audit)
    print(json.dumps(audit['summary']))
    return audit


def verify_against(revision, directory, snapshot_path, audit_path):
    """Read the original Git tree, re-plan independently and compare every stored file."""
    from apply_sec_restatements import hashes, protected_hashes as sec_protected_hashes, rebuild_growth
    def protected_hashes(report_dir):
        protected = sec_protected_hashes(report_dir)
        for pattern in ('*classification*', '*acceptance-ledger*'):
            protected.update({path.name: sha(path.read_bytes()) for path in report_dir.glob(pattern) if path.is_file()})
        return protected
    audit = json.loads(audit_path.read_bytes())
    snapshot = json.loads(gzip.decompress(snapshot_path.read_bytes()))
    if audit['sourceSha256'] != sha(snapshot_path.read_bytes()):
        raise ValueError('snapshot does not match audit')
    paths = ['docs/report/financials', 'docs/report/fundamentals', 'docs/report/universe.json',
             'docs/report/growth.json', 'docs/report/q4-8k', 'docs/report/q4-eps-missing-classification.json',
             'docs/report/q4-eps-missing-classification.md', 'docs/report/q4-eps-blocked-classification.json', 'docs/report/q4-eps-acceptance-ledger.json',
             'docs/report/q4-eps-acceptance-ledger.md', 'docs/report/sec-restated-comparatives-apply.json',
             'docs/report/sec-restated-comparatives.json']
    paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', revision, '--', *paths],
                                    cwd=ROOT, text=True).splitlines()
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / 'baseline.tar'
        with archive.open('wb') as stream:
            subprocess.run(['git', 'archive', revision, '--', *paths], cwd=ROOT, stdout=stream, check=True)
        with tarfile.open(archive) as tree:
            tree.extractall(tmp, filter='data')
        baseline = Path(tmp) / 'docs/report'
        old_hashes, new_hashes = hashes(baseline / 'financials'), hashes(directory)
        if set(old_hashes) != set(new_hashes):
            raise ValueError('financial files added or removed')
        actual, expected, skipped, proposals = [], [], [], {}
        for name in old_hashes:
            before = json.loads((baseline / 'financials' / name).read_bytes())
            after = json.loads((directory / name).read_bytes())
            if name == 'index.json':
                if old_hashes[name] != new_hashes[name]:
                    raise ValueError('index changed')
                continue
            actual.extend(validate(before, after))
            source = snapshot['symbols'].get(Path(name).stem, {'annual': {}})
            candidate, fills, excluded = backfill_document(before, source)
            expected.extend((c['symbol'], c['end'], c['field'], c['old'], c['new']) for c in fills)
            skipped.extend(excluded)
            if after != candidate:
                raise ValueError(f'written document differs from replay: {name}')
            if before != after:
                proposals[before['symbol']] = after
        audited = [(c['symbol'], c['end'], c['field'], c['old'], c['new']) for c in audit['fills']]
        if sorted(actual) != sorted(expected) or sorted(actual) != sorted(audited) or skipped != audit['skipped']:
            raise ValueError('written diff or skipped-period log differs from replay')
        protected = protected_hashes(baseline)
        if protected != protected_hashes(directory.parent):
            raise ValueError('Q4 protected data changed')
        for name in ['sec-restated-comparatives.json', 'sec-restated-comparatives-apply.json']:
            if (baseline / name).read_bytes() != (directory.parent / name).read_bytes():
                raise ValueError('SEC restatement manifest changed')
        growth, changes = rebuild_growth(baseline, proposals, datetime(2026, 10, 23, tzinfo=timezone.utc))
        if growth != json.loads((directory.parent / 'growth.json').read_bytes()):
            raise ValueError('growth output differs from independent rebuild')
        proof = {'base': revision, 'financialFiles': len(old_hashes), 'filledCells': len(actual),
                 'changedFiles': len(proposals), 'changedNonNull': 0, 'quarterlyChanges': 0,
                 'removedFlags': 0, 'revisionChanges': 0, 'secRestatedValueChanges': 0,
                 'protectedFiles': protected, 'growthChanges': changes, 'exactReplay': True,
                 'sourceSha256': audit['sourceSha256']}
        return proof


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--financials-dir', type=Path, default=ROOT / 'docs/report/financials')
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'docs/report/yahoo-annual-sources-20261023.json.gz')
    parser.add_argument('--audit', type=Path, default=ROOT / 'docs/report/yahoo-annual-backfill.json')
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--verify-against')
    parser.add_argument('--proof', type=Path, default=ROOT / 'docs/report/yahoo-annual-backfill-proof.json')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args(argv)
    if args.verify_against:
        proof = verify_against(args.verify_against, args.financials_dir, args.snapshot, args.audit)
        args.proof.write_bytes(encode(proof))
        print(json.dumps({**{k: v for k, v in proof.items() if k not in {'protectedFiles', 'growthChanges'}},
                          'protectedFiles': len(proof['protectedFiles']), 'growthChanges': len(proof['growthChanges'])}))
    elif args.fetch:
        fetch_sources(args.financials_dir, args.snapshot, args.workers)
    else:
        run(args.financials_dir, args.snapshot, args.audit, args.apply)


if __name__ == '__main__':
    main()

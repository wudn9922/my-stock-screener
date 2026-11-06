#!/usr/bin/env python3
"""Read-only archive scan: report suspected closure rows; never repair/publish prices."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
import time

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from market_sessions import CLOSURE_SHARE, archive_primary, classify_closures, read_policy


def scan_archive(root, now=None):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    stats, affected, samples = {}, {}, {}
    totals, errors, invalid_rows = Counter(), [], 0
    for market in ('tw', 'us'):
        daily = defaultdict(lambda: [0, 0, 0])
        affected[market], samples[market] = Counter(), defaultdict(list)
        for document in sorted((root / 'index' / market).glob('*.json')):
            meta = json.loads(document.read_text())
            symbol = meta['symbol']
            if '/' in symbol or '\\' in symbol or symbol in ('.','..'):
                raise ValueError('Unsafe archive index symbol')
            totals['symbols'] += 1
            for year, detail in sorted(meta['years'].items()):
                if not year.isdigit() or len(year) != 4:
                    raise ValueError('Unsafe archive year')
                path = root / market / symbol / (year + '.csv')
                # Consume each CSV once. No mutation helpers or archive git publisher.
                with path.open(newline='') as file:
                    rows = csv.DictReader(file)
                    if rows.fieldnames != ['Date','Open','High','Low','Close','Volume']:
                        raise ValueError(f'Invalid archive header: {path}')
                    count = 0
                    for row in rows:
                        day = row['Date']
                        if len(day) != 10 or not day.startswith(year + '-'):
                            raise ValueError(f'Invalid archive date: {path}')
                        count += 1
                        price = [float(row[k]) for k in ('Open','High','Low','Close')]
                        if not all(math.isfinite(v) and v > 0 for v in price) or not price[2] <= min(price[0],price[3]) <= max(price[0],price[3]) <= price[1]:
                            invalid_rows += 1
                            if len(errors) < 20:
                                errors.append(f'{market}/{symbol}/{day}: invalid OHLC')
                            continue
                        # An index/ETF can have a suspect row, but never increases
                        # the ordinary-stock denominator used to infer a closure.
                        affected[market][day] += 1
                        if len(samples[market][day]) < 5:
                            samples[market][day].append(symbol)
                        if meta.get('kind') != 'stock':
                            continue
                        volume = float(row['Volume']) if row['Volume'] else None
                        known = volume is not None and math.isfinite(volume) and volume >= 0
                        daily[day][0] += 1
                        daily[day][1] += int(known and volume == 0 and len(set(price)) == 1)
                        daily[day][2] += int(not known)
                    if count != detail['rows']:
                        raise ValueError(f'Archive index row mismatch: {path}')
                    totals['rows'] += count
                    totals['yearFiles'] += 1
            if totals['symbols'] % 500 == 0:
                print(f'Closure scan: {totals["symbols"]} symbols, {totals["rows"]} rows, {time.monotonic()-started:.1f}s', flush=True)
        frame = pd.DataFrame.from_dict(daily, orient='index', columns=['observed','flatZero','unknownVolume'])
        frame.index = pd.to_datetime(frame.index)
        stats[market] = frame.sort_index()
    markets = {}
    for market in ('tw','us'):
        primary = archive_primary(root, market, now)
        records = classify_closures(stats[market], primary, min_symbols=50)
        for day, row in records.items():
            row.update(affectedRows=affected[market][day], sampleSymbols=samples[market][day])
        markets[market] = dict(primary=None if primary is None else {k:v for k,v in primary.items() if k != 'dates'},
                               closedSessions=list(records.values()))
    try:
        commit = subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):
        commit = None
    rows = [r for m in markets.values() for r in m['closedSessions']]
    return dict(version=1, generatedAt=now.isoformat(), threshold=CLOSURE_SHARE,
                method='primary-index-or-flat-zero-v1', markets=markets,
                scan=dict(archiveCommit=commit, **dict(totals), invalidRows=invalid_rows, errors=errors,
                          suspectedDatesWithRows=sum(r['affectedRows'] > 0 for r in rows),
                          suspectedRows=sum(r['affectedRows'] for r in rows), seconds=round(time.monotonic()-started,3)))


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    body = json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+'\n'
    temp = path.with_suffix('.tmp')
    temp.write_text(body)
    temp.replace(path)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-dir',required=True)
    parser.add_argument('--out',required=True)
    parser.add_argument('--cache-policy',help='Optional derived metadata copy; never archive prices')
    parser.add_argument('--frontend-seed',help='Optional verified legacy dates for offline readers')
    args=parser.parse_args(argv)
    root, destination=Path(args.archive_dir).resolve(),Path(args.out).resolve()
    if any(Path(p).resolve().is_relative_to(root) for p in [args.out,args.cache_policy,args.frontend_seed] if p):
        raise ValueError('Read-only scan cannot write any output inside the archive')
    report=scan_archive(root)
    if args.cache_policy and report['scan']['invalidRows']:
        raise ValueError('Invalid archive rows; retaining the previous runtime closure policy')
    if args.cache_policy:
        # A just-observed emergency closure can be newer than the archive's
        # bounded index coverage. Do not erase its already-published evidence.
        previous = read_policy(args.cache_policy)
        for market, part in report['markets'].items():
            records = {r['date']:r for r in part['closedSessions']}
            for day, row in previous.get(market,{}).items():
                if day not in records or not records[day]['observedSymbols']:
                    records[day] = {**row,'reasons':list(dict.fromkeys([*row['reasons'],'published-closure']))}
            part['closedSessions'] = [records[day] for day in sorted(records)]
    write_report(destination,report)
    if args.cache_policy:
        write_report(args.cache_policy,report)
    if args.frontend_seed:
        seed=dict(version=1,threshold=CLOSURE_SHARE,archiveCommit=report['scan']['archiveCommit'],
                  markets={m:dict(closedSessions=[r for r in v['closedSessions'] if r['affectedRows']]) for m,v in report['markets'].items()})
        write_report(args.frontend_seed,seed)
    print(json.dumps(report['scan'],ensure_ascii=False),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

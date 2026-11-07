#!/usr/bin/env python3
"""Read-only archive scan: report suspected closure rows; never repair/publish prices."""
from __future__ import annotations

import argparse
from bisect import bisect_right
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
from market_sessions import (CALENDARS, CLOSURE_SHARE, archive_primary, archive_reference,
                             classify_closures, read_policy, read_trading_policy)


def completed_days(market, start, now):
    import exchange_calendars as xcals
    calendar = xcals.get_calendar(CALENDARS[market], start=pd.Timestamp(start)-pd.Timedelta(days=7),
                                 end=pd.Timestamp(now.date())+pd.Timedelta(days=7))
    return [label.strftime('%Y-%m-%d') for label in calendar.sessions
            if label.strftime('%Y-%m-%d') >= start and calendar.session_close(label).timestamp() <= now.timestamp()]


def data_gaps(market, stats, starts, primary, closed, official, now, start='2021-01-01'):
    """Eligibility begins at the first archived bar, avoiding future IPOs in denominators.

    An archived firstDate is a coverage bound, not proof of a historical listing
    date. Missing leading history and delistings cannot be resolved by this scan.
    """
    starts = sorted(starts)
    if primary is None and not starts and not official:
        return dict(status='unavailable', reason='沒有可驗證的主要指數或股票目錄', sessions=[])
    present = {day.strftime('%Y-%m-%d'): int(row.observed) for day, row in stats.iterrows()}
    records = []
    days = completed_days(market, start, now)
    for day in days:
        if day in closed:
            continue
        eligible = bisect_right(starts, day)
        observed = present.get(day, 0)
        missing = max(0, eligible-observed)
        share = missing/eligible if eligible else None
        index_missing = day not in primary['dates'] if primary else None
        reasons = []
        if index_missing:
            reasons.append('primary-index-missing')
        if share is not None and share > .5:
            reasons.append('majority-stock-rows-missing')
        if reasons:
            position = ('primary-unavailable' if primary is None else 'before-primary-coverage'
                        if day < primary['firstDate'] else 'after-primary-coverage'
                        if day > primary['lastDate'] else 'within-primary-coverage')
            records.append(dict(date=day, reason='missing-session-data', reasons=reasons, calendar=CALENDARS[market],
                calendarSession=True, primaryIndexMissing=index_missing, eligibleSymbols=eligible,
                observedSymbols=observed, missingSymbols=missing, missingShare=share,
                coveragePosition=position, officialSources=official.get(day, [])))
    return dict(status='ok' if primary else 'partial', startDate=start, through=days[-1] if days else None,
                calendar=CALENDARS[market], missingStockThreshold=.5, comparison='strictly-greater',
                eligibility='ordinary-stock archive firstDate <= session; no assumed pre-firstDate history',
                primaryFirstDate=primary['firstDate'] if primary else None,
                primaryLastDate=primary['lastDate'] if primary else None,
                reason=None if primary else '主要指數無法驗證；只列可證實的股票缺漏', sessions=records)


def scan_archive(root, now=None, previous=None, trading=None):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    stats, affected, samples, starts, official = {}, {}, {}, {}, {}
    totals, errors, invalid_rows = Counter(), [], 0
    for market in ('tw', 'us'):
        daily = defaultdict(lambda: [0, 0, 0, 0])
        starts[market], official[market] = [], defaultdict(list)
        affected[market], samples[market] = Counter(), defaultdict(list)
        for document in sorted((root / 'index' / market).glob('*.json')):
            meta = json.loads(document.read_text())
            symbol = meta['symbol']
            if '/' in symbol or '\\' in symbol or symbol in ('.','..'):
                raise ValueError('Unsafe archive index symbol')
            totals['symbols'] += 1
            if meta.get('kind') == 'stock':
                starts[market].append(meta['firstDate'])
            verified_official = (symbol == '^TWOII' and str(meta.get('source', '')).startswith('TPEx official cash-index OHLC')
                                 and archive_reference(root, market, symbol, now) is not None)
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
                        if verified_official:
                            official[market][day].append(dict(symbol=symbol, source=meta['source'],
                                ohlc=dict(zip(('open','high','low','close'), price))))
                        if meta.get('kind') != 'stock':
                            continue
                        volume = float(row['Volume']) if row['Volume'] else None
                        known = volume is not None and math.isfinite(volume) and volume >= 0
                        daily[day][0] += 1
                        daily[day][1] += int(known and volume == 0 and len(set(price)) == 1)
                        daily[day][2] += int(not known)
                        daily[day][3] += int(known and volume > 0)
                    if count != detail['rows']:
                        raise ValueError(f'Archive index row mismatch: {path}')
                    totals['rows'] += count
                    totals['yearFiles'] += 1
            if totals['symbols'] % 500 == 0:
                print(f'Closure scan: {totals["symbols"]} symbols, {totals["rows"]} rows, {time.monotonic()-started:.1f}s', flush=True)
        frame = pd.DataFrame.from_dict(daily, orient='index', columns=['observed','flatZero','unknownVolume','positiveVolume'])
        frame.index = pd.to_datetime(frame.index)
        stats[market] = frame.sort_index()
    markets = {}
    for market in ('tw','us'):
        primary = archive_primary(root, market, now)
        reviews, missing = {}, {}
        records = classify_closures(stats[market], primary, (previous or {}).get(market,{}), min_symbols=50,
            reviews=reviews, trading=(trading or {}).get(market,{}), market=market,
            official=official[market], missing=missing)
        for day, row in records.items():
            row.update(affectedRows=affected[market][day], sampleSymbols=samples[market][day])
        markets[market] = dict(primary=None if primary is None else {k:v for k,v in primary.items() if k != 'dates'},
                               closedSessions=list(records.values()), indexGapsWithTrading=list(reviews.values()),
                               missingSessions=list(missing.values()),
                               dataGaps=data_gaps(market,stats[market],starts[market],primary,records,official[market],now))
    try:
        commit = subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):
        commit = None
    rows = [r for m in markets.values() for r in m['closedSessions']]
    return dict(version=1, generatedAt=now.isoformat(), threshold=CLOSURE_SHARE,
                method='calendar-session-guard-v3', markets=markets,
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
    parser.add_argument('--missing-out',help='Separate read-only session-data gap report (2021 onward)')
    args=parser.parse_args(argv)
    root, destination=Path(args.archive_dir).resolve(),Path(args.out).resolve()
    if any(Path(p).resolve().is_relative_to(root) for p in [args.out,args.cache_policy,args.frontend_seed,args.missing_out] if p):
        raise ValueError('Read-only scan cannot write any output inside the archive')
    previous = read_policy(args.cache_policy) if args.cache_policy else {}
    prior_reviews = read_trading_policy(args.cache_policy) if args.cache_policy else {}
    report=scan_archive(root,previous=previous,trading=prior_reviews)
    if args.cache_policy and report['scan']['invalidRows']:
        raise ValueError('Invalid archive rows; retaining the previous runtime closure policy')
    if args.cache_policy:
        # A just-observed emergency closure can be newer than the archive's
        # bounded index coverage. Do not erase its already-published evidence.
        for market, part in report['markets'].items():
            records = {r['date']:r for r in part['closedSessions']}
            traded = {r['date']:r for r in part['indexGapsWithTrading']}
            # Current stock trading evidence corrects a previously inferred closure.
            for day, row in previous.get(market,{}).items():
                if day not in records:
                    continue
                if not records[day]['observedSymbols']:
                    records[day] = {**row,'reasons':list(dict.fromkeys([*row['reasons'],'published-closure']))}
            part['closedSessions'] = [records[day] for day in sorted(records)]
            part['indexGapsWithTrading'] = list({**prior_reviews.get(market,{}),**traded}.values())
    write_report(destination,report)
    if args.cache_policy:
        write_report(args.cache_policy,report)
    if args.frontend_seed:
        seed=dict(version=1,threshold=CLOSURE_SHARE,archiveCommit=report['scan']['archiveCommit'],
                  markets={m:dict(closedSessions=[r for r in v['closedSessions'] if r['affectedRows']],
                                  missingSessions=v['missingSessions']) for m,v in report['markets'].items()})
        write_report(args.frontend_seed,seed)
    if args.missing_out:
        write_report(args.missing_out, gap_report(report))
    print(json.dumps(report['scan'],ensure_ascii=False),flush=True)
    return 0


def gap_report(report):
    markets = {m:part['dataGaps'] for m,part in report['markets'].items()}
    return dict(version=1, generatedAt=report['generatedAt'], archiveCommit=report['scan']['archiveCommit'],
                summary={m:dict(dates=len(part['sessions']), missingStockRows=sum(r['missingSymbols'] for r in part['sessions']),
                                interiorDates=sum(r['coveragePosition']=='within-primary-coverage' for r in part['sessions']),
                                tailDates=sum(r['coveragePosition']=='after-primary-coverage' for r in part['sessions']))
                         for m,part in markets.items()}, markets=markets,
                limitations=['Current archive constituents have survivorship bias.',
                              'A first archived date is not a verified IPO date; missing leading history is not inferred.',
                              'Stocks whose history ends remain eligible; missing rows may include suspensions or delistings.',
                              'Confirmed closures are excluded; all price files remain unchanged.'])


if __name__ == '__main__':
    raise SystemExit(main())

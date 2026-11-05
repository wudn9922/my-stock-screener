"""Archive-only ETF/index daily cache. Reuses the existing Yahoo batch/rate-limit/validation reader.

No changes to the screener's stock universe, breadth, main records or monitor. All history merges
remain append-only at the archive publisher. This module produces transport files; never pushes.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import build_universe_full as bu
import kline_archive as archive
import kline_instruments as instruments
import kline_integrity as integrity
import universe_cache as uc
import archive_tpex_index


@contextmanager
def disposable_quality_log(cache):
    previous = os.environ.get('KLINE_CORRECTIONS_LOG')
    previous_path = integrity.LOG_PATH
    quality_path = Path(cache) / 'quality.json'
    # Keep the accepted quarantine exclusions while directing every new diagnostic
    # to disposable cache. Never modify the permanent main correction journal.
    accepted = integrity.read_log(previous_path)
    local = integrity.read_log(quality_path)
    integrity.write_log(quality_path, integrity.merge_logs(accepted, local))
    os.environ['KLINE_CORRECTIONS_LOG'] = str(quality_path)
    integrity.LOG_PATH = quality_path
    integrity._load_log.cache_clear()
    try:
        yield
    finally:
        integrity.LOG_PATH = previous_path
        integrity._load_log.cache_clear()
        if previous is None: os.environ.pop('KLINE_CORRECTIONS_LOG', None)
        else: os.environ['KLINE_CORRECTIONS_LOG'] = previous


def seed_selected(archive_dir, cache_dir, selected, state, now):
    count = 0
    for symbol in selected:
        path = uc.bars_path(str(cache_dir), symbol)
        old = uc.read_bars(path)
        if uc.is_complete(state['symbols'].get(symbol)) and not old.empty:
            continue
        rows = archive.latest_rows(archive_dir, symbol, uc.KEEP_BARS)
        rows = [r for r in rows if uc.session_done(r[0], 'TW' if archive.market_of(symbol) == 'tw' else 'US', now)]
        if not rows: continue
        frame = uc.frame_from_rows(rows)
        frame = uc.write_bars(path, frame, now=now)
        uc.record_frame(state, symbol, frame, now.date().isoformat(), full=True, verified=True)
        count += 1
    return count


def refresh(archive_dir, cache_dir, budget=300, now=None, net=None, selected=None,
            reports=None, getter=instruments.get_text, clock=time.monotonic, sleep=time.sleep):
    if not 30 <= budget <= 1800:
        raise ValueError('Instrument request budget must be 30..1800 seconds')
    now = now or datetime.now(timezone.utc)
    cache_dir = Path(cache_dir)
    with disposable_quality_log(cache_dir):
        args = bu.parse_args(['--cache-dir', str(cache_dir), '--budget', str(budget)])
        job = bu.Run(args, net=net, now=now, clock=clock, sleep=sleep)
        if selected is None:
            prior = archive.json_read(Path(archive_dir) / instruments.INSTRUMENTS_STATE, {}).get('symbols', {})
            selected, warnings = instruments.scope(reports=reports, previous=prior, getter=getter)
        else:
            warnings = []
        archive.json_write(cache_dir / 'scope.json', {'version': 1, 'symbols': selected})
        state = uc.load_state(str(cache_dir))
        seeded = seed_selected(archive_dir, cache_dir, selected, state, now)
        uc.save_state(str(cache_dir), state)
        counts = {'selected': len(selected), 'seeded': seeded, 'appended': 0, 'charts': 0, 'unchanged': 0, 'failed': 0}
        print(f'Archive instruments: batch start selected={len(selected)} seeded={seeded}', flush=True)
        if not selected: return {**counts, 'warnings': warnings}
        yahoo_symbols = sorted(s for s in selected if s != '^TWOII')
        yahoo_ready = not yahoo_symbols or job.connect_yahoo()
        if not yahoo_ready: warnings.append('Yahoo unavailable; keep accepted cache and archive')
        quotes = job.quote_bars(yahoo_symbols, 'Archive ETFs/indices') if yahoo_symbols and yahoo_ready else {}
        ordered = sorted(selected, key=lambda s: (state['symbols'].get(s, {}).get('lastAttemptAt', ''), s))
        for index, symbol in enumerate(ordered, 1):
            if job.time_left() < 30: break
            entry = uc.entry_of(state, symbol)
            if not uc.can_retry(entry, now.date().isoformat()): continue
            entry['lastAttemptAt'] = now.isoformat()
            path = uc.bars_path(str(cache_dir), symbol)
            old = uc.read_bars(path)
            market = 'TW' if archive.market_of(symbol) == 'tw' else 'US'
            bar = quotes.get(symbol)
            try:
                if symbol != '^TWOII' and not yahoo_ready:
                    counts['failed'] += 1
                    continue
                if bar and not old.empty and bar['date'] <= entry.get('last', ''):
                    counts['unchanged'] += 1
                    continue
                frame = None
                if symbol == '^TWOII':
                    start = old.index[-1].date() if not old.empty else now.date().replace(year=now.year-2, day=1)
                    fresh = archive_tpex_index.history(start, now, job.net, time_left=job.time_left, sleep=sleep)
                    frame = uc.merge_incremental(old, fresh) if not old.empty else fresh
                if bar and not old.empty and uc.is_complete(entry) and uc.session_done(bar['date'], market, now):
                    bar = {**bar, 'reference': bar.get('previousClose')}
                    action, row = uc.snapshot_action(entry, bar, uc.TradingCalendar([]), market)
                    if action == 'append' and row and not integrity.violation(dict(zip(uc.PRICE_COLUMNS, row[1:]))):
                        frame = uc.append_row(old, row)
                if frame is None:
                    fresh = job.fetch_chart(symbol, bu.FULL_RANGE if old.empty else '1mo', completed_market=market)
                    counts['charts'] += 1
                    if fresh.empty:
                        raise ValueError('no data from source: no valid completed sessions')
                    frame = uc.merge_incremental(old, fresh) if not old.empty else fresh
                if frame.empty:
                    raise ValueError('no data from source: no valid completed sessions')
                frame = uc.write_bars(path, frame, now=now)
                uc.record_frame(state, symbol, frame, now.date().isoformat(), full=not uc.is_complete(entry), verified=True)
                counts['appended'] += 1
            except Exception as exc:
                uc.record_failure(state, symbol, now.date().isoformat(), type(exc).__name__)
                counts['failed'] += 1
                if isinstance(exc, bu.RateLimited): break
            finally:
                if index % 50 == 0: uc.save_state(str(cache_dir), state)
                sleep(bu.REQUEST_DELAY)
                if index % 50 == 0 or index == len(ordered):
                    print(f'Archive instruments: {index}/{len(ordered)} counts={counts}', flush=True)
        uc.save_state(str(cache_dir), state)
        return {**counts, 'warnings': warnings}


def stage(archive_dir, cache_dir, payload_dir, previous_payload=None, now=None):
    plan = archive.stage_cache(cache_dir, archive_dir, payload_dir, now=now, previous=previous_payload)
    plan['instruments'] = archive.json_read(Path(cache_dir) / 'scope.json', {}).get('symbols', {})
    archive.json_write(Path(payload_dir) / 'operations.json', plan)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-dir', required=True)
    parser.add_argument('--cache-dir', default='.cache/universe-full/archive-instruments')
    parser.add_argument('--report-dir')
    parser.add_argument('--refresh-public-reports', action='store_true')
    parser.add_argument('--budget', type=float, default=300)
    parser.add_argument('--stage-only', action='store_true')
    parser.add_argument('--payload-dir')
    parser.add_argument('--previous-payload')
    args = parser.parse_args()
    if args.stage_only:
        if not args.payload_dir: parser.error('--stage-only requires --payload-dir')
        stage(args.archive_dir, args.cache_dir, args.payload_dir, args.previous_payload)
        return
    if args.refresh_public_reports:
        args.report_dir = args.report_dir or str(Path(args.cache_dir) / 'references')
        failures = instruments.restore_public_reports(args.report_dir)
        print('Archive public reference files unavailable:', ','.join(failures) or 'none', flush=True)
    started = time.perf_counter()
    # bu.Run never creates its own Network (bu.run() does); without one every Yahoo symbol fails.
    result = refresh(args.archive_dir, args.cache_dir, args.budget, net=bu.Network(), reports=args.report_dir)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    print(f'K-line timing: instruments.refresh: {time.perf_counter()-started:.3f}s', flush=True)

if __name__ == '__main__': main()

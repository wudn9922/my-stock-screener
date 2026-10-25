"""Prepare a reviewable recovery of a closure-erased public market snapshot.

Reads existing public snapshots and local OHLCV only; no network, messaging,
source-cache writes or publication. --out must be a separate review directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import report_metrics as rm
from scripts import build_breadth as breadth
from scripts.market_sessions import market_prices


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def restore(current_dir, previous_dir, data_dir, out_dir, market='TW'):
    if market != 'TW':
        raise ValueError('This recovery validates the Taiwan full-market cohort only')
    current_dir, previous_dir, data_dir, out_dir = map(Path, (current_dir, previous_dir, data_dir, out_dir))
    if any(path.resolve() == out_dir.resolve() or path.resolve() in out_dir.resolve().parents
           for path in (current_dir, previous_dir, data_dir)):
        raise ValueError('Recovery output must be separate from every input directory')
    key, group_key = 'tw', 'tw_all'
    latest, width = read(current_dir / 'latest.json'), read(current_dir / 'breadth.json')
    previous = read(previous_dir / 'latest.json')
    group = next(g for g in latest['groups'] if g['key'] == group_key)
    saved = next(g for g in previous['groups'] if g['key'] == group_key)
    if group['items'] or width['markets'][key]['segments']['all']['count']:
        raise ValueError('Refusing to replace a nonempty current scan or breadth snapshot')
    if not saved['items'] or group.get('maList') != saved.get('maList'):
        raise ValueError('Previous scan is empty or uses different moving-average parameters')
    files = list(data_dir.glob('*.csv'))
    symbols = breadth.select_tw_symbols([p.name for p in files])
    frames = {s: rm.read_price_csv(data_dir / f'{s}.csv') for s in symbols}
    frames, excluded = market_prices(frames)
    current_day = width['markets'][key]['asOf']
    if current_day not in excluded:
        raise ValueError('The empty current snapshot is not explained by a confirmed market closure')
    target = max(Counter(f.index[-1].strftime('%Y-%m-%d') for f in frames.values()).items(), key=lambda x: (x[1], x[0]))[0]
    if {r.get('asOf') for r in saved['items']} != {target}:
        raise ValueError('Saved scan does not match the latest observed trading session')
    items, rejected, sources = [], [], {}
    for old in saved['items']:
        symbol = old['symbol']
        frame = frames.get(symbol)
        if frame is None or frame.empty or frame.index[-1].strftime('%Y-%m-%d') != target:
            rejected.append({'symbol': symbol, 'reason': 'No validated current history on the saved session'})
            continue
        close, volume = float(frame['Close'].iloc[-1]), float(frame['Volume'].iloc[-1])
        if abs(close - old['close']) > max(1e-5, close * 1e-6) or abs(volume - old['volume']) > 1:
            rejected.append({'symbol': symbol, 'reason': 'Saved close or volume disagrees with validated history'})
            continue
        item = copy.deepcopy(old)
        # This field was introduced after the retained snapshot. Calculate the
        # real complete-window mean; never substitute daily volume or infer it.
        item.setdefault('metrics', {})['volumeAvg20'] = rm.compute_stock_metrics(frame)['volumeAvg20']
        items.append(item)
        path = data_dir / f'{symbol}.csv'
        sources[symbol] = digest(path)
    if not items:
        raise ValueError('No saved rows passed current OHLCV validation')
    group['items'], group['count'], group['unmatchedCount'] = items, len(items), 0
    segments = {s: 'tpex' if s.endswith('.TWO') else 'twse' for s in frames}
    rebuilt = breadth.build_market(frames, width['markets'][key]['universe'], segments)
    if not rebuilt or rebuilt['asOf'] != target or not rebuilt['segments']['all']['count']:
        raise ValueError('Rebuilt breadth does not have the expected valid session')
    width['markets'][key] = rebuilt
    receipt = {
        'preparedAt': datetime.now(timezone.utc).isoformat(), 'market': market,
        'replacedPlaceholderDate': current_day, 'asOf': target,
        'restoredScanRows': len(items), 'rejectedRows': rejected,
        'breadth': rebuilt['segments'], 'nonTradingDates': sorted(excluded),
        'inputs': {str(p): digest(p) for p in [current_dir / 'latest.json', current_dir / 'breadth.json', previous_dir / 'latest.json']},
        'rawCsvSha256': sources,
        'method': 'Retain original validated saved rows; calculate only the new volumeAvg20 field; rebuild breadth with current session projection',
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in [('latest.json', latest), ('breadth.json', width), ('recovery-proof.json', receipt)]:
        (out_dir / name).write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current', type=Path, required=True)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--market', choices=['TW'], default='TW')
    args = parser.parse_args()
    result = restore(args.current, args.previous, args.data_dir, args.out, args.market)
    print(json.dumps({k: result[k] for k in ['market', 'asOf', 'restoredScanRows', 'rejectedRows', 'breadth']}, ensure_ascii=False))

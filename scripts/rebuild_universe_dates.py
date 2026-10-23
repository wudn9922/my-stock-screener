"""Rebuild Taiwan rows from permanent CSVs and recover verified US row dates.

No permanent K-lines are written. Unverifiable legacy dates stay null rather than
being inferred from the market's modal date.
"""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
import report_metrics as metrics
import kline_integrity as kline
from research_store import MarketStore


def rebuild(report=ROOT / 'docs/report', data=ROOT / 'data'):
    report, data = Path(report), Path(data)
    path = report / 'universe.json'
    old = json.loads(path.read_text())
    old_fields = old['fields']
    fields = list(metrics.UNIVERSE_FIELDS) + [f for f in old_fields if f not in metrics.UNIVERSE_FIELDS]
    research = MarketStore(report / 'research', 'volume')
    benchmark = metrics.read_price_csv(data / '^TWII.csv')
    if benchmark.empty:
        benchmark = metrics.read_price_csv(ROOT / '.cache/task-a-benchmark/^TWII.csv')
    if benchmark.empty:
        raise ValueError('A verified ^TWII daily benchmark is required; no universe data changed')
    closes = benchmark['Close'].copy()
    official_path = ROOT / '.cache/task-a-benchmark/official-close.json'
    if official_path.exists():
        official = json.loads(official_path.read_text())
        closes.loc[pd.Timestamp(official['date'])] = official['close']
        closes = closes.sort_index()
    rebuilt, recovery, source_rebuilds = {}, Counter(), []
    for market, section in old['markets'].items():
        records, rows = [], []
        for packed in section['rows']:
            previous = dict(zip(old_fields, packed))
            symbol = previous['symbol']
            row = dict(previous)
            if market == 'TW':
                history = metrics.read_price_csv(data / (symbol + '.csv'))
                source_path = ROOT / ('.cache/task-a-' + symbol.lower() + '.json')
                if history.empty and source_path.exists():
                    from zoneinfo import ZoneInfo
                    raw = json.loads(source_path.read_text())['chart']['result'][0]
                    zone = ZoneInfo(raw['meta']['exchangeTimezoneName'])
                    quote = raw['indicators']['quote'][0]
                    history = pd.DataFrame({f:quote[f.lower()] for f in kline.FIELDS},
                        index=pd.to_datetime([datetime.fromtimestamp(t,zone).date() for t in raw['timestamp']]))
                    receipt = json.loads(source_path.with_suffix('.receipt.json').read_text())
                    history = kline.accept_frame(history, symbol, official=None,
                        source_url=receipt['sourceUrl'], fetched_at=receipt['fetchedAt'])
                    source_rebuilds.append(dict(symbol=symbol, previousClose=previous['close'],
                        source=receipt, newClose=float(history.iloc[-1]['Close']), asOf=str(history.index[-1])[:10]))
                record = metrics.build_stock_record(symbol, history, {'symbol':'^TWII', 'close': closes}, market,
                                                    previous.get('name','')) if not history.empty else None
                if record:
                    values = dict(zip(metrics.UNIVERSE_FIELDS, metrics.record_to_row(record)))
                    row.update(values)
                    records.append(record)
                    if previous.get('marketCap') and previous.get('close'):
                        row['marketCap'] = round(previous['marketCap'] * row['close'] / previous['close'])
                    recovery['TW.dated-history'] += 1
                else:
                    row['asOf'] = None
                    recovery['TW.unknown'] += 1
            else:
                snapshot = research.get(symbol)
                if snapshot and abs(snapshot['latest']['close'] - row['close']) <= .011:
                    row['asOf'] = snapshot['asOf']
                    recovery['US.matching-dated-research'] += 1
                else:
                    row['asOf'] = None
                    recovery['US.unknown'] += 1
                    raw_path = ROOT / ('.cache/task-a-' + symbol.lower() + '.json')
                    if raw_path.exists():
                        from zoneinfo import ZoneInfo
                        raw = json.loads(raw_path.read_text())['chart']['result'][0]
                        source_zone = ZoneInfo(raw['meta']['exchangeTimezoneName'])
                        today = datetime.now(timezone.utc).astimezone(source_zone).date()
                        matches = [datetime.fromtimestamp(ts, source_zone).date().isoformat()
                                   for i, ts in enumerate(raw['timestamp'])
                                   if datetime.fromtimestamp(ts, source_zone).date() < today
                                   and raw['indicators']['quote'][0]['close'][i] is not None
                                   and abs(raw['indicators']['quote'][0]['close'][i] - row['close']) <= .011]
                        if len(matches) == 1:
                            row['asOf'] = matches[0]
                            recovery['US.unknown'] -= 1
                            recovery['US.matching-dated-Yahoo'] += 1
            rows.append(row)
        dated = Counter(r['asOf'] for r in rows if r.get('asOf'))
        # Preserve all existing symbols; unknown legacy dates are visible and are
        # excluded from date-sensitive comparisons in the client.
        asof = dated.most_common(1)[0][0] if dated else None
        groups = {}
        for row in rows:
            if row.get('asOf'):
                groups.setdefault(row['asOf'], []).append(row)
            else:
                row['rsRank'] = None
        for group in groups.values():
            metrics.assign_rs_rank(group)
        rebuilt[market] = dict(section, asOf=asof, rows=[[r.get(f) for f in fields] for r in rows])
    document = dict(old, fields=fields, markets=rebuilt, generatedAt=datetime.now(timezone.utc).isoformat())
    for market in old['markets']:
        assert {r[0] for r in rebuilt[market]['rows']} == {r[0] for r in old['markets'][market]['rows']}
    path.write_text(json.dumps(document, ensure_ascii=False, separators=(',',':'), allow_nan=False)+'\n')
    receipt = dict(version=1, generatedAt=document['generatedAt'], recovery=dict(recovery),
                   modalDates={k:v['asOf'] for k,v in rebuilt.items()},
                   dateDistribution={k:dict(Counter(str(row[fields.index('asOf')]) for row in v['rows'])) for k,v in rebuilt.items()},
                   sourceRebuilds=source_rebuilds, removedSymbols=0, permanentCsvWrites=0)
    (report / 'universe-date-rebuild.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    rebuild()

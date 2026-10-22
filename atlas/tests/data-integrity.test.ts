import { describe, expect, it } from 'vitest';
import { isQuarantinedBar, usableBars, usableBarResult } from '../src/market-data/KlineIntegrity';
import { normalizeYahooResponse } from '../src/market-data/YahooNormalizer';
import { parseUniverse } from '../src/report/universe';
import { filterAndSortItems } from '../src/pages/screenerItems';
import { applyFilters, parseFilters } from '../src/pages/screenerFilters';
import type { BarResult } from '../src/market-data/MarketDataProvider';

const t = (day: string) => Date.parse(day + 'T01:00:00Z') / 1000;
const bar = (day: string) => ({ time: t(day), open: 87, high: 105, low: 85, close: 95, volume: 100 });

describe('K-line quarantine boundary', () => {
  it('skips explicitly quarantined bars even if their OHLC looks valid', () => {
    expect(usableBars([bar('2026-08-27'), bar('2026-08-28'), bar('2026-08-31')], '7825.TWO')).toHaveLength(2);
    expect(usableBars([bar('2026-08-28')], '2330.TW')).toHaveLength(1);
    expect(usableBars([{ ...bar('2026-08-27'), low: 96 }], '2330.TW')).toHaveLength(0);
    expect(usableBars([{ ...bar('2026-08-27'), low: 0 }], '2330.TW')).toHaveLength(0);
  });
  it('quarantined native weeks/months are excluded rather than inferred clean', () => {
    expect(isQuarantinedBar('7825.TWO', t('2026-08-24'), '1W')).toBe(true);
    expect(isQuarantinedBar('7825.TWO', t('2026-08-01'), '1M')).toBe(true);
    expect(isQuarantinedBar('7825.TWO', t('2026-09-01'), '1M')).toBe(false);
  });
  it('normalizes source data before any chart or indicator can consume the bad candle', () => {
    const bars = [bar('2026-08-27'), { ...bar('2026-08-28'), low: 94.7 }, bar('2026-08-31')];
    const raw = { chart: { result: [{ meta: { symbol: '7825.TWO', currency: 'TWD', exchangeTimezoneName: 'Asia/Taipei' },
      timestamp: bars.map(b => b.time), indicators: { quote: [{ open: bars.map(b=>b.open), high: bars.map(b=>b.high),
        low: bars.map(b=>b.low), close: bars.map(b=>b.close), volume: bars.map(b=>b.volume) }] } }] } };
    const result = normalizeYahooResponse(raw, t('2026-09-02'), '1D', '7825.TWO');
    expect(result.bars.map(b=>b.time)).toEqual([t('2026-08-27'), t('2026-08-31')]);
    // Cached (including stale offline) data goes through the same boundary.
    const cached = usableBarResult({ ...result, bars } as BarResult, '7825.TWO', '1D');
    expect(cached.bars).toEqual(result.bars);
  });
});

describe('per-row universe observation dates', () => {
  const fields = ['symbol', 'name', 'close', 'changePct', 'volume', 'rsRank', 'rs63', 'asOf'];
  const rows = [
    ['AAPL', 'Apple', 100, 1, 100, 50, 2, '2026-10-21'],
    ['NVDA', 'Nvidia', 100, 99, 10000, 99, 50, '2026-10-22'],
    ['MSFT', 'Microsoft', 100, 2, 200, 70, 3, '2026-10-21'],
  ];
  const parse = (columns = fields, values = rows) => {
    const result = parseUniverse({ fields: columns, markets: { US: { benchmark: 'SPY', asOf: '2026-10-21', rows: values } } });
    if (result.status !== 'ok') throw new Error('test universe must parse');
    return result.universe.markets.US!;
  };
  it('round trips distinct row dates, including reordered fields', () => {
    const reversed = [...fields].reverse();
    expect(parse(reversed, rows.map(row => [...row].reverse())).items.map(row=>row.asOf))
      .toEqual(['2026-10-21', '2026-10-22', '2026-10-21']);
  });
  it('groups numeric sorts by date and compares date-sensitive filters within the modal date', () => {
    const market = parse();
    for (const sort of ['gain', 'loss', 'rs', 'vr'] as const) {
      const sorted = filterAndSortItems(market.items, '', sort, { asOf: market.asOf });
      expect(sorted.at(-1)!.symbol).toBe('NVDA');
    }
    for (const filter of ['chg:0:100', 'vol:50', 'rs:1']) {
      expect(applyFilters(market.items, parseFilters(filter), { market: 'US', asOf: market.asOf }).map(row=>row.symbol))
        .toEqual(['AAPL', 'MSFT']);
    }
  });
  it('does not invent a per-row date for an explicitly unknown or invalid date', () => {
    const invalid = rows.map(row => [...row]);
    invalid[0]![7] = '2026-02-30';
    expect(parse(fields, invalid).items[0]!.asOf).toBeNull();
  });
});

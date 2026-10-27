import { describe, expect, it } from 'vitest';
import { rowQuote, savedListRows } from '../src/ui/WatchlistBoard';
import { createWatchlist, visitWatchlist } from '../src/watchlists/model';

const bar = (time: number, close: number) => ({ time, open: close, high: close, low: close, close, volume: 1 });

describe('watchlist board rows', () => {
  it('shows no rows before a rules list has any recorded result', () => {
    expect(savedListRows(undefined)).toEqual([]);
    expect(savedListRows(createWatchlist({ name: '動能', market: 'TW', group: 'universe-tw', filters: '' }))).toEqual([]);
  });

  it('reads the last viewed result of a rules list without moving its baseline', () => {
    const list = createWatchlist({ name: '動能', market: 'TW', group: 'universe-tw', filters: '' });
    const visited = visitWatchlist(list, [{ symbol: '2330.TW', name: '台積電' }, { symbol: '2317.TW', name: '鴻海' }], '2026-10-27').list;
    const before = structuredClone(visited);
    expect(savedListRows(visited)).toEqual([{ symbol: '2330.TW', name: '台積電' }, { symbol: '2317.TW', name: '鴻海' }]);
    expect(visited).toEqual(before);
  });

  it('keeps a manual list in its own order and falls back to the symbol for unknown names', () => {
    const list = createWatchlist({ name: '手動', market: 'US', group: 'universe-us', filters: '', kind: 'manual', symbols: ['NVDA', 'AAPL'] });
    expect(savedListRows(list)).toEqual([{ symbol: 'NVDA', name: 'NVDA' }, { symbol: 'AAPL', name: 'AAPL' }]);
    const visited = visitWatchlist(list, [{ symbol: 'AAPL', name: 'Apple' }], '2026-10-27').list;
    expect(savedListRows(visited)).toEqual([{ symbol: 'NVDA', name: 'NVDA' }, { symbol: 'AAPL', name: 'Apple' }]);
  });
});

describe('watchlist board quote', () => {
  it('computes the daily change against the previous session and a 72×24 path', () => {
    const quote = rowQuote([bar(1, 100), bar(2, 110), bar(3, 99)]);
    expect(quote.close).toBe(99);
    expect(quote.changePct).toBeCloseTo(-10);
    expect(quote.time).toBe(3);
    expect(quote.points.split(' ')).toEqual(['0.0,20.2', '36.0,2.0', '72.0,22.0']);
    expect(quote.trend).toBe(-1);
  });

  it('uses only the latest 30 bars for the path', () => {
    const bars = Array.from({ length: 40 }, (_, i) => bar(i, 100 + i));
    expect(rowQuote(bars).points.split(' ')).toHaveLength(30);
    expect(rowQuote(bars).trend).toBe(29);
  });

  it('has no change or path without two usable closes', () => {
    expect(rowQuote([])).toMatchObject({ close: null, changePct: null, points: '', trend: null });
    expect(rowQuote([bar(1, 5)])).toMatchObject({ close: 5, changePct: null, points: '', trend: null });
    expect(rowQuote([bar(1, 0), bar(2, 5)]).changePct).toBeNull();
  });
});

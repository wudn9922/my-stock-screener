import { describe, expect, it, vi } from 'vitest';
import { readFile } from 'node:fs/promises';
import { collectorValuationProvider } from '../scripts/refresh-watchlists.ts';
import { getScreenerPeFigures, screenerPeValue, type PeFigures } from '../src/app/dataSources';
import { ValuationProvider } from '../src/fundamentals/ValuationProvider';
import { activeChips, applyFilters, filterAvailability, parseFilters, peRangeOf, removeFilter, serializeFilters } from '../src/pages/screenerFilters';
import { groupItemSchema } from '../src/report/schema';
import { createWatchlist, evaluateWatchlist, watchlistSchema } from '../src/watchlists/model';

vi.mock('node:fs/promises', () => ({ readFile: vi.fn() }));

const figure = (peTtm: number | null, extra: Partial<PeFigures> = {}): PeFigures => ({
  pe: null, peTtm, peLoss: false, peTtmLoss: false, reason: null, fiscalYear: 2025, source: 'Yahoo Finance', asOf: '2026-10-27', ...extra,
});
const rows = ['LOW', 'HIGH', 'OUT', 'ANNUAL', 'LOSS', 'MISSING'].map(symbol => groupItemSchema.parse({ symbol, close: 100 }));
const pe = { LOW: figure(10), HIGH: figure(20), OUT: figure(20.01), ANNUAL: figure(null, { pe: 15 }), LOSS: figure(null, { pe: 5, peTtmLoss: true }) };

describe('P/E filter encoding and input', () => {
  it.each(['pe:10:20', 'pe::20', 'pe:10:', 'pe:0:0', 'pe:10.25:20.5'])('round-trips %s without changing old filter tokens', text => {
    expect(serializeFilters(parseFilters(text))).toBe(text);
    expect(serializeFilters(parseFilters(`vol:100000;${text};epsy:2:10`))).toBe(`vol:100000;${text};epsy:2:10`);
  });
  it.each(['pe:-1:20', 'pe:20:10', 'pe:1e2:200', 'pe:Infinity:20', 'pe:NaN:20', 'pe:10.123:20', 'pe:10:20:30', 'pe:10', 'pe::', 'pe:9007199254740991:'])('ignores unusable %s', text => {
    expect(parseFilters(text).pe).toBeUndefined();
    expect(serializeFilters(parseFilters(text))).toBe('');
  });
  it('supports blank bounds, validates drafts and removes only the P/E condition', () => {
    expect(peRangeOf('', '')).toBeNull();
    expect(peRangeOf('', '20')).toEqual({ min: null, max: 20 });
    expect(peRangeOf('20', '10')).toBeUndefined();
    expect(parseFilters('')).not.toHaveProperty('pe');
    const state = parseFilters('vol:100000;pe:10:20');
    expect(activeChips(state).map(chip => chip.label)).toEqual(['20 日均量 ≥100,000 股', '本益比 10～20 倍']);
    expect(serializeFilters(removeFilter(state, 'pe'))).toBe('vol:100000');
  });
});

describe('P/E selection', () => {
  it('includes both boundaries, uses the displayed annual fallback, excludes losses and missing data', () => {
    for (const market of ['TW', 'US'] as const) {
      expect(applyFilters(rows, parseFilters('pe:10:20'), { market, pe }).map(row => row.symbol)).toEqual(['LOW', 'HIGH', 'ANNUAL']);
      expect(applyFilters(rows, { ...parseFilters('pe:10:20'), keepMissing: true }, { market, pe }).map(row => row.symbol)).toEqual(['LOW', 'HIGH', 'ANNUAL']);
    }
    expect(screenerPeValue(figure(0))).toBeNull();
    expect(screenerPeValue(figure(Number.NaN))).toBeNull();
    expect(screenerPeValue(figure(10, { peLoss: true }))).toBe(10);
    expect(applyFilters([{ ...rows[0]!, status: 'failed' }], parseFilters('pe:10:20'), { market: 'US', pe })).toEqual([]);
  });
  it('combines P/E with other conditions and never falls back to an unfiltered list while unavailable', () => {
    const items = rows.map(row => groupItemSchema.parse({ ...row, metrics: { volumeAvg20: row.symbol === 'HIGH' ? 200000 : 50000 } }));
    expect(applyFilters(items, parseFilters('vol:100000;pe::20'), { market: 'US', pe }).map(row => row.symbol)).toEqual(['HIGH']);
    for (const source of [undefined, null]) {
      expect(filterAvailability(rows, { market: 'US', pe: source }).pe).toBeTruthy();
      expect(applyFilters(rows, parseFilters('pe::20'), { market: 'US', pe: source })).toEqual([]);
    }
    expect(applyFilters(rows, parseFilters('pe::20'), { market: 'US', pe: {} })).toEqual([]);
    expect(applyFilters(rows, parseFilters(''), { market: 'US' })).toEqual(rows);
  });
  it('preserves rules through saved strategy import and conservatively keeps the baseline on source failure', () => {
    const list = watchlistSchema.parse(JSON.parse(JSON.stringify(createWatchlist({ name: '合理本益比', market: 'US', group: 'universe-us', filters: 'pe:10:20' }))));
    expect(list.filters).toBe('pe:10:20');
    expect(evaluateWatchlist(list, rows, undefined, undefined, pe).map(row => row.symbol)).toEqual(['LOW', 'HIGH', 'ANNUAL']);
    expect(() => evaluateWatchlist(list, rows, undefined, undefined, null)).toThrow('保留上次清單');
    expect(() => evaluateWatchlist(list, rows, undefined)).toThrow('保留上次清單');
  });
});

describe('whole-universe P/E data', () => {
  it('uses the same existing valuation files in offline watchlist collection, with the Pages fallback', async () => {
    const json = JSON.stringify({ version: 1, market: 'US', items: { LOW: { epsTtm: 5, source: 'Yahoo Finance' } } });
    const read = vi.mocked(readFile).mockRejectedValueOnce(new Error('missing public file')).mockResolvedValueOnce(json);
    const result = await getScreenerPeFigures(rows, 'US', collectorValuationProvider('/offline'));
    expect(screenerPeValue(result!.LOW)).toBe(20);
    expect(read.mock.calls.map(call => String(call[0]))).toEqual(['/offline/atlas/public/valuation/us.json', '/offline/docs/atlas/valuation/us.json']);
    read.mockReset().mockRejectedValue(new Error('unavailable'));
    expect(await getScreenerPeFigures(rows, 'US', collectorValuationProvider('/missing'))).toBeNull();
  });
  it('evaluates all 6,000 rows with one market-file fetch, including rows outside the viewport', async () => {
    const items = Array.from({ length: 6000 }, (_, index) => ({ symbol: `X${index}`, close: 100 }));
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ version: 1, market: 'US', items: Object.fromEntries(items.map(item => [item.symbol, { epsTtm: 5, epsAnnual: 10, source: 'Yahoo Finance' }])) })));
    const provider = new ValuationProvider('/valuation/', { fetcher });
    const result = await getScreenerPeFigures(items, 'US', provider);
    expect(Object.keys(result!)).toHaveLength(6000);
    expect(screenerPeValue(result!.X5999)).toBe(20);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('uses Taiwan exchange TTM even without a close, and reports a missing/invalid market file', async () => {
    const provider = new ValuationProvider('/valuation/', { fetcher: async () => new Response(JSON.stringify({ version: 1, market: 'TW', items: { '2330.TW': { exchangePeTtm: 19.25, source: 'TWSE' } } })) });
    const result = await getScreenerPeFigures([{ symbol: '2330.TW', close: null }], 'TW', provider);
    expect(screenerPeValue(result!['2330.TW'])).toBe(19.25);
    expect(await getScreenerPeFigures([], 'US', provider)).toBeNull();
    const missing = new ValuationProvider('/missing/', { fetcher: async () => new Response('', { status: 404 }) });
    expect(await getScreenerPeFigures(rows, 'US', missing)).toBeNull();
  });
});

import { describe, expect, it } from 'vitest';
import {
  activeChips,
  applyFilters,
  filterAvailability,
  parseFilters,
  peRiverRangeOf,
  removeFilter,
  serializeFilters,
} from '../src/pages/screenerFilters';
import { loadPeRiverFile, parsePeRiverFile, peRiverPercentile, type PeRiverFile } from '../src/fundamentals/peRiverRank';
import { groupItemSchema, type GroupItem } from '../src/report/schema';

const item = (symbol: string): GroupItem =>
  groupItemSchema.parse({ symbol, close: 100, changePct: 1, maList: [20], maValues: { 20: 95 }, metrics: null });

const file = (items: Record<string, unknown>, market: 'TW' | 'US' = 'US'): PeRiverFile =>
  parsePeRiverFile({ version: 1, market, generatedAt: '2026-11-07T00:00:00Z', priceThrough: '2026-11-06', basis: 'test', items }, market)!;

describe('P/E river percentile filter encoding', () => {
  it('round-trips open and closed ranges and drops invalid ones', () => {
    expect(serializeFilters(parseFilters('per::30'))).toBe('per::30');
    expect(serializeFilters(parseFilters('per:70:'))).toBe('per:70:');
    expect(serializeFilters(parseFilters('per:30:70'))).toBe('per:30:70');
    expect(parseFilters('per:30:70').per).toEqual({ min: 30, max: 70 });
    for (const bad of ['per:70:30', 'per:-1:', 'per::101', 'per:abc:', 'per:10', 'per:12.34:'])
      expect(parseFilters(bad).per ?? null, bad).toBeNull();
    // 0 and 100 are the ends of the scale: the same as no bound.
    expect(peRiverRangeOf('0', '100')).toBeNull();
    expect(peRiverRangeOf('0', '30')).toEqual({ min: null, max: 30 });
    expect(peRiverRangeOf('', '')).toBeNull();
    expect(peRiverRangeOf('12.5', '')).toEqual({ min: 12.5, max: null });
  });

  it('combines with the existing P/E condition in canonical order', () => {
    expect(serializeFilters(parseFilters('per::30;pe::20'))).toBe('pe::20;per::30');
  });

  it('labels and removes its chip', () => {
    const state = parseFilters('per::30');
    expect(activeChips(state).map((chip) => chip.label)).toEqual(['河流分位 ≤P30']);
    expect(activeChips(parseFilters('per:30:70'))[0]!.label).toBe('河流分位 P30～P70');
    expect(activeChips(parseFilters('per:90:'))[0]!.label).toBe('河流分位 ≥P90');
    expect(removeFilter(state, 'per').per).toBeNull();
  });
});

describe('P/E river percentile filter', () => {
  const rows = [item('AAPL'), item('NVDA'), item('MSFT'), item('LOSS')];
  const river = file({ AAPL: [93.6, 0], NVDA: [0.2, 0], MSFT: [30, 1] });

  it('keeps inclusive bounds and always excludes stocks without a river position', () => {
    const context = { market: 'US' as const, peRiver: river };
    expect(applyFilters(rows, parseFilters('per::30'), context).map((row) => row.symbol)).toEqual(['NVDA', 'MSFT']);
    expect(applyFilters(rows, parseFilters('per:90:'), context).map((row) => row.symbol)).toEqual(['AAPL']);
    expect(applyFilters(rows, parseFilters('per:30:70'), context).map((row) => row.symbol)).toEqual(['MSFT']);
  });

  it('never shows an unfiltered list while the file is loading or missing', () => {
    expect(applyFilters(rows, parseFilters('per::30'), { market: 'US', peRiver: undefined })).toEqual([]);
    expect(applyFilters(rows, parseFilters('per::30'), { market: 'US', peRiver: null })).toEqual([]);
  });

  it('reports why the condition is unavailable', () => {
    expect(filterAvailability(rows, { market: 'US' }).per).toBe('本益比河流資料載入中…');
    expect(filterAvailability(rows, { market: 'US', peRiver: null }).per).toBe('本益比河流資料尚未產生或暫時無法取得');
    expect(filterAvailability([item('LOSS')], { market: 'US', peRiver: river }).per).toBe('這個群組的股票沒有可用的本益比河流');
    expect(filterAvailability(rows, { market: 'US', peRiver: river }).per).toBeNull();
  });
});

describe('P/E river percentile file', () => {
  it('drops invalid rows and rejects another market or version', () => {
    const parsed = file({ OK: [12.3, 0], BAD: [120, 0], ALSO: ['x', 0], FLAG: [5, 2] });
    expect(Object.keys(parsed.items)).toEqual(['OK']);
    expect(peRiverPercentile(parsed, 'OK')).toBe(12.3);
    expect(peRiverPercentile(parsed, 'BAD')).toBeNull();
    expect(parsePeRiverFile({ ...parsed, market: 'TW' }, 'US')).toBeNull();
    expect(parsePeRiverFile({ ...parsed, version: 2 }, 'US')).toBeNull();
  });

  it('loads the market file and treats HTTP or JSON failures as unavailable', async () => {
    const body = { version: 1, market: 'TW', generatedAt: 'x', priceThrough: null, basis: 'b', items: { '2330.TW': [47.5, 0] } };
    const ok: typeof fetch = async (input) => {
      expect(String(input)).toBe('/base/valuation/pe-river-tw.json');
      return new Response(JSON.stringify(body));
    };
    expect(peRiverPercentile(await loadPeRiverFile('TW', '/base/valuation/', ok, 1), '2330.TW')).toBe(47.5);
    const missing: typeof fetch = async () => new Response('', { status: 404 });
    expect(await loadPeRiverFile('TW', '/other/', missing, 1)).toBeNull();
    const broken: typeof fetch = async () => new Response('{');
    expect(await loadPeRiverFile('US', '/broken/', broken, 1)).toBeNull();
  });
});

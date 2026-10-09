import { describe, expect, it } from 'vitest';
import { displayTicker, formatPercent, formatPrice, formatRatio, maDistance, maValue, toneClass } from '../src/ui/format';
import { matchLocalSymbols } from '../src/ui/searchRanking';
import { filterAndSortItems } from '../src/pages/screenerItems';
import { groupItemSchema, type GroupItem } from '../src/report/schema';

describe('formatting', () => {
  it('formats prices, signed percents and ratios', () => {
    expect(formatPrice(22812.456)).toBe('22,812.46');
    expect(formatPrice(0.12345)).toBe('0.1235');
    expect(formatPrice(null)).toBe('—');
    expect(formatPercent(1.234)).toBe('+1.23%');
    expect(formatPercent(-0.004)).toBe('0.00%');
    expect(formatPercent(-2.5, 1)).toBe('-2.5%');
    expect(formatPercent(Number.NaN)).toBe('—');
    expect(formatRatio(22.64)).toBe('22.6');
    expect(formatRatio(1500)).toBe('>999');
    expect(formatRatio(null)).toBe('—');
  });

  it('maps signs to up/down tones', () => {
    expect(toneClass(0.5)).toBe('up');
    expect(toneClass(-0.5)).toBe('down');
    expect(toneClass(0)).toBe('flat');
    expect(toneClass(null)).toBe('flat');
  });

  it('reads report MA values under any key style and computes distance', () => {
    expect(maValue({ 20: 100 }, 20)).toBe(100);
    expect(maValue({ MA60: 50 }, 60)).toBe(50);
    expect(maValue({ sma5: 7 }, 5)).toBe(7);
    expect(maValue({}, 20)).toBeNull();
    expect(maDistance(110, 100)).toBeCloseTo(10, 10);
    expect(maDistance(110, null)).toBeNull();
    expect(maDistance(110, 0)).toBeNull();
    expect(displayTicker('6488.TWO')).toBe('6488');
    expect(displayTicker('NVDA')).toBe('NVDA');
  });
});

describe('search ranking of report symbols', () => {
  const symbols = [
    { symbol: '^TWII', name: '台灣加權指數' },
    { symbol: '^TWOII', name: '台灣櫃買指數(OTC)' },
    { symbol: '2330.TW', name: '台積電' },
    { symbol: '^GSPC', name: '美國標普500' },
  ];
  it('ranks exact ticker, ticker prefix, name prefix, then name substring', () => {
    expect(matchLocalSymbols('2330', symbols).map((s) => s.symbol)).toEqual(['2330.TW']);
    expect(matchLocalSymbols('台灣', symbols).map((s) => s.symbol)).toEqual(['^TWII', '^TWOII']);
    expect(matchLocalSymbols('^twii', symbols)[0]!.symbol).toBe('^TWII');
    expect(matchLocalSymbols('twii', symbols)[0]!.symbol).toBe('^TWII');
    expect(matchLocalSymbols('標普', symbols).map((s) => s.symbol)).toEqual(['^GSPC']);
    expect(matchLocalSymbols('  ', symbols)).toEqual([]);
  });
});

describe('screener list', () => {
  const item = (raw: Record<string, unknown>): GroupItem => groupItemSchema.parse(raw);
  const items = [
    item({ symbol: '2330.TW', name: '台積電', close: 110, changePct: 2, maList: [20], maValues: { 20: 100 } }),
    item({ symbol: '2317.TW', name: '鴻海', close: 95, changePct: -3, maList: [20], maValues: { 20: 100 }, note: '跌破月線' }),
    item({ symbol: 'NVDA', name: '', close: null, changePct: null, maList: [] }),
  ];
  it('filters by ticker, name and note', () => {
    expect(filterAndSortItems(items, '台積', 'default').map((i) => i.symbol)).toEqual(['2330.TW']);
    expect(filterAndSortItems(items, '月線', 'default').map((i) => i.symbol)).toEqual(['2317.TW']);
    expect(filterAndSortItems(items, 'nv', 'default').map((i) => i.symbol)).toEqual(['NVDA']);
  });
  it('sorts with missing values last and never mutates the input', () => {
    expect(filterAndSortItems(items, '', 'gain').map((i) => i.symbol)).toEqual(['2330.TW', '2317.TW', 'NVDA']);
    expect(filterAndSortItems(items, '', 'loss').map((i) => i.symbol)).toEqual(['2317.TW', '2330.TW', 'NVDA']);
    expect(filterAndSortItems(items, '', 'ma').map((i) => i.symbol)).toEqual(['2330.TW', '2317.TW', 'NVDA']);
    expect(filterAndSortItems(items, '', 'symbol').map((i) => i.symbol)).toEqual(['2317.TW', '2330.TW', 'NVDA']);
    expect(items.map((i) => i.symbol)).toEqual(['2330.TW', '2317.TW', 'NVDA']);
  });
});

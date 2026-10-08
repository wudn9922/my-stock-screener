import { describe, expect, it } from 'vitest';
import { normalizeSymbol } from '../src/market-data/MarketDataProvider';
import { alignIndexCurrency } from '../src/market-data/EdgeYahooProvider';
import { isIndexSymbol } from '../src/market-data/MarketProfile';

describe('Shanghai/Shenzhen index symbols (world-index page)', () => {
  it.each(['000001.SS', '399001.SZ'])('accepts %s as an index', (symbol) => {
    expect(normalizeSymbol(symbol.toLowerCase())).toBe(symbol);
    expect(isIndexSymbol(symbol)).toBe(true);
  });

  it.each(['00001.SS', '0000001.SS', '000001.SH', '2330.SS'])('rejects %s', (symbol) => {
    expect(() => normalizeSymbol(symbol)).toThrow();
  });

  it('labels CNY index points with the profile currency instead of rejecting them', () => {
    const raw = { chart: { result: [{ meta: { currency: 'CNY' } }] } };
    const aligned = alignIndexCurrency(raw, '000001.SS') as typeof raw;
    expect(aligned.chart.result[0]!.meta.currency).toBe('USD');
    expect(raw.chart.result[0]!.meta.currency).toBe('CNY');
  });

  it('keeps Taiwan stock symbols out of the index rule', () => {
    expect(isIndexSymbol('2330.TW')).toBe(false);
  });
});

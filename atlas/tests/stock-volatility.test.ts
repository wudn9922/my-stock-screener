import { describe, expect, it } from 'vitest';
import type { Bar, BarResult } from '../src/market-data/MarketDataProvider';
import { closedBars } from '../src/market-data/MarketDataProvider';
import { getMarketProfile } from '../src/market-data/MarketProfile';
import { stockVolatility } from '../src/market-data/StockVolatility';

const flat = (count = 21): Bar[] =>
  Array.from({ length: count }, (_, i) => ({
    time: 1000 + i * 86400,
    open: 100,
    high: 102,
    low: 98,
    close: 100,
    volume: 1000,
  }));

describe('completed daily stock volatility', () => {
  it('requires 20 ranges with known previous closes and accepts a truly flat market', () => {
    expect(stockVolatility(flat(20))).toBeNull();
    expect(stockVolatility(flat())).toMatchObject({
      atr: 4,
      atrPercent: 4,
      intradayRangePercent: 4,
    });
    expect(stockVolatility(flat().map((bar) => ({ ...bar, high: 100, low: 100 })))).toMatchObject({ atr: 0, atrPercent: 0, intradayRangePercent: 0 });
  });
  it('includes overnight gaps in ATR, excludes them from intraday range and uses daily close as denominator', () => {
    const bars = flat(22);
    bars[21] = { ...bars[21], open: 110, high: 112, low: 108, close: 110 };
    const stats = stockVolatility(bars)!;
    expect(stats.atr).toBeCloseTo((4 * 19 + 12) / 20, 12);
    expect(stats.atrPercent).toBeCloseTo((4.4 / 110) * 100, 12);
    expect(stats.intradayRangePercent).toBeCloseTo(4, 12);
    // The next day's range is divided by 110, never by its own open/close.
    bars.push({
      ...bars[21],
      time: bars[21].time + 86400,
      high: 114,
      low: 106,
    });
    expect(stockVolatility(bars)!.intradayRangePercent).toBeCloseTo((19 * 4 + (8 / 110) * 100) / 20, 12);
  });
  it('retains Wilder history beyond the last 20 ranges', () => {
    const bars = flat(42);
    bars[1] = { ...bars[1], high: 120, low: 80 };
    expect(stockVolatility(bars)!.atr).toBeCloseTo(4 + 1.8 * Math.pow(19 / 20, 21), 12);
    expect(stockVolatility(bars)!.intradayRangePercent).toBeCloseTo(4, 12);
  });
  it('rejects corrupt or unordered data instead of silently dropping bars', () => {
    for (const change of [{ close: 0 }, { high: NaN }, { low: 103 }, { time: 1000 }]) {
      const bars = flat();
      bars[20] = { ...bars[20], ...change };
      expect(stockVolatility(bars)).toBeNull();
    }
  });
  it('excludes a current unfinished daily candle before calculating', () => {
    const market = getMarketProfile('AAPL');
    let date = Date.UTC(2026, 8, 1, 13, 30) / 1000;
    const bars = flat().map((bar) => {
      while ([0, 6].includes(new Date(date * 1000).getUTCDay())) date += 86400;
      const time = date;
      date += 86400;
      return { ...bar, time };
    });
    const asOf = bars[20].time + 3600;
    const result = { bars, market } as BarResult;
    expect(stockVolatility(closedBars(result, '1D', asOf))).toBeNull();
    expect(stockVolatility(closedBars(result, '1D', asOf + 86400))).not.toBeNull();
  });
});

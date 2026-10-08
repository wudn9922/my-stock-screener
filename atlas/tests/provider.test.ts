import { it, expect } from 'vitest';
import { DemoProvider, DEMO_AS_OF } from '../src/market-data/DemoProvider';
import { nextSessionTime } from '../src/chart/TimeMapper';
it('Demo provides deterministic regular-session OHLCV for all exposed timeframes', async () => {
  const p = new DemoProvider();
  for (const tf of p.supportedTimeframes) {
    const { bars } = await p.getBars('AAPL', tf);
    expect(bars).toHaveLength(tf === '1M' ? 600 : 2500);
    expect(bars.at(-1)!.time).toBeLessThan(DEMO_AS_OF);
    for (let i = 0; i < bars.length; i++) {
      const b = bars[i];
      expect(b.high).toBeGreaterThanOrEqual(Math.max(b.open, b.close));
      expect(b.low).toBeLessThanOrEqual(Math.min(b.open, b.close));
      if (i) expect(b.time).toBeGreaterThan(bars[i - 1].time);
    }
    expect((await p.getBars('AAPL', tf)).bars).toEqual(bars);
  }
}, 30000);
it('future schedule skips weekends and handles short final hourly session', () => {
  const fri = Date.parse('2026-10-02T19:30:00Z') / 1000;
  expect(new Date(nextSessionTime(fri, '1H') * 1000).toISOString()).toBe(
    '2026-10-05T13:30:00.000Z',
  );
});

import { normalizeYahooResponse } from '../src/market-data/YahooNormalizer';
it('Yahoo provider normalizes, filters missing OHLC and sorts/deduplicates timestamps', () => {
  const result = normalizeYahooResponse({
    chart: {
      result: [
        {
          timestamp: [3, 1, 2, 1],
          indicators: {
            quote: [
              {
                open: [10, 11, null, 12],
                high: [12, 13, null, 14],
                low: [9, 10, null, 11],
                close: [11, 12, null, 13],
                volume: [10, 20, null, 30],
              },
            ],
          },
        },
      ],
    },
  });
  expect(result.bars.map((b) => b.time)).toEqual([1, 3]);
  expect(result.bars[0].close).toBe(13);
  expect(result).toMatchObject({ session: 'regular', delayed: true, adjusted: false });
  expect(() => normalizeYahooResponse({ chart: { result: null } })).toThrow();
});

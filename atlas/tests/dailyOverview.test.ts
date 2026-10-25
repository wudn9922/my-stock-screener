import { afterEach, expect, it, vi } from 'vitest';
import type { BarResult } from '../src/market-data/MarketDataProvider';

afterEach(() => { vi.doUnmock('../src/market-data/createScreenerMarketProvider'); vi.resetModules(); });

it('shares one daily request and its unchanged metadata between chart bars and index analysis', async () => {
  const data: BarResult = { bars: [{ time: 1791421200, open: 100, high: 110, low: 90, close: 105, volume: 1000 }], source: 'Official index daily history', session: 'regular', delayed: true, adjusted: false, asOf: 1791438000, cacheStatus: 'stale' };
  const getBars = vi.fn(async () => data);
  vi.doMock('../src/market-data/createScreenerMarketProvider', () => ({ createScreenerMarketProvider: () => ({ getBars }) }));
  const { getDailyBars, getDailyBarResult } = await import('../src/app/dataSources');
  const [chart, analysis] = await Promise.all([getDailyBars('^TWII'), getDailyBarResult('^TWII')]);
  expect(getBars).toHaveBeenCalledExactlyOnceWith('^TWII', '1D');
  expect(chart).toBe(data.bars);
  expect(analysis).toBe(data);
  expect(await getDailyBarResult('^TWII')).toBe(data);
});

it('evicts a failed overview request so a retry can recover', async () => {
  const data: BarResult = { bars: [], source: 'Recovered', session: 'regular', delayed: true, adjusted: false };
  const getBars = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(data);
  vi.doMock('../src/market-data/createScreenerMarketProvider', () => ({ createScreenerMarketProvider: () => ({ getBars }) }));
  const { getDailyBarResult } = await import('../src/app/dataSources');
  await expect(getDailyBarResult('^GSPC')).rejects.toThrow('offline');
  expect(await getDailyBarResult('^GSPC')).toBe(data);
  expect(getBars).toHaveBeenCalledTimes(2);
});

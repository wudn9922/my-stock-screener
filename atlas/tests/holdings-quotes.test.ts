import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BarResult } from '../src/market-data/MarketDataProvider';
const h = vi.hoisted(() => ({
  world: { series: {} as Record<string, Record<string, unknown>> },
  result: {} as BarResult,
  calls: [] as string[],
}));
vi.mock('../src/app/dataSources', () => ({
  getDailyBarResult: async (symbol: string) => {
    h.calls.push(symbol);
    return h.result;
  },
  getHoldingsSymbolDirectory: async () => [],
}));
vi.mock('../src/report/universe', () => ({ loadUniverse: async () => ({ status: 'missing' }) }));
vi.mock('../src/report/research', () => ({
  fetchResearch: async () => h.world,
  worldAssetsSchema: {},
  researchUrl: (path: string) => `https://fixture.invalid/${path}.json`,
}));
import { loadHoldingQuotes } from '../src/holdings/quotes';
const bar = (day: string, close: number) => ({
  time: Date.parse(day + 'T13:30:00Z') / 1000,
  close,
  open: close,
  high: close,
  low: close,
  volume: 100,
});
beforeEach(() => {
  h.calls = [];
  h.world = { series: {} };
  h.result = {
    bars: [bar('2026-10-27', 100), bar('2026-10-28', 105)],
    source: 'real-provider',
    session: 'regular',
    adjusted: false,
    delayed: true,
  };
});
afterEach(() => vi.unstubAllGlobals());
describe('holdings quote boundary', () => {
  it('uses actual daily closes and the previous session, de-duplicates symbols and never requests positions', async () => {
    const result = await loadHoldingQuotes(['AAPL', 'AAPL'], new AbortController().signal);
    expect(h.calls).toEqual(['AAPL']);
    expect(result.quotes.AAPL).toEqual({ price: '105', previous: '100', asOf: '2026-10-28', source: 'real-provider' });
  });
  it('leaves missing and simulated quotes unavailable', async () => {
    h.result.dataState = 'simulated';
    expect((await loadHoldingQuotes(['AAPL'], new AbortController().signal)).quotes).toEqual({});
    h.result.dataState = 'delayed';
    h.result.bars = [];
    expect((await loadHoldingQuotes(['AAPL'], new AbortController().signal)).quotes).toEqual({});
  });
  it('accepts only explicitly directed USD/TWD rates and does not treat USDT as USD', async () => {
    h.world.series.fx = {
      status: 'ok',
      latest: 32.1,
      asOf: '2026-10-27',
      baseCurrency: 'USD',
      quoteCurrency: 'TWD',
      source: 'existing fx',
    };
    h.world.series.wrong = {
      status: 'ok',
      latest: 0.031,
      asOf: '2026-10-27',
      baseCurrency: 'TWD',
      quoteCurrency: 'USD',
    };
    const result = await loadHoldingQuotes([], new AbortController().signal);
    expect(result.rates).toEqual([{ currency: 'USD', twd: '32.1', asOf: '2026-10-27' }]);
  });
  it('uses BTC/USDT spot candles for daily P&L and rejects a mismatched asset currency', async () => {
    h.world.series.btcusdt = {
      symbol: 'CRYPTO-BTCUSDT',
      status: 'ok',
      latest: 65000,
      asOf: '2026-10-28',
      baseCurrency: 'BTC',
      quoteCurrency: 'USDT',
      instrumentType: 'crypto-spot',
      candlePath: 'candles/btcusdt.json',
      source: 'existing crypto',
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              version: 1,
              symbol: 'CRYPTO-BTCUSDT',
              interval: '1D',
              bars: [
                ['2026-10-27', 64000, 64000, 64000, 64000, 1],
                ['2026-10-28', 65000, 65000, 65000, 65000, 2],
              ],
            }),
          ),
      ),
    );
    const result = await loadHoldingQuotes(['BTC/USDT', 'ETH/USDT'], new AbortController().signal);
    expect(result.quotes['BTC/USDT']).toMatchObject({ price: '65000', previous: '64000' });
    expect(result.quotes['ETH/USDT']).toBeUndefined();
    expect(h.calls).toEqual([]);
    h.world.series.btcusdt.quoteCurrency = 'USD';
    expect((await loadHoldingQuotes(['BTC/USDT'], new AbortController().signal)).quotes).toEqual({});
  });
});

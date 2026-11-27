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
import { holdingCloseQuote, loadHoldingQuotes } from '../src/holdings/quotes';
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
  it('does not label a forming daily bar as a closing price', async () => {
    h.result.asOf = Date.parse('2026-10-28T15:00:00Z') / 1000;
    expect((await loadHoldingQuotes(['AAPL'], new AbortController().signal)).quotes.AAPL).toMatchObject({ price: '100', asOf: '2026-10-27' });
  });
  it('labels UTC-midnight US archive bars with their actual trading dates', () => {
    const asOf = Date.parse('2026-10-28T20:10:00Z') / 1000;
    const bars = h.result.bars.map(row => ({ ...row, time: Math.floor(row.time / 86400) * 86400 }));
    const quote = { symbol: 'AAPL', price: 105, change: 5, changePercent: 5, asOf: bars.at(-1)!.time, dataState: 'delayed' as const };
    expect(holdingCloseQuote('AAPL', { ...h.result, bars, quote, asOf }, asOf * 1000))
      .toMatchObject({ price: '105', previous: '100', asOf: '2026-10-28' });
  });
  it('keeps the prior confirmed close for a repeated partial bar after the venue closes', () => {
    const asOf = Date.parse('2026-11-27T13:31:00+08:00') / 1000;
    const bars = [bar('2026-11-26', 2145), bar('2026-11-27', 2170)];
    const quote = { symbol: '2330.TW', price: 2170, change: 25, changePercent: 1.17,
      asOf: Date.parse('2026-11-27T13:20:00+08:00') / 1000, dataState: 'live' as const };
    const data = { ...h.result, bars, asOf, source: 'archive · 今日來源：證交所即時' };
    expect(holdingCloseQuote('2330.TW', { ...data, quote }, asOf * 1000))
      .toEqual({ price: '2145', previous: null, asOf: '2026-11-26', source: 'archive' });
    expect(holdingCloseQuote('2330.TW', data, asOf * 1000, true))
      .toMatchObject({ price: '2145', asOf: '2026-11-26' });
    expect(holdingCloseQuote('2330.TW', { ...data, source: 'archive · 今日開高低／量：證交所即時' }, asOf * 1000))
      .toMatchObject({ price: '2145', asOf: '2026-11-26' });
  });
  it('uses a verified closing print even when the latest archive bar is still from yesterday', () => {
    const asOf = Date.parse('2026-11-27T13:31:00+08:00') / 1000;
    const quote = { symbol: '2330.TW', price: 2160, change: 15, changePercent: 0.7,
      asOf: asOf - 60, source: '證交所即時（非官方 API）', dataState: 'live' as const };
    const data = { ...h.result, asOf, bars: [bar('2026-11-26', 2145)], quote };
    expect(holdingCloseQuote('2330.TW', data, asOf * 1000, true))
      .toEqual({ price: '2160', previous: '2145', asOf: '2026-11-27', source: '證交所 MIS（非官方 API）' });
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

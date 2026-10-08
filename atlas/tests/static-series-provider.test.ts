import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
  StaticSeriesProvider,
  aggregateBars,
  cleanDailyBars,
  defaultReportBase,
  resolveDotSegments,
} from '../src/market-data/StaticSeriesProvider';
import { ScreenerMarketRouter, createScreenerMarketProvider } from '../src/market-data/createScreenerMarketProvider';
import { EdgeYahooProvider } from '../src/market-data/EdgeYahooProvider';
import { MarketDataUnavailableError } from '../src/market-data/ProviderErrors';
import { getMarketProfile } from '../src/market-data/MarketProfile';

const day = (iso: string) => Date.parse(`${iso}T00:00:00Z`) / 1000;
const bar = (iso: string, open: number, high: number, low: number, close: number, volume = 100) => ({
  time: day(iso),
  open,
  high,
  low,
  close,
  volume,
});

// Wed 2026-09-30 … Wed 2026-10-07 (TPEx index, official 00:00 UTC day stamps)
const series = {
  symbol: '^TWOII',
  name: '櫃買指數',
  source: 'TPEx 櫃買中心',
  interval: '1D',
  asOf: day('2026-10-07'),
  bars: [
    bar('2026-09-29', 250, 252, 249, 251),
    bar('2026-09-30', 251, 255, 250, 254),
    bar('2026-10-01', 254, 258, 253, 257, 200),
    bar('2026-10-02', 257, 259, 251, 252),
    bar('2026-10-05', 252, 260, 251, 259),
    bar('2026-10-06', 259, 262, 258, 261),
    bar('2026-10-07', 261, 263, 255, 256),
    { time: day('2026-10-08'), open: 1, high: 0.5, low: 2, close: 1, volume: 1 }, // invalid OHLC → dropped
    { time: 'bad' },
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function reportFetch(files: Record<string, unknown>) {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    return Object.hasOwn(files, path) ? json(files[path]) : new Response('missing', { status: 404 });
  });
  return { fetcher: fetcher as unknown as typeof fetch, mock: fetcher };
}

const BASE = '/my-stock-screener/report/';
const files = {
  [`${BASE}series/index.json`]: { symbols: ['^TWOII', 'not a symbol!'] },
  [`${BASE}series/%5ETWOII.json`]: series,
};

describe('report base', () => {
  it('resolves BASE_URL/../report/ to the report directory', () => {
    expect(defaultReportBase('/my-stock-screener/atlas/')).toBe('/my-stock-screener/report/');
    expect(defaultReportBase('/')).toBe('/report/');
    expect(resolveDotSegments('https://x.github.io/a/b/../c/./d/')).toBe('https://x.github.io/a/c/d/');
    expect(resolveDotSegments('./../report/')).toBe('./../report/');
  });
});

describe('StaticSeriesProvider', () => {
  it('cleans daily rows and aggregates ISO weeks (Monday start) and calendar months in the market calendar', () => {
    const daily = cleanDailyBars(series.bars);
    expect(daily).toHaveLength(7);
    const market = getMarketProfile('^TWOII');
    const weeks = aggregateBars(daily, '1W', market);
    // Taiwan weeks start at Monday 00:00 Asia/Taipei (Sunday 16:00 UTC).
    expect(weeks.map((week) => new Date(week.time * 1000).toISOString())).toEqual([
      '2026-09-27T16:00:00.000Z',
      '2026-10-04T16:00:00.000Z',
    ]);
    expect(weeks[0]).toMatchObject({ open: 250, high: 259, low: 249, close: 252, volume: 500 });
    expect(weeks[1]).toMatchObject({ open: 252, high: 263, low: 251, close: 256, volume: 300 });
    const months = aggregateBars(daily, '1M', market);
    expect(months.map((month) => new Date(month.time * 1000).toISOString())).toEqual([
      '2026-08-31T16:00:00.000Z',
      '2026-09-30T16:00:00.000Z',
    ]);
    expect(months[0]).toMatchObject({ open: 250, high: 255, low: 249, close: 254, volume: 200 });
    expect(months[1]).toMatchObject({ open: 254, high: 263, low: 251, close: 256, volume: 600 });
    const usWeeks = aggregateBars(daily, '1W', getMarketProfile('^GSPC'));
    expect(new Date(usWeeks[0]!.time * 1000).toISOString()).toBe('2026-09-28T00:00:00.000Z');
  });

  it('serves 1D/1W/1M with quote and provenance, and rejects other intervals', async () => {
    const { fetcher, mock } = reportFetch(files);
    const provider = new StaticSeriesProvider(BASE, { fetcher });
    expect([...(await provider.listSymbols())]).toEqual(['^TWOII']);
    const daily = await provider.getBars('^twoii', '1D');
    expect(daily.bars).toHaveLength(7);
    expect(daily.market).toMatchObject({ market: 'TW', exchange: 'TPEx' });
    expect(daily.source).toContain('櫃買指數');
    expect(daily.source).toContain('TPEx 櫃買中心');
    expect(daily.quote).toMatchObject({ symbol: '^TWOII', price: 256, change: -5 });
    expect((await provider.getBars('^TWOII', '1W')).bars).toHaveLength(2);
    expect((await provider.getBars('^TWOII', '1M', { from: day('2026-09-15') })).bars).toHaveLength(1);
    await expect(provider.getBars('^TWOII', '5m')).rejects.toThrow('櫃買指數 只提供日 K、週 K、月 K');
    await expect(provider.getSymbolTimeframes()).resolves.toEqual(['1D', '1W', '1M']);
    expect((await provider.getCorporateEvents('^TWOII')).status).toBe('unavailable');
    expect((await provider.getQuote('^TWOII')).changePercent).toBeCloseTo((256 / 261 - 1) * 100);
    // index + one series file, cached afterwards
    expect(mock).toHaveBeenCalledTimes(2);
  });

  it('treats a missing index as empty and a missing series file as temporarily unavailable', async () => {
    let now = 0;
    const { fetcher, mock } = reportFetch({});
    const provider = new StaticSeriesProvider(BASE, { fetcher, now: () => now });
    expect((await provider.listSymbols()).size).toBe(0);
    expect((await provider.listSymbols()).size).toBe(0);
    expect(mock).toHaveBeenCalledTimes(1);
    now += 61_000;
    await provider.listSymbols();
    expect(mock).toHaveBeenCalledTimes(2);
    await expect(provider.getBars('^TWOII', '1D')).rejects.toBeInstanceOf(MarketDataUnavailableError);
    const mismatched = new StaticSeriesProvider(BASE, {
      fetcher: reportFetch({ [`${BASE}series/%5ETWOII.json`]: { ...series, symbol: '^TWII' } }).fetcher,
    });
    await expect(mismatched.getBars('^TWOII', '1D')).rejects.toThrow('代號不符');
  });
});

describe('ScreenerMarketRouter', () => {
  it('routes listed static series to StaticSeriesProvider and everything else to the Edge provider', async () => {
    const yahoo = JSON.parse(readFileSync(new URL('./fixtures/market/SMCI-current-1d.json', import.meta.url), 'utf8'));
    const edgeCalls: string[] = [];
    const edgeFetcher = (async (input: RequestInfo | URL) => {
      edgeCalls.push(String(input));
      return json(yahoo);
    }) as typeof fetch;
    const now = () => Date.parse('2026-10-07T20:05:00Z');
    const router = new ScreenerMarketRouter(
      new EdgeYahooProvider('https://edge.example/market-chart', { fetcher: edgeFetcher, now }),
      new StaticSeriesProvider(BASE, { fetcher: reportFetch(files).fetcher, now }),
    );
    expect(await router.route('^TWOII')).toBe(router.series);
    expect(await router.route('SMCI')).toBe(router.live);
    expect((await router.getBars('^TWOII', '1D')).source).toContain('櫃買指數');
    expect((await router.getBars('SMCI', '1D')).quote?.price).toBe(44.94);
    expect(edgeCalls).toHaveLength(1);
    await expect(router.getSymbolTimeframes('^TWOII')).resolves.toEqual(['1D', '1W', '1M']);
    await expect(router.getSymbolTimeframes('SMCI')).resolves.toEqual(['5m', '15m', '30m', '1H', '1D', '1W', '1M']);
    expect((await router.getCorporateEvents('^TWOII')).status).toBe('unavailable');
    expect((await router.getCorporateEvents('SMCI')).status).toBe('available');
    expect((await router.getQuote('^TWOII')).price).toBe(256);
  });

  it('createScreenerMarketProvider returns the bare router without cache and a cached wrapper by default', () => {
    const bare = createScreenerMarketProvider({ cache: false, proxyUrl: 'https://edge.example', reportBase: BASE });
    expect(bare).toBeInstanceOf(ScreenerMarketRouter);
    const cached = createScreenerMarketProvider({ proxyUrl: 'https://edge.example', reportBase: BASE });
    expect(cached).not.toBeInstanceOf(ScreenerMarketRouter);
    expect(cached.id).toBe('screener');
    expect(cached.supportedTimeframes).toEqual(['5m', '15m', '30m', '1H', '1D', '1W', '1M']);
    expect(typeof cached.getSymbolTimeframes).toBe('function');
  });
});

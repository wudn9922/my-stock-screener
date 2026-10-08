import 'fake-indexeddb/auto';
import { readFileSync } from 'node:fs';
import { afterEach, expect, it, vi } from 'vitest';
import { normalizeYahooCalendarResponse, normalizeYahooEvents, normalizeYahooResponse } from '../src/market-data/YahooNormalizer';
import { CachedMarketDataProvider, MarketCacheCorruptionError } from '../src/market-data/CachedMarketDataProvider';
import { DemoProvider } from '../src/market-data/DemoProvider';
import { SnapshotProvider } from '../src/market-data/SnapshotProvider';
import { YahooProvider } from '../src/market-data/YahooProvider';
import { snapshotSchema } from '../src/market-data/SnapshotSchema';
import { calendarPeriodStart, getMarketProfile, normalizeSymbol, sessionDate, SESSION_CLOSE_SOURCE_QUALIFIER, type BarResult, type MarketDataProvider } from '../src/market-data/MarketDataProvider';
import type { MarketProfile } from '../src/market-data/MarketProfile';
import { appSchema, emptySymbol, exportSchema, parseSettings, symbolStateSchema } from '../src/storage/schema';

interface YahooMeta {
  symbol: string;
  currency?: string;
  exchangeName?: string;
  exchangeTimezoneName?: string;
  regularMarketTime?: number;
  regularMarketPrice?: number;
}
interface YahooQuoteArrays {
  open: (number | null)[];
  high: (number | null)[];
  low: (number | null)[];
  close: (number | null)[];
  volume?: (number | null)[];
}
interface YahooRaw {
  chart: {
    result: Array<{
      meta: YahooMeta;
      timestamp: number[];
      indicators: { quote: YahooQuoteArrays[] };
      events?: unknown;
    }> | null;
  };
}

const generatedAt = Date.parse('2026-10-08T05:30:26Z') / 1000;
const taiwanFixture = (name: string): YahooRaw => JSON.parse(
  readFileSync(new URL(`./fixtures/market/taiwan/${name}.json`, import.meta.url), 'utf8'),
) as YahooRaw;
const usFixture = (): YahooRaw => JSON.parse(
  readFileSync(new URL('./fixtures/market/SMCI.json', import.meta.url), 'utf8'),
) as YahooRaw;

function currentTaiwanPack(withMissingDailyClose = true) {
  const dailyRaw = taiwanFixture('2330_TW_1d');
  const latestIndex = dailyRaw.chart.result![0].timestamp.length - 1;
  if (withMissingDailyClose) dailyRaw.chart.result![0].indicators.quote[0].close[latestIndex] = null;
  const weeklyRaw = taiwanFixture('2330_TW_1wk');
  const monthlyRaw = taiwanFixture('2330_TW_1mo');
  const daily = normalizeYahooResponse(dailyRaw, generatedAt, '1D', '2330.TW');
  const weekly = normalizeYahooCalendarResponse(weeklyRaw, dailyRaw, generatedAt, '1W', '2330.TW');
  const monthly = normalizeYahooCalendarResponse(monthlyRaw, dailyRaw, generatedAt, '1M', '2330.TW');
  if (!daily.quote) throw new Error('Fixture should provide a verified Taiwan quote');
  const market = getMarketProfile('2330.TW');
  return snapshotSchema.parse({
    version: 3,
    symbol: '2330.TW',
    generatedAt,
    market,
    results: { '1D': daily, '1W': weekly, '1M': monthly },
    quote: daily.quote,
    events: normalizeYahooEvents(dailyRaw, '2330.TW', generatedAt),
  });
}

function currentUsLegacyPack() {
  const raw = usFixture();
  const daily = normalizeYahooResponse(raw, generatedAt, '1D', 'SMCI');
  if (!daily.quote) throw new Error('Fixture should provide a verified US quote');
  const legacyDaily = { ...daily };
  const legacyQuote = { ...daily.quote };
  delete legacyDaily.market;
  delete legacyQuote.market;
  return {
    version: 3 as const,
    symbol: 'SMCI',
    generatedAt,
    results: { '1D': legacyDaily },
    quote: legacyQuote,
    events: normalizeYahooEvents(raw, 'SMCI', generatedAt),
  };
}

afterEach(() => vi.unstubAllGlobals());

it('accepts only canonical US/Taiwan symbols and preserves the US grammar', () => {
  expect(normalizeSymbol('  brk.b ')).toBe('BRK.B');
  expect(normalizeSymbol('2330.tw')).toBe('2330.TW');
  expect(normalizeSymbol('6488.two')).toBe('6488.TWO');
  expect(() => normalizeSymbol('2330')).toThrow();
  expect(() => normalizeSymbol('2330.X')).toThrow();
  expect(() => normalizeSymbol('2330-TW')).toThrow();
  expect(getMarketProfile()).toMatchObject({ market: 'US', currency: 'USD', exchange: 'US', timezone: 'America/New_York' });
  expect(getMarketProfile('2330.TW')).toMatchObject({ market: 'TW', currency: 'TWD', exchange: 'TWSE', timezone: 'Asia/Taipei', sessionOpenMinutes: 540, sessionCloseMinutes: 810 });
  expect(getMarketProfile('6488.TWO')).toMatchObject({ market: 'TW', currency: 'TWD', exchange: 'TPEx' });
});

it('round-trips Taiwan and US symbols through unchanged version-4 settings grammar', () => {
  const backup = exportSchema.parse({
    version: 4,
    exportedAt: '2026-10-08T00:00:00.000Z',
    app: appSchema.parse({
      watchlist: ['AAPL', '2330.TW', '6488.TWO'],
      activeSymbol: '2330.TW',
      provider: 'snapshot',
    }),
    symbols: [emptySymbol('AAPL'), emptySymbol('2330.TW'), emptySymbol('6488.TWO')],
  });
  const restored = parseSettings(JSON.stringify(backup));
  expect(restored.version).toBe(4);
  expect(restored.app.activeSymbol).toBe('2330.TW');
  expect(restored.app.watchlist).toEqual(['AAPL', '2330.TW', '6488.TWO']);
  expect(restored.symbols.map((state) => state.symbol)).toEqual(['AAPL', '2330.TW', '6488.TWO']);
  expect(() => symbolStateSchema.parse(emptySymbol('2330.X'))).toThrow();
  const invalid = structuredClone(backup);
  invalid.symbols[1].symbol = '2330.X';
  expect(() => parseSettings(JSON.stringify(invalid))).toThrow();
});

it('normalizes actual Taiwan Yahoo metadata as TWD/Taipei and rejects mismatches', () => {
  const raw = taiwanFixture('2330_TW_1d');
  const normalized = normalizeYahooResponse(raw, generatedAt, '1D', '2330.TW');
  expect(normalized).toMatchObject({ adjusted: true, priceBasis: 'split-adjusted', market: getMarketProfile('2330.TW') });
  expect(normalized.quote?.market).toEqual(getMarketProfile('2330.TW'));
  expect(normalized.quote?.price).toBe(raw.chart.result![0].meta.regularMarketPrice);

  const otcRaw = taiwanFixture('6488_TWO_1d');
  expect(normalizeYahooResponse(otcRaw, generatedAt, '1D', '6488.TWO').market).toMatchObject({
    market: 'TW', currency: 'TWD', exchange: 'TPEx', timezone: 'Asia/Taipei',
  });

  const wrongCurrency = structuredClone(raw);
  wrongCurrency.chart.result![0].meta.currency = 'USD';
  expect(() => normalizeYahooResponse(wrongCurrency, generatedAt, '1D', '2330.TW')).toThrow('TWD currency metadata');
  const wrongTimezone = structuredClone(raw);
  wrongTimezone.chart.result![0].meta.exchangeTimezoneName = 'America/New_York';
  expect(() => normalizeYahooResponse(wrongTimezone, generatedAt, '1D', '2330.TW')).toThrow('Asia/Taipei timezone metadata');
});

it('marks only Yahoo source-reported Taiwan terminal close rows and preserves them through ranges, snapshots, and cache validation', async () => {
  const raw = taiwanFixture('2330_TW_5m');
  const normalized = normalizeYahooResponse(raw, generatedAt, '5m', '2330.TW');
  const closeTime = Date.parse('2026-10-08T05:30:00Z') / 1000;
  expect(normalized.bars.at(-1)).toMatchObject({
    time: closeTime,
    open: 2550,
    high: 2550,
    low: 2550,
    close: 2550,
    volume: 0,
  });
  expect(normalized.sessionCloseObservations).toEqual([closeTime]);
  expect(normalized.source).toContain(SESSION_CLOSE_SOURCE_QUALIFIER);
  expect(normalized.bars.some(bar => bar.time === Date.parse('2026-10-08T05:25:00Z') / 1000)).toBe(false);
  const daily = normalizeYahooResponse(taiwanFixture('2330_TW_1d'), generatedAt, '1D', '2330.TW');
  expect(daily.sessionCloseObservations).toBeUndefined();
  expect(daily.source).not.toContain(SESSION_CLOSE_SOURCE_QUALIFIER);

  const pack = structuredClone(currentTaiwanPack(false));
  pack.results['5m'] = normalized as typeof pack.results['1D'];
  expect(snapshotSchema.parse(pack).results['5m']?.sessionCloseObservations).toEqual([closeTime]);
  const wrongObservation = structuredClone(pack);
  wrongObservation.results['5m']!.sessionCloseObservations = [wrongObservation.results['5m']!.bars[0].time];
  expect(snapshotSchema.safeParse(wrongObservation).success).toBe(false);
  const duplicateObservation = structuredClone(pack);
  duplicateObservation.results['5m']!.sessionCloseObservations = [closeTime, closeTime];
  expect(snapshotSchema.safeParse(duplicateObservation).success).toBe(false);
  const unqualifiedObservation = structuredClone(pack);
  unqualifiedObservation.results['5m']!.source = 'Yahoo delayed';
  expect(snapshotSchema.safeParse(unqualifiedObservation).success).toBe(false);
  const usBase = currentUsLegacyPack();
  const usDaily = usBase.results['1D']!;
  const usWithObservation = {
    ...usBase,
    results: {
      ...usBase.results,
      '5m': {
        ...usDaily,
        source: `Yahoo delayed · ${SESSION_CLOSE_SOURCE_QUALIFIER}`,
        sessionCloseObservations: [usDaily.bars.at(-1)!.time],
      },
    },
  };
  expect(snapshotSchema.safeParse(usWithObservation).success).toBe(false);

  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(normalized), { status: 200 })));
  const yahoo = new YahooProvider();
  const closeRange = await yahoo.getBars('2330.TW', '5m', { from: closeTime, to: closeTime });
  expect(closeRange.bars.map(bar => bar.time)).toEqual([closeTime]);
  expect(closeRange.sessionCloseObservations).toEqual([closeTime]);
  expect(closeRange.source).toContain(SESSION_CLOSE_SOURCE_QUALIFIER);
  const priorRange = await yahoo.getBars('2330.TW', '5m', { from: closeTime - 600, to: closeTime - 300 });
  expect(priorRange.sessionCloseObservations).toEqual([]);
  expect(priorRange.source).not.toContain(SESSION_CLOSE_SOURCE_QUALIFIER);

  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(pack), { status: 200 })));
  const snapshot = new SnapshotProvider('/market/', () => generatedAt * 1000);
  const snapshotRange = await snapshot.getBars('2330.TW', '5m', { from: closeTime, to: closeTime });
  expect(snapshotRange.sessionCloseObservations).toEqual([closeTime]);
  expect(snapshotRange.source).toContain(SESSION_CLOSE_SOURCE_QUALIFIER);

  const cacheProvider: MarketDataProvider = {
    id: 'taiwan-terminal-test',
    supportedTimeframes: ['5m'],
    async getBars() { return normalized; },
    async getQuote(symbol) { return { symbol, price: 2550, change: 0, changePercent: 0, asOf: closeTime, market: getMarketProfile(symbol) }; },
  };
  const cached = new CachedMarketDataProvider(cacheProvider, { dbName: `taiwan-terminal-${Math.random()}` });
  expect((await cached.getBars('2330.TW', '5m')).sessionCloseObservations).toEqual([closeTime]);

  const corruptProvider: MarketDataProvider = {
    ...cacheProvider,
    id: 'taiwan-terminal-corrupt-test',
    async getBars() {
      return { ...normalized, sessionCloseObservations: [normalized.bars[0].time] };
    },
  };
  const corruptCache = new CachedMarketDataProvider(corruptProvider, { dbName: `taiwan-terminal-corrupt-${Math.random()}` });
  await expect(corruptCache.getBars('2330.TW', '5m')).rejects.toBeInstanceOf(MarketCacheCorruptionError);
});

it('keeps terminal observation metadata aligned with the final duplicate source row', () => {
  const raw = taiwanFixture('2330_TW_5m');
  const closeTime = Date.parse('2026-10-08T05:30:00Z') / 1000;
  const appendDuplicate = (row: { open: number; high: number; low: number; close: number; volume: number | null }) => {
    const duplicate = structuredClone(raw);
    const result = duplicate.chart.result![0];
    const quote = result.indicators.quote[0];
    const volumes = quote.volume;
    if (!volumes) throw new Error('Fixture should contain Yahoo volume data');
    result.timestamp.push(closeTime);
    quote.open.push(row.open);
    quote.high.push(row.high);
    quote.low.push(row.low);
    quote.close.push(row.close);
    volumes.push(row.volume);
    return duplicate;
  };

  const laterFlat = normalizeYahooResponse(
    appendDuplicate({ open: 2551, high: 2551, low: 2551, close: 2551, volume: 0 }),
    generatedAt,
    '5m',
    '2330.TW',
  );
  expect(laterFlat.bars.at(-1)).toMatchObject({ time: closeTime, open: 2551, high: 2551, low: 2551, close: 2551, volume: 0 });
  expect(laterFlat.sessionCloseObservations).toEqual([closeTime]);

  const laterNonflat = normalizeYahooResponse(
    appendDuplicate({ open: 2550, high: 2551, low: 2550, close: 2551, volume: 0 }),
    generatedAt,
    '5m',
    '2330.TW',
  );
  expect(laterNonflat.bars.at(-1)).toMatchObject({ time: closeTime, open: 2550, high: 2551, low: 2550, close: 2551, volume: 0 });
  expect(laterNonflat.sessionCloseObservations).toBeUndefined();
  expect(laterNonflat.source).not.toContain(SESSION_CLOSE_SOURCE_QUALIFIER);

  const laterUnreportedVolume = normalizeYahooResponse(
    appendDuplicate({ open: 2550, high: 2550, low: 2550, close: 2550, volume: null }),
    generatedAt,
    '5m',
    '2330.TW',
  );
  expect(laterUnreportedVolume.bars.at(-1)).toMatchObject({ time: closeTime, volume: 0 });
  expect(laterUnreportedVolume.sessionCloseObservations).toBeUndefined();
});

it('uses Taipei calendar boundaries across the previous UTC month and week', () => {
  const taiwan = getMarketProfile('2330.TW');
  const us = getMarketProfile('AAPL');
  const taiwanOct1 = Date.parse('2026-09-30T16:30:00Z') / 1000;
  expect(sessionDate(taiwanOct1, taiwan)).toBe('2026-10-01');
  expect(sessionDate(taiwanOct1, us)).toBe('2026-09-30');
  expect(new Date(calendarPeriodStart(taiwanOct1, '1M', taiwan) * 1000).toISOString()).toBe('2026-09-30T16:00:00.000Z');
  expect(new Date(calendarPeriodStart(taiwanOct1, '1W', taiwan) * 1000).toISOString()).toBe('2026-09-27T16:00:00.000Z');
  expect(new Date(calendarPeriodStart(taiwanOct1, '1M', us) * 1000).toISOString()).toBe('2026-09-01T00:00:00.000Z');
});

it('heals the validated Taiwan daily close and aligns current native weekly/monthly bars to the local quote', () => {
  const pack = currentTaiwanPack();
  const daily = pack.results['1D']!;
  const weekly = pack.results['1W']!;
  const monthly = pack.results['1M']!;
  const market = getMarketProfile('2330.TW');
  expect(daily.normalization?.healedDailyClose).toMatchObject({
    method: 'validated-regular-market-price',
    time: daily.latestBarAt,
    quoteAsOf: pack.quote.asOf,
  });
  expect(weekly.normalization?.currentPeriod).toMatchObject({ timeframe: '1W', periodStart: weekly.bars.at(-1)!.time });
  expect(monthly.normalization?.currentPeriod).toMatchObject({ timeframe: '1M', periodStart: monthly.bars.at(-1)!.time });
  expect(daily.quote?.price).toBe(pack.quote.price);
  expect(weekly.bars.at(-1)?.close).toBe(pack.quote.price);
  expect(monthly.bars.at(-1)?.close).toBe(pack.quote.price);
  expect(weekly.bars.at(-1)?.time).toBe(calendarPeriodStart(pack.quote.asOf, '1W', market));
  expect(monthly.bars.at(-1)?.time).toBe(calendarPeriodStart(pack.quote.asOf, '1M', market));
  expect(new Date(monthly.bars.at(-1)!.time * 1000).toISOString()).toBe('2026-09-30T16:00:00.000Z');
  expect(snapshotSchema.parse(pack).market).toEqual(market);

  const missingProfile = structuredClone(pack);
  delete missingProfile.market;
  expect(snapshotSchema.safeParse(missingProfile).success).toBe(false);
  const usdQuote = structuredClone(pack);
  usdQuote.quote.market = { ...market, currency: 'USD' } as MarketProfile;
  expect(snapshotSchema.safeParse(usdQuote).success).toBe(false);
  const utcBucket = structuredClone(pack);
  utcBucket.results['1M']!.bars.at(-1)!.time = Date.UTC(2026, 9, 1) / 1000;
  expect(snapshotSchema.safeParse(utcBucket).success).toBe(false);
});

it('keeps Yahoo split-adjusted 0050 prices unchanged and accepts legacy US packs without market metadata', () => {
  const raw = taiwanFixture('0050_TW_1d');
  const result = normalizeYahooResponse(raw, generatedAt, '1D', '0050.TW');
  const source = raw.chart.result![0];
  const latestSourceIndex = source.timestamp.length - 1;
  expect(result.priceBasis).toBe('split-adjusted');
  expect(result.bars.at(-1)?.close).toBe(source.indicators.quote[0].close[latestSourceIndex]);
  expect(result.bars.at(-1)?.close).toBeCloseTo(114.95, 2);
  expect(normalizeYahooEvents(raw, '0050.TW', generatedAt).events.filter((event) => event.type === 'split')).toEqual([]);

  const legacyUsPack = currentUsLegacyPack();
  expect(snapshotSchema.parse(legacyUsPack).symbol).toBe('SMCI');
});

it('publishes snapshot interval coverage by symbol and the cache forwards that capability', async () => {
  const pack = currentTaiwanPack(false);
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(pack), { status: 200 })));
  const snapshot = new SnapshotProvider('/market/', () => generatedAt * 1000);
  expect(await snapshot.getSymbolTimeframes('2330.TW')).toEqual(['1D', '1W', '1M']);
  const cached = new CachedMarketDataProvider(snapshot, { dbName: `taiwan-cache-${Math.random()}` });
  expect(await cached.getSymbolTimeframes('2330.TW')).toEqual(['1D', '1W', '1M']);
  expect((await cached.getBars('2330.TW', '1D')).market).toEqual(getMarketProfile('2330.TW'));
  expect(new YahooProvider().cacheVersion).toBe('marketprofile-v4');
  expect(snapshot.cacheVersion).toBe('marketprofile-v4');
});

it('isolates Taiwan and US provider cache identities and rejects Taiwan Demo prices', async () => {
  const seen: string[] = [];
  const provider: MarketDataProvider = {
    id: 'mixed-market-test',
    supportedTimeframes: ['1D'],
    async getBars(symbol): Promise<BarResult> {
      seen.push(symbol);
      return {
        bars: [{ time: 100, open: 10, high: 11, low: 9, close: 10, volume: 100 }],
        source: 'test', session: 'regular', delayed: true, adjusted: true,
        market: getMarketProfile(symbol),
      };
    },
    async getQuote(symbol) {
      return { symbol, price: 10, change: 0, changePercent: 0, asOf: 100, market: getMarketProfile(symbol) };
    },
  };
  const cached = new CachedMarketDataProvider(provider, { dbName: `mixed-market-${Math.random()}` });
  const us = await cached.getBars('AAPL', '1D');
  const tw = await cached.getBars('2330.TW', '1D');
  expect(us.market?.currency).toBe('USD');
  expect(tw.market?.currency).toBe('TWD');
  expect(seen).toEqual(['AAPL', '2330.TW']);
  await expect(new DemoProvider().getBars('2330.TW', '1D')).rejects.toThrow('Taiwan Demo unavailable');
  await expect(new DemoProvider().getQuote('2330.TW')).rejects.toThrow('Taiwan Demo unavailable');
});

it('rejects fresh Taiwan cache data without the expected market profile', async () => {
  const provider: MarketDataProvider = {
    id: 'missing-market-profile',
    supportedTimeframes: ['1D'],
    async getBars(): Promise<BarResult> {
      return { bars: [{ time: 100, open: 10, high: 11, low: 9, close: 10, volume: 100 }], source: 'bad', session: 'regular', delayed: true, adjusted: true };
    },
    async getQuote(symbol) {
      return { symbol, price: 10, change: 0, changePercent: 0, asOf: 100 };
    },
  };
  const cached = new CachedMarketDataProvider(provider, { dbName: `missing-market-${Math.random()}` });
  await expect(cached.getBars('2330.TW', '1D')).rejects.toBeInstanceOf(MarketCacheCorruptionError);
});

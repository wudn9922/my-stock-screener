import 'fake-indexeddb/auto';
import { expect, it } from 'vitest';
import {
  CachedMarketDataProvider,
  MarketCacheCorruptionError,
  MARKET_CACHE_VERSION,
} from '../src/market-data/CachedMarketDataProvider';
import type {
  Bar,
  BarResult,
  CorporateEventsResult,
  MarketDataProvider,
  Quote,
  Timeframe,
} from '../src/market-data/MarketDataProvider';
import { MarketDataUnavailableError } from '../src/market-data/ProviderErrors';

function sampleResult(close = 10): BarResult {
  return {
    bars: [{ time: 100, open: close, high: close + 1, low: close - 1, close, volume: 100 }],
    source: 'delayed test source',
    session: 'regular',
    delayed: true,
    adjusted: false,
    asOf: 200,
    latestBarAt: 100,
    dataState: 'delayed',
  };
}

function testProvider(
  id: string,
  getBars: MarketDataProvider['getBars'],
): MarketDataProvider {
  return {
    id,
    supportedTimeframes: ['5m'],
    getBars,
    async getQuote(symbol): Promise<Quote> {
      return { symbol, price: 10, change: 0, changePercent: 0, asOf: 100 };
    },
  };
}

function uniqueDbName() {
  return `atlas-market-cache-test-${Math.random().toString(36).slice(2)}`;
}

it('keys the market cache by provider, symbol, timeframe, and exact range, and returns clones', async () => {
  let callsA = 0;
  let callsB = 0;
  const dbName = uniqueDbName();
  const providerA = new CachedMarketDataProvider(
    testProvider('source-a', async () => {
      callsA++;
      return sampleResult(10);
    }),
    { dbName },
  );
  const providerB = new CachedMarketDataProvider(
    testProvider('source-b', async () => {
      callsB++;
      return sampleResult(20);
    }),
    { dbName },
  );

  const first = await providerA.getBars('aapl', '5m', { from: 1, to: 2 });
  expect(first.cacheStatus).toBe('fresh');
  first.bars[0].close = 999;
  const sameKey = await providerA.getBars('AAPL', '5m', { from: 1, to: 2 });
  const differentRange = await providerA.getBars('AAPL', '5m', { from: 1, to: 3 });
  const isolatedProvider = await providerB.getBars('AAPL', '5m', { from: 1, to: 2 });

  expect(sameKey).toMatchObject({ cacheStatus: 'cached', bars: [{ close: 10 }] });
  expect(differentRange).toMatchObject({ cacheStatus: 'fresh', bars: [{ close: 10 }] });
  expect(isolatedProvider).toMatchObject({ bars: [{ close: 20 }] });
  expect(callsA).toBe(2);
  expect(callsB).toBe(1);
});

it('uses TTLs and labels expired offline fallback without pretending it is current', async () => {
  let now = 1000;
  let fail = false;
  let calls = 0;
  const cached = new CachedMarketDataProvider(
    testProvider('yahoo-test', async () => {
      calls++;
      if (fail) throw new MarketDataUnavailableError('network unavailable');
      return sampleResult();
    }),
    { dbName: uniqueDbName(), ttlMs: 100, now: () => now },
  );
  await cached.getBars('AAPL', '5m');
  now += 50;
  expect((await cached.getBars('AAPL', '5m')).cacheStatus).toBe('cached');
  expect(calls).toBe(1);
  now += 51;
  fail = true;
  const stale = await cached.getBars('AAPL', '5m');
  expect(stale).toMatchObject({
    cacheStatus: 'stale',
    asOf: 200,
    latestBarAt: 100,
    bars: [{ time: 100 }],
  });
  expect(stale.source).toContain('STALE OFFLINE CACHE');
  await expect(cached.getBars('MSFT', '5m')).rejects.toThrow('network unavailable');
});

it('bounds entries and does not use an evicted record as offline fallback', async () => {
  let now = 1000;
  let fail = false;
  const cached = new CachedMarketDataProvider(
    testProvider('bounded-cache', async () => {
      if (fail) throw new MarketDataUnavailableError('network unavailable');
      return sampleResult();
    }),
    { dbName: uniqueDbName(), ttlMs: 1000, maxEntries: 1, now: () => now },
  );
  await cached.getBars('AAPL', '5m');
  now++;
  await cached.getBars('MSFT', '5m');
  now += 1001;
  fail = true;
  await expect(cached.getBars('AAPL', '5m')).rejects.toThrow('network unavailable');
  await expect(cached.getBars('MSFT', '5m')).resolves.toMatchObject({ cacheStatus: 'stale' });
});

it('deduplicates provider requests while one caller can abort without cancelling another', async () => {
  let calls = 0;
  let start!: () => void;
  let finish!: (value: BarResult) => void;
  const started = new Promise<void>((resolve) => (start = resolve));
  const pending = new Promise<BarResult>((resolve) => (finish = resolve));
  const cached = new CachedMarketDataProvider(
    testProvider('dedup-test', async () => {
      calls++;
      start();
      return pending;
    }),
    { dbName: uniqueDbName() },
  );
  const controller = new AbortController();
  const first = cached.getBars('AAPL', '5m', undefined, controller.signal);
  await started;
  const second = cached.getBars('AAPL', '5m');
  controller.abort();
  finish(sampleResult());
  await expect(first).rejects.toMatchObject({ name: 'AbortError' });
  expect(await second).toMatchObject({ cacheStatus: 'fresh', bars: [{ close: 10 }] });
  expect(calls).toBe(1);
});

it('does not use stale data after cancellation or a non-availability response error', async () => {
  let now = 1000;
  let failure: Error | null = null;
  const cached = new CachedMarketDataProvider(
    testProvider('error-test', async () => {
      if (failure) throw failure;
      return sampleResult();
    }),
    { dbName: uniqueDbName(), ttlMs: 10, now: () => now },
  );
  await cached.getBars('AAPL', '5m');
  now += 11;
  failure = new DOMException('cancelled', 'AbortError');
  await expect(cached.getBars('AAPL', '5m')).rejects.toMatchObject({ name: 'AbortError' });
  failure = new Error('provider returned malformed data');
  await expect(cached.getBars('AAPL', '5m')).rejects.toThrow('malformed data');
});

it('rejects unsupported timeframes before consulting any cache entry', async () => {
  let calls = 0;
  const provider = new CachedMarketDataProvider(
    testProvider('tf-test', async () => {
      calls++;
      return sampleResult();
    }),
    { dbName: uniqueDbName() },
  );
  await expect(provider.getBars('AAPL', '4H' as Timeframe)).rejects.toThrow('Unsupported timeframe');
  expect(calls).toBe(0);
});

it('rejects persisted cache bars with inconsistent OHLC or nonascending times', async () => {
  for (const corrupt of [
    (bars: Bar[]) => [{ ...bars[0], high: bars[0].open - 1 }],
    (bars: Bar[]) => [{ ...bars[0], time: bars[0].time + 1 }, bars[0]],
  ]) {
    const dbName = uniqueDbName();
    let calls = 0;
    const cached = new CachedMarketDataProvider(
      testProvider('corrupt-bars', async () => {
        calls++;
        return sampleResult();
      }),
      { dbName },
    );
    await cached.getBars('AAPL', '5m');

    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(dbName, MARKET_CACHE_VERSION);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const transaction = database.transaction('entries', 'readwrite');
    const store = transaction.objectStore('entries');
    const key = JSON.stringify(['corrupt-bars', 'AAPL', 'bars', '5m', null, null]);
    const record = await new Promise<{ key: string; value: BarResult }>((resolve, reject) => {
      const request = store.get(key);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    record.value.bars = corrupt(record.value.bars);
    store.put(record);
    await new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });

    await expect(cached.getBars('AAPL', '5m')).rejects.toBeInstanceOf(MarketCacheCorruptionError);
    expect(calls).toBe(1);
    database.close();
  }
});

it('rejects provider responses whose quote or event identity does not match the request', async () => {
  let barCalls = 0;
  const quoteProvider: MarketDataProvider = {
    id: 'wrong-quote-symbol',
    supportedTimeframes: ['5m'],
    async getBars() {
      barCalls++;
      return sampleResult();
    },
    async getQuote(): Promise<Quote> {
      return { symbol: 'MSFT', price: 10, change: 0, changePercent: 0, asOf: 100 };
    },
  };
  const quoteCache = new CachedMarketDataProvider(quoteProvider, { dbName: uniqueDbName() });
  await expect(quoteCache.getQuote('AAPL')).rejects.toBeInstanceOf(MarketCacheCorruptionError);

  const eventProvider: MarketDataProvider = {
    id: 'wrong-event-symbol',
    supportedTimeframes: ['5m'],
    async getBars() {
      return sampleResult();
    },
    async getQuote(symbol) {
      return { symbol, price: 10, change: 0, changePercent: 0, asOf: 100 };
    },
    async getCorporateEvents(): Promise<CorporateEventsResult> {
      return {
        status: 'available',
        source: 'test source',
        asOf: 200,
        events: [
          { symbol: 'MSFT', type: 'split', time: 100, value: 2, source: 'test', numerator: 2, denominator: 1 },
        ],
      };
    },
  };
  const eventCache = new CachedMarketDataProvider(eventProvider, { dbName: uniqueDbName() });
  await expect(eventCache.getCorporateEvents('AAPL')).rejects.toBeInstanceOf(MarketCacheCorruptionError);
  expect(barCalls).toBe(0);
});

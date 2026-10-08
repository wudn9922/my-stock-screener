import 'fake-indexeddb/auto';
import { readFile } from 'node:fs/promises';
import { describe, expect, it, vi } from 'vitest';
import { FinancialNormalizer } from '../src/fundamentals/FinancialNormalizer';
import { financialMetrics } from '../src/fundamentals/FundamentalsProvider';
import {
  FinancialSnapshotProvider,
  FinancialSnapshotUnavailableError,
  IndexedDbFinancialSnapshotCache,
} from '../src/fundamentals/FinancialSnapshotProvider';
import { financialSnapshotSchema, type FinancialSnapshot } from '../src/fundamentals/FinancialSnapshotSchema';

const rawAapl = JSON.parse(await readFile('tests/fixtures/sec/AAPL.json', 'utf8')) as {
  cik: number;
  entityName: string;
  facts: Record<string, unknown>;
};
const normalizer = new FinancialNormalizer();
const cik = String(rawAapl.cik).padStart(10, '0');
const source = `https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`;

function snapshotFor(symbol = 'AAPL', generatedAt = Math.floor(Date.now() / 1000)): FinancialSnapshot {
  const quarterly = normalizer.normalize(rawAapl, 'AAPL', 'quarterly')
    .sort((a, b) => b.periodEnd.localeCompare(a.periodEnd) || b.filingDate.localeCompare(a.filingDate))
    .slice(0, 100)
    .map((record) => ({ ...record, symbol }));
  const annual = normalizer.normalize(rawAapl, 'AAPL', 'annual')
    .sort((a, b) => b.periodEnd.localeCompare(a.periodEnd) || b.filingDate.localeCompare(a.filingDate))
    .slice(0, 100)
    .map((record) => ({ ...record, symbol }));
  return financialSnapshotSchema.parse({
    version: 1,
    symbol,
    cik,
    issuerName: rawAapl.entityName,
    source,
    fetchedAt: generatedAt,
    generatedAt,
    quarterly,
    annual,
  });
}

function smallSnapshotFor(symbol: string, generatedAt = Math.floor(Date.now() / 1000)): FinancialSnapshot {
  const smallCik = '0000000001';
  const smallSource = `https://data.sec.gov/api/xbrl/companyfacts/CIK${smallCik}.json`;
  const accession = '0000000001-25-000001';
  const metricValues = Object.fromEntries(financialMetrics.map((metric) => [metric, null]));
  const record = {
    ...metricValues,
    symbol,
    issuerName: 'Example Issuer',
    cik: smallCik,
    period: 'annual',
    fiscalYear: 2024,
    fiscalQuarter: null,
    periodStart: '2024-01-01',
    periodEnd: '2024-12-31',
    filingDate: '2025-02-01',
    form: '10-K',
    accession,
    sourceConcepts: {
      revenue: {
        derived: false,
        calculation: null,
        inputs: [{
          concept: 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
          unit: 'USD',
          value: 100,
          accession,
          form: '10-K',
          filed: '2025-02-01',
          secUrl: smallSource,
          filingUrl: 'https://www.sec.gov/Archives/edgar/data/1/000000000125000001/',
          start: '2024-01-01',
          end: '2024-12-31',
          fiscalYearFocus: 2024,
          fiscalPeriodFocus: 'FY',
          kind: 'duration',
        }],
      },
    },
    warnings: [],
    revenue: 100,
  };
  return financialSnapshotSchema.parse({
    version: 1,
    symbol,
    cik: smallCik,
    issuerName: 'Example Issuer',
    source: smallSource,
    fetchedAt: generatedAt,
    generatedAt,
    quarterly: [],
    annual: [record],
  });
}

describe('same-origin financial snapshot provider', () => {
  it('returns network records with cloned metadata, then uses validated IndexedDB data offline', async () => {
    const dbName = 'financial-provider-' + crypto.randomUUID();
    const cachedAt = Date.now();
    const cache = new IndexedDbFinancialSnapshotCache(dbName, () => cachedAt);
    const pack = snapshotFor();
    const requests: string[] = [];
    let fail = false;
    const fetcher = (async (input: RequestInfo | URL) => {
      requests.push(String(input));
      if (fail) throw new Error('offline');
      return new Response(JSON.stringify(pack), { status: 200 });
    }) as typeof fetch;
    const provider = new FinancialSnapshotProvider({
      base: '/base/financial-data/',
      now: () => cachedAt,
      fetcher,
      cache,
    });
    const annual = await provider.getFinancials('aapl', 'annual');
    expect(annual).toEqual(pack.annual);
    expect(requests).toEqual(['/base/financial-data/AAPL.json']);
    expect(provider.getSnapshotMetadata('AAPL')).toMatchObject({ cacheStatus: 'fresh', offline: false, stale: false });
    const exposed = provider.getSnapshotMetadata('AAPL')!;
    exposed.source = 'changed';
    expect(provider.getSnapshotMetadata('AAPL')!.source).toBe(source);

    fail = true;
    const offlineProvider = new FinancialSnapshotProvider({
      base: '/base/financial-data/',
      now: () => cachedAt,
      fetcher,
      cache,
    });
    expect(await offlineProvider.getFinancials('AAPL', 'quarterly')).toEqual(pack.quarterly);
    expect(offlineProvider.getSnapshotMetadata('AAPL')).toMatchObject({ cacheStatus: 'cached', offline: true, stale: false });
  });

  it('marks expired network packs stale and never returns an invalid symbol, period, or aborted request', async () => {
    const now = Date.now();
    const stale = snapshotFor('AAPL', Math.floor(now / 1000) - 25 * 60 * 60);
    const cache = new IndexedDbFinancialSnapshotCache('financial-stale-' + crypto.randomUUID(), () => now);
    let fetchCount = 0;
    const fetcher = (async () => {
      fetchCount++;
      return new Response(JSON.stringify(stale), { status: 200 });
    }) as typeof fetch;
    const provider = new FinancialSnapshotProvider({ base: '/financial-data/', now: () => now, fetcher, cache });
    expect(await provider.getFinancials('AAPL', 'annual')).toEqual(stale.annual);
    expect(provider.getSnapshotMetadata('AAPL')).toMatchObject({ cacheStatus: 'stale', offline: false, stale: true });

    await expect(provider.getFinancials('AAPL', 'monthly' as 'annual')).rejects.toThrow('period');
    await expect(provider.getFinancials('bad ticker', 'annual')).rejects.toThrow();
    const controller = new AbortController();
    controller.abort();
    await expect(provider.getFinancials('AAPL', 'annual', controller.signal)).rejects.toThrow();
    expect(fetchCount).toBe(1);

    const lateController = new AbortController();
    const lateAbortProvider = new FinancialSnapshotProvider({
      base: '/financial-data/',
      now: () => now,
      fetcher: (async () => {
        lateController.abort();
        return new Response(JSON.stringify(stale), { status: 200 });
      }) as typeof fetch,
      cache: new IndexedDbFinancialSnapshotCache('financial-abort-' + crypto.randomUUID(), () => now),
    });
    await expect(lateAbortProvider.getFinancials('AAPL', 'annual', lateController.signal)).rejects.toThrow();
    expect(lateAbortProvider.getSnapshotMetadata('AAPL')).toBeUndefined();
  });

  it('serves valid network data if IndexedDB writes fail and clears metadata when no valid pack remains', async () => {
    const pack = snapshotFor();
    let failNetwork = false;
    const provider = new FinancialSnapshotProvider({
      base: '/financial-data/',
      fetcher: (async () => {
        if (failNetwork) throw new Error('offline');
        return new Response(JSON.stringify(pack), { status: 200 });
      }) as typeof fetch,
      cache: {
        get: async () => undefined,
        put: async () => { throw new Error('storage disabled'); },
      },
    });
    expect(await provider.getFinancials('AAPL', 'annual')).toEqual(pack.annual);
    expect(provider.getSnapshotMetadata('AAPL')).toMatchObject({ cacheStatus: 'fresh', offline: false });
    failNetwork = true;
    await expect(provider.getFinancials('AAPL', 'annual')).rejects.toBeInstanceOf(FinancialSnapshotUnavailableError);
    expect(provider.getSnapshotMetadata('AAPL')).toBeUndefined();
  });

  it('bounds the disposable IndexedDB cache to 32 validated packs', async () => {
    const now = Date.now();
    const cache = new IndexedDbFinancialSnapshotCache('financial-limit-' + crypto.randomUUID(), () => now);
    let offline = false;
    const fetcher = (async (input: RequestInfo | URL) => {
      if (offline) throw new Error('offline');
      const symbol = decodeURIComponent(String(input).split('/').at(-1)!.replace(/\.json$/, ''));
      return new Response(JSON.stringify(smallSnapshotFor(symbol)), { status: 200 });
    }) as typeof fetch;
    const provider = new FinancialSnapshotProvider({ base: '/financial-data/', now: () => now, fetcher, cache });
    for (let i = 0; i < 33; i++) {
      await provider.getFinancials(`A${String(i).padStart(3, '0')}`, 'annual');
    }
    expect(provider.getSnapshotMetadata('A000')).toBeUndefined();
    expect(provider.getSnapshotMetadata('A032')).toBeDefined();
    offline = true;
    await expect(provider.getFinancials('A000', 'annual')).rejects.toBeInstanceOf(FinancialSnapshotUnavailableError);
    expect(await provider.getFinancials('A032', 'annual')).toHaveLength(1);
  });

  it('returns a valid cached snapshot when the LRU write fails from simulated quota exhaustion', async () => {
    const cache = new IndexedDbFinancialSnapshotCache('financial-lru-failure-' + crypto.randomUUID());
    const pack = snapshotFor();
    await cache.put(pack);
    const db = await (cache as unknown as { db: Promise<{ put: (...args: never[]) => Promise<unknown> }> }).db;
    const lruWrite = vi.spyOn(db, 'put').mockRejectedValueOnce(new DOMException('Storage quota exceeded', 'QuotaExceededError'));

    expect(await cache.get('AAPL')).toEqual(pack);

    lruWrite.mockRestore();
    expect(await cache.get('AAPL')).toEqual(pack);
  });

  it('observes transaction completion before an IDB request failure can abort the cache write', async () => {
    let rejectCompletion!: (reason: Error) => void;
    const completion = new Promise<void>((_resolve, reject) => { rejectCompletion = reject; });
    const completionCatch = vi.spyOn(completion, 'catch');
    const requestError = new Error('simulated IDB write failure');
    const transaction = {
      store: {
        getAll: vi.fn(async () => []),
        put: vi.fn(async () => {
          rejectCompletion(requestError);
          throw requestError;
        }),
        delete: vi.fn(async () => undefined),
      },
      done: completion,
    };
    const cache = new IndexedDbFinancialSnapshotCache('financial-transaction-failure-' + crypto.randomUUID());
    (cache as unknown as { db: Promise<never> }).db = Promise.resolve({
      transaction: vi.fn(() => transaction),
    } as never);

    await cache.put(snapshotFor());

    expect(completionCatch).toHaveBeenCalledTimes(1);
    await expect(completion).rejects.toThrow('simulated IDB write failure');
  });
});

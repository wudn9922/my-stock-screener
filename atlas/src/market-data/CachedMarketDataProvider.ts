import { storageName } from '../app/HostingMode';
import { openDB, type DBSchema, type IDBPDatabase } from 'idb';
import type {
  BarResult,
  CacheStatus,
  CorporateEventsResult,
  MarketDataProvider,
  MarketRange,
  Bar,
  Quote,
  Timeframe,
} from './MarketDataProvider';
import { normalizeSymbol } from './MarketDataProvider';
import {
  getMarketProfile,
  isSessionCloseObservation,
  sameMarketProfile,
  SESSION_CLOSE_SOURCE_QUALIFIER,
} from './MarketProfile';
import { MarketDataUnavailableError } from './ProviderErrors';

export const MARKET_CACHE_DATABASE = storageName('atlas-market-cache');
export const MARKET_CACHE_VERSION = 1;
export const DEFAULT_DEMO_TTL_MS = 24 * 60 * 60 * 1000;
export const DEFAULT_YAHOO_TTL_MS = 60 * 1000;
const DEFAULT_MAX_ENTRIES = 150;

type CacheKind = 'bars' | 'quote' | 'events';
type CacheValue = BarResult | Quote | CorporateEventsResult;

interface NormalizedRange {
  from: number | null;
  to: number | null;
}

interface CacheIdentity {
  key: string;
  providerId: string;
  symbol: string;
  kind: CacheKind;
  timeframe: string;
  range: NormalizedRange;
}

interface MarketCacheEntry extends CacheIdentity {
  createdAt: number;
  expiresAt: number;
  value: CacheValue;
}

interface MarketCacheSchema extends DBSchema {
  entries: {
    key: string;
    value: MarketCacheEntry;
  };
}

export interface CachedMarketDataOptions {
  dbName?: string;
  ttlMs?: number;
  maxEntries?: number;
  now?: () => number;
}

export class MarketCacheCorruptionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'MarketCacheCorruptionError';
  }
}

function normalizeRange(range?: MarketRange): NormalizedRange {
  const from = range?.from ?? null;
  const to = range?.to ?? null;
  if ((from !== null && !Number.isFinite(from)) || (to !== null && !Number.isFinite(to))) {
    throw new Error('Market data range must contain finite timestamps');
  }
  if (from !== null && to !== null && from > to) throw new Error('Market data range is reversed');
  return { from, to };
}

function makeIdentity(
  providerId: string,
  symbolInput: string,
  kind: CacheKind,
  timeframe: string,
  range: NormalizedRange,
): CacheIdentity {
  const symbol = normalizeSymbol(symbolInput);
  const key = JSON.stringify([providerId, symbol, kind, timeframe, range.from, range.to]);
  return { key, providerId, symbol, kind, timeframe, range };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function validateMarket(value: unknown, expectedSymbol: string): void {
  const expected = getMarketProfile(expectedSymbol);
  if (expected.market === 'TW' && !sameMarketProfile(value, expected)) {
    throw new MarketCacheCorruptionError('Taiwan market data is missing or has mismatched market metadata');
  }
  if (value !== undefined && !sameMarketProfile(value, expected)) {
    throw new MarketCacheCorruptionError('Market metadata does not match the requested symbol');
  }
}

function validateQuote(value: unknown, expectedSymbol: string): void {
  if (!isObject(value) || value.symbol !== expectedSymbol) {
    throw new MarketCacheCorruptionError('Cached quote does not match the requested symbol');
  }
  if (
    typeof value.price !== 'number' || !Number.isFinite(value.price) || value.price <= 0 ||
    ![value.change, value.changePercent, value.asOf].every(part => typeof part === 'number' && Number.isFinite(part)) ||
    (value.retrievedAt !== undefined && (typeof value.retrievedAt !== 'number' || !Number.isFinite(value.retrievedAt)))
  ) {
    throw new MarketCacheCorruptionError('Cached quote has invalid numeric fields');
  }
  validateMarket(value.market, expectedSymbol);
}

function validateValue(
  kind: CacheKind,
  value: unknown,
  expectedSymbol: string,
  timeframe?: string,
): asserts value is CacheValue {
  if (!isObject(value)) throw new MarketCacheCorruptionError('Cached market data is not an object');
  if (kind === 'bars') {
    if (
      !Array.isArray(value.bars) ||
      typeof value.source !== 'string' ||
      !['regular', 'extended'].includes(String(value.session)) ||
      typeof value.delayed !== 'boolean' ||
      typeof value.adjusted !== 'boolean'
    ) {
      throw new MarketCacheCorruptionError('Cached bars have an invalid result shape');
    }
    validateMarket(value.market, expectedSymbol);
    if (value.quote !== undefined) validateQuote(value.quote, expectedSymbol);
    for (const bar of value.bars) {
      if (
        !isObject(bar) ||
        typeof bar.time !== 'number' ||
        !Number.isFinite(bar.time) ||
        bar.time <= 0 ||
        !Number.isInteger(bar.time) ||
        ![bar.open, bar.high, bar.low, bar.close, bar.volume].every(
          (part) => typeof part === 'number' && Number.isFinite(part) && part >= 0,
        ) ||
        (bar.high as number) < Math.max(bar.open as number, bar.close as number, bar.low as number) ||
        (bar.low as number) > Math.min(bar.open as number, bar.close as number, bar.high as number)
      ) {
        throw new MarketCacheCorruptionError('Cached bars contain invalid values');
      }
    }
    for (let index = 1; index < value.bars.length; index++) {
      if ((value.bars[index - 1].time as number) >= (value.bars[index].time as number)) {
        throw new MarketCacheCorruptionError('Cached bars are not strictly time sorted');
      }
    }
    if (value.sessionCloseObservations !== undefined) {
      if (!Array.isArray(value.sessionCloseObservations)) {
        throw new MarketCacheCorruptionError('Cached session-close observations are invalid');
      }
      const observations = value.sessionCloseObservations;
      if (new Set(observations).size !== observations.length) {
        throw new MarketCacheCorruptionError('Cached session-close observations are duplicated');
      }
      if (observations.length) {
        const market = getMarketProfile(expectedSymbol);
        if (
          market.market !== 'TW' ||
          ['1D', '1W', '1M'].includes(timeframe ?? '') ||
          !value.source.includes(SESSION_CLOSE_SOURCE_QUALIFIER)
        ) {
          throw new MarketCacheCorruptionError('Cached session-close observations have invalid provenance');
        }
        for (const time of observations) {
          if (typeof time !== 'number' || !Number.isFinite(time) || !Number.isInteger(time) || time <= 0) {
            throw new MarketCacheCorruptionError('Cached session-close observation time is invalid');
          }
          const bar = value.bars.find(candidate => candidate.time === time);
          if (!bar || !isSessionCloseObservation(bar as unknown as Bar, market)) {
            throw new MarketCacheCorruptionError('Cached session-close observation does not match a source bar');
          }
        }
      }
    }
    if (
      (value.asOf !== undefined &&
        (typeof value.asOf !== 'number' || !Number.isFinite(value.asOf))) ||
      (value.latestBarAt !== undefined &&
        (typeof value.latestBarAt !== 'number' || !Number.isFinite(value.latestBarAt))) ||
      (value.latestBarAt !== undefined &&
        value.bars.length > 0 &&
        value.latestBarAt !== value.bars[value.bars.length - 1].time)
    ) {
      throw new MarketCacheCorruptionError('Cached bar result has invalid time metadata');
    }
  } else if (kind === 'quote') {
    validateQuote(value, expectedSymbol);
  } else {
    if (
      !['available', 'unavailable'].includes(String(value.status)) ||
      !Array.isArray(value.events) ||
      typeof value.source !== 'string' ||
      (value.status === 'available' &&
        (typeof value.asOf !== 'number' || !Number.isFinite(value.asOf))) ||
      (value.status === 'unavailable' &&
        value.asOf !== undefined &&
        (typeof value.asOf !== 'number' || !Number.isFinite(value.asOf)))
    ) {
      throw new MarketCacheCorruptionError('Cached corporate events have an invalid result shape');
    }
    for (const event of value.events) {
      if (
        !isObject(event) ||
        event.symbol !== expectedSymbol ||
        !['dividend', 'split'].includes(String(event.type)) ||
        ![event.time, event.value].every(
          (part) => typeof part === 'number' && Number.isFinite(part),
        ) ||
        (event.time as number) <= 0 ||
        typeof event.source !== 'string' ||
        (event.currency !== undefined && typeof event.currency !== 'string') ||
        (event.numerator !== undefined &&
          (typeof event.numerator !== 'number' || !Number.isFinite(event.numerator))) ||
        (event.denominator !== undefined &&
          (typeof event.denominator !== 'number' || !Number.isFinite(event.denominator))) ||
        (event.type === 'split' &&
          (typeof event.numerator !== 'number' ||
            !Number.isFinite(event.numerator) ||
            event.numerator <= 0 ||
            typeof event.denominator !== 'number' ||
            !Number.isFinite(event.denominator) ||
            event.denominator <= 0))
      ) {
        throw new MarketCacheCorruptionError('Cached corporate events contain invalid values');
      }
    }
    if (value.status === 'unavailable' && value.events.length) {
      throw new MarketCacheCorruptionError('Unavailable corporate events cannot contain events');
    }
  }
}

function assertIdentity(entry: MarketCacheEntry, expected: CacheIdentity): void {
  if (
    entry.key !== expected.key ||
    entry.providerId !== expected.providerId ||
    entry.symbol !== expected.symbol ||
    entry.kind !== expected.kind ||
    entry.timeframe !== expected.timeframe ||
    entry.range?.from !== expected.range.from ||
    entry.range?.to !== expected.range.to ||
    !Number.isFinite(entry.createdAt) ||
    !Number.isFinite(entry.expiresAt)
  ) {
    throw new MarketCacheCorruptionError('Cached market data does not match its request key');
  }
  validateValue(expected.kind, entry.value, expected.symbol, expected.timeframe);
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function sourceWithCacheLabel(source: unknown, status: CacheStatus): string | undefined {
  if (typeof source !== 'string') return undefined;
  if (status === 'fresh') return source;
  const label = status === 'stale' ? 'STALE OFFLINE CACHE' : 'cached';
  return `${source} · ${label}`;
}

function labelValue<T extends CacheValue>(value: T, status: CacheStatus): T {
  const result = clone(value);
  if (!isObject(result)) return result;
  if ('cacheStatus' in result) result.cacheStatus = status;
  else result.cacheStatus = status;
  const source = sourceWithCacheLabel(result.source, status);
  if (source) result.source = source;
  return result as T;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : isObject(error) && error.name === 'AbortError';
}

function isUnavailable(error: unknown): boolean {
  if (error instanceof MarketDataUnavailableError) return true;
  if (error instanceof TypeError) {
    return /fetch|network|offline|connection|load failed/i.test(error.message);
  }
  return false;
}

function createDatabase(dbName: string): Promise<IDBPDatabase<MarketCacheSchema>> {
  return openDB<MarketCacheSchema>(dbName, MARKET_CACHE_VERSION, {
    upgrade(db) {
      if (!db.objectStoreNames.contains('entries')) db.createObjectStore('entries', { keyPath: 'key' });
    },
  });
}

/** Adds a bounded, TTL-based IndexedDB cache around any normalized provider. */
export class CachedMarketDataProvider implements MarketDataProvider {
  readonly id: string;
  readonly supportedTimeframes: readonly Timeframe[];
  readonly capabilities: MarketDataProvider['capabilities'];
  private readonly dbName: string;
  private readonly ttlMs: number;
  private readonly maxEntries: number;
  private readonly now: () => number;
  private readonly cacheIdentity: string;
  private readonly inFlight = new Map<string, Promise<CacheValue>>();
  private dbPromise?: Promise<IDBPDatabase<MarketCacheSchema>>;

  constructor(
    private readonly provider: MarketDataProvider,
    options: CachedMarketDataOptions = {},
  ) {
    this.id = provider.id;
    this.cacheIdentity = provider.cacheVersion ? `${provider.id}:${provider.cacheVersion}` : provider.id;
    this.supportedTimeframes = provider.supportedTimeframes;
    this.capabilities = provider.capabilities;
    this.dbName = options.dbName ?? MARKET_CACHE_DATABASE;
    this.ttlMs = options.ttlMs ?? (provider.id === 'demo' ? DEFAULT_DEMO_TTL_MS : DEFAULT_YAHOO_TTL_MS);
    this.maxEntries = options.maxEntries ?? DEFAULT_MAX_ENTRIES;
    this.now = options.now ?? Date.now;
    if (!Number.isFinite(this.ttlMs) || this.ttlMs < 0) throw new Error('Cache TTL must be nonnegative');
    if (!Number.isInteger(this.maxEntries) || this.maxEntries < 1) {
      throw new Error('Cache maxEntries must be a positive integer');
    }
  }

  async getBars(
    symbol: string,
    timeframe: Timeframe,
    range?: MarketRange,
    signal?: AbortSignal,
  ): Promise<BarResult> {
    signal?.throwIfAborted();
    if (!this.supportedTimeframes.includes(timeframe)) throw new Error('Unsupported timeframe');
    const normalizedRange = normalizeRange(range);
    const identity = makeIdentity(this.cacheIdentity, symbol, 'bars', timeframe, normalizedRange);
    const result = await this.request(
      identity,
      () => this.provider.getBars(identity.symbol, timeframe, range),
      signal,
    );
    return result as BarResult;
  }

  async getQuote(symbol: string, signal?: AbortSignal): Promise<Quote> {
    signal?.throwIfAborted();
    const identity = makeIdentity(this.cacheIdentity, symbol, 'quote', '1D', { from: null, to: null });
    const result = await this.request(identity, () => this.provider.getQuote(identity.symbol), signal);
    return result as Quote;
  }

  async getSymbolTimeframes(symbol: string, signal?: AbortSignal): Promise<readonly Timeframe[]> {
    signal?.throwIfAborted();
    const normalizedSymbol = normalizeSymbol(symbol);
    const getTimeframes = this.provider.getSymbolTimeframes;
    if (!getTimeframes) return this.supportedTimeframes;
    const result = await getTimeframes.call(this.provider, normalizedSymbol, signal);
    signal?.throwIfAborted();
    return this.supportedTimeframes.filter((timeframe) => result.includes(timeframe));
  }

  /** Optional provider support is exposed as a normalized unavailable status when absent. */
  async getCorporateEvents(
    symbol: string,
    range?: MarketRange,
    signal?: AbortSignal,
  ): Promise<CorporateEventsResult> {
    signal?.throwIfAborted();
    const normalizedRange = normalizeRange(range);
    const identity = makeIdentity(this.cacheIdentity, symbol, 'events', '1D', normalizedRange);
    const getEvents = (this.provider as MarketDataProvider & {
      getCorporateEvents?: (
        symbol: string,
        range?: MarketRange,
        signal?: AbortSignal,
      ) => Promise<CorporateEventsResult>;
    }).getCorporateEvents;
    if (!getEvents) {
      return {
        status: 'unavailable',
        events: [],
        source: `${this.id} provider does not provide corporate events`,
      };
    }
    return (await this.request(
      identity,
      () => getEvents.call(this.provider, identity.symbol, range),
      signal,
    )) as CorporateEventsResult;
  }

  private async request(
    identity: CacheIdentity,
    load: () => Promise<CacheValue>,
    signal?: AbortSignal,
  ): Promise<CacheValue> {
    let cached: MarketCacheEntry | undefined;
    try {
      cached = await this.read(identity);
    } catch (error) {
      if (error instanceof MarketCacheCorruptionError) throw error;
    }
    signal?.throwIfAborted();
    const now = this.now();
    if (cached && cached.expiresAt > now) return labelValue(cached.value, 'cached');

    let pending = this.inFlight.get(identity.key);
    if (!pending) {
      pending = (async () => {
        const raw = await load();
        validateValue(identity.kind, raw, identity.symbol, identity.timeframe);
        const fresh = labelValue(raw, 'fresh');
        try {
          await this.write(identity, fresh);
        } catch {
          // The market cache is disposable; a cache quota/private-mode failure
          // must not hide a successful provider response.
        }
        return fresh;
      })();
      this.inFlight.set(identity.key, pending);
      void pending.finally(() => {
        if (this.inFlight.get(identity.key) === pending) this.inFlight.delete(identity.key);
      }).catch(() => undefined);
    }

    try {
      const value = await pending;
      signal?.throwIfAborted();
      return clone(value);
    } catch (error) {
      signal?.throwIfAborted();
      if (isAbortError(error) || !isUnavailable(error)) throw error;
      if (!cached) throw error;
      return labelValue(cached.value, 'stale');
    }
  }

  private async read(identity: CacheIdentity): Promise<MarketCacheEntry | undefined> {
    const db = await this.database();
    const entry = await db.get('entries', identity.key);
    if (!entry) return undefined;
    assertIdentity(entry, identity);
    return clone(entry);
  }

  private async write(identity: CacheIdentity, value: CacheValue): Promise<void> {
    const db = await this.database();
    const createdAt = this.now();
    const entry: MarketCacheEntry = {
      ...identity,
      createdAt,
      expiresAt: createdAt + this.ttlMs,
      value: clone(value),
    };
    const tx = db.transaction('entries', 'readwrite');
    await tx.store.put(entry);
    const entries = await tx.store.getAll();
    entries.sort((a, b) => b.createdAt - a.createdAt);
    for (const expired of entries.slice(this.maxEntries)) await tx.store.delete(expired.key);
    await tx.done;
  }

  private database(): Promise<IDBPDatabase<MarketCacheSchema>> {
    this.dbPromise ??= createDatabase(this.dbName);
    return this.dbPromise;
  }
}

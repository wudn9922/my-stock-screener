import { openDB, type DBSchema, type IDBPDatabase } from 'idb';
import { normalizeSymbol } from '../market-data/MarketDataProvider';
import { storageName } from '../app/HostingMode';
import type { CompanyFundamentals, FinancialPeriod, FundamentalsProvider } from './FundamentalsProvider';
import {
  financialSnapshotSchema,
  type FinancialSnapshot,
  type FinancialSnapshotMetadata,
} from './FinancialSnapshotSchema';

const CACHE_DATABASE = storageName('atlas-financial-snapshots-v1');
const CACHE_LIMIT = 32;
const SNAPSHOT_TTL_SECONDS = 24 * 60 * 60;

interface StoredSnapshot {
  symbol: string;
  lastAccessed: number;
  snapshot: FinancialSnapshot;
}
interface FinancialCacheDB extends DBSchema {
  snapshots: { key: string; value: StoredSnapshot };
}

export interface FinancialSnapshotCache {
  get(symbol: string): Promise<FinancialSnapshot | undefined>;
  put(snapshot: FinancialSnapshot): Promise<void>;
}

/** A disposable, bounded cache. Every pack is revalidated before it can be returned. */
export class IndexedDbFinancialSnapshotCache implements FinancialSnapshotCache {
  private db: Promise<IDBPDatabase<FinancialCacheDB>>;
  constructor(name = CACHE_DATABASE, private now: () => number = Date.now) {
    this.db = openDB<FinancialCacheDB>(name, 1, {
      upgrade(db) {
        if (!db.objectStoreNames.contains('snapshots')) {
          db.createObjectStore('snapshots', { keyPath: 'symbol' });
        }
      },
    });
  }

  async get(symbol: string) {
    try {
      const db = await this.db;
      const stored = await db.get('snapshots', symbol);
      if (!stored) return undefined;
      let snapshot: FinancialSnapshot;
      try {
        snapshot = financialSnapshotSchema.parse(stored.snapshot);
        if (snapshot.symbol !== symbol || stored.symbol !== symbol) throw new Error('Cached snapshot symbol mismatch');
      } catch {
        try {
          await db.delete('snapshots', symbol);
        } catch {
          // Invalid entries are never returned, even if best-effort eviction fails.
        }
        return undefined;
      }

      try {
        const entries = await db.getAll('snapshots');
        const latestAccess = entries.reduce((latest, entry) => Math.max(latest, Number.isFinite(entry.lastAccessed) ? entry.lastAccessed : 0), 0);
        await db.put('snapshots', { ...stored, lastAccessed: Math.max(this.now(), latestAccess + 1), snapshot });
      } catch {
        // LRU bookkeeping is optional; keep serving a valid cached pack if storage is read-only/full.
      }
      return structuredClone(snapshot);
    } catch {
      return undefined;
    }
  }

  async put(value: FinancialSnapshot) {
    try {
      const snapshot = financialSnapshotSchema.parse(value);
      const db = await this.db;
      const tx = db.transaction('snapshots', 'readwrite');
      void tx.done.catch(() => undefined);
      const current = await tx.store.getAll();
      const latestAccess = current.reduce((latest, entry) => Math.max(latest, Number.isFinite(entry.lastAccessed) ? entry.lastAccessed : 0), 0);
      await tx.store.put({ symbol: snapshot.symbol, lastAccessed: Math.max(this.now(), latestAccess + 1), snapshot });
      const entries = await tx.store.getAll();
      entries.sort((a, b) => b.lastAccessed - a.lastAccessed);
      for (const expired of entries.slice(CACHE_LIMIT)) await tx.store.delete(expired.symbol);
      await tx.done;
    } catch {
      // The cache is optional. A validated network snapshot still serves this request.
    }
  }
}

export class FinancialSnapshotUnavailableError extends Error {
  constructor(readonly symbol: string, cause?: unknown) {
    super(`${symbol} has no valid SEC financial snapshot available.`, { cause });
    this.name = 'FinancialSnapshotUnavailableError';
  }
}

export interface FinancialSnapshotProviderOptions {
  base?: string;
  now?: () => number;
  fetcher?: typeof fetch;
  cache?: FinancialSnapshotCache;
  cacheName?: string;
}

function snapshotMetadata(
  snapshot: FinancialSnapshot,
  now: number,
  offline: boolean,
): FinancialSnapshotMetadata {
  const stale = now / 1000 - snapshot.generatedAt >= SNAPSHOT_TTL_SECONDS;
  return {
    symbol: snapshot.symbol,
    source: snapshot.source,
    fetchedAt: snapshot.fetchedAt,
    generatedAt: snapshot.generatedAt,
    cacheStatus: stale ? 'stale' : offline ? 'cached' : 'fresh',
    offline,
    stale,
  };
}

/** Loads same-origin static normalized facts, falling back only to validated local packs. */
export class FinancialSnapshotProvider implements FundamentalsProvider {
  readonly id = 'financial-snapshot';
  private readonly base: string;
  private readonly now: () => number;
  private readonly fetcher: typeof fetch;
  private readonly cache: FinancialSnapshotCache;
  private metadata = new Map<string, FinancialSnapshotMetadata>();

  constructor(options: FinancialSnapshotProviderOptions = {}) {
    this.base = `${(options.base ?? `${import.meta.env.BASE_URL}financial-data/`).replace(/\/?$/, '/')}`;
    this.now = options.now ?? Date.now;
    this.fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
    this.cache = options.cache ?? new IndexedDbFinancialSnapshotCache(options.cacheName, this.now);
  }

  getSnapshotMetadata(symbolInput: string): FinancialSnapshotMetadata | undefined {
    const symbol = normalizeSymbol(symbolInput);
    const value = this.metadata.get(symbol);
    if (value) {
      this.metadata.delete(symbol);
      this.metadata.set(symbol, value);
    }
    return value ? structuredClone(value) : undefined;
  }

  private rememberMetadata(symbol: string, value: FinancialSnapshotMetadata): void {
    this.metadata.delete(symbol);
    this.metadata.set(symbol, value);
    while (this.metadata.size > CACHE_LIMIT) {
      const oldest = this.metadata.keys().next().value;
      if (oldest === undefined) break;
      this.metadata.delete(oldest);
    }
  }

  async getFinancials(
    symbolInput: string,
    period: FinancialPeriod,
    signal?: AbortSignal,
  ): Promise<CompanyFundamentals[]> {
    const symbol = normalizeSymbol(symbolInput);
    if (period !== 'quarterly' && period !== 'annual') throw new Error('Unsupported financial period');
    signal?.throwIfAborted();
    let pack: FinancialSnapshot;
    let offline = false;
    try {
      const response = await this.fetcher(`${this.base}${encodeURIComponent(symbol)}.json`, {
        cache: 'no-cache',
        signal,
      });
      signal?.throwIfAborted();
      if (!response.ok) throw new Error(`Financial snapshot HTTP ${response.status}`);
      pack = financialSnapshotSchema.parse(await response.json());
      if (pack.symbol !== symbol) throw new Error('Financial snapshot symbol mismatch');
      signal?.throwIfAborted();
      try {
        await this.cache.put(pack);
      } catch {
        // Valid network data remains usable when browser storage is unavailable.
      }
    } catch (error) {
      signal?.throwIfAborted();
      let cached: FinancialSnapshot | undefined;
      try {
        cached = await this.cache.get(symbol);
      } catch {
        cached = undefined;
      }
      signal?.throwIfAborted();
      if (!cached || cached.symbol !== symbol) {
        this.metadata.delete(symbol);
        throw new FinancialSnapshotUnavailableError(symbol, error);
      }
      pack = cached;
      offline = true;
    }
    signal?.throwIfAborted();
    this.rememberMetadata(symbol, snapshotMetadata(pack, this.now(), offline));
    return structuredClone(pack[period]);
  }
}

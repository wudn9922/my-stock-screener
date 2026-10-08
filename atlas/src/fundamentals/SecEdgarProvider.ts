import { financialSchema } from './financialSchema';
import type {
  CompanyFundamentals,
  FinancialPeriod,
  FundamentalsProvider,
} from './FundamentalsProvider';
import { normalizeSymbol } from '../market-data/MarketDataProvider';
import { STATIC_HOSTING } from '../app/HostingMode';
import { isSecEligibleSymbol } from './SecEligibility';
import type { FinancialSnapshotMetadata } from './FinancialSnapshotSchema';

/** Static per-period files are rebuilt by a scheduled job; older checks are labelled stale. */
const STATIC_STALE_SECONDS = 4 * 24 * 60 * 60;

/** Subset of `fundamentals/manifest.json` (scripts/build-fundamentals.ts) used for provenance only. */
interface StaticManifestEntry {
  checkedAt?: unknown;
  fetchedAt?: unknown;
  source?: unknown;
}

/** Shown when a static deployment has no pre-built SEC file for a symbol. Never replaced by invented data. */
export const STATIC_FUNDAMENTALS_UNAVAILABLE =
  '此股票沒有預先下載的 SEC 財報（台股、指數與 ETF 不適用；美股資料每日更新）';

/** A static deployment has no normalized SEC file for this symbol/period. */
export class StaticFundamentalsUnavailableError extends Error {
  constructor(readonly symbol: string, readonly period: FinancialPeriod) {
    super(STATIC_FUNDAMENTALS_UNAVAILABLE);
    this.name = 'StaticFundamentalsUnavailableError';
  }
}

export interface SecEdgarProviderOptions {
  /** Read `fundamentals/<SYMBOL>-<period>.json` files instead of the `/api/fundamentals` backend. */
  staticFiles?: boolean;
  /** Static file directory; defaults to `${BASE_URL}fundamentals/`. */
  staticBase?: string;
  fetcher?: typeof fetch;
  now?: () => number;
}

/**
 * Browser provider sees normalized records only. SEC raw XBRL stays in the backend, or in the
 * build-time collector (scripts/build-fundamentals.mjs) for static hosting.
 */
export class SecEdgarProvider implements FundamentalsProvider {
  readonly id = 'sec-edgar';
  private cache = new Map<string, { at: number; records: CompanyFundamentals[] }>();
  private readonly staticFiles: boolean;
  private readonly staticBase: string;
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  private manifest?: Promise<Record<string, StaticManifestEntry>>;
  private metadata = new Map<string, FinancialSnapshotMetadata>();
  /** True when records come from pre-built files rather than a live backend response. */
  readonly staticSnapshot: boolean;
  constructor(options: SecEdgarProviderOptions = {}) {
    this.staticFiles = options.staticFiles ?? STATIC_HOSTING;
    this.staticBase = (options.staticBase ?? `${import.meta.env.BASE_URL}fundamentals/`).replace(/\/?$/, '/');
    this.fetcher = options.fetcher ?? ((input, init) => globalThis.fetch(input, init));
    this.now = options.now ?? Date.now;
    this.staticSnapshot = this.staticFiles;
  }
  /** Freshness of a static file, from the build manifest when it is available; never invented. */
  getSnapshotMetadata(symbolInput: string): FinancialSnapshotMetadata | undefined {
    if (!this.staticFiles) return undefined;
    const value = this.metadata.get(normalizeSymbol(symbolInput));
    return value ? structuredClone(value) : undefined;
  }
  private loadManifest(): Promise<Record<string, StaticManifestEntry>> {
    this.manifest ??= this.fetcher(`${this.staticBase}manifest.json`, { cache: 'no-cache' })
      .then(async (response) => {
        if (!response.ok) return {};
        const body: unknown = await response.json();
        const symbols = body && typeof body === 'object' && 'symbols' in body ? body.symbols : undefined;
        return symbols && typeof symbols === 'object' ? (symbols as Record<string, StaticManifestEntry>) : {};
      })
      .catch(() => ({}));
    return this.manifest;
  }
  private async rememberStaticMetadata(symbol: string, records: CompanyFundamentals[]) {
    const entry = (await this.loadManifest())[symbol];
    const checkedAt = Number(entry?.checkedAt);
    const fetchedAt = Number(entry?.fetchedAt ?? entry?.checkedAt);
    const recordSource = Object.values(records[0]?.sourceConcepts ?? {})[0]?.inputs[0]?.secUrl;
    const source = typeof entry?.source === 'string' ? entry.source : recordSource;
    if (!Number.isFinite(checkedAt) || checkedAt <= 0 || !Number.isFinite(fetchedAt) || !source) {
      this.metadata.delete(symbol);
      return;
    }
    const stale = this.now() / 1000 - checkedAt >= STATIC_STALE_SECONDS;
    this.metadata.set(symbol, {
      symbol,
      source,
      fetchedAt,
      generatedAt: checkedAt,
      cacheStatus: stale ? 'stale' : 'fresh',
      offline: false,
      stale,
    });
    while (this.metadata.size > 32) this.metadata.delete(this.metadata.keys().next().value!);
  }
  async getFinancials(
    symbol: string,
    period: FinancialPeriod,
    signal?: AbortSignal,
  ): Promise<CompanyFundamentals[]> {
    symbol = normalizeSymbol(symbol);
    if (period !== 'quarterly' && period !== 'annual') throw new Error('Unsupported financial period');
    const key = symbol + ':' + period,
      cached = this.cache.get(key);
    if (cached && this.now() - cached.at < 3600000) return structuredClone(cached.records);
    let response: Response;
    if (this.staticFiles) {
      // Taiwan listings and indices never have SEC filings; do not request a file that cannot exist.
      if (!isSecEligibleSymbol(symbol)) throw new StaticFundamentalsUnavailableError(symbol, period);
      response = await this.fetcher(
        `${this.staticBase}${encodeURIComponent(symbol)}-${period}.json`,
        { signal, cache: 'no-cache' },
      );
      // A missing file is a 404 on GitHub Pages; SPA-style static servers answer with index.html.
      if (response.status === 404 || (response.headers.get('content-type') ?? '').includes('text/html'))
        throw new StaticFundamentalsUnavailableError(symbol, period);
      if (!response.ok) throw new Error('SEC 財報快照暫時無法讀取，請稍後再試。');
    } else {
      response = await this.fetcher(
        `/api/fundamentals?symbol=${encodeURIComponent(symbol)}&period=${period}`,
        { signal },
      );
      if (!response.ok) throw new Error('SEC 資料來源暫時無法使用，請稍後再試。');
    }
    const records = financialSchema.array().parse(await response.json());
    if (records.some((r) => r.symbol !== symbol || r.period !== period))
      throw new Error('Financial provider returned mismatched records');
    if (this.staticFiles) await this.rememberStaticMetadata(symbol, records);
    this.cache.delete(key);
    this.cache.set(key, { at: this.now(), records });
    while (this.cache.size > 32) this.cache.delete(this.cache.keys().next().value!);
    return structuredClone(records);
  }
}

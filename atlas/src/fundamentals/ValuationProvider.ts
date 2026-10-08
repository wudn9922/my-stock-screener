import { z } from 'zod';
import { getMarketProfile, isIndexSymbol } from '../market-data/MarketProfile';
import { normalizeSymbol } from '../market-data/MarketDataProvider';

/**
 * EPS for P/E and P/E TTM, from the small per-market files built in GitHub Actions by
 * scripts/build-valuation.mjs: `${BASE_URL}valuation/us.json` (SEC XBRL frames) and
 * `${BASE_URL}valuation/tw.json` (TWSE / TPEx official daily P/E).
 */
export interface Valuation {
  symbol: string;
  market: 'US' | 'TW';
  /** Trailing-twelve-month EPS (US: sum of the latest four quarters; TW: close / exchange P/E). */
  epsTtm: number | null;
  /** Latest full fiscal-year EPS. */
  epsAnnual: number | null;
  fiscalYear: number | null;
  /** Taiwan only: the exchange's own P/E on `asOf` (TTM basis). */
  exchangePeTtm: number | null;
  /** YYYY-MM-DD: end of the EPS period (US) or the exchange's data date (TW). */
  asOf: string | null;
  source: 'SEC frames' | 'TWSE' | 'TPEx';
  /** `basic` when diluted EPS was not reported. */
  basis: 'diluted' | 'basic';
}

export interface PeResult {
  /** Price / EPS, rounded to 2 decimals; null when not meaningful. */
  pe: number | null;
  /** True when EPS ≤ 0: P/E is not meaningful for losses (show e.g. 「虧損」). */
  negativeEarnings: boolean;
  /** Why `pe` is null: missing price/EPS, or negative/zero earnings. */
  reason?: 'missing-price' | 'missing-eps' | 'negative-earnings';
}

/** P/E = price / EPS. Null when EPS is missing, zero or negative, or the price is not a positive number. */
export function computePe(price: number | null | undefined, eps: number | null | undefined): number | null {
  return describePe(price, eps).pe;
}

export function describePe(price: number | null | undefined, eps: number | null | undefined): PeResult {
  if (eps === null || eps === undefined || !Number.isFinite(eps)) return { pe: null, negativeEarnings: false, reason: 'missing-eps' };
  if (eps <= 0) return { pe: null, negativeEarnings: true, reason: 'negative-earnings' };
  if (price === null || price === undefined || !Number.isFinite(price) || price <= 0) {
    return { pe: null, negativeEarnings: false, reason: 'missing-price' };
  }
  return { pe: Math.round((price / eps) * 100) / 100, negativeEarnings: false };
}

export interface ValuationSummary {
  valuation: Valuation;
  peTtm: PeResult;
  peAnnual: PeResult;
}

/** P/E TTM and annual P/E for a price (e.g. the chart's latest close or live quote). */
export function summarizeValuation(valuation: Valuation, price: number | null | undefined): ValuationSummary {
  return {
    valuation,
    peTtm: describePe(price, valuation.epsTtm),
    peAnnual: describePe(price, valuation.epsAnnual),
  };
}

const nullableNumber = z.number().finite().nullable().optional();
const recordSchema = z.object({
  epsTtm: nullableNumber,
  epsAnnual: nullableNumber,
  fiscalYear: z.number().int().nullable().optional(),
  exchangePeTtm: nullableNumber,
  asOf: z.string().nullable().optional(),
  source: z.enum(['SEC frames', 'TWSE', 'TPEx']),
  basis: z.enum(['diluted', 'basic']).optional(),
});
const documentSchema = z.object({
  version: z.literal(1),
  market: z.enum(['US', 'TW']),
  generatedAt: z.string().optional(),
  items: z.record(z.string(), z.unknown()),
});

export interface ValuationFile {
  market: 'US' | 'TW';
  generatedAt?: string;
  items: Readonly<Record<string, unknown>>;
}

export interface ValuationProviderOptions {
  fetcher?: typeof fetch;
  now?: () => number;
}

const RETRY_AFTER_FAILURE_MS = 60_000;

export class ValuationProvider {
  readonly base: string;
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  private readonly files = new Map<'US' | 'TW', { at: number; failed: boolean; promise: Promise<ValuationFile | null> }>();

  constructor(base = `${import.meta.env?.BASE_URL ?? '/'}valuation/`, options: ValuationProviderOptions = {}) {
    this.base = base.endsWith('/') ? base : `${base}/`;
    this.fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
    this.now = options.now ?? Date.now;
  }

  /**
   * Valuation for a US or Taiwan stock, or null (index, unknown symbol, file missing). Each market file is
   * loaded once and cached; a failed load is retried after a minute.
   */
  async getValuation(symbolInput: string, signal?: AbortSignal): Promise<Valuation | null> {
    signal?.throwIfAborted();
    let symbol: string;
    try {
      symbol = normalizeSymbol(symbolInput);
    } catch {
      return null;
    }
    if (isIndexSymbol(symbol)) return null;
    const market = getMarketProfile(symbol).market;
    const file = await this.loadFile(market);
    signal?.throwIfAborted();
    if (!file || !Object.hasOwn(file.items, symbol)) return null;
    const parsed = recordSchema.safeParse(file.items[symbol]);
    if (!parsed.success) return null;
    const record = parsed.data;
    return {
      symbol,
      market,
      epsTtm: record.epsTtm ?? null,
      epsAnnual: record.epsAnnual ?? null,
      fiscalYear: record.fiscalYear ?? null,
      exchangePeTtm: record.exchangePeTtm ?? null,
      asOf: record.asOf ?? null,
      source: record.source,
      basis: record.basis ?? 'diluted',
    };
  }

  /** The raw market file (for diagnostics such as its `generatedAt`). */
  loadFile(market: 'US' | 'TW'): Promise<ValuationFile | null> {
    const cached = this.files.get(market);
    if (cached && (!cached.failed || this.now() - cached.at < RETRY_AFTER_FAILURE_MS)) return cached.promise;
    const entry: { at: number; failed: boolean; promise: Promise<ValuationFile | null> } = {
      at: this.now(),
      failed: false,
      promise: Promise.resolve(null),
    };
    entry.promise = (async (): Promise<ValuationFile | null> => {
      try {
        const response = await this.fetcher(`${this.base}${market === 'US' ? 'us' : 'tw'}.json`, { cache: 'no-cache' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const document = documentSchema.parse(await response.json());
        if (document.market !== market) throw new Error('Valuation market mismatch');
        return { market, ...(document.generatedAt ? { generatedAt: document.generatedAt } : {}), items: document.items };
      } catch {
        // Not built yet, offline or invalid: report "no valuation" and retry after a minute.
        entry.failed = true;
        entry.at = this.now();
        return null;
      }
    })();
    this.files.set(market, entry);
    return entry.promise;
  }
}

let defaultProvider: ValuationProvider | null = null;

/** Shared default instance reading `${BASE_URL}valuation/`. */
export function getValuation(symbol: string, signal?: AbortSignal): Promise<Valuation | null> {
  defaultProvider ??= new ValuationProvider();
  return defaultProvider.getValuation(symbol, signal);
}

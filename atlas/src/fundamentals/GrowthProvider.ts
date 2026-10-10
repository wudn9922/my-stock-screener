import { toFinite } from '../report/schema';

/**
 * Quarterly EPS / revenue growth for the screener's fundamental filters, from
 * `${BASE_URL}valuation/us-growth.json` (atlas/scripts/build_fundamentals_us.py, Yahoo Finance).
 * US only: Taiwan has no quarterly growth file yet. Loaded only when a fundamental filter or sort
 * is used; reused for 30 minutes and, like ValuationProvider, a failed reload keeps the last good copy.
 */
export interface GrowthRecord {
  /** EPS YoY % per quarter, index 0 = latest; null when the year-ago EPS was ≤ 0 or missing. */
  epsYoY: (number | null)[];
  /** Revenue YoY % per quarter, index 0 = latest. */
  revYoY: (number | null)[];
  /** Latest quarter turned from a loss (year-ago EPS ≤ 0) to a profit. */
  epsTurn: boolean;
  basis: 'diluted' | 'basic';
  /** Period end of the latest quarter (YYYY-MM-DD). */
  latest: string | null;
  checkedAt: string | null;
}

export interface GrowthFile {
  generatedAt: string | null;
  source: string | null;
  items: ReadonlyMap<string, GrowthRecord>;
}

/** Lookup key shared by the file and the screener rows (`BRK.B` and `BRK-B` both → `BRK-B`). */
export function growthKey(symbol: string): string {
  return symbol.trim().toUpperCase().replace(/\./g, '-');
}

const MAX_QUARTERS = 4;
const yoyList = (value: unknown): (number | null)[] =>
  Array.isArray(value) ? value.slice(0, MAX_QUARTERS).map((entry) => toFinite(entry)) : [];
const dateText = (value: unknown) => (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : null);

/** Tolerant per-symbol parse; null for anything that is not an object. */
export function parseGrowthRecord(value: unknown): GrowthRecord | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  return {
    epsYoY: yoyList(raw.epsYoY),
    revYoY: yoyList(raw.revYoY),
    epsTurn: raw.epsTurn === true,
    basis: raw.basis === 'basic' ? 'basic' : 'diluted',
    latest: dateText(raw.latest),
    checkedAt: dateText(raw.checkedAt),
  };
}

/** Parses the whole document; null when it is not a version-1 US growth file. */
export function parseGrowthFile(data: unknown): GrowthFile | null {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  const document = data as Record<string, unknown>;
  if (document.version !== 1 || document.market !== 'US') return null;
  if (!document.items || typeof document.items !== 'object' || Array.isArray(document.items)) return null;
  const items = new Map<string, GrowthRecord>();
  for (const [symbol, raw] of Object.entries(document.items as Record<string, unknown>)) {
    const record = parseGrowthRecord(raw);
    if (record && symbol.trim()) items.set(growthKey(symbol), record);
  }
  return {
    generatedAt: typeof document.generatedAt === 'string' ? document.generatedAt : null,
    source: typeof document.source === 'string' ? document.source : null,
    items,
  };
}

export interface GrowthProviderOptions {
  fetcher?: typeof fetch;
  now?: () => number;
}

const RETRY_AFTER_FAILURE_MS = 60_000;
const RELOAD_AFTER_MS = 30 * 60_000;

export class GrowthProvider {
  readonly url: string;
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  private entry: { at: number; failed: boolean; promise: Promise<GrowthFile | null> } | null = null;
  private lastGood: GrowthFile | null = null;

  constructor(base = `${import.meta.env?.BASE_URL ?? '/'}valuation/`, options: GrowthProviderOptions = {}) {
    this.url = `${base.endsWith('/') ? base : `${base}/`}us-growth.json`;
    this.fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
    this.now = options.now ?? Date.now;
  }

  /** The parsed file, or null when it is not built yet / invalid / offline (and nothing loaded before). */
  load(): Promise<GrowthFile | null> {
    const cached = this.entry;
    if (cached && this.now() - cached.at < (cached.failed ? RETRY_AFTER_FAILURE_MS : RELOAD_AFTER_MS)) return cached.promise;
    const entry = { at: this.now(), failed: false, promise: Promise.resolve<GrowthFile | null>(null) };
    entry.promise = (async () => {
      try {
        const response = await this.fetcher(this.url, { cache: 'no-cache' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const file = parseGrowthFile(JSON.parse(await response.text()));
        if (!file) throw new Error('Invalid growth file');
        this.lastGood = file;
        return file;
      } catch {
        entry.failed = true;
        entry.at = this.now();
        return this.lastGood;
      }
    })();
    this.entry = entry;
    return entry.promise;
  }
}

let defaultProvider: GrowthProvider | null = null;

/** Shared default instance reading `${BASE_URL}valuation/us-growth.json`. */
export function loadGrowth(): Promise<GrowthFile | null> {
  defaultProvider ??= new GrowthProvider();
  return defaultProvider.load();
}

import { toFinite } from '../report/schema';
import { reportUrl } from '../report/loadReport';

/**
 * EPS / revenue growth for the screener's fundamental filters. Two files, merged per stock:
 * - `report/growth.json` (atlas/scripts/build_growth.py): Taiwan and US, the whole market, quarterly (up to 12;
 *   older files with 4 are still read: missing slots are simply unknown)
 *   and annual (up to 10 years; Taiwan and the SEC frames have fewer), aligned on the same period a year earlier. Each block says its source
 *   (`src` / `srcY`): SEC companyfacts or Yahoo financials files, the Taiwan official statements (MOPS), the
 *   Taiwan history from FinMind (republished MOPS data) or SEC XBRL frames; `dQ` flags the quarterly slots that compare a derived Q4 (full year − first three quarters).
 * - `${BASE_URL}valuation/us-growth.json` (atlas/scripts/build_fundamentals_us.py, Yahoo Finance): quarterly,
 *   US only (the S&P 500 list), mostly 1 quarter of YoY.
 * growth.json comes first; us-growth only fills a quarterly metric growth.json has nothing for, or replaces the
 * quarterly numbers when its latest quarter is clearly newer (`mergeGrowth`). Loaded only when a fundamental
 * filter or sort is used; reused for 30 minutes and a failed reload keeps the last good copy, like ValuationProvider.
 */
export type GrowthSource = 'yahoo' | 'financials';
/** Where one block of numbers comes from (growth.json `src` / `srcY`, or the us-growth file). */
export type GrowthOrigin = 'sec' | 'yahoo' | 'mops' | 'finmind' | 'frames' | 'us-growth';
const ORIGINS: readonly GrowthOrigin[] = ['sec', 'yahoo', 'mops', 'finmind', 'frames', 'us-growth'];
const originOf = (value: unknown): GrowthOrigin | null => ORIGINS.find((origin) => origin === value) ?? null;
export interface GrowthRecord {
  /** EPS YoY % per quarter (up to 12), index 0 = latest; null when the year-ago EPS was ≤ 0 or missing. */
  epsYoY: (number | null)[];
  /** Revenue YoY % per quarter (up to 12), index 0 = latest. */
  revYoY: (number | null)[];
  /** Latest quarter turned from a loss (year-ago EPS ≤ 0) to a profit. */
  epsTurn: boolean;
  /** Per quarter slot (0 = latest): that quarter turned from a loss to a profit (YoY undefined). */
  epsTurnQ: boolean[];
  /** EPS / revenue YoY % per fiscal year (up to 10), index 0 = latest year. */
  epsYoYAnnual: (number | null)[];
  revYoYAnnual: (number | null)[];
  /** Per year slot: the year turned from a loss to a profit. */
  epsTurnAnnual: boolean[];
  basis: 'diluted' | 'basic';
  /** Period end of the latest quarter (YYYY-MM-DD). */
  latest: string | null;
  /** Period end of the latest fiscal year. */
  latestAnnual: string | null;
  checkedAt: string | null;
  /** Which file the quarterly numbers come from (annual ones always come from growth.json). */
  source: GrowthSource;
  /** Source of the quarterly EPS / revenue numbers and of the annual ones. */
  originQ: { eps: GrowthOrigin | null; rev: GrowthOrigin | null };
  originY: GrowthOrigin | null;
  /** Per quarter slot: the pair compares a derived Q4 (full year − first three quarters). */
  derivedQ: boolean[];
  /** Quality level of the financials file (growth.json rows built from one; null otherwise). */
  quality: 'good' | 'partial' | 'limited' | null;
}

export interface GrowthFile {
  generatedAt: string | null;
  source: string | null;
  items: ReadonlyMap<string, GrowthRecord>;
}

/** Lookup key shared by the files and the screener rows (`BRK.B` and `BRK-B` both → `BRK-B`; `2330.TW` → `2330-TW`). */
export function growthKey(symbol: string): string {
  return symbol.trim().toUpperCase().replace(/\./g, '-');
}

/** Quarterly YoY values in growth.json (build_growth.py QUARTER_SLOTS). */
export const MAX_QUARTERS = 12;
/** Annual YoY values in growth.json (build_growth.py YEAR_SLOTS). */
export const MAX_YEARS = 10;
const yoyList = (value: unknown, max = MAX_QUARTERS): (number | null)[] =>
  Array.isArray(value) ? value.slice(0, max).map((entry) => toFinite(entry)) : [];
/** Bit i of a mask → slot i (growth.json `tQ` / `tY`). */
const maskFlags = (mask: unknown, length: number): boolean[] => {
  const bits = typeof mask === 'number' && Number.isInteger(mask) && mask > 0 ? mask : 0;
  return Array.from({ length }, (_, slot) => ((bits >> slot) & 1) === 1);
};
const dateText = (value: unknown) => (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : null);

/** Tolerant per-symbol parse; null for anything that is not an object. */
export function parseGrowthRecord(value: unknown): GrowthRecord | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const epsTurn = raw.epsTurn === true;
  return {
    epsYoY: yoyList(raw.epsYoY),
    revYoY: yoyList(raw.revYoY),
    epsTurn,
    epsTurnQ: [epsTurn],
    epsYoYAnnual: [],
    revYoYAnnual: [],
    epsTurnAnnual: [],
    basis: raw.basis === 'basic' ? 'basic' : 'diluted',
    latest: dateText(raw.latest),
    latestAnnual: null,
    checkedAt: dateText(raw.checkedAt),
    source: 'yahoo',
    originQ: { eps: 'us-growth', rev: 'us-growth' },
    originY: null,
    derivedQ: [],
    quality: null,
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

// ---------------------------------------------------------------- report/growth.json

/** Column order of the contract; used when a file omits `fields`. */
export const REPORT_GROWTH_FIELDS = ['symbol', 'lv', 'src', 'qEnd', 'yEnd', 'epsQ', 'revQ', 'epsY', 'revY', 'tQ', 'tY', 'srcY', 'dQ'] as const;
const QUALITY_LEVELS = ['good', 'partial', 'limited'] as const;

/** One `rows` entry of growth.json → [symbol, record]; null for a row without a usable symbol. */
export function parseReportGrowthRow(row: unknown, fields: readonly string[] = REPORT_GROWTH_FIELDS): [string, GrowthRecord] | null {
  if (!Array.isArray(row)) return null;
  const get = (name: string): unknown => {
    const column = fields.indexOf(name);
    return column < 0 ? undefined : row[column];
  };
  const symbol = get('symbol');
  if (typeof symbol !== 'string' || !symbol.trim()) return null;
  const epsTurnQ = maskFlags(get('tQ'), MAX_QUARTERS);
  const level = QUALITY_LEVELS.find((entry) => entry === get('lv')) ?? null;
  const latest = dateText(get('qEnd'));
  const latestAnnual = dateText(get('yEnd'));
  const src = originOf(get('src'));
  // `src` is the quarterly block's source (the annual one's without a quarterly block); files before `srcY` had one source.
  const quarterOrigin = latest ? src : null;
  const annualOrigin = latestAnnual ? (fields.includes('srcY') ? originOf(get('srcY')) : src) : null;
  return [
    symbol,
    {
      epsYoY: yoyList(get('epsQ')),
      revYoY: yoyList(get('revQ')),
      epsTurn: epsTurnQ[0] === true,
      epsTurnQ,
      epsYoYAnnual: yoyList(get('epsY'), MAX_YEARS),
      revYoYAnnual: yoyList(get('revY'), MAX_YEARS),
      epsTurnAnnual: maskFlags(get('tY'), MAX_YEARS),
      basis: 'diluted',
      latest,
      latestAnnual,
      checkedAt: null,
      source: 'financials',
      originQ: { eps: quarterOrigin, rev: quarterOrigin },
      originY: annualOrigin,
      derivedQ: maskFlags(get('dQ'), MAX_QUARTERS),
      quality: level,
    },
  ];
}

/** Parses growth.json (`fields` + `rows`); null when it is not a version-1 file. */
export function parseReportGrowth(data: unknown): GrowthFile | null {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  const document = data as Record<string, unknown>;
  if (document.version !== 1 || !Array.isArray(document.rows)) return null;
  const fields =
    Array.isArray(document.fields) && document.fields.every((name) => typeof name === 'string')
      ? (document.fields as string[])
      : REPORT_GROWTH_FIELDS;
  const items = new Map<string, GrowthRecord>();
  for (const raw of document.rows) {
    const parsed = parseReportGrowthRow(raw, fields);
    if (parsed) items.set(growthKey(parsed[0]), parsed[1]);
  }
  return {
    generatedAt: typeof document.generatedAt === 'string' ? document.generatedAt : null,
    source: typeof document.source === 'string' ? document.source : null,
    items,
  };
}

/** A quarterly metric has at least one usable value (a YoY number, or for EPS a turnaround). */
const hasUsable = (values: readonly (number | null)[], turns: readonly boolean[] = []) =>
  values.some((value) => value !== null) || turns.some(Boolean);

/** Days a us-growth quarter must be newer than growth.json's to replace its quarterly numbers (same rule as build_growth.py). */
export const FRESHER_DAYS = 45;
const daysBetween = (later: string | null, earlier: string | null): number => {
  if (!later) return -Infinity;
  if (!earlier) return Infinity;
  return (Date.parse(later) - Date.parse(earlier)) / 86_400_000;
};

/**
 * One record per stock from both files. growth.json (whole market, quarterly and annual, one source per block)
 * comes first. The us-growth (Yahoo, S&P 500) quarterly numbers are used for a metric when growth.json has none
 * for it, or for both when its latest quarter ends more than FRESHER_DAYS after growth.json's (a newer report).
 * Annual numbers always come from growth.json. A stock only in one file keeps that file's record. Either file may
 * be missing.
 */
export function mergeGrowth(yahoo: GrowthFile | null | undefined, financials: GrowthFile | null | undefined): GrowthFile | null {
  if (!yahoo && !financials) return null;
  if (!financials) return yahoo ?? null;
  const items = new Map<string, GrowthRecord>(financials.items);
  let used = 0;
  for (const [key, y] of yahoo?.items ?? []) {
    const f = financials.items.get(key);
    if (!f) {
      items.set(key, y);
      used += 1;
      continue;
    }
    const newer = daysBetween(y.latest, f.latest) > FRESHER_DAYS;
    const useEps = hasUsable(y.epsYoY, y.epsTurnQ) && (newer || !hasUsable(f.epsYoY, f.epsTurnQ));
    const useRev = hasUsable(y.revYoY) && (newer || !hasUsable(f.revYoY));
    if (!useEps && !useRev) continue;
    used += 1;
    items.set(key, {
      ...f,
      epsYoY: useEps ? y.epsYoY : f.epsYoY,
      epsTurn: useEps ? y.epsTurn : f.epsTurn,
      epsTurnQ: useEps ? y.epsTurnQ : f.epsTurnQ,
      revYoY: useRev ? y.revYoY : f.revYoY,
      basis: useEps ? y.basis : f.basis,
      latest: (useEps && useRev) || newer ? (y.latest ?? f.latest) : (f.latest ?? y.latest),
      checkedAt: y.checkedAt ?? f.checkedAt,
      source: 'yahoo',
      originQ: { eps: useEps ? 'us-growth' : f.originQ.eps, rev: useRev ? 'us-growth' : f.originQ.rev },
      // derivedQ stays growth.json's: it only describes the metrics whose origin is not 'us-growth'
    });
  }
  return {
    generatedAt: financials.generatedAt ?? yahoo?.generatedAt ?? null,
    source: used ? `${financials.source ?? 'growth.json'} + ${yahoo?.source ?? 'Yahoo Finance'}` : financials.source,
    items,
  };
}

export interface GrowthProviderOptions {
  fetcher?: typeof fetch;
  now?: () => number;
  /** Full URL of the file (default: `<base>/us-growth.json`). */
  url?: string;
  /** Parser of the body (default: the US growth file). */
  parse?: (data: unknown) => GrowthFile | null;
}

const RETRY_AFTER_FAILURE_MS = 60_000;
const RELOAD_AFTER_MS = 30 * 60_000;

export class GrowthProvider {
  readonly url: string;
  private readonly parse: (data: unknown) => GrowthFile | null;
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  private entry: { at: number; failed: boolean; promise: Promise<GrowthFile | null> } | null = null;
  private lastGood: GrowthFile | null = null;
  get lastLoadFailed(): boolean { return this.entry?.failed ?? false; }

  constructor(base = `${import.meta.env?.BASE_URL ?? '/'}valuation/`, options: GrowthProviderOptions = {}) {
    this.url = options.url ?? `${base.endsWith('/') ? base : `${base}/`}us-growth.json`;
    this.parse = options.parse ?? parseGrowthFile;
    this.fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
    this.now = options.now ?? Date.now;
  }

  /** The parsed file, or null when it is not built yet / invalid / offline (and nothing loaded before). */
  load(refresh = false): Promise<GrowthFile | null> {
    const cached = this.entry;
    if (!refresh && cached && this.now() - cached.at < (cached.failed ? RETRY_AFTER_FAILURE_MS : RELOAD_AFTER_MS)) return cached.promise;
    const entry = { at: this.now(), failed: false, promise: Promise.resolve<GrowthFile | null>(null) };
    entry.promise = (async () => {
      try {
        const response = await this.fetcher(this.url, { cache: 'no-cache' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const file = this.parse(JSON.parse(await response.text()));
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
let reportProvider: GrowthProvider | null = null;
export function growthSourceStatus() { return { reportFailed: reportProvider?.lastLoadFailed ?? false, yahooFailed: defaultProvider?.lastLoadFailed ?? false }; }

/** Shared default instance reading `${BASE_URL}valuation/us-growth.json` (US quarterly, Yahoo). */
export function loadGrowth(refresh = false): Promise<GrowthFile | null> {
  defaultProvider ??= new GrowthProvider();
  return defaultProvider.load(refresh);
}

/** `report/growth.json`, a sibling of latest.json. */
export function reportGrowthUrl(report = reportUrl()): string {
  return new URL('growth.json', report).href;
}

/** Shared instance reading `report/growth.json` (Taiwan and US, quarterly and annual). */
export function loadReportGrowth(refresh = false): Promise<GrowthFile | null> {
  reportProvider ??= new GrowthProvider('', { url: reportGrowthUrl(), parse: parseReportGrowth });
  return reportProvider.load(refresh);
}

/**
 * The growth data for a market: Taiwan = growth.json; US = growth.json merged with us-growth.json (Yahoo).
 * Either source failing only removes its part; null when neither is available.
 */
export async function loadMarketGrowth(market: 'TW' | 'US', refresh = false): Promise<GrowthFile | null> {
  if (market === 'TW') return loadReportGrowth(refresh);
  const [yahoo, financials] = await Promise.all([loadGrowth(refresh), loadReportGrowth(refresh)]);
  return mergeGrowth(yahoo, financials);
}

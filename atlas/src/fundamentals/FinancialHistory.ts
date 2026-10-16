import { z } from 'zod';
import { reportUrl } from '../report/loadReport';
import { INDEX_SYMBOL_REGEX, TAIWAN_SYMBOL_REGEX } from '../market-data/MarketProfile';

/**
 * Per-symbol statement history (`report/financials/<KEY>.json`, written by atlas/scripts/build_financials.py
 * from Yahoo fundamentals-timeseries, optionally seeded with SEC EDGAR companyfacts history, and for Taiwan
 * stocks with FinMind statements (build_financials_tw_finmind.py, data from 公開資訊觀測站)).
 *
 * Tolerant like report/breadth.ts: unknown fields are ignored, numbers may arrive as strings, a period
 * with a bad end date is dropped (with its values in every parallel array), periods are re-sorted by end
 * date, and a missing `src` array means every period came from Yahoo. Derived figures (margins, ROE,
 * YoY…) are not in the file: financialMetrics.ts computes them in one place.
 */

/** Canonical raw fields (amounts in the statement currency, EPS per share, capex as a positive outflow). */
export const RAW_FIELDS = [
  'revenue',
  'costOfRevenue',
  'grossProfit',
  'operatingIncome',
  'operatingIncomeReported',
  'pretaxIncome',
  'taxProvision',
  'netIncome',
  'epsDiluted',
  'epsBasic',
  'dilutedShares',
  'interestExpense',
  'operatingCashFlow',
  'capex',
  'fcfYahoo',
  'equity',
  'totalDebt',
  'cash',
  'totalAssets',
  'dividendsPaid',
  'buybacks',
] as const;
export type RawField = (typeof RAW_FIELDS)[number];

export type PeriodKind = 'annual' | 'quarterly';
export type PeriodSource = 'sec' | 'finmind' | 'yahoo';
export type QualityLevel = 'good' | 'partial' | 'limited';
export type EpsEvidenceGrade = 'A' | 'B' | 'C' | 'D';
export interface PeriodEpsQuality {
  epsDiluted?: EpsEvidenceGrade;
  epsBasic?: EpsEvidenceGrade;
  /** SEC filing date for this Q4 fact; used to prevent historical look-ahead. */
  epsDilutedFiled?: string;
  epsBasicFiled?: string;
}

/** Retention limits of the builder (SEC-seeded files keep the longest history). */
export const MAX_PERIODS: Record<PeriodKind, number> = { annual: 20, quarterly: 60 };

export interface FinancialSeries {
  /** Period end dates, ascending (YYYY-MM-DD). */
  end: string[];
  /** Fiscal year as written by the builder (labels are recomputed by financialMetrics.ts). */
  fy: (number | null)[];
  /** Fiscal quarter as written by the builder; all null for annual periods. */
  fq: (number | null)[];
  /** Where each period came from; Yahoo unless the file says "sec" or "finmind" (official sources). */
  src: PeriodSource[];
  values: Record<RawField, (number | null)[]>;
  /** Per-period EPS grade and source filing dates; only A/B Q4 values are formal TTM inputs. */
  epsQuality?: (PeriodEpsQuality | null)[];
}

export interface FinancialHistory {
  version: 1;
  symbol: string;
  market: 'US' | 'TW';
  currency: string;
  /** Files annual reports as a foreign private issuer (ADR): EPS is per ordinary share unless adrRatio says otherwise. */
  foreignReporter: boolean;
  /** EPS basis of a foreign reporter whose SEC EPS were put on the ADR basis (see AdrRatio); null otherwise. */
  adrRatio: AdrRatio | null;
  generatedAt: string | null;
  /** Last day the builder changed this file's data (it does not rewrite unchanged files): 「資料更新日」. */
  checkedAt: string | null;
  source: string | null;
  /** Fiscal year-end month 1–12 from the file; null when absent or invalid. */
  fyEndMonth: number | null;
  annual: FinancialSeries;
  quarterly: FinancialSeries;
  split: { adjusted: boolean; events: { date: string; ratio: number }[] };
  quality: {
    level: QualityLevel | null;
    annualYears: number | null;
    quarters: number | null;
    issues: string[];
    /** Values the builder saw change (restated) or kept from SEC over a differing Yahoo value; newest last. */
    revisions: Revision[];
  };
}

/** 1 ADR = `epsFactor` ordinary shares: the EPS in this file is per ADR (SEC per-share EPS × epsFactor). */
export interface AdrRatio {
  epsFactor: number;
  /** Day the factor was derived (YYYY-MM-DD), if given. */
  derivedAt: string | null;
  /** Number of same-period EPS comparisons the factor was derived from, if given. */
  pairs: number | null;
}

export interface Revision {
  kind: 'restated' | 'yahoo-differs';
  period: PeriodKind;
  end: string;
  field: RawField;
  old: number | null;
  new: number | null;
  /** When the builder saw the change (YYYY-MM-DD), if given. */
  seen: string | null;
}

const MAX_REVISIONS = 50;
function parseRevisions(value: unknown): Revision[] {
  return arrayOf(value)
    .flatMap((item): Revision[] => {
      const raw = recordOf(item);
      const end = validDate(raw.end);
      const kind = raw.kind === 'restated' || raw.kind === 'yahoo-differs' ? raw.kind : null;
      const period = raw.period === 'annual' || raw.period === 'quarterly' ? raw.period : null;
      const field = (RAW_FIELDS as readonly unknown[]).includes(raw.field) ? (raw.field as RawField) : null;
      if (!end || !kind || !period || !field) return [];
      return [{ kind, period, end, field, old: finiteOrNull(raw.old), new: finiteOrNull(raw.new), seen: validDate(raw.seen) }];
    })
    .slice(-MAX_REVISIONS);
}

/** The builder's adrRatio; anything without a positive epsFactor is ignored (no ADR basis note). */
function parseAdrRatio(value: unknown): AdrRatio | null {
  const raw = recordOf(value);
  const epsFactor = finiteOrNull(raw.epsFactor);
  if (epsFactor === null || epsFactor <= 0) return null;
  const pairs = finiteOrNull(raw.pairs);
  return {
    epsFactor,
    derivedAt: validDate(raw.derivedAt),
    pairs: pairs === null ? null : Math.max(0, Math.floor(pairs)),
  };
}

export type FinancialsLoadResult =
  | { status: 'ok'; data: FinancialHistory; url: string }
  | { status: 'missing' | 'invalid' | 'error'; message: string; url: string };

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const ISSUE = /^[a-z0-9-]{1,40}$/;

const finiteOrNull = (value: unknown): number | null => {
  const number = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  return typeof number === 'number' && Number.isFinite(number) ? number : null;
};
const validDate = (value: unknown): string | null => {
  if (typeof value !== 'string') return null;
  const day = value.trim().slice(0, 10);
  if (!DATE.test(day)) return null;
  const time = Date.parse(`${day}T00:00:00Z`);
  return Number.isNaN(time) || new Date(time).toISOString().slice(0, 10) !== day ? null : day;
};
const optionalText = z
  .unknown()
  .optional()
  .transform((value) => (typeof value === 'string' && value.trim() ? value.trim() : null));
const arrayOf = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const recordOf = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

export function emptySeries(): FinancialSeries {
  const values = {} as Record<RawField, (number | null)[]>;
  for (const field of RAW_FIELDS) values[field] = [];
  return { end: [], fy: [], fq: [], src: [], values, epsQuality: [] };
}

/** Rebuilds one period block: valid ends only, ascending, one period per end date, newest `limit` kept. */
export function parseSeries(raw: unknown, kind: PeriodKind): FinancialSeries {
  const block = recordOf(raw);
  const ends = arrayOf(block.end);
  const fy = arrayOf(block.fy);
  const fq = arrayOf(block.fq);
  const src = arrayOf(block.src);
  const epsQuality = arrayOf(block.epsQuality);
  const rawValues = recordOf(block.values);
  const columns = RAW_FIELDS.map((field) => arrayOf(rawValues[field]));
  const byEnd = new Map<string, number>();
  ends.forEach((value, index) => {
    const end = validDate(value);
    // A repeated end keeps the later entry (the builder appends newer data).
    if (end) byEnd.set(end, index);
  });
  const order = [...byEnd.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).slice(-MAX_PERIODS[kind]);
  const out = emptySeries();
  for (const [end, index] of order) {
    out.end.push(end);
    const year = finiteOrNull(fy[index]);
    out.fy.push(year !== null && Number.isInteger(year) ? year : null);
    const quarter = finiteOrNull(fq[index]);
    out.fq.push(kind === 'quarterly' && quarter !== null && [1, 2, 3, 4].includes(quarter) ? quarter : null);
    out.src.push(src[index] === 'sec' ? 'sec' : src[index] === 'finmind' ? 'finmind' : 'yahoo');
    const rawGrade = recordOf(epsQuality[index]);
    const grade = (value: unknown): EpsEvidenceGrade | undefined =>
      value === 'A' || value === 'B' || value === 'C' || value === 'D' ? value : undefined;
    const epsDilutedGrade = grade(rawGrade.epsDiluted);
    const epsBasicGrade = grade(rawGrade.epsBasic);
    const epsDilutedFiled = validDate(rawGrade.epsDilutedFiled) ?? undefined;
    const epsBasicFiled = validDate(rawGrade.epsBasicFiled) ?? undefined;
    out.epsQuality!.push(epsDilutedGrade || epsBasicGrade
      ? { epsDiluted: epsDilutedGrade, epsBasic: epsBasicGrade,
          epsDilutedFiled, epsBasicFiled }
      : null);
    RAW_FIELDS.forEach((field, column) => {
      let value = finiteOrNull(columns[column]![index]);
      // Capex is stored as a positive outflow; a stray sign must not inflate free cash flow.
      if (field === 'capex' && value !== null) value = Math.abs(value);
      out.values[field].push(value);
    });
  }
  return out;
}

const marketOf = (value: unknown, symbol: string): 'US' | 'TW' =>
  value === 'TW' || value === 'US' ? value : /\.TWO?$/.test(symbol) ? 'TW' : 'US';

const historySchema = z
  .object({
    version: z.unknown().optional(),
    symbol: optionalText,
    market: z.unknown().optional(),
    currency: optionalText,
    foreignReporter: z.unknown().optional(),
    adrRatio: z.unknown().optional(),
    generatedAt: optionalText,
    checkedAt: optionalText,
    source: optionalText,
    fyEndMonth: z.unknown().optional(),
    annual: z.unknown().optional(),
    quarterly: z.unknown().optional(),
    split: z.unknown().optional(),
    quality: z.unknown().optional(),
    secQ4EpsFacts: z.unknown().optional(),
  })
  .transform((raw) => {
    const symbol = (raw.symbol ?? '').toUpperCase();
    const market = marketOf(raw.market, symbol);
    const month = finiteOrNull(raw.fyEndMonth);
    const split = recordOf(raw.split);
    const quality = recordOf(raw.quality);
    const level = quality.level;
    const count = (value: unknown) => {
      const number = finiteOrNull(value);
      return number === null ? null : Math.max(0, Math.floor(number));
    };
    const quarterly = parseSeries(raw.quarterly, 'quarterly');
    const q4Facts = recordOf(raw.secQ4EpsFacts);
    quarterly.epsQuality = quarterly.end.map((end, index) => {
      const existing = quarterly.epsQuality?.[index] ?? null;
      const source = recordOf(q4Facts[end]);
      const dilutedFiled = validDate(recordOf(source.epsDiluted).filed) ?? existing?.epsDilutedFiled;
      const basicFiled = validDate(recordOf(source.epsBasic).filed) ?? existing?.epsBasicFiled;
      if (!existing && !dilutedFiled && !basicFiled) return null;
      return { ...(existing ?? {}), epsDilutedFiled: dilutedFiled, epsBasicFiled: basicFiled };
    });
    const history: FinancialHistory = {
      version: 1,
      symbol,
      market,
      currency: (raw.currency ?? (market === 'TW' ? 'TWD' : 'USD')).toUpperCase(),
      foreignReporter: raw.foreignReporter === true,
      adrRatio: parseAdrRatio(raw.adrRatio),
      generatedAt: raw.generatedAt,
      checkedAt: validDate(raw.checkedAt),
      source: raw.source,
      fyEndMonth: month !== null && Number.isInteger(month) && month >= 1 && month <= 12 ? month : null,
      annual: parseSeries(raw.annual, 'annual'),
      quarterly,
      split: {
        adjusted: split.adjusted === true,
        events: arrayOf(split.events).flatMap((event) => {
          const pair = arrayOf(event);
          const date = validDate(pair[0]);
          const ratio = finiteOrNull(pair[1]);
          return date && ratio !== null && ratio > 0 ? [{ date, ratio }] : [];
        }),
      },
      quality: {
        level: level === 'good' || level === 'partial' || level === 'limited' ? level : null,
        annualYears: count(quality.annualYears),
        quarters: count(quality.quarters),
        issues: [
          ...new Set(
            arrayOf(quality.issues).filter((issue): issue is string => typeof issue === 'string' && ISSUE.test(issue)),
          ),
        ],
        revisions: parseRevisions(quality.revisions),
      },
    };
    return history;
  });

/**
 * File key: Taiwan `2330.TW` / `6488.TWO` as is; US upper-case with `.` → `-` (`BRK.B` → `BRK-B`, same
 * as growthKey). Null for indices and anything that cannot name a stock file.
 */
export function financialsKey(symbol: string | null | undefined): string | null {
  const value = (symbol ?? '').trim().toUpperCase();
  if (!value || INDEX_SYMBOL_REGEX.test(value) || value.startsWith('^')) return null;
  if (TAIWAN_SYMBOL_REGEX.test(value)) return value;
  if (/\.(TW|TWO|SS|SZ)$/.test(value)) return null;
  const key = value.replace(/\./g, '-');
  return /^[A-Z][A-Z0-9-]{0,14}$/.test(key) ? key : null;
}

/** `financials/<KEY>.json` next to `latest.json` (or next to `VITE_REPORT_URL`). */
export function financialsUrl(
  key: string,
  base: string = import.meta.env?.BASE_URL ?? '/',
  override: string | undefined = import.meta.env?.VITE_REPORT_URL,
  origin: string = typeof location === 'undefined' ? 'http://localhost' : location.origin,
): string {
  return new URL(`financials/${encodeURIComponent(key)}.json`, reportUrl(base, override, origin)).href;
}

const INVALID = '財報資料格式無法辨識。';
const MISSING = '這檔股票的財報資料尚未產生。';

/** Parses an already-fetched body. Never throws. */
export function parseFinancials(data: unknown, url = ''): FinancialsLoadResult {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return { status: 'invalid', message: INVALID, url };
  const version = (data as Record<string, unknown>).version;
  if (version !== undefined && finiteOrNull(version) !== 1) return { status: 'invalid', message: INVALID, url };
  const parsed = historySchema.safeParse(data);
  if (!parsed.success) return { status: 'invalid', message: INVALID, url };
  if (!parsed.data.annual.end.length && !parsed.data.quarterly.end.length)
    return { status: 'missing', message: '這檔股票目前沒有可用的財報期別。', url };
  return { status: 'ok', data: parsed.data, url };
}

/** Fetches and validates one file. Network, HTTP and JSON failures resolve to a friendly state. */
export async function fetchFinancials(url: string, fetcher: typeof fetch = fetch, signal?: AbortSignal): Promise<FinancialsLoadResult> {
  let response: Response;
  try {
    response = await fetcher(url, { cache: 'no-cache', signal });
  } catch {
    return { status: 'error', message: '無法連線取得財報資料，請稍後再試。', url };
  }
  if (response.status === 404) return { status: 'missing', message: MISSING, url };
  if (!response.ok) return { status: 'error', message: `財報資料暫時無法取得（HTTP ${response.status}）。`, url };
  let data: unknown;
  try {
    // An SPA fallback can answer with index.html; treat non-JSON as an unreadable file.
    data = JSON.parse(await response.text());
  } catch {
    return { status: 'invalid', message: INVALID, url };
  }
  return parseFinancials(data, url);
}

const RELOAD_AFTER_MS = 30 * 60_000;

export interface FinancialsProviderOptions {
  fetcher?: typeof fetch;
  now?: () => number;
  urlOf?: (key: string) => string;
}

/**
 * Per-symbol cache: a result is reused for 30 minutes; a network/HTTP error is never pinned, and when an
 * earlier load of the same file succeeded that copy is returned instead of the error.
 */
export class FinancialsProvider {
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  private readonly urlOf: (key: string) => string;
  private readonly entries = new Map<string, { at: number; promise: Promise<FinancialsLoadResult> }>();
  private readonly lastGood = new Map<string, FinancialsLoadResult & { status: 'ok' }>();

  constructor(options: FinancialsProviderOptions = {}) {
    this.fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
    this.now = options.now ?? Date.now;
    this.urlOf = options.urlOf ?? ((key) => financialsUrl(key));
  }

  load(symbol: string, refresh = false): Promise<FinancialsLoadResult> {
    const key = financialsKey(symbol);
    if (!key)
      return Promise.resolve({ status: 'missing', message: '指數或這種代號沒有個股財報。', url: '' });
    const cached = this.entries.get(key);
    if (!refresh && cached && this.now() - cached.at < RELOAD_AFTER_MS) return cached.promise;
    const promise: Promise<FinancialsLoadResult> = fetchFinancials(this.urlOf(key), this.fetcher).then((result) => {
      if (result.status === 'ok') this.lastGood.set(key, result);
      else if (result.status === 'error') {
        // Do not pin a transient failure: the next load retries.
        if (this.entries.get(key)?.promise === promise) this.entries.delete(key);
        return this.lastGood.get(key) ?? result;
      }
      return result;
    });
    this.entries.set(key, { at: this.now(), promise });
    return promise;
  }
}

let defaultProvider: FinancialsProvider | null = null;

/** Shared loader for the financials page (cached per symbol for 30 minutes). */
export function loadFinancials(symbol: string, refresh = false): Promise<FinancialsLoadResult> {
  defaultProvider ??= new FinancialsProvider();
  return defaultProvider.load(symbol, refresh);
}

/** Where the periods of one block came from, for the provenance note. */
export interface ProvenanceSpan {
  source: PeriodSource;
  from: string;
  to: string;
  count: number;
}
export function provenance(series: FinancialSeries): ProvenanceSpan[] {
  const spans: ProvenanceSpan[] = [];
  series.end.forEach((end, index) => {
    const source = series.src[index] ?? 'yahoo';
    const last = spans[spans.length - 1];
    if (last && last.source === source) {
      last.to = end;
      last.count += 1;
    } else spans.push({ source, from: end, to: end, count: 1 });
  });
  return spans;
}

export const hasSecHistory = (history: FinancialHistory) =>
  history.annual.src.includes('sec') || history.quarterly.src.includes('sec');

export const hasFinmindHistory = (history: FinancialHistory) =>
  history.annual.src.includes('finmind') || history.quarterly.src.includes('finmind');

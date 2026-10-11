import { z } from 'zod';
import { reportUrl } from './loadReport';

/**
 * US Treasury rates research data, written by scripts/build_rates.py next to latest.json:
 * - `rates.json`: summary (curve snapshots, spreads, breakevens, the five long-Treasury ETFs);
 * - `rates-history.json`: weekly history since 2003, the last ~260 daily points and weekly ETF
 *   total-return indices (inception = 100).
 *
 * Tolerant like breadth.ts: unknown fields are ignored (including a legacy `tmf` block), numbers may
 * arrive as strings, a bad ETF / spread / curve entry is dropped instead of failing the file, and
 * missing optional blocks become null / empty. Units follow the data contract: yields and breakevens
 * in percent, spreads in basis points, returns / volatility / drawdowns in percent, durations in
 * years, convexity in years² (ΔP/P ≈ −D·Δy + ½·C·Δy² with Δy as a decimal).
 */

export const DEFAULT_TENORS = ['1M', '3M', '6M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y'] as const;
export const DEFAULT_TENOR_YEARS = [1 / 12, 0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30] as const;
export const DEFAULT_REAL_TENORS = ['5Y', '7Y', '10Y', '20Y', '30Y'] as const;
/** FRED series behind each tenor of the nominal and real (TIPS) curves. */
export const NOMINAL_SERIES: Record<string, string> = {
  '1M': 'DGS1MO',
  '3M': 'DGS3MO',
  '6M': 'DGS6MO',
  '1Y': 'DGS1',
  '2Y': 'DGS2',
  '3Y': 'DGS3',
  '5Y': 'DGS5',
  '7Y': 'DGS7',
  '10Y': 'DGS10',
  '20Y': 'DGS20',
  '30Y': 'DGS30',
};
export const REAL_SERIES: Record<string, string> = { '5Y': 'DFII5', '7Y': 'DFII7', '10Y': 'DFII10', '20Y': 'DFII20', '30Y': 'DFII30' };

/** Summary and history are cached this long per page session. */
export const RATES_CACHE_MS = 30 * 60 * 1000;

// ---------------------------------------------------------------- tolerant primitives
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const finiteOrNull = (value: unknown): number | null => {
  const number = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  return typeof number === 'number' && Number.isFinite(number) ? number : null;
};
const textOrNull = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value.trim() : null);
const dateOrNull = (value: unknown): string | null => {
  const text = textOrNull(value);
  return text && DATE.test(text.slice(0, 10)) ? text.slice(0, 10) : null;
};
const recordOf = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
const arrayOf = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const intOrNull = (value: unknown): number | null => {
  const number = finiteOrNull(value);
  return number === null ? null : Math.round(number);
};
const fit = <T>(values: T[], length: number, fill: T): T[] =>
  values.length >= length ? values.slice(0, length) : [...values, ...Array.from({ length: length - values.length }, () => fill)];

/** Parses each element and drops the ones that come back null. */
function listOf<T>(value: unknown, parse: (item: unknown) => T | null): T[] {
  const out: T[] = [];
  for (const item of arrayOf(value)) {
    const parsed = parse(item);
    if (parsed !== null) out.push(parsed);
  }
  return out;
}

// ---------------------------------------------------------------- rates.json types
export interface CurveSnapshot {
  /** `now`, `1w`, `1m`, `3m`, `1y`, `2y` (unknown keys are kept). */
  key: string;
  date: string | null;
  /** Yields in percent aligned with `tenors` (nominal) or `realTenors` (real). */
  values: (number | null)[];
}
export interface InversionEpisode {
  start: string;
  /** Null while the inversion is still running. */
  end: string | null;
  minBp: number | null;
  minDate: string | null;
}
export interface RatesSpread {
  key: string;
  /** FRED IDs of the long and short legs (for example DGS10 / DGS2). */
  long: string | null;
  short: string | null;
  valueBp: number | null;
  chg1mBp: number | null;
  inverted: boolean;
  /** Length of the current inversion as written by the generator (0 when not inverted). */
  invertedDays: number | null;
  lastInversion: InversionEpisode | null;
  /** Percentile of today's value within the last five years, 0–100. */
  pct5y: number | null;
}
export interface InflationPoint {
  value: number | null;
  date: string | null;
  /** One-month change in percentage points (same unit as `value`). */
  chg1m: number | null;
  /** True for 20Y/30Y: nominal minus TIPS yield, not a published FRED breakeven. */
  derived: boolean;
}
export interface WindowReturn {
  total: number | null;
  annualized: number | null;
  start: string | null;
}
export interface Regression {
  /** Daily / weekly return (decimal) per 1 percentage point of yield change. */
  beta: number | null;
  /** Empirical duration in years, −100·beta. */
  durEmp: number | null;
  r2: number | null;
  n: number | null;
}
export interface Sensitivity {
  daily1y: Regression | null;
  weekly3y: Regression | null;
}
export interface Drawdown {
  value: number | null;
  peak: string | null;
  trough: string | null;
  recovered: string | null;
}
export interface RatesEtf {
  symbol: string;
  name: string;
  issuer: string | null;
  index: string | null;
  inception: string | null;
  /** Daily leverage multiple: 1, or 3 for TMF (daily reset). */
  leverage: number;
  /** Annual expense ratio in percent. */
  expenseRatio: number | null;
  expenseNote: string | null;
  /** Issuer-published effective duration (years) of the unlevered portfolio, when known. */
  effectiveDuration: number | null;
  maturityRange: string | null;
  holdings: string | null;
  staticAsOf: string | null;
  staticSource: string | null;
  /** Static fields were hand-entered; false until the user has checked them. */
  verified: boolean;
  /** Model duration (years) and convexity (years²) of the unlevered proxy portfolio. */
  model: { duration: number | null; convexity: number | null; proxy: string | null } | null;
  price: { asOf: string | null; close: number | null; adjClose: number | null } | null;
  returns: Record<'1y' | '3y' | '5y' | '10y' | 'inception', WindowReturn | null>;
  risk: {
    vol1y: number | null;
    vol3y: number | null;
    vol5y: number | null;
    maxDrawdown: Drawdown | null;
    mdd5y: number | null;
    currentDrawdown: number | null;
  };
  sensitivity: { dgs20: Sensitivity | null; dgs30: Sensitivity | null };
}
export interface Rates {
  version: number | null;
  generatedAt: string | null;
  /** Latest DGS10 observation date. */
  asOf: string | null;
  sources: { rates: string | null; etf: string | null; etfAsOf: string | null };
  /** Series the generator could not refresh (kept from the previous file). */
  missing: string[];
  tenors: string[];
  tenorYears: (number | null)[];
  realTenors: string[];
  curves: { nominal: CurveSnapshot[]; real: CurveSnapshot[] };
  policy: { lower: number | null; upper: number | null; date: string | null } | null;
  inflation: Record<string, InflationPoint>;
  spreads: RatesSpread[];
  etfs: RatesEtf[];
  /** Source and limitation notes (Chinese), in file order. */
  notes: string[];
}

// ---------------------------------------------------------------- rates.json parsing
const curveOf = (length: number) => (item: unknown): CurveSnapshot | null => {
  const raw = recordOf(item);
  const key = textOrNull(raw?.key);
  if (!raw || !key) return null;
  return { key, date: dateOrNull(raw.date), values: fit(arrayOf(raw.values).map(finiteOrNull), length, null) };
};

function spreadOf(item: unknown): RatesSpread | null {
  const raw = recordOf(item);
  const key = textOrNull(raw?.key);
  if (!raw || !key) return null;
  const last = recordOf(raw.lastInversion);
  const start = dateOrNull(last?.start);
  const valueBp = finiteOrNull(raw.valueBp);
  return {
    key,
    long: textOrNull(raw.long),
    short: textOrNull(raw.short),
    valueBp,
    chg1mBp: finiteOrNull(raw.chg1mBp),
    inverted: typeof raw.inverted === 'boolean' ? raw.inverted : valueBp !== null && valueBp < 0,
    invertedDays: intOrNull(raw.invertedDays),
    lastInversion:
      last && start
        ? { start, end: dateOrNull(last.end), minBp: finiteOrNull(last.minBp), minDate: dateOrNull(last.minDate) }
        : null,
    pct5y: finiteOrNull(raw.pct5y),
  };
}

function inflationOf(value: unknown): Record<string, InflationPoint> {
  const raw = recordOf(value) ?? {};
  const out: Record<string, InflationPoint> = {};
  for (const [key, item] of Object.entries(raw)) {
    const point = recordOf(item);
    if (!point) continue;
    out[key] = {
      value: finiteOrNull(point.value),
      date: dateOrNull(point.date),
      chg1m: finiteOrNull(point.chg1m),
      derived: point.derived === true,
    };
  }
  return out;
}

function windowReturnOf(value: unknown): WindowReturn | null {
  const raw = recordOf(value);
  if (!raw) return null;
  const total = finiteOrNull(raw.total);
  const annualized = finiteOrNull(raw.annualized);
  if (total === null && annualized === null) return null;
  return { total, annualized, start: dateOrNull(raw.start) };
}

function regressionOf(value: unknown): Regression | null {
  const raw = recordOf(value);
  if (!raw) return null;
  const beta = finiteOrNull(raw.beta);
  const durEmp = finiteOrNull(raw.durEmp) ?? (beta === null ? null : -100 * beta);
  if (beta === null && durEmp === null) return null;
  return { beta, durEmp, r2: finiteOrNull(raw.r2), n: intOrNull(raw.n) };
}

function sensitivityOf(value: unknown): Sensitivity | null {
  const raw = recordOf(value);
  if (!raw) return null;
  const daily1y = regressionOf(raw.daily1y);
  const weekly3y = regressionOf(raw.weekly3y);
  return daily1y || weekly3y ? { daily1y, weekly3y } : null;
}

function drawdownOf(value: unknown): Drawdown | null {
  const raw = recordOf(value);
  const number = finiteOrNull(value);
  if (number !== null) return { value: number, peak: null, trough: null, recovered: null };
  if (!raw || finiteOrNull(raw.value) === null) return null;
  return { value: finiteOrNull(raw.value), peak: dateOrNull(raw.peak), trough: dateOrNull(raw.trough), recovered: dateOrNull(raw.recovered) };
}

function etfOf(item: unknown): RatesEtf | null {
  const raw = recordOf(item);
  const symbol = textOrNull(raw?.symbol)?.toUpperCase();
  if (!raw || !symbol) return null;
  const leverage = finiteOrNull(raw.leverage);
  const model = recordOf(raw.model);
  const price = recordOf(raw.price);
  const returns = recordOf(raw.returns) ?? {};
  const risk = recordOf(raw.risk) ?? {};
  const sensitivity = recordOf(raw.sensitivity) ?? {};
  return {
    symbol,
    name: textOrNull(raw.name) ?? symbol,
    issuer: textOrNull(raw.issuer),
    index: textOrNull(raw.index),
    inception: dateOrNull(raw.inception),
    leverage: leverage !== null && leverage > 0 ? leverage : 1,
    expenseRatio: finiteOrNull(raw.expenseRatio),
    expenseNote: textOrNull(raw.expenseNote),
    effectiveDuration: finiteOrNull(raw.effectiveDuration),
    maturityRange: textOrNull(raw.maturityRange),
    holdings: textOrNull(raw.holdings),
    staticAsOf: dateOrNull(raw.staticAsOf),
    staticSource: textOrNull(raw.staticSource),
    verified: raw.verified === true,
    model: model
      ? { duration: finiteOrNull(model.duration), convexity: finiteOrNull(model.convexity), proxy: textOrNull(model.proxy) }
      : null,
    price: price ? { asOf: dateOrNull(price.asOf), close: finiteOrNull(price.close), adjClose: finiteOrNull(price.adjClose) } : null,
    returns: {
      '1y': windowReturnOf(returns['1y']),
      '3y': windowReturnOf(returns['3y']),
      '5y': windowReturnOf(returns['5y']),
      '10y': windowReturnOf(returns['10y']),
      inception: windowReturnOf(returns.inception),
    },
    risk: {
      vol1y: finiteOrNull(risk.vol1y),
      vol3y: finiteOrNull(risk.vol3y),
      vol5y: finiteOrNull(risk.vol5y),
      maxDrawdown: drawdownOf(risk.maxDrawdown),
      mdd5y: finiteOrNull(risk.mdd5y),
      currentDrawdown: finiteOrNull(risk.currentDrawdown),
    },
    sensitivity: { dgs20: sensitivityOf(sensitivity.dgs20), dgs30: sensitivityOf(sensitivity.dgs30) },
  };
}

function notesOf(value: unknown): string[] {
  if (typeof value === 'string') return textOrNull(value) ? [value.trim()] : [];
  const list = Array.isArray(value) ? value : Object.values(recordOf(value) ?? {});
  return list.map(textOrNull).filter((note): note is string => note !== null);
}

const textList = (value: unknown) => arrayOf(value).map(textOrNull).filter((item): item is string => item !== null);

export const ratesSchema = z.unknown().transform((value): Rates | null => {
  const raw = recordOf(value);
  if (!raw) return null;
  const tenors = textList(raw.tenors);
  const useTenors = tenors.length ? tenors : [...DEFAULT_TENORS];
  const tenorYears = fit(arrayOf(raw.tenorYears).map(finiteOrNull), useTenors.length, null);
  const realTenors = textList(raw.realTenors);
  const useReal = realTenors.length ? realTenors : [...DEFAULT_REAL_TENORS];
  const curves = recordOf(raw.curves) ?? {};
  const sources = recordOf(raw.sources);
  const policy = recordOf(raw.policy);
  return {
    version: finiteOrNull(raw.version),
    generatedAt: textOrNull(raw.generatedAt),
    asOf: dateOrNull(raw.asOf),
    sources: {
      rates: textOrNull(sources?.rates),
      etf: textOrNull(sources?.etf),
      etfAsOf: dateOrNull(sources?.etfAsOf),
    },
    missing: textList(raw.missing),
    tenors: useTenors,
    // A missing year falls back to the standard tenor of the same name.
    tenorYears: tenorYears.map((year, index) => {
      if (year !== null) return year;
      const standard = (DEFAULT_TENORS as readonly string[]).indexOf(useTenors[index]!);
      return standard >= 0 ? DEFAULT_TENOR_YEARS[standard]! : null;
    }),
    realTenors: useReal,
    curves: { nominal: listOf(curves.nominal, curveOf(useTenors.length)), real: listOf(curves.real, curveOf(useReal.length)) },
    policy:
      policy && (finiteOrNull(policy.upper) !== null || finiteOrNull(policy.lower) !== null)
        ? { lower: finiteOrNull(policy.lower), upper: finiteOrNull(policy.upper), date: dateOrNull(policy.date) }
        : null,
    inflation: inflationOf(raw.inflation),
    spreads: listOf(raw.spreads, spreadOf),
    etfs: listOf(raw.etfs, etfOf),
    notes: notesOf(raw.notes),
  };
});

// ---------------------------------------------------------------- rates-history.json
export interface HistoryBlock {
  /** Ascending YYYY-MM-DD dates. */
  dates: string[];
  /** Series aligned with `dates` (FRED IDs for yields; ETF symbols for the etf block). */
  series: Record<string, (number | null)[]>;
}
export interface RatesHistory {
  version: number | null;
  generatedAt: string | null;
  asOf: string | null;
  weekly: HistoryBlock;
  daily: HistoryBlock;
  /** Weekly total-return indices, inception = 100, null before inception. */
  etf: HistoryBlock;
}

/** Keeps points with a valid date (sorted, last duplicate wins) and every array-valued series, re-aligned. */
export function historyBlockOf(value: unknown): HistoryBlock {
  const raw = recordOf(value);
  const dates = arrayOf(raw?.dates);
  const rawSeries = recordOf(raw?.series) ?? {};
  const columns = Object.entries(rawSeries).filter((entry): entry is [string, unknown[]] => Array.isArray(entry[1]));
  const byDate = new Map<string, number>();
  dates.forEach((day, index) => {
    const date = typeof day === 'string' && DATE.test(day) ? day : null;
    if (date) byDate.set(date, index);
  });
  const ordered = [...byDate.entries()].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  const series: Record<string, (number | null)[]> = {};
  for (const [key, column] of columns) series[key] = ordered.map(([, index]) => finiteOrNull(column[index]));
  return { dates: ordered.map(([date]) => date), series };
}

export const ratesHistorySchema = z.unknown().transform((value): RatesHistory | null => {
  const raw = recordOf(value);
  if (!raw) return null;
  return {
    version: finiteOrNull(raw.version),
    generatedAt: textOrNull(raw.generatedAt),
    asOf: dateOrNull(raw.asOf),
    weekly: historyBlockOf(raw.weekly),
    daily: historyBlockOf(raw.daily),
    etf: historyBlockOf(raw.etf),
  };
});

// ---------------------------------------------------------------- loading
export type LoadResult<T> =
  | { status: 'ok'; data: T; url: string }
  | { status: 'missing' | 'invalid' | 'error'; message: string; url: string };
/** A cached result: `stale` marks the last good copy served after a failed refresh. */
export type CachedResult<T> =
  | { status: 'ok'; data: T; url: string; stale: boolean; message?: string }
  | { status: 'missing' | 'invalid' | 'error'; message: string; url: string };

const MESSAGES = {
  rates: { label: '美債利率資料', missing: '美債利率資料尚未產生。' },
  history: { label: '利率歷史資料', missing: '利率歷史資料尚未產生。' },
} as const;

export function ratesUrl(
  base: string = import.meta.env?.BASE_URL ?? '/',
  override: string | undefined = import.meta.env?.VITE_REPORT_URL,
  origin: string = typeof location === 'undefined' ? 'http://localhost' : location.origin,
): string {
  return new URL('rates.json', reportUrl(base, override, origin)).href;
}
export function ratesHistoryUrl(
  base: string = import.meta.env?.BASE_URL ?? '/',
  override: string | undefined = import.meta.env?.VITE_REPORT_URL,
  origin: string = typeof location === 'undefined' ? 'http://localhost' : location.origin,
): string {
  return new URL('rates-history.json', reportUrl(base, override, origin)).href;
}

/** Parses an already-fetched rates.json body. Never throws. */
export function parseRates(data: unknown, url = ''): LoadResult<Rates> {
  const parsed = ratesSchema.safeParse(data);
  if (!parsed.success || !parsed.data) return { status: 'invalid', message: '美債利率資料格式無法辨識。', url };
  const rates = parsed.data;
  const hasCurve = rates.curves.nominal.some((curve) => curve.values.some((value) => value !== null));
  if (!hasCurve && !rates.etfs.length && !rates.spreads.length) return { status: 'missing', message: MESSAGES.rates.missing, url };
  return { status: 'ok', data: rates, url };
}

/** Parses an already-fetched rates-history.json body. Never throws. */
export function parseRatesHistory(data: unknown, url = ''): LoadResult<RatesHistory> {
  const parsed = ratesHistorySchema.safeParse(data);
  if (!parsed.success || !parsed.data) return { status: 'invalid', message: '利率歷史資料格式無法辨識。', url };
  const history = parsed.data;
  if (!history.weekly.dates.length && !history.daily.dates.length && !history.etf.dates.length)
    return { status: 'missing', message: MESSAGES.history.missing, url };
  return { status: 'ok', data: history, url };
}

async function fetchJson<T>(
  url: string,
  fetcher: typeof fetch,
  kind: keyof typeof MESSAGES,
  parse: (data: unknown, url: string) => LoadResult<T>,
  signal?: AbortSignal,
): Promise<LoadResult<T>> {
  const { label, missing } = MESSAGES[kind];
  let response: Response;
  try {
    response = await fetcher(url, { cache: 'no-cache', signal });
  } catch {
    return { status: 'error', message: `無法連線取得${label}，請稍後再試。`, url };
  }
  if (response.status === 404) return { status: 'missing', message: missing, url };
  if (!response.ok) return { status: 'error', message: `${label}暫時無法取得（HTTP ${response.status}）。`, url };
  let data: unknown;
  try {
    // An SPA fallback can answer with index.html; treat non-JSON as an unreadable file.
    data = JSON.parse(await response.text());
  } catch {
    return { status: 'invalid', message: `${label}格式無法辨識。`, url };
  }
  return parse(data, url);
}

export function fetchRates(url = ratesUrl(), fetcher: typeof fetch = fetch, signal?: AbortSignal) {
  return fetchJson(url, fetcher, 'rates', parseRates, signal);
}
export function fetchRatesHistory(url = ratesHistoryUrl(), fetcher: typeof fetch = fetch, signal?: AbortSignal) {
  return fetchJson(url, fetcher, 'history', parseRatesHistory, signal);
}

/**
 * Wraps a fetch in a per-session cache: a good result is reused for `ttlMs`; concurrent callers share
 * one request; when a refresh fails the last good copy is served with `stale: true`; failures are
 * never pinned, so the next call retries.
 */
export function createCachedLoader<T>(
  fetchOnce: () => Promise<LoadResult<T>>,
  now: () => number = Date.now,
  ttlMs = RATES_CACHE_MS,
) {
  let good: { result: CachedResult<T>; at: number } | null = null;
  let inflight: Promise<CachedResult<T>> | null = null;
  return {
    load(refresh = false): Promise<CachedResult<T>> {
      if (!refresh && good && now() - good.at < ttlMs) return Promise.resolve(good.result);
      if (inflight) return inflight;
      const request = fetchOnce()
        .then((result): CachedResult<T> => {
          if (result.status === 'ok') {
            const fresh: CachedResult<T> = { ...result, stale: false };
            good = { result: fresh, at: now() };
            return fresh;
          }
          if (good && good.result.status === 'ok') return { ...good.result, stale: true, message: result.message };
          return result;
        })
        .finally(() => {
          if (inflight === request) inflight = null;
        });
      inflight = request;
      return request;
    },
  };
}

const ratesLoader = createCachedLoader(() => fetchRates());
const historyLoader = createCachedLoader(() => fetchRatesHistory());
/** rates.json, cached 30 minutes per page session. */
export const loadRates = (refresh = false) => ratesLoader.load(refresh);
/** rates-history.json, cached 30 minutes per page session. */
export const loadRatesHistory = (refresh = false) => historyLoader.load(refresh);

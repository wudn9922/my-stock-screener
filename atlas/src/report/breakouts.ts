import { z } from 'zod';
import { reportUrl } from './loadReport';
import { countOf, finiteOrNull, optionalText, pctOf, text } from './breadth';

/**
 * Breakout win rates (`report/breakouts.json`, written by scripts/build_breakouts.py from the local
 * daily bars of the same universes as the breadth card).
 *
 * Tolerant like breadth.ts: unknown fields are ignored, numbers may arrive as strings, a strategy or
 * window that does not parse is dropped, and a market without any usable window is left out instead
 * of failing the whole file. Missing `params` fall back to the definitions the job ships with.
 */

export const BREAKOUT_MARKET_KEYS = ['tw', 'us'] as const;
export type BreakoutMarketKey = (typeof BREAKOUT_MARKET_KEYS)[number];

export interface BreakoutStrategyMeta {
  key: string;
  label: string;
  kind: string;
  lookback: number | null;
  /** Legacy volume-confirmed variant, retained in reports but omitted from the three method families. */
  volume: boolean;
  definition: string;
}
export interface BreakoutWindowMeta {
  key: string;
  days: number;
  label: string;
}
export interface BreakoutParams {
  windows: BreakoutWindowMeta[];
  matureDays: number;
  minSample: number;
  cooldownDays: number | null;
  jumpLimitPct: number | null;
  volumeLookback: number | null;
  volumeMultiple: number | null;
  boxLookback: number | null;
  strategies: BreakoutStrategyMeta[];
}
export interface BreakoutStats {
  /** Stocks with enough clean history to evaluate the strategy over the whole window. */
  eligible: number;
  /** First fresh breakout per stock inside the window, matured or not. */
  n: number;
  /** Breakouts held at least `matureDays` trading days; every rate below uses only these. */
  maturedN: number;
  /** Exact numerator; absent in legacy reports, never reconstructed from a rounded rate. */
  wins: number | null;
  winRate: number | null;
  avgReturn: number | null;
  medianReturn: number | null;
  failRate: number | null;
  avgHoldDays: number | null;
  /** Share of the whole universe up from the same entry days to asOf (matched to the breakouts). */
  baseRate: number | null;
  baseAvgReturn: number | null;
}
export interface BreakoutWindow {
  days: number;
  start: string | null;
  maturedThrough: string | null;
  market: {
    entryDays: number;
    pairs: number;
    upRate: number | null;
    avgReturn: number | null;
  };
  strategies: Record<string, BreakoutStats>;
}
export interface BreakoutMarket {
  scope: 'full' | 'legacy';
  coverage: { loaded: number; missing: number } | null;
  listingAsOf: string | null;
  listingSource: string;
  asOf: string | null;
  universe: string;
  universeCount: number;
  evaluated: number;
  stale: number;
  weekendBars: number;
  boxMaxWidthPct: number | null;
  anomalies: { stocks: number; days: number };
  windows: Record<string, BreakoutWindow>;
}
export interface Breakouts {
  generatedAt: string | null;
  params: BreakoutParams;
  markets: Partial<Record<BreakoutMarketKey, BreakoutMarket>>;
}

/** What the job writes today; used when `params` is missing or partly unreadable. */
export const DEFAULT_BREAKOUT_PARAMS: BreakoutParams = {
  windows: [
    { key: '1w', days: 5, label: '近1週' },
    { key: '1m', days: 21, label: '近1月' },
  ],
  matureDays: 3,
  minSample: 20,
  cooldownDays: 10,
  jumpLimitPct: 30,
  volumeLookback: 20,
  volumeMultiple: 1.5,
  boxLookback: 20,
  strategies: [
    ...[20, 60, 120].map((n) => ({
      key: `high${n}`,
      label: `突破${n}日高`,
      kind: 'high',
      lookback: n,
      volume: false,
      definition: '',
    })),
    {
      key: 'box20',
      label: '突破20日箱型',
      kind: 'box',
      lookback: 20,
      volume: false,
      definition: '',
    },
    {
      key: 'ema20',
      label: '突破20EMA',
      kind: 'ema',
      lookback: 20,
      volume: false,
      definition: '',
    },
    ...[20, 60, 120].map((n) => ({
      key: `high${n}Vol`,
      label: `突破${n}日高＋放量`,
      kind: 'high',
      lookback: n,
      volume: true,
      definition: '',
    })),
    {
      key: 'box20Vol',
      label: '突破20日箱型＋放量',
      kind: 'box',
      lookback: 20,
      volume: true,
      definition: '',
    },
  ],
};

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
const positiveInt = (value: unknown): number | null => {
  const number = finiteOrNull(value);
  return number !== null && number > 0 ? Math.floor(number) : null;
};

const statsSchema = z.object({
  eligible: countOf,
  n: countOf,
  maturedN: countOf,
  wins: z.preprocess(finiteOrNull, z.number().int().nonnegative().nullable().catch(null)),
  winRate: pctOf,
  avgReturn: pctOf,
  medianReturn: pctOf,
  failRate: pctOf,
  avgHoldDays: pctOf,
  baseRate: pctOf,
  baseAvgReturn: pctOf,
});

const windowSchema = z
  .object({
    days: countOf,
    start: optionalText,
    maturedThrough: optionalText,
    market: z
      .object({
        entryDays: countOf,
        pairs: countOf,
        upRate: pctOf,
        avgReturn: pctOf,
      })
      .optional()
      .transform((value) => value ?? { entryDays: 0, pairs: 0, upRate: null, avgReturn: null }),
    strategies: z.unknown().optional(),
  })
  .transform((window): BreakoutWindow => {
    const strategies: Record<string, BreakoutStats> = {};
    for (const [key, value] of Object.entries(record(window.strategies))) {
      const parsed = statsSchema.safeParse(value);
      if (parsed.success) strategies[key] = parsed.data;
    }
    return { ...window, strategies };
  });

const marketSchema = z
  .object({
    scope: z.unknown().optional(),
    coverage: z.unknown().optional(),
    listingAsOf: optionalText,
    listingSource: text,
    asOf: optionalText,
    universe: text,
    universeCount: countOf,
    evaluated: countOf,
    stale: countOf,
    weekendBars: countOf,
    boxMaxWidthPct: pctOf,
    anomalies: z.unknown().optional(),
    windows: z.unknown().optional(),
  })
  .transform((market): BreakoutMarket | null => {
    const windows: Record<string, BreakoutWindow> = {};
    for (const [key, value] of Object.entries(record(market.windows))) {
      const parsed = windowSchema.safeParse(value);
      if (parsed.success && Object.keys(parsed.data.strategies).length) windows[key] = parsed.data;
    }
    if (!Object.keys(windows).length) return null;
    const anomalies = record(market.anomalies);
    const coverage = record(market.coverage);
    const loaded = finiteOrNull(coverage.loaded);
    const missing = finiteOrNull(coverage.missing);
    const full = market.scope === 'full' && loaded !== null && missing !== null &&
      Number.isInteger(loaded) && Number.isInteger(missing) && loaded >= market.evaluated && missing >= 0 &&
      loaded + missing === market.universeCount && loaded - market.evaluated === market.stale;
    return {
      ...market,
      scope: full ? 'full' : 'legacy',
      coverage: full ? { loaded, missing } : null,
      anomalies: {
        stocks: countOf.parse(anomalies.stocks),
        days: countOf.parse(anomalies.days),
      },
      windows,
    };
  });

function parseParams(value: unknown): BreakoutParams {
  const raw = record(value);
  const defaults = DEFAULT_BREAKOUT_PARAMS;
  const windows = (Array.isArray(raw.windows) ? raw.windows : [])
    .map(record)
    .filter((entry) => typeof entry.key === 'string' && entry.key.trim())
    .map((entry) => ({
      key: String(entry.key).trim(),
      days: positiveInt(entry.days) ?? 0,
      label: typeof entry.label === 'string' && entry.label.trim() ? entry.label.trim() : String(entry.key).trim(),
    }));
  const strategies = (Array.isArray(raw.strategies) ? raw.strategies : [])
    .map(record)
    .filter((entry) => typeof entry.key === 'string' && entry.key.trim())
    .map((entry) => ({
      key: String(entry.key).trim(),
      label: typeof entry.label === 'string' && entry.label.trim() ? entry.label.trim() : String(entry.key).trim(),
      kind: typeof entry.kind === 'string' ? entry.kind : '',
      lookback: positiveInt(entry.lookback),
      volume: entry.volume === true,
      definition: typeof entry.definition === 'string' ? entry.definition.trim() : '',
    }));
  const optional = (key: string, fallback: number | null) => finiteOrNull(raw[key]) ?? fallback;
  return {
    windows: windows.length ? windows : defaults.windows,
    matureDays: positiveInt(raw.matureDays) ?? defaults.matureDays,
    minSample: positiveInt(raw.minSample) ?? defaults.minSample,
    cooldownDays: optional('cooldownDays', defaults.cooldownDays),
    jumpLimitPct: optional('jumpLimitPct', defaults.jumpLimitPct),
    volumeLookback: optional('volumeLookback', defaults.volumeLookback),
    volumeMultiple: optional('volumeMultiple', defaults.volumeMultiple),
    boxLookback: optional('boxLookback', defaults.boxLookback),
    strategies: strategies.length ? strategies : defaults.strategies,
  };
}

export const breakoutsSchema = z.object({
  generatedAt: optionalText,
  params: z.unknown().optional().transform(parseParams),
  markets: z
    .unknown()
    .optional()
    .transform((value) => {
      const markets = record(value);
      const out: Partial<Record<BreakoutMarketKey, BreakoutMarket>> = {};
      for (const key of BREAKOUT_MARKET_KEYS) {
        const parsed = marketSchema.safeParse(markets[key]);
        if (parsed.success && parsed.data) out[key] = parsed.data;
      }
      return out;
    }),
});

export type BreakoutsLoadResult = { status: 'ok'; data: Breakouts; url: string } | { status: 'missing' | 'invalid' | 'error'; message: string; url: string };

/** `breakouts.json` sits next to `latest.json` (or next to `VITE_REPORT_URL`). */
export function breakoutsUrl(
  base: string = import.meta.env?.BASE_URL ?? '/',
  override: string | undefined = import.meta.env?.VITE_REPORT_URL,
  origin: string = typeof location === 'undefined' ? 'http://localhost' : location.origin,
): string {
  return new URL('breakouts.json', reportUrl(base, override, origin)).href;
}

const INVALID = '突破勝率格式無法辨識。';
const MISSING = '突破勝率資料尚未產生。';

/** Parses an already-fetched body. Never throws. */
export function parseBreakouts(data: unknown, url = ''): BreakoutsLoadResult {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return { status: 'invalid', message: INVALID, url };
  const parsed = breakoutsSchema.safeParse(data);
  if (!parsed.success) return { status: 'invalid', message: INVALID, url };
  if (!Object.keys(parsed.data.markets).length) return { status: 'missing', message: MISSING, url };
  return { status: 'ok', data: parsed.data, url };
}

/** Fetches and validates the file. Network, HTTP and JSON failures resolve to a friendly state. */
export async function fetchBreakouts(url = breakoutsUrl(), fetcher: typeof fetch = fetch, signal?: AbortSignal): Promise<BreakoutsLoadResult> {
  let response: Response;
  try {
    response = await fetcher(url, { cache: 'no-cache', signal });
  } catch {
    return {
      status: 'error',
      message: '無法連線取得突破勝率，請稍後再試。',
      url,
    };
  }
  if (response.status === 404) return { status: 'missing', message: MISSING, url };
  if (!response.ok)
    return {
      status: 'error',
      message: `突破勝率暫時無法取得（HTTP ${response.status}）。`,
      url,
    };
  let data: unknown;
  try {
    // An SPA fallback can answer with index.html; treat non-JSON as an unreadable file.
    data = JSON.parse(await response.text());
  } catch {
    return { status: 'invalid', message: INVALID, url };
  }
  return parseBreakouts(data, url);
}

let shared: Promise<BreakoutsLoadResult> | null = null;

/** One shared load per page session; `refresh` forces a new request. */
export function loadBreakouts(refresh = false): Promise<BreakoutsLoadResult> {
  if (!shared || refresh) {
    const request = fetchBreakouts().then((result) => {
      // Do not pin a transient failure for the rest of the session; the next card mount retries.
      if (result.status === 'error' && shared === request) shared = null;
      return result;
    });
    shared = request;
  }
  return shared;
}

export type SampleLevel = 'none' | 'pending' | 'low' | 'ok' | 'ineligible';

/**
 * How much a strategy row can be trusted: `ineligible` (no stock had enough history), `none` (no
 * breakout in the window), `pending` (breakouts exist but none has matured), `low` (matured sample
 * below `minSample`) or `ok`.
 */
export function sampleLevel(stats: BreakoutStats | undefined, minSample: number): SampleLevel {
  if (!stats || stats.eligible === 0) return 'ineligible';
  if (stats.n === 0) return 'none';
  if (stats.maturedN === 0 || stats.winRate === null) return 'pending';
  return stats.maturedN < minSample ? 'low' : 'ok';
}

/** Win rate minus the matched base rate, in percentage points (one decimal); null when either is missing. */
export function edgeVsBase(stats: BreakoutStats | undefined): number | null {
  if (!stats || stats.winRate === null || stats.baseRate === null) return null;
  return Math.round((stats.winRate - stats.baseRate) * 10) / 10;
}

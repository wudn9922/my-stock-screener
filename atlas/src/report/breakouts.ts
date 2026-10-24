import { z } from 'zod';
import { reportUrl } from './loadReport';
import { countOf, finiteOrNull, optionalText, pctOf, text } from './breadth';

/**
 * Full-market breakout outcome comparisons (`report/breakouts.json`). Legacy flat return
 * fields remain readable, but the UI requires an explicitly versioned, validated comparison.
 *
 * Tolerant like breadth.ts: unknown fields are ignored, numbers may arrive as strings, a strategy or
 * window that does not parse is dropped, and a market without any usable window is left out instead
 * of failing the whole file. Missing `params` fall back to the definitions the job ships with.
 */

export const BREAKOUT_MARKET_KEYS = ['tw', 'us'] as const;
export type BreakoutMarketKey = (typeof BREAKOUT_MARKET_KEYS)[number];
export const SUCCESS_MODEL = 'four-rules-v1';
export const OUTCOME_KEYS = ['hold5', 'nextHigh', 'firstHit', 'targetAndNext'] as const;
export type OutcomeKey = (typeof OUTCOME_KEYS)[number];
export const OUTCOME_RULES: { key: OutcomeKey; label: string; short: string; description: string }[] = [
  { key: 'hold5', label: '守住關鍵點', short: '五日守住', description: '後續5個交易日內，最低價未碰到關鍵點下方5%，即算成功。' },
  { key: 'nextHigh', label: '隔日再創高', short: '隔日創高', description: '下一個交易日的最高價，高於突破日最高價，即算成功。' },
  { key: 'firstHit', label: '先觸發判定', short: '先觸發', description: '先跌到−5%判失敗；先漲到＋5%或隔日先創高判成功。五日守住−5%也成功，先成立後不改判。' },
  { key: 'targetAndNext', label: '漲幅與續強', short: '目標＋續強', description: '五日內碰到關鍵點上方5%，且隔日再創高，兩項都成立才成功。' },
];

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
  swingMethod: 'atr-reversal-v1' | 'atr-reversal-v2' | 'fractal-v1' | 'unknown';
  successModel: typeof SUCCESS_MODEL | null;
  successAtrPeriod: number;
  closeBufferAtr: number;
  observationDays: number;
  barrierPct: number;
  swingAtrPeriod: number | null;
  swingReversalAtr: number | null;
  /** Only describes legacy local-pivot reports; never infer ATR waves from old counts. */
  swingPivotLeft: number;
  swingPivotRight: number;
  strategies: BreakoutStrategyMeta[];
}
export interface BreakoutStats {
  /** Usable swing references in the window; box/EMA require whole-window warmup. */
  eligible: number;
  /** Swing retries failed closes until qualified; box/EMA take the first fresh event per window. */
  n: number;
  /** Legacy as-of return cohort; the four public rates use comparison.completeN instead. */
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
  comparison: BreakoutComparison | null;
}
export interface BreakoutOutcome {
  wins: number;
  failures: number;
  evaluatedN: number;
  winRate: number | null;
}
export interface BreakoutComparison {
  n: number;
  completeN: number;
  pendingN: number;
  missingN: number;
  commonFailedN: number;
  sameBarFailedN: number;
  rules: Partial<Record<OutcomeKey, BreakoutOutcome>>;
}
export interface BreakoutWindow {
  days: number;
  start: string | null;
  maturedThrough: string | null;
  observedThrough: string | null;
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

/** Safe fallbacks: missing method metadata must not relabel old results as ATR waves. */
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
  swingMethod: 'unknown',
  successModel: null,
  successAtrPeriod: 14,
  closeBufferAtr: 0.1,
  observationDays: 5,
  barrierPct: 5,
  swingAtrPeriod: null,
  swingReversalAtr: null,
  swingPivotLeft: 2,
  swingPivotRight: 2,
  strategies: [
    { key: 'swingHigh', label: '突破波段前高', kind: 'swing', lookback: null, volume: false, definition: '' },
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
  ],
};

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
const positiveInt = (value: unknown): number | null => {
  const number = finiteOrNull(value);
  return number !== null && number > 0 ? Math.floor(number) : null;
};

const outcomeSchema = z.object({ wins: countOf, failures: countOf, evaluatedN: countOf, winRate: pctOf });
function parseComparison(value: unknown, signals: number): BreakoutComparison | null {
  const raw = record(value);
  if (raw.model !== SUCCESS_MODEL) return null;
  const numbers = ['n', 'completeN', 'pendingN', 'missingN', 'commonFailedN', 'sameBarFailedN'] as const;
  const counts = Object.fromEntries(numbers.map((key) => [key, finiteOrNull(raw[key])])) as Record<(typeof numbers)[number], number | null>;
  if (numbers.some((key) => counts[key] === null || !Number.isInteger(counts[key]) || counts[key]! < 0)) return null;
  const { n, completeN, pendingN, missingN, commonFailedN, sameBarFailedN } = counts as Record<(typeof numbers)[number], number>;
  if (n !== signals || n !== completeN + pendingN + missingN || commonFailedN > completeN || sameBarFailedN > completeN - commonFailedN) return null;
  const rules: BreakoutComparison['rules'] = {};
  for (const key of OUTCOME_KEYS) {
    const parsed = outcomeSchema.safeParse(record(raw.rules)[key]);
    if (!parsed.success) continue;
    const entry = parsed.data;
    const rateValid = completeN ? entry.winRate !== null && Math.abs(entry.winRate - entry.wins / completeN * 100) <= 0.050001 : entry.winRate === null;
    if (entry.evaluatedN !== completeN || entry.wins + entry.failures !== completeN || entry.wins > completeN - commonFailedN || !rateValid) continue;
    rules[key] = entry;
  }
  return { n, completeN, pendingN, missingN, commonFailedN, sameBarFailedN, rules };
}

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
  comparison: z.unknown().optional(),
}).transform((stats): BreakoutStats => ({ ...stats, comparison: parseComparison(stats.comparison, stats.n) }));

const windowSchema = z
  .object({
    days: countOf,
    start: optionalText,
    maturedThrough: optionalText,
    observedThrough: optionalText,
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
  const atrPeriod = positiveInt(raw.swingAtrPeriod);
  const reversal = finiteOrNull(raw.swingReversalAtr);
  const atrWaves = (raw.swingMethod === 'atr-reversal-v1' || raw.swingMethod === 'atr-reversal-v2') && atrPeriod !== null && reversal !== null && reversal > 0;
  const legacyPivots = (raw.swingMethod === undefined || raw.swingMethod === 'fractal-v1') &&
    positiveInt(raw.swingPivotLeft) !== null && positiveInt(raw.swingPivotRight) !== null;
  return {
    windows: windows.length ? windows : defaults.windows,
    matureDays: positiveInt(raw.matureDays) ?? defaults.matureDays,
    minSample: positiveInt(raw.minSample) ?? defaults.minSample,
    cooldownDays: optional('cooldownDays', defaults.cooldownDays),
    jumpLimitPct: optional('jumpLimitPct', defaults.jumpLimitPct),
    volumeLookback: optional('volumeLookback', defaults.volumeLookback),
    volumeMultiple: optional('volumeMultiple', defaults.volumeMultiple),
    boxLookback: optional('boxLookback', defaults.boxLookback),
    swingMethod: atrWaves ? raw.swingMethod as 'atr-reversal-v1' | 'atr-reversal-v2' : legacyPivots ? 'fractal-v1' : 'unknown',
    successModel: raw.successModel === SUCCESS_MODEL && raw.sameBarPolicy === 'conservative-failure' &&
      Number(raw.successAtrPeriod) === 14 && Number(raw.closeBufferAtr) === 0.1 && Number(raw.observationDays) === 5 && Number(raw.barrierPct) === 5 ? SUCCESS_MODEL : null,
    successAtrPeriod: positiveInt(raw.successAtrPeriod) ?? defaults.successAtrPeriod,
    closeBufferAtr: finiteOrNull(raw.closeBufferAtr) ?? defaults.closeBufferAtr,
    observationDays: positiveInt(raw.observationDays) ?? defaults.observationDays,
    barrierPct: finiteOrNull(raw.barrierPct) ?? defaults.barrierPct,
    swingAtrPeriod: atrWaves ? atrPeriod : null,
    swingReversalAtr: atrWaves ? reversal : null,
    swingPivotLeft: positiveInt(raw.swingPivotLeft) ?? defaults.swingPivotLeft,
    swingPivotRight: positiveInt(raw.swingPivotRight) ?? defaults.swingPivotRight,
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

const archiveDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(value => {
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
});
const historyEntry = z.object({
  asOf: archiveDate,
  generatedAt: optionalText,
  markets: z.object({ tw: archiveDate.optional(), us: archiveDate.optional() }),
});
export interface BreakoutsHistory {
  intervalDays: number;
  snapshots: z.infer<typeof historyEntry>[];
}
export type BreakoutsHistoryResult = { status: 'ok'; data: BreakoutsHistory } | { status: 'missing' | 'invalid' | 'error'; message: string };

/** Saved snapshots are selected by their validated data date, never an arbitrary URL. */
export function breakoutArchiveUrl(asOf: string, latestUrl = breakoutsUrl()): string {
  return new URL(`breakouts-archive/${archiveDate.parse(asOf)}.json`, latestUrl).href;
}

export function parseBreakoutsHistory(value: unknown): BreakoutsHistoryResult {
  const parsed = z.object({ version: z.literal(1), intervalDays: z.number().int().positive(), snapshots: z.array(z.unknown()) }).safeParse(value);
  if (!parsed.success) return { status: 'invalid', message: '歷史結果清單格式無法辨識。' };
  const snapshots = new Map<string, z.infer<typeof historyEntry>>();
  for (const raw of parsed.data.snapshots) {
    const entry = historyEntry.safeParse(raw);
    if (entry.success && Object.keys(entry.data.markets).length) snapshots.set(entry.data.asOf, entry.data);
  }
  return { status: 'ok', data: { intervalDays: parsed.data.intervalDays, snapshots: [...snapshots.values()].sort((a, b) => b.asOf.localeCompare(a.asOf)) } };
}

export async function fetchBreakoutsHistory(latestUrl = breakoutsUrl(), fetcher: typeof fetch = fetch): Promise<BreakoutsHistoryResult> {
  try {
    const response = await fetcher(new URL('breakouts-history.json', latestUrl).href, { cache: 'no-cache' });
    if (response.status === 404) return { status: 'missing', message: '尚無已保存的歷史結果。' };
    if (!response.ok) return { status: 'error', message: '歷史結果清單暫時無法取得。' };
    try { return parseBreakoutsHistory(JSON.parse(await response.text())); }
    catch { return { status: 'invalid', message: '歷史結果清單格式無法辨識。' }; }
  } catch { return { status: 'error', message: '無法連線取得歷史結果清單。' }; }
}

let sharedHistory: Promise<BreakoutsHistoryResult> | null = null;
export function loadBreakoutsHistory(refresh = false): Promise<BreakoutsHistoryResult> {
  if (!sharedHistory || refresh) {
    const request = fetchBreakoutsHistory().then(result => {
      if (result.status === 'error' && sharedHistory === request) sharedHistory = null;
      return result;
    });
    sharedHistory = request;
  }
  return sharedHistory;
}

/** Guard against a mislabeled archive so one date cannot display another date's rates. */
export async function fetchBreakoutSnapshot(asOf: string, latestUrl = breakoutsUrl(), fetcher: typeof fetch = fetch, signal?: AbortSignal): Promise<BreakoutsLoadResult> {
  const url = breakoutArchiveUrl(asOf, latestUrl);
  const result = await fetchBreakouts(url, fetcher, signal);
  if (result.status !== 'ok') return result;
  const dates = Object.values(result.data.markets).map(market => market.asOf).filter((date): date is string => date !== null);
  if (!dates.length || [...dates].sort().at(-1) !== asOf) return { status: 'invalid', message: '歷史結果的資料日期不符，請改選其他日期。', url };
  return result;
}

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

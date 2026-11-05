// Pure request validation and response-header helpers for the market-chart Edge Function.
// No Deno or browser globals beyond URLSearchParams, so Atlas unit-tests this file under Vitest
// (atlas/tests/market-chart-function.test.ts) and the Deno entrypoint (index.ts) imports it.

/** US tickers incl. share classes (BRK-B, BF-B, BRK.B); one or two letter class suffix. */
export const US_TICKER_REGEX = /^[A-Z][A-Z0-9]{0,6}(?:[.-][A-Z0-9]{1,2})?$/;
/** Taiwan listed (.TW) and OTC (.TWO) codes, e.g. 2330.TW, 6488.TWO, 00632R.TW. */
export const TAIWAN_TICKER_REGEX = /^[0-9]{4,6}[A-Z]?\.(?:TW|TWO)$/;
/** Yahoo index symbols such as ^TWII, ^TWOII, ^GSPC, ^SOX, ^N225, ^STOXX50E. */
export const INDEX_TICKER_REGEX = /^\^[A-Z0-9][A-Z0-9.-]{0,14}$/;
/** Shanghai / Shenzhen index codes as Yahoo spells them (000001.SS, 399001.SZ). */
export const CHINA_INDEX_REGEX = /^[0-9]{6}\.(?:SS|SZ)$/;

/**
 * Interval → allowed ranges. Atlas uses exactly one range per interval (src/market-data/YahooIntervals.ts:
 * 5m/15m/30m → 1mo, 60m → 3mo, 1d → 5y, 1wk/1mo → 10y); a few shorter/longer ranges are allowed so the
 * client can change its window without redeploying. Anything else is rejected before contacting Yahoo.
 */
export const ALLOWED_INTERVAL_RANGES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  '5m': ['1d', '5d', '1mo'],
  '15m': ['1d', '5d', '1mo'],
  '30m': ['1d', '5d', '1mo'],
  '60m': ['5d', '1mo', '3mo'],
  '1d': ['1mo', '3mo', '6mo', '1y', '2y', '5y', '10y'],
  '1wk': ['1y', '2y', '5y', '10y', 'max'],
  '1mo': ['1y', '2y', '5y', '10y', 'max'],
});

const INTRADAY_INTERVALS = new Set(['5m', '15m', '30m', '60m']);

export interface ChartRequest {
  symbol: string;
  interval: string;
  range: string;
  /** Include dividends and splits (`events=div,splits`). Default true; `events=0` turns it off. */
  events: boolean;
}

export type ValidationResult =
  | { ok: true; value: ChartRequest }
  | { ok: false; status: 400; code: 'invalid_symbol' | 'invalid_interval' | 'invalid_range' | 'invalid_events'; message: string };

export function isValidChartSymbol(symbol: string): boolean {
  return (
    US_TICKER_REGEX.test(symbol) ||
    TAIWAN_TICKER_REGEX.test(symbol) ||
    INDEX_TICKER_REGEX.test(symbol) ||
    CHINA_INDEX_REGEX.test(symbol)
  );
}

/** Validates `?symbol=&interval=&range=&events=` strictly; the symbol is trimmed and upper-cased. */
export function validateChartRequest(params: URLSearchParams): ValidationResult {
  const symbol = (params.get('symbol') ?? '').trim().toUpperCase();
  if (!symbol || symbol.length > 16 || !isValidChartSymbol(symbol)) {
    return { ok: false, status: 400, code: 'invalid_symbol', message: 'Invalid or unsupported symbol' };
  }
  const interval = (params.get('interval') ?? '').trim();
  const ranges = Object.hasOwn(ALLOWED_INTERVAL_RANGES, interval) ? ALLOWED_INTERVAL_RANGES[interval] : undefined;
  if (!ranges) {
    return { ok: false, status: 400, code: 'invalid_interval', message: 'Unsupported interval' };
  }
  const range = (params.get('range') ?? '').trim();
  if (!ranges.includes(range)) {
    return { ok: false, status: 400, code: 'invalid_range', message: 'Unsupported range for this interval' };
  }
  const eventsParam = params.get('events');
  if (eventsParam !== null && !['', '0', '1', 'true', 'false'].includes(eventsParam)) {
    return { ok: false, status: 400, code: 'invalid_events', message: 'events must be 0 or 1' };
  }
  const events = !(eventsParam === '0' || eventsParam === 'false');
  return { ok: true, value: { symbol, interval, range, events } };
}

export const YAHOO_HOSTS = ['query2.finance.yahoo.com', 'query1.finance.yahoo.com'] as const;

export function buildYahooChartUrl(host: (typeof YAHOO_HOSTS)[number], request: ChartRequest): string {
  const query = [
    `interval=${request.interval}`,
    `range=${request.range}`,
    ...(request.events ? ['events=div%2Csplits'] : []),
    'includePrePost=false',
  ].join('&');
  return `https://${host}/v8/finance/chart/${encodeURIComponent(request.symbol)}?${query}`;
}

/** Browser cache lifetime: one minute for intraday bars, ten minutes for daily and longer. */
export function cacheControlFor(interval: string): string {
  return INTRADAY_INTERVALS.has(interval) ? 'public, max-age=60' : 'public, max-age=600';
}

export const DEFAULT_ALLOWED_ORIGINS: readonly string[] = Object.freeze(['https://wudn9922.github.io']);
const LOCAL_ORIGIN_REGEX = /^http:\/\/(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?$/;

/** Exact-match allow list plus any http://localhost / 127.0.0.1 port for development. */
export function isAllowedOrigin(origin: string | null, allowed: readonly string[] = DEFAULT_ALLOWED_ORIGINS): boolean {
  if (!origin) return false;
  return allowed.includes(origin) || LOCAL_ORIGIN_REGEX.test(origin);
}

/** Parses an optional comma-separated `ALLOWED_ORIGINS` override; blank keeps the default list. */
export function parseAllowedOrigins(value: string | undefined | null): readonly string[] {
  const list = (value ?? '')
    .split(',')
    .map((item) => item.trim().replace(/\/+$/, ''))
    .filter((item) => /^https?:\/\/[^\s/]+$/.test(item));
  return list.length ? list : DEFAULT_ALLOWED_ORIGINS;
}

export function corsHeaders(origin: string | null, allowed: readonly string[] = DEFAULT_ALLOWED_ORIGINS): Record<string, string> {
  const headers: Record<string, string> = {
    Vary: 'Origin',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
    'Access-Control-Max-Age': '86400',
  };
  if (origin && isAllowedOrigin(origin, allowed)) headers['Access-Control-Allow-Origin'] = origin;
  return headers;
}

/** Maps an upstream Yahoo HTTP status to the status/code this function reports (never Yahoo's body). */
export function classifyUpstreamStatus(status: number): { status: number; code: string; message: string; retryOtherHost: boolean } {
  if (status === 404) return { status: 404, code: 'not_found', message: 'No chart data for this symbol', retryOtherHost: false };
  if (status === 429) return { status: 503, code: 'rate_limited', message: 'Upstream rate limited, retry later', retryOtherHost: true };
  if (status === 400 || status === 422) return { status: 404, code: 'not_found', message: 'No chart data for this symbol', retryOtherHost: false };
  return { status: 502, code: 'upstream_error', message: 'Upstream market data unavailable', retryOtherHost: true };
}

/** Minimal structural check of Yahoo's chart payload before passing it through unchanged. */
export function isChartPayload(value: unknown): boolean {
  if (typeof value !== 'object' || value === null) return false;
  const chart = (value as { chart?: unknown }).chart;
  if (typeof chart !== 'object' || chart === null) return false;
  const result = (chart as { result?: unknown }).result;
  return Array.isArray(result) && result.length > 0;
}

/**
 * Batch live-quote mode: `?quotes=AAPL,2330.TW` returns the last month of daily rows for each symbol
 * (one Yahoo chart request per symbol, `interval=1d&range=1mo`, no events). Atlas polls it once a minute
 * for the stocks on screen and patches the forming bar; see atlas/src/market-data/LiveQuotes.ts.
 */
export const MAX_QUOTE_SYMBOLS = 40;
export const QUOTE_INTERVAL = '1d';
export const QUOTE_RANGE = '1mo';
/** Browser and per-instance cache lifetime for a quote batch (the site polls every 60 s). */
export const QUOTE_CACHE_SECONDS = 20;

export type QuotesValidation =
  | { ok: true; symbols: string[] }
  | { ok: false; status: 400; code: 'invalid_symbol' | 'too_many_symbols'; message: string };

export function validateQuotesRequest(value: string): QuotesValidation {
  const symbols = [...new Set(value.split(',').map((part) => part.trim().toUpperCase()).filter(Boolean))];
  if (!symbols.length || symbols.some((symbol) => symbol.length > 16 || !isValidChartSymbol(symbol))) {
    return { ok: false, status: 400, code: 'invalid_symbol', message: 'Invalid or unsupported symbol' };
  }
  if (symbols.length > MAX_QUOTE_SYMBOLS) {
    return { ok: false, status: 400, code: 'too_many_symbols', message: `At most ${MAX_QUOTE_SYMBOLS} symbols per request` };
  }
  return { ok: true, symbols };
}

export function quoteChartRequest(symbol: string): ChartRequest {
  return { symbol, interval: QUOTE_INTERVAL, range: QUOTE_RANGE, events: false };
}

/** Keeps only what the client normalizer reads (meta minus its range lists, timestamps, OHLCV). */
export function slimChartPayload(value: unknown): unknown {
  const result = (value as { chart: { result: Record<string, unknown>[] } }).chart.result[0]!;
  const { validRanges: _validRanges, tradingPeriods: _tradingPeriods, ...meta } = (result.meta ?? {}) as Record<string, unknown>;
  const quote = ((result.indicators as { quote?: unknown[] } | undefined)?.quote ?? []).slice(0, 1);
  return { chart: { result: [{ meta, timestamp: result.timestamp ?? [], indicators: { quote } }], error: null } };
}

/**
 * Yahoo sometimes leaves recent daily rows empty (all OHLCV null) although the session traded:
 * ^TWII on 2026-11-05 had null rows for 11/04 and 11/05 while meta.regularMarketPrice carried
 * the 11/04 close; later the same morning 11/05 had prices but 11/04 stayed empty. When the latest
 * quote is from a session the daily rows do not show yet, or one of the last five rows before the
 * last priced row is completely empty, those empty sessions are rebuilt from Yahoo's 5-minute bars.
 * Rows with any price are never changed (the client heals a missing latest close from meta).
 */
export const INTRADAY_FILL_REQUEST = { interval: '5m', range: '5d' } as const;

type Series = (number | null)[];
interface ChartResult {
  meta?: { regularMarketTime?: number; gmtoffset?: number } & Record<string, unknown>;
  timestamp?: number[];
  indicators?: { quote?: { open?: Series; high?: Series; low?: Series; close?: Series; volume?: Series }[]; adjclose?: { adjclose?: Series }[] };
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

function firstResult(payload: unknown): ChartResult | undefined {
  return (payload as { chart?: { result?: ChartResult[] } })?.chart?.result?.[0];
}

/** Calendar date in the exchange's local time (Yahoo's gmtoffset, seconds). */
export function exchangeDate(time: number, gmtoffset = 0): string {
  return new Date((time + gmtoffset) * 1000).toISOString().slice(0, 10);
}

/** Rows near the end that the 5-day intraday request can still rebuild. */
const FILLABLE_TAIL = 5;

export function needsIntradayFill(payload: unknown): boolean {
  const result = firstResult(payload);
  const times = result?.timestamp ?? [];
  const q = result?.indicators?.quote?.[0];
  const metaTime = result?.meta?.regularMarketTime;
  if (!finite(metaTime) || !times.length || !q) return false;
  const offset = finite(result?.meta?.gmtoffset) ? result!.meta!.gmtoffset! : 0;
  const metaDate = exchangeDate(metaTime, offset);
  const priced = (i: number) => [q.open?.[i], q.high?.[i], q.low?.[i], q.close?.[i]].some(finite);
  // A row with any price is left to the client (it heals a missing latest close from meta).
  let last = -1;
  for (let i = times.length - 1; i >= 0; i--) if (priced(i)) { last = i; break; }
  if (last < 0) return false;
  // The latest quote is from a session the daily rows do not show yet.
  if (metaDate > exchangeDate(times[last]!, offset)) return true;
  // Or a recent session the quote has already moved past is completely empty (a gap).
  for (let i = Math.max(0, times.length - FILLABLE_TAIL); i < times.length; i++)
    if (!priced(i) && exchangeDate(times[i]!, offset) <= metaDate && i < last) return true;
  return false;
}

/** Returns the patched daily payload and the exchange dates that were rebuilt from 5-minute bars. */
export function fillDailyFromIntraday(daily: unknown, intraday: unknown): { payload: unknown; filled: string[] } {
  const result = firstResult(daily), minute = firstResult(intraday);
  const quote = result?.indicators?.quote?.[0];
  const bars = minute?.indicators?.quote?.[0];
  if (!result?.timestamp || !quote || !minute?.timestamp || !bars) return { payload: daily, filled: [] };
  const offset = finite(result.meta?.gmtoffset) ? result.meta!.gmtoffset! : 0;
  const sessions = new Map<string, { time: number; open: number; high: number; low: number; close: number; volume: number | null }>();
  minute.timestamp.forEach((time, i) => {
    const o = bars.open?.[i], h = bars.high?.[i], l = bars.low?.[i], c = bars.close?.[i], v = bars.volume?.[i];
    if (!finite(time) || !finite(o) || !finite(h) || !finite(l) || !finite(c) || o <= 0 || h <= 0 || l <= 0 || c <= 0) return;
    const date = exchangeDate(time, offset);
    const session = sessions.get(date);
    if (!session) sessions.set(date, { time, open: o, high: h, low: l, close: c, volume: finite(v) ? v : null });
    else {
      session.high = Math.max(session.high, h);
      session.low = Math.min(session.low, l);
      session.close = c;
      if (finite(v)) session.volume = (session.volume ?? 0) + v;
    }
  });
  if (!sessions.size) return { payload: daily, filled: [] };
  const copy = structuredClone(daily) as { chart: { result: ChartResult[] } };
  const target = copy.chart.result[0]!;
  const times = target.timestamp!;
  const q = target.indicators!.quote![0]!;
  const fields = ['open', 'high', 'low', 'close', 'volume'] as const;
  for (const field of fields) q[field] ??= times.map(() => null);
  const adj = target.indicators?.adjclose?.[0]?.adjclose;
  const filled: string[] = [];
  const put = (i: number, s: { open: number; high: number; low: number; close: number; volume: number | null }) => {
    q.open![i] = s.open; q.high![i] = s.high; q.low![i] = s.low; q.close![i] = s.close; q.volume![i] = s.volume;
    if (adj && !finite(adj[i])) adj[i] = s.close;
  };
  times.forEach((time, i) => {
    // Only completely empty rows are rebuilt.
    if ([q.open![i], q.high![i], q.low![i], q.close![i]].some(finite)) return;
    const date = exchangeDate(time, offset), session = sessions.get(date);
    if (session) { put(i, session); filled.push(date); }
  });
  const lastDate = times.length ? exchangeDate(times.at(-1)!, offset) : '';
  for (const [date, session] of [...sessions].sort(([a], [b]) => a.localeCompare(b))) {
    if (date <= lastDate) continue;
    times.push(session.time);
    for (const field of fields) q[field]!.push(null);
    if (adj) adj.push(null);
    put(times.length - 1, session);
    filled.push(date);
  }
  if (!filled.length) return { payload: daily, filled };
  // A rebuilt session that Yahoo's latest quote belongs to closes at that quote (the official
  // close after the session, the live price during it).
  const metaTime = target.meta?.regularMarketTime, metaPrice = target.meta?.regularMarketPrice;
  if (finite(metaTime) && finite(metaPrice) && metaPrice > 0) {
    const metaDate = exchangeDate(metaTime, offset);
    const i = times.findIndex((time) => exchangeDate(time, offset) === metaDate);
    if (i >= 0 && filled.includes(metaDate) && metaTime >= times[i]!) {
      q.close![i] = metaPrice;
      q.high![i] = Math.max(q.high![i]!, metaPrice);
      q.low![i] = Math.min(q.low![i]!, metaPrice);
      if (adj) adj[i] = metaPrice;
    }
  }
  target.meta = { ...target.meta, atlasIntradayFill: filled };
  return { payload: copy, filled };
}

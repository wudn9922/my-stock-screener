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

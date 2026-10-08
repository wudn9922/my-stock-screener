import { normalizeSymbol, timeframes, type Timeframe } from '../market-data/MarketDataProvider';

/**
 * Startup request from the page URL, e.g. `?symbol=2330.TW&tf=1D` (my-stock-screener report links).
 * Parsing is pure; App applies the result once per page load through the symbol search code path.
 */
export interface LaunchParams {
  /** Upper-cased symbol input; canonical, or a bare Taiwan code that needs catalog resolution. */
  symbol?: string;
  /** True for a bare 4–6 digit Taiwan code (e.g. `2330`) that must be resolved via the catalog. */
  needsCatalog?: boolean;
  /** Present when the symbol parameter is not a valid ticker; the chart is left unchanged. */
  error?: string;
  timeframe?: Timeframe;
  /** Raw `tf` value that was ignored because it is not an Atlas timeframe. */
  ignoredTimeframe?: string;
}

const MAX_PARAM_LENGTH = 32;
const BARE_TAIWAN_CODE = /^[0-9]{4,6}[A-Z]?$/;
// Unambiguous case-insensitive aliases. `1m` is deliberately absent: it could mean one minute.
const timeframeAliases: Record<string, Timeframe> = { '1H': '1H', '4H': '4H', '1D': '1D', '1W': '1W' };

export function parseLaunchTimeframe(value: string | null | undefined): Timeframe | undefined {
  const raw = value?.trim();
  if (!raw || raw.length > 4) return undefined;
  if ((timeframes as readonly string[]).includes(raw)) return raw as Timeframe;
  return timeframeAliases[raw.toUpperCase()];
}

/** Returns null when the URL carries no `symbol` parameter (a `tf` alone is ignored). */
export function parseLaunchParams(search: string): LaunchParams | null {
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(search);
  } catch {
    return null;
  }
  const rawSymbol = params.get('symbol')?.trim();
  if (!rawSymbol) return null;
  const result: LaunchParams = {};
  const rawTimeframe = params.get('tf') ?? params.get('timeframe');
  const timeframe = parseLaunchTimeframe(rawTimeframe);
  if (timeframe) result.timeframe = timeframe;
  else if (rawTimeframe?.trim()) result.ignoredTimeframe = rawTimeframe.trim().slice(0, MAX_PARAM_LENGTH);
  const upper = rawSymbol.toUpperCase();
  if (rawSymbol.length > MAX_PARAM_LENGTH) {
    result.error = '網址中的股票代號無效。';
    return result;
  }
  if (BARE_TAIWAN_CODE.test(upper)) {
    result.symbol = upper;
    result.needsCatalog = true;
    return result;
  }
  try {
    result.symbol = normalizeSymbol(upper);
  } catch (error) {
    result.error = error instanceof Error ? error.message : String(error);
  }
  return result;
}

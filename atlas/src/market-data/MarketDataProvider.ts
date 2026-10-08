import {
  CANONICAL_SYMBOL_REGEX,
  getMarketProfile,
  isCanonicalSymbol,
  calendarPeriodStart,
  marketProfileSchema,
  sessionDate,
  isSessionCloseObservation,
  SESSION_CLOSE_SOURCE_QUALIFIER,
  type MarketProfile,
} from './MarketProfile';

export const timeframes = ['5m', '15m', '30m', '1H', '4H', '1D', '1W', '1M'] as const;
export type Timeframe = (typeof timeframes)[number];
export type { MarketProfile } from './MarketProfile';
export { CANONICAL_SYMBOL_REGEX, getMarketProfile, isCanonicalSymbol, calendarPeriodStart, marketProfileSchema, sessionDate, isSessionCloseObservation, SESSION_CLOSE_SOURCE_QUALIFIER };
/** Kept as a source-compatible alias for callers that used the former roadmap type. */
export type PlannedTimeframe = Timeframe;
export type DataState = 'simulated' | 'delayed' | 'live';
export type CacheStatus = 'fresh' | 'cached' | 'stale';
export interface MarketRange {
  from?: number;
  to?: number;
}
export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
export interface Quote {
  symbol: string;
  price: number;
  change: number;
  changePercent: number;
  /** Timestamp of the latest bar represented by this quote. */
  asOf: number;
  source?: string;
  dataState?: DataState;
  retrievedAt?: number;
  cacheStatus?: CacheStatus;
  market?: MarketProfile;
}
export interface BarNormalization {
  healedDailyClose?: {
    method: 'validated-regular-market-price';
    time: number;
    quoteAsOf: number;
  };
  currentPeriod?: {
    method: 'daily-ohlcv';
    timeframe: '1W' | '1M';
    periodStart: number;
    firstDailyTime: number;
    lastDailyTime: number;
    dailyCount: number;
    quoteAsOf: number;
    native?: Bar;
  };
}
export interface BarResult {
  bars: Bar[];
  source: string;
  session: 'regular' | 'extended';
  delayed: boolean;
  adjusted: boolean;
  priceBasis?: 'split-adjusted' | 'unadjusted' | 'unknown';
  quote?: Quote;
  normalization?: BarNormalization;
  /** Retrieval time in Unix seconds (for Demo, its fixed simulation as-of). */
  asOf?: number;
  /** Explicit timestamp of the last normalized bar. */
  latestBarAt?: number;
  dataState?: DataState;
  /** Set by CachedMarketDataProvider; stale results always carry `stale`. */
  cacheStatus?: CacheStatus;
  market?: MarketProfile;
  /** Actual Yahoo Taiwan 13:30 close-only rows; not regular interval OHLC bars. */
  sessionCloseObservations?: number[];
}
export interface MarketDataProvider {
  /** Normalization changes must not reuse an older incompatible cache. */
  readonly cacheVersion?: string;
  readonly id: string;
  readonly supportedTimeframes: readonly Timeframe[];
  readonly capabilities?: Partial<MarketDataCapabilities>;
  getBars(
    symbol: string,
    timeframe: Timeframe,
    range?: MarketRange,
    signal?: AbortSignal,
  ): Promise<BarResult>;
  getQuote(symbol: string, signal?: AbortSignal): Promise<Quote>;
  getCorporateEvents?(
    symbol: string,
    range?: MarketRange,
    signal?: AbortSignal,
  ): Promise<CorporateEventsResult>;
  /** Snapshot providers can advertise per-symbol interval coverage before loading chart data. */
  getSymbolTimeframes?(symbol: string, signal?: AbortSignal): Promise<readonly Timeframe[]>;
}
export interface MarketDataCapabilities {
  corporateEvents: 'available' | 'unavailable';
}
export interface CorporateEvent {
  symbol: string;
  type: 'dividend' | 'split';
  time: number;
  /** Dividend amount or split ratio (numerator / denominator), from provider data. */
  value: number;
  source: string;
  /** Present only when the source explicitly supplies a currency. */
  currency?: string;
  /** Raw split components retained for auditability. */
  numerator?: number;
  denominator?: number;
}
export type CorporateEventsResult =
  | {
      status: 'available';
      events: CorporateEvent[];
      source: string;
      asOf: number;
      cacheStatus?: CacheStatus;
    }
  | {
      status: 'unavailable';
      events: [];
      source: string;
      asOf?: number;
      cacheStatus?: CacheStatus;
    };
export const intervalSeconds: Record<Timeframe, number> = {
  '5m': 300,
  '15m': 900,
  '30m': 1800,
  '1H': 3600,
  '4H': 14400,
  '1D': 86400,
  '1W': 604800,
  // Volatility sizing only. Monthly traversal and bar closes use calendar boundaries.
  '1M': 30 * 86400,
};
export function normalizeSymbol(value: string): string {
  const symbol = value.trim().toUpperCase();
  if (!CANONICAL_SYMBOL_REGEX.test(symbol)) throw new Error('請輸入有效的股票或指數代號（例如 NVDA、BRK-B、2330.TW、6488.TWO、^TWII）');
  return symbol;
}

export function normalizeMarketRange(range?: MarketRange): MarketRange | undefined {
  if (range === undefined) return undefined;
  if (
    (range.from !== undefined && !Number.isFinite(range.from)) ||
    (range.to !== undefined && !Number.isFinite(range.to))
  ) {
    throw new Error('Market data range must contain finite timestamps');
  }
  if (range.from !== undefined && range.to !== undefined && range.from > range.to) {
    throw new Error('Market data range is reversed');
  }
  return {
    ...(range.from !== undefined ? { from: range.from } : {}),
    ...(range.to !== undefined ? { to: range.to } : {}),
  };
}

export function filterBarsByRange(bars: readonly Bar[], range?: MarketRange): Bar[] {
  const bounds = normalizeMarketRange(range);
  if (bounds === undefined) return [...bars];
  return bars.filter(
    (bar) =>
      (bounds.from === undefined || bar.time >= bounds.from) &&
      (bounds.to === undefined || bar.time <= bounds.to),
  );
}

export { closedBars } from './MarketTiming';

import {
  filterBarsByRange,
  getMarketProfile,
  normalizeMarketRange,
  normalizeSymbol,
  type BarResult,
  type CorporateEventsResult,
  type MarketDataProvider,
  type MarketRange,
  type Quote,
  type Timeframe,
} from './MarketDataProvider';
import { MarketDataUnavailableError } from './ProviderErrors';
import { isYahooTimeframe, yahooIntervals, type YahooTimeframe } from './YahooIntervals';
import {
  normalizeYahooCalendarResponse,
  normalizeYahooEvents,
  normalizeYahooResponse,
  YAHOO_SOURCE,
} from './YahooNormalizer';
import { isIndexSymbol, SESSION_CLOSE_SOURCE_QUALIFIER } from './MarketProfile';

/** User-facing provenance of every result served through the Supabase `market-chart` function. */
export const EDGE_YAHOO_SOURCE = 'Yahoo 延遲行情（Supabase 代理，非即時）';

export const EDGE_PROXY_NOT_CONFIGURED =
  '尚未設定行情代理網址（VITE_MARKET_PROXY_URL），無法載入 Yahoo 延遲行情。';

export function edgeUnavailableMessage(symbol: string): string {
  return `暫時無法取得 ${symbol} 行情，請稍後再試`;
}

/** `VITE_MARKET_PROXY_URL` at build time; empty when unset (the provider then reports a clear error). */
export function defaultMarketProxyUrl(): string {
  const value = import.meta.env?.VITE_MARKET_PROXY_URL;
  return typeof value === 'string' ? value.trim() : '';
}

export interface EdgeYahooProviderOptions {
  /** Defaults to the global `fetch`. */
  fetcher?: typeof fetch;
  /** Milliseconds clock (tests). */
  now?: () => number;
  /** Per request timeout; default 15 s. */
  timeoutMs?: number;
  /** In-memory raw response lifetime for 5m–1H; default 60 s (matches the function's max-age). */
  intradayTtlMs?: number;
  /** In-memory raw response lifetime for 1D/1W/1M; default 5 min. */
  dailyTtlMs?: number;
  /** Raw responses kept in memory (oldest evicted first); default 24. */
  maxEntries?: number;
}

interface RawEntry {
  raw: unknown;
  /** Unix seconds when the payload arrived; used as the normalizer's retrieval time. */
  retrievedAt: number;
  fetchedAtMs: number;
}

interface InFlight {
  promise: Promise<RawEntry>;
  controller: AbortController;
  waiters: number;
}

type CalendarResult = BarResult & {
  normalization?: { currentPeriod?: { timeframe: '1W' | '1M'; periodStart: number } };
};

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException('Aborted', 'AbortError');
}

function isAbort(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as { name?: unknown }).name === 'AbortError';
}

function relabel(source: string): string {
  return source.startsWith(YAHOO_SOURCE) ? `${EDGE_YAHOO_SOURCE}${source.slice(YAHOO_SOURCE.length)}` : source;
}

/**
 * World indices (^N225, ^FTSE, ^HSI…) are quoted in points; Yahoo still labels them with the home currency
 * (JPY, GBP…), which the USD/TWD-only normalizer would reject. For non-Taiwan `^` indices the currency label
 * is aligned with the symbol's profile before normalization. Stocks are never relabelled.
 */
export function alignIndexCurrency(raw: unknown, symbol: string): unknown {
  const market = getMarketProfile(symbol);
  if (!isIndexSymbol(symbol) || market.market !== 'US') return raw;
  const meta = (raw as { chart?: { result?: { meta?: { currency?: unknown } }[] | null } })?.chart?.result?.[0]?.meta;
  if (!meta || meta.currency === undefined || meta.currency === market.currency) return raw;
  const copy = structuredClone(raw) as { chart: { result: { meta: { currency?: unknown } }[] } };
  copy.chart.result[0]!.meta.currency = market.currency;
  return copy;
}

function periodSource(source: string, result: CalendarResult, bars: BarResult['bars']): string {
  const period = result.normalization?.currentPeriod;
  return period && bars.some((bar) => bar.time === period.periodStart)
    ? `${source} · ${period.timeframe === '1W' ? '本週' : '本月'} K由日 K彙總`
    : source;
}

/**
 * Live, delayed Yahoo chart data through the Supabase Edge Function `market-chart`
 * (supabase/functions/market-chart). The function returns Yahoo's raw chart JSON; this class
 * normalizes it with the same pure functions as the local backend and the snapshot collector,
 * including the verified current-week/month rebuild from daily OHLCV for 1W/1M.
 */
export class EdgeYahooProvider implements MarketDataProvider {
  readonly id = 'edge-yahoo';
  readonly cacheVersion = 'edge-yahoo-v1';
  readonly supportedTimeframes = ['5m', '15m', '30m', '1H', '1D', '1W', '1M'] as const;
  readonly capabilities = { corporateEvents: 'available' } as const;
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  private readonly timeoutMs: number;
  private readonly intradayTtlMs: number;
  private readonly dailyTtlMs: number;
  private readonly maxEntries: number;
  private readonly recent = new Map<string, RawEntry>();
  private readonly inFlight = new Map<string, InFlight>();
  readonly proxyUrl: string;

  constructor(proxyUrl: string = defaultMarketProxyUrl(), options: EdgeYahooProviderOptions = {}) {
    this.proxyUrl = proxyUrl.trim();
    this.fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
    this.now = options.now ?? Date.now;
    this.timeoutMs = options.timeoutMs ?? 15000;
    this.intradayTtlMs = options.intradayTtlMs ?? 60_000;
    this.dailyTtlMs = options.dailyTtlMs ?? 300_000;
    this.maxEntries = options.maxEntries ?? 24;
  }

  /** False when no proxy URL was configured at build time. */
  get configured(): boolean {
    return this.proxyUrl.length > 0;
  }

  /** The exact function URL requested for a symbol/timeframe (exported for diagnostics and tests). */
  requestUrl(symbol: string, timeframe: YahooTimeframe): string {
    const { interval, range } = yahooIntervals[timeframe];
    const separator = this.proxyUrl.includes('?') ? '&' : '?';
    return `${this.proxyUrl}${separator}symbol=${encodeURIComponent(symbol)}&interval=${interval}&range=${range}&events=${timeframe === '1D' ? 1 : 0}`;
  }

  async getBars(symbol: string, timeframe: Timeframe, range?: MarketRange, signal?: AbortSignal): Promise<BarResult> {
    signal?.throwIfAborted();
    const normalizedSymbol = normalizeSymbol(symbol);
    const normalizedRange = normalizeMarketRange(range);
    if (!isYahooTimeframe(timeframe)) throw new Error(`${timeframe} 週期無法透過 Yahoo 取得`);
    this.assertConfigured();
    const result: CalendarResult = timeframe === '1W' || timeframe === '1M'
      ? await this.calendarBars(normalizedSymbol, timeframe, signal)
      : await this.nativeBars(normalizedSymbol, timeframe, signal);
    const bars = filterBarsByRange(result.bars, normalizedRange);
    if (!bars.length) throw new MarketDataUnavailableError(`${normalizedSymbol} 在此區間沒有行情資料`);
    const sessionCloseObservations = result.sessionCloseObservations?.filter((time) => bars.some((bar) => bar.time === time));
    let source = relabel(result.source);
    if (result.sessionCloseObservations?.length && !sessionCloseObservations?.length) {
      source = source.replace(` · ${SESSION_CLOSE_SOURCE_QUALIFIER}`, '');
    }
    return {
      ...result,
      bars,
      ...(result.sessionCloseObservations !== undefined ? { sessionCloseObservations } : {}),
      ...(result.quote ? { quote: { ...result.quote, source: relabel(result.quote.source ?? YAHOO_SOURCE) } } : {}),
      market: result.market ?? getMarketProfile(normalizedSymbol),
      source: periodSource(source, result, bars),
      latestBarAt: bars.at(-1)!.time,
    };
  }

  async getQuote(symbol: string, signal?: AbortSignal): Promise<Quote> {
    const normalizedSymbol = normalizeSymbol(symbol);
    const result = await this.getBars(normalizedSymbol, '1D', undefined, signal);
    if (result.quote) return { ...result.quote, market: result.market, cacheStatus: result.cacheStatus };
    const latest = result.bars.at(-1),
      previous = result.bars.at(-2);
    if (!latest || !previous || previous.close <= 0) throw new MarketDataUnavailableError(edgeUnavailableMessage(normalizedSymbol));
    return {
      symbol: normalizedSymbol,
      price: latest.close,
      change: latest.close - previous.close,
      changePercent: (latest.close / previous.close - 1) * 100,
      asOf: latest.time,
      source: result.source,
      dataState: 'delayed',
      retrievedAt: result.asOf,
      cacheStatus: result.cacheStatus ?? 'fresh',
      market: result.market ?? getMarketProfile(normalizedSymbol),
    };
  }

  async getCorporateEvents(symbol: string, range?: MarketRange, signal?: AbortSignal): Promise<CorporateEventsResult> {
    signal?.throwIfAborted();
    const normalizedSymbol = normalizeSymbol(symbol);
    const normalizedRange = normalizeMarketRange(range);
    this.assertConfigured();
    const { entry } = await this.fetchRaw(normalizedSymbol, '1D', signal);
    const result = this.validated(normalizedSymbol, () => normalizeYahooEvents(entry.raw, normalizedSymbol, entry.retrievedAt));
    if (result.status !== 'available') return result;
    return {
      ...result,
      source: relabel(result.source),
      events: result.events
        .filter((event) =>
          (normalizedRange?.from === undefined || event.time >= normalizedRange.from) &&
          (normalizedRange?.to === undefined || event.time <= normalizedRange.to))
        .map((event) => ({ ...event, source: relabel(event.source) })),
    };
  }

  private assertConfigured(): void {
    if (!this.configured) throw new MarketDataUnavailableError(EDGE_PROXY_NOT_CONFIGURED);
  }

  private async nativeBars(symbol: string, timeframe: Exclude<YahooTimeframe, '1W' | '1M'>, signal?: AbortSignal): Promise<BarResult> {
    const { entry } = await this.fetchRaw(symbol, timeframe, signal);
    return this.validated(symbol, () => normalizeYahooResponse(entry.raw, entry.retrievedAt, timeframe, symbol));
  }

  /** Same rule as scripts/refresh-market.ts: native aggregates, current period rebuilt from verified daily rows. */
  private async calendarBars(symbol: string, timeframe: '1W' | '1M', signal?: AbortSignal): Promise<BarResult> {
    const [native, daily] = await Promise.all([
      this.fetchRaw(symbol, timeframe, signal),
      this.fetchRaw(symbol, '1D', signal),
    ]);
    const attempt = (dailyEntry: RawEntry) =>
      normalizeYahooCalendarResponse(
        native.entry.raw,
        dailyEntry.raw,
        Math.max(native.entry.retrievedAt, dailyEntry.retrievedAt),
        timeframe,
        symbol,
      );
    try {
      return attempt(daily.entry);
    } catch {
      // A cached daily payload can be older than a fresh native one; retry once with fresh daily data.
      if (daily.cached) {
        try {
          const fresh = await this.fetchRaw(symbol, '1D', signal, true);
          return attempt(fresh.entry);
        } catch (error) {
          if (isAbort(error) || error instanceof MarketDataUnavailableError) throw error;
        }
      }
    }
    // Native Yahoo aggregates remain valid history; only the current bar lacks daily verification.
    const fallback = this.validated(symbol, () => normalizeYahooResponse(native.entry.raw, native.entry.retrievedAt, timeframe, symbol));
    return { ...fallback, source: `${fallback.source} · ${timeframe === '1W' ? '本週' : '本月'} K 未經日 K 校驗` };
  }

  private validated<T>(symbol: string, normalize: () => T): T {
    try {
      return normalize();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new Error(`${symbol} 行情資料未通過驗證（${detail}）`, { cause: error });
    }
  }

  private ttl(timeframe: YahooTimeframe): number {
    return ['1D', '1W', '1M'].includes(timeframe) ? this.dailyTtlMs : this.intradayTtlMs;
  }

  /** Short in-memory cache + in-flight dedupe; the shared request is aborted only when every waiter left. */
  private async fetchRaw(
    symbol: string,
    timeframe: YahooTimeframe,
    signal?: AbortSignal,
    fresh = false,
  ): Promise<{ entry: RawEntry; cached: boolean }> {
    signal?.throwIfAborted();
    const key = `${symbol}|${timeframe}`;
    const cached = this.recent.get(key);
    if (!fresh && cached && this.now() - cached.fetchedAtMs < this.ttl(timeframe)) return { entry: cached, cached: true };
    let flight = this.inFlight.get(key);
    if (!flight || fresh) {
      const controller = new AbortController();
      const created: InFlight = {
        controller,
        waiters: 0,
        promise: this.download(symbol, timeframe, controller.signal).then((entry) => {
          this.recent.delete(key);
          this.recent.set(key, entry);
          while (this.recent.size > this.maxEntries) this.recent.delete(this.recent.keys().next().value!);
          return entry;
        }),
      };
      void created.promise
        .finally(() => {
          if (this.inFlight.get(key) === created) this.inFlight.delete(key);
        })
        .catch(() => undefined);
      this.inFlight.set(key, created);
      flight = created;
    }
    const entry = await this.wait(key, flight, signal);
    return { entry, cached: false };
  }

  private wait(key: string, flight: InFlight, signal?: AbortSignal): Promise<RawEntry> {
    flight.waiters++;
    return new Promise<RawEntry>((resolve, reject) => {
      let settled = false;
      const onAbort = () => {
        if (settled) return;
        settled = true;
        flight.waiters--;
        if (flight.waiters === 0) {
          if (this.inFlight.get(key) === flight) this.inFlight.delete(key);
          flight.controller.abort();
        }
        reject(abortError(signal!));
      };
      signal?.addEventListener('abort', onAbort, { once: true });
      flight.promise.then(
        (value) => {
          if (settled) return;
          settled = true;
          flight.waiters--;
          signal?.removeEventListener('abort', onAbort);
          resolve(value);
        },
        (error: unknown) => {
          if (settled) return;
          settled = true;
          flight.waiters--;
          signal?.removeEventListener('abort', onAbort);
          reject(error);
        },
      );
    });
  }

  private async download(symbol: string, timeframe: YahooTimeframe, signal: AbortSignal): Promise<RawEntry> {
    const controller = new AbortController();
    const forward = () => controller.abort(abortError(signal));
    signal.addEventListener('abort', forward, { once: true });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort(new DOMException('Timed out', 'TimeoutError'));
    }, this.timeoutMs);
    try {
      let response: Response;
      try {
        response = await this.fetcher(this.requestUrl(symbol, timeframe), {
          signal: controller.signal,
          headers: { Accept: 'application/json' },
        });
      } catch (error) {
        if (signal.aborted && !timedOut) throw abortError(signal);
        throw new MarketDataUnavailableError(edgeUnavailableMessage(symbol), { cause: error });
      }
      if (!response.ok) {
        if (response.status === 404) throw new Error(`找不到 ${symbol} 的行情資料，代號可能有誤或已下市`);
        if (response.status === 400) throw new Error(`${symbol} 不是行情代理接受的代號或週期`);
        if (response.status === 403) throw new Error('行情代理拒絕此網站來源，請檢查 Edge Function 的允許來源設定');
        throw new MarketDataUnavailableError(edgeUnavailableMessage(symbol));
      }
      let raw: unknown;
      try {
        raw = await response.json();
      } catch (error) {
        if (signal.aborted && !timedOut) throw abortError(signal);
        throw new MarketDataUnavailableError(edgeUnavailableMessage(symbol), { cause: error });
      }
      const fetchedAtMs = this.now();
      return { raw: alignIndexCurrency(raw, symbol), retrievedAt: fetchedAtMs / 1000, fetchedAtMs };
    } finally {
      clearTimeout(timer);
      signal.removeEventListener('abort', forward);
    }
  }
}

import { CachedMarketDataProvider, type CachedMarketDataOptions } from './CachedMarketDataProvider';
import { EdgeYahooProvider, defaultMarketProxyUrl } from './EdgeYahooProvider';
import {
  normalizeSymbol,
  type BarResult,
  type CorporateEventsResult,
  type MarketDataProvider,
  type MarketRange,
  type Quote,
  type Timeframe,
} from './MarketDataProvider';
import { StaticSeriesProvider, defaultReportBase } from './StaticSeriesProvider';

export interface ScreenerMarketProviderOptions {
  /** Supabase `market-chart` function URL; default `import.meta.env.VITE_MARKET_PROXY_URL`. */
  proxyUrl?: string;
  /** Report root holding `series/`; default `${BASE_URL}../report/` (→ /my-stock-screener/report/). */
  reportBase?: string;
  /**
   * Wrap the router in the existing IndexedDB `CachedMarketDataProvider` (60 s TTL, stale fallback when
   * the network is down). Default true; pass false in tests or options to tune it.
   */
  cache?: boolean | CachedMarketDataOptions;
  fetcher?: typeof fetch;
  now?: () => number;
}

/**
 * Routes symbols listed in the report's `series/index.json` (e.g. ^TWOII from TPEx) to
 * `StaticSeriesProvider`; every other symbol goes to the live `EdgeYahooProvider`.
 */
export class ScreenerMarketRouter implements MarketDataProvider {
  readonly id = 'screener';
  readonly cacheVersion = 'screener-v1';
  readonly supportedTimeframes = ['5m', '15m', '30m', '1H', '1D', '1W', '1M'] as const;
  readonly capabilities = { corporateEvents: 'available' } as const;

  constructor(
    readonly live: EdgeYahooProvider,
    readonly series: StaticSeriesProvider,
  ) {}

  /** The provider that serves this symbol. */
  async route(symbol: string, signal?: AbortSignal): Promise<MarketDataProvider> {
    const normalized = normalizeSymbol(symbol);
    return (await this.series.hasSymbol(normalized, signal)) ? this.series : this.live;
  }

  async getBars(symbol: string, timeframe: Timeframe, range?: MarketRange, signal?: AbortSignal): Promise<BarResult> {
    return (await this.route(symbol, signal)).getBars(symbol, timeframe, range, signal);
  }

  async getQuote(symbol: string, signal?: AbortSignal): Promise<Quote> {
    return (await this.route(symbol, signal)).getQuote(symbol, signal);
  }

  async getCorporateEvents(symbol: string, range?: MarketRange, signal?: AbortSignal): Promise<CorporateEventsResult> {
    const provider = await this.route(symbol, signal);
    return provider.getCorporateEvents
      ? provider.getCorporateEvents(symbol, range, signal)
      : { status: 'unavailable', events: [], source: `${provider.id} provider does not provide corporate events` };
  }

  /** Static official series offer 1D/1W/1M only, so the UI disables intraday buttons for them. */
  async getSymbolTimeframes(symbol: string, signal?: AbortSignal): Promise<readonly Timeframe[]> {
    const provider = await this.route(symbol, signal);
    return provider === this.series ? this.series.getSymbolTimeframes() : this.supportedTimeframes;
  }
}

/** Builds the screener's market provider: live Yahoo via Supabase + official static series, cached. */
export function createScreenerMarketProvider(options: ScreenerMarketProviderOptions = {}): MarketDataProvider {
  const shared = { ...(options.fetcher ? { fetcher: options.fetcher } : {}), ...(options.now ? { now: options.now } : {}) };
  const router = new ScreenerMarketRouter(
    new EdgeYahooProvider(options.proxyUrl ?? defaultMarketProxyUrl(), shared),
    new StaticSeriesProvider(options.reportBase ?? defaultReportBase(), shared),
  );
  if (options.cache === false) return router;
  return new CachedMarketDataProvider(router, options.cache === true || options.cache === undefined ? {} : options.cache);
}

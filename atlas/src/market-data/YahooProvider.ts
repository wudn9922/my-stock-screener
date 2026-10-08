import type {
  CorporateEventsResult,
  MarketRange,
  MarketDataProvider,
  Timeframe,
  BarResult,
  Quote,
} from './MarketDataProvider';
import { filterBarsByRange, normalizeMarketRange, normalizeSymbol } from './MarketDataProvider';
import { MarketDataUnavailableError } from './ProviderErrors';
import { isYahooTimeframe } from './YahooIntervals';
import { STATIC_HOSTING } from '../app/HostingMode';
import { getMarketProfile, sameMarketProfile, SESSION_CLOSE_SOURCE_QUALIFIER } from './MarketProfile';

const STATIC_BACKEND_REQUIRED =
  'Yahoo 資料需要 backend；此 GitHub Pages 靜態部署沒有 Yahoo backend，請連接 backend 或手動選擇 Demo。';
type CalendarBarResult = BarResult & { normalization?: { currentPeriod?: { timeframe: '1W' | '1M'; periodStart: number } } };

function periodSource(source: string, result: CalendarBarResult, bars: BarResult['bars']) {
  const period = result.normalization?.currentPeriod;
  return period && bars.some(bar => bar.time === period.periodStart)
    ? `${source} · ${period.timeframe === '1W' ? '本週' : '本月'} K由日 K彙總`
    : source;
}

export class YahooProvider implements MarketDataProvider {
  readonly id = 'yahoo';
  readonly cacheVersion = 'marketprofile-v4';
  readonly supportedTimeframes = ['5m', '15m', '30m', '1H', '1D', '1W', '1M'] as const;
  readonly capabilities = { corporateEvents: 'available' } as const;
  async getBars(
    symbol: string,
    timeframe: Timeframe,
    range?: MarketRange,
    signal?: AbortSignal,
  ): Promise<BarResult> {
    if (STATIC_HOSTING) throw new MarketDataUnavailableError(STATIC_BACKEND_REQUIRED);
    const normalizedSymbol = normalizeSymbol(symbol);
    const normalizedRange = normalizeMarketRange(range);
    if (!isYahooTimeframe(timeframe)) throw new Error('Yahoo does not provide this timeframe');
    const res = await fetch(
      `/api/yahoo?symbol=${encodeURIComponent(normalizedSymbol)}&timeframe=${encodeURIComponent(timeframe)}`,
      { signal },
    );
    if (!res.ok) {
      if (res.status === 429 || [502, 503, 504].includes(res.status)) {
        throw new MarketDataUnavailableError('Yahoo prototype unavailable. 請切回 Demo 資料。');
      }
      throw new Error(`Yahoo request rejected (${res.status})`);
    }
    const result = (await res.json()) as CalendarBarResult;
    if (!result || !Array.isArray(result.bars) || typeof result.source !== 'string') {
      throw new Error('Yahoo returned invalid bar data');
    }
    const market = getMarketProfile(normalizedSymbol);
    if (market.market === 'TW' && !sameMarketProfile(result.market, market)) {
      throw new Error('Yahoo returned missing or mismatched Taiwan market metadata');
    }
    if (result.market !== undefined && !sameMarketProfile(result.market, market)) {
      throw new Error('Yahoo returned market metadata for a different symbol');
    }
    if (result.quote?.market !== undefined && !sameMarketProfile(result.quote.market, market)) {
      throw new Error('Yahoo quote market metadata does not match the requested symbol');
    }
    if (market.market === 'TW' && result.quote && !sameMarketProfile(result.quote.market, market)) {
      throw new Error('Yahoo Taiwan quote is missing market metadata');
    }
    const bars = filterBarsByRange(result.bars, normalizedRange);
    if (!bars.length) throw new Error('No Yahoo bars in requested range');
    const sessionCloseObservations = result.sessionCloseObservations?.filter(time => bars.some(bar => bar.time === time));
    const source = result.sessionCloseObservations?.length && !sessionCloseObservations?.length
      ? result.source.replace(` · ${SESSION_CLOSE_SOURCE_QUALIFIER}`, '')
      : result.source;
    return {
      ...result,
      bars,
      ...(result.sessionCloseObservations !== undefined ? { sessionCloseObservations } : {}),
      market: result.market ?? market,
      source: periodSource(source, result, bars),
      latestBarAt: bars.at(-1)!.time,
    };
  }
  async getCorporateEvents(
    symbol: string,
    range?: MarketRange,
    signal?: AbortSignal,
  ): Promise<CorporateEventsResult> {
    if (STATIC_HOSTING) throw new MarketDataUnavailableError(STATIC_BACKEND_REQUIRED);
    const normalizedSymbol = normalizeSymbol(symbol);
    const normalizedRange = normalizeMarketRange(range);
    const res = await fetch(`/api/yahoo/events?symbol=${encodeURIComponent(normalizedSymbol)}`, { signal });
    if (!res.ok) {
      if (res.status === 429 || [502, 503, 504].includes(res.status)) {
        throw new MarketDataUnavailableError('Yahoo corporate events unavailable.');
      }
      throw new Error(`Yahoo corporate events request rejected (${res.status})`);
    }
    const result = (await res.json()) as CorporateEventsResult;
    if (result.status === 'unavailable') return result;
    if (
      result.status !== 'available' ||
      !Array.isArray(result.events) ||
      !Number.isFinite(result.asOf) ||
      result.events.some((event) => event.symbol !== normalizedSymbol)
    ) {
      throw new Error('Yahoo returned invalid corporate event data');
    }
    const events = result.events.filter(
      (event) =>
        (normalizedRange?.from === undefined || event.time >= normalizedRange.from) &&
        (normalizedRange?.to === undefined || event.time <= normalizedRange.to),
    );
    return { ...result, events };
  }
  async getQuote(symbol: string, signal?: AbortSignal): Promise<Quote> {
    const normalizedSymbol = normalizeSymbol(symbol);
    const result = await this.getBars(normalizedSymbol, '1D', undefined, signal);
    if (result.quote) return { ...result.quote, market: result.market, cacheStatus: result.cacheStatus };
    const { bars } = result;
    const a = bars.at(-1),
      b = bars.at(-2);
    if (!a || !b) throw new Error('Quote unavailable');
    if (b.close <= 0) throw new Error('Quote unavailable: previous close must be positive');
    const changePercent = (a.close / b.close - 1) * 100;
    if (!Number.isFinite(changePercent)) throw new Error('Quote unavailable: invalid close values');
    return {
      symbol: normalizedSymbol,
      price: a.close,
      change: a.close - b.close,
      changePercent,
      asOf: a.time,
      source: result.source,
      dataState: result.dataState ?? 'delayed',
      retrievedAt: result.asOf,
      cacheStatus: result.cacheStatus ?? 'fresh',
      market: result.market ?? getMarketProfile(normalizedSymbol),
    };
  }
}

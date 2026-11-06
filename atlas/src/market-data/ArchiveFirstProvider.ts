import { ARCHIVE_SOURCE, KlineArchive, klineArchive, mergeArchiveDaily } from './KlineArchive';
import { aggregateBars } from './StaticSeriesProvider';
import { calendarPeriodStart, filterBarsByRange, getMarketProfile, type BarResult, type CorporateEventsResult,
  type MarketDataProvider, type MarketRange, type Quote, type Timeframe } from './MarketDataProvider';
import { EdgeYahooProvider } from './EdgeYahooProvider';
import { liveQuotes, mergeDailyBars, mergePeriodBars, type LiveDaily } from './LiveQuotes';
import { usableBarResult } from './KlineIntegrity';
import { worldAssetFor } from './worldAssets';
import { marketDateTime } from './MarketProfile';
import { applyLiveQuoteMetadata, TWSE_MIS_SOURCE } from './YahooNormalizer';

// Shared archive lookups may outlive a component, but must not start another
// provider request once this document has left. A restored BFCache page gets
// a fresh lifetime; already pending work retains its aborted signal.
const pageLifetime = typeof window === 'undefined' ? undefined : { current: new AbortController() };
if (pageLifetime) {
  const lifetime = pageLifetime;
  window.addEventListener('pagehide', () => lifetime.current.abort());
  window.addEventListener('pageshow', () => {
    if (lifetime.current.signal.aborted) lifetime.current = new AbortController();
  });
}

export function archiveQuote(symbol: string, result: BarResult): Quote {
  const last = result.bars.at(-1)!, previous = result.bars.at(-2);
  const profile = getMarketProfile(symbol);
  // Daily rows identify the session. Quote time is that venue's closing time.
  const asOf = marketDateTime(new Date(last.time * 1000).toISOString().slice(0, 10), profile.sessionCloseMinutes, profile);
  return { symbol, price: last.close, change: previous ? last.close - previous.close : 0,
    changePercent: previous ? (last.close / previous.close - 1) * 100 : 0, asOf,
    market: profile, source: result.source, dataState: 'delayed', retrievedAt: result.asOf };
}

export function archiveUnavailableSource(lastDate: string): string {
  return `${ARCHIVE_SOURCE} · 即時報價暫時無法取得，顯示至 ${lastDate} 收盤`;
}

export function archivePeriod(daily: BarResult, symbol: string, timeframe: Timeframe, range?: MarketRange): BarResult {
  const bars = timeframe === '1W' || timeframe === '1M' ? aggregateBars(daily.bars, timeframe, getMarketProfile(symbol)) : daily.bars;
  const lastDate = daily.archive?.lastDate ?? new Date(daily.bars.at(-1)!.time * 1000).toISOString().slice(0, 10);
  const missing = [...new Set((daily.missingVolumeAt ?? []).map(time => timeframe === '1W' || timeframe === '1M'
    ? calendarPeriodStart(time, timeframe, getMarketProfile(symbol)) : time))];
  return usableBarResult({ ...daily, bars: filterBarsByRange(bars, range), missingVolumeAt: missing,
    quote: daily.quote ?? archiveQuote(symbol, daily), archive: daily.archive ?? { lastDate, status: 'archive' } }, symbol, timeframe);
}

/** All archived history is available before either live quotes or optional Yahoo supplements finish. */
export class ArchiveFirstProvider implements MarketDataProvider {
  readonly id: string;
  readonly cacheVersion = 'archive-first-v2-market-open';
  readonly supportedTimeframes: readonly Timeframe[];
  readonly capabilities;
  constructor(readonly fallback: MarketDataProvider, readonly archive = klineArchive(), readonly yahoo?: EdgeYahooProvider,
    private readonly lifetime: () => AbortSignal | undefined = () => pageLifetime?.current.signal) {
    this.id = fallback.id; this.supportedTimeframes = fallback.supportedTimeframes; this.capabilities = fallback.capabilities;
  }
  private checkActive(signal?: AbortSignal, lifetime = this.lifetime()) {
    signal?.throwIfAborted();
    lifetime?.throwIfAborted();
  }
  private async daily(symbol: string, range?: MarketRange, signal?: AbortSignal): Promise<BarResult | null> {
    const lifetime = this.lifetime();
    this.checkActive(signal, lifetime);
    if (worldAssetFor(symbol)) return null;
    try {
      const result = await this.archive.getDaily(symbol, range, signal);
      this.checkActive(signal, lifetime);
      return result;
    } catch (error) {
      this.checkActive(signal, lifetime);
      // Explicit document/request cancellation is not evidence of an absent
      // archive. In particular, beforeunload precedes WebKit's pagehide.
      if (error instanceof Error && error.name === 'AbortError') throw error;
      return null;
    }
  }
  async getBars(symbol: string, timeframe: Timeframe, range?: MarketRange, signal?: AbortSignal): Promise<BarResult> {
    if (timeframe === '1D' || timeframe === '1W' || timeframe === '1M') {
      const daily = await this.daily(symbol, undefined, signal);
      if (daily) return archivePeriod(daily, symbol, timeframe, range);
    }
    this.checkActive(signal);
    return this.fallback.getBars(symbol, timeframe, range, signal);
  }
  async getQuote(symbol: string, signal?: AbortSignal): Promise<Quote> {
    const daily = await this.daily(symbol, undefined, signal);
    this.checkActive(signal);
    return daily ? archiveQuote(symbol, daily) : this.fallback.getQuote(symbol, signal);
  }
  async refreshBars(symbol: string, timeframe: Timeframe, signal?: AbortSignal): Promise<BarResult> {
    // Intraday intervals have no archive: refresh them directly (one request, not a stale read first).
    if (timeframe !== '1D' && timeframe !== '1W' && timeframe !== '1M')
      return this.fallback.refreshBars?.(symbol, timeframe, signal) ?? this.fallback.getBars(symbol, timeframe, undefined, signal);
    // LiveQuoteFeed is the only current-day refresh source for an archived daily/period chart.
    const current = await this.getBars(symbol, timeframe, undefined, signal);
    if (!current.archive) return this.fallback.refreshBars?.(symbol, timeframe, signal) ?? current;
    const value = await liveQuotes().refresh(symbol);
    this.checkActive(signal);
    const merged = await mergeArchivedLive(current, value, timeframe, this.archive);
    return merged.archive?.status === 'split' ? this.getOlderBars(symbol, timeframe, merged, signal) : merged;
  }
  async getCorporateEvents(symbol: string, range?: MarketRange, signal?: AbortSignal): Promise<CorporateEventsResult> {
    const daily = await this.daily(symbol, undefined, signal);
    this.checkActive(signal);
    if (daily)
      return this.yahoo ? this.yahoo.getArchiveEvents(symbol, signal) : { status: 'unavailable', events: [], source: '公司事件來源暫時無法取得' };
    return this.fallback.getCorporateEvents?.(symbol, range, signal) ?? { status: 'unavailable', events: [], source: '此來源不提供公司事件' };
  }
  async getSymbolTimeframes(symbol: string, signal?: AbortSignal): Promise<readonly Timeframe[]> {
    this.checkActive(signal);
    return this.fallback.getSymbolTimeframes?.(symbol, signal) ?? this.supportedTimeframes;
  }
  async getOlderBars(symbol: string, timeframe: Timeframe, current: BarResult, signal?: AbortSignal): Promise<BarResult> {
    this.checkActive(signal);
    if (!current.archive || !this.yahoo) return current;
    if (current.archive.status === 'split') {
      try {
        const daily = await this.yahoo.getBars(symbol, '1D', undefined, signal);
        return archivePeriod({ ...daily, archive: current.archive,
          source: 'Yahoo 延遲行情 · 與封存價格差異超過 2%，整段採用 Yahoo 分割調整資料' }, symbol, timeframe);
      } catch (error) {
        signal?.throwIfAborted(); void error;
        return { ...current, source: `${current.source} · 較長 Yahoo 歷史暫時無法取得` };
      }
    }
    if (timeframe !== '1W' && timeframe !== '1M') return current;
    try {
      const older = await this.yahoo.getArchiveHistory(symbol, timeframe, signal);
      const profile = getMarketProfile(symbol);
      const latest = calendarPeriodStart(current.bars.at(-1)!.time, timeframe, profile);
      const archiveCloses = new Map(current.bars.map(bar => [calendarPeriodStart(bar.time, timeframe, profile), bar.close]));
      const mismatch = older.bars.some(bar => {
        const period = calendarPeriodStart(bar.time, timeframe, profile), close = archiveCloses.get(period);
        return period < latest && close && Math.abs(bar.close / close - 1) > 0.02;
      });
      if (mismatch) return this.getOlderBars(symbol, timeframe, { ...current, archive: { ...current.archive, status: 'split' } }, signal);
      const first = current.bars[0]!.time;
      const bars = [...older.bars.filter(bar => bar.time < first), ...current.bars];
      return usableBarResult({ ...current, bars, source: `${current.source} · 更早期間 Yahoo` }, symbol, timeframe);
    } catch (error) {
      signal?.throwIfAborted(); void error;
      return { ...current, source: `${current.source} · 更早期間暫時無法取得，保留封存範圍` };
    }
  }
}

export async function mergeArchivedLive(current: BarResult, value: LiveDaily, timeframe: Timeframe,
  archive: KlineArchive = klineArchive()): Promise<BarResult> {
  if (current.archive?.status === 'split') {
    const bars = timeframe === '1W' || timeframe === '1M'
      ? mergePeriodBars(current.bars, value.bars, timeframe, getMarketProfile(value.symbol)) ?? current.bars
      : mergeDailyBars(current.bars, value.bars);
    return applyLiveQuoteMetadata({ ...current, bars, asOf: value.retrievedAt, quote: value.quote,
      latestBarAt: bars.at(-1)!.time }, value.quote, value.source);
  }
  const daily = await archive.getDaily(value.symbol);
  if (!daily) return current;
  const merged = mergeArchiveDaily(daily.bars, value.bars, value.symbol);
  const base: BarResult = { ...daily, bars: merged.bars, asOf: value.retrievedAt, quote: value.quote,
    source: merged.splitMismatch ? 'Yahoo 延遲行情 · 與封存價格差異超過 2%，暫用 Yahoo 以免混用分割前後價格'
      : value.source?.includes(TWSE_MIS_SOURCE) ? `${ARCHIVE_SOURCE} · 缺少交易日由 Yahoo／MIS 補上`
      : `${ARCHIVE_SOURCE} · 今日與缺少交易日由 Yahoo 延遲報價補上`,
    archive: { lastDate: current.archive!.lastDate, status: merged.splitMismatch ? 'split' : 'live' } };
  const period = archivePeriod(base, value.symbol, timeframe);
  // Keep optional pre-archive weekly/monthly history unless its adjustment basis changed.
  if (!merged.splitMismatch && (timeframe === '1W' || timeframe === '1M'))
    period.bars = [...current.bars.filter(bar => bar.time < period.bars[0]!.time), ...period.bars];
  return applyLiveQuoteMetadata(period, value.quote, value.source);
}

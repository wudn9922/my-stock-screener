import { z } from 'zod';
import {
  normalizeSymbol,
  type BarNormalization,
  type Bar,
  type BarResult,
  type CorporateEvent,
  type CorporateEventsResult,
  type Timeframe,
  type Quote,
  getMarketProfile,
  calendarPeriodStart,
  isSessionCloseObservation,
  SESSION_CLOSE_SOURCE_QUALIFIER,
} from './MarketDataProvider';
import { calendarDate, type MarketProfile } from './MarketProfile';
import { isQuarantinedBar } from './KlineIntegrity';
const prices = z.array(z.number().finite().nullable());
const dividendSchema = z.object({
  amount: z.number().finite(),
  date: z.number().int().positive(),
  currency: z.string().optional(),
});
const splitSchema = z.object({
  date: z.number().int().positive(),
  numerator: z.number().finite().positive(),
  denominator: z.number().finite().positive(),
});
const responseSchema = z.object({
  chart: z.object({
    result: z
      .array(
        z.object({
          meta: z.object({
            symbol: z.string(),
            currency: z.string().optional(),
            exchangeName: z.string().optional(),
            exchangeTimezoneName: z.string().optional(),
            regularMarketPrice: z.number().finite().positive().optional(),
            regularMarketTime: z.number().int().positive().optional(),
            chartPreviousClose: z.number().finite().positive().optional().catch(undefined),
            atlasLiveSource: z.literal('twse-mis').optional().catch(undefined),
            atlasOhlcSource: z.literal('twse-mis').optional().catch(undefined),
            atlasHistoryUnavailable: z.boolean().optional().catch(undefined),
            // Optional extended metadata must never invalidate regular-session bars.
            preMarketPrice: z.number().finite().positive().optional().catch(undefined),
            preMarketTime: z.number().int().positive().optional().catch(undefined),
            postMarketPrice: z.number().finite().positive().optional().catch(undefined),
            postMarketTime: z.number().int().positive().optional().catch(undefined),
          }).optional(),
          timestamp: z.array(z.number().int().positive()),
          indicators: z.object({
            quote: z.array(
              z.object({
                open: prices,
                high: prices,
                low: prices,
                close: prices,
                volume: prices.optional(),
              }),
            ),
          }),
        }),
      )
      .nullable(),
  }),
});
type YahooChartResult = NonNullable<NonNullable<z.infer<typeof responseSchema>['chart']['result']>[number]>;
const eventsResponseSchema = z.object({
  chart: z.object({
    result: z
      .array(
        z.object({
          events: z
            .object({
              dividends: z.record(z.string(), dividendSchema).optional(),
              splits: z.record(z.string(), splitSchema).optional(),
            })
            .optional(),
          meta: z.object({
            symbol: z.string().optional(),
            currency: z.string().optional(),
            exchangeName: z.string().optional(),
            exchangeTimezoneName: z.string().optional(),
          }).optional(),
        }),
      )
      .nullable(),
  }),
});
export const YAHOO_SOURCE = 'Yahoo unofficial prototype · delayed / as-of data';
export const TWSE_MIS_SOURCE = '證交所即時（非官方 API）';
const TODAY_SOURCE = ' · 今日來源：';
const PARTIAL_SOURCE = ' · 今日開高低／量：';

/** Keep the historical provider visible when the shared feed replaces today's bar. */
export function liveDailySource(history: string, quote: Quote, recentSource?: string): string {
  const price = quote.source?.includes(TWSE_MIS_SOURCE), partial = recentSource?.includes(PARTIAL_SOURCE);
  if (quote.market?.market !== 'TW' || (!price && !partial && !history.includes(TODAY_SOURCE) && !history.includes(PARTIAL_SOURCE))) return history;
  const base = history.split(TODAY_SOURCE)[0]!.split(PARTIAL_SOURCE)[0];
  return partial && !price ? `${base}${PARTIAL_SOURCE}${recentSource!.split(PARTIAL_SOURCE)[1]}`
    : `${base}${TODAY_SOURCE}${quote.source ?? 'Yahoo 延遲行情'}`;
}

function nextCalendarPeriodStart(start: number, timeframe: '1W' | '1M', market: MarketProfile): number {
  if (market.market === 'TW') {
    return calendarPeriodStart(start + (timeframe === '1W' ? 8 : 32) * 86400, timeframe, market);
  }
  const date = new Date(start * 1000);
  return timeframe === '1M'
    ? Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1) / 1000
    : start + 7 * 86400;
}

function maximumTimestamp(times: readonly number[]): number {
  return times.reduce((max, time) => Math.max(max, time), 0);
}

/** Unofficial response structure stays entirely inside the provider boundary. */
export function normalizeYahooResponse(raw: unknown, asOf = Date.now() / 1000, timeframe?: Timeframe, expectedSymbol?: string): BarResult {
  if (!Number.isFinite(asOf) || asOf <= 0) throw new Error('Invalid Yahoo retrieval time');
  const result = responseSchema.parse(raw).chart.result?.[0],
    q = result?.indicators.quote[0];
  if (!result || !q) throw new Error('No data');
  const canonicalSymbol = expectedSymbol ? normalizeSymbol(expectedSymbol) : result.meta?.symbol ? normalizeSymbol(result.meta.symbol) : undefined;
  if (expectedSymbol && result.meta?.symbol !== canonicalSymbol) throw new Error('Yahoo response symbol mismatch');
  const market = getMarketProfile(canonicalSymbol);
  if (market.market === 'TW') {
    if (result.meta?.currency !== market.currency) throw new Error('Yahoo Taiwan response requires TWD currency metadata');
    if (result.meta?.exchangeTimezoneName !== market.timezone) throw new Error('Yahoo Taiwan response requires Asia/Taipei timezone metadata');
  } else if (result.meta?.currency && result.meta.currency !== market.currency) {
    throw new Error('Only USD market data is supported');
  }
  const byTime = new Map<number, Bar>();
  const missingVolumeAt = new Set<number>();
  const originalTimes = new Map<number, number>();
  const sessionCloseEligibility = new Map<number, boolean>();
  const canMarkSessionClose = market.market === 'TW' &&
    ['5m', '15m', '30m', '1H', '4H'].includes(timeframe ?? '');
  const latestDailyTime = timeframe === '1D' ? maximumTimestamp(result.timestamp) : 0;
  let latestDailyIndex = -1;
  if (timeframe === '1D') {
    for (let i = 0; i < result.timestamp.length; i++) {
      if (result.timestamp[i] === latestDailyTime) latestDailyIndex = i;
    }
  }
  let healedDailyClose: BarNormalization['healedDailyClose'];
  let liveBarBounds: BarNormalization['liveBarBounds'];
  for (let i = 0; i < result.timestamp.length; i++) {
    if (canonicalSymbol && isQuarantinedBar(canonicalSymbol, result.timestamp[i]!, timeframe)) continue;
    const open = q.open[i],
      high = q.high[i],
      low = q.low[i],
      sourceClose = q.close[i],
      rawVolume = q.volume?.[i];
    let close = sourceClose;
    if (
      timeframe === '1D' &&
      i === latestDailyIndex &&
      sourceClose === null &&
      open != null &&
      high != null &&
      low != null &&
      typeof rawVolume === 'number' &&
      Number.isFinite(rawVolume) &&
      rawVolume >= 0
    ) {
      const metaTime = result.meta?.regularMarketTime,
        metaPrice = result.meta?.regularMarketPrice,
        rowTime = result.timestamp[i];
      if (
        metaTime !== undefined &&
        metaPrice !== undefined &&
        Number.isFinite(metaPrice) &&
        metaPrice > 0 &&
        metaTime >= rowTime &&
        metaTime <= asOf + 300 &&
        calendarDate(metaTime, market) === calendarDate(rowTime, market) &&
        Number.isFinite(open) &&
        Number.isFinite(high) &&
        Number.isFinite(low) &&
        open >= 0 &&
        high >= Math.max(open, metaPrice) &&
        low <= Math.min(open, metaPrice)
      ) {
        close = metaPrice;
        healedDailyClose = {
          method: 'validated-regular-market-price',
          time: rowTime,
          quoteAsOf: metaTime,
        };
      }
    }
    if (open == null || high == null || low == null || close == null) continue;
    // Keep the normalized Bar shape stable: Yahoo omits volume in some intervals,
    // and this prototype retains its established zero fallback for that field.
    const volume = q.volume?.[i] ?? 0;
    let rowHigh = high,
      rowLow = low;
    // Right after the open Yahoo's still-forming daily row already carries the live price while its
    // high/low lag behind (e.g. close 100.7 above high 100.5). Only that row of the live session is
    // widened to its own open/close; any other inconsistent row still fails the response.
    if (
      timeframe === '1D' &&
      i === latestDailyIndex &&
      open > 0 &&
      close > 0 &&
      (rowHigh < Math.max(open, close) || rowLow > Math.min(open, close))
    ) {
      const metaTime = result.meta?.regularMarketTime,
        rowTime = result.timestamp[i]!;
      if (
        metaTime !== undefined &&
        metaTime >= rowTime &&
        metaTime <= asOf + 300 &&
        calendarDate(metaTime, market) === calendarDate(rowTime, market)
      ) {
        rowHigh = Math.max(rowHigh, open, close);
        rowLow = Math.min(rowLow, open, close);
        liveBarBounds = { method: 'live-session-open-close', time: rowTime, quoteAsOf: metaTime };
      }
    }
    if (
      open <= 0 ||
      high <= 0 ||
      low <= 0 ||
      close <= 0 ||
      volume < 0 ||
      rowHigh < Math.max(open, close) ||
      rowLow > Math.min(open, close)
    ) {
      throw new Error(`Invalid Yahoo OHLCV values at ${result.timestamp[i]}`);
    }
    const originalTime = result.timestamp[i];
    const time = timeframe === '1M' || timeframe === '1W'
      ? calendarPeriodStart(originalTime, timeframe, market)
      : originalTime;
    // Native weekly/monthly responses append a latest-session row after the
    // aggregate. Keep the earliest source timestamp for a period: summing or
    // replacing it would double count or lose the aggregate's OHLC/volume.
    if ((timeframe === '1M' || timeframe === '1W') && (originalTimes.get(time) ?? Infinity) < originalTime) continue;
    originalTimes.set(time, originalTime);
    if (q.volume?.[i] == null) missingVolumeAt.add(time); else missingVolumeAt.delete(time);
    byTime.set(time, {
      time,
      open,
      high: rowHigh,
      low: rowLow,
      close,
      volume,
    });
    sessionCloseEligibility.set(
      time,
      canMarkSessionClose && rawVolume === 0 && isSessionCloseObservation({ time, open, high, low, close, volume }, market),
    );
  }
  const bars = [...byTime.values()].sort((a, b) => a.time - b.time);
  if (!bars.length) throw new Error('No valid bars');
  const sessionCloseObservations = bars
    .filter(bar => sessionCloseEligibility.get(bar.time))
    .map(bar => bar.time);
  // Yahoo's quote OHLC is already split-adjusted. adjclose also includes dividend
  // adjustment and is intentionally not used. Never divide by events.splits again.
  const verifiedBasis = !!result.meta;
  const meta = result.meta;
  const latest = bars.at(-1)!;
  const metaTime = meta?.regularMarketTime;
  const metaPrice = meta?.regularMarketPrice;
  const useMeta = !!metaTime && !!metaPrice && metaTime >= latest.time && metaTime <= asOf + 300;
  const quoteTime = useMeta ? metaTime : latest.time;
  const quotePrice = useMeta ? metaPrice : latest.close;
  const sameDay = calendarDate(quoteTime, market) === calendarDate(latest.time, market);
  const mis = market.market === 'TW' && timeframe === '1D' && meta?.atlasLiveSource === 'twse-mis'
    && useMeta && sameDay && calendarDate(quoteTime, market) === calendarDate(asOf, market);
  const dataState = mis ? 'live' as const : 'delayed' as const;
  const previous = mis && meta?.chartPreviousClose ? meta.chartPreviousClose : sameDay ? bars.at(-2)?.close : latest.close;
  const quote = meta && timeframe === '1D' && previous && previous > 0 ? {
    symbol: canonicalSymbol ?? normalizeSymbol(meta.symbol), price: quotePrice, change: quotePrice - previous,
    changePercent: (quotePrice / previous - 1) * 100, asOf: quoteTime,
    source: mis ? TWSE_MIS_SOURCE : YAHOO_SOURCE, dataState, retrievedAt: asOf, cacheStatus: 'fresh' as const,
    market,
    ...(market.market === 'US' ? { extended: Object.fromEntries((['pre', 'post'] as const).flatMap(mode => {
      const price = mode === 'pre' ? meta.preMarketPrice : meta.postMarketPrice;
      const time = mode === 'pre' ? meta.preMarketTime : meta.postMarketTime;
      return price && time && time <= asOf + 300 ? [[mode, { price, asOf: time }]] : [];
    })) } : {}),
  } : undefined;
  sessionCloseObservations.sort((a, b) => a - b);
  const hasSessionCloseObservations = sessionCloseObservations.length > 0;
  const historySource = meta?.atlasHistoryUnavailable && market.market === 'TW' ? '歷史行情暫時無法取得' : YAHOO_SOURCE;
  const source = mis ? `${historySource}${TODAY_SOURCE}${TWSE_MIS_SOURCE}`
    : meta?.atlasOhlcSource === 'twse-mis' && market.market === 'TW' ? `${historySource}${PARTIAL_SOURCE}${TWSE_MIS_SOURCE}；成交價沿用 Yahoo` : historySource;
  return {
    bars,
    ...(missingVolumeAt.size ? { missingVolumeAt: [...missingVolumeAt] } : {}),
    source: hasSessionCloseObservations ? `${source} · ${SESSION_CLOSE_SOURCE_QUALIFIER}` : source,
    session: 'regular',
    delayed: !mis,
    adjusted: verifiedBasis,
    priceBasis: verifiedBasis ? 'split-adjusted' : 'unknown',
    ...(quote ? { quote } : {}),
    ...(healedDailyClose || liveBarBounds
      ? { normalization: { ...(healedDailyClose ? { healedDailyClose } : {}), ...(liveBarBounds ? { liveBarBounds } : {}) } }
      : {}),
    asOf,
    latestBarAt: bars.at(-1)!.time,
    dataState,
    cacheStatus: 'fresh',
    market,
    ...(hasSessionCloseObservations ? { sessionCloseObservations } : {}),
  };
}

interface VerifiedCurrentMeta {
  time: number;
  price: number;
  latestRowTime: number;
}

function verifyCurrentMeta(
  result: YahooChartResult,
  expectedSymbol: string,
  asOf: number,
  label: 'native' | 'daily',
  market: MarketProfile,
): VerifiedCurrentMeta {
  const meta = result.meta;
  if (!meta || meta.symbol !== expectedSymbol) throw new Error(`Yahoo ${label} response symbol mismatch`);
  if (meta.currency !== market.currency) throw new Error(`Yahoo current-period data requires ${market.currency} currency metadata`);
  if (market.market === 'TW' && meta.exchangeTimezoneName !== market.timezone) throw new Error('Yahoo current-period data requires Asia/Taipei timezone metadata');
  const time = meta.regularMarketTime,
    price = meta.regularMarketPrice,
    latestRowTime = maximumTimestamp(result.timestamp);
  if (!result.timestamp.length || time === undefined || price === undefined) {
    throw new Error(`Yahoo ${label} response is missing verified market metadata`);
  }
  if (
    !Number.isFinite(time) ||
    !Number.isFinite(price) ||
    price <= 0 ||
    time < latestRowTime ||
    time > asOf + 300 ||
    (label === 'daily' && calendarDate(time, market) !== calendarDate(latestRowTime, market))
  ) {
    throw new Error(`Yahoo ${label} market metadata is stale or inconsistent`);
  }
  return { time, price, latestRowTime };
}

/**
 * Rebuild only the current calendar week/month from validated daily OHLCV.
 * Older native Yahoo aggregates retain their original prices and volumes.
 */
export function normalizeYahooCalendarResponse(
  rawNative: unknown,
  rawDaily: unknown,
  asOf: number,
  timeframe: '1W' | '1M',
  expectedSymbol: string,
): BarResult {
  if (!Number.isFinite(asOf) || asOf <= 0) throw new Error('Invalid Yahoo retrieval time');
  const symbol = normalizeSymbol(expectedSymbol);
  const nativeResult = responseSchema.parse(rawNative).chart.result?.[0];
  const dailyResult = responseSchema.parse(rawDaily).chart.result?.[0];
  if (!nativeResult || !dailyResult) throw new Error('No data for Yahoo current-period normalization');

  const native = normalizeYahooResponse(rawNative, asOf, timeframe, symbol);
  const daily = normalizeYahooResponse(rawDaily, asOf, '1D', symbol);
  const market = native.market ?? getMarketProfile(symbol);
  if (native.priceBasis !== 'split-adjusted' || daily.priceBasis !== 'split-adjusted') {
    throw new Error('Yahoo current-period data requires verified split-adjusted prices');
  }
  const nativeMeta = verifyCurrentMeta(nativeResult, symbol, asOf, 'native', market);
  const dailyMeta = verifyCurrentMeta(dailyResult, symbol, asOf, 'daily', market);
  if (dailyMeta.time < nativeMeta.time) {
    throw new Error('Yahoo daily metadata is older than the native period response');
  }
  if (dailyMeta.time === nativeMeta.time && Math.abs(dailyMeta.price - nativeMeta.price) > 0.01) {
    throw new Error('Yahoo daily and native quotes disagree at the same market time');
  }
  if (calendarDate(dailyMeta.latestRowTime, market) !== calendarDate(dailyMeta.time, market)) {
    throw new Error('Yahoo latest daily row does not match the quote session date');
  }
  const latestDailyBar = daily.bars.at(-1);
  if (
    !latestDailyBar ||
    latestDailyBar.time !== dailyMeta.latestRowTime ||
    Math.abs(latestDailyBar.close - dailyMeta.price) > 0.01
  ) {
    throw new Error('Yahoo latest daily close does not match the verified quote');
  }

  const periodStart = calendarPeriodStart(dailyMeta.time, timeframe, market);
  const periodEnd = nextCalendarPeriodStart(periodStart, timeframe, market);
  const dailyQuote = dailyResult.indicators.quote[0];
  const currentDailyRows = dailyResult.timestamp
    .map((time, index) => ({ time, index }))
    .filter(({ time }) => time >= periodStart && time < periodEnd)
    .sort((a, b) => a.time - b.time);
  if (!currentDailyRows.length || currentDailyRows.at(-1)!.time !== dailyMeta.latestRowTime) {
    throw new Error('Yahoo daily response does not cover the full current period through its latest row');
  }

  const dailyByTime = new Map(daily.bars.map((bar) => [bar.time, bar]));
  const seenSessionDates = new Set<string>();
  const currentBars: Bar[] = [];
  for (const { time, index } of currentDailyRows) {
    const marketDate = calendarDate(time, market);
    if (seenSessionDates.has(marketDate)) {
      throw new Error(market.market === 'TW'
        ? 'Yahoo daily response has duplicate local session dates'
        : 'Yahoo daily response has duplicate UTC session dates');
    }
    seenSessionDates.add(marketDate);

    const open = dailyQuote.open[index],
      sourceClose = dailyQuote.close[index],
      volume = dailyQuote.volume?.[index];
    const normalized = dailyByTime.get(time);
    // The live row's high/low may have been widened to its open/close by the daily normalizer.
    const widened = daily.normalization?.liveBarBounds;
    const live = !!normalized && time === dailyMeta.latestRowTime && widened?.time === time && widened.quoteAsOf === dailyMeta.time;
    const high = live ? normalized!.high : dailyQuote.high[index],
      low = live ? normalized!.low : dailyQuote.low[index];
    let close = sourceClose;
    if (sourceClose === null) {
      const healing = daily.normalization?.healedDailyClose;
      if (
        time !== dailyMeta.latestRowTime ||
        !healing ||
        healing.time !== time ||
        healing.quoteAsOf !== dailyMeta.time ||
        !normalized
      ) {
        throw new Error('Yahoo daily row has an unhealed null close in the current period');
      }
      close = normalized.close;
    }
    if (
      typeof open !== 'number' ||
      typeof high !== 'number' ||
      typeof low !== 'number' ||
      typeof close !== 'number' ||
      typeof volume !== 'number' ||
      ![open, high, low, close, volume].every(Number.isFinite) ||
      open < 0 ||
      high < Math.max(open, close) ||
      low > Math.min(open, close) ||
      volume < 0
    ) {
      throw new Error(`Yahoo daily row has invalid current-period OHLCV at ${time}`);
    }
    if (
      !normalized ||
      normalized.open !== open ||
      normalized.high !== high ||
      normalized.low !== low ||
      normalized.close !== close ||
      normalized.volume !== volume
    ) {
      throw new Error(`Yahoo daily current-period row was not retained at ${time}`);
    }
    currentBars.push({ time, open, high, low, close, volume });
  }

  const firstDaily = currentBars[0];
  const lastDaily = currentBars.at(-1)!;
  const nativeQuote = nativeResult.indicators.quote[0];
  const nativeBucketSource = nativeResult.timestamp
    .map((time, index) => ({ time, index }))
    .filter(({ time }) => calendarPeriodStart(time, timeframe, market) === periodStart)
    .sort((a, b) => a.time - b.time)[0];
  const nativeCandidate = native.bars.find((bar) => bar.time === periodStart);
  const nativeOpen = nativeBucketSource ? nativeQuote.open[nativeBucketSource.index] : undefined;
  const nativeHigh = nativeBucketSource ? nativeQuote.high[nativeBucketSource.index] : undefined;
  const nativeLow = nativeBucketSource ? nativeQuote.low[nativeBucketSource.index] : undefined;
  const nativeClose = nativeBucketSource ? nativeQuote.close[nativeBucketSource.index] : undefined;
  const hasNativeOpeningAggregate =
    nativeBucketSource !== undefined &&
    calendarDate(nativeBucketSource.time, market) === calendarDate(periodStart, market) &&
    typeof nativeOpen === 'number' &&
    typeof nativeHigh === 'number' &&
    typeof nativeLow === 'number' &&
    typeof nativeClose === 'number' &&
    nativeCandidate !== undefined &&
    nativeCandidate.open === nativeOpen &&
    nativeCandidate.high === nativeHigh &&
    nativeCandidate.low === nativeLow &&
    nativeCandidate.close === nativeClose;
  const nativeBucket = hasNativeOpeningAggregate ? nativeCandidate : undefined;
  if (nativeBucket) {
    if (Math.abs(firstDaily.open - nativeBucket.open) > 0.01) {
      throw new Error('Yahoo daily first open does not match the native current-period open');
    }
  } else if (Math.min(...dailyResult.timestamp) >= periodStart) {
    throw new Error('Yahoo daily history must begin before the period when native aggregate is absent');
  }

  const currentPeriodBar: Bar = {
    time: periodStart,
    open: firstDaily.open,
    high: Math.max(...currentBars.map((bar) => bar.high)),
    low: Math.min(...currentBars.map((bar) => bar.low)),
    close: lastDaily.close,
    volume: currentBars.reduce((sum, bar) => sum + bar.volume, 0),
  };
  if (
    ![currentPeriodBar.open, currentPeriodBar.high, currentPeriodBar.low, currentPeriodBar.close, currentPeriodBar.volume].every(Number.isFinite) ||
    currentPeriodBar.open < 0 ||
    currentPeriodBar.high < Math.max(currentPeriodBar.open, currentPeriodBar.close) ||
    currentPeriodBar.low > Math.min(currentPeriodBar.open, currentPeriodBar.close) ||
    currentPeriodBar.volume < 0
  ) {
    throw new Error('Yahoo daily current-period aggregate is invalid');
  }
  const bars = [
    ...native.bars.filter((bar) => bar.time !== periodStart),
    currentPeriodBar,
  ].sort((a, b) => a.time - b.time);
  const periodLabel = `${calendarDate(firstDaily.time, market)}–${calendarDate(lastDaily.time, market)}, ${currentBars.length} daily bars`;
  const normalization: BarNormalization = {
    ...native.normalization,
    ...daily.normalization,
    currentPeriod: {
      method: 'daily-ohlcv',
      timeframe,
      periodStart,
      firstDailyTime: firstDaily.time,
      lastDailyTime: lastDaily.time,
      dailyCount: currentBars.length,
      quoteAsOf: dailyMeta.time,
      ...(nativeBucket ? { native: nativeBucket } : {}),
    },
  };
  return {
    ...native,
    bars,
    source: `${native.source} · current period derived from daily OHLCV (${periodLabel})`,
    ...(daily.quote ? { quote: daily.quote } : {}),
    normalization,
    latestBarAt: bars.at(-1)!.time,
  };
}

/** Normalize only actual Yahoo corporate-event records; absent records remain a valid empty list. */
export function normalizeYahooEvents(
  raw: unknown,
  symbol: string,
  asOf = Date.now() / 1000,
): CorporateEventsResult {
  if (!Number.isFinite(asOf) || asOf <= 0) throw new Error('Invalid Yahoo retrieval time');
  const result = eventsResponseSchema.parse(raw).chart.result?.[0];
  if (!result) throw new Error('No corporate event data');
  const normalizedSymbol = normalizeSymbol(symbol);
  const market = getMarketProfile(normalizedSymbol);
  if (result.meta?.symbol && normalizeSymbol(result.meta.symbol) !== normalizedSymbol) {
    throw new Error('Yahoo corporate event response symbol mismatch');
  }
  if (market.market === 'TW') {
    if (result.meta?.currency !== market.currency) throw new Error('Yahoo Taiwan events require TWD currency metadata');
    if (result.meta?.exchangeTimezoneName !== market.timezone) throw new Error('Yahoo Taiwan events require Asia/Taipei timezone metadata');
  } else if (result.meta?.currency && result.meta.currency !== market.currency) {
    throw new Error('Yahoo events currency does not match the US market');
  }
  const events: CorporateEvent[] = [];
  for (const dividend of Object.values(result.events?.dividends ?? {})) {
    const event = {
      symbol: normalizedSymbol,
      type: 'dividend' as const,
      time: dividend.date,
      value: dividend.amount,
      source: YAHOO_SOURCE,
      ...(dividend.currency ? { currency: dividend.currency } : {}),
    };
    if (![event.time, event.value].every(Number.isFinite)) throw new Error('Invalid Yahoo dividend');
    events.push(event);
  }
  for (const split of Object.values(result.events?.splits ?? {})) {
    const value = split.numerator / split.denominator;
    const event = {
      symbol: normalizedSymbol,
      type: 'split' as const,
      time: split.date,
      value,
      source: YAHOO_SOURCE,
      numerator: split.numerator,
      denominator: split.denominator,
    };
    if (![event.time, event.value].every(Number.isFinite)) throw new Error('Invalid Yahoo split');
    events.push(event);
  }
  events.sort((a, b) => a.time - b.time || a.type.localeCompare(b.type));
  return { status: 'available', events, source: YAHOO_SOURCE, asOf };
}

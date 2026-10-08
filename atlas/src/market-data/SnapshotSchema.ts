import { z } from 'zod';
import { timeframes } from './MarketDataProvider';
import {
  CANONICAL_SYMBOL_REGEX,
  calendarDate,
  calendarPeriodStart,
  getMarketProfile,
  marketProfileSchema,
  sameMarketProfile,
  isSessionCloseObservation,
  SESSION_CLOSE_SOURCE_QUALIFIER,
} from './MarketProfile';

const number = z.number().finite();
const timestamp = number.int().positive();
const bar = z.object({ time: timestamp, open: number.nonnegative(), high: number.nonnegative(), low: number.nonnegative(), close: number.nonnegative(), volume: number.nonnegative() })
  .refine(b => b.low <= Math.min(b.open, b.close) && b.high >= Math.max(b.open, b.close) && b.low <= b.high, 'Invalid OHLC');
const quote = z.object({ symbol: z.string().regex(CANONICAL_SYMBOL_REGEX), price: number.positive(), change: number, changePercent: number, asOf: timestamp, source: z.string(), dataState: z.literal('delayed'), retrievedAt: number.positive(), cacheStatus: z.enum(['fresh', 'cached', 'stale']).optional(), market: marketProfileSchema.optional() });
const normalization = z.object({
  healedDailyClose: z.object({ method: z.literal('validated-regular-market-price'), time: timestamp, quoteAsOf: timestamp }).optional(),
  currentPeriod: z.object({
    method: z.literal('daily-ohlcv'), timeframe: z.enum(['1W', '1M']), periodStart: timestamp,
    firstDailyTime: timestamp, lastDailyTime: timestamp, dailyCount: z.number().int().positive(),
    quoteAsOf: timestamp, native: bar.optional(),
  }).optional(),
}).optional();
const bars = z.object({ bars: z.array(bar).min(1).max(10000), source: z.string(), session: z.literal('regular'), delayed: z.literal(true), adjusted: z.literal(true), priceBasis: z.literal('split-adjusted'), asOf: number.positive(), latestBarAt: timestamp, dataState: z.literal('delayed'), cacheStatus: z.enum(['fresh', 'cached', 'stale']).optional(), quote: quote.optional(), market: marketProfileSchema.optional(), sessionCloseObservations: z.array(timestamp).optional(), normalization })
  .refine(r => r.bars.every((b, i) => (!i || b.time > r.bars[i - 1].time) && b.time <= r.asOf + 300) && r.latestBarAt === r.bars.at(-1)!.time, 'Invalid bar timeline');
const event = z.object({ symbol: z.string(), type: z.enum(['split', 'dividend']), time: timestamp, value: number, source: z.string(), currency: z.string().optional(), numerator: number.positive().optional(), denominator: number.positive().optional() })
  .refine(e => e.type !== 'split' || !!e.numerator && !!e.denominator && Math.abs(e.value - e.numerator / e.denominator) < 1e-9, 'Invalid split ratio');
export const snapshotSchema = z.object({
  version: z.literal(3), symbol: z.string().regex(CANONICAL_SYMBOL_REGEX), generatedAt: number.positive(),
  market: marketProfileSchema.optional(),
  results: z.partialRecord(z.enum(timeframes), bars), quote,
  events: z.object({ status: z.literal('available'), events: z.array(event), source: z.string(), asOf: number.positive() }),
}).refine(s => {
  if (s.quote.symbol !== s.symbol || !s.events.events.every(e => e.symbol === s.symbol) || !Object.values(s.results).every(r => r.asOf <= s.generatedAt && (!r.quote || r.quote.symbol === s.symbol))) return false;
  const expectedMarket = getMarketProfile(s.symbol);
  const profileMetadata = [s.market, s.quote.market, ...Object.values(s.results).map(result => result.market), ...Object.values(s.results).map(result => result.quote?.market)];
  if (expectedMarket.market === 'TW') {
    if (!sameMarketProfile(s.market, expectedMarket) || !sameMarketProfile(s.quote.market, expectedMarket)) return false;
    if (Object.values(s.results).some(result => !sameMarketProfile(result.market, expectedMarket) || result.quote && !sameMarketProfile(result.quote.market, expectedMarket))) return false;
  } else if (profileMetadata.some(profile => profile !== undefined && !sameMarketProfile(profile, expectedMarket))) {
    return false;
  }
  for (const [timeframe, result] of Object.entries(s.results)) {
    const observations = result.sessionCloseObservations ?? [];
    if (new Set(observations).size !== observations.length) return false;
    if (!observations.length) continue;
    if (
      expectedMarket.market !== 'TW' ||
      ['1D', '1W', '1M'].includes(timeframe) ||
      !result.source.includes(SESSION_CLOSE_SOURCE_QUALIFIER)
    ) return false;
    for (const time of observations) {
      const observedBar = result.bars.find(bar => bar.time === time);
      if (!observedBar || !isSessionCloseObservation(observedBar, expectedMarket)) return false;
    }
  }
  const daily = s.results['1D'];
  if (!daily) return false;
  const dailyLatest = daily.bars.at(-1)!;
  if (calendarDate(dailyLatest.time, expectedMarket) !== calendarDate(s.quote.asOf, expectedMarket) || Math.abs(dailyLatest.close - s.quote.price) > 0.01) return false;
  for (const timeframe of ['1W', '1M'] as const) {
    const result = s.results[timeframe];
    if (!result) continue;
    const latest = result.bars.at(-1)!;
    if (calendarPeriodStart(latest.time, timeframe, expectedMarket) !== calendarPeriodStart(s.quote.asOf, timeframe, expectedMarket) || Math.abs(latest.close - s.quote.price) > 0.01) return false;
    const provenance = result.normalization?.currentPeriod;
    if (provenance && (provenance.timeframe !== timeframe || provenance.periodStart !== latest.time)) return false;
  }
  return true;
}, 'Snapshot ownership or current quote alignment mismatch');
export type MarketSnapshot = z.infer<typeof snapshotSchema>;

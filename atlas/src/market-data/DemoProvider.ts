import type {
  CorporateEventsResult,
  MarketRange,
  MarketDataProvider,
  Timeframe,
  Bar,
  BarResult,
  Quote,
} from './MarketDataProvider';
import { filterBarsByRange, intervalSeconds, normalizeMarketRange, normalizeSymbol } from './MarketDataProvider';
import { regularSessionOpen } from '../chart/TimeMapper';
import { barEndTime } from './MarketTiming';
import { getMarketProfile } from './MarketProfile';
export const DEMO_AS_OF = Date.parse('2026-10-05T20:05:00Z') / 1000;
const base: Record<string, number> = {
  AAPL: 232,
  MSFT: 425,
  NVDA: 138,
  TSLA: 258,
  AMD: 165,
  META: 580,
  GOOGL: 182,
  AMZN: 218,
  NVO: 65,
};
function hash(s: string) {
  let h = 2166136261;
  for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return h >>> 0;
}
const timelineCache = new Map<Timeframe, number[]>();

function buildTimeline(timeframe: Timeframe): number[] {
  const times: number[] = [];
  const asOfDate = new Date(DEMO_AS_OF * 1000);
  const asOfYear = asOfDate.getUTCFullYear();
  const asOfMonth = asOfDate.getUTCMonth();
  const asOfDay = asOfDate.getUTCDate();
  const date = new Date(asOfDate);
  if (timeframe === '1M') {
    // Monthly bars begin on the first UTC day. Walk calendar months and keep
    // only months whose Eastern midnight close is no later than the fixed as-of.
    date.setUTCDate(1);
    while (times.length < 600) {
      const time = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1) / 1000;
      if (barEndTime(time, timeframe) <= DEMO_AS_OF) times.push(time);
      date.setUTCMonth(date.getUTCMonth() - 1);
    }
    return times.reverse();
  }
  if (timeframe === '1W') {
    date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
    let week = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()) / 1000;
    while (barEndTime(week, '1W') > DEMO_AS_OF) week -= intervalSeconds['1W'];
    while (times.length < 2500) {
      times.push(week);
      week -= intervalSeconds['1W'];
    }
    return times.reverse();
  }

  while (times.length < 2500) {
    const weekday = date.getUTCDay();
    if (weekday !== 0 && weekday !== 6) {
      if (timeframe === '1D') {
        const time = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()) / 1000;
        if (barEndTime(time, timeframe) <= DEMO_AS_OF) times.push(time);
      } else {
        const open = regularSessionOpen(
          date.getUTCFullYear(),
          date.getUTCMonth() + 1,
          date.getUTCDate(),
        );
        const sessionSlots = Math.ceil((390 * 60) / intervalSeconds[timeframe]);
        for (let slot = sessionSlots - 1; slot >= 0; slot--) {
          const time = open + slot * intervalSeconds[timeframe];
          const isAsOfDate =
            date.getUTCFullYear() === asOfYear &&
            date.getUTCMonth() === asOfMonth &&
            date.getUTCDate() === asOfDay;
          if (isAsOfDate && barEndTime(time, timeframe) > DEMO_AS_OF) continue;
          times.push(time);
          if (times.length === 2500) break;
        }
      }
    }
    date.setUTCDate(date.getUTCDate() - 1);
  }
  return times.reverse();
}

export class DemoProvider implements MarketDataProvider {
  readonly id = 'demo';
  readonly supportedTimeframes = ['5m', '15m', '30m', '1H', '4H', '1D', '1W', '1M'] as const;
  readonly capabilities = { corporateEvents: 'unavailable' } as const;
  async getBars(
    symbol: string,
    timeframe: Timeframe,
    range?: MarketRange,
    signal?: AbortSignal,
  ): Promise<BarResult> {
    signal?.throwIfAborted();
    const normalizedSymbol = normalizeSymbol(symbol);
    if (getMarketProfile(normalizedSymbol).market === 'TW') {
      throw new Error('Taiwan Demo unavailable; choose delayed snapshots or Yahoo');
    }
    const normalizedRange = normalizeMarketRange(range);
    let seed = hash(normalizedSymbol + timeframe);
    const rand = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed / 4294967296;
    };
    // Build the regular-session timeline from the fixed as-of backwards.
    let selected = timelineCache.get(timeframe);
    if (!selected) {
      // Generate only the requested tail. Session dates use weekday scheduling
      // and each intraday timestamp uses the real Eastern opening conversion.
      selected = buildTimeline(timeframe);
      timelineCache.set(timeframe, selected);
    }
    let price = base[normalizedSymbol] ?? 50 + (hash(normalizedSymbol) % 250);
    const fullBars: Bar[] = selected.map((time) => {
      const open = price,
        // For 1M this 30-day interval is only an advisory volatility scale;
        // the monthly timeline and close boundary are calendar-based.
        scale = Math.sqrt(intervalSeconds[timeframe] / 86400);
      const close = Math.max(1, open * (1 + (rand() - 0.485) * 0.027 * scale));
      const high = Math.max(open, close) * (1 + rand() * 0.008 * scale),
        low = Math.min(open, close) * (1 - rand() * 0.008 * scale);
      price = close;
      return { time, open, high, low, close, volume: Math.round(1e5 + rand() * 3e6 * scale) };
    });
    const bars = filterBarsByRange(fullBars, normalizedRange);
    if (!bars.length) throw new Error('No Demo bars in requested range');
    return {
      bars,
      source: 'Deterministic demo · simulated data',
      session: 'regular',
      delayed: false,
      adjusted: false,
      asOf: DEMO_AS_OF,
      latestBarAt: bars.at(-1)?.time,
      dataState: 'simulated',
      cacheStatus: 'fresh',
    };
  }
  async getQuote(symbol: string, signal?: AbortSignal): Promise<Quote> {
    signal?.throwIfAborted();
    const normalizedSymbol = normalizeSymbol(symbol);
    if (getMarketProfile(normalizedSymbol).market === 'TW') {
      throw new Error('Taiwan Demo unavailable; choose delayed snapshots or Yahoo');
    }
    const { bars } = await this.getBars(normalizedSymbol, '1D', undefined, signal);
    const a = bars.at(-1)!,
      b = bars.at(-2)!;
    return {
      symbol: normalizedSymbol,
      price: a.close,
      change: a.close - b.close,
      changePercent: (a.close / b.close - 1) * 100,
      asOf: a.time,
      source: 'Deterministic demo · simulated data',
      dataState: 'simulated',
      retrievedAt: DEMO_AS_OF,
      cacheStatus: 'fresh',
    };
  }
  async getCorporateEvents(symbol: string): Promise<CorporateEventsResult> {
    const normalizedSymbol = normalizeSymbol(symbol);
    return {
      status: 'unavailable',
      events: [],
      source: `Deterministic demo does not provide corporate events for ${normalizedSymbol}`,
    };
  }
}

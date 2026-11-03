import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  inRegularSession,
  LIVE_INTERVAL_MS,
  LIVE_MAX_INTERVAL_MS,
  LiveQuoteFeed,
  MAX_BATCH,
  mergeDailyBars,
  mergePeriodBars,
  quotesUrl,
  sameBars,
} from '../src/market-data/LiveQuotes';
import { getMarketProfile } from '../src/market-data/MarketProfile';
import { WORLD_ASSETS } from '../src/market-data/worldAssets';
import type { Bar } from '../src/market-data/MarketDataProvider';
import {
  MAX_QUOTE_SYMBOLS,
  quoteChartRequest,
  slimChartPayload,
  validateQuotesRequest,
} from '../../supabase/functions/market-chart/validate.ts';

const DAY = 86400;
// 2026-10-01 (Thursday) 13:30 UTC, the US daily row convention.
const OCT1 = Date.UTC(2026, 9, 1, 13, 30) / 1000;
const bar = (time: number, close: number, extra: Partial<Bar> = {}): Bar => ({ time, open: close, high: close + 1, low: close - 1, close, volume: 100, ...extra });

/** US trading days from 2026-10-01 through 2026-11-03 at 13:30 UTC. */
function tradingDays(): number[] {
  const days: number[] = [];
  for (let t = OCT1; t <= Date.UTC(2026, 10, 3, 13, 30) / 1000; t += DAY) {
    const weekday = new Date(t * 1000).getUTCDay();
    if (weekday !== 0 && weekday !== 6) days.push(t);
  }
  return days;
}

function yahooPayload(symbol: string, times: readonly number[], closes: readonly number[], metaPrice = closes.at(-1)!) {
  return {
    chart: {
      result: [{
        meta: { symbol, currency: 'USD', exchangeTimezoneName: 'America/New_York', regularMarketPrice: metaPrice, regularMarketTime: times.at(-1)! + 3600, validRanges: ['1d', '5d'] },
        timestamp: [...times],
        indicators: { quote: [{ open: [...closes], high: closes.map((c) => c + 1), low: closes.map((c) => c - 1), close: [...closes], volume: closes.map(() => 100) }] },
      }],
      error: null,
    },
  };
}

describe('market-chart quote batch validation', () => {
  it('accepts a de-duplicated, upper-cased list and rejects bad or too many symbols', () => {
    expect(validateQuotesRequest('aapl, 2330.TW,AAPL,^GSPC')).toEqual({ ok: true, symbols: ['AAPL', '2330.TW', '^GSPC'] });
    expect(validateQuotesRequest('AAPL,../etc')).toMatchObject({ ok: false, code: 'invalid_symbol' });
    expect(validateQuotesRequest('')).toMatchObject({ ok: false, code: 'invalid_symbol' });
    const many = Array.from({ length: MAX_QUOTE_SYMBOLS + 1 }, (_, i) => `A${i}`).join(',');
    expect(validateQuotesRequest(many)).toMatchObject({ ok: false, code: 'too_many_symbols' });
    expect(MAX_BATCH).toBe(MAX_QUOTE_SYMBOLS);
    expect(quoteChartRequest('AAPL')).toEqual({ symbol: 'AAPL', interval: '1d', range: '1mo', events: false });
  });

  it('slims the payload to what the normalizer reads', () => {
    const raw = yahooPayload('AAPL', [OCT1], [10]);
    (raw.chart.result[0] as Record<string, unknown>).events = { dividends: {} };
    const slim = slimChartPayload(raw) as typeof raw;
    expect(slim.chart.result[0]!.meta).not.toHaveProperty('validRanges');
    expect(slim.chart.result[0]).not.toHaveProperty('events');
    expect(slim.chart.result[0]!.indicators.quote[0]!.close).toEqual([10]);
  });
});

describe('live bar merging', () => {
  it('replaces the forming bar, appends new sessions and never rewrites older history', () => {
    const existing = [bar(OCT1, 10), bar(OCT1 + DAY, 11)];
    const merged = mergeDailyBars(existing, [bar(OCT1 - DAY, 1), bar(OCT1 + DAY, 12), bar(OCT1 + 2 * DAY, 13)]);
    expect(merged.map((b) => [b.time, b.close])).toEqual([[OCT1, 10], [OCT1 + DAY, 12], [OCT1 + 2 * DAY, 13]]);
    expect(mergeDailyBars([], [bar(OCT1, 5)])).toEqual([bar(OCT1, 5)]);
    expect(sameBars(existing, [...existing])).toBe(true);
    expect(sameBars(existing, merged)).toBe(false);
  });

  it('rebuilds the current week and month from the daily rows', () => {
    const market = getMarketProfile('AAPL');
    const days = tradingDays();
    const daily = days.map((t, i) => bar(t, 100 + i, { volume: 10 }));
    // Existing weekly bars end with this week's (Mon 2026-11-02) partial bar.
    const weekStart = Date.UTC(2026, 10, 2) / 1000;
    const weekly = [bar(weekStart - 7 * DAY, 90), bar(weekStart, 91)];
    const week = mergePeriodBars(weekly, daily, '1W', market)!;
    expect(week).toHaveLength(2);
    expect(week.at(-1)).toEqual({ time: weekStart, open: daily.at(-2)!.open, high: daily.at(-1)!.high, low: daily.at(-2)!.low, close: daily.at(-1)!.close, volume: 20 });
    const monthStart = Date.UTC(2026, 10, 1) / 1000;
    const month = mergePeriodBars([bar(Date.UTC(2026, 9, 1) / 1000, 80), bar(monthStart, 81)], daily, '1M', market)!;
    expect(month.at(-1)).toMatchObject({ time: monthStart, close: daily.at(-1)!.close, volume: 20 });
    // Rows that start inside the period cannot rebuild it.
    expect(mergePeriodBars(weekly, daily.slice(-2), '1W', market)).toBeNull();
  });
});

describe('regular session window', () => {
  const us = getMarketProfile('AAPL'),
    tw = getMarketProfile('2330.TW');
  it('follows each market clock with a short grace period after the close', () => {
    // Tue 2026-11-03: US opens 14:30 UTC (EST), closes 21:00 UTC.
    expect(inRegularSession(us, Date.UTC(2026, 10, 3, 14, 29))).toBe(false);
    expect(inRegularSession(us, Date.UTC(2026, 10, 3, 14, 31))).toBe(true);
    expect(inRegularSession(us, Date.UTC(2026, 10, 3, 21, 10))).toBe(true);
    expect(inRegularSession(us, Date.UTC(2026, 10, 3, 21, 20))).toBe(false);
    // Taiwan 09:00–13:30 +08:00.
    expect(inRegularSession(tw, Date.UTC(2026, 10, 3, 1, 5))).toBe(true);
    expect(inRegularSession(tw, Date.UTC(2026, 10, 3, 6, 0))).toBe(false);
    // Saturday.
    expect(inRegularSession(us, Date.UTC(2026, 10, 7, 16, 0))).toBe(false);
  });
});

describe('LiveQuoteFeed', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const days = tradingDays().slice(-3);
  function setup(inSession = true) {
    let now = Date.UTC(2026, 10, 3, 16);
    const urls: string[] = [];
    let respond: (symbols: string[]) => Response = (symbols) =>
      Response.json({ results: Object.fromEntries(symbols.map((s) => [s, yahooPayload(s, days, [10, 11, 12 + urls.length])])), errors: {} });
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      urls.push(url.href);
      return respond(url.searchParams.get('quotes')!.split(','));
    }) as unknown as typeof fetch;
    const feed = new LiveQuoteFeed('https://x.supabase.co/functions/v1/market-chart', {
      fetcher, now: () => now, visible: () => true, inSession: () => inSession,
    });
    return {
      feed, urls,
      advance: async (ms: number) => { now += ms; await vi.advanceTimersByTimeAsync(ms); },
      setResponse: (next: typeof respond) => { respond = next; },
    };
  }

  it('fetches new subscribers at once in one batch, then once a minute in session', async () => {
    const { feed, urls, advance } = setup();
    const seen: number[] = [];
    const stopA = feed.subscribe('AAPL', (value) => seen.push(value.bars.at(-1)!.close));
    const stopB = feed.subscribe('msft', () => undefined);
    feed.subscribe(WORLD_ASSETS[0]!.symbol, () => undefined); // world asset: never polled
    feed.subscribe('not a symbol', () => undefined);
    await advance(300);
    expect(urls).toEqual([quotesUrl('https://x.supabase.co/functions/v1/market-chart', ['AAPL', 'MSFT'])]);
    expect(seen).toEqual([13]);
    await advance(LIVE_INTERVAL_MS);
    expect(urls).toHaveLength(2);
    expect(seen).toEqual([13, 14]);
    stopA();
    stopB();
    await advance(LIVE_INTERVAL_MS * 3);
    expect(urls).toHaveLength(2);
  });

  it('outside the session only the first fetch happens', async () => {
    const { feed, urls, advance } = setup(false);
    feed.subscribe('AAPL', () => undefined);
    await advance(300);
    await advance(LIVE_INTERVAL_MS * 3);
    expect(urls).toHaveLength(1);
  });

  it('backs off on rate limiting or failures and recovers after a success', async () => {
    const { feed, urls, advance, setResponse } = setup();
    setResponse(() => Response.json({ results: {}, errors: { AAPL: 'rate_limited' } }));
    feed.subscribe('AAPL', () => undefined);
    await advance(300);
    expect(feed.currentInterval).toBe(LIVE_INTERVAL_MS * 2);
    setResponse(() => new Response('', { status: 503 }));
    await advance(LIVE_INTERVAL_MS * 2);
    expect(feed.currentInterval).toBe(LIVE_INTERVAL_MS * 4);
    await advance(LIVE_INTERVAL_MS * 4);
    expect(feed.currentInterval).toBe(LIVE_MAX_INTERVAL_MS);
    const before = urls.length;
    setResponse((symbols) => Response.json({ results: Object.fromEntries(symbols.map((s) => [s, yahooPayload(s, days, [1, 2, 3])])), errors: {} }));
    await advance(LIVE_MAX_INTERVAL_MS);
    expect(urls.length).toBe(before + 1);
    expect(feed.currentInterval).toBe(LIVE_INTERVAL_MS);
  });

  it('a later subscriber gets the last value without a new request', async () => {
    const { feed, urls, advance } = setup();
    feed.subscribe('AAPL', () => undefined);
    await advance(300);
    const late = vi.fn();
    feed.subscribe('AAPL', late);
    await advance(300);
    expect(late).toHaveBeenCalledTimes(1);
    expect(urls).toHaveLength(1);
  });

  it('ignores rows that fail validation', async () => {
    const { feed, advance, setResponse } = setup();
    setResponse(() => Response.json({ results: { AAPL: yahooPayload('MSFT', days, [1, 2, 3]) }, errors: {} }));
    const listener = vi.fn();
    feed.subscribe('AAPL', listener);
    await advance(300);
    expect(listener).not.toHaveBeenCalled();
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import fixture from './fixtures/market-calendar.json';
import { calendarSessionOpen, MarketCalendarStore, parseMarketCalendar } from '../src/market-data/MarketCalendar';
import { getMarketProfile } from '../src/market-data/MarketProfile';
import { LiveQuoteFeed } from '../src/market-data/LiveQuotes';

const us = getMarketProfile('AAPL'), tw = getMarketProfile('2330.TW');
const at = (date: string) => Date.parse(date);
const store = (value: unknown = fixture) => new MarketCalendarStore({ fetcher: vi.fn(async () => Response.json(value)) });
afterEach(() => vi.useRealTimers());

describe('published exchange calendar', () => {
  it('parses XTAI/XNYS sessions and rejects corrupt bounds, zones, rows and dates', () => {
    expect(parseMarketCalendar(fixture)?.version).toBe(1);
    const variants = [null, {}, { ...fixture, version: 2 }, { ...fixture, validTo: '2026-02-30' },
      { ...fixture, validFrom: '2028-01-01' }, { ...fixture, generatedAt: 'bad' }];
    for (const value of variants) expect(parseMarketCalendar(value)).toBeNull();
    for (const mutate of [
      (x: typeof fixture) => { x.markets.US.timezone = 'Asia/Taipei'; },
      (x: typeof fixture) => { x.markets.US.sessions['2026-11-27'].close = 0; },
      (x: typeof fixture) => { x.markets.US.sessions['2026-11-27'].open += 86400; },
      (x: typeof fixture) => { x.markets.US.sessions = {} as typeof fixture.markets.US.sessions; },
    ]) {
      const value = structuredClone(fixture); mutate(value); expect(parseMarketCalendar(value)).toBeNull();
    }
  });

  it('knows Taiwan national/Lunar holidays, US Thanksgiving and weekends', async () => {
    const calendar = store(); await calendar.ensure(at('2026-10-08T12:00:00Z'));
    for (const [profile, date] of [[tw, '2026-10-09T03:00:00Z'], [tw, '2027-02-08T03:00:00Z'],
      [us, '2026-11-26T16:00:00Z'], [us, '2026-11-28T16:00:00Z']] as const)
      expect(calendarSessionOpen(calendar, profile, at(date))).toBe(false);
  });

  it('stops at the exact 13:00 NY early close and retains normal final-print grace', async () => {
    const calendar = store(); await calendar.ensure(at('2026-11-27T17:59:59Z'));
    expect(calendarSessionOpen(calendar, us, at('2026-11-27T17:59:59Z'))).toBe(true);
    expect(calendarSessionOpen(calendar, us, at('2026-11-27T18:00:00Z'))).toBe(false);
    expect(calendarSessionOpen(calendar, us, at('2026-11-27T18:10:00Z'))).toBe(false);
    expect(calendarSessionOpen(calendar, us, at('2026-11-04T21:10:00Z'))).toBe(true);
    expect(calendarSessionOpen(calendar, us, at('2026-11-04T21:10:00Z'), 0)).toBe(false);
  });

  it('uses actual DST opens and treats missing/out-of-range calendars as unknown', async () => {
    const calendar = store(); await calendar.ensure(at('2026-10-08T12:00:00Z'));
    expect(calendarSessionOpen(calendar, us, at('2026-10-08T13:29:59Z'))).toBe(false);
    expect(calendarSessionOpen(calendar, us, at('2026-10-08T13:30:00Z'))).toBe(true);
    expect(calendarSessionOpen(calendar, us, at('2026-11-04T14:29:59Z'))).toBe(false);
    expect(calendarSessionOpen(calendar, us, at('2026-11-04T14:30:00Z'))).toBe(true);
    expect(calendarSessionOpen(calendar, tw, at('2028-01-05T03:00:00Z'))).toBeUndefined();
    const invalid = store({}); await invalid.ensure(at('2026-11-04T16:00:00Z'));
    expect(calendarSessionOpen(invalid, us, at('2026-11-04T16:00:00Z'))).toBeUndefined();
  });

  it('deduplicates concurrent reads, refreshes next day, and fails softly on HTTP/network errors', async () => {
    const fetcher = vi.fn(async () => Response.json(fixture));
    const calendar = new MarketCalendarStore({ fetcher });
    const now = at('2026-11-04T16:00:00Z');
    await Promise.all([calendar.ensure(now), calendar.ensure(now)]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await calendar.ensure(now + 86400_000); expect(fetcher).toHaveBeenCalledTimes(2);
    for (const fail of [async () => new Response('', { status: 404 }), async () => { throw new Error('offline'); }]) {
      const missing = new MarketCalendarStore({ fetcher: vi.fn(fail) });
      await missing.ensure(now); expect(missing.session(us, now)).toBeUndefined();
    }
  });
});

describe('existing LiveQuoteFeed with calendar', () => {
  function setup(date: string, responseOk = true, calendar = store(), symbol = 'AAPL') {
    let now = at(date);
    const fetcher = vi.fn(async () => responseOk ? Response.json({ results: {}, errors: {} }) : new Response('', { status: 502 }));
    const feed = new LiveQuoteFeed('https://edge.test/market-chart', { fetcher, calendar, now: () => now, visible: () => true });
    const unsubscribe = feed.subscribe(symbol, () => undefined);
    return { feed, fetcher, unsubscribe, advance: (ms: number) => { now += ms; } };
  }

  it.each([
    ['US Thanksgiving', '2026-11-26T16:00:00Z', 'AAPL'],
    ['Taiwan national holiday', '2026-10-09T03:00:00Z', '2330.TW'],
    ['Taiwan Lunar holiday', '2027-02-08T03:00:00Z', '6488.TWO'],
  ])('%s reads last close once, without periodic polling', async (_, date, symbol) => {
    vi.useFakeTimers(); const value = setup(date, true, store(), symbol);
    try {
      await value.feed.poll(false); expect(value.fetcher).toHaveBeenCalledTimes(1);
      for (let n = 0; n < 3; n++) { value.advance(60_000); await value.feed.poll(true); }
      expect(value.fetcher).toHaveBeenCalledTimes(1);
    } finally { value.unsubscribe(); }
  });

  it('does not periodically retry failed initial quotes on a known holiday', async () => {
    vi.useFakeTimers(); const value = setup('2026-11-26T16:00:00Z', false);
    try {
      await value.feed.poll(false); value.advance(300_000); await value.feed.poll(true);
      expect(value.fetcher).toHaveBeenCalledTimes(1);
    } finally { value.unsubscribe(); }
  });

  it('updates before early close, then stops at 13:00 without normal close grace', async () => {
    vi.useFakeTimers(); const value = setup('2026-11-27T17:57:00Z');
    try {
      await value.feed.poll(false); value.advance(60_000); await value.feed.poll(true);
      expect(value.fetcher).toHaveBeenCalledTimes(2);
      value.advance(120_000); await value.feed.poll(true);
      value.advance(600_000); await value.feed.poll(true);
      expect(value.fetcher).toHaveBeenCalledTimes(2);
    } finally { value.unsubscribe(); }
  });

  it('falls back to original weekday session rules if calendar is unavailable', async () => {
    vi.useFakeTimers(); const value = setup('2026-11-04T16:00:00Z', true, store({}));
    try {
      await value.feed.poll(false); value.advance(60_000); await value.feed.poll(true);
      expect(value.fetcher).toHaveBeenCalledTimes(2);
    } finally { value.unsubscribe(); }
  });
});

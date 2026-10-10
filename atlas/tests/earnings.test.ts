import { describe, expect, it } from 'vitest';
import {
  EarningsCalendar,
  daysUntilEarnings,
  earningsBadge,
  easternDate,
  type EarningsEvent,
} from '../src/events/EarningsCalendar';

const DAY = 86_400_000;
/** 2026-10-28 20:00 in New York (EDT, UTC-4). */
const evening = Date.parse('2026-10-29T00:00:00Z');

function event(overrides: Partial<EarningsEvent> = {}): EarningsEvent {
  return {
    symbol: 'AAPL',
    earningsDate: '2026-10-29',
    earningsAt: Date.parse('2026-10-29T20:30:00Z') / 1000,
    window: null,
    estimate: false,
    session: 'amc',
    generatedAt: evening,
    ...overrides,
  };
}

describe('US Eastern calendar days', () => {
  it('names the New York date of an instant, whatever the viewer zone', () => {
    // 23:30 New York on 28 Oct is already 29 Oct in UTC and in Taipei.
    const instant = Date.parse('2026-10-28T23:30:00-04:00');
    expect(easternDate(instant)).toBe('2026-10-28');
    expect(easternDate(Date.parse('2026-10-29T03:30:00Z'))).toBe('2026-10-28');
  });

  it('counts a Taipei morning as the previous New York evening', () => {
    // 2026-10-29 08:00 in Taipei is 2026-10-28 20:00 in New York: the earnings day 29 Oct is tomorrow.
    const taipeiMorning = Date.parse('2026-10-29T08:00:00+08:00');
    expect(daysUntilEarnings('2026-10-29', taipeiMorning)).toBe(1);
    expect(daysUntilEarnings('2026-10-28', taipeiMorning)).toBe(0);
  });

  it('counts a UTC evening the same as its New York day', () => {
    // 2026-10-29 02:30 UTC is 22:30 on 28 Oct in New York: a 29 Oct report is still one day away.
    expect(daysUntilEarnings('2026-10-29', Date.parse('2026-10-29T02:30:00Z'))).toBe(1);
    // 2026-10-29 04:30 UTC is 00:30 on 29 Oct in New York: today.
    expect(daysUntilEarnings('2026-10-29', Date.parse('2026-10-29T04:30:00Z'))).toBe(0);
  });

  it('follows the switch to standard time on 1 November', () => {
    // 2026-11-01 05:30 UTC is 01:30 EDT on 1 Nov; 06:30 UTC is 01:30 EST, still 1 Nov.
    expect(easternDate(Date.parse('2026-11-01T05:30:00Z'))).toBe('2026-11-01');
    expect(easternDate(Date.parse('2026-11-01T06:30:00Z'))).toBe('2026-11-01');
    // 2026-11-01 03:30 UTC is still 31 Oct 23:30 EDT.
    expect(easternDate(Date.parse('2026-11-01T03:30:00Z'))).toBe('2026-10-31');
    expect(daysUntilEarnings('2026-11-02', Date.parse('2026-11-01T06:30:00Z'))).toBe(1);
  });

  it('is zero on the day and negative once the day has passed', () => {
    expect(daysUntilEarnings('2026-10-28', evening)).toBe(0);
    expect(daysUntilEarnings('2026-10-27', evening)).toBe(-1);
    expect(daysUntilEarnings('2026-11-27', evening)).toBe(30);
  });
});

describe('earnings badge', () => {
  const at = (days: number) => earningsBadge(event({ earningsDate: addDays('2026-10-28', days) }), evening);

  it('says today, tomorrow or the number of days', () => {
    expect(at(0)?.text).toBe('今天財報');
    expect(at(1)?.text).toBe('明天財報');
    expect(at(5)?.text).toBe('5 天後財報');
    expect(at(21)?.text).toBe('21 天後財報');
  });

  it('shows nothing before the 21-day window or after the date', () => {
    expect(at(22)).toBeNull();
    expect(at(-1)).toBeNull();
  });

  it('uses the warning tone within 7 days only', () => {
    expect(at(7)?.tone).toBe('soon');
    expect(at(8)?.tone).toBe('later');
  });

  it('explains the date, the session and estimates in the tooltip', () => {
    const confirmed = earningsBadge(event(), evening);
    expect(confirmed?.title).toBe('2026/10/29（美東） · 盤後公布');
    const morning = earningsBadge(event({ session: 'bmo' }), evening);
    expect(morning?.title).toContain('盤前公布');
    const estimated = earningsBadge(
      event({ earningsDate: '2026-11-10', estimate: true, session: null, window: ['2026-11-09', '2026-11-13'] }),
      evening,
    );
    expect(estimated?.title).toContain('預估');
    expect(estimated?.title).toContain('2026/11/09–2026/11/13');
    expect(estimated?.title).not.toContain('盤');
    expect(estimated?.text).toBe('13 天後財報');
    expect(estimated?.tone).toBe('later');
  });

  it('hides the badge when the file is older than 4 days or its time is unknown', () => {
    const fourDays = event({ generatedAt: evening - 4 * DAY });
    expect(earningsBadge(fourDays, evening)).not.toBeNull();
    expect(earningsBadge(event({ generatedAt: evening - 4 * DAY - 60_000 }), evening)).toBeNull();
    expect(earningsBadge(event({ generatedAt: null }), evening)).toBeNull();
  });

  it('hides nothing for a missing event or an invalid clock', () => {
    expect(earningsBadge(null, evening)).toBeNull();
    expect(earningsBadge(event(), Number.NaN)).toBeNull();
  });
});

describe('EarningsCalendar loader', () => {
  const fileBody = (generatedAt: string, items: Record<string, unknown>) => ({
    version: 1,
    market: 'US',
    generatedAt,
    source: 'Yahoo Finance',
    items,
  });
  const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

  function calendarWith(bodies: Array<unknown | 'fail'>, clock: { now: number }) {
    const urls: string[] = [];
    let index = 0;
    const fetcher = (async (input: RequestInfo | URL) => {
      urls.push(String(input));
      const next = bodies[Math.min(index, bodies.length - 1)];
      index += 1;
      if (next === 'fail') return new Response('nope', { status: 503 });
      return jsonResponse(next);
    }) as typeof fetch;
    return {
      urls,
      calendar: new EarningsCalendar('/valuation/', { fetcher, now: () => clock.now }),
    };
  }

  it('reads the event for a US symbol and ignores unknown fields', async () => {
    const generatedAt = new Date(evening).toISOString().replace('.000Z', 'Z');
    const { calendar, urls } = calendarWith([
      fileBody(generatedAt, {
        AAPL: {
          earningsDate: '2026-10-29',
          earningsAt: 1793305800,
          window: null,
          estimate: false,
          session: 'amc',
          extra: 'ignored',
        },
      }),
    ], { now: evening });
    const found = await calendar.getEarnings('aapl');
    expect(urls).toEqual(['/valuation/us-events.json']);
    expect(found).toEqual({
      symbol: 'AAPL',
      earningsDate: '2026-10-29',
      earningsAt: 1793305800,
      window: null,
      estimate: false,
      session: 'amc',
      generatedAt: evening,
    });
  });

  it('drops a broken record without losing the rest of the file', async () => {
    const { calendar } = calendarWith([
      fileBody('2026-10-29T00:00:00Z', {
        BAD: { earningsDate: '29/10/2026' },
        GOOD: { earningsDate: '2026-10-29', session: 'bmo' },
      }),
    ], { now: evening });
    expect(await calendar.getEarnings('BAD')).toBeNull();
    expect((await calendar.getEarnings('GOOD'))?.session).toBe('bmo');
  });

  it('never asks for Taiwan stocks or indices', async () => {
    const { calendar, urls } = calendarWith([fileBody('2026-10-29T00:00:00Z', {})], { now: evening });
    expect(await calendar.getEarnings('2330.TW')).toBeNull();
    expect(await calendar.getEarnings('^GSPC')).toBeNull();
    expect(await calendar.getEarnings('not a symbol!')).toBeNull();
    expect(urls).toEqual([]);
  });

  it('treats a missing or broken file as no dates, without throwing', async () => {
    const { calendar } = calendarWith(['fail'], { now: evening });
    expect(await calendar.getEarnings('AAPL')).toBeNull();
  });

  it('reuses a loaded file for 30 minutes, retries a failure after a minute, keeps the last good copy', async () => {
    const clock = { now: evening };
    const good = fileBody('2026-10-29T00:00:00Z', { AAPL: { earningsDate: '2026-10-29' } });
    const { calendar, urls } = calendarWith([good, 'fail'], clock);
    expect(await calendar.getEarnings('AAPL')).not.toBeNull();
    clock.now += 29 * 60_000;
    await calendar.getEarnings('AAPL');
    expect(urls).toHaveLength(1);

    clock.now += 2 * 60_000;
    expect((await calendar.getEarnings('AAPL'))?.earningsDate).toBe('2026-10-29');
    expect(urls).toHaveLength(2);

    clock.now += 30_000;
    await calendar.getEarnings('AAPL');
    expect(urls).toHaveLength(2);
  });
});

function addDays(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

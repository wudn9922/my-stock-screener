import { describe, expect, it } from 'vitest';
import { groupUpcomingEvents, upcomingEventsSchema, type UpcomingEvent } from '../src/events/UpcomingEvents';

const row = (symbol: string | null, date: string, kind: UpcomingEvent['kind'] = 'earnings'): UpcomingEvent =>
  ({ symbol, market: 'US', date, kind, detail: 'test', source: 'fixture' });

describe('upcoming events', () => {
  it('groups by market-local day, intersects and excludes outside 0–14 days', () => {
    const now = Date.parse('2026-11-28T02:00:00Z'); // still Nov 27 in New York
    const groups = groupUpcomingEvents([
      row('NVDA', '2026-11-27'), row('AAPL', '2026-11-27'), row('MSFT', '2026-11-27'),
      row(null, '2026-11-27', 'holiday'), row('NVDA', '2026-12-11'),
      row('NVDA', '2026-12-12'), row('AAPL', '2026-11-26'), row('AAPL', '2026-11-27'),
    ], ['NVDA', 'AAPL', 'NVDA'], now);
    expect(groups.map(g => [g.date, g.rows.length])).toEqual([['2026-11-27', 3], ['2026-12-11', 1]]);
    expect(groups[0]?.rows.map(r => r.days)).toEqual([0, 0, 0]);
    expect(groups[1]?.rows[0]?.days).toBe(14);
  });
  it('rejects invalid dates and inconsistent sample metrics', () => {
    const doc = { version: 1, generatedAt: '2026-11-28T02:00:00Z',
      coverage: { earnings: 1, alerts: 1, dividends: 1, cashDividends: 0 }, events: [row('AAPL', '2026-02-30')],
      moves: {} };
    expect(upcomingEventsSchema.safeParse(doc).success).toBe(false);
    expect(upcomingEventsSchema.safeParse({ ...doc, events: [], moves: {
      AAPL: { samples: 1, gapPct: null, dayPct: 4, first: '2026-10-01', last: '2026-10-01' },
    } }).success).toBe(false);
  });
});

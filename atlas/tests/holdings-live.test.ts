import { describe, expect, it } from 'vitest';
import { holdingLiveQuote } from '../src/holdings/useHoldingLiveQuotes';
import type { LiveDaily } from '../src/market-data/LiveQuotes';

const now = Date.parse('2026-11-27T02:00:00Z');
const value: LiveDaily = { symbol: '2330.TW', bars: [], retrievedAt: now / 1000, quote: {
  symbol: '2330.TW', price: 100, change: 5, changePercent: 5.26, asOf: now / 1000 - 20,
  source: 'MIS', dataState: 'live', cacheStatus: 'fresh',
}};
describe('holding live quote', () => {
  it('keeps observation time and provenance and derives previous close', () => {
    expect(holdingLiveQuote(value, now)).toEqual({ price: '100', previous: '95', asOf: '2026-11-27T01:59:40.000Z', source: 'MIS', liveState: 'live' });
  });
  it('rejects stale, invalid, simulated and missing quotes', () => {
    for (const quote of [undefined, { ...value.quote!, cacheStatus: 'stale' as const }, { ...value.quote!, dataState: 'simulated' as const },
      { ...value.quote!, asOf: now / 1000 - 3600 }, { ...value.quote!, price: 0 }])
      expect(holdingLiveQuote({ ...value, quote }, now)).toBeUndefined();
  });
  it('labels delayed sources and rejects out-of-session observations', () => {
    expect(holdingLiveQuote({ ...value, quote: { ...value.quote!, dataState: 'delayed' } }, now)?.liveState).toBe('delayed');
    expect(holdingLiveQuote(value, Date.parse('2026-11-27T06:00:00Z'))).toBeUndefined();
  });
});

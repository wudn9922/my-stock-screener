import { describe, expect, it } from 'vitest';
import { quoteStatus } from '../src/market-data/quoteStatus';
import { formatLiveTime, inRegularSession } from '../src/market-data/LiveQuotes';
import { getMarketProfile } from '../src/market-data/MarketProfile';
const ms = Date.parse;
describe('regular-session quote labels', () => {
  it('uses the receive time in an open TW session', () => {
    const now = ms('2026-11-04T04:20:00Z');
    expect(quoteStatus('2330.TW', { asOf: now / 1000 - 900 }, now, now)).toBe(`延遲報價 · 更新於 ${formatLiveTime(now)}`);
  });
  it('uses the last regular trade after close, including the final-print polling grace', () => {
    const trade = ms('2026-11-04T05:30:00Z') / 1000, now = ms('2026-11-04T05:31:00Z');
    expect(inRegularSession(getMarketProfile('2330.TW'), now)).toBe(true);
    expect(quoteStatus('2330.TW', { asOf: trade }, now, now)).toBe('已收盤 · 最後成交 11/04 13:30');
  });
  it.each(['2026-11-04T13:00:00Z', '2026-11-04T21:05:00Z', '2026-11-07T16:00:00Z'])('uses the regular trade in pre/postmarket and on weekends (%s)', date => {
    expect(quoteStatus('AAPL', { asOf: ms('2026-11-03T21:00:00Z') / 1000 }, ms(date), ms(date))).toBe('已收盤 · 最後成交 11/04 05:00');
  });
  it('handles US daylight saving hours and never fabricates a missing/future trade', () => {
    expect(quoteStatus('AAPL', null, undefined, ms('2026-07-06T14:00Z'))).toBe('延遲報價 · 更新於 —');
    expect(quoteStatus('AAPL', { asOf: Infinity }, undefined, ms('2026-11-04T23:00Z'))).toBe('已收盤 · 最後成交 —');
    expect(quoteStatus('AAPL', { asOf: ms('2026-11-05T01:00Z') / 1000 }, undefined, ms('2026-11-04T23:00Z'))).toBe('已收盤 · 最後成交 —');
  });
  it('preserves the simulation label', () => {
    expect(quoteStatus('AAPL', { asOf: 0, dataState: 'simulated' }, undefined, 0)).toBe('模擬報價 · 更新於 —');
  });
});

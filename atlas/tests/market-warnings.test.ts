import { describe, expect, it } from 'vitest';
import sample from './fixtures/market-warnings.sample.json';
import { marketWarningsSchema, type MarketWarnings } from '../src/report/schema';
import { loadMarketWarnings, parseMarketWarnings, weeklySample } from '../src/report/marketWarnings';
import { parseBreakouts, SWING_COUNTING_MODEL, withMonthlyBackfill } from '../src/report/breakouts';
import breakout from './fixtures/breakouts/breakouts.sample.json';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
function available(market: MarketWarnings['markets']['tw']) {
  if (!('asOf' in market)) throw new Error('Expected available market');
  return market;
}

describe('market warnings strict contract', () => {
  it('validates the real read-only full-cache calculation and keeps unavailable thresholds null', () => {
    const raw: unknown = JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/market-warnings.real.json'), 'utf8'));
    const data = marketWarningsSchema.parse(raw);
    const tw = available(data.markets.tw);
    expect(tw.asOf).toBe('2026-10-22');
    expect(tw.latest.count_p30).toBeNull();
    expect(tw.latest.warning_complete).toBe(false);
    expect(tw.indices['^TWII']!.series).toHaveLength(126);
  });
  it('accepts versioned ratios and explicit nulls without inventing a normal light', () => {
    const data = marketWarningsSchema.parse(sample);
    expect(available(data.markets.tw).latest.warning_level).toBe(2);
    expect(available(data.markets.us).latest.vol60_weak).toBeNull();
  });
  it('retains an explicitly dated failed market without changing its values or old calculation time', () => {
    const generatedAt = '2026-10-15T09:00:00+00:00';
    const raw = { ...structuredClone(sample), generatedAt, markets: {
      tw: { ...sample.markets.tw, update: { status: 'updated', generatedAt, checkedAt: generatedAt, reason: null } },
      us: { ...sample.markets.us, update: { status: 'retained', generatedAt: sample.generatedAt, checkedAt: generatedAt, reason: '^IXIC Yahoo 與封存都沒有完整資料' } },
    } };
    const data = marketWarningsSchema.parse(raw);
    expect(available(data.markets.us).latest).toEqual(sample.markets.us.latest);
    expect(data.markets.us.update?.generatedAt).toBe(sample.generatedAt);
    expect(data.markets.tw.update?.status).toBe('updated');
    for (const change of [
      { ...raw.markets.us.update, generatedAt: null },
      { ...raw.markets.us.update, reason: '' },
      { ...raw.markets.us.update, generatedAt: '2027-01-01T00:00:00Z' },
      { ...raw.markets.us.update, checkedAt: sample.generatedAt },
    ]) expect(parseMarketWarnings({ ...raw, markets: { ...raw.markets, us: { ...raw.markets.us, update: change } } }).error).toContain('格式無效');
  });
  it('allows a first-publication unavailable market only with an explicit reason and no fabricated values', () => {
    const missing = { update: { status: 'unavailable', checkedAt: sample.generatedAt, generatedAt: null, reason: '^IXIC 指數未取得' } };
    const raw = { ...sample, markets: { tw: sample.markets.tw, us: missing } };
    expect(marketWarningsSchema.parse(raw).markets.us).toEqual(missing);
    for (const invalid of [{ ...missing, asOf: sample.markets.us.asOf }, { update: { ...missing.update, reason: '' } }])
      expect(parseMarketWarnings({ ...raw, markets: { ...raw.markets, us: invalid } }).error).toContain('格式無效');
  });
  it('validates archive provenance and never accepts dividend-adjusted or unexplained fallback data', () => {
    const source = { source: 'kline-archive', lastDate: sample.markets.tw.asOf, validBars: 500, attempts: 3,
      priceBasis: 'split-adjusted', dividendAdjusted: false, fallbackReason: 'Yahoo 有效根數不足' };
    const raw = { ...sample, markets: { ...sample.markets, tw: { ...sample.markets.tw, indices: {
      ...sample.markets.tw.indices, '^TWII': { ...sample.markets.tw.indices['^TWII'], meta: { price: source, volume: source } },
    } } } };
    expect(available(marketWarningsSchema.parse(raw).markets.tw).indices['^TWII']!.meta?.price.source).toBe('kline-archive');
    for (const invalid of [{ ...source, dividendAdjusted: true }, { ...source, fallbackReason: null }, { ...source, attempts: 4 }]) {
      const changed = structuredClone(raw);
      Object.assign(changed.markets.tw.indices['^TWII'].meta.price, invalid);
      expect(parseMarketWarnings(changed).error).toContain('格式無效');
    }
  });
  it('rejects missing fields, invalid ratios, duplicate dates, stale latest and inconsistent lights', () => {
    const missing = structuredClone(sample) as Record<string, unknown>; delete missing.params;
    expect(parseMarketWarnings(missing).error).toContain('格式無效');
    for (const mutation of [
      (raw: typeof sample) => { raw.markets.tw.series[0]!.leader_success = 4; },
      (raw: typeof sample) => { raw.markets.tw.series[1]!.date = raw.markets.tw.series[0]!.date; },
      (raw: typeof sample) => { raw.markets.tw.latest.date = '2026-01-01'; },
      (raw: typeof sample) => { raw.markets.tw.series[0]!.warning_level = 0; },
    ]) {
      const raw = structuredClone(sample); mutation(raw);
      expect(parseMarketWarnings(raw).error).toContain('格式無效');
      expect(parseMarketWarnings(raw).data).toBeUndefined();
    }
  });
  it('handles HTTP, JSON and connection failures and requests only the small report', async () => {
    for (const response of [new Response('missing', { status: 404 }), new Response('bad'), new Error('offline')]) {
      const result = await loadMarketWarnings(undefined, (async (url: string) => {
        expect(url).toMatch(/\/report\/market-warnings.json$/);
        if (response instanceof Error) throw response;
        return response;
      }) as typeof fetch);
      expect(result.error).toBeTruthy(); expect(result.data).toBeUndefined();
    }
  });
  it('samples short-window lines once per week while preserving all dates', () => {
    const rows = sample.markets.tw.series;
    const sampled = weeklySample(rows, true);
    expect(sampled.size).toBeLessThan(rows.length/3);
    expect(sampled.has(rows.at(-1)!.date)).toBe(true);
    expect(weeklySample(rows, false).size).toBe(rows.length);
  });
  it('retains the swing counting version and cannot import an old monthly backfill', () => {
    const raw = structuredClone(breakout) as typeof breakout & { params: { swingCountingModel?: string } };
    raw.params.swingCountingModel = SWING_COUNTING_MODEL;
    const stats = raw.markets.tw.windows['1m'].strategies.swingHigh as Record<string, unknown>;
    stats.swingCounting = { countingModel: SWING_COUNTING_MODEL, first_attempts: 400, retry_attempts: 80, retry_failures: 65,
      lower_high_share: .49, close_fail_share: .54, unknown_previous_high: 2 };
    const next = parseBreakouts(raw);
    expect(next.status).toBe('ok');
    if (next.status !== 'ok') return;
    expect(next.data.params.swingCountingModel).toBe(SWING_COUNTING_MODEL);
    expect(next.data.markets.tw!.windows['1m']!.strategies.swingHigh!.swingCounting?.retry_attempts).toBe(80);
    expect(withMonthlyBackfill(next, parseBreakouts(breakout))).toBe(next);
  });
});

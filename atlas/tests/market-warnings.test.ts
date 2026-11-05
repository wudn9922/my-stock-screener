import { describe, expect, it } from 'vitest';
import sample from './fixtures/market-warnings.sample.json';
import { marketWarningsSchema } from '../src/report/schema';
import { loadMarketWarnings, parseMarketWarnings, weeklySample } from '../src/report/marketWarnings';
import { parseBreakouts, SWING_COUNTING_MODEL, withMonthlyBackfill } from '../src/report/breakouts';
import breakout from './fixtures/breakouts/breakouts.sample.json';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('market warnings strict contract', () => {
  it('validates the real read-only full-cache calculation and keeps unavailable thresholds null', () => {
    const raw: unknown = JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/market-warnings.real.json'), 'utf8'));
    const data = marketWarningsSchema.parse(raw);
    expect(data.markets.tw.asOf).toBe('2026-10-22');
    expect(data.markets.tw.latest.count_p30).toBeNull();
    expect(data.markets.tw.latest.warning_complete).toBe(false);
    expect(data.markets.tw.indices['^TWII']!.series).toHaveLength(126);
  });
  it('accepts versioned ratios and explicit nulls without inventing a normal light', () => {
    const data = marketWarningsSchema.parse(sample);
    expect(data.markets.tw.latest.warning_level).toBe(2);
    expect(data.markets.us.latest.vol60_weak).toBeNull();
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

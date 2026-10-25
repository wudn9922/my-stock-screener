import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseBreakouts, withMonthlyBackfill } from '../src/report/breakouts';

// Synthetic contracts exercise the merge; the shipped backfill is separately validated against its proof.
const sample = JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/breakouts/breakouts.sample.json'), 'utf8'));
const savedModule = '../../docs/report/breakouts-backfill.json';
function savedReport() {
  const saved = structuredClone(sample);
  const market = saved.markets.tw;
  market.monthly = { intervalMonths: 1, referenceDate: market.asOf, points: ['2026-08-09', '2026-09-09', market.asOf].map(targetDate => ({
    targetDate, asOf: targetDate, evaluated: market.evaluated, universeCount: market.universeCount,
    status: 'ok', strategies: market.windows['1m'].strategies,
  })) };
  return saved;
}

describe('saved monthly backfill', () => {
  it('fills the same reference date while keeping the live latest statistics and source untouched', () => {
    const latest = parseBreakouts(sample, 'https://example.test/report/breakouts.json');
    const saved = parseBreakouts(savedReport());
    const result = withMonthlyBackfill(latest, saved);
    expect(result.status).toBe('ok');
    if (result.status !== 'ok' || latest.status !== 'ok') return;
    expect(result.data.markets.tw!.monthly!.points.map(point => point.asOf)).toEqual(['2026-08-09', '2026-09-09', '2026-10-09']);
    expect(result.data.markets.tw!.monthly!.points.at(-1)!.strategies).toBe(latest.data.markets.tw!.windows['1m']!.strategies);
    expect(result.data.markets.tw!.windows).toBe(latest.data.markets.tw!.windows);
    expect(result.data.generatedAt).toBe(latest.data.generatedAt);
    if (saved.status === 'ok') expect(result.data.markets.tw!.monthly!.calculatedAt).toBe(saved.data.generatedAt);
    expect(result.url).toBe(latest.url);
    expect(latest.data.markets.tw!.monthly).toBeNull();
  });

  it('preserves a newly generated history including its explicit coverage gaps', () => {
    const raw = savedReport();
    raw.markets.tw.monthly.points[0].status = 'insufficient';
    raw.markets.tw.monthly.points[0].evaluated = 1;
    const latest = parseBreakouts(raw);
    expect(withMonthlyBackfill(latest, parseBreakouts(savedReport()))).toBe(latest);
  });

  it.each(['2026-10-08', '2026-10-10'])('never attaches another reference date (%s)', date => {
    const raw = structuredClone(sample);
    raw.markets.tw.asOf = date;
    const latest = parseBreakouts(raw);
    expect(withMonthlyBackfill(latest, parseBreakouts(savedReport()))).toBe(latest);
  });

  it('does not promote legacy partial-market data or incompatible success models', () => {
    for (const legacy of [true, false]) {
      const raw = structuredClone(sample);
      if (legacy) delete raw.markets.tw.coverage;
      else delete raw.params.successModel;
      const latest = parseBreakouts(raw);
      expect(withMonthlyBackfill(latest, parseBreakouts(savedReport()))).toBe(latest);
    }
  });

  it('never combines histories from different signal definitions or box thresholds', () => {
    for (const parameter of ['cooldownDays', 'swingMethod', 'boxLookback', 'jumpLimitPct']) {
      const raw = structuredClone(sample);
      raw.params[parameter] = parameter === 'swingMethod' ? 'atr-reversal-v1' : 3;
      const latest = parseBreakouts(raw);
      expect(withMonthlyBackfill(latest, parseBreakouts(savedReport()))).toBe(latest);
    }
    const raw = structuredClone(sample);
    raw.markets.tw.boxMaxWidthPct = 1;
    const latest = parseBreakouts(raw);
    expect(withMonthlyBackfill(latest, parseBreakouts(savedReport()))).toBe(latest);
  });

  it('preserves missing and malformed reports', () => {
    for (const latest of [parseBreakouts(null), parseBreakouts({})]) {
      expect(withMonthlyBackfill(latest, parseBreakouts(savedReport()))).toBe(latest);
    }
  });
});

describe('latest report backfill loading', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.doUnmock(savedModule);
    vi.resetModules();
  });

  it('shares the initial backfilled load and refreshes to a new producer-supplied history', async () => {
    vi.resetModules();
    vi.doMock(savedModule, () => ({ default: savedReport() }));
    const fetcher = vi.fn(async () => new Response(JSON.stringify(sample)));
    vi.stubGlobal('fetch', fetcher);
    const module = await import('../src/report/breakouts');
    const first = module.loadBreakouts(true);
    expect(module.loadBreakouts()).toBe(first);
    const result = await first;
    expect(result.status).toBe('ok');
    if (result.status === 'ok') expect(result.data.markets.tw!.monthly!.points).toHaveLength(3);
    expect(fetcher).toHaveBeenCalledTimes(1);
    fetcher.mockImplementation(async () => new Response(JSON.stringify(savedReport())));
    const refreshed = await module.loadBreakouts(true);
    expect(refreshed.status).toBe('ok');
    if (refreshed.status === 'ok') expect(refreshed.data.markets.tw!.monthly!.calculatedAt).toBeUndefined();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('preserves the live report if the saved result cannot load', async () => {
    vi.resetModules();
    vi.doMock(savedModule, () => { throw new Error('backfill chunk unavailable'); });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(sample))));
    const module = await import('../src/report/breakouts');
    const result = await module.loadBreakouts(true);
    expect(result.status).toBe('ok');
    if (result.status === 'ok') {
      expect(result.data.markets.tw!.monthly).toBeNull();
      expect(result.data.markets.tw!.windows['1m']!.strategies.swingHigh!.comparison!.rules.hold5!.winRate).toBe(50);
    }
  });

  it('does not let a failed older request clear a newer successful shared result', async () => {
    vi.resetModules();
    vi.doMock(savedModule, () => ({ default: savedReport() }));
    let rejectFirst!: (reason: Error) => void;
    const firstResponse = new Promise<Response>((_, reject) => { rejectFirst = reject; });
    const fetcher = vi.fn().mockImplementationOnce(() => firstResponse)
      .mockImplementation(async () => new Response(JSON.stringify(sample)));
    vi.stubGlobal('fetch', fetcher);
    const module = await import('../src/report/breakouts');
    const first = module.loadBreakouts(true);
    const current = module.loadBreakouts(true);
    await current;
    rejectFirst(new Error('older network failure'));
    expect((await first).status).toBe('error');
    expect(module.loadBreakouts()).toBe(current);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('leaves original archived reports unfilled even when their reference date matches', async () => {
    vi.resetModules();
    const loader = vi.fn(() => ({ default: savedReport() }));
    vi.doMock(savedModule, loader);
    const module = await import('../src/report/breakouts');
    const result = await module.fetchBreakoutSnapshot('2026-10-09', 'https://example.test/report/breakouts.json',
      vi.fn(async () => new Response(JSON.stringify(sample))));
    expect(result.status).toBe('ok');
    if (result.status === 'ok') expect(result.data.markets.tw!.monthly).toBeNull();
    expect(loader).not.toHaveBeenCalled();
  });
});

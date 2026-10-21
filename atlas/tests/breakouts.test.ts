import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { DEFAULT_BREAKOUT_PARAMS, breakoutsUrl, edgeVsBase, fetchBreakouts, parseBreakouts, sampleLevel, type BreakoutStats } from '../src/report/breakouts';

// Synthetic contract fixture (not market data); tests/test_breakouts.py checks it against the job output.
const sample = JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/breakouts/breakouts.sample.json'), 'utf8')) as Record<string, unknown>;

const stats = (overrides: Partial<BreakoutStats> = {}): BreakoutStats => ({
  eligible: 100,
  n: 40,
  maturedN: 30,
  wins: null,
  winRate: 55,
  avgReturn: 1.2,
  medianReturn: 0.8,
  failRate: 30,
  avgHoldDays: 9,
  baseRate: 50,
  baseAvgReturn: 0.5,
  comparison: null,
  ...overrides,
});

describe('breakouts schema', () => {
  it('does not accept an unsupported full-market claim without consistent coverage', () => {
    const raw = structuredClone(sample) as { markets: { tw: { coverage?: unknown }; us: unknown } };
    delete raw.markets.tw.coverage;
    const parsed = parseBreakouts(raw);
    expect(parsed.status).toBe('ok');
    if (parsed.status === 'ok') expect(parsed.data.markets.tw!.scope).toBe('legacy');
  });
  it('parses the job output: params, both markets, windows and strategies', () => {
    const result = parseBreakouts(sample);
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const { params, markets, generatedAt } = result.data;
    expect(generatedAt).toBe('2026-10-09T10:00:00+00:00');
    expect(params.windows.map((entry) => entry.key)).toEqual(['1w', '1m']);
    expect(params.windows[0]).toEqual({ key: '1w', days: 5, label: '近1週' });
    expect(params.matureDays).toBe(3);
    expect(params.minSample).toBe(20);
    expect(params.strategies).toHaveLength(3);
    expect(params.strategies.map((meta) => meta.key)).toEqual(['swingHigh', 'box20', 'ema20']);
    expect(params.strategies[0]!.definition).toContain('波段高點');
    expect(params.swingMethod).toBe('atr-reversal-v2');
    expect(params.successModel).toBe('four-rules-v1');
    expect(params.successAtrPeriod).toBe(14);
    expect(params.observationDays).toBe(5);
    expect(params.swingAtrPeriod).toBe(20);
    expect(params.swingReversalAtr).toBe(2);
    const tw = markets.tw!;
    expect(tw.asOf).toBe('2026-10-09');
    expect(tw.evaluated).toBe(900);
    expect(tw.anomalies).toEqual({ stocks: 3, days: 4 });
    expect(tw.windows['1m']!.strategies.swingHigh).toMatchObject(
      stats({
        eligible: 880,
        n: 300,
        maturedN: 250,
        wins: 135,
        winRate: 54,
        avgReturn: 4.9,
        medianReturn: 3.1,
        failRate: 35,
        avgHoldDays: 10.2,
        baseRate: 59,
        baseAvgReturn: 5,
        comparison: {
          n: 300, completeN: 220, pendingN: 75, missingN: 5, commonFailedN: 44, sameBarFailedN: 3,
          rules: {
            hold5: { wins: 110, failures: 110, evaluatedN: 220, winRate: 50 },
            nextHigh: { wins: 99, failures: 121, evaluatedN: 220, winRate: 45 },
            firstHit: { wins: 121, failures: 99, evaluatedN: 220, winRate: 55 },
            targetAndNext: { wins: 88, failures: 132, evaluatedN: 220, winRate: 40 },
          },
        },
      }),
    );
    expect(tw.windows['1w']!.strategies.box20!.winRate).toBeNull();
    expect(tw.windows['1w']!.market).toEqual({
      entryDays: 2,
      pairs: 1800,
      upRate: 48,
      avgReturn: -0.3,
    });
    expect(markets.us!.universe).toBe('全市場美股普通股與 ADR（NYSE／NASDAQ／NYSE American 等）');
    expect(markets.us!.scope).toBe('full');
    expect(tw.coverage).toEqual({ loaded: 980, missing: 20 });
  });

  it('tolerates unknown fields, numeric strings, bad strategies and missing params', () => {
    const result = parseBreakouts({
      version: 2,
      extra: true,
      markets: {
        tw: {
          asOf: '2026-10-08',
          universe: 'u',
          evaluated: '12',
          windows: {
            '1w': {
              days: '5',
              strategies: {
                high20: {
                  n: '4',
                  maturedN: 2,
                  winRate: '50.0',
                  baseRate: 'x',
                  extra: 1,
                },
                broken: 'not an object',
              },
            },
            '1m': { strategies: {} },
          },
        },
        us: { universe: 'no windows at all' },
        jp: { windows: { '1w': { strategies: { high20: {} } } } },
      },
    });
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(Object.keys(result.data.markets)).toEqual(['tw']);
    const tw = result.data.markets.tw!;
    expect(tw.evaluated).toBe(12);
    expect(tw.scope).toBe('legacy');
    expect(Object.keys(tw.windows)).toEqual(['1w']);
    expect(Object.keys(tw.windows['1w']!.strategies)).toEqual(['high20']);
    expect(tw.windows['1w']!.strategies.high20).toMatchObject({
      n: 4,
      maturedN: 2,
      winRate: 50,
      baseRate: null,
      eligible: 0,
    });
    expect(tw.windows['1w']!.market).toEqual({
      entryDays: 0,
      pairs: 0,
      upRate: null,
      avgReturn: null,
    });
    expect(result.data.params).toEqual(DEFAULT_BREAKOUT_PARAMS);
  });

  it('keeps valid params entries and falls back per field', () => {
    const result = parseBreakouts({
      params: {
        windows: [{ key: '1m', days: 21 }, { days: 5 }],
        minSample: 'bad',
        strategies: [{ key: 'box20', label: '箱型' }],
      },
      markets: {
        us: { windows: { '1m': { strategies: { box20: { n: 1 } } } } },
      },
    });
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.data.params.windows).toEqual([{ key: '1m', days: 21, label: '1m' }]);
    expect(result.data.params.minSample).toBe(20);
    expect(result.data.params.strategies).toEqual([
      {
        key: 'box20',
        label: '箱型',
        kind: '',
        lookback: null,
        volume: false,
        definition: '',
      },
    ]);
  });

  it('keeps old pivot reports distinct and rejects incomplete ATR-wave metadata', () => {
    const raw = structuredClone(sample) as { params: Record<string, unknown> };
    delete raw.params.swingMethod;
    delete raw.params.swingAtrPeriod;
    delete raw.params.swingReversalAtr;
    raw.params.swingPivotLeft = 2;
    raw.params.swingPivotRight = 2;
    const legacy = parseBreakouts(raw);
    expect(legacy.status).toBe('ok');
    if (legacy.status === 'ok') {
      expect(legacy.data.params.swingMethod).toBe('fractal-v1');
      expect(legacy.data.params.swingReversalAtr).toBeNull();
    }
    raw.params.swingMethod = 'atr-reversal-v1';
    raw.params.swingAtrPeriod = 20;
    raw.params.swingReversalAtr = -2;
    const invalid = parseBreakouts(raw);
    expect(invalid.status).toBe('ok');
    if (invalid.status === 'ok') expect(invalid.data.params.swingMethod).toBe('unknown');
  });

  it('validates the shared outcome cohort and never borrows a legacy rate', () => {
    const raw = structuredClone(sample) as { markets: { tw: { windows: Record<string, { strategies: Record<string, { comparison: Record<string, unknown> }> }> } } };
    const comparison = raw.markets.tw.windows['1m']!.strategies.swingHigh!.comparison;
    comparison.completeN = 999;
    const result = parseBreakouts(raw);
    expect(result.status).toBe('ok');
    if (result.status === 'ok') expect(result.data.markets.tw!.windows['1m']!.strategies.swingHigh!.comparison).toBeNull();
  });

  it('keeps a missing rule missing without substituting the other three', () => {
    const raw = structuredClone(sample) as { markets: { tw: { windows: Record<string, { strategies: Record<string, { comparison: { rules: Record<string, unknown> } }> }> } } };
    delete raw.markets.tw.windows['1m']!.strategies.swingHigh!.comparison.rules.firstHit;
    const result = parseBreakouts(raw);
    expect(result.status).toBe('ok');
    if (result.status === 'ok') {
      const rules = result.data.markets.tw!.windows['1m']!.strategies.swingHigh!.comparison!.rules;
      expect(rules.firstHit).toBeUndefined();
      expect(rules.hold5?.winRate).toBe(50);
    }
  });

  it('reports a document without any market as missing and a non-object as invalid', () => {
    expect(parseBreakouts({ markets: {} }).status).toBe('missing');
    expect(parseBreakouts({}).status).toBe('missing');
    expect(parseBreakouts([]).status).toBe('invalid');
    expect(parseBreakouts('x').status).toBe('invalid');
    expect(parseBreakouts(null).status).toBe('invalid');
  });
});

describe('breakouts loading', () => {
  it('sits next to latest.json', () => {
    expect(breakoutsUrl('/my-stock-screener/atlas/', undefined, 'https://x.test')).toBe('https://x.test/my-stock-screener/report/breakouts.json');
    expect(breakoutsUrl('/', 'https://cdn.test/r/latest.json', 'https://x.test')).toBe('https://cdn.test/r/breakouts.json');
  });

  it('maps 404, HTTP errors, network failures and non-JSON bodies to friendly states', async () => {
    const respond = (body: string, status = 200) => vi.fn(async () => new Response(body, { status }));
    expect((await fetchBreakouts('u', respond('nope', 404) as unknown as typeof fetch)).status).toBe('missing');
    const failed = await fetchBreakouts('u', respond('x', 503) as unknown as typeof fetch);
    expect(failed).toMatchObject({
      status: 'error',
      message: '突破勝率暫時無法取得（HTTP 503）。',
    });
    const offline = vi.fn(async () => {
      throw new TypeError('offline');
    });
    expect((await fetchBreakouts('u', offline as unknown as typeof fetch)).status).toBe('error');
    expect((await fetchBreakouts('u', respond('<!doctype html>') as unknown as typeof fetch)).status).toBe('invalid');
    const ok = await fetchBreakouts('u', respond(JSON.stringify(sample)) as unknown as typeof fetch);
    expect(ok.status).toBe('ok');
  });
});

describe('sample level and edge', () => {
  it('grades how far a row can be trusted', () => {
    expect(sampleLevel(undefined, 20)).toBe('ineligible');
    expect(sampleLevel(stats({ eligible: 0 }), 20)).toBe('ineligible');
    expect(sampleLevel(stats({ n: 0, maturedN: 0, winRate: null }), 20)).toBe('none');
    expect(sampleLevel(stats({ n: 5, maturedN: 0, winRate: null }), 20)).toBe('pending');
    expect(sampleLevel(stats({ maturedN: 19 }), 20)).toBe('low');
    expect(sampleLevel(stats({ maturedN: 20 }), 20)).toBe('ok');
  });

  it('measures the win rate against the matched base rate in percentage points', () => {
    expect(edgeVsBase(stats({ winRate: 41.7, baseRate: 44.1 }))).toBe(-2.4);
    expect(edgeVsBase(stats({ winRate: 60, baseRate: 50 }))).toBe(10);
    expect(edgeVsBase(stats({ baseRate: null }))).toBeNull();
    expect(edgeVsBase(undefined)).toBeNull();
  });
});

import { describe, expect, it, vi } from 'vitest';
import {
  breadthSignalsUrl,
  fetchBreadthSignals,
  parseBreadthSignals,
  SIGNAL_STATES,
} from '../src/report/breadthSignals';
import { breadthSignalsFixture, signalIndex } from './fixtures/breadth-signals';

function usOf(value: unknown) {
  const parsed = parseBreadthSignals(value);
  if (parsed.status !== 'ok' || !parsed.data.markets.us)
    throw new Error('Invalid fixture');
  return parsed.data.markets.us;
}
describe('breadth signals contract', () => {
  it.each(SIGNAL_STATES)(
    'accepts the daily %s state and ignores unknown fields',
    (state) => {
      const fixture = breadthSignalsFixture(state);
      expect(
        usOf({ ...fixture, extra: 'future extension' }).lowSignal.indices[0]!
          .state,
      ).toBe(state);
    },
  );
  it('accepts numeric strings, preserves nulls and isolates malformed individual indices', () => {
    const fixture = breadthSignalsFixture();
    const valid = fixture.markets.us;
    const parsed = usOf({
      ...fixture,
      markets: {
        ...fixture.markets,
        us: {
          ...valid,
          lowSignal: {
            ...valid.lowSignal,
            indices: [
              { ...signalIndex(), state: 'future' },
              { ...signalIndex(), symbol: '' },
              {
                ...signalIndex(),
                swing: '0.05',
                close: '6010',
                stats: { hitRate: '0.66', baseRate: null },
              },
            ],
          },
        },
      },
    });
    expect(parsed.lowSignal.indices).toHaveLength(1);
    expect(parsed.lowSignal.indices[0]).toMatchObject({
      swing: 0.05,
      close: 6010,
      stats: { hitRate: 0.66, baseRate: null, trades: null },
    });
  });
  it('accepts numeric strings from calibrated readings and threshold files', () => {
    const fixture = breadthSignalsFixture();
    const us = fixture.markets.us;
    const value = {
      ...fixture,
      markets: {
        ...fixture.markets,
        us: {
          ...us,
          readings: {
            ma20: { pct: '18.0', percentile: '6.5', zone: 'oversold' },
            ma60: us.readings.ma60,
          },
          thresholds: {
            ma20: { p5: '14', p10: '22', p50: '50', p90: '76', p95: '81' },
            ma60: us.thresholds.ma60,
          },
        },
      },
    };
    expect(usOf(value)).toMatchObject({
      readings: { ma20: { pct: 18, percentile: 6.5 } },
      thresholds: { ma20: { p5: 14, p10: 22, p50: 50, p90: 76, p95: 81 } },
    });
  });
  it('drops impossible probability values rather than inventing a hit rate', () => {
    const fixture = breadthSignalsFixture();
    fixture.markets.us.lowSignal.indices[0]!.stats.hitRate = 66;
    expect(usOf(fixture).lowSignal.indices[0]!.stats.hitRate).toBeNull();
  });
  it('isolates an invalid US calibration while retaining TW non-applicability', () => {
    const fixture = breadthSignalsFixture();
    fixture.markets.us.thresholds.ma20.p10 = 99;
    const result = parseBreadthSignals(fixture);
    expect(result.status).toBe('ok');
    if (result.status === 'ok') {
      expect(result.data.markets.us).toBeUndefined();
      expect(result.data.markets.tw?.applicable).toBe(false);
    }
  });
  it('aligns numeric history values to their valid dates, sorts and removes duplicate days', () => {
    const fixture = breadthSignalsFixture();
    fixture.markets.us.history = {
      dates: ['2026-10-30', 'bad', '2026-02-30', '2026-10-29', '2026-10-30'],
      ma20Pct: [1, 2, 3, 19, 18],
      ma60Pct: [1, 2, 3, 43],
    };
    expect(usOf(fixture).history).toEqual({
      dates: ['2026-10-29', '2026-10-30'],
      ma20Pct: [19, 18],
      ma60Pct: [43, null],
    });
  });
  it('treats unreadable roots, unsupported versions and empty markets as unavailable', () => {
    for (const value of [
      null,
      [],
      'html',
      { ...breadthSignalsFixture(), version: 2 },
    ])
      expect(parseBreadthSignals(value).status).toBe('invalid');
    expect(parseBreadthSignals({ version: 1, markets: {} }).status).toBe(
      'missing',
    );
  });
  it('rejects a trade state without its trade while preserving other indices', () => {
    const fixture = breadthSignalsFixture();
    const invalid = { ...signalIndex('active'), trade: null };
    const result = usOf({
      ...fixture,
      markets: {
        us: {
          ...fixture.markets.us,
          lowSignal: { indices: [invalid, signalIndex('watch')] },
        },
      },
    });
    expect(result.lowSignal.indices.map((index) => index.state)).toEqual([
      'watch',
    ]);
  });
});

describe('optional signal fetch', () => {
  it('uses the same report directory as breadth with BASE_URL and report URL overrides', () => {
    expect(
      breadthSignalsUrl(
        '/my-stock-screener/atlas/',
        undefined,
        'https://example.test',
      ),
    ).toBe(
      'https://example.test/my-stock-screener/report/breadth-signals.json',
    );
    expect(
      breadthSignalsUrl(
        '/',
        'https://cdn.example.test/daily/latest.json',
        'https://example.test',
      ),
    ).toBe('https://cdn.example.test/daily/breadth-signals.json');
  });
  it('handles missing files, SPA fallback, HTTP failures and offline errors without throwing', async () => {
    const url = 'https://example.test/report/breadth-signals.json';
    for (const [body, status, expected] of [
      ['', 404, 'missing'],
      ['<html/>', 200, 'invalid'],
      ['', 500, 'error'],
    ] as const) {
      await expect(
        fetchBreadthSignals(
          url,
          vi.fn(async () => new Response(body, { status })),
        ),
      ).resolves.toEqual({ status: expected, url });
    }
    await expect(
      fetchBreadthSignals(
        url,
        vi.fn(async () => {
          throw new TypeError('offline');
        }),
      ),
    ).resolves.toEqual({ status: 'error', url });
    await expect(
      fetchBreadthSignals(
        url,
        vi.fn(
          async () => new Response(JSON.stringify(breadthSignalsFixture())),
        ),
      ),
    ).resolves.toMatchObject({ status: 'ok' });
  });
});

import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { describe, expect, it } from 'vitest';
import { bandAt, bandStatus, parseYieldBands, suppliedYieldBands, Z80 } from '../src/rates/yieldBand';
import { japanSpread, parseJapanRates } from '../src/report/bondResearch';
import { parseRoute, serializeRoute } from '../src/app/routes';

const context = { module: { exports: {} as { bandAt: (...args: unknown[]) => { lo: number; center: number; hi: number }; status: (z: number) => string } } };
runInNewContext(readFileSync(new URL('fixtures/yield-band/reference.js', import.meta.url), 'utf8'), context);
const reference = context.module.exports;

describe('user-provided yield-band implementation', () => {
  it.each(['US', 'JP'] as const)('matches the supplied JS for %s, both methods, negative/zero/high policy rates', country => {
    const p = suppliedYieldBands[country]!;
    for (const policy of [-.1, 0, 1.25, 4, 8]) for (const method of ['regression', 'spread'] as const) {
      const result = bandAt(p, policy, Z80, method)!;
      const original = reference.bandAt(p, policy, Z80, method);
      expect(result.lo).toBeCloseTo(original.lo, 12);
      expect(result.center).toBeCloseTo(original.center, 12);
      expect(result.hi).toBeCloseTo(original.hi, 12);
    }
  });
  it('reproduces the attachment snapshot bands and every status boundary', () => {
    const us = bandAt(suppliedYieldBands.US!, 4)!;
    const jp = bandAt(suppliedYieldBands.JP!, 1.25)!;
    expect([us.lo, us.center, us.hi].map(v => Number(v.toFixed(4)))).toEqual([3.519, 5.1146, 6.7103]);
    expect([jp.lo, jp.center, jp.hi].map(v => Number(v.toFixed(4)))).toEqual([1.1354, 2.0957, 3.0561]);
    for (const z of [-3, -Z80, -.5, -.499, 0, .499, .5, Z80, 3]) expect(bandStatus(z)).toBe(reference.status(z));
    expect(bandStatus(NaN)).toBe('無法計算');
  });
  it('isolates malformed countries and refuses missing/zero-deviation/nonfinite inputs', () => {
    expect(parseYieldBands(null)).toBeNull();
    expect(parseYieldBands({ ...suppliedYieldBands, US: { ...suppliedYieldBands.US, sd: 0 } })).toMatchObject({ US: null, JP: expect.any(Object) });
    expect(parseYieldBands({ ...suppliedYieldBands, z80: NaN })).toBeNull();
    expect(bandAt(suppliedYieldBands.US!, NaN)).toBeNull();
    expect(bandAt({ ...suppliedYieldBands.US!, sd: 0 }, 4)).toBeNull();
  });
  it('parses independent Japanese data, aligns policy to the observation date and preserves gaps in spreads', () => {
    const data = { version: 1, asOf: '2026-10-28', generatedAt: '2026-10-29T00:00:00Z',
      tenYear: { value: 2.867, date: '2026-10-28', source: 'MOF' }, policy: { value: 1.25, date: '2026-09-24', source: 'BOJ' },
      daily: { dates: ['2026-10-27', '2026-10-28'], series: { JGB10: [null, 2.867], BOJ_TARGET: [1.25, 1.25] } }, weekly: {} };
    const parsed = parseJapanRates(data)!;
    expect(japanSpread(parsed.daily)).toEqual([{ time: '2026-10-27', value: null }, { time: '2026-10-28', value: 2.867 - 1.25 }]);
    expect(parseJapanRates({ ...data, policy: { ...data.policy, date: '2026-10-30' } })).toBeNull();
    expect(parseJapanRates({ ...data, asOf: '2026-10-29' })).toBeNull();
  });
  it('round-trips Japanese bond routing without changing the default US rates or old aliases', () => {
    const route = { page: 'world', view: 'jp-bonds' } as const;
    expect(parseRoute(serializeRoute(route)).route).toEqual(route);
    expect(parseRoute('?page=bonds').route).toEqual({ page: 'world' });
  });
});

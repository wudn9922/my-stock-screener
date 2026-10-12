import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseRatesHistory, type HistoryBlock, type RatesHistory, type RatesSpread } from '../src/report/rates';
import {
  SPREAD_DEFS,
  curveDomain,
  curveFrame,
  inversionRanges,
  rebase,
  spreadSeries,
  spreadSummary,
  valueOnOrBefore,
  windowStartIndex,
} from '../src/rates/ratesView';

// Synthetic fixture (not market data).
const history = (() => {
  const raw = JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/rates/rates-history.sample.json'), 'utf8')) as unknown;
  const result = parseRatesHistory(raw);
  if (result.status !== 'ok') throw new Error('fixture');
  return result.data;
})();

const block = (dates: string[], series: Record<string, (number | null)[]>): HistoryBlock => ({ dates, series });

describe('spreadSeries', () => {
  it('subtracts the short leg from the long leg in basis points and keeps gaps', () => {
    const data = block(['2024-01-05', '2024-01-12', '2024-01-19'], { DGS10: [4.0, 4.123, null], DGS2: [4.4, 4.1, 4.2] });
    const { dates, values } = spreadSeries(data, 'DGS10', 'DGS2');
    expect(dates).toEqual(data.dates);
    expect(values[0]).toBeCloseTo(-40, 10);
    expect(values[1]).toBeCloseTo(2.3, 10);
    expect(values[2]).toBeNull();
    expect(spreadSeries(data, 'DGS10', 'NOPE').values).toEqual([null, null, null]);
  });

  it('exposes only 10Y−Fed (default) and 10Y−3M spreads', () => {
    expect(SPREAD_DEFS.map((spread) => spread.key)).toEqual(['10yfed', '10y3m']);
    expect(SPREAD_DEFS[0]).toMatchObject({ long: 'DGS10', short: 'FEDTARGETMID' });
    expect(SPREAD_DEFS[1]).toMatchObject({ long: 'DGS10', short: 'DGS3MO' });
  });

  it('computes 10Y minus the Fed target midpoint, without a fallback to the upper bound', () => {
    const data = block(['2026-10-08', '2026-10-09', '2026-10-12'], {
      DGS10: [5.0, 5.1, 5.2],
      FEDTARGETMID: [4.125, null, 3.875],
      DFEDTARU: [4.25, 4.25, 4.0],
    });
    expect(spreadSeries(data, 'DGS10', 'FEDTARGETMID').values).toEqual([87.5, null, 132.5]);
    expect(spreadSeries(data, 'DGS10', 'MISSING').values).toEqual([null, null, null]);
  });
});

describe('inversionRanges', () => {
  const dates = ['2022-01-07', '2022-01-14', '2022-01-21', '2022-01-28', '2022-02-04', '2022-02-11', '2022-02-18', '2022-02-25'];

  it('finds runs of negative values, their low point and an ongoing tail', () => {
    const ranges = inversionRanges(dates, [10, -5, -20, null, -3, 4, -1, -2]);
    expect(ranges).toEqual([
      { start: '2022-01-14', end: '2022-02-04', startIndex: 1, endIndex: 4, minBp: -20, minDate: '2022-01-21' },
      { start: '2022-02-18', end: null, startIndex: 6, endIndex: 7, minBp: -2, minDate: '2022-02-25' },
    ]);
  });

  it('a trailing null does not end the inversion; a positive value does', () => {
    expect(inversionRanges(dates.slice(0, 3), [1, -1, null]).at(-1)!.end).toBeNull();
    expect(inversionRanges(dates.slice(0, 4), [1, -1, -2, 0]).at(-1)!.end).toBe('2022-01-21');
    expect(inversionRanges(dates, dates.map(() => 5))).toEqual([]);
    expect(inversionRanges([], [])).toEqual([]);
  });

  it('merges short gaps and drops short blips', () => {
    const values = [-5, -6, 3, -4, 2, 2, -1, 2];
    expect(inversionRanges(dates, values)).toHaveLength(3);
    const merged = inversionRanges(dates, values, { mergeGapDays: 14 });
    expect(merged[0]).toMatchObject({ start: '2022-01-07', end: '2022-01-28', minBp: -6 });
    // The lone −1 week (0 days long) is dropped with minDays = 7; the first episode lasts 21 days.
    const filtered = inversionRanges(dates, values, { mergeGapDays: 14, minDays: 7 });
    expect(filtered.map((range) => range.start)).toEqual(['2022-01-07']);
  });

  it('finds the 2022–2024 10Y−2Y inversion in the synthetic weekly history', () => {
    const { dates: weeks, values } = spreadSeries(history.weekly, 'DGS10', 'DGS2');
    const ranges = inversionRanges(weeks, values, { mergeGapDays: 30, minDays: 10 });
    const episode = ranges.find((range) => range.start.startsWith('2022'));
    expect(episode).toBeTruthy();
    expect(episode!.end!.startsWith('2024')).toBe(true);
    expect(episode!.minBp).toBeLessThan(-80);
  });
});

describe('windowStartIndex and rebase', () => {
  it('finds the window start by calendar years', () => {
    const dates = ['2020-01-03', '2021-01-01', '2022-01-07', '2023-01-06', '2024-01-05', '2025-01-03'];
    expect(windowStartIndex(dates, 1)).toBe(4);
    expect(windowStartIndex(dates, 3)).toBe(2);
    expect(windowStartIndex(dates, null)).toBe(0);
    expect(windowStartIndex(dates, 50)).toBe(0);
    expect(windowStartIndex([], 1)).toBe(0);
  });

  it('rebases to 100 at the window start', () => {
    const result = rebase([50, 80, 100, 120, null, 90], 2);
    expect(result.values).toEqual([100, 120, null, 90]);
    expect(result.baseIndex).toBe(2);
    expect(result.lateStart).toBe(false);
  });

  it('rebases a fund launched inside the window at its own first value and flags it', () => {
    const result = rebase([null, null, null, 100, 110], 0);
    expect(result.values).toEqual([null, null, null, 100, 110.00000000000001]);
    expect(result.baseIndex).toBe(3);
    expect(result.lateStart).toBe(true);
    expect(rebase([null, null], 0)).toEqual({ values: [null, null], baseIndex: null, lateStart: false });
  });

  it('keeps relative performance: the ratio between two dates is unchanged', () => {
    const tlt = history.etf.series.TLT!;
    const start = windowStartIndex(history.etf.dates, 5);
    const { values } = rebase(tlt, start);
    const last = values.at(-1)!;
    expect(last / 100).toBeCloseTo(tlt.at(-1)! / tlt[start]!, 10);
  });
});

describe('curveFrame', () => {
  it('builds a replay frame from the weekly history', () => {
    const first = curveFrame(history, 0)!;
    expect(first.date.startsWith('2003-01')).toBe(true);
    expect(first.nominal).toHaveLength(11);
    expect(first.real).toHaveLength(5);
    // DFII30 did not exist in 2003.
    expect(first.real[4]).toBeNull();
    const lastIndex = history.weekly.dates.length - 1;
    const last = curveFrame(history, lastIndex)!;
    const ten = history.weekly.series.DGS10![lastIndex]!;
    const two = history.weekly.series.DGS2![lastIndex]!;
    expect(last.nominal[8]).toBe(ten);
    expect(last.spread10y2yBp).toBe(Math.round((ten - two) * 100));
    expect(curveFrame(history, -1)).toBeNull();
    expect(curveFrame(history, lastIndex + 1)).toBeNull();
  });

  it('follows the requested tenor order and tolerates missing series', () => {
    const tiny: RatesHistory = {
      version: 1,
      generatedAt: null,
      asOf: null,
      weekly: block(['2024-01-05'], { DGS10: [4.2] }),
      daily: block([], {}),
      etf: block([], {}),
    };
    expect(curveFrame(tiny, 0, ['10Y', '2Y'], ['10Y'])).toEqual({ date: '2024-01-05', nominal: [4.2, null], real: [null], spread10y2yBp: null });
  });
});

describe('valueOnOrBefore and curveDomain', () => {
  it('looks back to the latest non-null value', () => {
    const data = block(['2024-01-05', '2024-01-12', '2024-01-19'], { X: [1, null, 3] });
    expect(valueOnOrBefore(data, 'X', '2024-01-15')).toEqual({ date: '2024-01-05', value: 1 });
    expect(valueOnOrBefore(data, 'X', '2024-02-01')).toEqual({ date: '2024-01-19', value: 3 });
    expect(valueOnOrBefore(data, 'X', '2023-12-31')).toBeNull();
    expect(valueOnOrBefore(data, 'Y', '2024-02-01')).toBeNull();
  });

  it('pads and rounds the y range to quarter points', () => {
    expect(curveDomain([[3.6, 4.1, null], [3.9]])).toEqual([3.25, 4.5]);
    expect(curveDomain([[null]])).toEqual([0, 5]);
    const [low, high] = curveDomain([[4.0, 4.05]]);
    expect(high - low).toBeGreaterThanOrEqual(1);
  });
});

describe('spreadSummary', () => {
  const base: RatesSpread = {
    key: '10y2y',
    long: 'DGS10',
    short: 'DGS2',
    valueBp: 52,
    chg1mBp: 4,
    inverted: false,
    invertedDays: 0,
    lastInversion: { start: '2022-07-05', end: '2024-09-03', minBp: -108, minDate: '2023-07-03' },
    pct5y: 88,
  };
  it('describes a normal curve with the last inversion and percentile', () => {
    expect(spreadSummary(base, '10Y−2Y')).toEqual([
      '目前未倒掛（+52bp）。',
      '最近一次倒掛 2022/07–2024/09，最深 -108bp（2023/07/03）。',
      '目前利差位於近 5 年第 88 百分位（0＝最低、100＝最高）。',
    ]);
  });
  it('describes an ongoing inversion', () => {
    const lines = spreadSummary(
      { ...base, valueBp: -35, inverted: true, invertedDays: 120, lastInversion: { start: '2026-06-11', end: null, minBp: -48, minDate: '2026-08-01' }, pct5y: null },
      '10Y−2Y',
    );
    expect(lines[0]).toBe('目前倒掛 -35bp，已持續 120 個交易日（自 2026/06/11 起）。');
    expect(lines[1]).toBe('這段倒掛最深 -48bp（2026/08/01）。');
    expect(lines).toHaveLength(2);
  });
  it('handles no data and no inversion history', () => {
    expect(spreadSummary({ ...base, valueBp: null, lastInversion: null, pct5y: null }, '30Y−10Y')).toEqual([
      '30Y−10Y 目前沒有資料。',
      '資料期間內沒有倒掛紀錄。',
    ]);
  });
});

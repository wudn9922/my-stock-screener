import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseFinancials, type FinancialHistory } from '../src/fundamentals/FinancialHistory';
import {
  DAY_SECONDS,
  INSUFFICIENT_MESSAGE,
  PE_MAX,
  buildEpsPeriods,
  buildPeRiver,
  dateToSec,
  epsAt,
  percentRank,
  percentile,
  publishDelayDays,
  riverLine,
  zoneOf,
  type PeRiverModel,
  type PriceBar,
} from '../src/fundamentals/peRiver';

interface Period {
  end: string;
  eps: number | null;
  fq?: number;
  basic?: number | null;
}
interface Spec {
  market?: 'US' | 'TW';
  currency?: string;
  quarters?: Period[];
  annuals?: Period[];
  split?: { adjusted: boolean; events: [string, number][] };
  issues?: string[];
  foreign?: boolean;
}

/** A financials file in the builder's shape, parsed with the real parser. */
function history(spec: Spec): FinancialHistory {
  const block = (periods: Period[], quarterly: boolean) => ({
    end: periods.map((p) => p.end),
    fy: periods.map((p) => Number(p.end.slice(0, 4))),
    fq: periods.map((p) => (quarterly ? (p.fq ?? null) : null)),
    values: {
      epsDiluted: periods.map((p) => p.eps),
      epsBasic: periods.map((p) => (p.basic === undefined ? p.eps : p.basic)),
    },
  });
  const market = spec.market ?? 'US';
  const result = parseFinancials({
    version: 1,
    symbol: market === 'US' ? 'TEST' : '9999.TW',
    market,
    currency: spec.currency ?? (market === 'US' ? 'USD' : 'TWD'),
    foreignReporter: spec.foreign ?? false,
    annual: block(spec.annuals ?? [], false),
    quarterly: block(spec.quarters ?? [], true),
    split: spec.split ?? { adjusted: false, events: [] },
    quality: { issues: spec.issues ?? [] },
  });
  if (result.status !== 'ok') throw new Error(result.status);
  return result.data;
}

/** Eight consecutive calendar quarters 2023Q1..2024Q4 with EPS 1.0 each (TTM 4.0), end-of-quarter dates. */
const QUARTER_ENDS = ['2023-03-31', '2023-06-30', '2023-09-30', '2023-12-31', '2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31'];
const flatQuarters = (eps = 1): Period[] => QUARTER_ENDS.map((end, i) => ({ end, fq: (i % 4) + 1, eps }));

/** Weekly bars (Monday 00:00 UTC) between two dates with a price function of the bar's date. */
function weekly(from: string, to: string, price: (day: string) => number): PriceBar[] {
  const bars: PriceBar[] = [];
  for (let t = dateToSec(from); t <= dateToSec(to); t += 7 * DAY_SECONDS) {
    bars.push({ time: t, close: price(new Date(t * 1000).toISOString().slice(0, 10)) });
  }
  return bars;
}
const asOf = (day: string) => dateToSec(day);
const okModel = (result: ReturnType<typeof buildPeRiver>): PeRiverModel => {
  if (result.status !== 'ok') throw new Error(`${result.status}: ${result.message}`);
  return result.model;
};
const opts = (day: string) => ({ asOfSec: asOf(day), priceBasis: 'split-adjusted' as const });

describe('publish delay constants', () => {
  it('uses the filing deadlines per market and period kind', () => {
    expect(publishDelayDays('US', 'quarter', 1)).toBe(45);
    expect(publishDelayDays('US', 'quarter', 3)).toBe(45);
    expect(publishDelayDays('US', 'quarter', 4)).toBe(60);
    expect(publishDelayDays('US', 'annual', null)).toBe(60);
    expect(publishDelayDays('TW', 'quarter', 1)).toBe(45);
    expect(publishDelayDays('TW', 'quarter', 2)).toBe(60);
    expect(publishDelayDays('TW', 'quarter', 4)).toBe(90);
    expect(publishDelayDays('TW', 'annual', null)).toBe(90);
    // A quarter whose end matches an annual period is the fiscal year-end quarter even without fq.
    expect(publishDelayDays('US', 'quarter', null, true)).toBe(60);
    expect(publishDelayDays('US', 'quarter', null)).toBe(45);
  });
});

describe('percentile / percentRank', () => {
  const sorted = [1, 2, 3, 4, 5];
  it('interpolates linearly like numpy', () => {
    expect(percentile(sorted, 0)).toBe(1);
    expect(percentile(sorted, 50)).toBe(3);
    expect(percentile(sorted, 100)).toBe(5);
    expect(percentile(sorted, 10)).toBeCloseTo(1.4, 10);
    expect(percentile(sorted, 90)).toBeCloseTo(4.6, 10);
    expect(percentile([7], 30)).toBe(7);
    expect(percentile([], 50)).toBeNaN();
  });
  it('uses mid-rank for ties', () => {
    expect(percentRank(sorted, 3)).toBeCloseTo(50, 10);
    expect(percentRank(sorted, 0)).toBe(0);
    expect(percentRank(sorted, 6)).toBe(100);
    expect(percentRank([2, 2, 2, 2], 2)).toBe(50);
    expect(percentRank([], 1)).toBeNaN();
  });
  it('maps a rank to the zone between the river lines', () => {
    expect([5, 10, 29.9, 30, 69.9, 70, 89.9, 90, 100].map(zoneOf)).toEqual([
      'very-low', 'low', 'low', 'middle', 'middle', 'high', 'high', 'very-high', 'very-high',
    ]);
  });
});


describe('epsAt: TTM, fallback and the publish delay', () => {
  it('sums the latest four published quarters', () => {
    const periods = buildEpsPeriods(history({ quarters: flatQuarters() }));
    // Published by 2024-08-20: ends up to 2024-06-30 (+45d = 08-14) → Q3'23..Q2'24.
    expect(epsAt(dateToSec('2024-08-20'), periods)).toMatchObject({ eps: 4, basis: 'ttm', periodEnd: '2024-06-30', usedBasic: false });
  });

  it('does not use a quarter before end + delay (no look-ahead)', () => {
    const periods = buildEpsPeriods(
      history({ quarters: QUARTER_ENDS.map((end, i) => ({ end, fq: (i % 4) + 1, eps: end === '2024-06-30' ? 11 : 1 })) }),
    );
    // Q2'24 ends 06-30, published 08-14: the day before it, the older TTM (4.0) still applies.
    expect(epsAt(dateToSec('2024-08-13'), periods)).toMatchObject({ eps: 4, periodEnd: '2024-03-31' });
    // On the publish day the new quarter is in: 1 + 1 + 1 + 11.
    expect(epsAt(dateToSec('2024-08-14'), periods)).toMatchObject({ eps: 14, periodEnd: '2024-06-30' });
  });

  it('waits 60 days for a US fiscal Q4 and 90 for a Taiwan one', () => {
    const quarters = QUARTER_ENDS.slice(0, 4).map((end, i) => ({ end, fq: i + 1, eps: end === '2023-12-31' ? 9 : 1 }));
    const us = buildEpsPeriods(history({ quarters }));
    // Q4'23 ends 12-31; +60 days = 2024-02-29 (leap year). Three quarters before that: no TTM.
    expect(epsAt(dateToSec('2024-02-28'), us)).toBeNull();
    expect(epsAt(dateToSec('2024-02-29'), us)).toMatchObject({ eps: 12, periodEnd: '2023-12-31' });
    const tw = buildEpsPeriods(history({ market: 'TW', quarters }));
    // Taiwan: Q2 +60 (08-29), Q4 +90 (2024-03-30).
    expect(epsAt(dateToSec('2024-03-29'), tw)).toBeNull();
    expect(epsAt(dateToSec('2024-03-30'), tw)).toMatchObject({ eps: 12 });
    expect(tw.quarters[1]!.availSec).toBe(dateToSec('2023-08-29'));
  });

  it('returns null when nothing is published yet', () => {
    expect(epsAt(dateToSec('2023-01-01'), buildEpsPeriods(history({ quarters: flatQuarters() })))).toBeNull();
  });

  it('falls back to the latest published annual EPS when fewer than four quarters exist', () => {
    const periods = buildEpsPeriods(
      history({
        quarters: flatQuarters().slice(0, 3),
        annuals: [
          { end: '2022-12-31', eps: 3 },
          { end: '2023-12-31', eps: 3.5 },
        ],
      }),
    );
    expect(epsAt(dateToSec('2023-02-28'), periods)).toBeNull(); // 2022 annual is published 03-01
    expect(epsAt(dateToSec('2023-12-01'), periods)).toMatchObject({ eps: 3, basis: 'annual', periodEnd: '2022-12-31' });
    // 2023 annual: 12-31 + 60 = 2024-02-29.
    expect(epsAt(dateToSec('2024-02-28'), periods)).toMatchObject({ eps: 3 });
    expect(epsAt(dateToSec('2024-02-29'), periods)).toMatchObject({ eps: 3.5, basis: 'annual' });
  });

  it('prefers the quarterly TTM over the annual figure once four quarters are published', () => {
    const periods = buildEpsPeriods(history({ quarters: flatQuarters(), annuals: [{ end: '2023-12-31', eps: 3.5 }] }));
    expect(epsAt(dateToSec('2024-03-20'), periods)).toMatchObject({ eps: 4, basis: 'ttm' });
  });

  it('a missing quarter inside the window breaks the TTM and the annual figure takes over', () => {
    const quarters = flatQuarters().map((q) => (q.end === '2024-03-31' ? { ...q, eps: null, basic: null } : q));
    const annuals = [{ end: '2023-12-31', eps: 3.5 }];
    const periods = buildEpsPeriods(history({ quarters, annuals }));
    // The latest four published quarters at 2024-09-01 are Q4'23..Q2'24 plus Q3'23: the window holds the null Q1'24.
    expect(epsAt(dateToSec('2024-09-01'), periods)).toMatchObject({ basis: 'annual', eps: 3.5 });
    // Four clean quarters again once the null one has scrolled out of the window.
    const later = buildEpsPeriods(
      history({
        quarters: [...quarters, { end: '2025-03-31', fq: 1, eps: 1 }, { end: '2025-06-30', fq: 2, eps: 1 }, { end: '2025-09-30', fq: 3, eps: 1 }],
        annuals,
      }),
    );
    expect(epsAt(dateToSec('2025-11-20'), later)).toMatchObject({ basis: 'ttm', eps: 4, periodEnd: '2025-09-30' });
  });

  it('a gap between non-adjacent quarters is not summed as one year', () => {
    // Q1'23, Q2'23, Q1'24, Q2'24: four published quarters but not consecutive.
    const all = flatQuarters();
    const periods = buildEpsPeriods(history({ quarters: [all[0]!, all[1]!, all[4]!, all[5]!] }));
    expect(epsAt(dateToSec('2024-09-01'), periods)).toBeNull();
  });

  it('stops using stale figures', () => {
    const annualOnly = buildEpsPeriods(history({ annuals: [{ end: '2020-12-31', eps: 3 }] }));
    expect(epsAt(dateToSec('2021-06-01'), annualOnly)).toMatchObject({ eps: 3, basis: 'annual' });
    expect(epsAt(dateToSec('2022-09-01'), annualOnly)).toBeNull();
    const quartersOnly = buildEpsPeriods(history({ quarters: flatQuarters() }));
    expect(epsAt(dateToSec('2025-01-20'), quartersOnly)).toMatchObject({ basis: 'ttm' });
    expect(epsAt(dateToSec('2025-09-01'), quartersOnly)).toBeNull();
  });

  it('uses basic EPS only for periods that have no diluted EPS and says so', () => {
    const quarters = flatQuarters().map((q) => (q.end === '2024-06-30' ? { ...q, eps: null, basic: 1.5 } : q));
    const periods = buildEpsPeriods(history({ quarters }));
    expect(epsAt(dateToSec('2024-08-20'), periods)).toMatchObject({ eps: 4.5, usedBasic: true });
  });

  it('ignores cumulative-looking quarters and uses annual figures', () => {
    const periods = buildEpsPeriods(
      history({ quarters: flatQuarters(), annuals: [{ end: '2023-12-31', eps: 3.5 }], issues: ['quarterly-ytd-suspected'] }),
    );
    expect(periods.quarters).toHaveLength(0);
    expect(periods.notes.join('')).toContain('年度 EPS');
    expect(epsAt(dateToSec('2024-09-01'), periods)).toMatchObject({ basis: 'annual', eps: 3.5 });
  });
});

describe('splits', () => {
  it('keeps everything when the file says it is split-adjusted, even with events', () => {
    const periods = buildEpsPeriods(history({ quarters: flatQuarters(), split: { adjusted: true, events: [['2024-05-01', 4]] } }));
    expect(periods.quarters).toHaveLength(8);
    expect(periods.notes).toEqual([]);
  });
  it('keeps everything when an unadjusted file has no events or only events before its history', () => {
    expect(buildEpsPeriods(history({ quarters: flatQuarters() })).quarters).toHaveLength(8);
    const old = buildEpsPeriods(history({ quarters: flatQuarters(), split: { adjusted: false, events: [['2005-01-01', 2]] } }));
    expect(old.quarters).toHaveLength(8);
    expect(old.notes).toEqual([]);
  });
  it('drops periods that end before the last split of an unadjusted file', () => {
    const periods = buildEpsPeriods(
      history({
        quarters: flatQuarters(),
        annuals: [
          { end: '2023-12-31', eps: 4 },
          { end: '2024-12-31', eps: 4 },
        ],
        split: { adjusted: false, events: [['2024-05-01', 2]] },
      }),
    );
    expect(periods.quarters.map((q) => q.end)).toEqual(['2024-06-30', '2024-09-30', '2024-12-31']);
    expect(periods.annuals.map((a) => a.end)).toEqual(['2024-12-31']);
    expect(periods.notes[0]).toContain('2024-05-01');
  });
  it('is unavailable when no EPS survives the split cut', () => {
    const result = buildPeRiver(
      history({ quarters: flatQuarters(), split: { adjusted: false, events: [['2025-01-01', 2]] } }),
      weekly('2023-01-02', '2025-03-31', () => 40),
      opts('2025-04-02'),
    );
    expect(result).toMatchObject({ status: 'unavailable', reason: 'no-eps' });
  });
});

describe('buildPeRiver', () => {
  // Weekly bars from Monday 2023-01-02; with flat EPS 1 per quarter the first TTM is published 2024-02-29,
  // so the first valid bar is Monday 2024-02-26 (judged on its Friday, 03-01) and the last is 2025-03-31.
  const ramp = (day: string) => Math.max(1, 4 * (10 + (dateToSec(day) - dateToSec('2024-02-26')) / (7 * DAY_SECONDS)));
  const model = () => okModel(buildPeRiver(history({ quarters: flatQuarters() }), weekly('2023-01-02', '2025-03-31', ramp), opts('2025-04-02')));

  it('counts valid points and ignores weeks before the first published TTM', () => {
    const m = model();
    expect(m.points).toHaveLength(118); // Mondays 2023-01-02 .. 2025-03-31
    expect(m.validCount).toBe(58);
    expect(m.excluded['no-eps']).toBe(60);
    expect(m.points.filter((p) => p.pe === null).every((p) => p.time < dateToSec('2024-02-26'))).toBe(true);
    expect(m.basisCounts).toEqual({ ttm: 58, annual: 0 });
    expect(m.epsPeriodCount).toBe(5);
    expect(m.coverageYears).toBeCloseTo(399 / 365.25, 6);
    expect(m.priceYears).toBeCloseTo((dateToSec('2025-03-31') - dateToSec('2023-01-02')) / (365.25 * DAY_SECONDS), 6);
  });

  it('sets the river lines at the percentiles of the valid P/E series', () => {
    const m = model();
    // P/E runs 10, 11, …, 67 (58 points): linear percentiles.
    expect(m.levels.map((l) => l.p)).toEqual([10, 30, 50, 70, 90]);
    expect(m.levels[0]!.multiple).toBeCloseTo(15.7, 9);
    expect(m.levels[1]!.multiple).toBeCloseTo(27.1, 9);
    expect(m.levels[2]!.multiple).toBeCloseTo(38.5, 9);
    expect(m.levels[3]!.multiple).toBeCloseTo(49.9, 9);
    expect(m.levels[4]!.multiple).toBeCloseTo(61.3, 9);
  });

  it('reports the current P/E, its percent rank and the premium to the median', () => {
    const { current } = model();
    expect(current).not.toBeNull();
    expect(current!.pe).toBeCloseTo(67, 9);
    expect(current!.percentile).toBeCloseTo((57.5 / 58) * 100, 9);
    expect(current!.median).toBeCloseTo(38.5, 9);
    expect(current!.premiumPct).toBeCloseTo((67 / 38.5 - 1) * 100, 9);
    expect(current).toMatchObject({ eps: 4, basis: 'ttm', epsPeriodEnd: '2024-12-31', close: 268 });
  });

  it('river lines are EPS × multiple and break exactly where the P/E is not valid', () => {
    const m = model();
    const line = riverLine(m, m.levels[2]!);
    expect(line).toHaveLength(m.points.length);
    m.points.forEach((point, i) => {
      if (point.pe === null) expect(line[i]).toBeNull();
      else expect(line[i]).toBeCloseTo(4 * 38.5, 9);
    });
  });

  it('flags a short history', () => {
    expect(model().short).toBe(true);
  });

  it('judges the in-progress bar at the data date, not at its own Friday', () => {
    // Q4'24 is published 2025-03-01. A bar starting Monday 02-24 would be judged on 02-28 (before it);
    // a bar starting 03-03 judged on min(03-07, asOf).
    const withAsOf = (last: string, asOfDay: string) =>
      okModel(buildPeRiver(history({ quarters: flatQuarters() }), weekly('2023-01-02', last, ramp), opts(asOfDay)));
    expect(withAsOf('2025-02-24', '2025-02-28').current!.epsPeriodEnd).toBe('2024-09-30');
    expect(withAsOf('2025-03-03', '2025-03-06').current!.epsPeriodEnd).toBe('2024-12-31');
    // Same bar, but the data date is the Friday before the release: Q4'24 stays out.
    expect(withAsOf('2025-03-03', '2025-02-28').current!.epsPeriodEnd).toBe('2024-09-30');
  });

  it('a later EPS release does not change earlier P/E (no look-ahead)', () => {
    const run = (q3: number) =>
      okModel(
        buildPeRiver(
          history({ quarters: QUARTER_ENDS.map((end, i) => ({ end, fq: (i % 4) + 1, eps: end === '2024-09-30' ? q3 : 1 })) }),
          weekly('2023-01-02', '2024-12-30', () => 40),
          opts('2025-01-01'),
        ),
      );
    const a = run(1);
    const b = run(50);
    // Q3'24 is published 2024-11-14; every bar judged before that is identical.
    const cutoff = dateToSec('2024-11-14') - 4 * DAY_SECONDS;
    const before = (m: PeRiverModel) => m.points.filter((p) => p.time < cutoff).map((p) => p.pe);
    expect(before(b)).toEqual(before(a));
    expect(b.points.at(-1)!.pe).not.toBe(a.points.at(-1)!.pe);
  });

  it('excludes loss periods from the statistics and breaks the river there', () => {
    const quarters = QUARTER_ENDS.map((end, i) => ({ end, fq: (i % 4) + 1, eps: end >= '2024-06-30' ? -1 : 1 }));
    const m = okModel(buildPeRiver(history({ quarters }), weekly('2023-01-02', '2025-03-31', () => 40), opts('2025-04-02')));
    // TTM 4 (P/E 10) from 02-29, 2 (P/E 20) from Q2'24 on 08-14, then 0 / negative from 11-14.
    expect(m.validCount).toBe(37);
    expect(m.excluded.loss).toBe(21);
    expect(Math.min(...m.levels.map((l) => l.multiple))).toBeGreaterThanOrEqual(10);
    expect(Math.max(...m.levels.map((l) => l.multiple))).toBeLessThanOrEqual(20);
    expect(m.current).toBeNull();
    expect(m.currentReason).toContain('虧損');
    const line = riverLine(m, m.levels[2]!);
    m.points.forEach((point, i) => {
      if (point.excluded === 'loss') {
        expect(line[i]).toBeNull();
        expect(point.pe).toBeNull();
      }
    });
  });

  it('drops P/E above the outlier limit from statistics and river', () => {
    const quarters = QUARTER_ENDS.map((end, i) => ({ end, fq: (i % 4) + 1, eps: i < 4 ? 1 : 0.01 }));
    const m = okModel(buildPeRiver(history({ quarters }), weekly('2023-01-02', '2025-03-31', () => 40), opts('2025-04-02')));
    expect(m.excluded.outlier).toBeGreaterThan(0);
    expect(Math.max(...m.levels.map((l) => l.multiple))).toBeLessThanOrEqual(PE_MAX);
    for (const point of m.points) if (point.excluded === 'outlier') expect(point.pe).toBeNull();
    // Q4'24 (published 2025-03-01) makes TTM 0.04: price 40 → P/E 1000.
    expect(m.current).toBeNull();
    expect(m.currentReason).toContain(String(PE_MAX));
  });

  it('shows the insufficient-history message below eight valid points', () => {
    const seven = buildPeRiver(history({ quarters: flatQuarters() }), weekly('2024-02-26', '2024-04-08', () => 40), opts('2024-04-10'));
    expect(seven).toMatchObject({ status: 'unavailable', reason: 'insufficient', message: INSUFFICIENT_MESSAGE, validCount: 7 });
  });

  it('needs at least three distinct EPS figures, however many weeks there are', () => {
    // 8+ valid weeks but only the 2023-12-31 TTM behind them: the P/E history is just the price path.
    const one = buildPeRiver(history({ quarters: flatQuarters() }), weekly('2024-02-26', '2024-04-15', () => 40), opts('2024-04-17'));
    expect(one).toMatchObject({ status: 'unavailable', reason: 'insufficient', validCount: 8, epsPeriodCount: 1 });
    // Q1'24 (05-15) and Q2'24 (08-14) add two more.
    const three = buildPeRiver(history({ quarters: flatQuarters() }), weekly('2024-02-26', '2024-08-19', () => 40), opts('2024-08-21'));
    expect(three.status).toBe('ok');
    expect(okModel(three).epsPeriodCount).toBe(3);
  });

  it('mixes annual and quarterly EPS and counts each', () => {
    const m = okModel(
      buildPeRiver(
        history({
          quarters: flatQuarters(),
          annuals: [
            { end: '2022-12-31', eps: 3 },
            { end: '2023-12-31', eps: 3.5 },
          ],
        }),
        weekly('2023-01-02', '2025-03-31', () => 40),
        opts('2025-04-02'),
      ),
    );
    expect(m.basisCounts.annual).toBeGreaterThan(0);
    expect(m.points[10]!.basis).toBe('annual');
    expect(m.points.at(-1)!.basis).toBe('ttm');
    expect(m.basisCounts.annual + m.basisCounts.ttm).toBe(m.validCount);
  });

  it('draws an annual-only river (Taiwan-style short quarterly history) and marks it short', () => {
    const m = okModel(
      buildPeRiver(
        history({
          market: 'TW',
          annuals: [
            { end: '2022-12-31', eps: 39 },
            { end: '2023-12-31', eps: 32 },
            { end: '2024-12-31', eps: 45 },
            { end: '2025-12-31', eps: 66 },
          ],
        }),
        weekly('2016-01-04', '2026-10-05', () => 1000),
        opts('2026-10-08'),
      ),
    );
    expect(m.basisCounts.ttm).toBe(0);
    expect(m.basisCounts.annual).toBe(m.validCount);
    expect(m.epsPeriodCount).toBe(4);
    expect(m.short).toBe(true);
    expect(m.coverageYears).toBeLessThan(4);
    expect(m.priceYears).toBeGreaterThan(10);
  });

  it('refuses to draw when price basis, issuer type, currency or bars are not trustworthy', () => {
    const bars = weekly('2023-01-02', '2025-03-31', () => 40);
    const h = history({ quarters: flatQuarters() });
    const now = asOf('2025-04-02');
    expect(buildPeRiver(h, bars, { asOfSec: now, priceBasis: 'unknown' })).toMatchObject({ reason: 'price-basis' });
    expect(buildPeRiver(h, bars, { asOfSec: now, priceBasis: undefined })).toMatchObject({ reason: 'price-basis' });
    expect(buildPeRiver(h, bars, { asOfSec: now, priceBasis: 'unadjusted' })).toMatchObject({ reason: 'price-basis' });
    expect(buildPeRiver(history({ quarters: flatQuarters(), foreign: true }), bars, opts('2025-04-02'))).toMatchObject({ reason: 'foreign' });
    expect(buildPeRiver(history({ quarters: flatQuarters(), issues: ['foreign-filer'] }), bars, opts('2025-04-02'))).toMatchObject({ reason: 'foreign' });
    expect(buildPeRiver(history({ quarters: flatQuarters(), currency: 'EUR' }), bars, opts('2025-04-02'))).toMatchObject({ reason: 'currency' });
    expect(buildPeRiver(history({ market: 'TW', quarters: flatQuarters(), currency: 'USD' }), bars, opts('2025-04-02'))).toMatchObject({ reason: 'currency' });
    expect(buildPeRiver(h, [], opts('2025-04-02'))).toMatchObject({ reason: 'no-bars' });
    expect(buildPeRiver(history({ quarters: [{ end: '2024-03-31', fq: 1, eps: null, basic: null }] }), bars, opts('2025-04-02'))).toMatchObject({ reason: 'no-eps' });
  });

  it('ignores invalid bars', () => {
    const bars = [
      ...weekly('2023-01-02', '2025-03-31', ramp),
      { time: Number.NaN, close: 5 },
      { time: dateToSec('2025-04-07'), close: 0 },
      { time: dateToSec('2025-04-14'), close: -3 },
    ];
    expect(okModel(buildPeRiver(history({ quarters: flatQuarters() }), bars, opts('2025-04-16'))).validCount).toBe(58);
  });
});

describe('with the financials fixtures', () => {
  const fixture = (name: string) => {
    const result = parseFinancials(JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/financials', name), 'utf8')));
    if (result.status !== 'ok') throw new Error(name);
    return result.data;
  };
  it('US (AAPL): annual EPS before the quarterly history starts, TTM after', () => {
    const m = okModel(buildPeRiver(fixture('us-full.json'), weekly('2016-01-04', '2025-09-29', () => 150), opts('2025-10-01')));
    expect(m.basisCounts.annual).toBeGreaterThan(0);
    expect(m.basisCounts.ttm).toBeGreaterThan(0);
    const last = m.points.at(-1)!;
    expect(last.basis).toBe('ttm');
    expect(last.periodEnd).toBe('2025-06-28');
    // Last four quarters in the file: 1.41 + 2.23 + 1.76 + 1.55.
    expect(last.eps).toBeCloseTo(6.95, 9);
  });
  it('Taiwan fixture (TSMC): annual EPS before the ten quarters, TTM after', () => {
    const m = okModel(buildPeRiver(fixture('tsmc-twd.json'), weekly('2016-01-04', '2025-10-06', () => 800), opts('2025-10-08')));
    expect(m.basisCounts.ttm).toBeGreaterThan(0);
    expect(m.basisCounts.annual).toBeGreaterThan(0);
  });
  it('a foreign issuer file is unavailable', () => {
    expect(buildPeRiver(fixture('short-history.json'), weekly('2016-01-04', '2025-10-06', () => 100), opts('2025-10-08'))).toMatchObject({
      status: 'unavailable',
      reason: 'foreign',
    });
  });
  it('does not label already rescaled ADR EPS as ordinary-share EPS', () => {
    const result = buildPeRiver(fixture('adr-rescaled.json'), weekly('2016-01-04', '2025-10-06', () => 100), opts('2025-10-08'));
    expect(result).toMatchObject({ status: 'unavailable', reason: 'foreign' });
    if (result.status === 'unavailable') {
      expect(result.message).toContain('EPS 已按每 ADR 換算');
      expect(result.message).not.toContain('EPS 為每普通股');
    }
  });
});

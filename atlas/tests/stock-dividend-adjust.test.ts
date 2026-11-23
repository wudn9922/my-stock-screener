import { describe, expect, it } from 'vitest';
import { parseFinancials } from '../src/fundamentals/FinancialHistory';
import { epsAdjusted, eps, contextOf, comparableValues, metricSeries } from '../src/fundamentals/financialMetrics';
import { epsAt, buildEpsPeriods, dateToSec } from '../src/fundamentals/peRiver';
import { adjustEps, dividendFactor, reportDelayDays } from '../src/fundamentals/stockDividendAdjust';

const A = { exDate: '2025-08-20', perShare: 1 }; // +10% shares
const B = { exDate: '2026-08-19', perShare: 2 }; // +20% shares

function ok(result: ReturnType<typeof parseFinancials>) {
  if (result.status !== 'ok') throw new Error(result.status);
  return result.data;
}

function file(events: unknown, market = 'TW') {
  const ends = ['2024-12-31', '2025-12-31', '2026-03-31'];
  return ok(parseFinancials({
    version: 1,
    symbol: '1773.TW',
    market,
    currency: 'TWD',
    annual: { end: ends.slice(0, 2), fy: [2024, 2025], fq: [null, null], src: ['finmind', 'finmind'], values: { epsBasic: [6, 6.6] } },
    quarterly: { end: ends, fy: [2024, 2025, 2026], fq: [4, 4, 1], src: ['finmind', 'finmind', 'finmind'], values: { epsBasic: [1.5, 1.65, 1.8] } },
    split: { adjusted: false, events: [] },
    ...(events === undefined ? {} : { stockDividends: events }),
    quality: { level: 'good', issues: [] },
  }));
}

describe('dividendFactor', () => {
  it('multiplies every stock dividend after the report was due', () => {
    expect(reportDelayDays('2024-12-31')).toBe(90);
    expect(dividendFactor('2024-12-31', [A, B])).toEqual({ factor: 1.32, events: [A, B] });
    expect(dividendFactor('2025-12-31', [A, B])?.factor).toBe(1.2);
    expect(dividendFactor('2026-03-31', [A, B])?.factor).toBe(1.2);
  });
  it('ignores a dividend already in the report (ex-rights before the due date)', () => {
    expect(dividendFactor('2025-06-30', [A])?.factor).toBe(1); // due 2025-08-29, ex 2025-08-20
    expect(dividendFactor('2025-03-31', [A])?.factor).toBe(1.1);
  });
  it('cash-only or no events → factor 1; missing data → null', () => {
    expect(dividendFactor('2024-12-31', [])?.factor).toBe(1);
    expect(dividendFactor('2024-12-31', null)).toBeNull();
    expect(adjustEps(5, '2024-12-31', null)).toBeNull();
    expect(adjustEps(null, '2024-12-31', [A])).toBeNull();
    expect(adjustEps(6, '2024-12-31', [A, B])).toBe(4.5455);
  });
  it('as-of restricts to the basis of an observation day', () => {
    expect(dividendFactor('2024-12-31', [A, B], '2025-12-01')?.factor).toBe(1.1);
  });
});

describe('metrics', () => {
  it('shows the restated EPS (reported kept alongside); 公告 shows the reported one; YoY uses the restated values', () => {
    const history = file({ events: [A, B] });
    const ctx = contextOf(history, 'annual');
    expect(eps(ctx, 0).value).toBe(4.5455);
    expect(eps(ctx, 0).reported).toBe(6);
    expect(eps(ctx, 0).basis).toBe('restated');
    expect(eps(contextOf(history, 'annual', 'reported'), 0).value).toBe(6);
    expect(eps(contextOf(history, 'annual', 'reported'), 0).comparable).toBe(4.5455);
    expect(eps(ctx, 0).comparable).toBe(4.5455);
    expect(epsAdjusted(ctx, 0).value).toBe(4.5455);
    expect(epsAdjusted(ctx, 0).notes.join()).toContain('配股 1 元');
    // reported YoY would be +10%; restated 4.5455 → 5.5 is +21%
    expect(comparableValues(metricSeries(ctx, 'eps'))[1]).toBe(5.5);
  });
  it('no stockDividends block → restated EPS null, reported untouched', () => {
    const ctx = contextOf(file(undefined), 'annual');
    expect(epsAdjusted(ctx, 0).value).toBeNull();
    expect(epsAdjusted(ctx, 0).reason).toContain('缺配股資料');
    expect(eps(ctx, 0).value).toBe(6);
    expect(eps(ctx, 0).comparable).toBeNull();
  });
});

describe('P/E river share basis', () => {
  it('TTM across an ex-rights date is put on the observation-day basis', () => {
    const history = ok(parseFinancials({
      version: 1, symbol: '1773.TW', market: 'TW', currency: 'TWD',
      annual: { end: [], fy: [], fq: [], src: [], values: {} },
      quarterly: {
        end: ['2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31'], fy: [2025, 2025, 2025, 2025], fq: [1, 2, 3, 4],
        src: ['finmind', 'finmind', 'finmind', 'finmind'], values: { epsBasic: [1.1, 1.1, 1.0, 1.0] },
      },
      split: { adjusted: false, events: [] },
      stockDividends: { events: [{ exDate: '2025-08-20', perShare: 1 }] },
      quality: { level: 'good', issues: [] },
    }));
    const periods = buildEpsPeriods(history);
    const point = epsAt(dateToSec('2026-04-15'), periods)!;
    // Q1 reported before the ex date (÷1.1); Q2 (due 08-29) already new basis; Q3, Q4 new basis
    expect(point.eps).toBeCloseTo(1.0 + 1.1 + 1.0 + 1.0, 6);
    const noEvents = epsAt(dateToSec('2026-04-15'), { ...periods, stockDividends: null })!;
    expect(noEvents.eps).toBeCloseTo(4.2, 6);
  });
});

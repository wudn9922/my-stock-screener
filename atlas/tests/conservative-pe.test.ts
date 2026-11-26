import { describe, expect, it } from 'vitest';
import { assessEligibility, conservativeEps, conservativePeClose, conservativePePriceIssue, epsGradeUsable, extrapolateGrowth, forecastInertia, type EpsObservation } from '../src/fundamentals/conservativePe';
import { parseFinancials } from '../src/fundamentals/FinancialHistory';
import type { BarResult } from '../src/market-data/MarketDataProvider';
import adr from './fixtures/financials/adr-rescaled.json';
import split from './fixtures/financials/tw-8422-split.json';
import us from './fixtures/financials/us-full.json';

const years = (values: number[]): EpsObservation[] => values.map((eps, i) => ({ end: `${2000 + i}-12-31`, period: 2000 + i, eps }));
function history(fixture: unknown) {
  const parsed = parseFinancials(fixture);
  if (parsed.status !== 'ok') throw new Error(parsed.message);
  return parsed.data;
}

describe('conservative PE inertia', () => {
  it('computes speed and acceleration in percentage points', () => {
    expect(extrapolateGrowth([10, 20, 30])).toMatchObject({ velocity: 10, acceleration: 0, nextGrowth: 40 });
    expect(extrapolateGrowth([10, 20, 40])).toMatchObject({ velocity: 20, acceleration: 10, nextGrowth: 70 });
    expect(extrapolateGrowth([-30, -20, -15]).nextGrowth).toBe(-15);
    expect(extrapolateGrowth([-20, -10, 10]).nextGrowth).toBe(40);
    expect(extrapolateGrowth([0, 0, 40])).toMatchObject({ nextGrowth: 120, excessive: true });
  });
  it('qualifies stable growth or range and rejects insufficient, loss and irregular years', () => {
    expect(assessEligibility(years(Array.from({ length: 10 }, (_, i) => i + 1)))).toMatchObject({ applicable: true, stableGrowth: true });
    expect(assessEligibility(years(Array.from({ length: 10 }, (_, i) => i % 2 ? 10 : 11)))).toMatchObject({ applicable: true, stableRange: true });
    expect(assessEligibility(years([1, 10, 1, 10, 1, 10, 1, 10, 1, 10]))).toMatchObject({ applicable: false });
    expect(assessEligibility(years(Array(9).fill(1)))).toMatchObject({ applicable: false });
    expect(assessEligibility(years([...Array(9).fill(1), -1]))).toMatchObject({ applicable: false });
  });
  it('rejects nonpositive bases and negative projections; does not clip excessive growth', () => {
    expect(forecastInertia(years([0, 1, 2, 3]), 'annual', 100)).toMatchObject({ applicable: false });
    expect(forecastInertia(years([100, 100, 100, 10]), 'annual', 100)).toMatchObject({ applicable: false, inertia: { excessive: true } });
    const result = forecastInertia(years([10, 10, 10, 14]), 'annual', 100);
    expect(result.applicable).toBe(true);
    if (result.applicable) {
      expect(result.inertia.nextGrowth).toBeCloseTo(120);
      expect(result.projectedEps).toBeCloseTo(30.8);
      expect(result.pe).toBeCloseTo(100 / 30.8);
    }
  });
  it('uses seven consecutive quarters and the correct next-year-ago base', () => {
    const rows = [10, 10, 10, 20, 11, 12, 13].map((eps, i) => ({
      eps, period: 2024 * 4 + i, end: `${2024 + Math.floor(i / 4)}-${['03-31', '06-30', '09-30', '12-31'][i % 4]}`,
    }));
    const result = forecastInertia(rows, 'quarterly', 128);
    expect(result.applicable).toBe(true);
    if (result.applicable) {
      expect(result.inertia.nextGrowth).toBeCloseTo(40);
      expect(result.projectedEps).toBeCloseTo(28);
      expect(result.denominator).toBeCloseTo(64);
      expect(result.pe).toBeCloseTo(2);
    }
    expect(forecastInertia(rows.slice(1), 'quarterly', 128).applicable).toBe(false);
  });
  it('uses the same low-confidence EPS grade exclusions as growth', () => {
    for (const grade of ['B', 'C', 'D']) expect(epsGradeUsable(grade, true)).toBe(false);
    for (const grade of ['A', 'Y', undefined]) expect(epsGradeUsable(grade, true)).toBe(true);
    expect(epsGradeUsable('other', true)).toBe(false);
  });
  it('preserves low-confidence and cumulative-quarter slots and uses a qualified basic EPS fallback', () => {
    const data = history({ ...us, quarterly: { ...us.quarterly, epsQuality: [
      { epsDiluted: 'C', epsBasic: 'A' }, { epsDiluted: 'B', epsBasic: 'D' },
    ] } });
    const rows = conservativeEps(data, 'quarterly');
    expect(rows[0]!.eps).toBe(data.quarterly.values.epsBasic[0]);
    expect(rows[1]).toMatchObject({ end: data.quarterly.end[1], eps: null });
    data.quality.issues.push('quarterly-ytd-suspected');
    expect(conservativeEps(data, 'quarterly').every(row => row.eps === null)).toBe(true);
  });
  it('uses the Taiwan split-adjusted history including Q4 recomputed on one share basis', () => {
    const data = history(split);
    expect(conservativeEps(data, 'annual').find(row => row.end === '2024-12-31')?.eps).toBeCloseTo(1.07);
    expect(conservativeEps(data, 'quarterly').find(row => row.end === '2025-12-31')?.eps).toBeCloseTo(0.242, 3);
    expect(conservativeEps({ ...data, stockDividends: null }, 'annual').every(row => row.eps === null)).toBe(true);
  });
  it('rejects an annual gap even when the fiscal labels are still consecutive', () => {
    const rows = years(Array(10).fill(10));
    rows[4]!.end = '2004-06-30';
    expect(assessEligibility(rows)).toMatchObject({ applicable: false, reason: '年度財報期別不連續或不明' });
  });
  it('requires matching reporting currency and a documented ADR share basis', () => {
    const data = history(adr);
    expect(conservativeEps(data, 'annual').at(-1)?.eps).toBe(62);
    expect(conservativePePriceIssue(data)).toContain('財報幣別（TWD）與股價幣別（USD）不同');
    expect(conservativePePriceIssue({ ...data, currency: 'USD' })).toBeNull();
    expect(conservativePePriceIssue({ ...data, currency: 'USD', foreignReporter: false, adrRatio: null,
      quality: { ...data.quality, issues: ['foreign-filer'] } })).toContain('EPS 股數基準不明');
  });
  it('dates US archive buckets correctly and excludes a cached intraday bar', () => {
    const seconds = (date: string) => Date.parse(date) / 1000;
    const bar = (date: string, close: number) => ({ time: seconds(date), open: close, high: close, low: close, close, volume: 1 });
    const result: BarResult = {
      bars: [bar('2026-11-23', 100), bar('2026-11-24T14:30:00Z', 110)],
      source: '封存日 K', session: 'regular', delayed: true, adjusted: true, priceBasis: 'split-adjusted',
      asOf: seconds('2026-11-24T19:00:00Z'), cacheStatus: 'stale',
    };
    const now = seconds('2026-11-25T00:00:00Z');
    expect(conservativePeClose('AAPL', result, now)).toEqual({ value: 100, date: '2026-11-23', currency: 'USD', source: '封存日 K', stale: true });
    expect(conservativePeClose('AAPL', { ...result, asOf: now }, now)).toMatchObject({ value: 110, date: '2026-11-24' });
    expect(conservativePeClose('AAPL', { ...result, dataState: 'simulated' }, now)).toBeNull();
  });
});

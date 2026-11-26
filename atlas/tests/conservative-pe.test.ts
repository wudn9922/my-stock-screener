import { describe, expect, it } from 'vitest';
import { assessEligibility, epsGradeUsable, extrapolateGrowth, forecastInertia, type EpsObservation } from '../src/fundamentals/conservativePe';

const years = (values: number[]): EpsObservation[] => values.map((eps, i) => ({ end: `${2000 + i}-12-31`, period: 2000 + i, eps }));
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
  });
});

import type { FinancialHistory, PeriodKind } from './FinancialHistory';
import { epsAdjustments, reportedEpsAt, restatedEps } from './epsBasis';

export interface EpsObservation {
  end: string;
  period: number | null;
  eps: number | null;
}
export type Eligibility =
  | { applicable: true; years: number; growthShare: number; stableGrowth: boolean; stableRange: boolean; mean: number }
  | { applicable: false; reason: string };
export interface Inertia {
  rates: [number, number, number];
  velocity: number;
  acceleration: number;
  nextGrowth: number;
  excessive: boolean;
}
export type Forecast =
  | { applicable: false; reason: string; inertia?: Inertia; inputs?: EpsObservation[] }
  | { applicable: true; inertia: Inertia; inputs: EpsObservation[]; base: EpsObservation; projectedEps: number; denominator: number; pe: number | null };

const consecutive = (rows: EpsObservation[]) =>
  rows.every((row, i) => row.period !== null && (i === 0 || row.period === rows[i - 1]!.period! + 1));
const valid = (row: EpsObservation): row is EpsObservation & { eps: number } =>
  row.eps !== null && Number.isFinite(row.eps);

/** Same default grading policy as build_growth.py eps_usable (Q4_EPS_STRICT off). */
export function epsGradeUsable(grade: string | undefined, q4: boolean): boolean {
  if (grade === 'B' || grade === 'C' || grade === 'D') return false;
  return !q4 || grade === undefined || grade === 'A' || grade === 'Y';
}

/** Preserve missing and low-confidence slots, never bridge gaps or substitute another quarter. */
export function conservativeEps(history: FinancialHistory, kind: PeriodKind): EpsObservation[] {
  const series = history[kind];
  const adjustment = history.market === 'TW' ? epsAdjustments(history, kind) : null;
  return series.end.map((end, i) => {
    const fy = series.fy[i], fq = series.fq[i];
    const period = fy == null || (kind === 'quarterly' && fq == null) ? null : kind === 'annual' ? fy : fy * 4 + fq! - 1;
    const grade = series.epsQuality?.[i];
    const q4 = kind === 'quarterly' && fq === 4;
    let eps = history.market === 'TW'
      ? restatedEps(reportedEpsAt(series, i), adjustment?.[i])
      : (epsGradeUsable(grade?.epsDiluted, q4) ? series.values.epsDiluted[i] ?? null : null)
        ?? (epsGradeUsable(grade?.epsBasic, q4) ? series.values.epsBasic[i] ?? null : null);
    if (kind === 'quarterly' && history.quality.issues.includes('quarterly-ytd-suspected')) eps = null;
    if (kind === 'annual' && history.market === 'US' && history.quality.issues.includes('eps-annual-summed')) eps = null;
    return { end, period, eps };
  });
}

export function assessEligibility(annual: EpsObservation[]): Eligibility {
  const rows = annual.slice(-15);
  if (rows.length < 10) return { applicable: false, reason: `年度 EPS 年數不足（${rows.length} 年，至少 10 年）` };
  if (!consecutive(rows)) return { applicable: false, reason: '年度財報期別不連續或不明' };
  for (const row of rows) {
    if (!valid(row)) return { applicable: false, reason: `${row.end} EPS 缺值、低信心或無法還原` };
    if (row.eps <= 0) return { applicable: false, reason: `${row.end} EPS 非正值（${row.eps}）` };
  }
  const values = rows.map(row => row.eps!);
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const growthShare = values.slice(1).filter((v, i) => v > values[i]!).length / (values.length - 1);
  const stableGrowth = growthShare >= 0.7;
  const stableRange = values.every(v => v >= mean * 0.8 && v <= mean * 1.2);
  return stableGrowth || stableRange
    ? { applicable: true, years: values.length, growthShare, stableGrowth, stableRange, mean }
    : { applicable: false, reason: `僅 ${(growthShare * 100).toFixed(1)}% 的年份成長，且年度 EPS 超出平均值 ±20%` };
}

/** Inputs and outputs are percentage points, not decimal fractions. */
export function extrapolateGrowth(rates: [number, number, number]): Inertia {
  const [g1, g2, g3] = rates;
  const velocity = g3 - g2;
  const acceleration = velocity - (g2 - g1);
  const nextGrowth = g3 + velocity + acceleration;
  return { rates, velocity, acceleration, nextGrowth, excessive: Math.abs(nextGrowth) > 100 };
}

export function forecastInertia(observations: EpsObservation[], kind: PeriodKind, price: number | null): Forecast {
  const quarterly = kind === 'quarterly';
  const rows = observations.slice(quarterly ? -7 : -4);
  const bad = (reason: string, inertia?: Inertia): Forecast => ({ applicable: false, reason, inertia, inputs: rows });
  if (rows.length < (quarterly ? 7 : 4)) return bad(quarterly ? '需要最近 7 季 EPS' : '需要最近 4 年 EPS');
  if (!consecutive(rows)) return bad('財報期別不連續或不明');
  const missing = rows.find(row => !valid(row));
  if (missing) return bad(`${missing.end} EPS 缺值、低信心或無法還原`);
  const step = quarterly ? 4 : 1;
  const rates: number[] = [];
  for (let i = 0; i < 3; i++) {
    const base = rows[i]!, current = rows[i + step]!;
    const days = (Date.parse(current.end) - Date.parse(base.end)) / 86400000;
    if (days < 335 || days > 395) return bad('年增率比較期別非相隔一年');
    if (base.eps! <= 0) return bad(`${base.end} 基期 EPS ≤ 0`);
    rates.push((current.eps! / base.eps! - 1) * 100);
  }
  const inertia = extrapolateGrowth([rates[0]!, rates[1]!, rates[2]!]);
  const base = rows[3]!;
  if (base.eps! <= 0) return bad(`${base.end} 預估基期 EPS ≤ 0`, inertia);
  const projectedEps = base.eps! * (1 + inertia.nextGrowth / 100);
  const denominator = quarterly ? projectedEps + rows.slice(-3).reduce((sum, row) => sum + row.eps!, 0) : projectedEps;
  if (!Number.isFinite(projectedEps) || !Number.isFinite(denominator) || projectedEps <= 0 || denominator <= 0)
    return bad('預估 EPS ≤ 0 或數值無效', inertia);
  const pe = price !== null && Number.isFinite(price) && price > 0 ? price / denominator : null;
  return { applicable: true, inertia, inputs: rows, base, projectedEps, denominator, pe };
}

import { z } from 'zod';
import seed from '../../data/yield-band-params.json';

export type BondCountry = 'US' | 'JP';
export const Z80 = 1.2816;
export const yieldParametersSchema = z.object({
  a: z.number().finite(), b: z.number().finite(), sd: z.number().finite().positive(),
  n: z.number().int().min(3), r2: z.number().finite(),
  spread_mean: z.number().finite(), spread_sd: z.number().finite().positive(),
  first: z.string().regex(/^\d{4}-\d{2}-\d{2}$/), last: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  fittedAt: z.string().optional(),
});
export type YieldParameters = z.infer<typeof yieldParametersSchema>;
export interface YieldBandParameters { asof: string; z80: number; US: YieldParameters | null; JP: YieldParameters | null }
export const parseYieldBands = (raw: unknown): YieldBandParameters | null => {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  if (typeof value.asof !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value.asof) || typeof value.z80 !== 'number' || !Number.isFinite(value.z80) || value.z80 <= 0) return null;
  const country = (key: BondCountry) => { const parsed = yieldParametersSchema.safeParse(value[key]); return parsed.success ? parsed.data : null; };
  const US = country('US'), JP = country('JP');
  return US || JP ? { asof: value.asof, z80: value.z80, US, JP } : null;
};
export const suppliedYieldBands = parseYieldBands(seed)!;
export const suppliedJapanQuote = { asOf: seed.JP.y10_now_date, value: seed.JP.y10_now, policy: seed.JP.policy_now };

/** Same percentage units, regression/spread centers and residual/sample deviations as yield_band.js. */
export function bandAt(p: YieldParameters, policy: number, z = Z80, method: 'regression' | 'spread' = 'regression') {
  const center = method === 'spread' ? policy + p.spread_mean : p.a + p.b * policy;
  const sd = method === 'spread' ? p.spread_sd : p.sd;
  if (![policy, center, sd, z].every(Number.isFinite) || sd <= 0 || z <= 0) return null;
  return { lo: center - z * sd, center, hi: center + z * sd, sd };
}
export function bandStatus(z: number, boundary = Z80) {
  if (!Number.isFinite(z)) return '無法計算';
  if (z >= boundary) return '高於區間上緣';
  if (z >= .5) return '區間內偏上';
  if (z > -.5) return '區間中段';
  if (z > -boundary) return '區間內偏下';
  return '低於區間下緣';
}

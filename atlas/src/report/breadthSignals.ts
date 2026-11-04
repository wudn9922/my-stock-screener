import { z } from 'zod';
import {
  breadthUrl,
  finiteOrNull,
  optionalText,
  type BreadthHistory,
} from './breadth';

const number = z.preprocess(finiteOrNull, z.number().finite());
const percentage = number.pipe(z.number().min(0).max(100));
const nullableNumber = z.unknown().optional().transform(finiteOrNull);
const rate = nullableNumber.transform((value) =>
  value !== null && value >= 0 && value <= 1 ? value : null,
);
const date = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/)
  .refine((value) => {
    const parsed = new Date(`${value}T00:00:00Z`);
    return (
      Number.isFinite(parsed.getTime()) &&
      parsed.toISOString().slice(0, 10) === value
    );
  });
const nullableDate = date
  .nullish()
  .catch(null)
  .transform((value) => value ?? null);
export const BREADTH_ZONES = [
  'extreme_oversold',
  'oversold',
  'neutral',
  'overbought',
  'extreme_overbought',
] as const;
export const SIGNAL_STATES = [
  'none',
  'watch',
  'new',
  'active',
  'target',
  'stop',
  'timeout',
] as const;
export type BreadthZone = (typeof BREADTH_ZONES)[number];
export type SignalState = (typeof SIGNAL_STATES)[number];

const reading = z.object({
  pct: percentage,
  percentile: percentage,
  zone: z.enum(BREADTH_ZONES),
});
const thresholds = z
  .object({
    p5: percentage,
    p10: percentage,
    p50: percentage,
    p90: percentage,
    p95: percentage,
  })
  .refine(
    (v) => v.p5 <= v.p10 && v.p10 <= v.p50 && v.p50 <= v.p90 && v.p90 <= v.p95,
  );
const stats = z
  .object({
    trades: nullableNumber,
    hitRate: rate,
    hitRateEarly: rate,
    hitRateLate: rate,
    baseRate: rate,
    avgReturn: nullableNumber,
    medianDays: nullableNumber,
    winnersMaeP90: nullableNumber,
    nearSwingLowRate: rate,
  })
  .catch({
    trades: null,
    hitRate: null,
    hitRateEarly: null,
    hitRateLate: null,
    baseRate: null,
    avgReturn: null,
    medianDays: null,
    winnersMaeP90: null,
    nearSwingLowRate: null,
  });
const trade = z.object({
  signalDate: date,
  entryDate: nullableDate,
  entry: nullableNumber,
  entryRef: nullableNumber,
  stop: nullableNumber,
  target: nullableNumber,
  exitDate: nullableDate,
  exit: nullableNumber,
  daysHeld: nullableNumber,
  maxHold: nullableNumber,
  expires: nullableDate,
  return: nullableNumber,
});
const event = z.object({
  signal: date,
  entry: nullableDate,
  exit: nullableDate,
  outcome: z.enum(['target', 'stop', 'timeout', 'open', 'pending']),
  ret: nullableNumber,
  days: nullableNumber,
});
const index = z
  .object({
    symbol: z.string().trim().min(1),
    name: z.string().trim().min(1),
    swing: number.pipe(z.number().positive().max(1)),
    state: z.enum(SIGNAL_STATES),
    close: nullableNumber,
    drawdown60: nullableNumber,
    breadthOversold: z.boolean().optional().catch(undefined),
    indexPulledBack: z.boolean().optional().catch(undefined),
    triggerPrice: nullableNumber,
    trade: trade.nullish().transform((value) => value ?? null),
    stats,
    recentEvents: z
      .unknown()
      .optional()
      .transform((value) => validEntries(value, event)),
    markers: z
      .unknown()
      .optional()
      .transform((value) => [...new Set(validEntries(value, date))].sort()),
  })
  .refine(
    (value) =>
      !['new', 'active', 'target', 'stop', 'timeout'].includes(value.state) ||
      value.trade !== null,
  );

function validEntries<T>(value: unknown, schema: z.ZodType<T>): T[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const parsed = schema.safeParse(item);
    return parsed.success ? [parsed.data] : [];
  });
}
const history = z
  .object({ dates: z.unknown(), ma20Pct: z.unknown(), ma60Pct: z.unknown() })
  .transform((value): BreadthHistory => {
    const dates = Array.isArray(value.dates) ? value.dates : [];
    const ma20 = Array.isArray(value.ma20Pct) ? value.ma20Pct : [];
    const ma60 = Array.isArray(value.ma60Pct) ? value.ma60Pct : [];
    const points = new Map<string, [number | null, number | null]>();
    dates.forEach((day, i) => {
      if (date.safeParse(day).success)
        points.set(day as string, [
          percentage.safeParse(ma20[i]).data ?? null,
          percentage.safeParse(ma60[i]).data ?? null,
        ]);
    });
    const sorted = [...points].sort(([a], [b]) => a.localeCompare(b));
    return {
      dates: sorted.map(([day]) => day),
      ma20Pct: sorted.map(([, v]) => v[0]),
      ma60Pct: sorted.map(([, v]) => v[1]),
    };
  })
  .optional()
  .catch(undefined);
const us = z.object({
  applicable: z.literal(true),
  asOf: date,
  readings: z.object({ ma20: reading, ma60: reading }),
  thresholds: z.object({ ma20: thresholds, ma60: thresholds }),
  lowSignal: z.object({
    rule: optionalText,
    indices: z.unknown().transform((value) => {
      const seen = new Set<string>();
      return validEntries(value, index).filter((item) => {
        if (seen.has(item.symbol)) return false;
        seen.add(item.symbol);
        return true;
      });
    }),
  }),
  overbought: z
    .object({
      active: z.boolean(),
      note: optionalText,
      dropFirstRate: rate,
      baseRate: rate,
      events: nullableNumber,
    })
    .optional()
    .catch(undefined),
  history,
  calibration: z
    .object({
      generatedAt: optionalText,
      sample: z
        .object({ start: optionalText, end: optionalText })
        .optional()
        .catch(undefined),
    })
    .optional()
    .catch(undefined),
});
const tw = z.object({
  applicable: z.literal(false),
  reason: z.string().trim().min(1),
});
export type BreadthSignalReading = z.infer<typeof reading>;
export type BreadthSignalIndex = z.infer<typeof index>;
export type UsBreadthSignals = z.infer<typeof us>;
export type TwBreadthSignals = z.infer<typeof tw>;
export interface BreadthSignals {
  generatedAt: string | null;
  markets: { us?: UsBreadthSignals; tw?: TwBreadthSignals };
}
export type BreadthSignalsLoadResult =
  | { status: 'ok'; data: BreadthSignals; url: string }
  | { status: 'missing' | 'invalid' | 'error'; url: string };
export const breadthSignalsSchema = z.object({
  version: z.literal(1),
  generatedAt: optionalText,
  markets: z.object({
    us: us.optional().catch(undefined),
    tw: tw.optional().catch(undefined),
  }),
});
export function breadthSignalsUrl(
  base?: string,
  override?: string,
  origin?: string,
): string {
  return new URL('breadth-signals.json', breadthUrl(base, override, origin))
    .href;
}
export function parseBreadthSignals(
  value: unknown,
  url = '',
): BreadthSignalsLoadResult {
  const parsed = breadthSignalsSchema.safeParse(value);
  if (!parsed.success) return { status: 'invalid', url };
  if (!parsed.data.markets.us && !parsed.data.markets.tw)
    return { status: 'missing', url };
  return { status: 'ok', data: parsed.data, url };
}
export async function fetchBreadthSignals(
  url = breadthSignalsUrl(),
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<BreadthSignalsLoadResult> {
  try {
    const response = await fetcher(url, { cache: 'no-cache', signal });
    if (response.status === 404) return { status: 'missing', url };
    if (!response.ok) return { status: 'error', url };
    try {
      return parseBreadthSignals(JSON.parse(await response.text()), url);
    } catch {
      return { status: 'invalid', url };
    }
  } catch {
    return { status: 'error', url };
  }
}
let shared: Promise<BreadthSignalsLoadResult> | null = null;
export function loadBreadthSignals(
  refresh = false,
): Promise<BreadthSignalsLoadResult> {
  if (!shared || refresh) {
    const request = fetchBreadthSignals().then((result) => {
      if (result.status === 'error' && shared === request) shared = null;
      return result;
    });
    shared = request;
  }
  return shared;
}

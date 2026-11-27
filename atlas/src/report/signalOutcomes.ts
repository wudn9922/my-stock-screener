import { z } from 'zod';
import { reportUrl } from './loadReport';

const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const number = z.number().finite().nullable();
const count = z.number().int().nonnegative();
const horizon = z.object({
  mature: count, pending: count, mean: number, median: number,
  winRate: z.number().min(0).max(100).nullable(), benchmarkMean: number,
  insufficient: z.boolean(),
});
const window = z.object({
  samples: count, horizons: z.object({ '1': horizon, '5': horizon, '20': horizon }),
  mfe: number, mae: number,
}).refine(w => Object.values(w.horizons).every(h => h.mature + h.pending === w.samples));
const signal = z.object({
  startDate: day, lastSignalDate: day, capturedSessions: count,
  windows: z.object({ '1': window, '3': window, '6': window }),
});
const market = z.object({
  observedThrough: day, asOf: day, benchmark: z.enum(['^TWII', '^GSPC']),
  signals: z.object({ 'ambush-v1': signal.optional(), 'daily-scan-v1': signal.optional() }),
});
const schema = z.object({
  version: z.literal(1), generatedAt: z.string(), minSample: z.literal(20),
  markets: z.object({ TW: market.optional(), US: market.optional() }),
});
export type SignalOutcomes = z.infer<typeof schema>;
export function parseSignalOutcomes(value: unknown): SignalOutcomes | null {
  const result = schema.safeParse(value);
  return result.success ? result.data : null;
}
export async function fetchSignalOutcomes(signal?: AbortSignal): Promise<SignalOutcomes> {
  const response = await fetch(new URL('signal-outcomes.json', reportUrl()), { signal, cache: 'no-cache' });
  if (!response.ok) throw new Error(response.status === 404 ? '訊號紀錄尚未開始累積。' : '訊號統計暫時無法取得。');
  const result = parseSignalOutcomes(await response.json());
  if (!result) throw new Error('訊號統計格式無法辨識。');
  return result;
}

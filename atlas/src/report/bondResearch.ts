import { z } from 'zod';
import { reportUrl } from './loadReport';
import { createCachedLoader, historyBlockOf, type HistoryBlock, type LoadResult } from './rates';
import { parseYieldBands } from '../rates/yieldBand';

const quote = z.object({ value: z.number().finite(), date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/), source: z.string() });
const japanSchema = z.object({
  version: z.literal(1), asOf: z.string(), generatedAt: z.string(),
  tenYear: quote, policy: quote, missing: z.array(z.string()).default([]),
  weekly: z.unknown().transform(historyBlockOf), daily: z.unknown().transform(historyBlockOf),
});
export type JapanRates = z.infer<typeof japanSchema>;
export function parseJapanRates(value: unknown): JapanRates | null {
  const parsed = japanSchema.safeParse(value);
  if (!parsed.success || parsed.data.asOf !== parsed.data.tenYear.date || parsed.data.policy.date > parsed.data.asOf) return null;
  return parsed.data;
}
async function fetchBond<T>(file: string, label: string, parse: (raw: unknown) => T | null): Promise<LoadResult<T>> {
  const url = new URL(file, reportUrl()).href;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, { cache: 'no-cache', signal: controller.signal });
    if (!response.ok) return { status: response.status === 404 ? 'missing' : 'error', url, message: `${label}${response.status === 404 ? '尚未產生' : '暫時無法讀取'}。` };
    const data = parse(await response.json());
    return data ? { status: 'ok', data, url } : { status: 'invalid', url, message: `${label}格式無法辨識。` };
  } catch { return { status: 'error', url, message: `${label}讀取失敗，請稍後重試。` }; }
  finally { clearTimeout(timer); }
}
export const yieldBandLoader = createCachedLoader(() => fetchBond('yield-band-params.json', '合理區間參數', parseYieldBands));
export const japanRatesLoader = createCachedLoader(() => fetchBond('jp-rates.json', '日本國債資料', parseJapanRates));
export function japanSpread(block: HistoryBlock) {
  return block.dates.map((time, i) => {
    const y = block.series.JGB10?.[i], p = block.series.BOJ_TARGET?.[i];
    return { time, value: typeof y === 'number' && typeof p === 'number' ? y - p : null };
  });
}

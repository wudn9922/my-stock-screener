import { z } from 'zod';
import { reportUrl } from './loadReport';

const readingSchema = z.object({
  date: z.iso.date(),
  close: z.number().positive().finite(),
  pct: z.number().finite().nullable(),
  mdd: z.number().finite().nullable(),
  hot: z.boolean(),
  cold: z.boolean(),
  // Additive fields (absent in files generated before the relative gauge existed).
  ratioPct: z.number().finite().nullable().optional(),
  relHot: z.boolean().optional(),
  hotWeeks: z.number().int().nonnegative().optional(),
});
const temperatureSchema = z.object({
  version: z.literal(1),
  asOf: z.iso.date(),
  provisional: z.boolean(),
  scope: z.string(),
  firstDate: z.iso.date().optional(),
  hotEpisodes: z.number().int().nonnegative().optional(),
  latest: readingSchema,
  rows: z.array(readingSchema).min(1),
});
export type MarginTemperature = z.infer<typeof temperatureSchema>;
export type TemperatureReading = z.infer<typeof readingSchema>;
export type TemperatureResult = { data: MarginTemperature; error?: never } | { data?: never; error: string };
export function parseMarginTemperature(raw: unknown): TemperatureResult {
  const result = temperatureSchema.safeParse(raw);
  if (!result.success) return { error: '融資溫度資料格式錯誤，保留其他指標顯示。' };
  const data = result.data;
  if (data.rows.at(-1)?.date !== data.asOf || data.latest.date !== data.asOf ||
    data.rows.some((row, i) => i > 0 && row.date <= data.rows[i - 1]!.date))
    return { error: '融資溫度歷史資料日期不一致。' };
  if (data.rows.some(row => row.hot !== (row.pct !== null && row.pct >= 90) ||
    row.cold !== (row.mdd !== null && row.mdd <= -30)))
    return { error: '融資溫度狀態與門檻不一致。' };
  if (data.rows.some(row => (row.relHot !== undefined && row.ratioPct !== undefined && row.relHot !== (row.ratioPct !== null && row.ratioPct >= 90)) ||
    (row.hotWeeks !== undefined && row.hot !== row.hotWeeks > 0)))
    return { error: '融資溫度相對指標與門檻不一致。' };
  return { data };
}
export async function loadMarginTemperature(signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<TemperatureResult> {
  try {
    const response = await fetcher(new URL('margin-temperature.json', reportUrl()).href, { cache: 'no-cache', signal });
    if (!response.ok) return { error: `融資溫度資料暫時無法取得（HTTP ${response.status}）。` };
    return parseMarginTemperature(await response.json());
  } catch {
    return { error: '融資溫度資料載入失敗，請稍後重試。' };
  }
}

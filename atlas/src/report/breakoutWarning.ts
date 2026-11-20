import { z } from 'zod';
import { reportUrl } from './loadReport';

const date = z.iso.date();
const rowSchema = z.object({
  date,
  rate: z.number().min(0).max(1).nullable(),
  n: z.number().int().nonnegative(),
  pct: z.number().min(0).max(100).nullable(),
  low: z.boolean(),
});
const episodeSchema = z.object({
  date,
  rate: z.number().min(0).max(1),
  pct: z.number().min(0).max(100),
  drawdown: z.number().max(0).nullable(),
  hit: z.boolean().nullable(),
});
const methodSchema = z.object({
  label: z.string().min(1),
  rows: z.array(rowSchema).min(1),
  latest: rowSchema.extend({ lowDays: z.number().int().nonnegative() }),
  stats: z.object({
    episodes: z.number().int().nonnegative(),
    hits: z.number().int().nonnegative(),
    avgDrawdown: z.number().nullable(),
    baseHitRate: z.number().min(0).max(1).nullable(),
    baseAvgDrawdown: z.number().nullable(),
    pending: z.number().int().nonnegative(),
    list: z.array(episodeSchema),
  }),
});
const marketSchema = z.object({
  asOf: date,
  index: z.object({ symbol: z.string(), name: z.string() }),
  stocks: z.number().int().nonnegative(),
  failedStocks: z.number().int().nonnegative(),
  note: z.string().nullable(),
  methods: z.partialRecord(z.enum(['swing', 'ema20']), methodSchema),
  closes: z.array(z.object({ date, close: z.number().positive().finite() })).min(1),
});
const warningSchema = z.object({
  version: z.literal(1),
  generatedAt: z.string(),
  params: z.object({
    windowDays: z.number().int().positive(),
    observationDays: z.number().int().positive(),
    minSignals: z.number().int().positive(),
    percentileWindow: z.number().int().positive(),
    percentileMinValid: z.number().int().positive(),
    lowPercentile: z.number().positive(),
    horizonDays: z.number().int().positive(),
    drawdown: z.number().positive(),
    episodeGapDays: z.number().int().positive(),
  }).loose(),
  markets: z.object({ tw: marketSchema.optional(), us: marketSchema.optional() }),
});
export type BreakoutWarning = z.infer<typeof warningSchema>;
export type BreakoutWarningMarket = z.infer<typeof marketSchema>;
export type BreakoutWarningMethod = z.infer<typeof methodSchema>;
export type BreakoutWarningRow = z.infer<typeof rowSchema>;
export type BreakoutWarningResult = { data: BreakoutWarning; error?: never } | { data?: never; error: string };

const ascending = (rows: readonly { date: string }[]) => rows.every((row, i) => i === 0 || row.date > rows[i - 1]!.date);

export function parseBreakoutWarning(raw: unknown): BreakoutWarningResult {
  const result = warningSchema.safeParse(raw);
  if (!result.success) return { error: '突破達成率警示資料格式錯誤，保留其他指標顯示。' };
  const data = result.data, limit = data.params.lowPercentile;
  for (const market of [data.markets.tw, data.markets.us]) {
    if (!market) continue;
    if (!ascending(market.closes)) return { error: '突破達成率警示的指數資料日期不一致。' };
    for (const method of Object.values(market.methods)) {
      if (!method) continue;
      const last = method.rows.at(-1)!;
      if (!ascending(method.rows) || last.date !== method.latest.date || last.date > market.asOf)
        return { error: '突破達成率警示歷史資料日期不一致。' };
      if (method.rows.some(row => row.low !== (row.pct !== null && row.pct <= limit)) || method.latest.low !== last.low ||
        (method.latest.lowDays > 0) !== method.latest.low)
        return { error: '突破達成率警示狀態與門檻不一致。' };
    }
  }
  return { data };
}

export async function loadBreakoutWarning(signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<BreakoutWarningResult> {
  try {
    const response = await fetcher(new URL('breakout-warning.json', reportUrl()).href, { cache: 'no-cache', signal });
    if (response.status === 404) return { error: '突破達成率警示資料尚未產生，下次全市場更新後會出現。' };
    if (!response.ok) return { error: `突破達成率警示資料暫時無法取得（HTTP ${response.status}）。` };
    return parseBreakoutWarning(await response.json());
  } catch {
    return { error: '突破達成率警示資料載入失敗，請稍後重試。' };
  }
}

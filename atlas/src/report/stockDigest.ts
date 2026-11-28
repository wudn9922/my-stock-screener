import { z } from 'zod';
import { reportUrl } from './loadReport';

export const DIGEST_MAX_AGE_DAYS = 30;
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(value => {
  const stamp = Date.parse(value);
  return Number.isFinite(stamp) && new Date(stamp).toISOString().slice(0, 10) === value;
});
const row = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('sec-restatement'), date: day, period: day }),
  z.object({ kind: z.literal('gross-margin'), date: day, period: z.string().regex(/^\d{4}Q[1-4]$/), before: z.number().finite(), after: z.number().finite() }),
  z.object({ kind: z.literal('operating-margin'), date: day, period: z.string().regex(/^\d{4}Q[1-4]$/), before: z.number().finite(), after: z.number().finite() }),
  z.object({ kind: z.literal('foreign-reversal'), date: day, direction: z.enum(['buy', 'sell']) }),
  z.object({ kind: z.literal('trust-reversal'), date: day, direction: z.enum(['buy', 'sell']) }),
]);
export const stockDigestSchema = z.object({
  version: z.literal(1), generatedAt: z.string().refine(s => Number.isFinite(Date.parse(s))),
  stocks: z.record(z.string(), z.array(row)),
});
export type StockDigest = z.infer<typeof stockDigestSchema>;
export type DigestRow = z.infer<typeof row>;

export function visibleDigest(data: StockDigest, symbol: string, today: string): DigestRow[] {
  return (data.stocks[symbol] ?? []).filter(row => {
    const age = (Date.parse(today) - Date.parse(row.date)) / 86400000;
    return age >= 0 && age <= DIGEST_MAX_AGE_DAYS;
  });
}
export function digestText(row: DigestRow): string {
  switch (row.kind) {
    case 'sec-restatement': return `SEC 財報重編：${row.period} 期間`;
    case 'foreign-reversal': case 'trust-reversal':
      return `${row.kind === 'foreign-reversal' ? '外資' : '投信'} 連續 3 日同向後轉為淨${row.direction === 'buy' ? '買' : '賣'}`;
    default: {
      const delta = row.after - row.before;
      return `${row.period} ${row.kind === 'gross-margin' ? '毛利率' : '營益率'} ${row.before.toFixed(1)}% → ${row.after.toFixed(1)}%（${delta >= 0 ? '+' : ''}${delta.toFixed(1)}pp）`;
    }
  }
}
export async function fetchStockDigest(signal?: AbortSignal): Promise<StockDigest> {
  const response = await fetch(new URL('stock-digest.json', reportUrl()), { signal, cache: 'no-cache' });
  if (!response.ok) throw new Error('Stock digest unavailable');
  return stockDigestSchema.parse(await response.json());
}

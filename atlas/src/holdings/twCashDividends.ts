import { z } from 'zod';
import { reportUrl } from '../report/loadReport';

const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(d => {
  const t = Date.parse(d);
  return Number.isFinite(t) && new Date(t).toISOString().slice(0, 10) === d;
});
const cashDividend = z.object({
  exDate: day, paymentDate: day.nullable(), perShare: z.number().finite().positive(), year: z.string(),
}).refine(row => row.paymentDate === null || row.paymentDate >= row.exDate);
const stock = z.object({ checked: day.nullable(), events: z.array(cashDividend) });
const symbol = z.string().regex(/^\d{4,6}[A-Z]?\.(TW|TWO)$/);
export type CashDividends = Record<string, z.infer<typeof stock>>;
// Validate the envelope once and isolate malformed stock records.
export const cashDividendsSchema = z.object({
  version: z.literal(1),
  generatedAt: z.string().refine(s => Number.isFinite(Date.parse(s))),
  stocks: z.record(z.string(), z.unknown()),
}).transform(doc => {
  const stocks: CashDividends = {};
  for (const [key, value] of Object.entries(doc.stocks)) {
    if (!symbol.safeParse(key).success) continue;
    const parsed = stock.safeParse(value);
    if (parsed.success) stocks[key] = parsed.data;
  }
  return { ...doc, stocks };
});
export async function fetchCashDividends(signal?: AbortSignal): Promise<CashDividends> {
  const response = await fetch(new URL('tw-cash-dividends.json', reportUrl()), { signal, cache: 'no-cache' });
  if (!response.ok) throw new Error('Cash dividends unavailable');
  const data = cashDividendsSchema.parse(await response.json());
  const age = Date.now() - Date.parse(data.generatedAt);
  if (age < -300000 || age > 4 * 86400000) throw new Error('Cash dividends unavailable');
  return data.stocks;
}

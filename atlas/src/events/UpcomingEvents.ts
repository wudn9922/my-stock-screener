import { z } from 'zod';
import { reportUrl } from '../report/loadReport';

const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(d => {
  const t = Date.parse(d);
  return Number.isFinite(t) && new Date(t).toISOString().slice(0, 10) === d;
});
const nonnegative = z.number().finite().nonnegative();
const eventSchema = z.object({
  symbol: z.string().regex(/^[A-Z0-9^][A-Z0-9.^=-]{0,31}$/).nullable(),
  market: z.enum(['TW', 'US']),
  kind: z.enum(['earnings', 'stock-dividend', 'cash-dividend', 'disposal-start', 'disposal-end', 'notice', 'holiday', 'early-close', 'fomc']),
  date: day, detail: z.string(), source: z.string(),
}).refine(row => (row.symbol === null) === ['holiday', 'early-close', 'fomc'].includes(row.kind));
const moveSchema = z.object({
  samples: z.number().int().min(0).max(8), gapPct: nonnegative.nullable(), dayPct: nonnegative.nullable(),
  first: day.nullable(), last: day.nullable(),
}).refine(row => row.samples === 0
  ? row.gapPct === null && row.dayPct === null && row.first === null && row.last === null
  : row.gapPct !== null && row.dayPct !== null && row.first !== null && row.last !== null && row.first <= row.last);
const cashDividendSchema = z.object({
  exDate: day, paymentDate: day.nullable(), perShare: z.number().finite().positive(), year: z.string(),
}).refine(row => row.paymentDate === null || row.paymentDate >= row.exDate);
export const cashDividendsSchema = z.record(z.string().regex(/^\d{4,6}[A-Z]?\.(TW|TWO)$/), z.object({
  checked: day.nullable(), events: z.array(cashDividendSchema),
}));
export type CashDividends = z.infer<typeof cashDividendsSchema>;
export const upcomingEventsSchema = z.object({
  version: z.literal(1), generatedAt: z.string().refine(s => Number.isFinite(Date.parse(s))),
  coverage: z.object({ earnings: nonnegative, alerts: nonnegative, dividends: nonnegative, cashDividends: nonnegative }),
  events: z.array(eventSchema), moves: z.record(z.string(), moveSchema),
  cashDividends: cashDividendsSchema.optional(),
});
export type UpcomingEvents = z.infer<typeof upcomingEventsSchema>;
export type UpcomingEvent = z.infer<typeof eventSchema>;
export const EVENTS_UNAVAILABLE = '事件資料暫時無法載入';

export async function fetchUpcomingEvents(signal?: AbortSignal): Promise<UpcomingEvents> {
  const response = await fetch(new URL('upcoming-events.json', reportUrl()), { signal, cache: 'no-cache' });
  if (!response.ok) throw new Error(EVENTS_UNAVAILABLE);
  const data = upcomingEventsSchema.parse(await response.json());
  const age = Date.now() - Date.parse(data.generatedAt);
  if (age < -300000 || age > 4 * 86400000) throw new Error(EVENTS_UNAVAILABLE);
  return data;
}
const zones = { TW: 'Asia/Taipei', US: 'America/New_York' } as const;
function localDate(now: number, market: 'TW' | 'US') {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: zones[market], year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
}
export function groupUpcomingEvents(events: readonly UpcomingEvent[], symbols: readonly string[], now: number) {
  const keys = new Set(symbols.map(s => s.trim().toUpperCase()));
  const groups = new Map<string, (UpcomingEvent & { days: number })[]>();
  const seen = new Set<string>();
  for (const row of events) {
    if (row.symbol !== null && !keys.has(row.symbol)) continue;
    const days = Math.round((Date.parse(row.date) - Date.parse(localDate(now, row.market))) / 86400000);
    if (days < 0 || days > 14) continue;
    const key = JSON.stringify([row.date, row.market, row.symbol, row.kind, row.detail]);
    if (seen.has(key)) continue;
    seen.add(key);
    const group = groups.get(row.date) ?? [];
    group.push({ ...row, days });
    groups.set(row.date, group);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([date, rows]) => ({ date, rows }));
}

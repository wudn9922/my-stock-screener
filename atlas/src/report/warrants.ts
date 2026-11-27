import { z } from 'zod';
import { reportUrl } from './loadReport';
import { defaultMarketProxyUrl } from '../market-data/EdgeYahooProvider';
import { marketProxyAccess } from '../market-data/marketProxyAccess';

export const WARRANT_BATCH = 20;
const date = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(value => {
  const t = Date.parse(value);
  return Number.isFinite(t) && new Date(t).toISOString().slice(0, 10) === value;
});
const positive = z.number().finite().positive().nullable();
export const underlyingPattern = /^(?:[0-9A-Z]{1,10}\.TWO?|\^[A-Z0-9]{1,10})$/;
const warrant = z.object({
  code: z.string().regex(/^\d{5}[0-9A-Z]$/), symbol: z.string().regex(/^\d{5}[0-9A-Z]\.TWO?$/),
  name: z.string(), type: z.enum(['call', 'put']), strikePrice: positive, exerciseRatio: positive,
  expiryDate: date, lastTradingDate: date, issuer: z.string().nullable(), market: z.enum(['TWSE', 'TPEx']),
}).refine(row => row.symbol === row.code + (row.market === 'TWSE' ? '.TW' : '.TWO') && row.lastTradingDate <= row.expiryDate);
export const warrantFileSchema = z.object({
  version: z.literal(1), underlying: z.string().regex(underlyingPattern), asOf: z.object({ TWSE: date, TPEx: date }),
  warrants: z.array(warrant),
}).refine(file => new Set(file.warrants.map(row => row.symbol)).size === file.warrants.length);
export type Warrant = z.infer<typeof warrant>;
export type WarrantFile = z.infer<typeof warrantFileSchema>;
const quote = z.object({
  status: z.enum(['available', 'no_book', 'no_quote', 'stale', 'source_unavailable']),
  quoteTime: z.number().finite().positive().nullable(),
  bid: positive, ask: positive, bidSize: z.number().finite().nonnegative().nullable(), askSize: z.number().finite().nonnegative().nullable(),
});
const quoteResponse = z.object({
  asOf: z.number().finite().positive(), source: z.literal('twse-mis'), session: z.enum(['regular', 'closed']),
  results: z.record(z.string(), quote),
});
export type WarrantQuote = z.infer<typeof quote>;
export type WarrantQuotes = z.infer<typeof quoteResponse>;
export async function loadWarrants(symbol: string, signal: AbortSignal): Promise<WarrantFile | null> {
  if (!underlyingPattern.test(symbol)) throw new Error('請選擇台灣標的代號');
  const response = await fetch(new URL(`warrants/${encodeURIComponent(symbol)}.json`, reportUrl()), { cache: 'no-cache', signal });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`權證清單載入失敗（HTTP ${response.status}）`);
  const file = warrantFileSchema.parse(await response.json());
  if (file.underlying !== symbol) throw new Error('權證清單標的不符');
  return file;
}
export async function loadWarrantQuotes(symbols: string[], signal: AbortSignal): Promise<WarrantQuotes> {
  const proxy = defaultMarketProxyUrl();
  if (!proxy) throw new Error('行情代理尚未設定');
  if (!symbols.length || symbols.length > WARRANT_BATCH) throw new Error('無效的權證批次');
  const url = new URL(proxy, location.href);
  url.searchParams.set('warrantQuotes', symbols.join(','));
  const response = await marketProxyAccess.fetch(fetch, url.href, { signal, cache: 'no-store', headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`報價載入失敗（HTTP ${response.status}）`);
  const result = quoteResponse.parse(await response.json());
  // An unsolicited result cannot change the rows being compared.
  return { ...result, results: Object.fromEntries(symbols.flatMap(symbol => result.results[symbol] ? [[symbol, result.results[symbol]!]] : [])) };
}
export function spreadPercent(value: WarrantQuote | undefined): number | null {
  if (value?.status !== 'available' || value.bid === null || value.ask === null || value.ask < value.bid) return null;
  const mid = (value.ask + value.bid) / 2;
  return mid > 0 ? (value.ask - value.bid) / mid * 100 : null;
}
export function filterWarrants(rows: Warrant[], type: '' | 'call' | 'put', from: string, to: string, today: string): Warrant[] {
  return rows.filter(row => row.lastTradingDate >= today && (!type || row.type === type)
    && (!from || row.expiryDate >= from) && (!to || row.expiryDate <= to));
}
export function sortWarrants(rows: Warrant[], quotes: Record<string, WarrantQuote>): Warrant[] {
  return [...rows].sort((a,b) => (spreadPercent(quotes[a.symbol]) ?? Infinity) - (spreadPercent(quotes[b.symbol]) ?? Infinity) || a.code.localeCompare(b.code));
}

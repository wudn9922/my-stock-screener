import { z } from 'zod';
import { reportUrl } from './loadReport';
import { defaultMarketProxyUrl } from '../market-data/EdgeYahooProvider';
import { marketProxyAccess } from '../market-data/marketProxyAccess';
import { getMarketProfile, marketDateTime, sessionDate } from '../market-data/MarketProfile';

export const WARRANT_BATCH = 20;
// W4 serializes upstream batches with a 1 s gap. A 4 s client gap leaves room under
// the ordinary user's shared 200 requests per ten minutes, including after a filter change.
const REQUEST_GAP_MS = 4_000;
const QUOTE_MAX_AGE = 60;
const taiwanProfile = getMarketProfile('^TWII');
let nextRequestAt = 0;

export const warrantDate = (now: number) => sessionDate(now / 1000, taiwanProfile);
const date = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(value => {
  const t = Date.parse(value);
  return Number.isFinite(t) && new Date(t).toISOString().slice(0, 10) === value;
});
const positive = z.number().finite().positive().nullable();
export const underlyingPattern = /^(?:[0-9]{4,6}[A-Z]?\.TWO?|\^[A-Z0-9]{1,12}|IX[0-9]{4})$/;
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
const timestamp = z.number().int().positive().max(253_402_300_799);
const bookPrice = z.number().nonnegative().nullable().transform(value => value || null);
const quote = z.object({
  status: z.enum(['available', 'no_book', 'no_quote', 'stale', 'source_unavailable']),
  quoteTime: timestamp.nullable(),
  bid: bookPrice, ask: bookPrice,
  bidSize: z.number().finite().nonnegative().nullable(), askSize: z.number().finite().nonnegative().nullable(),
}).transform(book => ({ ...book, bidSize: book.bid === null ? null : book.bidSize, askSize: book.ask === null ? null : book.askSize }));
export const warrantQuotesSchema = z.object({
  asOf: timestamp, source: z.literal('twse-mis'), session: z.enum(['regular', 'closed']),
  results: z.record(z.string(), quote),
});
export type WarrantQuote = z.infer<typeof quote>;
export type WarrantQuotes = z.infer<typeof warrantQuotesSchema>;
export type WarrantQuoteSample = { book: WarrantQuote; session: WarrantQuotes['session']; asOf: number };

export function waitForWarrantRefresh(delay: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(signal.reason);
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, delay);
    signal.addEventListener('abort', abort, { once: true });
  });
}
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
  while (nextRequestAt > Date.now()) await waitForWarrantRefresh(nextRequestAt - Date.now(), signal);
  if (signal.aborted) throw signal.reason;
  nextRequestAt = Date.now() + REQUEST_GAP_MS;
  const url = new URL(proxy, location.href);
  url.searchParams.set('warrantQuotes', symbols.join(','));
  const response = await marketProxyAccess.fetch(fetch, url.href, { signal, cache: 'no-store', headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`報價載入失敗（HTTP ${response.status}）`);
  const result = warrantQuotesSchema.parse(await response.json());
  // An unsolicited result cannot change the rows being compared.
  return { ...result, results: Object.fromEntries(symbols.flatMap(symbol => result.results[symbol] ? [[symbol, result.results[symbol]!]] : [])) };
}

/** A large scan can outlast a book's MIS freshness window before the last batch finishes. */
export function currentWarrantQuotes(samples: Record<string, WarrantQuoteSample>, now: number): Record<string, WarrantQuote> {
  const today = warrantDate(now);
  const midnight = marketDateTime(today, 0, taiwanProfile);
  const close = marketDateTime(today, taiwanProfile.sessionCloseMinutes, taiwanProfile);
  return Object.fromEntries(Object.entries(samples).map(([symbol, { book, session, asOf }]) => {
    const time = book.quoteTime;
    let status = book.status;
    if (status === 'available') {
      if (time === null || time > asOf || (book.bid === null && book.ask === null)) status = 'no_quote';
      else if (session === 'closed' && time !== close) status = 'stale';
      else if (session === 'regular' && (time < midnight || time >= midnight + 86_400 || now / 1000 - time > QUOTE_MAX_AGE)) status = 'stale';
    }
    if (status === 'available') return [symbol, book];
    return [symbol, { status, quoteTime: time, bid: null, ask: null, bidSize: null, askSize: null }];
  }));
}

export function spreadPercent(value: WarrantQuote | undefined): number | null {
  if (value?.status !== 'available' || value.bid === null || value.ask === null || value.bid <= 0 || value.ask < value.bid) return null;
  const mid = (value.ask + value.bid) / 2;
  return mid > 0 ? (value.ask - value.bid) / mid * 100 : null;
}
export function filterWarrants(rows: Warrant[], type: '' | 'call' | 'put', from: string, to: string, today: string): Warrant[] {
  return rows.filter(row => row.lastTradingDate >= today && (!type || row.type === type)
    && (!from || row.expiryDate >= from) && (!to || row.expiryDate <= to));
}
export function sortWarrants(rows: Warrant[], quotes: Record<string, WarrantQuote>): Warrant[] {
  return [...rows].sort((a,b) => (spreadPercent(quotes[a.symbol]) ?? Infinity) - (spreadPercent(quotes[b.symbol]) ?? Infinity) || a.symbol.localeCompare(b.symbol));
}

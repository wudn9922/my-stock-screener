import { z } from 'zod';
import { reportUrl } from './loadReport';
import { METRIC_MAS, type ReportGroup } from './schema';
import type { UniverseMarket } from './universe';

export const LINE_TRENDING_GROUP_KEY = 'line-trending-us';
export const LINE_TRENDING_MISSING = 'LINE 熱門美股清單尚未產生';

export const trendingStocksSchema = z.object({
  version: z.literal(1),
  source: z.string().min(1),
  fetchedAt: z.string().datetime({ offset: true }),
  sentAt: z.string().datetime({ offset: true }),
  symbols: z.array(z.string().regex(/^[A-Z][A-Z0-9.-]{0,14}$/)).min(1).max(5)
    .refine(symbols => new Set(symbols).size === symbols.length),
});
export type TrendingStocks = z.infer<typeof trendingStocksSchema>;
export type TrendingStocksResult =
  | { status: 'ok'; data: TrendingStocks }
  | { status: 'error'; message: string };

/** Fixed-group rows retain LINE order, including symbols outside the full-market universe. */
export function trendingStocksGroup(data?: TrendingStocks, universe?: UniverseMarket | null): ReportGroup {
  const bySymbol = new Map(universe?.items.map(item => [item.symbol, item]));
  return {
    key: LINE_TRENDING_GROUP_KEY, name: 'LINE 熱門', market: 'US', kind: 'fixed', maList: [...METRIC_MAS],
    items: (data?.symbols ?? []).map(symbol => bySymbol.get(symbol) ?? {
      symbol, name: symbol, maList: [...METRIC_MAS], close: null, changePct: null,
      maValues: {}, volume: null, asOf: null, metrics: null, status: 'unmatched',
      note: '未出現在美股全市場資料中',
    }),
  };
}

/** Data currency follows the report's Taipei calendar day, not a wall-clock age threshold. */
export function trendingStocksStale(data: TrendingStocks, latestReportDay: string | null | undefined): boolean {
  if (!latestReportDay || !/^\d{4}-\d{2}-\d{2}$/.test(latestReportDay)) return false;
  const fetchedDay = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Taipei' }).format(new Date(data.fetchedAt));
  return fetchedDay < latestReportDay;
}

export async function fetchTrendingStocks(signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<TrendingStocksResult> {
  try {
    const response = await fetcher(new URL('trending-stocks.json', reportUrl()).href, { cache: 'no-cache', signal });
    if (response.status === 404) return { status: 'error', message: LINE_TRENDING_MISSING };
    if (!response.ok) throw new Error('HTTP error');
    const body: unknown = await response.json().catch(() => null);
    const parsed = trendingStocksSchema.safeParse(body);
    if (!parsed.success) return { status: 'error', message: LINE_TRENDING_MISSING };
    return { status: 'ok', data: parsed.data };
  } catch {
    return { status: 'error', message: '暫時無法取得 LINE 熱門美股，請稍後重試。' };
  }
}

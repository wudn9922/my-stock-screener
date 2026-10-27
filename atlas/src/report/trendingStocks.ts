import { z } from 'zod';
import { reportUrl } from './loadReport';

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

export async function fetchTrendingStocks(signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<TrendingStocksResult> {
  try {
    const response = await fetcher(new URL('trending-stocks.json', reportUrl()).href, { cache: 'no-cache', signal });
    if (response.status === 404) return { status: 'error', message: 'LINE 熱門美股清單尚未產生。' };
    if (!response.ok) throw new Error('HTTP error');
    const parsed = trendingStocksSchema.safeParse(await response.json());
    if (!parsed.success) return { status: 'error', message: 'LINE 熱門美股資料格式無法辨識。' };
    return { status: 'ok', data: parsed.data };
  } catch {
    return { status: 'error', message: '暫時無法取得 LINE 熱門美股，請稍後重試。' };
  }
}

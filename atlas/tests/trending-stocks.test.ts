import { describe, expect, it, vi } from 'vitest';
import { fetchTrendingStocks, trendingStocksSchema } from '../src/report/trendingStocks';

const data = {
  version: 1, source: 'Yahoo Finance US trending · LINE 財經報告',
  fetchedAt: '2026-10-27T09:00:00+00:00', sentAt: '2026-10-27T09:05:00Z',
  symbols: ['NVDA', 'AAPL', 'AMD', 'TSLA', 'MSFT'],
};
describe('LINE trending stocks', () => {
  it('preserves the LINE order and timestamps', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => Response.json(data));
    expect(await fetchTrendingStocks(undefined, fetcher)).toEqual({ status: 'ok', data });
    expect(String(fetcher.mock.calls[0]?.[0])).toContain('/report/trending-stocks.json');
  });
  it('rejects duplicate, unsafe, oversized and invalid timestamp reports', () => {
    for (const change of [
      { symbols: ['NVDA', 'NVDA'] }, { symbols: ['<script>'] }, { symbols: [] },
      { symbols: [...data.symbols, 'META'] }, { fetchedAt: 'yesterday' },
    ]) expect(trendingStocksSchema.safeParse({ ...data, ...change }).success).toBe(false);
  });
  it('reports missing, invalid and failed fetches without fake symbols', async () => {
    for (const response of [new Response('', { status: 404 }), new Response('html'), new Response('', { status: 503 })]) {
      expect((await fetchTrendingStocks(undefined, async () => response)).status).toBe('error');
    }
    expect((await fetchTrendingStocks(undefined, async () => { throw new TypeError('offline'); })).status).toBe('error');
  });
});

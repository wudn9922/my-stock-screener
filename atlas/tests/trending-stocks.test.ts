import { describe, expect, it, vi } from 'vitest';
import { fetchTrendingStocks, trendingStocksSchema } from '../src/report/trendingStocks';

const data = {
  version: 1 as const, source: 'Yahoo Finance US trending · LINE 財經報告',
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

it('builds fixed US rows from the universe without dropping or reordering missing symbols', async () => {
  const { trendingStocksGroup } = await import('../src/report/trendingStocks');
  const { parseUniverse } = await import('../src/report/universe');
  const { readFileSync } = await import('node:fs');
  const parsed = parseUniverse(JSON.parse(readFileSync(new URL('./fixtures/screener/universe.json', import.meta.url), 'utf8')));
  expect(parsed.status).toBe('ok');
  if (parsed.status !== 'ok') throw new Error('fixture invalid');
  const universe = parsed.universe.markets.US!;
  const group = trendingStocksGroup({ ...data, symbols: ['NVDA', 'NOTFOUND', 'AAPL'] }, universe);
  expect(group).toMatchObject({ key: 'line-trending-us', kind: 'fixed', market: 'US', name: 'LINE 熱門' });
  expect(group.items.map(item => item.symbol)).toEqual(['NVDA', 'NOTFOUND', 'AAPL']);
  expect(group.items[0]).toEqual(universe.items.find(item => item.symbol === 'NVDA'));
  expect(group.items[2]).toEqual(universe.items.find(item => item.symbol === 'AAPL'));
  expect(group.items[1]).toMatchObject({ status: 'unmatched', close: null, changePct: null, metrics: null, note: '未出現在美股全市場資料中' });
  expect(trendingStocksGroup().items).toEqual([]);
});

it('compares the fetch date to the newest report day in Taipei, including midnight and a later send', async () => {
  const { trendingStocksStale } = await import('../src/report/trendingStocks');
  expect(trendingStocksStale(data, '2026-10-28')).toBe(true);
  expect(trendingStocksStale(data, '2026-10-27')).toBe(false);
  expect(trendingStocksStale(data, null)).toBe(false);
  expect(trendingStocksStale({ ...data, fetchedAt: '2026-10-27T16:01:00Z' }, '2026-10-28')).toBe(false);
  expect(trendingStocksStale({ ...data, sentAt: '2026-10-28T09:05:00Z' }, '2026-10-28')).toBe(true);
});

it('returns the group missing message for 404 and every invalid payload', async () => {
  const { LINE_TRENDING_MISSING } = await import('../src/report/trendingStocks');
  for (const response of [new Response('', { status: 404 }), new Response('<html>fallback</html>'), Response.json({ version: 99 })]) {
    expect(await fetchTrendingStocks(undefined, async () => response)).toEqual({ status: 'error', message: LINE_TRENDING_MISSING });
  }
});

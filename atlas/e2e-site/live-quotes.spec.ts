import { expect, test, type Page } from '@playwright/test';
import { mockSite, yahooChart } from './mocks';

const LIVE_PRICE = 1234.5;

/** Answers `market-chart?quotes=` with the chart's own rows, the latest close moved to LIVE_PRICE. */
async function liveQuotes(page: Page) {
  const batches: string[][] = [];
  await page.route('https://edge.test/functions/v1/market-chart**', (route) => {
    const symbols = new URL(route.request().url()).searchParams.get('quotes');
    if (!symbols) return route.fallback();
    batches.push(symbols.split(','));
    const results = Object.fromEntries(symbols.split(',').map((symbol) => {
      const payload = yahooChart(symbol, '1d');
      const data = payload.chart.result[0]!,
        quote = data.indicators.quote[0]!,
        last = quote.close.length - 1;
      quote.close[last] = LIVE_PRICE;
      quote.high[last] = Math.max(quote.high[last]!, LIVE_PRICE);
      data.meta.regularMarketPrice = LIVE_PRICE;
      return [symbol, payload];
    }));
    return route.fulfill({ json: { asOf: 0, results, errors: {} } });
  });
  return batches;
}

test('the open chart takes the fresh price at once, keeping its loaded history', async ({ page }) => {
  await mockSite(page);
  const batches = await liveQuotes(page);
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('1234.50');
  expect(batches).toContainEqual(['AAPL']);
});

test('watchlist rows on screen show the fresh price in one batched request', async ({ page }) => {
  await page.clock.install({ time: new Date('2026-11-04T16:00:00Z') });
  await mockSite(page);
  await page.route('**/my-stock-screener/report/trending-stocks.json', (route) =>
    route.fulfill({ json: { version: 1, source: 'line', fetchedAt: '2026-10-27T01:00:00+08:00', sentAt: '2026-10-27T01:05:00+08:00', symbols: ['NVDA', 'AAPL'] } }));
  const batches = await liveQuotes(page);
  await page.goto('./?page=watchlist');
  const rows = page.getByRole('region', { name: '自選行情' }).getByRole('list', { name: '自選行情列表' }).getByRole('link');
  await expect(rows).toHaveCount(2);
  for (const row of await rows.all()) await expect(row.locator('.wb-quote b')).toHaveText(/1,?234\.50/);
  expect(batches.flat().sort()).toEqual(['AAPL', 'NVDA']);
  await expect(page.getByTestId('board-live-time')).toHaveText(/^延遲報價 · 更新於 \d{2}:\d{2}:\d{2}$/);
});

test('during the session the chart price and its update time move again a minute later', async ({ page }) => {
  // Wednesday 2026-11-04 12:20 in Taipei: the Taiwan session is open.
  await page.clock.install({ time: new Date('2026-11-04T04:20:00Z') });
  await mockSite(page);
  await page.route('**/my-stock-screener/report/trending-stocks.json', (route) =>
    route.fulfill({ json: { version: 1, source: 'line', fetchedAt: '2026-10-27T01:00:00+08:00', sentAt: '2026-10-27T01:05:00+08:00', symbols: ['NVDA'] } }));
  let price = 1000;
  await page.route('https://edge.test/functions/v1/market-chart**', (route) => {
    const symbols = new URL(route.request().url()).searchParams.get('quotes');
    if (!symbols) return route.fallback();
    const results = Object.fromEntries(symbols.split(',').map((symbol) => {
      const payload = yahooChart(symbol, '1d');
      const data = payload.chart.result[0]!, quote = data.indicators.quote[0]!, last = quote.close.length - 1;
      quote.close[last] = price;
      quote.high[last] = Math.max(quote.high[last]!, price);
      quote.low[last] = Math.min(quote.low[last]!, price);
      data.meta.regularMarketPrice = price;
      return [symbol, payload];
    }));
    return route.fulfill({ json: { asOf: 0, results, errors: {} } });
  });
  await page.goto('./?page=chart&symbol=2330.TW&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('1000.00');
  await expect(page.getByTestId('live-time')).toHaveText(/^延遲報價 · 更新於 12:20:\d{2}$/);
  price = 1010;
  await page.clock.fastForward(61_000);
  await expect(page.getByTestId('ohlc-header')).toContainText('1010.00');
  await expect(page.getByTestId('header-quote')).toContainText('1,010.00');
  await expect(page.getByTestId('live-time')).toHaveText(/^延遲報價 · 更新於 12:21:\d{2}$/);
  await page.screenshot({ path: test.info().outputPath('live-time-header.png') });
});

test('market index cards follow the live refresh without rebuilding their chart', async ({ page }) => {
  await mockSite(page);
  const batches = await liveQuotes(page);
  await page.goto('./?page=markets&market=tw');
  const card = page.locator('article.index-card').first();
  await expect(card).toBeVisible();
  await expect(card.locator('.index-card-quote b')).toHaveText(/1,?234\.50/);
  await expect(card.getByTestId('index-live-time')).toHaveText(/^更新於 \d{2}:\d{2}:\d{2}$/);
  await expect(card.locator('.mini-chart')).toHaveAttribute('data-status', 'ready');
  expect(batches.flat().length).toBeGreaterThan(0);
});

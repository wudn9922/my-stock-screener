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
  await mockSite(page);
  await page.route('**/my-stock-screener/report/trending-stocks.json', (route) =>
    route.fulfill({ json: { version: 1, source: 'line', fetchedAt: '2026-10-27T01:00:00+08:00', sentAt: '2026-10-27T01:05:00+08:00', symbols: ['NVDA', 'AAPL'] } }));
  const batches = await liveQuotes(page);
  await page.goto('./?page=watchlist');
  const rows = page.getByRole('region', { name: '自選行情' }).getByRole('list', { name: '自選行情列表' }).getByRole('link');
  await expect(rows).toHaveCount(2);
  for (const row of await rows.all()) await expect(row.locator('.wb-quote b')).toHaveText(/1,?234\.50/);
  expect(batches.flat().sort()).toEqual(['AAPL', 'NVDA']);
});

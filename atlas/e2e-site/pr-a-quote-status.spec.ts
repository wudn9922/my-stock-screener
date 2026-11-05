import { expect, test } from '@playwright/test';
import { mockSite, yahooChart } from './mocks';

for (const phase of ['open', 'closed'] as const) test(`A2 ${phase}: header, source and board show the same regular-session status`, async ({ page }, info) => {
  const now = phase === 'open' ? '2026-11-04T16:00:00Z' : '2026-11-04T22:00:00Z';
  await page.clock.install({ time: new Date(now) });
  await mockSite(page);
  const trade = Date.parse(phase === 'open' ? '2026-11-04T15:45:00Z' : '2026-11-04T21:00:00Z') / 1000;
  const payload = () => {
    const raw = yahooChart('AAPL', '1d'), row = raw.chart.result[0]!;
    const times = [Date.parse('2026-11-03T14:30:00Z') / 1000, Date.parse('2026-11-04T14:30:00Z') / 1000];
    row.timestamp = times;
    row.indicators.quote = [{ open: [100, 101], high: [101, 103], low: [99, 100], close: [100, 102], volume: [100, 200] }];
    row.meta.regularMarketTime = trade;
    row.meta.regularMarketPrice = 102;
    return raw;
  };
  await page.route('https://edge.test/functions/v1/market-chart**', route => new URL(route.request().url()).searchParams.has('quotes')
    ? route.fulfill({ json: { results: { AAPL: payload() }, errors: {} } }) : route.fulfill({ json: payload() }));
  await page.route('**/report/trending-stocks.json', route => route.fulfill({ json: { version: 1, source: 'line', fetchedAt: now, sentAt: now, symbols: ['AAPL'] } }));
  await page.goto('./?page=chart&symbol=AAPL');
  const expected = phase === 'open' ? /^延遲報價 · 更新於 00:00:\d{2}$/ : '已收盤 · 最後成交 11/05 05:00';
  await expect(page.getByTestId('live-time')).toHaveText(expected);
  await page.getByTestId('source-info-toggle').click();
  const source = page.locator('.data-source');
  await expect(source).toContainText(phase === 'open' ? '延遲報價 · 更新於 00:00:' : expected as string);
  await page.getByTestId('source-info-toggle').click();
  await page.screenshot({ path: info.outputPath(`a2-${phase}-header.png`) });
  await page.goto('./?page=watchlist');
  await expect(page.getByTestId('board-live-time')).toHaveText(expected);
  await expect(page.locator('.wb-quote b')).toHaveText('102.00');
  expect(await page.locator('#main').evaluate(e => e.scrollWidth - e.clientWidth)).toBe(0);
  await page.screenshot({ path: info.outputPath(`a2-${phase}-board.png`) });
});

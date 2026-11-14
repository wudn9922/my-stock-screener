import { expect, test, type Page, type Locator } from '@playwright/test';
import { mockSite, yahooChart } from './mocks';
import { readFileSync } from 'node:fs';
const INITIAL_IPOS = JSON.parse(readFileSync(new URL('../../config/ipo-watch.json', import.meta.url), 'utf8'));
const now = '2026-11-04T22:30:00Z';
const snapshot = { version: 1, symbol: 'AAPL', checkedAt: now, source: 'Nasdaq 公開快照（非即時）', pre: { status: 'ok', price: 99, asOf: '2026-11-04T14:00:00Z' }, post: { status: 'ok', price: 102, asOf: '2026-11-04T21:45:00Z' } };
async function setup(page: Page, inline: boolean) {
  await page.clock.install({ time: new Date(now) });
  await mockSite(page);
  await page.route('**/atlas/valuation/us.json', route => route.fulfill({ json: { version: 1, market: 'US', items: { AAPL: { epsAnnual: 100 / 77.6, epsTtm: 100 / 41.4, fiscalYear: 2025, source: 'SEC frames' } } } }));
  const raw = (batch = false) => {
    const value = yahooChart('AAPL', '1d'), row = value.chart.result[0]!;
    row.timestamp = ['2026-11-03T14:30:00Z', '2026-11-04T14:30:00Z'].map(t => Date.parse(t) / 1000);
    row.indicators.quote = [{ open: [99, 100], high: [100, 101], low: [98, 99], close: [99, 100], volume: [100, 200] }];
    Object.assign(row.meta, { regularMarketPrice: 100, regularMarketTime: Date.parse('2026-11-04T21:00:00Z') / 1000, ...(inline && batch ? { postMarketPrice: 105, postMarketTime: Date.parse('2026-11-04T22:00:00Z') / 1000 } : {}) });
    return value;
  };
  await page.route('https://edge.test/functions/v1/market-chart**', route => route.fulfill({ json: new URL(route.request().url()).searchParams.has('quotes') ? { results: { AAPL: raw(true) }, errors: {} } : raw() }));
  await page.route('**/report/trending-stocks.json', route => route.fulfill({ json: { version: 1, source: 'line', fetchedAt: now, sentAt: now, symbols: ['AAPL'] } }));
}
async function separate(elements: Locator[]) {
  const boxes = await Promise.all(elements.map(async e => { await expect(e).toBeVisible(); return (await e.boundingBox())!; }));
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i]!, b = boxes[j]!;
    expect(Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y))).toBe(0);
  }
}
for (const inline of [false, true]) test(`A3 direct extended price, in-flow details and 88px phone rows (newer batch=${inline})`, async ({ page }, info) => {
  await setup(page, inline);
  let staticRequests = 0;
  await page.route('**/report/extended-quotes/AAPL.json', route => { staticRequests++; return route.fulfill({ json: snapshot }); });
  await page.goto('./?page=chart&symbol=AAPL');
  const line = page.getByTestId('extended-quotes-toggle');
  await expect(line).toHaveText(inline ? '盤後 105.00 +5.00%（17:00 紐約）' : '盤後 102.00 +2.00%（16:45 紐約）');
  await expect(page.getByTestId('header-quote').locator('b')).toHaveText('100.00');
  await expect(page.getByTestId('valuation')).toContainText('77.6');
  await expect(page.getByTestId('valuation')).toContainText('41.4');
  const headerParts = [page.getByTestId('valuation').locator('div').nth(0), page.getByTestId('valuation').locator('div').nth(1), page.getByTestId('valuation-help-toggle'), ...await page.locator('.ws-header-actions > button').all(), page.getByTestId('source-info-toggle'), line];
  await separate(headerParts);
  for (const element of headerParts) {
    const b = (await element.boundingBox())!;
    expect(b.x).toBeGreaterThanOrEqual(0);
    expect(b.x + b.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  }
  await page.screenshot({ path: info.outputPath('a3-direct-header.png') });
  await line.click();
  const details = page.getByRole('region', { name: '盤前盤後報價', exact: true });
  await expect(details).toContainText('非即時');
  expect(await details.evaluate(el => getComputedStyle(el).position)).toBe('static');
  await separate([details, page.locator('.ws-stock-controls'), page.getByTestId('chart')]);
  expect(staticRequests).toBe(1);
  await page.screenshot({ path: info.outputPath('a3-expanded-source.png') });
  await line.click();
  await page.goto('./?page=watchlist');
  const row = page.locator('.wb-row');
  await expect(row.getByTestId('board-extended-quote')).toContainText(inline ? '盤後 105.00 +5.00%' : '盤後 102.00 +2.00%');
  await expect(row.locator('.wb-quote b')).toHaveText('100.00');
  expect((await row.boundingBox())!.height).toBeGreaterThanOrEqual(87);
  expect((await row.boundingBox())!.height).toBeLessThanOrEqual(89);
  await separate([row.locator('.wb-name'), row.locator('.wb-spark'), row.locator('.wb-quote')]);
  const quote = (await row.locator('.wb-quote').boundingBox())!, extended = (await row.getByTestId('board-extended-quote').boundingBox())!;
  expect(extended.x).toBeGreaterThanOrEqual(quote.x);
  expect(extended.x + extended.width).toBeLessThanOrEqual(quote.x + quote.width);
  expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBe(0);
  await page.screenshot({ path: info.outputPath('a3-extended-board.png') });
});
test('A3 missing extended data reserves no space; source details retain the explicit failure reason', async ({ page }) => {
  await setup(page, false);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/report/extended-quotes/AAPL.json', async route => { await pending; return route.fulfill({ status: 404 }); });
  await page.goto('./?page=chart&symbol=AAPL');
  await expect(page.getByTestId('header-quote').locator('b')).toHaveText('100.00');
  const height = await page.locator('.ws-price-block').evaluate(el => el.clientHeight);
  const response = page.waitForResponse('**/report/extended-quotes/AAPL.json');
  release(); await response;
  await expect(page.getByTestId('extended-quotes-toggle')).toHaveCount(0);
  expect(await page.locator('.ws-price-block').evaluate(el => el.clientHeight)).toBe(height);
  await page.getByTestId('source-info-toggle').click();
  await expect(page.getByRole('region', { name: '行情資料說明', exact: true })).toContainText('此股票尚未收錄延長時段快照');
});
test('A3 an actually empty monthly IPO calendar is explained without losing curated filings', async ({ page }) => {
  await mockSite(page);
  await page.route('**/report/ipo-watch.json', route => route.fulfill({ json: { version: 1, generatedAt: now, reason: '本月行事曆暫無資料', companies: INITIAL_IPOS } }));
  await page.goto('./?page=watchlist');
  await expect(page.getByRole('heading', { name: '自選', exact: true })).toBeVisible();
  if (await page.locator('.site-tabbar').isVisible()) await page.getByRole('button', { name: '搜尋股票', exact: true }).click();
  const input = page.getByRole('combobox', { name: '搜尋股票', exact: true });
  await input.fill('ANT');
  await expect(page.getByRole('option', { name: /ANT.*Anthropic/ })).toBeVisible();
  await expect(page.getByText('本月行事曆暫無資料', { exact: true })).toBeVisible();
});

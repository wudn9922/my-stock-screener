import { expect, test } from '@playwright/test';
import { mockSite, yahooChart } from './mocks';

const trending = {
  version: 1, source: 'Yahoo Finance US trending · LINE 財經報告',
  fetchedAt: '2026-10-27T09:00:00Z', sentAt: '2026-10-27T09:05:00Z',
  symbols: ['NVDA', 'AAPL', 'AMD', 'TSLA', 'MSFT'],
};

test('LINE stocks open a chart and repeated updates request only that stock; failure retains the chart', async ({ page }, testInfo) => {
  await mockSite(page);
  await page.route('**/report/trending-stocks.json', route => route.fulfill({ json: trending }));
  await page.goto('./?page=screener');
  const card = page.getByRole('region', { name: 'LINE 熱門美股', exact: true });
  await expect(card.locator('li b')).toHaveText(trending.symbols);
  await card.getByRole('button', { name: 'NVDA，開啟 K 線' }).click();
  const update = page.getByRole('button', { name: '更新 NVDA K 線', exact: true });
  await expect(update).toBeEnabled();
  const header = page.getByTestId('header-quote');
  const originalPrice = (await header.textContent())!;
  const refreshed: URL[] = [];
  let fail = false;
  await page.route('https://edge.test/functions/v1/market-chart**', async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.has('_refresh')) refreshed.push(url);
    if (fail) return route.fulfill({ status: 502, json: { error: 'upstream error' } });
    const body = yahooChart('NVDA', url.searchParams.get('interval')!);
    const result = body.chart.result[0];
    result.meta.regularMarketPrice += 10;
    const quotes = result.indicators.quote[0];
    for (const values of [quotes.open, quotes.high, quotes.low, quotes.close]) {
      for (let i = 0; i < values.length; i++) values[i] += 10;
    }
    return route.fulfill({ json: body });
  });
  await update.click();
  await expect(page.getByRole('status').filter({ hasText: '已更新 NVDA 的 1D K 線' })).toBeVisible();
  await expect(header).not.toHaveText(originalPrice);
  await expect(update).toBeEnabled();
  await update.click();
  await expect.poll(() => refreshed.length).toBe(2);
  expect(refreshed.every(url => url.searchParams.get('symbol') === 'NVDA')).toBe(true);
  const price = (await header.textContent())!;
  await expect(update).toBeEnabled();
  fail = true;
  await update.click();
  await expect(page.getByRole('status').filter({ hasText: 'NVDA 更新失敗，保留原有 K 線' })).toBeVisible();
  await expect(header).toHaveText(price);
  await expect(page.getByTestId('chart')).toBeVisible();
  await expect(page.locator('.chart-loading.error')).toHaveCount(0);
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath('single-stock-refresh.png') });
});

test('US markets show the same list; missing data supports retry without affecting the report', async ({ page }) => {
  await mockSite(page);
  let missing = true;
  await page.route('**/report/trending-stocks.json', route => missing
    ? route.fulfill({ status: 404 }) : route.fulfill({ json: trending }));
  await page.goto('./?page=markets&market=us');
  const card = page.getByRole('region', { name: 'LINE 熱門美股', exact: true });
  await expect(card).toContainText('尚未產生');
  missing = false;
  await card.getByRole('button', { name: '重試熱門股清單' }).click();
  await expect(card.locator('li b')).toHaveText(trending.symbols);
});

test('switching symbols while refreshing cancels the result for the old chart', async ({ page }) => {
  await mockSite(page);
  await page.goto('./?page=chart&symbol=NVDA&tf=1D');
  const update = page.getByRole('button', { name: '更新 NVDA K 線', exact: true });
  await expect(update).toBeEnabled();
  let release!: () => void;
  let started = false;
  await page.route('https://edge.test/functions/v1/market-chart**', async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.has('_refresh')) {
      started = true;
      await new Promise<void>(resolve => { release = resolve; });
    }
    await route.fulfill({ json: yahooChart(url.searchParams.get('symbol')!, url.searchParams.get('interval')!) }).catch(() => {});
  });
  await update.click();
  await expect.poll(() => started).toBe(true);
  // Route navigation within the mounted app, without reloading the document.
  await page.evaluate(() => {
    history.pushState(null, '', '?page=chart&symbol=AAPL&tf=1D');
    dispatchEvent(new PopStateEvent('popstate'));
  });
  const nextUpdate = page.getByRole('button', { name: '更新 AAPL K 線', exact: true });
  await expect(nextUpdate).toBeEnabled();
  const nextPrice = (await page.getByTestId('header-quote').textContent())!;
  release();
  await expect(page.getByTestId('header-quote')).toHaveText(nextPrice);
  await expect(page.getByRole('status').filter({ hasText: '已更新 NVDA' })).toHaveCount(0);
});

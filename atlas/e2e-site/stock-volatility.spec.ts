import { expect, test } from '@playwright/test';
import { dailyBars, mockSite, yahooChart } from './mocks';

// Deliberately simple synthetic prices: 20 TRs of 4, then one overnight gap with TR=12.
function fixture(symbol: string, count = 22) {
  const payload = yahooChart(symbol, '1d');
  const result = payload.chart.result[0];
  const times = dailyBars(symbol, count).map((bar) => bar.time);
  const series = result.indicators.quote[0];
  result.timestamp = times;
  series.open = times.map(() => 100);
  series.high = times.map(() => 102);
  series.low = times.map(() => 98);
  series.close = times.map(() => 100);
  series.volume = times.map(() => 1000);
  if (count === 22) {
    series.open[21] = 110;
    series.high[21] = 112;
    series.low[21] = 108;
    series.close[21] = 110;
  }
  // Current quote intentionally differs from the last complete daily close.
  result.meta.regularMarketPrice = 999;
  return payload;
}

test('daily volatility stays below PE and is independent of quote and chart timeframe', async ({ page }, testInfo) => {
  await mockSite(page);
  const intervals: string[] = [];
  await page.route('https://edge.test/functions/v1/market-chart**', (route) => {
    const url = new URL(route.request().url());
    const symbol = url.searchParams.get('symbol')!;
    const interval = url.searchParams.get('interval')!;
    intervals.push(interval);
    return route.fulfill({
      json: interval === '1d' ? fixture(symbol) : yahooChart(symbol, interval),
    });
  });
  await page.goto('./?page=chart&symbol=NVDA&tf=1W');
  const stats = page.getByTestId('stock-volatility');
  await expect(stats.locator('dd')).toHaveText(['4.00%', '4.00%']);
  await expect(stats).toContainText('2026-10-08');
  expect(intervals).toContain('1d');
  expect(intervals).toContain('1wk');
  await page.getByTestId('stock-stats-toggle').click();
  const geometry = await page.evaluate(() => ({
    pe: document.querySelector('[data-testid="valuation"]')!.getBoundingClientRect().bottom,
    volatility: document.querySelector('[data-testid="stock-volatility"]')!.getBoundingClientRect().top,
    overflow: document.scrollingElement!.scrollWidth - innerWidth,
  }));
  expect(geometry.volatility).toBeGreaterThanOrEqual(geometry.pe);
  expect(geometry.overflow).toBeLessThanOrEqual(0);
  // The floating panel covers the toolbar on phones; close it before switching timeframe, then reopen.
  await page.keyboard.press('Escape');
  await expect(stats).toBeHidden();
  await page.getByRole('button', { name: '週期 1D', exact: true }).click();
  await expect(stats.locator('dd')).toHaveText(['4.00%', '4.00%']);
  await page.getByTestId('stock-stats-toggle').click();
  await stats.locator('summary').click();
  await expect(stats.locator('p')).toContainText('ATR 原值 4.40，基準收盤 110.00');
  await expect(stats.locator('p')).toContainText('不含隔夜跳空');
  await stats.locator('summary').click();
  await page.screenshot({ path: testInfo.outputPath('volatility-header.png') });
});

test('switching symbols clears old values; short history shows no invented volatility', async ({ page }) => {
  await mockSite(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('https://edge.test/functions/v1/market-chart**', async (route) => {
    const url = new URL(route.request().url());
    const symbol = url.searchParams.get('symbol')!;
    if (symbol === 'NVDA') await gate;
    await route.fulfill({
      json: fixture(symbol, symbol === 'NVDA' ? 20 : 22),
    });
  });
  await page.goto('./?page=chart&symbol=2330.TW&tf=1D');
  await expect(page.getByTestId('stock-volatility').locator('dd')).toHaveText(['4.00%', '4.00%']);
  await page.getByRole('button', { name: /切換股票（目前/ }).click();
  const input = page.getByRole('dialog', { name: '搜尋股票' }).getByRole('combobox', { name: '搜尋股票' });
  await input.fill('NVDA');
  await input.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
  const stats = page.getByTestId('stock-volatility');
  await expect(stats.locator('dd')).toHaveText(['—', '—']);
  await expect(stats).toHaveAttribute('aria-busy', 'true');
  release();
  await expect(stats).toHaveAttribute('aria-busy', 'false');
  await expect(stats).toContainText('至少需 21 根完整日線');
  await expect(stats.locator('dd')).toHaveText(['—', '—']);
});

test('daily request failure leaves the weekly chart usable and shows unavailable values', async ({ page }) => {
  await mockSite(page);
  await page.route('https://edge.test/functions/v1/market-chart**', (route) => {
    const url = new URL(route.request().url());
    return url.searchParams.get('interval') === '1d'
      ? route.fulfill({ status: 502, json: { error: 'offline' } })
      : route.fulfill({
          json: yahooChart(url.searchParams.get('symbol')!, '1wk'),
        });
  });
  await page.goto('./?page=chart&symbol=NVDA&tf=1W');
  const stats = page.getByTestId('stock-volatility');
  await expect(stats).toContainText('日線暫時無法取得');
  await expect(stats.locator('dd')).toHaveText(['—', '—']);
  await expect(page.getByRole('button', { name: '週期 1W', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.tv-lightweight-charts canvas').first()).toBeVisible();
});

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

test('daily volatility and independent volume analysis stay beside recommendations without following quote or chart timeframe', async ({ page }, testInfo) => {
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
  await expect(stats.locator('[data-volatility-value]')).toHaveText(['4.00%', '4.00%']);
  await expect(stats.getByTestId('volatility-toggle')).toHaveAccessibleName('波動率');
  await stats.getByTestId('volatility-toggle').click();
  await expect(stats.getByRole('region', { name: '波動率', exact: true })).toBeVisible();
  await expect(stats).toContainText('2026-10-08');
  expect(intervals).toContain('1d');
  expect(intervals).toContain('1wk');
  const geometry = await page.evaluate(() => {
    const tools = document.querySelector('.ws-analysis-tools')!.getBoundingClientRect();
    const tabs = document.querySelector('.ws-stock-controls > .stock-tabs')!.getBoundingClientRect();
    return {
      volume: document.querySelector('[data-testid="stock-tab-volume"]')!.getBoundingClientRect().top,
      volatility: document.querySelector('[data-testid="stock-volatility"]')!.getBoundingClientRect().top,
      tabsOverlap: Math.max(0, Math.min(tools.right, tabs.right) - Math.max(tools.left, tabs.left)) *
        Math.max(0, Math.min(tools.bottom, tabs.bottom) - Math.max(tools.top, tabs.top)),
      overflow: document.scrollingElement!.scrollWidth - innerWidth,
    };
  });
  expect(Math.abs(geometry.volatility - geometry.volume)).toBeLessThanOrEqual(12);
  expect(geometry.tabsOverlap).toBe(0);
  expect(geometry.overflow).toBeLessThanOrEqual(0);
  // 價量 (volume analysis and the MA recommendation) is a view of the stock page, not a menu.
  await page.getByTestId('stock-tab-volume').click();
  await expect(page.getByRole('region', { name: '價量', exact: true })).toBeVisible();
  await expect(page.getByTestId('stock-tab-volume')).toHaveAttribute('aria-current', 'page');
  await expect(page.getByTestId('ma-recommendation')).toBeVisible();
  await expect(page.getByTestId('chart')).toBeHidden();
  // The volatility controls stay in the header and usable from every view.
  await expect(stats).toBeVisible();
  await page.getByTestId('stock-tab-chart').click();
  await expect(page.getByRole('region', { name: '價量', exact: true })).toBeHidden();
  await page.getByRole('button', { name: '週期 1D', exact: true }).click();
  await expect(stats.locator('[data-volatility-value]')).toHaveText(['4.00%', '4.00%']);
  await stats.getByTestId('volatility-toggle').click();
  const popup = stats.getByRole('region', { name: '波動率', exact: true });
  await popup.getByText('ATR（20）% 計算說明', { exact: true }).click();
  await expect(popup).toContainText('ATR 原值 4.40，基準收盤 110.00');
  await popup.getByText('20 日平均日內振幅 計算說明', { exact: true }).click();
  await expect(popup).toContainText('不含隔夜跳空');
  await page.keyboard.press('Escape');
  // Keyboard focus moving to a sibling dismisses the previous popup.
  await stats.getByTestId('volatility-toggle').focus();
  await page.keyboard.press('Enter');
  await expect(stats.getByTestId('volatility-toggle')).toHaveAttribute('aria-expanded', 'true');
  await page.getByTestId('source-info-toggle').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('source-info-toggle')).toHaveAttribute('aria-expanded', 'true');
  await expect(stats.getByTestId('volatility-toggle')).toHaveAttribute('aria-expanded', 'false');
  await page.keyboard.press('Escape');
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
  await expect(page.getByTestId('stock-volatility').locator('[data-volatility-value]')).toHaveText(['4.00%', '4.00%']);
  await page.getByRole('button', { name: /切換股票（目前/ }).click();
  const input = page.getByRole('dialog', { name: '搜尋股票' }).getByRole('combobox', { name: '搜尋股票' });
  await input.fill('NVDA');
  await input.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
  const stats = page.getByTestId('stock-volatility');
  await expect(stats.locator('[data-volatility-value]')).toHaveText(['—', '—']);
  await expect(stats).toHaveAttribute('aria-busy', 'true');
  release();
  await expect(stats).toHaveAttribute('aria-busy', 'false');
  await expect(stats).toContainText('至少需 21 根完整日線');
  await expect(stats.locator('[data-volatility-value]')).toHaveText(['—', '—']);
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
  await expect(stats.locator('[data-volatility-value]')).toHaveText(['—', '—']);
  await expect(page.getByRole('button', { name: '週期 1W', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.tv-lightweight-charts canvas').first()).toBeVisible();
});

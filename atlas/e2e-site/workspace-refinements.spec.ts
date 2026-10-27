import { expect, test } from '@playwright/test';
import { mockDeviceAccount } from './account-fixture';
import { mockSite } from './mocks';
import { closePanel, openPanel, searchSymbol } from '../e2e/site-helpers';

test('stock controls share one compact row and the normal chart fills the space above navigation', async ({ page }, testInfo) => {
  test.setTimeout(120000);
  await mockDeviceAccount(page);
  await mockSite(page);
  const sizes: { width: number; height: number; safeTop?: number; safeBottom?: number }[] = testInfo.project.name === 'desktop-chromium'
    ? [{ width: 1440, height: 900 }]
    : [{ width: 390, height: 710 }, { width: 320, height: 640 }, { width: 375, height: 667 }, { width: 390, height: 844, safeTop: 12, safeBottom: 20 }, { width: 844, height: 390 }];
  const measurements = [];
  for (const size of sizes) {
    await page.setViewportSize({ width: size.width, height: size.height });
    await page.goto('./?page=chart&symbol=2330.TW&tf=1D');
    await page.evaluate(({ safeTop = 0, safeBottom = 0 }) => {
      document.documentElement.style.setProperty('--safe-area-inset-top', `${safeTop}px`);
      document.documentElement.style.setProperty('--safe-area-inset-bottom', `${safeBottom}px`);
    }, size);
    await expect(page.getByTestId('ohlc-header')).toContainText('O ');
    await expect.poll(() => page.locator('.chart-host').evaluate(host =>
      Math.abs(host.getBoundingClientRect().bottom - host.querySelector('.tv-lightweight-charts')!.getBoundingClientRect().bottom),
    )).toBeLessThanOrEqual(1);
    const controls = page.locator('.ws-stock-controls');
    await expect(controls.getByRole('button')).toHaveText(['K線', '財務', '價量', '研究', '波動率']);
    await expect(page.locator('.app-shell')).not.toHaveClass(/chart-focus/);
    const bounds = await page.evaluate(() => {
      const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
      const buttons = [...document.querySelectorAll('.ws-stock-controls > .stock-tabs button, .ws-analysis-tools > .ws-stats-menu > button, .ws-volatility > .ws-stats-menu > button')].map(button => button.getBoundingClientRect());
      const valuation = box('[data-testid="valuation"]'), help = box('[data-testid="valuation-help-toggle"]');
      const main = document.getElementById('main')!;
      return {
        header: box('.ws-header').height,
        availableHeight: box('.app-shell').height,
        controlRowSpan: Math.max(...buttons.map(button => button.top)) - Math.min(...buttons.map(button => button.top)),
        controlsInsideViewport: buttons.every(button => button.left >= 0 && button.right <= innerWidth),
        helpRightOfValues: help.left >= valuation.right - 1,
        helpAligned: Math.abs((help.top + help.bottom) / 2 - (valuation.top + valuation.bottom) / 2),
        plot: box('.chart-host'), stage: box('.chart-stage'), navigation: box('.site-tabbar'),
        mainOverflow: main.scrollHeight - main.clientHeight,
        horizontalOverflow: document.scrollingElement!.scrollWidth - innerWidth,
      };
    });
    expect(bounds.controlRowSpan).toBeLessThanOrEqual(4);
    expect(bounds.controlsInsideViewport).toBe(true);
    expect(bounds.helpRightOfValues).toBe(true);
    expect(bounds.helpAligned).toBeLessThanOrEqual(4);
    expect(bounds.horizontalOverflow).toBeLessThanOrEqual(0);
    expect(bounds.mainOverflow).toBeLessThanOrEqual(1);
    if (bounds.navigation.height > 0) {
      expect(bounds.header).toBeLessThanOrEqual(124);
      expect(Math.abs(bounds.plot.bottom - bounds.navigation.top)).toBeLessThanOrEqual(2);
      expect(bounds.plot.height).toBeGreaterThan(size.height > size.width ? bounds.availableHeight * 0.5 : 40);
    }
    const plotHeight = bounds.plot.height;
    await page.getByTestId('valuation-help-toggle').click();
    await expect(page.getByRole('region', { name: '本益比說明', exact: true })).toBeVisible();
    await expect.poll(async () => (await page.locator('.chart-host').boundingBox())!.height).toBe(plotHeight);
    await page.keyboard.press('Escape');
    await page.getByTestId('source-info-toggle').click();
    const source = page.getByRole('region', { name: '行情資料說明', exact: true });
    await expect(source).toBeVisible();
    await expect(source.locator('.data-source')).not.toBeEmpty();
    await page.keyboard.press('Escape');
    measurements.push({ ...size, header: bounds.header, plot: plotHeight });
    if (size.width === 390 && size.height === 710)
      await page.screenshot({ path: testInfo.outputPath('compact-normal-chart.png') });
  }
  await testInfo.attach('chart-layout-measurements', { body: JSON.stringify(measurements), contentType: 'application/json' });
});

test('report navigation starts without MAs and preserves manually selected per-stock indicators', async ({ page }) => {
  await mockDeviceAccount(page);
  await mockSite(page);
  await page.goto('./?page=screener&group=tw_all');
  await page.getByRole('searchbox', { name: '在群組內篩選' }).fill('台泥');
  await page.locator('.screener-row').click();
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.locator('.indicator-chip')).toHaveCount(0);
  await expect(page.locator('[data-legacy-volume]')).toContainText('成交量（張）');
  await expect(page.locator('[data-volume-value]')).toContainText('張');
  await openPanel(page, 'indicators');
  await page.getByLabel('SMA 週期', { exact: true }).fill('13');
  await page.getByRole('button', { name: '新增 SMA', exact: true }).click();
  await closePanel(page);
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveText(['SMA 13']);
  await searchSymbol(page, 'NVDA');
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.locator('.indicator-chip')).toHaveCount(0);
  await expect(page.locator('[data-volume-value]')).toContainText('股');
  await searchSymbol(page, '1101');
  await expect(page.getByTestId('active-symbol')).toHaveText('1101.TW');
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveText(['SMA 13']);
  await page.reload();
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveText(['SMA 13']);
});

test('the bottom of the more-panels sheet stays reachable above the site navigation', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'desktop-chromium', 'Phone sheet layout');
  await mockDeviceAccount(page);
  await mockSite(page);
  await page.goto('./?page=chart&symbol=NVDA&tf=1D');
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await openPanel(page, 'settings');
  const debug = page.getByLabel('啟用除錯模式', { exact: true });
  await debug.scrollIntoViewIfNeeded();
  await expect.poll(() => page.evaluate(() => document.querySelector('.bottom-sheet')!.getBoundingClientRect().bottom - document.querySelector('.site-tabbar')!.getBoundingClientRect().top)).toBeLessThanOrEqual(1);
  const bounds = await page.evaluate(() => ({
    sheet: document.querySelector('.bottom-sheet')!.getBoundingClientRect().bottom,
    nav: document.querySelector('.site-tabbar')!.getBoundingClientRect().top,
    debug: document.querySelector('[aria-label="啟用除錯模式"]')!.getBoundingClientRect().bottom,
  }));
  expect(bounds.sheet).toBeLessThanOrEqual(bounds.nav + 1);
  expect(bounds.debug).toBeLessThan(bounds.nav);
  await debug.check();
  await expect(debug).toBeChecked();
  await page.screenshot({ path: testInfo.outputPath('sheet-bottom.png') });
  await closePanel(page);
  await expect(page.locator('.site-tabbar').getByRole('button', { name: '市場', exact: true })).toBeVisible();
});

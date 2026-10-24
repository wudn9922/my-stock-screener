import { expect, test } from '@playwright/test';
import { mockDeviceAccount } from './account-fixture';
import { mockSite } from './mocks';
import { closePanel, openPanel, searchSymbol } from '../e2e/site-helpers';

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
  await expect(page.locator('.site-tabbar').getByRole('button', { name: '大盤', exact: true })).toBeVisible();
});

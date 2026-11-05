import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';
import { mockDeviceAccount } from './account-fixture';
const routes = ['watchlist', 'markets', 'screener', 'chart&symbol=AAPL'];
test('A4 an empty device has no legacy card or empty notice on any page', async ({ page }) => {
  await mockDeviceAccount(page); await mockSite(page);
  for (const route of [...routes, 'console']) {
    await page.goto(`./?page=${route}`);
    await expect(page.locator('#main')).toBeVisible();
    await expect(page.getByTestId('legacy-import-card')).toHaveCount(0);
    await expect(page.getByText('這台裝置沒有可匯入的舊版資料。', { exact: true })).toHaveCount(0);
  }
});
test('A4 importable legacy data appears only in the My page footer, including after leaving My', async ({ page }, info) => {
  await mockDeviceAccount(page); await mockSite(page);
  await page.addInitScript(() => localStorage.setItem('atlas-site-preferences-screener', JSON.stringify({ colors: 'us', recent: [] })));
  for (const route of routes) {
    await page.goto(`./?page=${route}`);
    await expect(page.getByTestId('legacy-import-card')).toHaveCount(0);
  }
  await page.goto('./?page=console');
  const card = page.locator('.site-data-footer').getByTestId('legacy-import-card');
  await expect(card).toContainText('這台裝置有舊版資料');
  await card.scrollIntoViewIfNeeded();
  await expect(card).toBeVisible();
  expect(await page.getByTestId('legacy-import-card').count()).toBe(1);
  await page.screenshot({ path: info.outputPath('a4-my-footer-import.png') });
  await page.goto('./?page=chart&symbol=AAPL');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await expect(page.getByTestId('legacy-import-card')).toHaveCount(0);
  await expect(page.getByText('這台裝置沒有可匯入的舊版資料。', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('a4-clean-chart.png') });
});

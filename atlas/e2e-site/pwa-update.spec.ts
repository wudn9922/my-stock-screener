import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';

test('deployment checks preserve the current page until the user explicitly reloads', async ({ page }) => {
  await mockSite(page);
  let version = '', malformed = false;
  await page.route('**/atlas/app-version.json', route => route.fulfill({ json: malformed ? { app: 'other', version } : { app: 'atlas', version, scope: '/my-stock-screener/atlas/' } }));
  await page.goto('./?page=screener&group=tw_all');
  version = await page.locator('meta[name="atlas-build"]').getAttribute('content') ?? '';
  expect(version).toMatch(/^[a-f0-9]{12}$/);
  await page.getByRole('button', { name: '顯示設定', exact: true }).click();
  const panel = page.getByRole('region', { name: '版本更新', exact: true });
  await expect(panel.getByRole('status')).toHaveText('目前是最新版本');
  version = version === '0123456789ab' ? 'abcdef123456' : '0123456789ab';
  await panel.getByRole('button', { name: '檢查更新', exact: true }).click();
  await expect(panel).toContainText('網站已有新版');
  await expect(page).toHaveURL(/page=screener&group=tw_all/);
  await expect(panel).toContainText('重新開啟會清除目前暫存操作');
  await panel.getByRole('button', { name: '更新並重新開啟', exact: true }).click();
  await expect(page.locator('.screener-row').first()).toBeVisible();
  await expect(page).toHaveURL(/page=screener&group=tw_all/);
  malformed = true;
  await page.getByRole('button', { name: '顯示設定', exact: true }).click();
  await expect(panel.getByRole('status')).toContainText('暫時無法確認版本');
  await expect(panel.getByRole('button', { name: '更新並重新開啟' })).toHaveCount(0);
});

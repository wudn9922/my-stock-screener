import { expect, test } from '@playwright/test';
import { mockDeviceAccount } from './account-fixture';
import { mockSite } from './mocks';

const expectedPath = '/my-stock-screener/atlas/?page=chart&symbol=2330.TW&tf=1D';

async function openShare(page: import('@playwright/test').Page) {
  await mockDeviceAccount(page);
  await mockSite(page);
  await page.goto('./?page=chart&symbol=2330.TW&tf=1D&account=never-share&list=private-list');
  await expect(page.getByTestId('active-symbol')).toHaveText('2330.TW');
  await page.getByTestId('source-info-toggle').click();
  const panel = page.getByRole('region', { name: '行情資料說明' });
  const action = panel.getByRole('button', { name: '分享個股連結' });
  await expect(action).toBeVisible();
  expect((await action.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await action.click();
}

test('native share receives the exact public chart URL without account or list data', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'share', {
      configurable: true,
      value: async (data: ShareData) => {
        (window as Window & { __shared?: ShareData }).__shared = data;
      },
    });
  });
  await openShare(page);
  const expectedUrl = new URL(expectedPath, page.url()).href;
  await expect.poll(() => page.evaluate(() => (window as Window & { __shared?: ShareData }).__shared))
    .toEqual({ title: '2330.TW 個股圖表', url: expectedUrl });
});

test('clipboard fallback copies the exact public URL and confirms success', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'share', { configurable: true, value: undefined });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (url: string) => { (window as Window & { __copied?: string }).__copied = url; } },
    });
  });
  await openShare(page);
  await expect.poll(() => page.evaluate(() => (window as Window & { __copied?: string }).__copied))
    .toBe(new URL(expectedPath, page.url()).href);
  await expect(page.getByRole('region', { name: '行情資料說明' }).getByRole('status')).toContainText('連結已複製');
});

test('dismissed native share sheet does not show an error', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'share', {
      configurable: true,
      value: async () => { throw new DOMException('Share canceled', 'AbortError'); },
    });
  });
  await openShare(page);
  await expect(page.getByRole('region', { name: '行情資料說明' }).getByRole('status')).toBeEmpty();
});

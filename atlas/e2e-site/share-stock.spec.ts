import { expect, test, type Page } from '@playwright/test';
import { mockDeviceAccount } from './account-fixture';
import { mockSite } from './mocks';

const expectedPath = '/my-stock-screener/atlas/?page=chart&symbol=2330.TW&tf=1D';

async function stubShare(page: Page, mode: 'share' | 'clipboard' | 'abort') {
  await page.addInitScript((shareMode) => {
    const store = window as Window & { __shared?: ShareData; __copied?: string };
    Object.defineProperty(navigator, 'share', {
      configurable: true,
      value: shareMode === 'clipboard' ? undefined
        : shareMode === 'abort' ? async () => { throw new DOMException('Share canceled', 'AbortError'); }
        : async (data: ShareData) => { store.__shared = data; },
    });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (url: string) => { store.__copied = url; } },
    });
  }, mode);
}

/** Phone/tablet share from the header icon; desktop from the 行情資料說明 menu. */
async function share(page: Page) {
  await mockDeviceAccount(page);
  await mockSite(page);
  await page.goto('./?page=chart&symbol=2330.TW&tf=1D&account=never-share&list=private-list');
  if (page.viewportSize()!.width < 1100) {
    const headerShare = page.getByRole('button', { name: '分享個股連結' });
    await expect(headerShare).toBeVisible();
    expect((await headerShare.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await headerShare.click();
    return;
  }
  await page.getByTestId('source-info-toggle').click();
  const action = page.locator('.source-details').getByRole('button', { name: '分享個股連結' });
  await expect(action).toBeVisible();
  expect((await action.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await action.click();
}

test('native share gets the canonical chart URL without account or list data', async ({ page }) => {
  await stubShare(page, 'share');
  await share(page);
  const expected = new URL(expectedPath, page.url()).href;
  await expect.poll(() => page.evaluate(() => (window as Window & { __shared?: ShareData }).__shared?.url)).toBe(expected);
});

test('without native share the canonical URL is copied and confirmed', async ({ page }) => {
  await stubShare(page, 'clipboard');
  await share(page);
  await expect.poll(() => page.evaluate(() => (window as Window & { __copied?: string }).__copied))
    .toBe(new URL(expectedPath, page.url()).href);
  await expect(page.locator('.notice[role="status"]')).toContainText('已複製個股連結');
});

test('closing the native share sheet is not an error', async ({ page }) => {
  await stubShare(page, 'abort');
  await share(page);
  await page.waitForTimeout(300);
  await expect(page.locator('.notice[role="status"]')).toHaveCount(0);
});

import { expect, test, type Page } from '@playwright/test';
import { mockDeviceAccount } from './account-fixture';
import { mockSite } from './mocks';

type Violation = { directive: string; blockedURI: string; sourceFile: string };
type CspWindow = Window & { __cspViolations?: Violation[] };

async function watchViolations(page: Page) {
  await page.addInitScript(() => {
    const state = window as CspWindow;
    state.__cspViolations = [];
    document.addEventListener('securitypolicyviolation', (event) => {
      state.__cspViolations!.push({
        directive: event.violatedDirective,
        blockedURI: event.blockedURI,
        sourceFile: event.sourceFile,
      });
    });
  });
}

test('stock chart loads without Zod eval or other CSP violations', async ({ page }) => {
  await watchViolations(page);
  await mockDeviceAccount(page);
  await mockSite(page);
  await page.goto('./?page=chart&symbol=2330.TW&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content');
  expect(csp).toContain('https://edge.test');
  expect(csp).not.toContain('__ATLAS_MARKET_PROXY_ORIGIN__');
  expect(csp).not.toContain("'unsafe-eval'");
  await expect.poll(() => page.evaluate(() => (window as CspWindow).__cspViolations ?? []))
    .toEqual([]);
});

test('LINE handoff HTML loads with the same resolved CSP and no violations', async ({ page }) => {
  await watchViolations(page);
  await page.goto('./line-login-launch.html');
  await expect(page.getByRole('heading', { name: '返回原本的 Atlas 完成登入' })).toBeVisible();
  const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content');
  expect(csp).toContain('https://edge.test');
  expect(csp).not.toContain('__ATLAS_MARKET_PROXY_ORIGIN__');
  await expect.poll(() => page.evaluate(() => (window as CspWindow).__cspViolations ?? []))
    .toEqual([]);
});

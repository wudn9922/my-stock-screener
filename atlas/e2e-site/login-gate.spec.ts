import { expect, test, type Page } from '@playwright/test';
import { accessEnvelope, DEVICE_USER_ID } from './account-fixture';
import { buildReport, mockSite } from './mocks';

const TOKEN = `atlasdev.${'d'.repeat(43)}`;
const SESSION_KEY = 'atlas-device-session-v1';

async function installedApp(page: Page, remembered: { token: string; userId: string } | null = null) {
  await page.addInitScript(session => {
    Object.defineProperty(navigator, 'standalone', { configurable: true, get: () => true });
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'Mozilla/5.0 (iPhone) AppleWebKit/605.1.15' });
    const match = window.matchMedia.bind(window);
    window.matchMedia = query => {
      const result = match(query);
      if (query.includes('display-mode: standalone')) Object.defineProperty(result, 'matches', { value: true });
      return result;
    };
    // Seed once: a later reload must see what the page itself left behind.
    if (session && !sessionStorage.getItem('seeded')) {
      localStorage.setItem('atlas-device-session-v1', JSON.stringify(session));
      sessionStorage.setItem('seeded', '1');
    }
  }, remembered);
}

function settings(page: Page, respond: (body: Record<string, unknown>) => { status?: number; json: unknown }) {
  const actions: Record<string, unknown>[] = [];
  const sdk: string[] = [];
  void page.route('**/functions/v1/user-settings', route => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    actions.push(body);
    return route.fulfill(respond(body));
  });
  void page.route('https://static.line-scdn.net/**', route => { sdk.push(route.request().url()); return route.abort(); });
  return { actions, sdk };
}

test('the installed app opens on a login page; guest browsing is a per-launch choice', async ({ page }, info) => {
  await installedApp(page);
  const { actions, sdk } = settings(page, () => ({ status: 403, json: { ok: false } }));
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./');
  const gate = page.getByRole('main', { name: 'Atlas' });
  await expect(gate.getByRole('button', { name: '使用 LINE 登入', exact: true })).toBeVisible();
  await expect(gate.getByRole('checkbox', { name: /記住這台裝置（90 天內免登入）/ })).toBeChecked();
  await expect(page.locator('.site-tabbar, .site-rail')).toHaveCount(0);
  const box = (await gate.getByRole('button', { name: '使用 LINE 登入', exact: true }).boundingBox())!;
  expect(box.height).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: info.outputPath('login-gate.png') });
  await gate.getByRole('button', { name: '先以訪客瀏覽', exact: true }).click();
  await expect(page.locator(info.project.name === 'desktop-chromium' ? '.site-rail' : '.site-tabbar')).toBeVisible();
  // The browsing pages no longer carry the login bar.
  await expect(page.getByRole('button', { name: '使用 LINE 登入', exact: true })).toHaveCount(0);
  expect(actions).toEqual([]);
  expect(sdk).toEqual([]);
  expect(await page.evaluate(key => localStorage.getItem(key), SESSION_KEY)).toBeNull();
  await page.reload();
  await expect(page.getByRole('main', { name: 'Atlas' }).getByRole('button', { name: '先以訪客瀏覽', exact: true })).toBeVisible();
});

test('a remembered device opens straight into the account without LINE, and logout forgets it', async ({ page }) => {
  await installedApp(page, { token: TOKEN, userId: DEVICE_USER_ID });
  const { actions, sdk } = settings(page, body => body.action === 'access.get'
    ? { json: accessEnvelope(DEVICE_USER_ID, 'device') }
    : { json: { ok: true, data: { revoked: true } } });
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=console');
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'device');
  await expect(page.locator('[data-storage-mode]')).toContainText('此裝置已記住登入');
  await expect(page.getByRole('button', { name: '先以訪客瀏覽' })).toHaveCount(0);
  expect(actions[0]).toEqual({ action: 'access.get', idToken: TOKEN });
  expect(sdk).toEqual([]);
  await page.getByRole('button', { name: '登出 LINE', exact: true }).click();
  await expect(page.getByRole('main', { name: 'Atlas' }).getByRole('button', { name: '使用 LINE 登入', exact: true })).toBeVisible();
  await expect.poll(() => actions.some(action => action.action === 'device.session.revoke' && action.idToken === TOKEN)).toBe(true);
  expect(await page.evaluate(key => localStorage.getItem(key), SESSION_KEY)).toBeNull();
  await page.reload();
  await expect(page.getByRole('main', { name: 'Atlas' }).getByRole('button', { name: '使用 LINE 登入', exact: true })).toBeVisible();
  expect(actions.filter(action => action.action === 'access.get')).toHaveLength(1);
});

test('a device the server no longer accepts returns to the login page with an explanation', async ({ page }) => {
  await installedApp(page, { token: TOKEN, userId: DEVICE_USER_ID });
  const { actions } = settings(page, () => ({ status: 401, json: { ok: false, code: 'device_session', error: '此裝置的登入已失效，請重新登入。' } }));
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./');
  const gate = page.getByRole('main', { name: 'Atlas' });
  await expect(gate.getByRole('alert')).toHaveText('此裝置的登入已失效，請重新登入。');
  expect(await page.evaluate(key => localStorage.getItem(key), SESSION_KEY)).toBeNull();
  // A rejected device is forgotten, not revoked again.
  expect(actions.map(action => action.action)).toEqual(['access.get']);
});

test('a temporary server error keeps the remembered device for the next launch', async ({ page }) => {
  await installedApp(page, { token: TOKEN, userId: DEVICE_USER_ID });
  settings(page, () => ({ status: 503, json: { ok: false, error: '裝置登入服務暫時無法使用，請稍後重試。' } }));
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=console');
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
  await expect(page.locator('[data-storage-mode]')).toContainText('無法確認儲存授權');
  expect(await page.evaluate(key => localStorage.getItem(key), SESSION_KEY)).toBe(JSON.stringify({ token: TOKEN, userId: DEVICE_USER_ID }));
});

test('an ordinary browser tab keeps browsing without a login page; login stays on 我的', async ({ page }) => {
  settings(page, () => ({ status: 403, json: { ok: false } }));
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./');
  await expect(page.getByRole('button', { name: '先以訪客瀏覽' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '使用 LINE 登入', exact: true })).toHaveCount(0);
  await page.goto('./?page=console');
  await expect(page.getByRole('button', { name: '使用 LINE 登入', exact: true })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: /記住這台裝置/ })).toBeChecked();
});

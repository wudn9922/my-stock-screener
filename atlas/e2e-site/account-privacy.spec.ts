import { expect, test, type Page } from '@playwright/test';
import { buildReport, mockSite } from './mocks';
import { chooseDrawingTool } from '../e2e/drawing-picker-helper';
import { accessEnvelope, DEVICE_USER_ID, installLineAccount, OWNER_USER_ID, SECOND_USER_ID, type TestStorageMode } from './account-fixture';

async function openParameters(page: Page) {
  await page.getByRole('button', { name: '均線參數', exact: true }).click();
  await expect(page.getByRole('region', { name: '原有均線參數控制台' })).toBeVisible();
}
async function createParameters(page: Page, name: string) {
  const details = page.locator('.parameter-console details').filter({ hasText: '管理自訂分組' });
  await details.locator('summary').click();
  await details.getByRole('textbox', { name: '新增個人分組名稱' }).fill(name);
  await details.getByRole('button', { name: '新增分組', exact: true }).click();
  await expect(page.getByRole('combobox', { name: '參數股票分組' })).toContainText(name);
  await page.getByRole('textbox', { name: '參數股票代號' }).fill('NVDA');
  await page.getByRole('combobox', { name: '參數股票分組' }).selectOption({ label: name });
  await page.getByRole('textbox', { name: '股票均線參數' }).fill('13, 34');
  await page.getByRole('button', { name: '新增股票參數', exact: true }).click();
  await expect(page.getByRole('table', { name: '個人股票參數' })).toContainText('NVDA');
}
async function drawHorizontal(page: Page) {
  await expect(page.getByTestId('chart').locator('canvas').first()).toBeVisible();
  await expect(page.getByTestId('ohlc-header')).toContainText('O');
  await chooseDrawingTool(page, '水平線');
  const bounds = (await page.getByTestId('chart').boundingBox())!;
  const x = bounds.x + bounds.width * 0.5, y = bounds.y + bounds.height * 0.4;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 25, y + 10, { steps: 5 });
  await page.mouse.up();
  await expect(page.locator('.chart-status')).toContainText('畫線 1 條');
}
async function installPolicy(page: Page, initial = DEVICE_USER_ID, admin = false) {
  await installLineAccount(page, initial, `token-${initial}`);
  const policies = new Map<string, { storageMode: TestStorageMode; revision: string; label: string }>([
    [initial, { storageMode: 'device', revision: 'test-1', label: '本人' }],
    [SECOND_USER_ID, { storageMode: 'device', revision: 'test-1', label: 'B' }],
  ]);
  const privateRequests: string[] = [];
  const adminSaves: Record<string, unknown>[] = [];
  let revision = 1;
  await page.route('**/functions/v1/**', async route => {
    if (route.request().method() === 'GET') return route.fallback();
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const userId = String(body.idToken ?? '').replace(/^token-/, '');
    const action = String(body.action ?? 'chart-drawings');
    const policy = policies.get(userId) ?? { storageMode: 'none' as const, revision: 'none-default', label: '' };
    if (action === 'access.get') return route.fulfill({ json: accessEnvelope(userId, policy.storageMode, admin && userId === OWNER_USER_ID, policy.revision) });
    if (action === 'admin.access.list' && admin && userId === OWNER_USER_ID)
      return route.fulfill({ json: { ok: true, data: [...policies].map(([id, row]) => ({ userId: id, ...row })) } });
    if (action === 'admin.access.save' && admin && userId === OWNER_USER_ID) {
      adminSaves.push(body);
      const grant = { storageMode: body.storageMode as TestStorageMode, revision: `test-${++revision}`, label: String(body.label ?? '') };
      policies.set(String(body.userId), grant);
      return route.fulfill({ json: { ok: true, data: { userId: body.userId, ...grant } } });
    }
    privateRequests.push(action);
    return route.fulfill({ status: 403, json: { ok: false, error: 'No cloud persistence granted in this fixture' } });
  });
  const switchAccount = async (userId: string | null) => {
    await page.evaluate(id => {
      const state = globalThis as unknown as { __lineAccount: string | null; __lineToken: string };
      state.__lineAccount = id;
      state.__lineToken = `token-${id}`;
      window.dispatchEvent(new Event('focus'));
    }, userId);
    await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', userId ? policies.get(userId)?.storageMode ?? 'none' : 'none');
    if (userId) await expect(page.getByRole('complementary', { name: '目前儲存權限' })).toContainText(userId);
  };
  return { policies, privateRequests, adminSaves, switchAccount };
}

test('ordinary visitors can group, set MAs and draw, with zero application persistence or personal cloud requests', async ({ page }) => {
  await page.addInitScript(() => {
    const state = globalThis as unknown as { __persistentCalls: string[] };
    state.__persistentCalls = [];
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) { state.__persistentCalls.push(`storage:${key}`); return setItem.call(this, key, value); };
    const open = IDBFactory.prototype.open;
    IDBFactory.prototype.open = function (...args) { state.__persistentCalls.push(`idb:${args[0]}`); return open.apply(this, args); };
    if (globalThis.CacheStorage) {
      const cacheOpen = CacheStorage.prototype.open;
      CacheStorage.prototype.open = function (name) { state.__persistentCalls.push(`cache:${name}`); return cacheOpen.call(this, name); };
    }
  });
  const personalRequests: string[] = [];
  await page.route('**/functions/v1/user-settings', route => { personalRequests.push(route.request().url()); return route.fulfill({ status: 403, json: { ok: false } }); });
  await page.route('**/functions/v1/chart-drawings', route => { personalRequests.push(route.request().url()); return route.fulfill({ status: 403, json: { ok: false } }); });
  page.on('request', request => { if (/github\.com|api\.github\.com/.test(request.url()) && request.method() !== 'GET') personalRequests.push(request.url()); });
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=console');
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
  await expect(page.getByRole('button', { name: '管理員', exact: true })).toHaveCount(0);
  await openParameters(page);
  await createParameters(page, '訪客測試分組');
  await page.getByRole('button', { name: '圖表', exact: true }).click();
  await drawHorizontal(page);
  await expect(page.getByTestId('save-state')).toContainText('僅本次瀏覽暫存');
  expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length, calls: (globalThis as unknown as { __persistentCalls: string[] }).__persistentCalls }))).toEqual({ local: 0, session: 0, calls: [] });
  expect(personalRequests).toEqual([]);
  await page.reload();
  await expect(page.locator('.chart-status')).toContainText('畫線 0 條');
  await page.getByRole('button', { name: '控制台', exact: true }).click();
  await openParameters(page);
  await expect(page.getByRole('table', { name: '個人股票參數' }).locator('tbody tr')).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: '參數股票分組' })).not.toContainText('訪客測試分組');
  expect(personalRequests).toEqual([]);
});

test('device grants isolate accounts, keep their own parameters after reload, and stop persistence after revocation', async ({ page }) => {
  const policy = await installPolicy(page);
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=console');
  await openParameters(page);
  await createParameters(page, 'A 的私人分組');
  await page.reload();
  await openParameters(page);
  await expect(page.getByRole('table', { name: '個人股票參數' })).toContainText('13 / 34');
  await policy.switchAccount(SECOND_USER_ID);
  await openParameters(page);
  await expect(page.getByRole('table', { name: '個人股票參數' }).locator('tbody tr')).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: '參數股票分組' })).not.toContainText('A 的私人分組');
  await createParameters(page, 'B 的私人分組');
  await policy.switchAccount(DEVICE_USER_ID);
  await openParameters(page);
  await expect(page.getByRole('combobox', { name: '參數股票分組' })).toContainText('A 的私人分組');
  await expect(page.getByRole('combobox', { name: '參數股票分組' })).not.toContainText('B 的私人分組');
  await page.getByRole('button', { name: '圖表', exact: true }).click();
  await drawHorizontal(page);
  await page.getByRole('button', { name: '控制台', exact: true }).click();
  await policy.switchAccount(SECOND_USER_ID);
  await page.getByRole('button', { name: '圖表', exact: true }).click();
  await expect(page.locator('.chart-status')).toContainText('畫線 0 條');
  await page.getByRole('button', { name: '控制台', exact: true }).click();
  await policy.switchAccount(DEVICE_USER_ID);
  await page.getByRole('button', { name: '圖表', exact: true }).click();
  await expect(page.locator('.chart-status')).toContainText('畫線 1 條');
  await page.getByRole('button', { name: '控制台', exact: true }).click();
  const before = await page.evaluate(() => Object.fromEntries(Object.entries(localStorage)));
  policy.policies.set(DEVICE_USER_ID, { storageMode: 'none', revision: 'revoked', label: 'A' });
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
  await openParameters(page);
  await expect(page.getByRole('table', { name: '個人股票參數' }).locator('tbody tr')).toHaveCount(0);
  await createParameters(page, '撤權後只在記憶體');
  expect(await page.evaluate(() => Object.fromEntries(Object.entries(localStorage)))).toEqual(before);
  expect(policy.privateRequests).toEqual([]);
  await expect(page.getByRole('button', { name: '管理員', exact: true })).toHaveCount(0);
});

for (const mode of ['none', 'device'] as const) {
  test(`${mode} users can rename personal lists without enabling cloud storage`, async ({ page }) => {
    const policy = mode === 'device' ? await installPolicy(page) : null;
    await mockSite(page, { report: buildReport(20), fundamentals: true });
    await page.goto('./?page=console');
    await page.getByRole('button', { name: '建立手動清單', exact: true }).click();
    await page.getByRole('textbox', { name: '手動清單名稱' }).fill('我的研究');
    await page.getByRole('textbox', { name: '手動清單股票' }).fill('2330.TW');
    await page.getByRole('button', { name: mode === 'none' ? '套用手動清單' : '儲存手動清單', exact: true }).click();
    await page.getByText('清單名稱與設定', { exact: true }).click();
    await page.getByRole('textbox', { name: '重新命名清單' }).fill('重新命名研究');
    await page.getByRole('button', { name: mode === 'none' ? '套用名稱' : '儲存名稱', exact: true }).click();
    await expect(page.getByRole('group', { name: '我的自選清單' })).toContainText('重新命名研究');
    await expect(page.getByRole('button', { name: '重試同步', exact: true })).toHaveCount(0);
    await page.reload();
    const tabs = page.getByRole('group', { name: '我的自選清單' });
    if (mode === 'device') await expect(tabs).toContainText('重新命名研究');
    else await expect(tabs).not.toContainText('重新命名研究');
    expect(policy?.privateRequests ?? []).toEqual([]);
  });
}

test('only the verified owner can manage per-account storage modes even while the owner uses device storage', async ({ page }) => {
  const policy = await installPolicy(page, OWNER_USER_ID, true);
  await mockSite(page);
  await page.goto('./?page=console');
  await page.getByRole('button', { name: '管理員', exact: true }).click();
  const panel = page.getByRole('region', { name: '管理員儲存權限' });
  await expect(panel).toBeVisible();
  await panel.getByRole('textbox', { name: '授權 LINE 帳號 ID' }).fill(DEVICE_USER_ID);
  await panel.getByRole('textbox', { name: '授權帳號辨識名稱' }).fill('朋友的裝置');
  await panel.getByRole('combobox', { name: '帳號保存位置' }).selectOption('device');
  await panel.getByRole('button', { name: '設定此帳號權限', exact: true }).click();
  await expect.poll(() => policy.adminSaves.length).toBe(1);
  expect(policy.adminSaves[0]).toMatchObject({ userId: DEVICE_USER_ID, storageMode: 'device', label: '朋友的裝置', idToken: `token-${OWNER_USER_ID}` });
  await expect(panel).toContainText('朋友的裝置');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await policy.switchAccount(SECOND_USER_ID);
  await expect(page.getByRole('button', { name: '管理員', exact: true })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '管理員儲存權限' })).toHaveCount(0);
  expect(policy.privateRequests).toEqual([]);
});

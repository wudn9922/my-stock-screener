import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { installLineAccount } from './account-fixture';
import { researchFixtureBody } from './research-fixtures';
import { createMexcFixture, MEXC_USER, TEST_KEY, TEST_SECRET } from '../tests/fixtures/mexcHarness';
const panel = (page: Page) => page.getByRole('region', { name: '持股', exact: true });
const mexc = (page: Page) => panel(page).getByRole('region', { name: 'MEXC 自動同步' });
async function setup(page: Page, mode: 'cloud' | 'device' | 'none' = 'cloud') {
  const fixture = createMexcFixture(mode),
    request = fixture.handler();
  await page.clock.install({ time: fixture.now() });
  await installLineAccount(page, MEXC_USER);
  await mockSite(page, { fundamentals: true });
  await page.route('**/report/research/**', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: researchFixtureBody(new URL(route.request().url()).pathname.split('/research/')[1]!).toString(),
    }),
  );
  const bodies: Record<string, unknown>[] = [],
    urls: string[] = [];
  page.on('request', (request) => urls.push(request.url()));
  await page.route('**/functions/v1/user-settings', async (route) => {
    const body = route.request().postDataJSON();
    bodies.push(body);
    const result = await request(body);
    await route.fulfill({
      status: result.status,
      headers: Object.fromEntries(result.headers.entries()),
      json: result.body,
    });
  });
  await page.goto('./?page=watchlist');
  await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  await panel(page).getByRole('button', { name: '新增帳戶', exact: true }).click();
  await panel(page).getByRole('combobox', { name: '券商／交易所' }).selectOption('mexc');
  return { ...fixture, bodies, urls };
}
async function enterKeys(page: Page) {
  await panel(page).getByRole('button', { name: '使用 MEXC API 連線' }).click();
  await expect(mexc(page).getByLabel('MEXC API Key', { exact: true })).toHaveAttribute('type', 'password');
  await mexc(page).getByLabel('MEXC API Key', { exact: true }).fill(TEST_KEY);
  await mexc(page).getByLabel('MEXC Secret Key', { exact: true }).fill(TEST_SECRET);
  await expect(mexc(page).getByRole('button', { name: /^連接並讀取 MEXC/ })).toBeDisabled();
  await mexc(page)
    .getByRole('checkbox', { name: /我確認只開讀取權限/ })
    .check();
  await mexc(page)
    .getByRole('button', { name: /^連接並讀取 MEXC/ })
    .click();
}
async function confirmCosts(page: Page) {
  await expect(mexc(page)).toContainText('已連線');
  await mexc(page).getByRole('textbox', { name: 'BTC 平均成本', exact: true }).fill('50000');
  await mexc(page).getByRole('textbox', { name: 'ETH 平均成本', exact: true }).fill('3000');
  await mexc(page).getByRole('textbox', { name: 'USDT 平均成本', exact: true }).fill('1');
  await mexc(page).getByRole('checkbox', { name: '已補上新增餘額的成本，並核對所有平均成本' }).check();
  await mexc(page).getByRole('button', { name: '確認成本並套用同步餘額' }).click();
  await expect(panel(page).getByRole('article')).toHaveCount(3);
}
async function tick(page: Page, fixture: ReturnType<typeof createMexcFixture>, ms = 31000) {
  fixture.advance(ms);
  await page.clock.fastForward(ms);
}
async function privateStorage(page: Page) {
  const storage = await page.evaluate(() =>
    JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }),
  );
  expect(storage).not.toContain(TEST_KEY);
  expect(storage).not.toContain(TEST_SECRET);
  expect(storage).not.toContain('holdings:v1');
}

test('website connects a read-only MEXC key once, reviews costs and shows actual MEXC P&L at 390px', async ({
  page,
}) => {
  const f = await setup(page);
  await enterKeys(page);
  await expect(mexc(page)).toContainText('API Key 末四碼 9876');
  await expect(mexc(page)).toContainText('有新的餘額或數量增加');
  await expect(panel(page)).toContainText('暫不納入總計');
  await expect(panel(page).getByRole('article')).toHaveCount(0);
  await confirmCosts(page);
  const btc = panel(page).getByRole('article', { name: 'BTC/USDT 持股' });
  await expect(btc).toContainText('65,000.0000');
  await expect(btc).toContainText('+1,800.00');
  await expect(btc).toContainText('MEXC 現貨');
  await expect(btc).toContainText('0.12');
  expect(f.bodies.filter((body) => body.action === 'holdings.mexc.connect')).toHaveLength(1);
  expect(f.urls.join('\n')).not.toMatch(/test-mexc|api\.mexc\.com/);
  expect(await page.locator('body').innerText()).not.toContain(TEST_KEY);
  expect(await page.locator('body').innerText()).not.toContain(TEST_SECRET);
  expect(JSON.stringify([...f.connections.values()])).not.toContain(TEST_SECRET);
  await privateStorage(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  for (const button of await mexc(page).getByRole('button').all())
    if (await button.isVisible()) expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await btc.scrollIntoViewIfNeeded();
  await page.screenshot({ path: test.info().outputPath('mexc-positions.png') });
});

test('increases require cost review, decreases preserve costs, and disconnect deletes the key while retaining holdings', async ({
  page,
}) => {
  const f = await setup(page);
  await enterKeys(page);
  await confirmCosts(page);
  await tick(page, f);
  f.setBalances([{ asset: 'BTC', free: '0.15', locked: '0' }]);
  await mexc(page)
    .getByRole('button', { name: /^同步餘額/ })
    .click();
  await expect(mexc(page).getByRole('textbox', { name: 'BTC 平均成本', exact: true })).toHaveValue('50000');
  await expect(panel(page).getByRole('article', { name: 'BTC/USDT 持股' })).toContainText('等候確認同步成本');
  await mexc(page).getByRole('textbox', { name: 'BTC 平均成本', exact: true }).fill('55000');
  await mexc(page).getByRole('checkbox', { name: '已補上新增餘額的成本，並核對所有平均成本' }).check();
  await mexc(page).getByRole('button', { name: '確認成本並套用同步餘額' }).click();
  await expect(panel(page).getByRole('article')).toHaveCount(1);
  await expect(panel(page).getByRole('article')).toContainText('+1,500.00');
  await tick(page, f);
  f.setBalances([{ asset: 'BTC', free: '0.06', locked: '0' }]);
  await mexc(page)
    .getByRole('button', { name: /^同步餘額/ })
    .click();
  await expect(panel(page).getByRole('article')).toContainText('0.06');
  await expect(panel(page).getByRole('article')).toContainText('55,000.0000');
  page.once('dialog', (dialog) => dialog.accept());
  await mexc(page).getByRole('button', { name: '中斷連線', exact: true }).click();
  await expect(mexc(page)).toContainText('已中斷連線並刪除伺服器金鑰');
  expect(f.connections.size).toBe(0);
  await expect(panel(page).getByRole('article')).toHaveCount(1);
  await privateStorage(page);
});

test('write-enabled keys fail clearly, clear both inputs and preserve the original portfolio', async ({ page }) => {
  const f = await setup(page);
  f.setFailure('write-key');
  await enterKeys(page);
  await expect(mexc(page).getByRole('alert')).toContainText('只含讀取權限');
  await expect(mexc(page).getByLabel('MEXC API Key', { exact: true })).toHaveValue('');
  await expect(mexc(page).getByLabel('MEXC Secret Key', { exact: true })).toHaveValue('');
  expect(f.connections.size).toBe(0);
  expect(f.errorLogs).toEqual([]);
  await privateStorage(page);
});

test('a manual revision change during cost editing can be recovered without applying stale costs', async ({ page }) => {
  const f = await setup(page);
  await enterKeys(page);
  await mexc(page).getByRole('textbox', { name: 'BTC 平均成本', exact: true }).fill('50000');
  f.documents.get(MEXC_USER)!.accounts[0]!.revision++;
  await panel(page).getByRole('button', { name: '重新載入持股', exact: true }).click();
  await expect(mexc(page)).toContainText('持股已手動修改');
  await mexc(page).getByRole('button', { name: '捨棄未儲存成本，準備重新同步' }).click();
  await tick(page, f);
  await mexc(page)
    .getByRole('button', { name: /^同步餘額/ })
    .click();
  await confirmCosts(page);
  expect(f.documents.get(MEXC_USER)!.accounts[0]!.holdings).toHaveLength(3);
});

for (const mode of ['none', 'device'] as const)
  test(`${mode} can still import MEXC manually but has no key entry or API synchronization`, async ({ page }) => {
    const f = await setup(page, mode);
    await expect(panel(page)).toContainText('自動讀取 MEXC 需要雲端儲存授權');
    await expect(panel(page).getByRole('button', { name: '使用 MEXC API 連線' })).toHaveCount(0);
    await panel(page).getByRole('button', { name: '繼續匯入' }).click();
    await expect(panel(page).getByRole('textbox', { name: '貼上庫存表格' })).toBeVisible();
    expect(f.bodies.filter((body) => String(body.action).startsWith('holdings.mexc.'))).toHaveLength(0);
    expect(f.upstream).toHaveLength(0);
  });

test('visible-page sync uses the stored key without resending it and pauses while hidden', async ({ page }) => {
  const f = await setup(page);
  await enterKeys(page);
  await confirmCosts(page);
  const start = f.upstream.length;
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
  });
  await tick(page, f, 301000);
  expect(f.upstream.length).toBe(start);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(() => f.upstream.length).toBe(start + 3);
  await expect.poll(() => f.documents.get(MEXC_USER)?.accounts[0]?.revision).toBe(3);
  await tick(page, f, 301000);
  await expect.poll(() => f.upstream.length).toBe(start + 6);
  expect(f.bodies.filter((body) => body.apiKey || body.apiSecret)).toHaveLength(1);
  await privateStorage(page);
});

test('automatic synchronization keeps the selected account when server order changes after a reload', async ({
  page,
}) => {
  const f = await setup(page);
  await enterKeys(page);
  await confirmCosts(page);
  const document = f.documents.get(MEXC_USER)!;
  const mexcId = document.accounts[0]!.account.id;
  document.accounts.push({
    account: {
      id: '22222222-2222-4222-8222-222222222222',
      name: '其他台股帳戶',
      broker: 'kgi',
      kind: 'tw-broker',
      currency: 'TWD',
    },
    holdings: [],
    revision: 1,
  });
  await page.reload();
  await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  await expect(mexc(page)).toContainText('已連線');
  const count = f.upstream.length;
  await tick(page, f, 301000);
  await expect.poll(() => f.upstream.length).toBe(count + 3);
  await expect(panel(page).getByRole('combobox', { name: '持股帳戶' })).toHaveValue(mexcId);
  await expect(mexc(page)).toBeVisible();
  expect(f.bodies.filter((body) => body.apiSecret)).toHaveLength(1);
  page.once('dialog', (dialog) => dialog.accept());
  await panel(page).getByRole('button', { name: '刪除帳戶', exact: true }).click();
  await expect(panel(page).getByRole('combobox', { name: '持股帳戶' })).toHaveValue(
    '22222222-2222-4222-8222-222222222222',
  );
  await expect(panel(page).getByRole('region', { name: '持股總計' })).not.toContainText('總計尚未完整');
  expect(f.connections.size).toBe(0);
});

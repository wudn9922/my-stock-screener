import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { installLineAccount } from './account-fixture';
import { researchFixtureBody } from './research-fixtures';
import { createMexcFixture, MEXC_USER, TEST_KEY, TEST_SECRET } from '../tests/fixtures/mexcHarness';
import { futuresResponses, assetsPath, positionsPath } from '../tests/fixtures/mexc/futures';
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

test('futures long/short positions are read-only, fit a phone and leave spot totals unchanged across manual and automatic sync', async ({ page }, info) => {
  const f = await setup(page);
  await enterKeys(page); await confirmCosts(page);
  const totals = panel(page).getByRole('region', { name: '持股總計', exact: true });
  await expect(totals).toContainText(/1 USD＝[\d.]+ TWD/);
  const previous = (await totals.textContent())!;
  f.setFutures(futuresResponses());
  await tick(page, f);
  await mexc(page).getByRole('button', { name: /^同步餘額/ }).click();
  const region = mexc(page).getByRole('region', { name: '合約部位', exact: true });
  await expect(region).toContainText('僅供查看，未計入持股總值');
  await expect(region).toContainText('1,000.12 USDT');
  const long = region.getByRole('article', { name: 'BTC_USDT 多單 合約部位' });
  const short = region.getByRole('article', { name: 'ETH_USDT 空單 合約部位' });
  await expect(long).toContainText('0.0005 BTC');
  await expect(long).toContainText('10× · 逐倉');
  await expect(long).toContainText('-0.5 USDT · -1.67%');
  await expect(long).toContainText('54,000 USDT');
  await expect(short).toContainText('5× · 全倉');
  await expect(short).toContainText('+1.25 USDT · +6.25%');
  await expect(long.locator('.mexc-futures-long')).toHaveCSS('color', 'rgb(47, 189, 131)');
  await expect(short.locator('.mexc-futures-short')).toHaveCSS('color', 'rgb(244, 106, 117)');
  await expect(totals).toHaveText(previous);
  expect(f.documents.get(MEXC_USER)!.accounts[0]!.holdings).toHaveLength(3);
  expect(JSON.stringify(f.documents.get(MEXC_USER))).not.toContain('BTC_USDT');
  await region.locator('header').scrollIntoViewIfNeeded();
  await page.clock.runFor(100);
  await page.screenshot({ path: info.outputPath('mexc-futures.png') });
  const changed = futuresResponses();
  (changed[assetsPath] as Record<string, unknown>[])[0]!.equity = '999999999999999.123456789012345678';
  (changed[positionsPath] as Record<string, unknown>[])[0]!.unRealizedPnl = '-999999999999999.123456789012345678';
  f.setFutures(changed); await tick(page, f, 301000);
  await expect(region).toContainText('999,999,999,999,999.123456789012345678 USDT');
  await expect(totals).toHaveText(previous);
  await region.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  expect(await region.evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  await page.clock.runFor(100);
  await page.screenshot({ path: info.outputPath('mexc-futures-long-numbers.png') });
  await privateStorage(page);
});

test('futures permission, failure and empty-position notices preserve a successful spot portfolio', async ({ page }) => {
  const f = await setup(page); await enterKeys(page); await confirmCosts(page);
  const region = mexc(page).getByRole('region', { name: '合約部位', exact: true });
  await expect(region).toContainText('這把 API Key 沒有合約讀取權限。請到 MEXC 編輯 Key，勾選『合約－讀取』（不要勾交易）後重新同步。');
  const unavailable = futuresResponses();
  unavailable[assetsPath] = () => Response.json({ success: false, code: 500 });
  f.setFutures(unavailable); await tick(page, f);
  await mexc(page).getByRole('button', { name: /^同步餘額/ }).click();
  await expect(region).toContainText('合約資料暫時無法讀取，現貨已同步。');
  await expect(mexc(page).getByRole('alert')).toHaveCount(0);
  const empty = futuresResponses(); empty[positionsPath] = [];
  f.setFutures(empty); await tick(page, f);
  await mexc(page).getByRole('button', { name: /^同步餘額/ }).click();
  await expect(region).toContainText('目前沒有合約持倉。');
  await expect(region.getByRole('article')).toHaveCount(0);
  await expect(panel(page).getByRole('article')).toHaveCount(3);
});

test('unsupported names show a count on connect and automatic sync while other balances continue normally', async ({
  page,
}) => {
  const f = await setup(page);
  f.setBalances([
    { asset: 'bTc', free: '0.1', locked: '0.02' },
    { asset: 'ETH', free: '2', locked: '0' },
    { asset: 'USDT', free: '100', locked: '0' },
    { asset: 'PRIVATE-ASSET', free: '7', locked: '0' },
    { asset: 'VERYLONGPRIVATECOIN123456789', free: '8', locked: '0' },
  ]);
  await enterKeys(page);
  await expect(mexc(page)).toContainText('有 2 個 MEXC 資產名稱格式不支援，未匯入；其他持股已同步。');
  await expect(mexc(page).getByRole('alert')).toHaveCount(0);
  await expect(mexc(page)).not.toContainText('PRIVATE-ASSET');
  await expect(mexc(page)).not.toContainText('VERYLONGPRIVATECOIN123456789');
  await confirmCosts(page);
  await expect(mexc(page)).toContainText('有 2 個 MEXC 資產名稱格式不支援');
  f.setBalances([
    { asset: 'btc', free: '0.1', locked: '0.02' },
    { asset: 'PRIVATE-ASSET', free: '7', locked: '0' },
  ]);
  await tick(page, f, 301000);
  await expect(mexc(page)).toContainText('有 1 個 MEXC 資產名稱格式不支援，未匯入；其他持股已同步。');
  await expect(panel(page).getByRole('article')).toHaveCount(1);
  f.setBalances([{ asset: 'BTC', free: '0.1', locked: '0.02' }]);
  await tick(page, f);
  await mexc(page)
    .getByRole('button', { name: /^同步餘額/ })
    .click();
  await expect(mexc(page)).not.toContainText('MEXC 資產名稱格式不支援');
  await expect(panel(page).getByRole('article')).toHaveCount(1);
  await privateStorage(page);
});

test('an all-skipped snapshot connects successfully and displays the warning with an empty portfolio', async ({
  page,
}) => {
  const f = await setup(page);
  f.setBalances([{ asset: 'PRIVATE-ASSET', free: '7', locked: '0' }]);
  await enterKeys(page);
  await expect(mexc(page)).toContainText('已連線');
  await expect(mexc(page)).toContainText('有 1 個 MEXC 資產名稱格式不支援，未匯入；其他持股已同步。');
  await expect(mexc(page).getByRole('alert')).toHaveCount(0);
  await expect(panel(page).getByRole('article')).toHaveCount(0);
});

test('80-day key reminder and expired-key error preserve confirmed holdings and last successful sync', async ({ page }, info) => {
  const f = await setup(page);
  await enterKeys(page);
  await confirmCosts(page);
  const saved = structuredClone(f.documents.get(MEXC_USER));
  const previous = structuredClone([...f.connections.values()][0]!);
  await expect(mexc(page).getByTestId('mexc-key-age-warning')).toHaveCount(0);
  f.advance(80 * 86400000);
  await page.clock.setFixedTime(f.now());
  await page.clock.runFor(1100);
  await expect(mexc(page).getByTestId('mexc-key-age-warning')).toHaveText('MEXC 未綁 IP 的 Key 約 90 天到期，請準備更換');
  await mexc(page).getByTestId('mexc-key-age-warning').scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath('mexc-key-age.png') });
  f.setFailure('bad-key');
  await mexc(page).getByRole('button', { name: /^同步餘額/ }).click();
  await expect(mexc(page).getByRole('alert')).toContainText('MEXC API Key 已失效，請到 MEXC 重新建立唯讀 Key 後重新填入');
  await expect(panel(page).getByRole('article')).toHaveCount(3);
  expect(f.documents.get(MEXC_USER)).toEqual(saved);
  expect([...f.connections.values()][0]!.syncedAt).toBe(previous.syncedAt);
  await expect(mexc(page)).toContainText('上次同步');
  await mexc(page).getByRole('alert').scrollIntoViewIfNeeded();
  expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  expect(await page.locator('body').innerText()).not.toContain(TEST_KEY);
  expect(await page.locator('body').innerText()).not.toContain(TEST_SECRET);
  await privateStorage(page);
  await page.screenshot({ path: info.outputPath('mexc-key-expired.png') });
});

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

import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { accessEnvelope, installLineAccount, mockDeviceAccount } from './account-fixture';
import { researchFixtureBody } from './research-fixtures';

const panel = (page: Page) => page.getByRole('region', { name: '持股', exact: true });
const importer = (page: Page) => panel(page).getByRole('region', { name: '匯入持股', exact: true });
const fixture = (name: string) => new URL(`../tests/fixtures/holdings/${name}`, import.meta.url).pathname;
async function open(page: Page) {
  await mockSite(page, { fundamentals: true });
  await page.route('**/report/research/**', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: researchFixtureBody(new URL(route.request().url()).pathname.split('/research/')[1]!).toString(),
    }),
  );
  await page.goto('./?page=watchlist');
  await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  await expect(panel(page)).toBeVisible();
}
async function create(page: Page, broker = 'cathay') {
  await panel(page).getByRole('button', { name: '新增帳戶', exact: true }).click();
  await panel(page).getByRole('combobox', { name: '券商／交易所' }).selectOption(broker);
  await panel(page).getByRole('textbox', { name: '帳戶名稱' }).fill('PRIVATE-LOCAL-ONLY');
  await panel(page).getByRole('button', { name: '繼續匯入', exact: true }).click();
}
async function paste(page: Page, value: string) {
  await importer(page).getByRole('textbox', { name: '貼上庫存表格' }).fill(value);
  await importer(page).getByRole('button', { name: '辨識貼上內容' }).click();
}
async function save(page: Page) {
  await importer(page).getByRole('button', { name: '用這份資料取代此帳戶持股' }).click();
  await expect(importer(page)).not.toBeVisible();
  await expect(panel(page).getByRole('region', { name: '帳戶損益' })).toBeVisible();
}
async function layout(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  for (const button of await panel(page).getByRole('button').all())
    if (await button.isVisible()) expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
}

test('guest paste previews errors, supports correction/skip, shows P&L and leaks no private input', async ({
  page,
}) => {
  const requests: string[] = [],
    logs: string[] = [];
  page.on('request', (request) => requests.push(request.url() + (request.postData() ?? '')));
  page.on('console', (message) => logs.push(message.text()));
  await open(page);
  await create(page);
  await expect(panel(page)).toContainText('重新開啟就會消失');
  await paste(page, '代號\t股數\t平均成本\n2330\t123.4567890123\t815.252525252525\n6488\t錯誤\t100\n9999\t2\t1');
  await expect(importer(page).getByRole('button', { name: '用這份資料取代此帳戶持股' })).toBeDisabled();
  await importer(page).getByRole('textbox', { name: '第 2 列股數' }).fill('2000');
  await importer(page).getByRole('checkbox', { name: '略過此列' }).nth(2).check();
  await layout(page);
  await save(page);
  await expect(panel(page).getByRole('article')).toHaveCount(2);
  const position = panel(page).getByRole('article', { name: '2330.TW 持股' });
  await expect(position).toContainText('0.1234567890123 張');
  await expect(position).not.toContainText('無報價');
  await expect(position).toContainText('今日損益');
  await expect(position.locator('dd').nth(3)).toHaveText(/[\d,]+\.\d{2}/);
  await position.scrollIntoViewIfNeeded();
  await page.screenshot({ path: test.info().outputPath('holdings-overview.png'), fullPage: true });
  expect(requests.join('\n')).not.toMatch(/PRIVATE-LOCAL-ONLY|123\.4567890123|815\.252525252525/);
  expect(logs.join('\n')).not.toMatch(/PRIVATE-LOCAL-ONLY|123\.4567890123|815\.252525252525/);
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage }))).not.toContain('holdings:v1');
  await page.getByRole('button', { name: '清單', exact: true }).click();
  await expect(page.getByRole('button', { name: '建立手動清單', exact: true })).toBeVisible();
  await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  await expect(panel(page).getByRole('article')).toHaveCount(2);
  await page.reload();
  await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  await expect(panel(page)).toContainText('尚未匯入持股');
});

test('CSV upload replaces the selected device account, survives reload, and opens the stock chart', async ({
  page,
}) => {
  await mockDeviceAccount(page);
  await open(page);
  await create(page, 'kgi');
  await importer(page).getByLabel('上傳 CSV／XLSX').setInputFiles(fixture('inventory.csv'));
  await expect(importer(page)).toContainText('預覽確認');
  await save(page);
  await expect(panel(page).getByRole('article')).toHaveCount(2);
  await panel(page).getByRole('button', { name: '匯入持股', exact: true }).click();
  await expect(panel(page).getByRole('button', { name: '修正持股', exact: true })).toBeDisabled();
  await expect(panel(page).getByRole('button', { name: '新增帳戶', exact: true })).toBeDisabled();
  await paste(page, '代號,股數,平均成本\n2330,4500,800');
  await save(page);
  await expect(panel(page).getByRole('article')).toHaveCount(1);
  await expect(panel(page).getByRole('article')).toContainText('4.5 張');
  await panel(page).locator('summary').filter({ hasText: '台股賣出成本試算' }).click();
  await panel(page).getByRole('checkbox', { name: '扣除預估賣出成本' }).check();
  await panel(page).getByRole('textbox', { name: '手續費折扣' }).fill('0.28');
  await expect(panel(page).getByRole('article')).toContainText('預估賣出成本');
  await page.reload();
  await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  await expect(panel(page).getByRole('article')).toHaveCount(1);
  await layout(page);
  await panel(page)
    .getByRole('button', { name: /2330.TW/ })
    .click();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'chart');
  await expect(page.getByTestId('active-symbol')).toHaveText('2330.TW');
});

test('manual column mapping is retained for this account and invalid rows stay editable', async ({ page }) => {
  await mockDeviceAccount(page);
  await open(page);
  await create(page, 'pocket');
  const input = '證券編碼\t持有數\t購入價\n2330\t壞資料\t800';
  await paste(page, input);
  await expect(importer(page).getByRole('heading', { name: '欄位對應' })).toBeVisible();
  await importer(page).getByRole('combobox', { name: '代號欄位' }).selectOption('0');
  await importer(page).getByRole('combobox', { name: '股數欄位' }).selectOption('1');
  await importer(page).getByRole('combobox', { name: '均價欄位' }).selectOption('2');
  await importer(page).getByRole('button', { name: '預覽解析結果' }).click();
  await expect(importer(page).getByRole('alert')).toContainText('股數');
  await importer(page).getByRole('textbox', { name: '第 1 列股數' }).fill('1000');
  await save(page);
  await panel(page).getByRole('button', { name: '匯入持股', exact: true }).click();
  await paste(page, input.replace('壞資料', '2000'));
  await expect(importer(page).getByRole('heading', { name: /預覽確認/ })).toBeVisible();
  await save(page);
  await expect(panel(page).getByRole('article')).toContainText('2 張');
});

test('fake KGI Excel requires missing costs to be supplied and converts lots to shares', async ({ page }) => {
  await open(page);
  await create(page, 'kgi');
  await importer(page).getByLabel('上傳 CSV／XLSX').setInputFiles(fixture('kgi-inventory.xlsx'));
  await expect(importer(page).getByRole('heading', { name: '欄位對應' })).toBeVisible();
  await importer(page).getByRole('combobox', { name: '代號欄位' }).selectOption('1');
  await importer(page).getByRole('combobox', { name: '股數欄位' }).selectOption('2');
  await importer(page).getByRole('combobox', { name: '數量單位' }).selectOption('lots');
  await importer(page).getByRole('button', { name: '預覽解析結果' }).click();
  await expect(importer(page).getByRole('button', { name: '用這份資料取代此帳戶持股' })).toBeDisabled();
  await expect(importer(page).getByRole('textbox', { name: '第 1 列股數' })).toHaveValue('1000');
  await importer(page).getByRole('textbox', { name: '第 1 列均價' }).fill('800');
  await importer(page).getByRole('textbox', { name: '第 2 列均價' }).fill('1000');
  await save(page);
  await expect(panel(page).getByRole('article')).toHaveCount(2);
  await layout(page);
});

test('Firstrade CSV rebuilds current shares and costs with an explicit skipped-rows review', async ({ page }) => {
  await open(page);
  await create(page, 'firstrade');
  await importer(page).getByLabel('上傳 CSV／XLSX').setInputFiles(fixture('firstrade-transactions.csv'));
  await expect(importer(page)).toContainText('略過 1 筆非交易列');
  await expect(importer(page).getByRole('textbox', { name: '第 1 列股數' })).toHaveValue('15');
  await expect(importer(page).getByRole('textbox', { name: '第 1 列均價' })).toHaveValue('110.1');
  await expect(importer(page).getByRole('button', { name: '用這份資料取代此帳戶持股' })).toBeDisabled();
  await importer(page).getByRole('checkbox', { name: '已核對券商庫存與略過的非交易列' }).check();
  await save(page);
  await expect(panel(page).getByRole('article')).toHaveCount(1);
  await expect(panel(page).getByRole('article')).toContainText('15 股');
  await expect(panel(page)).toContainText('匯率日期');
});

test('undeployed cloud holdings fail clearly and never fall back to device persistence', async ({ page }) => {
  await installLineAccount(page);
  await page.route('**/functions/v1/user-settings', (route) => {
    const body = route.request().postDataJSON();
    return body.action === 'access.get'
      ? route.fulfill({ json: accessEnvelope(undefined, 'cloud') })
      : route.fulfill({ status: 503, json: { ok: false, error: 'schema missing' } });
  });
  await open(page);
  await expect(panel(page).getByRole('alert')).toContainText('無法載入雲端持股');
  await expect(panel(page).getByRole('button', { name: '新增帳戶', exact: true })).toBeDisabled();
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage }))).not.toContain('holdings:v1');
});

test('unsupported crypto stays unquoted rather than guessed and USDT is kept separate', async ({ page }) => {
  await open(page);
  await create(page, 'mexc');
  await paste(page, 'Symbol,Quantity,Avg Cost\nNOTQUOTED/USDT,0.05,2');
  await save(page);
  await expect(panel(page).getByRole('article')).toContainText('無報價');
  await expect(panel(page)).toContainText('無 USDT/TWD 匯率');
  await expect(panel(page)).toContainText('總計尚未完整');
  await layout(page);
});

test('BTC uses the existing actual USDT quote and opens its existing commodity chart', async ({ page }) => {
  await open(page);
  await create(page, 'mexc');
  await paste(page, 'Symbol,Quantity,Avg Cost\nBTC/USDT,0.001,60000');
  await save(page);
  const position = panel(page).getByRole('article');
  await expect(position).not.toContainText('無報價');
  await expect(position).toContainText('Binance');
  await position.getByRole('button', { name: 'BTC/USDT' }).click();
  await expect(page).toHaveURL(/page=world&view=commodities/);
});

test('cloud import sends normalized positions only and reloads from the server without a local copy', async ({
  page,
}) => {
  await installLineAccount(page);
  let document = { version: 1, accounts: [] as unknown[] };
  const positions: Record<string, unknown>[] = [];
  await page.route('**/functions/v1/user-settings', (route) => {
    const body = route.request().postDataJSON();
    if (body.action === 'access.get') return route.fulfill({ json: accessEnvelope(undefined, 'cloud') });
    if (body.action.startsWith('holdings.')) {
      positions.push(body);
      if (body.action === 'holdings.replaceAccount')
        document = { version: 1, accounts: [{ account: body.account, holdings: body.holdings, revision: 1 }] };
      return route.fulfill({ json: { ok: true, data: document } });
    }
    return route.fulfill({ json: { ok: true, data: [] } });
  });
  await open(page);
  await create(page);
  await paste(page, '代號,股數,平均成本,私人備註\n2330,1000,800,RAW-FILE-MUST-NOT-UPLOAD');
  await save(page);
  expect(JSON.stringify(positions)).not.toContain('RAW-FILE-MUST-NOT-UPLOAD');
  expect(positions.filter((body) => body.action === 'holdings.replaceAccount')).toHaveLength(1);
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage }))).not.toContain('holdings:v1');
  await page.reload();
  await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  await expect(panel(page).getByRole('article')).toHaveCount(1);
});

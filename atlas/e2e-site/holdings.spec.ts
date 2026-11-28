import { openListActions } from './archive-fixture';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { accessEnvelope, accountPreference, installLineAccount, mockDeviceAccount, mockMarketLogin } from './account-fixture';
import { researchFixtureBody } from './research-fixtures';
import { overlayMis, parseMisRow } from '../../supabase/functions/market-chart/twse-mis';

const panel = (page: Page) => page.getByRole('region', { name: '持股', exact: true });
const importer = (page: Page) => panel(page).getByRole('region', { name: '匯入持股', exact: true });
const fixture = (name: string) => new URL(`../tests/fixtures/holdings/${name}`, import.meta.url).pathname;
async function selectHoldings(page: Page) {
  const toolbar = page.locator('.watchlist-board .wb-toolbar');
  await toolbar.waitFor({ state: 'visible' });
  const sheet = page.getByRole('dialog', { name: '清單操作' });
  if (await sheet.isVisible()) await page.getByRole('button', { name: '關閉清單操作' }).click();
  if (await page.locator('.site-tabbar').isVisible()) {
    // On phones 持股 is the fourth underline tab; desktop retains the original view switch.
    await toolbar.getByRole('button', { name: '持股', exact: true }).click();
  } else {
    await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  }
}
async function open(page: Page, afterMocks?: () => Promise<unknown>) {
  await mockSite(page, { fundamentals: true });
  await page.route('**/report/research/**', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: researchFixtureBody(new URL(route.request().url()).pathname.split('/research/')[1]!).toString(),
    }),
  );
  await afterMocks?.();
  await page.goto('./?page=watchlist');
  await selectHoldings(page);
  await expect(panel(page)).toBeVisible();
}
async function create(page: Page, broker = 'cathay') {
  await panel(page).getByRole('button', { name: '新增帳戶', exact: true }).click();
  await panel(page).getByRole('combobox', { name: '帳戶市場' }).selectOption(broker === 'mexc' ? 'crypto' : (broker.endsWith('-us') || broker === 'firstrade') ? 'us' : 'tw');
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
  // Quotes come from market-chart, which needs a LINE login; storage stays guest-only (no grant).
  await mockMarketLogin(page);
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
  if (await page.getByRole('button', { name: '新增清單' }).isVisible()) {
    await page.getByRole('group', { name: '行情來源' }).getByRole('button', { name: '我的清單' }).click();
  } else {
    await page.getByRole('button', { name: '清單', exact: true }).click();
  }
  await openListActions(page);
  await expect(page.getByRole('button', { name: '建立手動清單', exact: true })).toBeVisible();
  await selectHoldings(page);
  await expect(panel(page).getByRole('article')).toHaveCount(2);
  await page.reload();
  await selectHoldings(page);
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
  await selectHoldings(page);
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

test('a Taiwan broker 複委託 account holds US stocks in USD', async ({ page }) => {
  await mockMarketLogin(page); // quotes come from market-chart, which needs a LINE login
  await open(page);
  await panel(page).getByRole('button', { name: '新增帳戶', exact: true }).click();
  await expect(panel(page)).toContainText('永豐美股請用匯入 CSV');
  await expect(panel(page).getByRole('combobox', { name: '帳戶市場' }).getByRole('option')).toHaveText(['台股', '美股', '加密貨幣']);
  await panel(page).getByRole('button', { name: '取消', exact: true }).click();
  await create(page, 'cathay-us');
  await paste(page, 'Symbol,Quantity,Avg Cost\nAAPL,10,150\n2330,1,500');
  // A US-stock account resolves US tickers only; a Taiwan code is flagged instead of guessed.
  await expect(importer(page)).toContainText('代號找不到');
  await importer(page).getByRole('checkbox', { name: '略過此列' }).nth(1).check();
  await save(page);
  const position = panel(page).getByRole('article', { name: 'AAPL 持股' });
  await expect(position).toBeVisible();
  await expect(position).not.toContainText('無報價');
  await expect(panel(page)).toContainText('USD 市值');
  await layout(page);
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
  await selectHoldings(page);
  await expect(panel(page).getByRole('article')).toHaveCount(1);
});


test('six compact stock rows precede totals and operations, with full details on expansion', async ({ page }, info) => {
  await open(page); await create(page, 'kgi');
  await paste(page, '代號,股數,平均成本\n2330,1000,100\n2317,1000,100\n2454,1000,100\n2308,1000,100\n2881,1000,100\n6488,1000,100');
  await save(page);
  const rows = panel(page).locator('.holdings-position');
  await expect(rows).toHaveCount(6);
  await expect(rows.first().locator('details:has(> summary.holding-compact-row)')).not.toHaveAttribute('open');
  await expect(rows.first().locator('summary.holding-compact-row').getByText('均價 100.00', { exact: true })).toBeVisible();
  await layout(page);
  await panel(page).evaluate(el => { for (let parent: HTMLElement | null = el as HTMLElement; parent; parent = parent.parentElement) parent.scrollTop = 0; window.scrollTo(0, 0); });
  const boxes = await rows.evaluateAll(nodes => nodes.map(node => { const r=node.getBoundingClientRect(); return { bottom:r.bottom, height:r.height }; }));
  expect(boxes.every(b => b.height <= 72)).toBe(true);
  if (info.project.name.includes('mobile') || info.project.name.includes('iphone'))
    expect(boxes[4]!.bottom).toBeLessThanOrEqual(await page.evaluate(() => innerHeight - 64));
  const totals = panel(page).getByRole('region', { name: '持股總計', exact: true });
  expect((await totals.boundingBox())!.y).toBeGreaterThan((await rows.last().boundingBox())!.y);
  await page.screenshot({ path: info.outputPath('holdings-compact-stocks.png') });
  await rows.first().locator('summary.holding-compact-row').click();
  await expect(rows.first().getByText('平均成本／股', { exact: true })).toBeVisible();
  await expect(rows.first().getByText('今日損益', { exact: true })).toBeVisible();
  await expect(rows.first().getByRole('button', { name: '隱藏', exact: true })).toBeVisible();
  await layout(page);
});

test('compact holding rows contain long symbols and extreme average costs at 390px', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page);
  await create(page, 'mexc');
  await paste(page, [
    'Symbol,Quantity,Avg Cost',
    'LONGESTCOINTICKER123/USDT,1000,0.0000000012345',
    'BTC/USDT,999999999999999,999999999999999',
    'ETH/USDT,1,3000',
  ].join('\n'));
  await save(page);

  const rows = panel(page).locator('.holding-compact-row');
  await expect(rows).toHaveCount(3);
  await expect(rows.getByText('均價 0.000000001235', { exact: true })).toBeVisible();
  await expect(rows.getByText('均價 999,999,999,999,999.00', { exact: true })).toBeVisible();
  await layout(page);
  const boxes = await rows.evaluateAll(elements => elements.map(row => ({
    height: row.getBoundingClientRect().height,
    overflow: row.scrollWidth - row.clientWidth,
    identityWidth: row.firstElementChild!.getBoundingClientRect().width,
  })));
  expect(boxes.every(box => box.height <= 71 && box.overflow <= 0 && box.identityWidth > 0)).toBe(true);

  if (info.project.name === 'iphone-webkit') {
    await panel(page).evaluate(el => {
      for (let parent: HTMLElement | null = el as HTMLElement; parent; parent = parent.parentElement) parent.scrollTop = 0;
      window.scrollTo(0, 0);
    });
    await page.screenshot({ path: info.outputPath('holdings-compact-390.png'), scale: 'css' });
  }

  const btc = panel(page).getByRole('article', { name: 'BTC/USDT 持股' });
  await btc.locator('summary.holding-compact-row').click();
  await expect(btc.locator('dd[title="999999999999999"]').first()).toHaveText('999,999,999,999,999.00');
});


test('account creation offers CSV markets and only supported API connections', async ({ page }, info) => {
  // SinoPac API sync is offered to the administrator only (sinopac.spec covers everyone else).
  await mockDeviceAccount(page, true);
  await open(page);
  await panel(page).getByRole('button', { name: '新增帳戶', exact: true }).click();
  await expect(panel(page).getByLabel('新增方式').getByRole('option')).toHaveText(['API 自動同步', '匯入 CSV']);
  await expect(panel(page).getByRole('button', { name: '繼續匯入', exact: true })).toBeDisabled();
  await expect(panel(page)).toContainText('永豐美股請用匯入 CSV');
  await panel(page).getByLabel('新增方式').selectOption('api');
  await panel(page).getByLabel('API 服務').selectOption('sinopac');
  await expect(panel(page).getByRole('region', { name: '永豐台股自動同步' })).toContainText('不勾「交易」');
  await expect(panel(page).getByRole('button', { name: '同步', exact: true })).toBeDisabled();
  await expect(importer(page)).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('account-methods-390.png') });
  await panel(page).getByLabel('新增方式').selectOption('csv');
  await panel(page).getByLabel('帳戶名稱', { exact: true }).fill('永豐美股');
  await panel(page).getByLabel('帳戶市場').selectOption('us');
  await panel(page).getByRole('button', { name: '繼續匯入', exact: true }).click();
  await paste(page, 'Symbol,Quantity,Avg Cost\nAAPL,10,150');
  await save(page);
  await expect(panel(page).getByRole('article', { name: 'AAPL 持股' })).toBeVisible();
  const saved = await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.endsWith(':holdings:v1')).map(([, value]) => JSON.parse(value)));
  expect(saved[0].accounts[0].account).toMatchObject({ broker: 'other', kind: 'us-broker', currency: 'USD', name: '永豐美股' });
});

const liveStart = Date.parse('2026-11-27T11:43:03+08:00');
const finalClose = Date.parse('2026-11-27T13:30:00+08:00');
const previousCloses = { '2330.TW': 2145, '6488.TWO': 518 };
type LiveSymbol = keyof typeof previousCloses;

async function seedHoldings(page: Page, accounts: { name: string; positions: [LiveSymbol, string, string][] }[]) {
  const uuid = (index: number) => '10000000-0000-4000-8000-' + String(index).padStart(12, '0');
  const entries = accounts.map(({ name, positions }, index) => {
    const accountId = uuid(index + 1);
    return {
      account: { id: accountId, name, kind: 'tw-broker', broker: 'other', currency: 'TWD' }, revision: 1,
      holdings: positions.map(([symbol, quantity, averageCost], n) => ({
        id: uuid(101 + index * 100 + n), accountId, symbol, quantity, averageCost, market: 'TW',
        costCurrency: 'TWD', source: 'manual', updatedAt: '2026-11-26T09:00:00Z',
      })),
    };
  });
  await page.addInitScript(({ key, document }) => localStorage.setItem(key, JSON.stringify(document)), {
    key: accountPreference('holdings:v1'), document: { version: 1, accounts: entries },
  });
}

function dailyPayload(symbol: LiveSymbol) {
  const previous = previousCloses[symbol];
  const close = [previous - 5, previous];
  return { chart: { result: [{
    meta: { symbol, currency: 'TWD', exchangeTimezoneName: 'Asia/Taipei', gmtoffset: 28800,
      regularMarketPrice: previous, regularMarketTime: Date.parse('2026-11-26T13:30:00+08:00') / 1000 },
    timestamp: ['2026-11-25', '2026-11-26'].map(date => Date.parse(`${date}T09:00:00+08:00`) / 1000),
    indicators: { quote: [{ open: close, high: close, low: close, close, volume: [100, 100] }] },
  }], error: null } };
}

function recordedMis(symbol: LiveSymbol, phase: 'first' | 'second') {
  // Nested trades from the official Nov 27 11:43 and 11:44 MIS batches.
  const raw = symbol === '2330.TW'
    ? { c: '2330', ex: 'tse', o: '2145', h: '2175', l: '2135', y: '2145', v: '16465',
        trade: { z: phase === 'first' ? '2165' : '2170', t: phase === 'first' ? '11:40:45' : '11:43:20' } }
    : { c: '6488', ex: 'otc', o: '517', h: '526', l: '505', y: '518', v: '3627',
        trade: { z: '506', t: '11:35:33' } };
  const t = phase === 'first' ? '11:42:05' : '11:44:05';
  const row = parseMisRow({ ...raw, t, d: '20261127', z: '-' }, symbol,
    liveStart + (phase === 'first' ? 0 : 79_000))!;
  return overlayMis(dailyPayload(symbol), row);
}

function recordedClosingPrice() {
  // Regular-market Yahoo response collected after the Nov 27 session, when the proxy no longer uses MIS.
  const payload = dailyPayload('2330.TW');
  const result = payload.chart.result[0]!;
  const quote = result.indicators.quote[0]!;
  const closingBar = { open: 2145, high: 2180, low: 2135, close: 2180, volume: 38_980_378 };
  result.meta.regularMarketPrice = closingBar.close;
  result.meta.regularMarketTime = finalClose / 1000 + 2;
  result.timestamp.push(Date.parse('2026-11-27T09:00:00+08:00') / 1000);
  for (const key of Object.keys(closingBar) as (keyof typeof closingBar)[]) quote[key] = [...quote[key], closingBar[key]];
  return payload;
}

test('received MIS prints update holding, account and market totals and preserve the final close', async ({ page }, info) => {
  await page.clock.install({ time: liveStart });
  await mockDeviceAccount(page);
  await seedHoldings(page, [{ name: '測試台股', positions: [['2330.TW', '1000', '2000']] }]);
  const accountChoice = panel(page).getByRole('combobox', { name: '持股帳戶' });
  let phase: 'first' | 'second' | 'close' = 'first';
  let release!: () => void;
  const received = new Promise<void>(resolve => { release = resolve; });
  let requests = 0;
  await open(page, () => page.route('https://edge.test/functions/v1/market-chart**', async route => {
    const params = new URL(route.request().url()).searchParams;
    if (params.get('symbol') === '2330.TW' && params.get('interval') === '1d')
      return route.fulfill({ json: dailyPayload('2330.TW') });
    if (!params.get('quotes')?.split(',').includes('2330.TW')) return route.fallback();
    requests++;
    await received;
    const result = phase === 'close' ? recordedClosingPrice() : recordedMis('2330.TW', phase);
    return route.fulfill({ json: { results: { '2330.TW': result }, errors: {} } });
  }));
  await accountChoice.selectOption({ label: '測試台股 · TWD' });
  const position = panel(page).getByRole('article', { name: '2330.TW 持股' });
  await position.locator('summary.holding-compact-row').click();
  const account = panel(page).locator('details').filter({ has: page.locator('summary', { hasText: /^帳戶小計$/ }) });
  const market = panel(page).getByTestId('holdings-spot-subtotals');
  const total = panel(page).getByRole('region', { name: '台股總計' });
  try {
    await expect.poll(() => requests).toBeGreaterThan(0);
    await expect(position.getByText('2,145.00', { exact: true })).toBeVisible();
    await expect(position).toContainText('非即時');
    release();
    await expect(position.getByText('2,165.00', { exact: true })).toBeVisible();
    await expect(position).toContainText('證交所即時');
    await expect(position).toContainText('+165,000.00');
    await expect(position).toContainText('+20,000.00');
    await expect(total).toContainText('2,165,000.00 TWD');
    await expect(total).toContainText('165,000.00 TWD');
    await account.locator('summary').click();
    await expect(account).toContainText('TWD 市值 2,165,000.00');
    await expect(account).toContainText('今日損益 +20,000.00');
    await market.locator('summary').click();
    await expect(market).toContainText('未實現損益 +165,000.00');
    await expect(market).toContainText('今日損益 +20,000.00');
    phase = 'second';
    await page.clock.fastForward(80_000);
    await expect(position.getByText('2,170.00', { exact: true })).toBeVisible();
    await expect(position).toContainText('+170,000.00');
    await expect(total).toContainText('2,170,000.00 TWD');
    await expect(account).toContainText('今日損益 +25,000.00');
    await expect(market).toContainText('未實現損益 +170,000.00');
    if (info.project.name === 'iphone-webkit' && process.env.ATLAS_HOLDINGS_LIVE_SCREENSHOT) {
      await page.setViewportSize({ width: 390, height: 844 });
      await position.scrollIntoViewIfNeeded();
      await page.screenshot({ path: process.env.ATLAS_HOLDINGS_LIVE_SCREENSHOT, scale: 'css' });
    }
    await page.clock.fastForward(32 * 60_000);
    await expect(position).toContainText('非即時');
    await expect(total).toContainText('2,145,000.00 TWD');
    phase = 'close';
    await page.clock.fastForward(finalClose - (liveStart + 80_000 + 32 * 60_000) + 10 * 60_000);
    await expect(total).toContainText('2,180,000.00 TWD');
    await expect(position).toContainText(/2026-11-27 · Yahoo 延遲行情.*（非即時）/);
    await expect(account).toContainText('今日損益 +35,000.00');
    await page.clock.fastForward(31 * 60_000);
    await expect(total).toContainText('2,180,000.00 TWD');
  } finally { release(); }
});

test('holdings poll only visible positions in the active account and stop when backgrounded, left, or signed out', async ({ page }) => {
  await page.clock.install({ time: liveStart });
  await mockDeviceAccount(page);
  await seedHoldings(page, [
    { name: '帳戶甲', positions: [['6488.TWO', '100', '500'], ['2330.TW', '0.0464', '2000']] },
    { name: '帳戶乙', positions: [['2330.TW', '1', '2000']] },
  ]);
  const batches: LiveSymbol[][] = [];
  await open(page, () => page.route('https://edge.test/functions/v1/market-chart**', route => {
    const params = new URL(route.request().url()).searchParams;
    const symbol = params.get('symbol') as LiveSymbol;
    if (Object.hasOwn(previousCloses, symbol) && params.get('interval') === '1d')
      return route.fulfill({ json: dailyPayload(symbol) });
    const symbols = (params.get('quotes') ?? '').split(',').filter((item): item is LiveSymbol => Object.hasOwn(previousCloses, item));
    if (!symbols.length) return route.fallback();
    batches.push(symbols);
    const results = Object.fromEntries(symbols.map(item => [item, recordedMis(item, 'first')]));
    return route.fulfill({ json: { results, errors: {} } });
  }));

  const position = (symbol: LiveSymbol) => panel(page).getByRole('article', { name: `${symbol} 持股` });
  const choice = panel(page).getByRole('combobox', { name: '持股帳戶' });
  const hidden = panel(page).getByRole('region', { name: '已隱藏持股', exact: true });
  const navigation = page.getByRole('navigation', { name: '主要頁面' }).filter({ visible: true });
  const goto = (name: string) => navigation.getByRole('button', { name, exact: true }).click();
  await expect(position('6488.TWO')).toBeVisible();
  await expect(position('2330.TW')).toHaveCount(1);
  await position('2330.TW').locator('summary.holding-compact-row').click();
  await expect(position('2330.TW')).toContainText('證交所即時');
  // The 0.0464-share lot crosses TWD 100 at the live price but not at the confirmed close.
  await expect(panel(page).getByRole('region', { name: '帳戶甲 帳戶損益' }).getByRole('article')).toHaveCount(1);

  const pollOnce = async () => {
    const start = batches.length;
    await page.clock.fastForward(65_000);
    await expect.poll(() => batches.length).toBeGreaterThan(start);
    return batches.slice(start).flat();
  };
  const noPoll = async () => {
    const start = batches.length;
    await page.clock.fastForward(65_000);
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    expect(batches).toHaveLength(start);
  };

  await choice.selectOption({ label: '帳戶甲 · TWD' });
  await expect(position('2330.TW')).toHaveCount(0);
  await expect(hidden.locator('summary')).toHaveText('已隱藏 1 檔');
  expect(new Set(await pollOnce())).toEqual(new Set(['6488.TWO']));
  await position('6488.TWO').locator('summary.holding-compact-row').click();
  await expect(position('6488.TWO')).toContainText('證交所即時');
  await position('6488.TWO').getByRole('button', { name: '隱藏', exact: true }).click();
  await expect(panel(page).getByRole('article')).toHaveCount(0);
  await noPoll();
  await hidden.locator('summary').click();
  await hidden.getByRole('listitem', { name: '6488.TWO 已隱藏' }).getByRole('button', { name: '取消隱藏' }).click();
  await position('6488.TWO').locator('summary.holding-compact-row').click();
  await expect(position('6488.TWO')).toContainText('證交所即時');

  const setHidden = (value: boolean) => page.evaluate(background => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: background });
    document.dispatchEvent(new Event('visibilitychange'));
  }, value);
  await setHidden(true);
  await expect(position('6488.TWO')).toContainText('非即時');
  await noPoll();
  await setHidden(false);
  await expect(position('6488.TWO')).toContainText('證交所即時');

  await goto('我的');
  await expect(panel(page)).not.toBeVisible();
  await noPoll();
  await goto('自選');
  if (!await panel(page).isVisible()) await selectHoldings(page);
  await expect(position('6488.TWO')).toBeVisible();
  expect(await pollOnce()).toContain('6488.TWO');
  await goto('我的');
  await page.getByRole('button', { name: '登出 Atlas', exact: true }).click();
  await goto('自選');
  if (!await panel(page).isVisible()) await selectHoldings(page);
  await expect(panel(page)).toContainText('尚未匯入持股');
  await noPoll();
});


test('Taiwan dividend cashflow converts imported lots once and stays local', async ({ page }, info) => {
  await mockMarketLogin(page);
  const requests: string[] = [];
  await open(page, async () => {
    await page.route('**/report/upcoming-events.json', route => {
      requests.push(route.request().url() + (route.request().postData() ?? ''));
      const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' }).format(Date.now());
      return route.fulfill({ json: { version: 1, generatedAt: new Date().toISOString(), coverage: { earnings: 0, alerts: 0, dividends: 1, cashDividends: 1 },
        events: [], moves: {}, cashDividends: { '2330.TW': { checked: today, events: [{ exDate: today, paymentDate: null, perShare: 20, year: today }] } } } });
    });
  });
  await create(page);
  await paste(page, '代號\t張數\t平均成本\n2330\t1\t812.34567');
  await expect(importer(page).getByRole('textbox', { name: '第 1 列股數' })).toHaveValue('1000');
  await save(page);
  await panel(page).locator('summary').filter({ hasText: '台股股息現金流' }).click();
  const card = panel(page).locator('details').filter({ has: page.locator('summary', { hasText: '台股股息現金流' }) });
  await expect(card).toContainText('20,000.00');
  await expect(card).toContainText('422.00');
  await expect(card).toContainText('發放日未公告');
  expect(requests).toHaveLength(1);
  expect(requests[0]).not.toMatch(/2330|812\.34567|PRIVATE-LOCAL-ONLY|quantity/);
  await layout(page);
  if (info.project.name === 'mobile-chromium' && page.viewportSize()?.width === 390)
    await card.screenshot({ path: info.outputPath('dividend-cashflow-390.png') });
});

import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { installLineAccount, mockDeviceAccount } from './account-fixture';
import { researchFixtureBody } from './research-fixtures';
import { createMexcFixture, MEXC_USER, TEST_KEY, TEST_SECRET } from '../tests/fixtures/mexcHarness';

const panel = (page: Page) => page.getByRole('region', { name: '持股', exact: true });
const mexc = (page: Page) => panel(page).getByRole('region', { name: 'MEXC 自動同步' });
const account = (page: Page) => panel(page).getByRole('region', { name: '帳戶損益', exact: true });
const totals = (page: Page) => panel(page).getByRole('region', { name: '持股總計', exact: true });
const accountSettings = (page: Page) => panel(page).getByRole('region', { name: '帳戶設定', exact: true });
const hidden = (page: Page) => accountSettings(page).getByRole('region', { name: '已隱藏持股', exact: true });

async function research(page: Page) {
  await mockSite(page, { fundamentals: true });
  await page.route('**/report/research/**', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: researchFixtureBody(new URL(route.request().url()).pathname.split('/research/')[1]!).toString(),
    }),
  );
}
async function openHoldings(page: Page) {
  await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  await expect(panel(page)).toBeVisible();
}
async function noHorizontalScroll(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
}
async function settings(page: Page) {
  const details = accountSettings(page).locator('details.holdings-visibility');
  if (!(await details.evaluate((el) => (el as HTMLDetailsElement).open)))
    await details.locator('summary').click();
  return details;
}

async function connectMexc(page: Page) {
  const f = createMexcFixture('cloud'),
    request = f.handler();
  await page.clock.install({ time: f.now() });
  await installLineAccount(page, MEXC_USER);
  await research(page);
  await page.route('**/functions/v1/user-settings', async (route) => {
    const result = await request(route.request().postDataJSON());
    await route.fulfill({ status: result.status, headers: Object.fromEntries(result.headers.entries()), json: result.body });
  });
  // BTC 0.12 × 65000, ETH 0.002 × 3500 = 7 USDT (dust), USDT 100, SHIB has no quote.
  f.setBalances([
    { asset: 'BTC', free: '0.1', locked: '0.02' },
    { asset: 'ETH', free: '0.002', locked: '0' },
    { asset: 'USDT', free: '100', locked: '0' },
    { asset: 'SHIB', free: '1000', locked: '0' },
  ]);
  await page.goto('./?page=watchlist');
  await openHoldings(page);
  await panel(page).getByRole('button', { name: '新增帳戶', exact: true }).click();
  await panel(page).getByRole('combobox', { name: '新增方式' }).selectOption('api');
  await panel(page).getByRole('combobox', { name: 'API 服務' }).selectOption('mexc');
  await panel(page).getByRole('button', { name: '使用 MEXC API 連線' }).click();
  await mexc(page).getByLabel('MEXC API Key', { exact: true }).fill(TEST_KEY);
  await mexc(page).getByLabel('MEXC Secret Key', { exact: true }).fill(TEST_SECRET);
  await mexc(page).getByRole('checkbox', { name: /我確認只開讀取權限/ }).check();
  await mexc(page).getByRole('button', { name: /^連接並讀取 MEXC/ }).click();
  await expect(mexc(page)).toContainText('已連線');
  for (const [asset, cost] of [['BTC', '50000'], ['ETH', '3000'], ['USDT', '1'], ['SHIB', '0.00001']] as const)
    await mexc(page).getByRole('textbox', { name: `${asset} 平均成本`, exact: true }).fill(cost);
  await mexc(page).getByRole('checkbox', { name: '已補上新增餘額的成本，並核對所有平均成本' }).check();
  await mexc(page).getByRole('button', { name: '確認成本並套用同步餘額' }).click();
  await expect(account(page).getByRole('article')).toHaveCount(4);
  return f;
}

test('MEXC: USDT and dust rules plus a manual hide exclude holdings from every total, survive re-sync and reload, and fit 375px', async ({
  page,
}, info) => {
  const f = await connectMexc(page);
  const subtotal = accountSettings(page).locator('.holdings-subtotal').first();
  await expect(subtotal).toContainText('USDT 市值 7,907.00');
  await expect(hidden(page)).toHaveCount(0);

  const rules = await settings(page);
  await rules.getByRole('checkbox', { name: '隱藏 USDT（計價單位）' }).check();
  await expect(account(page).getByRole('article', { name: 'USDT/USDT 持股' })).toHaveCount(0);
  await expect(subtotal).toContainText('USDT 市值 7,807.00');
  await expect(hidden(page).locator('summary')).toHaveText('已隱藏 1 檔');

  await rules.getByRole('checkbox', { name: '隱藏小額持倉' }).check();
  await rules.getByRole('combobox', { name: '小額門檻' }).selectOption('10');
  await expect(account(page).getByRole('article', { name: 'ETH/USDT 持股' })).toHaveCount(0);
  // A holding without a price is never auto-hidden.
  await expect(account(page).getByRole('article', { name: 'SHIB/USDT 持股' })).toHaveCount(1);
  await expect(subtotal).toContainText('USDT 市值 7,800.00');

  await account(page).getByRole('article', { name: 'BTC/USDT 持股' }).locator('summary.holding-compact-row').click();
  await account(page).getByRole('article', { name: 'BTC/USDT 持股' }).getByRole('button', { name: '隱藏', exact: true }).click();
  await expect(account(page).getByRole('article')).toHaveCount(1);
  await expect(hidden(page).locator('summary')).toHaveText('已隱藏 3 檔');
  await expect(subtotal).toContainText('USDT 市值 0.00');
  // Market subtotal and the portfolio note use the same visible set.
  await totals(page).locator('summary').filter({ hasText: '市場小計' }).click();
  await expect(totals(page)).toContainText('USDT 市值 0.00');
  await expect(totals(page).getByTestId('holdings-hidden-total-note')).toHaveText('已隱藏 3 檔持股，未計入總計與小計。');

  // Hidden list is collapsed, explains totals, and gives reasons.
  await expect(hidden(page).locator('details')).not.toHaveAttribute('open', '');
  await hidden(page).locator('summary').click();
  await expect(hidden(page).getByRole('note')).toContainText('不計入總市值');
  await expect(hidden(page).getByRole('listitem', { name: 'USDT/USDT 已隱藏' })).toContainText('USDT 計價單位');
  await expect(hidden(page).getByRole('listitem', { name: 'ETH/USDT 已隱藏' })).toContainText('小額（< 10 USDT）');
  await expect(hidden(page).getByRole('listitem', { name: 'BTC/USDT 已隱藏' })).toContainText('手動隱藏');

  // Sync still imports everything; hiding is display-only.
  f.advance(31000);
  await page.clock.fastForward(31000);
  await mexc(page).getByRole('button', { name: /^同步餘額/ }).click();
  await expect(mexc(page)).toContainText('已讀取最新 MEXC 餘額。');
  expect(f.documents.get(MEXC_USER)!.accounts[0]!.holdings.map((h) => h.symbol).sort()).toEqual([
    'BTC/USDT',
    'ETH/USDT',
    'SHIB/USDT',
    'USDT/USDT',
  ]);
  await expect(hidden(page).locator('summary')).toHaveText('已隱藏 3 檔');

  await page.reload();
  await openHoldings(page);
  await expect(account(page).getByRole('article')).toHaveCount(1);
  await expect(hidden(page).locator('summary')).toHaveText('已隱藏 3 檔');
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }));
  expect(storage).toContain('holdings-visibility-v1');
  expect(storage).not.toMatch(/holdings:v1|0\.12|50000|7,800|TEST|secret/i);

  await page.setViewportSize({ width: 375, height: 812 });
  await hidden(page).locator('summary').click();
  await hidden(page).scrollIntoViewIfNeeded();
  await noHorizontalScroll(page);
  expect(await hidden(page).evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  for (const button of await hidden(page).getByRole('button').all())
    expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await page.clock.runFor(100);
  if (info.project.name === 'mobile-chromium') {
    await hidden(page).screenshot({ path: info.outputPath('mexc-hidden-375.png') });
  }

  // One click unhides even a rule-hidden holding; totals follow.
  await hidden(page).getByRole('listitem', { name: 'ETH/USDT 已隱藏' }).getByRole('button', { name: '取消隱藏', exact: true }).click();
  await hidden(page).getByRole('listitem', { name: 'BTC/USDT 已隱藏' }).getByRole('button', { name: '取消隱藏', exact: true }).click();
  await expect(account(page).getByRole('article')).toHaveCount(3);
  await expect(subtotal).toContainText('USDT 市值 7,807.00');
  await expect(hidden(page).locator('summary')).toHaveText('已隱藏 1 檔');
  await noHorizontalScroll(page);
});

test('device account: a manual hide is kept after re-import and reload, and leaves the stored holdings unchanged', async ({
  page,
}) => {
  await mockDeviceAccount(page);
  await research(page);
  await page.goto('./?page=watchlist');
  await openHoldings(page);
  await panel(page).getByRole('button', { name: '新增帳戶', exact: true }).click();
  await panel(page).getByRole('combobox', { name: '帳戶市場' }).selectOption('tw');
  await panel(page).getByLabel('帳戶名稱', { exact: true }).fill('凱基台股');
  await panel(page).getByRole('button', { name: '繼續匯入', exact: true }).click();
  const importer = panel(page).getByRole('region', { name: '匯入持股', exact: true });
  const paste = async (value: string) => {
    await importer.getByRole('textbox', { name: '貼上庫存表格' }).fill(value);
    await importer.getByRole('button', { name: '辨識貼上內容' }).click();
    await importer.getByRole('button', { name: '用這份資料取代此帳戶持股' }).click();
    await expect(importer).not.toBeVisible();
  };
  await paste('代號\t股數\t平均成本\n2330\t1000\t500\n6488\t2000\t100');
  await expect(account(page).getByRole('article')).toHaveCount(2);
  const before = (await totals(page).getByRole('region', { name: '台股總計', exact: true }).textContent())!;
  const stored = async () =>
    page.evaluate(() =>
      Object.entries(localStorage)
        .filter(([key]) => key.endsWith(':holdings:v1'))
        .map(([, value]) => JSON.parse(value).accounts[0].holdings.map((h: { symbol: string }) => h.symbol))
        .flat(),
    );
  expect(await stored()).toEqual(['2330.TW', '6488.TWO']);

  await account(page).getByRole('article', { name: '2330.TW 持股' }).locator('summary.holding-compact-row').click();
  await account(page).getByRole('article', { name: '2330.TW 持股' }).getByRole('button', { name: '隱藏', exact: true }).click();
  await expect(account(page).getByRole('article')).toHaveCount(1);
  await expect(totals(page).getByRole('region', { name: '台股總計', exact: true })).not.toHaveText(before);
  await expect(hidden(page).locator('summary')).toHaveText('已隱藏 1 檔');
  expect(await stored()).toEqual(['2330.TW', '6488.TWO']);

  await panel(page).getByRole('button', { name: '匯入持股', exact: true }).click();
  await paste('代號\t股數\t平均成本\n2330\t3000\t500\n6488\t2000\t100');
  await expect(account(page).getByRole('article')).toHaveCount(1);
  await expect(account(page).getByRole('article', { name: '6488.TWO 持股' })).toHaveCount(1);
  await page.reload();
  await openHoldings(page);
  await expect(account(page).getByRole('article')).toHaveCount(1);
  await expect(hidden(page).locator('summary')).toHaveText('已隱藏 1 檔');
  await hidden(page).locator('summary').click();
  await hidden(page).getByRole('listitem', { name: '2330.TW 已隱藏' }).getByRole('button', { name: '取消隱藏', exact: true }).click();
  await expect(account(page).getByRole('article')).toHaveCount(2);
  await expect(hidden(page)).toHaveCount(0);
});

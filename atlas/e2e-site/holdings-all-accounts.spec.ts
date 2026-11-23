import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { mockDeviceAccount } from './account-fixture';
import { researchFixtureBody } from './research-fixtures';

const panel = (page: Page) => page.getByRole('region', { name: '持股', exact: true });
async function openHoldings(page: Page) {
  const toolbar = page.locator('.watchlist-board .wb-toolbar');
  await toolbar.waitFor({ state: 'visible' });
  const sheet = page.getByRole('dialog', { name: '清單操作' });
  if (await sheet.isVisible()) await page.getByRole('button', { name: '關閉清單操作' }).click();
  if (await page.locator('.site-tabbar').isVisible())
    await toolbar.getByRole('button', { name: '持股', exact: true }).click();
  else await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  await expect(panel(page)).toBeVisible();
}
async function addAccount(page: Page, market: 'tw' | 'us', name: string, table: string) {
  await panel(page).getByRole('button', { name: '新增帳戶', exact: true }).click();
  await panel(page).getByRole('combobox', { name: '帳戶市場' }).selectOption(market);
  await panel(page).getByRole('textbox', { name: '帳戶名稱' }).fill(name);
  await panel(page).getByRole('button', { name: '繼續匯入', exact: true }).click();
  const importer = panel(page).getByRole('region', { name: '匯入持股', exact: true });
  await importer.getByRole('textbox', { name: '貼上庫存表格' }).fill(table);
  await importer.getByRole('button', { name: '辨識貼上內容' }).click();
  await importer.getByRole('button', { name: '用這份資料取代此帳戶持股' }).click();
  await expect(importer).not.toBeVisible();
}

test('全部帳號 shows every account labelled, remembers the default view, and symbols open the chart', async ({ page }, info) => {
  await mockDeviceAccount(page);
  await mockSite(page, { fundamentals: true });
  await page.route('**/report/research/**', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: researchFixtureBody(new URL(route.request().url()).pathname.split('/research/')[1]!).toString(),
    }),
  );
  await page.goto('./?page=watchlist');
  await openHoldings(page);
  await addAccount(page, 'tw', '凱基台股', '代號\t股數\t平均成本\n2330\t1000\t500');
  await addAccount(page, 'us', '嘉信美股', 'Symbol,Quantity,Avg Cost\nAAPL,10,150');

  const select = panel(page).getByRole('combobox', { name: '持股帳戶' });
  await select.selectOption('all');
  await expect(panel(page).getByRole('article')).toHaveCount(2);
  await expect(panel(page).getByRole('heading', { name: /凱基台股/ })).toBeVisible();
  await expect(panel(page).getByRole('heading', { name: /嘉信美股/ })).toBeVisible();
  await expect(panel(page).getByRole('button', { name: '匯入持股', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  if (info.project.name.includes('iphone') || info.project.name.includes('mobile'))
  {
    await select.scrollIntoViewIfNeeded();
    await page.screenshot({ path: process.env.HOLDINGS_SHOT ?? info.outputPath('holdings-all-accounts.png') });
  }

  // The default is 全部帳號 for a fresh visit, even after choosing a single account for this visit.
  await select.selectOption({ label: '凱基台股 · TWD' });
  await expect(panel(page).getByRole('article')).toHaveCount(1);
  await page.reload();
  await openHoldings(page);
  await expect(select).toHaveValue('all');
  await expect(panel(page).getByRole('article')).toHaveCount(2);

  // Remember a specific account as the default, then reopen.
  const preference = panel(page).getByRole('combobox', { name: '預設開啟帳戶' });
  await preference.selectOption({ label: '嘉信美股 · USD' });
  await page.reload();
  await openHoldings(page);
  await expect(panel(page).getByRole('article')).toHaveCount(1);
  await expect(panel(page).getByRole('article')).toContainText('AAPL');

  // Symbol buttons are real, keyboard-reachable buttons that open the in-app chart.
  await select.selectOption('all');
  const tsmc = panel(page).getByRole('button', { name: '2330.TW' });
  await tsmc.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'chart');
  await expect(page.getByTestId('active-symbol')).toHaveText('2330.TW');
});

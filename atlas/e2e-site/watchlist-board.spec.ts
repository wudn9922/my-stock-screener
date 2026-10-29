import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { accessEnvelope, installLineAccount, OWNER_USER_ID } from './account-fixture';

const board = (page: Page) => page.getByRole('region', { name: '自選行情' });
const rows = (page: Page) => board(page).getByRole('list', { name: '自選行情列表' }).getByRole('link');
const source = (page: Page, name: string) => board(page).getByRole('group', { name: '行情來源' }).getByRole('button', { name, exact: true });

async function trending(page: Page, symbols: string[] | 'missing') {
  await page.route('**/my-stock-screener/report/trending-stocks.json', (route) => symbols === 'missing'
    ? route.fulfill({ status: 404, body: 'not found' })
    : route.fulfill({ json: { version: 1, source: 'line', fetchedAt: '2026-10-27T01:00:00+08:00', sentAt: '2026-10-27T01:05:00+08:00', symbols } }));
}

async function cloudOwner(page: Page) {
  await installLineAccount(page, OWNER_USER_ID);
  await page.route('**/functions/v1/chart-drawings', (route) => route.fulfill({ json: { ok: true, records: [] } }));
  await page.route('**/functions/v1/user-settings', (route) => {
    const body = route.request().postDataJSON();
    const data = body.action === 'access.get' ? accessEnvelope(OWNER_USER_ID, 'cloud', true).data
      : body.action === 'watchlists.save' ? { id: body.definition.id, definition: body.definition, updated_at: '2026-10-27T00:00:01.000Z' }
        : [];
    return route.fulfill({ json: { ok: true, data } });
  });
}

test('without lists the board opens on LINE 熱門 with quotes, and explains the empty sources', async ({ page }) => {
  const requested = await mockSite(page);
  await trending(page, ['NVDA', 'AAPL']);
  await page.goto('./?page=watchlist');
  await expect(board(page)).toBeVisible();
  await expect(source(page, 'LINE 熱門')).toHaveAttribute('aria-pressed', 'true');
  await expect(rows(page)).toHaveCount(2);
  await expect(rows(page).first()).toHaveAccessibleName('NVDA 行情，開啟個股');
  await expect(rows(page).first().locator('.wb-change')).toHaveText(/^[▲▼]? ?[+-]?\d+\.\d{2}%$/);
  await expect(rows(page).first().locator('polyline')).toHaveAttribute('points', /^0\.0,/);
  expect(requested).toEqual(expect.arrayContaining(['NVDA', 'AAPL']));
  await expect(board(page).getByRole('group', { name: '主要指數' })).toHaveCount(0);

  await source(page, '我的清單').click();
  await expect(board(page).getByRole('status')).toContainText('還沒有自選清單');
  await board(page).getByRole('button', { name: '到選股建立清單', exact: true }).click();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'screener');
  await page.goBack();
  await expect(source(page, '最近搜尋')).toHaveCount(0);
  // List management below the board is unchanged.
  await expect(page.getByRole('button', { name: '以篩選條件建立清單', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '建立手動清單', exact: true })).toBeVisible();
});

test('a missing LINE 熱門 file offers a retry instead of rows', async ({ page }) => {
  await mockSite(page);
  await trending(page, 'missing');
  await page.goto('./?page=watchlist');
  await expect(board(page).getByRole('status')).toContainText('LINE 熱門美股清單尚未產生');
  await expect(board(page).getByRole('button', { name: '重試', exact: true })).toBeVisible();
});

test('a saved list shows its rows read-only; a row opens 個股 and a failed quote stays labelled', async ({ page }) => {
  await mockSite(page, { fundamentals: true, failingSymbols: ['AAPL'] });
  await trending(page, ['NVDA']);
  await cloudOwner(page);
  await page.goto('./?page=watchlist');
  await page.getByRole('button', { name: '建立手動清單', exact: true }).click();
  await page.getByRole('textbox', { name: '手動清單名稱' }).fill('看板測試');
  await page.getByRole('combobox', { name: '手動清單市場' }).selectOption('US');
  await page.getByRole('textbox', { name: '手動清單股票' }).fill('NVDA, AAPL');
  await page.getByRole('button', { name: '儲存手動清單', exact: true }).click();
  await expect(source(page, '我的清單')).toHaveAttribute('aria-pressed', 'true');
  await expect(board(page)).toContainText('清單：看板測試');
  await expect(rows(page)).toHaveCount(2);
  // Rows load lazily near the viewport; saving the list leaves the page scrolled to the form on phones.
  await rows(page).nth(1).scrollIntoViewIfNeeded();
  await expect(rows(page).nth(1).locator('.wb-change')).toHaveText('暫無行情');
  await source(page, 'LINE 熱門').click();
  await expect(rows(page)).toHaveCount(1);
  await source(page, '我的清單').click();
  await expect(rows(page)).toHaveCount(2);

  await rows(page).first().click();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'chart');
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
});

test('board rows keep 44px targets and no horizontal overflow at 320px', async ({ page }) => {
  await mockSite(page);
  await trending(page, ['NVDA', 'AAPL', 'MSFT']);
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto('./?page=watchlist');
  await expect(rows(page)).toHaveCount(3);
  for (const box of await rows(page).evaluateAll((links) => links.map((link) => link.getBoundingClientRect().height)))
    expect(box).toBeGreaterThanOrEqual(44);
  for (const name of ['我的清單', 'LINE 熱門', '突破／跌破'])
    expect((await source(page, name).boundingBox())!.height).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.querySelector('#main')!.scrollWidth - document.querySelector('#main')!.clientWidth)).toBeLessThanOrEqual(0);
});

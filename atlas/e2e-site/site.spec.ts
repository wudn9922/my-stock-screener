import { expect, test, type Page } from '@playwright/test';
import { buildReport, mockSite } from './mocks';

async function isPhone(page: Page) {
  return page.locator('.site-tabbar').isVisible();
}
async function openSearch(page: Page) {
  if (await isPhone(page)) {
    await page.locator('.site-tabbar').getByRole('button', { name: '搜尋', exact: true }).click();
    return page.getByRole('dialog', { name: '搜尋股票' }).getByRole('combobox', { name: '搜尋股票' });
  }
  const input = page.locator('.site-topbar').getByRole('combobox', { name: '搜尋股票' });
  await input.click();
  return input;
}
async function chartReady(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0, { timeout: 20000 });
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}

test('lands on 大盤 / 台灣 with report cards, LINE summary and mini charts', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockSite(page);
  await page.goto('./');
  await expect(page.getByRole('tab', { name: /台灣/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.index-card')).toHaveCount(2);
  await expect(page.locator('.index-card').first()).toContainText('台灣加權指數');
  await expect(page.locator('.mini-chart[data-status="ready"]').first()).toBeVisible();
  const line = page.getByRole('region', { name: /LINE 摘要/ });
  await expect(line.locator('.line-status')).toHaveCount(2);
  await expect(line).toContainText('多頭趨勢中的空頭走勢');
  await expect(page.locator('.page-sub')).toContainText('報告日 2026/10/05');
  // No horizontal page overflow.
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  await page.getByRole('tab', { name: /美國/ }).click();
  await expect(page).toHaveURL(/page=markets&market=us/);
  await expect(page.locator('.index-card')).toHaveCount(5);
  await page.goBack();
  await expect(page).not.toHaveURL(/market=us/);
  await expect(page.getByRole('tab', { name: /台灣/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.index-card')).toHaveCount(2);
  expect(errors).toEqual([]);
});

test('index card opens the chart workspace with the index MAs', async ({ page }) => {
  await mockSite(page);
  await page.goto('./?page=markets&market=tw');
  await page.locator('.index-card').first().click();
  await expect(page).toHaveURL(/page=chart&symbol=\^TWII&tf=1D/);
  await chartReady(page);
  await expect(page.getByTestId('active-symbol')).toHaveText('^TWII');
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveText(['SMA 23', 'SMA 29', 'SMA 61']);
  await page.goBack();
  await expect(page.locator('.index-card')).toHaveCount(2);
});

test('deep links, legacy links and LINE liff.state open the right page', async ({ page }) => {
  await mockSite(page);
  await page.goto(`./?liff.state=${encodeURIComponent('/atlas/?page=world')}`);
  await expect(page).toHaveURL(/\?page=world$/);
  await expect(page.locator('.world-table-card')).toBeVisible();
  await page.goto('./?symbol=2330.TW&tf=1D');
  await expect(page).toHaveURL(/page=chart&symbol=2330\.TW&tf=1D/);
  await chartReady(page);
  await expect(page.getByTestId('active-symbol')).toHaveText('2330.TW');
  await page.goto('./?page=screener&group=us_g1');
  await expect(page.getByRole('button', { name: /權值精選/, pressed: true })).toBeVisible();
  await expect(page.locator('.screener-row')).toHaveCount(3);
});

test('global search finds Chinese names, bare digits and tickers, with keyboard and recent list', async ({ page }) => {
  await mockSite(page);
  await page.goto('./?page=markets&market=tw');
  let input = await openSearch(page);
  await input.fill('台積');
  const option = page.locator('.search-results [role="option"]').filter({ hasText: '台積電' });
  await expect(option.first()).toBeVisible();
  await input.press('Enter');
  await expect(page).toHaveURL(/page=chart&symbol=2330\.TW/);
  await chartReady(page);
  await expect(page.locator('[data-testid="valuation"]')).toContainText('22.6');

  input = await openSearch(page);
  await expect(page.locator('.search-results [role="option"]').first()).toContainText('台積電');
  await input.fill('nvd');
  await expect(page.locator('.search-results [role="option"]').first()).toContainText('NVIDIA');
  await input.press('ArrowDown');
  await input.press('ArrowUp');
  await input.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
  await chartReady(page);
  // P/E = price / EPS; both figures render for a profitable US stock.
  await expect(page.locator('[data-testid="valuation"] dd').first()).not.toHaveText('—');

  input = await openSearch(page);
  await input.fill('2330');
  await expect(page.locator('.search-results [role="option"]').first()).toContainText('2330');
});

test('screener virtualizes a long list and opens rows with the group MA', async ({ page }) => {
  await mockSite(page, { report: buildReport(600) });
  await page.goto('./?page=screener&group=tw_all');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  const rendered = await page.locator('.screener-row').count();
  expect(rendered).toBeGreaterThan(5);
  expect(rendered).toBeLessThan(80);
  await page.locator('.screener-list').evaluate((list) => list.scrollTo({ top: 64 * 400 }));
  await expect(page.locator('.screener-row').filter({ hasText: /./ }).first()).toBeVisible();
  await page.getByRole('searchbox', { name: '在群組內篩選' }).fill('台泥');
  await expect(page.locator('.screener-row')).toHaveCount(1);
  await page.locator('.screener-row').click();
  await expect(page).toHaveURL(/page=chart&symbol=1101\.TW/);
  await chartReady(page);
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveText(['SMA 20']);
});

test('world page compares indices and isolates per-row failures', async ({ page }) => {
  await mockSite(page, { failingSymbols: ['^FTSE'] });
  await page.goto('./?page=world');
  const ftse = page.locator('.data-table tbody tr').filter({ hasText: '英國富時100' });
  await expect(ftse).toContainText('暫無資料', { timeout: 30000 });
  const twii = page.locator('.data-table tbody tr').filter({ hasText: '台灣加權指數' });
  await expect(twii.locator('td').nth(3)).toHaveText(/^[+-]?\d+\.\d{2}%$/);
  const ytd = page.getByRole('group', { name: '比較區間' }).getByRole('button', { name: '今年' });
  await ytd.click();
  await expect(ytd).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('columnheader', { name: /1日/ }).getByRole('button').click();
  await expect(page.getByRole('columnheader', { name: /1日/ })).toHaveAttribute('aria-sort', 'descending');
  const legendButton = page.locator('.series-legend button').filter({ hasText: '日經225' });
  await legendButton.click();
  await expect(legendButton).toHaveAttribute('aria-pressed', 'false');
});

test('a missing or broken report shows a friendly state and search still works', async ({ page }) => {
  await mockSite(page, { report: 'missing' });
  await page.goto('./');
  await expect(page.getByRole('status')).toContainText('今日報告尚未產生');
  await page.unroute('**/my-stock-screener/report/latest.json');
  await page.route('**/my-stock-screener/report/latest.json', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html>' }),
  );
  await page.goto('./?page=world');
  await expect(page.getByRole('alert')).toContainText('每日報告暫時無法顯示');
  const input = await openSearch(page);
  await input.fill('台積');
  await expect(page.locator('.search-results [role="option"]').first()).toContainText('台積電');
});

test('color convention defaults to 紅漲綠跌 and can be switched', async ({ page }) => {
  await mockSite(page);
  await page.goto('./');
  await expect(page.locator('html')).toHaveAttribute('data-colors', 'tw');
  await page.getByRole('button', { name: '顯示設定' }).click();
  await page.getByText('綠漲紅跌').click();
  await expect(page.locator('html')).toHaveAttribute('data-colors', 'us');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-colors', 'us');
});

import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { researchFixtureBody } from './research-fixtures';

const tabs = (page: Page) => page.locator('.ws-stock-controls').getByRole('navigation', { name: '個股頁面' });

async function fixtures(page: Page) {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/research/**', (route) => {
    const path = new URL(route.request().url()).pathname.split('/research/')[1]!;
    try {
      return route.fulfill({ contentType: 'application/json', body: researchFixtureBody(path).toString() });
    } catch {
      return route.fulfill({ status: 404 });
    }
  });
  await page.route('**/report/financials/*.json', (route) => route.fulfill({ contentType: 'application/json',
    body: readFileSync(new URL('../tests/fixtures/financials/us-full.json', import.meta.url), 'utf8') }));
}

test('K線｜財務｜價量｜研究 switch views of one stock and keep the same chart', async ({ page }) => {
  await fixtures(page);
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await expect(tabs(page).getByRole('button')).toHaveText(['K線', '財務', '價量', '研究']);
  await expect(tabs(page).getByRole('button', { name: 'K線', exact: true })).toHaveAttribute('aria-current', 'page');
  // The old header menus moved into the views; the volatility menu stays in the header.
  await expect(page.getByTestId('volume-analysis-toggle')).toHaveCount(0);
  await expect(page.getByTestId('volatility-toggle')).toBeVisible();
  await page.getByTestId('chart').evaluate((node) => { (window as unknown as { viewsChart: Element }).viewsChart = node; });

  await page.getByTestId('stock-tab-volume').click();
  const volume = page.getByRole('region', { name: '價量', exact: true });
  await expect(volume.getByText('上漲縮量比例', { exact: true })).toBeVisible();
  await expect(volume.getByTestId('ma-recommendation')).toBeVisible();
  await expect(page.locator('.chart-toolbar')).toBeHidden();
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);

  await page.getByTestId('stock-tab-research').click();
  const research = page.getByRole('region', { name: '研究', exact: true });
  await expect(research.locator('.stock-official-notices')).toContainText('SEC 8-K');
  await expect(volume).toBeHidden();

  await page.getByTestId('stock-tab-chart').click();
  await expect(page.getByTestId('chart')).toBeVisible();
  await expect(page.locator('.chart-toolbar')).toBeVisible();
  expect(await page.getByTestId('chart').evaluate((node) => (window as unknown as { viewsChart: Element }).viewsChart === node)).toBe(true);
  await expect(page.locator('.chart-host canvas').first()).toBeVisible();
});

test('財務 tabs open the chart page in the chosen view; opening another stock shows K線', async ({ page }) => {
  await fixtures(page);
  await page.goto('./?page=financials&symbol=AAPL&tf=1D');
  const financialTabs = page.locator('.fin-head').getByRole('navigation', { name: '個股頁面' });
  await expect(financialTabs.getByRole('button')).toHaveText(['K線', '財務', '價量', '研究']);
  await financialTabs.getByRole('button', { name: '價量', exact: true }).click();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'chart');
  await expect(page.getByTestId('active-symbol')).toHaveText('AAPL');
  await expect(page.getByRole('region', { name: '價量', exact: true })).toBeVisible();
  await expect(page.getByTestId('stock-tab-volume')).toHaveAttribute('aria-current', 'page');

  await page.getByTestId('stock-tab-financials').click();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'financials');
  await page.locator('.fin-head').getByRole('button', { name: 'K線', exact: true }).click();
  await expect(page.getByTestId('chart')).toBeVisible();
  await expect(page.getByTestId('stock-tab-chart')).toHaveAttribute('aria-current', 'page');

  await page.getByTestId('stock-tab-research').click();
  await expect(page.getByRole('region', { name: '研究', exact: true })).toBeVisible();
  // Opening another stock from search starts at its chart.
  await page.getByRole('button', { name: /^切換股票（目前/ }).click();
  const input = page.getByRole('dialog', { name: '搜尋股票' }).getByRole('combobox', { name: '搜尋股票' });
  await input.fill('NVDA');
  await expect(page.locator('.search-results [role="option"]').first()).toBeVisible();
  await input.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
  await expect(page.getByTestId('stock-tab-chart')).toHaveAttribute('aria-current', 'page');
  await expect(page.getByTestId('chart')).toBeVisible();
});

test('an index has no stock views and keeps the full chart', async ({ page }) => {
  await fixtures(page);
  await page.goto('./?page=chart&symbol=%5ETWII&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await expect(page.getByTestId('stock-tab-volume')).toHaveCount(0);
  await expect(page.getByTestId('chart')).toBeVisible();
});

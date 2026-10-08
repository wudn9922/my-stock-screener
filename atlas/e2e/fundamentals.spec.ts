import { test, expect, type Page } from '@playwright/test';
import { chooseDrawingTool } from './drawing-picker-helper';
import { readFileSync } from 'node:fs';
import { FinancialNormalizer } from '../src/fundamentals/FinancialNormalizer';
import type { FinancialPeriod } from '../src/fundamentals/FundamentalsProvider';
const normalizer = new FinancialNormalizer();
const companies = ['AAPL', 'MSFT', 'NVDA', 'TSLA'] as const;
const fixtures = Object.fromEntries(
  companies.map((symbol) => {
    const raw = JSON.parse(readFileSync(`tests/fixtures/sec/${symbol}.json`, 'utf8'));
    return [
      symbol,
      {
        annual: normalizer.normalize(raw, symbol, 'annual'),
        quarterly: normalizer.normalize(raw, symbol, 'quarterly'),
      },
    ];
  }),
);
async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}
async function open(page: Page) {
  if (await page.locator('.mobile-nav').isVisible())
    await page
      .locator('.mobile-nav')
      .getByRole('button', { name: 'Financials', exact: true })
      .click();
  else await page.getByRole('button', { name: 'Financials', exact: true }).click();
}
async function close(page: Page) {
  const b = page.getByRole('button', { name: 'Close panel', exact: true });
  if (await b.isVisible()) await b.click();
}
async function switchTo(page: Page, symbol: string) {
  await close(page);
  await page.getByLabel('Symbol search', { exact: true }).fill(symbol);
  await page.getByLabel('Symbol search', { exact: true }).press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText(symbol);
  await loaded(page);
  if (await page.locator('.mobile-nav').isVisible()) await open(page);
}
test('SEC financial UI: four company patterns, annual/quarterly, statements, provenance and chart isolation', async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/api/fundamentals?**', async (route) => {
    const url = new URL(route.request().url()),
      symbol = url.searchParams.get('symbol')!,
      period = url.searchParams.get('period') as FinancialPeriod;
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(fixtures[symbol]?.[period] ?? []),
    });
  });
  await page.goto('/');
  await loaded(page);
  const chartBox = (await page.getByTestId('chart').boundingBox())!;
  await open(page);
  const panel = page.getByTestId('financial-panel');
  await expect(panel.getByTestId('financial-revenue')).toHaveText('$109.42B');
  await expect(panel).toContainText('FY2026 Q3');
  await expect(panel.getByRole('img', { name: 'Revenue history' })).toBeVisible();
  await panel.getByRole('button', { name: 'Cash Flow', exact: true }).click();
  await panel.getByLabel('Audit Operating Cash Flow', { exact: true }).click();
  await expect(panel).toContainText('Derived calculation');
  await expect(panel).toContainText('current cumulative');
  await expect(panel).toContainText('NetCashProvidedByUsedInOperatingActivities');
  await expect(panel).toContainText('Accession');
  await expect(
    panel.getByRole('link', { name: 'SEC companyfacts', exact: true }).first(),
  ).toHaveAttribute('href', /data\.sec\.gov/);
  await panel.getByRole('button', { name: 'Income Statement', exact: true }).click();
  await expect(panel.getByTestId('financial-operatingExpenses')).not.toHaveText('N/A');
  await panel.getByRole('button', { name: 'Balance Sheet', exact: true }).click();
  await expect(panel.getByTestId('financial-totalAssets')).toHaveText('$383.27B');
  await panel.getByRole('button', { name: 'Annual', exact: true }).click();
  await expect(panel.getByLabel('Fiscal period', { exact: true })).toHaveValue('2025-09-27');
  await panel.getByRole('button', { name: 'Overview', exact: true }).click();
  await expect(panel.getByTestId('financial-revenue')).toHaveText('$416.16B');
  await panel.getByLabel('Audit Revenue', { exact: true }).click();
  await expect(panel).toContainText('Raw filing fact');
  await expect(panel).toContainText('RevenueFromContractWithCustomerExcludingAssessedTax');
  await panel.getByRole('button', { name: 'Quarterly', exact: true }).click();
  await panel.getByLabel('Fiscal period', { exact: true }).selectOption('2025-09-27');
  await expect(panel.getByTestId('financial-epsDiluted')).toHaveText('N/A');
  await page.screenshot({ path: `scratch/financial-${info.project.name}.png` });
  for (const [symbol, fy, q] of [
    ['MSFT', 2026, 4],
    ['NVDA', 2027, 2],
    ['TSLA', 2026, 2],
  ] as const) {
    await switchTo(page, symbol);
    await expect(panel).toContainText(`FY${fy} Q${q}`);
    await expect(panel.getByTestId('financial-revenue')).not.toHaveText('N/A');
    await expect(panel).toContainText('DEMO 模擬');
  }
  await switchTo(page, 'AAPL');
  await close(page);
  expect((await page.getByTestId('chart').boundingBox())!.height).toBe(chartBox.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await chooseDrawingTool(page, 'Horizontal Line');
  const r = (await page.getByTestId('chart').boundingBox())!;
  await page.mouse.move(r.x + r.width * 0.5, r.y + r.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(r.x + r.width * 0.6, r.y + r.height * 0.55);
  await page.mouse.up();
  await expect(page.locator('.chart-status')).toContainText('1 DRAWINGS');
  expect(errors).toEqual([]);
});
test('SEC unavailable/missing and delayed symbol response never break or contaminate the chart', async ({
  page,
}) => {
  let mode: 'error' | 'missing' | 'slow' = 'error';
  await page.route('**/api/fundamentals?**', async (route) => {
    const symbol = new URL(route.request().url()).searchParams.get('symbol')!;
    if (mode === 'error') {
      await route.fulfill({ status: 502, body: '{}' });
      return;
    }
    if (mode === 'missing') {
      await route.fulfill({ contentType: 'application/json', body: '[]' });
      return;
    }
    if (symbol === 'AAPL') await new Promise((resolve) => setTimeout(resolve, 500));
    await route
      .fulfill({
        contentType: 'application/json',
        body: JSON.stringify(fixtures[symbol]?.quarterly ?? []),
      })
      .catch(() => {});
  });
  await page.goto('/');
  await loaded(page);
  await open(page);
  await expect(page.getByTestId('financial-panel').getByRole('alert')).toContainText(
    'SEC 資料來源暫時無法使用',
  );
  await close(page);
  await loaded(page);
  mode = 'missing';
  await switchTo(page, 'NODATA');
  await expect(page.getByTestId('financial-panel')).toContainText('此資料來源暫無可用財報');
  await close(page);
  mode = 'slow';
  if (!(await page.locator('.mobile-nav').isVisible()))
    await page.getByRole('button', { name: 'Indicators', exact: true }).click();
  const pending = page.waitForRequest('**/api/fundamentals?symbol=AAPL*');
  await switchTo(page, 'AAPL');
  if (!(await page.locator('.mobile-nav').isVisible())) await open(page);
  await pending;
  await switchTo(page, 'NVDA');
  await expect(
    page.getByTestId('financial-panel').getByLabel('Fiscal period', { exact: true }),
  ).toHaveValue('2026-07-26');
  await page.waitForTimeout(650);
  await expect(page.getByTestId('financial-panel')).toHaveAttribute(
    'aria-label',
    'NVDA financials',
  );
  await expect(
    page.getByTestId('financial-panel').getByLabel('Fiscal period', { exact: true }),
  ).toHaveValue('2026-07-26');
  await close(page);
  await loaded(page);
});

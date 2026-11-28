import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { mockMarketLogin } from './account-fixture';
import { mockSite, valuation } from './mocks';

/** 揚秦 2755.TWO statements (FinMind, trimmed) with its 配股 events: ex 2025-09-04 1.9738 and 2026-07-16 2.4646 元. */
const file = readFileSync(resolve(import.meta.dirname, '../tests/fixtures/financials/tw-2755-stock-dividends.json'), 'utf8');
/** 可寧衛 8422.TW (FinMind, trimmed): 1 拆 10 on 2025-11-06; Q4 2025 published as 1.29 − (3.97 + 4.03 + 2.48) = −9.18. */
const kingNet = readFileSync(resolve(import.meta.dirname, '../tests/fixtures/financials/tw-8422-split.json'), 'utf8');
/** 寶雅 5904.TWO (FinMind, as on main 2026-11-23 without sourceCorrections): par 10 → 1 between the 2026 Q2 and Q3 reports, no split event. */
const poya = readFileSync(resolve(import.meta.dirname, '../tests/fixtures/financials/tw-5904-par-split.json'), 'utf8');

async function routeFile(page: Page) {
  await page.route('**/my-stock-screener/report/financials/*.json', (route) => {
    const url = route.request().url();
    const body = url.includes('2755.TWO') ? file : url.includes('8422.TW') ? kingNet : url.includes('5904.TWO') ? poya : null;
    return body ? route.fulfill({ status: 200, contentType: 'application/json', body }) : route.fulfill({ status: 404, body: 'not found' });
  });
}

test('financials EPS on today\'s share basis by default, 公告 on request; 390px phone', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await page.setViewportSize({ width: 390, height: 844 });
  await mockSite(page);
  await routeFile(page);
  await page.goto('./?page=financials&symbol=2755.TWO&p=q');

  const toggle = page.getByTestId('eps-basis');
  await expect(toggle.getByRole('button', { name: '還原' })).toHaveAttribute('aria-pressed', 'true');
  await expect(toggle).toContainText('還原 EPS（依配股追溯調整）');
  const growth = page.locator('.fin-card[data-group="growth"]');
  await expect(growth.locator('.fin-chart-title')).toContainText('還原 EPS（依配股追溯調整）');
  // FY2024 Q2 reported 1.47 on 30M shares; on today's 47M shares 0.98
  await growth.getByRole('button', { name: /^FY2024 Q2 還原 EPS/ }).click();
  await expect(growth.locator('.fin-detail-value b')).toHaveText('0.98 元');
  await expect(growth.getByTestId('fin-reported-eps')).toHaveText('公告 1.47');
  await growth.screenshot({ path: testInfo.outputPath('eps-restated-phone.png') });

  await toggle.getByRole('button', { name: '公告' }).click();
  await expect(growth.locator('.fin-chart-title')).toHaveText('EPS（元）');
  await growth.getByRole('button', { name: /^FY2024 Q2 EPS 1\.47/ }).click();
  await expect(growth.locator('.fin-detail-value b')).toHaveText('1.47 元');

  await page.getByRole('group', { name: '顯示方式' }).getByRole('button', { name: '表格' }).click();
  const names = page.locator('.fin-table tbody .fin-row-name');
  await expect(names.filter({ hasText: 'EPS' })).toHaveText(['公告 EPS', '還原 EPS（依配股追溯調整）']);
  await toggle.getByRole('button', { name: '還原' }).click();
  await expect(names.filter({ hasText: 'EPS' })).toHaveText(['還原 EPS（依配股追溯調整）', '公告 EPS']);
  expect(errors).toEqual([]);
});

test('chart header P/E uses the restated trailing EPS (5.74, not the reported 6.42)', async ({ page }, testInfo) => {
  await page.clock.setFixedTime(new Date('2026-11-22T10:00:00+08:00'));
  await mockMarketLogin(page); // quotes come from market-chart, which needs a LINE login
  await mockSite(page);
  await routeFile(page);
  const tw = {
    ...valuation.TW,
    items: { ...valuation.TW.items, '2755.TWO': { epsTtm: 6.42, epsAnnual: 6.44, fiscalYear: 2025, exchangePeTtm: 12.3, epsTtmCalculated: 5.7437, epsTtmCalculation: 'restated', epsAnnualRestated: 5.1666, epsNote: '本益比以目前股數的還原 EPS（依配股追溯調整）計算：近四季 EPS 公告 6.42 → 還原 5.74；2025 年 EPS 公告 6.44 → 還原 5.17', asOf: '2026-10-07', source: 'TPEx' } },
  };
  await page.route('**/my-stock-screener/atlas/valuation/tw.json', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(tw) }));
  await page.goto('./?page=chart&symbol=2755.TWO&tf=1D');
  const quote = page.getByTestId('header-quote').locator('b');
  await expect(quote).not.toHaveText('—', { timeout: 20000 });
  const price = Number((await quote.innerText()).replace(/,/g, ''));
  expect(price).toBeGreaterThan(0);
  const valuationBox = page.getByTestId('valuation');
  await expect(valuationBox).toContainText((Math.round((price / 5.7437) * 100) / 100).toFixed(1));
  await expect(valuationBox).toContainText((Math.round((price / 5.1666) * 100) / 100).toFixed(1));
  await expect(valuationBox).not.toContainText('12.3');
  await page.getByTestId('valuation-help-toggle').click();
  await expect(page.getByTestId('valuation-eps-note')).toContainText('近四季 EPS 公告 6.42 → 還原 5.74');
  if (testInfo.project.name === 'iphone-webkit') {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: testInfo.outputPath('tw-pe-restated-390.png') });
  }
});

test('a derived Q4 across a split is recomputed on today\'s basis (8422.TW Q4 2025 0.24, 公告 −9.18 with a note); 390px', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await page.setViewportSize({ width: 390, height: 844 });
  await mockSite(page);
  await routeFile(page);
  await page.goto('./?page=financials&symbol=8422.TW&p=q');

  const toggle = page.getByTestId('eps-basis');
  await expect(toggle).toContainText('還原 EPS（依分割追溯調整）');
  const growth = page.locator('.fin-card[data-group="growth"]');
  await growth.getByRole('button', { name: /^FY2025 Q4 還原 EPS/ }).click();
  await expect(growth.locator('.fin-detail-value b')).toHaveText('0.24 元');
  await expect(growth.getByTestId('fin-reported-eps')).toHaveText('公告 -9.18');
  await expect(growth).toContainText('還原全年 EPS 1.29 − 還原前三季 (0.397 + 0.403 + 0.248) = 0.242');
  const shot = process.env.EPS_Q4_SCREENSHOT;
  await growth.screenshot({ path: shot || testInfo.outputPath('eps-q4-recomputed-phone.png') });

  await toggle.getByRole('button', { name: '公告' }).click();
  await growth.getByRole('button', { name: /^FY2025 Q4 EPS -9\.18/ }).click();
  await expect(growth.locator('.fin-detail-value b')).toHaveText('-9.18 元');
  await expect(growth).toContainText('此季為全年減前三季推算，跨越股數變動，請參考還原值');
  expect(errors).toEqual([]);
});

test('an inferred par-value change restates the earlier quarters (5904.TWO Q2 2026 0.815, Q3 YoY +25%); 390px', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await page.setViewportSize({ width: 390, height: 844 });
  await mockSite(page);
  await routeFile(page);
  await page.goto('./?page=financials&symbol=5904.TWO&p=q');

  const toggle = page.getByTestId('eps-basis');
  await expect(toggle.getByRole('button', { name: '還原' })).toHaveAttribute('aria-pressed', 'true');
  await expect(toggle).toContainText('還原 EPS（依配股與分割追溯調整）');
  const growth = page.locator('.fin-card[data-group="growth"]');
  await growth.getByRole('button', { name: /^FY2026 Q2 還原 EPS/ }).click();
  await expect(growth.locator('.fin-detail-value b')).toHaveText('0.81 元');
  await expect(growth.getByTestId('fin-reported-eps')).toHaveText('公告 8.15');
  await expect(growth).toContainText('推定股票分割 1 拆 10');
  await growth.getByRole('button', { name: /^FY2026 Q3 還原 EPS/ }).click();
  await expect(growth.locator('.fin-detail-value b')).toHaveText('0.95 元');
  await expect(growth).toContainText('+25.2%');
  const shot = process.env.EPS_SPLIT_SCREENSHOT;
  await growth.screenshot({ path: shot || testInfo.outputPath('eps-inferred-split-phone.png') });
  await expect(page.locator('.fin-notes')).toContainText('推定股票分割 1 拆 10');
  expect(errors).toEqual([]);
});

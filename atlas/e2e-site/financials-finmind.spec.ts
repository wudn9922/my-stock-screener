import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';

/** Synthetic Taiwan statements file written by build_financials_tw_finmind.py (made-up numbers, symbol 9999.TW). */
const finmindFile = readFileSync(resolve(import.meta.dirname, '../tests/fixtures/financials/tw-finmind.json'), 'utf8');

async function openFinmindPage(page: Page) {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/financials/*.json', (route) => {
    const key = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop()!.replace(/\.json$/, ''));
    if (key !== '9999.TW') return route.fulfill({ status: 404, body: 'not found' });
    return route.fulfill({ status: 200, contentType: 'application/json', body: finmindFile });
  });
  await page.goto('./?page=financials&symbol=9999.TW');
}

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => {
    const main = document.getElementById('main')!;
    return Math.max(document.scrollingElement!.scrollWidth - innerWidth, main.scrollWidth - main.clientWidth);
  });

test('FinMind statements page: cards, provenance, statement source row; desktop and 390px phone', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await page.setViewportSize({ width: 1280, height: 900 });
  await openFinmindPage(page);

  await expect(page.locator('.fin-title')).toContainText('9999');
  await expect(page.locator('.fin-title')).toContainText('TWD');
  await expect(page.locator('.fin-card')).toHaveCount(6);
  await expect(page.locator('.fin-depth')).toContainText('FinMind（公開資訊觀測站）申報歷史');
  await expect(page.locator('.fin-notes')).toContainText('公開資訊觀測站（FinMind）');

  const growth = page.locator('.fin-card[data-group="growth"]');
  await growth.getByRole('button', { name: /^FY2016 營收/ }).click();
  await expect(growth.locator('.fin-detail')).toContainText('FinMind');

  const profit = page.locator('.fin-card[data-group="profitability"]');
  await expect(profit).toContainText('毛利');
  await page.getByRole('group', { name: '顯示方式' }).getByRole('button', { name: '表格' }).click();
  await expect(page.locator('.fin-source-row td').first()).toContainText('FinMind');
  await expect(page.locator('.fin-table-note')).toContainText('數字表顯示所有已取得期別');
  await page.screenshot({ path: testInfo.outputPath('finmind-desktop.png'), fullPage: true });
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath('finmind-phone-table.png'), fullPage: true });
  await page.getByRole('group', { name: '顯示方式' }).getByRole('button', { name: '圖表' }).click();
  await expect(page.locator('.fin-card')).toHaveCount(6);
  await expect(page.locator('.fin-card[data-group="profitability"]')).toContainText('毛利');
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath('finmind-phone.png'), fullPage: true });
  await page.locator('.fin-card[data-group="growth"]').screenshot({ path: testInfo.outputPath('finmind-phone-growth.png') });
  await profit.screenshot({ path: testInfo.outputPath('finmind-phone-profit.png') });
  expect(errors).toEqual([]);
});

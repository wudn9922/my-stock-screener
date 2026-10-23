import { expect, test } from '@playwright/test';
import { buildReport, mockSite } from './mocks';

test('mixed universe dates are labelled, grouped and scoped in filters', async ({ page }) => {
  const universe = { version: 1, fields: ['symbol','name','close','changePct','volume','rsRank','rs63','asOf'],
    markets: { TW: { benchmark: '^TWII', asOf: '2026-10-21', scope: 'full', universe: '日期測試', rows: [
      ['2330.TW','台積電',100,1,100,50,2,'2026-10-21'],
      ['1240.TWO','茂生農經',100,99,10000,99,50,'2026-10-22'],
      ['1101.TW','台泥',100,2,200,70,3,'2026-10-21'],
    ] } } };
  await mockSite(page, { report: buildReport(20), universe });
  await page.goto('./?page=screener&group=universe-tw');
  await expect(page.locator('.row-observation-date')).toHaveText('資料日 2026-10-22');
  await expect(page.locator('.screener-date-notice')).toContainText('只比較 2026-10-21');
  await page.getByRole('combobox', { name: '排序' }).selectOption('gain');
  const rows = page.locator('.screener-row .row-id b');
  await expect(rows).toHaveText(['1101','2330','1240']);
  // Direct links use the same filter path as the filter sheet.
  await page.goto('./?page=screener&group=universe-tw&f=chg:0:100');
  await expect(page.locator('.screener-row .row-id b')).toHaveText(['2330','1101']);
  await expect(page.locator('.row-observation-date')).toHaveCount(0);
});

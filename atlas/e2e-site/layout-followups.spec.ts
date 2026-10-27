import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';
import { researchFixtureBody } from './research-fixtures';

test('世界 views stay on one row on narrow phones in every view', async ({ page }) => {
  await mockSite(page);
  await page.route('**/report/research/**', (route) => {
    const path = new URL(route.request().url()).pathname.split('/research/')[1]!;
    try { return route.fulfill({ contentType: 'application/json', body: researchFixtureBody(path).toString() }); } catch { return route.fulfill({ status: 404 }); }
  });
  for (const [file, fixture] of [['rates', 'backend-sample-rates'], ['rates-history', 'backend-sample-rates-history']])
    await page.route(`**/report/${file}.json`, (route) => route.fulfill({ json: JSON.parse(readFileSync(new URL(`../tests/fixtures/rates/${fixture}.json`, import.meta.url), 'utf8')) }));
  for (const width of [320, 375, 390]) {
    await page.setViewportSize({ width, height: 760 });
    for (const view of ['', '&view=indices', '&view=commodities', '&view=fx']) {
      await page.goto(`./?page=world${view}`);
      const tabs = page.getByRole('group', { name: '世界頁面' }).getByRole('button');
      await expect(tabs).toHaveText(['美債利率', '指數', '原物料', '匯率']);
      const boxes = await tabs.evaluateAll((buttons) => buttons.map((button) => button.getBoundingClientRect()));
      expect(new Set(boxes.map((box) => Math.round(box.top))).size, `${width}px ${view || 'rates'}`).toBe(1);
      for (const box of boxes) expect(box.height).toBeGreaterThanOrEqual(43.5);
      expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    }
  }
});

test('the 價量 view opens with the MA recommendation first', async ({ page }) => {
  await mockSite(page);
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await page.getByTestId('stock-tab-volume').click();
  const view = page.getByRole('region', { name: '價量', exact: true });
  await expect(view.locator('.stock-view-card > h2')).toHaveText(['建議均線', '價量分析']);
  await expect(view.getByTestId('ma-recommendation')).toBeInViewport();
});

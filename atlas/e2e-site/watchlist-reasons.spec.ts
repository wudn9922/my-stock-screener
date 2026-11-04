import { expect, test, type Page } from '@playwright/test';
import { buildReport, mockSite } from './mocks';
import { accountPreference, mockDeviceAccount } from './account-fixture';
import { createWatchlist, visitWatchlist } from '../src/watchlists/model';

async function setup(page: Page) {
  await mockDeviceAccount(page);
  const report = buildReport(3) as ReturnType<typeof buildReport> & { groups: { key: string; items: { symbol: string; name: string; metrics: Record<string, unknown> }[] }[] };
  report.reportDate = '2026-10-08'; report.generatedAt = '2026-10-08T18:00:00Z';
  const items = report.groups.find(group => group.key === 'tw_all')!.items;
  items.forEach((item, index) => { item.metrics.rsRank = index === 0 ? 70 : 90; });
  const list = visitWatchlist(createWatchlist({ name: '進出榜實證', market: 'TW', group: 'tw_all', filters: 'rs:80;vol:10000' }), [items[0]!], '2026-10-07', 1).list;
  const key = accountPreference('atlas-saved-watchlists-v1');
  await page.addInitScript(({ key, list }) => {
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ version: 1, lists: [list] }));
  }, { key, list });
  await mockSite(page, { report });
  return { items, key, list };
}

test('watchlist changes expand current evidence with TW volume lots and open the individual chart', async ({ page }, testInfo) => {
  const { items } = await setup(page);
  await page.goto('./?page=watchlist');
  const entered = page.getByRole('region', { name: '進榜原因' });
  const exited = page.getByRole('region', { name: '出榜原因' });
  await expect(entered.locator('details')).toHaveCount(2);
  await expect(exited.locator('details')).toHaveCount(1);
  const reason = exited.locator('details');
  await expect(reason.locator('summary')).toContainText('未通過：RS 百分位 ≥80');
  await expect(reason.locator('ul')).toBeHidden();
  await reason.locator('summary').click();
  await expect(reason).toContainText('RS 百分位 70');
  await expect(reason).toContainText('20 日平均成交量 100 張');
  await expect(reason).toContainText('不推測先前未保存的指標變化');
  await expect(reason).toContainText('2026-10-07 與本次 2026-10-08');
  const firstEntry = entered.locator('details').first();
  await firstEntry.locator('summary').click();
  await expect(firstEntry).toContainText('本次符合 2 項條件');
  await expect(firstEntry).toContainText('RS 百分位 90');
  expect((await reason.locator('summary').boundingBox())!.height).toBeGreaterThanOrEqual(44);
  expect(await page.locator('#main').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath('watchlist-reasons.png') });
  await reason.getByRole('button', { name: '開啟個股圖表', exact: true }).click();
  await expect(page.getByTestId('active-symbol')).toHaveText(items[0]!.symbol);
});

test('an unavailable condition preserves the prior opening baseline instead of inventing exit reasons', async ({ page }) => {
  const { key, list } = await setup(page);
  const report = buildReport(3) as ReturnType<typeof buildReport> & { groups: { key: string; items: { metrics: Record<string, unknown> }[] }[] };
  report.reportDate = '2026-10-08'; report.generatedAt = '2026-10-08T18:00:00Z';
  report.groups.find(group => group.key === 'tw_all')!.items.forEach(item => { delete item.metrics.rsRank; });
  await page.route('**/my-stock-screener/report/latest.json', route => route.fulfill({ json: report }));
  await page.goto('./?page=watchlist');
  await expect(page.getByRole('region', { name: '清單追蹤結果' })).toContainText('本次沒有取得完整資料，未判定新入選或出榜。');
  await expect(page.getByRole('region', { name: '清單追蹤結果' }).getByRole('heading', { name: '上次成功名單（2026-10-07）' })).toBeVisible();
  await expect(page.getByRole('region', { name: '出榜原因' })).toHaveCount(0);
  const stored = await page.evaluate(key => JSON.parse(localStorage.getItem(key)!).lists[0], key);
  expect(stored.lastViewed).toEqual(list.lastViewed);
  expect(stored.lastOpenedAt).toBe(list.lastOpenedAt);
});

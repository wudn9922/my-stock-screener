import { expect, test } from '@playwright/test';
import { mockSite, buildReport, yahooChart } from './mocks';
import { mockDeviceAccount, accountPreference } from './account-fixture';
import { createWatchlist, visitWatchlist } from '../src/watchlists/model';

test('A1 receive time stays above the list without adding toolbar height and advances via the batch feed', async ({ page }, info) => {
  await page.clock.install({ time: new Date('2026-11-04T04:20:00Z') });
  await mockDeviceAccount(page);
  const report = buildReport(35) as ReturnType<typeof buildReport> & { reportDate: string; groups: { key: string; items: { symbol: string; name: string }[] }[] };
  const members = report.groups.find(g => g.key === 'tw_all')!.items.slice(0, 30);
  const list = visitWatchlist(createWatchlist({ name: 'A1 即時清單', market: 'TW', kind: 'manual', group: 'tw_all', filters: '', symbols: members.map(x => x.symbol) }), members, report.reportDate).list;
  await page.addInitScript(({ key, list }) => localStorage.setItem(key, JSON.stringify({ version: 1, lists: [list] })), { key: accountPreference('atlas-saved-watchlists-v1'), list });
  await mockSite(page, { report });
  let release!: () => void;
  const first = new Promise<void>(resolve => { release = resolve; });
  let requests = 0;
  await page.route('https://edge.test/functions/v1/market-chart**', async route => {
    const symbols = new URL(route.request().url()).searchParams.get('quotes');
    if (!symbols) return route.fallback();
    requests++;
    if (requests === 1) await first;
    return route.fulfill({ json: { results: Object.fromEntries(symbols.split(',').map(s => [s, yahooChart(s, '1d')])), errors: {} } });
  });
  await page.goto('./?page=watchlist');
  const toolbar = page.locator('.wb-list-toolbar');
  await expect(page.locator('.wb-row').first()).toBeVisible();
  const before = (await toolbar.boundingBox())!.height;
  release();
  const time = toolbar.getByTestId('board-live-time');
  await expect(time).toHaveText(/^更新於 12:20:\d{2}$/);
  const firstTime = await time.textContent();
  expect((await toolbar.boundingBox())!.height).toBe(before);
  expect(await page.locator('.site-data-footer').getByTestId('board-live-time').count()).toBe(0);
  const layout = await page.locator('#main').evaluate(main => ({
    proportion: (main.getBoundingClientRect().bottom - document.querySelector('.wb-rows')!.getBoundingClientRect().top) / main.getBoundingClientRect().height,
    overflow: main.scrollWidth - main.clientWidth,
  }));
  expect(layout.proportion).toBeGreaterThanOrEqual(.8);
  expect(layout.overflow).toBe(0);
  await page.clock.fastForward(61000);
  await expect(time).toHaveText(/^更新於 12:21:\d{2}$/);
  expect(await time.textContent()).not.toBe(firstTime);
  expect(requests).toBeGreaterThanOrEqual(2);
  await page.screenshot({ path: info.outputPath('a1-live-clock.png') });
});

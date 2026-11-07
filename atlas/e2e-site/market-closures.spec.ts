import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';
import { archiveIndexFixture } from '../tests/fixtures/archive-index';

const root = 'https://raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/';
// Synthetic prices: 10/22 is an individual halt on an open market date and must
// remain. Only 10/23 is the independently audited market-wide legacy closure.
const body = 'Date,Open,High,Low,Close,Volume\n2026-10-21,99,101,98,100,1000\n2026-10-22,100,100,100,100,0\n2026-10-23,100,100,100,100,0\n';

for (const mode of ['audited-offline', 'published-evidence', 'gap-with-trading'] as const) {
  test(`closed market: ${mode} filters daily/weekly/monthly without erasing individual halts`, async ({ page }, info) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await mockSite(page);
    const traded = mode === 'gap-with-trading';
    const csv = traded ? 'Date,Open,High,Low,Close,Volume\n2026-11-03,99,101,98,100,1000\n2026-11-04,100,102,99,101,1000\n' : body;
    const policy = traded ? { version: 1, threshold: .95, markets: { tw: { closedSessions: [], indexGapsWithTrading: [{ date: '2026-11-04', reason: 'primary-index-gap-with-trading', observedSymbols: 100, positiveVolumeSymbols: 100 }] } } } : { version: 1, threshold: .95, markets: { tw: { closedSessions: [{
      // Synthetic future evidence also removes 10/22, proving the reader uses
      // the published policy rather than only its bundled 10/23 audit seed.
      date: '2026-10-22', reasons: ['flat-zero-cohort', 'primary-index-missing'], calendar: 'XTAI', calendarSession: true, observedSymbols: 100,
      flatZeroSymbols: 95, eligibleSymbols: 100, unknownVolumeSymbols: 0,
      flatZeroShare: .95, coverageReference: 100,
    }] } } };
    await page.route('**/report/market-closures.json', route => route.fulfill(mode === 'audited-offline'
      ? { status: 503, body: 'unavailable' } : { json: policy }));
    await page.route(root + '**', route => {
      const path = route.request().url().slice(root.length);
      if (path === 'index/tw/2330.TW.json') return route.fulfill({ json: archiveIndexFixture('2330.TW', { 2026: csv }) });
      return route.fulfill(path === 'tw/2330.TW/2026.csv' ? { contentType: 'text/csv', body: csv } : { status: 404 });
    });
    await page.route('**/functions/v1/market-chart?**', route => route.fulfill({ status: 502, body: 'unavailable' }));
    const cutoff = traded ? '2026-11-04' : mode === 'audited-offline' ? '2026-10-22' : '2026-10-21';
    for (const tf of ['1D', '1W', '1M']) {
      await page.goto(`./?page=chart&symbol=2330.TW&tf=${tf}`);
      await expect(page.getByTestId('chart')).toBeVisible();
      await expect(page.locator('.chart-loading.error')).toHaveCount(0);
      await expect(page.getByTestId('ohlc-header')).toContainText(traded ? '101.00' : '100.00');
      await expect(page.getByTestId('source-label')).toContainText(`顯示至 ${cutoff} 收盤`);
      await expect(page.getByTestId('archive-availability')).toContainText(cutoff);
      expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
      if (tf === '1D') await page.screenshot({ path: info.outputPath(`market-closure-${mode}.png`) });
    }
    expect(errors).toEqual([]);
  });
}

test('calendar session gap retains official April 6 in daily, weekly and monthly charts', async ({ page }, info) => {
  await mockSite(page);
  const csv = 'Date,Open,High,Low,Close,Volume\n2021-04-01,207,208,206,207,\n2021-04-06,207.79,209.81,207.79,209.70,\n';
  await page.route('**/report/market-closures.json', route => route.fulfill({ status: 503 }));
  await page.route(root + '**', route => {
    const path = decodeURIComponent(route.request().url().slice(root.length));
    if (path === 'index/tw/%5ETWOII.json') return route.fulfill({ json: archiveIndexFixture('^TWOII', { 2021: csv }) });
    return route.fulfill(path === 'tw/^TWOII/2021.csv' ? { contentType: 'text/csv', body: csv } : { status: 404 });
  });
  await page.route('**/functions/v1/market-chart?**', route => route.fulfill({ status: 502, body: 'unavailable' }));
  for (const tf of ['1D', '1W', '1M']) {
    await page.goto(`./?page=chart&symbol=%5ETWOII&tf=${tf}`);
    await expect(page.getByTestId('chart')).toBeVisible();
    await expect(page.locator('.chart-loading.error')).toHaveCount(0);
    await expect(page.getByTestId('ohlc-header')).toContainText('209.70');
    await expect(page.getByTestId('archive-availability')).toContainText('2021-04-06');
    expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
    if (tf === '1D') await page.screenshot({ path: info.outputPath('calendar-missing-session.png') });
  }
});

test('physically removed placeholder keeps chart dates and prices aligned', async ({ page }) => {
  await mockSite(page);
  const csv = 'Date,Open,High,Low,Close,Volume\n2026-10-22,100,100,100,100,0\n2026-10-27,101,102,100,101,1000\n';
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/report/market-closures.json', route => route.fulfill({ status: 503 }));
  await page.route(root + '**', route => {
    const path = route.request().url().slice(root.length);
    if (path === 'index/tw/2330.TW.json') return route.fulfill({ json: archiveIndexFixture('2330.TW', { 2026: csv }) });
    return route.fulfill(path === 'tw/2330.TW/2026.csv' ? { contentType: 'text/csv', body: csv } : { status: 404 });
  });
  await page.route('**/functions/v1/market-chart?**', route => route.fulfill({ status: 502 }));
  for (const tf of ['1D', '1W', '1M']) {
    await page.goto(`./?page=chart&symbol=2330.TW&tf=${tf}`);
    await expect(page.getByTestId('chart')).toBeVisible();
    await expect(page.locator('.chart-loading.error')).toHaveCount(0);
    await expect(page.getByTestId('ohlc-header')).toContainText('101.00');
    await expect(page.getByTestId('source-label')).toContainText('顯示至 2026-10-27 收盤');
    await expect(page.getByTestId('archive-availability')).toContainText('2026-10-27');
    expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  }
  expect(errors).toEqual([]);
});

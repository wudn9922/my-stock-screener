import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';
import { archiveIndexFixture } from '../tests/fixtures/archive-index';

const root = 'https://raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/';
// Synthetic prices: 10/22 is an individual halt on an open market date and must
// remain. Only 10/23 is the independently audited market-wide legacy closure.
const body = 'Date,Open,High,Low,Close,Volume\n2026-10-21,99,101,98,100,1000\n2026-10-22,100,100,100,100,0\n2026-10-23,100,100,100,100,0\n';

for (const mode of ['audited-offline', 'published-evidence'] as const) {
  test(`closed market: ${mode} filters daily/weekly/monthly without erasing individual halts`, async ({ page }, info) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await mockSite(page);
    const policy = { version: 1, threshold: .95, markets: { tw: { closedSessions: [{
      // Synthetic future evidence also removes 10/22, proving the reader uses
      // the published policy rather than only its bundled 10/23 audit seed.
      date: '2026-10-22', reasons: ['flat-zero-cohort'], observedSymbols: 100,
      flatZeroSymbols: 95, eligibleSymbols: 100, unknownVolumeSymbols: 0,
      flatZeroShare: .95, coverageReference: 100,
    }] } } };
    await page.route('**/report/market-closures.json', route => route.fulfill(mode === 'audited-offline'
      ? { status: 503, body: 'unavailable' } : { json: policy }));
    await page.route(root + '**', route => {
      const path = route.request().url().slice(root.length);
      if (path === 'index/tw/2330.TW.json') return route.fulfill({ json: archiveIndexFixture('2330.TW', { 2026: body }) });
      return route.fulfill(path === 'tw/2330.TW/2026.csv' ? { contentType: 'text/csv', body } : { status: 404 });
    });
    await page.route('**/functions/v1/market-chart?**', route => route.fulfill({ status: 502, body: 'unavailable' }));
    const cutoff = mode === 'audited-offline' ? '2026-10-22' : '2026-10-21';
    for (const tf of ['1D', '1W', '1M']) {
      await page.goto(`./?page=chart&symbol=2330.TW&tf=${tf}`);
      await expect(page.getByTestId('chart')).toBeVisible();
      await expect(page.locator('.chart-loading.error')).toHaveCount(0);
      await expect(page.getByTestId('ohlc-header')).toContainText('100.00');
      await expect(page.getByTestId('source-label')).toContainText(`顯示至 ${cutoff} 收盤`);
      await expect(page.getByTestId('archive-availability')).toContainText(cutoff);
      expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
      if (tf === '1D') await page.screenshot({ path: info.outputPath(`market-closure-${mode}.png`) });
    }
    expect(errors).toEqual([]);
  });
}

import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { buildReport, dailyBars, mockSite } from './mocks';
import { mockDeviceAccount } from './account-fixture';
import { formatMarketVolume } from '../src/ui/format';

test('screener chart review follows filters and search, returns to the same rows and scrolls all sixty charts', async ({ page }, info) => {
  await mockDeviceAccount(page);
  await mockSite(page, { report: buildReport(60) });
  await page.goto('./?page=screener&group=tw_all&f=vol:5800000');
  await expect(page.locator('.screener-row')).toHaveCount(3);
  await page.getByLabel('排序', { exact: true }).selectOption('gain');
  const expected = await page.locator('.screener-row .row-id > small').allTextContents();
  const charts = page.getByRole('region', { name: '連續股票圖表' });
  const toggle = page.locator('.screener-chart-toggle');
  await expect(toggle).toHaveText('連續看全部股票圖表（3 檔）');
  await toggle.click();
  await expect(charts.locator('.stock-chart-name h3')).toHaveText(expected);
  const first = charts.locator('.stock-chart-card').first();
  await first.scrollIntoViewIfNeeded();
  await expect(first.locator('.mini-chart')).toHaveAttribute('data-status', 'ready');
  await expect(first.locator('.stock-chart-date')).toContainText('近 6 個月');
  await expect(first.locator('.stock-chart-date')).toContainText('SMA 20');
  await expect(first.getByText('日 K · 成交量', { exact: true })).toBeVisible();
  const search = page.getByRole('searchbox', { name: '在群組內篩選' });
  await search.fill(expected[0]!);
  await expect(charts.locator('.stock-chart-card')).toHaveCount(1);
  await search.fill('');
  await toggle.click();
  await expect(page.locator('.screener-row .row-id > small')).toHaveText(expected);
  expect(new URL(page.url()).searchParams.get('f')).toBe('vol:5800000');
  await page.getByRole('button', { name: /移除條件：20 日均量/ }).click();
  await toggle.click();
  await expect(charts.locator('.stock-chart-card')).toHaveCount(20);
  for (const count of [40, 60]) {
    await charts.getByRole('button', { name: /繼續載入圖表/ }).scrollIntoViewIfNeeded();
    await expect(charts.locator('.stock-chart-card')).toHaveCount(count);
  }
  const last = charts.locator('.stock-chart-card').last();
  await last.scrollIntoViewIfNeeded();
  await expect(last.locator('.mini-chart')).toHaveAttribute('data-status', 'ready');
  await expect(first.locator('.mini-chart')).toHaveCount(0);
  expect(await charts.locator('.mini-chart').count()).toBeLessThan(10);
  expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: info.outputPath('screener-chart-review.png') });
  await last.getByRole('button', { name: '開啟個股', exact: true }).click();
  await expect(page.getByTestId('active-symbol')).toBeVisible();
});

test('published whole-market RS leads comparison and stays independent of comparison controls', async ({ page }, info) => {
  await mockSite(page, { universe: { fields: ['symbol', 'rsRank', 'asOf'], markets: {
    TW: { scope: 'full', benchmark: '^TWII', asOf: '2026-10-29', rows: [['2330.TW', 87, '2026-10-29']] },
  } } });
  await page.goto('./?page=chart&symbol=2330.TW&tf=1D');
  await page.getByTestId('stock-tab-research').click();
  const panel = page.getByRole('region', { name: '個股與大盤強弱比較' });
  const rs = panel.getByTestId('stock-rs');
  await expect(rs.locator('b')).toHaveText('87');
  await expect(rs).toContainText('台股全市場');
  await expect(rs).toContainText('2026-10-29');
  const rsBox = (await rs.boundingBox())!;
  expect(rsBox.y + rsBox.height).toBeLessThanOrEqual((await panel.locator('.analysis-controls').boundingBox())!.y);
  await panel.getByLabel('比較基準', { exact: true }).selectOption('^TWOII');
  await panel.getByLabel('相對強弱期間').selectOption('21');
  await expect(panel.getByTestId('relative-strength-result')).toContainText('^TWOII');
  await expect(rs.locator('b')).toHaveText('87');
  await expect(rs).toContainText('加權指數');
  await page.screenshot({ path: info.outputPath('relative-strength-rank.png') });
});

test('unavailable whole-market RS is explicit while the original comparison still works', async ({ page }) => {
  await mockSite(page);
  await page.route('**/report/universe.json', route => route.fulfill({ status: 404 }));
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await page.getByTestId('stock-tab-research').click();
  await expect(page.getByTestId('stock-rs')).toContainText('全市場 RS 尚無可用資料');
  await expect(page.getByTestId('stock-rs').locator('b')).toHaveText('—');
  await expect(page.getByTestId('relative-strength-result')).toContainText('大盤');
  await page.getByTestId('stock-tab-chart').click();
  await expect(page.getByTestId('chart')).toBeVisible();
});

test('US and TW theme candles show actual daily and aggregated weekly volume in market units', async ({ page }, info) => {
  await mockSite(page);
  for (const market of ['us', 'tw']) {
    const suffix = market === 'tw' ? '-tw' : '';
    const report = JSON.parse(readFileSync(new URL(`../src/report/fixtures/themes${suffix}.sample.json`, import.meta.url), 'utf8')) as { themes: { key: string }[] };
    await page.route(`**/report/themes${suffix}.json`, route => route.fulfill({ json: report }));
    await page.goto(`./?page=themes&market=${market}&theme=${report.themes[0]!.key}`);
    const sheet = page.getByRole('dialog', { name: /題材詳情/ });
    const symbol = await sheet.getByLabel('題材K線標的').inputValue();
    const chart = sheet.locator('.drawable-candles');
    await expect(chart).toHaveAttribute('data-show-volume', 'true');
    const volume = chart.getByTestId('research-volume-value');
    await expect(volume).toContainText(formatMarketVolume(dailyBars(symbol).at(-1)!.volume, symbol));
    await expect(volume).toContainText(market === 'tw' ? '張' : '股');
    await chart.getByRole('button', { name: '週K', exact: true }).click();
    const weekly = dailyBars(symbol).filter(bar => bar.time >= Date.parse('2026-10-05T00:00:00Z') / 1000).reduce((sum, bar) => sum + bar.volume, 0);
    await expect(volume).toContainText(formatMarketVolume(weekly, symbol));
    await chart.scrollIntoViewIfNeeded();
    await chart.screenshot({ path: info.outputPath(`theme-${market}-volume.png`) });
    expect(await sheet.evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  }
});

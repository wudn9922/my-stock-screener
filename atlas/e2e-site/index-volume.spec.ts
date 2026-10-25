import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { dailyBars, mockSite } from './mocks';

const symbols = ['^TWII', '^TWOII', '^GSPC', '^DJI', '^IXIC', '^RUT', '^SOX'];
const breakout = readFileSync(resolve(import.meta.dirname, '../tests/fixtures/breakouts/breakouts.sample.json'), 'utf8');
const panel = (page: Page) => page.getByRole('region', { name: '指數價量分析', exact: true });
function packet(symbol: string, zero = false) {
  const tail = symbol === '^TWII' ? [300, 200, 100, 200, 400] : [300, 400, 200, 100, 50];
  return {
    symbol, name: symbol, source: 'Synthetic index-volume browser fixture', interval: '1D', asOf: Date.now() / 1000,
    bars: dailyBars(symbol, 25).map((bar, index) => {
      const close = 100 + index % 2 * 2;
      return { ...bar, open: close, close, high: close + 1, low: close - 1, volume: zero ? 0 : index < 20 ? 100 : tail[index - 20]! };
    }),
  };
}
async function fixtures(page: Page) {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/breakouts.json', route => route.fulfill({ contentType: 'application/json', body: breakout }));
  await page.route('**/my-stock-screener/report/breakouts-history.json', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ version: 1, intervalDays: 20, snapshots: [] }) }));
  const requested: string[] = [];
  await page.route('**/my-stock-screener/report/series/*.json', route => {
    const symbol = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1)!.replace(/\.json$/, ''));
    if (symbol === 'index') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ symbols }) });
    requested.push(symbol);
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(packet(symbol)) });
  });
  return requested;
}

test('index ratios appear directly above full-market success rates, share chart data and change with the selected window', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const requests = await fixtures(page);
  for (const market of ['tw', 'us']) {
    await page.goto(`./?page=markets&market=${market}`);
    const card = panel(page);
    await expect(card.getByLabel('價量分析指數')).toHaveValue(market === 'tw' ? '^TWII' : '^GSPC');
    await expect(card.locator('.research-stats dd')).toHaveText(market === 'tw'
      ? ['1/10 = 10%', '2/10 = 20%', '1/10 = 10%', '1/10 = 10%']
      : ['1/10 = 10%', '1/10 = 10%', '1/10 = 10%', '2/10 = 20%']);
    await expect(card.locator('.index-volume-date')).toContainText('2026-10-08');
    await expect(card).not.toContainText(/異常量|RVOL/);
    await expect(card.locator('input[type="number"]')).toHaveCount(0);
    expect(await page.locator('.markets-side > section.card').evaluateAll(nodes => nodes.slice(0, 3).map(node => node.className)))
      .toEqual(['card index-volume-card', 'card breakout-card', 'card breadth-card']);
    await card.getByLabel('價量觀測窗口').selectOption('5');
    await expect(card.locator('.research-stats dd')).toHaveText(market === 'tw'
      ? ['1/2 = 50%', '2/3 = 66.7%', '1/2 = 50%', '1/3 = 33.3%']
      : ['1/2 = 50%', '1/3 = 33.3%', '1/2 = 50%', '2/3 = 66.7%']);
    await card.getByLabel('價量觀測窗口').selectOption('10');
    await expect(card.getByText(/有效觀測 10\/10 日/)).toBeVisible();
    await card.getByLabel('價量觀測窗口').selectOption('20');
    await card.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await card.screenshot({ path: testInfo.outputPath(`${market}-index-volume.png`) });
    expect(requests.filter(symbol => symbol === (market === 'tw' ? '^TWII' : '^GSPC'))).toHaveLength(1);
  }
  expect(errors).toEqual([]);
});

test('changing indices clears prior ratios while loading and zero volume stays N/A', async ({ page }) => {
  await fixtures(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/my-stock-screener/report/series/%5ETWOII.json', async route => {
    await held;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(packet('^TWOII', true)) });
  });
  try {
    await page.goto('./?page=markets&market=tw');
    const card = panel(page);
    await expect(card.locator('.research-stats dd').first()).toHaveText('1/10 = 10%');
    await card.getByLabel('價量分析指數').selectOption('^TWOII');
    await expect(card.getByRole('status')).toHaveText('載入指數價量資料…');
    await expect(card.locator('.research-stats dd')).toHaveCount(0);
    release();
    await expect(card.locator('.research-stats dd')).toHaveText(['0/0 = N/A', '0/0 = N/A', '0/0 = N/A', '0/0 = N/A']);
    await expect(card.getByRole('status')).toContainText('暫無可計算比例');
    await card.getByLabel('價量分析指數').selectOption('^TWII');
    await expect(card.locator('.research-stats dd').first()).toHaveText('1/10 = 10%');
    await page.getByRole('tab', { name: '美國' }).click();
    await expect(panel(page).getByLabel('價量分析指數')).toHaveValue('^GSPC');
    await expect(panel(page).locator('.research-stats dd').nth(1)).toHaveText('1/10 = 10%');
  } finally { release(); }
});

test('unavailable daily source leaves success rates usable and an explicit retry recovers', async ({ page }) => {
  await fixtures(page);
  let fail = true;
  await page.route('**/my-stock-screener/report/series/%5ETWII.json', route => route.fulfill(fail
    ? { status: 503, body: 'unavailable' }
    : { contentType: 'application/json', body: JSON.stringify(packet('^TWII')) }));
  await page.goto('./?page=markets&market=tw');
  const card = panel(page);
  await expect(card.getByRole('status')).toContainText('暫時無法取得');
  await expect(card.locator('.research-stats dd')).toHaveCount(0);
  await expect(page.locator('.breakout-card .bo-win').first()).toContainText('50.0%');
  fail = false;
  await card.getByRole('button', { name: '重試價量分析', exact: true }).click();
  await expect(card.locator('.research-stats dd').first()).toHaveText('1/10 = 10%');
});

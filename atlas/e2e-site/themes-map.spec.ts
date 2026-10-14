import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';

/** 題材 heatmap (treemap) view: US and Taiwan, against the sample theme files and a synthetic universe. */
const themesBody = readFileSync(resolve(import.meta.dirname, '../src/report/fixtures/themes.sample.json'), 'utf8');
const themesTwBody = readFileSync(resolve(import.meta.dirname, '../src/report/fixtures/themes-tw.sample.json'), 'utf8');

type SampleThemes = { themes: { key: string; constituents: { symbol: string }[] }[] };
const usSample = JSON.parse(themesBody) as SampleThemes;
const twSample = JSON.parse(themesTwBody) as SampleThemes;
const stockCount = (sample: SampleThemes) => sample.themes.reduce((n, t) => n + t.constituents.length, 0);

/** A universe file whose turnover spans three orders of magnitude, for every symbol of both samples. */
function universeBody() {
  const rows = (symbols: string[]) =>
    symbols.map((symbol, i) => {
      const row: (string | number | null)[] = Array(22).fill(null);
      row[0] = symbol;
      row[1] = symbol;
      row[2] = 100;
      row[21] = 1e7 * 10 ** (i % 4);
      return row;
    });
  const symbols = (sample: SampleThemes) => [...new Set(sample.themes.flatMap((t) => t.constituents.map((c) => c.symbol)))];
  return JSON.stringify({
    version: 1,
    generatedAt: '2026-10-13T00:00:00Z',
    reportDate: '2026-10-13',
    markets: {
      US: { benchmark: 'SPY', asOf: '2026-10-12', universe: 'S&P 500', rows: rows(symbols(usSample)) },
      TW: { benchmark: '^TWII', asOf: '2026-10-13', universe: '台股', rows: rows(symbols(twSample)) },
    },
  });
}

async function mockMap(page: Page, universe: 'ok' | 'missing' = 'ok') {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/themes.json', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: themesBody }));
  await page.route('**/my-stock-screener/report/themes-tw.json', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: themesTwBody }));
  await page.route('**/my-stock-screener/report/universe.json', (route) =>
    universe === 'ok'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: universeBody() })
      : route.fulfill({ status: 404, body: 'not found' }),
  );
}

const horizontalOverflow = (page: Page) => page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth);

test('view toggle switches between the list and the heatmap and keeps the link shareable', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockMap(page);
  await page.goto('./?page=themes');
  const view = page.getByRole('group', { name: '檢視' });
  await expect(view.getByRole('button', { name: '列表' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.map-tile')).toHaveCount(0);
  await view.getByRole('button', { name: '熱力圖' }).click();
  await expect(page).toHaveURL(/page=themes&v=map$/);
  await expect(page.locator('.map-tile')).toHaveCount(stockCount(usSample));
  await expect(page.locator('.theme-tile')).toHaveCount(0);
  await expect(page.locator('.map-group-head')).toHaveCount(usSample.themes.length);
  await view.getByRole('button', { name: '列表' }).click();
  await expect(page).toHaveURL(/page=themes$/);
  await expect(page.locator('.theme-tile')).toHaveCount(30);
  // A shared link opens the heatmap directly; unknown values fall back to the list and are rewritten.
  await page.goto('./?page=themes&v=map');
  await expect(page.locator('.map-tile')).toHaveCount(stockCount(usSample));
  await page.goto('./?page=themes&v=bogus');
  await expect(page.locator('.theme-tile')).toHaveCount(30);
  await expect(page).toHaveURL(/page=themes$/);
  expect(errors).toEqual([]);
});

test('every tile is tappable-sized, nothing scrolls sideways, and the period changes the colours', async ({ page }) => {
  await mockMap(page);
  await page.goto('./?page=themes&v=map');
  await expect(page.locator('.map-tile')).toHaveCount(stockCount(usSample));
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
  const sides = await page.locator('.map-tile').evaluateAll((nodes) =>
    nodes.map((node) => {
      const box = node.getBoundingClientRect();
      return Math.min(box.width, box.height);
    }),
  );
  // 22px layout minimum, less the 0.5px gap on each side
  expect(Math.min(...sides)).toBeGreaterThanOrEqual(20.9);
  const firstLabel = (await page.locator('.map-tile').first().getAttribute('aria-label')) ?? '';
  await expect(page.locator('.map-legend')).toContainText('1月報酬');
  await page.getByRole('group', { name: '期間' }).getByRole('button', { name: '1日' }).click();
  await expect(page.locator('.map-legend')).toContainText('1日報酬');
  await expect(page.locator('.map-tile').first()).not.toHaveAttribute('aria-label', firstLabel);
  await expect(page.locator('.map-tile').first()).toHaveAttribute('aria-label', /1日報酬/);
  // The relative-to-benchmark switch belongs to the list view only.
  await expect(page.getByRole('group', { name: '指標' })).toHaveCount(0);
  // The parent filter narrows the map.
  await page.getByRole('group', { name: '產業大類' }).getByRole('button', { name: /半導體/ }).click();
  await expect(page.locator('.map-group-head')).toHaveCount(5);
});

test('a stock tile opens its chart; a theme header opens the theme sheet and returns to the heatmap', async ({ page }) => {
  await mockMap(page);
  await page.goto('./?page=themes&v=map');
  await page.locator('.map-group-head', { hasText: '光通訊' }).click();
  await expect(page).toHaveURL(/page=themes&v=map&theme=optical/);
  await expect(page.getByRole('dialog', { name: /光通訊/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/page=themes&v=map$/);
  const tile = page.locator('.map-tile').nth(3);
  await expect(tile).toBeVisible();
  const symbol = ((await tile.getAttribute('aria-label')) ?? '').split(' ')[0]!;
  await tile.click();
  await expect(page).toHaveURL(new RegExp(`page=chart&symbol=${symbol}`));
});

test('size modes are remembered across reloads', async ({ page }) => {
  await mockMap(page);
  await page.goto('./?page=themes&v=map');
  const sizes = page.getByRole('group', { name: '大小' });
  await expect(sizes.getByRole('button', { name: '均衡' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.map-foot')).toContainText('近20日平均成交值');
  await expect(page.locator('.map-foot')).toContainText('不是市值');
  await sizes.getByRole('button', { name: '等大小' }).click();
  await expect(sizes.getByRole('button', { name: '等大小' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.map-foot')).toContainText('每檔一樣大');
  await page.reload();
  await expect(sizes.getByRole('button', { name: '等大小' })).toHaveAttribute('aria-pressed', 'true');
  await sizes.getByRole('button', { name: '成交值' }).click();
  await expect(page.locator('.map-foot')).toContainText('小型股會很小');
  await expect(page.locator('.map-tile')).toHaveCount(stockCount(usSample));
});

test('without universe data the map falls back to equal size and says so', async ({ page }) => {
  await mockMap(page, 'missing');
  await page.goto('./?page=themes&v=map');
  await expect(page.locator('.map-tile')).toHaveCount(stockCount(usSample));
  await expect(page.getByRole('status').filter({ hasText: '成交值資料暫時無法取得，以等大小顯示' })).toBeVisible();
  const sizes = page.getByRole('group', { name: '大小' });
  await expect(sizes.getByRole('button', { name: '等大小' })).toHaveAttribute('aria-pressed', 'true');
  await expect(sizes.getByRole('button', { name: '均衡' })).toBeDisabled();
  await expect(sizes.getByRole('button', { name: '成交值' })).toBeDisabled();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
});

test('Taiwan heatmap: the view survives the market tabs and tiles chart with the full symbol', async ({ page }) => {
  await mockMap(page);
  await page.goto('./?page=themes&market=tw&v=map');
  await expect(page.locator('.map-tile')).toHaveCount(stockCount(twSample));
  await expect(page.locator('.map-foot')).toContainText('新台幣');
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
  const tabs = page.getByRole('tablist', { name: '市場' });
  await tabs.getByRole('tab', { name: /美股/ }).click();
  await expect(page).toHaveURL(/page=themes&v=map$/);
  await expect(page.locator('.map-tile')).toHaveCount(stockCount(usSample));
  await tabs.getByRole('tab', { name: /台股/ }).click();
  await expect(page).toHaveURL(/page=themes&market=tw&v=map$/);
  await expect(page.locator('.map-tile')).toHaveCount(stockCount(twSample));
  await page.locator('.map-tile').first().click();
  await expect(page).toHaveURL(/page=chart&symbol=\d{4}\.(TW|TWO)/);
});

test('hovering a tile shows the tooltip with return and traded value', async ({ page, hasTouch }) => {
  test.skip(hasTouch, 'hover is a mouse affordance; touch uses a long press');
  await mockMap(page);
  await page.goto('./?page=themes&v=map');
  await page.locator('.map-tile').nth(5).hover();
  const tip = page.getByRole('tooltip');
  await expect(tip).toBeVisible();
  await expect(tip).toContainText('報酬');
  await expect(tip).toContainText('近20日均成交值');
  await expect(tip).toContainText('美元');
  await page.mouse.move(0, 0);
  await expect(tip).toHaveCount(0);
});

test('long-pressing a tile on a touch screen shows the tooltip without opening the chart', async ({ page, hasTouch }) => {
  test.skip(!hasTouch, 'long press is a touch gesture');
  await mockMap(page);
  await page.goto('./?page=themes&v=map');
  const tile = page.locator('.map-tile').nth(5);
  await tile.scrollIntoViewIfNeeded();
  const box = (await tile.boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  const session = await page.context().newCDPSession(page);
  await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await expect(page.getByRole('tooltip')).toBeVisible();
  await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await expect(page).toHaveURL(/page=themes&v=map$/);
  await expect(page.getByRole('tooltip')).toContainText('近20日均成交值');
});

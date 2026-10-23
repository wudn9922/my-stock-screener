import { mockDeviceAccount, accountPreference } from './account-fixture';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { universeBody, type UniverseOptions } from '../tests/fixtures/industry/universeGen';

/**
 * 題材 heatmap: every cell is an industry (TW one level, US sector → industry); until the universe carries
 * profile data it falls back to theme cells. Single stocks are never cells.
 */
const themesBody = readFileSync(resolve(import.meta.dirname, '../src/report/fixtures/themes.sample.json'), 'utf8');
const themesTwBody = readFileSync(resolve(import.meta.dirname, '../src/report/fixtures/themes-tw.sample.json'), 'utf8');
const themeCount = (body: string) => (JSON.parse(body) as { themes: unknown[] }).themes.length;

/** Small profile universe: TW 12 industries (≥ 3 stocks each), US 22 industries over 11 sectors. */
const SMALL: UniverseOptions = { twCount: 300, twIndustries: 12, usCount: 700, usIndustries: 22 };
const FALLBACK_TEXT = '全市場產業資料尚未產生，暫以題材分組顯示';

/** A universe file without profile columns (today's online shape) whose turnover covers every theme member. */
function themeUniverseBody() {
  const symbols = new Set<string>();
  for (const body of [themesBody, themesTwBody])
    for (const theme of (JSON.parse(body) as { themes: { constituents: { symbol: string }[] }[] }).themes) for (const c of theme.constituents) symbols.add(c.symbol);
  const rows = (list: string[]) =>
    list.map((symbol, i) => {
      const row: (string | number | null)[] = Array(22).fill(null);
      row[0] = symbol;
      row[1] = symbol;
      row[2] = 100;
      row[21] = 1e7 * 10 ** (i % 4);
      return row;
    });
  const all = [...symbols];
  return {
    version: 1,
    reportDate: '2026-10-13',
    markets: {
      US: { benchmark: 'SPY', asOf: '2026-10-12', universe: 'S&P 500', rows: rows(all.filter((s) => !/\.TWO?$/.test(s))) },
      TW: { benchmark: '^TWII', asOf: '2026-10-13', universe: '台股', rows: rows(all.filter((s) => /\.TWO?$/.test(s))) },
    },
  };
}

type UniverseMode = 'profile' | 'big' | 'noprofile' | 'missing';

async function mockMap(page: Page, universe: UniverseMode = 'profile') {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/themes.json', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: themesBody }));
  await page.route('**/my-stock-screener/report/themes-tw.json', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: themesTwBody }));
  const body =
    universe === 'missing'
      ? null
      : JSON.stringify(
          universe === 'big' ? universeBody() : universe === 'noprofile' ? themeUniverseBody() : universeBody(SMALL),
        );
  await page.route('**/my-stock-screener/report/universe.json', (route) =>
    body === null ? route.fulfill({ status: 404, body: 'not found' }) : route.fulfill({ status: 200, contentType: 'application/json', body }),
  );
}

const horizontalOverflow = (page: Page) => page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth);
const cells = (page: Page) => page.locator('.map-cell');

test('view toggle switches between the list and the industry heatmap and keeps the link shareable', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockMap(page);
  await page.goto('./?page=themes');
  const view = page.getByRole('group', { name: '檢視' });
  await expect(view.getByRole('button', { name: '列表' })).toHaveAttribute('aria-pressed', 'true');
  await expect(cells(page)).toHaveCount(0);
  await view.getByRole('button', { name: '熱力圖' }).click();
  await expect(page).toHaveURL(/page=themes&v=map$/);
  // US: 22 industries inside 11 sectors; no stock cells, no theme tiles, no fallback notice.
  await expect(cells(page)).toHaveCount(22);
  await expect(page.locator('.map-group-head')).toHaveCount(11);
  await expect(page.locator('.theme-tile')).toHaveCount(0);
  await expect(page.getByText(FALLBACK_TEXT)).toHaveCount(0);
  await view.getByRole('button', { name: '列表' }).click();
  await expect(page).toHaveURL(/page=themes$/);
  await expect(page.locator('.theme-tile')).toHaveCount(30);
  // A shared link opens the heatmap directly; unknown values fall back to the list and are rewritten.
  await page.goto('./?page=themes&v=map');
  await expect(cells(page)).toHaveCount(22);
  await page.goto('./?page=themes&v=bogus');
  await expect(page.locator('.theme-tile')).toHaveCount(30);
  await expect(page).toHaveURL(/page=themes$/);
  expect(errors).toEqual([]);
});

test('period, weighting and size controls change the map; the industry map offers 1日 to 3月 only', async ({ page }) => {
  await mockDeviceAccount(page);
  await mockMap(page);
  await page.goto('./?page=themes&market=tw&v=map');
  await expect(cells(page)).toHaveCount(12);
  // Taiwan is one level: no sector headers.
  await expect(page.locator('.map-group-head')).toHaveCount(0);
  const periods = page.getByRole('group', { name: '期間' });
  await expect(periods.getByRole('button')).toHaveText(['1日', '1週', '1月', '3月']);
  await expect(page.locator('.map-legend')).toContainText('1月報酬');
  // The theme parent filter belongs to themes, not industries.
  await expect(page.getByRole('group', { name: '產業大類' })).toHaveCount(0);
  const labelAt = async () => (await cells(page).first().getAttribute('aria-label')) ?? '';
  const before = await labelAt();
  await periods.getByRole('button', { name: '1日' }).click();
  await expect(page.locator('.map-legend')).toContainText('1日報酬');
  expect(await labelAt()).not.toBe(before);
  await expect(cells(page).first()).toHaveAttribute('aria-label', /1日報酬/);

  const agg = page.getByRole('group', { name: '加權方式' });
  await expect(agg.getByRole('button', { name: '市值加權' })).toHaveAttribute('aria-pressed', 'true');
  const weightedLabels = await cells(page).evaluateAll((nodes) => nodes.map((n) => n.getAttribute('aria-label')));
  await agg.getByRole('button', { name: '等權（中位數）' }).click();
  await expect(agg.getByRole('button', { name: '等權（中位數）' })).toHaveAttribute('aria-pressed', 'true');
  const medianLabels = await cells(page).evaluateAll((nodes) => nodes.map((n) => n.getAttribute('aria-label')));
  expect(medianLabels).not.toEqual(weightedLabels);
  await expect(page.locator('.map-foot')).toContainText('等權中位數');

  const sizes = page.getByRole('group', { name: '大小' });
  await expect(sizes.getByRole('button', { name: '均衡' })).toHaveAttribute('aria-pressed', 'true');
  await expect(sizes.getByRole('button')).toHaveText(['均衡', '等大小', '市值']);
  await expect(page.locator('.map-foot')).toContainText('產業市值合計');
  await expect(page.locator('.map-foot')).toContainText('資料日 2026/10/13');
  await expect(page.locator('.map-foot')).toContainText('涵蓋 300 檔');
  await sizes.getByRole('button', { name: '等大小' }).click();
  await expect(page.locator('.map-foot')).toContainText('每個方塊一樣大');
  // Remembered across reloads (agg and size share one stored object).
  await page.reload();
  await expect(sizes.getByRole('button', { name: '等大小' })).toHaveAttribute('aria-pressed', 'true');
  await expect(agg.getByRole('button', { name: '等權（中位數）' })).toHaveAttribute('aria-pressed', 'true');
  await sizes.getByRole('button', { name: '市值' }).click();
  await expect(page.locator('.map-foot')).toContainText('小產業會很小');
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
});

test('a stored period beyond 3 months shows as 3月 on the industry map and comes back on the list', async ({ page }) => {
  await mockDeviceAccount(page);
  await mockMap(page);
  await page.addInitScript(key => localStorage.setItem(key, JSON.stringify({ period: 'ytd', mode: 'return' })), accountPreference('atlas-themes-view-v1'));
  await page.goto('./?page=themes&v=map');
  const periods = page.getByRole('group', { name: '期間' });
  await expect(periods.getByRole('button', { name: '3月' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('group', { name: '檢視' }).getByRole('button', { name: '列表' }).click();
  await expect(page.getByRole('group', { name: '期間' }).getByRole('button', { name: 'YTD' })).toHaveAttribute('aria-pressed', 'true');
});

test('an industry cell opens the constituent panel, and a row opens the stock chart', async ({ page }) => {
  await mockMap(page);
  await page.goto('./?page=themes&market=tw&v=map');
  const cell = cells(page).first();
  await expect(cell).toBeVisible();
  const industry = ((await cell.getAttribute('aria-label')) ?? '').split('，')[0]!;
  await cell.click();
  const dialog = page.getByRole('dialog', { name: new RegExp(industry) });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('市值加權');
  await expect(dialog).toContainText('等權中位數');
  await expect(dialog).toContainText('上漲家數');
  await expect(dialog).toContainText('市值合計');
  const rows = dialog.locator('.ind-row');
  expect(await rows.count()).toBeGreaterThanOrEqual(3);
  expect(await rows.count()).toBeLessThanOrEqual(100);
  // Sorted by market cap, largest first (the sub-line carries the cap).
  await expect(dialog.getByRole('group', { name: '成分股期間' }).getByRole('button')).toHaveText(['1日', '1週', '1月', '3月']);
  // Escape closes the panel and the heatmap stays.
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(page).toHaveURL(/page=themes&market=tw&v=map$/);
  await cell.click();
  const symbol = ((await dialog.locator('.ind-row').first().getAttribute('aria-label')) ?? '').split(' ')[0]!;
  await dialog.locator('.ind-row').first().click();
  await expect(page).toHaveURL(new RegExp(`page=chart&symbol=${symbol.replace('.', '\\.')}`));
});

test('big synthetic markets: every cell is tappable-sized, nothing scrolls sideways, text is not clipped', async ({ page }) => {
  await mockMap(page, 'big');
  for (const [url, expected] of [
    ['./?page=themes&market=tw&v=map', 30],
    ['./?page=themes&v=map', 120],
  ] as const) {
    await page.goto(url);
    await expect(cells(page)).toHaveCount(expected);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    const sides = await cells(page).evaluateAll((nodes) =>
      nodes.map((node) => {
        const box = node.getBoundingClientRect();
        return Math.min(box.width, box.height);
      }),
    );
    // 22px layout minimum, less the 0.5px gap on each side
    expect(Math.min(...sides)).toBeGreaterThanOrEqual(20.9);
    // A visible label must fit inside its cell.
    const clipped = await page.locator('.map-cell .map-code').evaluateAll((nodes) =>
      nodes.filter((n) => n.scrollWidth > n.clientWidth + 1 || (n.parentElement && n.getBoundingClientRect().height > n.parentElement.getBoundingClientRect().height)).length,
    );
    expect(clipped).toBe(0);
  }
});

test('hovering a cell shows both averages, the up share, the market cap and the best and worst stocks', async ({ page, hasTouch }) => {
  test.skip(hasTouch, 'hover is a mouse affordance; touch uses a long press');
  await mockMap(page);
  await page.goto('./?page=themes&market=tw&v=map');
  await cells(page).nth(2).hover();
  const tip = page.getByRole('tooltip');
  await expect(tip).toBeVisible();
  await expect(tip).toContainText('市值加權');
  await expect(tip).toContainText('等權中位數');
  await expect(tip).toContainText('上漲');
  await expect(tip).toContainText('檔');
  await expect(tip).toContainText('市值合計');
  await expect(tip).toContainText('強：');
  await expect(tip).toContainText('弱：');
  await page.mouse.move(0, 0);
  await expect(tip).toHaveCount(0);
});

test('long-pressing a cell on a touch screen shows the tooltip without opening the panel', async ({ page, hasTouch, browserName }) => {
  test.skip(!hasTouch, 'long press is a touch gesture');
  await mockMap(page);
  await page.goto('./?page=themes&v=map');
  const cell = cells(page).nth(5);
  await cell.scrollIntoViewIfNeeded();
  const box = (await cell.boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  if (browserName === 'chromium') {
    const session = await page.context().newCDPSession(page);
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    await expect(page.getByRole('tooltip')).toBeVisible();
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await session.detach();
  } else {
    // Playwright exposes native touch hold through CDP only on Chromium. Exercise
    // WebKit's DOM pointer sequence, including the release click suppression.
    const pointer = { pointerType: 'touch', pointerId: 1, isPrimary: true, clientX: x, clientY: y, buttons: 1 };
    await cell.dispatchEvent('pointerdown', pointer);
    await expect(page.getByRole('tooltip')).toBeVisible();
    await cell.dispatchEvent('pointerup', { ...pointer, buttons: 0 });
    await cell.dispatchEvent('click');
  }
  await expect(page).toHaveURL(/page=themes&v=map$/);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('tooltip')).toContainText('市值合計');
});

test('Taiwan and US keep the heatmap view across the market tabs', async ({ page }) => {
  await mockMap(page);
  await page.goto('./?page=themes&market=tw&v=map');
  await expect(cells(page)).toHaveCount(12);
  await expect(page.locator('.map-foot')).toContainText('新台幣');
  const tabs = page.getByRole('tablist', { name: '市場' });
  await tabs.getByRole('tab', { name: /美股/ }).click();
  await expect(page).toHaveURL(/page=themes&v=map$/);
  await expect(cells(page)).toHaveCount(22);
  await expect(page.locator('.map-foot')).toContainText('美元');
  await tabs.getByRole('tab', { name: /台股/ }).click();
  await expect(page).toHaveURL(/page=themes&market=tw&v=map$/);
  await expect(cells(page)).toHaveCount(12);
});

test('without profile data the map falls back to theme cells, says so, and never shows stocks', async ({ page }) => {
  await mockMap(page, 'noprofile');
  await page.goto('./?page=themes&v=map');
  await expect(page.getByRole('status').filter({ hasText: FALLBACK_TEXT })).toBeVisible();
  await expect(cells(page)).toHaveCount(themeCount(themesBody));
  await expect(page.locator('.map-group-head')).not.toHaveCount(0);
  // Theme periods (the full six) and the parent filter return.
  await expect(page.getByRole('group', { name: '期間' }).getByRole('button')).toHaveText(['1日', '1週', '1月', '3月', '6月', 'YTD']);
  await expect(page.getByRole('group', { name: '產業大類' })).toBeVisible();
  await expect(page.getByRole('group', { name: '加權方式' })).toHaveCount(0);
  // The universe list still sizes the cells by turnover.
  const sizes = page.getByRole('group', { name: '大小' });
  await expect(sizes.getByRole('button', { name: '成交值' })).toBeEnabled();
  await expect(page.locator('.map-foot')).toContainText('不是市值');
  // A click opens the existing theme detail, and closing returns to the heatmap.
  await cells(page).filter({ hasText: '光通訊' }).first().click();
  await expect(page).toHaveURL(/page=themes&v=map&theme=optical/);
  await expect(page.getByRole('dialog', { name: /光通訊/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/page=themes&v=map$/);
  await page.getByRole('group', { name: '產業大類' }).getByRole('button', { name: /半導體/ }).click();
  expect(await cells(page).count()).toBeLessThan(themeCount(themesBody));
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
});

test('when the universe is missing the theme map still works with equal-size cells', async ({ page }) => {
  await mockMap(page, 'missing');
  await page.goto('./?page=themes&market=tw&v=map');
  await expect(page.getByRole('status').filter({ hasText: FALLBACK_TEXT })).toBeVisible();
  await expect(cells(page)).toHaveCount(themeCount(themesTwBody));
  const sizes = page.getByRole('group', { name: '大小' });
  await expect(sizes.getByRole('button', { name: '等大小' })).toHaveAttribute('aria-pressed', 'true');
  await expect(sizes.getByRole('button', { name: '均衡' })).toBeDisabled();
  await expect(sizes.getByRole('button', { name: '成交值' })).toBeDisabled();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
});

test('an old stored 成交值 preference in an authorized account still selects the raw size mode', async ({ page }) => {
  await mockDeviceAccount(page);
  await mockMap(page);
  await page.addInitScript(key => localStorage.setItem(key, JSON.stringify({ weight: 'turnover' })), accountPreference('atlas-themes-map-v1-screener'));
  await page.goto('./?page=themes&market=tw&v=map');
  await expect(page.getByRole('group', { name: '大小' }).getByRole('button', { name: '市值' })).toHaveAttribute('aria-pressed', 'true');
});

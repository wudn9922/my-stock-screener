import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { mockDeviceAccount, accountDatabase } from './account-fixture';
import { researchFixtureBody } from './research-fixtures';
import { openPanel, closePanel } from '../e2e/site-helpers';
import { chooseDrawingTool } from '../e2e/drawing-picker-helper';
import { universeBody } from '../tests/fixtures/industry/universeGen';

async function setup(page: Page) {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/themes*.json', route => {
    const name = new URL(route.request().url()).pathname.endsWith('themes-tw.json') ? 'themes-tw' : 'themes';
    return route.fulfill({ contentType: 'application/json', body: readFileSync(new URL(`../src/report/fixtures/${name}.sample.json`, import.meta.url)) });
  });
  await page.route('**/my-stock-screener/report/research/**', route => {
    try { return route.fulfill({ contentType: 'application/json', body: researchFixtureBody(new URL(route.request().url()).pathname.split('/research/')[1]!) }); }
    catch { return route.fulfill({ status: 404 }); }
  });
  await page.route('**/my-stock-screener/report/financials/*.json', route => {
    const key = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1)!);
    try { return route.fulfill({ contentType: 'application/json', body: readFileSync(new URL(`../../docs/report/financials/${key}`, import.meta.url)) }); }
    catch { return route.fulfill({ status: 404 }); }
  });
}
const noOverflow = (page: Page) => page.evaluate(() => Math.max(document.documentElement.scrollWidth - innerWidth, document.getElementById('main')!.scrollWidth - document.getElementById('main')!.clientWidth));
async function savedDrawings(page: Page, symbol: string) {
  return page.evaluate(({ database, symbol }) => new Promise<number>((resolve, reject) => {
    const request = indexedDB.open(database);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const db = request.result;
      const row = db.transaction('symbols', 'readonly').objectStore('symbols').get(symbol);
      row.onsuccess = () => { resolve(row.result?.drawings.length ?? 0); db.close(); };
      row.onerror = () => { reject(row.error); db.close(); };
    };
  }), { database: accountDatabase(), symbol });
}

test('FX and commodities start as lists, offer a continuous gallery and open the complete chart workbench', async ({ page }, info) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await mockDeviceAccount(page); await setup(page);
  await page.goto('./?page=world&view=commodities');
  await expect(page.locator('.asset-list-row')).toHaveCount(7);
  await expect(page.locator('[data-testid="research-candle-host"]')).toHaveCount(0);
  await page.getByRole('button', { name: /連續看全部圖表/ }).click();
  const wti = page.locator('.world-candle-card').first();
  await expect(wti.locator('.candle-ohlc')).toContainText('O ');
  const oldIdentity = await wti.locator('.drawable-candles').getAttribute('data-drawing-symbol');
  await wti.getByRole('button', { name: /開啟完整圖表/ }).click();
  await expect(page.getByTestId('active-symbol')).toHaveText('WTI');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  expect(new URL(page.url()).searchParams.get('symbol')).toBe(oldIdentity);
  await expect(page.getByTestId('header-quote')).toContainText('USD／桶');
  await openPanel(page, 'indicators');
  await page.getByRole('spinbutton', { name: 'SMA 週期', exact: true }).fill('30');
  await page.getByRole('button', { name: '新增 SMA', exact: true }).click();
  await closePanel(page);
  await expect(page.locator('.indicator-chip')).toContainText('SMA 30');
  await chooseDrawingTool(page, '水平線');
  const box = (await page.getByTestId('chart').boundingBox())!;
  await page.mouse.move(box.x + box.width * .5, box.y + box.height * .5);
  await page.mouse.down(); await page.mouse.move(box.x + box.width * .51, box.y + box.height * .51); await page.mouse.up();
  await expect(page.getByRole('toolbar', { name: '所選畫線操作' })).toBeVisible();
  await expect.poll(() => savedDrawings(page, oldIdentity!)).toBe(1);
  await page.reload();
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await expect(page.locator('.indicator-chip')).toContainText('SMA 30');
  await page.goto('./?page=world&view=commodities');
  await page.getByRole('button', { name: /連續看全部圖表/ }).click();
  await expect(wti.locator('.candle-ohlc')).toContainText('O ');
  await expect.poll(() => savedDrawings(page, oldIdentity!)).toBe(1);
  await page.getByRole('button', { name: '匯率', exact: true }).click();
  await expect(page.locator('.asset-list-row')).toHaveCount(5);
  await page.locator('.asset-list-row').first().click();
  await expect(page.getByTestId('active-symbol')).toHaveText('USD/TWD');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await expect(page.locator('.indicator-chip')).toHaveCount(0);
  await expect(page.locator('[data-legacy-volume]')).toHaveCount(0);
  await expect.poll(() => savedDrawings(page, new URL(page.url()).searchParams.get('symbol')!)).toBe(0);
  await expect(page.locator('.chart-pe-history')).toHaveCount(0);
  expect(await noOverflow(page)).toBeLessThanOrEqual(1);
  expect(errors).toEqual([]);
  await page.screenshot({ path: info.outputPath('fx-workbench.png') });
});

test('permission display is immediately before data health at the foot of every page, and stock list uses most of the viewport', async ({ page }) => {
  await setup(page);
  for (const route of ['markets', 'world&view=fx', 'themes', 'screener', 'watchlist', 'console', 'chart&symbol=AAPL']) {
    await page.goto(`./?page=${route}`);
    const footer = page.locator('.site-data-footer');
    await expect(footer.getByRole('complementary', { name: '目前儲存權限' })).toHaveCount(1);
    expect(await footer.evaluate(element => element.firstElementChild?.getAttribute('aria-label'))).toBe('目前儲存權限');
    expect(await footer.evaluate(element => element.lastElementChild?.classList.contains('data-health'))).toBe(true);
    expect(await footer.evaluate(element => element === document.getElementById('main')!.lastElementChild)).toBe(true);
    expect(await noOverflow(page)).toBeLessThanOrEqual(1);
  }
  await page.goto('./?page=screener&group=tw_all');
  const list = page.locator('.screener-list');
  await expect(list).toBeVisible();
  expect((await list.boundingBox())!.height).toBeGreaterThan(page.viewportSize()!.height * .75);
});

test('Chinese heatmap preserves original identities and makes narrow cell names readable', async ({ page }) => {
  await setup(page);
  await page.route('**/my-stock-screener/report/universe.json', route => route.fulfill({ json: universeBody({ usCount: 700, usIndustries: 17 }) }));
  await page.goto('./?page=themes&market=us&v=map');
  await expect(page.locator('.map-cell')).toHaveCount(17);
  const labels = await page.locator('.map-code').allTextContents();
  expect(labels.filter(text => /[\u3400-\u9fff]/.test(text)).length).toBeGreaterThanOrEqual(15);
  await expect(page.locator('.map-cell').filter({ hasText: '半導體' }).first()).toHaveAttribute('aria-label', /Semiconductors/);
  expect(await noOverflow(page)).toBeLessThanOrEqual(1);
});

test('historical P/E follows the full-height K-line with an actual zoomable time series and a clear missing-data state', async ({ page }, info) => {
  await setup(page);
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  const shell = (await page.locator('.app-shell').boundingBox())!;
  const main = (await page.locator('#main').boundingBox())!;
  expect(shell.height).toBeGreaterThanOrEqual(main.height - 2);
  const history = page.getByRole('region', { name: 'AAPL 本益比走勢' });
  await page.getByTestId('valuation-help-toggle').click();
  await page.getByRole('button', { name: '查看下方本益比圖表' }).click();
  await expect(page.getByRole('region', { name: '本益比說明', exact: true })).toBeHidden();
  await expect(history.getByRole('img', { name: /歷史本益比折線圖/ })).toBeVisible();
  await expect(history.locator('canvas').first()).toBeVisible();
  await history.screenshot({ path: info.outputPath('pe-history.png') });
  await page.goto('./?page=chart&symbol=ZZZZ&tf=1D');
  const missing = page.getByRole('region', { name: 'ZZZZ 本益比走勢' });
  await missing.scrollIntoViewIfNeeded();
  await expect(missing).toContainText('尚未');
  await expect(missing.getByRole('img')).toHaveCount(0);
  expect(await noOverflow(page)).toBeLessThanOrEqual(1);
});

test('a currently loss-making stock keeps historical P/E and explicitly says current P/E is inapplicable', async ({ page }) => {
  await setup(page);
  const history = JSON.parse(readFileSync(new URL('../../docs/report/financials/AAPL.json', import.meta.url), 'utf8'));
  for (const [key, count] of [['annual', 2], ['quarterly', 8]] as const) {
    for (const field of ['epsDiluted', 'epsBasic']) {
      const values = history[key].values[field] as (number | null)[];
      history[key].values[field] = values.map((value, index) => index >= values.length - count ? -10 : value);
    }
  }
  await page.route('**/my-stock-screener/report/financials/AAPL.json', route => route.fulfill({ json: history }));
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  const section = page.getByRole('region', { name: 'AAPL 本益比走勢' });
  await section.scrollIntoViewIfNeeded();
  await expect(section.getByRole('img', { name: /歷史本益比折線圖/ })).toBeVisible();
  await expect(section.locator('.chart-pe-heading')).toContainText('虧損');
  await expect(section.locator('.chart-pe-heading')).toContainText('目前本益比不適用');
});

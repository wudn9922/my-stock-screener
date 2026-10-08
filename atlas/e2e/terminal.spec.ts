import { test, expect, type Page } from '@playwright/test';
import { chooseDrawingTool, clickDrawingUtility } from './drawing-picker-helper';
import type { SymbolState } from '../src/storage/schema';
async function readSymbol(page: Page, symbol = 'AAPL'): Promise<SymbolState | undefined> {
  return page.evaluate(async (s) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const r = indexedDB.open('atlas-terminal');
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
    const data = await new Promise<SymbolState | undefined>((resolve, reject) => {
      const r = db.transaction('symbols').objectStore('symbols').get(s);
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
    db.close();
    return data;
  }, symbol);
}
async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}
async function drag(page: Page, x: number, y: number, toX: number, toY: number) {
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(toX, toY, { steps: 12 });
  await page.mouse.up();
}
async function addMA(page: Page, period: number) {
  if (
    (await page.locator('.mobile-nav').isVisible()) &&
    !(await page.getByRole('dialog', { name: 'indicators panel', exact: true }).isVisible())
  )
    await page.locator('.mobile-nav').getByRole('button', { name: 'SMA', exact: true }).click();
  await page.getByLabel('SMA period', { exact: true }).fill(String(period));
  await page.getByRole('button', { name: 'Add SMA', exact: true }).click();
}
async function closeSheet(page: Page) {
  const close = page.getByRole('button', { name: 'Close panel', exact: true });
  if (await close.isVisible()) await close.click();
}
async function switchTo(page: Page, symbol: string) {
  await page.getByLabel('Symbol search', { exact: true }).fill(symbol);
  await page.getByLabel('Symbol search', { exact: true }).press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText(symbol);
  await loaded(page);
}
test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await loaded(page);
});
test('per-symbol multiple SMA locks and drawing persistence survive symbol switches and reload', async ({
  page,
}) => {
  await addMA(page, 24);
  await page.getByRole('button', { name: 'Lock SMA 24', exact: true }).click();
  await addMA(page, 58);
  await closeSheet(page);
  await chooseDrawingTool(page, 'Trend Line');
  const r = (await page.getByTestId('chart').boundingBox())!;
  const x = r.x + Math.min(130, r.width * 0.3),
    y = r.y + r.height * 0.45;
  await drag(page, x, y, x + 12, y + 8);
  await expect.poll(async () => (await readSymbol(page))?.drawings.length ?? 0).toBe(0);
  await drag(page, r.x + r.width * 0.8, y + 60, r.x + r.width * 0.82, y + 55);
  await expect.poll(async () => (await readSymbol(page))?.drawings.length).toBe(1);
  await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();
  await expect.poll(async () => (await readSymbol(page))?.drawings[0].locked).toBe(true);
  const aapl = await readSymbol(page);
  expect(aapl!.drawings[0].points[1].logical).toBeGreaterThan(2499);
  await switchTo(page, 'NVDA');
  await addMA(page, 43);
  await addMA(page, 56);
  await closeSheet(page);
  expect((await readSymbol(page, 'NVDA'))!.drawings).toEqual([]);
  await expect(page.locator('.chart-status')).toContainText('0 DRAWINGS');
  await switchTo(page, 'AAPL');
  await expect(page.locator('.chart-status')).toContainText('1 DRAWINGS');
  expect((await readSymbol(page))!.indicators.map((i) => i.period)).toEqual([24, 58]);
  await page.reload();
  await loaded(page);
  await expect(page.locator('.indicator-chip')).toHaveCount(2);
  expect((await readSymbol(page))!.indicators.find((i) => i.period === 24)!.locked).toBe(true);
  expect((await readSymbol(page))!.drawings[0]).toEqual(aapl!.drawings[0]);
  await switchTo(page, 'NVDA');
  expect((await readSymbol(page, 'NVDA'))!.indicators.map((i) => i.period)).toEqual([43, 56]);
});
test('press-drag-release, endpoint and body edits, undo/redo, locked pan and timeframe mapping', async ({
  page,
}) => {
  const r = (await page.getByTestId('chart').boundingBox())!,
    a = { x: r.x + r.width * 0.35, y: r.y + r.height * 0.5 },
    b = { x: r.x + r.width * 0.65, y: r.y + r.height * 0.63 };
  await chooseDrawingTool(page, 'Trend Line');
  await drag(page, a.x - 20, a.y - 20, a.x, a.y);
  await drag(page, b.x - 20, b.y - 20, b.x, b.y);
  await expect.poll(async () => (await readSymbol(page))?.drawings.length).toBe(1);
  const initial = (await readSymbol(page))!.drawings[0];
  await drag(page, a.x, a.y, a.x + 20, a.y + 20);
  await expect
    .poll(async () => (await readSymbol(page))?.drawings[0].points[0].price)
    .not.toBe(initial.points[0].price);
  const edited = (await readSymbol(page))!.drawings[0];
  await drag(
    page,
    (a.x + 20 + b.x) / 2,
    (a.y + 20 + b.y) / 2,
    (a.x + 20 + b.x) / 2 + 15,
    (a.y + 20 + b.y) / 2 + 25,
  );
  await expect
    .poll(async () => (await readSymbol(page))?.drawings[0].points[1].price)
    .not.toBe(edited.points[1].price);
  await clickDrawingUtility(page, 'Undo drawing');
  await expect
    .poll(async () => (await readSymbol(page))?.drawings[0].points)
    .toEqual(edited.points);
  await clickDrawingUtility(page, 'Redo drawing');
  await expect
    .poll(async () => (await readSymbol(page))?.drawings[0].points[1].price)
    .not.toBe(edited.points[1].price);
  await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();
  const locked = (await readSymbol(page))!.drawings[0];
  await drag(
    page,
    (a.x + 20 + b.x) / 2 + 15,
    (a.y + 20 + b.y) / 2 + 25,
    (a.x + 20 + b.x) / 2 + 55,
    (a.y + 20 + b.y) / 2 + 25,
  );
  await expect.poll(async () => (await readSymbol(page))?.preferences.views['1D']).toBeDefined();
  expect((await readSymbol(page))!.drawings[0].points).toEqual(locked.points);
  for (const tf of ['5m', '1H', '1D']) {
    await page.getByRole('button', { name: `Timeframe ${tf}`, exact: true }).click();
    await loaded(page);
    expect((await readSymbol(page))!.drawings[0].points).toEqual(locked.points);
    await expect(page.locator('.chart-status')).toContainText(
      tf === '1D' ? '1 DRAWINGS' : '0 DRAWINGS',
    );
  }
});
test('Horizontal Line, export/import and locked SMA UI guards', async ({ page }) => {
  await addMA(page, 24);
  await page.getByRole('button', { name: 'Lock SMA 24', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Settings SMA 24', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Remove SMA 24', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Hide SMA 24', exact: true }).click();
  await closeSheet(page);
  const r = (await page.getByTestId('chart').boundingBox())!;
  await chooseDrawingTool(page, 'Horizontal Line');
  await drag(
    page,
    r.x + r.width * 0.7,
    r.y + r.height * 0.5,
    r.x + r.width * 0.8,
    r.y + r.height * 0.6,
  );
  await expect.poll(async () => (await readSymbol(page))?.drawings[0].type).toBe('horizontal');
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export settings', exact: true }).click();
  const download = await pending;
  expect(download.suggestedFilename()).toMatch(/atlas-settings.*json/);
  const path = await download.path();
  expect(path).not.toBeNull();
  await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();
  await page.getByLabel('Settings file', { exact: true }).setInputFiles(path!);
  await expect(page.getByRole('status')).toContainText('設定已還原');
  await expect.poll(async () => (await readSymbol(page))?.drawings[0].locked).toBe(false);
});
test('watchlist add, reorder and remove persist', async ({ page }) => {
  const mobile = await page.locator('.mobile-nav').isVisible();
  if (mobile)
    await page
      .locator('.mobile-nav')
      .getByRole('button', { name: 'Watchlist', exact: true })
      .click();
  await page.getByRole('button', { name: 'Add watchlist symbol', exact: true }).click();
  await page.getByLabel('Watchlist ticker', { exact: true }).fill('NVO');
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Select NVO', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Manage watchlist', exact: true }).click();
  await page.getByRole('button', { name: 'Move NVO up', exact: true }).click();
  await page.getByRole('button', { name: 'Remove AMD from watchlist', exact: true }).click();
  await closeSheet(page);
  await page.reload();
  await loaded(page);
  if (mobile)
    await page
      .locator('.mobile-nav')
      .getByRole('button', { name: 'Watchlist', exact: true })
      .click();
  await expect(page.getByRole('button', { name: 'Select NVO', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Select AMD', exact: true })).toHaveCount(0);
});
test('mobile touch placement loupe, pointer cancel and no page scroll', async ({
  page,
  browserName,
  isMobile,
}) => {
  test.skip(!isMobile, 'Touch regression on mobile devices');
  const r = (await page.getByTestId('chart').boundingBox())!,
    x = r.x + r.width * 0.35,
    y = r.y + r.height * 0.5;
  await chooseDrawingTool(page, 'Trend Line');
  if (browserName === 'chromium') {
    const cdp = await page.context().newCDPSession(page);
    const touch = async (
      type: 'touchStart' | 'touchMove' | 'touchEnd' | 'touchCancel',
      tx = x,
      ty = y,
    ) =>
      cdp.send('Input.dispatchTouchEvent', {
        type,
        touchPoints: type === 'touchEnd' || type === 'touchCancel' ? [] : [{ x: tx, y: ty, id: 1 }],
      });
    await touch('touchStart');
    await touch('touchMove', x + 10, y + 10);
    await expect(page.getByTestId('loupe')).toBeVisible();
    await expect(page.getByTestId('loupe')).toHaveCSS('right', '12px');
    await touch('touchEnd');
    await expect(page.getByTestId('loupe')).toBeHidden();
    await touch('touchStart', r.x + r.width * 0.75, y + 50);
    await expect(page.getByTestId('loupe')).toBeVisible();
    await expect(page.getByTestId('loupe')).toHaveCSS('left', '12px');
    await touch('touchEnd');
    await expect.poll(async () => (await readSymbol(page))?.drawings.length).toBe(1);
    // Endpoint editing must also show the loupe. Release first endpoint at the exact updated coordinate.
    await touch('touchStart', x + 10, y + 10);
    await touch('touchMove', x + 15, y + 15);
    await expect(page.getByTestId('loupe')).toBeVisible();
    await touch('touchCancel');
    await expect(page.getByTestId('loupe')).toBeHidden();
    const drawingBeforePinch = (await readSymbol(page))!.drawings;
    await clickDrawingUtility(page, 'Zoom out');
    await clickDrawingUtility(page, 'Zoom in');
    await expect.poll(async () => (await readSymbol(page))?.preferences.views['1D']).toBeDefined();
    const rangeBeforePinch = (await readSymbol(page))!.preferences.views['1D']!;
    await chooseDrawingTool(page, 'Trend Line');
    await touch('touchStart', x - 25, y);
    await expect(page.getByTestId('loupe')).toBeVisible();
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x: x - 25, y, id: 1 }, { x: x + 25, y, id: 2 }],
    });
    await expect(page.getByTestId('loupe')).toBeHidden();
    for (let step = 1; step <= 8; step++)
      await cdp.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{ x: x - 25 - step * 4, y, id: 1 }, { x: x + 25 + step * 4, y, id: 2 }],
      });
    await touch('touchEnd');
    await expect.poll(async () => {
      const range = (await readSymbol(page))?.preferences.views['1D'];
      return range && range.to - range.from;
    }).toBeLessThan(rangeBeforePinch.to - rangeBeforePinch.from);
    expect((await readSymbol(page))!.drawings).toEqual(drawingBeforePinch);

  } else {
    // WebKit native mouse pointer/capture with touch modality, to cover engine ownership/loupe.
    await page.evaluate(() => {
      for (const type of ['pointerdown', 'pointermove', 'pointerup'])
        document.addEventListener(
          type,
          (e) => Object.defineProperty(e, 'pointerType', { value: 'touch' }),
          { capture: true },
        );
    });
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 10, y + 10);
    await expect(page.getByTestId('loupe')).toBeVisible();
    await page.mouse.up();
    await drag(page, r.x + r.width * 0.75, y + 50, r.x + r.width * 0.75 + 10, y + 60);
    await expect.poll(async () => (await readSymbol(page))?.drawings.length).toBe(1);
  }
  expect(await page.evaluate(() => scrollY)).toBe(0);
  expect(await page.getByTestId('chart').evaluate((e) => getComputedStyle(e).touchAction)).toBe(
    'none',
  );
});
test('layout and rendered chart have no runtime errors', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.mouse.move(500, 350);
  await page.waitForTimeout(200);
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `scratch/${info.project.name}.png` });
});

test('prototype provider failure is explicit and switching back to Demo restores chart', async ({
  page,
}) => {
  await page.route('**/api/yahoo?**', (route) =>
    route.fulfill({
      status: 502,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'Prototype provider unavailable' }),
    }),
  );
  await page.getByLabel('Market data source', { exact: true }).selectOption('yahoo');
  await expect(page.locator('.chart-loading.error')).toContainText('資料來源暫時無法使用');
  await page.getByRole('button', { name: '使用離線 Demo', exact: true }).click();
  await loaded(page);
  await expect(page.getByLabel('Market data source', { exact: true })).toHaveValue('demo');
});

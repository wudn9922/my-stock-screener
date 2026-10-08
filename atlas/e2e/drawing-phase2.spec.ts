import { test, expect, type Page } from '@playwright/test';
import { chooseDrawingTool, clickDrawingUtility } from './drawing-picker-helper';
import type { SymbolState } from '../src/storage/schema';
async function read(page: Page, symbol = 'AAPL'): Promise<SymbolState> {
  return page.evaluate(async (symbol) => {
    const db = await new Promise<IDBDatabase>((resolve) => {
      const r = indexedDB.open('atlas-terminal');
      r.onsuccess = () => resolve(r.result);
    });
    const state = await new Promise<SymbolState>((resolve) => {
      const r = db.transaction('symbols').objectStore('symbols').get(symbol);
      r.onsuccess = () => resolve(r.result);
    });
    db.close();
    return state;
  }, symbol);
}
async function ready(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}
for (const [name, kind] of [
  ['Horizontal Ray', 'ray'],
  ['Rectangle', 'rectangle'],
  ['Fibonacci Retracement', 'fibonacci'],
] as const) {
  test(`${name}: gestures, edits, history, locked pan, future, isolation, reload and export/import`, async ({
    page,
    isMobile,
    browserName,
  }, info) => {
    await page.goto('/');
    await ready(page);
    const box = (await page.getByTestId('chart').boundingBox())!;
    const a = { x: box.x + box.width * 0.22, y: box.y + box.height * 0.48 };
    const b = { x: box.x + box.width * 0.8, y: a.y + (kind !== 'ray' ? 90 : 0) };
    const cdp =
      isMobile && browserName === 'chromium' ? await page.context().newCDPSession(page) : null;
    if (isMobile && !cdp)
      await page.evaluate(() => {
        for (const type of ['pointerdown', 'pointermove', 'pointerup'])
          document.addEventListener(
            type,
            (e) => Object.defineProperty(e, 'pointerType', { value: 'touch' }),
            { capture: true },
          );
      });
    const drag = async (x: number, y: number, dx: number, dy: number, loupe = false, hold = false) => {
      if (cdp) {
        await cdp.send('Input.dispatchTouchEvent', {
          type: 'touchStart',
          touchPoints: [{ x, y, id: 1 }],
        });
        // Native chart pan begins on the first move and scrolls on later moves.
        for (let step = 1; step <= 8; step++) {
          await cdp.send('Input.dispatchTouchEvent', {
            type: 'touchMove',
            touchPoints: [{ x: x + (dx - x) * step / 8, y: y + (dy - y) * step / 8, id: 1 }],
          });
        }
        if (loupe) await expect(page.getByTestId('loupe')).toBeVisible();
        if (hold) await page.waitForTimeout(300);
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      } else {
        await page.mouse.move(x, y);
        await page.mouse.down();
        await page.mouse.move(dx, dy, { steps: 8 });
        if (loupe) await expect(page.getByTestId('loupe')).toBeVisible();
        if (hold) await page.waitForTimeout(300);
        await page.mouse.up();
      }
    };
    await chooseDrawingTool(page, name);
    // Precision placement can outlast the chart's native long-press threshold.
    await drag(a.x - 6, a.y - 8, a.x, a.y, isMobile, isMobile);
    await drag(b.x - 6, b.y - 8, b.x, b.y, isMobile);
    await expect.poll(async () => (await read(page))?.drawings[0]?.type).toBe(kind);
    const initial = (await read(page)).drawings[0];
    await page.screenshot({ path: `scratch/phase2-${kind}-${info.project.name}.png` });
    expect(initial.points[1].logical).toBeGreaterThan(2499);
    await drag(a.x, a.y, a.x + 6, a.y + 16, isMobile);
    await expect
      .poll(async () => (await read(page)).drawings[0].points[0].price)
      .not.toBe(initial.points[0].price);
    await drag(
      b.x,
      kind === 'ray' ? a.y + 16 : b.y,
      b.x - 6,
      kind === 'ray' ? a.y + 26 : b.y + 10,
      isMobile,
    );
    await expect
      .poll(async () => (await read(page)).drawings[0].points[1].time)
      .not.toBe(initial.points[1].time);
    const edited = (await read(page)).drawings[0];
    const mid = (a.x + b.x) / 2;
    const bodyY = kind === 'ray' ? a.y + 26 : (a.y + 16 + b.y + 10) / 2;
    await drag(mid, bodyY, mid + 8, bodyY + 12);
    await expect
      .poll(async () => (await read(page)).drawings[0].points[0].price)
      .not.toBe(edited.points[0].price);
    await clickDrawingUtility(page, 'Undo drawing');
    await expect.poll(async () => (await read(page)).drawings[0].points).toEqual(edited.points);
    await clickDrawingUtility(page, 'Redo drawing');
    await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();
    const locked = (await read(page)).drawings[0];
    await clickDrawingUtility(page, 'Zoom out');
    await clickDrawingUtility(page, 'Zoom in');
    await expect.poll(async () => (await read(page)).preferences.views['1D']).toBeDefined();
    const range = (await read(page)).preferences.views['1D'];
    await drag(mid + 8, bodyY + 12, mid + 45, bodyY + 12);
    await expect.poll(async () => (await read(page)).preferences.views['1D']).not.toEqual(range);
    expect((await read(page)).drawings[0]).toEqual(locked);
    for (const tf of ['5m', '1H', '1D']) {
      await page.getByRole('button', { name: `Timeframe ${tf}`, exact: true }).click();
      await ready(page);
      expect((await read(page)).drawings[0]).toEqual(locked);
      await expect(page.locator('.chart-status')).toContainText(
        tf === '1D' ? '1 DRAWINGS' : '0 DRAWINGS',
      );
    }
    for (const symbol of ['NVDA', 'AAPL']) {
      await page.getByLabel('Symbol search', { exact: true }).fill(symbol);
      await page.getByLabel('Symbol search', { exact: true }).press('Enter');
      await ready(page);
      await expect(page.getByTestId('active-symbol')).toHaveText(symbol);
      await expect(page.locator('.chart-status')).toContainText(
        `${symbol === 'NVDA' ? 0 : 1} DRAWINGS`,
      );
    }
    await page.reload();
    await ready(page);
    expect((await read(page)).drawings[0]).toEqual(locked);
    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export settings', exact: true }).click();
    const backup = (await (await downloadEvent).path())!;
    await page.getByLabel('Settings file', { exact: true }).setInputFiles(backup);
    await expect(page.getByRole('status')).toContainText('設定已還原');
    await ready(page);
    expect((await read(page)).drawings[0]).toEqual(locked);
    // Select through the object list after viewport changes/reload.
    if (await page.locator('.mobile-nav').isVisible())
      await page
        .locator('.mobile-nav')
        .getByRole('button', { name: 'Drawings', exact: true })
        .click();
    else await page.getByRole('button', { name: 'Drawings', exact: true }).click();
    await page.getByRole('button', { name: 'Unlock drawing 1', exact: true }).click();
    await page.getByRole('button', { name: 'Delete drawing 1', exact: true }).click();
    await expect.poll(async () => (await read(page)).drawings.length).toBe(0);
    const close = page.getByRole('button', { name: 'Close panel', exact: true });
    if (await close.isVisible()) await close.click();
    await clickDrawingUtility(page, 'Undo drawing');
    await expect.poll(async () => (await read(page)).drawings.length).toBe(1);
    await clickDrawingUtility(page, 'Redo drawing');
    await expect.poll(async () => (await read(page)).drawings.length).toBe(0);
    expect(await page.evaluate(() => scrollY)).toBe(0);
  });
}

test('Rectangle four corners retain component ownership on desktop and touch', async ({
  page,
  isMobile,
  browserName,
}) => {
  await page.goto('/');
  await ready(page);
  const r = (await page.getByTestId('chart').boundingBox())!;
  const a = { x: r.x + r.width * 0.26, y: r.y + r.height * 0.4 },
    b = { x: r.x + r.width * 0.7, y: a.y + 95 };
  const cdp =
    isMobile && browserName === 'chromium' ? await page.context().newCDPSession(page) : null;
  if (isMobile && !cdp)
    await page.evaluate(() => {
      for (const t of ['pointerdown', 'pointermove', 'pointerup'])
        document.addEventListener(
          t,
          (e) => Object.defineProperty(e, 'pointerType', { value: 'touch' }),
          { capture: true },
        );
    });
  const drag = async (x: number, y: number, tx: number, ty: number) => {
    if (cdp) {
      await cdp.send('Input.dispatchTouchEvent', {
        type: 'touchStart',
        touchPoints: [{ x, y, id: 1 }],
      });
      await cdp.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{ x: tx, y: ty, id: 1 }],
      });
      await expect(page.getByTestId('loupe')).toBeVisible();
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    } else {
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(tx, ty, { steps: 8 });
      if (isMobile) await expect(page.getByTestId('loupe')).toBeVisible();
      await page.mouse.up();
    }
  };
  await chooseDrawingTool(page, 'Rectangle');
  await drag(a.x, a.y, a.x, a.y);
  await drag(b.x, b.y, b.x, b.y);
  await expect.poll(async () => (await read(page))?.drawings.length).toBe(1);
  const initial = (await read(page)).drawings[0];
  for (const [index, x, y] of [
    [0, a.x, a.y],
    [1, b.x, b.y],
    [2, a.x, b.y],
    [3, b.x, a.y],
  ]) {
    await drag(x, y, x + 14, y + 16);
    const xIndex = index < 2 ? index : index === 2 ? 0 : 1,
      yIndex = index < 2 ? index : 1 - xIndex;
    await expect
      .poll(async () => (await read(page)).drawings[0].points[xIndex].time)
      .not.toBe(initial.points[xIndex].time);
    await expect
      .poll(async () => (await read(page)).drawings[0].points[yIndex].price)
      .not.toBe(initial.points[yIndex].price);
    const changed = (await read(page)).drawings[0];
    expect(changed.points[1 - xIndex].time).toBe(initial.points[1 - xIndex].time);
    expect(changed.points[1 - yIndex].price).toBe(initial.points[1 - yIndex].price);
    await clickDrawingUtility(page, 'Undo drawing');
    await expect.poll(async () => (await read(page)).drawings[0].points).toEqual(initial.points);
  }
  expect(await page.evaluate(() => scrollY)).toBe(0);
});

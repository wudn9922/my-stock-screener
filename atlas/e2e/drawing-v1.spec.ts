import { expect, test, type Page } from '@playwright/test';
import { chooseDrawingTool, clickDrawingUtility, type DrawingToolName } from './drawing-picker-helper';
import { readFile } from 'node:fs/promises';
import type { SymbolState } from '../src/storage/schema';

async function readState(page: Page, symbol = 'AAPL'): Promise<SymbolState | undefined> {
  return page.evaluate(async (ticker) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('atlas-terminal');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const state = await new Promise<SymbolState | undefined>((resolve, reject) => {
      const request = db.transaction('symbols').objectStore('symbols').get(ticker);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    db.close();
    return state;
  }, symbol);
}
async function readDrawings(page: Page, symbol = 'AAPL'): Promise<SymbolState['drawings']> {
  return (await readState(page, symbol))?.drawings ?? [];
}
async function ready(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}
function canonicalDrawingSnapshot(drawings: SymbolState['drawings']) {
  return drawings.map(({ id, type, locked, points }) => ({
    id,
    type,
    locked,
    points: points.map(({ time, price }) => ({ time, price })),
  }));
}

test('V1 channel, range measurements and vertical line share chart gestures and storage', async ({
  page,
  isMobile,
  browserName,
}) => {
  test.setTimeout(60_000);
  await page.goto('/');
  await ready(page);
  const plot = (await page.getByTestId('chart').locator('canvas').first().boundingBox())!;

  const cdp = isMobile && browserName === 'chromium' ? await page.context().newCDPSession(page) : null;
  if (isMobile && !cdp)
    await page.evaluate(() => {
      for (const type of ['pointerdown', 'pointermove', 'pointerup'])
        document.addEventListener(
          type,
          (event) => Object.defineProperty(event, 'pointerType', { value: 'touch' }),
          { capture: true },
        );
    });

  const drag = async (x: number, y: number, toX: number, toY: number) => {
    if (cdp) {
      await cdp.send('Input.dispatchTouchEvent', {
        type: 'touchStart',
        touchPoints: [{ x, y, id: 1 }],
      });
      // Native chart pan begins on the first move and scrolls on later moves.
      for (let step = 1; step <= 8; step++) {
        await cdp.send('Input.dispatchTouchEvent', {
          type: 'touchMove',
          touchPoints: [{ x: x + (toX - x) * step / 8, y: y + (toY - y) * step / 8, id: 1 }],
        });
      }
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    } else {
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(toX, toY, { steps: 8 });
      await page.mouse.up();
    }
  };
  const placeOne = async (name: DrawingToolName, from: [number, number], to: [number, number]) => {
    await chooseDrawingTool(page, name);
    await drag(...from, ...to);
  };
  const placeTwo = async (
    name: DrawingToolName,
    first: [number, number],
    second: [number, number],
  ) => {
    await chooseDrawingTool(page, name);
    await drag(first[0] - 6, first[1] - 6, first[0], first[1]);
    await drag(second[0] - 6, second[1] - 6, second[0], second[1]);
  };
  const x = (fraction: number) => plot.x + plot.width * fraction;
  const y = (fraction: number) => plot.y + plot.height * fraction;

  await chooseDrawingTool(page, 'Parallel Channel');
  await drag(x(0.18) - 6, y(0.48) - 6, x(0.18), y(0.48));
  await drag(x(0.92) - 6, y(0.6) - 6, x(0.92), y(0.6));
  await drag(x(0.48) - 6, y(0.35) - 6, x(0.48), y(0.35));
  await expect.poll(async () => (await readDrawings(page)).find((d) => d.type === 'channel')?.points.length).toBe(3);
  const channelPlaced = (await readDrawings(page)).find((d) => d.type === 'channel')!;
  expect(channelPlaced.points[1].logical).toBeGreaterThan(2499);
  await drag(x(0.48), y(0.35), x(0.5), y(0.33));
  await expect
    .poll(async () => (await readDrawings(page)).find((d) => d.type === 'channel')?.points[2].price)
    .not.toBe(channelPlaced.points[2].price);
  const channelEndpointEdit = (await readDrawings(page)).find((d) => d.type === 'channel')!;
  expect(channelEndpointEdit.points[0]).toEqual(channelPlaced.points[0]);
  expect(channelEndpointEdit.points[1]).toEqual(channelPlaced.points[1]);
  await drag(x(0.49), y(0.42), x(0.52), y(0.44));
  const channelMoved = (await readDrawings(page)).find((d) => d.type === 'channel')!;
  expect(channelMoved.points.every((point, i) => point.time !== channelEndpointEdit.points[i].time)).toBe(true);
  await clickDrawingUtility(page, 'Undo drawing');
  await expect
    .poll(async () => (await readDrawings(page)).find((d) => d.type === 'channel')?.points)
    .toEqual(channelEndpointEdit.points);
  await clickDrawingUtility(page, 'Redo drawing');
  await expect
    .poll(async () => (await readDrawings(page)).find((d) => d.type === 'channel')?.points)
    .toEqual(channelMoved.points);

  await placeTwo('Price Range', [x(0.24), y(0.37)], [x(0.62), y(0.62)]);
  await expect
    .poll(async () => (await readDrawings(page)).find((drawing) => drawing.type === 'price-range')?.points.length)
    .toBe(2);
  const priceRange = (await readDrawings(page)).find((drawing) => drawing.type === 'price-range')!;
  await drag(x(0.62), y(0.62), x(0.65), y(0.6));
  await expect
    .poll(async () => (await readDrawings(page)).find((drawing) => drawing.type === 'price-range')?.points[1].time)
    .not.toBe(priceRange.points[1].time);
  const priceRangeEdited = (await readDrawings(page)).find((drawing) => drawing.type === 'price-range')!;
  expect(priceRangeEdited.points[0]).toEqual(priceRange.points[0]);

  await placeTwo('Date Range', [x(0.24), y(0.4)], [x(0.62), y(0.57)]);
  await expect
    .poll(async () => (await readDrawings(page)).find((drawing) => drawing.type === 'date-range')?.points.length)
    .toBe(2);
  const dateRange = (await readDrawings(page)).find((drawing) => drawing.type === 'date-range')!;
  await drag(x(0.43), y(0.485), x(0.46), y(0.505));
  await expect
    .poll(async () => (await readDrawings(page)).find((drawing) => drawing.type === 'date-range')?.points[0].time)
    .not.toBe(dateRange.points[0].time);
  const dateRangeMoved = (await readDrawings(page)).find((drawing) => drawing.type === 'date-range')!;
  expect(dateRangeMoved.points[1].time).not.toBe(dateRange.points[1].time);

  await placeTwo('Price + Date Range', [x(0.56), y(0.35)], [x(0.84), y(0.64)]);
  await expect
    .poll(async () => (await readDrawings(page)).find((drawing) => drawing.type === 'price-date-range')?.points.length)
    .toBe(2);
  const combined = (await readDrawings(page)).find((drawing) => drawing.type === 'price-date-range')!;
  await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();
  const locked = (await readDrawings(page)).find((drawing) => drawing.type === 'price-date-range')!;
  expect(locked.locked).toBe(true);
  const beforePan = (await readState(page))!.preferences.views['1D'];
  await drag(x(0.7), y(0.5), x(0.78), y(0.5));
  await expect
    .poll(async () => (await readState(page))!.preferences.views['1D'])
    .not.toEqual(beforePan);
  expect((await readDrawings(page)).find((drawing) => drawing.type === 'price-date-range')).toEqual(locked);
  expect(combined.points).toEqual(locked.points);

  await placeOne('Vertical Line', [x(0.76) - 6, y(0.42) - 6], [x(0.76), y(0.42)]);
  await expect
    .poll(async () => (await readDrawings(page)).find((drawing) => drawing.type === 'vertical')?.points.length)
    .toBe(1);
  const vertical = (await readDrawings(page)).find((drawing) => drawing.type === 'vertical')!;
  await drag(x(0.76), y(0.66), x(0.82), y(0.68));
  await expect
    .poll(async () => (await readDrawings(page)).find((drawing) => drawing.type === 'vertical')?.points[0].time)
    .not.toBe(vertical.points[0].time);
  expect((await readDrawings(page)).find((drawing) => drawing.type === 'vertical')!.points[0].price).toBe(
    vertical.points[0].price,
  );

  const saved = await readDrawings(page);
  expect(saved.map((drawing) => drawing.type)).toEqual([
    'channel',
    'price-range',
    'date-range',
    'price-date-range',
    'vertical',
  ]);
  expect(saved.every((drawing) => drawing.points.every((point) => Number.isFinite(point.time) && Number.isFinite(point.price)))).toBe(true);
  expect(await page.evaluate(() => scrollY)).toBe(0);

  const openDrawingPanel = async () => {
    if (await page.locator('.mobile-nav').isVisible())
      await page.locator('.mobile-nav').getByRole('button', { name: 'Drawings', exact: true }).click();
    else await page.getByLabel('Research panel', { exact: true }).selectOption('drawings');
    await expect(page.getByRole('region', { name: 'Drawing objects' })).toBeVisible();
  };
  const closeDrawingPanel = async () => {
    const close = page.getByRole('button', { name: 'Close panel', exact: true });
    if (await close.isVisible()) await close.click();
  };

  // Verify the channel remains selectable while locked, delegates body drags to chart pan,
  // and can be unlocked again before capturing the locked state of every new tool.
  await openDrawingPanel();
  await page.getByRole('button', { name: /Parallel Channel 1/ }).click();
  await page.getByRole('button', { name: 'Lock drawing 1', exact: true }).click();
  const channelLocked = (await readDrawings(page))[0]!;
  expect(channelLocked.locked).toBe(true);
  const beforeChannelPan = (await readState(page))!.preferences.views['1D'];
  await closeDrawingPanel();
  await drag(x(0.5), y(0.44), x(0.58), y(0.44));
  await expect
    .poll(async () => (await readState(page))!.preferences.views['1D'])
    .not.toEqual(beforeChannelPan);
  expect((await readDrawings(page))[0]).toEqual(channelLocked);

  await openDrawingPanel();
  await page.getByRole('button', { name: 'Unlock drawing 1', exact: true }).click();
  await expect.poll(async () => (await readDrawings(page))[0]?.locked).toBe(false);

  const requiredTypes = [
    'channel',
    'price-range',
    'date-range',
    'price-date-range',
    'vertical',
  ] as const;
  const lockedSnapshots: ReturnType<typeof canonicalDrawingSnapshot> = [];
  for (const [index, type] of requiredTypes.entries()) {
    let drawing = (await readDrawings(page))[index]!;
    expect(drawing.type).toBe(type);
    if (!drawing.locked) {
      await page.getByRole('button', { name: `Lock drawing ${index + 1}`, exact: true }).click();
      await expect.poll(async () => (await readDrawings(page))[index]?.locked).toBe(true);
      drawing = (await readDrawings(page))[index]!;
    }
    lockedSnapshots.push(...canonicalDrawingSnapshot([drawing]));
  }
  expect(lockedSnapshots.map((drawing) => drawing.type)).toEqual(requiredTypes);
  expect(lockedSnapshots.every((drawing) => drawing.locked)).toBe(true);
  await closeDrawingPanel();

  const search = page.getByLabel('Symbol search', { exact: true });
  await search.fill('NVDA');
  await search.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
  await ready(page);
  expect(await readDrawings(page, 'NVDA')).toEqual([]);

  await search.fill('AAPL');
  await search.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText('AAPL');
  await ready(page);
  expect(canonicalDrawingSnapshot(await readDrawings(page))).toEqual(lockedSnapshots);

  for (const timeframe of ['5m', '1H', '1D'] as const) {
    await page.getByRole('button', { name: `Timeframe ${timeframe}`, exact: true }).click();
    await ready(page);
    expect((await readState(page))!.preferences.timeframe).toBe(timeframe);
    expect(canonicalDrawingSnapshot(await readDrawings(page))).toEqual(lockedSnapshots);
    await expect(page.locator('.chart-status')).toContainText(
      timeframe === '1D' ? '5 DRAWINGS' : '0 DRAWINGS',
    );
  }

  await page.reload();
  await ready(page);
  expect(canonicalDrawingSnapshot(await readDrawings(page))).toEqual(lockedSnapshots);

  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export settings', exact: true }).click();
  const backup = await downloadEvent;
  const backupPath = await backup.path();
  expect(backupPath).not.toBeNull();
  const exported = JSON.parse(await readFile(backupPath!, 'utf8')) as {
    version: number;
    symbols: SymbolState[];
  };
  expect(exported.version).toBe(4);
  const exportedAapl = exported.symbols.find((state) => state.symbol === 'AAPL');
  expect(exportedAapl).toBeDefined();
  expect(canonicalDrawingSnapshot(exportedAapl!.drawings)).toEqual(lockedSnapshots);

  await openDrawingPanel();
  await page.getByRole('button', { name: 'Unlock drawing 1', exact: true }).click();
  await expect.poll(async () => (await readDrawings(page))[0]?.locked).toBe(false);
  await closeDrawingPanel();
  await page.getByLabel('Settings file', { exact: true }).setInputFiles(backupPath!);
  await expect(page.getByRole('status')).toContainText('設定已還原');
  await ready(page);
  expect(canonicalDrawingSnapshot(await readDrawings(page))).toEqual(lockedSnapshots);
});

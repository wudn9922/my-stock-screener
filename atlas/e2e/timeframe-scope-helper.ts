import { expect, type Page } from '@playwright/test';
import { clickExport, openPanel as openWorkspacePanel } from './site-helpers';
import { chooseDrawingTool } from './drawing-picker-helper';
import { readFile } from 'node:fs/promises';
import type { SymbolState } from '../src/storage/schema';

type OwnedTimeframe = '1D' | '1W' | '1M';

async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}

async function savedSymbol(page: Page): Promise<SymbolState | undefined> {
  return page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('atlas-terminal');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const data = await new Promise<SymbolState | undefined>((resolve, reject) => {
      const request = db.transaction('symbols', 'readonly').objectStore('symbols').get('AAPL');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    db.close();
    return data;
  });
}

async function openPanel(page: Page, panel: 'indicators' | 'drawings') {
  await openWorkspacePanel(page, panel);
  await expect(page.locator(panel === 'indicators' ? '.indicator-panel' : '.drawing-panel')).toBeVisible();
}

async function closePanel(page: Page) {
  const close = page.getByRole('button', { name: '關閉面板', exact: true });
  if (await close.isVisible()) await close.click();
}

async function addSma(page: Page, period: number) {
  await openPanel(page, 'indicators');
  await page.getByLabel('指標類型', { exact: true }).selectOption('SMA');
  await page.getByLabel('SMA 週期', { exact: true }).fill(String(period));
  await page.getByRole('button', { name: '新增 SMA', exact: true }).click();
  await closePanel(page);
}

async function addVolume(page: Page) {
  await openPanel(page, 'indicators');
  await page.getByLabel('指標類型', { exact: true }).selectOption('Volume');
  await page.getByRole('button', { name: '新增 Volume', exact: true }).click();
  await closePanel(page);
}

async function addHorizontalLine(page: Page, timeframe: OwnedTimeframe, count: number) {
  const box = (await page.getByTestId('chart').boundingBox())!;
  await chooseDrawingTool(page, '水平線');
  await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.46);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.51, { steps: 8 });
  await page.mouse.up();
  await expect
    .poll(async () =>
      (await savedSymbol(page))?.drawings.filter((drawing) =>
        drawing.scope.timeframes.includes(timeframe),
      ).length,
    )
    .toBe(count);
}

async function verifyActivePanel(
  page: Page,
  timeframe: OwnedTimeframe,
  titles: string[],
  drawings: number,
) {
  await expect(page.locator('.indicator-chip')).toHaveCount(titles.length);
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveText(titles);
  await expect(page.locator('.chart-status')).toContainText(`畫線 ${drawings} 條`);
  await expect(page.locator('.chart-status')).toContainText(`指標 ${titles.length} 個`);
  await openPanel(page, 'indicators');
  await expect(page.getByTestId('indicator-scope')).toHaveText(
    `AAPL · ${timeframe} 股票＋週期專屬`,
  );
  await expect(page.locator('.indicator-panel .ma-card')).toHaveCount(titles.length);
  await expect(page.locator('.indicator-panel .ma-card-title b')).toHaveText(titles);
  await closePanel(page);
  await openPanel(page, 'drawings');
  await expect(page.locator('.drawing-panel .drawing-row')).toHaveCount(drawings);
  await closePanel(page);
}

export async function verifyTimeframeOwnership(page: Page) {
  await loaded(page);
  await addSma(page, 24);
  await addHorizontalLine(page, '1D', 1);
  await addVolume(page);
  await expect(page.locator('[data-legacy-volume]')).toHaveCount(0);

  for (const [timeframe, period] of [
    ['1W', 58],
    ['1M', 100],
  ] as const) {
    await page.getByRole('button', { name: `週期 ${timeframe}`, exact: true }).click();
    await loaded(page);
    await expect(page.locator('.indicator-chip')).toHaveCount(0);
    await expect(page.locator('[data-legacy-volume]')).toHaveCount(1);
    await expect(page.locator('.chart-status')).toContainText('畫線 0 條');
    await addSma(page, period);
    await addHorizontalLine(page, timeframe, 1);
  }

  const allScopes = await savedSymbol(page);
  expect(allScopes?.indicators.map((indicator) => indicator.scope.timeframe)).toEqual([
    '1D',
    '1D',
    '1W',
    '1M',
  ]);
  expect(allScopes?.drawings.map((drawing) => drawing.scope.timeframes)).toEqual([
    ['1D'],
    ['1W'],
    ['1M'],
  ]);

  await verifyActivePanel(page, '1M', ['SMA 100'], 1);
  const downloadEvent = page.waitForEvent('download');
  await clickExport(page);
  const download = await downloadEvent;
  const backup = JSON.parse(await readFile((await download.path())!, 'utf8')) as {
    version: number;
    symbols: SymbolState[];
  };
  const backupAapl = backup.symbols.find((state) => state.symbol === 'AAPL');
  expect(backup.version).toBe(4);
  expect(backupAapl?.indicators.map((indicator) => indicator.scope.timeframe)).toEqual([
    '1D',
    '1D',
    '1W',
    '1M',
  ]);
  expect(backupAapl?.drawings.map((drawing) => drawing.scope.timeframes)).toEqual([
    ['1D'],
    ['1W'],
    ['1M'],
  ]);

  await page.reload();
  await loaded(page);
  await verifyActivePanel(page, '1M', ['SMA 100'], 1);
  await page.getByRole('button', { name: '週期 1D', exact: true }).click();
  await loaded(page);
  await verifyActivePanel(page, '1D', ['SMA 24', '成交量'], 1);
  await expect(page.locator('[data-legacy-volume]')).toHaveCount(0);
  await page.getByRole('button', { name: '週期 1W', exact: true }).click();
  await loaded(page);
  await verifyActivePanel(page, '1W', ['SMA 58'], 1);
  await expect(page.locator('[data-legacy-volume]')).toHaveCount(1);
}

import { readFile } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';
import { chooseDrawingTool } from './drawing-picker-helper';
import type { Bar, BarResult, Timeframe } from '../src/market-data/MarketDataProvider';
import type { SymbolState } from '../src/storage/schema';

const tools = [
  'Trend Line',
  'Horizontal Line',
  'Horizontal Ray',
  'Parallel Channel',
  'Rectangle',
  'Fibonacci Retracement',
  'Price Range',
  'Date Range',
  'Price + Date Range',
  'Vertical Line',
] as const;

const categories = [
  {
    slug: 'common',
    tools: ['Trend Line', 'Horizontal Line', 'Rectangle', 'Fibonacci Retracement'],
  },
  { slug: 'lines', tools: ['Trend Line', 'Horizontal Line', 'Horizontal Ray', 'Vertical Line'] },
  { slug: 'channel', tools: ['Parallel Channel'] },
  { slug: 'shapes', tools: ['Rectangle'] },
  { slug: 'fibonacci', tools: ['Fibonacci Retracement'] },
  { slug: 'measurements', tools: ['Price Range', 'Date Range', 'Price + Date Range'] },
] as const;

const activeToolValue: Record<(typeof tools)[number], string> = {
  'Trend Line': 'trend',
  'Horizontal Line': 'horizontal',
  'Horizontal Ray': 'ray',
  'Parallel Channel': 'channel',
  Rectangle: 'rectangle',
  'Fibonacci Retracement': 'fibonacci',
  'Price Range': 'price-range',
  'Date Range': 'date-range',
  'Price + Date Range': 'price-date-range',
  'Vertical Line': 'vertical',
};
const utilities = [
  'Select / Pan',
  'Magnet',
  'Undo drawing',
  'Redo drawing',
  'Zoom in',
  'Zoom out',
  'Show future area',
  'Reset chart view',
] as const;

async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}

async function readSymbol(page: Page, symbol = 'AAPL'): Promise<SymbolState | undefined> {
  return page.evaluate(async (ticker) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('atlas-terminal');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const state = await new Promise<SymbolState | undefined>((resolve, reject) => {
      const request = db.transaction('symbols', 'readonly').objectStore('symbols').get(ticker);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    db.close();
    return state;
  }, symbol);
}

async function openPicker(page: Page) {
  const dialog = page.getByRole('dialog', { name: 'Drawing tools', exact: true });
  if (await dialog.isVisible()) return dialog;

  const launcher = page.getByRole('button', { name: 'Drawing Tools', exact: true });
  await launcher.click();
  await expect(dialog).toBeVisible();
  return dialog;
}

async function addSma14(page: Page) {
  await page.getByRole('button', { name: 'Manage indicators', exact: true }).click();
  await page.getByLabel('Indicator type', { exact: true }).filter({ visible: true }).selectOption('SMA');
  await page.getByLabel('SMA period', { exact: true }).filter({ visible: true }).fill('14');
  await page.getByRole('button', { name: 'Add SMA', exact: true }).click();
  await page.getByRole('button', { name: 'Lock SMA 14', exact: true }).click();
  const close = page.getByRole('button', { name: 'Close panel', exact: true });
  if (await close.isVisible()) await close.click();
}

async function addVolume(page: Page) {
  await page.getByRole('button', { name: 'Manage indicators', exact: true }).click();
  await page.getByLabel('Indicator type', { exact: true }).filter({ visible: true }).selectOption('Volume');
  await page.getByRole('button', { name: 'Add Volume', exact: true }).click();
  const close = page.getByRole('button', { name: 'Close panel', exact: true });
  if (await close.isVisible()) await close.click();
}

async function exportSettings(page: Page) {
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export settings', exact: true }).click();
  const download = await downloadEvent;
  const path = await download.path();
  expect(path).not.toBeNull();
  return JSON.parse(await readFile(path!, 'utf8')) as {
    version: number;
    app: unknown;
    symbols: SymbolState[];
  };
}

async function drag(page: Page, x: number, y: number, toX: number, toY: number) {
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(toX, toY, { steps: 8 });
  await page.mouse.up();
}

test('Drawing Tools picker keeps the chart clear and supports keyboard, backdrop and fullscreen use', async ({
  page,
}) => {
  await page.goto('/');
  await loaded(page);

  const launcher = page.getByRole('button', { name: 'Drawing Tools', exact: true });
  const launcherElement = page.locator('.drawing-tools-launcher');
  await expect(launcher).toHaveCount(1);
  const chart = page.getByTestId('chart');
  const chartBox = (await chart.boundingBox())!;
  const stageBox = (await page.locator('.chart-stage').boundingBox())!;
  const launcherBox = (await launcher.boundingBox())!;
  expect(launcherBox.y + launcherBox.height).toBeLessThanOrEqual(chartBox.y + 1);
  expect(Math.abs(chartBox.x - stageBox.x)).toBeLessThanOrEqual(1);
  expect(chartBox.width).toBeGreaterThan(stageBox.width - 2);
  for (const name of tools)
    await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('C ');

  const chartHeight = chartBox.height;
  const dialog = await openPicker(page);
  await expect(dialog).toHaveAttribute('aria-modal', 'true');
  for (const name of utilities)
    await expect(dialog.getByRole('button', { name, exact: true })).toBeVisible();
  await expect(page.locator('.workspace')).toHaveAttribute('inert', '');
  await expect(dialog.getByRole('button', { name: 'Close drawing tools', exact: true })).toBeFocused();
  expect((await chart.boundingBox())!.height).toBeCloseTo(chartHeight, 0);

  const firstFocusable = dialog.locator('button:not(:disabled)').first();
  const lastFocusable = dialog.locator('button:not(:disabled)').last();
  await lastFocusable.focus();
  await page.keyboard.press('Tab');
  await expect(firstFocusable).toBeFocused();
  await firstFocusable.focus();
  await page.keyboard.press('Shift+Tab');
  await expect(lastFocusable).toBeFocused();

  for (const category of categories) {
    const picker = await openPicker(page);
    const categoryButton = picker.getByRole('button', {
      name: `Drawing category ${category.slug}`,
      exact: true,
    });
    await categoryButton.click();
    await expect(categoryButton).toHaveAttribute('aria-pressed', 'true');
    for (const name of tools) {
      const tool = picker.getByRole('button', { name, exact: true });
      if ((category.tools as readonly string[]).includes(name)) await expect(tool).toHaveCount(1);
      else await expect(tool).toHaveCount(0);
    }
    await expect(launcherElement).toHaveAttribute('data-active-tool', 'select');
    await expect.poll(async () => (await readSymbol(page))?.drawings.length ?? 0).toBe(0);
    await page.keyboard.press('Escape');
    await expect(picker).toBeHidden();
    await expect(launcher).toBeFocused();
    expect((await chart.boundingBox())!.height).toBeCloseTo(chartHeight, 0);
  }

  for (const name of tools) {
    await chooseDrawingTool(page, name);
    await expect(launcher).toHaveAttribute('data-active-tool', activeToolValue[name]);
    await expect(launcher).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('dialog', { name: 'Drawing tools', exact: true })).toBeHidden();
  }
  await expect.poll(async () => (await readSymbol(page))?.drawings.length ?? 0).toBe(0);

  await openPicker(page);
  await page.locator('.drawing-tool-picker-backdrop').click({ position: { x: 4, y: 4 } });
  await expect(page.getByRole('dialog', { name: 'Drawing tools', exact: true })).toBeHidden();
  await expect(launcher).toBeFocused();
  expect((await chart.boundingBox())!.height).toBeCloseTo(chartHeight, 0);

  await page.getByRole('button', { name: 'Enter chart fullscreen', exact: true }).click();
  await expect(page.locator('.app-shell')).toHaveClass(/chart-focus/);
  const fullscreenRoot = (await page.locator('.app-shell').boundingBox())!;
  const fullscreenDialog = await openPicker(page);
  const dialogBox = (await fullscreenDialog.boundingBox())!;
  expect(dialogBox.x).toBeGreaterThanOrEqual(fullscreenRoot.x);
  expect(dialogBox.y).toBeGreaterThanOrEqual(fullscreenRoot.y);
  expect(dialogBox.x + dialogBox.width).toBeLessThanOrEqual(fullscreenRoot.x + fullscreenRoot.width + 1);
  expect(dialogBox.y + dialogBox.height).toBeLessThanOrEqual(fullscreenRoot.y + fullscreenRoot.height + 1);
  await page.keyboard.press('Escape');
  await expect(page.locator('.app-shell')).toHaveClass(/chart-focus/);
  await page.keyboard.press('Escape');
  await expect(launcher).toHaveAttribute('data-active-tool', 'select');
  await expect(page.locator('.app-shell')).toHaveClass(/chart-focus/);
  await page.keyboard.press('Escape');
  await expect(page.locator('.app-shell')).not.toHaveClass(/chart-focus/);
});

test('new drawing and SMA use ATR width defaults and preserve locked symbol/timeframe ownership', async ({
  page,
}) => {
  await page.goto('/');
  await loaded(page);
  await addSma14(page);
  await addVolume(page);

  await chooseDrawingTool(page, 'Horizontal Line');
  const chart = (await page.getByTestId('chart').boundingBox())!;
  await drag(
    page,
    chart.x + chart.width * 0.52 - 8,
    chart.y + chart.height * 0.5 - 8,
    chart.x + chart.width * 0.52,
    chart.y + chart.height * 0.5,
  );
  await expect.poll(async () => (await readSymbol(page))?.drawings.length).toBe(1);
  await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();
  await expect.poll(async () => (await readSymbol(page))?.drawings[0]?.locked).toBe(true);

  const initial = await readSymbol(page);
  expect(initial!.drawings[0].style).toMatchObject({ lineWidth: 1, widthMode: 'atr' });
  expect(initial!.indicators[0]).toMatchObject({ lineWidth: 1, widthMode: 'atr', locked: true });
  expect(initial!.indicators.find((indicator) => indicator.type === 'Volume')).toMatchObject({
    lineWidth: 2,
    widthMode: 'pixels',
  });

  const backup = await exportSettings(page);
  expect(backup.version).toBe(4);
  const exportedAapl = backup.symbols.find((state) => state.symbol === 'AAPL')!;
  expect(exportedAapl.drawings[0].style.widthMode).toBe('atr');
  expect(exportedAapl.indicators.find((indicator) => indicator.type === 'SMA')!.widthMode).toBe('atr');
  expect(exportedAapl.indicators.find((indicator) => indicator.type === 'Volume')).toMatchObject({
    lineWidth: 2,
    widthMode: 'pixels',
  });

  await page.getByRole('button', { name: 'Timeframe 1W', exact: true }).click();
  await loaded(page);
  const weekly = await readSymbol(page);
  expect(weekly!.drawings[0].scope.timeframes).toEqual(['1D']);
  expect(weekly!.indicators[0].scope.timeframe).toBe('1D');
  await expect(page.locator('.chart-status')).toContainText('0 DRAWINGS');

  const search = page.getByLabel('Symbol search', { exact: true });
  await search.fill('NVDA');
  await search.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
  await loaded(page);
  expect((await readSymbol(page, 'NVDA'))?.drawings ?? []).toEqual([]);
  expect((await readSymbol(page, 'NVDA'))?.indicators ?? []).toEqual([]);

  await search.fill('AAPL');
  await search.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText('AAPL');
  await loaded(page);
  await page.getByRole('button', { name: 'Timeframe 1D', exact: true }).click();
  await loaded(page);
  await page.reload();
  await loaded(page);
  const restored = await readSymbol(page);
  expect(restored!.drawings[0]).toMatchObject({ locked: true, style: { widthMode: 'atr', lineWidth: 1 } });
  expect(restored!.indicators.find((indicator) => indicator.type === 'SMA')).toMatchObject({
    locked: true,
    widthMode: 'atr',
    lineWidth: 1,
  });
  expect(restored!.indicators.find((indicator) => indicator.type === 'Volume')).toMatchObject({
    lineWidth: 2,
    widthMode: 'pixels',
  });
});

test('short landscape picker keeps category targets reachable without resizing or scrolling the chart page', async ({
  page,
}) => {
  await page.setViewportSize({ width: 844, height: 390 });
  await page.goto('/');
  await loaded(page);
  const chart = page.getByTestId('chart');
  const chartHeight = (await chart.boundingBox())!.height;
  const dialog = await openPicker(page);
  const measurements = dialog.getByRole('button', {
    name: 'Drawing category measurements',
    exact: true,
  });
  const categoryNav = dialog.getByRole('navigation', { name: 'Drawing tool categories', exact: true });
  await expect
    .poll(() => categoryNav.evaluate((nav) => nav.scrollHeight > nav.clientHeight))
    .toBe(true);
  const initialNavScroll = await categoryNav.evaluate((nav) => nav.scrollTop);
  await measurements.scrollIntoViewIfNeeded();
  await expect(measurements).toBeVisible();
  expect(await categoryNav.evaluate((nav) => nav.scrollTop)).toBeGreaterThan(initialNavScroll);
  const target = (await measurements.boundingBox())!;
  expect(target.width).toBeGreaterThanOrEqual(44);
  expect(target.height).toBeGreaterThanOrEqual(44);
  await measurements.click();
  await expect(dialog.getByRole('button', { name: 'Price Range', exact: true })).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Date Range', exact: true })).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Price + Date Range', exact: true })).toBeVisible();
  const optionsPanel = dialog.locator('.drawing-tool-options');
  const optionsBox = (await optionsPanel.boundingBox())!;
  for (const name of ['Price Range', 'Date Range', 'Price + Date Range']) {
    const optionBox = (await dialog.getByRole('button', { name, exact: true }).boundingBox())!;
    expect(optionBox.x).toBeGreaterThanOrEqual(optionsBox.x);
    expect(optionBox.y).toBeGreaterThanOrEqual(optionsBox.y);
    expect(optionBox.x + optionBox.width).toBeLessThanOrEqual(optionsBox.x + optionsBox.width + 1);
    expect(optionBox.y + optionBox.height).toBeLessThanOrEqual(optionsBox.y + optionsBox.height + 1);
  }
  expect(await page.evaluate(() => scrollY)).toBe(0);
  expect((await chart.boundingBox())!.height).toBeCloseTo(chartHeight, 0);
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  expect(await page.evaluate(() => scrollY)).toBe(0);
  expect((await chart.boundingBox())!.height).toBeCloseTo(chartHeight, 0);
});

test('Escape closes an owned input dialog and passes through ordinary focused inputs', async ({
  page,
}) => {
  await page.goto('/');
  await loaded(page);
  const search = page.getByLabel('Symbol search', { exact: true });
  await search.fill('AAPL');
  await search.focus();
  await page.keyboard.press('Escape');
  await expect(search).toBeFocused();
  await expect(search).toHaveValue('AAPL');
  await expect(page.getByTestId('active-symbol')).toHaveText('AAPL');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Manage indicators', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'indicators panel', exact: true });
  await expect(dialog).toBeVisible();
  const input = page.getByLabel('SMA period', { exact: true }).filter({ visible: true });
  await input.fill('45');
  await input.focus();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect.poll(async () => (await readSymbol(page))?.indicators.length ?? 0).toBe(0);
});

test('legacy locked pixel widths survive V3 import and V4 export without being reinterpreted as ATR', async ({
  page,
}) => {
  await page.goto('/');
  await loaded(page);
  await addSma14(page);
  await chooseDrawingTool(page, 'Horizontal Line');
  const chart = (await page.getByTestId('chart').boundingBox())!;
  await drag(
    page,
    chart.x + chart.width * 0.42 - 8,
    chart.y + chart.height * 0.47 - 8,
    chart.x + chart.width * 0.42,
    chart.y + chart.height * 0.47,
  );
  await expect.poll(async () => (await readSymbol(page))?.drawings.length).toBe(1);
  await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();

  const baseline = await exportSettings(page);
  const aapl = baseline.symbols.find((state) => state.symbol === 'AAPL')!;
  const oldDrawing = structuredClone(aapl.drawings[0]);
  oldDrawing.locked = true;
  oldDrawing.style.lineWidth = 3;
  delete oldDrawing.style.widthMode;
  const oldIndicator = structuredClone(aapl.indicators[0]);
  oldIndicator.locked = true;
  oldIndicator.lineWidth = 3;
  delete oldIndicator.widthMode;
  const v3 = {
    ...baseline,
    version: 3,
    symbols: baseline.symbols.map((state) =>
      state.symbol === 'AAPL'
        ? { ...state, drawings: [oldDrawing], indicators: [oldIndicator] }
        : state,
    ),
  };
  await page.getByLabel('Settings file', { exact: true }).setInputFiles({
    name: 'legacy-pixel-widths-v3.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(v3)),
  });
  await expect(page.getByRole('status')).toContainText('設定已還原');
  const imported = await readSymbol(page);
  expect(imported!.drawings[0]).toMatchObject({ locked: true, style: { lineWidth: 3 } });
  expect(imported!.drawings[0].style.widthMode).toBeUndefined();
  expect(imported!.indicators[0]).toMatchObject({ locked: true, lineWidth: 3 });
  expect(imported!.indicators[0].widthMode).toBeUndefined();

  await page.reload();
  await loaded(page);
  const restored = await readSymbol(page);
  expect(restored!.drawings[0]).toMatchObject({ locked: true, style: { lineWidth: 3 } });
  expect(restored!.drawings[0].style.widthMode).toBeUndefined();
  expect(restored!.indicators[0]).toMatchObject({ locked: true, lineWidth: 3 });
  expect(restored!.indicators[0].widthMode).toBeUndefined();
  const exported = await exportSettings(page);
  expect(exported.version).toBe(4);
  const exportedAapl = exported.symbols.find((state) => state.symbol === 'AAPL')!;
  expect(exportedAapl.drawings[0].style.widthMode).toBeUndefined();
  expect(exportedAapl.indicators[0].widthMode).toBeUndefined();
});

test('price scale auto-sizes naturally for ordinary and million-price labels', async ({ page }) => {
  await page.goto('/');
  await loaded(page);
  const measurements = await page.evaluate(async () => {
    const chartEngineUrl = new URL('/src/chart/ChartEngine.ts', location.origin).href;
    const { ChartEngine } = (await import(chartEngineUrl)) as typeof import('../src/chart/ChartEngine');
    const width = Math.max(1, document.querySelector('[data-testid="chart"]')!.clientWidth);
    const host = document.createElement('div');
    Object.assign(host.style, {
      position: 'fixed',
      left: '-10000px',
      top: '0',
      width: `${width}px`,
      height: '360px',
      overflow: 'hidden',
    });
    const header = document.createElement('div');
    const source = document.createElement('div');
    host.append(header, source);
    document.body.append(host);
    const engine = new ChartEngine(host, header, source, {
      commit: () => undefined,
      selection: () => undefined,
      tool: () => undefined,
      view: () => undefined,
    });
    const result = (base: number): BarResult => {
      const start = Date.parse('2026-01-01T00:00:00Z') / 1000;
      const bars: Bar[] = Array.from({ length: 72 }, (_, index) => {
        const close = base + index * (base >= 100000 ? 12_000 : 0.3);
        return {
          time: start + index * 86400,
          open: close - (base >= 100000 ? 400 : 0.4),
          high: close + (base >= 100000 ? 600 : 0.6),
          low: close - (base >= 100000 ? 700 : 0.7),
          close,
          volume: 100_000 + index,
        };
      });
      return {
        bars,
        source: 'Controlled axis sizing test fixture',
        session: 'regular',
        delayed: false,
        adjusted: false,
        asOf: bars.at(-1)!.time,
        latestBarAt: bars.at(-1)!.time,
        dataState: 'simulated',
        cacheStatus: 'fresh',
      };
    };
    const frame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    const measure = async (price: number) => {
      engine.load('AAPL', '1D' as Timeframe, result(price), [], []);
      await frame();
      await frame();
      await frame();
      return {
        minimumWidth: engine.chart.options().rightPriceScale.minimumWidth,
        axisWidth: engine.candles.priceScale().width(),
        paneWidth: engine.chart.paneSize().width,
        hostWidth: host.clientWidth,
      };
    };
    try {
      const ordinary = await measure(232);
      const large = await measure(1_000_000);
      return { ordinary, large };
    } finally {
      engine.destroy();
      host.remove();
    }
  });

  expect(measurements.ordinary.minimumWidth).toBe(0);
  expect(measurements.ordinary.axisWidth).toBeGreaterThan(0);
  expect(measurements.ordinary.axisWidth).toBeLessThan(64);
  expect(measurements.ordinary.paneWidth).toBeGreaterThan(0);
  expect(measurements.large.axisWidth).toBeGreaterThan(measurements.ordinary.axisWidth);
  expect(measurements.large.axisWidth + measurements.large.paneWidth).toBeLessThanOrEqual(
    measurements.large.hostWidth,
  );
});

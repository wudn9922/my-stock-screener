import { writeFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { chooseDrawingTool, clickDrawingUtility } from './drawing-picker-helper';
import { DEFAULT_FIB_LEVELS } from '../src/tools/Fibonacci';
import { DemoProvider } from '../src/market-data/DemoProvider';
import type { Anchor, Drawing, ToolKind } from '../src/drawing/DrawingModel';
import type { IndicatorInstance } from '../src/indicators/IndicatorRegistry';
import { defaultApp } from '../src/storage/IndexedDBStore';
import type { SymbolState } from '../src/storage/schema';

async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}

async function readSymbol(page: Page, symbol = 'AAPL'): Promise<SymbolState | undefined> {
  return page.evaluate(async (ticker) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('atlas-terminal');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () =>
        reject(request.error ?? new Error('Could not open workspace database'));
    });
    const state = await new Promise<SymbolState | undefined>((resolve, reject) => {
      const request = db.transaction('symbols', 'readonly').objectStore('symbols').get(ticker);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error('Symbol read failed'));
    });
    db.close();
    return state;
  }, symbol);
}

function anchor(
  bars: Awaited<ReturnType<DemoProvider['getBars']>>['bars'],
  index: number,
  field: 'open' | 'high' | 'low' | 'close' = 'close',
): Anchor {
  const bar = bars[index];
  return { time: bar.time, logical: index, price: bar[field], timeframe: '1D' };
}

function mixedDrawings(bars: Awaited<ReturnType<DemoProvider['getBars']>>['bars']): Drawing[] {
  const kinds: ToolKind[] = [
    'trend',
    'horizontal',
    'ray',
    'rectangle',
    'fibonacci',
    'channel',
    'price-range',
    'date-range',
    'price-date-range',
    'vertical',
  ];
  const recentBars = bars.slice(-190);
  const visibleLow = Math.min(...recentBars.map((bar) => bar.low));
  const visibleHigh = Math.max(...recentBars.map((bar) => bar.high));
  const globalLow = Math.min(...bars.map((bar) => bar.low));
  const globalHigh = Math.max(...bars.map((bar) => bar.high));
  const extremeBarIndex = bars.findIndex(
    (bar) =>
      (globalLow < visibleLow && bar.low === globalLow) ||
      (globalLow >= visibleLow && bar.high === globalHigh),
  );
  const lowOutsideView = globalLow < visibleLow;
  const highOutsideView = globalHigh > visibleHigh;
  const useLow =
    lowOutsideView && (!highOutsideView || visibleLow - globalLow >= globalHigh - visibleHigh);
  const farPrice = useLow ? globalLow : globalHigh;

  return kinds.flatMap((type, typeIndex) =>
    Array.from({ length: 10 }, (_, copyIndex) => {
      const inCurrentView = copyIndex === 0;
      const firstIndex = inCurrentView
        ? 2360 + typeIndex * 2
        : 250 + typeIndex * 25 + copyIndex * 5;
      const secondIndex = inCurrentView
        ? 2460 + typeIndex * 2
        : 1050 + typeIndex * 20 + copyIndex * 4;
      const first = anchor(bars, firstIndex);
      const second = anchor(bars, secondIndex);
      const third = anchor(
        bars,
        Math.floor((firstIndex + secondIndex) / 2),
        copyIndex % 2 ? 'high' : 'low',
      );
      let points: Anchor[];
      if (type === 'horizontal' || type === 'vertical') {
        const single = anchor(
          bars,
          inCurrentView ? firstIndex : extremeBarIndex,
          useLow ? 'low' : 'high',
        );
        points = [inCurrentView ? single : { ...single, price: farPrice }];
      } else if (type === 'ray') {
        const start = inCurrentView
          ? first
          : anchor(bars, extremeBarIndex, useLow ? 'low' : 'high');
        const end = inCurrentView ? second : anchor(bars, secondIndex);
        points = [start, { ...end, price: inCurrentView ? start.price : farPrice }];
      } else if (type === 'channel') {
        points = [first, second, third];
      } else {
        points = [first, second];
      }
      return {
        id: `stress-${type}-${copyIndex}`,
        symbol: 'AAPL',
        type,
        points,
        ...(type === 'fibonacci' ? { levels: [...DEFAULT_FIB_LEVELS] } : {}),
        locked: copyIndex < 5,
        visible: true,
        scope: { timeframes: 'all' },
        style: {
          color: `#${(0x5180c0 + typeIndex * 0x070301 + copyIndex * 0x010509).toString(16).padStart(6, '0')}`,
          lineWidth: 2,
          lineStyle: copyIndex % 2 ? 'dotted' : 'solid',
          opacity: 0.9,
          ...(type === 'rectangle' ? { fillOpacity: 0.08 } : {}),
        },
      } satisfies Drawing;
    }),
  );
}

test('V1 stress: 2,500 Demo bars, eight mixed averages and 100 mixed drawings keep chart pan and zoom available', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'desktop-chromium',
    'One controlled renderer stress run; journey and mobile coverage run in all projects.',
  );
  test.setTimeout(120_000);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await loaded(page);

  const demo = await new DemoProvider().getBars('AAPL', '1D');
  expect(demo.bars).toHaveLength(2500);
  const drawings = mixedDrawings(demo.bars);
  const indicators: IndicatorInstance[] = [
    ['SMA', 5],
    ['EMA', 12],
    ['SMA', 24],
    ['EMA', 43],
    ['SMA', 56],
    ['EMA', 58],
    ['SMA', 100],
    ['EMA', 200],
  ].map(([type, period], index) => ({
    id: `stress-${type}-${period}`,
    symbol: 'AAPL',
    type: type as 'SMA' | 'EMA',
    period: period as number,
    source: 'close',
    visible: true,
    locked: index % 2 === 0,
    lineWidth: 1,
    color: ['#f0b35b', '#8e99f3', '#49cbbb', '#e479a0'][index % 4],
    scope: {},
  }));
  const app = structuredClone(defaultApp);
  app.activeSymbol = 'AAPL';
  app.provider = 'demo';
  const exported = {
    version: 2,
    exportedAt: new Date().toISOString(),
    app,
    symbols: [
      {
        symbol: 'AAPL',
        drawings,
        indicators,
        preferences: { timeframe: '1D', views: {}, magnet: false },
      },
    ],
  };
  const migratedDrawings: Drawing[] = drawings.map((drawing) => ({
    ...drawing,
    scope: { timeframes: [drawing.points[0].timeframe] },
  }));
  const migratedIndicators: IndicatorInstance[] = indicators.map((indicator) => ({
    ...indicator,
    scope: { timeframe: '1D' },
  }));

  await page.getByLabel('Settings file', { exact: true }).setInputFiles({
    name: 'v1-mixed-stress.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(exported)),
  });
  await expect(page.getByRole('status')).toContainText('設定已還原');
  await loaded(page);
  await expect(page.locator('.chart-status')).toContainText('100 DRAWINGS');
  await expect(page.locator('.indicator-chip')).toHaveCount(8);

  const types = [
    'trend',
    'horizontal',
    'ray',
    'rectangle',
    'fibonacci',
    'channel',
    'price-range',
    'date-range',
    'price-date-range',
    'vertical',
  ];
  const stored = (await readSymbol(page))!;
  expect(stored.drawings).toEqual(migratedDrawings);
  expect(stored.indicators).toEqual(migratedIndicators);
  expect(stored.drawings.filter((drawing) => drawing.locked)).toHaveLength(50);
  expect(
    Object.fromEntries(
      types.map((type) => [
        type,
        stored.drawings.filter((drawing) => drawing.type === type).length,
      ]),
    ),
  ).toEqual(Object.fromEntries(types.map((type) => [type, 10])));
  expect(
    stored.drawings.every((drawing) =>
      drawing.points.every((point) => Number.isFinite(point.time) && Number.isFinite(point.price)),
    ),
  ).toBe(true);

  await clickDrawingUtility(page, 'Select / Pan');
  await clickDrawingUtility(page, 'Zoom in');
  await clickDrawingUtility(page, 'Zoom out');
  await expect.poll(async () => (await readSymbol(page))?.preferences.views['1D']).toBeDefined();
  const beforePan = (await readSymbol(page))!.preferences.views['1D'];
  const chart = (await page.getByTestId('chart').locator('canvas').first().boundingBox())!;
  await page.mouse.move(chart.x + chart.width * 0.5, chart.y + chart.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(chart.x + chart.width * 0.5 + 50, chart.y + chart.height * 0.5 + 8, {
    steps: 12,
  });
  await page.mouse.up();
  await expect
    .poll(async () => (await readSymbol(page))?.preferences.views['1D'])
    .not.toEqual(beforePan);
  await clickDrawingUtility(page, 'Zoom in');
  await clickDrawingUtility(page, 'Zoom out');
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  expect((await readSymbol(page))!.drawings).toEqual(migratedDrawings);

  // Exercise placement and endpoint editing while the full mixed workload is present.
  const editingPlot = (await page.getByTestId('chart').locator('canvas').first().boundingBox())!;
  const first = {
    x: editingPlot.x + editingPlot.width * 0.2,
    y: editingPlot.y + editingPlot.height * 0.62,
  };
  const second = {
    x: editingPlot.x + editingPlot.width * 0.6,
    y: editingPlot.y + editingPlot.height * 0.74,
  };
  await chooseDrawingTool(page, 'Trend Line');
  for (const point of [first, second]) {
    await page.mouse.move(point.x - 6, point.y - 6);
    await page.mouse.down();
    await page.mouse.move(point.x, point.y, { steps: 6 });
    await page.mouse.up();
  }
  await expect.poll(async () => (await readSymbol(page))?.drawings.length).toBe(101);
  const editable = (await readSymbol(page))!.drawings[100];
  await page.mouse.move(first.x, first.y);
  await page.mouse.down();
  await page.mouse.move(first.x + 18, first.y - 18, { steps: 8 });
  await page.mouse.up();
  await expect
    .poll(async () => (await readSymbol(page))?.drawings[100]?.points[0].price)
    .not.toBe(editable.points[0].price);
  expect((await readSymbol(page))!.drawings.slice(0, 100)).toEqual(migratedDrawings);
  await page.getByRole('button', { name: 'Delete selected drawing', exact: true }).click();
  await expect.poll(async () => (await readSymbol(page))?.drawings.length).toBe(100);

  const gaps = await page.evaluate(
    () =>
      new Promise<number[]>((resolve) => {
        const values: number[] = [];
        let previous = performance.now();
        const sample = (time: number) => {
          values.push(time - previous);
          previous = time;
          if (values.length < 90) requestAnimationFrame(sample);
          else resolve(values);
        };
        requestAnimationFrame(sample);
      }),
  );
  const sorted = gaps.slice(1).sort((a, b) => a - b);
  const report = {
    environment:
      'Headless desktop Chromium RAF scheduling only; not device FPS or GPU paint performance.',
    bars: demo.bars.length,
    indicators: indicators.length,
    drawings: drawings.length,
    lockedDrawings: stored.drawings.filter((drawing) => drawing.locked).length,
    types,
    editWithFullWorkload: true,
    peakDrawingCount: 101,
    rafSamples: sorted.length,
    rafGapMedianMs: sorted[Math.floor(sorted.length * 0.5)],
    rafGapP95Ms: sorted[Math.floor(sorted.length * 0.95)],
    runtimeErrors: errors,
  };
  writeFileSync('docs/v1-performance-measurement.json', JSON.stringify(report, null, 2) + '\n');
  await info.attach('v1-headless-raf-scheduling.json', {
    body: JSON.stringify(report, null, 2),
    contentType: 'application/json',
  });
  expect(errors).toEqual([]);
});

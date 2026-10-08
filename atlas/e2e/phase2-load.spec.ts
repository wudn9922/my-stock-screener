import { test, expect } from '@playwright/test';
import { clickDrawingUtility } from './drawing-picker-helper';
import { DEFAULT_FIB_LEVELS } from '../src/tools/Fibonacci';
test('Phase 2 mixed geometry: 2,500 bars, 8 SMAs and 100 locked objects keep pan/zoom operable', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'desktop-chromium',
    'Controlled renderer load scenario; mobile gesture coverage remains in drawing/terminal suites',
  );
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/');
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  const last = Date.parse('2026-10-05T00:00:00Z') / 1000;
  const types = ['trend', 'horizontal', 'ray', 'rectangle', 'fibonacci'] as const;
  const drawings = Array.from({ length: 100 }, (_, i) => {
    const type = types[i % 5],
      price = 260 + i * 0.12;
    const first = {
      time: last - (10 + (i % 70)) * 86400,
      logical: 2400 + (i % 70),
      price,
      timeframe: '1D',
    };
    const second = {
      time: last + 30 * 86400,
      logical: 2529,
      price: type === 'ray' ? price : price + 3,
      timeframe: '1D',
    };
    return {
      id: `mixed-${i}`,
      symbol: 'AAPL',
      type,
      points: type === 'horizontal' ? [first] : [first, second],
      ...(type === 'fibonacci' ? { levels: [...DEFAULT_FIB_LEVELS] } : {}),
      locked: true,
      visible: true,
      scope: { timeframes: 'all' },
      style: { color: '#5ca9ff', lineWidth: 2 },
    };
  });
  const indicators = [5, 12, 24, 43, 56, 58, 100, 200].map((period) => ({
    id: `ma-${period}`,
    symbol: 'AAPL',
    type: 'SMA',
    period,
    source: 'close',
    visible: true,
    locked: true,
    lineWidth: 1,
    color: '#f0b35b',
    scope: {},
  }));
  const migratedDrawings = drawings.map((drawing) => ({
    ...drawing,
    scope: { timeframes: [drawing.points[0].timeframe] },
  }));
  await page.getByLabel('Settings file', { exact: true }).setInputFiles({
    name: 'mixed-load.json',
    mimeType: 'application/json',
    buffer: Buffer.from(
      JSON.stringify({
        version: 1,
        exportedAt: new Date().toISOString(),
        app: { activeSymbol: 'AAPL', watchlist: ['AAPL'], provider: 'demo' },
        symbols: [
          {
            symbol: 'AAPL',
            drawings,
            indicators,
            preferences: { timeframe: '1D', magnet: false, views: {} },
          },
        ],
      }),
    ),
  });
  await expect(page.getByRole('status')).toContainText('設定已還原');
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.locator('.chart-status')).toContainText('100 DRAWINGS');
  await expect(page.locator('.indicator-chip')).toHaveCount(8);
  const r = (await page.getByTestId('chart').boundingBox())!;
  for (let i = 0; i < 40; i++) await page.mouse.move(r.x + 80 + i * 8, r.y + r.height * 0.55);
  await page.mouse.move(r.x + r.width * 0.55, r.y + r.height * 0.55);
  await page.mouse.down();
  await page.mouse.move(r.x + r.width * 0.55 + 50, r.y + r.height * 0.55, { steps: 12 });
  await page.mouse.up();
  await clickDrawingUtility(page, 'Zoom in');
  await clickDrawingUtility(page, 'Zoom out');
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  const stored = await page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve) => {
      const r = indexedDB.open('atlas-terminal');
      r.onsuccess = () => resolve(r.result);
    });
    const result = await new Promise<unknown>((resolve) => {
      const r = db.transaction('symbols').objectStore('symbols').get('AAPL');
      r.onsuccess = () => resolve(r.result.drawings);
    });
    db.close();
    return result;
  });
  expect(stored).toEqual(migratedDrawings);
  expect(errors).toEqual([]);
  await page.screenshot({ path: 'scratch/stress-phase2-mixed.png' });
});

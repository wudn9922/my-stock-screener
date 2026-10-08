import { test, expect } from '@playwright/test';
import { clickDrawingUtility } from './drawing-picker-helper';
import { writeFile } from 'node:fs/promises';
test('2,500 bars + 8 SMAs + 100 drawings remain operable; record headless RAF scheduling only', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'desktop-chromium',
    'Single controlled headless measurement; no device FPS claim',
  );
  await page.goto('/');
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  const last = Date.parse('2026-10-05T00:00:00Z') / 1000;
  const drawings = Array.from({ length: 100 }, (_, i) => ({
    id: `stress-${i}`,
    symbol: 'AAPL',
    type: 'trend',
    points: [
      {
        time: last - (10 + (i % 70)) * 86400,
        logical: 2400 + (i % 70),
        price: 262 + i * 0.13,
        timeframe: '1D',
      },
      { time: last + 30 * 86400, logical: 2529, price: 266 + i * 0.15, timeframe: '1D' },
    ],
    locked: true,
    visible: true,
    scope: { timeframes: 'all' },
    style: { color: '#5ca9ff', lineWidth: 2 },
  }));
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
  const json = JSON.stringify({
    version: 1,
    exportedAt: new Date().toISOString(),
    app: { activeSymbol: 'AAPL', watchlist: ['AAPL', 'NVDA'], provider: 'demo' },
    symbols: [
      {
        symbol: 'AAPL',
        drawings,
        indicators,
        preferences: { timeframe: '1D', magnet: false, views: {} },
      },
    ],
  });
  await page.getByLabel('Settings file', { exact: true }).setInputFiles({
    name: 'stress.json',
    mimeType: 'application/json',
    buffer: Buffer.from(json),
  });
  await expect(page.getByRole('status')).toContainText('設定已還原');
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.locator('.chart-status')).toContainText('100 DRAWINGS');
  await expect(page.locator('.indicator-chip')).toHaveCount(8);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.evaluate(() => {
    const gaps: number[] = [];
    let prev = performance.now();
    const sample = (time: number) => {
      gaps.push(time - prev);
      prev = time;
      if (gaps.length < 120) requestAnimationFrame(sample);
      else (window as unknown as { __rafGaps: number[] }).__rafGaps = gaps;
    };
    requestAnimationFrame(sample);
  });
  const r = (await page.getByTestId('chart').boundingBox())!;
  for (let i = 0; i < 90; i++)
    await page.mouse.move(r.x + 80 + (i % 50) * 8, r.y + r.height * 0.55 + Math.sin(i / 8) * 40);
  await clickDrawingUtility(page, 'Zoom in');
  await clickDrawingUtility(page, 'Zoom out');
  await expect
    .poll(() => page.evaluate(() => !!(window as unknown as { __rafGaps?: number[] }).__rafGaps))
    .toBe(true);
  const samples = await page.evaluate(
      () => (window as unknown as { __rafGaps: number[] }).__rafGaps,
    ),
    sorted = samples.slice(1).sort((a, b) => a - b);
  const report = {
    environment: 'Cloud Linux headless Chromium; not physical device FPS or GPU paint benchmark',
    bars: 2500,
    smas: 8,
    drawings: 100,
    rafSamples: sorted.length,
    rafGapMedianMs: sorted[Math.floor(sorted.length * 0.5)],
    rafGapP95Ms: sorted[Math.floor(sorted.length * 0.95)],
    runtimeErrors: errors,
  };
  await writeFile('docs/performance-measurement.json', JSON.stringify(report, null, 2) + '\n');
  await info.attach('headless-raf-scheduling.json', {
    body: JSON.stringify(report, null, 2),
    contentType: 'application/json',
  });
  expect(errors).toEqual([]);
  await page.screenshot({ path: 'scratch/stress-100-drawings.png' });
});

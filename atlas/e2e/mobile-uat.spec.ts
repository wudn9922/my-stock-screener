import { test, expect, type Page } from '@playwright/test';
import { chooseDrawingTool } from './drawing-picker-helper';
import { DemoProvider } from '../src/market-data/DemoProvider';
import type { SymbolState } from '../src/storage/schema';

async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}
async function closePanel(page: Page) {
  const close = page.getByRole('button', { name: 'Close panel', exact: true });
  if (await close.isVisible()) await close.click();
}
async function addMA(page: Page, period: number) {
  await page.getByRole('button', { name: 'Manage indicators', exact: true }).click();
  await page
    .getByLabel('Indicator type', { exact: true })
    .filter({ visible: true })
    .selectOption('SMA');
  await page
    .getByLabel('SMA period', { exact: true })
    .filter({ visible: true })
    .fill(String(period));
  await page.getByRole('button', { name: 'Add SMA', exact: true }).click();
  await closePanel(page);
}
async function saved(page: Page): Promise<SymbolState> {
  return page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const r = indexedDB.open('atlas-terminal');
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
    const result = await new Promise<SymbolState>((resolve, reject) => {
      const r = db.transaction('symbols').objectStore('symbols').get('AAPL');
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
    db.close();
    return result;
  });
}
function mean(values: number[]) {
  return values.reduce((a, b) => a + b, 0) / values.length;
}
const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 });

test('passive numeric legend, directional volume MA20 and zoom actions preserve chart space', async ({
  page,
  isMobile,
}) => {
  await page.goto('/');
  await loaded(page);
  await addMA(page, 24);
  await addMA(page, 58);
  const chart = page.getByTestId('chart');
  await expect
    .poll(() =>
      page.locator('.timeframe-buttons').evaluate((strip) => {
        const button = strip.querySelector('[aria-pressed="true"]')!;
        const a = strip.getBoundingClientRect(),
          b = button.getBoundingClientRect();
        return b.left >= a.left - 1 && b.right <= a.right + 1;
      }),
    )
    .toBe(true);
  const twoHeight = (await chart.boundingBox())!.height;
  if (isMobile && page.viewportSize()!.width < 600) expect(twoHeight).toBeGreaterThan(330);
  const { bars } = await new DemoProvider().getBars('AAPL', '1D');
  for (const period of [24, 58]) {
    const row = page.locator('.indicator-chip').filter({ hasText: `SMA ${period}` });
    await expect(row.locator('[data-indicator-value]')).toHaveText(
      mean(bars.slice(-period).map((b) => b.close)).toFixed(2),
    );
    await expect(row.getByRole('button')).toHaveCount(0);
  }
  const legacy = page.locator('[data-legacy-volume]');
  await expect(legacy.locator('[data-volume-average]')).toHaveText(
    compact.format(mean(bars.slice(-20).map((b) => b.volume))),
  );
  if (!isMobile) {
    for (const label of ['Zoom in', 'Zoom out']) {
      const box = (await page.getByRole('button', { name: label, exact: true }).boundingBox())!;
      const host = (await chart.boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(
        box.x + box.width <= host.x || box.y + box.height <= host.y || box.y >= host.y + host.height,
      ).toBe(true);
    }
  } else {
    const chartHeight = (await chart.boundingBox())!.height;
    await page.getByRole('button', { name: 'Drawing Tools', exact: true }).click();
    const picker = page.getByRole('dialog', { name: 'Drawing tools', exact: true });
    await expect(picker).toBeVisible();
    for (const label of ['Zoom in', 'Zoom out']) {
      const button = picker.getByRole('button', { name: label, exact: true });
      await expect(button).toBeVisible();
      const box = (await button.boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(await button.evaluate((element) => element.closest('[data-testid="chart"]'))).toBeNull();
    }
    await page.keyboard.press('Escape');
    await expect(picker).toBeHidden();
    expect((await chart.boundingBox())!.height).toBeCloseTo(chartHeight, 0);
    await expect(page.locator('.chart-status-quick-actions')).toBeHidden();
    for (const label of ['Zoom in', 'Zoom out'])
      await expect(page.getByRole('button', { name: label, exact: true })).toHaveCount(0);
  }
  for (const period of [8, 12, 20, 32, 43, 100]) await addMA(page, period);
  await expect(page.locator('.indicator-chip')).toHaveCount(8);
  expect((await chart.boundingBox())!.height).toBeCloseTo(twoHeight, 0);
  expect((await page.locator('.indicator-chips').boundingBox())!.height).toBeLessThanOrEqual(44);
  await page.getByRole('button', { name: 'Manage indicators', exact: true }).click();
  await page
    .getByLabel('Indicator type', { exact: true })
    .filter({ visible: true })
    .selectOption('Volume');
  await page.getByRole('button', { name: 'Add Volume', exact: true }).click();
  await closePanel(page);
  await expect(legacy).toHaveCount(0);
  const vol = page.locator('.indicator-chip').filter({ hasText: 'Volume' });
  await expect(vol.locator('[data-volume-average]')).toHaveText(
    compact.format(mean(bars.slice(-20).map((b) => b.volume))),
  );
  await page.getByRole('button', { name: 'Manage indicators', exact: true }).click();
  await page.getByRole('button', { name: 'Hide Volume', exact: true }).click();
  await closePanel(page);
  await expect(vol.locator('[data-volume-average]')).toHaveText('Hidden');
  await expect(legacy).toHaveCount(0);
  await page.reload();
  await loaded(page);
  await expect(vol.locator('[data-volume-average]')).toHaveText('Hidden');
  await expect(page.locator('.indicator-chip')).toHaveCount(9);
});

test('chart focus fallback keeps the engine, dialogs, drawing locks and viewport ownership', async ({
  page,
  isMobile,
  browserName,
}) => {
  await page.addInitScript(() =>
    Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: false }),
  );
  await page.goto('/');
  await loaded(page);
  await addMA(page, 24);
  const before = (await page.getByTestId('chart').boundingBox())!;
  const host = await page.getByTestId('chart').elementHandle();
  await page.getByRole('button', { name: 'Enter chart fullscreen', exact: true }).click();
  const exit = page.getByRole('button', { name: 'Exit chart fullscreen', exact: true });
  await expect(exit).toBeVisible();
  await expect
    .poll(async () => (await page.getByTestId('chart').boundingBox())!.height)
    .toBeGreaterThan(before.height + 60);
  expect(await host!.evaluate((el) => el === document.querySelector('[data-testid="chart"]'))).toBe(
    true,
  );
  await page.getByRole('button', { name: 'Manage indicators', exact: true }).click();
  await expect(
    page.getByLabel('SMA period', { exact: true }).filter({ visible: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(exit).toBeVisible();
  await chooseDrawingTool(page, 'Horizontal Line');
  const box = (await page.getByTestId('chart').boundingBox())!;
  const x = box.x + box.width * 0.4,
    y = box.y + box.height * 0.4;
  if (isMobile && browserName === 'chromium') {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x, y, id: 1 }],
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: x + 10, y: y + 10, id: 1 }],
    });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await cdp.detach();
  } else {
    if (isMobile)
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
    await page.mouse.move(x + 10, y + 10, { steps: 4 });
    await page.mouse.up();
  }
  await expect.poll(async () => (await saved(page))?.drawings.length).toBe(1);
  await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();
  await expect.poll(async () => (await saved(page))?.drawings[0].locked).toBe(true);
  // Selection/actions must never resize the plot beneath its original anchors.
  expect((await page.getByTestId('chart').boundingBox())!.height).toBeCloseTo(box.height, 0);
  expect(await page.evaluate(() => scrollY)).toBe(0);
  if (isMobile && page.viewportSize()!.width < 600) {
    await page.setViewportSize({ width: 844, height: 390 });
    await expect(exit).toBeVisible();
    expect((await page.getByTestId('chart').boundingBox())!.height).toBeGreaterThan(200);
  }
  await exit.click();
  await expect(
    page.getByRole('button', { name: 'Enter chart fullscreen', exact: true }),
  ).toBeVisible();
  expect(await host!.evaluate((el) => el === document.querySelector('[data-testid="chart"]'))).toBe(
    true,
  );
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export settings', exact: true }).click();
  await downloading;
  await expect(page.locator('.notice')).toHaveCSS('pointer-events', 'none');
  await expect(
    page.getByRole('button', { name: 'Dismiss notification', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();
  await expect.poll(async () => (await saved(page))?.drawings[0].locked).toBe(false);
  await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();
  await expect.poll(async () => (await saved(page))?.drawings[0].locked).toBe(true);
  await page.reload();
  await loaded(page);
  expect((await saved(page)).drawings[0].locked).toBe(true);
});

test('fullscreen exit and crosshair numeric readouts use current candle then latest', async ({
  page,
  isMobile,
}) => {
  await page.goto('/');
  await loaded(page);
  await addMA(page, 24);
  await page.getByRole('button', { name: 'Enter chart fullscreen', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Exit chart fullscreen', exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('button', { name: 'Enter chart fullscreen', exact: true }),
  ).toBeVisible();
  const readout = page.locator('.indicator-chip [data-indicator-value]');
  const { bars } = await new DemoProvider().getBars('AAPL', '1D');
  const latest = mean(bars.slice(-24).map((b) => b.close)).toFixed(2);
  await expect(readout).toHaveText(latest);
  if (!isMobile) {
    const box = (await page.getByTestId('chart').boundingBox())!;
    await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.4);
    await expect(readout).not.toHaveText(latest);
    const header = (await page.getByTestId('ohlc-header').textContent())!;
    const close = /C ([\d.]+)/.exec(header)![1];
    const indices = bars.flatMap((b, i) => (b.close.toFixed(2) === close ? [i] : []));
    expect(
      indices.map((i) => mean(bars.slice(i - 23, i + 1).map((b) => b.close)).toFixed(2)),
    ).toContain(await readout.textContent());
    await page.mouse.move(0, 0);
    await expect(readout).toHaveText(latest);
  }
  const search = page.getByLabel('Symbol search', { exact: true });
  await search.fill('NVDA');
  await search.press('Enter');
  await loaded(page);
  await expect(page.locator('.indicator-chip')).toHaveCount(0);
  const nvda = await new DemoProvider().getBars('NVDA', '1D');
  await expect(page.locator('[data-legacy-volume] [data-volume-value]')).toHaveText(
    compact.format(nvda.bars.at(-1)!.volume),
  );
  await search.fill('AAPL');
  await search.press('Enter');
  await loaded(page);
  await expect(readout).toHaveText(latest);
  expect(
    await search.evaluate((input) => {
      const event = new KeyboardEvent('keydown', {
        key: 'Escape',
        bubbles: true,
        cancelable: true,
      });
      input.dispatchEvent(event);
      return event.defaultPrevented;
    }),
  ).toBe(false);
  await page.getByRole('button', { name: 'Timeframe 1H', exact: true }).click();
  await loaded(page);
  await expect(page.locator('.indicator-chip')).toHaveCount(0);
  expect((await saved(page)).indicators.map((indicator) => indicator.scope.timeframe)).toEqual([
    '1D',
  ]);
  await addMA(page, 24);
  const hourly = await new DemoProvider().getBars('AAPL', '1H');
  const hourlyLatest = mean(hourly.bars.slice(-24).map((b) => b.close)).toFixed(2);
  await expect(page.locator('.indicator-chip')).toHaveCount(1);
  await expect(readout).toHaveText(hourlyLatest);
  expect((await saved(page)).indicators.map((indicator) => indicator.scope.timeframe)).toEqual([
    '1D',
    '1H',
  ]);
  await page.getByRole('button', { name: 'Timeframe 1D', exact: true }).click();
  await loaded(page);
  await expect(page.locator('.indicator-chip')).toHaveCount(1);
  await expect(readout).toHaveText(latest);
});

test('denied browser fullscreen falls back to chart focus without losing indicator controls', async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: true });
    HTMLElement.prototype.requestFullscreen = () =>
      Promise.reject(new DOMException('Fullscreen denied', 'NotAllowedError'));
  });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/');
  await loaded(page);
  await page.getByRole('button', { name: 'Enter chart fullscreen', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Exit chart fullscreen', exact: true }),
  ).toBeVisible();
  await addMA(page, 24);
  await expect(page.locator('.indicator-chip [data-indicator-value]')).toHaveText(/\d+\.\d{2}/);
  await page.getByRole('button', { name: 'Exit chart fullscreen', exact: true }).click();
  expect(errors).toEqual([]);
});

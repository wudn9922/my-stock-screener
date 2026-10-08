import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { clickExport, expectProvider, openPanel as openWorkspacePanel, searchSymbol } from './site-helpers';
import { chooseDrawingTool, clickDrawingUtility } from './drawing-picker-helper';
import type { CDPSession } from 'playwright-core';
import type { AppSettings, SymbolState } from '../src/storage/schema';

interface SavedWorkspace {
  app: AppSettings;
  symbols: SymbolState[];
}

const panelSelectors = {
  indicators: '.indicator-panel',
  drawings: '.drawing-panel',
  backtest: '.backtest-panel',
  alerts: '.alert-panel',
  settings: '.workspace-settings',
} as const;
type PanelName = keyof typeof panelSelectors;

async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}

async function readWorkspace(page: Page): Promise<SavedWorkspace> {
  const read = () =>
    page.evaluate(async () => {
      const db = await new Promise<IDBDatabase>((resolve, reject) => {
        const request = indexedDB.open('atlas-terminal');
        request.onsuccess = () => resolve(request.result);
        request.onerror = () =>
          reject(request.error ?? new Error('Could not open workspace database'));
      });
      const transaction = db.transaction(['app', 'symbols'], 'readonly');
      const read = <T>(request: IDBRequest<T>) =>
        new Promise<T>((resolve, reject) => {
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error ?? new Error('Workspace read failed'));
        });
      const [app, symbols] = await Promise.all([
        read<AppSettings | undefined>(transaction.objectStore('app').get('settings')),
        read<SymbolState[]>(transaction.objectStore('symbols').getAll()),
      ]);
      db.close();
      if (!app) return null;
      return { app, symbols };
    });
  await expect.poll(async () => (await read()) !== null).toBe(true);
  return (await read())!;
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

async function closePanel(page: Page) {
  const close = page.getByRole('button', { name: '關閉面板', exact: true });
  if (await close.isVisible()) await close.click();
}

async function openPanel(page: Page, panel: PanelName) {
  await openWorkspacePanel(page, panel);
  await expect(page.locator(panelSelectors[panel])).toBeVisible();
}

async function switchSymbol(page: Page, symbol: string) {
  await closePanel(page);
  await searchSymbol(page, symbol);
  await expect(page.getByTestId('active-symbol')).toHaveText(symbol);
  await loaded(page);
}

async function addIndicator(page: Page, type: 'SMA' | 'EMA' | 'Volume', period?: number) {
  await openPanel(page, 'indicators');
  await page.getByLabel('指標類型', { exact: true }).selectOption(type);
  if (period !== undefined)
    await page.getByLabel(`${type} 週期`, { exact: true }).fill(String(period));
  await page.getByRole('button', { name: `新增 ${type}`, exact: true }).click();
}

function createDrag(page: Page, isMobile: boolean, browserName: string) {
  let cdp: CDPSession | null = null;
  const ready =
    isMobile && browserName === 'chromium'
      ? page
          .context()
          .newCDPSession(page)
          .then((session) => {
            cdp = session;
          })
      : isMobile
        ? page.evaluate(() => {
            for (const type of ['pointerdown', 'pointermove', 'pointerup']) {
              document.addEventListener(
                type,
                (event) => Object.defineProperty(event, 'pointerType', { value: 'touch' }),
                { capture: true },
              );
            }
          })
        : Promise.resolve();

  return {
    ready,
    async drag(x: number, y: number, toX: number, toY: number) {
      await ready;
      if (cdp) {
        await cdp.send('Input.dispatchTouchEvent', {
          type: 'touchStart',
          touchPoints: [{ x, y, id: 1 }],
        });
        for (let step = 1; step <= 8; step++) {
          await cdp.send('Input.dispatchTouchEvent', {
            type: 'touchMove',
            touchPoints: [{ x: x + ((toX - x) * step) / 8, y: y + ((toY - y) * step) / 8, id: 1 }],
          });
        }
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        return;
      }
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(toX, toY, { steps: 8 });
      await page.mouse.up();
    },
  };
}

async function markerPixelCount(page: Page): Promise<number> {
  return page.getByTestId('chart').evaluate((host) => {
    // This reads only visible chart pixels; it does not inspect the chart engine or marker state.
    const markerColors = [
      [102, 219, 187], // public BUY/COVER marker color
      [240, 155, 170], // public EXIT/SELL marker color
    ];
    let count = 0;
    for (const canvas of host.querySelectorAll('canvas')) {
      const context = canvas.getContext('2d');
      if (!context || canvas.width === 0 || canvas.height === 0) continue;
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i + 3] < 150) continue;
        if (
          markerColors.some(
            ([red, green, blue]) =>
              Math.abs(pixels[i] - red) <= 12 &&
              Math.abs(pixels[i + 1] - green) <= 12 &&
              Math.abs(pixels[i + 2] - blue) <= 12,
          )
        )
          count++;
      }
    }
    return count;
  });
}

test('V1 research journey keeps per-symbol indicators, drawings, alerts and backups isolated', async ({
  page,
  isMobile,
  browserName,
}, info) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(15_000);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await loaded(page);
  await expect(page.getByTestId('active-symbol')).toHaveText('AAPL');
  await expectProvider(page, 'demo');

  await addIndicator(page, 'SMA', 24);
  const sma24 = page.getByTestId('ma-24');
  await sma24.getByRole('button', { name: '設定 SMA 24', exact: true }).click();
  await sma24.getByLabel('週期', { exact: true }).fill('26');
  await sma24.getByRole('combobox', { name: '價格來源', exact: true }).selectOption('high');
  await sma24.getByLabel('顏色', { exact: true }).fill('#67b8ee');
  await sma24.getByRole('combobox', { name: 'SMA 24 線寬', exact: true }).selectOption('3');
  await sma24.getByRole('button', { name: '儲存', exact: true }).click();
  const sma26 = page.getByTestId('ma-26');
  await expect(sma26).toContainText('最高價');
  await sma26.getByRole('button', { name: '鎖定 SMA 26', exact: true }).click();
  await expect(sma26.getByRole('button', { name: '設定 SMA 26', exact: true })).toBeDisabled();
  await expect(sma26.getByRole('button', { name: '移除 SMA 26', exact: true })).toBeDisabled();
  await sma26.getByRole('button', { name: '隱藏 SMA 26', exact: true }).click();
  await sma26.getByRole('button', { name: '顯示 SMA 26', exact: true }).click();

  await addIndicator(page, 'EMA', 58);
  await page.getByRole('button', { name: '鎖定 EMA 58', exact: true }).click();
  await addIndicator(page, 'Volume');
  const aaplVolume = page.locator('.indicator-panel .ma-card').filter({ hasText: '成交量' });
  await expect(aaplVolume).toContainText('量');
  await aaplVolume.getByRole('button', { name: '設定 成交量', exact: true }).click();
  await aaplVolume.getByLabel('顏色', { exact: true }).fill('#77ddee');
  await aaplVolume.getByRole('button', { name: '儲存', exact: true }).click();
  await expect
    .poll(
      async () =>
        (await readSymbol(page))?.indicators.find((indicator) => indicator.type === 'Volume')
          ?.color,
    )
    .toBe('#77ddee');
  await aaplVolume.getByRole('button', { name: '鎖定 成交量', exact: true }).click();
  await expect(
    aaplVolume.getByRole('button', { name: '設定 成交量', exact: true }),
  ).toBeDisabled();
  await aaplVolume.getByRole('button', { name: '隱藏 成交量', exact: true }).click();
  await aaplVolume.getByRole('button', { name: '顯示 成交量', exact: true }).click();
  await page.getByLabel('指標組合名稱', { exact: true }).fill('AAPL mix');
  await page.getByRole('button', { name: '儲存目前指標為組合', exact: true }).click();
  await expect.poll(async () => (await readWorkspace(page)).app.indicatorPresets.length).toBe(1);
  await aaplVolume.getByRole('button', { name: '解鎖 成交量', exact: true }).click();
  await expect(
    aaplVolume.getByRole('button', { name: '移除 成交量', exact: true }),
  ).toBeEnabled();
  await aaplVolume.getByRole('button', { name: '移除 成交量', exact: true }).click();
  await expect(page.locator('.indicator-panel .ma-card').filter({ hasText: '成交量' })).toHaveCount(
    0,
  );
  await addIndicator(page, 'Volume');
  const restoredVolume = page.locator('.indicator-panel .ma-card').filter({ hasText: '成交量' });
  await restoredVolume.getByRole('button', { name: '鎖定 成交量', exact: true }).click();
  await restoredVolume.getByRole('button', { name: '隱藏 成交量', exact: true }).click();
  await restoredVolume.getByRole('button', { name: '顯示 成交量', exact: true }).click();

  const drag = createDrag(page, isMobile, browserName);
  await openPanel(page, 'settings');
  await page.getByLabel('預設樣式的繪圖工具', { exact: true }).selectOption('horizontal');
  await page.getByLabel('預設線條顏色', { exact: true }).fill('#b47cff');
  await page.getByLabel('預設線寬', { exact: true }).selectOption('3');
  await page.getByLabel('預設線條樣式', { exact: true }).selectOption('dotted');
  await page.getByRole('button', { name: '儲存新畫線預設樣式', exact: true }).click();
  await expect
    .poll(async () => (await readWorkspace(page)).app.drawingDefaults.horizontal?.color)
    .toBe('#b47cff');
  await closePanel(page);

  await chooseDrawingTool(page, '水平線');
  const chart = (await page.getByTestId('chart').locator('canvas').first().boundingBox())!;
  await drag.drag(
    chart.x + chart.width * 0.68,
    chart.y + chart.height * 0.48,
    chart.x + chart.width * 0.75,
    chart.y + chart.height * 0.52,
  );
  await expect.poll(async () => (await readSymbol(page))?.drawings.length ?? 0).toBe(1);
  const aapl = (await readSymbol(page))!;
  expect(aapl.drawings[0]).toMatchObject({
    type: 'horizontal',
    locked: false,
    style: { color: '#b47cff', lineWidth: 3, lineStyle: 'dotted' },
  });
  await page.getByRole('button', { name: '鎖定所選畫線', exact: true }).click();
  await expect.poll(async () => (await readSymbol(page))?.drawings[0].locked).toBe(true);
  await page.getByRole('button', { name: '取消選取', exact: true }).click();

  await openPanel(page, 'alerts');
  const alertPanel = page.locator('.alert-panel');
  await alertPanel.getByLabel('提醒價格', { exact: true }).fill('10000');
  await alertPanel.getByRole('button', { name: '建立提醒', exact: true }).click();
  const levelAlert = alertPanel.locator('.alert-card').filter({ hasText: '價格穿越 10000' });
  await expect(levelAlert).toContainText('啟用中');
  await levelAlert.getByRole('button', { name: '停用提醒', exact: true }).click();
  await expect(levelAlert).toContainText('已停用');
  await levelAlert.getByRole('button', { name: '啟用提醒', exact: true }).click();
  await expect(levelAlert).toContainText('啟用中');
  await levelAlert.getByRole('button', { name: '停用提醒', exact: true }).click();

  await alertPanel.getByLabel('提醒價格', { exact: true }).fill('10100');
  await alertPanel.getByRole('button', { name: '建立提醒', exact: true }).click();
  const disposableAlert = alertPanel
    .locator('.alert-card')
    .filter({ hasText: '價格穿越 10100' });
  await expect(disposableAlert).toBeVisible();
  await disposableAlert.getByRole('button', { name: '刪除', exact: true }).click();
  await expect(disposableAlert).toHaveCount(0);

  await alertPanel.getByLabel('提醒條件', { exact: true }).selectOption('drawing');
  await alertPanel
    .getByLabel('提醒參考畫線', { exact: true })
    .selectOption(aapl.drawings[0].id);
  await alertPanel.getByRole('button', { name: '建立提醒', exact: true }).click();
  const drawingAlert = alertPanel.locator('.alert-card').filter({ hasText: '穿越畫線' });
  await expect(drawingAlert).toContainText('啟用中');
  await drawingAlert.getByRole('button', { name: '停用提醒', exact: true }).click();
  await drawingAlert.getByRole('button', { name: '啟用提醒', exact: true }).click();
  await expect(drawingAlert).toContainText('啟用中');
  await expect.poll(async () => (await readWorkspace(page)).app.alerts.length).toBe(2);

  await switchSymbol(page, 'NVDA');
  await addIndicator(page, 'SMA', 43);
  await page.getByRole('button', { name: '鎖定 SMA 43', exact: true }).click();
  await addIndicator(page, 'EMA', 56);
  const ema56 = page.getByTestId('ma-56');
  await ema56.getByRole('button', { name: '設定 EMA 56', exact: true }).click();
  await ema56.getByRole('combobox', { name: '價格來源', exact: true }).selectOption('low');
  await ema56.getByLabel('顏色', { exact: true }).fill('#f0a266');
  await ema56.getByRole('combobox', { name: 'EMA 56 線寬', exact: true }).selectOption('4');
  await ema56.getByRole('button', { name: '儲存', exact: true }).click();
  await addIndicator(page, 'Volume');
  await page
    .locator('.indicator-panel .ma-card')
    .filter({ hasText: '成交量' })
    .getByRole('button', { name: '鎖定 成交量', exact: true })
    .click();
  await page.getByLabel('指標組合名稱', { exact: true }).fill('NVDA mix');
  await page.getByRole('button', { name: '儲存目前指標為組合', exact: true }).click();
  await expect.poll(async () => (await readWorkspace(page)).app.indicatorPresets.length).toBe(2);
  const nvdaBeforeApply = (await readSymbol(page, 'NVDA'))!;
  await page.getByLabel('指標組合', { exact: true }).selectOption({ label: 'AAPL mix' });
  await page.getByRole('button', { name: '套用組合', exact: true }).click();
  await expect.poll(async () => (await readSymbol(page, 'NVDA'))?.indicators.length ?? 0).toBe(6);
  const nvdaAfterApply = (await readSymbol(page, 'NVDA'))!;
  expect(nvdaAfterApply.indicators.map((indicator) => indicator.symbol)).toEqual(
    Array.from({ length: 6 }, () => 'NVDA'),
  );
  expect(new Set(nvdaAfterApply.indicators.map((indicator) => indicator.id)).size).toBe(6);
  expect(nvdaAfterApply.indicators.slice(0, 3)).toEqual(nvdaBeforeApply.indicators);
  expect(nvdaAfterApply.indicators[0].locked).toBe(true);
  expect(nvdaAfterApply.indicators.slice(3).map((indicator) => indicator.type)).toEqual([
    'SMA',
    'EMA',
    'Volume',
  ]);
  expect(nvdaAfterApply.indicators.slice(3).every((indicator) => indicator.locked)).toBe(true);
  await switchSymbol(page, 'AAPL');

  await openPanel(page, 'backtest');
  const backtest = page.locator('.backtest-panel');
  await expect(backtest).toContainText('模擬資料');
  await backtest.getByLabel('快線類型', { exact: true }).selectOption('EMA');
  await backtest.getByLabel('快線週期', { exact: true }).fill('1');
  await backtest.getByLabel('慢線類型', { exact: true }).selectOption('SMA');
  await backtest.getByLabel('慢線週期', { exact: true }).fill('2');
  await backtest.getByRole('button', { name: '執行回測', exact: true }).click();
  await expect(backtest.locator('.backtest-results')).toBeVisible();
  await expect(backtest.locator('summary').filter({ hasText: '訊號' })).toHaveText(/^訊號（[1-9]\d*）$/);
  await expect(backtest.locator('.backtest-metrics dd').nth(0)).toHaveText(/^\d+$/);
  await expect(backtest.locator('.backtest-metrics dd').nth(2)).toHaveText(/^-?\d+\.\d+%$/);
  await page.screenshot({ path: `scratch/v1-backtest-${info.project.name}.png` });
  if (!info.project.name.includes('ipad')) await closePanel(page);
  await expect.poll(() => markerPixelCount(page)).toBeGreaterThan(0);

  if (info.project.name === 'ipad-webkit') {
    await closePanel(page);
    await clickDrawingUtility(page, '縮小圖表');
    await expect.poll(async () => (await readSymbol(page))?.preferences.views['1D']).toBeDefined();
    await openPanel(page, 'alerts');
    const sheet = page.locator('.sheet-backdrop.tablet-context');
    await expect(sheet).toHaveCSS('pointer-events', 'none');
    await expect(page.locator('.bottom-sheet')).toHaveCSS('pointer-events', 'auto');
    expect(
      await page.getByTestId('chart').evaluate((element) => element.closest('[inert]') !== null),
    ).toBe(false);
    const chartBox = (await page.getByTestId('chart').locator('canvas').first().boundingBox())!;
    const sheetBox = (await page.locator('.bottom-sheet').boundingBox())!;
    const startX = chartBox.x + chartBox.width * 0.5;
    expect(startX).toBeLessThan(sheetBox.x - 10);
    const before = (await readSymbol(page))!.preferences.views['1D'];
    await drag.drag(
      startX,
      chartBox.y + chartBox.height * 0.26,
      startX + 45,
      chartBox.y + chartBox.height * 0.26,
    );
    await expect
      .poll(async () => (await readSymbol(page))?.preferences.views['1D'])
      .not.toEqual(before);
  }

  await openPanel(page, 'settings');
  await page.getByLabel('預設樣式的繪圖工具', { exact: true }).selectOption('horizontal');
  await expect(page.getByLabel('預設線條顏色', { exact: true })).toHaveValue('#b47cff');
  await expect(page.getByLabel('預設線寬', { exact: true })).toHaveValue('3');
  await expect(page.getByLabel('預設線條樣式', { exact: true })).toHaveValue('dotted');
  await page.getByLabel('啟用除錯模式', { exact: true }).uncheck();
  await expect
    .poll(async () => (await readWorkspace(page)).app.workspace.rightTab)
    .toBe('settings');
  await expect.poll(async () => (await readWorkspace(page)).app.workspace.debug).toBe(false);
  await closePanel(page);

  await page.reload();
  await loaded(page);
  let saved = await readWorkspace(page);
  expect(saved.app.indicatorPresets.map((preset) => preset.name)).toEqual(['AAPL mix', 'NVDA mix']);
  expect(saved.app.workspace).toMatchObject({
    rightOpen: true,
    rightTab: 'settings',
    debug: false,
  });
  expect(saved.app.drawingDefaults.horizontal).toMatchObject({
    color: '#b47cff',
    lineWidth: 3,
    lineStyle: 'dotted',
  });
  expect(saved.app.alerts).toHaveLength(2);
  expect(saved.app.alerts.find((alert) => alert.kind === 'level')?.enabled).toBe(false);
  expect(saved.app.alerts.find((alert) => alert.kind === 'drawing')?.enabled).toBe(true);
  expect(saved.symbols.find((state) => state.symbol === 'AAPL')?.drawings[0].locked).toBe(true);
  expect(saved.symbols.find((state) => state.symbol === 'AAPL')?.indicators).toHaveLength(3);
  expect(saved.symbols.find((state) => state.symbol === 'NVDA')?.indicators).toHaveLength(6);
  await openPanel(page, 'settings');
  await expect(page.locator('.workspace-settings')).toBeVisible();
  await closePanel(page);

  const baseline = await readWorkspace(page);
  const invalid = JSON.stringify({ version: 5, app: baseline.app, symbols: baseline.symbols });
  await page.getByLabel('設定檔', { exact: true }).setInputFiles({
    name: 'invalid-settings.json',
    mimeType: 'application/json',
    buffer: Buffer.from(invalid),
  });
  await expect(page.getByRole('status')).toContainText('匯入:');
  expect(await readWorkspace(page)).toEqual(baseline);

  const downloadEvent = page.waitForEvent('download');
  await clickExport(page);
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toMatch(/atlas-settings.*\.json/);
  const path = await download.path();
  expect(path).not.toBeNull();
  const exported = JSON.parse(readFileSync(path!, 'utf8')) as {
    version: number;
    app: AppSettings;
    symbols: SymbolState[];
  };
  expect(exported.version).toBe(4);
  expect(exported.app.indicatorPresets.map((preset) => preset.name)).toEqual([
    'AAPL mix',
    'NVDA mix',
  ]);

  await openPanel(page, 'settings');
  await page.getByLabel('啟用除錯模式', { exact: true }).check();
  await expect(page.getByTestId('debug')).toBeVisible();
  await closePanel(page);
  await page.getByLabel('設定檔', { exact: true }).setInputFiles(path!);
  await expect(page.getByRole('status')).toContainText('設定已還原');
  await expect.poll(async () => (await readWorkspace(page)).app.workspace.debug).toBe(false);
  await page.reload();
  await loaded(page);
  saved = await readWorkspace(page);
  expect(saved.app).toEqual(exported.app);
  expect(saved.symbols).toEqual(exported.symbols);
  expect(await page.getByTestId('debug').count()).toBe(0);
  await openPanel(page, 'alerts');
  await expect(page.locator('.alert-panel .alert-card')).toHaveCount(2);
  await expect(
    page.locator('.alert-panel .alert-card').filter({ hasText: '價格穿越 10000' }),
  ).toContainText('已停用');
  await expect(
    page.locator('.alert-panel .alert-card').filter({ hasText: '穿越畫線' }),
  ).toContainText('啟用中');
  await expect(page.getByTestId('save-state')).toHaveText('已儲存於本機');
  expect(errors).toEqual([]);
});

import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { accountDatabase, mockDeviceAccount } from './account-fixture';
import { chooseDrawingTool } from '../e2e/drawing-picker-helper';
import { searchSymbol } from '../e2e/site-helpers';
import type { SymbolState } from '../src/storage/schema';

async function saved(page: Page, symbol = 'AAPL'): Promise<SymbolState | undefined> {
  return page.evaluate(async ({ database, symbol }) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(database);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return new Promise<SymbolState | undefined>((resolve, reject) => {
      const request = db.transaction('symbols', 'readonly').objectStore('symbols').get(symbol);
      request.onsuccess = () => { resolve(request.result); db.close(); };
      request.onerror = () => { reject(request.error); db.close(); };
    });
  }, { database: accountDatabase(), symbol });
}

async function loaded(page: Page) {
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await expect(page.locator('.chart-loading')).toHaveCount(0);
}

test('hourly, weekly and monthly MAs and drawings fix independently and survive reload and symbol switches', async ({ page }, testInfo) => {
  await mockDeviceAccount(page); await mockSite(page);
  await page.goto('./?page=chart&symbol=AAPL&tf=1H');
  for (const [timeframe, period] of [['1H', 13], ['1W', 34], ['1M', 8]] as const) {
    await page.getByRole('button', { name: `週期 ${timeframe}`, exact: true }).click();
    await loaded(page);
    await expect(page.locator('.indicator-chip')).toHaveCount(0);
    await page.getByTestId('ma-recommendation-toggle').click();
    const editor = page.locator('.timeframe-ma-editor');
    await expect(editor.getByRole('region', { name: `${timeframe} 週期專屬設定` })).toContainText('變更自動保存');
    await editor.getByRole('spinbutton', { name: 'SMA 週期', exact: true }).fill(String(period));
    await editor.getByRole('button', { name: '新增 SMA', exact: true }).click();
    await page.getByTestId('ma-recommendation-toggle').click();
    const chart = page.getByTestId('chart'); await chart.scrollIntoViewIfNeeded();
    await chooseDrawingTool(page, '水平線');
    const box = (await chart.boundingBox())!;
    await page.mouse.move(box.x + box.width * .45, box.y + box.height * .45);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * .55, box.y + box.height * .46, { steps: 6 });
    await page.mouse.up();
    await expect(page.getByRole('toolbar', { name: '所選畫線操作' })).toBeVisible();
    await page.getByRole('button', { name: '取消選取', exact: true }).click();
    await page.getByTestId('ma-recommendation-toggle').click();
    await expect(editor).toContainText('均線 1 條／畫線 1 條');
    await editor.getByRole('button', { name: '固定此週期設定', exact: true }).click();
    await expect(editor.getByRole('button', { name: '解除固定此週期', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(editor.getByRole('button', { name: `設定 SMA ${period}`, exact: true })).toBeDisabled();
    await page.getByTestId('ma-recommendation-toggle').click();
  }
  await expect.poll(async () => (await saved(page))?.drawings.filter(line => line.locked).length).toBe(3);
  const before = (await saved(page))!;
  expect(before.indicators.map(ma => [ma.scope.timeframe, ma.period, ma.locked, ma.lineWidth])).toEqual([['1H', 13, true, 2], ['1W', 34, true, 2], ['1M', 8, true, 2]]);
  expect(before.drawings.map(line => line.scope.timeframes)).toEqual([['1H'], ['1W'], ['1M']]);
  await page.reload(); await loaded(page);
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveText(['SMA 8']);
  await searchSymbol(page, 'NVDA'); await loaded(page);
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
  await expect(page.locator('.indicator-chip')).toHaveCount(0);
  await searchSymbol(page, 'AAPL'); await loaded(page);
  await page.getByRole('button', { name: '週期 1W', exact: true }).click(); await loaded(page);
  await page.getByTestId('ma-recommendation-toggle').click();
  const editor = page.locator('.timeframe-ma-editor');
  await expect(editor.getByRole('button', { name: '解除固定此週期', exact: true })).toBeVisible();
  await editor.getByRole('button', { name: '解除固定此週期', exact: true }).click();
  await expect.poll(async () => (await saved(page))?.drawings.filter(line => line.locked).length).toBe(2);
  expect((await saved(page))!.drawings.map(line => line.points)).toEqual(before.drawings.map(line => line.points));
  expect(await page.locator('#main').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath('timeframe-fixed-studies.png') });
});

test('visitors see a RAM-only warning and reload does not persist timeframe studies', async ({ page }) => {
  await mockSite(page);
  await page.goto('./?page=chart&symbol=AAPL&tf=1W'); await loaded(page);
  await page.getByTestId('ma-recommendation-toggle').click();
  const editor = page.locator('.timeframe-ma-editor');
  await expect(editor).toContainText('僅本次瀏覽，重新開啟即消失');
  await editor.getByRole('button', { name: '新增 SMA', exact: true }).click();
  await editor.getByRole('button', { name: '固定此週期設定', exact: true }).click();
  await page.reload(); await loaded(page);
  await expect(page.locator('.indicator-chip')).toHaveCount(0);
});

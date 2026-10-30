import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { accessEnvelope, installLineAccount } from './account-fixture';
import type { ParameterStock } from '../src/cloud/settingsApi';
import type { SavedWatchlist } from '../src/watchlists/model';

const stock = (ticker: string, group_id: string, id: number, periods: number[]): ParameterStock => ({ id, ticker, group_id,
  ma1: periods[0] ?? 0, ma2: periods[1] ?? 0, ma3: periods[2] ?? 0, ma4: periods[3] ?? 0 });

async function cloudParameters(page: Page, initial: ParameterStock[]) {
  await mockSite(page); await installLineAccount(page);
  const stocks = [...initial];
  const writes: Record<string, unknown>[] = [];
  const state = { failSave: false, failDelete: false, failRead: false, version: 0 };
  const lists = new Map<string, { id: string; definition: SavedWatchlist; updated_at: string }>();
  await page.route('**/functions/v1/chart-drawings', route => route.fulfill({ json: { ok: true, records: [] } }));
  await page.route('**/functions/v1/user-settings', async route => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    if (body.action === 'access.get') return route.fulfill({ json: accessEnvelope(undefined, 'cloud') });
    const fail = () => route.fulfill({ status: 503, json: { ok: false, error: '測試連線失敗' } });
    let data: unknown = [];
    if (body.action === 'stocks.list') { if (state.failRead) return fail(); data = stocks; }
    if (body.action === 'stocks.save') {
      writes.push(body); if (state.failSave) return fail();
      const row = stock(String(body.ticker), String(body.groupId), Number(body.id ?? 100), body.mas as number[]);
      const index = stocks.findIndex(entry => entry.id === row.id);
      if (index < 0) stocks.push(row); else stocks[index] = row;
      data = [{ id: row.id }];
    }
    if (body.action === 'stocks.delete') {
      writes.push(body); if (state.failDelete) return fail();
      const index = stocks.findIndex(entry => entry.id === body.id);
      if (index >= 0) stocks.splice(index, 1);
    }
    if (body.action === 'watchlists.list') data = [...lists.values()];
    if (body.action === 'watchlists.save') {
      const definition = body.definition as SavedWatchlist;
      data = { id: definition.id, definition, updated_at: `2026-10-29T10:00:${String(++state.version).padStart(2, '0')}Z` };
      lists.set(definition.id, data as { id: string; definition: SavedWatchlist; updated_at: string });
    }
    return route.fulfill({ json: { ok: true, data } });
  });
  return { stocks, writes, state };
}
const chipNames = (page: Page) => page.locator('.indicator-chip .indicator-name');

async function movingAveragePixels(host: ReturnType<Page['locator']>) {
  return host.evaluate(element => [...element.querySelectorAll('canvas')].filter(canvas => canvas.clientHeight > 100).reduce((sum, canvas) => {
    const pixels = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i] === 240 && pixels[i + 1] === 179 && pixels[i + 2] === 91 && pixels[i + 3]! > 200) sum++;
    return sum;
  }, 0));
}

async function openFixed(page: Page, symbol = '2330.TW') {
  await page.getByTestId('ma-recommendation-toggle').click();
  await page.getByTestId('ma-recommendation').getByRole('button', { name: '固定為個股參數' }).click();
  return page.getByRole('dialog', { name: `${symbol} 固定均線參數` });
}

test('cloud grouped MAs render on a fresh chart; confirm updates the original row and consolidates the dormant duplicate', async ({ page }, testInfo) => {
  const cloud = await cloudParameters(page, [stock('2330.TW', 'ungrouped', 9, [20, 50]), stock('2330', 'tw_g1', 4, [29, 34, 73])]);
  await page.goto('./?page=chart&symbol=2330.TW&tf=1D');
  await expect(chipNames(page)).toHaveText(['SMA 29', 'SMA 34', 'SMA 73']);
  await expect.poll(() => movingAveragePixels(page.getByTestId('chart'))).toBeGreaterThan(20);
  let dialog = await openFixed(page);
  await expect(dialog.getByRole('textbox', { name: '個股固定均線參數' })).toHaveValue('29, 34, 73');
  await expect(dialog).toContainText('20 / 50'); await expect(dialog).toContainText('原有分組');
  await page.screenshot({ path: testInfo.outputPath('original-grouped-parameters.png') });
  await dialog.getByRole('button', { name: '取消編輯' }).click();
  expect(cloud.writes).toHaveLength(0); expect(cloud.stocks).toHaveLength(2);
  await page.getByTestId('ma-recommendation').getByRole('button', { name: '固定為個股參數' }).click();
  dialog = page.getByRole('dialog', { name: '2330.TW 固定均線參數' });
  await dialog.getByRole('textbox', { name: '個股固定均線參數' }).fill('13, 34');
  await dialog.getByRole('button', { name: '確認並儲存到雲端' }).click();
  await expect(dialog).toBeHidden();
  expect(cloud.writes).toEqual([
    expect.objectContaining({ action: 'stocks.save', id: 4, ticker: '2330', groupId: 'tw_g1', mas: [13, 34] }),
    expect.objectContaining({ action: 'stocks.delete', id: 9 }),
  ]);
  expect(cloud.stocks).toEqual([stock('2330', 'tw_g1', 4, [13, 34])]);
  await expect(chipNames(page)).toHaveText(['SMA 34', 'SMA 13']);
  await page.reload(); await page.getByTestId('stock-tab-chart').click();
  await expect(chipNames(page)).toHaveText(['SMA 34', 'SMA 13']);
});

test('editing an original grouped stock remains effective even when legacy dormant duplicates exist', async ({ page }) => {
  const cloud = await cloudParameters(page, [stock('NVDA', 'us_g1', 4, [29, 34, 73]), stock('NVDA', 'ungrouped', 9, [20, 50])]);
  await page.goto('./?page=console');
  const table = page.getByRole('table', { name: '個人股票參數' });
  await expect(table.locator('tbody tr')).toHaveCount(2);
  await table.locator('tbody tr').first().getByRole('button', { name: '編輯', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '編輯 NVDA 均線參數' });
  await expect(dialog.getByRole('textbox', { name: '編輯股票均線參數' })).toHaveValue('29, 34, 73');
  await expect(dialog).toContainText('清除無分組重複筆');
  await dialog.getByRole('textbox', { name: '編輯股票均線參數' }).fill('17, 43');
  await dialog.getByRole('button', { name: '確認編輯' }).click();
  await expect(table.locator('tbody tr')).toHaveCount(1);
  expect(cloud.stocks).toEqual([stock('NVDA', 'us_g1', 4, [17, 43])]);
  await page.goto('./?page=chart&symbol=NVDA&tf=1D');
  await expect(chipNames(page)).toHaveText(['SMA 17', 'SMA 43']);
});

test('a failed save leaves both rows intact and a load error prevents saving guessed defaults', async ({ page }) => {
  const original = [stock('2330.TW', 'tw_g1', 4, [29, 34]), stock('2330.TW', 'ungrouped', 9, [20, 50])];
  const cloud = await cloudParameters(page, original);
  await page.goto('./?page=chart&symbol=2330.TW&tf=1D');
  await expect(chipNames(page)).toHaveText(['SMA 29', 'SMA 34']);
  let dialog = await openFixed(page);
  cloud.state.failSave = true;
  await dialog.getByRole('textbox', { name: '個股固定均線參數' }).fill('13');
  await dialog.getByRole('button', { name: '確認並儲存到雲端' }).click();
  await expect(dialog.getByRole('alert')).toContainText('測試連線失敗');
  expect(cloud.stocks).toEqual(original);
  expect(cloud.writes.filter(write => write.action === 'stocks.delete')).toHaveLength(0);
  await dialog.getByRole('button', { name: '取消編輯' }).click();
  cloud.state.failRead = true;
  await page.getByTestId('ma-recommendation').getByRole('button', { name: '固定為個股參數' }).click();
  dialog = page.getByRole('dialog', { name: '2330.TW 固定均線參數' });
  await expect(dialog.getByRole('alert')).toContainText('測試連線失敗');
  await expect(dialog.getByRole('button', { name: '確認並儲存到雲端' })).toBeDisabled();
});

test('continuous charts use saved stock MAs before group MAs and reset to six months; index strip is removed', async ({ page }, testInfo) => {
  await cloudParameters(page, [stock('AAPL', 'ungrouped', 4, [13, 34]), stock('NVDA', 'us_g1', 5, [29, 73])]);
  await page.goto('./?page=watchlist');
  await page.getByRole('button', { name: '建立手動清單', exact: true }).click();
  await page.getByRole('textbox', { name: '手動清單名稱' }).fill('六個月持續看圖');
  await page.getByRole('combobox', { name: '手動清單市場' }).selectOption('US');
  await page.getByRole('textbox', { name: '手動清單股票' }).fill('AAPL, NVDA, MSFT');
  await page.getByRole('button', { name: '儲存手動清單', exact: true }).click();
  const board = page.getByRole('region', { name: '自選行情' });
  await expect(board.getByRole('group', { name: '主要指數' })).toHaveCount(0);
  await board.getByRole('button', { name: '連續看全部股票圖表（3 檔）' }).click();
  const cards = board.locator('.stock-chart-card');
  for (const [index, periods] of [[0, '13 / 34'], [1, '29 / 73'], [2, '20 / 50 / 200']] as const) {
    const card = cards.nth(index); await card.scrollIntoViewIfNeeded();
    await expect(card.locator('.mini-chart')).toHaveAttribute('data-status', 'ready');
    await expect(card.locator('.stock-chart-date')).toContainText(`SMA ${periods}`);
    await expect.poll(() => movingAveragePixels(card)).toBeGreaterThan(20);
    await expect(card.locator('.stock-chart-date')).toContainText('近 6 個月');
    const count = card.getByRole('status', { name: '顯示 K 線根數' });
    await expect.poll(async () => Number(await count.textContent())).toBeGreaterThan(120);
    if (index === 0) await page.screenshot({ path: testInfo.outputPath('six-month-saved-ma.png') });
    const initial = Number(await count.textContent());
    expect(initial).toBeLessThan(140);
    await card.getByRole('button', { name: '放大圖表', exact: true }).click();
    await expect.poll(async () => Number(await count.textContent())).toBeLessThan(initial);
    await card.getByRole('button', { name: '重設', exact: true }).click();
    await expect(count).toHaveText(String(initial));
  }
  expect(await page.locator('#main').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(0);
});

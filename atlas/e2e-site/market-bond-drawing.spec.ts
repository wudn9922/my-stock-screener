import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { accountDatabase, mockDeviceAccount } from './account-fixture';
import { chooseDrawingTool } from '../e2e/drawing-picker-helper';
import type { Drawing } from '../src/drawing/DrawingModel';

const parameters = JSON.parse(readFileSync(new URL('../data/yield-band-params.json', import.meta.url), 'utf8'));
const usRates = JSON.parse(readFileSync(new URL('../tests/fixtures/rates/rates.sample.json', import.meta.url), 'utf8'));
usRates.policy = { upper: 4, lower: 3.75, date: '2026-10-27' };
usRates.curves.nominal.find((curve: { key: string }) => curve.key === 'now').values[usRates.tenors.indexOf('10Y')] = 4.99;
const history = readFileSync(new URL('../tests/fixtures/rates/rates-history.sample.json', import.meta.url), 'utf8');
const dates = Array.from({ length: 350 }, (_, i) => new Date(Date.UTC(2020, 0, 3 + i * 7)).toISOString().slice(0, 10)).filter(day => day <= '2026-10-29');
const block = { dates, series: { JGB10: dates.map((_, i) => 2 + Math.sin(i / 20)), BOJ_TARGET: dates.map(() => 1.25) } };
// Synthetic shape emitted by build_bond_research.py; all input files are local mocked JSON.
const japan = { version: 1, generatedAt: '2026-10-29T12:00:00Z', asOf: '2026-10-29',
  tenYear: { value: 2.83, date: '2026-10-29', source: 'MOF' }, policy: { value: 1.25, date: '2026-09-24', source: 'BOJ' },
  missing: [], weekly: block, daily: block };
async function bondSources(page: Page, brokenJapan = false) {
  await mockSite(page, { report: 'missing' });
  await page.route('**/report/rates.json', route => route.fulfill({ json: usRates }));
  await page.route('**/report/rates-history.json', route => route.fulfill({ contentType: 'application/json', body: history }));
  await page.route('**/report/jp-rates.json', route => brokenJapan ? route.fulfill({ status: 404 }) : route.fulfill({ json: japan }));
  await page.route('**/report/yield-band-params.json', route => route.fulfill({ json: parameters }));
}

test('global includes Japanese spreads and both countries use the attached reasonable-band formulas without a daily report', async ({ page }, testInfo) => {
  await bondSources(page);
  const sourceRequests: string[] = [];
  page.on('request', request => { if (/fred\.stlouisfed|mof\.go\.jp|boj\.or\.jp/.test(request.url())) sourceRequests.push(request.url()); });
  await page.goto('./?page=world&view=jp-bonds');
  await expect(page.getByRole('navigation', { name: '市場分頁' }).getByRole('link')).toHaveText(['大盤', '全球', '題材']);
  await expect(page.getByRole('group', { name: '世界頁面' }).getByRole('button', { name: '日債利差', exact: true })).toHaveAttribute('aria-pressed', 'true');
  const spread = page.getByRole('region', { name: '日本國債利差', exact: true });
  await expect(spread.locator('.rates-headline')).toContainText('+1.58%');
  await expect(spread.locator('canvas').first()).toBeVisible();
  await spread.getByRole('button', { name: '1年', exact: true }).click();
  await expect(spread.getByRole('button', { name: '1年', exact: true })).toHaveAttribute('aria-pressed', 'true');
  for (const name of ['存續期間情境分析', '長天期美債 ETF 比較']) await expect(page.getByRole('heading', { name })).toHaveCount(0);
  const jpBand = page.getByRole('region', { name: '日本10年債合理區間' });
  await expect(jpBand.locator('.yield-band-values b')).toHaveText(['1.14%', '2.10%', '3.06%']);
  await expect(jpBand).toContainText('區間內偏上');
  await jpBand.getByRole('combobox', { name: '合理區間算法' }).selectOption('spread');
  await expect(jpBand.locator('.yield-band-values b')).toHaveText(['1.10%', '2.15%', '3.19%']);
  await page.screenshot({ path: testInfo.outputPath('japan-bond-spread-band.png') });
  await page.getByRole('group', { name: '世界頁面' }).getByRole('button', { name: '美債利率', exact: true }).click();
  const usBand = page.getByRole('region', { name: '美國10年債合理區間' });
  await expect(usBand.locator('.yield-band-values b')).toHaveText(['3.52%', '5.11%', '6.71%']);
  await expect(usBand).toContainText('區間中段');
  await expect(page.getByRole('heading', { name: '長天期美債 ETF 比較' })).toBeVisible();
  expect(sourceRequests).toEqual([]);
  expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
});

test('missing Japan reports show the dated attachment baseline and missing models do not break existing US sections', async ({ page }) => {
  await bondSources(page, true);
  await page.route('**/report/yield-band-params.json', route => route.fulfill({ status: 503, body: 'unavailable' }));
  await page.goto('./?page=world&view=jp-bonds');
  await expect(page.getByRole('status').filter({ hasText: '基準快照' })).toContainText('並非即時行情');
  await expect(page.getByRole('region', { name: '日本10年債合理區間' })).toContainText('附檔基準參數');
  await expect(page.getByText('歷史利差尚未產生', { exact: false })).toBeVisible();
  await page.getByRole('group', { name: '世界頁面' }).getByRole('button', { name: '美債利率', exact: true }).click();
  await expect(page.getByRole('region', { name: '美國10年債合理區間' }).locator('.yield-band-values b')).toHaveCount(3);
  await expect(page.getByRole('heading', { name: '存續期間情境分析' })).toBeVisible();
});

async function drawings(page: Page): Promise<Drawing[]> {
  return page.evaluate(async database => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open(database); req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error);
    });
    const result = await new Promise<Drawing[]>((resolve, reject) => {
      const req = db.transaction('symbols', 'readonly').objectStore('symbols').get('AAPL');
      req.onsuccess = () => resolve(req.result?.drawings ?? []); req.onerror = () => reject(req.error);
    });
    db.close(); return result;
  }, accountDatabase());
}
async function linePoint(page: Page) {
  return page.getByTestId('chart').evaluate(host => {
    for (const canvas of host.querySelectorAll('canvas')) {
      if (canvas.clientWidth < 100 || canvas.clientHeight < 100) continue;
      const values = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
      for (let y = 0; y < canvas.height; y++) {
        let count = 0;
        for (let x = 0; x < canvas.width; x++) {
          const i = (y * canvas.width + x) * 4;
          if (values[i] === 92 && values[i + 1] === 169 && values[i + 2] === 255 && values[i + 3]! > 200) count++;
        }
        if (count > canvas.width / 3) {
          const rect = canvas.getBoundingClientRect();
          return { x: rect.x + rect.width * .45, y: rect.y + y / (canvas.height / rect.height) };
        }
      }
    }
    return null;
  });
}
async function tap(page: Page, point: { x: number; y: number }) {
  if (page.context().browser()?.browserType().name() === 'chromium' && page.viewportSize()!.width < 600) {
    const session = await page.context().newCDPSession(page);
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ ...point, id: 1 }] });
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await session.detach();
  } else await page.mouse.click(point.x, point.y);
}

test('tapping a 2px drawing opens a compact chart-local menu for lock, edit and delete without taking bottom space', async ({ page }, testInfo) => {
  await mockDeviceAccount(page); await mockSite(page);
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  const chart = page.getByTestId('chart'); const box = (await chart.boundingBox())!;
  await chooseDrawingTool(page, '水平線');
  await expect(page.locator('.drawing-gesture-hint')).toContainText('按住 → 拖曳 → 放開');
  expect((await chart.boundingBox())!.height).toBeCloseTo(box.height, 0);
  await page.mouse.move(box.x + box.width * .5, box.y + box.height * .5);
  await page.mouse.down(); await page.mouse.move(box.x + box.width * .51, box.y + box.height * .51); await page.mouse.up();
  const menu = page.getByRole('toolbar', { name: '所選畫線操作' });
  await expect(menu).toBeVisible();
  await expect.poll(async () => (await drawings(page)).length).toBe(1);
  expect((await chart.boundingBox())!.height).toBeCloseTo(box.height, 0);
  expect((await drawings(page))[0].style).toMatchObject({ lineWidth: 2, widthMode: 'pixels' });
  await menu.getByRole('button', { name: '取消選取', exact: true }).click();
  const height = (await chart.boundingBox())!.height;
  await expect.poll(() => linePoint(page)).not.toBeNull();
  const line = (await linePoint(page))!;
  // WebKit rounds synthetic mouse events to whole CSS pixels.
  const tappedPoint = { x: Math.round(line.x), y: Math.round(line.y) };
  await tap(page, tappedPoint);
  await expect(menu).toBeVisible();
  const menuBox = (await menu.boundingBox())!, chartBox = (await chart.boundingBox())!;
  expect(menuBox.width).toBeLessThanOrEqual(210); expect(menuBox.height).toBeLessThanOrEqual(56);
  expect(menuBox.x).toBeGreaterThanOrEqual(chartBox.x); expect(menuBox.y).toBeGreaterThanOrEqual(chartBox.y);
  expect(menuBox.x + menuBox.width).toBeLessThanOrEqual(chartBox.x + chartBox.width);
  expect(menuBox.y + menuBox.height).toBeLessThanOrEqual(chartBox.y + chartBox.height);
  expect(menuBox.y + menuBox.height).toBeCloseTo(tappedPoint.y - 14, 0);
  expect(chartBox.height).toBeCloseTo(height, 0);
  await expect(page.locator('.chart-status button')).toHaveCount(0);
  for (const button of await menu.getByRole('button').all()) expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await page.screenshot({ path: testInfo.outputPath('drawing-quick-menu.png') });
  await menu.getByRole('button', { name: '鎖定所選畫線', exact: true }).click();
  await expect(menu.getByRole('button', { name: '刪除所選畫線', exact: true })).toBeDisabled();
  await expect.poll(async () => (await drawings(page))[0].locked).toBe(true);
  await menu.getByRole('button', { name: '鎖定所選畫線', exact: true }).click();
  await menu.getByRole('button', { name: '畫線設定', exact: true }).click();
  const editor = page.getByRole('dialog', { name: '修改畫線', exact: true });
  await expect(editor.getByRole('combobox', { name: '線寬', exact: true })).toHaveValue('2');
  await editor.getByRole('combobox', { name: '線寬', exact: true }).selectOption('3');
  await editor.getByRole('button', { name: '儲存畫線', exact: true }).click();
  await expect.poll(async () => (await drawings(page))[0].style.lineWidth).toBe(3);
  await editor.getByRole('button', { name: '完成', exact: true }).click();
  await menu.getByRole('button', { name: '刪除所選畫線', exact: true }).click();
  await expect(menu).toBeHidden();
  await expect.poll(async () => (await drawings(page)).length).toBe(0);
});

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';

const body = readFileSync(resolve(import.meta.dirname, '../tests/fixtures/breakouts/breakouts.sample.json'), 'utf8');
async function mockBreakouts(page: Page, payload: unknown = JSON.parse(body), status = 200) {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/breakouts-history.json', route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({ version: 1, intervalDays: 20, snapshots: [] }),
  }));
  await page.route('**/my-stock-screener/report/breakouts.json', route => route.fulfill({
    status, contentType: typeof payload === 'string' ? 'text/html' : 'application/json',
    body: typeof payload === 'string' ? payload : JSON.stringify(payload),
  }));
}
const card = (page: Page) => page.locator('section.breakout-card');
const row = (page: Page, method = 'swingHigh') => card(page).locator(`.bo-row[data-method="${method}"]`);
const picker = (page: Page) => card(page).getByRole('group', { name: '勝率計算方式' });
const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);

test('three entry cards compare four outcomes with fixed cohorts on desktop and phone', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(String(error)));
  await mockBreakouts(page);
  await page.goto('./?page=markets&market=tw');
  const section = card(page);
  await expect(section.getByRole('heading', { name: /全市場進場成功率/ })).toContainText('台股');
  await expect(section.locator('.bo-asof')).toHaveText('資料日 2026/10/09');
  const order = await page.locator('.markets-side > section.card').evaluateAll(nodes => nodes.map(node => node.className));
  expect(order.slice(0, 2)).toEqual(['card breakout-card', 'card breadth-card']);
  await expect(section.locator('.bo-row')).toHaveCount(3);
  await expect(picker(page).getByRole('button')).toHaveCount(4);
  await expect(row(page).locator('.bo-win')).toContainText('50.0%');
  await expect(row(page).locator('.bo-ratio')).toContainText('110／220');
  await expect(row(page).locator('.bo-metrics')).toContainText('75');
  await expect(row(page)).not.toContainText('54.0%'); // legacy end-of-report profit rate is never shown

  for (const [label, value, wins] of [['隔日再創高', '45.0%', '99／220'], ['先觸發判定', '55.0%', '121／220'], ['漲幅與續強', '40.0%', '88／220']]) {
    await picker(page).getByRole('button', { name: new RegExp(label!) }).click();
    await expect(row(page).locator('.bo-win')).toContainText(value!);
    await expect(row(page).locator('.bo-ratio')).toContainText(wins!);
    await expect(picker(page).getByRole('button', { name: new RegExp(label!) })).toHaveAttribute('aria-pressed', 'true');
  }
  await section.getByText('比較全部四種勝率', { exact: true }).click();
  const table = section.getByRole('table');
  await expect(table.locator('tbody td')).toHaveCount(12);
  await table.getByRole('button', { name: '先觸發', exact: true }).click();
  await expect(row(page).locator('.bo-win')).toContainText('55.0%');
  await expect(row(page)).toContainText('先後不明 3 次');
  await section.getByText('比較全部四種勝率', { exact: true }).click();

  const windows = section.getByRole('group', { name: '統計窗口' });
  await windows.getByRole('button', { name: '近1週' }).click();
  await expect(row(page)).toContainText('等待5日觀察');
  await expect(row(page, 'box20')).toContainText('窗口內沒有新突破');
  await expect(row(page).locator('.bo-win')).toHaveText('—成功率');
  await windows.getByRole('button', { name: '近1月' }).click();
  await expect(row(page).locator('.bo-win')).toContainText('55.0%');
  await section.getByText('計算與資料說明', { exact: true }).click();
  const methodology = section.locator('.bo-method');
  for (const text of ['完整 5 個市場交易日', '先後不明則保守判失敗', '前高仍保留', '隔日可再次突破', 'ATR(20)', '不設10日冷卻', '沒有訊號的股票不進分母', '存活者偏差', '門檻 14%', '全市場名單 1,000 檔', '歷史缺漏 20 檔'])
    await expect(methodology).toContainText(text);
  await section.getByText('計算與資料說明', { exact: true }).click();
  for (const button of await picker(page).getByRole('button').all()) expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await noOverflow(page);
  expect(errors).toEqual([]);
  // Capture the complete panel after interaction checks at the actual device size.
  // The site's internal scroller otherwise clips a panel taller than the viewport.
  await page.setViewportSize({ width: page.viewportSize()!.width, height: 1800 });
  await section.scrollIntoViewIfNeeded();
  await section.screenshot({ path: testInfo.outputPath('four-outcome-card.png') });
});

test('United States has independent rates and unsupported markets omit the panel', async ({ page }) => {
  await mockBreakouts(page);
  await page.goto('./?page=markets&market=us');
  await expect(card(page).getByRole('heading', { name: /全市場進場成功率/ })).toContainText('美股');
  await expect(row(page).locator('.bo-win')).toContainText('44.5%');
  await picker(page).getByRole('button', { name: /先觸發判定/ }).click();
  await expect(row(page).locator('.bo-win')).toContainText('50.0%');
  await expect(card(page).locator('.bo-foot')).toContainText('全市場美股普通股與 ADR');
  await expect(card(page)).not.toContainText('S&P 500');
  for (const name of ['日本', '韓國', '歐洲']) {
    await page.getByRole('tab', { name: new RegExp(name) }).click();
    await expect(card(page)).toHaveCount(0);
  }
});

test('old reports wait for the new model instead of relabelling their rates', async ({ page }) => {
  const old = JSON.parse(body);
  delete old.params.successModel;
  old.params.swingMethod = 'atr-reversal-v1';
  await mockBreakouts(page, old);
  await page.goto('./?page=markets&market=tw');
  await expect(card(page).getByRole('status')).toContainText('等待新版全市場報表');
  await expect(card(page).locator('.bo-win')).toHaveCount(0);
});

test('missing and malformed files preserve the rest of the markets page', async ({ page }) => {
  await mockBreakouts(page, 'not found', 404);
  await page.goto('./?page=markets&market=tw');
  await expect(card(page).getByRole('status')).toHaveText('突破勝率資料尚未產生。');
  await expect(page.locator('.index-card')).toHaveCount(2);
  await expect(page.locator('section.breadth-card')).toBeVisible();
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockBreakouts(page, '<html>broken</html>');
  await page.reload();
  await expect(card(page).getByRole('status')).toHaveText('突破勝率格式無法辨識。');
});

test('a missing method or rule never borrows another percentage', async ({ page }) => {
  const missing = JSON.parse(body);
  delete missing.markets.tw.windows['1m'].strategies.ema20;
  delete missing.markets.tw.windows['1m'].strategies.swingHigh.comparison.rules.firstHit;
  await mockBreakouts(page, missing);
  await page.goto('./?page=markets&market=tw');
  await expect(row(page, 'ema20')).toContainText('報表尚未提供此方法');
  await expect(row(page).locator('.bo-win')).toContainText('50.0%');
  await picker(page).getByRole('button', { name: /先觸發判定/ }).click();
  await expect(row(page)).toContainText('此口徑資料尚未提供');
  await expect(row(page).locator('.bo-win')).not.toContainText('50.0%');
});

test('inconsistent cohort counts are rejected without falling back to legacy profit rates', async ({ page }) => {
  const bad = JSON.parse(body);
  bad.markets.tw.windows['1m'].strategies.swingHigh.comparison.completeN = 999;
  await mockBreakouts(page, bad);
  await page.goto('./?page=markets&market=tw');
  await expect(row(page)).toContainText('此口徑資料尚未提供');
  await expect(row(page).locator('.bo-win')).not.toContainText('54.0%');
});

test('partial-market scope remains hidden', async ({ page }) => {
  const partial = JSON.parse(body);
  delete partial.markets.us.scope;
  await mockBreakouts(page, partial);
  await page.goto('./?page=markets&market=us');
  await expect(card(page).getByRole('status')).toContainText('舊的部分市場樣本不列入');
  await expect(card(page).locator('.bo-win')).toHaveCount(0);
});

test('saved 20-day test dates show their own original results and return to the latest report', async ({ page }) => {
  await mockBreakouts(page);
  await page.route('**/my-stock-screener/report/breakouts-history.json', route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({ version: 1, intervalDays: 20, snapshots: [
      { asOf: '2026-09-19', generatedAt: '2026-09-19T10:00:00Z', markets: { tw: '2026-09-19', us: '2026-09-18' } },
      { asOf: '2026-08-30', generatedAt: '2026-08-30T10:00:00Z', markets: { tw: '2026-08-30' } },
    ] }),
  }));
  const archive = JSON.parse(body);
  archive.generatedAt = '2026-09-19T10:00:00Z';
  archive.markets.tw.asOf = '2026-09-19';
  archive.markets.us.asOf = '2026-09-18';
  const comparison = archive.markets.tw.windows['1m'].strategies.swingHigh.comparison;
  comparison.rules.hold5 = { wins: 66, failures: 154, evaluatedN: 220, winRate: 30 };
  await page.route('**/my-stock-screener/report/breakouts-archive/2026-09-19.json', route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify(archive),
  }));
  await page.route('**/my-stock-screener/report/breakouts-archive/2026-08-30.json', route => route.fulfill({ status: 404, body: 'not found' }));
  await page.goto('./?page=markets&market=tw');
  const dates = card(page).getByRole('combobox', { name: '進場成功率測試日期' });
  await expect(dates.locator('option')).toHaveCount(3);
  await expect(row(page).locator('.bo-win')).toContainText('50.0%');
  await dates.selectOption('2026-09-19');
  await expect(card(page).locator('.bo-asof')).toHaveText('資料日 2026/09/19');
  await expect(row(page).locator('.bo-win')).toContainText('30.0%');
  await expect(row(page).locator('.bo-ratio')).toContainText('66／220');
  await expect(card(page).locator('.bo-history')).toContainText('2026/09/19');
  await dates.selectOption('2026-08-30');
  await expect(card(page).getByRole('status')).toContainText('突破勝率資料尚未產生');
  await expect(card(page).locator('.bo-win')).toHaveCount(0);
  await dates.selectOption('latest');
  await expect(card(page).locator('.bo-asof')).toHaveText('資料日 2026/10/09');
  await expect(row(page).locator('.bo-win')).toContainText('50.0%');
  await noOverflow(page);
});

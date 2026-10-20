import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';

// Synthetic contract fixture (not market data): breakouts.json.
const body = readFileSync(resolve(import.meta.dirname, '../tests/fixtures/breakouts/breakouts.sample.json'), 'utf8');

type Mode = 'ok' | 'missing' | 'broken';

async function mockBreakouts(page: Page, mode: Mode = 'ok') {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/breakouts.json', (route) =>
    mode === 'missing'
      ? route.fulfill({ status: 404, body: 'not found' })
      : mode === 'broken'
        ? route.fulfill({
            status: 200,
            contentType: 'text/html',
            body: '<!doctype html><title>x</title>',
          })
        : route.fulfill({ status: 200, contentType: 'application/json', body }),
  );
}

const card = (page: Page) => page.locator('section.breakout-card');
const row = (page: Page, label: string) =>
  card(page).locator('.bo-row', {
    has: page.locator('.bo-label', { hasText: new RegExp(`^${label}$`) }),
  });
const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);

test('Taiwan shows breakout win rates above the breadth card, with window switch and methodology', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockBreakouts(page);
  await page.goto('./?page=markets&market=tw');
  const section = card(page);
  await expect(section.getByRole('heading', { name: /全市場進場成功率/ })).toContainText('台股');
  await expect(section.locator('.bo-asof')).toHaveText('資料日 2026/10/09');
  // Placed directly above the breadth card in the side column.
  const order = await page.locator('.markets-side > section.card').evaluateAll((nodes) => nodes.map((node) => node.className));
  expect(order.slice(0, 2)).toEqual(['card breakout-card', 'card breadth-card']);

  // Three method families; prior-high lookbacks remain selectable within the first method.
  const windows = section.getByRole('group', { name: '統計窗口' });
  await expect(windows.getByRole('button', { name: '近1月' })).toHaveAttribute('aria-pressed', 'true');
  await expect(section.locator('.bo-list .bo-row')).toHaveCount(3);
  const high20 = row(page, '突破新高進場法');
  await expect(high20.locator('.bo-win')).toContainText('54.0%');
  await expect(high20).toContainText('成功／成熟訊號樣本');
  await expect(section).not.toContainText('同日市場');
  await expect(high20).toContainText('250／300');
  await expect(high20.locator('.bo-tag')).toHaveCount(0);
  await expect(high20).toContainText('135／250');
  await expect(row(page, '突破20EMA進場法')).toContainText('66.7%');
  await expect(section.locator('.bo-sub').first()).toContainText('成功樣本／成熟訊號樣本');

  // 近1週: a low sample is flagged, unmatured and empty rows show a friendly note instead of a rate.
  await windows.getByRole('button', { name: '近1週' }).click();
  await expect(windows.getByRole('button', { name: '近1週' })).toHaveAttribute('aria-pressed', 'true');
  await expect(high20.locator('.bo-win')).toContainText('40.0%');
  await section.getByLabel('新高回看期間').selectOption('60');
  await expect(high20.locator('.bo-tag')).toHaveText('樣本偏少，參考性低');
  await section.getByLabel('新高回看期間').selectOption('120');
  await expect(high20).toContainText('尚無成熟樣本');
  await expect(high20.locator('.bo-win')).toHaveCount(0);
  await expect(row(page, '箱型突破進場法')).toContainText('窗口內沒有新突破');

  // Methodology spells out definitions, maturity, base rate and biases.
  const method = section.locator('details', {
    has: page.locator('summary', { hasText: '口徑說明' }),
  });
  await method.locator('summary').click();
  for (const text of ['不含當日', '已滿 3 個交易日', '沒有訊號的股票不進分母', '存活者偏差', '門檻 14%', '全市場名單 1,000 檔', '歷史缺漏 20 檔'])
    await expect(method).toContainText(text);
  await noOverflow(page);
  expect(errors).toEqual([]);
});

test('United States uses its own market and other markets have no breakout card', async ({ page }) => {
  await mockBreakouts(page);
  await page.goto('./?page=markets&market=us');
  await expect(card(page).getByRole('heading', { name: /全市場進場成功率/ })).toContainText('美股');
  await expect(row(page, '突破新高進場法').locator('.bo-win')).toContainText('47.3%');
  await expect(card(page).locator('.bo-foot')).toContainText('全市場美股普通股與 ADR');
  await expect(card(page)).not.toContainText('S&P 500');
  for (const name of ['日本', '韓國', '歐洲']) {
    await page.getByRole('tab', { name: new RegExp(name) }).click();
    await expect(page.getByRole('tab', { name: new RegExp(name) })).toHaveAttribute('aria-selected', 'true');
    await expect(card(page)).toHaveCount(0);
  }
  await page.getByRole('tab', { name: /台灣/ }).click();
  await expect(card(page)).toBeVisible();
});

test('missing or unreadable file shows a friendly state without breaking the page', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockBreakouts(page, 'missing');
  await page.goto('./?page=markets&market=tw');
  await expect(card(page).getByRole('status')).toHaveText('突破勝率資料尚未產生。');
  await expect(page.locator('.index-card')).toHaveCount(2);
  await expect(page.locator('section.breadth-card')).toBeVisible();
  await page.unrouteAll({ behavior: 'ignoreErrors' });

  await mockBreakouts(page, 'broken');
  await page.reload();
  await expect(card(page).getByRole('status')).toHaveText('突破勝率格式無法辨識。');
  await expect(page.locator('.index-card')).toHaveCount(2);
  expect(errors).toEqual([]);
});

test('legacy reports do not substitute another strategy for missing EMA results', async ({ page }) => {
  await mockSite(page);
  const legacy = JSON.parse(body);
  legacy.params.strategies = legacy.params.strategies.filter((meta: { key: string }) => meta.key !== 'ema20');
  for (const market of Object.values(legacy.markets) as {
    windows: Record<string, { strategies: Record<string, unknown> }>;
  }[]) {
    for (const window of Object.values(market.windows)) delete window.strategies.ema20;
  }
  await page.route('**/my-stock-screener/report/breakouts.json', (route) => route.fulfill({ json: legacy }));
  await page.goto('./?page=markets&market=tw');
  await expect(row(page, '突破20EMA進場法')).toContainText('報表尚未提供此方法');
  await expect(row(page, '突破20EMA進場法').locator('.bo-win')).toHaveCount(0);
  await expect(row(page, '突破新高進場法').locator('.bo-win')).toContainText('54.0%');
});

test('a legacy partial-market report is never presented as full-market success', async ({ page }) => {
  await mockSite(page);
  const partial = JSON.parse(body);
  for (const market of Object.values(partial.markets) as { scope?: string }[]) delete market.scope;
  await page.route('**/my-stock-screener/report/breakouts.json', route => route.fulfill({ json: partial }));
  await page.goto('./?page=markets&market=us');
  await expect(card(page).getByRole('status')).toContainText('舊的部分市場樣本不列入');
  await expect(card(page).locator('.bo-win')).toHaveCount(0);
});

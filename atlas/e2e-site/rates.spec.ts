import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';

// Synthetic contract fixtures (not market data): rates.json / rates-history.json.
const fixtures = resolve(import.meta.dirname, '../tests/fixtures/rates');
const ratesBody = readFileSync(resolve(fixtures, 'rates.sample.json'), 'utf8');
const historyBody = readFileSync(resolve(fixtures, 'rates-history.sample.json'), 'utf8');

type Mode = 'ok' | 'missing' | 'broken' | 'history-down';

async function mockRates(page: Page, mode: Mode = 'ok') {
  // The daily report is unavailable on purpose: the rates view must not depend on it.
  await mockSite(page, { report: 'missing' });
  await page.route('**/my-stock-screener/report/rates.json', (route) =>
    mode === 'missing'
      ? route.fulfill({ status: 404, body: 'not found' })
      : mode === 'broken'
        ? route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>x</title>' })
        : route.fulfill({ status: 200, contentType: 'application/json', body: ratesBody }),
  );
  await page.route('**/my-stock-screener/report/rates-history.json', (route) =>
    mode === 'history-down'
      ? route.fulfill({ status: 503, body: 'unavailable' })
      : route.fulfill({ status: 200, contentType: 'application/json', body: historyBody }),
  );
}

const card = (page: Page, title: string | RegExp) => page.locator('section.rates-card', { has: page.getByRole('heading', { name: title }) });

test('rates view renders every section without the daily report', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockRates(page);
  await page.goto('./?page=rates');
  await expect(page).toHaveURL(/page=world&view=rates/);
  await expect(page.locator('.rates-key')).toHaveCount(6);
  await expect(page.locator('.rates-key').first()).toContainText('%');
  for (const title of [/^利差/, /存續期間情境分析/, /長天期美債 ETF 比較/])
    await expect(card(page, title)).toBeVisible();
  for (const title of ['殖利率曲線', '實質利率與通膨預期', '白話說明'])
    await expect(card(page, title)).toHaveCount(0);
  // History-driven charts render (lightweight-charts canvases).
  await expect(card(page, '利差').locator('canvas').first()).toBeVisible();
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
});

test('spreads, scenario slider and ETF comparison respond to controls', async ({ page }) => {
  await mockRates(page);
  await page.goto('./?page=world&view=rates');
  const spread = card(page, '利差');
  const selectors = spread.getByRole('group', { name: '選擇利差' });
  await expect(selectors.getByRole('button')).toHaveCount(2);
  await expect(selectors.getByRole('button', { name: '10Y−Fed' })).toHaveAttribute('aria-pressed', 'true');
  await expect(spread).toContainText('Fed 目標區間中點');
  await selectors.getByRole('button', { name: '10Y−3M' }).click();
  await expect(selectors.getByRole('button', { name: '10Y−3M' })).toHaveAttribute('aria-pressed', 'true');
  await expect(spread.locator('.card-head small')).toContainText('10 年殖利率減 3 個月殖利率');

  const scenario = card(page, '存續期間情境分析');
  const tlt = scenario.locator('.rates-bar-row', { hasText: 'TLT' });
  const before = await tlt.locator('.rates-bar-value').textContent();
  await scenario.getByRole('button', { name: '-200bp' }).click();
  await expect(tlt.locator('.rates-bar-value')).not.toHaveText(before ?? '');
  await expect(tlt.locator('.rates-bar-value')).toContainText('+');
  await expect(scenario.locator('.rates-note.warn')).toHaveCount(0);
  await expect(scenario.locator('tbody tr', { hasText: 'TMF' })).toContainText('單日');
  await expect(scenario.locator('table tbody tr')).toHaveCount(5);
  await expect(scenario.locator('.rates-formula')).toContainText('½');

  const etf = card(page, '長天期美債 ETF 比較');
  await expect(etf.locator('.rates-compare-row')).toHaveCount(5);
  await expect(etf.getByRole('group', { name: '比較項目' })).toHaveCount(0);
  await expect(etf.getByRole('button', { name: '利率敏感度' })).toHaveCount(0);
  await expect(etf.getByRole('heading', { name: '總報酬走勢' })).toHaveCount(0);
  await expect(etf.locator('.rates-compare-row').first()).toContainText('%');
  await expect(etf.locator('.rates-facts')).toHaveCount(5);
  await expect(etf.locator('.rates-static').first()).toContainText('待核對');
  await etf.getByRole('button', { name: '開啟 TLT 圖表' }).click();
  await expect(page).toHaveURL(/page=chart&symbol=TLT/);
});

test('history failure leaves the summary sections usable', async ({ page }) => {
  await mockRates(page, 'history-down');
  await page.goto('./?page=world&view=rates');
  await expect(page.locator('.rates-key')).toHaveCount(6);
  await expect(card(page, '利差').locator('.rates-section-state')).toContainText('暫時無法顯示');
  await expect(card(page, '存續期間情境分析').locator('.rates-bar-row')).toHaveCount(5);
});

test('missing and unreadable rates.json show friendly states', async ({ page }) => {
  await mockRates(page, 'missing');
  await page.goto('./?page=world&view=rates');
  await expect(page.getByText('資料尚未產生')).toBeVisible();
  await mockRates(page, 'broken');
  await page.goto('./?page=world&view=rates');
  await expect(page.getByText('美債利率資料暫時無法顯示')).toBeVisible();
});

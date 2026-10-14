import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';

const financials = (file: string) => readFileSync(resolve(import.meta.dirname, '../tests/fixtures/financials', file), 'utf8');
const fixtures: Record<string, string> = { AAPL: financials('us-full.json'), '2330.TW': financials('tsmc-twd.json') };

const iso = (date: Date) => date.toISOString().slice(0, 10);
/** `count` weekday dates ending at the latest weekday on or before today (so the data is never "old"). */
function weekdays(count: number, endOffsetDays = 0) {
  const days: string[] = [];
  const cursor = new Date();
  cursor.setUTCDate(cursor.getUTCDate() - endOffsetDays);
  while (days.length < count) {
    if (cursor.getUTCDay() !== 0 && cursor.getUTCDay() !== 6) days.unshift(iso(cursor));
    cursor.setUTCDate(cursor.getUTCDate() - 1);
  }
  return days;
}
function fridays(count: number) {
  const days: string[] = [];
  const cursor = new Date();
  while (cursor.getUTCDay() !== 5) cursor.setUTCDate(cursor.getUTCDate() - 1);
  for (let i = 0; i < count; i++) {
    days.unshift(iso(cursor));
    cursor.setUTCDate(cursor.getUTCDate() - 7);
  }
  return days;
}

function chipsFile(options: { stale?: boolean; tpexUnavailable?: boolean } = {}) {
  const foreignDates = options.stale ? weekdays(25, 40) : weekdays(25);
  const holderDates = options.stale ? fridays(10).map((day) => `${Number(day.slice(0, 4)) - 1}${day.slice(4)}`) : fridays(10);
  const foreignSeries = foreignDates.map((_, index) => Number((72 + index * 0.05).toFixed(2)));
  const holderSeries = holderDates.map((_, index) => 1_210_000 - index * 700);
  const last = foreignSeries.length - 1;
  return {
    version: 1,
    generatedAt: new Date().toISOString(),
    sources: {
      twse: { label: '證交所', asOf: foreignDates[last], status: 'ok', message: null },
      tpex: { label: '櫃買中心', asOf: null, status: options.tpexUnavailable ? 'unavailable' : 'ok', message: null },
      tdcc: { label: '集保結算所', asOf: holderDates.at(-1), status: 'ok', message: null },
    },
    axes: { foreign: { twse: foreignDates, tpex: [] }, holders: holderDates },
    stocks: {
      '2330.TW': {
        foreign: { pct: foreignSeries[last], asOf: foreignDates[last], d1: 0.05, d1Date: foreignDates[last - 1], m1: 1.2, m1Date: foreignDates[2], series: foreignSeries },
        holders: { count: holderSeries.at(-1), asOf: holderDates.at(-1), wow: -700, wowDate: holderDates.at(-2), series: holderSeries },
      },
      '6488.TWO': {
        holders: { count: 21_000, asOf: holderDates.at(-1), wow: 40, wowDate: holderDates.at(-2), series: holderDates.map((_, index) => 20_000 + index * 100) },
      },
    },
  };
}

async function mockChips(page: Page, mode: 'ok' | 'missing' | 'garbled' | ReturnType<typeof chipsFile> = chipsFile()) {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/financials/*.json', (route) => {
    const key = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop()!.replace(/\.json$/, ''));
    const body = fixtures[key];
    return body ? route.fulfill({ status: 200, contentType: 'application/json', body }) : route.fulfill({ status: 404, body: 'not found' });
  });
  const requests: string[] = [];
  await page.route('**/my-stock-screener/report/tw-chips.json', (route) => {
    requests.push(route.request().url());
    if (mode === 'missing') return route.fulfill({ status: 404, body: 'not found' });
    if (mode === 'garbled') return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>x</title>' });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mode === 'ok' ? chipsFile() : mode) });
  });
  return requests;
}

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => {
    const main = document.getElementById('main')!;
    return Math.max(document.scrollingElement!.scrollWidth - innerWidth, main.scrollWidth - main.clientWidth);
  });

test('Taiwan stock shows foreign ownership and shareholder count cards', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockChips(page);
  await page.goto('./?page=financials&symbol=2330.TW');
  const section = page.locator('.fin-chips-section');
  await expect(section).toBeVisible();
  await expect(section.getByRole('heading', { name: '籌碼' })).toBeVisible();
  const foreign = section.locator('[data-chip="foreign"]');
  await expect(foreign).toContainText('外資持股比例');
  await expect(foreign).toContainText('證交所');
  await expect(foreign.locator('.fin-big')).toHaveText(/^\d+\.\d{2}$/);
  await expect(foreign.locator('[data-chip-change="d1"]')).toContainText('較前一交易日');
  await expect(foreign.locator('[data-chip-change="d1"]')).toContainText('+0.05 個百分點');
  await expect(foreign.locator('[data-chip-change="m1"]')).toContainText('近 1 月');
  await expect(foreign.locator('.chip-spark svg path')).toHaveCount(1);
  await expect(foreign.locator('.chip-def')).toContainText('持有股數 ÷ 發行股數');
  const holders = section.locator('[data-chip="holders"]');
  await expect(holders).toContainText('總股東人數');
  await expect(holders).toContainText('集保結算所');
  await expect(holders.locator('.fin-big')).toHaveText(/^1,2\d\d,\d{3}$/);
  await expect(holders.locator('[data-chip-change="wow"]')).toContainText('較上週');
  await expect(holders.locator('[data-chip-change="wow"]')).toContainText('-700 人');
  await expect(holders.locator('.chip-spark svg path')).toHaveCount(1);
  await expect(holders.locator('.chip-def')).toContainText('第 17 級');
  await expect(section.locator('.fin-chip.warn')).toHaveCount(0);
  // The financial cards are still there and the page does not scroll sideways.
  await expect(page.locator('.fin-card').first()).toBeVisible();
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  await section.scrollIntoViewIfNeeded();
  expect(errors).toEqual([]);
});

test('old data gets a 資料較舊 hint', async ({ page }) => {
  await mockChips(page, chipsFile({ stale: true }));
  await page.goto('./?page=financials&symbol=2330.TW');
  await expect(page.locator('[data-chip="foreign"] .fin-chip.warn')).toContainText('資料較舊');
  await expect(page.locator('[data-chip="holders"] .fin-chip.warn')).toContainText('資料較舊');
});

test('OTC stock with unavailable TPEx source: friendly empty foreign card, holders still shown', async ({ page }) => {
  await mockChips(page, chipsFile({ tpexUnavailable: true }));
  await page.goto('./?page=financials&symbol=6488.TWO');
  const foreign = page.locator('[data-chip="foreign"]');
  await expect(foreign).toContainText('櫃買中心');
  await expect(foreign.locator('.chip-empty')).toContainText('暫時無法取得');
  await expect(page.locator('[data-chip="holders"] .fin-big')).toHaveText('21,000');
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
});

test('Taiwan stock missing from the file shows an empty state', async ({ page }) => {
  await mockChips(page);
  await page.goto('./?page=financials&symbol=2317.TW');
  await expect(page.locator('.fin-chips-section .chip-empty')).toContainText('沒有籌碼資料');
});

test('US stock never shows chips and never requests the file', async ({ page }) => {
  const requests = await mockChips(page);
  await page.goto('./?page=financials&symbol=AAPL');
  await expect(page.locator('.fin-card').first()).toBeVisible();
  await expect(page.locator('.fin-chips-section')).toHaveCount(0);
  expect(requests).toEqual([]);
});

test('missing or garbled tw-chips.json does not break the page', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockChips(page, 'missing');
  await page.goto('./?page=financials&symbol=2330.TW');
  await expect(page.locator('.fin-chips-section .chip-empty')).toContainText('尚未產生');
  await expect(page.locator('.fin-card').first()).toBeVisible();
  await page.unroute('**/my-stock-screener/report/tw-chips.json');
  await page.route('**/my-stock-screener/report/tw-chips.json', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>x</title>' }));
  await page.goto('./?page=financials&symbol=2330.TW&p=q');
  await expect(page.locator('.fin-chips-section .chip-empty')).toContainText('無法辨識');
  await expect(page.locator('.fin-card').first()).toBeVisible();
  expect(errors).toEqual([]);
});

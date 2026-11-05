import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { mockSite, yahooChart } from './mocks';

const calendar = JSON.parse(readFileSync(resolve(import.meta.dirname, '../tests/fixtures/market-calendar.json'), 'utf8'));

async function setup(page: Page, date: string, options: { symbol?: string; calendar?: '404' | 'invalid'; failQuote?: boolean } = {}) {
  await page.clock.install({ time: new Date(date) });
  await mockSite(page);
  let calendarReads = 0;
  await page.route('**/my-stock-screener/report/market-calendar.json', route => {
    calendarReads++;
    return route.fulfill(options.calendar === '404' ? { status: 404 }
      : { json: options.calendar === 'invalid' ? { version: 1, markets: {} } : calendar });
  });
  const batches: string[][] = [];
  await page.route('https://edge.test/functions/v1/market-chart**', route => {
    const symbols = new URL(route.request().url()).searchParams.get('quotes');
    if (!symbols) return route.fallback();
    batches.push(symbols.split(','));
    if (options.failQuote) return route.fulfill({ status: 502 });
    const results = Object.fromEntries(symbols.split(',').map(symbol => {
      const payload = yahooChart(symbol, '1d'), raw = payload.chart.result[0]!, rows = raw.indicators.quote[0]!;
      const last = rows.close.length - 1, price = 1200 + batches.length;
      rows.close[last] = price; rows.high[last] = Math.max(rows.high[last]!, price);
      raw.meta.regularMarketPrice = price;
      return [symbol, payload];
    }));
    return route.fulfill({ json: { results, errors: {} } });
  });
  await page.goto(`./?page=chart&symbol=${options.symbol ?? 'AAPL'}&tf=1D`);
  await expect(page.getByTestId('chart')).toBeVisible();
  await expect.poll(() => batches.length).toBe(1);
  return { batches, reads: () => calendarReads };
}

for (const [name, date, symbol] of [
  ['台股國慶補假', '2026-10-09T03:00:00Z', '2330.TW'],
  ['台股農曆年', '2027-02-08T03:00:00Z', '2330.TW'],
  ['美股感恩節', '2026-11-26T16:00:00Z', 'AAPL'],
] as const) test(`D1 ${name} keeps the initial close and does not poll`, async ({ page }) => {
  const state = await setup(page, date, { symbol });
  await expect(page.getByTestId('ohlc-header')).toContainText('1201.00');
  await page.clock.fastForward(181_000);
  await expect(page.getByTestId('ohlc-header')).toContainText('1201.00');
  expect(state.batches).toHaveLength(1); expect(state.reads()).toBe(1);
});

test('D1 early-close day updates before 13:00 NY then stops exactly at the close', async ({ page }, info) => {
  const state = await setup(page, '2026-11-27T17:57:00Z');
  await expect(page.getByTestId('ohlc-header')).toContainText('1201.00');
  await page.clock.fastForward(61_000);
  await expect(page.getByTestId('ohlc-header')).toContainText('1202.00');
  expect(state.batches).toHaveLength(2);
  await page.clock.fastForward(121_000); // 13:00:02 NY; no ordinary final-print grace on early closes.
  await page.clock.fastForward(600_000);
  await expect(page.getByTestId('ohlc-header')).toContainText('1202.00');
  expect(state.batches).toHaveLength(2); expect(state.reads()).toBe(1);
  await page.screenshot({ path: info.outputPath('calendar-early-close.png') });
});

for (const failure of ['404', 'invalid'] as const) test(`D1 ${failure} calendar preserves weekday minute refresh fallback`, async ({ page }) => {
  const state = await setup(page, '2026-11-26T16:00:00Z', { calendar: failure });
  await expect(page.getByTestId('ohlc-header')).toContainText('1201.00');
  await page.clock.fastForward(61_000);
  await expect(page.getByTestId('ohlc-header')).toContainText('1202.00');
  expect(state.batches).toHaveLength(2);
});

test('D1 unsuccessful initial holiday quote never starts periodic retries', async ({ page }) => {
  const state = await setup(page, '2026-11-26T16:00:00Z', { failQuote: true });
  await page.clock.fastForward(301_000);
  expect(state.batches).toHaveLength(1);
  await expect(page.getByTestId('chart')).toBeVisible();
});

for (const mode of ['holiday', 'early-close', 'fallback'] as const) test(`D1 intraday ${mode} uses the same calendar without another polling loop`, async ({ page }) => {
  await page.clock.install({ time: new Date(mode === 'early-close' ? '2026-11-27T17:57:00Z' : '2026-11-26T16:00:00Z') });
  await mockSite(page);
  let calendarReads = 0;
  await page.route('**/my-stock-screener/report/market-calendar.json', route => {
    calendarReads++;
    return route.fulfill(mode === 'fallback' ? { status: 404 } : { json: calendar });
  });
  let intervals = 0;
  await page.route('https://edge.test/functions/v1/market-chart**', route => {
    if (new URL(route.request().url()).searchParams.get('interval') !== '5m') return route.fallback();
    intervals++;
    return route.fulfill({ json: yahooChart('AAPL', '5m') });
  });
  await page.goto('./?page=chart&symbol=AAPL&tf=5m');
  await expect(page.getByTestId('chart')).toBeVisible();
  await expect.poll(() => intervals).toBe(1);
  // Chart mounting precedes the effect that owns its existing interval. Wait
  // for that effect's calendar request, then let its response/JSON settle.
  await expect.poll(() => calendarReads).toBe(1);
  await page.clock.runFor(1000);
  await page.clock.fastForward(61_000);
  if (mode === 'holiday') expect(intervals).toBe(1);
  else await expect.poll(() => intervals).toBe(2);
  if (mode === 'early-close') {
    await page.clock.fastForward(121_000);
    await page.clock.fastForward(600_000);
    expect(intervals).toBe(2);
  }
  await expect(page.getByTestId('chart')).toBeVisible();
});

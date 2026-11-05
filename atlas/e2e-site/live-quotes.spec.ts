import { expect, test, type Page } from '@playwright/test';
import { mockSite, yahooChart } from './mocks';
import { misOnlyChart, overlayMis, parseMisRow } from '../../supabase/functions/market-chart/twse-mis';
import { MIS_NOW, MIS_TODAY, misRows, misYahoo } from '../tests/fixtures/twse-mis';

const LIVE_PRICE = 1234.5;

/** Answers `market-chart?quotes=` with the chart's own rows, the latest close moved to LIVE_PRICE. */
async function liveQuotes(page: Page) {
  const batches: string[][] = [];
  await page.route('https://edge.test/functions/v1/market-chart**', (route) => {
    const symbols = new URL(route.request().url()).searchParams.get('quotes');
    if (!symbols) return route.fallback();
    batches.push(symbols.split(','));
    const results = Object.fromEntries(symbols.split(',').map((symbol) => {
      const payload = yahooChart(symbol, '1d');
      const data = payload.chart.result[0]!,
        quote = data.indicators.quote[0]!,
        last = quote.close.length - 1;
      quote.close[last] = LIVE_PRICE;
      quote.high[last] = Math.max(quote.high[last]!, LIVE_PRICE);
      data.meta.regularMarketPrice = LIVE_PRICE;
      return [symbol, payload];
    }));
    return route.fulfill({ json: { asOf: 0, results, errors: {} } });
  });
  return batches;
}

test('the open chart takes the fresh price at once, keeping its loaded history', async ({ page }) => {
  await mockSite(page);
  const batches = await liveQuotes(page);
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('1234.50');
  expect(batches).toContainEqual(['AAPL']);
});

test('watchlist rows on screen show the fresh price in one batched request', async ({ page }) => {
  await page.clock.install({ time: new Date('2026-11-04T16:00:00Z') });
  await mockSite(page);
  await page.route('**/my-stock-screener/report/trending-stocks.json', (route) =>
    route.fulfill({ json: { version: 1, source: 'line', fetchedAt: '2026-10-27T01:00:00+08:00', sentAt: '2026-10-27T01:05:00+08:00', symbols: ['NVDA', 'AAPL'] } }));
  const batches = await liveQuotes(page);
  await page.goto('./?page=watchlist');
  const rows = page.getByRole('region', { name: '自選行情' }).getByRole('list', { name: '自選行情列表' }).getByRole('link');
  await expect(rows).toHaveCount(2);
  for (const row of await rows.all()) await expect(row.locator('.wb-quote b')).toHaveText(/1,?234\.50/);
  expect(batches.flat().sort()).toEqual(['AAPL', 'NVDA']);
  await expect(page.getByTestId('board-live-time')).toHaveText(/^延遲報價 · 更新於 \d{2}:\d{2}:\d{2}$/);
});

test('during the session the chart price and its update time move again a minute later', async ({ page }) => {
  // Wednesday 2026-11-04 12:20 in Taipei: the Taiwan session is open.
  await page.clock.install({ time: new Date('2026-11-04T04:20:00Z') });
  await mockSite(page);
  await page.route('**/my-stock-screener/report/trending-stocks.json', (route) =>
    route.fulfill({ json: { version: 1, source: 'line', fetchedAt: '2026-10-27T01:00:00+08:00', sentAt: '2026-10-27T01:05:00+08:00', symbols: ['NVDA'] } }));
  let price = 1000;
  await page.route('https://edge.test/functions/v1/market-chart**', (route) => {
    const symbols = new URL(route.request().url()).searchParams.get('quotes');
    if (!symbols) return route.fallback();
    const results = Object.fromEntries(symbols.split(',').map((symbol) => {
      const payload = yahooChart(symbol, '1d');
      const data = payload.chart.result[0]!, quote = data.indicators.quote[0]!, last = quote.close.length - 1;
      quote.close[last] = price;
      quote.high[last] = Math.max(quote.high[last]!, price);
      quote.low[last] = Math.min(quote.low[last]!, price);
      data.meta.regularMarketPrice = price;
      return [symbol, payload];
    }));
    return route.fulfill({ json: { asOf: 0, results, errors: {} } });
  });
  await page.goto('./?page=chart&symbol=2330.TW&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('1000.00');
  await expect(page.getByTestId('live-time')).toHaveText(/^延遲報價 · 更新於 12:20:\d{2}$/);
  price = 1010;
  await page.clock.fastForward(61_000);
  await expect(page.getByTestId('ohlc-header')).toContainText('1010.00');
  await expect(page.getByTestId('header-quote')).toContainText('1,010.00');
  await expect(page.getByTestId('live-time')).toHaveText(/^延遲報價 · 更新於 12:21:\d{2}$/);
  await page.screenshot({ path: test.info().outputPath('live-time-header.png') });
});

test('market index cards follow the live refresh without rebuilding their chart', async ({ page }) => {
  await mockSite(page);
  const batches = await liveQuotes(page);
  await page.goto('./?page=markets&market=tw');
  const card = page.locator('article.index-card').first();
  await expect(card).toBeVisible();
  await expect(card.locator('.index-card-quote b')).toHaveText(/1,?234\.50/);
  await expect(card.getByTestId('index-live-time')).toHaveText(/^更新於 \d{2}:\d{2}:\d{2}$/);
  await expect(card.locator('.mini-chart')).toHaveAttribute('data-status', 'ready');
  expect(batches.flat().length).toBeGreaterThan(0);
});

/** No test contacts TWSE: the Edge payload is built from synthetic MIS rows. */
for (const symbol of ['2330.TW', '6488.TWO', '^TWII', '^TWOII']) {
  test(`MIS ${symbol}: today's candle and source arrive through the existing minute feed`, async ({ page }, info) => {
    await page.clock.install({ time: new Date(MIS_NOW) });
    await mockSite(page);
    if (symbol === '^TWOII') {
      await page.route('**/report/series/index.json', route => route.fulfill({ json: { symbols: ['^TWOII'] } }));
      await page.route('**/report/series/%5ETWOII.json', route => route.fulfill({ json: {
        symbol: '^TWOII', name: '櫃買指數', currency: 'TWD', source: 'TPEx 歷史', interval: '1D', asOf: MIS_TODAY - 86400,
        bars: misYahoo('^TWOII').chart.result[0]!.timestamp.map((time, i) => ({ time: time - 3600, open: 331, high: 335, low: 320, close: 330 + i, volume: 0 })),
      } }));
    }
    const codes = { '2330.TW': '2330', '6488.TWO': '6488', '^TWII': 't00', '^TWOII': 'o00' };
    const raw = misRows().find(row => row.c === codes[symbol as keyof typeof codes])!;
    let price = symbol === '2330.TW' ? 1828 : Number(raw.z);
    const requests: string[] = [];
    await page.route('https://edge.test/functions/v1/market-chart**', route => {
      const params = new URL(route.request().url()).searchParams, quotes = params.get('quotes');
      if (!quotes) return route.fallback();
      const results = Object.fromEntries(quotes.split(',').map(item => {
        if (item !== symbol) return [item, yahooChart(item, '1d')];
        requests.push(item);
        const row = parseMisRow({ ...raw, z: String(price) }, symbol, MIS_NOW)!;
        return [item, symbol === '^TWOII' ? misOnlyChart(row) : overlayMis(misYahoo(symbol, true), row)];
      }));
      return route.fulfill({ json: { results, errors: {} } });
    });
    await page.goto(`./?page=chart&symbol=${encodeURIComponent(symbol)}&tf=1D`);
    const formatted = (value: number) => value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    await expect(page.getByTestId('header-quote')).toContainText(formatted(price));
    await expect(page.getByTestId('ohlc-header')).toContainText(price.toFixed(2));
    await expect(page.getByTestId('live-time')).toContainText('即時報價');
    await page.getByTestId('source-info-toggle').click();
    await expect(page.locator('.data-source')).toContainText('證交所即時（非官方 API）');
    await expect(page.locator('.data-source')).toContainText(new Date(MIS_TODAY * 1000).toLocaleDateString('zh-TW', { timeZone: 'Asia/Taipei' }));
    await page.getByTestId('source-info-toggle').click();
    price += symbol.startsWith('^') ? 0.1 : 1;
    await page.clock.fastForward(61_000);
    await expect(page.getByTestId('header-quote')).toContainText(formatted(price));
    await expect(page.getByTestId('ohlc-header')).toContainText(price.toFixed(2));
    expect(requests).toHaveLength(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (symbol === '^TWII' || symbol === '2330.TW') await page.screenshot({ path: info.outputPath(`mis-${symbol === '^TWII' ? 'index' : 'stock'}.png`) });
  });
}

test('MIS failure restores Yahoo labeling through the same minute feed', async ({ page }) => {
  await page.clock.install({ time: new Date(MIS_NOW) });
  await mockSite(page);
  let fail = false;
  await page.route('https://edge.test/functions/v1/market-chart**', route => {
    const params = new URL(route.request().url()).searchParams;
    if (!params.has('quotes')) return route.fallback();
    const row = parseMisRow(misRows()[0], '^TWII', MIS_NOW)!;
    const raw = misYahoo('^TWII', true);
    return route.fulfill({ json: { results: { '^TWII': fail ? raw : overlayMis(raw, row) }, errors: {} } });
  });
  await page.goto('./?page=chart&symbol=%5ETWII&tf=1D');
  await expect(page.getByTestId('live-time')).toContainText('即時報價');
  fail = true;
  await page.clock.fastForward(61_000);
  await expect(page.getByTestId('live-time')).toContainText('延遲報價');
  await page.getByTestId('source-info-toggle').click();
  await expect(page.locator('.data-source')).not.toContainText('證交所即時');
  await expect(page.locator('.data-source')).toContainText('Yahoo');
  await expect(page.getByTestId('chart')).toBeVisible();
});

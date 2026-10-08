import { expect, test, type Page } from '@playwright/test';
import { openPanel as openWorkspacePanel, searchSymbol } from '../e2e/site-helpers';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { chooseDrawingTool } from '../e2e/drawing-picker-helper';
import { closedBars, normalizeSymbol, type BarResult } from '../src/market-data/MarketDataProvider';
import { getMarketProfile } from '../src/market-data/MarketProfile';
import {
  normalizeYahooCalendarResponse,
  normalizeYahooEvents,
  normalizeYahooResponse,
} from '../src/market-data/YahooNormalizer';
import { snapshotSchema, type MarketSnapshot } from '../src/market-data/SnapshotSchema';
import type { SymbolState } from '../src/storage/schema';

test.use({ serviceWorkers: 'block' });

const base = '/lightweight-drawing-lab/';
const fixture = (name: string) =>
  JSON.parse(readFileSync(resolve('tests/fixtures', name), 'utf8')) as unknown;
const taiwanProvenance = fixture('market/taiwan/provenance.json') as {
  source: string;
  symbolsAndIntervals: string[];
};

type TaiwanSymbol = '2330.TW' | '0050.TW' | '6488.TWO';
const taiwanFixtureFiles: Record<
  TaiwanSymbol,
  { daily: string; fiveMinute?: string; weekly?: string; monthly?: string }
> = {
  '2330.TW': {
    daily: '2330_TW_1d.json',
    fiveMinute: '2330_TW_5m.json',
    weekly: '2330_TW_1wk.json',
    monthly: '2330_TW_1mo.json',
  },
  '0050.TW': { daily: '0050_TW_1d.json' },
  '6488.TWO': { daily: '6488_TWO_1d.json' },
};

function buildTaiwanSnapshot(symbol: TaiwanSymbol): MarketSnapshot {
  const files = taiwanFixtureFiles[symbol];
  const dailyRaw = fixture(`market/taiwan/${files.daily}`);
  const asOf = Date.now() / 1000;
  const daily = normalizeYahooResponse(dailyRaw, asOf, '1D', symbol);
  if (!daily.quote) throw new Error(`${symbol} fixture has no normalized daily quote`);
  const results: Record<string, unknown> = { '1D': daily };
  if (files.weekly) {
    results['1W'] = normalizeYahooCalendarResponse(
      fixture(`market/taiwan/${files.weekly}`),
      dailyRaw,
      asOf,
      '1W',
      symbol,
    );
  }
  if (files.monthly) {
    results['1M'] = normalizeYahooCalendarResponse(
      fixture(`market/taiwan/${files.monthly}`),
      dailyRaw,
      asOf,
      '1M',
      symbol,
    );
  }
  if (files.fiveMinute) {
    results['5m'] = normalizeYahooResponse(
      fixture(`market/taiwan/${files.fiveMinute}`),
      asOf,
      '5m',
      symbol,
    );
  }
  return snapshotSchema.parse({
    version: 3,
    symbol,
    market: getMarketProfile(symbol),
    generatedAt: asOf,
    results,
    quote: daily.quote,
    events: normalizeYahooEvents(dailyRaw, symbol, asOf),
  });
}

async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}

async function closePanel(page: Page) {
  const close = page.getByRole('button', { name: '關閉面板', exact: true });
  if (await close.isVisible()) await close.click();
}

async function openPanel(page: Page, panel: 'indicators' | 'backtest' | 'watchlist') {
  await openWorkspacePanel(page, panel);
}

async function switchSymbol(page: Page, query: string, canonical: string) {
  await closePanel(page);
  await searchSymbol(page, query);
  await expect(page.getByTestId('active-symbol')).toHaveText(canonical);
  await loaded(page);
}

async function addBareWatchlistTicker(page: Page, ticker: string, canonical: string) {
  await openPanel(page, 'watchlist');
  const row = page.getByRole('button', { name: `選擇 ${canonical}`, exact: true });
  if (!(await row.isVisible())) {
    await page.getByRole('button', { name: '新增自選股', exact: true }).click();
    await page.getByLabel('自選股代號', { exact: true }).fill(ticker);
    await page.getByRole('button', { name: '加入', exact: true }).click();
  }
  await expect(row).toBeVisible();
  await closePanel(page);
}

async function addSma(page: Page, period: number, locked: boolean) {
  await openPanel(page, 'indicators');
  await page.getByLabel('SMA 週期', { exact: true }).fill(String(period));
  await page.getByRole('button', { name: '新增 SMA', exact: true }).click();
  if (locked) await page.getByRole('button', { name: `鎖定 SMA ${period}`, exact: true }).click();
}

async function savedSymbol(page: Page, symbol: string): Promise<SymbolState | undefined> {
  return page.evaluate(async (ticker) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('atlas-terminal');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const data = await new Promise<SymbolState | undefined>((resolve, reject) => {
      const request = db.transaction('symbols', 'readonly').objectStore('symbols').get(ticker);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    db.close();
    return data;
  }, symbol);
}

async function pointerGesture(
  page: Page,
  isMobile: boolean,
  browserName: string,
  start: { x: number; y: number },
  end: { x: number; y: number },
) {
  if (isMobile && browserName === 'chromium') {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ ...start, id: 1 }],
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ ...end, id: 1 }],
    });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await cdp.detach();
    return;
  }
  if (isMobile) {
    await page.evaluate(() => {
      for (const type of ['pointerdown', 'pointermove', 'pointerup'])
        document.addEventListener(
          type,
          (event) => Object.defineProperty(event, 'pointerType', { value: 'touch' }),
          { capture: true },
        );
    });
  }
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 5 });
  await page.mouse.up();
}

test('Pages resolves official Taiwan symbols and keeps TWD snapshots, state, and closed-bar research isolated', async ({
  page,
  isMobile,
  browserName,
}) => {
  expect(taiwanProvenance.source).toContain('query1.finance.yahoo.com');
  expect(taiwanProvenance.symbolsAndIntervals).toContain('2330.TW 1d, 1wk, 1mo');
  expect(taiwanProvenance.symbolsAndIntervals).toContain(
    '2330.TW 5m (final eight actual rows, including terminal close-only row)',
  );
  expect(taiwanProvenance.symbolsAndIntervals).toContain('0050.TW 1d');
  expect(taiwanProvenance.symbolsAndIntervals).toContain('6488.TWO 1d');
  const packs: Record<TaiwanSymbol, MarketSnapshot> = {
    '2330.TW': buildTaiwanSnapshot('2330.TW'),
    '0050.TW': buildTaiwanSnapshot('0050.TW'),
    '6488.TWO': buildTaiwanSnapshot('6488.TWO'),
  };
  const apiRequests: string[] = [];
  const financialRequests: string[] = [];
  const snapshotRequests: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (/\/api\//.test(path)) apiRequests.push(request.url());
    if (path.includes('/financial-data/')) financialRequests.push(request.url());
  });
  await page.route('**/market-data/*.json', async (route) => {
    const file = new URL(route.request().url()).pathname.split('/').at(-1) ?? '';
    const symbol = normalizeSymbol(decodeURIComponent(file.replace(/\.json$/, '')));
    snapshotRequests.push(symbol);
    const snapshot = packs[symbol as TaiwanSymbol];
    if (!snapshot) {
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
      return;
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(snapshot) });
  });

  await page.goto(base);
  await loaded(page);
  await addSma(page, 24, true);
  await expect.poll(async () => (await savedSymbol(page, 'AAPL'))?.indicators.length).toBe(1);

  await switchSymbol(page, '2330', '2330.TW');
  await expect(page.locator('.ws-symbol')).toContainText('台積電');
  const legend = page.getByRole('group', { name: '2330.TW 圖例', exact: true });
  await expect(legend).toContainText('上市');
  await expect(page.locator('.ws-quote')).toContainText('TWD');
  await expect(page.getByRole('button', { name: '週期 4H', exact: true })).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText(
    `C ${packs['2330.TW'].quote.price.toFixed(2)}`,
  );
  await addBareWatchlistTicker(page, '2330', '2330.TW');

  await page.getByRole('button', { name: '週期 1W', exact: true }).click();
  await loaded(page);
  await expect(page.locator('.data-source')).toContainText('本週 K由日 K彙總');
  await expect(page.getByTestId('ohlc-header')).toContainText(
    `C ${packs['2330.TW'].quote.price.toFixed(2)}`,
  );
  await page.getByRole('button', { name: '週期 1M', exact: true }).click();
  await loaded(page);
  await expect(page.locator('.data-source')).toContainText('本月 K由日 K彙總');
  await expect(page.getByTestId('ohlc-header')).toContainText(
    `C ${packs['2330.TW'].quote.price.toFixed(2)}`,
  );
  await page.getByRole('button', { name: '週期 1D', exact: true }).click();
  await loaded(page);
  expect(financialRequests).toEqual([]);

  await addSma(page, 12, true);
  await closePanel(page);
  await chooseDrawingTool(page, '水平線');
  const plot = (await page.getByTestId('chart').locator('canvas').first().boundingBox())!;
  const start = { x: plot.x + plot.width * 0.94, y: plot.y + plot.height * 0.48 };
  const end = { x: start.x - 12, y: start.y + 8 };
  await pointerGesture(page, isMobile, browserName, start, end);
  await expect.poll(async () => (await savedSymbol(page, '2330.TW'))?.drawings.length).toBe(1);
  const latestTaiwanBar = packs['2330.TW'].results['1D']!.bars.at(-1)!.time;
  await expect
    .poll(async () => (await savedSymbol(page, '2330.TW'))?.drawings[0]?.points[0]?.time ?? 0)
    .toBeGreaterThan(latestTaiwanBar);
  await page.getByRole('button', { name: '鎖定所選畫線', exact: true }).click();
  await expect
    .poll(async () => (await savedSymbol(page, '2330.TW'))?.drawings[0]?.locked)
    .toBe(true);

  const taiwanState = await savedSymbol(page, '2330.TW');
  expect(
    taiwanState?.indicators.map((indicator) => [
      indicator.period,
      indicator.scope.timeframe,
      indicator.locked,
    ]),
  ).toContainEqual([12, '1D', true]);
  expect(taiwanState?.drawings[0]).toMatchObject({
    symbol: '2330.TW',
    type: 'horizontal',
    locked: true,
    scope: { timeframes: ['1D'] },
  });
  const usState = await savedSymbol(page, 'AAPL');
  expect(
    usState?.indicators.map((indicator) => [
      indicator.period,
      indicator.scope.timeframe,
      indicator.locked,
    ]),
  ).toContainEqual([24, '1D', true]);
  expect(usState?.drawings).toHaveLength(0);

  await page.getByRole('button', { name: '週期 1W', exact: true }).click();
  await loaded(page);
  await openPanel(page, 'indicators');
  await expect(page.locator('.indicator-chip').filter({ hasText: 'SMA 12' })).toHaveCount(0);
  await closePanel(page);
  await page.getByRole('button', { name: '週期 1D', exact: true }).click();
  await loaded(page);

  await switchSymbol(page, '0050', '0050.TW');
  await expect(page.locator('.chart-heading')).toContainText('上市');
  await expect(page.locator('.ws-quote')).toContainText('TWD');
  await expect(page.getByTestId('ohlc-header')).toContainText(
    `C ${packs['0050.TW'].quote.price.toFixed(2)}`,
  );
  await expect(page.getByRole('button', { name: '週期 4H', exact: true })).toHaveCount(0);

  await switchSymbol(page, '6488', '6488.TWO');
  await expect(page.locator('.ws-symbol')).toContainText('環球晶');
  await expect(page.locator('.chart-heading')).toContainText('上櫃');
  await expect(page.locator('.ws-quote')).toContainText('TWD');
  await expect(page.getByTestId('ohlc-header')).toContainText(
    `C ${packs['6488.TWO'].quote.price.toFixed(2)}`,
  );
  await addBareWatchlistTicker(page, '6488', '6488.TWO');

  await switchSymbol(page, '2330', '2330.TW');
  await page.reload();
  await loaded(page);
  await expect(page.getByTestId('active-symbol')).toHaveText('2330.TW');
  await expect
    .poll(async () => (await savedSymbol(page, '2330.TW'))?.drawings[0]?.locked)
    .toBe(true);
  await expect
    .poll(async () =>
      (await savedSymbol(page, '2330.TW'))?.indicators.map((i) => [i.period, i.locked]),
    )
    .toContainEqual([12, true]);
  await expect
    .poll(async () =>
      (await savedSymbol(page, 'AAPL'))?.indicators.map((i) => [i.period, i.locked]),
    )
    .toContainEqual([24, true]);

  await openPanel(page, 'watchlist');
  await expect(page.getByRole('button', { name: '選擇 2330.TW', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '選擇 6488.TWO', exact: true })).toBeVisible();
  await closePanel(page);

  await openPanel(page, 'backtest');
  const backtest = page.getByRole('region', { name: '2330.TW 策略回測', exact: true });
  await expect(backtest).toContainText('2330.TW · 1D · TWD');
  await backtest.getByRole('button', { name: '執行回測', exact: true }).click();
  const closedBarSummary = backtest
    .locator('.small.muted')
    .filter({ hasText: '已選用' });
  await expect(closedBarSummary).toContainText('根已收盤 K 棒');
  await expect(
    backtest.getByRole('heading', { name: '模擬結果', exact: true }),
  ).toBeVisible();
  await expect(backtest.locator('.backtest-metrics')).toContainText('TWD');

  const taiwanFiveMinute = packs['2330.TW'].results['5m'] as BarResult | undefined;
  expect(taiwanFiveMinute).toBeDefined();
  if (!taiwanFiveMinute) {
    throw new Error('The 2330.TW snapshot is missing its normalized 5m fixture');
  }
  const auctionTimes = taiwanFiveMinute.sessionCloseObservations ?? [];
  expect(auctionTimes).toHaveLength(1);
  expect(taiwanFiveMinute.source).toContain('source-reported session-close observations');
  const auctionClose = taiwanFiveMinute.bars.find((bar) => auctionTimes.includes(bar.time));
  expect(auctionClose).toBeDefined();
  if (!auctionClose) throw new Error('The normalized Taiwan fixture lost its terminal close sample');
  const previousRegularBar = taiwanFiveMinute.bars
    .filter((bar) => bar.time < auctionClose.time)
    .at(-1);
  expect(auctionClose).toMatchObject({ open: 2550, high: 2550, low: 2550, close: 2550, volume: 0 });
  expect(previousRegularBar?.close).toBe(2560);
  expect(auctionClose.close).not.toBe(previousRegularBar?.close);

  const expectedFiveMinuteClosedBars = closedBars(
    taiwanFiveMinute,
    '5m',
    taiwanFiveMinute.asOf ?? Date.now() / 1000,
  ).length;
  await closePanel(page);
  await page.getByRole('button', { name: '週期 5m', exact: true }).click();
  await loaded(page);
  await expect(page.locator('.data-source')).toContainText(
    `${auctionTimes.length} 筆收盤競價觀測點（瞬時樣本）`,
  );
  await expect(page.getByTestId('ohlc-header')).toContainText(`C ${auctionClose.close.toFixed(2)}`);
  await openPanel(page, 'backtest');
  const fiveMinuteBacktest = page.getByRole('region', { name: '2330.TW 策略回測', exact: true });
  await expect(fiveMinuteBacktest).toContainText('2330.TW · 5m · TWD');
  await expect(fiveMinuteBacktest).toContainText('source-reported session-close observations');
  const fiveMinuteClosedBars = fiveMinuteBacktest
    .locator('.small.muted')
    .filter({ hasText: '已選用' });
  await fiveMinuteBacktest.getByRole('button', { name: '執行回測', exact: true }).click();
  await expect(fiveMinuteClosedBars).toContainText(`已選用 ${expectedFiveMinuteClosedBars} 根已收盤 K 棒`);
  await expect(
    fiveMinuteBacktest.getByRole('heading', { name: '模擬結果', exact: true }),
  ).toBeVisible();

  expect(snapshotRequests).toContain('2330.TW');
  expect(snapshotRequests).toContain('0050.TW');
  expect(snapshotRequests).toContain('6488.TWO');
  expect(apiRequests).toEqual([]);
  expect(financialRequests).toEqual([]);
});

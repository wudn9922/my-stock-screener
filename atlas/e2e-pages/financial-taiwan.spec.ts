import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { chooseDrawingTool } from '../e2e/drawing-picker-helper';
import { FinancialNormalizer } from '../src/fundamentals/FinancialNormalizer';
import {
  financialSnapshotSchema,
  type FinancialSnapshot,
} from '../src/fundamentals/FinancialSnapshotSchema';
import { financialMetrics, type FinancialMetric } from '../src/fundamentals/FundamentalsProvider';
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

type SecFixture = { cik: number; entityName: string };
type SecFixtureProvenance = { symbol: string; retrieved: string }[];
const secRaw = fixture('sec/AAPL.json') as SecFixture & Record<string, unknown>;
const secProvenance = fixture('sec/provenance.json') as unknown as SecFixtureProvenance;
const secSnapshot = buildSecSnapshot();

function newestFirst<T extends { periodEnd: string; filingDate: string }>(records: T[]) {
  return records
    .sort(
      (a, b) => b.periodEnd.localeCompare(a.periodEnd) || b.filingDate.localeCompare(a.filingDate),
    )
    .slice(0, 100);
}

function buildSecSnapshot(): FinancialSnapshot {
  const retrieved = secProvenance.find((entry) => entry.symbol === 'AAPL')?.retrieved;
  if (!retrieved) throw new Error('The AAPL SEC fixture is missing retrieval provenance');
  const asOf = Math.floor(Date.parse(`${retrieved}T23:59:59.000Z`) / 1000);
  const cik = String(secRaw.cik).padStart(10, '0');
  const source = `https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`;
  const normalizer = new FinancialNormalizer();
  return financialSnapshotSchema.parse({
    version: 1,
    symbol: 'AAPL',
    cik,
    issuerName: secRaw.entityName,
    source,
    fetchedAt: asOf,
    generatedAt: asOf,
    quarterly: newestFirst(normalizer.normalize(secRaw, 'AAPL', 'quarterly')),
    annual: newestFirst(normalizer.normalize(secRaw, 'AAPL', 'annual')),
  });
}

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

const fiscalPeriod = (record: { fiscalYear: number; fiscalQuarter: number | null }) =>
  `FY${record.fiscalYear}${record.fiscalQuarter ? ` Q${record.fiscalQuarter}` : ''}`;

async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}

async function closePanel(page: Page) {
  const close = page.getByRole('button', { name: 'Close panel', exact: true });
  if (await close.isVisible()) await close.click();
}

async function openPanel(page: Page, panel: 'indicators' | 'financials' | 'backtest') {
  await closePanel(page);
  const nav = page.locator('.mobile-nav');
  if (await nav.isVisible()) {
    await nav
      .getByRole('button', {
        name: panel === 'indicators' ? 'SMA' : panel === 'financials' ? 'Financials' : 'Research',
        exact: true,
      })
      .click();
  } else {
    await page.getByLabel('Research panel', { exact: true }).selectOption(panel);
  }
}

async function switchSymbol(page: Page, query: string, canonical: string) {
  await closePanel(page);
  const search = page.getByLabel('Symbol search', { exact: true });
  await search.fill(query);
  if (/^\d{4,6}[A-Z]?$/i.test(query)) {
    await expect(page.locator(`#symbol-options option[value="${canonical}"]`)).toHaveCount(1);
  }
  await search.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText(canonical);
  await loaded(page);
}

async function addBareWatchlistTicker(page: Page, ticker: string, canonical: string) {
  const nav = page.locator('.mobile-nav');
  const mobile = await nav.isVisible();
  if (mobile) await nav.getByRole('button', { name: 'Watchlist', exact: true }).click();
  const row = page.getByRole('button', { name: `Select ${canonical}`, exact: true });
  if (!(await row.isVisible())) {
    await page.getByRole('button', { name: 'Add watchlist symbol', exact: true }).click();
    await page.getByLabel('Watchlist ticker', { exact: true }).fill(ticker);
    await page.getByRole('button', { name: 'Add', exact: true }).click();
  }
  await expect(row).toBeVisible();
  if (mobile) await closePanel(page);
}

async function addSma(page: Page, period: number, locked: boolean) {
  await openPanel(page, 'indicators');
  await page.getByLabel('SMA period', { exact: true }).fill(String(period));
  await page.getByRole('button', { name: 'Add SMA', exact: true }).click();
  if (locked) await page.getByRole('button', { name: `Lock SMA ${period}`, exact: true }).click();
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

test('Pages financial snapshot validates filing provenance, offline cache, and unavailable states', async ({
  page,
}) => {
  const apiRequests: string[] = [];
  const financialRequests: string[] = [];
  let failAapl = false;
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (/\/api\//.test(path)) apiRequests.push(request.url());
    if (path.endsWith('/financial-data/AAPL.json')) financialRequests.push(request.url());
  });
  await page.route('**/financial-data/AAPL.json', async (route) => {
    if (failAapl) {
      await route.abort('failed');
      return;
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(secSnapshot),
    });
  });
  await page.route('**/financial-data/NVDA.json', (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }),
  );

  await page.goto(base);
  await loaded(page);
  await openPanel(page, 'financials');
  const panel = page.getByTestId('financial-panel');
  const provenance = panel.locator('.financial-provenance-note[role="status"]');
  await expect(provenance).toContainText('SEC snapshot · ONLINE · STALE');
  await expect(provenance).toContainText('Fetched');
  await expect(provenance).toContainText('Snapshot as of');
  await expect(provenance).toContainText('Latest included filing:');
  await expect(panel.getByRole('link', { name: 'SEC companyfacts' })).toHaveAttribute(
    'href',
    secSnapshot.source,
  );

  const latestQuarter = secSnapshot.quarterly[0]!;
  await expect(panel.getByLabel('Fiscal period', { exact: true })).toHaveValue(
    latestQuarter.periodEnd,
  );
  const revenueHistory = panel
    .getByRole('img', { name: 'Revenue history' })
    .locator(':scope > title');
  const quarterHistory = (await revenueHistory.textContent()) ?? '';
  expect(quarterHistory).toContain(fiscalPeriod(latestQuarter));
  if (secSnapshot.quarterly.length > 12)
    expect(quarterHistory).not.toContain(fiscalPeriod(secSnapshot.quarterly.at(-1)!));

  const metricLabels: Partial<Record<FinancialMetric, string>> = {
    revenue: 'Revenue',
    costOfRevenue: 'Cost of Revenue',
    grossProfit: 'Gross Profit',
    operatingExpenses: 'Operating Expenses',
    operatingIncome: 'Operating Income',
    netIncome: 'Net Income',
    epsBasic: 'EPS Basic',
    epsDiluted: 'EPS Diluted',
    operatingCashFlow: 'Operating Cash Flow',
    capex: 'CapEx',
    freeCashFlow: 'Free Cash Flow',
    cash: 'Cash',
    totalAssets: 'Total Assets',
    totalLiabilities: 'Total Liabilities',
    equity: 'Equity',
  };
  const metricTabs: Record<
    FinancialMetric,
    'Overview' | 'Income Statement' | 'Cash Flow' | 'Balance Sheet'
  > = {
    revenue: 'Overview',
    costOfRevenue: 'Income Statement',
    grossProfit: 'Income Statement',
    operatingExpenses: 'Income Statement',
    operatingIncome: 'Income Statement',
    netIncome: 'Overview',
    epsBasic: 'Income Statement',
    epsDiluted: 'Overview',
    operatingCashFlow: 'Cash Flow',
    capex: 'Cash Flow',
    freeCashFlow: 'Overview',
    cash: 'Balance Sheet',
    totalAssets: 'Balance Sheet',
    totalLiabilities: 'Balance Sheet',
    equity: 'Balance Sheet',
  };
  const rawMetric = financialMetrics.find(
    (metric) => latestQuarter.sourceConcepts[metric]?.derived === false,
  );
  const derivedMetric = financialMetrics.find(
    (metric) => latestQuarter.sourceConcepts[metric]?.derived === true,
  );
  expect(rawMetric, 'AAPL SEC fixture must expose a raw filing fact').toBeDefined();
  expect(derivedMetric, 'AAPL SEC fixture must expose a derived metric').toBeDefined();
  for (const [metric, expectedKind] of [
    [rawMetric!, 'Raw filing fact'],
    [derivedMetric!, 'Derived calculation'],
  ] as const) {
    const tab = metricTabs[metric];
    if (tab !== 'Overview') await panel.getByRole('button', { name: tab, exact: true }).click();
    const details = panel.locator(
      `details.financial-audit:has(> summary[aria-label="Audit ${metricLabels[metric]}"])`,
    );
    const summary = details.locator(':scope > summary');
    await summary.click();
    await expect(details.locator('.financial-audit-body')).toContainText(expectedKind);
    await expect(details.locator('.audit-source')).not.toHaveCount(0);
  }

  await panel.getByRole('button', { name: 'Overview', exact: true }).click();
  await panel.getByRole('button', { name: 'Annual', exact: true }).click();
  const latestAnnual = secSnapshot.annual[0]!;
  await expect(panel.getByLabel('Fiscal period', { exact: true })).toHaveValue(
    latestAnnual.periodEnd,
  );
  await expect(
    panel.getByRole('img', { name: 'Revenue history' }).locator(':scope > title'),
  ).toContainText(fiscalPeriod(latestAnnual));

  failAapl = true;
  await page.reload();
  await loaded(page);
  await openPanel(page, 'financials');
  const offlinePanel = page.getByTestId('financial-panel');
  await expect(offlinePanel.locator('.financial-provenance-note[role="status"]')).toContainText(
    'SEC snapshot · OFFLINE CACHE · STALE',
  );
  await expect(offlinePanel.getByLabel('Fiscal period', { exact: true })).toHaveValue(
    latestQuarter.periodEnd,
  );

  await switchSymbol(page, 'NVDA', 'NVDA');
  await openPanel(page, 'financials');
  await expect(page.getByTestId('financial-panel')).toContainText('NVDA SEC 財報快照目前不可用');
  await expect(page.getByTestId('financial-panel')).toContainText('K 線與 drawing 可繼續使用');
  await loaded(page);
  expect(financialRequests.length).toBeGreaterThanOrEqual(2);
  expect(apiRequests).toEqual([]);
});

test('Pages discards an aborted AAPL SEC response after switching to NVDA', async ({ page }) => {
  const apiRequests: string[] = [];
  let releaseAapl!: () => void;
  const aaplResponseGate = new Promise<void>((resolveGate) => {
    releaseAapl = resolveGate;
  });
  page.on('request', (request) => {
    if (/\/api\//.test(new URL(request.url()).pathname)) apiRequests.push(request.url());
  });
  await page.route('**/financial-data/AAPL.json', async (route) => {
    await aaplResponseGate;
    try {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(secSnapshot) });
    } catch {
      // The component should have aborted this request when its symbol changed.
    }
  });
  await page.route('**/financial-data/NVDA.json', (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }),
  );

  try {
    await page.goto(base);
    await loaded(page);
    const aaplRequest = page.waitForRequest((request) =>
      new URL(request.url()).pathname.endsWith('/financial-data/AAPL.json'),
    );
    await openPanel(page, 'financials');
    await aaplRequest;
    await switchSymbol(page, 'NVDA', 'NVDA');
    await openPanel(page, 'financials');
    const panel = page.getByTestId('financial-panel');
    await expect(panel).toContainText('NVDA SEC 財報快照目前不可用');
    releaseAapl();
    await expect(panel).toHaveAttribute('aria-label', 'NVDA financials');
    await expect(panel).not.toContainText('Apple Inc.');
    await expect(panel).not.toContainText('AAPL SEC');
    await loaded(page);
    expect(apiRequests).toEqual([]);
  } finally {
    releaseAapl();
  }
});

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
  await expect(page.locator('.symbol-title')).toContainText('台積電');
  const legend = page.getByRole('group', { name: '2330.TW chart legend', exact: true });
  await expect(legend).toContainText('台股上市');
  await expect(legend).toContainText('TWD');
  await expect(page.getByRole('button', { name: 'Timeframe 4H', exact: true })).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText(
    `C ${packs['2330.TW'].quote.price.toFixed(2)}`,
  );
  await addBareWatchlistTicker(page, '2330', '2330.TW');

  await page.getByRole('button', { name: 'Timeframe 1W', exact: true }).click();
  await loaded(page);
  await expect(page.locator('.data-source')).toContainText('本週 K由日 K彙總');
  await expect(page.getByTestId('ohlc-header')).toContainText(
    `C ${packs['2330.TW'].quote.price.toFixed(2)}`,
  );
  await page.getByRole('button', { name: 'Timeframe 1M', exact: true }).click();
  await loaded(page);
  await expect(page.locator('.data-source')).toContainText('本月 K由日 K彙總');
  await expect(page.getByTestId('ohlc-header')).toContainText(
    `C ${packs['2330.TW'].quote.price.toFixed(2)}`,
  );
  await page.getByRole('button', { name: 'Timeframe 1D', exact: true }).click();
  await loaded(page);
  await openPanel(page, 'financials');
  await expect(page.getByTestId('financial-panel')).toContainText('SEC 財報不支援台灣上市市場');
  await expect(page.getByTestId('financial-panel')).toContainText(
    'K 線、報價與其他研究工具仍可繼續使用',
  );
  expect(financialRequests).toEqual([]);
  await closePanel(page);
  await loaded(page);

  await addSma(page, 12, true);
  await closePanel(page);
  await chooseDrawingTool(page, 'Horizontal Line');
  const plot = (await page.getByTestId('chart').locator('canvas').first().boundingBox())!;
  const start = { x: plot.x + plot.width * 0.94, y: plot.y + plot.height * 0.48 };
  const end = { x: start.x - 12, y: start.y + 8 };
  await pointerGesture(page, isMobile, browserName, start, end);
  await expect.poll(async () => (await savedSymbol(page, '2330.TW'))?.drawings.length).toBe(1);
  const latestTaiwanBar = packs['2330.TW'].results['1D']!.bars.at(-1)!.time;
  await expect
    .poll(async () => (await savedSymbol(page, '2330.TW'))?.drawings[0]?.points[0]?.time ?? 0)
    .toBeGreaterThan(latestTaiwanBar);
  await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();
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

  await page.getByRole('button', { name: 'Timeframe 1W', exact: true }).click();
  await loaded(page);
  await openPanel(page, 'indicators');
  await expect(page.locator('.indicator-chip').filter({ hasText: 'SMA 12' })).toHaveCount(0);
  await closePanel(page);
  await page.getByRole('button', { name: 'Timeframe 1D', exact: true }).click();
  await loaded(page);

  await switchSymbol(page, '0050', '0050.TW');
  await expect(page.locator('.chart-heading')).toContainText('台股上市');
  await expect(page.locator('.chart-heading')).toContainText('TWD');
  await expect(page.getByTestId('ohlc-header')).toContainText(
    `C ${packs['0050.TW'].quote.price.toFixed(2)}`,
  );
  await expect(page.getByRole('button', { name: 'Timeframe 4H', exact: true })).toHaveCount(0);

  await switchSymbol(page, '6488', '6488.TWO');
  await expect(page.locator('.symbol-title')).toContainText('環球晶');
  await expect(page.locator('.chart-heading')).toContainText('台股上櫃');
  await expect(page.locator('.chart-heading')).toContainText('TWD');
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

  const nav = page.locator('.mobile-nav');
  const mobile = await nav.isVisible();
  if (mobile) await nav.getByRole('button', { name: 'Watchlist', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Select 2330.TW', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Select 6488.TWO', exact: true })).toBeVisible();
  if (mobile) await closePanel(page);

  await openPanel(page, 'backtest');
  const backtest = page.getByRole('region', { name: '2330.TW backtest', exact: true });
  await expect(backtest).toContainText('2330.TW · 1D · TWD');
  await backtest.getByRole('button', { name: 'Run backtest', exact: true }).click();
  const closedBarSummary = backtest
    .locator('.small.muted')
    .filter({ hasText: 'Closed bars selected:' });
  await expect(closedBarSummary).toContainText('Closed bars selected:');
  await expect(
    backtest.getByRole('heading', { name: 'Simulation results', exact: true }),
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
  await page.getByRole('button', { name: 'Timeframe 5m', exact: true }).click();
  await loaded(page);
  await expect(page.locator('.data-source')).toContainText(
    `${auctionTimes.length} source-reported auction-close observations (instant samples)`,
  );
  await expect(page.getByTestId('ohlc-header')).toContainText(`C ${auctionClose.close.toFixed(2)}`);
  await openPanel(page, 'backtest');
  const fiveMinuteBacktest = page.getByRole('region', { name: '2330.TW backtest', exact: true });
  await expect(fiveMinuteBacktest).toContainText('2330.TW · 5m · TWD');
  await expect(fiveMinuteBacktest).toContainText('source-reported session-close observations');
  const fiveMinuteClosedBars = fiveMinuteBacktest
    .locator('.small.muted')
    .filter({ hasText: 'Closed bars selected:' });
  await fiveMinuteBacktest.getByRole('button', { name: 'Run backtest', exact: true }).click();
  await expect(fiveMinuteClosedBars).toContainText(
    `Closed bars selected: ${expectedFiveMinuteClosedBars}.`,
  );
  await expect(
    fiveMinuteBacktest.getByRole('heading', { name: 'Simulation results', exact: true }),
  ).toBeVisible();

  expect(snapshotRequests).toContain('2330.TW');
  expect(snapshotRequests).toContain('0050.TW');
  expect(snapshotRequests).toContain('6488.TWO');
  expect(apiRequests).toEqual([]);
  expect(financialRequests).toEqual([]);
});

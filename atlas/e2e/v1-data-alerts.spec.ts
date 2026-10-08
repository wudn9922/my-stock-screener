import { expect, test, type Page, type Route } from '@playwright/test';
import type { AlertDefinition } from '../src/storage/schema';
import type { Bar, BarResult, Timeframe } from '../src/market-data/MarketDataProvider';

const AS_OF = Date.parse('2026-10-05T20:05:00Z') / 1000;
const TF_SECONDS: Record<Timeframe, number> = {
  '5m': 300,
  '15m': 900,
  '30m': 1800,
  '1H': 3600,
  '4H': 14400,
  '1D': 86400,
  '1W': 604800,
  // Advisory volatility interval only; monthly bars below use calendar boundaries.
  '1M': 2592000,
};

type YahooMode = 'baseline' | 'crossing';
type RequestCounts = Map<string, number>;

interface MarketCacheRecord {
  providerId: string;
  kind: string;
  symbol: string;
  timeframe: string;
  value: BarResult & { cacheStatus?: string };
}

function normalizedBars(timeframe: Timeframe, closes?: number[]): Bar[] {
  const values = closes ?? Array.from({ length: 12 }, (_, index) => 90 + index);
  let barValues = values;
  let times: number[];
  if (timeframe === '1M') {
    const date = new Date('2025-10-01T00:00:00Z');
    times = values.map(() => {
      const time = date.getTime() / 1000;
      date.setUTCMonth(date.getUTCMonth() + 1);
      return time;
    });
  } else if (timeframe === '1D') {
    const date = new Date('2026-09-14T00:00:00Z');
    times = [];
    while (times.length < values.length) {
      if (date.getUTCDay() !== 0 && date.getUTCDay() !== 6) times.push(date.getTime() / 1000);
      date.setUTCDate(date.getUTCDate() + 1);
    }
  } else if (timeframe === '1W') {
    const date = new Date('2026-09-28T00:00:00Z');
    times = values
      .map(() => {
        const time = date.getTime() / 1000;
        date.setUTCDate(date.getUTCDate() - 7);
        return time;
      })
      .reverse();
  } else {
    const interval = TF_SECONDS[timeframe];
    const sessionOpen = Date.parse('2026-10-02T13:30:00Z') / 1000;
    const lastSlot = Math.ceil((390 * 60) / interval);
    barValues = values.slice(0, timeframe === '1H' ? lastSlot : values.length);
    times = barValues.map((_, index) => sessionOpen + index * interval);
  }
  return barValues.map((close, index) => ({
    time: times[index],
    open: close - 0.5,
    high: close + 1,
    low: close - 1,
    close,
    volume: 1000 + index,
  }));
}

function yahooResult(timeframe: Timeframe, mode: YahooMode): BarResult {
  let bars: Bar[];
  if (timeframe === '1D' && mode === 'baseline') {
    bars = normalizedBarsAt(['2026-09-30', '2026-10-01', '2026-10-02'], [90, 95, 100]);
  } else if (timeframe === '1D' && mode === 'crossing') {
    bars = normalizedBarsAt(
      ['2026-09-30', '2026-10-01', '2026-10-02', '2026-10-05'],
      [90, 95, 100, 101],
    );
  } else {
    bars = normalizedBars(timeframe);
  }
  return {
    bars,
    source: 'Yahoo normalized browser fixture',
    session: 'regular',
    delayed: true,
    adjusted: false,
    asOf: AS_OF,
    latestBarAt: bars.at(-1)!.time,
    dataState: 'delayed',
    cacheStatus: 'fresh',
  };
}

function normalizedBarsAt(dates: string[], closes: number[]): Bar[] {
  return closes.map((close, index) => ({
    time: Date.parse(`${dates[index]}T00:00:00Z`) / 1000,
    open: close - 0.5,
    high: close + 1,
    low: close - 1,
    close,
    volume: 1000 + index,
  }));
}

async function installYahooRoutes(
  page: Page,
  currentMode: () => YahooMode = () => 'baseline',
  shouldFail: () => boolean = () => false,
) {
  const counts: RequestCounts = new Map();
  await page.route(/\/api\/yahoo\?/, async (route: Route) => {
    const url = new URL(route.request().url());
    const symbol = url.searchParams.get('symbol') ?? '';
    const timeframe = url.searchParams.get('timeframe') as Timeframe;
    const key = `${symbol}:${timeframe}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
    if (shouldFail()) {
      await route.abort('failed');
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(yahooResult(timeframe, currentMode())),
    });
  });
  await page.route(/\/api\/yahoo\/events\?/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'unavailable',
        events: [],
        source: 'Yahoo events fixture unavailable',
      }),
    });
  });
  return counts;
}

async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}

async function readMarketCache(page: Page): Promise<MarketCacheRecord[]> {
  return page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('atlas-market-cache');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error('Could not read market cache'));
    });
    const transaction = db.transaction('entries', 'readonly');
    const entries = await new Promise<MarketCacheRecord[]>((resolve, reject) => {
      const request = transaction.objectStore('entries').getAll();
      request.onsuccess = () => resolve(request.result as MarketCacheRecord[]);
      request.onerror = () => reject(request.error ?? new Error('Market cache read failed'));
    });
    db.close();
    return entries;
  });
}

async function readAlerts(page: Page): Promise<AlertDefinition[]> {
  return page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('atlas-terminal');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () =>
        reject(request.error ?? new Error('Could not read workspace database'));
    });
    const transaction = db.transaction('app', 'readonly');
    const app = await new Promise<{ alerts?: AlertDefinition[] } | undefined>((resolve, reject) => {
      const request = transaction.objectStore('app').get('settings');
      request.onsuccess = () =>
        resolve(request.result as { alerts?: AlertDefinition[] } | undefined);
      request.onerror = () => reject(request.error ?? new Error('Workspace alert read failed'));
    });
    db.close();
    return app?.alerts ?? [];
  });
}

async function yahooCacheProviderId(page: Page): Promise<string> {
  return page.evaluate(async () => {
    const { YahooProvider } = await import(
      new URL('/src/market-data/YahooProvider.ts', location.origin).href
    );
    const provider = new YahooProvider();
    return provider.cacheVersion ? `${provider.id}:${provider.cacheVersion}` : provider.id;
  });
}

async function openAlerts(page: Page) {
  if (await page.locator('.mobile-nav').isVisible()) {
    await page
      .locator('.mobile-nav')
      .getByRole('button', { name: 'Research', exact: true })
      .click();
    await page.getByLabel('Research panel', { exact: true }).selectOption('alerts');
  } else {
    await page.getByLabel('Research panel', { exact: true }).selectOption('alerts');
  }
  await expect(page.locator('.alert-panel')).toBeVisible();
}

test('Yahoo exposes its seven direct intervals and caches normalized delayed bars by symbol and timeframe', async ({
  page,
}) => {
  await page.clock.install({ time: new Date(AS_OF * 1000) });
  const counts = await installYahooRoutes(page);
  await page.goto('/');
  await loaded(page);

  await expect(page.getByLabel('Timeframe 4H', { exact: true })).toHaveCount(1);
  await page.getByLabel('Market data source', { exact: true }).selectOption('yahoo');
  await loaded(page);
  const providerId = await yahooCacheProviderId(page);
  await expect(page.getByLabel('Timeframe 4H', { exact: true })).toHaveCount(0);
  await expect(page.locator('.timeframe-buttons button')).toHaveCount(7);
  await expect(page.locator('.data-source')).toContainText('Yahoo normalized browser fixture');
  await expect(page.locator('.data-source')).toContainText(/delayed/i);
  await expect(page.locator('.data-source')).toContainText('fresh');

  const initialDaily = await readMarketCache(page);
  const dailyEntry = initialDaily.find(
    (entry) =>
      entry.providerId === providerId &&
      entry.kind === 'bars' &&
      entry.symbol === 'AAPL' &&
      entry.timeframe === '1D',
  );
  expect(dailyEntry?.value.asOf).toBe(AS_OF);
  expect(dailyEntry?.value.delayed).toBe(true);
  expect(dailyEntry?.value.source).toContain('Yahoo normalized browser fixture');

  for (const timeframe of ['5m', '15m', '30m', '1H', '1D', '1W', '1M'] as const) {
    await page.getByLabel(`Timeframe ${timeframe}`, { exact: true }).click();
    await expect(page.getByLabel(`Timeframe ${timeframe}`, { exact: true })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await loaded(page);
  }
  for (const timeframe of ['5m', '15m', '30m', '1H', '1D', '1W', '1M'] as const) {
    expect(counts.get(`AAPL:${timeframe}`) ?? 0).toBeGreaterThan(0);
  }

  const firstFifteenMinuteFetches = counts.get('AAPL:15m');
  await page.getByLabel('Timeframe 15m', { exact: true }).click();
  await loaded(page);
  expect(counts.get('AAPL:15m')).toBe(firstFifteenMinuteFetches);
  await expect(page.locator('.data-source')).toContainText('cached');
  const cache = await readMarketCache(page);
  expect(
    cache.some(
      (entry) =>
        entry.providerId === providerId &&
        entry.kind === 'bars' &&
        entry.symbol === 'AAPL' &&
        entry.timeframe === '15m',
    ),
  ).toBe(true);
});

test('expired Yahoo bars remain visible with a stale label when refresh fails', async ({
  page,
}) => {
  await page.clock.install({ time: new Date(AS_OF * 1000) });
  let offline = false;
  await installYahooRoutes(
    page,
    () => 'baseline',
    () => offline,
  );
  await page.goto('/');
  await loaded(page);
  await page.getByLabel('Market data source', { exact: true }).selectOption('yahoo');
  await loaded(page);
  const providerId = await yahooCacheProviderId(page);
  await expect(page.locator('.data-source')).toContainText('fresh');
  expect(
    (await readMarketCache(page)).some(
      (entry) =>
        entry.providerId === providerId &&
        entry.kind === 'bars' &&
        entry.symbol === 'AAPL' &&
        entry.timeframe === '1D',
    ),
  ).toBe(true);

  await page.clock.fastForward(60_001);
  offline = true;
  if (await page.locator('.mobile-nav').isVisible()) {
    await page
      .locator('.mobile-nav')
      .getByRole('button', { name: 'Research', exact: true })
      .click();
    await page.getByLabel('Research panel', { exact: true }).selectOption('settings');
  }
  await page
    .getByRole('button', { name: 'Refresh market data', exact: true })
    .filter({ visible: true })
    .click();
  await loaded(page);
  await expect(page.locator('.data-source')).toContainText('STALE OFFLINE CACHE');
  await expect(page.locator('.ohlc-header')).toContainText('O ');
  await expect(page.locator('.chart-loading.error')).toHaveCount(0);
});

test('visible active alerts baseline history, trigger once on a newly closed bar, and persist deduplication', async ({
  page,
}) => {
  await page.clock.install({ time: new Date(AS_OF * 1000) });
  let mode: YahooMode = 'baseline';
  const counts = await installYahooRoutes(page, () => mode);
  await page.goto('/');
  await loaded(page);
  await page.getByLabel('Market data source', { exact: true }).selectOption('yahoo');
  await loaded(page);
  await expect.poll(() => counts.get('AAPL:1D') ?? 0).toBe(2);

  await openAlerts(page);
  await page.getByLabel('Alert price level', { exact: true }).fill('100');
  await page.getByLabel('Level trigger direction', { exact: true }).selectOption('above');
  await page.getByRole('button', { name: 'Create alert', exact: true }).click();
  await expect(page.locator('.alert-card')).toHaveCount(1);
  await expect(page.locator('.alert-state dd').first()).toHaveText('Never');
  const baselineTime = Date.parse('2026-10-02T00:00:00Z') / 1000;
  await expect.poll(async () => (await readAlerts(page))[0]?.lastEvaluated).toBe(baselineTime);
  expect((await readAlerts(page))[0]?.lastTriggered).toBeNull();

  mode = 'crossing';
  await page.evaluate(() =>
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }),
  );
  await page.clock.fastForward(60_001);
  expect(counts.get('AAPL:1D')).toBe(2);

  await page.evaluate(() =>
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }),
  );
  await page.clock.fastForward(60_001);
  await expect.poll(() => counts.get('AAPL:1D') ?? 0).toBe(3);
  const crossingTime = Date.parse('2026-10-05T00:00:00Z') / 1000;
  await expect.poll(async () => (await readAlerts(page))[0]?.lastTriggered).toBe(crossingTime);
  expect((await readAlerts(page))[0]?.lastEvaluated).toBe(crossingTime);

  await page.clock.fastForward(60_001);
  await expect.poll(() => counts.get('AAPL:1D') ?? 0).toBe(4);
  const persisted = (await readAlerts(page))[0];
  expect(persisted?.lastTriggered).toBe(crossingTime);
  expect(persisted?.lastEvaluated).toBe(crossingTime);
  await expect(page.locator('.alert-card')).toHaveCount(1);
});

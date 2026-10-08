import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  normalizeYahooCalendarResponse,
  normalizeYahooEvents,
  normalizeYahooResponse,
} from '../src/market-data/YahooNormalizer';
import { snapshotSchema } from '../src/market-data/SnapshotSchema';

test.use({ serviceWorkers: 'block' });

const fixturePath = (name: string) => resolve('tests/fixtures/market', name);
const readFixture = (name: string): unknown =>
  JSON.parse(readFileSync(fixturePath(name), 'utf8')) as unknown;

function buildSmciFixturePack() {
  const asOf = Date.now() / 1000;
  const dailyRaw = readFixture('SMCI-current-1d.json');
  const daily = normalizeYahooResponse(dailyRaw, asOf, '1D', 'SMCI');
  const weekly = normalizeYahooCalendarResponse(
    readFixture('SMCI-current-1wk.json'),
    dailyRaw,
    asOf,
    '1W',
    'SMCI',
  );
  const monthly = normalizeYahooCalendarResponse(
    readFixture('SMCI-current-1mo.json'),
    dailyRaw,
    asOf,
    '1M',
    'SMCI',
  );
  if (!daily.quote) throw new Error('SMCI current daily fixture has no quote');
  return snapshotSchema.parse({
    version: 3,
    symbol: 'SMCI',
    generatedAt: asOf,
    results: { '1D': daily, '1W': weekly, '1M': monthly },
    quote: daily.quote,
    events: normalizeYahooEvents(dailyRaw, 'SMCI', asOf),
  });
}

function verifyCalendarPeriod(
  pack: ReturnType<typeof buildSmciFixturePack>,
  timeframe: '1W' | '1M',
) {
  const daily = pack.results['1D'];
  const period = pack.results[timeframe];
  if (!daily || !period) throw new Error(`Snapshot is missing required ${timeframe} bars`);
  const currentPeriod = period.normalization?.currentPeriod;
  expect(currentPeriod).toBeDefined();
  if (!currentPeriod) throw new Error(`${timeframe} snapshot has no current-period provenance`);
  const periodEnd =
    timeframe === '1W'
      ? currentPeriod.periodStart + 7 * 86400
      : (() => {
          const date = new Date(currentPeriod.periodStart * 1000);
          return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1) / 1000;
        })();
  const dailyBars = daily.bars.filter(
    (bar) => bar.time >= currentPeriod.periodStart && bar.time < periodEnd,
  );
  const aggregate = period.bars.at(-1)!;

  expect(currentPeriod.method).toBe('daily-ohlcv');
  expect(currentPeriod.timeframe).toBe(timeframe);
  expect(currentPeriod.quoteAsOf).toBe(pack.quote.asOf);
  expect(dailyBars.length).toBe(currentPeriod.dailyCount);
  expect(dailyBars[0]?.time).toBe(currentPeriod.firstDailyTime);
  expect(dailyBars.at(-1)?.time).toBe(currentPeriod.lastDailyTime);
  expect(dailyBars.length).toBeGreaterThan(0);
  expect(aggregate.open).toBe(dailyBars[0]!.open);
  expect(aggregate.high).toBe(Math.max(...dailyBars.map((bar) => bar.high)));
  expect(aggregate.low).toBe(Math.min(...dailyBars.map((bar) => bar.low)));
  expect(aggregate.close).toBe(dailyBars.at(-1)!.close);
  expect(aggregate.volume).toBe(dailyBars.reduce((sum, bar) => sum + bar.volume, 0));
  expect(Math.abs(aggregate.close - pack.quote.price)).toBeLessThanOrEqual(0.01);
}

test('static snapshot keeps the current quote aligned across daily, weekly and monthly bars', async ({
  page,
}) => {
  const pack = buildSmciFixturePack();
  const apiRequests: string[] = [];
  page.on('request', (request) => {
    if (/\/api\//.test(new URL(request.url()).pathname)) apiRequests.push(request.url());
  });
  await page.route('**/market-data/*.json', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/SMCI.json')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(pack),
      });
      return;
    }
    await route.fulfill({ status: 404, body: 'No fixture for this symbol' });
  });

  await page.goto('./');
  await expect(page.getByTestId('active-symbol')).toHaveText('AAPL');
  await expect(page.locator('.chart-loading')).toHaveCount(0);

  // Select the fixture symbol while still on Demo so SnapshotProvider does not
  // request an unrelated AAPL snapshot before the test route is exercised.
  const symbolSearch = page.getByLabel('Symbol search', { exact: true });
  await symbolSearch.fill('SMCI');
  await symbolSearch.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText('SMCI');
  await expect(page.locator('.chart-loading')).toHaveCount(0);

  await page.getByLabel('Market data source', { exact: true }).selectOption('snapshot');
  await expect(page.locator('.demo-badge')).toHaveText('DELAYED SNAPSHOT');
  await expect(page.locator('.chart-loading')).toHaveCount(0);

  for (const timeframe of ['1D', '1W', '1M'] as const) {
    const control = page.getByRole('button', { name: `Timeframe ${timeframe}`, exact: true });
    await control.click();
    await expect(control).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.chart-loading')).toHaveCount(0);

    const result = pack.results[timeframe];
    if (!result) throw new Error(`SMCI fixture is missing ${timeframe} bars`);
    const bars = result.bars;
    const close = bars.at(-1)!.close;
    expect(Math.abs(close - pack.quote.price)).toBeLessThanOrEqual(0.01);
    await expect(page.getByTestId('ohlc-header')).toContainText(`C ${pack.quote.price.toFixed(2)}`);

    if (timeframe !== '1D') verifyCalendarPeriod(pack, timeframe);
  }

  expect(apiRequests).toEqual([]);
});

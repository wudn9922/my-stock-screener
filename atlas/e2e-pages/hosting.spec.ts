import { test, expect, type Page } from '@playwright/test';
import { chooseDrawingTool } from '../e2e/drawing-picker-helper';
import { readFileSync } from 'node:fs';
import { spawn, type ChildProcess } from 'node:child_process';
import { once } from 'node:events';
import { resolve } from 'node:path';
import { normalizeYahooCalendarResponse, normalizeYahooEvents, normalizeYahooResponse } from '../src/market-data/YahooNormalizer';
import { snapshotSchema } from '../src/market-data/SnapshotSchema';
import {
  financialSnapshotSchema,
  type FinancialSnapshot,
} from '../src/fundamentals/FinancialSnapshotSchema';
import type { SymbolState } from '../src/storage/schema';

const base = '/lightweight-drawing-lab/';
async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}
async function closePanel(page: Page) {
  const close = page.getByRole('button', { name: 'Close panel', exact: true });
  if (await close.isVisible()) await close.click();
}
async function openPanel(page: Page, panel: 'indicators' | 'financials') {
  await closePanel(page);
  const nav = page.locator('.mobile-nav');
  if (await nav.isVisible())
    await nav
      .getByRole('button', { name: panel === 'indicators' ? 'SMA' : 'Financials', exact: true })
      .click();
  else await page.getByLabel('Research panel', { exact: true }).selectOption(panel);
}
async function addMA(page: Page, period: number) {
  await openPanel(page, 'indicators');
  await page.getByLabel('SMA period', { exact: true }).fill(String(period));
  await page.getByRole('button', { name: 'Add SMA', exact: true }).click();
}
async function switchSymbol(page: Page, symbol: string) {
  await closePanel(page);
  const search = page.getByLabel('Symbol search', { exact: true });
  await search.fill(symbol);
  await search.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText(symbol);
  await loaded(page);
}
async function addWatchlistTicker(page: Page, ticker: string) {
  const nav = page.locator('.mobile-nav');
  const mobile = await nav.isVisible();
  if (mobile) await nav.getByRole('button', { name: 'Watchlist', exact: true }).click();
  const row = page.getByRole('button', { name: `Select ${ticker}`, exact: true });
  if (!(await row.isVisible())) {
    await page.getByRole('button', { name: 'Add watchlist symbol', exact: true }).click();
    await page.getByLabel('Watchlist ticker', { exact: true }).fill(ticker);
    await page.getByRole('button', { name: 'Add', exact: true }).click();
  }
  await expect(row).toBeVisible();
  if (mobile) await closePanel(page);
}
async function expectWatchlistQuote(page: Page, symbol: string, price: string) {
  const nav = page.locator('.mobile-nav');
  const mobile = await nav.isVisible();
  if (mobile) await nav.getByRole('button', { name: 'Watchlist', exact: true }).click();
  const row = page.getByRole('button', { name: `Select ${symbol}`, exact: true });
  await expect(row).toContainText(price);
  await expect(row).toContainText('delayed');
  if (mobile) await closePanel(page);
}

function snapshotFromActualFixture(symbol: 'SMCI' | 'NFLX') {
  const fixtureAsOf = Date.now() / 1000;
  const readFixture = (file: string) =>
    JSON.parse(
      readFileSync(
        resolve('tests/fixtures/market', file),
        'utf8',
      ),
    ) as unknown;
  const dailyRaw = readFixture(`${symbol}-current-1d.json`);
  const weeklyRaw = readFixture(`${symbol}-current-1wk.json`);
  const monthlyRaw = readFixture(`${symbol}-current-1mo.json`);
  const daily = normalizeYahooResponse(dailyRaw, fixtureAsOf, '1D', symbol);
  const weekly = normalizeYahooCalendarResponse(weeklyRaw, dailyRaw, fixtureAsOf, '1W', symbol);
  const monthly = normalizeYahooCalendarResponse(monthlyRaw, dailyRaw, fixtureAsOf, '1M', symbol);
  if (!daily.quote) throw new Error(`${symbol} fixture has no validated daily quote`);
  return snapshotSchema.parse({
    version: 3,
    symbol,
    generatedAt: fixtureAsOf,
    results: { '1D': daily, '1W': weekly, '1M': monthly },
    quote: daily.quote,
    events: normalizeYahooEvents(dailyRaw, symbol, fixtureAsOf),
  });
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
async function controlled(page: Page) {
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
}

test('project-path PWA preserves scope, drawings, isolated MAs and portable backups without backend requests', async ({
  page,
  isMobile,
  browserName,
}) => {
  const runtimeErrors: string[] = [];
  const apiRequests: string[] = [];
  page.on('pageerror', (error) => runtimeErrors.push(error.message));
  page.on('request', (request) => {
    if (/\/(?:api|lightweight-drawing-lab\/api)\//.test(new URL(request.url()).pathname))
      apiRequests.push(request.url());
  });
  await page.route('**/seed', (route) =>
    route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Cache test</title>' }),
  );
  await page.goto('/seed');
  await page.evaluate(async (path) => {
    await caches.open('atlas-shell-unrelated-old');
    await caches.open(`atlas-shell-${encodeURIComponent(location.origin + path)}-old`);
  }, base);
  await page.goto(base);
  await loaded(page);
  await controlled(page);
  const shell = await page.evaluate(async () => ({
    caches: await caches.keys(),
    scope: (await navigator.serviceWorker.ready).scope,
    manifest: (document.querySelector('link[rel="manifest"]') as HTMLLinkElement).href,
    icons: [
      ...document.querySelectorAll<HTMLLinkElement>(
        'link[rel="icon"],link[rel="apple-touch-icon"]',
      ),
    ].map((link) => link.href),
  }));
  expect(shell.scope).toBe(new URL(base, page.url()).href);
  expect(shell.caches).toContain('atlas-shell-unrelated-old');
  expect(shell.caches).not.toContain(`atlas-shell-${encodeURIComponent(shell.scope)}-old`);
  const manifestResponse = await page.request.get(shell.manifest);
  expect(manifestResponse.ok()).toBe(true);
  const manifest = (await manifestResponse.json()) as {
    start_url: string;
    scope: string;
    icons: { src: string }[];
  };
  expect(new URL(manifest.start_url, shell.manifest).href).toBe(shell.scope);
  expect(new URL(manifest.scope, shell.manifest).href).toBe(shell.scope);
  for (const url of [
    ...shell.icons,
    ...manifest.icons.map((icon) => new URL(icon.src, shell.manifest).href),
  ]) {
    expect(new URL(url).pathname.startsWith(base)).toBe(true);
    expect((await page.request.get(url)).ok()).toBe(true);
  }
  await expect(page.locator('.static-hosting-note')).toHaveText(
    'GitHub Pages：延遲行情與 SEC 財報快照可用，均非即時；即時 Yahoo 仍需 backend。台股財報尚未提供。',
  );
  await expect(page.locator('.static-hosting-note')).toBeVisible();
  await expect(
    page.getByLabel('Market data source', { exact: true }).locator('option[value="yahoo"]'),
  ).toBeDisabled();
  await expect(
    page.getByLabel('Market data source', { exact: true }).locator('option[value="snapshot"]'),
  ).toBeEnabled();
  await addMA(page, 24);
  await page.getByRole('button', { name: 'Lock SMA 24', exact: true }).click();
  await addMA(page, 58);
  await closePanel(page);
  await chooseDrawingTool(page, 'Horizontal Line');
  const box = (await page.getByTestId('chart').boundingBox())!;
  const x = box.x + box.width * 0.45,
    y = box.y + box.height * 0.45;
  if (isMobile && browserName === 'chromium') {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x, y, id: 1 }],
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: x + 10, y: y + 12, id: 1 }],
    });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await cdp.detach();
  } else {
    if (isMobile)
      await page.evaluate(() => {
        for (const type of ['pointerdown', 'pointermove', 'pointerup'])
          document.addEventListener(
            type,
            (event) => Object.defineProperty(event, 'pointerType', { value: 'touch' }),
            { capture: true },
          );
      });
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 10, y + 12, { steps: 5 });
    await page.mouse.up();
  }
  await expect.poll(async () => (await savedSymbol(page, 'AAPL'))?.drawings.length).toBe(1);
  await page.getByRole('button', { name: 'Lock selected drawing', exact: true }).click();
  await expect.poll(async () => (await savedSymbol(page, 'AAPL'))?.drawings[0].locked).toBe(true);
  await switchSymbol(page, 'NVDA');
  await addMA(page, 43);
  await addMA(page, 56);
  await expect
    .poll(async () =>
      (await savedSymbol(page, 'NVDA'))?.indicators.map((indicator) => indicator.period),
    )
    .toEqual([43, 56]);
  expect((await savedSymbol(page, 'NVDA'))?.drawings).toHaveLength(0);
  await switchSymbol(page, 'AAPL');
  await page.reload();
  await loaded(page);
  expect(
    (await savedSymbol(page, 'AAPL'))?.indicators.map((indicator) => [
      indicator.period,
      indicator.locked,
    ]),
  ).toEqual([
    [24, true],
    [58, false],
  ]);
  expect((await savedSymbol(page, 'AAPL'))?.drawings[0].locked).toBe(true);
  await page.getByLabel('Market data source', { exact: true }).selectOption('snapshot');
  await loaded(page);
  await expect(page.locator('.demo-badge')).toHaveText('DELAYED SNAPSHOT');
  expect((await savedSymbol(page, 'AAPL'))?.drawings[0].locked).toBe(true);
  let aaplFinancialSnapshot: FinancialSnapshot | undefined;
  try {
    const response = await page.request.get(
      new URL('financial-data/AAPL.json', page.url()).href,
    );
    if (response.ok()) {
      const parsed = financialSnapshotSchema.safeParse(await response.json());
      if (parsed.success && parsed.data.symbol === 'AAPL') aaplFinancialSnapshot = parsed.data;
    }
  } catch {
    // The unavailable branch below covers a missing same-origin pack.
  }
  await openPanel(page, 'financials');
  const financialPanel = page.getByTestId('financial-panel');
  if (aaplFinancialSnapshot) {
    const latestQuarter = aaplFinancialSnapshot.quarterly[0]!;
    const fiscalLabel = `FY${latestQuarter.fiscalYear}${latestQuarter.fiscalQuarter ? ` Q${latestQuarter.fiscalQuarter}` : ''}`;
    await expect(financialPanel.locator('.financial-provenance-note')).toContainText(
      'SEC snapshot',
    );
    await expect(financialPanel.locator('.financial-provenance-note')).toContainText(
      'Latest included filing:',
    );
    await expect(financialPanel.locator('.financial-filing').first()).toContainText(
      `${aaplFinancialSnapshot.issuerName} · SEC CIK ${aaplFinancialSnapshot.cik}`,
    );
    const fiscalPeriod = financialPanel.getByLabel('Fiscal period', { exact: true });
    await expect(fiscalPeriod).toHaveValue(latestQuarter.periodEnd);
    await expect(
      fiscalPeriod.locator(`option[value="${latestQuarter.periodEnd}"]`),
    ).toHaveText(`${fiscalLabel} · ${latestQuarter.periodEnd}`);
  } else {
    await expect(financialPanel).toContainText('AAPL SEC 財報快照目前不可用');
    await expect(financialPanel).toContainText('K 線與 drawing 可繼續使用');
    await expect(financialPanel).not.toContainText('ACTUAL');
  }
  await closePanel(page);
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export settings', exact: true }).click();
  const download = await downloadEvent;
  const backup = JSON.parse(readFileSync((await download.path())!, 'utf8'));
  backup.app.provider = 'yahoo';
  await page
    .getByLabel('Settings file', { exact: true })
    .setInputFiles({
      name: 'portable.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(backup)),
    });
  await expect(page.getByRole('status').filter({ hasText: '設定已還原' })).toBeVisible();
  await expect(page.getByLabel('Market data source', { exact: true })).toHaveValue('yahoo');
  await expect(page.getByRole('button', { name: '使用離線 Demo', exact: true })).toBeVisible();
  expect((await savedSymbol(page, 'AAPL'))?.drawings[0].locked).toBe(true);
  await page.getByRole('button', { name: '使用離線 Demo', exact: true }).click();
  await loaded(page);
  await expect(page.getByLabel('Market data source', { exact: true })).toHaveValue('demo');
  expect(apiRequests).toEqual([]);
  expect(runtimeErrors).toEqual([]);
});

async function isolatedPreview(): Promise<ChildProcess> {
  const child = spawn(
    process.execPath,
    [
      resolve('node_modules/vite/bin/vite.js'),
      'preview',
      '--outDir',
      'dist-pages',
      '--host',
      '127.0.0.1',
      '--port',
      '4176',
      '--strictPort',
    ],
    {
      stdio: 'pipe',
      env: { ...process.env, VITE_STATIC_HOSTING: '1', VITE_PUBLIC_BASE: base },
    },
  );
  await new Promise<void>((resolve, reject) => {
    child.stdout!.on('data', (data) => {
      if (data.toString().includes('4176')) resolve();
    });
    child.once('error', reject);
    child.once('exit', (code) => reject(new Error(`Isolated preview exited ${code}`)));
  });
  return child;
}

test('scoped production shell and local Demo settings reopen offline', async ({
  page,
  context,
  browserName,
}) => {
  let server: ChildProcess | undefined;
  try {
    // WebKit forced-offline navigation fails before worker dispatch; stop a real isolated origin instead.
    if (browserName === 'webkit') server = await isolatedPreview();
    await page.goto(`http://127.0.0.1:${server ? 4176 : 4175}${base}`);
    await loaded(page);
    await controlled(page);
    await addMA(page, 24);
    await closePanel(page);
    await expect.poll(async () => (await savedSymbol(page, 'AAPL'))?.indicators.length).toBe(1);
    if (server) {
      const exited = once(server, 'exit');
      server.kill('SIGTERM');
      await exited;
      server = undefined;
    } else await context.setOffline(true);
    await page.reload();
    await loaded(page);
    await expect(page.locator('.indicator-chip')).toContainText('SMA 24');
    await expect(page.getByLabel('Market data source', { exact: true })).toHaveValue('demo');
  } finally {
    server?.kill('SIGTERM');
    await context.setOffline(false);
  }
});

test.describe('delayed snapshot source', () => {
  test.use({ serviceWorkers: 'block' });

  test('Pages exposes 1W/1M delayed SMCI/NFLX snapshots without backend fallback', async ({
    page,
  }) => {
    const apiRequests: string[] = [];
    const snapshotRequests: string[] = [];
    const runtimeErrors: string[] = [];
    const fixtures = {
      SMCI: snapshotFromActualFixture('SMCI'),
      NFLX: snapshotFromActualFixture('NFLX'),
    };
    const nflxPrice = fixtures.NFLX.quote.price.toFixed(2);
    expect(fixtures.SMCI.results['1W']?.bars.at(-1)).toMatchObject({ close: 44.94, volume: 90284251 });
    expect(fixtures.SMCI.results['1M']?.bars.at(-1)).toMatchObject({ close: 44.94, volume: 161285951 });
    expect(fixtures.NFLX.results['1M']?.bars.at(-1)?.close).toBeCloseTo(Number(nflxPrice), 2);
    page.on('pageerror', (error) => runtimeErrors.push(error.message));
    page.on('request', (request) => {
      if (/\/api\//.test(new URL(request.url()).pathname)) apiRequests.push(request.url());
    });
    await page.route('**/market-data/*.json', async (route) => {
      const file = new URL(route.request().url()).pathname.split('/').at(-1) ?? '';
      const symbol = decodeURIComponent(file.replace(/\.json$/, ''));
      snapshotRequests.push(symbol);
      const snapshot = fixtures[symbol as keyof typeof fixtures];
      if (!snapshot) {
        await route.fulfill({
          status: 404,
          contentType: 'application/json',
          body: JSON.stringify({ error: 'No fixture for this public snapshot symbol' }),
        });
        return;
      }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(snapshot) });
    });

    await page.goto(base);
    await loaded(page);
    await addWatchlistTicker(page, 'SMCI');
    await addWatchlistTicker(page, 'NFLX');
    await switchSymbol(page, 'SMCI');
    await page.getByLabel('Market data source', { exact: true }).selectOption('snapshot');
    await loaded(page);
    await expect(page.locator('.demo-badge')).toHaveText('DELAYED SNAPSHOT');
    await expect(page.getByTestId('ohlc-header')).toContainText('C 44.94');
    await expectWatchlistQuote(page, 'SMCI', '44.94');

    const weekly = page.getByRole('button', { name: 'Timeframe 1W', exact: true });
    await expect(weekly).toBeEnabled();
    await weekly.click();
    await loaded(page);
    await expect(page.getByTestId('ohlc-header')).toContainText('C 44.94');
    await expect(page.locator('.data-source')).toContainText('本週 K由日 K彙總');

    const monthly = page.getByRole('button', { name: 'Timeframe 1M', exact: true });
    await expect(monthly).toBeEnabled();
    await monthly.click();
    await loaded(page);
    await expect(page.getByTestId('ohlc-header')).toContainText('C 44.94');
    await expect(page.locator('.data-source')).toContainText('本月 K由日 K彙總');

    await switchSymbol(page, 'NFLX');
    await expect(page.getByTestId('ohlc-header')).toContainText(`C ${nflxPrice}`);
    await expectWatchlistQuote(page, 'NFLX', nflxPrice);
    await expect(page.getByLabel('Market data source', { exact: true })).toHaveValue('snapshot');
    await page.getByRole('button', { name: 'Timeframe 1W', exact: true }).click();
    await loaded(page);
    await expect(page.getByTestId('ohlc-header')).toContainText(`C ${nflxPrice}`);
    await monthly.click();
    await loaded(page);

    await page.getByLabel('Symbol search', { exact: true }).fill('ZZZZ');
    await page.getByLabel('Symbol search', { exact: true }).press('Enter');
    await expect(page.getByTestId('active-symbol')).toHaveText('ZZZZ');
    await expect(page.locator('.chart-loading.error')).toContainText('ZZZZ 暫無延遲快照');
    await expect(page.locator('.demo-badge')).toHaveText('DELAYED SNAPSHOT');
    await expect(page.getByLabel('Market data source', { exact: true })).toHaveValue('snapshot');
    await expect(page.getByRole('button', { name: '使用離線 Demo', exact: true })).toBeVisible();
    expect(snapshotRequests).toContain('SMCI');
    expect(snapshotRequests).toContain('NFLX');
    expect(snapshotRequests).toContain('ZZZZ');
    expect(apiRequests).toEqual([]);
    expect(runtimeErrors).toEqual([]);
  });
});

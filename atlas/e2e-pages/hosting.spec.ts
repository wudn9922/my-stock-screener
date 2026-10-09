import { test, expect, type Page } from '@playwright/test';
import { clickExport, expectProvider, openPanel as openWorkspacePanel, searchSymbol, setProvider } from '../e2e/site-helpers';
import { chooseDrawingTool } from '../e2e/drawing-picker-helper';
import { readFileSync } from 'node:fs';
import { spawn, type ChildProcess } from 'node:child_process';
import { once } from 'node:events';
import { resolve } from 'node:path';
import { normalizeYahooCalendarResponse, normalizeYahooEvents, normalizeYahooResponse } from '../src/market-data/YahooNormalizer';
import { snapshotSchema } from '../src/market-data/SnapshotSchema';
import type { SymbolState } from '../src/storage/schema';

const base = '/lightweight-drawing-lab/';
async function loaded(page: Page) {
  await expect(page.locator('.chart-loading')).toHaveCount(0);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
}
async function closePanel(page: Page) {
  const close = page.getByRole('button', { name: '關閉面板', exact: true });
  if (await close.isVisible()) await close.click();
}
async function openPanel(page: Page, panel: 'indicators') {
  await openWorkspacePanel(page, panel);
}
async function addMA(page: Page, period: number) {
  await openPanel(page, 'indicators');
  await page.getByLabel('SMA 週期', { exact: true }).fill(String(period));
  await page.getByRole('button', { name: '新增 SMA', exact: true }).click();
}
async function switchSymbol(page: Page, symbol: string) {
  await closePanel(page);
  await searchSymbol(page, symbol);
  await expect(page.getByTestId('active-symbol')).toHaveText(symbol);
  await loaded(page);
}
async function addWatchlistTicker(page: Page, ticker: string) {
  await openWorkspacePanel(page, 'watchlist');
  const row = page.getByRole('button', { name: `選擇 ${ticker}`, exact: true });
  if (!(await row.isVisible())) {
    await page.getByRole('button', { name: '新增自選股', exact: true }).click();
    await page.getByLabel('自選股代號', { exact: true }).fill(ticker);
    await page.getByRole('button', { name: '加入', exact: true }).click();
  }
  await expect(row).toBeVisible();
  await closePanel(page);
}
async function expectWatchlistQuote(page: Page, symbol: string, price: string) {
  await openWorkspacePanel(page, 'watchlist');
  const row = page.getByRole('button', { name: `選擇 ${symbol}`, exact: true });
  await expect(row).toContainText(price);
  await expect(row).toContainText('延遲');
  await closePanel(page);
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
  await expect(page.locator('.static-hosting-note')).toHaveText('DEMO 價格為模擬資料，並非市場行情。');
  await expect(page.locator('.static-hosting-note')).toBeVisible();
  await openWorkspacePanel(page, 'settings');
  const optionDisabled = (value: string) =>
    page.getByLabel('資料來源', { exact: true }).locator(`option[value="${value}"]`).evaluate((option) => (option as HTMLOptionElement).disabled);
  expect(await optionDisabled('yahoo')).toBe(true);
  expect(await optionDisabled('snapshot')).toBe(false);
  await closePanel(page);
  await addMA(page, 24);
  await page.getByRole('button', { name: '鎖定 SMA 24', exact: true }).click();
  await addMA(page, 58);
  await closePanel(page);
  await chooseDrawingTool(page, '水平線');
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
  await page.getByRole('button', { name: '鎖定所選畫線', exact: true }).click();
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
  await setProvider(page, 'snapshot');
  await loaded(page);
  await expect(page.locator('.demo-badge')).toHaveText('延遲');
  expect((await savedSymbol(page, 'AAPL'))?.drawings[0].locked).toBe(true);
  await closePanel(page);
  const downloadEvent = page.waitForEvent('download');
  await clickExport(page);
  const download = await downloadEvent;
  const backup = JSON.parse(readFileSync((await download.path())!, 'utf8'));
  backup.app.provider = 'yahoo';
  await page
    .getByLabel('設定檔', { exact: true })
    .setInputFiles({
      name: 'portable.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(backup)),
    });
  await expect(page.getByRole('status').filter({ hasText: '設定已還原' })).toBeVisible();
  await expectProvider(page, 'yahoo');
  await expect(page.getByRole('button', { name: '改看離線模擬資料', exact: true })).toBeVisible();
  expect((await savedSymbol(page, 'AAPL'))?.drawings[0].locked).toBe(true);
  await page.getByRole('button', { name: '改看離線模擬資料', exact: true }).click();
  await loaded(page);
  await expectProvider(page, 'demo');
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
    await expectProvider(page, 'demo');
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
    await setProvider(page, 'snapshot');
    await loaded(page);
    await expect(page.locator('.demo-badge')).toHaveText('延遲');
    await expect(page.getByTestId('ohlc-header')).toContainText('C 44.94');
    await expectWatchlistQuote(page, 'SMCI', '44.94');

    const weekly = page.getByRole('button', { name: '週期 1W', exact: true });
    await expect(weekly).toBeEnabled();
    await weekly.click();
    await loaded(page);
    await expect(page.getByTestId('ohlc-header')).toContainText('C 44.94');
    await expect(page.locator('.data-source')).toContainText('本週 K由日 K彙總');

    const monthly = page.getByRole('button', { name: '週期 1M', exact: true });
    await expect(monthly).toBeEnabled();
    await monthly.click();
    await loaded(page);
    await expect(page.getByTestId('ohlc-header')).toContainText('C 44.94');
    await expect(page.locator('.data-source')).toContainText('本月 K由日 K彙總');

    await switchSymbol(page, 'NFLX');
    await expect(page.getByTestId('ohlc-header')).toContainText(`C ${nflxPrice}`);
    await expectWatchlistQuote(page, 'NFLX', nflxPrice);
    await expectProvider(page, 'snapshot');
    await page.getByRole('button', { name: '週期 1W', exact: true }).click();
    await loaded(page);
    await expect(page.getByTestId('ohlc-header')).toContainText(`C ${nflxPrice}`);
    await monthly.click();
    await loaded(page);

    await searchSymbol(page, 'ZZZZ');
    await expect(page.getByTestId('active-symbol')).toHaveText('ZZZZ');
    await expect(page.locator('.chart-loading.error')).toContainText('ZZZZ 暫無延遲快照');
    await expect(page.locator('.demo-badge')).toHaveText('延遲');
    await expectProvider(page, 'snapshot');
    await expect(page.getByRole('button', { name: '改看離線模擬資料', exact: true })).toBeVisible();
    expect(snapshotRequests).toContain('SMCI');
    expect(snapshotRequests).toContain('NFLX');
    expect(snapshotRequests).toContain('ZZZZ');
    expect(apiRequests).toEqual([]);
    expect(runtimeErrors).toEqual([]);
  });
});

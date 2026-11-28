import { expect, test, type Page } from '@playwright/test';
import { mockMarketLogin } from './account-fixture';
import { mockSite } from './mocks';

const DAY = '2026-11-27';
const REGULAR = Date.parse(DAY + 'T11:20:00+08:00');
const CLOSE = Date.parse(DAY + 'T13:30:00+08:00') / 1000;
const file = (underlying: string, warrants: ReturnType<typeof warrant>[]) => ({
  version: 1, underlying, asOf: { TWSE: '2026-11-26', TPEx: '2026-11-26' }, warrants,
});
const warrant = (code: string, type = 'call', expiryDate = '2027-01-08', market = 'TWSE') => ({
  code, symbol: code + (market === 'TPEx' ? '.TWO' : '.TW'), name: '測試權證 ' + code, type,
  strikePrice: 100, exerciseRatio: 0.01, expiryDate, lastTradingDate: expiryDate, issuer: '測試券商', market,
});
const book = (bid: number | null, ask: number | null, quoteTime: number | null, status = 'available') => ({
  status, bid, ask, bidSize: bid == null ? null : 10, askSize: ask == null ? null : 10, quoteTime,
});
const reply = (asOf: number, session: 'regular' | 'closed', results: Record<string, ReturnType<typeof book>>) => ({
  asOf, source: 'twse-mis', session, results,
});

async function site(page: Page, instant: number) {
  await page.clock.install({ time: instant });
  await mockMarketLogin(page);
  await mockSite(page);
  await page.setViewportSize({ width: 390, height: 844 });
}

const list = (page: Page) => page.getByLabel('權證清單').getByRole('article');
const row = (page: Page, symbol: string) => list(page).and(page.getByRole('article', { name: symbol, exact: true }));
const section = (page: Page, name: string) => page.getByRole('navigation', { name: '市場分頁' }).getByRole('link', { name, exact: true });

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(complete => { resolve = complete; });
  return { promise, resolve };
}

test('ranks every matching warrant across sequential quote batches before paging', async ({ page }, info) => {
  await site(page, CLOSE * 1_000 + 30 * 60_000);
  const rows = Array.from({ length: 24 }, (_, i) => warrant(
    String(66186 + i).padStart(6, '0'), i === 20 ? 'put' : 'call', i === 20 ? '2027-02-12' : '2027-01-08',
  ));
  await page.route('**/report/warrants/2330.TW.json', route => route.fulfill({
    json: file('2330.TW', [...rows, warrant('066300', 'call', '2026-11-26')]),
  }));
  const requests: string[][] = [];
  await page.route('https://edge.test/functions/v1/market-chart**', route => {
    const query = new URL(route.request().url()).searchParams.get('warrantQuotes');
    if (query === null) return route.fallback();
    const symbols = query.split(',');
    requests.push(symbols);
    return route.fulfill({ json: reply(CLOSE + 1800, 'closed', Object.fromEntries(symbols.map(symbol => [
      symbol, symbol === '066209.TW' ? book(0.68, 0.69, CLOSE)
        : symbol === '066206.TW' ? book(1, null, CLOSE)
        : symbol === '066207.TW' ? book(0, 0.80, CLOSE)
        : symbol === '066208.TW' ? book(null, null, CLOSE - 300, 'stale')
        : book(0.45, 0.70, CLOSE),
    ]))) });
  });
  await page.goto('./?page=warrants&symbol=2330.TW');
  await expect(section(page, '權證')).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText(/已查詢 20／24 檔/)).toBeVisible();
  await expect(page.getByText('分批載入中，排序尚未完成')).toBeVisible();
  expect(requests.map(request => request.length)).toEqual([20]);

  await page.clock.fastForward(4_100);
  await expect(page.getByText('全部結果已查詢，依目前可用價差率排序')).toBeVisible();
  await expect(list(page).first()).toHaveAttribute('aria-label', '066209.TW');
  await expect(list(page).first()).toContainText('1.46%');
  await expect(page.getByText(/已查詢 24／24 檔 · 24 檔有來源結果 · 21 檔可比較/)).toBeVisible();
  expect(requests.map(request => request.length)).toEqual([20, 4]);
  expect(requests.flat()).not.toContain('066300.TW');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  if (info.project.name === 'iphone-webkit' && process.env.ATLAS_WARRANT_SCREENSHOT === '1') {
    await list(page).first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath('warrants-390.png'), scale: 'css' });
  }

  await page.getByRole('button', { name: '下一頁' }).click();
  await expect(list(page)).toHaveCount(4);
  const single = row(page, '066206.TW');
  await expect(single.locator('.warrant-book dd')).toHaveText(['1', '—', '—']);
  await expect(row(page, '066207.TW').locator('.warrant-book dd')).toHaveText(['—', '0.8', '—']);
  await expect(row(page, '066208.TW')).toContainText('過期報價');
  await expect(row(page, '066208.TW').locator('.warrant-book dd')).toHaveText(['—', '—', '—']);
  expect(requests).toHaveLength(2);

  await page.getByLabel('認購／認售').selectOption('put');
  await expect(list(page)).toHaveCount(1);
  await expect(list(page).first()).toHaveAttribute('aria-label', '066206.TW');
  await page.getByLabel('到期日自').fill('2027-02-13');
  await expect(page.getByText('沒有符合條件的權證。')).toBeVisible();
  await page.getByLabel('到期日至').fill('2027-01-01');
  await expect(page.getByText('開始日期不可晚於結束日期。')).toBeVisible();
  const callsWithEmptyFilter = requests.length;
  await page.clock.fastForward(4_100);
  expect(requests).toHaveLength(callsWithEmptyFilter);
});

test('expires intraday prices, stops off-page and hidden requests, and starts afresh on return or symbol change', async ({ page }) => {
  await site(page, REGULAR);
  await page.route('**/report/warrants/2330.TW.json', route => route.fulfill({ json: file('2330.TW', [warrant('066186')]) }));
  await page.route('**/report/warrants/6488.TWO.json', route => route.fulfill({ json: file('6488.TWO', [warrant('714060', 'put', '2027-01-08', 'TPEx')]) }));
  const requests: string[] = [];
  const returning = deferred();
  const previousSymbol = deferred();
  const cancellations: string[] = [];
  page.on('requestfailed', request => {
    const query = new URL(request.url()).searchParams.get('warrantQuotes');
    if (query !== null) cancellations.push(query);
  });
  await page.route('https://edge.test/functions/v1/market-chart**', async route => {
    const query = new URL(route.request().url()).searchParams.get('warrantQuotes');
    if (query === null) return route.fallback();
    requests.push(query);
    if (requests.length === 2) await returning.promise;
    if (requests.length === 3) {
      await previousSymbol.promise;
      return;
    }
    const now = await page.evaluate(() => Math.floor(Date.now() / 1000));
    return route.fulfill({ json: reply(now, 'regular', {
      [query]: requests.length === 1 ? book(0.68, 0.69, now - 58)
        : query === '714060.TWO' ? book(1.1, 1.2, now)
        : book(0.50, 0.51, now),
    }) });
  });
  await page.goto('./?page=warrants&symbol=2330.TW');
  await expect(row(page, '066186.TW').locator('.warrant-book dd')).toHaveText(['0.68', '0.69', '1.46%']);
  await page.clock.fastForward(3_500);
  await expect(row(page, '066186.TW')).toContainText('過期報價');
  await expect(row(page, '066186.TW').locator('.warrant-book dd')).toHaveText(['—', '—', '—']);

  await section(page, '全球').click();
  await expect(page).toHaveURL(/page=world/);
  await page.clock.fastForward(90_000);
  expect(requests).toEqual(['066186.TW']);
  await section(page, '權證').click();
  try {
    await expect.poll(() => requests.length).toBe(2);
    await expect(row(page, '066186.TW').locator('.warrant-book dd')).toHaveText(['—', '—', '—']);
  } finally {
    returning.resolve();
  }
  await expect(row(page, '066186.TW').locator('.warrant-book dd')).toHaveText(['0.5', '0.51', '1.98%']);

  const setHidden = (hidden: boolean) => page.evaluate(value => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => value });
    document.dispatchEvent(new Event('visibilitychange'));
  }, hidden);
  await setHidden(true);
  await page.clock.fastForward(90_000);
  expect(requests).toHaveLength(2);
  await setHidden(false);
  await expect.poll(() => requests.length).toBe(3);
  const search = page.getByRole('region', { name: '權證篩選' }).getByRole('combobox', { name: '搜尋股票' });
  try {
    await search.fill('6488.TWO');
    await search.press('Enter');
    await expect(page).toHaveURL(/page=warrants&symbol=6488.TWO/);
    await expect.poll(() => cancellations).toContain('066186.TW');
  } finally {
    previousSymbol.resolve();
  }
  await expect(list(page)).toHaveCount(1);
  await expect(list(page).first()).toHaveAttribute('aria-label', '714060.TWO');
  await expect(list(page).first().locator('.warrant-book dd')).toHaveText(['—', '—', '—']);
  await page.clock.fastForward(4_100);
  await expect(list(page).first().locator('.warrant-book dd')).toHaveText(['1.1', '1.2', '8.70%']);
  expect(requests.at(-1)).toBe('714060.TWO');
});

test('resumes the outstanding batch after Retry-After and discloses incomplete coverage', async ({ page }) => {
  await site(page, REGULAR);
  const rows = Array.from({ length: 21 }, (_, i) => warrant(String(66186 + i).padStart(6, '0')));
  await page.route('**/report/warrants/2330.TW.json', route => route.fulfill({ json: file('2330.TW', rows) }));
  const requests: string[][] = [];
  await page.route('https://edge.test/functions/v1/market-chart**', async route => {
    const query = new URL(route.request().url()).searchParams.get('warrantQuotes');
    if (query === null) return route.fallback();
    const symbols = query.split(',');
    requests.push(symbols);
    const now = await page.evaluate(() => Math.floor(Date.now() / 1000));
    if (requests.length === 2)
      return route.fulfill({
        status: 429,
        headers: { 'Retry-After': '8' },
        json: { ok: false, code: 'USER_WINDOW', error: '已達查詢上限', retryAfterSeconds: 8 },
      });
    return route.fulfill({ json: reply(now, 'regular', Object.fromEntries(symbols.map(symbol => [
      symbol, requests.length === 3 ? book(null, null, null, 'source_unavailable') : book(0.5, 0.55, now),
    ]))) });
  });
  await page.goto('./?page=warrants&symbol=2330.TW');
  await expect(page.getByText(/已查詢 20／21 檔/)).toBeVisible();
  await page.clock.fastForward(4_100);
  await expect(page.getByText('已達查詢上限')).toBeVisible();
  await page.clock.fastForward(4_100);
  expect(requests).toHaveLength(2);
  await page.clock.fastForward(4_100);
  await expect(page.getByText('部分報價暫不可用，排序涵蓋已取得的結果')).toBeVisible();
  await expect(page.getByText(/已查詢 21／21 檔 · 20 檔有來源結果 · 20 檔可比較/)).toBeVisible();
  expect(requests.map(values => values.length)).toEqual([20, 1, 1]);
  expect(requests[2]).toEqual(requests[1]);
});

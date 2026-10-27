import { expect, test } from '@playwright/test';
import { buildReport, mockSite, screenerFixtures, yahooChart } from './mocks';
import { mockDeviceAccount } from './account-fixture';

const trending = {
  version: 1, source: 'Yahoo Finance US trending · LINE 財經報告',
  fetchedAt: '2026-10-27T09:00:00Z', sentAt: '2026-10-27T09:05:00Z',
  symbols: ['NVDA', 'AAPL', 'AMD', 'TSLA', 'MSFT'],
};

test('LINE stocks open a chart and repeated updates request only that stock; failure retains the chart', async ({ page }, testInfo) => {
  await mockSite(page, { fundamentals: true });
  await page.route('**/report/trending-stocks.json', route => route.fulfill({ json: trending }));
  await page.goto('./?page=screener');
  await page.getByRole('button', { name: /^LINE 熱門/ }).click();
  const list = page.getByRole('list', { name: 'LINE 熱門股票清單', exact: true });
  await expect(list.locator('.row-id b')).toHaveText(trending.symbols);
  await list.getByRole('link').filter({ hasText: 'NVDA' }).click();
  const update = page.getByRole('button', { name: '更新 NVDA K 線', exact: true });
  await expect(update).toBeEnabled();
  const header = page.getByTestId('header-quote');
  const originalPrice = (await header.textContent())!;
  const refreshed: URL[] = [];
  let fail = false;
  await page.route('https://edge.test/functions/v1/market-chart**', async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.has('_refresh')) refreshed.push(url);
    if (fail) return route.fulfill({ status: 502, json: { error: 'upstream error' } });
    const body = yahooChart('NVDA', url.searchParams.get('interval')!);
    const result = body.chart.result[0];
    result.meta.regularMarketPrice += 10;
    const quotes = result.indicators.quote[0];
    for (const values of [quotes.open, quotes.high, quotes.low, quotes.close]) {
      for (let i = 0; i < values.length; i++) values[i] += 10;
    }
    return route.fulfill({ json: body });
  });
  await update.click();
  await expect(page.getByRole('status').filter({ hasText: '已更新 NVDA 的 1D K 線' })).toBeVisible();
  await expect(header).not.toHaveText(originalPrice);
  await expect(update).toBeEnabled();
  await update.click();
  await expect.poll(() => refreshed.length).toBe(2);
  expect(refreshed.every(url => url.searchParams.get('symbol') === 'NVDA')).toBe(true);
  const price = (await header.textContent())!;
  await expect(update).toBeEnabled();
  fail = true;
  await update.click();
  await expect(page.getByRole('status').filter({ hasText: 'NVDA 更新失敗，保留原有 K 線' })).toBeVisible();
  await expect(header).toHaveText(price);
  await expect(page.getByTestId('chart')).toBeVisible();
  await expect(page.locator('.chart-loading.error')).toHaveCount(0);
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath('single-stock-refresh.png') });
});

test('LINE group is US-only, next to full market, and retains LINE order, universe metrics and unmatched symbols', async ({ page }) => {
  const report = buildReport(20);
  report.reportDate = '2026-10-29';
  await mockSite(page, { report, fundamentals: true });
  const data = { ...trending, symbols: ['NVDA', 'NOTFOUND', 'AAPL', 'AMD', 'MSFT'] };
  await page.route('**/report/trending-stocks.json', route => route.fulfill({ json: data }));
  await page.goto('./?page=screener');
  const rows = page.locator('.group-chips .chip-row');
  await expect(rows.nth(0).getByRole('button', { name: /LINE 熱門/ })).toHaveCount(0);
  await expect(rows.nth(1).getByRole('button').nth(0)).toHaveAttribute('aria-label', '美股全市場篩選');
  await expect(rows.nth(1).getByRole('button').nth(1)).toHaveText('LINE 熱門');
  await rows.nth(1).getByRole('button', { name: /LINE 熱門/ }).click();
  await expect(page).toHaveURL(/group=line-trending-us/);
  const list = page.getByRole('list', { name: 'LINE 熱門股票清單', exact: true });
  await expect(list.locator('.row-id b')).toHaveText(data.symbols);
  const missing = list.getByRole('link').filter({ hasText: 'NOTFOUND' });
  await expect(missing).toHaveClass(/is-unmatched/);
  await expect(missing).toContainText('未符合');
  await expect(missing).toContainText('未出現在美股全市場資料中');
  await expect(missing).toHaveAttribute('href', /symbol=NOTFOUND/);
  await expect(page.getByLabel('LINE 熱門資料時間')).toContainText('抓取時間 2026/10/27 17:00');
  await expect(page.getByLabel('LINE 熱門資料時間')).toContainText('LINE 推播時間 2026/10/27 17:05');
  await expect(page.getByRole('status').filter({ hasText: '早於最新報告日 2026-10-29' })).toBeVisible();
  await expect(list.locator('.row-mas').first()).toContainText('MA20');
  const universe = screenerFixtures.universe() as { fields: string[]; markets: { US: { rows: unknown[][] } } };
  const volume = universe.markets.US.rows.find(row => row[0] === 'NVDA')![universe.fields.indexOf('volVsPrev')] as number;
  await page.getByRole('combobox', { name: '排序', exact: true }).selectOption('vr');
  await expect(list.getByRole('link').filter({ hasText: 'NVDA' }).locator('.metric-pill')).toContainText(`量比 ${volume.toFixed(volume >= 10 ? 0 : 1)}`);
  await page.getByRole('combobox', { name: '排序', exact: true }).selectOption('symbol');
  await expect(list.locator('.row-id b')).toHaveText([...data.symbols].sort());
  await page.getByRole('combobox', { name: '排序', exact: true }).selectOption('default');
  await expect(list.locator('.row-id b')).toHaveText(data.symbols);
  await page.getByRole('button', { name: /^未成功/ }).click();
  await expect(list.locator('.row-id b')).toHaveText(['NOTFOUND']);
  await page.getByRole('button', { name: /^全部/ }).click();
  await list.getByRole('link').filter({ hasText: 'AAPL' }).click();
  await expect(page.getByTestId('active-symbol')).toHaveText('AAPL');
  await page.goto('./?page=markets&market=us');
  await expect(page.locator('.trending-stocks')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /LINE 熱門/ })).toHaveCount(0);
});

test('LINE tab remains visible for missing or invalid files and retry recovers inside the group', async ({ page }) => {
  await mockSite(page, { fundamentals: true });
  let response: 'missing' | 'invalid' | 'html' | 'ok' = 'missing';
  await page.route('**/report/trending-stocks.json', route => response === 'missing'
    ? route.fulfill({ status: 404 }) : response === 'invalid'
      ? route.fulfill({ json: { version: 99, symbols: ['NVDA'] } }) : response === 'html'
        ? route.fulfill({ contentType: 'text/html', body: '<html>fallback</html>' }) : route.fulfill({ json: trending }));
  await page.goto('./?page=screener&group=line-trending-us');
  for (const value of ['missing', 'invalid', 'html'] as const) {
    response = value;
    if (value !== 'missing') await page.reload();
    await expect(page.getByRole('button', { name: /^LINE 熱門/ })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.screener-body .page-state')).toContainText('LINE 熱門美股清單尚未產生');
    await expect(page.getByRole('button', { name: '存成自選清單', exact: true })).toBeDisabled();
  }
  response = 'ok';
  await page.locator('.screener-body .page-state').getByRole('button', { name: '重新載入', exact: true }).click();
  await expect(page.getByRole('list', { name: 'LINE 熱門股票清單' }).locator('.row-id b')).toHaveText(trending.symbols);
});

test('LINE group shares filters and saves only its symbols as a manual watchlist', async ({ page }) => {
  await mockDeviceAccount(page);
  await mockSite(page, { fundamentals: true });
  await page.route('**/report/trending-stocks.json', route => route.fulfill({ json: trending }));
  await page.goto('./?page=screener&group=line-trending-us&f=vol:100000');
  const list = page.getByRole('list', { name: 'LINE 熱門股票清單' });
  await expect(list.locator('.row-id b')).toHaveText(trending.symbols);
  await expect(page.getByRole('group', { name: '已套用的篩選條件' })).toContainText('20 日均量');
  await page.getByRole('button', { name: /篩選條件/ }).click();
  const sheet = page.getByRole('dialog', { name: '篩選條件' });
  await sheet.getByRole('button', { name: /^套用/ }).click();
  await page.getByRole('button', { name: '存成自選清單', exact: true }).click();
  await page.getByRole('textbox', { name: '自選清單名稱' }).fill('LINE 五檔追蹤');
  await page.getByRole('button', { name: '儲存並追蹤清單' }).click();
  const result = page.getByRole('region', { name: '清單追蹤結果' });
  await expect(result).toContainText('LINE 五檔追蹤');
  for (const symbol of trending.symbols) await expect(result).toContainText(symbol);
  await page.reload();
  await expect(result).toContainText('LINE 五檔追蹤');
  for (const symbol of trending.symbols) await expect(result).toContainText(symbol);
});

test('switching symbols while refreshing cancels the result for the old chart', async ({ page }) => {
  await mockSite(page);
  await page.goto('./?page=chart&symbol=NVDA&tf=1D');
  const update = page.getByRole('button', { name: '更新 NVDA K 線', exact: true });
  await expect(update).toBeEnabled();
  let release!: () => void;
  let started = false;
  await page.route('https://edge.test/functions/v1/market-chart**', async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.has('_refresh')) {
      started = true;
      await new Promise<void>(resolve => { release = resolve; });
    }
    await route.fulfill({ json: yahooChart(url.searchParams.get('symbol')!, url.searchParams.get('interval')!) }).catch(() => {});
  });
  await update.click();
  await expect.poll(() => started).toBe(true);
  // Route navigation within the mounted app, without reloading the document.
  await page.evaluate(() => {
    history.pushState(null, '', '?page=chart&symbol=AAPL&tf=1D');
    dispatchEvent(new PopStateEvent('popstate'));
  });
  const nextUpdate = page.getByRole('button', { name: '更新 AAPL K 線', exact: true });
  await expect(nextUpdate).toBeEnabled();
  const nextPrice = (await page.getByTestId('header-quote').textContent())!;
  release();
  await expect(page.getByTestId('header-quote')).toHaveText(nextPrice);
  await expect(page.getByRole('status').filter({ hasText: '已更新 NVDA' })).toHaveCount(0);
});

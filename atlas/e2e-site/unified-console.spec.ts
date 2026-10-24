import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { buildReport, mockSite } from './mocks';
import { accessEnvelope, mockDeviceAccount } from './account-fixture';

test('filter application is reachable and changes the rows, URL, and reloaded view', async ({ page }) => {
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=screener&group=tw_all');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  await page.getByRole('button', { name: '篩選條件', exact: true }).click();
  const sheet = page.getByRole('dialog', { name: '篩選條件' });
  await sheet.getByRole('button', { name: '站上MA20', exact: true }).click();
  const apply = sheet.getByRole('button', { name: /^套用/ });
  await expect(apply).toBeInViewport();
  await apply.click();
  await expect(sheet).not.toBeVisible();
  await expect(page).toHaveURL(/f=tr/);
  const count = await page.locator('.result-count').innerText();
  await expect(page.getByRole('group', { name: '已套用的篩選條件' })).toContainText('站上MA20');
  expect(await page.locator('.screener-row').count()).toBeGreaterThan(0);
  await page.reload();
  await expect(page.locator('.result-count')).toHaveText(count);
});

test('apply remains clickable after scrolling a long sheet and shrinking the visible viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 420 });
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=tw_all');
  await page.getByRole('button', { name: '篩選條件', exact: true }).click();
  const sheet = page.getByRole('dialog', { name: '篩選條件' });
  await sheet.getByRole('button', { name: '站上MA20', exact: true }).click();
  await sheet.locator('.fs-body').evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  await page.setViewportSize({ width: 390, height: 280 });
  const apply = sheet.getByRole('button', { name: /^套用/ });
  await expect(apply).toBeInViewport();
  const box = await apply.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  await apply.click();
  await expect(sheet).not.toBeVisible();
  await expect(page).toHaveURL(/f=tr/);
});

test('Apply stays inside the visual viewport when a keyboard leaves the layout viewport tall', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 700 });
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=tw_all&f=vol:1000000;epsy:5:10;epsy:1:20');
  await page.getByRole('button', { name: /篩選條件/ }).click();
  const sheet = page.getByRole('dialog', { name: '篩選條件' });
  await sheet.locator('.fs-body').evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await page.evaluate(() => {
    // Mobile keyboards can shrink visualViewport without resizing window.innerHeight.
    Object.defineProperty(window.visualViewport!, 'height', { configurable: true, get: () => 280 });
    Object.defineProperty(window.visualViewport!, 'offsetTop', { configurable: true, get: () => 0 });
    window.visualViewport!.dispatchEvent(new Event('resize'));
  });
  const apply = sheet.getByRole('button', { name: /^套用/ });
  await expect.poll(async () => {
    const box = await apply.boundingBox();
    return box !== null && box.y >= 0 && box.y + box.height <= 280 && box.height >= 44;
  }).toBe(true);
  await apply.click();
  await expect(sheet).not.toBeVisible();
  expect(new URL(page.url()).searchParams.get('f')).toBe('vol:1000000;epsy:5:10;epsy:1:20');
});

test('multiple saved lists preserve independent rules, opening diffs, streaks, and the monitoring choice', async ({ page }) => {
  await mockDeviceAccount(page);
  const report = buildReport(20) as {
    reportDate: string;
    generatedAt: string;
    groups: { key: string; items: { symbol: string; name: string; changePct: number; metrics: { ema20: number }; asOf?: string }[] }[];
  };
  report.reportDate = '2026-10-08';
  report.generatedAt = '2026-10-08T09:00:00Z';
  await mockSite(page, { report });
  await page.goto('./?page=screener&group=tw_all&f=ema20:3:');
  await page.getByRole('button', { name: '存成自選清單', exact: true }).click();
  await page.getByRole('textbox', { name: '自選清單名稱' }).fill('強勢追蹤');
  await page.getByRole('button', { name: '儲存並追蹤清單' }).click();
  await expect(page.getByRole('region', { name: '清單追蹤結果' })).toContainText('首次開啟');
  const firstUrl = page.url();
  await page.getByRole('switch', { name: '監控此清單的突破提醒' }).check();
  await page.goto('./?page=screener&group=tw_all&f=ema20::-3');
  await page.getByRole('button', { name: '存成自選清單', exact: true }).click();
  await page.getByRole('textbox', { name: '自選清單名稱' }).fill('回檔觀察');
  await page.getByRole('button', { name: '儲存並追蹤清單' }).click();
  await expect(page.getByRole('group', { name: '我的自選清單' }).getByRole('button')).toHaveCount(2);
  await expect(page.getByRole('switch', { name: '監控此清單的突破提醒' })).not.toBeChecked();
  const next = structuredClone(report);
  next.reportDate = '2026-10-09';
  next.generatedAt = '2026-10-09T09:00:00Z';
  const rows = next.groups.find((group) => group.key === 'tw_all')!.items;
  rows.find((row) => row.metrics.ema20 >= 3)!.metrics.ema20 = -4;
  rows.find((row) => row.metrics.ema20 < 3 && row.metrics.ema20 > -4)!.metrics.ema20 = 4;
  rows.forEach((row) => {
    row.asOf = '2026-10-09';
  });
  await page.route('**/my-stock-screener/report/latest.json', (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(next) }),
  );
  await page.goto(firstUrl);
  const result = page.getByRole('region', { name: '清單追蹤結果' });
  await expect(result.locator('.watchlist-counts > div').nth(0)).toContainText('1');
  await expect(result.locator('.watchlist-counts > div').nth(1)).toContainText('1');
  await expect(page.getByRole('switch', { name: '監控此清單的突破提醒' })).toBeChecked();
  await expect(result).toContainText('連續入選 2 個已確認交易日');
  if (process.env.SCREENSHOT_DIR) {
    await page.locator('#main').evaluate((node) => node.scrollTo(0, 0));
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/watchlists-${test.info().project.name}.png` });
  }
  await page.reload();
  await expect(result.locator('.watchlist-counts > div').nth(0)).toContainText('0');
  await expect(result).toContainText('連續入選 2 個已確認交易日');
});

test('the original parameter API is available inside Atlas and an undeployed cloud-list table never claims sync success', async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'userAgent', { get: () => 'Mozilla/5.0 Line/14.0.0' });
    (globalThis as unknown as { liff: unknown }).liff = {
      init: async () => {},
      isLoggedIn: () => true,
      getIDToken: () => 'test-token',
      getDecodedIDToken: () => ({ sub: `U${'0'.repeat(32)}`, exp: Date.now() / 1000 + 3600 }),
    };
  });
  await mockSite(page, { report: buildReport(20) });
  const stocks: { id: number; ticker: string; group_id: string; ma1: number; ma2: number; ma3: number; ma4: number }[] = [];
  await page.route('**/functions/v1/user-settings', async (route) => {
    const payload = route.request().postDataJSON() as { action: string; ticker: string; groupId: string; mas: number[]; idToken: string };
    expect(payload.idToken).toBe('test-token');
    if (payload.action === 'access.get') return route.fulfill({ json: accessEnvelope(undefined, 'cloud') });
    if (payload.action.startsWith('watchlists.'))
      return route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ ok: false, error: '雲端清單資料表尚未部署' }),
      });
    if (payload.action === 'stocks.save')
      stocks.push({
        id: 1,
        ticker: payload.ticker,
        group_id: payload.groupId,
        ma1: payload.mas[0],
        ma2: payload.mas[1],
        ma3: payload.mas[2],
        ma4: 0,
      });
    return route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ ok: true, data: payload.action === 'stocks.list' ? stocks : [] }),
    });
  });
  await page.goto('./?page=console');
  await page.getByRole('button', { name: '均線參數', exact: true }).click();
  await page.getByRole('textbox', { name: '參數股票代號' }).fill('NVDA');
  await page.getByRole('combobox', { name: '參數股票分組' }).selectOption('us_g1');
  await page.getByRole('textbox', { name: '股票均線參數' }).fill('20, 50, 200');
  await page.getByRole('button', { name: '新增股票參數', exact: true }).click();
  await expect(page.getByRole('table', { name: '個人股票參數' })).toContainText('NVDA');
  await page.goto('./?page=screener&group=tw_all&f=ema20:3:');
  await page.getByRole('button', { name: '存成自選清單', exact: true }).click();
  await page.getByRole('button', { name: '儲存並追蹤清單' }).click();
  await page.getByText('清單設定與 Supabase 同步', { exact: true }).click();
  await page.getByRole('button', { name: '重試同步', exact: true }).click();
  await expect(page.locator('.console-message').last()).toContainText('尚未部署');
  await expect(page.locator('.console-message').last()).toContainText('本機清單仍保留');
  await expect(page.getByRole('group', { name: '我的自選清單' }).getByRole('button')).toHaveCount(1);
});

test('data health is folded at the page bottom and reports source failures explicitly', async ({ page }) => {
  await mockSite(page, { fundamentals: true });
  await page.goto('./?page=markets');
  const health = page.locator('.data-health');
  await expect(health).not.toHaveAttribute('open');
  await health.locator('summary').click();
  await expect(health).toContainText('每日報告');
  await expect(health).toContainText('全市場與技術指標');
  await expect(health).toContainText('資料缺少');
  await expect(health.getByRole('button', { name: '重新檢查' })).toBeEnabled();
});

test('theme rotation uses aligned history and filters the observed quadrants', async ({ page }) => {
  await mockSite(page);
  const body = readFileSync(new URL('../src/report/fixtures/themes.sample.json', import.meta.url), 'utf8');
  await page.route('**/my-stock-screener/report/themes.json', (route) => route.fulfill({ contentType: 'application/json', body }));
  await page.goto('./?page=themes');
  const rotation = page.locator('.theme-rotation');
  await rotation.locator(':scope > summary').click();
  await expect(rotation.getByRole('table', { name: '題材輪動比較' })).toBeVisible();
  expect(await rotation.locator('tbody tr').count()).toBeGreaterThan(0);
  await rotation.getByRole('button', { name: /^領先/ }).click();
  const states = await rotation.locator('tbody .rotation-state').allTextContents();
  expect(states.every((value) => value === '領先')).toBe(true);
});

test('peer comparison loads added companies on demand and shows their actual statement periods', async ({ page }) => {
  await mockSite(page);
  const original = JSON.parse(readFileSync(new URL('../tests/fixtures/financials/us-full.json', import.meta.url), 'utf8'));
  const requests: string[] = [];
  await page.route('**/report/financials/*.json', (route) => {
    const symbol = new URL(route.request().url()).pathname.split('/').pop()!.replace('.json', '');
    requests.push(symbol);
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...original, symbol }) });
  });
  await page.goto('./?page=financials&symbol=AAPL&p=q');
  const peers = page.locator('.fin-peers');
  await expect(peers).not.toHaveAttribute('open');
  expect(requests).not.toContain('NVDA');
  await peers.locator('summary').click();
  await peers.locator('.search-field input').fill('NVDA');
  await peers.locator('.search-results li').filter({ hasText: 'NVDA' }).first().click();
  const table = peers.getByRole('table', { name: '同業財報比較' });
  await expect(table).toContainText('NVDA');
  await expect(table).toContainText('2025-06-28');
  expect(requests).toContain('NVDA');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
});

test('US quarterly statements retain five-year controls and allow annual round trips', async ({ page }) => {
  await mockSite(page);
  const body = readFileSync(new URL('../tests/fixtures/financials/us-full.json', import.meta.url), 'utf8');
  await page.route('**/report/financials/AAPL.json', (route) => route.fulfill({ contentType: 'application/json', body }));
  await page.goto('./?page=financials&symbol=AAPL&p=q');
  await expect(page.getByRole('button', { name: '季度', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('group', { name: '圖表區間' })).toBeVisible();
  await page.getByRole('button', { name: '年度', exact: true }).click();
  await expect(page).toHaveURL(/p=a/);
  await page.getByRole('button', { name: '季度', exact: true }).click();
  await expect(page).toHaveURL(/p=q/);
  const peers = page.locator('.fin-peers');
  await expect(peers).not.toHaveAttribute('open');
  await peers.locator('summary').click();
  await expect(page.getByRole('table', { name: '同業財報比較' })).toBeVisible();
});

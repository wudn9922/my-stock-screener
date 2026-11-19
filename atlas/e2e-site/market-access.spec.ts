import { expect, test, type Page, type Request } from '@playwright/test';
import { mockSite, NO_LIVE_QUOTES, yahooChart } from './mocks';
import { archives } from './archive-fixture';
import { mockDeviceAccount } from './account-fixture';

/** Every request that reached the Supabase market-chart function (mockSite answers them unless overridden). */
function marketRequests(page: Page) {
  const requests: Request[] = [];
  page.on('request', (request) => { if (request.url().includes('/functions/v1/market-chart')) requests.push(request); });
  return requests;
}
const loginPrompt = (page: Page) => page.getByRole('group', { name: '登入 LINE 後可查看' });
const TRENDING = { version: 1, source: 'line', fetchedAt: '2026-10-27T01:00:00+08:00', sentAt: '2026-10-27T01:05:00+08:00', symbols: ['NVDA', 'AAPL'] };

test('signed out: 自選, 市場, 選股 and 個股 never call market-chart; unpublished stocks ask for LINE login', async ({ page }) => {
  await mockSite(page);
  await archives(page, ['2330.TW']);
  await page.route('**/my-stock-screener/report/trending-stocks.json', (route) => route.fulfill({ json: TRENDING }));
  const requests = marketRequests(page);

  // 自選: LINE 熱門 rows without static data, one login prompt for the board.
  await page.goto('./?page=watchlist');
  const board = page.getByRole('region', { name: '自選行情' });
  await expect(board.getByRole('list', { name: '自選行情列表' }).getByRole('link')).toHaveCount(2);
  await expect(board.locator('.wb-error').first()).toHaveText('登入 LINE 後可查看');
  await expect(loginPrompt(page).getByRole('button', { name: '使用 LINE 登入' })).toBeVisible();

  // 市場: index mini charts.
  await page.goto('./?page=markets&market=tw');
  await expect(page.locator('.mini-chart').first()).toHaveAttribute('data-status', 'error');
  await expect(loginPrompt(page).first()).toBeVisible();

  // 選股: continuous chart review.
  await page.goto('./?page=screener');
  await page.getByRole('button', { name: /連續看/ }).first().click();
  const card = page.locator('.stock-chart-card').first();
  await expect(card.locator('.mini-chart')).toHaveAttribute('data-status', 'error');
  await expect(card.getByRole('group', { name: '登入 LINE 後可查看' })).toBeVisible();

  // 個股: a published stock still charts from the static archive; an unpublished one asks for login.
  await page.goto('./?page=chart&symbol=2330.TW&tf=1D');
  await expect(page.locator('[data-testid="source-label"]')).toContainText('GitHub 永久封存');
  await expect(page.locator('.chart-loading.error')).toHaveCount(0);
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  const prompt = page.locator('.market-login-overlay');
  await expect(prompt).toContainText('登入 LINE 後可查看');
  await expect(prompt.getByRole('button', { name: '使用 LINE 登入' })).toBeVisible();
  await expect(page.getByText('行情資料暫時無法取得')).toHaveCount(0);

  await page.waitForTimeout(1500);
  expect(requests.map((request) => request.url())).toEqual([]);
});

test('signed in: every market-chart request carries X-Atlas-Token', async ({ page }) => {
  await mockDeviceAccount(page);
  await mockSite(page);
  const requests = marketRequests(page);
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toBeVisible();
  await expect(loginPrompt(page)).toHaveCount(0);
  await expect.poll(() => requests.some((request) => new URL(request.url()).searchParams.has('quotes'))).toBe(true);
  expect(requests.length).toBeGreaterThan(1);
  for (const request of requests) expect(request.headers()['x-atlas-token']).toBe('test-token');
});

test('429: the server text shows and nothing is retried for the rest of the session', async ({ page }) => {
  await mockDeviceAccount(page);
  await mockSite(page);
  const requests = marketRequests(page);
  await page.route('https://edge.test/functions/v1/market-chart**', (route) => route.fulfill({
    status: 429, headers: { 'Retry-After': '3600' },
    json: { ok: false, code: 'USER_DAILY', error: '今日免費額度已用完，明天再試', retryAfterSeconds: 3600 },
  }));
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  const notice = page.locator('.market-quota-notice');
  await expect(notice).toHaveText('今日免費額度已用完，明天再試');
  await expect(page.locator('.chart-loading.error')).toContainText('今日免費額度已用完，明天再試');
  await page.waitForTimeout(500);
  const refused = requests.length;
  expect(refused).toBeGreaterThan(0);
  await page.locator('.chart-loading.error').getByRole('button', { name: '重試' }).click();
  // In-app navigation (no reload) to the other market pages: still nothing sent.
  await page.getByRole('navigation', { name: '主要頁面' }).getByRole('button', { name: '市場' }).click();
  await expect(page.locator('.mini-chart').first()).toHaveAttribute('data-status', 'error');
  await expect(page.locator('.mini-chart').first()).toContainText('今日免費額度已用完，明天再試');
  await expect(notice).toBeVisible();
  await page.waitForTimeout(1500);
  expect(requests.length).toBe(refused);
});

test('401: treated as signed out; the login prompt shows and live polling stops', async ({ page }) => {
  await page.clock.install({ time: new Date('2026-11-04T16:00:00Z') });
  await mockDeviceAccount(page);
  await mockSite(page);
  const requests = marketRequests(page);
  await page.route('https://edge.test/functions/v1/market-chart**', (route) => {
    const url = new URL(route.request().url());
    // The live batch is accepted; the chart download says the login is no longer valid.
    if (url.searchParams.has('quotes')) return route.fulfill({ json: NO_LIVE_QUOTES });
    if (url.searchParams.get('symbol') === 'AAPL')
      return route.fulfill({ status: 401, json: { ok: false, code: 'LOGIN_REQUIRED', error: '登入 LINE 後可查看即時行情與更多股票。' } });
    return route.fulfill({ json: yahooChart(url.searchParams.get('symbol') ?? '', '1d') });
  });
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await expect(page.locator('.market-login-overlay')).toContainText('登入 LINE 後可查看');
  await page.waitForTimeout(500);
  const sent = requests.length;
  await page.clock.fastForward(5 * 60_000);
  await page.waitForTimeout(500);
  expect(requests.length).toBe(sent);
});

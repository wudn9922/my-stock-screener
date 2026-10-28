import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { accessEnvelope, installLineAccount, OWNER_USER_ID } from './account-fixture';
import type { MonitorSignal, MonitorSnapshot } from '../../supabase/functions/monitor-signals/contract';

const signal = (extra: Partial<MonitorSignal> = {}): MonitorSignal => ({ groupId: 'us_g1', groupName: '美股權值精選', ticker: 'NVDA', name: 'NVIDIA', market: 'US',
  direction: 'breakout_up', maPeriod: 20, sentAt: '2026-10-27T15:00:00.000Z', price: 150, maValue: 148, volumeRatio: 1.2, ...extra });
const snapshot = (signals: MonitorSignal[]): MonitorSnapshot => ({ version: 1, generatedAt: '2026-10-28T10:00:00.000Z', windowStart: '2026-09-28T10:00:00.000Z', signals, truncated: false });
const source = (page: Page) => page.getByRole('group', { name: '行情來源' }).getByRole('button', { name: '突破／跌破', exact: true });
const panel = (page: Page) => page.getByRole('region', { name: 'Discord 突破跌破監控' });
async function setup(page: Page, mode: 'cloud' | 'device' | 'none' = 'cloud') {
  const requested = await mockSite(page, { fundamentals: true });
  await installLineAccount(page, OWNER_USER_ID);
  await page.route('**/functions/v1/chart-drawings', route => route.fulfill({ json: { ok: true, records: [] } }));
  await page.route('**/functions/v1/user-settings', route => {
    const body = route.request().postDataJSON();
    return route.fulfill({ json: body.action === 'access.get' ? accessEnvelope(OWNER_USER_ID, mode, true) : { ok: true, data: [] } });
  });
  return requested;
}
async function mockSignals(page: Page, signals: MonitorSignal[]) {
  await page.route('**/functions/v1/monitor-signals', route => {
    const request = route.request();
    expect(request.method()).toBe('POST'); expect(new URL(request.url()).search).toBe('');
    expect(request.postDataJSON()).toEqual({ action: 'list', idToken: 'test-token' });
    return route.fulfill({ json: { ok: true, data: snapshot(signals) } });
  });
}

test('signals sit next to LINE; group details show coalesced candles, volume and native zoom', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await setup(page);
  await mockSignals(page, [signal(), signal({ maPeriod: 50, direction: 'breakout_down' }), signal({ ticker: 'AAPL', name: 'Apple', direction: 'breakout_down' }),
    signal({ groupId: 'tw_g1', groupName: '台股權值精選', ticker: '2330.TW', name: '台積電', market: 'TW' })]);
  await page.goto('./?page=watchlist');
  await expect(source(page)).toBeVisible();
  const names = await page.getByRole('group', { name: '行情來源' }).getByRole('button').allTextContents();
  expect(names.indexOf('突破／跌破')).toBe(names.indexOf('LINE 熱門') + 1);
  await source(page).click();
  await expect(panel(page).getByRole('button', { name: '美股權值精選 詳細', exact: true })).toBeVisible();
  await expect(panel(page).locator('.monitor-group').first()).toContainText('突破 1');
  await expect(panel(page).locator('.monitor-group').first()).toContainText('跌破 2');
  await panel(page).getByRole('button', { name: '美股權值精選 詳細', exact: true }).click();
  await expect(panel(page).locator('.monitor-stock')).toHaveCount(2);
  const first = panel(page).locator('.monitor-stock').first();
  await first.scrollIntoViewIfNeeded();
  await expect(first.locator('.mini-chart')).toHaveAttribute('data-status', 'ready');
  await expect(first).toContainText('突破 MA20'); await expect(first).toContainText('跌破 MA50');
  await expect(first.getByText('日 K · 成交量', { exact: true })).toBeVisible();
  const count = first.getByLabel('顯示 K 線根數'); await expect(count).not.toHaveText('—');
  const before = Number(await count.textContent());
  await first.getByRole('button', { name: '放大圖表', exact: true }).click();
  await expect.poll(async () => Number(await count.textContent())).toBeLessThan(before);
  await panel(page).getByRole('group', { name: '訊號方向' }).getByRole('button', { name: '突破', exact: true }).click();
  await expect(panel(page).locator('.monitor-stock')).toHaveCount(1);
  await first.getByRole('button', { name: '開啟個股', exact: true }).click();
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
  expect(errors).toEqual([]);
});

test('long group scrolls directly through charts, loads more automatically and releases old canvases', async ({ page, browserName, isMobile }) => {
  await setup(page); await mockSignals(page, Array.from({ length: 60 }, (_, i) => signal({ ticker: `S${i}`, name: `測試股票 ${i}` })));
  await page.goto('./?page=watchlist'); await source(page).click();
  await panel(page).getByRole('button', { name: '美股權值精選 詳細', exact: true }).click();
  const first = panel(page).locator('.monitor-stock').first();
  await first.scrollIntoViewIfNeeded(); await expect(first.locator('.mini-chart')).toHaveAttribute('data-status', 'ready');
  await first.locator('.mini-chart-canvas').hover();
  const scrollBefore = await page.locator('#main').evaluate(el => el.scrollTop);
  if (browserName === 'webkit' && isMobile) {
    // Mobile WebKit has no Playwright wheel API. Exercise the actual chart's
    // touch listeners: a vertical gesture must remain uncancelled for page scroll.
    const gestures = await first.locator('.mini-chart-canvas').evaluate(host => {
      const canvas = [...host.querySelectorAll('canvas')].reverse().sort((a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight)[0]!;
      const box = canvas.getBoundingClientRect(), x = box.left + box.width / 2, y = box.top + box.height / 2;
      // WebKit also lacks constructible Touch objects; DOM events with touch
      // coordinates exercise the same listeners without pretending to be hardware.
      const touch = (dx: number, dy: number) => ({ identifier: 1, target: canvas, clientX: x + dx, clientY: y + dy, pageX: x + dx, pageY: y + dy, screenX: x + dx, screenY: y + dy });
      const event = (type: string, touches: ReturnType<typeof touch>[], changedTouches: ReturnType<typeof touch>[]) =>
        Object.assign(new Event(type, { bubbles: true, cancelable: true }), { touches, targetTouches: touches, changedTouches });
      const drag = (dx: number, dy: number) => {
        canvas.dispatchEvent(event('touchstart', [touch(0, 0)], [touch(0, 0)]));
        const move = event('touchmove', [touch(dx, dy)], [touch(dx, dy)]);
        canvas.dispatchEvent(move);
        canvas.dispatchEvent(event('touchend', [], [touch(dx, dy)]));
        return move.defaultPrevented;
      };
      return { verticalAllowed: !drag(0, -80), horizontalHandled: drag(-80, 0) };
    });
    expect(gestures).toEqual({ verticalAllowed: true, horizontalHandled: true });
    await page.locator('#main').evaluate(el => el.scrollBy(0, 400));
  } else await page.mouse.wheel(0, 400);
  await expect.poll(() => page.locator('#main').evaluate(el => el.scrollTop)).toBeGreaterThan(scrollBefore);
  for (const next of [40, 60]) {
    await panel(page).getByRole('button', { name: /繼續載入圖表/ }).scrollIntoViewIfNeeded();
    await expect(panel(page).locator('.monitor-stock')).toHaveCount(next);
  }
  const last = panel(page).locator('.monitor-stock').last(); await last.scrollIntoViewIfNeeded();
  await expect(last.locator('.mini-chart')).toHaveAttribute('data-status', 'ready');
  await expect(panel(page).locator('.monitor-stock').first().locator('.mini-chart')).toHaveCount(0);
  expect(await panel(page).locator('.mini-chart').count()).toBeLessThan(10);
  expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  for (const height of await last.getByRole('button').evaluateAll(buttons => buttons.map(button => button.getBoundingClientRect().height))) expect(height).toBeGreaterThanOrEqual(44);
  await page.screenshot({ path: 'test-results/site/monitor-signal-scroll.png' });
  await panel(page).getByRole('button', { name: '返回組別', exact: true }).scrollIntoViewIfNeeded();
  await panel(page).getByRole('button', { name: '返回組別', exact: true }).click();
  await expect(panel(page).getByRole('heading', { name: '突破／跌破', exact: true })).toBeFocused();
  await expect(panel(page).getByRole('button', { name: '美股權值精選 詳細', exact: true })).toBeInViewport();
});

test('a missing reader is explained and retry recovers without breaking saved lists', async ({ page }) => {
  await setup(page); let calls = 0;
  await page.route('**/functions/v1/monitor-signals', route => ++calls === 1 ? route.fulfill({ status: 404, body: 'not enabled' })
    : route.fulfill({ json: { ok: true, data: snapshot([]) } }));
  await page.goto('./?page=watchlist'); await source(page).click();
  await expect(panel(page).getByRole('alert')).toContainText('監控訊號讀取服務尚未啟用');
  await panel(page).getByRole('button', { name: '重試', exact: true }).click();
  await expect(panel(page).getByRole('status')).toContainText('最近沒有成功發送');
  await expect(page.getByRole('button', { name: '建立手動清單', exact: true })).toBeVisible();
});

for (const mode of ['none', 'device'] as const) test(`${mode} never requests cloud monitoring data`, async ({ page }) => {
  await setup(page, mode); let calls = 0;
  await page.route('**/functions/v1/monitor-signals', route => { calls++; return route.fulfill({ status: 500 }); });
  await page.goto('./?page=watchlist'); await source(page).click();
  await expect(page.getByRole('status').filter({ hasText: '突破／跌破是個人雲端監控資料' })).toBeVisible();
  expect(calls).toBe(0);
});

test('only the latest session per market is shown; an older day is replaced', async ({ page }) => {
  await setup(page);
  await mockSignals(page, [signal(), signal({ ticker: 'MSFT', name: 'Microsoft', sentAt: '2026-10-26T15:00:00.000Z' }),
    signal({ groupId: 'us_g3', groupName: '美股超級績效', ticker: 'TSLA', name: 'Tesla', sentAt: '2026-10-24T15:00:00.000Z' })]);
  await page.goto('./?page=watchlist'); await source(page).click();
  await expect(panel(page)).toContainText('最新一天 · 美股 2026/10/27');
  await expect(panel(page).locator('.monitor-group')).toHaveCount(1);
  await expect(panel(page).getByRole('button', { name: '美股超級績效 詳細', exact: true })).toHaveCount(0);
  await panel(page).getByRole('button', { name: '美股權值精選 詳細', exact: true }).click();
  await expect(panel(page).locator('.monitor-stock')).toHaveCount(1);
  await expect(panel(page)).toContainText('NVIDIA'); await expect(panel(page)).not.toContainText('Microsoft');
});

test('previous successful sends remain visible after market closure; unavailable charts retain signals', async ({ page }) => {
  await setup(page); await mockSignals(page, [signal({ ticker: '9999', market: 'TW', groupId: 'tw_g1', groupName: '台股權值精選', name: '未對應代號', sentAt: '2026-10-24T09:00:00.000Z' })]);
  await page.goto('./?page=watchlist'); await source(page).click();
  await panel(page).getByRole('button', { name: '台股權值精選 詳細', exact: true }).click();
  await expect(panel(page)).toContainText('未對應代號'); await expect(panel(page)).toContainText('代號尚未對應行情來源');
  await expect(panel(page)).toContainText('觸發價 150.00');
});

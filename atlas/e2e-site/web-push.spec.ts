import { expect, test, type Page } from '@playwright/test';
import { accessEnvelope, DEVICE_USER_ID } from './account-fixture';
import { isCompactConsole } from './console-nav';
import { buildReport, mockSite } from './mocks';

const IPHONE_SAFARI = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const ANDROID_CHROME = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36';
const TOKEN = `atlasdev.${'p'.repeat(43)}`;
const ENDPOINT = 'https://web.push.apple.com/QAtlasTestDevice';
const SCREENSHOT_DIR = process.env.ATLAS_PUSH_SCREENSHOT_DIR;

interface Browser { ua: string; standalone?: boolean; push?: boolean; permission?: NotificationPermission; subscribed?: boolean; remembered?: boolean }

/** Simulated browser capabilities: Web Push, the permission state and the push-only worker are stubs. */
async function browser(page: Page, options: Browser) {
  await page.addInitScript(({ ua, standalone, push, permission, subscribed, remembered, endpoint, token, user }) => {
    Object.defineProperty(navigator, 'userAgent', { configurable: true, get: () => ua });
    Object.defineProperty(navigator, 'standalone', { configurable: true, get: () => standalone });
    if (standalone) {
      const match = window.matchMedia.bind(window);
      window.matchMedia = query => {
        const result = match(query);
        if (query.includes('display-mode: standalone')) Object.defineProperty(result, 'matches', { value: true });
        return result;
      };
    }
    if (remembered) localStorage.setItem('atlas-device-session-v1', JSON.stringify({ token, userId: user }));
    const state = window as unknown as { pushLog: string[] };
    state.pushLog = [];
    if (!push) {
      Reflect.deleteProperty(window, 'PushManager');
      return;
    }
    const scope = new URL('push/', location.href.replace(/[?#].*$/, '')).href;
    let current: object | null = null;
    const subscription = {
      endpoint, options: { applicationServerKey: null },
      toJSON: () => ({ endpoint, keys: { p256dh: 'test', auth: 'test' } }),
      unsubscribe: async () => { state.pushLog.push('unsubscribe'); current = null; return true; },
    };
    if (subscribed) current = subscription;
    const registration = { scope, active: {}, pushManager: { getSubscription: async () => current, subscribe: async () => (current = subscription) },
      unregister: async () => { state.pushLog.push('unregister'); return true; } };
    Object.defineProperty(window, 'PushManager', { configurable: true, value: function PushManager() {} });
    Object.defineProperty(window, 'Notification', { configurable: true, value: Object.assign(function Notification() {}, {
      permission, requestPermission: async () => { state.pushLog.push('requestPermission'); return permission; },
    }) });
    Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: {
      getRegistration: async () => (current || state.pushLog.includes('register') ? registration : undefined),
      getRegistrations: async () => [],
      register: async () => { state.pushLog.push('register'); return registration; },
      addEventListener: () => {}, startMessages: () => {},
    } });
  }, { ua: options.ua, standalone: options.standalone ?? false, push: options.push ?? true, permission: options.permission ?? 'default',
    subscribed: options.subscribed ?? false, remembered: options.remembered ?? false, endpoint: ENDPOINT, token: TOKEN, user: DEVICE_USER_ID });
}

const devices = (subscribed: boolean) => [
  ...(subscribed ? [{ id: '00000000-0000-4000-8000-000000000001', label: 'iPhone · 主畫面 App', service: 'Apple', createdAt: '2026-11-18T01:00:00Z', lastSuccessAt: '2026-11-19T01:32:00Z', failureCount: 0, current: true }] : []),
  { id: '00000000-0000-4000-8000-000000000002', label: 'Android · Chrome', service: 'Google', createdAt: '2026-11-10T08:00:00Z', lastSuccessAt: null, failureCount: 0, current: false },
];

function cloudAccount(page: Page, subscribed: boolean) {
  const actions: Record<string, unknown>[] = [];
  void page.route('**/functions/v1/user-settings', route => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    actions.push(body);
    const data = body.action === 'access.get' ? accessEnvelope(DEVICE_USER_ID, 'cloud').data
      : body.action === 'push.status' ? { devices: devices(subscribed && !actions.some(a => a.action === 'push.unsubscribe')), limit: 10, configured: true }
        : body.action === 'push.test' ? { devices: 2, sent: 2, removed: 0, failed: 0 }
          : body.action === 'push.unsubscribe' ? { removed: 1 } : [];
    return route.fulfill({ json: { ok: true, data } });
  });
  return actions;
}

async function openPanel(page: Page) {
  await page.goto('./?page=console&view=notifications');
  const panel = page.getByRole('region', { name: '手機通知', exact: true });
  await expect(panel).toBeVisible();
  return panel;
}

test.beforeEach(({ page }) => { test.skip(!isCompactConsole(page), '手機通知 is a 我的 sub-view on phones'); });

test('iPhone Safari shows the Home Screen steps instead of a toggle that cannot work', async ({ page }) => {
  await browser(page, { ua: IPHONE_SAFARI, push: false });
  await mockSite(page);
  const panel = await openPanel(page);
  for (const step of ['分享', '加入主畫面', '從主畫面的 Atlas 圖示開啟', '開啟「在這台裝置接收通知」', 'iOS 16.4'])
    await expect(panel).toContainText(step);
  await expect(panel.getByRole('switch')).toHaveCount(0);
});

test('a browser without Web Push explains which browsers work', async ({ page }) => {
  await browser(page, { ua: ANDROID_CHROME, push: false });
  await mockSite(page);
  const panel = await openPanel(page);
  await expect(panel).toContainText('不支援網頁通知');
  await expect(panel.getByRole('switch')).toHaveCount(0);
});

test('signed-out users are asked to log in before a subscription can be saved', async ({ page }) => {
  await browser(page, { ua: ANDROID_CHROME });
  await mockSite(page);
  const panel = await openPanel(page);
  await expect(panel).toContainText('請先使用 LINE 登入');
  await expect(panel.getByRole('switch')).toHaveCount(0);
});

test('denied permission explains how to re-enable it in the system settings', async ({ page }) => {
  await browser(page, { ua: IPHONE_SAFARI, standalone: true, permission: 'denied', remembered: true });
  await mockSite(page, { report: buildReport(20) });
  cloudAccount(page, false);
  const panel = await openPanel(page);
  await expect(panel).toContainText('通知權限已被封鎖');
  await expect(panel).toContainText('「設定」→「通知」');
  await expect(panel.getByRole('switch')).toHaveCount(0);
});

test('installed app: device list, test notification and turning this device off', async ({ page }) => {
  await browser(page, { ua: IPHONE_SAFARI, standalone: true, permission: 'granted', subscribed: true, remembered: true });
  await mockSite(page, { report: buildReport(20) });
  const actions = cloudAccount(page, true);
  await page.setViewportSize({ width: 390, height: 844 });
  const panel = await openPanel(page);
  const toggle = panel.getByRole('switch', { name: /在這台裝置接收通知/ });
  await expect(toggle).toBeChecked();
  const list = panel.getByRole('region', { name: '已開啟通知的裝置' });
  await expect(list.getByRole('listitem')).toHaveCount(2);
  await expect(list).toContainText('這台裝置');
  expect(actions.find(action => action.action === 'push.status')).toMatchObject({ endpoint: ENDPOINT, idToken: TOKEN });
  expect(await page.locator('#main').evaluate(element => element.scrollWidth - element.clientWidth)).toBe(0);
  if (SCREENSHOT_DIR) await panel.screenshot({ path: `${SCREENSHOT_DIR}/atlas-push-settings-390.png` });
  await panel.getByRole('button', { name: '傳送測試通知' }).click();
  await expect(panel.getByRole('status')).toContainText('已傳送到 2 台裝置');
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await expect(panel.getByRole('status')).toContainText('已關閉');
  expect(actions.find(action => action.action === 'push.unsubscribe')).toMatchObject({ endpoint: ENDPOINT });
  expect(await page.evaluate(() => (window as unknown as { pushLog: string[] }).pushLog)).toEqual(['unsubscribe', 'unregister']);
});

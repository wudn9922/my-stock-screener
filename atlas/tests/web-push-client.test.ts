import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { MessageChannel, type MessagePort } from 'node:worker_threads';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { retireScreenerWorker } from '../src/auth/retireWorker';
import { configuredPublicKey } from '../src/push/pushConfig';
import { deviceLabel, pushSupport } from '../src/push/webPush';

const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const ANDROID = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36';
const KEY = 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

const host = (userAgent: string, options: { standalone?: boolean; push?: boolean; worker?: boolean; touch?: number } = {}) => ({
  navigator: { userAgent, platform: '', maxTouchPoints: options.touch ?? 5, standalone: options.standalone, ...(options.worker === false ? {} : { serviceWorker: {} }) } as unknown as Navigator,
  matchMedia: () => ({ matches: false }) as unknown as MediaQueryList,
  PushManager: options.push === false ? undefined : function PushManager() {},
  Notification: options.push === false ? undefined : function Notification() {},
});

describe('Web Push client support detection', () => {
  it('hides the feature without a valid public key', () => {
    expect(configuredPublicKey(undefined)).toBe('');
    expect(configuredPublicKey('not-a-key')).toBe('');
    expect(configuredPublicKey(` ${KEY} `)).toBe(KEY);
  });

  it('guides iPhone Safari to the Home Screen and supports the installed app and Android Chrome', () => {
    expect(pushSupport(host(IPHONE))).toBe('ios-install');
    expect(pushSupport(host(IPHONE, { standalone: true }))).toBe('supported');
    expect(pushSupport(host(IPHONE, { standalone: true, push: false }))).toBe('unsupported');
    expect(pushSupport(host(ANDROID))).toBe('supported');
    expect(pushSupport(host(`${ANDROID} Line/14.0.0`))).toBe('line');
    expect(pushSupport(host(ANDROID, { worker: false }))).toBe('unsupported');
    expect(deviceLabel(host(IPHONE, { standalone: true }))).toBe('iPhone · 主畫面 App');
    expect(deviceLabel(host(ANDROID))).toBe('Android · Chrome');
  });

  it('retiring the old screener worker keeps the opt-in push worker', async () => {
    const scope = 'https://example.test/my-stock-screener/atlas/';
    const push = { scope: `${scope}push/`, update: vi.fn(), unregister: vi.fn() };
    vi.stubGlobal('navigator', { serviceWorker: { getRegistrations: async () => [push] } });
    vi.stubGlobal('caches', { keys: async () => [], delete: vi.fn() });
    await retireScreenerWorker(scope);
    expect(push.unregister).not.toHaveBeenCalled();
  });
});

it('acknowledges a tap during startup and defers navigation until the login URL is safe to change', async () => {
  vi.stubEnv('VITE_WEB_PUSH_PUBLIC_KEY', KEY);
  vi.resetModules();
  const { startPushNavigation, pushScript } = await import('../src/push/pushConfig');
  const worker = new EventTarget();
  const history = { pushState: vi.fn() };
  vi.stubGlobal('navigator', { serviceWorker: worker });
  vi.stubGlobal('location', new URL('https://atlas.example/?code=login'));
  vi.stubGlobal('history', history);
  vi.stubGlobal('PopStateEvent', Event);
  vi.stubGlobal('dispatchEvent', vi.fn());
  const ready = startPushNavigation();
  const url = 'https://atlas.example/?page=chart&symbol=2330.TW&tf=1D';
  const postMessage = vi.fn();
  const send = (scriptURL: string) => worker.dispatchEvent(Object.assign(new Event('message'), {
    data: { type: 'atlas-push-open', url }, source: { scriptURL }, ports: [{ postMessage }],
  }));
  send('https://atlas.example/other/push-sw.js');
  expect(postMessage).not.toHaveBeenCalled();
  send(pushScript());
  expect(postMessage).toHaveBeenCalledWith(url);
  expect(history.pushState).not.toHaveBeenCalled();
  ready();
  expect(history.pushState).toHaveBeenCalledWith(null, '', url);
  expect(dispatchEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'popstate' }));
});

it('asks the browser to update an existing push worker at startup, and tolerates none', async () => {
  vi.stubEnv('VITE_WEB_PUSH_PUBLIC_KEY', KEY);
  vi.resetModules();
  const { startPushNavigation, pushScope } = await import('../src/push/pushConfig');
  vi.stubGlobal('location', new URL('https://atlas.example/'));
  const update = vi.fn(async () => undefined);
  const getRegistration = vi.fn(async () => ({ update }));
  vi.stubGlobal('navigator', { serviceWorker: Object.assign(new EventTarget(), { getRegistration }) });
  startPushNavigation();
  await vi.waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  expect(getRegistration).toHaveBeenCalledWith(pushScope());

  vi.stubGlobal('navigator', { serviceWorker: Object.assign(new EventTarget(), { getRegistration: async () => undefined }) });
  expect(() => startPushNavigation()).not.toThrow();
  const failing = vi.fn(async () => { throw new Error('offline'); });
  vi.stubGlobal('navigator', { serviceWorker: Object.assign(new EventTarget(), { getRegistration: async () => ({ update: failing }) }) });
  startPushNavigation();
  await vi.waitFor(() => expect(failing).toHaveBeenCalled());
});

it('checks the push worker for updates again when a resumed app returns to the foreground, at most every 10 minutes', async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.stubEnv('VITE_WEB_PUSH_PUBLIC_KEY', KEY);
  vi.resetModules();
  const { startPushNavigation } = await import('../src/push/pushConfig');
  vi.stubGlobal('location', new URL('https://atlas.example/'));
  const update = vi.fn(async () => undefined);
  vi.stubGlobal('navigator', { serviceWorker: Object.assign(new EventTarget(), { getRegistration: async () => ({ update }) }) });
  const doc = Object.assign(new EventTarget(), { visibilityState: 'visible' });
  vi.stubGlobal('document', doc);
  startPushNavigation();
  await vi.waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  doc.dispatchEvent(new Event('visibilitychange'));
  await Promise.resolve();
  expect(update).toHaveBeenCalledTimes(1);
  vi.setSystemTime(Date.now() + 11 * 60 * 1000);
  doc.visibilityState = 'hidden';
  doc.dispatchEvent(new Event('visibilitychange'));
  await Promise.resolve();
  expect(update).toHaveBeenCalledTimes(1);
  doc.visibilityState = 'visible';
  doc.dispatchEvent(new Event('visibilitychange'));
  await vi.waitFor(() => expect(update).toHaveBeenCalledTimes(2));
  vi.useRealTimers();
});

it('pulls a tap held by the push worker at startup and when the app returns to the foreground', async () => {
  vi.stubEnv('VITE_WEB_PUSH_PUBLIC_KEY', KEY);
  vi.resetModules();
  const { startPushNavigation, pushScript } = await import('../src/push/pushConfig');
  const location = new URL('https://atlas.example/?page=markets');
  vi.stubGlobal('location', location);
  const url = 'https://atlas.example/?page=chart&tf=1D&symbol=2330.TW';
  let reply: unknown = url;
  const pulls: unknown[] = [];
  const active = { scriptURL: pushScript(), postMessage: (message: unknown, [port]: MessagePort[]) => { pulls.push(message); port.postMessage(reply); } };
  const history = { pushState: vi.fn((_state: unknown, _title: string, href: string) => { location.href = href; }) };
  vi.stubGlobal('history', history);
  vi.stubGlobal('PopStateEvent', Event);
  vi.stubGlobal('dispatchEvent', vi.fn());
  vi.stubGlobal('navigator', { serviceWorker: Object.assign(new EventTarget(), { getRegistration: async () => ({ active, update: async () => {} }) }) });
  const doc = Object.assign(new EventTarget(), { visibilityState: 'visible' });
  vi.stubGlobal('document', doc);
  const ready = startPushNavigation();
  ready();
  await vi.waitFor(() => expect(history.pushState).toHaveBeenCalledWith(null, '', url));
  expect(pulls[0]).toEqual({ type: 'atlas-push-pull' });

  // A later pull for the same chart (parameters reordered) adds no history entry; a new tap routes again.
  reply = 'https://atlas.example/?symbol=2330.TW&page=chart&tf=1D';
  doc.dispatchEvent(new Event('visibilitychange'));
  await vi.waitFor(() => expect(pulls.length).toBeGreaterThanOrEqual(2));
  expect(history.pushState).toHaveBeenCalledTimes(1);
  reply = 'https://atlas.example/?page=chart&tf=1W&symbol=NVDA';
  doc.dispatchEvent(new Event('visibilitychange'));
  await vi.waitFor(() => expect(history.pushState).toHaveBeenLastCalledWith(null, '', reply));
  reply = 'https://evil.example/?page=chart';
  const before = history.pushState.mock.calls.length;
  doc.dispatchEvent(new Event('visibilitychange'));
  await new Promise((resolve) => setTimeout(resolve, 30));
  expect(history.pushState).toHaveBeenCalledTimes(before);
});

/** Run public/push-sw.js against a fake worker global. */
function worker(windows: { url: string; focus?: () => Promise<unknown>; postMessage?: (m: unknown, ports: MessagePort[]) => void; navigate?: (url: string) => Promise<unknown> }[] = []) {
  const listeners: Record<string, (event: unknown) => void> = {};
  const shown: { title: string; options: Record<string, unknown> }[] = [];
  const opened: string[] = [];
  const self = {
    registration: {
      scope: 'https://wudn9922.github.io/my-stock-screener/atlas/push/',
      showNotification: async (title: string, options: Record<string, unknown>) => { shown.push({ title, options }); },
    },
    clients: { matchAll: async () => windows, openWindow: async (url: string) => { opened.push(url); }, claim: async () => {} },
    skipWaiting: () => {},
    addEventListener: (type: string, listener: (event: unknown) => void) => { listeners[type] = listener; },
  };
  // The worker waits up to 30 s for the page to pull a tap; keep the fake worker's timers short.
  const fastTimeout = (callback: () => void, ms: number) => setTimeout(callback, Math.min(ms, 20));
  runInNewContext(readFileSync(new URL('../public/push-sw.js', import.meta.url), 'utf8'), { self, URL, Date, Number, String, MessageChannel, setTimeout: fastTimeout, clearTimeout });
  const dispatch = async (type: string, event: Record<string, unknown>) => {
    let pending: Promise<unknown> = Promise.resolve();
    listeners[type]({ ...event, waitUntil: (promise: Promise<unknown>) => { pending = promise; } });
    await pending;
  };
  return { listeners, shown, opened, dispatch };
}

describe('push-only worker', () => {
  it('has no fetch handler and uses no CacheStorage', () => {
    const source = readFileSync(new URL('../public/push-sw.js', import.meta.url), 'utf8');
    expect(Object.keys(worker().listeners).sort()).toEqual(['activate', 'install', 'message', 'notificationclick', 'push']);
    expect(source).not.toMatch(/caches\.|addEventListener\('fetch'/);
  });

  it('shows the alert with icon, badge, tag and an Atlas-only URL', async () => {
    const sw = worker();
    const payload = { title: '🚨 2330.TW 台積電 向上突破 MA20', body: '現價 1,050.00', tag: '2330.TW:ma20',
      url: 'https://wudn9922.github.io/my-stock-screener/atlas/?page=chart&tf=1D&symbol=2330.TW' };
    await sw.dispatch('push', { data: { json: () => payload } });
    expect(sw.shown[0].title).toBe(payload.title);
    expect(sw.shown[0].options).toMatchObject({ body: payload.body, tag: payload.tag, data: { url: payload.url },
      icon: 'https://wudn9922.github.io/my-stock-screener/atlas/icon-192.png', badge: 'https://wudn9922.github.io/my-stock-screener/atlas/badge-96.png' });
    await sw.dispatch('push', { data: { json: () => ({ title: 'x', url: 'https://evil.example/' }) } });
    expect(sw.shown[1].options.data).toEqual({ url: 'https://wudn9922.github.io/my-stock-screener/atlas/' });
    await sw.dispatch('push', { data: { json: () => { throw new Error('not json'); }, text: () => 'plain' } });
    expect(sw.shown[2].title).toBe('Atlas 提醒');
  });

  it('tap focuses an open Atlas window and asks it to open the chart, else opens a new one', async () => {
    const url = 'https://wudn9922.github.io/my-stock-screener/atlas/?page=chart&tf=4H&symbol=NVDA';
    const notification = { close: vi.fn(), data: { url } };
    const posted: unknown[] = [];
    const open = { url: 'https://wudn9922.github.io/my-stock-screener/atlas/?page=markets', focus: vi.fn(async () => open),
      postMessage: (m: unknown, [port]: MessagePort[]) => { posted.push(m); port.postMessage(url); port.close(); }, navigate: vi.fn() };
    const withWindow = worker([{ url: 'https://wudn9922.github.io/other/', focus: vi.fn() }, open]);
    await withWindow.dispatch('notificationclick', { notification });
    expect(notification.close).toHaveBeenCalled();
    expect(open.focus).toHaveBeenCalled();
    expect(open.navigate).not.toHaveBeenCalled();
    expect(posted).toEqual([{ type: 'atlas-push-open', url }]);
    expect(withWindow.opened).toEqual([]);
    const closed = worker();
    await closed.dispatch('notificationclick', { notification: { close: vi.fn(), data: { url: 'javascript:alert(1)' } } });
    expect(closed.opened).toEqual(['https://wudn9922.github.io/my-stock-screener/atlas/']);
  });

  it('holds a tap in memory until the page pulls it once, and drops it after in-page routing', async () => {
    const url = 'https://wudn9922.github.io/my-stock-screener/atlas/?page=chart&tf=1D&symbol=2330.TW';
    const pull = (sw: ReturnType<typeof worker>) => new Promise<unknown>((resolve) => {
      const channel = new MessageChannel();
      channel.port1.on('message', (value) => { channel.port1.close(); resolve(value); });
      sw.listeners.message({ data: { type: 'atlas-push-pull' }, ports: [channel.port2] });
    });
    // No Atlas window listed (iOS may not list a resumed Home Screen app): open the URL and keep the tap.
    const closed = worker();
    const click = closed.dispatch('notificationclick', { notification: { close: vi.fn(), data: { url } } });
    expect(await pull(closed)).toBe(url);
    expect(await pull(closed)).toBeNull();
    await click;
    expect(closed.opened).toEqual([url]);
    closed.listeners.message({ data: { type: 'other' }, ports: [] });

    // The open window acknowledged the in-page route: nothing is left to pull.
    const routed = worker([{
      url: 'https://wudn9922.github.io/my-stock-screener/atlas/', focus: vi.fn(async () => undefined),
      postMessage: (_m, [port]) => { port.postMessage(url); port.close(); } }]);
    await routed.dispatch('notificationclick', { notification: { close: vi.fn(), data: { url } } });
    expect(await pull(routed)).toBeNull();
  });

  it('still routes the open window in place when focusing it is refused', async () => {
    const url = 'https://wudn9922.github.io/my-stock-screener/atlas/?page=chart&tf=1D&symbol=2330.TW';
    const posted: unknown[] = [];
    const navigate = vi.fn();
    const sw = worker([{ url: 'https://wudn9922.github.io/my-stock-screener/atlas/', navigate,
      focus: vi.fn(async () => { throw new Error('InvalidAccessError'); }),
      postMessage: (m, [port]) => { posted.push(m); port.postMessage(url); port.close(); } }]);
    await sw.dispatch('notificationclick', { notification: { close: vi.fn(), data: { url } } });
    expect(posted).toEqual([{ type: 'atlas-push-open', url }]);
    expect(navigate).not.toHaveBeenCalled();
    expect(sw.opened).toEqual([]);
  });

  it('falls back to the stock URL when an existing page does not acknowledge the tap', async () => {
    const url = 'https://wudn9922.github.io/my-stock-screener/atlas/?page=chart&symbol=2330.TW&tf=1D';
    const focus = vi.fn(async () => undefined);
    const navigate = vi.fn(async () => ({ focus }));
    const sw = worker([{ url: 'https://wudn9922.github.io/my-stock-screener/atlas/', focus, navigate,
      postMessage: (_, [port]) => port.close() }]);
    await sw.dispatch('notificationclick', { notification: { close: vi.fn(), data: { url } } });
    expect(navigate).toHaveBeenCalledWith(url);
    expect(focus).toHaveBeenCalledTimes(2);
    expect(sw.opened).toEqual([]);

    const closed = worker([{ url: 'https://wudn9922.github.io/my-stock-screener/atlas/', focus,
      navigate: async () => null }]);
    await closed.dispatch('notificationclick', { notification: { close: vi.fn(), data: { url } } });
    expect(closed.opened).toEqual([url]);
  });
});

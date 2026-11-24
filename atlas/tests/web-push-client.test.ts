import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { MessageChannel, type MessagePort } from 'node:worker_threads';
import { IDBFactory } from 'fake-indexeddb';
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

/** Write a tap the way public/push-sw.js does. */
function recordTap(factory: IDBFactory, value: unknown) {
  return new Promise<void>((resolve, reject) => {
    const request = factory.open('atlas-push-tap', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('tap');
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const transaction = request.result.transaction('tap', 'readwrite');
      transaction.objectStore('tap').put(value, 'latest');
      transaction.oncomplete = () => { request.result.close(); resolve(); };
    };
  });
}

it('takes a recorded tap once, ignoring stale or malformed records', async () => {
  vi.resetModules();
  const factory = new IDBFactory();
  vi.stubGlobal('indexedDB', factory);
  const { takeRecordedTap } = await import('../src/push/pushConfig');
  const now = 1_800_000_000_000;
  const url = 'https://atlas.example/?page=chart&tf=1D&symbol=2330.TW';
  expect(await takeRecordedTap(() => now)).toBeNull();
  await recordTap(factory, { url, at: now - 59_000 });
  expect(await takeRecordedTap(() => now)).toBe(url);
  expect(await takeRecordedTap(() => now)).toBeNull();
  await recordTap(factory, { url, at: now - 61_000 });
  expect(await takeRecordedTap(() => now)).toBeNull();
  expect(await takeRecordedTap(() => now)).toBeNull();
  await recordTap(factory, { url: 42, at: now });
  expect(await takeRecordedTap(() => now)).toBeNull();
});

it('routes a recorded tap at startup and when the app returns to the foreground, without duplicate entries', async () => {
  vi.stubEnv('VITE_WEB_PUSH_PUBLIC_KEY', KEY);
  vi.resetModules();
  const factory = new IDBFactory();
  vi.stubGlobal('indexedDB', factory);
  const { startPushNavigation } = await import('../src/push/pushConfig');
  const location = new URL('https://atlas.example/?page=markets');
  vi.stubGlobal('location', location);
  const history = { pushState: vi.fn((_state: unknown, _title: string, href: string) => { location.href = href; }) };
  vi.stubGlobal('history', history);
  vi.stubGlobal('PopStateEvent', Event);
  vi.stubGlobal('dispatchEvent', vi.fn());
  let registration: object | undefined = { update: async () => {} };
  vi.stubGlobal('navigator', { serviceWorker: Object.assign(new EventTarget(), { getRegistration: async () => registration }) });
  const doc = Object.assign(new EventTarget(), { visibilityState: 'visible' });
  vi.stubGlobal('document', doc);
  const url = 'https://atlas.example/?page=chart&tf=1D&symbol=2330.TW';
  await recordTap(factory, { url, at: Date.now() });
  const ready = startPushNavigation();
  ready();
  await vi.waitFor(() => expect(history.pushState).toHaveBeenCalledWith(null, '', url));

  // The same chart with reordered parameters adds no entry; another stock routes again; other origins never route.
  await recordTap(factory, { url: 'https://atlas.example/?symbol=2330.TW&page=chart&tf=1D', at: Date.now() });
  doc.dispatchEvent(new Event('visibilitychange'));
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(history.pushState).toHaveBeenCalledTimes(1);
  const nvda = 'https://atlas.example/?page=chart&tf=1W&symbol=NVDA';
  await recordTap(factory, { url: nvda, at: Date.now() });
  doc.dispatchEvent(new Event('visibilitychange'));
  await vi.waitFor(() => expect(history.pushState).toHaveBeenLastCalledWith(null, '', nvda));
  await recordTap(factory, { url: 'https://evil.example/?page=chart', at: Date.now() });
  doc.dispatchEvent(new Event('visibilitychange'));
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(history.pushState).toHaveBeenCalledTimes(2);

  // Without a push registration (guests), the store is never opened.
  registration = undefined;
  const open = vi.spyOn(factory, 'open');
  doc.dispatchEvent(new Event('visibilitychange'));
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(open).not.toHaveBeenCalled();
});

/** Run public/push-sw.js against a fake worker global. */
function worker(windows: { url: string; focus?: () => Promise<unknown>; postMessage?: (m: unknown, ports: MessagePort[]) => void; navigate?: (url: string) => Promise<unknown> }[] = [], indexedDB: IDBFactory = new IDBFactory()) {
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
  // Keep the fake worker's acknowledgement timeout short.
  const fastTimeout = (callback: () => void, ms: number) => setTimeout(callback, Math.min(ms, 20));
  runInNewContext(readFileSync(new URL('../public/push-sw.js', import.meta.url), 'utf8'), { self, URL, Date, Number, String, MessageChannel, setTimeout: fastTimeout, clearTimeout, indexedDB });
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
    expect(Object.keys(worker().listeners).sort()).toEqual(['activate', 'install', 'notificationclick', 'push']);
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

  it('records the tap for the page unless the open window routed in place', async () => {
    vi.resetModules();
    const { takeRecordedTap } = await import('../src/push/pushConfig');
    const url = 'https://wudn9922.github.io/my-stock-screener/atlas/?page=chart&tf=1D&symbol=2330.TW';
    // No Atlas window listed (iOS does not list a resumed Home Screen app): open the URL and keep the record.
    const factory = new IDBFactory();
    vi.stubGlobal('indexedDB', factory);
    const closed = worker([], factory);
    await closed.dispatch('notificationclick', { notification: { close: vi.fn(), data: { url } } });
    expect(closed.opened).toEqual([url]);
    expect(await takeRecordedTap()).toBe(url);
    expect(await takeRecordedTap()).toBeNull();

    // The open window acknowledged the in-page route: nothing is left for the page to take again.
    const routedFactory = new IDBFactory();
    vi.stubGlobal('indexedDB', routedFactory);
    const routed = worker([{
      url: 'https://wudn9922.github.io/my-stock-screener/atlas/', focus: vi.fn(async () => undefined),
      postMessage: (_m, [port]) => { port.postMessage(url); port.close(); } }], routedFactory);
    await routed.dispatch('notificationclick', { notification: { close: vi.fn(), data: { url } } });
    expect(await takeRecordedTap()).toBeNull();
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

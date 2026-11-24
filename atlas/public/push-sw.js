/*
 * Atlas push-only worker (registered at <base>push/ only after the user turns on notifications).
 *
 * Deliberately NO fetch handler and NO CacheStorage: the screener deployment stays memory-only
 * (ADR-016) and the retired offline shell worker (sw.js) is not reintroduced. Its scope does not
 * contain any Atlas page, so it never controls or intercepts a page either.
 */
const scopeURL = new URL(self.registration.scope);
// The Atlas app root is the parent of this worker's push/ scope.
const atlasRoot = new URL('../', scopeURL);
const iconURL = new URL('icon-192.png', atlasRoot).href;
const badgeURL = new URL('badge-96.png', atlasRoot).href;

function atlasURL(value) {
  try {
    const url = new URL(String(value || ''), atlasRoot);
    if (url.origin === atlasRoot.origin && url.pathname.startsWith(atlasRoot.pathname)) return url.href;
  } catch {
    // Fall through to the app root.
  }
  return atlasRoot.href;
}

self.addEventListener('install', () => { self.skipWaiting(); });
self.addEventListener('activate', (event) => { event.waitUntil(self.clients.claim()); });

self.addEventListener('push', (event) => {
  let message = {};
  try {
    message = event.data ? event.data.json() : {};
  } catch {
    message = { body: event.data ? event.data.text() : '' };
  }
  // Every push must show a notification (iOS/Chrome revoke silent subscriptions).
  const title = String(message.title || 'Atlas 提醒').slice(0, 120);
  event.waitUntil(self.registration.showNotification(title, {
    body: String(message.body || '').slice(0, 500),
    icon: iconURL,
    badge: badgeURL,
    tag: String(message.tag || 'atlas'),
    renotify: true,
    timestamp: Number(message.ts) || Date.now(),
    data: { url: atlasURL(message.url) },
    lang: 'zh-Hant',
  }));
});

// A tap is also kept in memory until the Atlas page asks for it. iOS resumes a backgrounded Home Screen app
// without delivering a worker message sent while it was suspended (and may not list it as a client), so the
// page pulls the tap when it returns to the foreground. Memory only: no storage outside the account policy.
const PENDING_TAP_MS = 60 * 1000;
const HOLD_TAP_MS = 30 * 1000;
let pendingTap = null;
// TEMPORARY tap diagnostics (iPhone background tap investigation): steps of the latest tap, sent with each pull.
let tapTrace = [];
const trace = (step) => { tapTrace.push(`${((Date.now() - (tapTrace.at || Date.now())) / 1000).toFixed(1)}s ${step}`); };

function rememberTap(url) {
  if (pendingTap) pendingTap.release();
  let release;
  const taken = new Promise((resolve) => { release = resolve; });
  pendingTap = { url, at: Date.now(), release };
  tapTrace = [];
  tapTrace.at = Date.now();
  return taken;
}

function takeTap() {
  const tap = pendingTap;
  pendingTap = null;
  if (!tap) return null;
  tap.release();
  return Date.now() - tap.at <= PENDING_TAP_MS ? tap.url : null;
}

self.addEventListener('message', (event) => {
  const data = event.data;
  const port = event.ports && event.ports[0];
  if (!data || data.type !== 'atlas-push-pull' || !port) return;
  const url = takeTap();
  if (url) trace('pulled');
  port.postMessage({ url, trace: tapTrace.slice() });
});

function routeInWindow(client, url) {
  const channel = new MessageChannel();
  let timer;
  return new Promise((resolve) => {
    timer = setTimeout(() => resolve(false), 750);
    channel.port1.onmessage = (event) => resolve(event.data === url);
    client.postMessage({ type: 'atlas-push-open', url }, [channel.port2]);
  }).finally(() => {
    clearTimeout(timer);
    channel.port1.close();
  });
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = atlasURL(event.notification.data && event.notification.data.url);
  const taken = rememberTap(url);
  event.waitUntil((async () => {
    trace('click');
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const open = windows.find((client) => {
      try { return new URL(client.url).pathname.startsWith(atlasRoot.pathname); } catch { return false; }
    });
    trace(`windows ${windows.length}, atlas ${open ? 'found' : 'none'}`);
    if (open) {
      // Prefer in-page routing to keep the active Atlas session and workspace. Focus can be refused
      // (iOS Home Screen apps), so route the window whether or not focusing it worked.
      let target = open;
      try { target = (await open.focus()) || open; trace('focus ok'); } catch (error) { trace(`focus failed ${error && error.name}`); }
      try {
        if (await routeInWindow(target, url)) { trace('page acknowledged'); takeTap(); return; }
        trace('no page acknowledgement');
      } catch (error) { trace(`message failed ${error && error.name}`); }
      try {
        const navigated = await open.navigate(url);
        if (navigated) {
          takeTap();
          await navigated.focus();
          return;
        }
      } catch (error) { trace(`navigate failed ${error && error.name}`); }
    }
    try {
      const opened = await self.clients.openWindow(url);
      trace(`openWindow ${opened ? 'client' : 'null'}`);
    } catch (error) { trace(`openWindow failed ${error && error.name}`); }
    // Stay alive until the page pulls the tap (or give up): an opened or resumed window asks on start/foreground.
    let timer;
    const pulled = await Promise.race([taken.then(() => true), new Promise((resolve) => { timer = setTimeout(() => resolve(false), HOLD_TAP_MS); })]);
    clearTimeout(timer);
    if (!pulled) trace('hold expired without a pull');
  })());
});

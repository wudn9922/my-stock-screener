/*
 * Atlas push-only worker (registered at <base>push/ only after the user turns on notifications).
 *
 * Deliberately NO fetch handler and NO CacheStorage: the screener deployment stays memory-only
 * (ADR-016) and the retired offline shell worker (sw.js) is not reintroduced. Its scope does not
 * contain any Atlas page, so it never controls or intercepts a page either.
 *
 * Tap handoff: iOS runs the push/click handlers in a worker instance the page cannot reach, delivers no
 * message to a resumed Home Screen app, and openWindow does not move it. So a tap also records the chart
 * URL in a one-record IndexedDB store that the page takes (and deletes) when it comes to the foreground.
 * Push requires the account's cloud storage grant (user-settings checks it on every push request), and the
 * record is only a public chart URL that expires after a minute.
 */
const scopeURL = new URL(self.registration.scope);
// The Atlas app root is the parent of this worker's push/ scope.
const atlasRoot = new URL('../', scopeURL);
const iconURL = new URL('icon-192.png', atlasRoot).href;
const badgeURL = new URL('badge-96.png', atlasRoot).href;
const TAP_DB = 'atlas-push-tap';
const TAP_STORE = 'tap';
const TAP_KEY = 'latest';

function atlasURL(value) {
  try {
    const url = new URL(String(value || ''), atlasRoot);
    if (url.origin === atlasRoot.origin && url.pathname.startsWith(atlasRoot.pathname)) return url.href;
  } catch {
    // Fall through to the app root.
  }
  return atlasRoot.href;
}

/** Run one request against the tap store; resolves when its transaction commits. */
function tapStore(mode, run) {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(TAP_DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(TAP_STORE);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const db = request.result;
      const transaction = db.transaction(TAP_STORE, mode);
      run(transaction.objectStore(TAP_STORE));
      transaction.oncomplete = () => { db.close(); resolve(); };
      transaction.onerror = transaction.onabort = () => { db.close(); reject(transaction.error); };
    };
  });
}
const saveTap = (url) => tapStore('readwrite', (store) => store.put({ url, at: Date.now() }, TAP_KEY)).catch(() => {});
const clearTap = () => tapStore('readwrite', (store) => store.delete(TAP_KEY)).catch(() => {});

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
  event.waitUntil((async () => {
    // Record first: on iOS this is the only path that reaches a resumed app.
    await saveTap(url);
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const open = windows.find((client) => {
      try { return new URL(client.url).pathname.startsWith(atlasRoot.pathname); } catch { return false; }
    });
    if (open) {
      // Prefer in-page routing to keep the active Atlas session and workspace. Focus can be refused
      // (iOS Home Screen apps), so route the window whether or not focusing it worked.
      let target = open;
      try { target = (await open.focus()) || open; } catch { /* Route the unfocused window below. */ }
      try {
        if (await routeInWindow(target, url)) { await clearTap(); return; }
      } catch { /* Fall back to navigation with the actual stock URL. */ }
      try {
        const navigated = await open.navigate(url);
        if (navigated) {
          await clearTap();
          await navigated.focus();
          return;
        }
      } catch { /* The original window may already have closed. */ }
    }
    await self.clients.openWindow(url).catch(() => null);
  })());
});

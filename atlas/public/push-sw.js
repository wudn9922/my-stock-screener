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

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = atlasURL(event.notification.data && event.notification.data.url);
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const open = windows.find((client) => {
      try { return new URL(client.url).pathname.startsWith(atlasRoot.pathname); } catch { return false; }
    });
    if (open) {
      // This worker does not control Atlas pages, so the page itself routes to the chart.
      try {
        const focused = await open.focus();
        (focused || open).postMessage({ type: 'atlas-push-open', url });
        return;
      } catch {
        // A window that cannot be focused: open a fresh one below.
      }
    }
    await self.clients.openWindow(url);
  })());
});

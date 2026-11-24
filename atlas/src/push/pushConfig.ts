/** Build-time Web Push configuration and notification-tap routing (kept tiny: loaded at startup). */
const PUBLIC_KEY_PATTERN = /^B[A-Za-z0-9_-]{86}$/;

export function configuredPublicKey(value: unknown = import.meta.env?.VITE_WEB_PUSH_PUBLIC_KEY): string {
  return typeof value === 'string' && PUBLIC_KEY_PATTERN.test(value.trim()) ? value.trim() : '';
}
export const WEB_PUSH_PUBLIC_KEY = configuredPublicKey();
export const webPushEnabled = () => WEB_PUSH_PUBLIC_KEY !== '';

const base = () => new URL(import.meta.env?.BASE_URL || '/', location.origin);
export const pushScope = () => new URL('push/', base()).href;
export const pushScript = () => new URL('push-sw.js', base()).href;
/** At most one worker update check per foreground return within this window. */
const WORKER_REFRESH_MS = 10 * 60 * 1000;
/** When a foregrounded app asks the push worker for a pending tap. */
const TAP_PULL_DELAYS_MS = [0, 500, 1500, 3000, 6000, 10000];

/** Same store as public/push-sw.js: a tap record { url, at } and the last push { id, url, at, listed }. */
const TAP_DB = 'atlas-push-tap';
const TAP_STORE = 'tap';
const TAP_KEY = 'latest';
const PUSH_KEY = 'push';
const TAP_TTL_MS = 60 * 1000;
/** How long a shown notification can still count as tapped once it disappears. */
const PUSH_TTL_MS = 2 * 60 * 1000;
/** How often a visible iOS Home Screen app checks for a tapped notification. */
const PUSH_POLL_MS = 2000;
/** The same chart reported again within this window (a tap record after a disappeared notification) is one tap. */
const TAKE_REPEAT_MS = 15 * 1000;

type Stored = { id?: unknown; url?: unknown; at?: unknown; listed?: unknown };

/** Run requests in one store transaction; resolves with `run`'s result once it commits, else `fallback`. */
function tapStore<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => () => T, fallback: T): Promise<T> {
  return new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') { resolve(fallback); return; }
    let request: IDBOpenDBRequest;
    try { request = indexedDB.open(TAP_DB, 1); } catch { resolve(fallback); return; }
    request.onupgradeneeded = () => request.result.createObjectStore(TAP_STORE);
    request.onerror = () => resolve(fallback);
    request.onsuccess = () => {
      const db = request.result;
      try {
        const transaction = db.transaction(TAP_STORE, mode);
        const result = run(transaction.objectStore(TAP_STORE));
        transaction.oncomplete = () => { db.close(); resolve(result()); };
        transaction.onerror = transaction.onabort = () => { db.close(); resolve(fallback); };
      } catch {
        db.close();
        resolve(fallback);
      }
    };
  });
}

/** The record's URL when it is well formed and younger than `ttl` (allowing small clock skew). */
function freshURL(record: Stored | null, now: number, ttl: number): string | null {
  const age = record && typeof record.at === 'number' ? now - record.at : NaN;
  return record && typeof record.url === 'string' && age >= -5000 && age <= ttl ? record.url : null;
}

/** Read and delete the recorded tap in one transaction; a stale or malformed record yields null. */
export function takeRecordedTap(now = () => Date.now()): Promise<string | null> {
  return tapStore('readwrite', (store) => {
    let record: Stored | null = null;
    const read = store.get(TAP_KEY);
    read.onsuccess = () => {
      record = (read.result as Stored | undefined) ?? null;
      if (record) store.delete(TAP_KEY);
    };
    return () => freshURL(record, now(), TAP_TTL_MS);
  }, null);
}

/**
 * iOS can bring the Home Screen app forward from a notification without firing notificationclick (a tap while
 * Atlas is open, or just after leaving it). A notification the worker saw listed that is gone within two minutes
 * was tapped (a banner swiped away moves to Notification Center and stays listed), so take its chart URL once.
 */
export async function takeDismissedPush(
  registration: Pick<ServiceWorkerRegistration, 'getNotifications'>, now = () => Date.now(),
): Promise<string | null> {
  const record = await tapStore('readonly', (store) => {
    let value: Stored | null = null;
    const read = store.get(PUSH_KEY);
    read.onsuccess = () => { value = (read.result as Stored | undefined) ?? null; };
    return () => value;
  }, null as Stored | null);
  if (!record) return null;
  const url = freshURL(record, now(), PUSH_TTL_MS);
  if (!url || typeof record.id !== 'string' || record.listed !== true) {
    // Expired, or a platform that does not list its notifications: nothing to guess from.
    if (!url) await removePush(record.id);
    return null;
  }
  let shown: Notification[];
  try { shown = await registration.getNotifications(); } catch { return null; }
  if (shown.some((notification) => (notification.data as { pushId?: unknown } | null)?.pushId === record.id)) return null;
  return await removePush(record.id) ? url : null;
}

/** Delete the push record if it is still the one with `id` (a newer push may have replaced it). */
function removePush(id: unknown): Promise<boolean> {
  return tapStore('readwrite', (store) => {
    let removed = false;
    const read = store.get(PUSH_KEY);
    read.onsuccess = () => {
      const record = read.result as Stored | undefined;
      if (!record || record.id !== id) return;
      store.delete(PUSH_KEY);
      removed = true;
    };
    return () => removed;
  }, false);
}

/** Capture taps at startup and defer URL changes until LINE has consumed any OAuth return. */
export function startPushNavigation() {
  if (!webPushEnabled() || !('serviceWorker' in navigator)) return () => {};
  const root = base();
  const script = pushScript();
  let ready = false;
  let pending: URL | null = null;
  // Same page and parameters in any order: a window opened at the tap URL does not get a duplicate entry.
  const target = (url: URL) => {
    const params = new URLSearchParams(url.search);
    params.sort();
    return `${url.origin}${url.pathname}?${params}`;
  };
  const open = (url: URL) => {
    if (target(new URL(location.href)) === target(url)) return;
    history.pushState(null, '', url.href);
    dispatchEvent(new PopStateEvent('popstate'));
  };
  /** Accept an Atlas URL from the push worker; returns it when valid. */
  const accept = (value: unknown): URL | null => {
    if (typeof value !== 'string') return null;
    let url: URL;
    try { url = new URL(value, root); } catch { return null; }
    if (url.origin !== root.origin || !url.pathname.startsWith(root.pathname)) return null;
    if (ready) open(url);
    else pending = url;
    return url;
  };
  navigator.serviceWorker.addEventListener('message', (event: MessageEvent) => {
    const source = event.source as ServiceWorker | null;
    const data = event.data as { type?: string; url?: string } | null;
    if (data?.type !== 'atlas-push-open' || source?.scriptURL !== script) return;
    const url = accept(data.url);
    if (url) event.ports[0]?.postMessage(url.href);
  });
  // Take a tap the push worker recorded (iOS delivers no worker message to a resumed Home Screen app), and on
  // iOS a notification that disappeared. Only a device with a push registration reads the store, so guests never
  // create it (ADR-016). Both paths can report the same tap: route the same chart once.
  const iosHomeScreen = (navigator as Navigator & { standalone?: boolean }).standalone === true;
  let taken: { key: string; at: number } | null = null;
  const take = (value: string | null) => {
    if (!value) return;
    let key: string;
    try { key = target(new URL(value, root)); } catch { return; }
    if (taken?.key === key && Date.now() - taken.at < TAKE_REPEAT_MS) return;
    if (accept(value)) taken = { key, at: Date.now() };
  };
  let pulling = false;
  const pullTap = () => {
    if (pulling) return;
    pulling = true;
    Promise.resolve(navigator.serviceWorker?.getRegistration?.(pushScope())).then(async (registration) => {
      if (!registration) return;
      take(await takeRecordedTap());
      if (iosHomeScreen) take(await takeDismissedPush(registration));
    }).catch(() => {}).finally(() => { pulling = false; });
  };
  // iOS gives a page that stays open no event for a notification tap: check while the Home Screen app is visible.
  let poll: ReturnType<typeof setInterval> | undefined;
  const watch = () => {
    if (!iosHomeScreen || !ready || typeof document === 'undefined') return;
    const visible = document.visibilityState === 'visible';
    if (visible && poll === undefined) poll = setInterval(pullTap, PUSH_POLL_MS);
    if (!visible && poll !== undefined) { clearInterval(poll); poll = undefined; }
  };
  navigator.serviceWorker.startMessages?.();
  // The push worker's scope (push/) is never navigated to, so the browser rarely checks it for updates on its own.
  // Ask at start and when the app returns to the foreground (iOS resumes a Home Screen app without restarting it),
  // so a deployed fix to notification taps reaches devices that enabled push earlier.
  const refreshWorker = () => {
    navigator.serviceWorker?.getRegistration?.(pushScope()).then((registration) => registration?.update()).catch(() => {});
  };
  refreshWorker();
  let refreshedAt = Date.now();
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      watch();
      if (document.visibilityState !== 'visible') return;
      // The tap handler may still be running (or run late) when the app comes forward: ask again for a while.
      for (const delay of TAP_PULL_DELAYS_MS) setTimeout(pullTap, delay);
      if (Date.now() - refreshedAt < WORKER_REFRESH_MS) return;
      refreshedAt = Date.now();
      refreshWorker();
    });
  }
  return () => {
    ready = true;
    if (pending) open(pending);
    pending = null;
    pullTap();
    watch();
  };
}

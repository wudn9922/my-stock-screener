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
const TAP_PULL_DELAYS_MS = [0, 400, 1500];

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
  // Ask the push worker for a tap it is still holding (iOS does not deliver its message to a resumed app).
  const pullTap = () => {
    navigator.serviceWorker?.getRegistration?.(pushScope()).then((registration) => {
      const worker = registration?.active;
      if (!worker || worker.scriptURL !== script || typeof MessageChannel === 'undefined') return;
      const channel = new MessageChannel();
      channel.port1.onmessage = (event: MessageEvent) => {
        channel.port1.close();
        accept(event.data);
      };
      worker.postMessage({ type: 'atlas-push-pull' }, [channel.port2]);
    }).catch(() => {});
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
      if (document.visibilityState !== 'visible') return;
      // The tap handler may still be running when the app comes forward: ask again shortly after.
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
  };
}

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

/** Capture taps at startup and defer URL changes until LINE has consumed any OAuth return. */
export function startPushNavigation() {
  if (!webPushEnabled() || !('serviceWorker' in navigator)) return () => {};
  const root = base();
  const script = pushScript();
  let ready = false;
  let pending: URL | null = null;
  const open = (url: URL) => {
    if (location.href === url.href) return;
    history.pushState(null, '', url.href);
    dispatchEvent(new PopStateEvent('popstate'));
  };
  navigator.serviceWorker.addEventListener('message', (event: MessageEvent) => {
    const source = event.source as ServiceWorker | null;
    const data = event.data as { type?: string; url?: string } | null;
    if (data?.type !== 'atlas-push-open' || typeof data.url !== 'string' || source?.scriptURL !== script) return;
    let url: URL;
    try { url = new URL(data.url, root); } catch { return; }
    if (url.origin !== root.origin || !url.pathname.startsWith(root.pathname)) return;
    if (ready) open(url);
    else pending = url;
    event.ports[0]?.postMessage(url.href);
  });
  navigator.serviceWorker.startMessages?.();
  // The push worker's scope (push/) is never navigated to, so the browser rarely checks it for updates on its own.
  // Ask once per start so a deployed fix to notification taps reaches devices that enabled push earlier.
  navigator.serviceWorker.getRegistration?.(pushScope()).then((registration) => registration?.update()).catch(() => {});
  return () => {
    ready = true;
    if (pending) open(pending);
    pending = null;
  };
}

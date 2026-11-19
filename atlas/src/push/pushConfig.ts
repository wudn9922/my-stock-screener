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

/** Notification taps focus an open Atlas window; the push worker asks it to open the chart. */
export function startPushNavigation() {
  if (!webPushEnabled() || !('serviceWorker' in navigator)) return;
  const root = base();
  navigator.serviceWorker.addEventListener('message', (event: MessageEvent) => {
    const source = event.source as ServiceWorker | null;
    const data = event.data as { type?: string; url?: string } | null;
    if (data?.type !== 'atlas-push-open' || !source?.scriptURL?.endsWith('/push-sw.js')) return;
    let url: URL;
    try { url = new URL(String(data.url), root); } catch { return; }
    if (url.origin !== root.origin || !url.pathname.startsWith(root.pathname)) return;
    history.pushState(null, '', url.href);
    dispatchEvent(new PopStateEvent('popstate'));
  });
  navigator.serviceWorker.startMessages?.();
}

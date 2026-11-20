import { installPlatform, isLineBrowser } from '../pwa/install';
import { settingsApi } from '../cloud/settingsApi';
import { WEB_PUSH_PUBLIC_KEY, pushScope, pushScript } from './pushConfig';

export { webPushEnabled } from './pushConfig';

/**
 * Phone notifications through standard Web Push. A separate push-only worker (public/push-sw.js,
 * no fetch handler, no caches) is registered at `<base>push/` only when the user turns
 * notifications on, so the screener's memory-only policy and retired shell worker stay intact.
 */
export type PushSupport = 'line' | 'ios-install' | 'unsupported' | 'supported';
type PushHost = Pick<Window, 'matchMedia'> & { navigator: Navigator & { standalone?: boolean }; PushManager?: unknown; Notification?: unknown };

/** What this browser can do, before any permission prompt. */
export function pushSupport(host: PushHost = window): PushSupport {
  const navigator = host.navigator;
  if (isLineBrowser(navigator.userAgent)) return 'line';
  const standalone = navigator.standalone === true || host.matchMedia?.('(display-mode: standalone)').matches === true;
  // iOS/iPadOS deliver Web Push only to a Home Screen web app (16.4+).
  if (installPlatform(navigator) === 'ios' && !standalone) return 'ios-install';
  if (!('serviceWorker' in navigator) || !host.PushManager || !host.Notification) return 'unsupported';
  return 'supported';
}

export const notificationPermission = (): NotificationPermission | 'unsupported' =>
  typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;

/** A short, non-identifying label so the device list is readable. */
export function deviceLabel(host: PushHost = window) {
  const ua = host.navigator.userAgent;
  const device = installPlatform(host.navigator) === 'ios' ? (/iPhone|iPod/.test(ua) ? 'iPhone' : 'iPad') : /Android/.test(ua) ? 'Android' : /Mac/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : '電腦';
  const browser = /EdgA?\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /SamsungBrowser/.test(ua) ? 'Samsung'
    : /Chrome\//.test(ua) ? 'Chrome' : 'Safari';
  const standalone = host.navigator.standalone === true || host.matchMedia?.('(display-mode: standalone)').matches === true;
  return `${device} · ${standalone ? '主畫面 App' : browser}`;
}

function keyBytes(key: string) {
  const text = atob(key.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (key.length % 4)) % 4));
  return Uint8Array.from(text, (char) => char.charCodeAt(0));
}
const sameKey = (buffer: ArrayBuffer | null, key: string) => {
  if (!buffer) return false;
  const a = new Uint8Array(buffer), b = keyBytes(key);
  return a.length === b.length && a.every((value, index) => value === b[index]);
};

async function pushRegistration() {
  if (!('serviceWorker' in navigator)) return null;
  const registration = await navigator.serviceWorker.getRegistration(pushScope()).catch(() => undefined);
  // getRegistration matches by prefix; only the push-only worker counts.
  return registration && registration.scope === pushScope() ? registration : null;
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  const registration = await pushRegistration();
  return registration ? registration.pushManager.getSubscription().catch(() => null) : null;
}

function activated(registration: ServiceWorkerRegistration) {
  if (registration.active) return Promise.resolve(registration);
  const worker = registration.installing ?? registration.waiting;
  return new Promise<ServiceWorkerRegistration>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('通知服務啟動逾時，請重新整理後再試。')), 15000);
    worker?.addEventListener('statechange', () => {
      if (worker.state === 'activated') { clearTimeout(timer); resolve(registration); }
      if (worker.state === 'redundant') { clearTimeout(timer); reject(new Error('通知服務無法啟動，請重新整理後再試。')); }
    });
  });
}

export interface PushDevice { id: string; label: string; service: string; createdAt: string; lastSuccessAt: string | null; failureCount: number; current: boolean }
export interface PushStatus { devices: PushDevice[]; limit: number; configured: boolean }

export const fetchPushStatus = async () => {
  const subscription = await currentSubscription();
  return settingsApi<PushStatus>('push.status', { endpoint: subscription?.endpoint ?? null });
};

/** Call directly from the toggle's click: the permission prompt needs the user's gesture. */
export async function enablePush(): Promise<NotificationPermission> {
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') return permission;
  const registration = await activated(await navigator.serviceWorker.register(pushScript(), { scope: pushScope(), updateViaCache: 'none' }));
  let subscription = await registration.pushManager.getSubscription();
  // A subscription made for another server key can never be delivered to; replace it.
  if (subscription && !sameKey(subscription.options.applicationServerKey, WEB_PUSH_PUBLIC_KEY)) {
    await subscription.unsubscribe().catch(() => false);
    subscription = null;
  }
  subscription ??= await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(WEB_PUSH_PUBLIC_KEY) });
  try {
    await settingsApi('push.subscribe', { subscription: subscription.toJSON(), label: deviceLabel() });
  } catch (error) {
    // Keep browser and server in agreement: an unsaved subscription is removed again.
    await subscription.unsubscribe().catch(() => false);
    throw error;
  }
  return permission;
}

export async function disablePush() {
  const registration = await pushRegistration();
  const subscription = await registration?.pushManager.getSubscription().catch(() => null);
  let serverError: unknown = null;
  if (subscription) {
    try { await settingsApi('push.unsubscribe', { endpoint: subscription.endpoint }); } catch (error) { serverError = error; }
    await subscription.unsubscribe().catch(() => false);
  }
  await registration?.unregister().catch(() => false);
  if (serverError) throw serverError;
}

export const removePushDevice = async (device: PushDevice) => {
  await settingsApi('push.unsubscribe', { id: device.id });
  if (device.current) {
    const registration = await pushRegistration();
    await (await registration?.pushManager.getSubscription().catch(() => null))?.unsubscribe().catch(() => false);
    await registration?.unregister().catch(() => false);
  }
};

export const sendTestPush = () => settingsApi<{ devices: number; sent: number; removed: number; failed: number }>('push.test');

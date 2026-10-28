/**
 * Remembered-device sign-in: after an explicit LINE login with 「記住這台裝置」 checked, the server issues
 * a random device token kept in this browser's localStorage. It stands in for the short-lived LINE ID
 * token (same `idToken` field) for up to 90 days of inactivity; the server still verifies the token and
 * the storage grant on every request. Logging out forgets it here and on the server.
 */
import type { LineIdentity } from './lineIdentity';

const API = 'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/user-settings';
const SESSION_KEY = 'atlas-device-session-v1';
/** Carries the checkbox across a same-window LINE OAuth redirect only. */
const REMEMBER_KEY = 'atlas-remember-device';
const TOKEN = /^atlasdev\.[A-Za-z0-9_-]{43}$/;
const USER_ID = /^U[0-9a-f]{32}$/;
export const DEVICE_SESSION_DAYS = 90;

interface StoredSession { token: string; userId: string }
const listeners = new Set<() => void>();
let generation = 0;

const apiUrl = () => import.meta.env?.VITE_SETTINGS_API_URL || API;
function notify() { generation++; listeners.forEach(listener => listener()); }
export function subscribeDeviceSession(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export const deviceSessionVersion = () => generation;

/** The remembered session, if any. Reading never writes storage. */
export function rememberedDevice(): StoredSession | null {
  try {
    const parsed = JSON.parse(globalThis.localStorage?.getItem(SESSION_KEY) ?? 'null') as Partial<StoredSession> | null;
    if (parsed && typeof parsed.token === 'string' && TOKEN.test(parsed.token) && typeof parsed.userId === 'string' && USER_ID.test(parsed.userId))
      return { token: parsed.token, userId: parsed.userId };
  } catch { /* Unavailable or corrupt storage is the same as no remembered device. */ }
  return null;
}

/** An identity backed by the remembered device token, while it stays the stored one. */
export function deviceIdentity(): LineIdentity | null {
  const session = rememberedDevice();
  if (!session) return null;
  return {
    userId: session.userId,
    kind: 'device',
    idToken: () => rememberedDevice()?.token === session.token ? session.token : null,
  };
}

/** Exchange a fresh LINE ID token for a device token. Failure leaves the live login working. */
export async function rememberDevice(identity: LineIdentity): Promise<boolean> {
  const idToken = identity.kind === 'device' ? null : identity.idToken();
  if (!idToken) return false;
  try {
    const response = await fetch(apiUrl(), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'device.session.create', idToken }),
      signal: AbortSignal.timeout(15000), cache: 'no-store',
    });
    const envelope = await response.json() as { ok?: boolean; data?: { userId?: string; deviceToken?: string } };
    const token = envelope.data?.deviceToken;
    if (!response.ok || !envelope.ok || envelope.data?.userId !== identity.userId || typeof token !== 'string' || !TOKEN.test(token)) return false;
    const previous = rememberedDevice();
    globalThis.localStorage.setItem(SESSION_KEY, JSON.stringify({ token, userId: identity.userId }));
    if (previous && previous.token !== token) revoke(previous.token);
    notify();
    return true;
  } catch {
    return false;
  }
}

function revoke(token: string) {
  void fetch(apiUrl(), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'device.session.revoke', idToken: token }),
    keepalive: true, cache: 'no-store',
  }).catch(() => {});
}

/**
 * Forget this device locally; `revokeOnServer` also signs it out server-side (best effort).
 * Only an explicit logout revokes: a token the server already rejected needs no revocation.
 */
export function forgetDevice(revokeOnServer = true) {
  const session = rememberedDevice();
  if (!session) return; // Never write storage for a visitor who did not opt in.
  try { globalThis.localStorage.removeItem(SESSION_KEY); } catch { /* already unavailable */ }
  if (revokeOnServer) revoke(session.token);
  notify();
}

/** Before a same-window LINE OAuth redirect, keep the checkbox for the returning page. */
export function rememberAfterRedirect(remember: boolean) {
  try {
    if (remember) globalThis.sessionStorage?.setItem(REMEMBER_KEY, '1');
    else globalThis.sessionStorage?.removeItem(REMEMBER_KEY);
  } catch { /* The returning page then simply does not remember the device. */ }
}

/** On the OAuth return: remember the device if the login started with the checkbox on. */
export async function completeRememberAfterRedirect(identity: LineIdentity | null) {
  let requested = false;
  try {
    requested = globalThis.sessionStorage?.getItem(REMEMBER_KEY) === '1';
    if (requested) globalThis.sessionStorage.removeItem(REMEMBER_KEY);
  } catch { requested = false; }
  if (requested && identity?.idToken()) await rememberDevice(identity);
}

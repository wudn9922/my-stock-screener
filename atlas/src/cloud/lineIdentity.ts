/**
 * LINE identity for cloud drawing sync. Only inside the LINE app (opened from the report LIFF): the LIFF
 * SDK is loaded on demand there and nowhere else, so ordinary browsers use transient memory and
 * never download the SDK.
 *
 * liff.init() must run before the router rewrites the URL: a LINE login returns with `code`, `state`,
 * `liffClientId`… in the query, which the canonical-route rewrite would strip, making LIFF log in again
 * forever. main.tsx therefore starts it before rendering (startLineIdentity). A session guard also stops
 * after repeated unsuccessful attempts so a login problem can never loop the page.
 */
export const DEFAULT_LIFF_ID = '2010330411-6JhrotT9';
const LIFF_SDK_URL = 'https://static.line-scdn.net/liff/edge/2/sdk.js';
/** Treat a token that expires within this window as already expired. */
const EXPIRY_MARGIN_MS = 60_000;
/** At most this many liff.init() attempts without a login within GUARD_WINDOW_MS (per tab). */
const MAX_ATTEMPTS = 2;
const GUARD_WINDOW_MS = 120_000;
const GUARD_KEY = 'atlas-liff-attempts';
// Login attempts are transient too: unidentified visitors must never write sessionStorage.
const attemptMemory = new Map<string, string>();
const attemptStore = {
  getItem: (key: string) => attemptMemory.get(key) ?? null,
  setItem: (key: string, value: string) => { attemptMemory.set(key, value); },
  removeItem: (key: string) => { attemptMemory.delete(key); },
} as Storage;
let initializedSdk: LiffSdk | null = null;

interface LiffSdk {
  init(config: { liffId: string }): Promise<void>;
  isLoggedIn(): boolean;
  getIDToken(): string | null;
  getDecodedIDToken(): { sub?: string; exp?: number } | null;
}

export interface LineIdentity {
  /** LINE user ID (`U…`), used only to key local sync metadata. */
  userId: string;
  /** Current ID token, or null once it has expired (reopening the LIFF page issues a new one). */
  idToken(): string | null;
}

export function isLineInAppBrowser(userAgent = globalThis.navigator?.userAgent ?? ''): boolean {
  return /\bLine\/\d/i.test(userAgent);
}

function loadSdk(): Promise<LiffSdk> {
  const existing = (globalThis as { liff?: LiffSdk }).liff;
  if (existing) return Promise.resolve(existing);
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = LIFF_SDK_URL;
    script.async = true;
    script.onload = () => {
      const sdk = (globalThis as { liff?: LiffSdk }).liff;
      if (sdk) resolve(sdk);
      else reject(new Error('LIFF SDK did not load'));
    };
    script.onerror = () => reject(new Error('LIFF SDK failed to load'));
    document.head.appendChild(script);
  });
}

/** Recent attempt timestamps; false when another attempt now could loop the page. */
export function claimLoginAttempt(storage: Storage | undefined, now: number): boolean {
  let attempts: number[] = [];
  try {
    const parsed: unknown = JSON.parse(storage?.getItem(GUARD_KEY) ?? '[]');
    if (Array.isArray(parsed)) attempts = parsed.filter((value): value is number => typeof value === 'number' && now - value < GUARD_WINDOW_MS);
  } catch {
    attempts = [];
  }
  if (attempts.length >= MAX_ATTEMPTS) return false;
  try {
    storage?.setItem(GUARD_KEY, JSON.stringify([...attempts, now]));
  } catch {
    // Without storage the guard cannot persist across reloads; refuse rather than risk a loop.
    return false;
  }
  return true;
}

function clearLoginAttempts(storage: Storage | undefined) {
  try {
    storage?.removeItem(GUARD_KEY);
  } catch {
    // ignore
  }
}

let pending: Promise<LineIdentity | null> | null = null;

/**
 * Starts LIFF once per page load and returns the shared result. Call it before the router touches the
 * URL (main.tsx); later callers (drawing sync) reuse the same promise.
 */
export function startLineIdentity(liffId = DEFAULT_LIFF_ID, now: () => number = Date.now): Promise<LineIdentity | null> {
  pending ??= getLineIdentity(liffId, now);
  return pending;
}

/** Resolves to the LINE identity, or null outside LINE / when not logged in / on any LIFF error. */
async function getLineIdentity(liffId: string, now: () => number): Promise<LineIdentity | null> {
  if (!liffId || !isLineInAppBrowser()) return null;
  const storage = attemptStore;
  if (!claimLoginAttempt(storage, now())) {
    console.warn('LINE login skipped after repeated attempts; drawings remain in memory only');
    return null;
  }
  try {
    const liff = await loadSdk();
    await liff.init({ liffId });
    initializedSdk = liff;
    if (!liff.isLoggedIn()) return null;
    clearLoginAttempts(storage);
    return identityFromSdk(liff, now);
  } catch (error) {
    console.warn('LINE login unavailable; drawings remain in memory only', error);
    return null;
  }
}

function identityFromSdk(liff: LiffSdk, now: () => number): LineIdentity | null {
  if (!liff.isLoggedIn()) return null;
  const userId = liff.getDecodedIDToken()?.sub;
  if (!userId) return null;
  return {
    userId,
    idToken() {
      const token = liff.getIDToken(), decoded = liff.getDecodedIDToken();
      // A retained callback must never start writing a different LINE account's token.
      if (!token || decoded?.sub !== userId || typeof decoded.exp !== 'number' || !Number.isFinite(decoded.exp) || decoded.exp * 1000 - EXPIRY_MARGIN_MS < now()) return null;
      return token;
    },
  };
}

/** Re-read the SDK's current identity, including account switches, without replaying OAuth init. */
export async function refreshLineIdentity(): Promise<LineIdentity | null> {
  if (!isLineInAppBrowser()) return null;
  if (initializedSdk) return identityFromSdk(initializedSdk, Date.now);
  return startLineIdentity(import.meta.env?.VITE_LIFF_ID || DEFAULT_LIFF_ID);
}

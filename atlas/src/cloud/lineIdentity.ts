/**
 * LINE identity for account-scoped storage. Ordinary external-browser visitors never load LIFF.
 * External browsers / installed PWAs opt in with the login button; an OAuth return is initialized
 * before the router changes its URL. Neither route parameters nor decoded claims grant storage:
 * access.get must independently verify the SDK's ID token on the server.
 */
export const DEFAULT_LIFF_ID = '2010330411-6JhrotT9';
const LIFF_SDK_URL = 'https://static.line-scdn.net/liff/edge/2/sdk.js';
const LOGIN_RETURN_PARAM = 'atlasLineLogin';
/** Treat a token that expires within this window as already expired. */
const EXPIRY_MARGIN_MS = 60_000;
const SDK_LOAD_TIMEOUT_MS = 15_000;
const EXPLICIT_INIT_TIMEOUT_MS = 15_000;
/** Initialization never invokes login automatically, including after an unsuccessful callback. */
const MAX_ATTEMPTS = 2;
const GUARD_WINDOW_MS = 120_000;
const GUARD_KEY = 'atlas-liff-attempts';
// No application login state or guest actions are written to browser storage.
const attemptMemory = new Map<string, string>();
const attemptStore = {
  getItem: (key: string) => attemptMemory.get(key) ?? null,
  setItem: (key: string, value: string) => { attemptMemory.set(key, value); },
  removeItem: (key: string) => { attemptMemory.delete(key); },
} as Storage;
let initializedSdk: LiffSdk | null = null;
let loadingSdk: Promise<LiffSdk> | null = null;
let pending: Promise<LineIdentity | null> | null = null;
let signedOut = false;
// Recovery can opt out even inside LINE. Keep that choice in RAM after route normalization.
let guestOnly = new URLSearchParams(globalThis.location?.search ?? '').get('atlasGuest') === '1';
let identityGeneration = 0;
let loginError: string | null = null;

interface LiffSdk {
  init(config: { liffId: string; withLoginOnExternalBrowser: false }): Promise<void>;
  isLoggedIn(): boolean;
  isInClient?(): boolean;
  login(config: { redirectUri: string }): void;
  logout(): void;
  getIDToken(): string | null;
  getDecodedIDToken(): { sub?: string; exp?: number } | null;
}

export interface LineIdentity {
  /** LINE user ID (`U…`), used only to key local sync metadata. */
  userId: string;
  /** Current SDK ID token, or null after logout, account change or expiry. */
  idToken(): string | null;
}

export function isLineInAppBrowser(userAgent = globalThis.navigator?.userAgent ?? ''): boolean {
  return /\bLine\/\d/i.test(userAgent);
}

function isLiffClient(liff: LiffSdk): boolean {
  return liff.isInClient?.() ?? isLineInAppBrowser();
}

/** The SDK owns code/PKCE validation. These markers only preserve its handoff before routing. */
export function hasLineLoginCallback(search = globalThis.location?.search ?? ''): boolean {
  const params = new URLSearchParams(search);
  return (params.has('code') || params.has('error')) &&
    (params.get(LOGIN_RETURN_PARAM) === '1' || params.has('liffClientId') ||
      params.has('liffRedirectUri') || params.has('liffOAuth2Error'));
}

/** LIFF primary redirects can carry state/credentials before an OAuth code exists. */
function hasLiffLaunchParameters(): boolean {
  const params = new URLSearchParams(globalThis.location?.search ?? '');
  const fragment = new URLSearchParams((globalThis.location?.hash ?? '').replace(/^#/, ''));
  return params.has('liff.state') || params.has('liff.referrer') ||
    (fragment.has('access_token') && fragment.has('context_token'));
}

/** main.tsx must await startLineIdentity fully for these launches before rendering the router. */
export function needsLineIdentityBeforeRouting(): boolean {
  return !guestOnly && (isLineInAppBrowser() || hasLineLoginCallback() || hasLiffLaunchParameters());
}

export function lineLoginError(): string | null { return loginError; }

function loadSdk(): Promise<LiffSdk> {
  const existing = (globalThis as { liff?: LiffSdk }).liff;
  if (existing) return Promise.resolve(existing);
  if (loadingSdk) return loadingSdk;
  loadingSdk = new Promise<LiffSdk>((resolve, reject) => {
    const script = document.createElement('script');
    const fail = () => { script.remove(); reject(new Error('LINE 登入服務未能載入，請稍後重試。')); };
    const timer = setTimeout(fail, SDK_LOAD_TIMEOUT_MS);
    script.src = LIFF_SDK_URL;
    script.async = true;
    script.onload = () => {
      clearTimeout(timer);
      const sdk = (globalThis as { liff?: LiffSdk }).liff;
      if (sdk) resolve(sdk);
      else fail();
    };
    script.onerror = () => { clearTimeout(timer); fail(); };
    document.head.appendChild(script);
  }).catch((error: unknown) => { loadingSdk = null; throw error; });
  return loadingSdk;
}

/** Recent attempt timestamps; false when another attempt now could repeatedly initialize LIFF. */
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
    return false;
  }
  return true;
}

function clearLoginAttempts() { attemptMemory.delete(GUARD_KEY); }

/** Clear only consumed SDK credentials, after init; leave unrelated anchors intact. */
function clearConsumedLiffFragment() {
  const fragment = new URLSearchParams((globalThis.location?.hash ?? '').replace(/^#/, ''));
  if (!fragment.has('access_token') && !fragment.has('context_token')) return;
  const url = new URL(globalThis.location.href);
  for (const key of ['access_token', 'context_token', 'id_token', 'feature_token', 'token_type', 'expires_in']) fragment.delete(key);
  url.hash = fragment.toString();
  globalThis.history?.replaceState(globalThis.history.state, '', url.href);
}

class LineInitializationTimeout extends Error {
  constructor() { super('LINE 登入連線逾時，請確認網路後再按登入重試。'); }
}

async function waitForExplicitInitialization(initialization: Promise<LineIdentity | null>) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      initialization,
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new LineInitializationTimeout()), EXPLICIT_INIT_TIMEOUT_MS); }),
    ]);
  } finally { clearTimeout(timer); }
}

function initializeIdentity(liffId: string, now: () => number): Promise<LineIdentity | null> {
  pending ??= (async () => {
    if (!liffId || !claimLoginAttempt(attemptStore, now())) throw new Error('LINE 登入暫停，請稍後再試。');
    const oauthReturn = hasLineLoginCallback();
    const liff = await loadSdk();
    // LINE requires init on each OAuth return, before history.replaceState or other URL changes.
    // Do not time out init and then render the router: the SDK may still be reading the callback.
    await liff.init({ liffId, withLoginOnExternalBrowser: false });
    clearConsumedLiffFragment();
    initializedSdk = liff;
    loginError = null;
    const identity = identityFromSdk(liff, now);
    if (identity?.idToken()) clearLoginAttempts();
    else if (oauthReturn) loginError = 'LINE 登入回傳未完成驗證，請再按一次「使用 LINE 登入」。';
    return identity;
  })();
  return pending;
}

/**
 * Start before routing inside LINE, on a LIFF primary handoff or a recognized OAuth return. There is no automatic
 * external-browser login or remembered application opt-in; a later fresh PWA launch shows the
 * login button again. Tapping it can restore the SDK session, followed by a fresh server grant.
 */
export async function startLineIdentity(liffId = DEFAULT_LIFF_ID, now: () => number = Date.now): Promise<LineIdentity | null> {
  if (signedOut || guestOnly) return null;
  if (initializedSdk) return identityFromSdk(initializedSdk, now);
  if (!needsLineIdentityBeforeRouting()) return null;
  try { return await initializeIdentity(liffId, now); }
  catch {
    loginError = '無法完成 LINE 登入，目前操作僅暫存；可按登入重試。';
    return null;
  }
}

function identityFromSdk(liff: LiffSdk, now: () => number): LineIdentity | null {
  if (signedOut || !liff.isLoggedIn()) return null;
  const userId = liff.getDecodedIDToken()?.sub;
  if (!userId) return null;
  const generation = identityGeneration;
  return {
    userId,
    idToken() {
      if (signedOut || generation !== identityGeneration || !liff.isLoggedIn()) return null;
      const token = liff.getIDToken(), decoded = liff.getDecodedIDToken();
      if (!token || decoded?.sub !== userId || typeof decoded.exp !== 'number' || !Number.isFinite(decoded.exp) || decoded.exp * 1000 - EXPIRY_MARGIN_MS <= now()) return null;
      return token;
    },
  };
}

/** Only called by the explicit login button, whose nearby copy discloses LINE's login storage. */
export async function loginLineIdentity(liffId = import.meta.env?.VITE_LIFF_ID || DEFAULT_LIFF_ID): Promise<'authenticated' | 'redirecting'> {
  guestOnly = false;
  signedOut = false;
  loginError = null;
  if (!initializedSdk) {
    // An explicit retry may reattempt a failed initialization, within the in-memory attempt limit.
    if (pending) {
      try { await waitForExplicitInitialization(pending); }
      catch (error) {
        // Keep an unresolved SDK init shared. A timed-out click must never resume
        // later and silently navigate, or start a second overlapping SDK init.
        if (error instanceof LineInitializationTimeout) throw error;
        pending = null;
      }
    }
    if (!initializedSdk) await waitForExplicitInitialization(initializeIdentity(liffId, Date.now));
  }
  const liff = initializedSdk;
  if (!liff) throw new Error('LINE 登入服務尚未就緒，請稍後重試。');
  if (identityFromSdk(liff, Date.now)?.idToken()) return 'authenticated';
  // The LINE app owns LIFF-client authentication; SDK login/logout only support external browsers.
  if (isLiffClient(liff)) throw new Error('LINE 登入已過期，請關閉此頁並從 LINE 重新開啟 Atlas。');
  // A stale SDK session must not be reused just because isLoggedIn() still returns true.
  identityGeneration++;
  if (liff.isLoggedIn()) liff.logout();
  const callback = new URL(globalThis.location.href);
  for (const key of [...callback.searchParams.keys()]) {
    if (key.startsWith('liff.') || ['code', 'state', 'error', 'error_description', 'liffClientId', 'liffRedirectUri', 'atlasGuest'].includes(key)) callback.searchParams.delete(key);
  }
  callback.hash = '';
  callback.searchParams.set(LOGIN_RETURN_PARAM, '1');
  liff.login({ redirectUri: callback.href });
  return 'redirecting';
}

/** Sign out of Atlas, and clear SDK authentication where supported. Saved workspaces stay intact. */
export function logoutLineIdentity(): void {
  signedOut = true;
  identityGeneration++;
  loginError = null;
  clearLoginAttempts();
  if (initializedSdk?.isLoggedIn() && !isLiffClient(initializedSdk)) initializedSdk.logout();
}

/** Re-read account switches and expiry without replaying OAuth init or automatic login. */
export async function refreshLineIdentity(): Promise<LineIdentity | null> {
  if (signedOut || guestOnly) return null;
  if (initializedSdk) return identityFromSdk(initializedSdk, Date.now);
  return startLineIdentity(import.meta.env?.VITE_LIFF_ID || DEFAULT_LIFF_ID);
}

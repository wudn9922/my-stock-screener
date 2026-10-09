/**
 * LINE identity for cloud drawing sync. Only inside the LINE app (opened from the report LIFF): the LIFF
 * SDK is loaded on demand there and nowhere else, so ordinary browsers keep local-only storage and
 * never download the SDK.
 */
export const DEFAULT_LIFF_ID = '2010330411-6JhrotT9';
const LIFF_SDK_URL = 'https://static.line-scdn.net/liff/edge/2/sdk.js';
/** Treat a token that expires within this window as already expired. */
const EXPIRY_MARGIN_MS = 60_000;

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

/** Resolves to the LINE identity, or null outside LINE / when not logged in / on any LIFF error. */
export async function getLineIdentity(liffId = DEFAULT_LIFF_ID, now: () => number = Date.now): Promise<LineIdentity | null> {
  if (!liffId || !isLineInAppBrowser()) return null;
  try {
    const liff = await loadSdk();
    await liff.init({ liffId });
    if (!liff.isLoggedIn()) return null;
    const decoded = liff.getDecodedIDToken();
    if (!decoded?.sub) return null;
    return {
      userId: decoded.sub,
      idToken() {
        const token = liff.getIDToken();
        const exp = liff.getDecodedIDToken()?.exp;
        if (!token || (exp && exp * 1000 - EXPIRY_MARGIN_MS < now())) return null;
        return token;
      },
    };
  } catch (error) {
    console.warn('LINE login unavailable; drawings stay on this device', error);
    return null;
  }
}

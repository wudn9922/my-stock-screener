/**
 * The one gate in front of the Supabase `market-chart` function (EdgeYahooProvider, LiveQuoteFeed and
 * everything built on them). Signed-out visitors never reach the function: charts fall back to the
 * static GitHub data and anything else shows 「登入 LINE 後可查看」. Signed-in requests carry the
 * login token in `X-Atlas-Token` (re-read per request, so sign-in/sign-out applies without reload).
 * A 401 counts as signed out for that token; a 429 stops every request until `retryAfterSeconds`
 * passes, or for the rest of the session for the daily caps. A 503 AUTH_UNAVAILABLE /
 * QUOTA_UNAVAILABLE is temporary: requests pause for `retryAfterSeconds`, then resume.
 * The new 401/429/503 bodies are `{ ok:false, code, error, retryAfterSeconds }`; older market-chart
 * errors keep `{ error: { code, message } }` and are left to the caller (e.g. 503 `rate_limited`).
 */
import { accessState, currentIdentity, initializeAccess, refreshAccess } from '../auth/access';

export const MARKET_TOKEN_HEADER = 'X-Atlas-Token';
export const MARKET_LOGIN_MESSAGE = '登入 LINE 後可查看';
const DEFAULT_QUOTA_MESSAGE = '查詢太頻繁，請稍後再試';
const DEFAULT_PAUSE_MESSAGE = '行情服務暫時無法確認登入或額度，請稍後再試';
const DAILY_CODES = new Set(['SITE_DAILY', 'USER_DAILY']);
/** Temporary 503s from the function's login/quota checks (not signed out). */
const PAUSE_CODES = new Set(['AUTH_UNAVAILABLE', 'QUOTA_UNAVAILABLE']);
const DEFAULT_RETRY_SECONDS = 60;

/** No request was (or will be) sent: the visitor must sign in with LINE first. */
export class MarketLoginRequiredError extends Error {
  constructor(message = MARKET_LOGIN_MESSAGE) {
    super(message);
    this.name = 'MarketLoginRequiredError';
  }
}

/**
 * The function refused for quota (429) or its checks are temporarily unavailable (503); no request is
 * sent until `until` (epoch ms; Infinity: for the rest of this session).
 */
export class MarketQuotaError extends Error {
  constructor(message: string, readonly code: string, readonly until: number) {
    super(message);
    this.name = 'MarketQuotaError';
  }
}

export const isMarketLoginRequired = (error: unknown): error is MarketLoginRequiredError =>
  error instanceof MarketLoginRequiredError;
export const isMarketQuotaError = (error: unknown): error is MarketQuotaError => error instanceof MarketQuotaError;

export type MarketAccessStatus = 'pending' | 'signed-out' | 'ready' | 'quota';
export interface MarketAccessSnapshot {
  status: MarketAccessStatus;
  /** The server's text for `quota`. */
  message?: string;
  code?: string;
  /** Epoch ms when requests resume (`quota` only; Infinity for the daily caps). */
  until?: number;
}

export interface MarketTokenSource {
  /** The current login token, or null when signed out. Called before every request. */
  token(): string | null;
  /** Resolves once the first account check finished (so a remembered login is not mistaken for a guest). */
  ready(): Promise<unknown>;
  /** True once `ready` has resolved. */
  settled(): boolean;
  /** Account changes (sign-in, sign-out, expiry). */
  subscribe(listener: () => void): () => void;
  /** After a 401: re-verify the account (a revoked remembered device is then forgotten). */
  unauthorized?(): void;
}

/** The site's LINE / remembered-device login (src/auth/access.ts). */
export const accountTokenSource: MarketTokenSource = {
  token: () => currentIdentity()?.idToken() ?? null,
  ready: () => initializeAccess(),
  settled: () => accessState.getSnapshot().settled,
  subscribe: (listener) => accessState.subscribe(listener),
  unauthorized: () => { void refreshAccess().catch(() => undefined); },
};

/** Explicit test/script access: always signed in with this token. */
export function staticTokenSource(token: string | null): MarketTokenSource {
  return { token: () => token, ready: () => Promise.resolve(), settled: () => true, subscribe: () => () => undefined };
}

interface QuotaBlock { code: string; message: string; until: number }

export class MarketProxyAccess {
  private source: MarketTokenSource;
  private readonly now: () => number;
  private readonly listeners = new Set<() => void>();
  private unsubscribeSource: (() => void) | null = null;
  /** The token the function answered 401 for; it counts as signed out until a different token appears. */
  private rejected: string | null = null;
  private quota: QuotaBlock | null = null;
  private quotaTimer: ReturnType<typeof setTimeout> | null = null;
  private snapshot: MarketAccessSnapshot = { status: 'pending' };
  private snapshotKey = '';

  constructor(source: MarketTokenSource = accountTokenSource, now: () => number = Date.now) {
    this.source = source;
    this.now = now;
  }

  /** Replace the login source (tests, Node scripts); clears any 401/429 state. */
  useTokenSource(source: MarketTokenSource): void {
    this.source = source;
    this.rejected = null;
    this.clearQuota();
    if (this.unsubscribeSource) {
      this.unsubscribeSource();
      this.unsubscribeSource = source.subscribe(() => this.notify());
    }
    this.notify();
  }

  /** The token for the next request, or null when signed out (or the function rejected this token). */
  token(): string | null {
    const token = this.source.token();
    return token && token !== this.rejected ? token : null;
  }

  private activeQuota(): QuotaBlock | null {
    if (this.quota && this.quota.until <= this.now()) this.clearQuota();
    return this.quota;
  }

  private clearQuota() {
    this.quota = null;
    if (this.quotaTimer) clearTimeout(this.quotaTimer);
    this.quotaTimer = null;
  }

  /** Synchronous view for React (stable object while nothing changed). */
  getSnapshot = (): MarketAccessSnapshot => {
    const quota = this.activeQuota();
    const token = this.token();
    const next: MarketAccessSnapshot = !this.source.settled() ? { status: 'pending' } : !token
      ? { status: 'signed-out' }
      : quota ? { status: 'quota', message: quota.message, code: quota.code, until: quota.until } : { status: 'ready' };
    const key = `${next.status}|${next.message ?? ''}|${next.code ?? ''}|${next.until ?? ''}`;
    if (key !== this.snapshotKey) {
      this.snapshotKey = key;
      this.snapshot = next;
    }
    return this.snapshot;
  };

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    this.unsubscribeSource ??= this.source.subscribe(() => this.notify());
    return () => {
      this.listeners.delete(listener);
      if (!this.listeners.size && this.unsubscribeSource) {
        this.unsubscribeSource();
        this.unsubscribeSource = null;
      }
    };
  };

  private notify() {
    for (const listener of [...this.listeners]) listener();
  }

  /**
   * Waits for the first account check, then returns the token to send, or throws without any request:
   * MarketLoginRequiredError when signed out, MarketQuotaError while a 429 is in force.
   */
  async authorize(): Promise<string> {
    if (!this.source.settled()) await this.source.ready().catch(() => undefined);
    return this.authorizeNow();
  }

  /** `authorize` without waiting (the account check must already have finished). */
  private authorizeNow(): string {
    const token = this.token();
    if (!token) throw new MarketLoginRequiredError();
    const quota = this.activeQuota();
    if (quota) throw new MarketQuotaError(quota.message, quota.code, quota.until);
    return token;
  }

  /** A request may be sent now (signed in, settled, no quota block). Never sends anything. */
  canRequest(): boolean {
    return this.source.settled() && !!this.token() && !this.activeQuota();
  }

  /**
   * The only way to call market-chart: adds `X-Atlas-Token`, and turns 401/429 into the shared
   * signed-out / quota state (thrown as MarketLoginRequiredError / MarketQuotaError).
   */
  async fetch(fetcher: typeof fetch, url: string, init: RequestInit = {}): Promise<Response> {
    const token = this.source.settled() ? this.authorizeNow() : await this.authorize();
    const headers = new Headers(init.headers);
    headers.set(MARKET_TOKEN_HEADER, token);
    const response = await fetcher(url, { ...init, headers });
    if (response.status === 401) {
      this.rejected = token;
      this.notify();
      this.source.unauthorized?.();
      throw new MarketLoginRequiredError();
    }
    if (response.status === 429) throw this.blockForQuota(response, topLevelCode(await readJson(response)));
    if (response.status === 503) {
      // Only the new top-level-code shape pauses; an older `{ error: { code } }` 503 stays the caller's.
      const body = topLevelCode(await readJson(response.clone()));
      if (body && PAUSE_CODES.has(body.code)) throw this.blockForQuota(response, body);
    }
    return response;
  }

  private blockForQuota(response: Response, body: NewErrorBody | null): MarketQuotaError {
    const payload: Partial<NewErrorBody> = body ?? {};
    const code = payload.code ?? 'RATE_LIMITED';
    const message = typeof payload.error === 'string' && payload.error.trim() ? payload.error.trim()
      : PAUSE_CODES.has(code) ? DEFAULT_PAUSE_MESSAGE : DEFAULT_QUOTA_MESSAGE;
    const header = Number(response.headers.get('Retry-After'));
    const seconds = typeof payload.retryAfterSeconds === 'number' && Number.isFinite(payload.retryAfterSeconds) && payload.retryAfterSeconds > 0
      ? payload.retryAfterSeconds : Number.isFinite(header) && header > 0 ? header : DEFAULT_RETRY_SECONDS;
    // Daily caps stay closed for the rest of this session; the window cap reopens after Retry-After.
    const until = DAILY_CODES.has(code) ? Number.POSITIVE_INFINITY : this.now() + seconds * 1000;
    this.clearQuota();
    this.quota = { code, message, until };
    if (Number.isFinite(until)) {
      this.quotaTimer = setTimeout(() => {
        this.quotaTimer = null;
        if (this.quota && this.quota.until <= this.now()) this.quota = null;
        this.notify();
      }, Math.max(0, until - this.now()) + 50);
    }
    this.notify();
    return new MarketQuotaError(message, code, until);
  }
}

interface NewErrorBody { code: string; error?: unknown; retryAfterSeconds?: unknown }

async function readJson(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { return null; }
}

/** `{ ok:false, code, error, retryAfterSeconds }` (the new shape), or null for anything else. */
function topLevelCode(body: unknown): NewErrorBody | null {
  return body && typeof body === 'object' && typeof (body as { code?: unknown }).code === 'string' ? body as NewErrorBody : null;
}

/** The site-wide gate shared by every market-chart caller. */
export const marketProxyAccess = new MarketProxyAccess();

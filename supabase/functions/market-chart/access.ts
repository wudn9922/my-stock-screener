// Sign-in and usage quota for market-chart. Pure logic with injected dependencies so it is unit-tested
// offline (atlas/tests/market-chart-access.test.ts); index.ts wires in LINE, device sessions and the
// public.atlas_market_chart_quota RPC (supabase/manual/atlas_market_chart_quota.sql).
import type { StorageMode } from '../_shared/storageAccess.ts';

// ─── Usage limits: edit only this block, then redeploy market-chart (the SQL takes them as arguments). ───
export const QUOTA_LIMITS = {
  /** Signed-in users whose storage_mode is none or device: requests per fixed 10-minute window / per day. */
  user: { window: 200, daily: 3_000 },
  /** Cloud-authorised users (storage_mode cloud). */
  cloud: { window: 600, daily: 15_000 },
  /** Site-wide requests per day; above it only cloud users and the admin are served. */
  siteDaily: 50_000,
} as const;
// The admin (adminLineId()) is never counted or refused. "Day" is the Asia/Taipei calendar day and the
// window is a fixed 10-minute bucket; both are computed by the SQL function.
// ──────────────────────────────────────────────────────────────────────────────────────────────────────────

/** A verified token (and its storage mode) is reused for this long per function instance. */
export const AUTH_CACHE_TTL_MS = 5 * 60_000;
export const AUTH_CACHE_MAX_ENTRIES = 5_000;
export const QUOTA_RPC = 'atlas_market_chart_quota';
export const ATLAS_TOKEN_HEADER = 'X-Atlas-Token';
/** LINE ID tokens (JWT) and device tokens (atlasdev.…): printable ASCII without spaces. */
const TOKEN_PATTERN = /^[\x21-\x7e]{16,4096}$/;

export type QuotaCode = 'USER_WINDOW' | 'USER_DAILY' | 'SITE_DAILY';
export type QuotaDecision =
  | { allowed: true }
  | { allowed: false; code: QuotaCode; retryAfterSeconds: number };
export interface QuotaRequest {
  userId: string;
  windowLimit: number;
  dailyLimit: number;
  siteLimit: number;
  /** Cloud users keep being served after the site cap; they still count toward the site total. */
  siteExempt: boolean;
}
export interface GateDependencies {
  /** LINE ID token or device token → LINE user ID. Throw `{ status: 401 }` (or anything) to reject, status ≥ 500 when unavailable. */
  resolveUser(token: string): Promise<string>;
  getStorageMode(userId: string): Promise<StorageMode>;
  consumeQuota(request: QuotaRequest): Promise<QuotaDecision>;
  adminId: string;
  now?: () => number;
  limits?: typeof QUOTA_LIMITS;
  warn?: (message: string) => void;
}
export interface GateFailure {
  ok: false;
  status: 401 | 429 | 503;
  body: { ok: false; code: string; error: string; retryAfterSeconds?: number };
  retryAfterSeconds?: number;
}
export type GateOutcome =
  | { ok: true; userId: string; storageMode: StorageMode; isAdmin: boolean }
  | GateFailure;

export const GATE_MESSAGES = {
  LOGIN_REQUIRED: '登入 LINE 後可查看即時行情與更多股票。',
  SITE_DAILY: '今日免費額度已用完，明天再試',
  USER_DAILY: '今日免費額度已用完，明天再試',
  USER_WINDOW: '查詢太頻繁，請稍後再試（每 10 分鐘上限）',
  AUTH_UNAVAILABLE: '登入驗證服務暫時無法使用，請稍後再試。',
  QUOTA_UNAVAILABLE: '使用額度服務暫時無法使用，請稍後再試。',
} as const;
const UNAVAILABLE_RETRY_SECONDS = 60;

function failure(status: GateFailure['status'], code: keyof typeof GATE_MESSAGES, retryAfterSeconds?: number): GateFailure {
  const body: GateFailure['body'] = { ok: false, code, error: GATE_MESSAGES[code] };
  if (retryAfterSeconds !== undefined) body.retryAfterSeconds = retryAfterSeconds;
  return { ok: false, status, body, retryAfterSeconds };
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

const isUnavailable = (error: unknown) =>
  typeof (error as { status?: unknown } | null)?.status === 'number' && (error as { status: number }).status >= 500;

interface CachedIdentity {
  userId: string;
  expiresAt: number;
  /** Unset when the storage lookup failed; retried on the next request. */
  storageMode?: StorageMode;
}

export function createMarketChartGate(deps: GateDependencies) {
  const now = deps.now ?? Date.now;
  const limits = deps.limits ?? QUOTA_LIMITS;
  const warn = deps.warn ?? ((message: string) => console.warn(message));
  // Keyed by SHA-256 of the token (raw tokens are never kept). Map order = insertion order, so the
  // first key is the oldest entry when the size bound is reached.
  const cache = new Map<string, CachedIdentity>();
  const pending = new Map<string, Promise<CachedIdentity>>();

  function remember(key: string, entry: CachedIdentity) {
    cache.delete(key);
    while (cache.size >= AUTH_CACHE_MAX_ENTRIES) cache.delete(cache.keys().next().value!);
    cache.set(key, entry);
  }

  async function identify(token: string): Promise<CachedIdentity> {
    const key = await sha256Hex(token);
    const cached = cache.get(key);
    if (cached && cached.expiresAt > now()) return cached;
    if (cached) cache.delete(key);
    let inflight = pending.get(key);
    if (!inflight) {
      inflight = deps.resolveUser(token).then((userId) => {
        const entry: CachedIdentity = { userId, expiresAt: now() + AUTH_CACHE_TTL_MS };
        remember(key, entry);
        return entry;
      }).finally(() => pending.delete(key));
      pending.set(key, inflight);
    }
    return inflight;
  }

  async function storageModeOf(entry: CachedIdentity): Promise<StorageMode> {
    if (entry.storageMode) return entry.storageMode;
    try {
      entry.storageMode = await deps.getStorageMode(entry.userId);
      return entry.storageMode;
    } catch {
      // Unknown grant: apply the ordinary signed-in limits for this request and look it up again next time.
      warn('market-chart: storage mode lookup failed; using signed-in limits');
      return 'none';
    }
  }

  return {
    async check(rawToken: string | null | undefined): Promise<GateOutcome> {
      const token = rawToken?.trim() ?? '';
      if (!TOKEN_PATTERN.test(token)) return failure(401, 'LOGIN_REQUIRED');
      let identity: CachedIdentity;
      try {
        identity = await identify(token);
      } catch (error) {
        if (isUnavailable(error)) {
          warn('market-chart: sign-in verification unavailable');
          return failure(503, 'AUTH_UNAVAILABLE', UNAVAILABLE_RETRY_SECONDS);
        }
        return failure(401, 'LOGIN_REQUIRED');
      }
      const userId = identity.userId;
      if (userId === deps.adminId) return { ok: true, userId, storageMode: 'cloud', isAdmin: true };
      const storageMode = await storageModeOf(identity);
      const cloud = storageMode === 'cloud';
      const tier = cloud ? limits.cloud : limits.user;
      let decision: QuotaDecision;
      try {
        decision = await deps.consumeQuota({
          userId, windowLimit: tier.window, dailyLimit: tier.daily, siteLimit: limits.siteDaily, siteExempt: cloud,
        });
      } catch {
        warn('market-chart: quota RPC failed');
        // Fail closed for ordinary users; cloud-authorised users keep working.
        if (!cloud) return failure(503, 'QUOTA_UNAVAILABLE', UNAVAILABLE_RETRY_SECONDS);
        return { ok: true, userId, storageMode, isAdmin: false };
      }
      if (!decision.allowed) return failure(429, decision.code, decision.retryAfterSeconds);
      return { ok: true, userId, storageMode, isAdmin: false };
    },
    /** For tests/diagnostics only. */
    cacheSize: () => cache.size,
  };
}

interface RpcDatabase {
  rpc(name: string, parameters: Record<string, unknown>): PromiseLike<{ data: unknown; error: { code?: string } | null }>;
}

const QUOTA_CODES: readonly QuotaCode[] = ['USER_WINDOW', 'USER_DAILY', 'SITE_DAILY'];

/** Validates the RPC's jsonb answer; anything unexpected is an error (the caller fails closed). */
export function parseQuotaDecision(data: unknown): QuotaDecision {
  const row = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  if (row.allowed === true) return { allowed: true };
  if (row.allowed === false && QUOTA_CODES.includes(row.code as QuotaCode)) {
    const seconds = Number(row.retryAfterSeconds);
    return {
      allowed: false,
      code: row.code as QuotaCode,
      retryAfterSeconds: Number.isFinite(seconds) && seconds > 0 ? Math.min(Math.ceil(seconds), 86_400) : 60,
    };
  }
  throw new Error('Unexpected market-chart quota answer');
}

/** One atomic RPC per request: increments the user's window/day buckets and the site's day bucket. */
export async function consumeQuotaRpc(database: RpcDatabase, request: QuotaRequest): Promise<QuotaDecision> {
  const { data, error } = await database.rpc(QUOTA_RPC, {
    user_id: request.userId,
    window_limit: request.windowLimit,
    daily_limit: request.dailyLimit,
    site_limit: request.siteLimit,
    site_exempt: request.siteExempt,
  });
  if (error) throw new Error(`market-chart quota RPC failed ${error.code ?? ''}`.trim());
  return parseQuotaDecision(data);
}

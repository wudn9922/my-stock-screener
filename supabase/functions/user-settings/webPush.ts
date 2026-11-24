/**
 * Atlas phone notifications through standard Web Push (no Firebase project or third-party account).
 *
 * Browser actions (verified LINE identity + cloud grant, checked by index.ts):
 *   push.subscribe   { subscription: { endpoint, keys: { p256dh, auth } }, label }
 *   push.unsubscribe { id } | { endpoint }
 *   push.status      { endpoint? }      this account's devices, never endpoints or keys
 *   push.test        send a test notification to the caller's own devices
 * Server action (breakout_alert on Cloud Run, Authorization: Bearer <service-role key>):
 *   push.notify      { line_user_id, title, body, url, tag }
 *
 * Subscriptions live in atlas_private.web_push_subscriptions behind public.atlas_web_push
 * (service_role only). The VAPID private key exists only as a Supabase secret.
 */
import { encryptPushPayload, base64UrlDecode, base64UrlEncode, importP256Public, loadVapidKeys, vapidAuthorization, vapidJwt, type VapidKeys } from './webPushCrypto.ts';

export const MAX_SUBSCRIPTIONS_PER_USER = 10;
/** Push services accept 4 KB records; stay well below after encryption overhead. */
export const MAX_PAYLOAD_BYTES = 3072;
export const ATLAS_ORIGIN = 'https://wudn9922.github.io';
export const ATLAS_PATH = '/my-stock-screener/atlas/';
const LINE_USER = /^U[0-9a-f]{32}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TAG = /^[A-Za-z0-9._:^=-]{1,64}$/;
const SEND_TIMEOUT_MS = 8000;
const encoder = new TextEncoder();
const VAPID_REFRESH_SECONDS = 11 * 3600; // The signed token expires after 12 hours.
const vapidTokens = new Map<string, { refreshAt: number; jwt: Promise<string> }>();
const APPLE_FAILURE_REASONS = new Set([
  'BadTtl', 'BadUrgency', 'BadWebPushRequest', 'BadWebPushTopic', 'VapidPkHashMismatch',
  'IdleTimeout', 'BadAuthorizationHeader', 'BadJwtToken', 'BadVapidPublicKey', 'BadPath',
  'MethodNotAllowed', 'PayloadTooLarge', 'TooManyRequests',
]);

/** Browser push services only: no other host ever receives a server-side request (SSRF guard). */
const PUSH_HOSTS = new Set(['fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com']);
const PUSH_HOST_SUFFIXES = ['.push.apple.com', '.notify.windows.com', '.push.services.mozilla.com'];
const SERVICE_NAMES: [RegExp, string][] = [
  [/\.apple\.com$/, 'Apple'],
  [/^fcm\.googleapis\.com$/, 'Google'],
  [/\.mozilla\.com$/, 'Mozilla'],
  [/\.windows\.com$/, 'Microsoft'],
];

export class WebPushError extends Error {
  constructor(readonly status: number, message: string, readonly code = 'push') {
    super(message);
  }
}

export interface PushRpc {
  rpc(name: string, parameters: Record<string, unknown>): PromiseLike<{ data: unknown; error: { code?: string } | null }>;
}
export interface PushDeps {
  db: PushRpc;
  env: (name: string) => string | undefined;
  fetch: typeof fetch;
  now?: () => number;
}
export interface PushMessage { title: string; body: string; url: string; tag: string }
interface Target { id: string; endpoint: string; p256dh: string; auth: string }
interface DeviceRow { id: string; label: string | null; host: string; created_at: string; last_success_at: string | null; failure_count: number; current: boolean }

export function validateEndpoint(value: unknown): string {
  if (typeof value !== 'string' || value.length > 1024) throw new WebPushError(400, '通知訂閱格式不正確。');
  let url: URL;
  try { url = new URL(value); } catch { throw new WebPushError(400, '通知訂閱格式不正確。'); }
  const host = url.hostname.toLowerCase();
  if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')
    || !(PUSH_HOSTS.has(host) || PUSH_HOST_SUFFIXES.some((suffix) => host.endsWith(suffix) && host.length > suffix.length)))
    throw new WebPushError(400, '不支援這個瀏覽器的推播服務。', 'unsupported_service');
  return url.href;
}

function key(value: unknown, length: number) {
  try {
    const bytes = base64UrlDecode(String(value ?? ''));
    if (bytes.length === length) return bytes;
  } catch { /* reported below */ }
  throw new WebPushError(400, '通知訂閱金鑰不正確。');
}

export async function validateSubscription(value: unknown) {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  const keys = raw.keys && typeof raw.keys === 'object' ? raw.keys as Record<string, unknown> : {};
  const endpoint = validateEndpoint(raw.endpoint);
  const p256dh = key(keys.p256dh, 65);
  const auth = key(keys.auth, 16);
  // A point that is not on the curve can never be encrypted to; refuse it up front.
  try { await importP256Public(p256dh); } catch { throw new WebPushError(400, '通知訂閱金鑰不正確。'); }
  return { endpoint, p256dh: base64UrlEncode(p256dh), auth: base64UrlEncode(auth) };
}

export function deviceLabel(value: unknown) {
  const label = typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, 60) : '';
  return label || '未命名裝置';
}

/** Notifications open Atlas only: never another site, even if the alert payload says so. */
export function validateAtlasUrl(value: unknown): string {
  if (typeof value !== 'string' || value.length > 512) throw new WebPushError(400, '通知連結必須是 Atlas 網址。');
  let url: URL;
  try { url = new URL(value); } catch { throw new WebPushError(400, '通知連結必須是 Atlas 網址。'); }
  if (url.origin !== ATLAS_ORIGIN || !url.pathname.startsWith(ATLAS_PATH) || url.username || url.password)
    throw new WebPushError(400, '通知連結必須是 Atlas 網址。');
  return url.href;
}

const clip = (value: unknown, max: number) => String(value ?? '').replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, '').trim().slice(0, max);

export function buildPayload(message: Partial<PushMessage>, nowMs: number) {
  const title = clip(message.title, 100);
  const body = clip(message.body, 400);
  if (!title) throw new WebPushError(400, '通知標題不可空白。');
  const url = validateAtlasUrl(message.url);
  const tag = String(message.tag ?? '');
  if (!TAG.test(tag)) throw new WebPushError(400, '通知分類格式不正確。');
  const payload = encoder.encode(JSON.stringify({ title, body, url, tag, ts: nowMs }));
  if (payload.length > MAX_PAYLOAD_BYTES) throw new WebPushError(413, '通知內容過長。');
  return { payload, tag };
}

/** RFC 8030 Topic: up to 32 URL-safe characters; a newer message with the same topic replaces a queued one. */
export function topicFor(tag: string) {
  return tag.replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 32);
}

async function digest(text: string) {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(text)));
}

/** The server keys Supabase gives every Edge Function; no extra shared secret is needed. */
export function serverKeys(env: (name: string) => string | undefined) {
  const keys = [env('SUPABASE_SERVICE_ROLE_KEY') ?? ''];
  try {
    const parsed = JSON.parse(env('SUPABASE_SECRET_KEYS') ?? '{}');
    if (parsed && typeof parsed === 'object') for (const value of Object.values(parsed)) if (typeof value === 'string') keys.push(value);
  } catch { /* absent or not JSON: the legacy service-role key still works */ }
  return keys.filter((value) => value.length >= 20);
}

/** Constant-time comparison of SHA-256 digests, so timing reveals nothing about the key. */
export async function bearerMatches(header: string | null, secrets: string[]) {
  const match = /^Bearer\s+(\S+)$/i.exec((header ?? '').trim());
  if (!match || !secrets.length) return false;
  const given = await digest(match[1]);
  let matched = 0;
  for (const secret of secrets) {
    const expected = await digest(secret);
    let difference = 0;
    for (let index = 0; index < expected.length; index += 1) difference |= expected[index] ^ given[index];
    matched |= Number(difference === 0);
  }
  return matched === 1;
}

async function rpc(db: PushRpc, operation: string, lineUserId: string, payload: Record<string, unknown> = {}) {
  const { data, error } = await db.rpc('atlas_web_push', { operation, account: lineUserId, payload });
  if (error) {
    if (error.code === '42883' || error.code === 'PGRST202' || error.code === '42P01')
      throw new WebPushError(503, '手機通知資料表尚未部署，請先套用 web_push_subscriptions.sql。', 'schema_required');
    console.error('Web push RPC failed', error.code);
    throw new WebPushError(503, '手機通知服務暫時無法使用，請稍後再試。');
  }
  return data;
}

async function vapid(env: PushDeps['env']): Promise<VapidKeys> {
  try {
    return await loadVapidKeys(env('WEB_PUSH_VAPID_PUBLIC_KEY'), env('WEB_PUSH_VAPID_PRIVATE_KEY'), env('WEB_PUSH_SUBJECT'));
  } catch {
    throw new WebPushError(503, '手機通知尚未設定伺服器金鑰。', 'not_configured');
  }
}

/** Apple requires JWT reuse for at least an hour. Share the in-flight signature across sends. */
async function pushAuthorization(keys: VapidKeys, endpoint: URL, nowSeconds: number) {
  const cacheKey = JSON.stringify([endpoint.origin, keys.publicKeyText, keys.subject]);
  let entry = vapidTokens.get(cacheKey);
  if (!entry || entry.refreshAt <= nowSeconds) {
    for (const [key, token] of vapidTokens) {
      if (token.refreshAt <= nowSeconds) vapidTokens.delete(key);
    }
    const jwt = vapidJwt(keys, endpoint.href, nowSeconds).catch((error: unknown) => {
      if (vapidTokens.get(cacheKey)?.jwt === jwt) vapidTokens.delete(cacheKey);
      throw error;
    });
    entry = { refreshAt: nowSeconds + VAPID_REFRESH_SECONDS, jwt };
    vapidTokens.set(cacheKey, entry);
  }
  return vapidAuthorization(await entry.jwt, keys);
}

type Outcome = 'ok' | 'gone' | 'failed';
async function deliver(deps: PushDeps, keys: VapidKeys, target: Target, payload: Uint8Array, tag: string): Promise<Outcome> {
  let endpoint: URL;
  try { endpoint = new URL(validateEndpoint(target.endpoint)); } catch { return 'gone'; }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SEND_TIMEOUT_MS);
  try {
    const body = await encryptPushPayload(payload, base64UrlDecode(target.p256dh), base64UrlDecode(target.auth));
    const response = await deps.fetch(endpoint, {
      method: 'POST',
      redirect: 'manual',
      signal: controller.signal,
      headers: {
        Authorization: await pushAuthorization(keys, endpoint, Math.floor((deps.now?.() ?? Date.now()) / 1000)),
        'Content-Encoding': 'aes128gcm',
        'Content-Type': 'application/octet-stream',
        TTL: '3600',
        Urgency: 'high',
        Topic: topicFor(tag),
      },
      body: body.slice().buffer as ArrayBuffer,
    });
    // 404/410: the browser unsubscribed or the subscription expired; delete it.
    if (response.ok || response.status === 404 || response.status === 410) {
      await response.body?.cancel().catch(() => {});
      return response.ok ? 'ok' : 'gone';
    }
    if (endpoint.hostname.endsWith('.push.apple.com')) {
      const error = await response.json().catch(() => null) as { reason?: unknown } | null;
      if (typeof error?.reason === 'string' && APPLE_FAILURE_REASONS.has(error.reason)) {
        console.error('Web push delivery failed', response.status, error.reason);
        return 'failed';
      }
    } else {
      await response.body?.cancel().catch(() => {});
    }
    console.error('Web push delivery failed', response.status);
    return 'failed';
  } catch (error) {
    // Never log endpoints (they are bearer capabilities) or payloads.
    console.error('Web push delivery error', error instanceof Error ? error.name : 'unknown');
    return 'failed';
  } finally {
    clearTimeout(timer);
  }
}

/** Send one message to every device of one account and record the outcome per device. */
export async function sendToUser(deps: PushDeps, lineUserId: string, message: Partial<PushMessage>) {
  const { payload, tag } = buildPayload(message, deps.now?.() ?? Date.now());
  const keys = await vapid(deps.env);
  const targets = await rpc(deps.db, 'targets', lineUserId) as Target[] | null;
  const list = Array.isArray(targets) ? targets.slice(0, MAX_SUBSCRIPTIONS_PER_USER) : [];
  if (!list.length) return { devices: 0, sent: 0, removed: 0, failed: 0 };
  const outcomes = await Promise.all(list.map((target) => deliver(deps, keys, target, payload, tag)));
  const ids = (kind: Outcome) => list.filter((_, index) => outcomes[index] === kind).map((target) => target.id);
  const report = { ok: ids('ok'), gone: ids('gone'), failed: ids('failed') };
  try { await rpc(deps.db, 'report', lineUserId, report); } catch { /* delivery already happened; bookkeeping retries next time */ }
  return { devices: list.length, sent: report.ok.length, removed: report.gone.length, failed: report.failed.length };
}

const serviceName = (host: string) => SERVICE_NAMES.find(([pattern]) => pattern.test(host))?.[1] ?? '瀏覽器';

export const TEST_MESSAGE: PushMessage = {
  title: 'Atlas 測試通知',
  body: '點一下這則通知，會開啟 2330.TW 台積電的日線圖。',
  url: `${ATLAS_ORIGIN}${ATLAS_PATH}?page=chart&tf=1D&symbol=2330.TW`,
  tag: 'atlas-test',
};

/** Browser actions; the caller has verified the LINE identity and the cloud grant. */
export async function handlePushAction(action: string, lineUserId: string, body: Record<string, unknown>, deps: PushDeps) {
  if (!LINE_USER.test(lineUserId)) throw new WebPushError(401, 'LINE 帳號驗證失敗。');
  if (action === 'push.subscribe') {
    const subscription = await validateSubscription(body.subscription);
    const result = await rpc(deps.db, 'subscribe', lineUserId, { ...subscription, label: deviceLabel(body.label), limit: MAX_SUBSCRIPTIONS_PER_USER }) as { id?: string; count?: number } | null;
    return { id: result?.id ?? null, devices: result?.count ?? null };
  }
  if (action === 'push.unsubscribe') {
    const payload = typeof body.id === 'string' && UUID.test(body.id) ? { id: body.id } : { endpoint: validateEndpoint(body.endpoint) };
    const result = await rpc(deps.db, 'unsubscribe', lineUserId, payload) as { removed?: number } | null;
    return { removed: result?.removed ?? 0 };
  }
  if (action === 'push.status') {
    let endpoint: string | null = null;
    try { endpoint = body.endpoint == null ? null : validateEndpoint(body.endpoint); } catch { endpoint = null; }
    const rows = await rpc(deps.db, 'list', lineUserId, { endpoint }) as DeviceRow[] | null;
    const devices = (Array.isArray(rows) ? rows : []).map((row) => ({
      id: row.id, label: row.label || '未命名裝置', service: serviceName(String(row.host ?? '')), createdAt: row.created_at,
      lastSuccessAt: row.last_success_at, failureCount: Number(row.failure_count) || 0, current: row.current === true,
    }));
    return { devices, limit: MAX_SUBSCRIPTIONS_PER_USER, configured: !!(deps.env('WEB_PUSH_VAPID_PUBLIC_KEY') && deps.env('WEB_PUSH_VAPID_PRIVATE_KEY') && deps.env('WEB_PUSH_SUBJECT')) };
  }
  if (action === 'push.test') return sendToUser(deps, lineUserId, TEST_MESSAGE);
  throw new WebPushError(400, '未知的通知操作。');
}

const acceptedServerKeys = new Map<string, number>();
const ACCEPTED_KEY_TTL_MS = 10 * 60_000;
const PROBE_ACCOUNT = 'U' + '0'.repeat(32);

/**
 * Fallback when the bearer is not byte-identical to this runtime's service-role key (e.g. Cloud Run holds the
 * other key format, legacy JWT vs sb_secret_): ask PostgREST itself whether the key may run the service_role-only
 * RPC. anon/authenticated keys are refused there, so only a real server key passes. Accepted keys are cached by
 * digest for 10 minutes; nothing about the key is logged.
 */
export async function serverKeyAccepted(header: string | null, deps: PushDeps) {
  const match = /^Bearer\s+(\S+)$/i.exec((header ?? '').trim());
  const base = deps.env('SUPABASE_URL');
  if (!match || match[1].length < 20 || !base) return false;
  const key = match[1];
  const id = Array.from(await digest(key), (byte) => byte.toString(16).padStart(2, '0')).join('');
  const now = (deps.now ?? Date.now)();
  if ((acceptedServerKeys.get(id) ?? 0) > now) return true;
  try {
    const headers: Record<string, string> = { apikey: key, 'Content-Type': 'application/json' };
    if (!key.startsWith('sb_secret_')) headers.Authorization = `Bearer ${key}`;
    const response = await deps.fetch(new URL('/rest/v1/rpc/atlas_web_push', base), {
      method: 'POST', headers, redirect: 'error', signal: AbortSignal.timeout(5000),
      body: JSON.stringify({ operation: 'list', account: PROBE_ACCOUNT, payload: {} }),
    });
    await response.body?.cancel();
    if (!response.ok) return false;
  } catch {
    return false;
  }
  acceptedServerKeys.set(id, now + ACCEPTED_KEY_TTL_MS);
  return true;
}

/** Cloud Run alert sender. Authorization is a Supabase server key: constant-time match, else verified by PostgREST. */
export async function handlePushNotify(
  request: Request, body: Record<string, unknown>, deps: PushDeps,
  requireCloud: (lineUserId: string) => Promise<unknown>,
) {
  const authorization = request.headers.get('authorization');
  if (!(await bearerMatches(authorization, serverKeys(deps.env))) && !(await serverKeyAccepted(authorization, deps)))
    throw new WebPushError(401, 'Unauthorized', 'unauthorized');
  const lineUserId = String(body.line_user_id ?? '');
  if (!LINE_USER.test(lineUserId)) throw new WebPushError(400, 'Invalid line_user_id');
  // Same rule as every private table: a revoked cloud grant also stops notifications.
  try { await requireCloud(lineUserId); }
  catch { return { devices: 0, sent: 0, removed: 0, failed: 0, skipped: 'storage' }; }
  return sendToUser(deps, lineUserId, {
    title: body.title as string, body: body.body as string, url: body.url as string, tag: body.tag as string,
  });
}

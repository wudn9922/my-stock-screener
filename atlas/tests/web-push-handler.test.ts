import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transformSync } from 'esbuild';
import { describe, expect, it } from 'vitest';
import * as policy from '../../supabase/functions/_shared/storageAccess';
import * as deviceSession from '../../supabase/functions/_shared/deviceSession';
import * as lineHandoff from '../../supabase/functions/_shared/lineLoginHandoff';
import * as webPush from '../../supabase/functions/user-settings/webPush';
import { base64UrlDecode, base64UrlEncode } from '../../supabase/functions/user-settings/webPushCrypto';

const OWNER = policy.DEFAULT_ADMIN_LINE_ID;
const A = 'U11111111111111111111111111111111';
const SERVICE_KEY = 'eyJhbGciOiJIUzI1NiJ9.service-role-offline-test-key.signature';
// RFC 8291 Appendix A keys: valid P-256 subscription keys, public test data.
const VAPID = { pub: 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8', priv: 'yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw' };
const UA = { p256dh: 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4', auth: 'BTBZMqHH6r4Tts7J_aSIgg' };
const CHART = 'https://wudn9022.github.io/my-stock-screener/atlas/?page=chart&tf=1D&symbol=2330.TW';
type Row = Record<string, unknown>;
const ENV: Record<string, string> = {
  SUPABASE_URL: 'https://offline.invalid', SUPABASE_SERVICE_ROLE_KEY: SERVICE_KEY, ADMIN_LINE_USER_ID: OWNER, LINE_LOGIN_CHANNEL_ID: '2000000000',
  WEB_PUSH_VAPID_PUBLIC_KEY: VAPID.pub, WEB_PUSH_VAPID_PRIVATE_KEY: VAPID.priv, WEB_PUSH_SUBJECT: 'mailto:alerts@example.invalid',
};

/** An offline stand-in for public.atlas_web_push plus the storage-policy table and the push services. */
function fixture(options: { grants?: Row[]; status?: number | ((endpoint: string) => number); env?: Record<string, string> } = {}) {
  const env = { ...ENV, ...options.env };
  const devices: Row[] = [];
  const rpcCalls: { operation: string; account: string; payload: Row }[] = [];
  const sent: { url: string; headers: Record<string, string>; body: Uint8Array }[] = [];
  const logs: unknown[][] = [];
  const db = {
    from: () => {
      const query = {
        select: () => query, eq: () => query, maybeSingle: () => query,
        then: (resolve: (value: unknown) => unknown) => resolve({ data: (options.grants ?? [])[0] ?? null, error: null }),
      };
      return query;
    },
    rpc: async (name: string, parameters: Row) => {
      expect(name).toBe('atlas_web_push');
      const { operation, account, payload } = parameters as { operation: string; account: string; payload: Row };
      rpcCalls.push({ operation, account, payload });
      if (operation === 'subscribe') {
        devices.push({ id: `00000000-0000-4000-8000-00000000000${devices.length}`, account, ...payload });
        return { data: { id: devices.at(-1)!.id, count: devices.length }, error: null };
      }
      if (operation === 'targets') return { data: devices.filter(d => d.account === account), error: null };
      if (operation === 'list') return { data: devices.map(d => ({ id: d.id, label: d.label, host: new URL(String(d.endpoint)).host, created_at: 'x', last_success_at: null, failure_count: 0, current: d.endpoint === payload.endpoint })), error: null };
      return { data: {}, error: null };
    },
  };
  const pushFetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    sent.push({ url, headers: init!.headers as Record<string, string>, body: new Uint8Array(init!.body as ArrayBuffer) });
    const status = typeof options.status === 'function' ? options.status(url) : options.status ?? 201;
    return new Response(null, { status });
  };
  const deps: webPush.PushDeps = { db, env: (name) => env[name], fetch: pushFetch, now: () => 1_800_000_000_000 };
  let serve!: (request: Request) => Promise<Response>;
  const source = readFileSync(new URL('../../supabase/functions/user-settings/index.ts', import.meta.url), 'utf8').replace(/^import .*;\r?\n/gm, '');
  runInNewContext(transformSync(source, { loader: 'ts', target: 'es2022', format: 'cjs' }).code, {
    ...policy, ...deviceSession, ...lineHandoff, ...webPush,
    Deno: { env: { get: (name: string) => env[name] }, serve: (callback: typeof serve) => { serve = callback; } },
    createClient: () => db,
    // LINE verification for browser actions; every other fetch is a push service.
    fetch: async (input: RequestInfo | URL, init?: RequestInit) => String(input).startsWith('https://api.line.me/')
      ? new Response(JSON.stringify({ sub: A }), { status: 200 })
      : pushFetch(input, init),
    Request, Response, URLSearchParams, TextEncoder, crypto, btoa, atob, setTimeout, clearTimeout, AbortController,
    console: { error: (...args: unknown[]) => { logs.push(args); } },
  });
  const request = async (body: Row, headers: Record<string, string> = {}) => {
    const response = await serve(new Request('https://offline.invalid/', { method: 'POST', headers, body: JSON.stringify(body) }));
    return { status: response.status, body: await response.json() };
  };
  return { devices, rpcCalls, sent, logs, deps, request };
}

const subscription = (endpoint = 'https://fcm.googleapis.com/fcm/send/abc') => ({ endpoint, keys: UA });
const notify = (overrides: Row = {}) => ({
  action: 'push.notify', line_user_id: A, title: '🚨 2330.TW 台積電 向上突破 MA20', body: '現價 1,050.00｜量比 1.35', url: CHART, tag: '2330.TW:breakout_up:20', ...overrides,
});
const bearer = (key = SERVICE_KEY) => ({ authorization: `Bearer ${key}` });

describe('web push endpoint and payload validation', () => {
  it('accepts only https endpoints of the browsers push services', () => {
    for (const endpoint of ['https://fcm.googleapis.com/fcm/send/x', 'https://web.push.apple.com/QA', 'https://api.push.apple.com/3/x',
      'https://updates.push.services.mozilla.com/wpush/v2/x', 'https://wns2-par02p.notify.windows.com/w/?token=x'])
      expect(webPush.validateEndpoint(endpoint)).toBe(new URL(endpoint).href);
    for (const endpoint of ['http://fcm.googleapis.com/fcm/send/x', 'https://fcm.googleapis.com:8443/x', 'https://user:pw@fcm.googleapis.com/x',
      'https://evil.example/fcm.googleapis.com', 'https://push.apple.com.evil.example/x', 'https://notify.windows.com/x', 'https://169.254.169.254/latest',
      'https://localhost/x', `https://fcm.googleapis.com/${'x'.repeat(1100)}`, 42])
      expect(() => webPush.validateEndpoint(endpoint)).toThrow();
  });

  it('normalizes subscription keys and refuses wrong lengths or points off the curve', async () => {
    const padded = { endpoint: 'https://fcm.googleapis.com/fcm/send/abc', keys: { p256dh: `${UA.p256dh}=`, auth: `${UA.auth}==` } };
    expect(await webPush.validateSubscription(padded)).toEqual({ endpoint: padded.endpoint, p256dh: UA.p256dh, auth: UA.auth });
    const offCurve = base64UrlEncode(new Uint8Array([4, ...new Uint8Array(64).fill(1)]));
    for (const keys of [{ ...UA, auth: 'AAAA' }, { ...UA, p256dh: UA.p256dh.slice(4) }, { ...UA, p256dh: offCurve }])
      await expect(webPush.validateSubscription({ endpoint: padded.endpoint, keys })).rejects.toThrow('金鑰');
  });

  it('opens only Atlas URLs and keeps the encrypted payload under the size limit', () => {
    expect(webPush.validateAtlasUrl(CHART)).toBe(CHART);
    for (const url of ['https://evil.example/my-stock-screener/atlas/', 'https://wudn9022.github.io/other/', 'http://wudn9022.github.io/my-stock-screener/atlas/', 'javascript:alert(1)'])
      expect(() => webPush.validateAtlasUrl(url)).toThrow('Atlas');
    const { payload } = webPush.buildPayload({ title: '標'.repeat(500), body: '長'.repeat(5000), url: CHART, tag: 'x' }, 1);
    const decoded = JSON.parse(new TextDecoder().decode(payload));
    expect(decoded.title).toHaveLength(100);
    expect(decoded.body).toHaveLength(400);
    expect(payload.length).toBeLessThanOrEqual(webPush.MAX_PAYLOAD_BYTES);
    expect(() => webPush.buildPayload({ title: 'x', url: CHART, tag: 'bad tag!' }, 1)).toThrow();
    expect(webPush.topicFor('2330.TW:breakout_up:20')).toBe('2330_TW_breakout_up_20');
    expect(webPush.topicFor('x'.repeat(80))).toHaveLength(32);
  });
});

describe('push.notify server-to-server authentication', () => {
  it('rejects a missing, wrong or publishable key, in constant time, without reading subscriptions', async () => {
    const offline = fixture({ grants: [{ line_user_id: A, storage_mode: 'cloud', label: '', updated_at: '2026-11-01T00:00:00Z' }] });
    for (const headers of [{}, bearer('sb_publishable_offline_test_key_value'), bearer(`${SERVICE_KEY}x`), { authorization: SERVICE_KEY }])
      expect((await offline.request(notify(), headers)).status).toBe(401);
    expect(offline.rpcCalls).toHaveLength(0);
    expect(await webPush.bearerMatches(`Bearer ${SERVICE_KEY}`, [SERVICE_KEY])).toBe(true);
    expect(await webPush.bearerMatches(`Bearer ${SERVICE_KEY}`, [])).toBe(false);
  });

  it('also accepts a new-style secret key listed in SUPABASE_SECRET_KEYS', () => {
    const keys = webPush.serverKeys((name) => ({ SUPABASE_SERVICE_ROLE_KEY: SERVICE_KEY, SUPABASE_SECRET_KEYS: JSON.stringify({ default: 'sb_secret_offline_test_value_1234' }) })[name]);
    expect(keys).toEqual([SERVICE_KEY, 'sb_secret_offline_test_value_1234']);
    expect(webPush.serverKeys(() => undefined)).toEqual([]);
  });

  it('sends one encrypted VAPID message per device, deletes 404/410 devices and counts other failures', async () => {
    const offline = fixture({
      grants: [{ line_user_id: A, storage_mode: 'cloud', label: '', updated_at: '2026-11-01T00:00:00Z' }],
      status: (url) => url.includes('gone') ? 410 : url.includes('broken') ? 500 : 201,
    });
    for (const endpoint of ['https://fcm.googleapis.com/fcm/send/ok', 'https://web.push.apple.com/gone', 'https://updates.push.services.mozilla.com/broken'])
      offline.devices.push({ id: endpoint, account: A, endpoint, ...UA });
    const result = await offline.request(notify(), bearer());
    expect(result).toEqual({ status: 200, body: { ok: true, data: { devices: 3, sent: 1, removed: 1, failed: 1 } } });
    expect(offline.rpcCalls.at(-1)).toEqual({ operation: 'report', account: A, payload: {
      ok: ['https://fcm.googleapis.com/fcm/send/ok'], gone: ['https://web.push.apple.com/gone'], failed: ['https://updates.push.services.mozilla.com/broken'] } });
    const message = offline.sent.find(entry => entry.url.includes('googleapis'))!;
    expect(message.headers['Content-Encoding']).toBe('aes128gcm');
    expect(message.headers.TTL).toBe('3600');
    expect(message.headers.Topic).toBe('2330_TW_breakout_up_20');
    expect(message.headers.Authorization).toMatch(new RegExp(`^vapid t=[\\w-]+\\.[\\w-]+\\.[\\w-]+, k=${VAPID.pub}$`));
    const claims = JSON.parse(new TextDecoder().decode(base64UrlDecode(message.headers.Authorization.split('.')[1])));
    expect(claims.aud).toBe('https://fcm.googleapis.com');
    expect(message.body.length).toBeLessThan(4096);
    // Logs carry only status codes: no endpoint, key or alert content.
    expect(JSON.stringify(offline.logs)).not.toMatch(/googleapis|mozilla|apple|2330|vapid/);
  });

  it('refuses non-Atlas links and skips accounts without a cloud grant', async () => {
    const granted = fixture({ grants: [{ line_user_id: A, storage_mode: 'cloud', label: '', updated_at: '2026-11-01T00:00:00Z' }] });
    expect((await granted.request(notify({ url: 'https://evil.example/' }), bearer())).status).toBe(400);
    const revoked = fixture({ grants: [{ line_user_id: A, storage_mode: 'device', label: '', updated_at: '2026-11-01T00:00:00Z' }] });
    revoked.devices.push({ id: 'd', account: A, endpoint: 'https://fcm.googleapis.com/fcm/send/ok', ...UA });
    expect((await revoked.request(notify(), bearer())).body.data).toMatchObject({ sent: 0, skipped: 'storage' });
    expect(revoked.sent).toHaveLength(0);
  });

  it('answers 503 without sending when the VAPID secrets are missing', async () => {
    const offline = fixture({ grants: [{ line_user_id: A, storage_mode: 'cloud', label: '', updated_at: '2026-11-01T00:00:00Z' }], env: { WEB_PUSH_VAPID_PRIVATE_KEY: '' } });
    expect(await offline.request(notify(), bearer())).toMatchObject({ status: 503, body: { code: 'not_configured' } });
  });
});

describe('browser push actions', () => {
  const cloud = { grants: [{ line_user_id: A, storage_mode: 'cloud', label: '', updated_at: '2026-11-01T00:00:00Z' }] };
  it('subscribes with the device cap, lists devices without endpoints and sends a test to the callers own devices', async () => {
    const offline = fixture(cloud);
    const subscribed = await offline.request({ action: 'push.subscribe', idToken: 'line-token', subscription: subscription(), label: 'iPhone\u0000 主畫面 App' });
    expect(subscribed).toMatchObject({ status: 200, body: { ok: true, data: { devices: 1 } } });
    expect(offline.rpcCalls[0]).toMatchObject({ operation: 'subscribe', account: A, payload: { limit: webPush.MAX_SUBSCRIPTIONS_PER_USER, label: 'iPhone 主畫面 App' } });
    const status = await offline.request({ action: 'push.status', idToken: 'line-token', endpoint: subscription().endpoint });
    expect(status.body.data.devices).toEqual([expect.objectContaining({ service: 'Google', current: true, label: 'iPhone 主畫面 App' })]);
    expect(JSON.stringify(status.body)).not.toContain('fcm/send');
    expect(status.body.data.configured).toBe(true);
    const test = await offline.request({ action: 'push.test', idToken: 'line-token' });
    expect(test.body.data).toMatchObject({ devices: 1, sent: 1 });
  });

  it('requires a verified LINE identity with a cloud grant and validates the endpoint', async () => {
    expect((await fixture(cloud).request({ action: 'push.status' })).status).toBe(401);
    expect((await fixture({ grants: [] }).request({ action: 'push.status', idToken: 'line-token' })).status).toBe(403);
    const bad = await fixture(cloud).request({ action: 'push.subscribe', idToken: 'line-token', subscription: subscription('https://169.254.169.254/x') });
    expect(bad).toMatchObject({ status: 400, body: { code: 'unsupported_service' } });
  });
});

import { describe, expect, it, vi } from 'vitest';
import {
  AUTH_CACHE_MAX_ENTRIES,
  AUTH_CACHE_TTL_MS,
  consumeQuotaRpc,
  createMarketChartGate,
  type GateDependencies,
  parseQuotaDecision,
  QUOTA_LIMITS,
  QUOTA_RPC,
} from '../../supabase/functions/market-chart/access';
import { DeviceSessionError } from '../../supabase/functions/_shared/deviceSession';
import { LINE_VERIFY_URL, verifyLineIdTokenWith } from '../../supabase/functions/_shared/lineIdToken';
import { DEFAULT_ADMIN_LINE_ID } from '../../supabase/functions/_shared/storageAccess';

const USER = 'U11111111111111111111111111111111';
const TOKEN = 'line-token-user-0001';

function gate(overrides: Partial<GateDependencies> = {}) {
  let now = 1_800_000_000_000;
  const resolveUser = vi.fn(async (token: string) => {
    if (token.startsWith('line-token-')) return USER;
    throw new DeviceSessionError(401, 'rejected');
  });
  const getStorageMode = vi.fn(async () => 'none' as const);
  const consumeQuota = vi.fn(async () => ({ allowed: true as const }));
  const instance = createMarketChartGate({
    resolveUser, getStorageMode, consumeQuota, adminId: DEFAULT_ADMIN_LINE_ID, now: () => now, warn: () => {}, ...overrides,
  });
  return { instance, resolveUser, getStorageMode, consumeQuota, advance: (ms: number) => { now += ms; } };
}

describe('market-chart access gate', () => {
  it('keeps the documented limits in one block', () => {
    expect(QUOTA_LIMITS).toEqual({ user: { window: 200, daily: 3000 }, cloud: { window: 600, daily: 15000 }, siteDaily: 50000 });
  });

  it('rejects missing and malformed tokens without asking LINE', async () => {
    const g = gate();
    for (const token of [null, undefined, '', '   ', 'short', 'has spaces in the token value', 'x'.repeat(4097)]) {
      expect(await g.instance.check(token)).toMatchObject({ ok: false, status: 401, body: { code: 'LOGIN_REQUIRED' } });
    }
    expect(g.resolveUser).not.toHaveBeenCalled();
  });

  it('caches a verified token (and its storage mode) for 5 minutes, then verifies again', async () => {
    const g = gate();
    expect(await g.instance.check(TOKEN)).toEqual({ ok: true, userId: USER, storageMode: 'none', isAdmin: false });
    expect(AUTH_CACHE_TTL_MS).toBe(5 * 60_000);
    g.advance(5 * 60_000 - 1);
    await g.instance.check(TOKEN);
    expect(g.resolveUser).toHaveBeenCalledTimes(1);
    expect(g.getStorageMode).toHaveBeenCalledTimes(1);
    expect(g.consumeQuota).toHaveBeenCalledTimes(2);
    g.advance(1);
    await g.instance.check(TOKEN);
    expect(g.resolveUser).toHaveBeenCalledTimes(2);
    expect(g.getStorageMode).toHaveBeenCalledTimes(2);
  });

  it('shares one verification between concurrent requests and never caches a rejection', async () => {
    const g = gate();
    await Promise.all([g.instance.check(TOKEN), g.instance.check(TOKEN), g.instance.check(TOKEN)]);
    expect(g.resolveUser).toHaveBeenCalledTimes(1);
    expect((await g.instance.check('rejected-token-value')).ok).toBe(false);
    expect((await g.instance.check('rejected-token-value')).ok).toBe(false);
    expect(g.resolveUser).toHaveBeenCalledTimes(3);
  });

  it('bounds the cache by evicting the oldest entries', async () => {
    expect(AUTH_CACHE_MAX_ENTRIES).toBe(5000);
    const g = gate();
    for (let i = 0; i <= AUTH_CACHE_MAX_ENTRIES; i++) await g.instance.check(`line-token-${i.toString().padStart(8, '0')}`);
    expect(g.instance.cacheSize()).toBe(AUTH_CACHE_MAX_ENTRIES);
    const calls = g.resolveUser.mock.calls.length;
    await g.instance.check(`line-token-${'0'.repeat(8)}`); // the oldest one was evicted
    expect(g.resolveUser).toHaveBeenCalledTimes(calls + 1);
    await g.instance.check(`line-token-${String(AUTH_CACHE_MAX_ENTRIES).padStart(8, '0')}`); // the newest is still cached
    expect(g.resolveUser).toHaveBeenCalledTimes(calls + 1);
  });

  it('reports an unavailable verifier as 503, not as signed out', async () => {
    const g = gate({ resolveUser: async () => { throw new DeviceSessionError(503, 'down'); } });
    expect(await g.instance.check(TOKEN)).toMatchObject({ ok: false, status: 503, body: { code: 'AUTH_UNAVAILABLE' }, retryAfterSeconds: 60 });
  });

  it('passes the tier limits to the quota and maps refusals to 429', async () => {
    const consumeQuota = vi.fn(async () => ({ allowed: false as const, code: 'USER_WINDOW' as const, retryAfterSeconds: 42 }));
    const g = gate({ consumeQuota, getStorageMode: async () => 'cloud' });
    expect(await g.instance.check(TOKEN)).toEqual({
      ok: false, status: 429, retryAfterSeconds: 42,
      body: { ok: false, code: 'USER_WINDOW', error: '查詢太頻繁，請稍後再試（每 10 分鐘上限）', retryAfterSeconds: 42 },
    });
    expect(consumeQuota).toHaveBeenCalledWith({ userId: USER, windowLimit: 600, dailyLimit: 15000, siteLimit: 50000, siteExempt: true });
  });

  it('does not consult storage or quota for the admin', async () => {
    const g = gate({ resolveUser: async () => DEFAULT_ADMIN_LINE_ID });
    expect(await g.instance.check(TOKEN)).toEqual({ ok: true, userId: DEFAULT_ADMIN_LINE_ID, storageMode: 'cloud', isAdmin: true });
    expect(g.getStorageMode).not.toHaveBeenCalled();
    expect(g.consumeQuota).not.toHaveBeenCalled();
  });

  it('uses the signed-in limits when the storage lookup fails and retries the lookup next time', async () => {
    const getStorageMode = vi.fn().mockRejectedValueOnce(new Error('db down')).mockResolvedValue('cloud');
    const g = gate({ getStorageMode });
    expect(await g.instance.check(TOKEN)).toMatchObject({ ok: true, storageMode: 'none' });
    expect(g.consumeQuota).toHaveBeenLastCalledWith(expect.objectContaining({ windowLimit: 200, siteExempt: false }));
    expect(await g.instance.check(TOKEN)).toMatchObject({ ok: true, storageMode: 'cloud' });
    expect(getStorageMode).toHaveBeenCalledTimes(2);
  });

  it('fails closed on a quota error for ordinary users but serves cloud users', async () => {
    const consumeQuota = vi.fn(async () => { throw new Error('rpc down'); });
    expect(await gate({ consumeQuota }).instance.check(TOKEN)).toMatchObject({ ok: false, status: 503, body: { code: 'QUOTA_UNAVAILABLE' } });
    expect(await gate({ consumeQuota, getStorageMode: async () => 'cloud' }).instance.check(TOKEN)).toMatchObject({ ok: true, storageMode: 'cloud' });
  });
});

describe('market-chart quota RPC adapter', () => {
  it('sends one RPC with the SQL parameter names and validates the answer', async () => {
    const rpc = vi.fn(async () => ({ data: { allowed: false, code: 'SITE_DAILY', retryAfterSeconds: 3600.2, window: 1, daily: 1, site: 50000 }, error: null }));
    expect(await consumeQuotaRpc({ rpc }, { userId: USER, windowLimit: 200, dailyLimit: 3000, siteLimit: 50000, siteExempt: false }))
      .toEqual({ allowed: false, code: 'SITE_DAILY', retryAfterSeconds: 3601 });
    expect(rpc).toHaveBeenCalledWith(QUOTA_RPC, { user_id: USER, window_limit: 200, daily_limit: 3000, site_limit: 50000, site_exempt: false });
    await expect(consumeQuotaRpc({ rpc: async () => ({ data: null, error: { code: '42883' } }) }, { userId: USER, windowLimit: 1, dailyLimit: 1, siteLimit: 1, siteExempt: false })).rejects.toThrow();
  });

  it('rejects unexpected answers and clamps retry times', () => {
    expect(parseQuotaDecision({ allowed: true, window: 3 })).toEqual({ allowed: true });
    expect(parseQuotaDecision({ allowed: false, code: 'USER_DAILY' })).toEqual({ allowed: false, code: 'USER_DAILY', retryAfterSeconds: 60 });
    expect(parseQuotaDecision({ allowed: false, code: 'USER_DAILY', retryAfterSeconds: 1e9 })).toMatchObject({ retryAfterSeconds: 86400 });
    for (const bad of [null, 'yes', {}, { allowed: 'true' }, { allowed: false, code: 'OTHER' }]) expect(() => parseQuotaDecision(bad)).toThrow();
  });
});

describe('shared LINE ID token verification', () => {
  const ok = (body: unknown, status = 200) => vi.fn(async () => Response.json(body, { status })) as unknown as typeof fetch;

  it('posts the token and channel to LINE and returns the LINE user ID', async () => {
    const fetcher = ok({ sub: USER });
    expect(await verifyLineIdTokenWith(TOKEN, { channelId: '1234567890', fetcher })).toBe(USER);
    const [url, init] = (fetcher as unknown as ReturnType<typeof vi.fn>).mock.calls[0]!;
    expect(url).toBe(LINE_VERIFY_URL);
    expect(init.method).toBe('POST');
    expect(Object.fromEntries(new URLSearchParams(init.body))).toEqual({ id_token: TOKEN, client_id: '1234567890' });
  });

  it('maps rejections to 401 and outages or missing configuration to 503', async () => {
    const status = (promise: Promise<unknown>) => promise.then(() => 0, (error: DeviceSessionError) => error.status);
    expect(await status(verifyLineIdTokenWith(TOKEN, { channelId: '1', fetcher: ok({ error: 'invalid_request' }, 400) }))).toBe(401);
    expect(await status(verifyLineIdTokenWith(TOKEN, { channelId: '1', fetcher: ok({ sub: 'not-a-line-id' }) }))).toBe(401);
    expect(await status(verifyLineIdTokenWith(TOKEN, { channelId: '1', fetcher: ok({}, 502) }))).toBe(503);
    expect(await status(verifyLineIdTokenWith(TOKEN, { channelId: '1', fetcher: async () => { throw new TypeError('network'); } }))).toBe(503);
    expect(await status(verifyLineIdTokenWith(TOKEN, { channelId: undefined, fetcher: ok({ sub: USER }) }))).toBe(503);
  });
});

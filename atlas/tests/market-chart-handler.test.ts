import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transformSync } from 'esbuild';
import { describe, expect, it, vi } from 'vitest';
import * as validation from '../../supabase/functions/market-chart/validate';
import * as mis from '../../supabase/functions/market-chart/twse-mis';
import * as access from '../../supabase/functions/market-chart/access';
import * as deviceSession from '../../supabase/functions/_shared/deviceSession';
import * as lineIdToken from '../../supabase/functions/_shared/lineIdToken';
import * as storageAccess from '../../supabase/functions/_shared/storageAccess';
import { MIS_NOW, MIS_TODAY, misRows, misYahoo } from './fixtures/twse-mis';

const ADMIN = storageAccess.DEFAULT_ADMIN_LINE_ID;
const USER = 'U11111111111111111111111111111111';
const CLOUD = 'U22222222222222222222222222222222';
const DEVICE = 'U33333333333333333333333333333333';
/** Fake LINE ID tokens accepted by the fake LINE verify endpoint. */
const LINE_TOKENS: Record<string, string> = { 'line-token-user-0001': USER, 'line-token-cloud-001': CLOUD, 'line-token-admin-001': ADMIN };
const DEVICE_TOKEN = 'atlasdev.' + 'd'.repeat(43);
type QuotaLimits = access.QuotaLimits;

/**
 * In-memory stand-in for public.atlas_market_chart_quota with the same semantics as the SQL: count the
 * user window, user day and site day in order; a refused request is uncounted again.
 */
function fakeQuotaDatabase() {
  const counts = new Map<string, number>();
  let failing = false;
  const rpcCalls: Record<string, unknown>[] = [];
  const bump = (key: string, by: number) => { const next = (counts.get(key) ?? 0) + by; counts.set(key, next); return next; };
  const rpc = async (name: string, parameters: Record<string, unknown>) => {
    if (name === 'atlas_device_session') {
      return parameters.operation === 'use'
        ? { data: { status: 'active', userId: DEVICE }, error: null }
        : { data: null, error: { code: 'XX000' } };
    }
    if (name !== access.QUOTA_RPC) throw new Error('unexpected rpc ' + name);
    rpcCalls.push(parameters);
    if (failing) return { data: null, error: { code: '42883' } };
    const user = String(parameters.user_id);
    const touched: string[] = [];
    const step = (key: string, limit: number) => { touched.push(key); return bump(key, 1) > limit; };
    let code: string | null = null;
    if (step(`${user}:window`, Number(parameters.window_limit))) code = 'USER_WINDOW';
    else if (step(`${user}:day`, Number(parameters.daily_limit))) code = 'USER_DAILY';
    else if (step('site:day', Number(parameters.site_limit)) && !parameters.site_exempt) code = 'SITE_DAILY';
    if (!code) return { data: { allowed: true }, error: null };
    for (const key of touched) bump(key, -1);
    return { data: { allowed: false, code, retryAfterSeconds: code === 'USER_WINDOW' ? 120 : 3600 }, error: null };
  };
  return { rpc, rpcCalls, counts, fail: (value: boolean) => { failing = value; } };
}

function harness(options: { now?: number; fail?: string; fill?: boolean; limits?: QuotaLimits; env?: Record<string, string>; warrants?: unknown[] } = {}) {
  let now = options.now ?? MIS_NOW;
  let fail = options.fail;
  let serve!: (request: Request) => Promise<Response>;
  const calls: URL[] = [];
  const lineCalls: string[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = new URL(String(input)); calls.push(url);
    if (url.href === lineIdToken.LINE_VERIFY_URL) {
      const token = new URLSearchParams(String(init?.body)).get('id_token')!;
      lineCalls.push(token);
      return LINE_TOKENS[token] ? Response.json({ sub: LINE_TOKENS[token] }) : Response.json({ error: 'invalid_request' }, { status: 400 });
    }
    if (url.hostname === 'mis.twse.com.tw') {
      if (fail === 'network' || fail === 'timeout') throw new DOMException('fixture failure', 'AbortError');
      if (fail === 'http') return new Response('offline', { status: 503 });
      if (fail === 'json') return new Response('{');
      if (fail === 'shape') return Response.json({ msgArray: {} });
      if (fail === 'api') return Response.json({ rtcode: '5000', msgArray: misRows() });
      return Response.json({ msgArray: options.warrants ?? misRows().map(row => fail === 'stale' ? { ...row, d: '20261104' } : row) });
    }
    if (!validation.YAHOO_HOSTS.includes(url.hostname as typeof validation.YAHOO_HOSTS[number])) throw new Error('Unexpected network in offline fixture');
    const symbol = decodeURIComponent(url.pathname.split('/').at(-1)!);
    if (symbol === '^TWOII') return new Response('', { status: 404 });
    const raw = misYahoo(symbol, true);
    if (options.fill && symbol === '^TWII') {
      const r = raw.chart.result[0]!, q = r.indicators.quote[0]!;
      if (url.searchParams.get('interval') === '5m') {
        r.timestamp = [MIS_TODAY - 86400, MIS_TODAY - 86400 + 300];
        r.indicators.quote[0] = { open: [42900, 42920], high: [42940, 42960], low: [42890, 42910], close: [42920, 42948.91], volume: [100, 200] };
      } else for (const field of Object.values(q)) field[1] = null;
    }
    return Response.json(raw);
  };
  // Test dependency injection: every fetch, including the adapter's fetch, stays in this fixture.
  class BoundMis extends mis.TwseMisClient {
    constructor() { super({ fetcher, now: () => now, sleep: async (ms) => { now += ms; } }); }
  }
  class Clock extends Date { static now() { return now; } }
  const quota = fakeQuotaDatabase();
  const modes: Record<string, storageAccess.StorageMode> = { [CLOUD]: 'cloud', [DEVICE]: 'device' };
  const env: Record<string, string> = { SUPABASE_URL: 'https://db.offline.test', SUPABASE_SERVICE_ROLE_KEY: 'service-role-fixture', LINE_LOGIN_CHANNEL_ID: '1234567890', ...options.env };
  const source = readFileSync(new URL('../../supabase/functions/market-chart/index.ts', import.meta.url), 'utf8')
    .replace(/^import[\s\S]*?from "[^"]+";\r?\n/gm, '');
  runInNewContext(transformSync(source, { loader: 'ts', target: 'es2022', format: 'cjs' }).code, {
    ...validation, ...mis, TwseMisClient: BoundMis,
    ...deviceSession, ...storageAccess,
    ...access, createMarketChartGate: (deps: access.GateDependencies) => access.createMarketChartGate({ ...deps, limits: options.limits ?? deps.limits }),
    verifyLineIdTokenWith: (token: string, verify: lineIdToken.LineVerifyOptions) => lineIdToken.verifyLineIdTokenWith(token, { ...verify, fetcher }),
    createClient: () => ({ rpc: quota.rpc, from: () => { throw new Error('storage table is faked below'); } }),
    getStorageAccess: async (_db: unknown, userId: string) => ({ storageMode: modes[userId] ?? 'none' }),
    Deno: { env: { get: (name: string) => env[name] }, serve: (handler: typeof serve) => { serve = handler; } },
    fetch: fetcher, Date: Clock, Request, Response, URL, URLSearchParams, AbortSignal, structuredClone,
    console: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  });
  return { calls, lineCalls, quota, advance: (ms: number) => { now += ms; }, fail: (value: string) => { fail = value; },
    request: (query: string, token: string | null = 'line-token-user-0001', init: RequestInit = {}) => {
      const headers = new Headers(init.headers);
      if (token !== null) headers.set('X-Atlas-Token', token);
      return serve(new Request('https://offline.test/?' + query, { ...init, headers }));
    },
    misCalls: () => calls.filter(url => url.hostname === 'mis.twse.com.tw') };
}

describe('market-chart warrant book endpoint', () => {
  const close = Date.parse('2026-11-26T13:30:00+08:00');
  const row = { d: '20261126', t: '13:30:00', tlong: String(close), ex: 'tse', rch: '2454' };
  const captured = [
    { ...row, c: '066186', b: '0.6800_', g: '10_', a: '0.6900_', f: '10_' },
    { ...row, c: '04917T', b: '2.0700_', g: '10_', a: '-', f: '-', z: '-', v: '0' },
    { ...row, c: '714060', ex: 'otc', b: '0.4500_', g: '10_', a: '0.7000_', f: '10_', trade: { t: '09:23:39', z: '0.4400' } },
  ];

  it('uses the shared gate for login and quota before touching MIS', async () => {
    const h = harness({ now: close, warrants: captured, limits: { user: { window: 1, daily: 5 }, cloud: { window: 3, daily: 5 }, siteDaily: 10 } });
    const path = 'warrantQuotes=066186.TW';
    expect(await (await h.request(path, null)).json()).toMatchObject({ ok: false, code: 'LOGIN_REQUIRED' });
    expect(h.misCalls()).toHaveLength(0);
    expect((await h.request(path)).status).toBe(200);
    const limited = await h.request(path);
    expect(limited.status).toBe(429);
    expect(limited.headers.get('Retry-After')).toBe('120');
    expect(await limited.json()).toMatchObject({ ok: false, code: 'USER_WINDOW' });
    expect(h.misCalls()).toHaveLength(1);
  });

  it('validates warrant symbols and uses 20 by default with an explicit hard cap of 40', async () => {
    const h = harness({ now: close, warrants: [] });
    const codes = Array.from({ length: 41 }, (_, i) => `06${String(i).padStart(4, '0')}.TW`);
    for (const path of [
      'warrantQuotes=2330.TW', 'warrantQuotes=066186.TW&quotes=AAPL', 'warrantQuotes=066186.TW&limit=41',
      `warrantQuotes=${codes.slice(0, 21).join(',')}`, `warrantQuotes=${codes.join(',')}&limit=40`,
    ]) expect((await h.request(path)).status, path).toBe(400);
    expect(h.misCalls()).toHaveLength(0);
    const response = await h.request(`warrantQuotes=${codes.slice(0, 40).join(',')}&limit=40`);
    expect(response.status).toBe(200);
    expect(Object.keys((await response.json()).results)).toHaveLength(40);
    expect(h.misCalls()[0]!.searchParams.get('ex_ch')!.split('|')).toHaveLength(40);
  });

  it('uses the actual book timestamp and labels the recorded after-close snapshot as closed', async () => {
    const h = harness({ now: close + 41 * 60_000, warrants: captured });
    const response = await h.request('warrantQuotes=066186.tw,04917t.tw,714060.TWO,066186.TW');
    expect(response.headers.get('Cache-Control')).toBe('private, no-store');
    expect(await response.json()).toEqual({
      asOf: close / 1000 + 41 * 60, session: 'closed', source: 'twse-mis',
      results: {
        '066186.TW': { status: 'available', bid: 0.68, bidSize: 10, ask: 0.69, askSize: 10, quoteTime: close / 1000 },
        '04917T.TW': { status: 'available', bid: 2.07, bidSize: 10, ask: null, askSize: null, quoteTime: close / 1000 },
        '714060.TWO': { status: 'available', bid: 0.45, bidSize: 10, ask: 0.7, askSize: 10, quoteTime: close / 1000 },
      },
    });
    expect(h.misCalls()[0]!.searchParams.get('ex_ch')).toBe('tse_066186.tw|tse_04917T.tw|otc_714060.tw');
    expect(h.calls.every(call => call.hostname === 'mis.twse.com.tw' || call.href === lineIdToken.LINE_VERIFY_URL)).toBe(true);
  });

  it('separates ask-only, no book, mismatched venue, malformed time and stale quotes', async () => {
    const h = harness({ now: close + 1000, warrants: [
      { ...row, c: '066186', a: '-_0.6900_', f: '0_18_', b: '-' },
      { ...row, c: '066187', a: '-_', b: '-' },
      { ...row, c: '066188', ex: 'otc', a: '1.10' },
      { ...row, c: '066189', t: '13:29:00', a: '1.10' },
      { ...row, c: '066190', d: '20261125', tlong: String(close - 86400000), a: '1.10' },
      { ...row, c: '009999', rch: undefined, a: '1.10' },
    ] });
    const body = await (await h.request('warrantQuotes=066186.TW,066187.TW,066188.TW,066189.TW,066190.TW,009999.TW')).json();
    expect(body.results['066186.TW']).toMatchObject({ status: 'available', bid: null, ask: 0.69, askSize: 18 });
    expect(body.results['066187.TW']).toMatchObject({ status: 'no_book', bid: null, ask: null });
    expect(body.results['066188.TW']).toMatchObject({ status: 'no_quote', quoteTime: null });
    expect(body.results['066189.TW']).toMatchObject({ status: 'no_quote', quoteTime: null });
    expect(body.results['066190.TW']).toMatchObject({ status: 'stale', quoteTime: close / 1000 - 86400, bid: null, ask: null });
    expect(body.results['009999.TW']).toMatchObject({ status: 'no_quote', quoteTime: null });
  });

  it('ages regular-session records out even within the shared adapter cache', async () => {
    const bookTime = close - 10 * 60_000;
    const h = harness({ now: bookTime + 59_000, warrants: [{ ...row, c: '066186', t: '13:20:00', tlong: String(bookTime), a: '1.10' }] });
    const path = 'warrantQuotes=066186.TW';
    expect(await (await h.request(path)).json()).toMatchObject({ session: 'regular', results: { '066186.TW': { status: 'available', ask: 1.1 } } });
    h.advance(2000);
    expect(await (await h.request(path)).json()).toMatchObject({ session: 'regular', results: { '066186.TW': { status: 'stale', ask: null } } });
    expect(h.misCalls()).toHaveLength(1);
  });

  it.each(['network', 'timeout', 'http', 'json', 'shape', 'api'])('returns source_unavailable on MIS %s', async (fail) => {
    const h = harness({ now: close, fail });
    expect(await (await h.request('warrantQuotes=066186.TW')).json()).toMatchObject({ results: {
      '066186.TW': { status: 'source_unavailable', bid: null, ask: null, quoteTime: null },
    } });
  });
});

describe('market-chart endpoint MIS integration (all upstreams are fake)', () => {
  it('uses one MIS batch for mixed TW/US requests and supplies the otherwise absent OTC index', async () => {
    const h = harness(), response = await h.request('quotes=^TWII,2330.TW,^TWOII,6488.TWO,AAPL');
    expect(response.status).toBe(200);
    const { results, errors } = await response.json();
    expect(errors).toEqual({}); expect(h.misCalls()).toHaveLength(1);
    expect(h.misCalls()[0]!.searchParams.get('ex_ch')).toBe('tse_t00.tw|tse_2330.tw|otc_o00.tw|otc_6488.tw');
    expect(results['^TWII'].chart.result[0].meta).toMatchObject({ atlasLiveSource: 'twse-mis', regularMarketPrice: 42306.23 });
    expect(results['^TWOII'].chart.result[0].timestamp).toEqual([MIS_TODAY]);
    expect(results['6488.TWO'].chart.result[0].indicators.quote[0].volume.at(-1)).toBe(100000);
    expect(results['2330.TW'].chart.result[0].indicators.quote[0].close.at(-1)).toBeNull();
    expect(results.AAPL).toEqual(validation.slimChartPayload(misYahoo('AAPL', true)));
    await h.request('quotes=^TWII,6488.TWO'); expect(h.misCalls()).toHaveLength(1);
  });
  it.each(['^TWII', '6488.TWO'])('overlays a daily chart request for %s with a short HTTP cache', async (symbol) => {
    const h = harness(), response = await h.request(`symbol=${encodeURIComponent(symbol)}&interval=1d&range=5y`);
    expect(response.status).toBe(200); expect(response.headers.get('Cache-Control')).toBe('private, max-age=15');
    const raw = await response.json();
    expect(raw.chart.result[0].timestamp.at(-1)).toBe(MIS_TODAY);
    expect(raw.chart.result[0].meta.atlasLiveSource).toBe('twse-mis'); expect(h.misCalls()).toHaveLength(1);
  });
  it.each(['network', 'timeout', 'http', 'json', 'shape', 'stale', 'api'])('keeps the full Yahoo payload after MIS %s', async (fail) => {
    const h = harness({ fail }), response = await h.request('symbol=%5ETWII&interval=1d&range=5y');
    expect(response.status).toBe(200); expect(await response.json()).toEqual(misYahoo('^TWII', true));
  });
  it('keeps a failed Yahoo history an error in chart mode instead of a lone MIS bar', async () => {
    const h = harness(), response = await h.request('symbol=%5ETWOII&interval=1d&range=5y');
    expect(response.status).toBe(404); expect((await response.json()).error).toBeDefined();
  });
  it('preserves #224 5m hole filling when MIS is unavailable', async () => {
    const h = harness({ fail: 'network', fill: true }), response = await h.request('symbol=%5ETWII&interval=1d&range=5y');
    const raw = await response.json();
    expect(raw.chart.result[0].meta.atlasIntradayFill).toContain('2026-11-04');
    expect(raw.chart.result[0].indicators.quote[0].close[1]).toBe(42948.91);
    expect(raw.chart.result[0].meta.atlasLiveSource).toBeUndefined();
    expect(h.calls.filter(url => url.searchParams.get('interval') === '5m')).toHaveLength(1);
  });
  it('does not cache the MIS overlay as Yahoo and restores the Yahoo source on failure', async () => {
    const h = harness();
    expect((await (await h.request('quotes=^TWII')).json()).results['^TWII'].chart.result[0].meta.atlasLiveSource).toBe('twse-mis');
    h.advance(21_000); h.fail('http');
    const next = await (await h.request('quotes=^TWII')).json();
    expect(next.results['^TWII']).toEqual(validation.slimChartPayload(misYahoo('^TWII', true)));
  });
  it.each(['2026-11-05T05:30:01Z', '2026-11-07T01:47:00Z'])('never calls MIS after the close or on a weekend: %s', async (date) => {
    const h = harness({ now: Date.parse(date) });
    const response = await h.request('symbol=2330.TW&interval=1d&range=5y');
    expect(await response.json()).toEqual(misYahoo('2330.TW', true)); expect(h.misCalls()).toHaveLength(0);
    expect(response.headers.get('Cache-Control')).toBe('private, max-age=600');
  });
  it('leaves US and intraday requests unchanged', async () => {
    const h = harness();
    for (const query of ['symbol=AAPL&interval=1d&range=5y', 'symbol=2330.TW&interval=5m&range=5d']) {
      const response = await h.request(query);
      expect(response.status).toBe(200);
      expect((await response.json()).chart.result[0].meta.atlasLiveSource).toBeUndefined();
    }
    expect(h.misCalls()).toHaveLength(0);
  });
});

describe('market-chart sign-in and usage quota (LINE, device sessions and the quota RPC are fake)', () => {
  const small = (overrides: Partial<QuotaLimits> = {}): QuotaLimits => ({ user: { window: 1000, daily: 1000 }, cloud: { window: 1000, daily: 1000 }, siteDaily: 1000, ...overrides });
  const QUERY = 'symbol=AAPL&interval=1d&range=5y';

  it('answers 401 LOGIN_REQUIRED without a token, also for callers without an Origin, before any upstream call', async () => {
    const h = harness(), response = await h.request(QUERY, null);
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ ok: false, code: 'LOGIN_REQUIRED', error: '登入 LINE 後可查看即時行情與更多股票。' });
    expect(response.headers.get('Cache-Control')).toBe('private, no-store');
    expect(h.calls).toHaveLength(0); expect(h.quota.rpcCalls).toHaveLength(0);
  });

  it('answers 401 for a token LINE rejects and for a malformed token', async () => {
    const h = harness();
    const rejected = await h.request(QUERY, 'line-token-unknown-1');
    expect(rejected.status).toBe(401); expect((await rejected.json()).code).toBe('LOGIN_REQUIRED');
    expect(h.lineCalls).toEqual(['line-token-unknown-1']);
    expect((await h.request(QUERY, 'short')).status).toBe(401);
    expect((await h.request(QUERY, 'atlasdev.not-a-valid-device-token')).status).toBe(401);
    expect(h.lineCalls).toHaveLength(1);
    expect(h.calls.filter(url => validation.YAHOO_HOSTS.includes(url.hostname as never))).toHaveLength(0);
  });

  it('serves a signed-in user with private caching and verifies the token with LINE once per 5 minutes', async () => {
    const h = harness();
    const first = await h.request(QUERY);
    expect(first.status).toBe(200);
    expect(first.headers.get('Cache-Control')).toBe('private, max-age=600');
    expect(first.headers.get('Vary')).toBe('X-Atlas-Token, Origin');
    const quotes = await h.request('quotes=AAPL');
    expect(quotes.status).toBe(200); expect(quotes.headers.get('Cache-Control')).toBe('private, max-age=20');
    expect(h.lineCalls).toHaveLength(1);
    expect(h.quota.rpcCalls).toHaveLength(2);
    expect(h.quota.rpcCalls[0]).toEqual({ user_id: USER, window_limit: 200, daily_limit: 3000, site_limit: 50000, site_exempt: false });
  });

  it('accepts a remembered-device token and applies the signed-in limits', async () => {
    const h = harness();
    expect((await h.request(QUERY, DEVICE_TOKEN)).status).toBe(200);
    expect(h.lineCalls).toHaveLength(0);
    expect(h.quota.rpcCalls[0]).toMatchObject({ user_id: DEVICE, window_limit: 200, daily_limit: 3000, site_exempt: false });
  });

  it('uses the cloud limits for cloud-authorised users', async () => {
    const h = harness();
    expect((await h.request(QUERY, 'line-token-cloud-001')).status).toBe(200);
    expect(h.quota.rpcCalls[0]).toEqual({ user_id: CLOUD, window_limit: 600, daily_limit: 15000, site_limit: 50000, site_exempt: true });
  });

  it('answers 429 USER_WINDOW with Retry-After once the 10-minute cap is used', async () => {
    const h = harness({ limits: small({ user: { window: 2, daily: 1000 } }) });
    expect((await h.request(QUERY)).status).toBe(200);
    expect((await h.request(QUERY)).status).toBe(200);
    const refused = await h.request(QUERY);
    expect(refused.status).toBe(429);
    expect(refused.headers.get('Retry-After')).toBe('120');
    expect(refused.headers.get('Cache-Control')).toBe('private, no-store');
    expect(await refused.json()).toEqual({ ok: false, code: 'USER_WINDOW', error: '查詢太頻繁，請稍後再試（每 10 分鐘上限）', retryAfterSeconds: 120 });
  });

  it('answers 429 USER_DAILY once the daily cap is used', async () => {
    const h = harness({ limits: small({ user: { window: 1000, daily: 1 } }) });
    expect((await h.request(QUERY)).status).toBe(200);
    const refused = await h.request(QUERY);
    expect(refused.status).toBe(429); expect(refused.headers.get('Retry-After')).toBe('3600');
    expect(await refused.json()).toEqual({ ok: false, code: 'USER_DAILY', error: '今日免費額度已用完，明天再試', retryAfterSeconds: 3600 });
  });

  it('never counts or refuses the admin', async () => {
    const h = harness({ limits: small({ user: { window: 1, daily: 1 }, cloud: { window: 1, daily: 1 }, siteDaily: 1 }) });
    for (let i = 0; i < 5; i++) expect((await h.request(QUERY, 'line-token-admin-001')).status).toBe(200);
    expect(h.quota.rpcCalls).toHaveLength(0);
  });

  it('after the site cap refuses ordinary users with SITE_DAILY but still serves cloud users and the admin', async () => {
    const h = harness({ limits: small({ siteDaily: 2 }) });
    expect((await h.request(QUERY)).status).toBe(200);
    expect((await h.request(QUERY, DEVICE_TOKEN)).status).toBe(200);
    const refused = await h.request(QUERY);
    expect(refused.status).toBe(429);
    expect(await refused.json()).toEqual({ ok: false, code: 'SITE_DAILY', error: '今日免費額度已用完，明天再試', retryAfterSeconds: 3600 });
    expect((await h.request(QUERY, DEVICE_TOKEN)).status).toBe(429);
    expect((await h.request(QUERY, 'line-token-cloud-001')).status).toBe(200);
    expect((await h.request(QUERY, 'line-token-admin-001')).status).toBe(200);
  });

  it('fails closed with 503 for ordinary users when the quota RPC fails, without crashing', async () => {
    const h = harness();
    h.quota.fail(true);
    const response = await h.request(QUERY);
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ ok: false, code: 'QUOTA_UNAVAILABLE' });
    expect((await h.request(QUERY, 'line-token-cloud-001')).status).toBe(200);
    expect((await h.request(QUERY, 'line-token-admin-001')).status).toBe(200);
    h.quota.fail(false);
    expect((await h.request(QUERY)).status).toBe(200);
  });

  it('answers 503 when the server configuration is incomplete', async () => {
    const response = await harness({ env: { SUPABASE_SERVICE_ROLE_KEY: '' } }).request(QUERY);
    expect(response.status).toBe(503); expect((await response.json()).code).toBe('AUTH_UNAVAILABLE');
  });

  it('answers the preflight without a token, allowing X-Atlas-Token for a day', async () => {
    const h = harness();
    const response = await h.request(QUERY, null, { method: 'OPTIONS', headers: { Origin: 'https://wudn9922.github.io', 'Access-Control-Request-Headers': 'x-atlas-token' } });
    expect(response.status).toBe(204);
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe('https://wudn9922.github.io');
    expect(response.headers.get('Access-Control-Allow-Headers')!.split(', ')).toContain('x-atlas-token');
    expect(response.headers.get('Access-Control-Max-Age')).toBe('86400');
    const foreign = await h.request(QUERY, 'line-token-user-0001', { headers: { Origin: 'https://example.com' } });
    expect(foreign.status).toBe(403);
    expect(h.lineCalls).toHaveLength(0);
  });
});

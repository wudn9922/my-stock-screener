import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transformSync } from 'esbuild';
import * as policy from '../../supabase/functions/_shared/storageAccess';
import * as deviceSession from '../../supabase/functions/_shared/deviceSession';
import * as monitorHandler from '../../supabase/functions/monitor-signals/handler';
import { createMonitorHandler, MAX_SIGNALS } from '../../supabase/functions/monitor-signals/handler';
import { projectSignal } from '../../supabase/functions/monitor-signals/contract';
import { DEFAULT_ADMIN_LINE_ID, StorageAccessError } from '../../supabase/functions/_shared/storageAccess';

const A = 'U11111111111111111111111111111111', B = 'U22222222222222222222222222222222';
const now = Date.parse('2026-10-28T10:00:00Z');
type Row = Record<string, unknown>;
const row = (extra: Row = {}): Row => ({ line_user_id: A, group_id: 'us_g1', ticker: 'NVDA', market: 'US', ma_period: 20,
  last_alert_at: '2026-10-27T15:00:00Z', last_alert_direction: 'breakout_up', last_alert_price: 150, last_alert_ma_value: 148,
  last_alert_volume_ratio: 1.2, metadata: { group_name: '美股權值', display_name: 'NVIDIA', private: 'do not return' }, ...extra });
function database(rows: Row[], mode = 'cloud', options: { error?: boolean; rogue?: boolean; revoke?: boolean } = {}) {
  const reads: string[] = []; let grants = 0;
  return { reads, from(table: string) {
    reads.push(table);
    let list = table === 'atlas_storage_access'
      ? [{ line_user_id: A, storage_mode: options.revoke && ++grants > 1 ? 'none' : mode, label: '', updated_at: '2026-10-28T00:00:00Z' }]
      : rows;
    let start = 0, end = Infinity, single = false;
    const query = {
      select: () => query,
      eq: (key: string, value: unknown) => { if (!options.rogue) list = list.filter(item => item[key as keyof typeof item] === value); return query; },
      gte: (key: string, value: string) => { list = list.filter(item => String(item[key as keyof typeof item] ?? '') >= value); return query; },
      order: () => query,
      range: (from: number, to: number) => { start = from; end = to + 1; return query; },
      maybeSingle: () => { single = true; return query; },
      then: (resolve: (value: unknown) => unknown) => Promise.resolve(resolve({ data: single ? list[0] ?? null : list.slice(start, end),
        error: options.error && table === 'breakout_alert_state' ? { message: 'private database details' } : null })),
    };
    return query;
  } };
}
const request = (body: Record<string, unknown> = {}) => new Request('https://api.test/monitor-signals', {
  method: 'POST', headers: { 'Content-Type': 'application/json', Origin: 'https://wudn9922.github.io' },
  body: JSON.stringify({ action: 'list', idToken: 'verified-A', ...body }),
});
const handler = (db: ReturnType<typeof database>) => createMonitorHandler({ database: db, adminId: DEFAULT_ADMIN_LINE_ID,
  verifyIdentity: async token => { if (token !== 'verified-A') throw new StorageAccessError(401, 'invalid token', 'TOKEN'); return A; }, now: () => now });

describe('read-only Discord signal projection', () => {
  it('keeps only successful last_alert fields, without account or raw metadata', () => {
    const result = projectSignal(row())!;
    expect(result).toMatchObject({ ticker: 'NVDA', direction: 'breakout_up', price: 150 });
    expect(JSON.stringify(result)).not.toMatch(/line_user_id|private|last_checked/);
    expect(projectSignal(row({ last_alert_at: null, last_alert_direction: null }))).toBeNull();
    expect(projectSignal(row({ group_id: 'atlas_lines' }))).toBeNull();
    expect(projectSignal(row({ last_alert_direction: 'alert_failed' }))).toBeNull();
    expect(projectSignal(row({ ma_period: 0 }))).toBeNull();
  });
  it('supports numeric legacy, custom, watchlist and index groups', () => {
    for (const group_id of ['12', 'custom_12', 'watchlist_12345678-abcd', 'admin_index']) expect(projectSignal(row({ group_id }))).not.toBeNull();
  });
  it('ignores spoofed user IDs and only reads the verified owner', async () => {
    const db = database([row(), row({ line_user_id: B, ticker: 'PRIVATE' })]);
    const response = await handler(db)(request({ userId: B }));
    expect(response.status).toBe(200); expect(response.headers.get('cache-control')).toBe('no-store');
    const body = await response.json(); expect(body.data.signals).toHaveLength(1);
    expect(body.data.signals[0].ticker).toBe('NVDA');
    expect(JSON.stringify(body)).not.toMatch(/PRIVATE|line_user_id|private/);
    expect(db.reads).toEqual(['atlas_storage_access', 'breakout_alert_state', 'atlas_storage_access']);
  });
  it.each(['none', 'device'])('denies %s before any personal table read', async mode => {
    const db = database([row()], mode); expect((await handler(db)(request())).status).toBe(403);
    expect(db.reads).toEqual(['atlas_storage_access']);
  });
  it('rejects invalid credentials before database reads', async () => {
    const db = database([row()]); expect((await handler(db)(request({ idToken: 'bad' }))).status).toBe(401); expect(db.reads).toEqual([]);
  });
  it('retains old holiday/weekend sends, excludes missing, old and future dates', async () => {
    const db = database([row({ last_alert_at: '2026-10-24T09:00:00Z' }), row({ last_alert_at: null }),
      row({ last_alert_at: '2026-09-01T00:00:00Z' }), row({ last_alert_at: '2027-01-01T00:00:00Z' })]);
    const body = await (await handler(db)(request())).json(); expect(body.data.signals).toHaveLength(1);
    expect(body.data.signals[0].sentAt).toBe('2026-10-24T09:00:00.000Z');
  });
  it('pages past the REST cap and reports the bounded truncation', async () => {
    const db = database(Array.from({ length: MAX_SIGNALS + 1 }, (_, i) => row({ ticker: `A${i}` })));
    const body = await (await handler(db)(request())).json(); expect(body.data.signals).toHaveLength(MAX_SIGNALS); expect(body.data.truncated).toBe(true);
    expect(db.reads.filter(table => table === 'breakout_alert_state')).toHaveLength(11);
  });
  it('fails closed on unexpected owner rows, database errors or mid-read revocation', async () => {
    for (const db of [database([row({ line_user_id: B })], 'cloud', { rogue: true }),
      database([row()], 'cloud', { error: true }), database([row()], 'cloud', { revoke: true })]) {
      const response = await handler(db)(request()); expect(response.status).toBeGreaterThanOrEqual(400);
      expect(await response.text()).not.toMatch(/NVDA|private database details|line_user_id/);
    }
  });
  it('supports preflight; rejects writes, oversized requests and other origins', async () => {
    const db = database([]), run = handler(db);
    expect((await run(new Request('https://api.test', { method: 'OPTIONS', headers: { Origin: 'https://wudn9922.github.io' } }))).status).toBe(204);
    expect((await run(new Request('https://api.test'))).status).toBe(405);
    expect((await run(request({ action: 'delete' }))).status).toBe(400);
    expect((await run(request({ idToken: 'a'.repeat(20_000) }))).status).toBe(413);
    expect((await run(new Request('https://api.test', { method: 'POST', headers: { Origin: 'https://evil.test' } }))).status).toBe(403);
    expect(db.reads).toEqual([]);
  });
  it('keeps the existing function entrypoints and gives the new function its own', () => {
    const config = readFileSync(new URL('../../supabase/config.toml', import.meta.url), 'utf8');
    expect(config.split('[functions.user-settings]')[1]!.split('[functions.monitor-signals]')[0]).toContain('entrypoint = "./functions/user-settings/index.ts"');
    expect(config.split('[functions.monitor-signals]')[1]).toContain('entrypoint = "./functions/monitor-signals/index.ts"');
  });
  it('runs the actual Edge entrypoint with server-verified LINE identity, not a body owner', async () => {
    const db = database([row(), row({ line_user_id: B, ticker: 'PRIVATE' })]);
    let serve!: (request: Request) => Promise<Response>;
    const module = { exports: {} };
    const env: Record<string, string> = { SUPABASE_URL: 'https://database.test', SUPABASE_SERVICE_ROLE_KEY: 'fake-server-key',
      LINE_LOGIN_CHANNEL_ID: '12345', ADMIN_LINE_USER_ID: DEFAULT_ADMIN_LINE_ID };
    const code = transformSync(readFileSync(new URL('../../supabase/functions/monitor-signals/index.ts', import.meta.url), 'utf8'), { loader: 'ts', format: 'cjs' }).code;
    const urls: string[] = [];
    runInNewContext(code, {
      module, exports: module.exports, Request, Response, URLSearchParams, AbortSignal, TextDecoder, Date: class extends Date { static now() { return now; } },
      Deno: { env: { get: (name: string) => env[name] }, serve: (fn: typeof serve) => { serve = fn; } },
      require: (name: string) => {
        if (name === '@supabase/supabase-js') return { createClient: () => db };
        if (name.endsWith('deviceSession.ts')) return deviceSession;
        if (name.endsWith('storageAccess.ts')) return policy;
        if (name.endsWith('handler.ts')) return monitorHandler;
        throw new Error('Unexpected module');
      },
      fetch: async (url: string, options: RequestInit) => {
        urls.push(url); expect(String(options.body)).toContain('id_token=verified-A'); expect(String(options.body)).toContain('client_id=12345');
        return new Response(JSON.stringify({ sub: A }));
      },
    });
    const response = await serve(request({ userId: B })); expect(response.status).toBe(200);
    expect(urls).toEqual(['https://api.line.me/oauth2/v2.1/verify']);
    expect(JSON.stringify(await response.json())).not.toMatch(/PRIVATE|private database details|line_user_id/);
  });
});

import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transformSync } from 'esbuild';
import { describe, expect, it } from 'vitest';
import * as policy from '../../supabase/functions/_shared/storageAccess';
import * as watchlists from '../../supabase/functions/user-settings/watchlists';
import * as lineHandoff from '../../supabase/functions/_shared/lineLoginHandoff';
import * as deviceSession from '../../supabase/functions/_shared/deviceSession';
import * as holdings from '../../supabase/functions/user-settings/holdings';

const OWNER = policy.DEFAULT_ADMIN_LINE_ID;
const A = 'U11111111111111111111111111111111';
const B = 'U22222222222222222222222222222222';
const REVISION = '2026-10-23T08:00:00.000Z';
type Row = Record<string, unknown>;
type DbError = { code?: string; message?: string };
type Result = { data: Row | Row[] | null; error: DbError | null; count?: number };
const grant = (user: string, mode: policy.StorageMode) => ({ line_user_id: user, storage_mode: mode, label: '', updated_at: REVISION });
const holdingAccount: holdings.HoldingAccount = { id: '11111111-1111-4111-8111-111111111111', name: 'Private test', kind: 'tw-broker', currency: 'TWD', broker: 'kgi' };
const holdingRow: holdings.Holding = { id: '22222222-2222-4222-8222-222222222222', accountId: holdingAccount.id, symbol: '2330.TW', market: 'TW', quantity: '1357.25', averageCost: '812.5', costCurrency: 'TWD', source: 'import', updatedAt: REVISION };

/** A small relational fake: execute the actual deployed handler, including the shared policy helper. */
function database(seed: Record<string, Row[]> = {}, policyError: DbError | null = null) {
  const rows: Record<string, Row[]> = structuredClone({ atlas_storage_access: [], groups: [], stocks: [], atlas_watchlists: [], chart_drawings: [], index_configs: [], ...seed });
  const calls: { table: string; operation: string; filters: [string, unknown][] }[] = [];
  const rpcCalls: { name: string; parameters: Row }[] = [];
  // Hash → owner, as public.atlas_device_session keeps it (no raw tokens).
  const deviceSessions = new Map<string, string>();
  const portfolios = new Map<string, holdings.HoldingsDocument>();
  const db = {
    rpc: async (name: string, parameters: Row): Promise<{ data: unknown; error: DbError | null }> => {
      rpcCalls.push({ name, parameters });
      if (name === 'atlas_holdings_action') {
        const owner = String(parameters.owner_id), operation = parameters.operation;
        const document: holdings.HoldingsDocument = structuredClone(portfolios.get(owner) ?? { version: 1, accounts: [] });
        const payload = parameters.payload as Row;
        const account = operation === 'replaceAccount' ? holdings.validateHoldingAccount(payload.account) : null;
        const accountId = account?.id ?? payload.accountId;
        const current = document.accounts.find(entry => entry.account.id === accountId);
        const result = (status: string) => ({ data: { status, document }, error: null });
        if (operation !== 'list') {
          if ((current?.revision ?? null) !== parameters.expected_revision || (!current && operation !== 'replaceAccount')) return result('conflict');
          if (account) {
            if (!current && document.accounts.length >= holdings.HOLDINGS_LIMITS.accounts) return result('limit');
            const next = holdings.validateAccountState({ account, holdings: payload.holdings, revision: (current?.revision ?? 0) + 1 });
            document.accounts = [...document.accounts.filter(entry => entry.account.id !== accountId), next];
          } else if (current) {
            if (operation === 'upsert') {
              const next = holdings.validateHolding(payload.holding, current.account);
              current.holdings = [...current.holdings.filter(item => item.id !== next.id), next];
            } else if (operation === 'delete') current.holdings = current.holdings.filter(item => item.id !== payload.holdingId);
            else if (operation === 'deleteAccount') document.accounts = document.accounts.filter(entry => entry.account.id !== accountId);
            current.revision++;
          }
          if (document.accounts.some(entry => entry.holdings.length > holdings.HOLDINGS_LIMITS.perAccount) || document.accounts.reduce((sum, entry) => sum + entry.holdings.length, 0) > holdings.HOLDINGS_LIMITS.total) return result('limit');
          portfolios.set(owner, document);
        }
        return result('ok');
      }
      if (name === 'atlas_device_session') {
        const hash = String(parameters.session_hash);
        if (parameters.operation === 'create') { deviceSessions.set(hash, String(parameters.line_user_id)); return { data: { status: 'created', expiresAt: '2027-01-25T00:00:00Z' }, error: null }; }
        if (parameters.operation === 'revoke') { deviceSessions.delete(hash); return { data: { status: 'revoked' }, error: null }; }
        const owner = deviceSessions.get(hash);
        return { data: owner ? { status: 'active', userId: owner } : { status: 'missing' }, error: null };
      }
      return { data: { status: 'pending', expiresAt: new Date(Date.now() + 120000).toISOString() }, error: null };
    },
    from(table: string) {
      const entry = { table, operation: 'read', filters: [] as [string, unknown][] };
      calls.push(entry);
      const predicates: ((row: Row) => boolean)[] = [];
      let head = false, single = false, limit = Infinity;
      let value: Row = {};
      const query = {
        select: (_columns: string, options?: { head?: boolean }) => { head = options?.head ?? false; return query; },
        eq: (key: string, expected: unknown) => { entry.filters.push([key, expected]); predicates.push(row => row[key] === expected); return query; },
        neq: (key: string, expected: unknown) => { predicates.push(row => row[key] !== expected); return query; },
        order: () => query,
        limit: (count: number) => { limit = count; return query; },
        maybeSingle: () => { single = true; return query; },
        single: () => { single = true; return query; },
        insert: (next: Row) => { entry.operation = 'insert'; value = next; return query; },
        upsert: (next: Row) => { entry.operation = 'upsert'; value = next; return query; },
        update: (next: Row) => { entry.operation = 'update'; value = next; return query; },
        delete: () => { entry.operation = 'delete'; return query; },
        then: (resolve: (result: Result) => unknown) => {
          if (table === policy.STORAGE_ACCESS_TABLE && policyError) return Promise.resolve(resolve({ data: null, error: policyError }));
          const records = rows[table as keyof typeof rows];
          if (!records) throw new Error(`Unexpected table ${table}`);
          let matched = records.filter(row => predicates.every(predicate => predicate(row)));
          if (entry.operation === 'insert' || entry.operation === 'upsert') {
            const record = { id: records.length + 1, updated_at: '2026-10-23T09:00:00.000Z', ...value };
            records.push(record); matched = [record];
          } else if (entry.operation === 'update') {
            matched.forEach(row => Object.assign(row, value, { updated_at: '2026-10-23T09:00:00.000Z' }));
          } else if (entry.operation === 'delete') {
            matched.forEach(row => records.splice(records.indexOf(row), 1));
          }
          return Promise.resolve(resolve({ data: head ? null : single ? (matched[0] ?? null) : structuredClone(matched.slice(0, limit)), error: null, count: matched.length }));
        },
      };
      return query;
    },
  };
  return { db, rows, calls, rpcCalls, deviceSessions, portfolios };
}

function handler(kind: 'user-settings' | 'chart-drawings', user = A, seed: Record<string, Row[]> = {}, policyError: DbError | null = null, verified = true) {
  const fixture = database(seed, policyError);
  const lineVerifications: string[] = [];
  const errorLogs: unknown[][] = [];
  let serve!: (request: Request) => Promise<Response>;
  const source = readFileSync(new URL(`../../supabase/functions/${kind}/index.ts`, import.meta.url), 'utf8').replace(/^import .*;\r?\n/gm, '');
  runInNewContext(transformSync(source, { loader: 'ts', target: 'es2022', format: 'cjs' }).code, {
    ...policy, ...watchlists, ...lineHandoff, ...deviceSession, ...holdings,
    Deno: { env: { get: (key: string) => key === 'ADMIN_LINE_USER_ID' ? OWNER : key === 'ATLAS_FIXED_PARAMETERS_ENABLED' ? '1' : 'offline-test-only' }, serve: (callback: typeof serve) => { serve = callback; } },
    createClient: () => fixture.db,
    fetch: async (_url: string, init: RequestInit) => {
      lineVerifications.push(new URLSearchParams(String(init.body)).get('id_token') ?? '');
      return new Response(JSON.stringify(verified ? { sub: user } : { error: 'invalid_token' }), { status: verified ? 200 : 401 });
    },
    Request, Response, URLSearchParams, TextEncoder, crypto, btoa, console: { error: (...args: unknown[]) => { errorLogs.push(args); } },
  });
  return {
    ...fixture,
    lineVerifications,
    errorLogs,
    async request(body: Row, headers: HeadersInit = { 'cf-connecting-ip': '203.0.113.21' }) {
      const response = await serve(new Request('https://offline.invalid/', { method: 'POST', headers, body: JSON.stringify({ idToken: 'verified-by-fixture', ...body }) }));
      return { status: response.status, body: await response.json() };
    },
  };
}

describe('holdings actual request handler', () => {
  const create = { action: 'holdings.replaceAccount', account: holdingAccount, holdings: [holdingRow], expectedRevision: null };
  it('scopes every action to the verified owner, ignoring forged ownership, and replaces rather than appends', async () => {
    const fixture = handler('user-settings', A, { atlas_storage_access: [grant(A, 'cloud')] });
    fixture.portfolios.set(B, { version: 1, accounts: [{ account: { ...holdingAccount, name: 'B only' }, holdings: [holdingRow], revision: 1 }] });
    expect((await fixture.request({ ...create, owner_id: B, userId: B, line_user_id: B })).status).toBe(200);
    expect(fixture.rpcCalls.at(-1)!.parameters.owner_id).toBe(A);
    expect(fixture.portfolios.get(B)!.accounts[0]!.account.name).toBe('B only');
    const result = await fixture.request({ ...create, expectedRevision: 1, holdings: [{ ...holdingRow, quantity: '2000' }] });
    expect(result.body.data.accounts[0].holdings).toHaveLength(1);
    expect(result.body.data.accounts[0].holdings[0].quantity).toBe('2000');
    expect((await fixture.request({ ...create, expectedRevision: 1 })).status).toBe(409);
    const listing = await fixture.request({ action: 'holdings.list', userId: B });
    expect(listing.body.data.accounts[0].account.name).toBe('Private test');
    expect(fixture.errorLogs).toEqual([]);
  });
  it('does not use another owner account, and covers authorized upsert/delete/clear', async () => {
    const fixture = handler('user-settings', A, { atlas_storage_access: [grant(A, 'cloud')] });
    fixture.portfolios.set(B, { version: 1, accounts: [{ account: holdingAccount, holdings: [holdingRow], revision: 1 }] });
    expect((await fixture.request({ action: 'holdings.deleteAccount', accountId: holdingAccount.id, expectedRevision: 1 })).status).toBe(409);
    await fixture.request(create);
    expect((await fixture.request({ action: 'holdings.upsert', holding: { ...holdingRow, quantity: '2' }, expectedRevision: 1 })).status).toBe(200);
    expect((await fixture.request({ action: 'holdings.delete', accountId: holdingAccount.id, holdingId: holdingRow.id, expectedRevision: 2 })).body.data.accounts[0].holdings).toEqual([]);
    expect((await fixture.request({ ...create, holdings: [], expectedRevision: 3 })).status).toBe(200);
    expect((await fixture.request({ action: 'holdings.deleteAccount', accountId: holdingAccount.id, expectedRevision: 4 })).body.data.accounts).toEqual([]);
    expect(fixture.portfolios.get(B)!.accounts[0]!.holdings).toHaveLength(1);
  });
  it('validates row limits before the RPC, and handles the per-owner transactional limit', async () => {
    const fixture = handler('user-settings', A, { atlas_storage_access: [grant(A, 'cloud')] });
    expect((await fixture.request({ ...create, holdings: Array.from({ length: 1001 }, () => holdingRow) })).status).toBe(400);
    expect(fixture.rpcCalls).toHaveLength(0);
    const accounts = Array.from({ length: 30 }, (_, index) => ({ account: { ...holdingAccount, id: `33333333-3333-4333-8333-${String(index).padStart(12, '0')}` }, holdings: [], revision: 1 }));
    fixture.portfolios.set(A, { version: 1, accounts });
    expect((await fixture.request(create)).status).toBe(400);
    expect(fixture.portfolios.get(A)!.accounts).toHaveLength(30);
  });
  it('suppresses private database error details and never logs request contents', async () => {
    const fixture = handler('user-settings', A, { atlas_storage_access: [grant(A, 'cloud')] });
    fixture.db.rpc = async () => ({ data: null, error: { code: '23505', message: 'private quantity 1357.25 / cost 812.5' } });
    const response = await fixture.request(create);
    expect(response.status).toBe(503);
    expect(JSON.stringify(response.body)).not.toMatch(/1357|812|Private/);
    expect(fixture.errorLogs).toEqual([]);
  });
  it('keeps the total owner limit across accounts and re-checks cloud permission after a successful request', async () => {
    const fixture = handler('user-settings', A, { atlas_storage_access: [grant(A, 'cloud')] });
    const existing = [0, 1].map(index => {
      const account = { ...holdingAccount, id: `33333333-3333-4333-8333-${String(index).padStart(12, '0')}` };
      return { account, revision: 1, holdings: Array.from({ length: 1000 }, (_, i) => ({ ...holdingRow, accountId: account.id, id: `44444444-4444-4444-8444-${String(i).padStart(12, '0')}`, symbol: `${7000 + i}.TW` })) };
    });
    fixture.portfolios.set(A, { version: 1, accounts: existing });
    expect((await fixture.request(create)).status).toBe(400);
    expect(fixture.portfolios.get(A)!.accounts).toHaveLength(2);
    expect((await fixture.request({ action: 'holdings.list' })).status).toBe(200);
    const count = fixture.rpcCalls.length;
    fixture.rows.atlas_storage_access[0].storage_mode = 'device';
    expect((await fixture.request({ action: 'holdings.list' })).status).toBe(403);
    expect(fixture.rpcCalls).toHaveLength(count);
  });
  it('fails safely when schema/RPC is not deployed', async () => {
    const fixture = handler('user-settings', A, { atlas_storage_access: [grant(A, 'cloud')] });
    fixture.db.rpc = async () => ({ data: null, error: { code: 'PGRST202' } });
    expect((await fixture.request({ action: 'holdings.list' })).body.error).toContain('尚未啟用');
  });
});

describe('storage authorization is enforced by deployed private-data handlers', () => {
  it('does not accept caller-supplied forwarding headers as a network binding', async () => {
    const fixture = handler('user-settings');
    const result = await fixture.request({ action: 'line.login.begin', intentId: '12345678-1234-4234-8234-123456789abc',
      readSecret: 'r'.repeat(43), writeSecret: 'w'.repeat(43) }, { 'x-forwarded-for': '203.0.113.21', 'x-real-ip': '203.0.113.21' });
    expect(result.status).toBe(403); expect(result.body.error).toContain('同一個網路');
    expect(fixture.rpcCalls).toEqual([]); expect(fixture.calls).toEqual([]);
  });
  it.each(['begin', 'poll', 'cancel'])('allows only proof-bound login %s without a LINE identity or personal-table access', async operation => {
    const fixture = handler('user-settings', A, {}, null, false);
    const result = await fixture.request({ action: `line.login.${operation}`, idToken: undefined,
      intentId: '12345678-1234-4234-8234-123456789abc', readSecret: 'r'.repeat(43), writeSecret: 'w'.repeat(43) });
    expect(result.status).toBe(200);
    expect(fixture.rpcCalls).toHaveLength(1);
    expect(fixture.calls).toEqual([]);
  });
  it('verifies the completing LINE account and rejects invalid proofs before creating private login data', async () => {
    const fixture = handler('user-settings', A, {}, null, false);
    const invalidToken = await fixture.request({ action: 'line.login.complete', confirmed: true, intentId: '12345678-1234-4234-8234-123456789abc', writeSecret: 'w'.repeat(43) });
    expect(invalidToken.status).toBe(401);
    const invalidProof = await fixture.request({ action: 'line.login.begin', idToken: undefined, intentId: '12345678-1234-4234-8234-123456789abc', readSecret: 'bad', writeSecret: 'w'.repeat(43) });
    expect(invalidProof.status).toBe(400);
    expect(fixture.rpcCalls).toEqual([]);
    expect(fixture.calls).toEqual([]);
  });
  const actions = [
    ['user-settings', 'groups.list'], ['user-settings', 'groups.create'], ['user-settings', 'groups.rename'], ['user-settings', 'groups.delete'],
    ['user-settings', 'stocks.list'], ['user-settings', 'stocks.save'], ['user-settings', 'stocks.delete'],
    ['user-settings', 'watchlists.list'], ['user-settings', 'watchlists.save'], ['user-settings', 'watchlists.delete'],
    ['user-settings', 'holdings.list'], ['user-settings', 'holdings.replaceAccount'], ['user-settings', 'holdings.upsert'], ['user-settings', 'holdings.delete'], ['user-settings', 'holdings.deleteAccount'],
    ['user-settings', 'holdings.mexc.status'], ['user-settings', 'holdings.mexc.connect'], ['user-settings', 'holdings.mexc.sync'], ['user-settings', 'holdings.mexc.apply'], ['user-settings', 'holdings.mexc.disconnect'],
    ['user-settings', 'admin.index.list'], ['user-settings', 'admin.index.save'],
    ['chart-drawings', 'atlas.list'], ['chart-drawings', 'atlas.save'], ['chart-drawings', 'load'], ['chart-drawings', 'save'],
  ] as const;
  it.each(actions)('denies %s %s in both unapproved and device modes before any personal table access', async (kind, action) => {
    for (const mode of ['none', 'device'] as const) {
      const fixture = handler(kind, A, { atlas_storage_access: [grant(A, mode)] });
      const response = await fixture.request({ action, ticker: 'NVDA', timeframe: '1d', symbol: 'NVDA', name: 'private', line_user_id: OWNER, userId: OWNER, storageMode: 'cloud', isAdmin: true });
      expect(response.status).toBe(403);
      expect(response.body.code).toBe('STORAGE_NOT_CLOUD');
      expect(fixture.calls.map(call => call.table)).toEqual(['atlas_storage_access']);
    }
  });

  it.each(['user-settings', 'chart-drawings'] as const)('rejects missing and invalid identity before database access in %s', async kind => {
    const fixture = handler(kind);
    expect((await fixture.request({ action: 'groups.list', idToken: '' })).status).toBe(401);
    expect(fixture.calls).toHaveLength(0);
    const invalid = handler(kind, A, {}, null, false);
    expect((await invalid.request({ action: 'atlas.list' })).status).toBe(401);
    expect(invalid.calls).toHaveLength(0);
  });

  it('discovers access with no automatic enrollment, even with a forged owner or cloud flag', async () => {
    const fixture = handler('user-settings');
    for (let i = 0; i < 2; i++) {
      const response = await fixture.request({ action: 'access.get', userId: OWNER, isAdmin: true, storageMode: 'cloud' });
      expect(response).toMatchObject({ status: 200, body: { data: { userId: A, isAdmin: false, storageMode: 'none' } } });
    }
    expect(fixture.calls.every(call => call.operation === 'read' && call.table === 'atlas_storage_access')).toBe(true);
    expect(fixture.rows.atlas_storage_access).toEqual([]);
  });

  it('retains owner bootstrap only when no explicit grant exists; an explicit device grant blocks owner cloud operations', async () => {
    const owner = handler('user-settings', OWNER);
    expect((await owner.request({ action: 'access.get' })).body.data).toMatchObject({ isAdmin: true, storageMode: 'cloud' });
    expect((await owner.request({ action: 'groups.list' })).status).toBe(200);
    const localOwner = handler('user-settings', OWNER, { atlas_storage_access: [grant(OWNER, 'device')] });
    expect((await localOwner.request({ action: 'groups.create', name: 'x' })).status).toBe(403);
    expect(localOwner.calls.every(call => call.table === 'atlas_storage_access')).toBe(true);
  });

  it('fails closed on an unavailable or malformed policy and allows missing-table bootstrap only for owner', async () => {
    for (const error of [{ code: '42P01' }, { code: 'PGRST205' }]) {
      const visitor = handler('user-settings', A, {}, error);
      expect((await visitor.request({ action: 'access.get' })).body.data).toMatchObject({ storageMode: 'none', policyAvailable: false });
      expect((await visitor.request({ action: 'stocks.list' })).status).toBe(403);
      expect((await handler('user-settings', OWNER, {}, error).request({ action: 'groups.list' })).status).toBe(200);
    }
    const outage = handler('user-settings', A, {}, { code: '08006', message: 'network' });
    expect((await outage.request({ action: 'access.get' })).status).toBe(503);
    const malformed = handler('user-settings', A, { atlas_storage_access: [{ ...grant(A, 'cloud'), updated_at: 'bad-date' }] });
    expect((await malformed.request({ action: 'stocks.list' })).status).toBe(503);
    expect(malformed.calls).toHaveLength(1);
  });

  it('checks revocation on every request, including reads of old cloud data', async () => {
    const fixture = handler('user-settings', A, { atlas_storage_access: [grant(A, 'cloud')], stocks: [{ id: 1, line_user_id: A, ticker: 'NVDA', group_id: 'us_g1' }] });
    expect((await fixture.request({ action: 'stocks.list' })).body.data).toHaveLength(1);
    fixture.rows.atlas_storage_access[0].storage_mode = 'none';
    const before = fixture.calls.length;
    expect((await fixture.request({ action: 'stocks.list' })).status).toBe(403);
    expect(fixture.calls.slice(before).map(call => call.table)).toEqual(['atlas_storage_access']);
    expect(fixture.rows.stocks).toHaveLength(1); // Revocation preserves old rows; it does not delete them.
  });

  it('scopes cloud reads and inserted group ownership to verified identity rather than body values', async () => {
    const fixture = handler('user-settings', A, { atlas_storage_access: [grant(A, 'cloud')], groups: [{ id: 1, line_user_id: A, name: 'A' }, { id: 2, line_user_id: B, name: 'B' }] });
    const result = await fixture.request({ action: 'groups.list', line_user_id: B, userId: B });
    expect(result.body.data).toEqual([expect.objectContaining({ name: 'A' })]);
    expect((await fixture.request({ action: 'groups.create', name: 'new', line_user_id: B })).status).toBe(200);
    expect(fixture.rows.groups.at(-1)).toMatchObject({ name: 'new', line_user_id: A });
    const before = fixture.rows.groups[1].name;
    expect((await fixture.request({ action: 'groups.rename', id: 2, name: 'stolen' })).status).toBe(200);
    expect(fixture.rows.groups[1].name).toBe(before);
  });

  it('scopes drawing reads and saves to the verified account in Atlas and legacy routes', async () => {
    for (const action of ['atlas.list', 'load']) {
      const fixture = handler('chart-drawings', A, { atlas_storage_access: [grant(A, 'cloud')], chart_drawings: [
        { line_user_id: A, ticker: 'NVDA', timeframe: action === 'load' ? '1d' : 'atlas', drawings: [{ id: 'own' }], updated_at: REVISION },
        { line_user_id: B, ticker: 'NVDA', timeframe: action === 'load' ? '1d' : 'atlas', drawings: [{ id: 'other' }], updated_at: REVISION },
      ] });
      const response = await fixture.request({ action, ticker: 'NVDA', timeframe: '1d', line_user_id: B });
      expect(response.status).toBe(200);
      expect(JSON.stringify(response.body)).toContain('own');
      expect(JSON.stringify(response.body)).not.toContain('other');
    }
    for (const action of ['atlas.save', 'save']) {
      const fixture = handler('chart-drawings', A, { atlas_storage_access: [grant(A, 'cloud')] });
      const response = await fixture.request({ action, symbol: 'NVDA', ticker: 'NVDA', timeframe: '1d', drawings: [], updatedAt: Date.now(), line_user_id: B });
      expect(response.status).toBe(200);
      expect(fixture.rows.chart_drawings.at(-1)).toMatchObject({ line_user_id: A, ticker: 'NVDA' });
    }
  });

  it.each(['admin.access.list', 'admin.access.save'])('denies non-owner %s before table access despite forged flags', async action => {
    const fixture = handler('user-settings', A, { atlas_storage_access: [grant(A, 'cloud')] });
    expect((await fixture.request({ action, userId: OWNER, storageMode: 'cloud', isAdmin: true })).status).toBe(403);
    expect(fixture.calls).toHaveLength(0);
  });

  it('lets the verified owner administer grants while personally using none mode', async () => {
    const fixture = handler('user-settings', OWNER, { atlas_storage_access: [grant(OWNER, 'none')] });
    expect((await fixture.request({ action: 'admin.access.list' })).status).toBe(200);
    const response = await fixture.request({ action: 'admin.access.save', userId: A, storageMode: 'device', expectedRevision: 'none-default', label: '手機' });
    expect(response).toMatchObject({ status: 200, body: { data: { userId: A, storageMode: 'device', label: '手機' } } });
    expect(fixture.rows.atlas_storage_access.find(row => row.line_user_id === A)).toMatchObject({ updated_by: OWNER });
    expect(fixture.calls.every(call => call.table === 'atlas_storage_access')).toBe(true);
  });

  it('rejects stale grant edits without mutation and validates target IDs/modes/labels', async () => {
    const fixture = handler('user-settings', OWNER, { atlas_storage_access: [grant(A, 'device')] });
    expect((await fixture.request({ action: 'admin.access.save', userId: A, storageMode: 'cloud', expectedRevision: 'stale' })).status).toBe(409);
    for (const body of [ { userId: 'not-a-line-id', storageMode: 'cloud' }, { userId: A, storageMode: 'unlimited' }, { userId: A, storageMode: 'cloud', label: 'x'.repeat(81) }, { userId: A, storageMode: 'cloud', label: 'bad\u0000label' } ]) {
      expect((await fixture.request({ action: 'admin.access.save', ...body })).status).toBe(400);
    }
    expect(fixture.rows.atlas_storage_access[0].storage_mode).toBe('device');
    expect(fixture.calls.every(call => call.operation === 'read')).toBe(true);
  });

  it.each([
    { ...grant(A, 'cloud'), storage_mode: 'unknown' },
    { ...grant(A, 'cloud'), label: null },
    { ...grant(A, 'cloud'), label: 'x'.repeat(81) },
    { ...grant(A, 'cloud'), updated_at: '' },
  ])('never grants cloud persistence from a malformed stored policy: %j', async malformed => {
    const fixture = handler('user-settings', A, { atlas_storage_access: [malformed] });
    expect((await fixture.request({ action: 'stocks.list' })).status).toBe(503);
    expect(fixture.calls.every(call => call.table === 'atlas_storage_access' && call.operation === 'read')).toBe(true);
  });

  it('treats a wrong-user or array policy result as unavailable even if the database filter misbehaves', async () => {
    for (const data of [grant(B, 'cloud'), [grant(A, 'cloud')]]) {
      const query = {
        select: () => query, eq: () => query, maybeSingle: () => query,
        then: (resolve: (value: unknown) => unknown) => Promise.resolve(resolve({ data, error: null })),
      };
      await expect(policy.getStorageAccess({ from: () => query }, A, OWNER)).rejects.toMatchObject({ status: 503, code: 'ACCESS_UNAVAILABLE' });
    }
  });

  it('does not expose or change grants before the manual metadata schema exists', async () => {
    const fixture = handler('user-settings', OWNER, {}, { code: '42P01' });
    expect((await fixture.request({ action: 'admin.access.list' })).body).toMatchObject({ ok: false, code: 'ACCESS_SCHEMA_REQUIRED' });
    expect((await fixture.request({ action: 'admin.access.save', userId: A, storageMode: 'cloud' })).body).toMatchObject({ ok: false, code: 'ACCESS_SCHEMA_REQUIRED' });
    expect(fixture.calls.every(call => call.operation === 'read')).toBe(true);
  });

  it('validates server owner configuration and refuses invalid verified LINE subject IDs', async () => {
    expect(() => policy.adminLineId('not-an-owner-id')).toThrow('管理員帳號設定');
    expect(policy.adminLineId(undefined)).toBe(OWNER);
    for (const subject of ['U-owner', 'U' + 'A'.repeat(32), '', A + '-extra']) {
      const fixture = handler('user-settings', subject);
      expect((await fixture.request({ action: 'access.get' })).status).toBe(401);
      expect(fixture.calls).toHaveLength(0);
    }
  });
});

describe('remembered-device sessions', () => {
  const DEVICE = /^atlasdev\.[A-Za-z0-9_-]{43}$/;
  it('issues a device token only for a verified LINE login and stores only its hash', async () => {
    const fixture = handler('user-settings', A);
    const created = await fixture.request({ action: 'device.session.create', idToken: 'line-id-token' });
    expect(created.status).toBe(200);
    expect(created.body.data.userId).toBe(A);
    const token = created.body.data.deviceToken as string;
    expect(token).toMatch(DEVICE);
    expect(fixture.lineVerifications).toEqual(['line-id-token']);
    const [hash] = [...fixture.deviceSessions.keys()];
    expect(hash).toMatch(/^[0-9a-f]{64}$/);
    expect(JSON.stringify(fixture.rpcCalls)).not.toContain(token.slice(9));
    // A device token can never mint another one.
    const chained = await fixture.request({ action: 'device.session.create', idToken: token });
    expect(chained.status).toBe(400);
    expect(fixture.deviceSessions.size).toBe(1);
    // An invalid LINE token creates nothing.
    const invalid = handler('user-settings', A, {}, null, false);
    expect((await invalid.request({ action: 'device.session.create', idToken: 'expired' })).status).toBe(401);
    expect(invalid.deviceSessions.size).toBe(0);
  });

  it('resolves a device token to its account without LINE, while every request still checks the grant', async () => {
    const fixture = handler('user-settings', A, { atlas_storage_access: [grant(A, 'cloud')], stocks: [{ id: 1, line_user_id: A, ticker: 'NVDA', group_id: 'us_g1' }] });
    const token = (await fixture.request({ action: 'device.session.create', idToken: 'line-id-token' })).body.data.deviceToken as string;
    const access = await fixture.request({ action: 'access.get', idToken: token });
    expect(access.status).toBe(200);
    expect(access.body.data).toMatchObject({ userId: A, storageMode: 'cloud' });
    expect((await fixture.request({ action: 'stocks.list', idToken: token })).status).toBe(200);
    expect(fixture.lineVerifications).toEqual(['line-id-token']);
    fixture.rows.atlas_storage_access[0].storage_mode = 'none';
    expect((await fixture.request({ action: 'stocks.list', idToken: token })).status).toBe(403);
  });

  it('signs a device out on revoke, and rejects unknown or malformed device tokens without LINE', async () => {
    const fixture = handler('user-settings', A);
    const token = (await fixture.request({ action: 'device.session.create', idToken: 'line-id-token' })).body.data.deviceToken as string;
    expect((await fixture.request({ action: 'device.session.revoke', idToken: token })).status).toBe(200);
    expect(fixture.deviceSessions.size).toBe(0);
    const revoked = await fixture.request({ action: 'access.get', idToken: token });
    expect(revoked.status).toBe(401);
    expect(revoked.body.code).toBe('device_session');
    const calls = fixture.rpcCalls.length;
    expect((await fixture.request({ action: 'access.get', idToken: 'atlasdev.short' })).status).toBe(401);
    expect(fixture.rpcCalls).toHaveLength(calls);
    expect(fixture.lineVerifications).toEqual(['line-id-token']);
  });

  it('lets chart drawings use a device token under the same grant check', async () => {
    const settings = handler('user-settings', A);
    const token = (await settings.request({ action: 'device.session.create', idToken: 'line-id-token' })).body.data.deviceToken as string;
    for (const [mode, status] of [['cloud', 200], ['device', 403]] as const) {
      const drawings = handler('chart-drawings', A, { atlas_storage_access: [grant(A, mode)] });
      for (const [hash, owner] of settings.deviceSessions) drawings.deviceSessions.set(hash, owner);
      expect((await drawings.request({ action: 'atlas.list', idToken: token })).status).toBe(status);
      expect(drawings.lineVerifications).toEqual([]);
    }
  });
});

import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transformSync } from 'esbuild';
import { describe, expect, it } from 'vitest';
import { watchlistId, watchlistVersion } from '../../supabase/functions/user-settings/watchlists';
import * as storageAccess from '../../supabase/functions/_shared/storageAccess';
import * as lineHandoff from '../../supabase/functions/_shared/lineLoginHandoff';

type Row = Record<string, unknown>;
/** Exercise the deployed handler's validation and ownership predicates with an offline database. */
function handlerFixture(seed: Row[] = [], count = seed.length, enabled = true, tableName = 'stocks') {
  const rows = structuredClone(seed);
  let handler!: (request: Request) => Promise<Response>;
  const from = (table: string) => {
    const filters: ((row: Row) => boolean)[] = [];
    let mode = 'read',
      value: Row = {},
      head = false,
      single = false;
    const query = {
      select: (_columns: string, options?: { head?: boolean }) => {
        head = options?.head ?? false;
        return query;
      },
      eq: (key: string, expected: unknown) => {
        filters.push((row) => row[key] === expected);
        return query;
      },
      neq: (key: string, expected: unknown) => {
        filters.push((row) => row[key] !== expected);
        return query;
      },
      order: () => query,
      maybeSingle: () => {
        single = true;
        return query;
      },
      update: (next: Row) => {
        mode = 'update';
        value = next;
        return query;
      },
      upsert: (next: Row) => {
        mode = 'upsert';
        value = next;
        return query;
      },
      delete: () => {
        mode = 'delete';
        return query;
      },
      then: (resolve: (result: { data: Row[] | Row | null; error: null; count?: number }) => unknown) => {
        if (table === 'atlas_storage_access') return Promise.resolve(resolve({ data: null, error: null }));
        if (table !== tableName) throw new Error(`Unexpected table ${table}`);
        let matched = rows.filter((row) => filters.every((filter) => filter(row)));
        if (mode === 'upsert') {
          let record = rows.find((row) => ['line_user_id', 'ticker', 'group_id'].every((key) => row[key] === value[key]));
          if (!record) {
            record = { id: rows.length + 1 };
            rows.push(record);
          }
          Object.assign(record, value);
          matched = [record];
        }
        if (mode === 'update') matched.forEach((row) => Object.assign(row, value));
        if (mode === 'delete') matched.forEach((row) => rows.splice(rows.indexOf(row), 1));
        return Promise.resolve(
          resolve({
            data: head ? null : single ? (matched[0] ?? null) : structuredClone(matched),
            error: null,
            count: head ? count : undefined,
          }),
        );
      },
    };
    return query;
  };
  const source = readFileSync(new URL('../../supabase/functions/user-settings/index.ts', import.meta.url), 'utf8').replace(
    /^import .*;\r?\n/gm,
    '',
  );
  const code = transformSync(source, {
    loader: 'ts',
    target: 'es2022',
    format: 'cjs',
  }).code;
  runInNewContext(code, {
    Deno: {
      env: {
        get: (name: string) => name === 'ADMIN_LINE_USER_ID' ? storageAccess.DEFAULT_ADMIN_LINE_ID : (name === 'ATLAS_FIXED_PARAMETERS_ENABLED' ? (enabled ? '1' : '0') : 'configured-test-value'),
      },
      serve: (callback: typeof handler) => {
        handler = callback;
      },
    },
    createClient: () => ({ from }),
    watchlistId,
    watchlistVersion,
    ...storageAccess,
    ...lineHandoff,
    Request,
    Response,
    URLSearchParams,
    console,
    fetch: async () => new Response(JSON.stringify({ sub: storageAccess.DEFAULT_ADMIN_LINE_ID }), { status: 200 }),
  });
  const request = async (body: Row) => {
    const response = await handler(
      new Request('https://test.invalid', {
        method: 'POST',
        body: JSON.stringify({ idToken: 'offline-test-token', ...body }),
      }),
    );
    return { status: response.status, body: await response.json() };
  };
  return { rows, request };
}

describe('user-settings stock parameters', () => {
  it('treats an already deleted watchlist as absent while retaining version-conflict protection', async () => {
    const id = 'd93f0aa6-c873-4f97-8cf9-45a7b55b6439';
    const body = { action: 'watchlists.delete', id, expectedUpdatedAt: '2026-10-20T10:00:00Z' };
    const empty = handlerFixture([], 0, true, 'atlas_watchlists');
    expect((await empty.request(body)).status).toBe(200);
    const changed = handlerFixture([{ id, line_user_id: storageAccess.DEFAULT_ADMIN_LINE_ID, updated_at: '2026-10-20T10:01:00Z' }], 1, true, 'atlas_watchlists');
    expect((await changed.request(body)).status).toBe(409);
    expect(changed.rows).toHaveLength(1);
  });
  it('requires the dormant-row consumers to be enabled before creating fixed parameters', async () => {
    const fixture = handlerFixture([], 0, false);
    expect(
      (
        await fixture.request({
          action: 'stocks.save',
          ticker: 'NVDA',
          groupId: 'ungrouped',
          mas: [13, 34],
        })
      ).status,
    ).toBe(503);
    expect(fixture.rows).toHaveLength(0);
  });
  it('stores a dormant per-ticker preference in the verified user account and updates it at the stock limit', async () => {
    const fixed = {
      id: 1,
      line_user_id: storageAccess.DEFAULT_ADMIN_LINE_ID,
      ticker: 'NVDA',
      group_id: 'ungrouped',
      ma1: 20,
    };
    const other = { ...fixed, id: 2, line_user_id: 'U11111111111111111111111111111111' };
    const fixture = handlerFixture([fixed, other], 500);
    const result = await fixture.request({
      action: 'stocks.save',
      ticker: 'nvda',
      groupId: 'ungrouped',
      mas: [13, 34],
      line_user_id: 'U11111111111111111111111111111111',
    });
    expect(result.status).toBe(200);
    expect(fixture.rows.find((row) => row.id === 1)).toMatchObject({
      ma1: 13,
      ma2: 34,
      ma3: 0,
      ma4: 0,
    });
    expect(fixture.rows.find((row) => row.id === 2)).toEqual(other);
    expect(
      (
        await fixture.request({
          action: 'stocks.save',
          ticker: 'AAPL',
          groupId: 'ungrouped',
          mas: [20],
        })
      ).status,
    ).toBe(400);
  });
  it('does not edit or delete a parameter row owned by another user', async () => {
    const other = {
      id: 1,
      line_user_id: 'U11111111111111111111111111111111',
      ticker: 'NVDA',
      group_id: 'ungrouped',
      ma1: 20,
    };
    const fixture = handlerFixture([other]);
    expect(
      (
        await fixture.request({
          action: 'stocks.save',
          id: 1,
          ticker: 'NVDA',
          groupId: 'ungrouped',
          mas: [13],
        })
      ).status,
    ).toBe(404);
    expect((await fixture.request({ action: 'stocks.delete', id: 1 })).status).toBe(404);
    expect(fixture.rows).toEqual([other]);
  });
  it('keeps grouped and fixed rows independent and never accepts invalid periods or privileged groups', async () => {
    const fixture = handlerFixture();
    expect(
      (
        await fixture.request({
          action: 'stocks.save',
          ticker: 'NVDA',
          groupId: 'ungrouped',
          mas: [13, 34],
        })
      ).status,
    ).toBe(200);
    expect(
      (
        await fixture.request({
          action: 'stocks.save',
          ticker: 'NVDA',
          groupId: 'us_g1',
          mas: [20, 50],
        })
      ).status,
    ).toBe(200);
    expect(fixture.rows).toHaveLength(2);
    for (const body of [
      { groupId: 'ungrouped', mas: [20, 20] },
      { groupId: 'ungrouped', mas: [1001] },
      { groupId: 'admin_index', mas: [20] },
      { groupId: 'someone_else', mas: [20] },
    ])
      expect(
        (
          await fixture.request({
            action: 'stocks.save',
            ticker: 'NVDA',
            ...body,
          })
        ).status,
      ).toBe(400);
    expect((await fixture.request({ action: 'stocks.delete', id: 2 })).status).toBe(200);
    expect(fixture.rows).toHaveLength(1);
    expect(fixture.rows[0].group_id).toBe('ungrouped');
  });
});

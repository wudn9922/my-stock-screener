import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transformSync } from 'esbuild';
import { describe, expect, it, vi } from 'vitest';
import { HoldingsError } from '../../supabase/functions/user-settings/holdings';

const owner = 'U' + 'a'.repeat(32);
const us = '20000000-0000-4000-8000-000000000001';
const tw = '20000000-0000-4000-8000-000000000002';
type Row = Record<string, unknown>;

/** Minimal PostgREST-style builder over in-memory tables. */
function fakeDb(accounts: { id: string; kind: string }[], applyStatus: Row = { status: 'ok', applied: 2 }) {
  const tables: Record<string, Row[]> = { atlas_statement_links: [], atlas_statement_messages: [], atlas_statement_trades: [] };
  const query = (table: string) => {
    const filters: ((row: Row) => boolean)[] = [];
    let mode: 'select' | 'delete' = 'select';
    const run = () => {
      const rows = tables[table]!.filter((row) => filters.every((f) => f(row)));
      if (mode === 'delete') tables[table] = tables[table]!.filter((row) => !rows.includes(row));
      return Promise.resolve({ data: rows, error: null });
    };
    const builder: Record<string, unknown> = {
      select: () => builder,
      delete: () => ((mode = 'delete'), builder),
      eq: (key: string, value: unknown) => (filters.push((row) => row[key] === value), builder),
      is: (key: string, value: unknown) => (filters.push((row) => (row[key] ?? null) === value), builder),
      order: () => builder,
      limit: () => builder,
      upsert: (row: Row) => {
        tables[table] = tables[table]!.filter((r) => !(r.line_user_id === row.line_user_id && r.account_id === row.account_id));
        tables[table]!.push(row);
        return Promise.resolve({ error: null });
      },
      then: (resolve: (value: unknown) => unknown) => run().then(resolve),
    };
    return builder;
  };
  const rpc = vi.fn(async (name: string) =>
    name === 'atlas_holdings_action'
      ? { data: { status: 'ok', document: { accounts: accounts.map((account) => ({ account })) } }, error: null }
      : { data: applyStatus, error: null },
  );
  return { db: { from: vi.fn(query), rpc }, tables, rpc };
}

function load() {
  const module = { exports: {} as { handleStatement: (db: unknown, owner: string, admin: string | null, body: Row) => Promise<Row> } };
  const text = readFileSync(new URL('../../supabase/functions/user-settings/statement.ts', import.meta.url), 'utf8');
  runInNewContext(transformSync(text, { loader: 'ts', format: 'cjs' }).code, {
    module, exports: module.exports, require: () => ({ HoldingsError }),
  });
  return module.exports.handleStatement;
}

describe('e-mail statement sync bridge', () => {
  const handle = load();
  const accounts = [{ id: us, kind: 'us-broker' }, { id: tw, kind: 'tw-broker' }];

  it('is limited to the owner', async () => {
    const { db, rpc } = fakeDb(accounts);
    await expect(handle(db, owner, 'U' + 'b'.repeat(32), { action: 'holdings.statement.status' })).rejects.toMatchObject({ status: 403 });
    expect(rpc).not.toHaveBeenCalled();
  });

  it('links a matching account and applies stored trades at once', async () => {
    const { db, tables, rpc } = fakeDb(accounts);
    const result = await handle(db, owner, owner, { action: 'holdings.statement.link', accountId: us, broker: 'cathay', market: 'US' });
    expect(result).toEqual({ status: 'ok', applied: 2, snapshots: 0, missingCost: 0 });
    expect(tables.atlas_statement_links).toEqual([{ line_user_id: owner, account_id: us, broker: 'cathay', market: 'US' }]);
    expect(rpc).toHaveBeenLastCalledWith('atlas_statement_apply', { owner_id: owner, broker_code: 'cathay', market_code: 'US' });
  });

  it('refuses a market mismatch, an unsupported broker and a second account for the same broker market', async () => {
    const { db } = fakeDb(accounts);
    await expect(handle(db, owner, owner, { action: 'holdings.statement.link', accountId: tw, broker: 'cathay', market: 'US' }))
      .rejects.toMatchObject({ status: 400 });
    await expect(handle(db, owner, owner, { action: 'holdings.statement.link', accountId: us, broker: 'kgi', market: 'US' }))
      .rejects.toMatchObject({ status: 400 });
    // 永豐 reports cover Taiwan stocks only.
    await expect(handle(db, owner, owner, { action: 'holdings.statement.link', accountId: us, broker: 'sinopac', market: 'US' }))
      .rejects.toMatchObject({ status: 400 });
    await handle(db, owner, owner, { action: 'holdings.statement.link', accountId: tw, broker: 'sinopac', market: 'TW' });
    await handle(db, owner, owner, { action: 'holdings.statement.link', accountId: us, broker: 'cathay', market: 'US' });
    const other = '20000000-0000-4000-8000-000000000003';
    const second = fakeDb([...accounts, { id: other, kind: 'us-broker' }]);
    second.tables.atlas_statement_links.push({ line_user_id: owner, account_id: us, broker: 'cathay', market: 'US' });
    await expect(handle(second.db, owner, owner, { action: 'holdings.statement.link', accountId: other, broker: 'cathay', market: 'US' }))
      .rejects.toMatchObject({ status: 409 });
  });

  it('replaces a link left behind by a deleted account', async () => {
    const { db, tables } = fakeDb([{ id: us, kind: 'us-broker' }]);
    tables.atlas_statement_links.push({ line_user_id: owner, account_id: '20000000-0000-4000-8000-0000000000ff', broker: 'cathay', market: 'US' });
    await handle(db, owner, owner, { action: 'holdings.statement.link', accountId: us, broker: 'cathay', market: 'US' });
    expect(tables.atlas_statement_links.map((row) => row.account_id)).toEqual([us]);
  });

  it('reports links with pending counts and the processing log, and unlinks', async () => {
    const { db, tables } = fakeDb(accounts, { status: 'oversell', symbol: 'UHS', tradeDate: '2026-11-19' });
    tables.atlas_statement_links.push({ line_user_id: owner, account_id: us, broker: 'cathay', market: 'US' });
    tables.atlas_statement_trades.push({ line_user_id: owner, broker: 'cathay', market: 'US', applied_at: null },
      { line_user_id: owner, broker: 'cathay', market: 'TW', applied_at: null });
    tables.atlas_statement_messages.push({ line_user_id: owner, broker: 'cathay', received_at: '2026-11-19T12:00:00Z',
      report_date: '2026-11-19', status: 'ok', trades: 2, processed_at: '2026-11-19T13:00:00Z' });
    const status = await handle(db, owner, owner, { action: 'holdings.statement.status' });
    expect(status.links).toEqual([{ accountId: us, broker: 'cathay', market: 'US', pending: 1 }]);
    expect(status.unlinkedPending).toBe(1);
    expect((status.messages as Row[])[0]).toMatchObject({ reportDate: '2026-11-19', status: 'ok', trades: 2 });
    expect(await handle(db, owner, owner, { action: 'holdings.statement.apply', accountId: us }))
      .toEqual({ status: 'oversell', applied: 0, snapshots: 0, missingCost: 0, symbol: 'UHS', tradeDate: '2026-11-19' });
    expect(await handle(db, owner, owner, { action: 'holdings.statement.unlink', accountId: us })).toEqual({ unlinked: true });
    expect(tables.atlas_statement_links).toEqual([]);
  });
});

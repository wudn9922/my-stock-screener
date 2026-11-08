import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
  createMexcFixture,
  MEXC_ACCOUNT,
  MEXC_USER,
  OTHER_USER,
  TEST_KEY,
  TEST_SECRET,
} from './fixtures/mexcHarness';
import { assetsPath, futuresResponses } from './fixtures/mexc/futures';

const read = { action: 'holdings.mexc.futures', accountId: MEXC_ACCOUNT.id };
async function connected() {
  const f = createMexcFixture();
  f.setFutures(futuresResponses());
  const request = f.handler();
  expect(
    (
      await request({
        action: 'holdings.mexc.connect',
        account: MEXC_ACCOUNT,
        apiKey: TEST_KEY,
        apiSecret: TEST_SECRET,
        readOnlyConfirmed: true,
        expectedRevision: null,
      })
    ).status,
  ).toBe(200);
  return { ...f, request };
}
describe('independent read-only futures action', () => {
  it('reads only futures, preserves every spot document/connection field and keeps data out of RPC payloads', async () => {
    const f = await connected();
    const documents = structuredClone(f.documents),
      connections = structuredClone(f.connections);
    const spotQuota = structuredClone(f.reservations),
      start = f.upstream.length;
    const response = await f.request(read);
    expect(response.status).toBe(200);
    expect(response.body.data.futures.positions).toHaveLength(2);
    expect(response.body.data.fetchedAt).toBe(new Date(f.now()).toISOString());
    expect(Object.keys(response.body.data).sort()).toEqual(['fetchedAt', 'futures']);
    expect(f.documents).toEqual(documents);
    expect(f.connections).toEqual(connections);
    expect(f.reservations).toEqual(spotQuota);
    expect(f.upstream.slice(start).map((c) => new URL(c.url).pathname)).not.toContain('/api/v3/account');
    expect(f.upstream.slice(start).map((c) => new URL(c.url).pathname)).not.toContain('/api/v3/ticker/price');
    expect(JSON.stringify(f.calls)).not.toMatch(/BTC_USDT|equity|unrealized/);
    expect(JSON.stringify(response.body)).not.toMatch(/credential|test-mexc/);
  });
  it('enforces a separate cross-handler 30-second quota without consuming spot sync quota', async () => {
    const f = await connected();
    expect((await f.request(read)).status).toBe(200);
    expect((await f.handler()(read)).status).toBe(429);
    f.advance(30000);
    expect((await f.handler()(read)).status).toBe(200);
    // The independent read did not claim the still-available spot quota.
    expect(
      (
        await f.request({
          action: 'holdings.mexc.sync',
          accountId: MEXC_ACCOUNT.id,
        })
      ).status,
    ).toBe(200);
  });
  it.each(['none', 'device'] as const)('requires cloud authorization for %s', async (mode) => {
    const f = createMexcFixture(mode);
    expect((await f.handler()(read)).status).toBe(403);
    expect(f.calls).toEqual([]);
    expect(f.upstream).toEqual([]);
  });
  it('does not read another owner’s ciphertext', async () => {
    const f = await connected();
    f.grants.set(OTHER_USER, 'cloud');
    const before = f.upstream.length;
    expect((await f.handler(OTHER_USER)(read)).status).toBe(409);
    expect(f.upstream).toHaveLength(before);
  });
  it('rechecks cloud access after upstream I/O', async () => {
    const f = await connected();
    f.onUpstream((path) => {
      if (path === assetsPath) f.grants.set(MEXC_USER, 'device');
    });
    expect((await f.request(read)).status).toBe(403);
  });
  it.each(['disconnect', 'key-change'])('rejects an in-flight read after %s', async (change) => {
    const f = await connected();
    const before = structuredClone(f.documents);
    f.onUpstream((path) => {
      if (path !== assetsPath) return;
      if (change === 'disconnect') f.connections.clear();
      else for (const c of f.connections.values()) c.credential = { data: 'changed' };
    });
    expect((await f.request(read)).status).toBe(409);
    expect(f.documents).toEqual(before);
  });
  it('returns unavailable without modifying spot state when futures fails', async () => {
    const f = await connected(),
      before = structuredClone(f.connections);
    f.setFutures({ ...futuresResponses(), [assetsPath]: null });
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      const result = await f.request(read);
      expect(result.status).toBe(200);
      expect(result.body.data.futures).toEqual({ status: 'unavailable' });
      expect(f.connections).toEqual(before);
    } finally {
      warn.mockRestore();
    }
  });
  it('uses a service-role-only SQL quota and never updates spot storage', () => {
    const sql = readFileSync(
      new URL('../../supabase/manual/atlas_mexc_futures_read.sql', import.meta.url),
      'utf8',
    );
    expect(sql).toContain('enable row level security');
    expect(sql).toContain('from public,anon,authenticated');
    expect(sql).toContain('to service_role');
    expect(sql).toContain('on delete cascade');
    expect(sql).toContain("interval '30 seconds'");
    expect(sql).toContain('pg_advisory_xact_lock');
    expect(sql).not.toMatch(
      /(?:insert into|update|delete from)\s+(?:public\.atlas_hold|atlas_private\.atlas_mexc_(?:connections|rate_limits))/i,
    );
  });
});

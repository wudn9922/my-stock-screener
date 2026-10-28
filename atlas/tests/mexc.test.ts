import { createHmac } from 'node:crypto';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  assertReadOnlyKey,
  decryptMexcCredentials,
  encryptMexcCredentials,
  handleMexc,
  mexcDecimal,
  mexcSignature,
  parseMexcBalances,
} from '../../supabase/functions/user-settings/mexc';
import { validateMexcState } from '../../supabase/functions/user-settings/mexcContract';
import {
  createMexcFixture,
  MASTER,
  MEXC_ACCOUNT,
  MEXC_USER,
  OTHER_USER,
  TEST_KEY,
  TEST_SECRET,
} from './fixtures/mexcHarness';

const connect = {
  action: 'holdings.mexc.connect',
  account: MEXC_ACCOUNT,
  apiKey: TEST_KEY,
  apiSecret: TEST_SECRET,
  readOnlyConfirmed: true,
  expectedRevision: null,
};
const costs = { BTC: '50000', ETH: '3000', USDT: '1' };
async function connected() {
  const fixture = createMexcFixture(),
    request = fixture.handler();
  const response = await request(connect);
  expect(response.status).toBe(200);
  return { ...fixture, request, connection: response.body.data.connections[0] };
}
afterEach(() => vi.useRealTimers());

describe('MEXC cryptography and response validation', () => {
  it('uses HMAC SHA256 over the exact encoded query, with lowercase hexadecimal', async () => {
    const query = 'accessKey=test-key&recvWindow=5000&timestamp=1793181600000';
    expect(await mexcSignature(TEST_SECRET, query)).toBe(createHmac('sha256', TEST_SECRET).update(query).digest('hex'));
  });
  it('encrypts both credentials with randomized AES-GCM, and binds ciphertext to owner/account', async () => {
    const bytes = Uint8Array.from(atob(MASTER), (c) => c.charCodeAt(0));
    const key = await crypto.subtle.importKey('raw', bytes, 'AES-GCM', false, ['encrypt', 'decrypt']);
    const data = { apiKey: TEST_KEY, apiSecret: TEST_SECRET };
    const a = await encryptMexcCredentials(data, key, MEXC_USER, MEXC_ACCOUNT.id),
      b = await encryptMexcCredentials(data, key, MEXC_USER, MEXC_ACCOUNT.id);
    expect(a).not.toEqual(b);
    expect(JSON.stringify(a)).not.toMatch(/test-mexc/);
    expect(await decryptMexcCredentials(a, key, MEXC_USER, MEXC_ACCOUNT.id)).toEqual(data);
    await expect(decryptMexcCredentials(a, key, OTHER_USER, MEXC_ACCOUNT.id)).rejects.toThrow('無法驗證');
    await expect(decryptMexcCredentials(a, key, MEXC_USER, '22222222-2222-4222-8222-222222222222')).rejects.toThrow();
    const tampered = { ...a, data: (a.data[0] === 'A' ? 'B' : 'A') + a.data.slice(1) };
    await expect(decryptMexcCredentials(tampered, key, MEXC_USER, MEXC_ACCOUNT.id)).rejects.toThrow();
  });
  it('sums free and locked exactly, ignores zero balances and never double-counts available', () => {
    expect(
      parseMexcBalances({
        accountType: 'SPOT',
        balances: [
          { asset: 'BTC', free: '0.100000000000000001', locked: '0.200000000000000002', available: '999' },
          { asset: 'ZERO-NOT-SUPPORTED', free: '0.0000', locked: '0' },
        ],
      }),
    ).toEqual([
      {
        asset: 'BTC',
        symbol: 'BTC/USDT',
        free: '0.100000000000000001',
        locked: '0.200000000000000002',
        quantity: '0.300000000000000003',
      },
    ]);
    expect(mexcDecimal('001.230000000000000000000')).toBe('1.23');
  });
  it.each([
    null,
    {},
    { accountType: 'SPOT', balances: null },
    { accountType: 'CONTRACT', balances: [] },
    { accountType: 'SPOT', balances: [{ asset: 'BTC', free: '-1', locked: '0' }] },
    { accountType: 'SPOT', balances: [{ asset: 'BTC', free: '0.1234567890123456789', locked: '0' }] },
    {
      accountType: 'SPOT',
      balances: [
        { asset: 'BTC', free: '1', locked: '0' },
        { asset: 'BTC', free: '1', locked: '0' },
      ],
    },
  ])('rejects malformed balances without creating empty holdings: %j', (input) =>
    expect(() => parseMexcBalances(input)).toThrow(),
  );
  it.each([
    'SPOT_ACCOUNT_READ,SPOT_DEAL_WRITE',
    'SPOT_ACCOUNT_READ,SPOT_WITHDRAW_WRITE',
    'SPOT_ACCOUNT_READ,FUTURE_UNKNOWN_PERMISSION',
    'SPOT_DEAL_READ',
    '',
  ])('rejects unverified/write permissions: %s', (permissions) => {
    expect(() =>
      assertReadOnlyKey({ accessKey: TEST_KEY, status: 'VALID', remainingValidity: 60, permissions }, TEST_KEY),
    ).toThrow();
  });
  it('does not confuse account canTrade/canWithdraw flags with the API key permission', () => {
    expect(() =>
      assertReadOnlyKey(
        {
          apikey: TEST_KEY,
          status: 'VALID',
          remainingValidity: -999,
          permissions: 'SPOT_ACCOUNT_READ,SPOT_DEAL_READ',
          canTrade: true,
          canWithdraw: true,
        },
        TEST_KEY,
      ),
    ).not.toThrow();
    expect(() =>
      assertReadOnlyKey(
        { apikey: TEST_KEY, status: 'VALID', remainingValidity: 0, permissions: 'SPOT_ACCOUNT_READ' },
        TEST_KEY,
      ),
    ).toThrow();
  });
});

describe('actual user-settings handler with fake MEXC and relational storage', () => {
  it.each(['none', 'device'] as const)(
    'denies all MEXC actions for %s before database/private API access',
    async (mode) => {
      const fixture = createMexcFixture(mode),
        request = fixture.handler();
      for (const action of ['status', 'connect', 'sync', 'apply', 'disconnect'])
        expect(
          (await request({ ...connect, action: 'holdings.mexc.' + action, accountId: MEXC_ACCOUNT.id })).status,
        ).toBe(403);
      expect(fixture.calls).toEqual([]);
      expect(fixture.upstream).toEqual([]);
    },
  );
  it('rejects invalid identity and missing setup without any MEXC request', async () => {
    const fixture = createMexcFixture();
    expect((await fixture.handler(MEXC_USER, false)(connect)).status).toBe(401);
    await expect(
      handleMexc(fixture.db, MEXC_USER, connect, undefined, async () => {}, fixture.mexcFetch),
    ).rejects.toThrow('尚未啟用');
    expect(fixture.upstream).toEqual([]);
  });
  it('connects with GET-only signed requests and persists only encrypted keys plus the last four characters', async () => {
    const f = await connected();
    const response = await f.request({ action: 'holdings.mexc.status' });
    const serialized = JSON.stringify(response.body);
    expect(serialized).not.toContain(TEST_KEY);
    expect(serialized).not.toContain(TEST_SECRET);
    expect(serialized).not.toMatch(/credential|upstream-echo|PRIVATE-DROP/);
    expect(f.connection.keyLast4).toBe('9876');
    expect(f.connection.needsReview).toBe(true);
    expect(f.connection.balances.find((b: { asset: string }) => b.asset === 'BTC').quantity).toBe('0.12');
    expect(f.documents.get(MEXC_USER)!.accounts[0]!.holdings).toEqual([]);
    expect(JSON.stringify([...f.connections.values()])).not.toContain(TEST_SECRET);
    expect(JSON.stringify(f.calls)).not.toContain(TEST_KEY);
    expect(JSON.stringify(f.calls)).not.toContain(TEST_SECRET);
    for (const { url, init } of f.upstream) {
      expect(new URL(url).origin).toBe('https://api.mexc.com');
      expect(init?.method).toBe('GET');
      expect(init?.redirect).toBe('error');
      expect(url).not.toContain(TEST_SECRET);
      const query = new URL(url).searchParams;
      if (query.has('signature')) {
        const signature = query.get('signature');
        query.delete('signature');
        expect(signature).toBe(createHmac('sha256', TEST_SECRET).update(query.toString()).digest('hex'));
      }
    }
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(f.errorLogs).toEqual([]);
  });
  it('requires initial costs, preserves average on decreases, and stages increases without false profit', async () => {
    const f = await connected();
    const invalid = await f.request({
      action: 'holdings.mexc.apply',
      accountId: MEXC_ACCOUNT.id,
      snapshotId: f.connection.snapshotId,
      expectedRevision: f.connection.holdingRevision,
      costs: { BTC: '1' },
    });
    expect(invalid.status).toBe(400);
    const apply = await f.request({
      action: 'holdings.mexc.apply',
      accountId: MEXC_ACCOUNT.id,
      snapshotId: f.connection.snapshotId,
      expectedRevision: f.connection.holdingRevision,
      costs,
    });
    expect(apply.status).toBe(200);
    expect(apply.body.data.connections[0].needsReview).toBe(false);
    const old = f.documents.get(MEXC_USER)!;
    f.advance();
    f.setBalances([{ asset: 'BTC', free: '0.05', locked: '0' }]);
    const decrease = await f.request({ action: 'holdings.mexc.sync', accountId: MEXC_ACCOUNT.id });
    expect(decrease.status).toBe(200);
    expect(decrease.body.data.document.accounts[0].holdings).toMatchObject([
      { symbol: 'BTC/USDT', quantity: '0.05', averageCost: '50000', source: 'api' },
    ]);
    expect(decrease.body.data.connections[0].quotes['BTC/USDT']).toMatchObject({ price: '65000', previous: null });
    f.advance();
    f.setBalances([{ asset: 'BTC', free: '0.1', locked: '0' }]);
    const increase = await f.request({ action: 'holdings.mexc.sync', accountId: MEXC_ACCOUNT.id });
    expect(increase.body.data.connections[0].needsReview).toBe(true);
    expect(increase.body.data.document.accounts[0].holdings[0].quantity).toBe('0.05');
    expect(old.accounts[0]!.holdings).toHaveLength(3);
  });
  it('clears a genuinely empty source snapshot after confirmed holdings and never treats malformed data as empty', async () => {
    const f = await connected();
    await f.request({
      action: 'holdings.mexc.apply',
      accountId: MEXC_ACCOUNT.id,
      snapshotId: f.connection.snapshotId,
      expectedRevision: 1,
      costs,
    });
    f.advance();
    f.setFailure('malformed');
    expect((await f.request({ action: 'holdings.mexc.sync', accountId: MEXC_ACCOUNT.id })).status).toBe(502);
    expect(f.documents.get(MEXC_USER)!.accounts[0]!.holdings).toHaveLength(3);
    f.advance();
    f.setFailure('none');
    f.setBalances([]);
    const empty = await f.request({ action: 'holdings.mexc.sync', accountId: MEXC_ACCOUNT.id });
    expect(empty.body.data.document.accounts[0].holdings).toEqual([]);
  });
  it('enforces rate limits across requests and persists upstream Retry-After', async () => {
    const f = await connected(),
      count = f.upstream.length;
    expect((await f.request({ action: 'holdings.mexc.sync', accountId: MEXC_ACCOUNT.id })).status).toBe(429);
    expect(f.upstream).toHaveLength(count);
    f.advance();
    f.setFailure('rate');
    const upstreamRate = await f.request({ action: 'holdings.mexc.sync', accountId: MEXC_ACCOUNT.id });
    expect(upstreamRate.status).toBe(429);
    expect(upstreamRate.headers.get('Retry-After')).toBe('120');
    f.advance(31000);
    const before = f.upstream.length;
    expect((await f.request({ action: 'holdings.mexc.sync', accountId: MEXC_ACCOUNT.id })).status).toBe(429);
    expect(f.upstream).toHaveLength(before);
  });
  it.each(['write-key', 'bad-key', 'network', 'redirect', 'missing-rpc', 'db-detail'] as const)(
    'keeps credentials and private error details out of logs/responses for %s',
    async (failure) => {
      const fixture = createMexcFixture();
      fixture.setFailure(failure);
      const result = await fixture.handler()(connect);
      expect(result.status).toBeGreaterThanOrEqual(400);
      expect(fixture.connections.size).toBe(0);
      expect(JSON.stringify(result.body)).not.toMatch(/test-mexc|PRIVATE|upstream-echo|api\.mexc/);
      expect(fixture.errorLogs).toEqual([]);
    },
  );
  it('binds owner from the verified identity, hides other accounts and deletes only own keys', async () => {
    const f = await connected();
    f.grants.set(OTHER_USER, 'cloud');
    const other = f.handler(OTHER_USER);
    expect((await other({ action: 'holdings.mexc.status', owner_id: MEXC_USER })).body.data.connections).toEqual([]);
    expect(
      (await other({ action: 'holdings.mexc.disconnect', accountId: MEXC_ACCOUNT.id, owner_id: MEXC_USER })).status,
    ).toBe(409);
    const doc = f.documents.get(MEXC_USER);
    expect((await f.request({ action: 'holdings.mexc.disconnect', accountId: MEXC_ACCOUNT.id })).status).toBe(200);
    expect(f.connections.size).toBe(0);
    expect(f.documents.get(MEXC_USER)).toEqual(doc);
  });
  it('does not resurrect a disconnected key when an upstream sync finishes late', async () => {
    const f = await connected();
    f.advance();
    f.onUpstream(async (path) => {
      if (path === '/api/v3/account')
        await f.request({ action: 'holdings.mexc.disconnect', accountId: MEXC_ACCOUNT.id });
    });
    const late = await f.request({ action: 'holdings.mexc.sync', accountId: MEXC_ACCOUNT.id });
    expect(late.status).toBe(409);
    expect(f.connections.size).toBe(0);
  });
  it('rechecks grants after upstream I/O and preserves newer imported costs on a revision race', async () => {
    const f = await connected();
    f.advance();
    f.onUpstream((path) => {
      if (path === '/api/v3/account') f.grants.set(MEXC_USER, 'device');
    });
    expect(
      (await f.request({ action: 'holdings.mexc.sync', accountId: MEXC_ACCOUNT.id })).status,
    ).toBeGreaterThanOrEqual(400);
    expect(f.documents.get(MEXC_USER)!.accounts[0]!.revision).toBe(1);
    f.grants.set(MEXC_USER, 'cloud');
    f.advance();
    f.onUpstream((path) => {
      if (path === '/api/v3/account') f.documents.get(MEXC_USER)!.accounts[0]!.revision++;
    });
    expect((await f.request({ action: 'holdings.mexc.sync', accountId: MEXC_ACCOUNT.id })).status).toBe(409);
  });
  it('rejects stale cost confirmation snapshots and does not return private columns even if extra fields exist', async () => {
    const f = await connected();
    expect(
      (
        await f.request({
          action: 'holdings.mexc.apply',
          accountId: MEXC_ACCOUNT.id,
          snapshotId: crypto.randomUUID(),
          expectedRevision: 1,
          costs,
        })
      ).status,
    ).toBe(409);
    const normalized = validateMexcState({
      connections: [{ ...f.connection, credential: TEST_SECRET, apiKey: TEST_KEY }],
      retryAt: null,
      configured: true,
    });
    expect(JSON.stringify(normalized)).not.toContain(TEST_SECRET);
    expect(JSON.stringify(normalized)).not.toContain(TEST_KEY);
  });
});

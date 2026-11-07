import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  assertReadOnlyKey,
  MexcError,
  parseMexcBalances,
  quotesFrom,
  readMexc,
} from '../../supabase/functions/user-settings/mexc';
import { MEXC_ERRORS } from '../../supabase/functions/user-settings/mexcContract';
import keyInfo from './fixtures/mexc/api-key-info.official.json';
import account from './fixtures/mexc/account.official.json';
import ticker from './fixtures/mexc/ticker.official.json';
import { createMexcFixture, MEXC_ACCOUNT, MEXC_USER, TEST_KEY, TEST_SECRET } from './fixtures/mexcHarness';
import { mexcKeyAgeNotice } from '../src/holdings/mexcStatus';

const now = Date.parse('2026-11-06T02:00:00Z');
const forbidden = [
  TEST_KEY,
  TEST_SECRET,
  MEXC_USER,
  'PRIVATE-ASSET',
  '987654321.123456789',
  'signature=',
  'api.mexc.com',
  'NBNTEST',
  '1111078',
];
let warnings: string[];
beforeEach(() => {
  warnings = [];
  vi.spyOn(console, 'warn').mockImplementation((line: string) => warnings.push(line));
});
afterEach(() => {
  const text = warnings.join('\n');
  for (const value of forbidden) expect(text).not.toContain(value);
  for (const line of warnings) {
    const event = JSON.parse(line);
    if (event.reason === 'asset-skipped') {
      expect(Object.keys(event).sort()).toEqual(['classes', 'count', 'reason', 'reasons', 'step']);
      expect(event.step).toBe('parse-balances');
      expect(Number.isSafeInteger(event.count) && event.count > 0).toBe(true);
      expect(Object.keys(event.reasons).sort()).toEqual(['asset-duplicate', 'asset-invalid']);
      expect(event.reasons['asset-invalid'] + event.reasons['asset-duplicate']).toBe(event.count);
      expect(
        event.classes.every((c: string) =>
          ['lowercase', 'symbol', 'too-long', 'empty', 'non-string', 'duplicate'].includes(c),
        ),
      ).toBe(true);
      continue;
    }

    expect(
      Object.keys(event).every((key) =>
        ['step', 'status', 'topLevelKeys', 'code', 'msg', 'reason'].includes(key),
      ),
    ).toBe(true);
    expect(['apiKeyInfo', 'account', 'ticker', 'parse-balances', 'parse-quotes', 'assert-key']).toContain(
      event.step,
    );
  }
  vi.restoreAllMocks();
});
const fetchResponse = (body: unknown, status = 200) =>
  vi.fn<typeof fetch>(async () => Response.json(body, { status }));
const read = (
  fetcher: typeof fetch,
  path: '/api/v3/apiKeyInfo' | '/api/v3/account' | '/api/v3/ticker/price' = '/api/v3/apiKeyInfo',
) =>
  readMexc(
    path,
    fetcher,
    new AbortController().signal,
    now,
    path.endsWith('price') ? undefined : { apiKey: TEST_KEY, apiSecret: TEST_SECRET },
  );
const event = () => {
  expect(warnings).toHaveLength(1);
  return JSON.parse(warnings[0]!);
};

describe('MEXC unsupported asset names', () => {
  it('normalizes mixed-case names and matches uppercase USDT tickers without warnings', () => {
    const parsed = parseMexcBalances({
      accountType: 'SPOT',
      balances: [
        { asset: 'USDe', free: '2', locked: '0.5' },
        { asset: 'stETH', free: '3', locked: '0' },
        { asset: 'ZERO-UNSUPPORTED', free: '0', locked: '0' },
      ],
    });
    expect(parsed.skippedAssets).toBe(0);
    expect(parsed.balances).toMatchObject([
      { asset: 'STETH', symbol: 'STETH/USDT', quantity: '3' },
      { asset: 'USDE', symbol: 'USDE/USDT', quantity: '2.5' },
    ]);
    expect(
      quotesFrom(
        [
          { symbol: 'USDEUSDT', price: '1.01' },
          { symbol: 'STETHUSDT', price: '3500' },
        ],
        parsed.balances,
        now,
      ),
    ).toMatchObject({
      'USDE/USDT': { price: '1.01' },
      'STETH/USDT': { price: '3500' },
    });
    expect(warnings).toEqual([]);
  });

  it('skips unsupported and normalized duplicate names, counts rows once and logs only safe classes', () => {
    const names = ['USDe', 'USDE', 'PRIVATE-ASSET', 'VERYLONGPRIVATECOIN123456789'];
    const parsed = parseMexcBalances({
      accountType: 'SPOT',
      balances: names.map((asset) => ({
        asset,
        free: '987654321.123456789',
        locked: '0',
      })),
    });
    expect(parsed.skippedAssets).toBe(3);
    expect(parsed.balances).toHaveLength(1);
    expect(parsed.balances[0]).toMatchObject({
      asset: 'USDE',
      quantity: '987654321.123456789',
    });
    expect(event()).toEqual({
      step: 'parse-balances',
      reason: 'asset-skipped',
      count: 3,
      reasons: { 'asset-invalid': 2, 'asset-duplicate': 1 },
      classes: ['duplicate', 'symbol', 'too-long'],
    });
    for (const name of names) expect(warnings.join('\n')).not.toContain(name);
  });

  it('retains the first accepted balance instead of adding a normalized duplicate, ignoring zero rows', () => {
    const parsed = parseMexcBalances({
      accountType: 'SPOT',
      balances: [
        { asset: 'USDE', free: '0', locked: '0' },
        { asset: 'USDe', free: '2', locked: '0' },
        { asset: 'usde', free: '99', locked: '0' },
        { asset: 'USDE', free: '0', locked: '0' },
      ],
    });
    expect(parsed.skippedAssets).toBe(1);
    expect(parsed.balances).toMatchObject([{ asset: 'USDE', quantity: '2' }]);
    expect(event()).toMatchObject({
      reasons: { 'asset-invalid': 0, 'asset-duplicate': 1 },
      classes: ['duplicate', 'lowercase'],
    });
  });

  it.each(['connect', 'sync'] as const)(
    'successfully returns an empty snapshot when %s skips every name',
    async (operation) => {
      const f = createMexcFixture(),
        request = f.handler();
      const connect = {
        action: 'holdings.mexc.connect',
        account: MEXC_ACCOUNT,
        apiKey: TEST_KEY,
        apiSecret: TEST_SECRET,
        readOnlyConfirmed: true,
        expectedRevision: null,
      };
      if (operation === 'sync') {
        expect((await request(connect)).status).toBe(200);
        f.advance();
      }
      f.setBalances([
        { asset: 'PRIVATE-ASSET', free: '1', locked: '0' },
        { asset: 'VERYLONGPRIVATECOIN123456789', free: '2', locked: '0' },
      ]);
      const response = await request(
        operation === 'connect'
          ? connect
          : {
              action: 'holdings.mexc.sync',
              accountId: MEXC_ACCOUNT.id,
            },
      );
      expect(response.status).toBe(200);
      expect(response.body.data.skippedAssets).toBe(2);
      expect(response.body.data.connections[0].balances).toEqual([]);
      expect(response.body.data.document.accounts[0].holdings).toEqual([]);
      expect(
        f.calls
          .filter((call) => call.parameters.operation === 'finish')
          .every((call) => !Object.hasOwn(call.parameters.payload as object, 'skippedAssets')),
      ).toBe(true);
      expect((await request({ action: 'holdings.mexc.status' })).body.data).not.toHaveProperty(
        'skippedAssets',
      );
      expect(event()).toMatchObject({
        count: 2,
        reasons: { 'asset-invalid': 2, 'asset-duplicate': 0 },
      });
    },
  );

  it('returns other holdings and their quotes on connect and sync, exposing only a skipped count', async () => {
    const f = createMexcFixture(),
      request = f.handler();
    f.setBalances([
      { asset: 'bTc', free: '1', locked: '0' },
      { asset: 'BTC', free: '9', locked: '0' },
      { asset: 'PRIVATE-ASSET', free: '1', locked: '0' },
    ]);
    const linked = await request({
      action: 'holdings.mexc.connect',
      account: MEXC_ACCOUNT,
      apiKey: TEST_KEY,
      apiSecret: TEST_SECRET,
      readOnlyConfirmed: true,
      expectedRevision: null,
    });
    expect(linked.status).toBe(200);
    expect(linked.body.data.skippedAssets).toBe(2);
    expect(linked.body.data.connections[0].balances).toMatchObject([{ asset: 'BTC', quantity: '1' }]);
    expect(linked.body.data.connections[0].quotes['BTC/USDT'].price).toBe('65000');
    expect(JSON.stringify(linked.body)).not.toContain('PRIVATE-ASSET');
    f.advance();
    f.setBalances([{ asset: 'ETH', free: '1', locked: '0' }]);
    const synced = await request({
      action: 'holdings.mexc.sync',
      accountId: MEXC_ACCOUNT.id,
    });
    expect(synced.status).toBe(200);
    expect(synced.body.data.skippedAssets).toBe(0);
    expect(synced.body.data.connections[0].balances).toMatchObject([{ asset: 'ETH' }]);
  });
});

describe('MEXC source diagnostics disclose only approved metadata', () => {
  it.each(['/api/v3/apiKeyInfo', '/api/v3/account', '/api/v3/ticker/price'] as const)(
    'logs the failing HTTP step %s without response values',
    async (path) => {
      await expect(
        read(
          fetchResponse(
            {
              code: 10072,
              msg: 'Api key info invalid',
              [TEST_KEY]: TEST_SECRET,
              balances: ['PRIVATE-ASSET'],
            },
            400,
          ),
          path,
        ),
      ).rejects.toBeInstanceOf(MexcError);
      expect(event()).toMatchObject({
        step: path.endsWith('apiKeyInfo') ? 'apiKeyInfo' : path.endsWith('account') ? 'account' : 'ticker',
        status: 400,
        code: 10072,
        msg: 'Api key info invalid',
        reason: 'upstream-code',
      });
    },
  );
  it('redacts unexpected message and property names even when upstream echoes credentials', async () => {
    await expect(
      read(
        fetchResponse(
          { code: 700002, msg: `${TEST_KEY} ${TEST_SECRET} signature=private`, [MEXC_USER]: 'PRIVATE-ASSET' },
          400,
        ),
      ),
    ).rejects.toMatchObject({ code: 'credentials' });
    expect(event().msg).toBe('[redacted]');
  });
  it('reports transport/timeout without exception text, signed URL or credentials', async () => {
    await expect(
      read(async () => {
        throw new Error(TEST_SECRET + ' https://api.mexc.com/?signature=private');
      }),
    ).rejects.toMatchObject({ code: 'source' });
    expect(event()).toMatchObject({ status: null, reason: 'network-or-timeout', topLevelKeys: [] });
  });
  it.each([10072, 602, 700001, 700002, 730706])(
    'maps authentication error %s to the fixed invalid-key message',
    async (code) => {
      await expect(read(fetchResponse({ code, msg: 'private ' + TEST_KEY }, 200))).rejects.toThrow(
        MEXC_ERRORS.credentials,
      );
      expect(event().code).toBe(code);
    },
  );
  it.each([
    ['invalid-json-or-stream', () => new Response(TEST_SECRET, { status: 200 })],
    ['response-size-or-empty', () => new Response(null, { status: 200 })],
    [
      'response-size-or-empty',
      () => new Response(TEST_SECRET, { headers: { 'Content-Length': String(2 * 1024 * 1024 + 1) } }),
    ],
    ['upstream-rate-limit', () => Response.json({ code: 429, msg: 'Too Many Requests' }, { status: 429 })],
    ['upstream-http', () => Response.json({ private: TEST_SECRET }, { status: 503 })],
  ] as const)('logs bounded response failure %s once', async (reason, response) => {
    await expect(read(async () => response())).rejects.toBeInstanceOf(MexcError);
    expect(event().reason).toBe(reason);
  });
  it('logs a streamed oversize without consuming or echoing the body', async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(2 * 1024 * 1024 + 1));
        controller.close();
      },
    });
    await expect(read(async () => new Response(body))).rejects.toBeInstanceOf(MexcError);
    expect(event().reason).toBe('invalid-json-or-stream');
  });
  it.each([
    [
      'parse-balances',
      () => parseMexcBalances({ accountType: 'SPOT', balances: null, private: TEST_SECRET }, 200),
    ],
    [
      'parse-balances',
      () =>
        parseMexcBalances(
          { accountType: 'SPOT', balances: [{ asset: 'PRIVATE-ASSET', free: 'bad', locked: '0' }] },
          200,
        ),
    ],
    ['parse-quotes', () => quotesFrom({}, [], now, 200)],
    [
      'parse-quotes',
      () =>
        quotesFrom(
          [{ symbol: 'BTCUSDT', price: 'bad' }],
          [{ asset: 'BTC', symbol: 'BTC/USDT', quantity: '1', free: '1', locked: '0' }],
          now,
          200,
        ),
    ],
    [
      'assert-key',
      () => assertReadOnlyKey({ ...keyInfo, accessKey: TEST_KEY, status: 'FROZEN' }, TEST_KEY, 200),
    ],
    ['assert-key', () => assertReadOnlyKey({ ...keyInfo, accessKey: TEST_KEY }, TEST_KEY, 200)],
  ] as const)('logs parser boundary %s without values', (step, task) => {
    expect(task).toThrow(MexcError);
    expect(event()).toMatchObject({ step, status: 200 });
  });
});

describe('official Spot V3 examples and explicit success envelopes', () => {
  it('uses actual HTTP envelope top-level keys when a nested parser fails', async () => {
    const response = await read(
      fetchResponse({ code: 0, data: { accountType: 'SPOT', balances: null }, msg: 'success' }),
      '/api/v3/account',
    );
    expect(() => parseMexcBalances(response.value, response.status, response.diagnosticValue)).toThrow(
      MexcError,
    );
    expect(event()).toMatchObject({
      step: 'parse-balances',
      status: 200,
      topLevelKeys: ['code', 'data', 'msg'],
      code: 0,
      msg: 'success',
    });
  });

  it('diagnoses an unexpected internal response without leaking database values', async () => {
    const f = createMexcFixture(),
      original = f.db.rpc.bind(f.db);
    vi.spyOn(f.db, 'rpc').mockImplementation(async (name, parameters) =>
      parameters.operation === 'finish' ? { data: null, error: null } : original(name, parameters),
    );
    const response = await f.handler()({
      action: 'holdings.mexc.connect',
      account: MEXC_ACCOUNT,
      apiKey: TEST_KEY,
      apiSecret: TEST_SECRET,
      readOnlyConfirmed: true,
      expectedRevision: null,
    });
    expect(response.body.error).toBe(MEXC_ERRORS.source);
    expect(event()).toMatchObject({ step: 'account', status: null, reason: 'unexpected-internal-response' });
  });
  it.each([false, true])(
    'accepts documented bare responses and numeric-zero envelope=%s without success logs',
    async (wrapped) => {
      const wrap = (value: unknown) => (wrapped ? { code: 0, data: value, msg: 'success' } : value);
      const info = await read(fetchResponse(wrap(keyInfo)));
      expect(info.value).toEqual(keyInfo); // official example has WRITE permissions: still reject it
      const readOnly = { ...keyInfo, accessKey: TEST_KEY, permissions: 'SPOT_ACCOUNT_READ,SPOT_DEAL_READ' };
      const checked = await read(fetchResponse(wrap(readOnly)));
      expect(() => assertReadOnlyKey(checked.value, TEST_KEY, checked.status)).not.toThrow();
      const response = await read(fetchResponse(wrap(account)), '/api/v3/account');
      expect(
        parseMexcBalances(response.value, response.status).balances.find((b) => b.asset === 'NBNTEST')!.quantity,
      ).toBe('1111111');
      const prices = await read(fetchResponse(wrap(ticker)), '/api/v3/ticker/price');
      expect(
        quotesFrom(
          prices.value,
          [{ asset: 'BTC', symbol: 'BTC/USDT', free: '1', locked: '0', quantity: '1' }],
          now,
          prices.status,
        )['BTC/USDT']!.price,
      ).toBe('6.65');
      expect(warnings).toEqual([]);
    },
  );
  it('preserves positions, successful sync time and encrypted connection on an expired key', async () => {
    const f = createMexcFixture(),
      request = f.handler();
    const linked = await request({
      action: 'holdings.mexc.connect',
      account: MEXC_ACCOUNT,
      apiKey: TEST_KEY,
      apiSecret: TEST_SECRET,
      readOnlyConfirmed: true,
      expectedRevision: null,
    });
    const connection = linked.body.data.connections[0];
    await request({
      action: 'holdings.mexc.apply',
      accountId: MEXC_ACCOUNT.id,
      snapshotId: connection.snapshotId,
      expectedRevision: connection.holdingRevision,
      costs: { BTC: '50000', ETH: '3000', USDT: '1' },
    });
    const positions = structuredClone(f.documents.get(MEXC_USER)),
      saved = structuredClone([...f.connections]);
    f.advance();
    f.setFailure('bad-key');
    const response = await request({ action: 'holdings.mexc.sync', accountId: MEXC_ACCOUNT.id });
    expect(response.body.error).toBe(MEXC_ERRORS.credentials);
    expect(f.documents.get(MEXC_USER)).toEqual(positions);
    expect([...f.connections]).toEqual(saved);
    expect(event().step).toBe('assert-key');
  });
});

describe('MEXC key age reminder', () => {
  it('starts at 80 complete days after connectedAt, without inventing an expiry date', () => {
    const connected = new Date(now - 80 * 86400000).toISOString();
    expect(mexcKeyAgeNotice(connected, now - 1)).toBeUndefined();
    expect(mexcKeyAgeNotice(connected, now)).toBe('MEXC 未綁 IP 的 Key 約 90 天到期，請準備更換');
    expect(mexcKeyAgeNotice('invalid', now)).toBeUndefined();
    expect(mexcKeyAgeNotice(new Date(now + 86400000).toISOString(), now)).toBeUndefined();
  });
});

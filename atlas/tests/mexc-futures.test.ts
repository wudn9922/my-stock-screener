import { createHmac } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { futuresAuth, futuresJson, sourceDecimal } from '../../supabase/functions/user-settings/mexcFutures';
import { validateMexcFutures } from '../../supabase/functions/user-settings/mexcFuturesContract';
import { createMexcFixture, MEXC_ACCOUNT, TEST_KEY, TEST_SECRET } from './fixtures/mexcHarness';
import {
  assetsPath,
  positionsPath,
  detailPath,
  tickerPath,
  futuresResponses,
  manyFuturesResponses,
  publicList,
} from './fixtures/mexc/futures';

const connect = {
  action: 'holdings.mexc.connect',
  account: MEXC_ACCOUNT,
  apiKey: TEST_KEY,
  apiSecret: TEST_SECRET,
  readOnlyConfirmed: true,
  expectedRevision: null,
};
let warnings: string[];
beforeEach(() => {
  warnings = [];
  vi.spyOn(console, 'warn').mockImplementation((line: string) => warnings.push(line));
});
afterEach(() => {
  const text = warnings.join('\n');
  for (const privateValue of [
    TEST_KEY,
    TEST_SECRET,
    'BTC_USDT',
    'ETH_USDT',
    'PRIVATE_USDT',
    '123456789.987654321',
  ])
    expect(text).not.toContain(privateValue);
  for (const line of warnings) {
    const event = JSON.parse(line);
    if (event.step.startsWith('futures-')) {
      expect(
        Object.keys(event).every((key) => ['step', 'status', 'reason', 'code', 'msg'].includes(key)),
      ).toBe(true);
      if (event.msg) expect(['Internal error', 'No permission to access the endpoint']).toContain(event.msg);
    }
  }
  vi.useRealTimers();
  vi.restoreAllMocks();
});
const fixture = () => {
  const f = createMexcFixture();
  f.setFutures(futuresResponses());
  return f;
};

describe('read-only Futures V1', () => {
  it('signs the exact sorted GET parameter string, excluding nulls, with fixed-time headers', async () => {
    const timestamp = 1794000000000;
    const result = await futuresAuth({ apiKey: TEST_KEY, apiSecret: TEST_SECRET }, timestamp, {
      symbol: 'BTC_USDT',
      limit: '20',
      omitted: null,
    });
    expect(result.query).toBe('limit=20&symbol=BTC_USDT');
    expect(result.headers).toEqual({
      ApiKey: TEST_KEY,
      'Request-Time': String(timestamp),
      Signature: createHmac('sha256', TEST_SECRET)
        .update(TEST_KEY + timestamp + result.query)
        .digest('hex'),
    });
  });
  it('preserves numeric JSON decimal precision and converts exponents without accepting invalid signs', () => {
    const value = futuresJson('{"v":0.100000000000000001,"s":"quoted 123","small":1e-8}') as Record<
      string,
      unknown
    >;
    expect(value.v).toBe('0.100000000000000001');
    expect(value.s).toBe('quoted 123');
    expect(sourceDecimal(value.small)).toBe('0.00000001');
    expect(sourceDecimal('-1.25', true)).toBe('-1.25');
    expect(() => sourceDecimal('-1.25')).toThrow();
    expect(() => sourceDecimal('1e100')).toThrow();
    expect(() => sourceDecimal('0.0000000000000000001')).toThrow();
  });
  it('maps long/short and isolated/cross, computes coin size exactly and never persists contract data', async () => {
    const f = fixture(),
      response = await f.handler()(connect);
    expect(response.status).toBe(200);
    const futures = response.body.data.futures;
    expect(futures.status).toBe('ok');
    expect(futures.account).toEqual({
      equity: '1000.12',
      available: '700.12',
      positionMargin: '300',
      unrealized: '-12.34',
    });
    expect(futures.positions).toEqual([
      {
        symbol: 'BTC_USDT',
        side: 'long',
        marginMode: 'isolated',
        contracts: '5',
        size: '0.0005',
        entryPrice: '60000',
        markPrice: '59000',
        leverage: '10',
        margin: '30',
        unrealized: '-0.5',
        liquidationPrice: '54000',
      },
      {
        symbol: 'ETH_USDT',
        side: 'short',
        marginMode: 'cross',
        contracts: '2.5',
        size: '0.025',
        entryPrice: '3500',
        markPrice: '3450',
        leverage: '5',
        margin: '20',
        unrealized: '1.25',
        liquidationPrice: null,
      },
    ]);
    expect(validateMexcFutures(futures)).toEqual(futures);
    expect(JSON.stringify(response.body.data.document)).not.toMatch(/futures|BTC_USDT|unrealized/);
    const finish = f.calls.find((c) => c.parameters.operation === 'finish')!.parameters.payload;
    expect(finish).not.toHaveProperty('futures');
    expect(JSON.stringify(finish)).not.toContain('BTC_USDT');
    expect((await f.handler()({ action: 'holdings.mexc.status' })).body.data).not.toHaveProperty('futures');
    const calls = f.upstream.filter((c) => new URL(c.url).pathname.startsWith('/api/v1/'));
    expect(calls).toHaveLength(6);
    // Two held symbols: only per-symbol public reads, never the unfiltered all-contracts lists.
    const publicCalls = calls.filter((c) => !new URL(c.url).pathname.includes('/private/'));
    expect(publicCalls.map((c) => new URL(c.url).pathname + new URL(c.url).search).sort()).toEqual([
      `${detailPath}?symbol=BTC_USDT`,
      `${detailPath}?symbol=ETH_USDT`,
      `${tickerPath}?symbol=BTC_USDT`,
      `${tickerPath}?symbol=ETH_USDT`,
    ]);
    expect(publicCalls.every((c) => new URL(c.url).searchParams.has('symbol'))).toBe(true);
    for (const c of calls.filter((c) => new URL(c.url).pathname.includes('/private/')))
      expect(new URL(c.url).search).toBe('');
    expect(f.upstream.slice(0, 3).map((c) => new URL(c.url).pathname)).toEqual([
      '/api/v3/apiKeyInfo',
      '/api/v3/account',
      '/api/v3/ticker/price',
    ]);
    for (const { url, init } of calls) {
      expect(new URL(url).origin).toBe('https://api.mexc.com');
      expect(init).toMatchObject({
        method: 'GET',
        redirect: 'error',
        cache: 'no-store',
        referrerPolicy: 'no-referrer',
      });
      const headers = new Headers(init?.headers);
      expect(url).not.toMatch(/signature|accessKey|test-mexc/);
      if (new URL(url).pathname.includes('/private/')) {
        expect(headers.get('ApiKey')).toBe(TEST_KEY);
        expect(headers.get('Request-Time')).toBe(String(f.now()));
        expect(headers.get('Signature')).toBe(
          createHmac('sha256', TEST_SECRET)
            .update(TEST_KEY + f.now())
            .digest('hex'),
        );
      } else {
        expect(headers.has('ApiKey')).toBe(false);
        expect(headers.has('Signature')).toBe(false);
      }
    }
    expect(warnings).toEqual([]);
  });
  it('skips unsupported symbols, ignores closed/zero positions and avoids unnecessary public reads', async () => {
    const f = fixture(),
      source = futuresResponses();
    const p = (source[positionsPath] as Record<string, unknown>[])[0]!;
    source[positionsPath] = [
      { symbol: 'PRIVATE_USDC', holdVol: '123456789.987654321' },
      { symbol: 'TOOLONGPRIVATECONTRACTNAME_USDT' },
      { ...p, state: 3 },
      { ...p, holdVol: '0' },
    ];
    f.setFutures(source);
    const response = await f.handler()(connect);
    expect(response.status).toBe(200);
    expect(response.body.data.futures).toMatchObject({ status: 'ok', positions: [], skippedPositions: 2 });
    expect(f.upstream.filter((c) => new URL(c.url).pathname.startsWith('/api/v1/'))).toHaveLength(2);
    expect(warnings).toEqual([]);
  });
  it('missing CONTRACT_ACCOUNT_READ makes no contract request at all', async () => {
    const f = createMexcFixture(),
      response = await f.handler()(connect);
    expect(response.status).toBe(200);
    expect(response.body.data.futures).toEqual({ status: 'no-permission' });
    expect(f.upstream).toHaveLength(3);
    expect(warnings).toEqual([]);
  });
  it.each(['bad-key', 'malformed', 'network'] as const)(
    'does not start futures after spot %s failure',
    async (failure) => {
      const f = fixture();
      f.setFailure(failure);
      expect((await f.handler()(connect)).status).toBeGreaterThanOrEqual(400);
      expect(f.upstream.some((c) => new URL(c.url).pathname.startsWith('/api/v1/'))).toBe(false);
    },
  );
  it.each([assetsPath, positionsPath, detailPath, tickerPath])(
    'keeps spot successful when %s returns success:false with sensitive error text',
    async (path) => {
      const f = fixture(),
        data = futuresResponses();
      data[path] = () =>
        Response.json({
          success: false,
          code: 500,
          message: `${TEST_KEY} BTC_USDT 123456789.987654321`,
          data: null,
        });
      f.setFutures(data);
      const response = await f.handler()(connect);
      expect(response.status).toBe(200);
      expect(response.body.data.futures).toEqual({ status: 'unavailable' });
      expect(response.body.data.connections[0].balances).toHaveLength(3);
      expect(f.calls.some((c) => c.parameters.operation === 'finish')).toBe(true);
      expect(warnings).toHaveLength(1);
      expect(JSON.parse(warnings[0]!).code).toBe(500);
    },
  );
  it.each([511, 701, 703])('maps official read-permission code %s without failing spot', async (code) => {
    const f = fixture(),
      data = futuresResponses();
    data[positionsPath] = () =>
      Response.json({ success: false, code, message: 'No permission to access the endpoint' });
    f.setFutures(data);
    const response = await f.handler()(connect);
    expect(response.status).toBe(200);
    expect(response.body.data.futures.status).toBe('no-permission');
  });
  it.each(['http', 'network', 'oversize', 'stream-size', 'invalid-json', 'not-true'] as const)(
    'isolates %s futures failures from spot',
    async (failure) => {
      const f = fixture(),
        data = futuresResponses();
      data[assetsPath] = () => {
        if (failure === 'http') return Response.json({ success: false, code: 500 }, { status: 500 });
        if (failure === 'network') throw new Error(TEST_SECRET);
        if (failure === 'oversize')
          return new Response('private', { headers: { 'Content-Length': '2097153' } });
        if (failure === 'stream-size') return new Response(' '.repeat(2097153));
        if (failure === 'invalid-json') return new Response(TEST_SECRET);
        return Response.json({ success: 'true', code: 0, data: [] });
      };
      f.setFutures(data);
      const response = await f.handler()(connect);
      expect(response.status).toBe(200);
      expect(response.body.data.futures.status).toBe('unavailable');
      expect(warnings).toHaveLength(1);
    },
  );
  it('enforces the existing nine-second deadline even if a futures transport never resolves', async () => {
    vi.useFakeTimers();
    const f = fixture(),
      data = futuresResponses();
    let started!: () => void;
    const reached = new Promise<void>((resolve) => {
      started = resolve;
    });
    data[assetsPath] = () => {
      started();
      return new Promise<Response>(() => {});
    };
    f.setFutures(data);
    const response = f.handler()(connect);
    await reached;
    await vi.advanceTimersByTimeAsync(9001);
    const result = await response;
    expect(result.status).toBe(200);
    expect(result.body.data.futures.status).toBe('unavailable');
    expect(result.body.data.connections[0].balances).toHaveLength(3);
    expect(JSON.parse(warnings[0]!).reason).toBe('network-or-timeout');
  });
  it.each([false, undefined, 1])(
    'requires success literally true even with code zero: %j',
    async (success) => {
      const f = fixture(),
        data = futuresResponses();
      const assets = data[assetsPath];
      data[assetsPath] = () => Response.json({ success, code: 0, data: assets });
      f.setFutures(data);
      const result = await f.handler()(connect);
      expect(result.status).toBe(200);
      expect(result.body.data.futures.status).toBe('unavailable');
    },
  );
  it.each(['invalid-state', 'inexact-size', 'missing-mark'] as const)(
    'refuses malformed financial data: %s',
    async (failure) => {
      const f = fixture(),
        data = futuresResponses();
      if (failure === 'invalid-state') (data[positionsPath] as Record<string, unknown>[])[0]!.state = ['1'];
      if (failure === 'inexact-size') {
        (data[positionsPath] as Record<string, unknown>[])[0]!.holdVol = '0.1';
        (data[detailPath] as Record<string, unknown>[])[0]!.contractSize = '0.000000000000000001';
      }
      if (failure === 'missing-mark') data[tickerPath] = [];
      f.setFutures(data);
      const result = await f.handler()(connect);
      expect(result.status).toBe(200);
      expect(result.body.data.futures.status).toBe('unavailable');
    },
  );
  it.each(['im', 'holdVol', 'holdAvgPrice', 'leverage'] as const)(
    'rejects a negative %s rather than displaying invalid financial data',
    async (field) => {
      const f = fixture(),
        data = futuresResponses();
      (data[positionsPath] as Record<string, unknown>[])[0]![field] = '-1';
      f.setFutures(data);
      const response = await f.handler()(connect);
      expect(response.status).toBe(200);
      expect(response.body.data.futures.status).toBe('unavailable');
    },
  );
  it('accepts 200 positions and refuses 201 instead of silently truncating', async () => {
    for (const count of [200, 201]) {
      const f = fixture(),
        data = futuresResponses();
      const p = (data[positionsPath] as unknown[])[0];
      data[positionsPath] = Array.from({ length: count }, () => p);
      f.setFutures(data);
      const response = await f.handler()(connect);
      expect(response.status).toBe(200);
      expect(response.body.data.futures.status).toBe(count === 200 ? 'ok' : 'unavailable');
      if (count === 200) expect(response.body.data.futures.positions).toHaveLength(200);
    }
  });
  it('regression: an all-contracts list over 2 MiB no longer makes the whole block unavailable', async () => {
    const f = fixture(),
      data = futuresResponses();
    const big = 2 * 1024 * 1024 + 4096;
    data[detailPath] = publicList(data[detailPath] as Record<string, unknown>[], big);
    data[tickerPath] = publicList(data[tickerPath] as Record<string, unknown>[], big);
    f.setFutures(data);
    const response = await f.handler()(connect);
    expect(response.status).toBe(200);
    expect(response.body.data.futures.status).toBe('ok');
    expect(response.body.data.futures.positions.map((p: { size: string }) => p.size)).toEqual(['0.0005', '0.025']);
    // The oversized unfiltered list (the production failure) is never requested.
    const unfiltered = f.upstream.filter(
      (c) => [detailPath, tickerPath].includes(new URL(c.url).pathname) && !new URL(c.url).searchParams.has('symbol'),
    );
    expect(unfiltered).toHaveLength(0);
    expect(warnings).toEqual([]);
  });
  it('accepts a per-symbol answer wrapped as a one-row array as well as a single object', async () => {
    const f = fixture();
    f.setFuturesSymbolShape('array');
    const response = await f.handler()(connect);
    expect(response.status).toBe(200);
    expect(response.body.data.futures.status).toBe('ok');
    expect(response.body.data.futures.positions.map((p: { markPrice: string }) => p.markPrice)).toEqual([
      '59000',
      '3450',
    ]);
  });
  it('refuses a per-symbol answer for a different contract', async () => {
    const f = fixture(),
      data = futuresResponses();
    data[tickerPath] = () => Response.json({ success: true, code: 0, data: { symbol: 'OTHER_USDT', fairPrice: '1' } });
    f.setFutures(data);
    const response = await f.handler()(connect);
    expect(response.status).toBe(200);
    expect(response.body.data.futures.status).toBe('unavailable');
    expect(warnings).toHaveLength(1);
    expect(JSON.parse(warnings[0]!)).toMatchObject({ step: 'futures-detail', reason: 'invalid-response' });
  });
  it('caps per-symbol concurrency for 20 distinct symbols and still avoids the full lists', async () => {
    const f = fixture(),
      data = manyFuturesResponses(20);
    let active = 0,
      peak = 0;
    for (const path of [detailPath, tickerPath]) {
      const respond = publicList(data[path] as Record<string, unknown>[], 0);
      data[path] = async (input: RequestInfo | URL) => {
        active++;
        peak = Math.max(peak, active);
        await new Promise((resolve) => setTimeout(resolve, 1));
        active--;
        return respond(input);
      };
    }
    f.setFutures(data);
    const response = await f.handler()(connect);
    expect(response.status).toBe(200);
    expect(response.body.data.futures.status).toBe('ok');
    expect(response.body.data.futures.positions).toHaveLength(20);
    const publicCalls = f.upstream.filter((c) => [detailPath, tickerPath].includes(new URL(c.url).pathname));
    expect(publicCalls).toHaveLength(40);
    expect(publicCalls.every((c) => new URL(c.url).searchParams.has('symbol'))).toBe(true);
    expect(peak).toBeGreaterThan(1);
    expect(peak).toBeLessThanOrEqual(4);
  });
  it('stops the remaining per-symbol lookups after the first failure and logs it once', async () => {
    const f = fixture(),
      data = manyFuturesResponses(12);
    data[tickerPath] = () => Response.json({ success: false, code: 500 }, { status: 500 });
    f.setFutures(data);
    const response = await f.handler()(connect);
    expect(response.status).toBe(200);
    expect(response.body.data.futures.status).toBe('unavailable');
    expect(f.upstream.filter((c) => [detailPath, tickerPath].includes(new URL(c.url).pathname)).length).toBeLessThan(24);
    expect(warnings).toHaveLength(1);
    expect(JSON.parse(warnings[0]!)).toMatchObject({ step: 'futures-detail', reason: 'upstream-http' });
  });
  it('falls back to the two full lists with an 8 MiB cap above 20 distinct symbols', async () => {
    const f = fixture(),
      data = manyFuturesResponses(21);
    const big = 6 * 1024 * 1024;
    data[detailPath] = publicList(data[detailPath] as Record<string, unknown>[], big);
    data[tickerPath] = publicList(data[tickerPath] as Record<string, unknown>[], big);
    f.setFutures(data);
    const response = await f.handler()(connect);
    expect(response.status).toBe(200);
    expect(response.body.data.futures.status).toBe('ok');
    expect(response.body.data.futures.positions).toHaveLength(21);
    const publicCalls = f.upstream.filter((c) => [detailPath, tickerPath].includes(new URL(c.url).pathname));
    expect(publicCalls.map((c) => new URL(c.url).pathname + new URL(c.url).search)).toEqual([detailPath, tickerPath]);
    expect(warnings).toEqual([]);
  });
  it.each(['content-length', 'stream'] as const)(
    'still refuses a full-list fallback over 8 MiB (%s)',
    async (kind) => {
      const f = fixture(),
        data = manyFuturesResponses(21);
      data[detailPath] =
        kind === 'content-length'
          ? () => new Response('{}', { headers: { 'Content-Length': String(8 * 1024 * 1024 + 1) } })
          : publicList(data[detailPath] as Record<string, unknown>[], 8 * 1024 * 1024 + 1);
      f.setFutures(data);
      const response = await f.handler()(connect);
      expect(response.status).toBe(200);
      expect(response.body.data.futures.status).toBe('unavailable');
      expect(warnings).toHaveLength(1);
      expect(JSON.parse(warnings[0]!)).toMatchObject({ step: 'futures-detail', reason: 'response-size-or-empty' });
    },
  );
  it('keeps the 2 MiB cap on per-symbol and private reads', async () => {
    const f = fixture(),
      data = futuresResponses();
    data[tickerPath] = () => new Response(' '.repeat(2 * 1024 * 1024 + 1));
    f.setFutures(data);
    const response = await f.handler()(connect);
    expect(response.status).toBe(200);
    expect(response.body.data.futures.status).toBe('unavailable');
    expect(JSON.parse(warnings[0]!)).toMatchObject({ step: 'futures-detail', reason: 'response-size-or-empty' });
  });
});

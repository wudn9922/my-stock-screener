import { it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { createSecBackend } from '../src/fundamentals/secProxy';
import { SecClient } from '../src/fundamentals/SecClient';
import { financialSchema } from '../src/fundamentals/financialSchema';
function setup() {
  const raw = JSON.parse(readFileSync('tests/fixtures/sec/AAPL.json', 'utf8'));
  const transport = vi.fn(async (url: string) =>
    url.includes('company_tickers') ? { '0': { ticker: 'AAPL', cik_str: 320193 } } : raw,
  );
  const backend = createSecBackend({
    client: new SecClient(transport, 'Atlas Test', 0),
    close: async () => {},
  });
  const request = async (url: string, method = 'GET') => {
    const result = {
      statusCode: 200,
      body: '',
      headers: {} as Record<string, string>,
      setHeader(key: string, value: string) {
        this.headers[key] = value;
      },
      end(value: string) {
        this.body = value;
      },
    };
    const next = vi.fn();
    await backend.middleware(
      { url, method } as IncomingMessage,
      result as unknown as ServerResponse,
      next,
    );
    return { ...result, next };
  };
  return { transport, request };
}
it('SEC backend returns normalized audited data, caches raw requests, and unknown symbols remain empty', async () => {
  const { transport, request } = setup();
  const first = await request('/api/fundamentals?symbol=AAPL&period=quarterly');
  expect(first.statusCode).toBe(200);
  const records = financialSchema.array().parse(JSON.parse(first.body));
  expect(records.at(-1)!.revenue).toBe(109417000000);
  expect(first.body).not.toContain('"facts"');
  expect(first.headers['Cache-Control']).toContain('private');
  await request('/api/fundamentals?symbol=AAPL&period=annual');
  await request('/api/fundamentals?symbol=AAPL&period=quarterly');
  expect(transport).toHaveBeenCalledTimes(2);
  expect((await request('/api/fundamentals?symbol=NOTFOUND&period=quarterly')).body).toBe('[]');
});
it('backend validates method/ticker/period before any upstream request and only owns its route', async () => {
  const { transport, request } = setup();
  expect((await request('/api/fundamentals?symbol=AAPL&period=quarterly', 'POST')).statusCode).toBe(
    405,
  );
  expect((await request('/api/fundamentals?symbol=AAPL&period=monthly')).statusCode).toBe(400);
  expect((await request('/api/fundamentals?symbol=bad%2Fhost&period=annual')).statusCode).toBe(400);
  expect((await request('/api/yahoo?symbol=AAPL')).next).toHaveBeenCalledOnce();
  expect(transport).not.toHaveBeenCalled();
});

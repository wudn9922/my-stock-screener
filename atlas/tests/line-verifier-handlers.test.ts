import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transformSync } from 'esbuild';
import { describe, expect, it, vi } from 'vitest';
import * as deviceSession from '../../supabase/functions/_shared/deviceSession';
import * as lineVerifier from '../../supabase/functions/_shared/lineIdToken';
import { LineHandoffError } from '../../supabase/functions/_shared/lineLoginHandoff';
import * as storageAccess from '../../supabase/functions/_shared/storageAccess';

const USER = storageAccess.DEFAULT_ADMIN_LINE_ID;
const ID_TOKEN = 'offline-line-id-token';
const DEVICE_TOKEN = 'atlasdev.' + 'A'.repeat(43);

function fixture(endpoint: string, fetch: typeof globalThis.fetch) {
  let serve!: (request: Request) => Promise<Response>;
  const accounts: unknown[] = [];
  const rpc = vi.fn(async () => ({ data: { status: 'active', userId: USER }, error: null }));
  const source = readFileSync(new URL(`../../supabase/functions/${endpoint}/index.ts`, import.meta.url), 'utf8').replace(/^import .*;\r?\n/gm, '');
  runInNewContext(transformSync(source, { loader: 'ts', target: 'es2022', format: 'cjs' }).code, {
    ...deviceSession,
    ...lineVerifier,
    ...storageAccess,
    LineHandoffError,
    verifyLineIdTokenWith: (token: string, options: lineVerifier.LineVerifyOptions) =>
      lineVerifier.verifyLineIdTokenWith(token, { ...options, timeoutMs: 10 }),
    Deno: {
      env: { get: (name: string) => name === 'ADMIN_LINE_USER_ID' ? USER : 'configured-test-value' },
      serve: (callback: typeof serve) => { serve = callback; },
    },
    createClient: () => ({
      rpc,
      from: (table: string) => {
        const query = {
          select: () => query,
          eq: (column: string, value: unknown) => {
            if (column === 'line_user_id') accounts.push(value);
            return query;
          },
          maybeSingle: async () => ({ data: null, error: null }),
          limit: async () => {
            expect(table).toBe('chart_drawings');
            return { data: [], error: null };
          },
        };
        return query;
      },
    }),
    fetch,
    Request,
    Response,
  });
  return {
    accounts,
    rpc,
    request: (idToken = ID_TOKEN) => serve(new Request('https://offline.invalid', {
      method: 'POST',
      body: JSON.stringify({ idToken, action: endpoint === 'user-settings' ? 'access.get' : 'atlas.list' }),
    })),
  };
}

describe.each(['user-settings', 'chart-drawings'])('%s LINE verification', (endpoint) => {
  it('uses the verified user ID and disables redirects', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ sub: USER }));
    const server = fixture(endpoint, fetcher);
    const response = await server.request();
    expect(response.status).toBe(200);
    const body = await response.json();
    if (endpoint === 'user-settings') expect(body.data.userId).toBe(USER);
    else expect(body.records).toEqual([]);
    expect(server.accounts.length).toBeGreaterThan(0);
    expect(server.accounts.every((account) => account === USER)).toBe(true);
    expect(fetcher).toHaveBeenCalledWith(lineVerifier.LINE_VERIFY_URL, expect.objectContaining({
      method: 'POST',
      redirect: 'error',
      signal: expect.any(AbortSignal),
    }));
  });

  it.each([
    [400, 401, 'Invalid LINE ID Token'],
    [500, 503, '登入驗證服務暫時無法使用，請稍後再試。'],
  ])('maps LINE HTTP %i to %i', async (upstream, status, error) => {
    const server = fixture(endpoint, async () => new Response('LINE error', { status: upstream }));
    const response = await server.request();
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ ok: false, error });
    expect(server.accounts).toEqual([]);
  });

  it.each(['fetch', 'response body'])('returns 503 if LINE stalls during %s', async (stage) => {
    const server = fixture(endpoint, async (_url, init) => {
      const signal = init!.signal!;
      if (stage === 'response body') {
        return new Response(new ReadableStream({
          start: (controller) => signal.addEventListener('abort', () => controller.error(signal.reason), { once: true }),
        }));
      }
      return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      });
    });
    const response = await server.request();
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ ok: false, error: '登入驗證服務暫時無法使用，請稍後再試。' });
    expect(server.accounts).toEqual([]);
  });

  it('uses the remembered-device session RPC and skips LINE', async () => {
    const fetcher = vi.fn<typeof fetch>();
    const server = fixture(endpoint, fetcher);
    expect((await server.request(DEVICE_TOKEN)).status).toBe(200);
    expect(server.rpc).toHaveBeenCalledWith('atlas_device_session', {
      operation: 'use',
      session_hash: expect.stringMatching(/^[0-9a-f]{64}$/),
    });
    expect(fetcher).not.toHaveBeenCalled();
  });
});

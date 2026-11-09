import { webcrypto } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transformSync } from 'esbuild';
import { describe, it, expect, vi } from 'vitest';
import { HoldingsError } from '../../supabase/functions/user-settings/holdings';
const owner = 'U' + '0'.repeat(32),
  account = '20000000-0000-4000-8000-000000000001';
function setup() {
  const env: Record<string, string> = {
    SINOPAC_OWNER_ID: owner,
    SINOPAC_ACCOUNT_ID: account,
  };
  const module = {
    exports: {} as {
      handleSinopac: (
        db: unknown,
        owner: string,
        body: Record<string, unknown>,
        fetcher?: typeof fetch,
      ) => Promise<unknown>;
    },
  };
  const text = readFileSync(
    new URL('../../supabase/functions/user-settings/sinopac.ts', import.meta.url),
    'utf8',
  );
  runInNewContext(transformSync(text, { loader: 'ts', format: 'cjs' }).code, {
    module,
    exports: module.exports,
    require: () => ({ HoldingsError }),
    Deno: { env: { get: (key: string) => env[key] } },
    crypto: webcrypto,
    btoa,
    atob,
    URLSearchParams,
    TextEncoder,
    URL,
    AbortSignal,
    fetch,
  });
  const maybeSingle = vi.fn(async () => ({
    data: {
      last_sync_at: '2026-11-09T01:00:00Z',
      last_error_code: 'closed',
      skipped_shorts: 2,
    },
    error: null,
  }));
  const eq = vi.fn(() => ({ eq: vi.fn(() => ({ maybeSingle })) }));
  const db = { from: vi.fn(() => ({ select: vi.fn(() => ({ eq })) })) };
  return { handle: module.exports.handleSinopac, db, env, maybeSingle };
}
describe('SinoPac verified-owner bridge', () => {
  it('rejects a nonowner before database or Cloud Run calls', async () => {
    const s = setup();
    const fetcher = vi.fn();
    await expect(s.handle(s.db, 'other', { action: 'holdings.sinopac.sync' }, fetcher)).rejects.toMatchObject(
      { status: 403 },
    );
    expect(s.db.from).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('rejects another holdings account', async () => {
    const s = setup();
    await expect(
      s.handle(s.db, owner, {
        action: 'holdings.sinopac.status',
        accountId: 'other',
      }),
    ).rejects.toMatchObject({ status: 403 });
  });
  it('returns only owner metadata with closed/short status', async () => {
    const s = setup();
    await expect(s.handle(s.db, owner, { action: 'holdings.sinopac.status' })).resolves.toMatchObject({
      accountId: account,
      code: 'closed',
      skippedShorts: 2,
    });
  });
  it('fails closed without invoking an unconfigured service', async () => {
    const s = setup();
    const fetcher = vi.fn();
    await expect(s.handle(s.db, owner, { action: 'holdings.sinopac.sync' }, fetcher)).rejects.toMatchObject({
      status: 503,
    });
    expect(fetcher).not.toHaveBeenCalled();
  });
});

async function configured() {
  const s = setup();
  const keys = await webcrypto.subtle.generateKey(
    {
      name: 'RSASSA-PKCS1-v1_5',
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: 'SHA-256',
    },
    true,
    ['sign', 'verify'],
  );
  const pem = Buffer.from(await webcrypto.subtle.exportKey('pkcs8', keys.privateKey)).toString('base64');
  Object.assign(s.env, {
    SINOPAC_INVOKER_EMAIL: 'fake-invoker@example.iam.gserviceaccount.com',
    SINOPAC_INVOKER_PRIVATE_KEY: `-----BEGIN PRIVATE KEY-----\n${pem}\n-----END PRIVATE KEY-----`,
    SINOPAC_SHARED_SECRET: 'x'.repeat(48),
    SINOPAC_SYNC_URL: 'https://fake-service.run.app',
  });
  return { ...s, keys };
}
it('signs an audience-bound ID token exchange, then sends both private credentials and verified identity', async () => {
  const s = await configured();
  const fetcher = vi.fn(async (url: URL | RequestInfo, init?: RequestInit) => {
    if (String(url) === 'https://oauth2.googleapis.com/token') {
      const assertion = new URLSearchParams(String(init?.body)).get('assertion')!;
      const [header, payload, signature] = assertion.split('.');
      expect(JSON.parse(Buffer.from(payload!, 'base64url').toString())).toMatchObject({
        iss: s.env.SINOPAC_INVOKER_EMAIL,
        target_audience: s.env.SINOPAC_SYNC_URL,
        aud: 'https://oauth2.googleapis.com/token',
      });
      expect(
        await webcrypto.subtle.verify(
          'RSASSA-PKCS1-v1_5',
          s.keys.publicKey,
          Buffer.from(signature!, 'base64url'),
          new TextEncoder().encode(header + '.' + payload),
        ),
      ).toBe(true);
      return Response.json({ id_token: 'fake-google-id-token' });
    }
    expect(String(url)).toBe('https://fake-service.run.app/sync');
    expect(init?.headers).toMatchObject({
      Authorization: 'Bearer fake-google-id-token',
      'X-Sinopac-Secret': 'x'.repeat(48),
    });
    expect(JSON.parse(String(init?.body))).toEqual({ ownerId: owner, accountId: account });
    return Response.json({ code: 'ok', message: '正常' });
  });
  await expect(
    s.handle(s.db, owner, { action: 'holdings.sinopac.sync', accountId: account }, fetcher),
  ).resolves.toMatchObject({ accountId: account });
  expect(fetcher).toHaveBeenCalledTimes(2);
});
it('returns a fixed quota message and never relays upstream private errors', async () => {
  const s = await configured();
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ id_token: 'fake-token' }))
    .mockResolvedValueOnce(
      Response.json({ code: 'quota', message: 'PRIVATE account / credentials' }, { status: 503 }),
    );
  await expect(s.handle(s.db, owner, { action: 'holdings.sinopac.sync' }, fetcher)).rejects.toMatchObject({
    status: 503,
    message: '永豐流量超限，請稍後再試',
  });
  expect(s.db.from).not.toHaveBeenCalled();
});
it('never calls a redirected or non-Cloud-Run target', async () => {
  const s = await configured();
  s.env.SINOPAC_SYNC_URL = 'https://evil.example';
  const fetcher = vi.fn();
  await expect(s.handle(s.db, owner, { action: 'holdings.sinopac.sync' }, fetcher)).rejects.toMatchObject({
    status: 503,
  });
  expect(fetcher).not.toHaveBeenCalled();
});

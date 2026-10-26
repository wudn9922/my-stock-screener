/** Actual pinned SDK, local test signing keys and provider HTTP doubles; never contacts LINE. */
import { buildSync } from 'esbuild';
import { generateKeyPairSync, sign } from 'node:crypto';
import { resolve } from 'node:path';
import { readFile } from 'node:fs/promises';
import type { BrowserContext, Page } from '@playwright/test';
import { DEVICE_USER_ID } from './account-fixture';
const sdk = buildSync({
  stdin: {
    contents: "import liff from '@line/liff'; window.liff=liff;",
    resolveDir: resolve(import.meta.dirname, '..'),
  },
  bundle: true,
  write: false,
  format: 'iife',
  platform: 'browser',
  target: 'es2022',
}).outputFiles[0].text;
const encode = (value: unknown) =>
  Buffer.from(JSON.stringify(value)).toString('base64url');
export const SDK_TEST_ORIGIN = 'https://atlas.test';
export function sdkTestUrl(value: string) {
  const url = new URL(value);
  url.protocol = 'https:';
  url.host = 'atlas.test';
  url.port = '';
  return url.href;
}
export function sdkIdentityFixture() {
  const { privateKey, publicKey } = generateKeyPairSync('ec', {
    namedCurve: 'P-256',
  });
  const jwk = {
    ...publicKey.export({ format: 'jwk' }),
    kid: 'atlas-offline-test',
    use: 'sig',
    alg: 'ES256',
  };
  const payload = {
    iss: 'https://access.line.me',
    aud: '2010330411',
    sub: DEVICE_USER_ID,
    exp: Math.floor(Date.now() / 1000) + 3600,
    iat: Math.floor(Date.now() / 1000),
    name: '本人測試帳號',
  };
  const input =
    encode({ alg: 'ES256', kid: jwk.kid, typ: 'JWT' }) + '.' + encode(payload);
  return {
    jwk,
    idToken:
      input +
      '.' +
      sign('sha256', Buffer.from(input), {
        key: privateKey,
        dsaEncoding: 'ieee-p1363',
      }).toString('base64url'),
  };
}
export async function installRealSdk(
  context: BrowserContext,
  identity = sdkIdentityFixture(),
) {
  const exchanges: { frameUrl: string; hasVerifier: boolean }[] = [];
  // WebKit correctly rejects the SDK's Secure expiry cookie on plain HTTP.
  // Serve only our local public build under an isolated HTTPS origin; no browser
  // headers/cookies/bodies are forwarded and no external TLS checks are disabled.
  const dist = resolve(
    import.meta.dirname,
    '..',
    process.env.SITE_OUT_DIR || 'test-results/site-dist',
  );
  await context.route(`${SDK_TEST_ORIGIN}/**`, async (r) => {
    const path = decodeURIComponent(new URL(r.request().url()).pathname);
    const relative =
      path === '/my-stock-screener/'
        ? 'index.html'
        : path.startsWith('/my-stock-screener/atlas/')
          ? path.slice('/my-stock-screener/atlas/'.length) || 'index.html'
          : null;
    const file = relative ? resolve(dist, relative) : '';
    if (!file.startsWith(dist + '/'))
      return r.fulfill({ status: 404, body: '' });
    try {
      const contentType = file.endsWith('.js')
        ? 'text/javascript; charset=utf-8'
        : file.endsWith('.css')
          ? 'text/css'
          : file.endsWith('.html')
            ? 'text/html; charset=utf-8'
            : file.endsWith('.json')
              ? 'application/json'
              : 'application/octet-stream';
      return r.fulfill({ contentType, body: await readFile(file) });
    } catch {
      return r.fulfill({ status: 404, body: '' });
    }
  });
  await context.route('https://static.line-scdn.net/liff/edge/2/sdk.js', (r) =>
    r.fulfill({ contentType: 'text/javascript; charset=utf-8', body: sdk }),
  );
  await context.route(
    'https://static.line-scdn.net/liff/edge/2/*extensions*',
    (r) => r.fulfill({ contentType: 'text/javascript', body: '' }),
  );
  await context.route('https://liffsdk.line-scdn.net/**', (r) =>
    r.fulfill({ json: {} }),
  );
  await context.route('https://api.line.me/**', (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/contextToken')) {
      const token =
        encode({ alg: 'none' }) +
        '.' +
        encode({
          type: 'external',
          liffId: '2010330411-6JhrotT9',
          endpointUrl: `${SDK_TEST_ORIGIN}/my-stock-screener/`,
          permanentLinkPattern: 'concat',
          scope: ['openid', 'profile'],
          availability: {},
          features: [],
        }) +
        '.test';
      return r.fulfill({ json: { contextToken: token } });
    }
    if (path === '/oauth2/v2.1/certs')
      return r.fulfill({ json: { keys: [identity.jwk] } });
    if (path === '/oauth2/v2.1/token') {
      exchanges.push({
        frameUrl: r.request().frame().url(),
        hasVerifier: !!new URLSearchParams(r.request().postData() ?? '').get(
          'code_verifier',
        ),
      });
      return r.fulfill({
        json: {
          access_token: 'offline-fixture-access',
          id_token: identity.idToken,
          expires_in: 3600,
          token_type: 'Bearer',
          scope: 'openid profile',
        },
      });
    }
    return r.abort();
  });
  await context.route('https://access.line.me/liff/v1/authorize**', (r) =>
    r.abort(),
  );
  await context.route('**/my-stock-screener/sdk-fixture', (r) =>
    r.fulfill({
      contentType: 'text/html',
      body: '<!doctype html><html><meta charset="utf-8"><body>Offline SDK entry</body></html>',
    }),
  );
  return { identity, exchanges };
}
type FixtureSdk = {
  init(config: {
    liffId: string;
    withLoginOnExternalBrowser: false;
  }): Promise<void>;
  login(config: { redirectUri: string }): void;
};
export async function sdkLogin(page: Page, returnUrl: string) {
  await page.goto(`${SDK_TEST_ORIGIN}/my-stock-screener/sdk-fixture`);
  await page.addScriptTag({
    url: 'https://static.line-scdn.net/liff/edge/2/sdk.js',
  });
  await page.evaluate(async () =>
    (window as unknown as { liff: FixtureSdk }).liff.init({
      liffId: '2010330411-6JhrotT9',
      withLoginOnExternalBrowser: false,
    }),
  );
  const request = page.waitForRequest((r) =>
    r.url().startsWith('https://access.line.me/liff/v1/authorize'),
  );
  await page.evaluate(
    (url) =>
      (window as unknown as { liff: FixtureSdk }).liff.login({
        redirectUri: url,
      }),
    returnUrl,
  );
  const authorization = new URL((await request).url());
  const callback = new URL(returnUrl);
  callback.searchParams.set('code', 'offline-fixture-code');
  callback.searchParams.set('state', authorization.searchParams.get('state')!);
  callback.searchParams.set('liffClientId', '2010330411');
  callback.searchParams.set('liffRedirectUri', returnUrl);
  return callback.href;
}

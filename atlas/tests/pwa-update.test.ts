import { describe, expect, it, vi } from 'vitest';
import { checkAppVersion } from '../src/pwa/AppUpdate';

const base = new URL('https://example.test/my-stock-screener/atlas/');
const version = 'abcdef123456';
const data = (extra = {}) => ({ app: 'atlas', scope: base.pathname, version, ...extra });
const fetcher = (body: unknown, status = 200) => vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

describe('PWA public deployment version', () => {
  it('checks the same scope without credentials or cached grants', async () => {
    const get = fetcher(data());
    expect(await checkAppVersion(version, base, get)).toBe('current');
    expect(get).toHaveBeenCalledWith(new URL('app-version.json', base).href, expect.objectContaining({ cache: 'no-store', credentials: 'omit' }));
    expect(await checkAppVersion(version, base, fetcher(data({ version: '0123456789ab' })))).toBe('available');
  });
  it('rejects wrong app, scope, malformed version, unavailable and non-JSON responses', async () => {
    for (const value of [null, data({ app: 'other' }), data({ scope: '/other/' }), data({ version: '<html>' })])
      expect(await checkAppVersion(version, base, fetcher(value))).toBe('unavailable');
    expect(await checkAppVersion(version, base, fetcher(data(), 404))).toBe('unavailable');
    expect(await checkAppVersion(version, base, vi.fn(async () => new Response('<html>')) as typeof fetch)).toBe('unavailable');
    expect(await checkAppVersion(version, base, vi.fn(async () => { throw new Error('offline'); }) as typeof fetch)).toBe('unavailable');
  });
  it('does not accept a redirected deployment or fabricate a version in dev', async () => {
    const get = vi.fn(async () => Object.assign(new Response(JSON.stringify(data())), {}));
    const response = new Response(JSON.stringify(data()));
    Object.defineProperty(response, 'url', { value: 'https://elsewhere.test/app-version.json' });
    expect(await checkAppVersion(version, base, vi.fn(async () => response) as typeof fetch)).toBe('unavailable');
    expect(await checkAppVersion('', base, get as typeof fetch)).toBe('unavailable');
    expect(get).not.toHaveBeenCalled();
  });
});

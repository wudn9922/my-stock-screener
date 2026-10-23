import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccessController, type StorageMode } from '../src/auth/access';
import type { LineIdentity } from '../src/cloud/lineIdentity';

const A = `U${'1'.repeat(32)}`, B = `U${'2'.repeat(32)}`;
function identity(userId = A): LineIdentity { return { userId, idToken: () => `token-${userId}` }; }
function grant(userId = A, storageMode: StorageMode = 'device', revision = '1') {
  return new Response(JSON.stringify({ ok: true, data: { userId, storageMode, revision, isAdmin: false } }));
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('verified access lifecycle (no cached browser grants)', () => {
  it('does not contact private APIs or grant persistence to unidentified visitors', async () => {
    const fetcher = vi.fn();
    const access = new AccessController(true, async () => null, fetcher);
    expect(await access.initialize()).toMatchObject({ ready: true, userId: null, storageMode: 'none', isAdmin: false });
    await expect(access.requireCloud()).rejects.toThrow('未獲雲端');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    { ok: true, data: { userId: B, storageMode: 'cloud', revision: '1', isAdmin: true } },
    { ok: true, data: { userId: A, storageMode: 'forever', revision: '1', isAdmin: false } },
    { ok: true, data: { userId: A, storageMode: 'cloud', revision: '1' } },
    { ok: false },
  ])('fails closed on invalid or mismatched server access: %j', async (body) => {
    const access = new AccessController(true, async () => identity(), vi.fn(async () => new Response(JSON.stringify(body))));
    expect(await access.initialize()).toMatchObject({ userId: A, storageMode: 'none', isAdmin: false });
    await expect(access.requireCloud()).rejects.toThrow('未獲雲端');
  });

  it('fails closed on source errors and does not restore a previously granted cloud mode', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(grant(A, 'cloud')).mockRejectedValueOnce(new Error('offline'));
    const access = new AccessController(true, async () => identity(), fetcher);
    await access.initialize();
    const previous = access.getSnapshot().epoch;
    expect((await access.requireCloud()).userId).toBe(A);
    expect(await access.refresh()).toMatchObject({ storageMode: 'none', error: 'offline' });
    expect(access.getSnapshot().epoch).toBeGreaterThan(previous);
    await expect(access.requireCloud()).rejects.toThrow('未獲雲端');
  });

  it('returns the current grant after revocation instead of the initial resolved promise', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(grant(A, 'cloud')).mockResolvedValueOnce(grant(A, 'none', '2'));
    const access = new AccessController(true, async () => identity(), fetcher);
    await access.initialize();
    await access.refresh();
    expect(await access.initialize()).toMatchObject({ storageMode: 'none', revision: '2' });
  });

  it('drops the old account immediately and ignores an older asynchronous grant response', async () => {
    let current = identity();
    let finishA!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementationOnce(() => new Promise(resolve => { finishA = resolve; })).mockResolvedValueOnce(grant(B));
    const access = new AccessController(true, async () => current, fetcher);
    const old = access.initialize();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    current = identity(B);
    await access.refresh();
    expect(access.getSnapshot()).toMatchObject({ userId: B, storageMode: 'device' });
    finishA(grant(A, 'cloud'));
    await old;
    expect(access.getSnapshot()).toMatchObject({ userId: B, storageMode: 'device', isAdmin: false });
  });

  it('does not accept a grant if the captured token expires while it is in flight', async () => {
    let valid = true;
    const access = new AccessController(true, async () => ({ userId: A, idToken: () => valid ? 'token' : null }), vi.fn(async () => {
      valid = false;
      return grant(A, 'cloud');
    }));
    expect(await access.initialize()).toMatchObject({ storageMode: 'none', userId: null });
    expect(access.authenticatedIdentity()).toBeNull();
  });

  it('rejects new cloud requests as soon as the verified token becomes invalid', async () => {
    let valid = true;
    const access = new AccessController(true, async () => ({ userId: A, idToken: () => valid ? 'token' : null }), vi.fn(async () => grant(A, 'cloud')));
    await access.initialize();
    valid = false;
    expect(access.authenticatedIdentity()).toBeNull();
    await expect(access.requireCloud()).rejects.toThrow('未獲雲端');
  });
});

describe('actual LINE SDK identity callbacks', () => {
  it('does not load the SDK or touch session storage outside LINE', async () => {
    vi.resetModules();
    vi.stubGlobal('navigator', { userAgent: 'Mozilla/5.0 Safari' });
    const init = vi.fn();
    vi.stubGlobal('liff', { init });
    const setItem = vi.fn();
    vi.stubGlobal('sessionStorage', { setItem });
    const { refreshLineIdentity } = await import('../src/cloud/lineIdentity');
    expect(await refreshLineIdentity()).toBeNull();
    expect(init).not.toHaveBeenCalled(); expect(setItem).not.toHaveBeenCalled();
  });

  it('invalidates an old callback after account switching, logout or token expiry', async () => {
    vi.resetModules();
    let userId: string | null = A;
    let exp = Date.now() / 1000 + 3600;
    vi.stubGlobal('navigator', { userAgent: 'Mozilla/5.0 Line/14.0.0' });
    const init = vi.fn(async () => {});
    vi.stubGlobal('liff', {
      init, isLoggedIn: () => !!userId,
      getIDToken: () => userId ? `token-${userId}` : null,
      getDecodedIDToken: () => userId ? { sub: userId, exp } : null,
    });
    const setItem = vi.fn();
    vi.stubGlobal('sessionStorage', { setItem });
    const { refreshLineIdentity } = await import('../src/cloud/lineIdentity');
    const a = await refreshLineIdentity();
    expect(a?.idToken()).toBe(`token-${A}`);
    userId = B;
    expect(a?.idToken()).toBeNull();
    const b = await refreshLineIdentity();
    expect(b?.userId).toBe(B); expect(b?.idToken()).toBe(`token-${B}`);
    exp = Date.now() / 1000;
    expect(b?.idToken()).toBeNull();
    exp = Number.NaN;
    expect(b?.idToken()).toBeNull();
    userId = null;
    expect(await refreshLineIdentity()).toBeNull();
    expect(init).toHaveBeenCalledOnce(); expect(setItem).not.toHaveBeenCalled();
  });
});

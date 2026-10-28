import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessController } from '../src/auth/access';
import { completeRememberAfterRedirect, deviceIdentity, forgetDevice, rememberAfterRedirect, rememberDevice, rememberedDevice } from '../src/cloud/deviceSession';
import type { LineIdentity } from '../src/cloud/lineIdentity';

const A = `U${'1'.repeat(32)}`, B = `U${'2'.repeat(32)}`;
const TOKEN = `atlasdev.${'a'.repeat(43)}`, OTHER = `atlasdev.${'b'.repeat(43)}`;
const live = (userId = A): LineIdentity => ({ userId, kind: 'line', idToken: () => `line-${userId}` });
function memoryStorage() {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); }, values };
}
const created = (userId = A, deviceToken = TOKEN) => new Response(JSON.stringify({ ok: true, data: { userId, deviceToken } }));
const body = (call: unknown[]) => JSON.parse(String((call[1] as RequestInit).body));
let local: ReturnType<typeof memoryStorage>, session: ReturnType<typeof memoryStorage>;
beforeEach(() => {
  local = memoryStorage(); session = memoryStorage();
  vi.stubGlobal('localStorage', local); vi.stubGlobal('sessionStorage', session);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('remembered device', () => {
  it('stores a server-issued device token for the logged-in account and forgets it on logout', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(created()).mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetcher);
    expect(await rememberDevice(live())).toBe(true);
    expect(body(fetcher.mock.calls[0])).toEqual({ action: 'device.session.create', idToken: `line-${A}` });
    expect(rememberedDevice()).toEqual({ token: TOKEN, userId: A });
    const identity = deviceIdentity()!;
    expect(identity).toMatchObject({ userId: A, kind: 'device' });
    expect(identity.idToken()).toBe(TOKEN);
    forgetDevice();
    expect(rememberedDevice()).toBeNull();
    expect(identity.idToken()).toBeNull();
    expect(body(fetcher.mock.calls[1])).toEqual({ action: 'device.session.revoke', idToken: TOKEN });
    expect((fetcher.mock.calls[1][1] as RequestInit).keepalive).toBe(true);
  });

  it('rejects a mismatched or malformed response and never exchanges a device token for another', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(created(B)).mockResolvedValueOnce(created(A, 'atlasdev.short'));
    vi.stubGlobal('fetch', fetcher);
    expect(await rememberDevice(live())).toBe(false);
    expect(await rememberDevice(live())).toBe(false);
    expect(await rememberDevice({ userId: A, kind: 'device', idToken: () => TOKEN })).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(local.values.size).toBe(0);
  });

  it('replaces an earlier remembered account and revokes its token', async () => {
    local.setItem('atlas-device-session-v1', JSON.stringify({ token: OTHER, userId: B }));
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(created()).mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetcher);
    expect(await rememberDevice(live())).toBe(true);
    expect(rememberedDevice()).toEqual({ token: TOKEN, userId: A });
    expect(body(fetcher.mock.calls[1])).toEqual({ action: 'device.session.revoke', idToken: OTHER });
  });

  it('remembers after a same-window LINE redirect only when the login started with the checkbox on', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(created());
    vi.stubGlobal('fetch', fetcher);
    rememberAfterRedirect(false);
    await completeRememberAfterRedirect(live());
    expect(fetcher).not.toHaveBeenCalled();
    rememberAfterRedirect(true);
    await completeRememberAfterRedirect(live());
    expect(rememberedDevice()?.userId).toBe(A);
    expect(session.values.size).toBe(0);
  });
});

describe('access with a remembered device', () => {
  const remembered = () => { local.setItem('atlas-device-session-v1', JSON.stringify({ token: TOKEN, userId: A })); return deviceIdentity()!; };
  it('forgets a device the server no longer accepts and returns to guest', async () => {
    const identity = remembered();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ ok: false, code: 'device_session' }), { status: 401 }));
    const access = new AccessController(true, async () => identity, fetcher);
    expect(await access.initialize()).toMatchObject({ ready: true, settled: true, userId: null, storageMode: 'none', error: '此裝置的登入已失效，請重新登入。' });
    expect(body(fetcher.mock.calls[0])).toEqual({ action: 'access.get', idToken: TOKEN });
    expect(rememberedDevice()).toBeNull();
  });

  it('keeps the device through server or network errors', async () => {
    const identity = remembered();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ ok: false }), { status: 503 })).mockRejectedValueOnce(new Error('offline'));
    const access = new AccessController(true, async () => identity, fetcher);
    expect(await access.initialize()).toMatchObject({ settled: true, userId: A, storageMode: 'none' });
    await access.refresh();
    expect(rememberedDevice()).toEqual({ token: TOKEN, userId: A });
  });

  it('settles only after the first server check, even when shown early as a temporary guest', async () => {
    const identity = remembered();
    let finish!: (response: Response) => void;
    const access = new AccessController(true, async () => identity, vi.fn<typeof fetch>(() => new Promise(resolve => { finish = resolve; })));
    const initial = access.initialize();
    access.allowGuestWhileLoading();
    expect(access.getSnapshot()).toMatchObject({ ready: true, settled: false });
    await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
    finish(new Response(JSON.stringify({ ok: true, data: { userId: A, storageMode: 'cloud', revision: '1', isAdmin: false } })));
    expect(await initial).toMatchObject({ settled: true, userId: A, storageMode: 'cloud' });
    expect(access.authenticatedIdentity()?.idToken()).toBe(TOKEN);
  });
});

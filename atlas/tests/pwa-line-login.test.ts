import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const A = `U${'a'.repeat(32)}`, B = `U${'b'.repeat(32)}`;
const URL_BASE = 'https://wudn9922.github.io/my-stock-screener/atlas/';
function environment(query = '?page=chart&symbol=NVDA&tf=1D') {
  vi.stubGlobal('navigator', { userAgent: 'Mozilla/5.0 Safari' });
  vi.stubGlobal('location', new URL(`${URL_BASE}${query}`));
  const storage = { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() };
  vi.stubGlobal('sessionStorage', storage);
  vi.stubGlobal('localStorage', storage);
  const appendChild = vi.fn();
  vi.stubGlobal('document', { createElement: vi.fn(), head: { appendChild } });
  return { storage, appendChild };
}
function sdkFixture(loggedIn = false) {
  const state = { userId: loggedIn ? A as string | null : null, expiry: Date.now() / 1000 + 3600 };
  const sdk = {
    init: vi.fn(async (_config: unknown) => {}),
    isLoggedIn: () => !!state.userId,
    login: vi.fn((_config: { redirectUri: string }) => {}),
    logout: vi.fn(() => { state.userId = null; }),
    getIDToken: () => state.userId ? `token-${state.userId}` : null,
    getDecodedIDToken: () => state.userId ? { sub: state.userId, exp: state.expiry } : null,
  };
  vi.stubGlobal('liff', sdk);
  return { sdk, state };
}
beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('explicit external-browser / installed-PWA LINE login', () => {
  it('keeps the recovery guest choice inside LINE until an explicit login, even after route cleanup', async () => {
    environment('?page=console&atlasGuest=1');
    vi.stubGlobal('navigator', { userAgent: 'Mozilla/5.0 Line/14.0.0' });
    const { sdk } = sdkFixture(true);
    const auth = await import('../src/cloud/lineIdentity');
    expect(auth.needsLineIdentityBeforeRouting()).toBe(false);
    expect(await auth.startLineIdentity()).toBeNull();
    vi.stubGlobal('location', new URL(`${URL_BASE}?page=console`));
    expect(await auth.refreshLineIdentity()).toBeNull();
    expect(sdk.init).not.toHaveBeenCalled();
    expect(await auth.loginLineIdentity()).toBe('authenticated');
    expect(sdk.init).toHaveBeenCalledOnce();
    expect((await auth.refreshLineIdentity())?.idToken()).toBe(`token-${A}`);
  });

  it('removes the recovery guest flag from an explicit login return URL', async () => {
    environment('?page=console&atlasGuest=1');
    const { sdk } = sdkFixture();
    const auth = await import('../src/cloud/lineIdentity');
    expect(await auth.startLineIdentity()).toBeNull();
    expect(await auth.loginLineIdentity()).toBe('redirecting');
    const callback = new URL(sdk.login.mock.calls[0]![0].redirectUri);
    expect(callback.searchParams.has('atlasGuest')).toBe(false);
    expect(callback.searchParams.get('atlasLineLogin')).toBe('1');
  });

  it('ignores a retained SDK session until the visitor explicitly requests login', async () => {
    const { storage, appendChild } = environment();
    const { sdk } = sdkFixture(true);
    const auth = await import('../src/cloud/lineIdentity');
    expect(auth.needsLineIdentityBeforeRouting()).toBe(false);
    expect(await auth.refreshLineIdentity()).toBeNull();
    expect(sdk.init).not.toHaveBeenCalled();
    expect(appendChild).not.toHaveBeenCalled();
    expect(await auth.loginLineIdentity()).toBe('authenticated');
    expect(sdk.init).toHaveBeenCalledExactlyOnceWith({ liffId: auth.DEFAULT_LIFF_ID, withLoginOnExternalBrowser: false });
    expect(sdk.login).not.toHaveBeenCalled();
    expect((await auth.refreshLineIdentity())?.idToken()).toBe(`token-${A}`);
    // Only an opted-in remembered device is ever looked up; nothing is written.
    expect(storage.getItem.mock.calls.every(([key]) => key === 'atlas-device-session-v1')).toBe(true);
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it('uses SDK login only after the button and keeps a same-origin return route', async () => {
    environment('?page=chart&symbol=NVDA&tf=1D&code=stale&state=stale&liff.unrelated=old#secret');
    const { sdk } = sdkFixture();
    const auth = await import('../src/cloud/lineIdentity');
    expect(await auth.startLineIdentity()).toBeNull();
    expect(sdk.init).not.toHaveBeenCalled();
    expect(await auth.loginLineIdentity()).toBe('redirecting');
    const callback = new URL(sdk.login.mock.calls[0]![0].redirectUri);
    expect(callback.origin).toBe(new URL(URL_BASE).origin);
    expect(callback.pathname).toBe(new URL(URL_BASE).pathname);
    expect(Object.fromEntries(callback.searchParams)).toEqual({ page: 'chart', symbol: 'NVDA', tf: '1D', atlasLineLogin: '1' });
    expect(callback.hash).toBe('');
    await auth.refreshLineIdentity(); await auth.refreshLineIdentity();
    expect(sdk.login).toHaveBeenCalledOnce();
  });

  it('recognizes a marked SDK callback but does not treat unrelated code parameters as login', async () => {
    environment();
    const { hasLineLoginCallback } = await import('../src/cloud/lineIdentity');
    expect(hasLineLoginCallback('?code=x&state=y')).toBe(false);
    expect(hasLineLoginCallback('?atlasLineLogin=1')).toBe(false);
    expect(hasLineLoginCallback('?atlasLineLogin=1&code=x&state=y')).toBe(true);
    expect(hasLineLoginCallback('?atlasLineLogin=1&error=access_denied&state=y')).toBe(true);
    expect(hasLineLoginCallback('?liffClientId=x&code=x&state=y')).toBe(true);
  });

  it.each([
    '?atlasLineLogin=1&code=fixture-code&liffClientId=2010330411',
    '?code=fixture-code&liffClientId=2010330411&liffRedirectUri=fixture',
    '?liffOAuth2Error=true&error=access_denied',
    '?liff.state=%2Fatlas%2F%3Fpage%3Dconsole',
    '?liff.referrer=https%3A%2F%2Fliff.line.me%2Ffixture',
    '?page=console#access_token=fixture-access&context_token=fixture-context',
  ])('initializes a supported LINE handoff before routing: %s', async (query) => {
    environment(query);
    const { sdk } = sdkFixture();
    const auth = await import('../src/cloud/lineIdentity');
    expect(auth.needsLineIdentityBeforeRouting()).toBe(true);
    expect(await auth.startLineIdentity()).toBeNull();
    expect(sdk.init).toHaveBeenCalledOnce();
    expect(sdk.login).not.toHaveBeenCalled();
  });

  it('keeps credential fragments for init and removes them only after completion', async () => {
    environment('?page=console#access_token=fixture-access&context_token=fixture-context&view=chart');
    const replaceState = vi.fn();
    vi.stubGlobal('history', { state: { preserved: true }, replaceState });
    const { sdk } = sdkFixture();
    let finish!: () => void;
    sdk.init.mockImplementation(() => new Promise<void>(resolve => { finish = resolve; }));
    const auth = await import('../src/cloud/lineIdentity');
    const start = auth.startLineIdentity();
    await vi.waitFor(() => expect(sdk.init).toHaveBeenCalledOnce());
    expect(replaceState).not.toHaveBeenCalled();
    finish(); await start;
    expect(replaceState).toHaveBeenCalledExactlyOnceWith({ preserved: true }, '', `${URL_BASE}?page=console#view=chart`);
    expect(sdk.login).not.toHaveBeenCalled();
  });

  it('bounds an explicit init wait and never redirects after that attempt times out', async () => {
    vi.useFakeTimers();
    environment();
    const { sdk } = sdkFixture();
    let finish!: () => void;
    sdk.init.mockImplementation(() => new Promise<void>(resolve => { finish = resolve; }));
    const auth = await import('../src/cloud/lineIdentity');
    const attempt = auth.loginLineIdentity().then(() => 'unexpected success', error => String(error));
    await vi.advanceTimersByTimeAsync(16000);
    expect(await Promise.race([attempt, Promise.resolve('still pending')])).toContain('逾時');
    expect(sdk.init).toHaveBeenCalledOnce();
    finish();
    await vi.advanceTimersByTimeAsync(1);
    expect(sdk.login).not.toHaveBeenCalled();
    expect(await auth.loginLineIdentity()).toBe('redirecting');
    expect(sdk.login).toHaveBeenCalledOnce();
  });

  it('shares callback initialization until the SDK finishes, without another login redirect', async () => {
    environment('?page=chart&symbol=NVDA&atlasLineLogin=1&code=provider-code&state=provider-state');
    const { sdk } = sdkFixture(true);
    let finish!: () => void;
    sdk.init.mockImplementation(() => new Promise<void>(resolve => { finish = resolve; }));
    const auth = await import('../src/cloud/lineIdentity');
    expect(auth.needsLineIdentityBeforeRouting()).toBe(true);
    let finished = false;
    const first = auth.startLineIdentity().then(result => { finished = true; return result; });
    const second = auth.refreshLineIdentity();
    await vi.waitFor(() => expect(sdk.init).toHaveBeenCalledOnce());
    expect(finished).toBe(false);
    expect(location.search).toContain('code=provider-code');
    finish();
    expect((await first)?.userId).toBe(A);
    expect((await second)?.userId).toBe(A);
    expect(sdk.login).not.toHaveBeenCalled();
  });

  it('explains an OAuth return without a verified SDK session instead of claiming login', async () => {
    environment('?code=fixture-code&liffClientId=2010330411');
    const { sdk } = sdkFixture();
    const auth = await import('../src/cloud/lineIdentity');
    expect(await auth.startLineIdentity()).toBeNull();
    expect(auth.lineLoginError()).toContain('回傳未完成驗證');
    expect(sdk.login).not.toHaveBeenCalled();
  });

  it('a failed/cancelled OAuth return remains temporary without automatic login loops', async () => {
    environment('?atlasLineLogin=1&error=access_denied&state=provider-state');
    const { sdk } = sdkFixture();
    sdk.init.mockRejectedValue(new Error('authorization cancelled'));
    const auth = await import('../src/cloud/lineIdentity');
    expect(await auth.startLineIdentity()).toBeNull();
    expect(await auth.refreshLineIdentity()).toBeNull();
    expect(await auth.refreshLineIdentity()).toBeNull();
    expect(sdk.init).toHaveBeenCalledOnce();
    expect(sdk.login).not.toHaveBeenCalled();
    expect(auth.lineLoginError()).toContain('僅暫存');
  });

  it('clears SDK authentication on signout and invalidates retained callbacks across re-login', async () => {
    const { storage } = environment();
    const { sdk, state } = sdkFixture(true);
    const auth = await import('../src/cloud/lineIdentity');
    await auth.loginLineIdentity();
    const previous = await auth.refreshLineIdentity();
    auth.logoutLineIdentity();
    expect(sdk.logout).toHaveBeenCalledOnce();
    expect(previous?.idToken()).toBeNull();
    expect(await auth.refreshLineIdentity()).toBeNull();
    state.userId = A;
    await auth.loginLineIdentity();
    expect((await auth.refreshLineIdentity())?.idToken()).toBe(`token-${A}`);
    expect(previous?.idToken()).toBeNull();
    expect(storage.removeItem).not.toHaveBeenCalled();
  });

  it('signs out of Atlas inside LINE without calling unsupported SDK logout', async () => {
    environment();
    vi.stubGlobal('navigator', { userAgent: 'Mozilla/5.0 Line/14.0.0' });
    const { sdk } = sdkFixture(true);
    sdk.logout.mockImplementation(() => { throw new Error('logout is unavailable inside LIFF'); });
    const auth = await import('../src/cloud/lineIdentity');
    const previous = await auth.refreshLineIdentity();
    expect(previous?.idToken()).toBe(`token-${A}`);
    expect(() => auth.logoutLineIdentity()).not.toThrow();
    expect(previous?.idToken()).toBeNull();
    expect(await auth.refreshLineIdentity()).toBeNull();
    expect(sdk.logout).not.toHaveBeenCalled();
    expect(sdk.login).not.toHaveBeenCalled();
  });

  it('asks to reopen an expired LIFF-client session without unsupported provider login/logout', async () => {
    environment();
    const { sdk, state } = sdkFixture(true);
    vi.stubGlobal('liff', { ...sdk, isInClient: () => true });
    state.expiry = Date.now() / 1000;
    const auth = await import('../src/cloud/lineIdentity');
    await expect(auth.loginLineIdentity()).rejects.toThrow('從 LINE 重新開啟');
    expect(sdk.logout).not.toHaveBeenCalled();
    expect(sdk.login).not.toHaveBeenCalled();
  });

  it('rejects expiry and account switches and refresh never starts a new OAuth redirect', async () => {
    environment();
    const { sdk, state } = sdkFixture(true);
    const auth = await import('../src/cloud/lineIdentity');
    await auth.loginLineIdentity();
    const old = await auth.refreshLineIdentity();
    state.userId = B;
    expect(old?.idToken()).toBeNull();
    const current = await auth.refreshLineIdentity();
    expect(current?.userId).toBe(B);
    state.expiry = Date.now() / 1000;
    expect(current?.idToken()).toBeNull();
    expect((await auth.refreshLineIdentity())?.idToken()).toBeNull();
    expect(sdk.login).not.toHaveBeenCalled();
    expect(sdk.init).toHaveBeenCalledOnce();
  });
});

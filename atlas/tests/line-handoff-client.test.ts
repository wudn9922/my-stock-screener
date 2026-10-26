import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  acceptLineHandoff,
  logoutLineIdentity,
  refreshLineIdentity,
} from '../src/cloud/lineIdentity';
import {
  beginSeparateLineLogin,
  incomingLineHandoff,
} from '../src/cloud/lineLoginHandoff';
const ID = '12345678-1234-4234-8234-123456789abc',
  USER = `U${'a'.repeat(32)}`;
function environment(status = 'complete') {
  const popup = {
    opener: {},
    document: { title: '', body: { textContent: '' } },
    location: { replace: vi.fn() },
    close: vi.fn(),
  };
  vi.stubGlobal('window', { open: vi.fn(() => popup) });
  vi.stubGlobal('document', { visibilityState: 'visible' });
  const calls: Record<string, unknown>[] = [];
  const candidate = {
    userId: USER,
    idToken: 'server-verified-test-token',
    expiresAt: Date.now() + 3600000,
    displayName: '測試帳號',
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url, init: RequestInit) => {
      const body = JSON.parse(init.body as string);
      calls.push(body);
      return new Response(
        JSON.stringify({
          ok: true,
          data:
            body.action === 'line.login.begin'
              ? {
                  status: 'pending',
                  expiresAt: new Date(Date.now() + 120000).toISOString(),
                }
              : { status, identity: candidate },
        }),
      );
    }),
  );
  const storage = { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() };
  vi.stubGlobal('localStorage', storage);
  vi.stubGlobal('sessionStorage', storage);
  return { popup, calls, candidate, storage };
}
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  logoutLineIdentity();
});
describe('partition-independent explicit LINE login', () => {
  it('keeps the receiver secret in RAM, opens LIFF in another window and returns only a candidate', async () => {
    const { calls, candidate, storage } = environment();
    expect(await beginSeparateLineLogin(new AbortController().signal)).toEqual(
      candidate,
    );
    const begin = calls[0]!;
    const url = new URL(vi.mocked(window.open).mock.calls[0]![0] as string);
    expect(url.origin).toBe('https://liff.line.me');
    expect(url.pathname).toBe('/2010330411-6JhrotT9/atlas/');
    expect(url.searchParams.get('atlasLineWrite')).toBe(begin.writeSecret);
    expect(url.href).not.toContain(begin.readSecret);
    expect(window.open).toHaveBeenCalledWith(
      url.href,
      '_blank',
      'noopener,noreferrer',
    );
    expect(calls.find((c) => c.action === 'line.login.poll')).toMatchObject({
      intentId: begin.intentId,
      readSecret: begin.readSecret,
    });
    expect(await refreshLineIdentity()).toBeNull(); // Explicit account confirmation still required.
    expect(storage.setItem).not.toHaveBeenCalled();
  });
  it('accepts an identity in RAM only after confirmation and invalidates retained callbacks on signout', async () => {
    const { candidate, storage } = environment();
    acceptLineHandoff(candidate);
    const identity = await refreshLineIdentity();
    expect(identity?.idToken()).toBe(candidate.idToken);
    logoutLineIdentity();
    expect(identity?.idToken()).toBeNull();
    expect(await refreshLineIdentity()).toBeNull();
    expect(storage.setItem).not.toHaveBeenCalled();
  });
  it('does not require a WindowProxy to receive the verified candidate', async () => {
    const { candidate } = environment();
    vi.mocked(window.open).mockReturnValue(null);
    const offered = vi.fn();
    expect(
      await beginSeparateLineLogin(new AbortController().signal, offered),
    ).toEqual(candidate);
    expect(offered.mock.calls[0]![0]).toContain('https://liff.line.me/');
  });
  it('cancels pending login and releases the original window without redirecting it', async () => {
    vi.useFakeTimers();
    const { calls } = environment('pending');
    const controller = new AbortController();
    const pending = beginSeparateLineLogin(controller.signal);
    const rejected = expect(pending).rejects.toThrow('取消');
    await vi.advanceTimersByTimeAsync(1);
    controller.abort();
    await rejected;
    expect(calls.at(-1)?.action).toBe('line.login.cancel');
  });
  it('ends a pending intent after two minutes without reopening LINE', async () => {
    vi.useFakeTimers();
    const { calls } = environment('pending');
    const expired = expect(
      beginSeparateLineLogin(new AbortController().signal),
    ).rejects.toThrow('逾時');
    await vi.advanceTimersByTimeAsync(120001);
    await expired;
    expect(window.open).toHaveBeenCalledOnce();
    expect(calls.filter((c) => c.action === 'line.login.begin')).toHaveLength(
      1,
    );
    expect(calls.at(-1)?.action).toBe('line.login.cancel');
  });
  it.each([false, true])(
    'bounds begin/complete race retries: alwaysMissing=%s',
    async (alwaysMissing) => {
      vi.useFakeTimers();
      vi.resetModules();
      vi.stubGlobal(
        'location',
        new URL(
          `https://atlas.test/?atlasLineHandoff=${ID}&atlasLineWrite=${'w'.repeat(43)}`,
        ),
      );
      const module = await import('../src/cloud/lineLoginHandoff');
      let attempts = 0;
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => {
          attempts++;
          const missing = attempts < 3 || alwaysMissing;
          return new Response(
            JSON.stringify(
              missing
                ? { ok: false, error: 'not ready' }
                : { ok: true, data: { status: 'complete' } },
            ),
            { status: missing ? 404 : 200 },
          );
        }),
      );
      module.prepareIncomingLineHandoff({
        userId: USER,
        idToken: () => 'verified-test-token',
      });
      expect(fetch).not.toHaveBeenCalled();
      const completed = module.completeIncomingLineHandoff();
      await vi.advanceTimersByTimeAsync(8000);
      await completed;
      expect(attempts).toBe(alwaysMissing ? 11 : 3);
      expect(module.lineHandoffMessage()).toContain(
        alwaysMissing ? 'not ready' : 'LINE 驗證完成',
      );
    },
  );
  it('never transfers identity from a framed approval page', async () => {
    vi.resetModules();
    vi.stubGlobal(
      'location',
      new URL(
        `https://atlas.test/?atlasLineHandoff=${ID}&atlasLineWrite=${'w'.repeat(43)}`,
      ),
    );
    vi.stubGlobal('window', { top: {}, self: {} });
    vi.stubGlobal('fetch', vi.fn());
    const module = await import('../src/cloud/lineLoginHandoff');
    module.prepareIncomingLineHandoff({
      userId: USER,
      idToken: () => 'verified-test-token',
    });
    expect(module.incomingLineApproval()).toBeNull();
    await module.completeIncomingLineHandoff();
    expect(fetch).not.toHaveBeenCalled();
  });
  it('captures the same transaction from primary and secondary LIFF URLs', () => {
    const query = `?atlasLineHandoff=${ID}&atlasLineWrite=${'w'.repeat(43)}`;
    expect(incomingLineHandoff(query)).toEqual(
      incomingLineHandoff(
        '?liff.state=' + encodeURIComponent('/atlas/' + query),
      ),
    );
    expect(
      incomingLineHandoff('?atlasLineHandoff=bad&atlasLineWrite=bad'),
    ).toBeNull();
  });
});

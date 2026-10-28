import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HoldingAccount, Holding } from '../src/holdings/model';

const h = vi.hoisted(() => ({
  userId: null as string | null,
  mode: 'none',
  valid: true,
  document: { version: 1, accounts: [] } as unknown,
  failure: false,
  calls: [] as string[],
}));
vi.mock('../src/app/HostingMode', () => ({ SCREENER_HOSTING: true, storageName: (key: string) => `${key}-screener` }));
vi.mock('../src/cloud/lineIdentity', () => ({
  refreshLineIdentity: async () => {
    const id = h.userId;
    return id ? { userId: id, idToken: () => (h.valid && id === h.userId ? 'test-token' : null) } : null;
  },
}));
const account: HoldingAccount = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'PRIVATE',
  kind: 'tw-broker',
  broker: 'kgi',
  currency: 'TWD',
};
const holding: Holding = {
  id: '22222222-2222-4222-8222-222222222222',
  accountId: account.id,
  symbol: '2330.TW',
  market: 'TW',
  quantity: '1500',
  averageCost: '800',
  costCurrency: 'TWD',
  source: 'paste',
  updatedAt: '2026-10-28T01:00:00Z',
};
let disk: Map<string, string>;
beforeEach(() => {
  vi.resetModules();
  h.userId = null;
  h.mode = 'none';
  h.valid = true;
  h.failure = false;
  h.calls = [];
  h.document = { version: 1, accounts: [] };
  disk = new Map();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => disk.get(key) ?? null,
    setItem: (key: string, value: string) => {
      disk.set(key, value);
    },
    removeItem: (key: string) => disk.delete(key),
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: unknown, init: RequestInit) => {
      const body = JSON.parse(String(init.body));
      h.calls.push(body.action);
      if (body.action === 'access.get')
        return new Response(
          JSON.stringify({
            ok: true,
            data: { userId: h.userId, storageMode: h.mode, isAdmin: false, revision: h.mode },
          }),
        );
      if (h.failure)
        return new Response(JSON.stringify({ ok: false, error: 'PRIVATE server detail' }), { status: 503 });
      if (body.action === 'holdings.replaceAccount')
        h.document = { version: 1, accounts: [{ account: body.account, holdings: body.holdings, revision: 1 }] };
      return new Response(JSON.stringify({ ok: true, data: h.document }));
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('holdings use the existing verified storage context', () => {
  it('keeps guests in RAM across views, writes no local storage and never calls the cloud', async () => {
    const { accessState } = await import('../src/auth/access');
    await accessState.initialize();
    const { holdingsStore, HoldingsStore } = await import('../src/holdings/store');
    await holdingsStore().replace(account, [holding]);
    expect(new HoldingsStore().getSnapshot().document.accounts[0]!.holdings).toHaveLength(1);
    expect(disk.size).toBe(0);
    expect(h.calls).toEqual([]);
  });
  it('separates device accounts and denies operations from an old context', async () => {
    const { accessState } = await import('../src/auth/access');
    h.userId = `U${'1'.repeat(32)}`;
    h.mode = 'device';
    await accessState.initialize();
    const { holdingsStore, HoldingsStore } = await import('../src/holdings/store');
    const original = holdingsStore();
    await original.replace(account, [holding]);
    expect(new HoldingsStore().getSnapshot().document.accounts).toHaveLength(1);
    expect([...disk.keys()]).toEqual([expect.stringContaining(`:${h.userId}:device:holdings:v1`)]);
    h.userId = `U${'2'.repeat(32)}`;
    await accessState.refresh();
    expect(holdingsStore().getSnapshot().document.accounts).toEqual([]);
    expect(original.getSnapshot().document.accounts).toEqual([]);
    await expect(original.replace(account, [])).rejects.toThrow('權限已變更');
    expect(h.calls.every((action) => action === 'access.get')).toBe(true);
  });
  it('uses cloud POST actions and keeps no local portfolio copy; failed writes preserve the snapshot', async () => {
    const { accessState } = await import('../src/auth/access');
    h.userId = `U${'1'.repeat(32)}`;
    h.mode = 'cloud';
    await accessState.initialize();
    const { holdingsStore } = await import('../src/holdings/store');
    const store = holdingsStore();
    await store.reload();
    await store.replace(account, [holding]);
    expect(h.calls).toContain('holdings.replaceAccount');
    expect(disk.size).toBe(0);
    const previous = store.getSnapshot().document;
    h.failure = true;
    await expect(store.replace(account, [])).rejects.toThrow('無法確認');
    expect(store.getSnapshot().document).toEqual(previous);
    expect(disk.size).toBe(0);
  });
  it('blocks writes after a failed cloud load and preserves unreadable device data', async () => {
    const { accessState } = await import('../src/auth/access');
    h.userId = `U${'1'.repeat(32)}`;
    h.mode = 'cloud';
    await accessState.initialize();
    const { holdingsStore } = await import('../src/holdings/store');
    h.failure = true;
    const cloud = holdingsStore();
    await cloud.reload();
    await expect(cloud.replace(account, [])).rejects.toThrow('成功載入');
    h.failure = false;
    h.mode = 'device';
    await accessState.refresh();
    const { userStorage } = await import('../src/auth/storage');
    userStorage.setItem('holdings:v1', 'broken-private-data');
    const device = holdingsStore();
    expect(device.getSnapshot().error).toContain('停止寫入');
    await expect(device.replace(account, [])).rejects.toThrow();
    expect(userStorage.getItem('holdings:v1')).toBe('broken-private-data');
  });
  it('ignores an in-flight cloud read after its account context changes without an unhandled error', async () => {
    const { accessState } = await import('../src/auth/access');
    h.userId = `U${'1'.repeat(32)}`;
    h.mode = 'cloud';
    await accessState.initialize();
    const { holdingsStore } = await import('../src/holdings/store');
    let respond!: (response: Response) => void;
    const originalFetch = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        if (JSON.parse(String(init?.body)).action === 'holdings.list')
          return new Promise<Response>((resolve) => {
            respond = resolve;
          });
        return originalFetch(url, init);
      }),
    );
    const old = holdingsStore(),
      pending = old.reload();
    await vi.waitFor(() => expect(respond).toBeTypeOf('function'));
    h.userId = `U${'2'.repeat(32)}`;
    await accessState.refresh();
    respond(
      new Response(
        JSON.stringify({ ok: true, data: { version: 1, accounts: [{ account, holdings: [holding], revision: 1 }] } }),
      ),
    );
    await expect(pending).resolves.toBeUndefined();
    expect(holdingsStore().getSnapshot().document.accounts).toEqual([]);
    expect(old.getSnapshot().document.accounts).toEqual([]);
  });
});

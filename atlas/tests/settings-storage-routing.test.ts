import { beforeEach, describe, expect, it, vi } from 'vitest';

const h = vi.hoisted(() => ({
  access: { ready: true, userId: null as string | null, isAdmin: false, storageMode: 'none' as 'none' | 'device' | 'cloud', revision: '1', epoch: 1, error: null },
  listeners: new Set<() => void>(),
  values: new Map<string, string>(),
  requireCloud: vi.fn(),
}));
const context = () => `${h.access.userId}:${h.access.storageMode}:${h.access.epoch}`;
vi.mock('../src/auth/access', () => ({
  accessState: { getSnapshot: () => h.access, subscribe: (listener: () => void) => { h.listeners.add(listener); return () => h.listeners.delete(listener); } },
  initializeAccess: async () => h.access,
  requireCloudAccess: h.requireCloud,
  currentIdentity: () => h.access.userId ? { userId: h.access.userId, idToken: () => `token-${h.access.userId}` } : null,
}));
vi.mock('../src/auth/storage', () => ({
  getStorageContextKey: () => `${h.access.userId}:${h.access.storageMode}:${h.access.epoch}`,
  userStorage: {
    getItem: (key: string) => h.values.get(`${h.access.userId}:${h.access.storageMode}:${h.access.epoch}/${key}`) ?? null,
    setItem: (key: string, value: string) => { h.values.set(`${h.access.userId}:${h.access.storageMode}:${h.access.epoch}/${key}`, value); },
  },
}));
vi.mock('../src/cloud/lineIdentity', () => ({
  startLineIdentity: async () => h.access.userId ? { userId: h.access.userId, idToken: () => `token-${h.access.userId}` } : null,
}));
import { parameterStorageMessage, settingsApi, type ParameterGroup } from '../src/cloud/settingsApi';

beforeEach(() => {
  h.access = { ready: true, userId: null, isAdmin: false, storageMode: 'none', revision: '1', epoch: 1, error: null };
  h.listeners.clear(); h.values.clear(); h.requireCloud.mockReset();
  h.requireCloud.mockImplementation(async () => {
    if (!h.access.userId || h.access.storageMode !== 'cloud') throw new Error('Cloud persistence not authorized');
    return { userId: h.access.userId, idToken: () => `token-${h.access.userId}` };
  });
  vi.stubGlobal('fetch', vi.fn());
});

describe('private settings transport respects storage authorization', () => {
  it.each(['none', 'device'] as const)('runs personal group/MA operations in %s without sending any request', async storageMode => {
    h.access = { ...h.access, storageMode, userId: storageMode === 'device' ? 'user-a' : null };
    const group = await settingsApi<ParameterGroup>('groups.create', { name: '獨立分組' });
    await settingsApi('stocks.save', { ticker: 'NVDA', groupId: `custom_${group.id}`, mas: [24, 58] });
    expect(await settingsApi('stocks.list')).toEqual([expect.objectContaining({ ticker: 'NVDA', ma1: 24 })]);
    expect(fetch).not.toHaveBeenCalled(); expect(h.requireCloud).not.toHaveBeenCalled();
    expect(parameterStorageMessage()).toContain(storageMode === 'none' ? '僅本次瀏覽' : '不會上傳');
  });

  it('never carries parameters from a previous account or permission epoch', async () => {
    h.access = { ...h.access, storageMode: 'device', userId: 'user-a' };
    await settingsApi('groups.create', { name: 'A 的參數' });
    const a = context();
    h.access = { ...h.access, userId: 'user-b', epoch: 2 };
    expect(context()).not.toBe(a);
    expect(await settingsApi('groups.list')).toEqual([]);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('sends cloud operations only with a verified cloud session', async () => {
    h.access = { ...h.access, storageMode: 'cloud', userId: 'user-a' };
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ ok: true, data: [] }) } as Response);
    await settingsApi('stocks.list');
    expect(h.requireCloud).toHaveBeenCalledOnce();
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string)).toEqual({ action: 'stocks.list', idToken: 'token-user-a' });
  });

  it('blocks private watchlist cloud operations and admin actions for unapproved visitors', async () => {
    await expect(settingsApi('watchlists.save', { definition: {} })).rejects.toThrow('not authorized');
    await expect(settingsApi('admin.access.save', { userId: 'someone', storageMode: 'cloud' })).rejects.toThrow('管理員');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('allows the verified owner to administer grants while personal mode is device', async () => {
    h.access = { ...h.access, storageMode: 'device', userId: 'owner', isAdmin: true };
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ ok: true, data: [] }) } as Response);
    await settingsApi('admin.access.list');
    expect(h.requireCloud).not.toHaveBeenCalled();
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string).idToken).toBe('token-owner');
    await expect(settingsApi('admin.index.save', {})).rejects.toThrow('允許 Supabase');
  });

  it('aborts old-account requests and refuses their late results', async () => {
    h.access = { ...h.access, storageMode: 'cloud', userId: 'user-a' };
    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const request = settingsApi('groups.list');
    const rejected = expect(request).rejects.toThrow('忽略舊回應');
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    const signal = vi.mocked(fetch).mock.calls[0]![1]!.signal!;
    h.access = { ...h.access, userId: 'user-b', epoch: 2 };
    h.listeners.forEach(listener => listener());
    expect(signal.aborted).toBe(true);
    finish({ ok: true, json: async () => ({ ok: true, data: [{ name: 'A 的私人分組' }] }) } as Response);
    await rejected;
  });
});

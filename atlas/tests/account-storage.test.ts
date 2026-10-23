import 'fake-indexeddb/auto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { openDB } from 'idb';

const h = vi.hoisted(() => ({
  userId: null as string | null,
  storageMode: 'none',
  valid: true,
}));
vi.mock('../src/app/HostingMode', () => ({ SCREENER_HOSTING: true, storageName: (name: string) => `${name}-screener` }));
vi.mock('../src/cloud/lineIdentity', () => ({ refreshLineIdentity: async () => {
  const userId = h.userId;
  return userId ? { userId, idToken: () => h.valid && h.userId === userId ? `token-${userId}` : null } : null;
} }));

const A = `U${'1'.repeat(32)}`, B = `U${'2'.repeat(32)}`;
let disk: Map<string, string>;
let writes: ReturnType<typeof vi.fn<(key: string) => void>>;
beforeEach(() => {
  vi.resetModules();
  h.userId = null; h.storageMode = 'none'; h.valid = true;
  disk = new Map(); writes = vi.fn<(key: string) => void>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => disk.get(key) ?? null,
    setItem: (key: string, value: string) => { writes(key); disk.set(key, value); },
    removeItem: (key: string) => { writes(key); disk.delete(key); },
  });
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true, data: { userId: h.userId, isAdmin: false, storageMode: h.storageMode, revision: h.storageMode } }))));
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('real account-scoped browser storage', () => {
  it('keeps guest parameters and chart app state in RAM without opening IndexedDB or writing storage', async () => {
    const { accessState } = await import('../src/auth/access');
    const { userStorage } = await import('../src/auth/storage');
    const { IndexedDBStore, defaultApp } = await import('../src/storage/IndexedDBStore');
    await accessState.initialize();
    const spy = vi.spyOn(indexedDB, 'open');
    userStorage.setItem('groups', 'guest choice');
    const store = new IndexedDBStore();
    await store.saveApp({ ...defaultApp, activeSymbol: 'NVDA' });
    expect((await store.load()).app.activeSymbol).toBe('NVDA');
    expect(userStorage.getItem('groups')).toBe('guest choice');
    expect((await new IndexedDBStore().load()).app.activeSymbol).toBe(defaultApp.activeSymbol);
    expect(spy).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
  });

  it('never reads shared legacy settings; separates persistent accounts and persistence modes', async () => {
    const { accessState } = await import('../src/auth/access');
    const { userStorage, persistentDatabaseName } = await import('../src/auth/storage');
    disk.set('atlas-group-settings', 'legacy private data');
    h.userId = A; h.storageMode = 'device'; await accessState.initialize();
    expect(userStorage.getItem('atlas-group-settings')).toBeNull();
    userStorage.setItem('atlas-group-settings', 'A device');
    const nameA = persistentDatabaseName('atlas-terminal');
    expect(nameA).toBe(`atlas-terminal-screener:account:${A}:device`);
    expect(persistentDatabaseName('atlas-terminal-screener')).toBe(nameA);
    h.userId = B; await accessState.refresh();
    expect(userStorage.getItem('atlas-group-settings')).toBeNull();
    expect(persistentDatabaseName('atlas-terminal')).not.toBe(nameA);
    userStorage.setItem('atlas-group-settings', 'B device');
    h.userId = A; await accessState.refresh();
    expect(userStorage.getItem('atlas-group-settings')).toBe('A device');
    h.storageMode = 'cloud'; await accessState.refresh();
    expect(userStorage.getItem('atlas-group-settings')).toBeNull();
    expect(persistentDatabaseName('atlas-terminal')).toBe(`atlas-terminal-screener:account:${A}:cloud`);
    expect(disk.get('atlas-group-settings')).toBe('legacy private data');
  });

  it('loads only the correct account database and rejects writes queued by an old workspace', async () => {
    const { accessState } = await import('../src/auth/access');
    const { IndexedDBStore, defaultApp } = await import('../src/storage/IndexedDBStore');
    h.userId = A; h.storageMode = 'device'; await accessState.initialize();
    const a = new IndexedDBStore();
    await a.saveApp({ ...defaultApp, activeSymbol: 'NVDA' });
    h.userId = B; await accessState.refresh();
    await expect(a.saveApp({ ...defaultApp, activeSymbol: 'MSFT' })).rejects.toThrow('舊操作已取消');
    const b = new IndexedDBStore();
    expect((await b.load()).app.activeSymbol).toBe(defaultApp.activeSymbol);
    await b.close(); await a.close();
    h.userId = A; await accessState.refresh();
    const restored = new IndexedDBStore();
    expect((await restored.load()).app.activeSymbol).toBe('NVDA');
    await restored.close();
  });

  it('retains existing records without reading them after revocation; guest memory never becomes saved data', async () => {
    const { accessState } = await import('../src/auth/access');
    const { userStorage } = await import('../src/auth/storage');
    await accessState.initialize();
    userStorage.setItem('private', 'guest');
    h.userId = A; h.storageMode = 'device'; await accessState.refresh();
    expect(userStorage.getItem('private')).toBeNull();
    userStorage.setItem('private', 'A');
    const saved = new Map(disk);
    h.storageMode = 'none'; await accessState.refresh();
    expect(userStorage.getItem('private')).toBeNull();
    userStorage.setItem('private', 'temporary revoked');
    expect(disk).toEqual(saved);
    h.storageMode = 'device'; await accessState.refresh();
    expect(userStorage.getItem('private')).toBe('A');
  });

  it('blocks storage writes immediately at token expiry even before the next policy refresh', async () => {
    const { accessState } = await import('../src/auth/access');
    const { userStorage, canPersist } = await import('../src/auth/storage');
    const { IndexedDBStore, defaultApp } = await import('../src/storage/IndexedDBStore');
    h.userId = A; h.storageMode = 'device'; await accessState.initialize();
    const store = new IndexedDBStore(); await store.load();
    h.valid = false;
    expect(canPersist()).toBe(false);
    userStorage.setItem('choice', 'RAM');
    await expect(store.saveApp(defaultApp)).rejects.toThrow('登入已過期');
    expect(writes).not.toHaveBeenCalled();
    await store.close();
  });

  it('does not adopt a legacy IndexedDB database even for the owner', async () => {
    const legacy = await openDB('atlas-terminal-screener', 4, { upgrade(db) { db.createObjectStore('app'); db.createObjectStore('symbols', { keyPath: 'symbol' }); } });
    await legacy.put('app', { activeSymbol: 'LEGACY' }, 'settings');
    const { accessState } = await import('../src/auth/access');
    const { IndexedDBStore } = await import('../src/storage/IndexedDBStore');
    h.userId = B; h.storageMode = 'cloud'; await accessState.initialize();
    const store = new IndexedDBStore();
    expect((await store.load()).app.activeSymbol).not.toBe('LEGACY');
    expect(await legacy.get('app', 'settings')).toEqual({ activeSymbol: 'LEGACY' });
    await store.close(); legacy.close();
  });
});

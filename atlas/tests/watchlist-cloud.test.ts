import { beforeEach, describe, expect, it, vi } from 'vitest';
import { requireCloudAccess } from '../src/auth/access';
import { settingsApi } from '../src/cloud/settingsApi';
import { refreshCloudWatchlist, pushCloudWatchlist } from '../src/watchlists/cloud';
import { createWatchlist, recordWatchlistData, visitWatchlist } from '../src/watchlists/model';
import { WatchlistStore } from '../src/watchlists/store';
import { WatchlistCloudSync } from '../src/watchlists/sync';

const permission = vi.hoisted(() => ({ storageMode: 'cloud' as 'none' | 'device' | 'cloud', epoch: 0 }));
vi.mock('../src/auth/access', () => ({
  accessState: { getSnapshot: () => permission },
  requireCloudAccess: vi.fn(),
}));
vi.mock('../src/auth/storage', () => ({
  getStorageContextKey: () => `test:${permission.epoch}:${permission.storageMode}`,
  canPersist: () => permission.storageMode !== 'none',
  userStorage: { getItem: () => null, setItem() {} },
}));
vi.mock('../src/cloud/settingsApi', () => ({ settingsApi: vi.fn() }));
const identity = { userId: 'U-test', idToken: () => 'test-token' };
const members = (...symbols: string[]) => symbols.map((symbol) => ({ symbol, name: symbol }));
const linked = () => ({
  ...visitWatchlist(
    createWatchlist({
      name: '成長',
      market: 'US',
      group: 'universe-us',
      filters: '',
    }),
    members('AAPL'),
    '2026-10-08',
    1,
  ).list,
  cloud: { userId: identity.userId, version: '2026-10-08T09:00:00Z' },
});
beforeEach(() => {
  vi.resetAllMocks();
  permission.storageMode = 'cloud';
  permission.epoch = 0;
  vi.mocked(requireCloudAccess).mockResolvedValue(identity);
});

describe('linked cloud observation sync', () => {
  it('merges daily observations without losing the actual previous-opening comparison', async () => {
    const local = linked();
    const remote = recordWatchlistData(local, members('AAPL', 'NVDA'), '2026-10-09', '2026-10-08');
    vi.mocked(settingsApi).mockResolvedValue([{ id: local.id, definition: remote, updated_at: '2026-10-09T09:00:00Z' }]);
    const refreshed = await refreshCloudWatchlist(local);
    const opened = visitWatchlist(refreshed, members('AAPL', 'NVDA'), '2026-10-09', 2);
    expect(opened.previousDate).toBe('2026-10-08');
    expect(opened.entered.map((entry) => entry.symbol)).toEqual(['NVDA']);
    expect(opened.members[0].streak).toBe(2);
    expect(refreshed.cloud?.version).toBe('2026-10-09T09:00:00Z');
  });
  it('does not automatically access a previously linked account after the identity changes', async () => {
    const local = linked();
    vi.mocked(requireCloudAccess).mockResolvedValue({
      ...identity,
      userId: 'U-other',
    });
    expect(await refreshCloudWatchlist(local)).toBe(local);
    expect(settingsApi).not.toHaveBeenCalled();
    await expect(pushCloudWatchlist(local)).rejects.toThrow('其他 LINE 帳號');
    expect(settingsApi).not.toHaveBeenCalled();
  });
  it('retains unsynced local rules and uses the expected server version rather than overwriting a conflict', async () => {
    const local = linked();
    const edited = {
      ...local,
      filters: 'chg:3:',
      updatedAt: local.updatedAt + 1,
    };
    vi.mocked(settingsApi).mockResolvedValue([{ id: local.id, definition: local, updated_at: '2026-10-09T09:00:00Z' }]);
    expect(await refreshCloudWatchlist(edited)).toBe(edited);
    vi.mocked(settingsApi).mockRejectedValueOnce(new Error('409 version conflict'));
    await expect(pushCloudWatchlist(edited)).rejects.toThrow('409');
    expect(edited.cloud.version).toBe('2026-10-08T09:00:00Z');
    expect(settingsApi).toHaveBeenLastCalledWith('watchlists.save', {
      definition: edited,
      expectedUpdatedAt: edited.cloud.version,
    });
  });
});

function storedList() {
  const store = new WatchlistStore({ getItem: () => null, setItem: () => {} });
  const list = linked();
  store.save(list);
  return { store, list, sync: new WatchlistCloudSync(store) };
}

describe('automatic setting writes and deletion', () => {
  it.each(['none', 'device'] as const)('keeps %s operations in memory/device only, including linked-list deletion', async (mode) => {
    permission.storageMode = mode;
    const { store, list, sync } = storedList();
    expect(await refreshCloudWatchlist(list)).toBe(list);
    expect(await sync.sync(list.id)).toBe(false);
    await sync.remove(list.id);
    expect(store.getSnapshot().document.lists).toHaveLength(0);
    expect(requireCloudAccess).not.toHaveBeenCalled();
    expect(settingsApi).not.toHaveBeenCalled();
  });
  it('does not apply a cloud response to another storage context after an account switch', async () => {
    const { store, list, sync } = storedList();
    let release!: (value: unknown) => void;
    vi.mocked(settingsApi).mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const pending = sync.sync(list.id);
    await vi.waitFor(() => expect(settingsApi).toHaveBeenCalledTimes(1));
    permission.epoch++;
    release({ updated_at: 'new-version' });
    await expect(pending).rejects.toThrow('帳號或儲存權限已變更');
    expect(store.getSnapshot().document.lists[0].cloud?.version).toBe(list.cloud.version);
  });
  it('serializes rapid edits using the latest server version and preserves edits made during a request', async () => {
    const { store, list, sync } = storedList();
    let release!: () => void;
    const wait = new Promise<void>((resolve) => {
      release = resolve;
    });
    const sent: { name: string; expected: unknown }[] = [];
    vi.mocked(settingsApi).mockImplementation(async (_action, payload) => {
      const definition = payload!.definition as typeof list;
      sent.push({
        name: definition.name,
        expected: payload!.expectedUpdatedAt,
      });
      if (sent.length === 1) await wait;
      return { updated_at: `version-${sent.length}` };
    });
    const first = sync.sync(list.id);
    await vi.waitFor(() => expect(sent).toHaveLength(1));
    store.save({
      ...store.getSnapshot().document.lists[0],
      name: '已修改名稱',
      updatedAt: list.updatedAt + 1,
    });
    const second = sync.sync(list.id);
    expect(sent).toHaveLength(1);
    release();
    await Promise.all([first, second]);
    expect(sent).toEqual([
      { name: list.name, expected: list.cloud.version },
      { name: '已修改名稱', expected: 'version-1' },
    ]);
    expect(store.getSnapshot().document.lists[0]).toMatchObject({
      name: '已修改名稱',
      cloud: { version: 'version-2' },
    });
  });
  it('deletes remotely before removing locally, with the version returned by an earlier queued save', async () => {
    const { store, list, sync } = storedList();
    vi.mocked(settingsApi).mockImplementation(async (action) => {
      expect(store.getSnapshot().document.lists).toHaveLength(1);
      return action === 'watchlists.save' ? { updated_at: 'saved-version' } : [];
    });
    const save = sync.sync(list.id);
    const remove = sync.remove(list.id);
    await Promise.all([save, remove]);
    expect(settingsApi).toHaveBeenLastCalledWith('watchlists.delete', {
      id: list.id,
      expectedUpdatedAt: 'saved-version',
    });
    expect(store.getSnapshot().document.lists).toHaveLength(0);
  });
  it('retains local data on a delete conflict and allows an explicit retry', async () => {
    const { store, list, sync } = storedList();
    vi.mocked(settingsApi).mockRejectedValueOnce(new Error('409 conflict'));
    await expect(sync.remove(list.id)).rejects.toThrow('409');
    expect(store.getSnapshot().document.lists).toHaveLength(1);
    vi.mocked(settingsApi).mockResolvedValueOnce([]);
    await sync.remove(list.id);
    expect(store.getSnapshot().document.lists).toHaveLength(0);
  });
  it('cannot delete a linked list with another account and keeps local-only browser operation available', async () => {
    const { store, list, sync } = storedList();
    vi.mocked(requireCloudAccess).mockResolvedValue({
      ...identity,
      userId: 'U-other',
    });
    await expect(sync.remove(list.id)).rejects.toThrow('LINE 帳號');
    expect(settingsApi).not.toHaveBeenCalled();
    store.save({ ...list, cloud: undefined });
    permission.storageMode = 'none';
    const localSync = new WatchlistCloudSync(store);
    expect(await localSync.sync(list.id)).toBe(false);
    await localSync.remove(list.id);
    expect(store.getSnapshot().document.lists).toHaveLength(0);
    expect(settingsApi).not.toHaveBeenCalled();
  });
  it('finds and deletes an unlinked remote copy left by a timed-out create', async () => {
    const { store, list, sync } = storedList();
    store.save({ ...list, cloud: undefined });
    vi.mocked(settingsApi)
      .mockResolvedValueOnce([{ id: list.id, updated_at: 'orphan-version' }])
      .mockResolvedValueOnce([]);
    await sync.remove(list.id);
    expect(settingsApi).toHaveBeenLastCalledWith('watchlists.delete', {
      id: list.id,
      expectedUpdatedAt: 'orphan-version',
    });
    expect(store.getSnapshot().document.lists).toHaveLength(0);
  });
});

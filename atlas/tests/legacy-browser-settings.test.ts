import { beforeEach, describe, expect, it, vi } from 'vitest';

const h = vi.hoisted(() => ({
  access: { ready: true, userId: 'account-a' as string | null, storageMode: 'device' as 'none' | 'device' | 'cloud', epoch: 1 },
  disk: new Map<string, string>(),
  remote: new Map<string, { id: string; definition: unknown; updated_at: string }[]>(),
  writes: [] as string[],
  cloudCalls: [] as string[],
  refresh: vi.fn(),
  failAfterCreate: false,
}));
vi.mock('../src/auth/access', () => ({
  accessState: { getSnapshot: () => h.access },
  requireCloudAccess: async () => {
    if (!h.access.userId || h.access.storageMode !== 'cloud') throw new Error('Unauthorized');
    return { userId: h.access.userId };
  },
}));
vi.mock('../src/auth/storage', () => ({
  canPersist: () => h.access.ready && !!h.access.userId && h.access.storageMode !== 'none',
  getStorageContextKey: () => `${h.access.userId}:${h.access.storageMode}:${h.access.epoch}`,
  userStorage: {
    getItem: (key: string) => h.disk.get(`${h.access.userId}/${h.access.storageMode}/${key}`) ?? null,
    setItem: (key: string, value: string) => { h.writes.push(`${h.access.userId}/${h.access.storageMode}/${key}`); h.disk.set(`${h.access.userId}/${h.access.storageMode}/${key}`, value); },
  },
}));
vi.mock('../src/app/sitePreferences', () => ({ sitePreferences: { refresh: h.refresh } }));
vi.mock('../src/cloud/settingsApi', async () => {
  const { LocalParameters } = await import('../src/cloud/localParameters');
  const { userStorage } = await import('../src/auth/storage');
  return {
    parameterMAs: (item: { ma1: number; ma2: number; ma3: number; ma4: number }) => [item.ma1, item.ma2, item.ma3, item.ma4].filter(n => n > 0),
    settingsApi: async (action: string, payload: Record<string, unknown> = {}) => {
      if (h.access.storageMode === 'device') return new LocalParameters(userStorage).call(action, payload);
      if (!h.access.userId || h.access.storageMode !== 'cloud') throw new Error('Unauthorized');
      h.cloudCalls.push(action);
      const rows = h.remote.get(h.access.userId) ?? [];
      if (action === 'watchlists.list') return rows;
      if (action === 'watchlists.save') {
        const definition = payload.definition as { id: string };
        if (rows.some(row => row.id === definition.id) && !payload.expectedUpdatedAt) throw new Error('Cloud conflict');
        const saved = { id: definition.id, definition, updated_at: 'version-1' };
        h.remote.set(h.access.userId, [...rows.filter(row => row.id !== saved.id), saved]);
        if (h.failAfterCreate) { h.failAfterCreate = false; throw new Error('Timed out after remote create'); }
        return saved;
      }
      if (/^(groups|stocks)\./.test(action)) return new LocalParameters({
        getItem: key => h.disk.get(`server/${h.access.userId}/${key}`) ?? null,
        setItem: (key, value) => { h.disk.set(`server/${h.access.userId}/${key}`, value); },
      }).call(action, payload);
      throw new Error(`Unexpected ${action}`);
    },
  };
});
import { mergeLegacyBrowserSettings, readLegacyBrowserSettings } from '../src/storage/legacyBrowserSettings';
import { watchlistDocumentSchema, watchlistSchema } from '../src/watchlists/model';
import { watchlistStore, WATCHLIST_STORAGE_KEY } from '../src/watchlists/store';
import { PARAMETER_STORAGE_KEY } from '../src/cloud/localParameters';

const LIST_ID = '11111111-1111-4111-8111-111111111111';
const SECOND_ID = '22222222-2222-4222-8222-222222222222';
const list = (id = LIST_ID, name = '舊清單') => watchlistSchema.parse({ id, name, market: 'TW', group: 'tw_g1', kind: 'manual', filters: '', symbols: ['2330.TW'], updatedAt: 100 });
const sourceLists = (lists = [list()]) => JSON.stringify(watchlistDocumentSchema.parse({ version: 1, lists }));
const accountKey = (key: string) => `${h.access.userId}/${h.access.storageMode}/${key}`;
beforeEach(() => {
  h.access = { ready: true, userId: 'account-a', storageMode: 'device', epoch: h.access.epoch + 1 };
  h.disk.clear(); h.remote.clear(); h.writes.length = 0; h.cloudCalls.length = 0; h.refresh.mockClear();
  h.failAfterCreate = false;
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => h.disk.get(key) ?? null,
    setItem: (key: string, value: string) => { h.writes.push(key); h.disk.set(key, value); },
  });
});

describe('explicit legacy browser settings copy', () => {
  it.each(['guest', 'none', 'unverified'])('does not even inspect legacy localStorage for %s', async mode => {
    const get = vi.spyOn(localStorage, 'getItem');
    const snapshot = { entries: [], counts: { lists: 0, settings: 0, parameters: 0 }, warnings: [] };
    if (mode === 'guest') h.access.userId = null;
    if (mode === 'none') h.access.storageMode = 'none';
    if (mode === 'unverified') h.access.ready = false;
    expect(() => readLegacyBrowserSettings()).toThrow('需先驗證');
    await expect(mergeLegacyBrowserSettings(snapshot, () => {})).rejects.toThrow('需先驗證');
    expect(get).not.toHaveBeenCalled(); expect(h.writes).toEqual([]);
  });

  it('copies display choices and missing per-market filters while preserving account fields', async () => {
    const oldSite = JSON.stringify({ colors: 'us', recent: [{ symbol: 'AAPL', name: 'Apple' }] });
    h.disk.set('atlas-site-preferences-screener', oldSite);
    h.disk.set('atlas-screener-filters-v1', JSON.stringify({ TW: 'vr:2', US: 'hi:20' }));
    h.disk.set(accountKey('atlas-site-preferences-screener'), JSON.stringify({ colors: 'tw', recent: [{ symbol: 'NVDA', name: 'NVIDIA' }] }));
    h.disk.set(accountKey('atlas-screener-filters-v1'), JSON.stringify({ TW: 'vr:4' }));
    const snapshot = readLegacyBrowserSettings()!;
    expect(snapshot.counts.settings).toBe(2);
    const result = await mergeLegacyBrowserSettings(snapshot, () => {});
    expect(result).toMatchObject({ conflicts: 2, added: 2 });
    expect(JSON.parse(h.disk.get(accountKey('atlas-screener-filters-v1'))!)).toEqual({ TW: 'vr:4', US: 'hi:20' });
    expect(JSON.parse(h.disk.get(accountKey('atlas-site-preferences-screener'))!)).toEqual({ colors: 'tw', recent: [{ symbol: 'NVDA', name: 'NVIDIA' }, { symbol: 'AAPL', name: 'Apple' }] });
    expect(h.disk.get('atlas-site-preferences-screener')).toBe(oldSite);
    expect(h.refresh).toHaveBeenCalledOnce();
    h.writes.length = 0;
    expect(await mergeLegacyBrowserSettings(snapshot, () => {})).toMatchObject({ added: 0 });
    expect(h.writes).toEqual([]);
  });

  it.each(['device', 'cloud'] as const)('merges %s lists, keeps account conflicts and leaves the source available to another account', async mode => {
    h.access.storageMode = mode;
    const old = sourceLists([list(), list(SECOND_ID, '第二份')]);
    h.disk.set(WATCHLIST_STORAGE_KEY, old);
    watchlistStore().save(list(LIST_ID, '帳號的新版本'));
    const snapshot = readLegacyBrowserSettings()!;
    expect(snapshot.counts.lists).toBe(2);
    const result = await mergeLegacyBrowserSettings(snapshot, () => {});
    expect(result).toMatchObject({ added: 1, conflicts: 1 });
    expect(watchlistStore().getSnapshot().document.lists.map(value => value.name)).toEqual(['帳號的新版本', '第二份']);
    expect(h.disk.get(WATCHLIST_STORAGE_KEY)).toBe(old);
    expect(h.writes).not.toContain(WATCHLIST_STORAGE_KEY);
    if (mode === 'cloud') expect(h.remote.get('account-a')?.map(row => row.id)).toEqual([SECOND_ID]);
    h.writes.length = 0; h.cloudCalls.length = 0;
    expect(await mergeLegacyBrowserSettings(snapshot, () => {})).toMatchObject({ added: 0, conflicts: 1 });
    expect(h.writes).toEqual([]);
    h.access = { ...h.access, userId: 'account-b', epoch: h.access.epoch + 1 };
    expect(await mergeLegacyBrowserSettings(readLegacyBrowserSettings()!, () => {})).toMatchObject({ added: 2, conflicts: 0 });
    expect(watchlistStore().getSnapshot().document.lists.map(value => value.name)).toEqual(['舊清單', '第二份']);
    expect(h.disk.get(WATCHLIST_STORAGE_KEY)).toBe(old);
  });

  it('preserves a server-only cloud list instead of replacing it with the old shared list', async () => {
    h.access.storageMode = 'cloud';
    h.disk.set(WATCHLIST_STORAGE_KEY, sourceLists());
    h.remote.set('account-a', [{ id: LIST_ID, definition: list(LIST_ID, '伺服器的新版本'), updated_at: 'server-new' }]);
    expect(await mergeLegacyBrowserSettings(readLegacyBrowserSettings()!, () => {})).toMatchObject({ added: 0, conflicts: 1 });
    expect(watchlistStore().getSnapshot().document.lists[0]).toMatchObject({ name: '伺服器的新版本', cloud: { userId: 'account-a', version: 'server-new' } });
    expect(h.cloudCalls).toEqual(['watchlists.list']);
  });

  it('removes the legacy list cloud ownership before linking it to the importing account', async () => {
    h.access.storageMode = 'cloud';
    h.disk.set(WATCHLIST_STORAGE_KEY, sourceLists([{ ...list(), cloud: { userId: 'someone-else', version: 'old' } }]));
    await mergeLegacyBrowserSettings(readLegacyBrowserSettings()!, () => {});
    expect(watchlistStore().getSnapshot().document.lists[0].cloud).toEqual({ userId: 'account-a', version: 'version-1' });
    expect(JSON.parse(h.disk.get(WATCHLIST_STORAGE_KEY)!).lists[0].cloud.userId).toBe('someone-else');
  });

  it('can retry a timed-out cloud create without duplicating or overwriting its successful remote result', async () => {
    h.access.storageMode = 'cloud';
    const source = sourceLists();
    h.disk.set(WATCHLIST_STORAGE_KEY, source); h.failAfterCreate = true;
    const snapshot = readLegacyBrowserSettings()!;
    await expect(mergeLegacyBrowserSettings(snapshot, () => {})).rejects.toThrow('Timed out');
    expect(watchlistStore().getSnapshot().document.lists[0].cloud).toBeUndefined();
    expect(await mergeLegacyBrowserSettings(snapshot, () => {})).toMatchObject({ added: 0, conflicts: 0 });
    expect(h.remote.get('account-a')).toHaveLength(1);
    expect(h.cloudCalls.filter(action => action === 'watchlists.save')).toHaveLength(1);
    expect(watchlistStore().getSnapshot().document.lists[0].cloud?.userId).toBe('account-a');
    expect(h.disk.get(WATCHLIST_STORAGE_KEY)).toBe(source);
  });

  it('validates all source sections before writing any destination data and never erases malformed old records', async () => {
    h.disk.set('atlas-financials-view-v1', 'table');
    h.disk.set(WATCHLIST_STORAGE_KEY, '{not json');
    const snapshot = readLegacyBrowserSettings()!;
    expect(snapshot.warnings).toHaveLength(1);
    await expect(mergeLegacyBrowserSettings(snapshot, () => {})).rejects.toThrow(WATCHLIST_STORAGE_KEY);
    expect(h.writes).toEqual([]); expect(h.disk.get(WATCHLIST_STORAGE_KEY)).toBe('{not json');
  });

  it('imports valid device fixed parameters without overwriting newer per-stock MAs', async () => {
    h.disk.set(PARAMETER_STORAGE_KEY, JSON.stringify({ version: 1, nextId: 4, groups: [{ id: 1, name: '我的分組', ma1: 5, ma2: 10, ma3: 20, ma4: 60 }], stocks: [
      { id: 2, ticker: '2330.TW', group_id: 'custom_1', ma1: 10, ma2: 30, ma3: 0, ma4: 0 },
      { id: 3, ticker: 'NVDA', group_id: 'ungrouped', ma1: 22, ma2: 66, ma3: 0, ma4: 0 },
    ] }));
    h.disk.set(accountKey(PARAMETER_STORAGE_KEY), JSON.stringify({ version: 1, nextId: 2, groups: [], stocks: [{ id: 1, ticker: 'NVDA', group_id: 'ungrouped', ma1: 50, ma2: 200, ma3: 0, ma4: 0 }] }));
    const snapshot = readLegacyBrowserSettings()!;
    expect(await mergeLegacyBrowserSettings(snapshot, () => {})).toMatchObject({ added: 2, conflicts: 1 });
    const merged = JSON.parse(h.disk.get(accountKey(PARAMETER_STORAGE_KEY))!);
    expect(merged.stocks).toEqual(expect.arrayContaining([expect.objectContaining({ ticker: 'NVDA', ma1: 50 }), expect.objectContaining({ ticker: '2330.TW', ma1: 10 })]));
    expect(await mergeLegacyBrowserSettings(snapshot, () => {})).toMatchObject({ added: 0, conflicts: 1 });
    expect(h.cloudCalls).toEqual([]);
  });

  it('merges cloud fixed parameters through the normal checked settings API and keeps existing server MAs', async () => {
    h.access.storageMode = 'cloud';
    h.disk.set(PARAMETER_STORAGE_KEY, JSON.stringify({ version: 1, nextId: 4, groups: [{ id: 1, name: '我的分組', ma1: 5, ma2: 10, ma3: 20, ma4: 60 }], stocks: [
      { id: 2, ticker: '2330.TW', group_id: 'custom_1', ma1: 10, ma2: 30, ma3: 0, ma4: 0 },
      { id: 3, ticker: 'NVDA', group_id: 'ungrouped', ma1: 22, ma2: 66, ma3: 0, ma4: 0 },
    ] }));
    h.disk.set(`server/account-a/${PARAMETER_STORAGE_KEY}`, JSON.stringify({ version: 1, nextId: 2, groups: [], stocks: [{ id: 1, ticker: 'NVDA', group_id: 'ungrouped', ma1: 50, ma2: 200, ma3: 0, ma4: 0 }] }));
    const snapshot = readLegacyBrowserSettings()!;
    expect(await mergeLegacyBrowserSettings(snapshot, () => {})).toMatchObject({ added: 2, conflicts: 1 });
    const merged = JSON.parse(h.disk.get(`server/account-a/${PARAMETER_STORAGE_KEY}`)!);
    expect(merged.stocks).toEqual(expect.arrayContaining([expect.objectContaining({ ticker: 'NVDA', ma1: 50 }), expect.objectContaining({ ticker: '2330.TW', ma1: 10 })]));
    expect(h.cloudCalls).toEqual(['groups.list', 'stocks.list', 'groups.create', 'stocks.save']);
    expect(await mergeLegacyBrowserSettings(snapshot, () => {})).toMatchObject({ added: 0, conflicts: 1 });
  });

  it.each(['device', 'cloud'] as const)('does not recreate a grouped/dormant duplicate or shadow current-account values during legacy import (%s)', async mode => {
    h.access.storageMode = mode;
    const original = { id: 1, ticker: '2330.TW', group_id: 'ungrouped', ma1: 50, ma2: 200, ma3: 0, ma4: 0 };
    h.disk.set(PARAMETER_STORAGE_KEY, JSON.stringify({ version: 1, nextId: 4, groups: [], stocks: [
      { ...original, id: 2, ticker: '2330', ma1: 13 },
      { ...original, id: 3, ticker: '2330', group_id: 'tw_g1', ma1: 29 },
    ] }));
    const target = mode === 'cloud' ? `server/account-a/${PARAMETER_STORAGE_KEY}` : accountKey(PARAMETER_STORAGE_KEY);
    h.disk.set(target, JSON.stringify({ version: 1, nextId: 2, groups: [], stocks: [original] }));
    const snapshot = readLegacyBrowserSettings()!;
    expect(await mergeLegacyBrowserSettings(snapshot, () => {})).toMatchObject({ added: 0, conflicts: 2 });
    expect(JSON.parse(h.disk.get(target)!).stocks).toEqual([original]);
    expect(h.cloudCalls).not.toContain('stocks.save');
    h.disk.delete(target);
    await mergeLegacyBrowserSettings(snapshot, () => {});
    expect(JSON.parse(h.disk.get(target)!).stocks).toEqual([expect.objectContaining({ group_id: 'tw_g1', ma1: 29 })]);
  });

  it.each(['device', 'cloud'] as const)('preserves nondefault group MAs in device mode or prevents partial cloud import (%s)', async mode => {
    h.access.storageMode = mode;
    h.disk.set('atlas-financials-view-v1', 'table');
    h.disk.set(PARAMETER_STORAGE_KEY, JSON.stringify({ version: 1, nextId: 2, groups: [{ id: 1, name: '不同預設', ma1: 22, ma2: 66, ma3: 0, ma4: 0 }], stocks: [] }));
    const snapshot = readLegacyBrowserSettings()!;
    if (mode === 'cloud') {
      expect(snapshot.warnings).toHaveLength(1);
      await expect(mergeLegacyBrowserSettings(snapshot, () => {})).rejects.toThrow('裝置保存');
      expect(h.writes).toEqual([]); expect(h.cloudCalls).toEqual([]);
    } else {
      await mergeLegacyBrowserSettings(snapshot, () => {});
      expect(JSON.parse(h.disk.get(accountKey(PARAMETER_STORAGE_KEY))!).groups[0]).toMatchObject({ ma1: 22, ma2: 66 });
    }
  });

  it('rejects orphaned parameter group references during discovery before copying other valid settings', async () => {
    h.disk.set('atlas-financials-view-v1', 'table');
    h.disk.set(PARAMETER_STORAGE_KEY, JSON.stringify({ version: 1, nextId: 2, groups: [], stocks: [{ id: 1, ticker: 'NVDA', group_id: 'custom_99', ma1: 20, ma2: 60, ma3: 0, ma4: 0 }] }));
    const snapshot = readLegacyBrowserSettings()!;
    await expect(mergeLegacyBrowserSettings(snapshot, () => {})).rejects.toThrow(PARAMETER_STORAGE_KEY);
    expect(h.writes).toEqual([]);
  });

  it('stops after an account change rather than copying into another namespace', async () => {
    h.disk.set('atlas-financials-view-v1', 'table');
    const snapshot = readLegacyBrowserSettings()!;
    await expect(mergeLegacyBrowserSettings(snapshot, () => { h.access = { ...h.access, userId: 'account-b', epoch: h.access.epoch + 1 }; })).rejects.toThrow('帳號或儲存權限');
    expect(h.writes).toEqual([]);
  });
});

import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ParameterGroup, ParameterStock } from '../src/cloud/settingsApi';
import { createWatchlist } from '../src/watchlists/model';

const memory = () => {
  const data = new Map<string, string>();
  return { data, getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } };
};
const mocked = vi.hoisted(() => ({
  params: undefined as unknown as { data: Map<string, string>; getItem: (k: string) => string | null; setItem: (k: string, v: string) => void },
  lists: undefined as unknown as { data: Map<string, string>; getItem: (k: string) => string | null; setItem: (k: string, v: string) => void },
  recommendation: null as number[] | null,
  fail: null as string | null,
  calls: [] as string[],
}));
vi.mock('../src/auth/storage', () => ({ getStorageContextKey: () => 'account-a', canPersist: () => true, userStorage: undefined }));
vi.mock('../src/auth/access', () => ({
  accessState: { getSnapshot: () => ({ storageMode: 'device' }), subscribe: () => () => undefined },
  requireCloudAccess: async () => ({ userId: 'U' }),
}));
vi.mock('../src/cloud/useStockParameters', () => ({ stockParametersChanged: () => undefined }));
vi.mock('../src/cloud/settingsApi', async () => {
  const actual = await vi.importActual<typeof import('../src/cloud/settingsApi')>('../src/cloud/settingsApi');
  const { LocalParameters } = await vi.importActual<typeof import('../src/cloud/localParameters')>('../src/cloud/localParameters');
  return {
    ...actual,
    // The device/guest path of settingsApi: the same LocalParameters operations.
    settingsApi: async (action: string, payload: Record<string, unknown> = {}) => {
      mocked.calls.push(action);
      if (mocked.fail && action === mocked.fail) throw new Error('測試寫入失敗');
      return new LocalParameters(mocked.params).call(action, payload);
    },
  };
});
vi.mock('../src/report/maRecommendation', () => ({
  fetchMaRecommendation: async () => (mocked.recommendation ? { status: 'ok', data: mocked.recommendation } : { status: 'missing', message: '', url: '' }),
  recommendedPeriods: (data: number[]) => data,
}));
vi.mock('../src/watchlists/store', async () => {
  const actual = await vi.importActual<typeof import('../src/watchlists/store')>('../src/watchlists/store');
  let store: InstanceType<typeof actual.WatchlistStore> | undefined;
  return { ...actual, watchlistStore: () => (store ??= new actual.WatchlistStore(mocked.lists)) };
});

import {
  buildUnifiedLists,
  defaultGroupMas,
  listAvailability,
  listsContaining,
  MembershipToggler,
  optimisticChanges,
  planGroupToggle,
  symbolInAnyList,
  type UnifiedList,
} from '../src/lists/unifiedLists';
import { setListMembership } from '../src/lists/listActions';
import { LocalParameters } from '../src/cloud/localParameters';
import { watchlistStore } from '../src/watchlists/store';

const row = (id: number, ticker: string, group_id: string, mas: number[]): ParameterStock => ({ id, ticker, group_id, ma1: mas[0] ?? 0, ma2: mas[1] ?? 0, ma3: mas[2] ?? 0, ma4: mas[3] ?? 0 });
const custom: ParameterGroup = { id: 7, name: '波段', ma1: 5, ma2: 10, ma3: 20, ma4: 60 };

beforeEach(() => {
  mocked.params = memory();
  mocked.lists = mocked.lists ?? memory();
  mocked.recommendation = null;
  mocked.fail = null;
  mocked.calls = [];
});

describe('one list view over groups, watchlists and chart favourites', () => {
  const manual = createWatchlist({ name: '觀察', market: 'TW', group: 'universe-tw', kind: 'manual', filters: '', symbols: ['2330.TW'] });
  const rules = createWatchlist({ name: '強勢', market: 'US', group: 'universe-us', filters: 'rs:80' });
  const stocks = [row(1, '2330', 'tw_g1', [20, 60]), row(2, 'NVDA', 'custom_7', [10, 50]), row(3, 'AAPL', 'ungrouped', [5]), row(4, '^TWII', 'admin_index', [20])];

  it('maps every source without copying data; ungrouped and admin rows confer no membership', () => {
    const lists = buildUnifiedLists({ groups: [custom], stocks, watchlists: [manual, rules], favorites: ['AMD'], cloud: true });
    expect(lists.map((list) => [list.key, list.kind, list.feedsAlerts])).toEqual([
      ['favorites', 'favorites', false],
      [`watchlist:${manual.id}`, 'watchlist', false],
      [`watchlist:${rules.id}`, 'rules', false],
      ['group:custom_7', 'group', true],
      ['group:tw_g1', 'group', true],
      ['group:tw_g2', 'group', true],
      ['group:us_g1', 'group', true],
      ['group:us_g2', 'group', true],
      ['group:us_g3', 'group', true],
      ['group:us_g4', 'group', true],
    ]);
    expect(lists.find((list) => list.key === 'group:tw_g1')!.symbols).toEqual(['2330']);
    expect(lists.flatMap((list) => list.symbols)).not.toContain('AAPL');
    expect(lists.flatMap((list) => list.symbols)).not.toContain('^TWII');
    expect([...listsContaining(lists, '2330.TW')]).toEqual([`watchlist:${manual.id}`, 'group:tw_g1']);
    // Device/guest groups are local only: no 提醒／日報 badge.
    expect(buildUnifiedLists({ groups: [custom], stocks, watchlists: [], favorites: null, cloud: false }).some((list) => list.feedsAlerts)).toBe(false);
  });

  it('fills the star for a favourite, a manual list member or a grouped row only', () => {
    const sources = { favorites: ['AMD'], watchlists: [manual], stocks };
    expect(symbolInAnyList('AMD', sources)).toBe(true);
    expect(symbolInAnyList('2330.TW', { ...sources, stocks: [] })).toBe(true);
    expect(symbolInAnyList('NVDA', sources)).toBe(true);
    expect(symbolInAnyList('AAPL', sources)).toBe(false);
    expect(symbolInAnyList('TSLA', sources)).toBe(false);
  });

  it('explains lists that cannot take a symbol', () => {
    const [favorites, tw, rulesList, group] = buildUnifiedLists({ groups: [custom], stocks: [], watchlists: [manual, rules], favorites: [], cloud: false });
    expect(listAvailability(favorites, 'JPY=X')).toBeNull();
    expect(listAvailability(tw, 'NVDA')).toBe('僅限台股');
    expect(listAvailability(tw, '6488.TWO')).toBeNull();
    expect(listAvailability(rulesList, 'NVDA')).toBe('條件清單由篩選條件決定');
    expect(listAvailability(group, 'JPY=X')).toBe('此代號不能加入分組');
  });

  it('defaults new group MAs to the ma-selector picks, else the console default', () => {
    expect(defaultGroupMas([60, 13, 240])).toEqual([13, 60, 240]);
    expect(defaultGroupMas([20, 20, 0, 1200])).toEqual([20]);
    expect(defaultGroupMas(null)).toEqual([20, 50, 200]);
    expect(defaultGroupMas([])).toEqual([20, 50, 200]);
  });

  it('plans a join, a move with the same row and MAs, a leave to ungrouped, and no-ops', () => {
    expect(planGroupToggle([], 'TSM', 'us_g1', true, [13, 60])).toEqual({ ticker: 'TSM', mas: [13, 60], current: undefined, destination: 'us_g1', expected: [] });
    const moved = planGroupToggle(stocks, '2330.TW', 'custom_7', true, [99])!;
    expect(moved).toMatchObject({ ticker: '2330', mas: [20, 60], destination: 'custom_7' });
    expect(moved.current?.id).toBe(1);
    expect(planGroupToggle(stocks, 'AAPL', 'us_g1', true, [99])).toMatchObject({ mas: [5], destination: 'us_g1' });
    expect(planGroupToggle(stocks, '2330', 'tw_g1', false, [99])).toMatchObject({ mas: [20, 60], destination: 'ungrouped' });
    expect(planGroupToggle(stocks, '2330', 'tw_g1', true, [99])).toBeNull();
    expect(planGroupToggle(stocks, '2330', 'tw_g2', false, [99])).toBeNull();
  });

  it('a group join optimistically leaves the previous group; other lists are independent', () => {
    const lists = buildUnifiedLists({ groups: [custom], stocks, watchlists: [manual], favorites: [], cloud: true });
    const members = listsContaining(lists, '2330.TW');
    expect([...optimisticChanges(lists, members, 'group:custom_7', true)]).toEqual([['group:custom_7', true], ['group:tw_g1', false]]);
    expect([...optimisticChanges(lists, members, 'favorites', true)]).toEqual([['favorites', true]]);
  });
});

describe('optimistic membership toggling', () => {
  const lists: UnifiedList[] = [
    { key: 'favorites', kind: 'favorites', name: '圖表收藏', symbols: [], feedsAlerts: false, editable: true },
    { key: 'group:tw_g1', kind: 'group', name: 'A', symbols: ['2330.TW'], feedsAlerts: true, editable: true, groupId: 'tw_g1' },
    { key: 'group:tw_g2', kind: 'group', name: 'B', symbols: [], feedsAlerts: true, editable: true, groupId: 'tw_g2' },
  ];
  it('shows the change at once and keeps it once the stored state is reloaded', async () => {
    let stored = new Set(['group:tw_g1']);
    let release: (() => void) | undefined;
    const toggler = new MembershipToggler(
      () => new Promise<void>((resolve) => { release = () => { stored = new Set(['group:tw_g2']); resolve(); }; }),
      async () => undefined,
    );
    const pending = toggler.toggle(lists, stored, 'group:tw_g2', true);
    expect([...toggler.members(stored)].sort()).toEqual(['group:tw_g2']);
    await vi.waitFor(() => expect(release).toBeTypeOf('function'));
    release!();
    await expect(pending).resolves.toEqual({ ok: true });
    expect(toggler.pending()).toBe(false);
    expect([...toggler.members(stored)]).toEqual(['group:tw_g2']);
  });

  it('rolls back to the stored state when a write fails, and still refreshes', async () => {
    const stored = new Set(['group:tw_g1']);
    const refresh = vi.fn(async () => undefined);
    const toggler = new MembershipToggler(async () => { throw new Error('伺服器拒絕'); }, refresh);
    const result = toggler.toggle(lists, stored, 'group:tw_g2', true);
    expect(toggler.members(stored).has('group:tw_g1')).toBe(false);
    await expect(result).resolves.toEqual({ ok: false, error: '伺服器拒絕' });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect([...toggler.members(stored)]).toEqual(['group:tw_g1']);
  });

  it('runs writes one at a time and a failed earlier write does not undo a later choice', async () => {
    const order: string[] = [];
    const toggler = new MembershipToggler(async (key, checked) => {
      order.push(`${key}:${checked}`);
      if (key === 'favorites' && checked) throw new Error('x');
    }, async () => undefined);
    const base = new Set<string>();
    const first = toggler.toggle(lists, base, 'favorites', true);
    const second = toggler.toggle(lists, base, 'favorites', false);
    expect(toggler.members(base).has('favorites')).toBe(false);
    await first;
    expect(toggler.members(base).has('favorites')).toBe(false);
    await second;
    expect(order).toEqual(['favorites:true', 'favorites:false']);
  });
});

describe('membership writes through the existing stores', () => {
  const group = (groupId: string, name = groupId): UnifiedList => ({ key: `group:${groupId}`, kind: 'group', name, symbols: [], feedsAlerts: false, editable: true, groupId });
  const stocks = () => new LocalParameters(mocked.params).call('stocks.list', {}) as ParameterStock[];

  it('a new group member gets the 建議均線 periods; moving keeps the same row and MAs; leaving keeps them ungrouped', async () => {
    mocked.recommendation = [60, 13, 240];
    await expect(setListMembership(group('tw_g1', '台股權值'), '2330.TW', true)).resolves.toContain('13 / 60 / 240');
    expect(stocks()).toEqual([row(1, '2330.TW', 'tw_g1', [13, 60, 240])]);
    await setListMembership(group('tw_g2'), '2330.TW', true);
    expect(stocks()).toEqual([row(1, '2330.TW', 'tw_g2', [13, 60, 240])]);
    await setListMembership(group('tw_g2'), '2330.TW', false);
    expect(stocks()).toEqual([row(1, '2330.TW', 'ungrouped', [13, 60, 240])]);
    // Rejoining reuses the kept row instead of the recommendation.
    mocked.recommendation = [5];
    await setListMembership(group('tw_g1'), '2330', true);
    expect(stocks()).toEqual([row(1, '2330.TW', 'tw_g1', [13, 60, 240])]);
  });

  it('falls back to 20 / 50 / 200 without a recommendation, and a failed save writes nothing', async () => {
    await setListMembership(group('us_g1'), 'NVDA', true);
    expect(stocks()).toEqual([row(1, 'NVDA', 'us_g1', [20, 50, 200])]);
    mocked.fail = 'stocks.save';
    await expect(setListMembership(group('us_g2'), 'NVDA', true)).rejects.toThrow('測試寫入失敗');
    expect(stocks()).toEqual([row(1, 'NVDA', 'us_g1', [20, 50, 200])]);
  });

  it('edits only the manual watchlist symbols and refuses another market', async () => {
    const list = createWatchlist({ name: '觀察', market: 'TW', group: 'universe-tw', kind: 'manual', filters: '', symbols: ['2330.TW'] });
    const before = JSON.parse(JSON.stringify(list));
    watchlistStore().save(list);
    const unified = buildUnifiedLists({ groups: [], stocks: [], watchlists: [list], favorites: null, cloud: false })[0];
    await setListMembership(unified, '2317.TW', true);
    let saved = watchlistStore().getSnapshot().document.lists.find((entry) => entry.id === list.id)!;
    expect(saved.symbols).toEqual(['2330.TW', '2317.TW']);
    expect({ ...saved, symbols: before.symbols, updatedAt: before.updatedAt }).toEqual(before);
    await expect(setListMembership(unified, 'NVDA', true)).rejects.toThrow('僅限台股');
    await setListMembership(unified, '2330.TW', false);
    saved = watchlistStore().getSnapshot().document.lists.find((entry) => entry.id === list.id)!;
    expect(saved.symbols).toEqual(['2317.TW']);
  });

  it('chart favourites go through the workspace store', async () => {
    let watchlist = ['AMD'];
    const appStore = { getSnapshot: () => ({ app: { watchlist } }), updateApp: (patch: { watchlist: string[] }) => { watchlist = patch.watchlist; } };
    const favorites: UnifiedList = { key: 'favorites', kind: 'favorites', name: '圖表收藏', symbols: [], feedsAlerts: false, editable: true };
    await setListMembership(favorites, 'NVDA', true, appStore as never);
    await setListMembership(favorites, 'NVDA', true, appStore as never);
    expect(watchlist).toEqual(['AMD', 'NVDA']);
    await setListMembership(favorites, 'AMD', false, appStore as never);
    expect(watchlist).toEqual(['NVDA']);
  });
});

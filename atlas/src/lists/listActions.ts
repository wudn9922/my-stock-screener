import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import type { AppStore } from '../app/AppStore';
import { accessState } from '../auth/access';
import { getStorageContextKey } from '../auth/storage';
import { saveStockParameters } from '../cloud/saveStockParameters';
import { settingsApi, type ParameterGroup, type ParameterStock } from '../cloud/settingsApi';
import { stockParameterRows } from '../cloud/stockParameters';
import { stockParametersChanged } from '../cloud/useStockParameters';
import { fetchMaRecommendation, recommendedPeriods } from '../report/maRecommendation';
import { UNIVERSE_GROUP_KEYS } from '../report/universe';
import { createWatchlist, type SavedWatchlist } from '../watchlists/model';
import { watchlistStore } from '../watchlists/store';
import { watchlistCloudSync } from '../watchlists/sync';
import {
  buildUnifiedLists,
  DEFAULT_LIST_MAS,
  defaultGroupMas,
  groupKey,
  planGroupToggle,
  symbolMarket,
  watchlistKey,
  type UnifiedList,
} from './unifiedLists';

let revision = 0;
const listeners = new Set<() => void>();
/** Groups/lists changed outside the stock parameter flow (create, rename, delete). */
export function listsChanged() {
  revision++;
  listeners.forEach((listener) => listener());
}
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};

const recommendations = new Map<string, Promise<number[] | null>>();
/** The 「建議均線」 file for this symbol (the same ma-selector JSON the chart card reads), cached per page load. */
export function recommendedMas(symbol: string): Promise<number[] | null> {
  let pending = recommendations.get(symbol);
  if (!pending) {
    pending = fetchMaRecommendation(symbol).then((result) => (result.status === 'ok' ? recommendedPeriods(result.data) : null)).catch(() => null);
    recommendations.set(symbol, pending);
  }
  return pending;
}

function cloudSyncWatchlist(id: string): Promise<string | void> {
  if (accessState.getSnapshot().storageMode !== 'cloud') return Promise.resolve();
  return watchlistCloudSync(watchlistStore()).sync(id).then(
    () => undefined,
    (error) => `已保存在本機，雲端同步未完成：${error instanceof Error ? error.message : '同步失敗'}`,
  );
}

function saveWatchlist(id: string, update: (list: SavedWatchlist) => SavedWatchlist) {
  const store = watchlistStore();
  const list = store.getSnapshot().document.lists.find((entry) => entry.id === id);
  if (!list) throw new Error('此清單已不存在，請重新整理。');
  store.save(update(list));
  return cloudSyncWatchlist(id);
}

/**
 * Add/remove one symbol. A group join keeps an existing row's MAs, otherwise uses the ma-selector
 * recommendation or the console default; leaving a group keeps the row as ungrouped (never deletes MAs).
 * Returns an informational message, or throws so the caller rolls back.
 */
export async function setListMembership(list: UnifiedList, symbol: string, checked: boolean, appStore?: AppStore): Promise<string | void> {
  if (list.kind === 'favorites') {
    if (!appStore) throw new Error('圖表收藏尚未載入。');
    const current = appStore.getSnapshot().app.watchlist;
    appStore.updateApp({ watchlist: checked ? [...new Set([...current, symbol])] : current.filter((entry) => entry !== symbol) });
    return;
  }
  if (list.kind === 'watchlist' && list.watchlistId) {
    if (checked && list.market && list.market !== symbolMarket(symbol)) throw new Error(`「${list.name}」${list.market === 'TW' ? '僅限台股' : '僅限美股'}。`);
    return saveWatchlist(list.watchlistId, (current) => ({
      ...current,
      symbols: checked ? [...new Set([...current.symbols, symbol])] : current.symbols.filter((entry) => entry !== symbol),
      updatedAt: Date.now(),
    }));
  }
  if (list.kind === 'group' && list.groupId) {
    const context = getStorageContextKey();
    const stocks = await settingsApi<ParameterStock[]>('stocks.list');
    const fresh = checked && !stockParameterRows(stocks, symbol).length;
    const defaults = fresh ? defaultGroupMas(await recommendedMas(symbol)) : [...DEFAULT_LIST_MAS];
    if (context !== getStorageContextKey()) throw new Error('帳號或儲存權限已變更，請重新操作。');
    const plan = planGroupToggle(stocks, symbol, list.groupId, checked, defaults);
    if (!plan) return;
    await saveStockParameters(plan.ticker, plan.mas, plan.current, plan.destination, plan.expected);
    return fresh ? `已加入「${list.name}」，均線 ${plan.mas.join(' / ')}（可在「我的 › 清單」修改）` : undefined;
  }
  throw new Error('條件清單由篩選條件決定，不能手動加入。');
}

/** A new list: a group (提醒／日報 with cloud storage) or a manual watchlist for one market. */
export async function createList(input: { name: string; type: 'group' | 'watchlist'; market: 'TW' | 'US'; symbols?: string[] }): Promise<{ key: string; message?: string }> {
  const name = input.name.trim();
  if (!name) throw new Error('請輸入清單名稱。');
  if (input.type === 'group') {
    if (name.length > 30) throw new Error('分組名稱最多 30 個字。');
    const group = await settingsApi<ParameterGroup>('groups.create', { name });
    listsChanged();
    return { key: groupKey(`custom_${group.id}`) };
  }
  const list = createWatchlist({ name, market: input.market, group: UNIVERSE_GROUP_KEYS[input.market], kind: 'manual', filters: '', symbols: input.symbols ?? [] });
  watchlistStore().save(list);
  const message = await cloudSyncWatchlist(list.id);
  return { key: watchlistKey(list.id), message: message ?? undefined };
}

export async function renameList(list: UnifiedList, name: string): Promise<string | void> {
  if (list.kind === 'group' && list.customGroupId !== undefined) {
    await settingsApi('groups.rename', { id: list.customGroupId, name: name.trim() });
    listsChanged();
    return;
  }
  if (list.watchlistId) return saveWatchlist(list.watchlistId, (current) => ({ ...current, name: name.trim(), updatedAt: Date.now() }));
  throw new Error('固定分組不能重新命名。');
}

/** Groups must be empty first (members keep their MAs as ungrouped when removed); watchlists use the checked cloud delete. */
export async function deleteList(list: UnifiedList): Promise<void> {
  if (list.kind === 'group' && list.customGroupId !== undefined) {
    if (list.symbols.length) throw new Error('分組內仍有股票，請先移除（個股均線會保留）。');
    await settingsApi('groups.delete', { id: list.customGroupId });
    listsChanged();
    return;
  }
  if (list.watchlistId) {
    await watchlistCloudSync(watchlistStore()).remove(list.watchlistId);
    return;
  }
  throw new Error('此清單不能刪除。');
}

/** Groups, stock rows, saved watchlists and (when given) chart favourites as one list set for this account. */
export function useUnifiedLists(enabled: boolean, appStore?: AppStore) {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const context = getStorageContextKey();
  const version = useSyncExternalStore(subscribe, () => revision);
  const store = watchlistStore();
  const { document } = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const app = useSyncExternalStore(appStore?.subscribe ?? noopSubscribe, appStore?.getSnapshot ?? noSnapshot);
  const [state, setState] = useState<{ context: string; groups: ParameterGroup[]; stocks: ParameterStock[]; error: string | null }>();
  const reload = useCallback(async () => {
    const current = getStorageContextKey();
    try {
      const [groups, stocks] = await Promise.all([settingsApi<ParameterGroup[]>('groups.list'), settingsApi<ParameterStock[]>('stocks.list')]);
      if (current === getStorageContextKey()) setState({ context: current, groups, stocks, error: null });
    } catch (error) {
      if (current === getStorageContextKey())
        setState((previous) => ({
          context: current,
          groups: previous?.context === current ? previous.groups : [],
          stocks: previous?.context === current ? previous.stocks : [],
          error: error instanceof Error ? error.message : '無法載入分組',
        }));
    }
  }, []);
  useEffect(() => {
    if (enabled) void reload();
  }, [enabled, context, version, reload]);
  const loaded = state?.context === context ? state : undefined;
  const lists = buildUnifiedLists({
    groups: loaded?.groups ?? [],
    stocks: loaded?.stocks ?? [],
    watchlists: document.lists,
    favorites: app?.ready ? app.app.watchlist : null,
    cloud: access.storageMode === 'cloud',
  });
  return {
    lists,
    stocks: loaded?.stocks ?? [],
    loading: enabled && !loaded,
    error: loaded?.error ?? null,
    cloud: access.storageMode === 'cloud',
    reload: async () => {
      await reload();
      stockParametersChanged();
    },
  };
}
const noopSubscribe = () => () => undefined;
const noSnapshot = () => undefined;

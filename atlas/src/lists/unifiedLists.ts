import { FIXED_PARAMETER_GROUPS, type ParameterGroup, type ParameterStock } from '../cloud/settingsApi';
import { FIXED_STOCK_GROUP, parameterMAs, parameterTickerKey, stockParameterRows } from '../cloud/stockParameters';
import type { SavedWatchlist } from '../watchlists/model';

/**
 * One 「清單」 view over the three existing stores, without moving any data:
 * - 分組: the parameter groups (fixed tw_g1…us_g4 plus custom_<id>) and their `stocks` rows. With cloud storage
 *   these feed the daily report and the breakout/MA alerts; a stock is in at most one group (ADR-015 single row).
 * - 自選清單: saved watchlists (manual lists are editable; rule lists are read-only results of their filters).
 * - 圖表收藏: the chart workspace favourites (`app.watchlist` in the account's IndexedDB).
 * Ungrouped rows and admin_index never confer membership.
 */
export type ListKind = 'favorites' | 'group' | 'watchlist' | 'rules';
export interface UnifiedList {
  key: string;
  kind: ListKind;
  name: string;
  symbols: string[];
  /** Cloud groups are read by the daily report and the breakout/MA alert service. */
  feedsAlerts: boolean;
  /** Members can be added/removed here (rule lists follow their filters). */
  editable: boolean;
  groupId?: string;
  customGroupId?: number;
  watchlistId?: string;
  market?: 'TW' | 'US';
  /** A watchlist's own 「監控突破提醒」 switch (what MA alerts follow while every list notifies). */
  monitored?: boolean;
}

export const FAVORITES_KEY = 'favorites';
export const groupKey = (groupId: string) => `group:${groupId}`;
export const watchlistKey = (id: string) => `watchlist:${id}`;
export const isGroupKey = (key: string) => key.startsWith('group:');
/** Console default for a new stock row (ParameterConsole, watchlist maList). */
export const DEFAULT_LIST_MAS: readonly number[] = [20, 50, 200];
const PARAMETER_TICKER = /^[A-Z0-9.^_-]{1,30}$/;

export const symbolMarket = (symbol: string): 'TW' | 'US' => (/\.(TW|TWO)$/i.test(symbol) ? 'TW' : 'US');
/** The parameter API's ticker format (no FX `=X` or other symbols the stocks table rejects). */
export const canJoinGroup = (symbol: string) => PARAMETER_TICKER.test(symbol.trim().toUpperCase());

export function buildUnifiedLists(input: {
  groups: readonly ParameterGroup[];
  stocks: readonly ParameterStock[];
  watchlists: readonly SavedWatchlist[];
  favorites: readonly string[] | null;
  cloud: boolean;
}): UnifiedList[] {
  const members = (groupId: string) =>
    input.stocks.filter((row) => row.group_id === groupId).map((row) => row.ticker);
  const lists: UnifiedList[] = [];
  if (input.favorites)
    lists.push({ key: FAVORITES_KEY, kind: 'favorites', name: '圖表收藏', symbols: [...input.favorites], feedsAlerts: false, editable: true });
  for (const list of input.watchlists)
    lists.push({
      key: watchlistKey(list.id),
      kind: list.kind === 'manual' ? 'watchlist' : 'rules',
      name: list.name,
      symbols: list.kind === 'manual' ? [...list.symbols] : ((list.lastViewed ?? list.history.at(-1))?.members.map((m) => m.symbol) ?? []),
      feedsAlerts: false,
      editable: list.kind === 'manual',
      watchlistId: list.id,
      market: list.market,
      monitored: list.monitorBreakouts,
    });
  for (const group of input.groups) {
    const groupId = `custom_${group.id}`;
    lists.push({ key: groupKey(groupId), kind: 'group', name: group.name, symbols: members(groupId), feedsAlerts: input.cloud, editable: true, groupId, customGroupId: group.id });
  }
  for (const [groupId, name] of Object.entries(FIXED_PARAMETER_GROUPS))
    lists.push({ key: groupKey(groupId), kind: 'group', name, symbols: members(groupId), feedsAlerts: input.cloud, editable: true, groupId });
  return lists;
}

export function listHasSymbol(list: UnifiedList, symbol: string): boolean {
  if (list.kind === 'favorites') return list.symbols.includes(symbol);
  const key = parameterTickerKey(symbol);
  return list.symbols.some((entry) => entry === symbol || parameterTickerKey(entry) === key);
}

export function listsContaining(lists: readonly UnifiedList[], symbol: string): Set<string> {
  return new Set(lists.filter((list) => listHasSymbol(list, symbol)).map((list) => list.key));
}

/** Cheap star state without the group names: any favourite, manual/rule list member or grouped row. */
export function symbolInAnyList(symbol: string, sources: {
  favorites: readonly string[];
  watchlists: readonly SavedWatchlist[];
  stocks: readonly ParameterStock[];
}): boolean {
  if (sources.favorites.includes(symbol)) return true;
  if (stockParameterRows(sources.stocks, symbol).some((row) => row.group_id !== FIXED_STOCK_GROUP)) return true;
  return buildUnifiedLists({ groups: [], stocks: [], watchlists: sources.watchlists, favorites: null, cloud: false })
    .some((list) => listHasSymbol(list, symbol));
}

/** Whether a list can take this symbol, with the reason shown when it cannot. */
export function listAvailability(list: UnifiedList, symbol: string): string | null {
  if (!list.editable) return '條件清單由篩選條件決定';
  if (list.kind === 'group' && !canJoinGroup(symbol)) return '此代號不能加入分組';
  if (list.kind === 'watchlist' && list.market && list.market !== symbolMarket(symbol))
    return list.market === 'TW' ? '僅限台股' : '僅限美股';
  return null;
}

/** ma-selector periods (short/medium/long finals) as 1–4 distinct ascending integers, else the console default. */
export function defaultGroupMas(recommended: readonly number[] | null | undefined): number[] {
  const valid = [...new Set((recommended ?? []).filter((n) => Number.isInteger(n) && n >= 1 && n <= 1000))].sort((a, b) => a - b).slice(0, 4);
  return valid.length ? valid : [...DEFAULT_LIST_MAS];
}

export interface GroupTogglePlan {
  ticker: string;
  mas: number[];
  current: ParameterStock | undefined;
  destination: string;
  expected: ParameterStock[];
}

/**
 * Joining a group reuses the ticker's single row (its MAs move with it) or creates one with `defaults`;
 * leaving moves the row to `ungrouped`, keeping its MAs. Nothing is deleted. Null when already in that state.
 */
export function planGroupToggle(
  stocks: readonly ParameterStock[],
  symbol: string,
  groupId: string,
  checked: boolean,
  defaults: readonly number[],
): GroupTogglePlan | null {
  const rows = stockParameterRows(stocks, symbol);
  const grouped = rows.find((row) => row.group_id !== FIXED_STOCK_GROUP);
  if (checked ? grouped?.group_id === groupId : grouped?.group_id !== groupId) return null;
  return {
    ticker: rows[0]?.ticker ?? symbol.trim().toUpperCase(),
    mas: rows[0] ? parameterMAs(rows[0]) : [...defaults],
    current: rows[0],
    destination: checked ? groupId : FIXED_STOCK_GROUP,
    expected: rows,
  };
}

/** The optimistic key changes: a group join also leaves the symbol's previous group. */
export function optimisticChanges(lists: readonly UnifiedList[], members: ReadonlySet<string>, key: string, checked: boolean): Map<string, boolean> {
  const changes = new Map([[key, checked]]);
  if (checked && isGroupKey(key))
    for (const list of lists) if (list.kind === 'group' && list.key !== key && members.has(list.key)) changes.set(list.key, false);
  return changes;
}

export type ToggleResult = { ok: true; message?: string } | { ok: false; error: string };

/**
 * Optimistic membership with rollback. Writes run one at a time (one stock row per ticker); after each write,
 * success or failure, `refresh` reloads the real state, and a failure drops its optimistic overrides.
 */
export class MembershipToggler {
  private overrides = new Map<string, boolean>();
  private owners = new Map<string, symbol>();
  private queue: Promise<unknown> = Promise.resolve();
  private listeners = new Set<() => void>();
  private version = 0;
  constructor(
    private readonly execute: (key: string, checked: boolean) => Promise<string | void>,
    private readonly refresh: () => Promise<void>,
  ) {}
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getVersion = () => this.version;
  private publish() {
    this.version++;
    this.listeners.forEach((listener) => listener());
  }
  members(base: ReadonlySet<string>): Set<string> {
    const result = new Set(base);
    for (const [key, value] of this.overrides) if (value) result.add(key); else result.delete(key);
    return result;
  }
  pending() { return this.overrides.size > 0; }
  toggle(lists: readonly UnifiedList[], base: ReadonlySet<string>, key: string, checked: boolean): Promise<ToggleResult> {
    const changes = optimisticChanges(lists, this.members(base), key, checked);
    const mine = new Map<string, symbol>();
    for (const [changed, value] of changes) {
      const token = Symbol(changed);
      this.overrides.set(changed, value);
      this.owners.set(changed, token);
      mine.set(changed, token);
    }
    this.publish();
    const settle = () => {
      // A later toggle of the same list keeps its own optimistic value.
      for (const [changed, token] of mine) if (this.owners.get(changed) === token) { this.owners.delete(changed); this.overrides.delete(changed); }
      this.publish();
    };
    const task = this.queue.catch(() => undefined).then(async (): Promise<ToggleResult> => {
      try {
        const message = await this.execute(key, checked);
        await this.refresh().catch(() => undefined);
        settle();
        return message ? { ok: true, message } : { ok: true };
      } catch (error) {
        await this.refresh().catch(() => undefined);
        settle();
        return { ok: false, error: error instanceof Error ? error.message : '清單更新失敗' };
      }
    });
    this.queue = task;
    return task;
  }
}

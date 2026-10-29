import { z } from 'zod';
import { accessState } from '../auth/access';
import { canPersist, getStorageContextKey, userStorage } from '../auth/storage';
import { sitePreferences } from '../app/sitePreferences';
import { FILTER_PARAM_PATTERN } from '../pages/screenerFilters';
import { parseAggMode, parseWeightMode } from '../pages/themesMapModel';
import { PERIODS } from '../report/themes';
import { parameterDocumentSchema, PARAMETER_STORAGE_KEY } from '../cloud/localParameters';
import { parameterMAs, settingsApi, type ParameterGroup, type ParameterStock } from '../cloud/settingsApi';
import { parameterTickerKey, FIXED_STOCK_GROUP } from '../cloud/stockParameters';
import { stockParametersChanged } from '../cloud/useStockParameters';
import { watchlistDocumentSchema, watchlistSchema, type SavedWatchlist } from '../watchlists/model';
import { watchlistStore, WATCHLIST_STORAGE_KEY } from '../watchlists/store';
import { watchlistCloudSync } from '../watchlists/sync';
import type { CloudWatchlist } from '../watchlists/cloud';

// Explicit inventory of the old screener deployment. Never scan authentication, sync ownership,
// public caches, or the original drawing-lab site's records on this shared origin.
export const LEGACY_BROWSER_KEYS = [
  'atlas-site-preferences-screener', 'atlas-screener-filters-v1', 'atlas-themes-view-v1',
  'atlas-themes-map-v1-screener', 'atlas-financials-view-v1', 'atlas-financials-yoy-v1',
  WATCHLIST_STORAGE_KEY, PARAMETER_STORAGE_KEY,
] as const;
type Entry = { key: typeof LEGACY_BROWSER_KEYS[number]; value: unknown };
export interface BrowserLegacySnapshot {
  entries: Entry[];
  counts: { lists: number; settings: number; parameters: number };
  warnings: string[];
}
type MergeResult = { added: number; conflicts: number; warnings: string[] };

const record = z.record(z.string(), z.unknown());
const siteSchema = z.object({
  colors: z.enum(['tw', 'us']).optional(),
  recent: z.array(z.object({ symbol: z.string(), name: z.string() })).max(8).optional(),
});
const filtersSchema = z.object({
  TW: z.string().refine(value => value === '' || FILTER_PARAM_PATTERN.test(value)).optional(),
  US: z.string().refine(value => value === '' || FILTER_PARAM_PATTERN.test(value)).optional(),
});
const themeSchema = z.object({ period: z.enum(PERIODS).optional(), mode: z.enum(['return', 'rel']).optional() });
const mapSchema = z.object({
  weight: z.enum(['balanced', 'equal', 'size', 'turnover']).optional(),
  agg: z.enum(['weighted', 'median']).optional(),
});

function permitted() {
  const access = accessState.getSnapshot();
  if (!access.ready || !access.userId || access.storageMode === 'none' || !canPersist())
    throw new Error('需先驗證 LINE 帳號與裝置或雲端保存權限，才能讀取舊版資料。');
}
function validate(key: Entry['key'], raw: string): unknown {
  if (raw.length > 8_000_000) throw new Error('舊資料超過可安全讀取的大小。');
  if (key === 'atlas-financials-view-v1') return z.enum(['chart', 'table']).parse(raw);
  if (key === 'atlas-financials-yoy-v1') return z.enum(['shown', 'hidden']).parse(raw);
  const parsed: unknown = JSON.parse(raw);
  if (key === WATCHLIST_STORAGE_KEY) return watchlistDocumentSchema.parse(parsed);
  if (key === PARAMETER_STORAGE_KEY) return parameterDocumentSchema.parse(parsed);
  if (key === 'atlas-site-preferences-screener') return siteSchema.parse(parsed);
  if (key === 'atlas-screener-filters-v1') return filtersSchema.parse(parsed);
  if (key === 'atlas-themes-view-v1') return themeSchema.parse(parsed);
  const map = mapSchema.parse(parsed);
  return { ...(map.weight ? { weight: parseWeightMode(map.weight) } : {}), ...(map.agg ? { agg: parseAggMode(map.agg) } : {}) };
}

/** Read-only discovery. The coordinator calls this only after server verification, never on guests. */
export function readLegacyBrowserSettings(): BrowserLegacySnapshot | null {
  permitted();
  const result: BrowserLegacySnapshot = { entries: [], counts: { lists: 0, settings: 0, parameters: 0 }, warnings: [] };
  for (const key of LEGACY_BROWSER_KEYS) {
    const raw = globalThis.localStorage.getItem(key);
    if (raw === null) continue;
    try {
      const value = validate(key, raw);
      result.entries.push({ key, value });
      if (key === WATCHLIST_STORAGE_KEY) result.counts.lists += watchlistDocumentSchema.parse(value).lists.length;
      else if (key === PARAMETER_STORAGE_KEY) {
        const parameters = parameterDocumentSchema.parse(value);
        result.counts.parameters += parameters.groups.length + parameters.stocks.length;
        if (accessState.getSnapshot().storageMode === 'cloud' && parameters.groups.some(group => !defaultGroupMAs(group)))
          result.warnings.push('舊版分組包含雲端介面不支援的分組預設均線；請由管理員改為裝置保存後匯入。股票固定均線與舊資料均未變動。');
      } else result.counts.settings++;
    } catch {
      result.warnings.push(`舊版 ${key} 格式無法辨識；原始資料未變動，尚未匯入。`);
    }
  }
  return result.entries.length || result.warnings.length ? result : null;
}

function equal(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false;
  if (Array.isArray(a) || Array.isArray(b)) return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => equal(v, b[i]));
  const left = Object.keys(a), right = Object.keys(b);
  return left.length === right.length && left.every(key => Object.hasOwn(b, key) && equal((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]));
}
function withoutCloud(list: SavedWatchlist) {
  const definition = { ...list };
  delete definition.cloud;
  return definition;
}
function addFields(current: Record<string, unknown>, legacy: Record<string, unknown>, result: MergeResult) {
  const merged = { ...current };
  for (const [key, value] of Object.entries(legacy)) {
    if (!Object.hasOwn(current, key)) { merged[key] = structuredClone(value); result.added++; }
    else if (key === 'recent' && Array.isArray(current[key]) && Array.isArray(value)) {
      const recent = current[key] as { symbol: string }[];
      const missing = (value as { symbol: string }[]).filter(item => !recent.some(existing => existing.symbol === item.symbol));
      const adds = missing.slice(0, Math.max(0, 8 - recent.length));
      merged[key] = [...recent, ...adds]; result.added += adds.length;
      result.conflicts += missing.length - adds.length;
    } else if (!equal(current[key], value)) result.conflicts++;
  }
  return merged;
}

async function mergeLists(value: unknown, check: () => void, result: MergeResult) {
  const legacy = watchlistDocumentSchema.parse(value);
  const store = watchlistStore();
  store.refresh();
  if (store.getSnapshot().error) throw new Error(store.getSnapshot().error!);
  let remote: (SavedWatchlist & { cloud: { userId: string; version: string } })[] = [];
  const access = accessState.getSnapshot();
  if (access.storageMode === 'cloud' && legacy.lists.length) {
    const rows = await settingsApi<CloudWatchlist[]>('watchlists.list');
    check();
    remote = rows.map(row => {
      const parsed = watchlistSchema.parse(row.definition);
      if (parsed.id !== row.id) throw new Error('雲端清單 ID 不一致；未以舊資料覆寫。');
      return { ...parsed, cloud: { userId: access.userId!, version: row.updated_at } };
    });
  }
  for (const source of legacy.lists) {
    check();
    const account = store.getSnapshot().document.lists.find(list => list.id === source.id) ?? remote.find(list => list.id === source.id);
    if (account) {
      if (!equal(withoutCloud(account), withoutCloud(source))) result.conflicts++;
      if (!store.getSnapshot().document.lists.some(list => list.id === account.id)) store.save(account);
      const saved = remote.find(list => list.id === account.id);
      if (!account.cloud && saved && equal(withoutCloud(account), withoutCloud(saved))) store.save({ ...account, cloud: saved.cloud });
      continue;
    }
    store.save(watchlistSchema.parse(withoutCloud(source)));
    result.added++;
  }
  if (access.storageMode === 'cloud') {
    // Retrying after a timed-out create is safe: the remote inventory above prevents another create.
    for (const source of legacy.lists) {
      check();
      const local = store.getSnapshot().document.lists.find(list => list.id === source.id);
      if (local && !local.cloud && !remote.some(list => list.id === local.id) && equal(withoutCloud(local), withoutCloud(source))) {
        if (!await watchlistCloudSync(store).sync(local.id)) throw new Error('清單尚未完成雲端同步；舊資料仍保留，可重試匯入。');
        check();
      }
    }
  }
}

function sameMAs(a: ParameterGroup | ParameterStock, b: ParameterGroup | ParameterStock) {
  return a.ma1 === b.ma1 && a.ma2 === b.ma2 && a.ma3 === b.ma3 && a.ma4 === b.ma4;
}
function defaultGroupMAs(group: ParameterGroup) {
  return group.ma1 === 5 && group.ma2 === 10 && group.ma3 === 20 && group.ma4 === 60;
}
function originalStockRows(stocks: readonly ParameterStock[]) {
  return [...stocks].sort((a, b) => Number(a.group_id === FIXED_STOCK_GROUP) - Number(b.group_id === FIXED_STOCK_GROUP) || a.id - b.id);
}
function mergeDeviceParameters(value: unknown, result: MergeResult) {
  const legacy = parameterDocumentSchema.parse(value);
  const raw = userStorage.getItem(PARAMETER_STORAGE_KEY);
  const account = raw ? parameterDocumentSchema.parse(JSON.parse(raw)) : { version: 1 as const, nextId: 1, groups: [], stocks: [] };
  const merged = structuredClone(account);
  const mapping = new Map<number, number>();
  for (const source of legacy.groups) {
    let group = merged.groups.find(group => group.name === source.name);
    if (group) { if (!sameMAs(group, source)) result.conflicts++; }
    else { group = { ...source, id: merged.nextId++ }; merged.groups.push(group); result.added++; }
    mapping.set(source.id, group.id);
  }
  for (const source of originalStockRows(legacy.stocks)) {
    const custom = /^custom_(\d+)$/.exec(source.group_id);
    const groupId = custom ? `custom_${mapping.get(Number(custom[1]))}` : source.group_id;
    const existing = merged.stocks.find(stock => parameterTickerKey(stock.ticker) === parameterTickerKey(source.ticker));
    if (existing) { if (existing.group_id !== groupId || !sameMAs(existing, source)) result.conflicts++; }
    else { merged.stocks.push({ ...source, id: merged.nextId++, group_id: groupId }); result.added++; }
  }
  const validated = parameterDocumentSchema.parse(merged);
  if (!equal(account, validated)) userStorage.setItem(PARAMETER_STORAGE_KEY, JSON.stringify(validated));
}
async function mergeParameters(value: unknown, check: () => void, result: MergeResult) {
  const legacy = parameterDocumentSchema.parse(value);
  if (accessState.getSnapshot().storageMode === 'device') { mergeDeviceParameters(value, result); return; }
  const groups = await settingsApi<ParameterGroup[]>('groups.list'); check();
  const stocks = await settingsApi<ParameterStock[]>('stocks.list'); check();
  const groupIds = new Map<number, number>();
  for (const source of legacy.groups) {
    check();
    let target = groups.find(group => group.name === source.name);
    if (target) { if (!sameMAs(target, source)) result.conflicts++; }
    else {
      target = await settingsApi<ParameterGroup>('groups.create', { name: source.name }); check();
      groups.push(target); result.added++;
    }
    groupIds.set(source.id, target.id);
  }
  for (const source of originalStockRows(legacy.stocks)) {
    check();
    const custom = /^custom_(\d+)$/.exec(source.group_id);
    const mapped = custom ? groupIds.get(Number(custom[1])) : undefined;
    if (custom && !mapped) throw new Error('舊版股票參數參照不存在的分組；原始資料未變動。');
    const groupId = custom ? `custom_${mapped}` : source.group_id;
    const target = stocks.find(stock => parameterTickerKey(stock.ticker) === parameterTickerKey(source.ticker));
    if (target) {
      if (target.group_id !== groupId || !sameMAs(target, source)) result.conflicts++;
      continue;
    }
    const mas = parameterMAs(source);
    const created = await settingsApi<{ id: number }[]>('stocks.save', { ticker: source.ticker, groupId, mas }); check();
    stocks.push({ ...source, id: created[0].id, group_id: groupId }); result.added++;
  }
}

/** Explicit additive copy. Current account data is never replaced by a legacy version. */
export async function mergeLegacyBrowserSettings(snapshot: BrowserLegacySnapshot, assertCurrent: () => void): Promise<MergeResult> {
  const context = getStorageContextKey();
  const check = () => {
    permitted(); assertCurrent();
    if (context !== getStorageContextKey()) throw new Error('帳號或儲存權限已變更，舊版資料匯入已停止。');
  };
  check();
  if (snapshot.warnings.length) throw new Error(snapshot.warnings.join(' '));
  // Validate the entire source before the first destination mutation.
  const entries = snapshot.entries.map(entry => ({ ...entry, value: validate(entry.key, typeof entry.value === 'string' ? entry.value : JSON.stringify(entry.value)) }));
  if (accessState.getSnapshot().storageMode === 'cloud' && entries.some(entry => entry.key === PARAMETER_STORAGE_KEY && parameterDocumentSchema.parse(entry.value).groups.some(group => !defaultGroupMAs(group))))
    throw new Error('舊版分組預設均線無法完整寫入目前雲端介面；請改為裝置保存後匯入，未覆寫任何資料。');
  const result: MergeResult = { added: 0, conflicts: 0, warnings: [] };
  for (const { key, value } of entries) {
    check();
    if (key === WATCHLIST_STORAGE_KEY) { await mergeLists(value, check, result); check(); continue; }
    if (key === PARAMETER_STORAGE_KEY) { await mergeParameters(value, check, result); check(); stockParametersChanged(); continue; }
    const raw = userStorage.getItem(key);
    if (raw === null) { userStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value)); result.added++; continue; }
    const current = validate(key, raw);
    if (typeof value === 'string') { if (current !== value) result.conflicts++; continue; }
    const merged = addFields(record.parse(current), record.parse(value), result);
    if (!equal(current, merged)) userStorage.setItem(key, JSON.stringify(merged));
  }
  check();
  sitePreferences.refresh();
  return result;
}

import { accessState, requireCloudAccess } from '../auth/access';
import { getStorageContextKey } from '../auth/storage';
import { settingsApi } from '../cloud/settingsApi';
import { watchlistSchema, type SavedWatchlist } from './model';

export interface CloudWatchlist {
  id: string;
  definition: unknown;
  updated_at: string;
}

/** Only an explicitly linked account participates in automatic observation sync. */
export async function refreshCloudWatchlist(local: SavedWatchlist): Promise<SavedWatchlist> {
  if (!local.cloud || accessState.getSnapshot().storageMode !== 'cloud') return local;
  const context = getStorageContextKey();
  const identity = await requireCloudAccess();
  if (identity.userId !== local.cloud.userId) return local;
  assertCurrent(context);
  const rows = await settingsApi<CloudWatchlist[]>('watchlists.list');
  assertCurrent(context);
  const row = rows.find((entry) => entry.id === local.id);
  if (!row) throw new Error('此清單已從雲端刪除；本機副本保留，請確認後再同步或解除連結。');
  const remote = watchlistSchema.parse(row.definition);
  if (remote.id !== local.id) throw new Error('雲端清單 ID 不一致，保留本機資料。');
  // Unsynced local edits remain local; a save uses the previous version and can report a conflict.
  if (remote.updatedAt !== local.updatedAt || remote.filters !== local.filters || remote.group !== local.group) return local;
  const baseline = (remote.lastOpenedAt ?? 0) >= (local.lastOpenedAt ?? 0) ? remote : local;
  return {
    ...remote,
    lastViewed: baseline.lastViewed,
    lastOpenedAt: baseline.lastOpenedAt,
    cloud: { userId: identity.userId, version: row.updated_at },
  };
}

export async function pushCloudWatchlist(list: SavedWatchlist): Promise<SavedWatchlist> {
  const context = getStorageContextKey();
  const identity = await requireCloudAccess();
  assertCurrent(context);
  if (list.cloud && list.cloud.userId !== identity.userId) throw new Error('此清單連結到其他 LINE 帳號；請切回原帳號後同步。');
  const expected = list.cloud?.userId === identity.userId ? list.cloud.version : null;
  const row = await settingsApi<CloudWatchlist>('watchlists.save', { definition: list, expectedUpdatedAt: expected });
  assertCurrent(context);
  return { ...list, cloud: { userId: identity.userId, version: row.updated_at } };
}

function assertCurrent(context: string) {
  if (context !== getStorageContextKey() || accessState.getSnapshot().storageMode !== 'cloud') throw new Error('帳號或儲存權限已變更，已忽略先前的雲端回應。');
}

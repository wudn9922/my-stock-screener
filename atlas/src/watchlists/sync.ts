import { accessState, requireCloudAccess } from '../auth/access';
import { getStorageContextKey } from '../auth/storage';
import { settingsApi } from '../cloud/settingsApi';
import { pushCloudWatchlist, type CloudWatchlist } from './cloud';
import type { WatchlistStore } from './store';

/** All writes for one list share a queue, including deletion and opening snapshots. */
export class WatchlistCloudSync {
  private pending = new Map<string, Promise<unknown>>();
  private deleting = new Set<string>();
  private readonly context = getStorageContextKey();
  constructor(private store: WatchlistStore) {}
  private current() { return this.context === getStorageContextKey(); }
  private cloudAllowed() { return this.current() && accessState.getSnapshot().storageMode === 'cloud'; }
  private enqueue<T>(id: string, action: () => Promise<T>): Promise<T> {
    const task = (this.pending.get(id) ?? Promise.resolve()).catch(() => {}).then(action);
    this.pending.set(id, task);
    void task
      .finally(() => {
        if (this.pending.get(id) === task) this.pending.delete(id);
      })
      .catch(() => {});
    return task;
  }
  sync(id: string) {
    if (this.deleting.has(id) || !this.cloudAllowed()) return Promise.resolve(false);
    return this.enqueue(id, async () => {
      if (!this.cloudAllowed()) return false;
      await requireCloudAccess();
      if (!this.cloudAllowed()) return false;
      const list = this.store.getSnapshot().document.lists.find((entry) => entry.id === id);
      if (!list) return false;
      const synced = await pushCloudWatchlist(list);
      if (!this.cloudAllowed()) return false;
      const latest = this.store.getSnapshot().document.lists.find((entry) => entry.id === id);
      // An in-flight response only advances the version; newer edits/history survive.
      if (latest && (!latest.cloud || latest.cloud.userId === synced.cloud?.userId)) this.store.save({ ...latest, cloud: synced.cloud });
      return true;
    });
  }
  remove(id: string) {
    this.deleting.add(id);
    return this.enqueue(id, async () => {
      if (!this.current()) throw new Error('帳號或儲存權限已變更，請重新操作。');
      const list = this.store.getSnapshot().document.lists.find((entry) => entry.id === id);
      if (!list) return;
      if (!this.cloudAllowed()) {
        this.store.remove(id);
        return;
      }
      const identity = await requireCloudAccess();
      if (!this.cloudAllowed()) throw new Error('帳號或儲存權限已變更，請重新操作。');
      if (list.cloud) {
        if (identity.userId !== list.cloud.userId) throw new Error('請登入此清單連結的 LINE 帳號後刪除；本機與雲端清單均保留。');
        await settingsApi('watchlists.delete', {
          id,
          expectedUpdatedAt: list.cloud.version,
        });
      } else {
        // A timed-out create may have succeeded remotely without returning a version.
        const rows = await settingsApi<CloudWatchlist[]>('watchlists.list');
        if (!this.cloudAllowed()) throw new Error('帳號或儲存權限已變更，請重新操作。');
        const remote = rows.find((entry) => entry.id === id);
        if (remote)
          await settingsApi('watchlists.delete', {
            id,
            expectedUpdatedAt: remote.updated_at,
          });
      }
      if (!this.cloudAllowed()) throw new Error('帳號或儲存權限已變更，已忽略先前的雲端回應。');
      this.store.remove(id);
    }).finally(() => {
      this.deleting.delete(id);
    });
  }
}

const instances = new WeakMap<WatchlistStore, WatchlistCloudSync>();
export function watchlistCloudSync(store: WatchlistStore) {
  let sync = instances.get(store);
  if (!sync) {
    sync = new WatchlistCloudSync(store);
    instances.set(store, sync);
  }
  return sync;
}

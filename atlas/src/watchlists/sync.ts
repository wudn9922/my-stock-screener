import { startLineIdentity } from '../cloud/lineIdentity';
import { settingsApi } from '../cloud/settingsApi';
import { pushCloudWatchlist, type CloudWatchlist } from './cloud';
import type { WatchlistStore } from './store';

/** All writes for one list share a queue, including deletion and opening snapshots. */
export class WatchlistCloudSync {
  private pending = new Map<string, Promise<unknown>>();
  private deleting = new Set<string>();
  constructor(private store: WatchlistStore) {}
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
    if (this.deleting.has(id)) return Promise.resolve(false);
    return this.enqueue(id, async () => {
      const identity = await startLineIdentity();
      if (!identity) return false;
      const list = this.store.getSnapshot().document.lists.find((entry) => entry.id === id);
      if (!list) return false;
      const synced = await pushCloudWatchlist(list);
      const latest = this.store.getSnapshot().document.lists.find((entry) => entry.id === id);
      // An in-flight response only advances the version; newer edits/history survive.
      if (latest && (!latest.cloud || latest.cloud.userId === synced.cloud?.userId)) this.store.save({ ...latest, cloud: synced.cloud });
      return true;
    });
  }
  remove(id: string) {
    this.deleting.add(id);
    return this.enqueue(id, async () => {
      const list = this.store.getSnapshot().document.lists.find((entry) => entry.id === id);
      if (!list) return;
      if (list.cloud) {
        const identity = await startLineIdentity();
        if (identity?.userId !== list.cloud.userId) throw new Error('請登入此清單連結的 LINE 帳號後刪除；本機與雲端清單均保留。');
        await settingsApi('watchlists.delete', {
          id,
          expectedUpdatedAt: list.cloud.version,
        });
      } else if (await startLineIdentity()) {
        // A timed-out create may have succeeded remotely without returning a version.
        const rows = await settingsApi<CloudWatchlist[]>('watchlists.list');
        const remote = rows.find((entry) => entry.id === id);
        if (remote)
          await settingsApi('watchlists.delete', {
            id,
            expectedUpdatedAt: remote.updated_at,
          });
      }
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

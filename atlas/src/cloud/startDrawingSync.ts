import { DrawingSync, createLocalSyncMetaStore, type DrawingSyncStatus, type SyncStore } from './DrawingSync';
import { DEFAULT_DRAWINGS_URL, createDrawingCloudApi } from './drawingCloudApi';
import { accessState, currentIdentity, initializeAccess } from '../auth/access';
import { getStorageContextKey, userStorage } from '../auth/storage';

export type CloudSyncState = DrawingSyncStatus | 'off';
let current: CloudSyncState = 'off';
const listeners = new Set<() => void>();
function setState(next: CloudSyncState) { current = next; listeners.forEach((listener) => listener()); }
export const cloudSyncState = {
  subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
  get: () => current,
};
let active: { context: string; sync: DrawingSync; removeVisibility: () => void } | null = null;
export function stopDrawingSync() {
  active?.sync.stop();
  active?.removeVisibility();
  active = null;
  setState('off');
}
accessState.subscribe(() => { if (active && active.context !== getStorageContextKey()) stopDrawingSync(); });

/** Only an explicitly granted cloud account can pull or push. Timers remain bound to that identity. */
export async function startDrawingSync(store: SyncStore) {
  await initializeAccess();
  const context = getStorageContextKey(), identity = currentIdentity();
  if (!identity || accessState.getSnapshot().storageMode !== 'cloud') { stopDrawingSync(); return; }
  if (active?.context === context) return;
  stopDrawingSync();
  const userId = identity.userId;
  const isCurrent = () => context === getStorageContextKey() && currentIdentity()?.userId === userId && accessState.getSnapshot().storageMode === 'cloud';
  const api = createDrawingCloudApi(
    () => isCurrent() ? currentIdentity()!.idToken() : null,
    import.meta.env?.VITE_DRAWINGS_URL || DEFAULT_DRAWINGS_URL,
  );
  const metadata = {
    getItem: (key: string) => isCurrent() ? userStorage.getItem(key) : null,
    setItem: (key: string, value: string) => { if (isCurrent()) userStorage.setItem(key, value); },
  } as Storage;
  const sync = new DrawingSync(store, api, createLocalSyncMetaStore(userId, metadata), { onStatus: (status) => { if (isCurrent()) setState(status); } });
  let lastPull = Date.now();
  const onVisible = () => {
    if (!isCurrent() || document.visibilityState !== 'visible' || Date.now() - lastPull < 60_000) return;
    lastPull = Date.now();
    void sync.pull();
  };
  active = { context, sync, removeVisibility: () => document.removeEventListener('visibilitychange', onVisible) };
  document.addEventListener('visibilitychange', onVisible);
  await sync.start();
}

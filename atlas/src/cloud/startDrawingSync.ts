import { DrawingSync, createLocalSyncMetaStore, type DrawingSyncStatus, type SyncStore } from './DrawingSync';
import { DEFAULT_DRAWINGS_URL, createDrawingCloudApi } from './drawingCloudApi';
import { DEFAULT_LIFF_ID, getLineIdentity } from './lineIdentity';

/** `off`: not inside LINE / not logged in; drawings are stored on this device only. */
export type CloudSyncState = DrawingSyncStatus | 'off';

const PULL_INTERVAL_MS = 60_000;

let current: CloudSyncState = 'off';
const listeners = new Set<() => void>();

function setState(next: CloudSyncState) {
  current = next;
  listeners.forEach((listener) => listener());
}

/** For useSyncExternalStore. */
export const cloudSyncState = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  get: () => current,
};

let started = false;

/**
 * Starts cloud drawing sync when the page runs inside LINE with a LIFF login; otherwise does nothing.
 * Re-downloads when the page returns to the foreground (at most once a minute) to pick up edits made
 * on another device.
 */
export async function startDrawingSync(store: SyncStore) {
  if (started) return;
  started = true;
  const env = import.meta.env ?? {};
  const identity = await getLineIdentity(env.VITE_LIFF_ID || DEFAULT_LIFF_ID);
  if (!identity) return;
  const sync = new DrawingSync(
    store,
    createDrawingCloudApi(identity.idToken, env.VITE_DRAWINGS_URL || DEFAULT_DRAWINGS_URL),
    createLocalSyncMetaStore(identity.userId),
    { onStatus: setState },
  );
  await sync.start();
  let lastPull = Date.now();
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible' || Date.now() - lastPull < PULL_INTERVAL_MS) return;
    lastPull = Date.now();
    void sync.pull();
  });
}

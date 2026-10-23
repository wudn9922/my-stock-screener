/** A fresh store for each verified account/policy epoch. No anonymous/legacy data is adopted. */
import { AppStore } from './AppStore';
import { SCREENER_HOSTING } from './HostingMode';
import { getStorageContextKey } from '../auth/storage';
import { startDrawingSync, stopDrawingSync } from '../cloud/startDrawingSync';

let active: { context: string; store: AppStore; ready?: Promise<void> } | undefined;
export function getChartStore() {
  const context = getStorageContextKey();
  if (!active || active.context !== context) {
    stopDrawingSync();
    void active?.store.storage.close();
    active = { context, store: new AppStore() };
  }
  return active.store;
}
export function initializeChartStore(store = getChartStore()) {
  const entry = active;
  if (!entry || entry.store !== store) return Promise.resolve();
  entry.ready ??= store.initialize().then(() => {
    if (entry !== active || entry.context !== getStorageContextKey()) return;
    const provider = store.getSnapshot().app.provider;
    if (SCREENER_HOSTING && provider !== 'market' && provider !== 'demo') store.updateApp({ provider: 'market' });
    if (SCREENER_HOSTING) void startDrawingSync(store);
  });
  return entry.ready;
}

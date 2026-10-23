import { SCREENER_HOSTING, storageName } from '../app/HostingMode';
import { accessState } from './access';

export function getStorageContextKey() {
  const state = accessState.getSnapshot();
  return SCREENER_HOSTING ? `${state.epoch}:${state.userId ?? 'guest'}:${state.storageMode}` : 'standalone';
}
export function canPersist() {
  if (!SCREENER_HOSTING) return true;
  const state = accessState.getSnapshot();
  return state.ready && !!state.userId && state.storageMode !== 'none' && !!accessState.authenticatedIdentity();
}
export function persistentDatabaseName(base: string): string | null {
  if (!canPersist()) return null;
  const scoped = SCREENER_HOSTING && base.endsWith('-screener') ? base : storageName(base);
  const state = accessState.getSnapshot();
  return SCREENER_HOSTING ? `${scoped}:account:${state.userId}:${state.storageMode}` : scoped;
}

let context = '';
let memory = new Map<string, string>();
function currentMemory() {
  const key = getStorageContextKey();
  if (context !== key) { context = key; memory = new Map(); }
  return memory;
}
function qualified(key: string) {
  const state = accessState.getSnapshot();
  return `${storageName('atlas-account')}:${state.userId}:${state.storageMode}:${key}`;
}

/** Guest/denied actions stay in RAM. Never read or adopt the previous unscoped browser records. */
export const userStorage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> = {
  getItem(key) {
    const temporary = currentMemory();
    if (!canPersist()) return temporary.get(key) ?? null;
    return globalThis.localStorage.getItem(SCREENER_HOSTING ? qualified(key) : key);
  },
  setItem(key, value) {
    const temporary = currentMemory();
    if (!canPersist()) { temporary.set(key, String(value)); return; }
    globalThis.localStorage.setItem(SCREENER_HOSTING ? qualified(key) : key, String(value));
  },
  removeItem(key) {
    const temporary = currentMemory();
    if (!canPersist()) { temporary.delete(key); return; }
    globalThis.localStorage.removeItem(SCREENER_HOSTING ? qualified(key) : key);
  },
};

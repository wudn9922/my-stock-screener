import { getStorageContextKey, userStorage } from '../auth/storage';

/**
 * Which holdings view opens by default: every account together (`all`) or one account id. A display-only preference
 * stored through the account/mode-scoped userStorage, like the visibility preferences (none: RAM only; device and
 * cloud: this browser's account space). Users who never chose get `all`; it never touches the holdings document.
 */
export const VIEW_KEY = 'holdings-default-view-v1';
export const ALL_ACCOUNTS = 'all';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export interface HoldingsViewPreference {
  version: 1;
  /** `all` or a holding account id. */
  view: string;
}
export const defaultViewPreference = (): HoldingsViewPreference => ({ version: 1, view: ALL_ACCOUNTS });
export function validateViewPreference(value: unknown): HoldingsViewPreference {
  const row = value as Record<string, unknown> | null;
  if (!row || typeof row !== 'object' || Array.isArray(row) || row.version !== 1) throw new Error('invalid');
  if (typeof row.view !== 'string' || (row.view !== ALL_ACCOUNTS && !UUID.test(row.view))) throw new Error('invalid');
  return { version: 1, view: row.view };
}
/**
 * The view to show: this visit's explicit choice, else the remembered default. An account that no longer exists
 * (deleted, or not loaded) falls back to 全部帳號.
 */
export function resolveView(
  accountIds: readonly string[],
  preferred: string,
  session?: string,
): string {
  const wanted = session ?? preferred;
  // With a single account 全部帳號 is that account, so its import/fix/delete tools stay available unless 全部帳號 was picked.
  if (wanted === ALL_ACCOUNTS && session === undefined && accountIds.length === 1) return accountIds[0]!;
  return wanted !== ALL_ACCOUNTS && accountIds.includes(wanted) ? wanted : ALL_ACCOUNTS;
}

class ViewStore {
  private snapshot = defaultViewPreference();
  private listeners = new Set<() => void>();
  private context = getStorageContextKey();
  constructor() {
    try {
      const raw = userStorage.getItem(VIEW_KEY);
      if (raw) this.snapshot = validateViewPreference(JSON.parse(raw));
    } catch {
      /* Unreadable or invalid preference: 全部帳號; nothing is written until the user changes it. */
    }
  }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  set(view: string) {
    if (this.context !== getStorageContextKey()) return;
    this.snapshot = validateViewPreference({ version: 1, view });
    try {
      userStorage.setItem(VIEW_KEY, JSON.stringify(this.snapshot));
    } catch {
      /* Optional preference; keep it for this page when storage is blocked. */
    }
    this.listeners.forEach((listener) => listener());
  }
}
let current: { context: string; store: ViewStore } | undefined;
export function holdingsViewPreference() {
  const context = getStorageContextKey();
  if (current?.context !== context) current = { context, store: new ViewStore() };
  return current.store;
}

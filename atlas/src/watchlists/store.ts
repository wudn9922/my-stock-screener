import { watchlistDocumentSchema, watchlistSchema, type SavedWatchlist, type WatchlistDocument } from './model';
import { canPersist, getStorageContextKey, userStorage } from '../auth/storage';

export const WATCHLIST_STORAGE_KEY = 'atlas-saved-watchlists-v1';
const EMPTY: WatchlistDocument = { version: 1, lists: [] };
type Snapshot = { document: WatchlistDocument; error: string | null };
const EMPTY_SNAPSHOT: Snapshot = { document: EMPTY, error: null };

/** Separate from the existing chart IndexedDB: failures never erase either store. */
export class WatchlistStore {
  private snapshot: Snapshot = { document: EMPTY, error: null };
  private listeners = new Set<() => void>();
  constructor(private storage?: Pick<Storage, 'getItem' | 'setItem'>, private context?: string) {
    try {
      const raw = storage?.getItem(WATCHLIST_STORAGE_KEY);
      if (raw) this.snapshot = { document: watchlistDocumentSchema.parse(JSON.parse(raw)), error: null };
    } catch {
      this.snapshot = { document: EMPTY, error: '清單資料無法讀取；原始資料仍保留，請先匯出原始備份再處理。' };
    }
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  getSnapshot = () => this.isActive() ? this.snapshot : EMPTY_SNAPSHOT;
  private isActive() {
    return this.context === undefined || this.context === getStorageContextKey();
  }
  refresh() {
    if (!this.isActive()) return;
    try {
      const raw = this.storage?.getItem(WATCHLIST_STORAGE_KEY);
      this.snapshot = { document: raw ? watchlistDocumentSchema.parse(JSON.parse(raw)) : EMPTY, error: null };
    } catch {
      this.snapshot = { ...this.snapshot, error: '其他分頁寫入的清單無法辨識，保留目前結果與原始備份。' };
    }
    this.listeners.forEach((listener) => listener());
  }
  private commit(document: WatchlistDocument) {
    if (!this.isActive()) throw new Error('帳號或儲存權限已變更，請在目前帳號重新操作。');
    if (this.snapshot.error) throw new Error(this.snapshot.error);
    const parsed = watchlistDocumentSchema.parse(document);
    if (!this.storage) throw new Error('瀏覽器儲存空間不可用，清單尚未儲存。');
    try {
      this.storage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(parsed));
    } catch {
      throw new Error('清單儲存失敗（可能已滿或被停用），保留上次結果。請匯出備份。');
    }
    this.snapshot = { document: parsed, error: null };
    this.listeners.forEach((listener) => listener());
  }
  save(list: SavedWatchlist) {
    const parsed = watchlistSchema.parse(list);
    const old = this.snapshot.document.lists;
    this.commit({
      version: 1,
      lists: old.some((entry) => entry.id === parsed.id) ? old.map((entry) => (entry.id === parsed.id ? parsed : entry)) : [...old, parsed],
    });
  }
  remove(id: string) {
    this.commit({ version: 1, lists: this.snapshot.document.lists.filter((entry) => entry.id !== id) });
  }
  exportJson() {
    this.assertExportAllowed();
    return JSON.stringify(this.snapshot.document, null, 2);
  }
  exportRaw() {
    this.assertExportAllowed();
    return this.storage?.getItem(WATCHLIST_STORAGE_KEY) ?? this.exportJson();
  }
  importJson(raw: string) {
    this.commit(watchlistDocumentSchema.parse(JSON.parse(raw)));
  }
  private assertExportAllowed() {
    if (!this.isActive() || (this.context !== undefined && !canPersist())) throw new Error('目前僅允許本次瀏覽使用，尚未取得保存或匯出權限。');
  }
}

let singleton: WatchlistStore | undefined;
let singletonContext: string | undefined;
let listening = false;
export function watchlistStore() {
  const context = getStorageContextKey();
  if (!singleton || singletonContext !== context) {
    singletonContext = context;
    singleton = new WatchlistStore(userStorage, context);
  }
  if (!listening) {
    listening = true;
    globalThis.addEventListener?.('storage', (event) => {
      if (event.key?.endsWith(WATCHLIST_STORAGE_KEY)) singleton?.refresh();
    });
  }
  return singleton;
}

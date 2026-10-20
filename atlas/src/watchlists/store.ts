import { watchlistDocumentSchema, watchlistSchema, type SavedWatchlist, type WatchlistDocument } from './model';

export const WATCHLIST_STORAGE_KEY = 'atlas-saved-watchlists-v1';
const EMPTY: WatchlistDocument = { version: 1, lists: [] };
type Snapshot = { document: WatchlistDocument; error: string | null };

/** Separate from the existing chart IndexedDB: failures never erase either store. */
export class WatchlistStore {
  private snapshot: Snapshot = { document: EMPTY, error: null };
  private listeners = new Set<() => void>();
  constructor(private storage?: Pick<Storage, 'getItem' | 'setItem'>) {
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
  getSnapshot = () => this.snapshot;
  refresh() {
    try {
      const raw = this.storage?.getItem(WATCHLIST_STORAGE_KEY);
      this.snapshot = { document: raw ? watchlistDocumentSchema.parse(JSON.parse(raw)) : EMPTY, error: null };
    } catch {
      this.snapshot = { ...this.snapshot, error: '其他分頁寫入的清單無法辨識，保留目前結果與原始備份。' };
    }
    this.listeners.forEach((listener) => listener());
  }
  private commit(document: WatchlistDocument) {
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
    return JSON.stringify(this.snapshot.document, null, 2);
  }
  exportRaw() {
    return this.storage?.getItem(WATCHLIST_STORAGE_KEY) ?? this.exportJson();
  }
  importJson(raw: string) {
    this.commit(watchlistDocumentSchema.parse(JSON.parse(raw)));
  }
}

let singleton: WatchlistStore | undefined;
export function watchlistStore() {
  if (!singleton) {
    let storage: Storage | undefined;
    try {
      storage = globalThis.localStorage;
    } catch {
      /* Reported on save. */
    }
    singleton = new WatchlistStore(storage);
    globalThis.addEventListener?.('storage', (event) => {
      if (event.key === WATCHLIST_STORAGE_KEY) singleton?.refresh();
    });
  }
  return singleton;
}

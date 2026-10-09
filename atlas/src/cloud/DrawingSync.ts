import type { SymbolState } from '../storage/schema';

/**
 * Keeps each symbol's drawings in sync with the cloud copy (Supabase chart-drawings function, keyed by
 * the LINE user). One record per symbol holds every timeframe's drawings; the newest edit wins.
 *
 * - start(): downloads the cloud list once; applies records newer than the local edit, uploads local
 *   drawings the cloud does not have yet.
 * - Afterwards every local drawings change is uploaded after a short debounce. Pointer movement never
 *   reaches here: AppStore publishes drawings only on commit.
 * - pull(): re-downloads (e.g. when the app returns to the foreground) to pick up other devices' edits.
 *
 * Local edit times and the last synced content live in SyncMetaStore so a reload does not re-upload or
 * overwrite anything. IndexedDB remains the source of truth for the chart; errors only change status.
 */

export interface RemoteDrawings {
  symbol: string;
  drawings: unknown[];
  /** Epoch milliseconds of the edit that produced this copy. */
  updatedAt: number;
}

export type SaveResult =
  | { ok: true; updatedAt: number }
  /** The cloud holds a newer edit; it is returned so the caller can apply it. */
  | { ok: false; conflict: RemoteDrawings };

export interface DrawingCloudApi {
  list(): Promise<RemoteDrawings[]>;
  save(symbol: string, drawings: unknown[], updatedAt: number): Promise<SaveResult>;
}

export class CloudAuthError extends Error {}

export interface SyncStore {
  getSnapshot(): { ready: boolean; symbols: Record<string, SymbolState> };
  subscribe(listener: () => void): () => void;
  /** Validates and replaces one symbol's drawings; false when the cloud copy is invalid. */
  applyRemoteDrawings(symbol: string, drawings: unknown[]): boolean;
}

export interface SyncMeta {
  /** Epoch ms of the last local edit not known to be older than the cloud copy. */
  editedAt?: number;
  /** Digest of the drawings last confirmed equal to the cloud copy. */
  synced?: string;
}

export interface SyncMetaStore {
  get(symbol: string): SyncMeta;
  set(symbol: string, meta: SyncMeta): void;
}

export type DrawingSyncStatus = 'syncing' | 'synced' | 'error' | 'expired';

export interface DrawingSyncOptions {
  debounceMs?: number;
  retryMs?: number;
  now?: () => number;
  onStatus?: (status: DrawingSyncStatus) => void;
}

const serialize = (drawings: unknown[]) => JSON.stringify(drawings);

/** cyrb53: short, stable digest so the sync metadata stays small in localStorage. */
export function digest(text: string): string {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i++) {
    const ch = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return `${text.length.toString(36)}-${(4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36)}`;
}

export class DrawingSync {
  private readonly debounceMs: number;
  private readonly retryMs: number;
  private readonly now: () => number;
  private readonly onStatus: (status: DrawingSyncStatus) => void;
  /** Last seen drawings array per symbol (reference check before serializing). */
  private readonly seen = new Map<string, SymbolState['drawings']>();
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>();
  private readonly failed = new Set<string>();
  private inFlight = 0;
  private unsubscribe: (() => void) | null = null;
  private applyingRemote = false;
  private stopped = false;
  private status: DrawingSyncStatus | null = null;

  constructor(
    private readonly store: SyncStore,
    private readonly api: DrawingCloudApi,
    private readonly meta: SyncMetaStore,
    options: DrawingSyncOptions = {},
  ) {
    this.debounceMs = options.debounceMs ?? 1500;
    this.retryMs = options.retryMs ?? 30_000;
    this.now = options.now ?? Date.now;
    this.onStatus = options.onStatus ?? (() => undefined);
  }

  async start() {
    this.rememberCurrent();
    this.unsubscribe = this.store.subscribe(() => this.onStoreChange());
    await this.pull();
  }

  stop() {
    this.stopped = true;
    this.unsubscribe?.();
    this.unsubscribe = null;
    for (const timer of this.timers.values()) clearTimeout(timer);
    this.timers.clear();
  }

  /** Downloads the cloud copies and reconciles them with local drawings. */
  async pull() {
    if (this.stopped) return;
    this.setStatus('syncing');
    let remote: RemoteDrawings[];
    try {
      remote = await this.api.list();
    } catch (error) {
      this.fail(error);
      return;
    }
    const remoteSymbols = new Set<string>();
    for (const record of remote) {
      remoteSymbols.add(record.symbol);
      this.reconcile(record);
    }
    // Local drawings the cloud has never seen (e.g. drawn before sync existed)
    for (const [symbol, state] of Object.entries(this.store.getSnapshot().symbols)) {
      if (remoteSymbols.has(symbol) || !state.drawings.length) continue;
      if (this.meta.get(symbol).synced === digest(serialize(state.drawings))) continue;
      const meta = this.meta.get(symbol);
      this.meta.set(symbol, { ...meta, editedAt: meta.editedAt ?? this.now() });
      this.schedule(symbol, 0);
    }
    this.settle();
  }

  private reconcile(record: RemoteDrawings) {
    const local = this.store.getSnapshot().symbols[record.symbol]?.drawings ?? [];
    const localDigest = digest(serialize(local)), remoteDigest = digest(serialize(record.drawings));
    const meta = this.meta.get(record.symbol);
    if (localDigest === remoteDigest) {
      this.meta.set(record.symbol, { editedAt: record.updatedAt, synced: remoteDigest });
      return;
    }
    const localPending = meta.synced !== localDigest && (meta.editedAt ?? 0) > record.updatedAt;
    if (localPending) {
      this.schedule(record.symbol, 0);
      return;
    }
    this.applyRemote(record);
  }

  private applyRemote(record: RemoteDrawings) {
    this.applyingRemote = true;
    let applied = false;
    try {
      applied = this.store.applyRemoteDrawings(record.symbol, record.drawings);
    } finally {
      this.applyingRemote = false;
    }
    if (!applied) return;
    this.seen.set(record.symbol, this.store.getSnapshot().symbols[record.symbol]?.drawings ?? []);
    this.meta.set(record.symbol, { editedAt: record.updatedAt, synced: digest(serialize(record.drawings)) });
  }

  private rememberCurrent() {
    for (const [symbol, state] of Object.entries(this.store.getSnapshot().symbols)) this.seen.set(symbol, state.drawings);
  }

  private onStoreChange() {
    if (this.applyingRemote || this.stopped) return;
    for (const [symbol, state] of Object.entries(this.store.getSnapshot().symbols)) {
      if (this.seen.get(symbol) === state.drawings) continue;
      const previous = this.seen.get(symbol);
      this.seen.set(symbol, state.drawings);
      if (previous && serialize(previous) === serialize(state.drawings)) continue;
      this.meta.set(symbol, { ...this.meta.get(symbol), editedAt: this.now() });
      this.schedule(symbol, this.debounceMs);
    }
  }

  private schedule(symbol: string, delay: number, retry = false) {
    const existing = this.timers.get(symbol);
    if (existing) clearTimeout(existing);
    // A pending retry keeps showing the error until it succeeds
    if (!retry) this.setStatus('syncing');
    this.timers.set(symbol, setTimeout(() => {
      this.timers.delete(symbol);
      void this.push(symbol);
    }, delay));
  }

  private async push(symbol: string) {
    if (this.stopped) return;
    const drawings = this.store.getSnapshot().symbols[symbol]?.drawings ?? [];
    const text = digest(serialize(drawings));
    const meta = this.meta.get(symbol);
    if (meta.synced === text) {
      this.failed.delete(symbol);
      this.settle();
      return;
    }
    this.inFlight++;
    try {
      const result = await this.api.save(symbol, drawings, meta.editedAt ?? this.now());
      this.failed.delete(symbol);
      if (result.ok) {
        // A newer local edit may have landed while this request was in flight; it has its own timer.
        this.meta.set(symbol, { editedAt: result.updatedAt, synced: text });
      } else {
        this.applyRemote(result.conflict);
      }
    } catch (error) {
      this.failed.add(symbol);
      this.fail(error);
      if (!(error instanceof CloudAuthError)) this.schedule(symbol, this.retryMs, true);
      return;
    } finally {
      this.inFlight--;
    }
    this.settle();
  }

  private settle() {
    if (this.status === 'expired') return;
    if (this.failed.size) this.setStatus('error');
    else if (!this.timers.size && !this.inFlight) this.setStatus('synced');
  }

  private fail(error: unknown) {
    this.setStatus(error instanceof CloudAuthError ? 'expired' : 'error');
  }

  private setStatus(status: DrawingSyncStatus) {
    if (this.status === 'expired' && status !== 'expired') return;
    if (status === this.status) return;
    this.status = status;
    this.onStatus(status);
  }
}

/** SyncMetaStore in localStorage, one key per LINE user. Falls back to memory when storage is blocked. */
export function createLocalSyncMetaStore(userKey: string, storage: Storage | undefined = globalThis.localStorage): SyncMetaStore {
  const key = `atlas-drawing-sync:${userKey}`;
  let cache: Record<string, SyncMeta> = {};
  try {
    const raw = storage?.getItem(key);
    if (raw) cache = JSON.parse(raw) as Record<string, SyncMeta>;
  } catch {
    cache = {};
  }
  return {
    get: (symbol) => cache[symbol] ?? {},
    set(symbol, meta) {
      cache = { ...cache, [symbol]: meta };
      try {
        storage?.setItem(key, JSON.stringify(cache));
      } catch {
        // Memory-only for this session; the cloud copy stays authoritative for the next start.
      }
    },
  };
}

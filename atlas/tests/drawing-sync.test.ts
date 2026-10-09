import 'fake-indexeddb/auto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppStore } from '../src/app/AppStore';
import { IndexedDBStore } from '../src/storage/IndexedDBStore';
import {
  CloudAuthError,
  DrawingSync,
  createLocalSyncMetaStore,
  digest,
  type DrawingCloudApi,
  type DrawingSyncStatus,
  type RemoteDrawings,
  type SaveResult,
  type SyncMeta,
} from '../src/cloud/DrawingSync';
import { createDrawingCloudApi } from '../src/cloud/drawingCloudApi';
import { isLineInAppBrowser } from '../src/cloud/lineIdentity';
import type { Drawing } from '../src/drawing/DrawingModel';

const line = (id: string, price = 100, symbol = 'AAPL'): Drawing => ({
  id,
  symbol,
  type: 'trend',
  points: [
    { time: 1700000000, logical: 10, price, timeframe: '1D' },
    { time: 1700086400, logical: 11, price: price + 5, timeframe: '1D' },
  ],
  locked: false,
  visible: true,
  scope: { timeframes: ['1D'] },
  style: { color: '#5ca9ff', lineWidth: 2 },
});

class FakeApi implements DrawingCloudApi {
  records = new Map<string, RemoteDrawings>();
  saves: { symbol: string; drawings: unknown[]; updatedAt: number }[] = [];
  failNext: Error | null = null;
  async list() {
    if (this.failNext) {
      const error = this.failNext;
      this.failNext = null;
      throw error;
    }
    return [...this.records.values()].map((record) => structuredClone(record));
  }
  async save(symbol: string, drawings: unknown[], updatedAt: number): Promise<SaveResult> {
    if (this.failNext) {
      const error = this.failNext;
      this.failNext = null;
      throw error;
    }
    const existing = this.records.get(symbol);
    if (existing && existing.updatedAt > updatedAt) return { ok: false, conflict: structuredClone(existing) };
    this.saves.push({ symbol, drawings: structuredClone(drawings), updatedAt });
    this.records.set(symbol, { symbol, drawings: structuredClone(drawings), updatedAt });
    return { ok: true, updatedAt };
  }
}

function memoryMeta(initial: Record<string, SyncMeta> = {}) {
  const data = { ...initial };
  return { data, get: (symbol: string) => data[symbol] ?? {}, set: (symbol: string, meta: SyncMeta) => { data[symbol] = meta; } };
}

async function setup(options: { remote?: RemoteDrawings[]; meta?: Record<string, SyncMeta>; local?: Drawing[] } = {}) {
  const store = new AppStore(new IndexedDBStore('sync-' + crypto.randomUUID()));
  await store.initialize();
  if (options.local) store.updateSymbol('AAPL', (s) => ({ ...s, drawings: options.local! }));
  const api = new FakeApi();
  for (const record of options.remote ?? []) api.records.set(record.symbol, record);
  const meta = memoryMeta(options.meta);
  const statuses: DrawingSyncStatus[] = [];
  let clock = 1_000_000;
  const sync = new DrawingSync(store, api, meta, {
    debounceMs: 1000,
    retryMs: 5000,
    now: () => clock,
    onStatus: (status) => statuses.push(status),
  });
  return { store, api, meta, statuses, sync, tick: (ms: number) => { clock += ms; } };
}

describe('DrawingSync', () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }));
  afterEach(() => vi.useRealTimers());

  it('applies a newer cloud copy on start and clears local undo history for that symbol', async () => {
    const { store, sync, statuses, meta } = await setup({
      local: [line('old')],
      remote: [{ symbol: 'AAPL', drawings: [line('cloud', 120)], updatedAt: 2_000_000 }],
      meta: { AAPL: { editedAt: 1_500_000 } },
    });
    await sync.start();
    expect(store.symbol('AAPL').drawings.map((d) => d.id)).toEqual(['cloud']);
    expect(store.history('AAPL', '1D').canUndo).toBe(false);
    expect(meta.data.AAPL).toEqual({ editedAt: 2_000_000, synced: digest(JSON.stringify([line('cloud', 120)])) });
    expect(statuses.at(-1)).toBe('synced');
    sync.stop();
  });

  it('keeps and uploads a local edit that is newer than the cloud copy', async () => {
    const { store, api, sync } = await setup({
      local: [line('mine')],
      remote: [{ symbol: 'AAPL', drawings: [line('cloud')], updatedAt: 2_000_000 }],
      meta: { AAPL: { editedAt: 3_000_000, synced: 'something-else' } },
    });
    await sync.start();
    await vi.runAllTimersAsync();
    expect(store.symbol('AAPL').drawings.map((d) => d.id)).toEqual(['mine']);
    expect(api.saves).toEqual([{ symbol: 'AAPL', drawings: [line('mine')], updatedAt: 3_000_000 }]);
    sync.stop();
  });

  it('uploads drawings that exist only on this device', async () => {
    const { api, sync } = await setup({ local: [line('local-only')] });
    await sync.start();
    await vi.runAllTimersAsync();
    expect(api.saves.map((s) => s.symbol)).toEqual(['AAPL']);
    sync.stop();
  });

  it('debounces local edits into one upload stamped with the edit time', async () => {
    const { store, api, sync, statuses, tick } = await setup();
    await sync.start();
    tick(10);
    store.updateSymbol('AAPL', (s) => ({ ...s, drawings: [line('a')] }));
    tick(10);
    store.updateSymbol('AAPL', (s) => ({ ...s, drawings: [line('a'), line('b')] }));
    expect(api.saves).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1000);
    expect(api.saves).toEqual([{ symbol: 'AAPL', drawings: [line('a'), line('b')], updatedAt: 1_000_020 }]);
    expect(statuses.at(-1)).toBe('synced');
    // Non-drawing changes (preferences) do not upload again
    store.updateSymbol('AAPL', (s) => ({ ...s, preferences: { ...s.preferences, timeframe: '1W' } }));
    await vi.runAllTimersAsync();
    expect(api.saves).toHaveLength(1);
    sync.stop();
  });

  it('applies the cloud copy when the server reports a newer edit', async () => {
    const { store, api, sync } = await setup();
    await sync.start();
    api.records.set('AAPL', { symbol: 'AAPL', drawings: [line('other-device')], updatedAt: 9_000_000 });
    store.updateSymbol('AAPL', (s) => ({ ...s, drawings: [line('stale')] }));
    await vi.runAllTimersAsync();
    expect(store.symbol('AAPL').drawings.map((d) => d.id)).toEqual(['other-device']);
    expect(api.saves).toHaveLength(0);
    sync.stop();
  });

  it('retries after a network error and stops on an expired login', async () => {
    const { store, api, sync, statuses } = await setup();
    await sync.start();
    api.failNext = new Error('offline');
    store.updateSymbol('AAPL', (s) => ({ ...s, drawings: [line('a')] }));
    await vi.advanceTimersByTimeAsync(1000);
    expect(statuses.at(-1)).toBe('error');
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.saves).toHaveLength(1);
    expect(statuses.at(-1)).toBe('synced');

    api.failNext = new CloudAuthError('expired');
    store.updateSymbol('AAPL', (s) => ({ ...s, drawings: [line('b')] }));
    await vi.runAllTimersAsync();
    expect(statuses.at(-1)).toBe('expired');
    expect(api.saves).toHaveLength(1);
    sync.stop();
  });

  it('leaves local drawings untouched when the cloud copy is invalid', async () => {
    const { store, sync } = await setup({
      local: [line('keep')],
      remote: [
        { symbol: 'AAPL', drawings: [{ id: 'broken' }], updatedAt: 5_000_000 },
        { symbol: 'MSFT', drawings: [line('wrong-symbol', 100, 'AAPL')], updatedAt: 5_000_000 },
      ],
    });
    await sync.start();
    expect(store.symbol('AAPL').drawings.map((d) => d.id)).toEqual(['keep']);
    expect(store.symbol('MSFT').drawings).toEqual([]);
    sync.stop();
  });
});

describe('cloud sync helpers', () => {
  it('detects the LINE in-app browser only', () => {
    expect(isLineInAppBrowser('Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 Line/14.5.0')).toBe(true);
    expect(isLineInAppBrowser('Mozilla/5.0 (Linux; Android 14) Chrome/126.0 Mobile Safari/537.36 Line/14.6.1/IAB')).toBe(true);
    expect(isLineInAppBrowser('Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0 Safari/537.36')).toBe(false);
    expect(isLineInAppBrowser('Mozilla/5.0 Linear/1.0')).toBe(false);
  });

  it('keeps sync metadata per LINE user and survives blocked storage', () => {
    const backing = new Map<string, string>();
    const storage = {
      getItem: (key: string) => backing.get(key) ?? null,
      setItem: (key: string, value: string) => void backing.set(key, value),
    } as unknown as Storage;
    createLocalSyncMetaStore('U1', storage).set('AAPL', { editedAt: 5, synced: 'x' });
    expect(createLocalSyncMetaStore('U1', storage).get('AAPL')).toEqual({ editedAt: 5, synced: 'x' });
    expect(createLocalSyncMetaStore('U2', storage).get('AAPL')).toEqual({});
    const blocked = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } } as unknown as Storage;
    const meta = createLocalSyncMetaStore('U1', blocked);
    meta.set('AAPL', { editedAt: 1 });
    expect(meta.get('AAPL')).toEqual({ editedAt: 1 });
  });

  it('calls the edge function with the ID token and maps 401 to CloudAuthError', async () => {
    const calls: unknown[] = [];
    const responses = [
      { status: 200, body: { ok: true, records: [{ symbol: 'AAPL', drawings: [], updatedAt: 7 }, { symbol: 1 }] } },
      { status: 200, body: { ok: false, conflict: { symbol: 'AAPL', drawings: [], updatedAt: 9 } } },
      { status: 401, body: { ok: false, error: 'Invalid LINE ID Token' } },
    ];
    const fetcher = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      calls.push(JSON.parse(String(init?.body)));
      const next = responses.shift()!;
      return new Response(JSON.stringify(next.body), { status: next.status });
    }) as unknown as typeof fetch;
    const api = createDrawingCloudApi(() => 'token', 'https://example.test/fn', fetcher);
    expect(await api.list()).toEqual([{ symbol: 'AAPL', drawings: [], updatedAt: 7 }]);
    expect(await api.save('AAPL', [], 8)).toEqual({ ok: false, conflict: { symbol: 'AAPL', drawings: [], updatedAt: 9 } });
    await expect(api.list()).rejects.toBeInstanceOf(CloudAuthError);
    expect(calls[0]).toEqual({ action: 'atlas.list', idToken: 'token' });
    expect(calls[1]).toEqual({ action: 'atlas.save', idToken: 'token', symbol: 'AAPL', drawings: [], updatedAt: 8 });
    await expect(createDrawingCloudApi(() => null, 'https://example.test/fn', fetcher).list()).rejects.toBeInstanceOf(CloudAuthError);
  });
});

import 'fake-indexeddb/auto';
import { describe, expect, it, vi } from 'vitest';
import { discordDrawingAvailability, priceAlertDrawing } from '../src/alerts/priceAlertDrawing';
import { AppStore } from '../src/app/AppStore';
import { IndexedDBStore } from '../src/storage/IndexedDBStore';
import { drawingSchema } from '../src/storage/schema';
import { createDrawingCloudApi } from '../src/cloud/drawingCloudApi';
import { DrawingSync, type SyncMeta } from '../src/cloud/DrawingSync';
const anchor = { time: 1793827800, logical: 125, timeframe: '1D' as const };
describe('Discord monitoring uses the existing horizontal drawing pipeline', () => {
  it('creates a canonical, visible, 2px horizontal line scoped to this stock/timeframe', () => {
    const { drawing, created } = priceAlertDrawing('AAPL', '1D', 123.45, anchor, []);
    expect(created).toBe(true);
    expect(drawingSchema.parse(drawing)).toMatchObject({ symbol: 'AAPL', type: 'horizontal', visible: true, locked: false, scope: { timeframes: ['1D'] }, points: [{ ...anchor, price: 123.45 }], style: { lineWidth: 2 } });
    expect(drawing.points[0]).not.toHaveProperty('x');
  });
  it('reuses an existing visible same-price line; preserves locked/foreign-frame drawings and custom style', () => {
    const first = priceAlertDrawing('AAPL', '1D', 123.45, anchor, [], { color: '#ff0000', lineWidth: 3 }).drawing;
    const locked = { ...first, locked: true };
    expect(priceAlertDrawing('AAPL', '1D', 123.45, anchor, [locked])).toEqual({ created: false, drawing: locked });
    expect(priceAlertDrawing('AAPL', '1D', 123.45, anchor, [{ ...first, visible: false }]).created).toBe(true);
    expect(priceAlertDrawing('NVDA', '1D', 123.45, anchor, [first]).created).toBe(true);
    expect(priceAlertDrawing('AAPL', '1W', 123.45, { ...anchor, timeframe: '1W' }, [first]).created).toBe(true);
    expect(priceAlertDrawing('AAPL', '1D', 123.45, anchor, [{ ...first, scope: { timeframes: 'all' }, points: [{ ...first.points[0]!, timeframe: '1W' }] }]).created).toBe(true);
    expect(first.style).toMatchObject({ color: '#ff0000', lineWidth: 3 });
  });
  it('refuses invalid prices and anchors', () => {
    for (const price of [0, -1, NaN, Infinity]) expect(() => priceAlertDrawing('AAPL', '1D', price, anchor, [])).toThrow('有效提醒價格');
    expect(() => priceAlertDrawing('AAPL', '1W', 10, anchor, [])).toThrow('尚未就緒');
    expect(() => priceAlertDrawing('AAPL', '1D', 10, { ...anchor, time: NaN }, [])).toThrow('尚未就緒');
  });
  it('permits cloud only after successful drawing sync and a loaded chart', () => {
    for (const storageMode of ['none', 'device'] as const) expect(discordDrawingAvailability({ ready: true, userId: 'test', storageMode }, 'synced', true)).toContain('雲端保存授權');
    expect(discordDrawingAvailability({ ready: true, userId: null, storageMode: 'cloud' }, 'synced', true)).toContain('雲端保存授權');
    const access = { ready: true, userId: 'test', storageMode: 'cloud' as const };
    for (const sync of ['off', 'syncing', 'error', 'expired'] as const) expect(discordDrawingAvailability(access, sync, true)).not.toBeNull();
    expect(discordDrawingAvailability(access, 'synced', false)).toContain('K 線載入');
    expect(discordDrawingAvailability(access, 'synced', true)).toBeNull();
  });
  it('commits through AppStore, synchronizes the existing atlas.save payload and supports undo', async () => {
    const storage = new IndexedDBStore('price-alert-' + crypto.randomUUID()), store = new AppStore(storage);
    await store.initialize();
    const calls: Record<string, unknown>[] = [];
    const fetcher = vi.fn(async (_: string | URL | Request, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      calls.push(body);
      return Response.json(body.action === 'atlas.list' ? { ok: true, records: [] } : { ok: true, updatedAt: body.updatedAt });
    });
    const meta = new Map<string, SyncMeta>();
    const sync = new DrawingSync(store, createDrawingCloudApi(() => 'synthetic-token', 'https://edge.invalid/chart-drawings', fetcher), { get: symbol => meta.get(symbol) ?? {}, set: (symbol, value) => { meta.set(symbol, value); } }, { debounceMs: 1 });
    try {
      await sync.start();
      const next = priceAlertDrawing('AAPL', '1D', 123.45, anchor, []);
      store.commitDrawings('AAPL', [next.drawing], 'create price alert', '1D');
      await vi.waitFor(() => expect(calls.filter(c => c.action === 'atlas.save')).toHaveLength(1));
      expect(calls.find(c => c.action === 'atlas.save')).toMatchObject({ symbol: 'AAPL', idToken: 'synthetic-token', drawings: [{ id: next.drawing.id, type: 'horizontal', points: [{ price: 123.45, timeframe: '1D' }] }] });
      store.undo('AAPL', '1D');
      await vi.waitFor(() => expect(calls.filter(c => c.action === 'atlas.save')).toHaveLength(2));
      expect(calls.filter(c => c.action === 'atlas.save').at(-1)?.drawings).toEqual([]);
    } finally { sync.stop(); await store.flush(); await storage.close(); }
  });
});

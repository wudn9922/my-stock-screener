import { DEFAULT_FIB_LEVELS } from '../src/tools/Fibonacci';
import 'fake-indexeddb/auto';
import { describe, it, expect } from 'vitest';
import { AppStore } from '../src/app/AppStore';
import { IndexedDBStore } from '../src/storage/IndexedDBStore';
import { emptySymbol, parseSettings } from '../src/storage/schema';
import { sma } from '../src/indicators/MovingAverage';
import type { IndicatorInstance } from '../src/indicators/IndicatorRegistry';
import type { Drawing } from '../src/drawing/DrawingModel';
const indicator = (symbol: string, period: number, locked = true): IndicatorInstance => ({
  id: crypto.randomUUID(),
  symbol,
  type: 'SMA',
  period,
  source: 'close',
  locked,
  visible: true,
  lineWidth: 2,
  color: '#f0b35b',
  scope: {},
});
const drawing: Drawing = {
  id: 'line',
  symbol: 'AAPL',
  type: 'trend',
  points: [
    { time: 1700000000, logical: 10, price: 100, timeframe: '1D' },
    { time: 1700086400, logical: 11, price: 110, timeframe: '1D' },
  ],
  locked: true,
  visible: true,
  scope: { timeframes: ['1D'] },
  style: { color: '#5ca9ff', lineWidth: 2 },
};
const drawingFor = (timeframe: '1D' | '1W' | '1M', id: string, price = 100): Drawing => ({
  id, symbol: 'AAPL', type: 'trend',
  points: [
    { time: 1700000000, logical: 10, price, timeframe },
    { time: 1700086400, logical: 11, price: price + 5, timeframe },
  ],
  locked: false, visible: true, scope: { timeframes: [timeframe] },
  style: { color: '#5ca9ff', lineWidth: 2 },
});
async function setup() {
  const db = new IndexedDBStore('test-' + crypto.randomUUID()),
    store = new AppStore(db);
  await store.initialize();
  return { db, store };
}
describe('per-symbol persistence and settings', () => {
  it('isolates AAPL indicators and drawings across 1D/1W/1M and reloads every timeframe bucket', async () => {
    const { db, store } = await setup();
    const records = [
      { timeframe: '1D' as const, period: 24 },
      { timeframe: '1W' as const, period: 58 },
      { timeframe: '1M' as const, period: 43 },
    ];
    for (const [index, record] of records.entries()) {
      store.updateSymbol('AAPL', s => ({ ...s, preferences: { ...s.preferences, timeframe: record.timeframe } }));
      store.addIndicator(indicator('AAPL', record.period, false));
      store.commitDrawings('AAPL', [...store.symbol('AAPL').drawings, drawingFor(record.timeframe, `line-${index}`)], 'create');
      expect(store.symbol('AAPL').indicators.at(-1)?.scope.timeframe).toBe(record.timeframe);
      expect(store.symbol('AAPL').drawings.at(-1)?.scope.timeframes).toEqual([record.timeframe]);
    }
    expect(store.symbol('AAPL').drawings).toHaveLength(3);
    expect(store.symbol('AAPL').indicators.map(i => i.period)).toEqual([24, 58, 43]);
    const dailyId = store.symbol('AAPL').indicators[0].id;
    store.updateIndicator('AAPL', dailyId, { period: 99 });
    store.removeIndicator('AAPL', dailyId);
    expect(store.symbol('AAPL').indicators[0].period).toBe(24);
    expect(store.symbol('AAPL').indicators).toHaveLength(3);
    await store.flush();
    const reload = new AppStore(db);
    await reload.initialize();
    for (const record of records) {
      expect(reload.symbol('AAPL').indicators.filter(i => i.scope.timeframe === record.timeframe).map(i => i.period)).toEqual([record.period]);
      expect(reload.symbol('AAPL').drawings.filter(d => d.scope.timeframes.includes(record.timeframe))).toHaveLength(1);
    }
    await db.close();
  });

  it('undo and redo merge only the current timeframe bucket using latest other-timeframe drawings', async () => {
    const { db, store } = await setup();
    const daily = drawingFor('1D', 'daily'), weekly = drawingFor('1W', 'weekly', 200);
    store.commitDrawings('AAPL', [daily], 'create');
    store.updateSymbol('AAPL', s => ({ ...s, preferences: { ...s.preferences, timeframe: '1W' } }));
    store.commitDrawings('AAPL', [...store.symbol('AAPL').drawings, weekly], 'create');
    store.updateSymbol('AAPL', s => ({ ...s, preferences: { ...s.preferences, timeframe: '1D' } }));
    store.commitDrawings('AAPL', store.symbol('AAPL').drawings.map(d => d.id === daily.id
      ? { ...d, points: d.points.map(p => ({ ...p, price: p.price + 20 })) }
      : d), 'edit P1');
    store.updateSymbol('AAPL', s => ({ ...s, preferences: { ...s.preferences, timeframe: '1W' } }));
    store.commitDrawings('AAPL', store.symbol('AAPL').drawings.map(d => d.id === weekly.id
      ? { ...d, points: d.points.map(p => ({ ...p, price: p.price + 30 })) }
      : d), 'edit P1');

    store.updateSymbol('AAPL', s => ({ ...s, preferences: { ...s.preferences, timeframe: '1D' } }));
    expect(store.history().canUndo).toBe(true);
    expect(store.history('AAPL', '1W')).not.toBe(store.history('AAPL', '1D'));
    store.undo();
    expect(store.symbol('AAPL').drawings.find(d => d.id === daily.id)?.points[0].price).toBe(100);
    expect(store.symbol('AAPL').drawings.find(d => d.id === weekly.id)?.points[0].price).toBe(230);
    store.redo();
    expect(store.symbol('AAPL').drawings.find(d => d.id === daily.id)?.points[0].price).toBe(120);
    expect(store.symbol('AAPL').drawings.find(d => d.id === weekly.id)?.points[0].price).toBe(230);
    store.undo();
    store.updateSymbol('AAPL', s => ({ ...s, preferences: { ...s.preferences, timeframe: '1W' } }));
    store.undo();
    expect(store.symbol('AAPL').drawings.find(d => d.id === daily.id)?.points[0].price).toBe(100);
    expect(store.symbol('AAPL').drawings.find(d => d.id === weekly.id)?.points[0].price).toBe(200);
    store.redo();
    expect(store.symbol('AAPL').drawings.find(d => d.id === daily.id)?.points[0].price).toBe(100);
    expect(store.symbol('AAPL').drawings.find(d => d.id === weekly.id)?.points[0].price).toBe(230);
    await store.flush();
    await db.close();
  });

  it('isolates AAPL 24/58, NVDA 43/56 and drawings through switch and reload', async () => {
    const { db, store } = await setup();
    for (const p of [24, 58]) store.addIndicator(indicator('AAPL', p));
    store.commitDrawings('AAPL', [drawing], 'create');
    store.updateApp({ activeSymbol: 'NVDA' });
    for (const p of [43, 56]) store.addIndicator(indicator('NVDA', p));
    expect(store.symbol().drawings).toEqual([]);
    expect(store.symbol().indicators.map((i) => i.period)).toEqual([43, 56]);
    store.updateApp({ activeSymbol: 'AAPL' });
    expect(store.symbol().drawings).toEqual([drawing]);
    expect(store.symbol().indicators.map((i) => i.period)).toEqual([24, 58]);
    await store.flush();
    const reload = new AppStore(db);
    await reload.initialize();
    expect(reload.symbol('AAPL').indicators.map((i) => i.period)).toEqual([24, 58]);
    expect(reload.symbol('NVDA').indicators.map((i) => i.period)).toEqual([43, 56]);
    expect(reload.symbol('AAPL').indicators.every((i) => i.locked)).toBe(true);
    expect(reload.symbol('AAPL').drawings[0].locked).toBe(true);
    await db.close();
  });
  it('locked SMA rejects period/source/delete but allows hide and unlock', async () => {
    const { db, store } = await setup();
    const i = indicator('AAPL', 24);
    store.addIndicator(i);
    store.updateIndicator('AAPL', i.id, { period: 50, source: 'high', visible: false });
    store.removeIndicator('AAPL', i.id);
    expect(store.symbol().indicators[0]).toMatchObject({
      period: 24,
      source: 'close',
      visible: false,
      locked: true,
    });
    store.updateIndicator('AAPL', i.id, { locked: false });
    store.updateIndicator('AAPL', i.id, { period: 58 });
    expect(store.symbol().indicators[0].period).toBe(58);
    store.removeIndicator('AAPL', i.id);
    expect(store.symbol().indicators).toEqual([]);
    await store.flush();
    await db.close();
  });
  it('history belongs to a symbol and lock/delete guard preserves locked drawings', async () => {
    const { db, store } = await setup();
    store.commitDrawings('AAPL', [drawing], 'create');
    store.mutateDrawing('line', 'delete');
    expect(store.symbol().drawings).toHaveLength(1);
    store.updateApp({ activeSymbol: 'NVDA' });
    store.undo();
    expect(store.symbol('AAPL').drawings).toHaveLength(1);
    store.updateApp({ activeSymbol: 'AAPL' });
    store.mutateDrawing('line', 'lock');
    expect(store.symbol().drawings[0].locked).toBe(false);
    store.undo();
    expect(store.symbol().drawings[0].locked).toBe(true);
    store.redo();
    store.mutateDrawing('line', 'delete');
    expect(store.symbol().drawings).toEqual([]);
    store.undo();
    expect(store.symbol().drawings).toHaveLength(1);
    await store.flush();
    await db.close();
  });
  it('export/import atomically restores all symbols, watchlist, locks and view preferences', async () => {
    const { db, store } = await setup();
    store.addIndicator(indicator('AAPL', 24));
    store.addIndicator(indicator('NVDA', 43));
    store.commitDrawings('AAPL', [drawing], 'create');
    store.updateApp({ watchlist: ['NVDA', 'AAPL'], activeSymbol: 'NVDA' });
    store.updateSymbol('NVDA', (s) => ({
      ...s,
      preferences: { timeframe: '5m', magnet: true, views: { '5m': { from: 200, to: 400 } } },
    }));
    const exported = await store.export(),
      other = await setup();
    await other.store.import(JSON.stringify(exported));
    expect((await other.store.export()).symbols).toEqual(exported.symbols);
    expect(other.store.getSnapshot().app).toEqual(exported.app);
    await other.db.close();
    await db.close();
  });
  it('rejects malformed data, mismatched symbols, duplicate ids and invalid anchors before writes', async () => {
    const { db, store } = await setup();
    store.addIndicator(indicator('AAPL', 24));
    const exported = await store.export();
    const legacyGlobal = { ...drawing, scope: { timeframes: 'all' as const } };
    for (const malformed of [
      { ...exported, version: 99 },
      { ...exported, symbols: [{ ...emptySymbol('AAPL'), drawings: [legacyGlobal] }] },
      { ...exported, symbols: [{ ...emptySymbol('NVDA'), drawings: [drawing] }] },
      { ...exported, symbols: [{ ...emptySymbol('AAPL'), drawings: [drawing, drawing] }] },
      {
        ...exported,
        symbols: [
          { ...emptySymbol('AAPL'), indicators: [{ ...indicator('AAPL', 24), period: 0 }] },
        ],
      },
    ]) {
      await expect(store.import(JSON.stringify(malformed))).rejects.toThrow();
      expect(store.symbol('AAPL').indicators).toHaveLength(1);
    }
    expect(() => parseSettings('{')).toThrow();
    await db.close();
  });
  it('surfaces unavailable IndexedDB writes instead of claiming a saved state', async () => {
    const { db, store } = await setup();
    await db.close();
    store.addIndicator(indicator('AAPL', 24));
    await store.flush();
    expect(store.getSnapshot().storageError).toContain('儲存失敗');
    await expect(store.export()).rejects.toThrow();
  });
  it('JSON schema permits zero MAs and missing symbol states without contamination', () => {
    const value = {
      version: 1,
      exportedAt: new Date().toISOString(),
      app: { watchlist: [], activeSymbol: 'AAPL', provider: 'demo' },
      symbols: [emptySymbol('AAPL')],
    };
    expect(parseSettings(JSON.stringify(value)).symbols[0].indicators).toEqual([]);
  });
});
describe('SMA', () => {
  const bars = [1, 2, 3, 4, 5].map((close, i) => ({
    time: i + 1,
    open: close * 2,
    high: close * 3,
    low: close / 2,
    close,
    volume: 1,
  }));
  it('uses full lookback, source, and aligns output time', () => {
    expect(sma(bars, 3)).toEqual([
      { time: 3, value: 2 },
      { time: 4, value: 3 },
      { time: 5, value: 4 },
    ]);
    expect(sma(bars, 2, 'open')[0]).toEqual({ time: 2, value: 3 });
  });
  it('handles 0/1/many MAs and periods longer than data', () => {
    expect(sma([], 24)).toEqual([]);
    expect(sma(bars, 10)).toEqual([]);
    expect(sma(bars, 1).map((x) => x.value)).toEqual([1, 2, 3, 4, 5]);
    expect(() => sma(bars, 0)).toThrow();
    expect(() => sma(bars, 1.5)).toThrow();
  });
});

it('rejects a stale endpoint/move commit when a drawing became locked during the gesture', async () => {
  const { db, store } = await setup();
  store.commitDrawings('AAPL', [{ ...drawing, locked: false }], 'create');
  const old = structuredClone(store.symbol().drawings[0]);
  store.mutateDrawing(old.id, 'lock');
  store.commitDrawings(
    'AAPL',
    [{ ...old, points: old.points.map((p) => ({ ...p, price: p.price + 20 })) }],
    'move',
  );
  expect(store.symbol().drawings[0]).toEqual(drawing);
  await store.flush();
  await db.close();
});

it('Horizontal Ray persists, exports/imports and retains per-symbol ownership', async () => {
  const { db, store } = await setup();
  const ray: Drawing = {
    ...drawing,
    type: 'ray',
    points: drawing.points.map((p) => ({ ...p, price: 100 })),
  };
  store.commitDrawings('AAPL', [ray], 'create');
  await store.flush();
  const backup = await store.export();
  expect(
    parseSettings(JSON.stringify(backup)).symbols.find((s) => s.symbol === 'AAPL')!.drawings,
  ).toEqual([ray]);
  await store.import(JSON.stringify(backup));
  const reload = new AppStore(db);
  await reload.initialize();
  expect(reload.symbol('AAPL').drawings).toEqual([ray]);
  expect(reload.symbol('NVDA').drawings).toEqual([]);
  const invalid = structuredClone(backup);
  invalid.symbols.find((s) => s.symbol === 'AAPL')!.drawings[0].points[1].price = 101;
  expect(() => parseSettings(JSON.stringify(invalid))).toThrow('Ray must be horizontal');
  await db.close();
});

it('Rectangle retains two anchors, locks and settings round trip', async () => {
  const { db, store } = await setup();
  const rectangle: Drawing = { ...drawing, type: 'rectangle' };
  store.commitDrawings('AAPL', [rectangle], 'create');
  const backup = await store.export();
  await store.import(JSON.stringify(backup));
  expect(store.symbol('AAPL').drawings).toEqual([rectangle]);
  expect(store.symbol('NVDA').drawings).toEqual([]);
  await db.close();
});

it('Fibonacci levels stay inside one persistent object; invalid level import is rejected atomically', async () => {
  const { db, store } = await setup();
  const fib: Drawing = { ...drawing, type: 'fibonacci', levels: [...DEFAULT_FIB_LEVELS] };
  store.commitDrawings('AAPL', [fib], 'create');
  const backup = await store.export();
  await store.import(JSON.stringify(backup));
  expect(store.symbol('AAPL').drawings).toEqual([fib]);
  const invalid = structuredClone(backup);
  invalid.symbols.find((s) => s.symbol === 'AAPL')!.drawings[0].levels = [0, 0, 1];
  await expect(store.import(JSON.stringify(invalid))).rejects.toThrow('Invalid Fibonacci levels');
  expect((await store.export()).symbols.find((s) => s.symbol === 'AAPL')!.drawings).toEqual([fib]);
  expect(store.symbol('NVDA').drawings).toEqual([]);
  await db.close();
});

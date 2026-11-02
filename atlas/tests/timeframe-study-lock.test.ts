import 'fake-indexeddb/auto';
import { describe, expect, it } from 'vitest';
import { AppStore } from '../src/app/AppStore';
import { IndexedDBStore } from '../src/storage/IndexedDBStore';
import type { Drawing } from '../src/drawing/DrawingModel';
import { setTimeframeStudiesLocked } from '../src/ui/TimeframeStudyLock';
import type { Timeframe } from '../src/market-data/Timeframes';

const drawing = (id: string, timeframe: Timeframe): Drawing => ({ id, symbol: 'AAPL', type: 'horizontal', points: [{ time: 1760000000, logical: 0, price: 100, timeframe }], locked: false, visible: true, scope: { timeframes: [timeframe] }, style: { color: '#ffffff', lineWidth: 2, widthMode: 'pixels' } });

describe('timeframe study fixation', () => {
  it('locks and restores each timeframe independently across reload, preserving all anchors', async () => {
    const database = `tf-fix-${crypto.randomUUID()}`;
    const store = new AppStore(new IndexedDBStore(database));
    await store.initialize();
    for (const timeframe of ['15m', '1H', '1D', '1W', '1M'] as const) {
      store.updateSymbol('AAPL', state => ({ ...state, preferences: { ...state.preferences, timeframe } }));
      store.addIndicator({ id: `ma-${timeframe}`, symbol: 'AAPL', type: 'EMA', period: 20, source: 'close', visible: true, locked: false, lineWidth: 2, color: '#ffffff', scope: { timeframe } });
      store.commitDrawings('AAPL', [drawing(`line-${timeframe}`, timeframe)], 'create', timeframe);
    }
    const before = structuredClone(store.symbol('AAPL'));
    store.updateSymbol('AAPL', state => ({ ...state, preferences: { ...state.preferences, timeframe: '1H' } }));
    setTimeframeStudiesLocked(store, 'AAPL', '1H', true);
    expect(store.symbol('AAPL').indicators.filter(indicator => indicator.locked).map(indicator => indicator.id)).toEqual(['ma-1H']);
    expect(store.symbol('AAPL').drawings.filter(line => line.locked).map(line => line.id)).toEqual(['line-1H']);
    expect(store.symbol('AAPL').drawings.map(line => line.points)).toEqual(before.drawings.map(line => line.points));
    await store.flush();
    const restored = new AppStore(new IndexedDBStore(database));
    await restored.initialize();
    expect(restored.symbol('AAPL').indicators.find(indicator => indicator.id === 'ma-1H')!.locked).toBe(true);
    expect(restored.symbol('AAPL').drawings.find(line => line.id === 'line-1H')!.locked).toBe(true);
    setTimeframeStudiesLocked(restored, 'AAPL', '1H', false);
    expect(restored.symbol('AAPL').drawings.every(line => !line.locked)).toBe(true);
  });
  it('does not change another symbol, another timeframe, volume, or a stale timeframe', async () => {
    const store = new AppStore(new IndexedDBStore(`tf-guards-${crypto.randomUUID()}`));
    await store.initialize();
    store.updateSymbol('AAPL', state => ({ ...state, drawings: [drawing('weekly', '1W'), drawing('daily', '1D')] }));
    store.addIndicator({ id: 'volume', symbol: 'AAPL', type: 'Volume', period: 1, source: 'close', visible: true, locked: false, lineWidth: 2, color: '#ffffff', scope: { timeframe: '1D' } });
    const other = structuredClone(store.symbol('NVDA'));
    expect(() => setTimeframeStudiesLocked(store, 'AAPL', '1W', true)).toThrow('週期已切換');
    setTimeframeStudiesLocked(store, 'AAPL', '1D', true);
    expect(store.symbol('AAPL').drawings.find(line => line.id === 'weekly')!.locked).toBe(false);
    expect(store.symbol('AAPL').drawings.find(line => line.id === 'daily')!.locked).toBe(true);
    expect(store.symbol('AAPL').indicators.find(indicator => indicator.id === 'volume')!.locked).toBe(false);
    expect(store.symbol('NVDA')).toEqual(other);
  });
});

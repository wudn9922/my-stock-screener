import 'fake-indexeddb/auto';
import { describe, expect, it } from 'vitest';
import { AppStore } from '../src/app/AppStore';
import { IndexedDBStore } from '../src/storage/IndexedDBStore';
import { emptySymbol } from '../src/storage/schema';
import type { Drawing } from '../src/drawing/DrawingModel';
import type { IndicatorInstance } from '../src/indicators/IndicatorRegistry';

const atrDrawing = (locked = false): Drawing => ({
  id: locked ? 'locked-atr-line' : 'atr-line',
  symbol: 'AAPL',
  type: 'horizontal',
  points: [{ time: 1_700_000_000, logical: 20, price: 120, timeframe: '1D' }],
  locked,
  visible: true,
  scope: { timeframes: ['1D'] },
  style: { color: '#5ca9ff', lineWidth: 1, widthMode: 'atr' },
});

const atrIndicator = (id: string, locked = false): IndicatorInstance => ({
  id,
  symbol: 'AAPL',
  type: 'SMA',
  period: 24,
  source: 'close',
  visible: true,
  locked,
  lineWidth: 1,
  widthMode: 'atr',
  color: '#f0b35b',
  scope: { timeframe: '1D' },
});

function setup() {
  const storage = new IndexedDBStore('atr-settings-' + crypto.randomUUID());
  return { storage, store: new AppStore(storage) };
}

describe('ATR width settings', () => {
  it('round trips ATR drawings, indicators, presets, and drawing defaults through v4 export/import', async () => {
    const { storage, store } = setup();
    await store.initialize();
    store.commitDrawings('AAPL', [atrDrawing(), atrDrawing(true)], 'create', '1D');
    store.addIndicator(atrIndicator('ma-atr'));
    store.addIndicator(atrIndicator('locked-ma-atr', true));
    store.saveIndicatorPreset('ATR average', 'AAPL');
    store.updateApp({
      drawingDefaults: {
        trend: { color: '#123456', lineWidth: 1, widthMode: 'atr', lineStyle: 'dashed' },
      },
    });
    await store.flush();

    const backup = await store.export();
    expect(backup.version).toBe(4);
    expect(backup.symbols[0].drawings.map(d => d.style.widthMode)).toEqual(['atr', 'atr']);
    expect(backup.symbols[0].indicators.map(i => i.widthMode)).toEqual(['atr', 'atr']);
    expect(backup.app.indicatorPresets[0].indicators[0]).toMatchObject({ lineWidth: 1, widthMode: 'atr' });
    expect(backup.app.drawingDefaults.trend).toMatchObject({ lineWidth: 1, widthMode: 'atr' });

    const incoming = JSON.stringify(backup);
    const target = setup();
    await target.store.initialize();
    await target.store.import(incoming);
    const restored = await target.store.export();
    expect(restored.version).toBe(4);
    expect(restored.symbols).toEqual(backup.symbols);
    expect(restored.app).toEqual(backup.app);
    await storage.close();
    await target.storage.close();
  });

  it('rejects invalid modes and Volume ATR imports before replacing stored state', async () => {
    const { storage, store } = setup();
    await store.initialize();
    store.addIndicator(atrIndicator('retained-ma'));
    await store.flush();
    const original = await store.export();

    const unknownMode: unknown = structuredClone(original);
    (unknownMode as { app: { drawingDefaults: Record<string, unknown> } }).app.drawingDefaults = {
      trend: { color: '#123456', lineWidth: 1, widthMode: 'diamonds' },
    };
    await expect(store.import(JSON.stringify(unknownMode))).rejects.toThrow();

    const volumeAtr = structuredClone(original);
    const volume = {
      ...atrIndicator('invalid-volume'),
      type: 'Volume' as const,
      widthMode: 'atr' as const,
      scope: { timeframe: '1D' as const },
    };
    volumeAtr.symbols = [{ ...emptySymbol('AAPL'), indicators: [volume] }];
    await expect(store.import(JSON.stringify(volumeAtr))).rejects.toThrow(/Volume indicators cannot use ATR width mode/);

    const after = await store.export();
    expect(after.app).toEqual(original.app);
    expect(after.symbols).toEqual(original.symbols);
    const persisted = await storage.load();
    expect(persisted.symbols).toEqual(original.symbols);
    expect(persisted.app).toEqual(original.app);
    await storage.close();
  });

  it('keeps locked drawing and SMA widths unchanged when a caller attempts to edit them', async () => {
    const { storage, store } = setup();
    await store.initialize();
    store.commitDrawings('AAPL', [atrDrawing(true)], 'create', '1D');
    store.addIndicator(atrIndicator('locked-ma', true));
    store.updateDrawing('locked-atr-line', {
      style: { color: '#123456', lineWidth: 4, widthMode: 'pixels' },
    });
    store.updateIndicator('AAPL', 'locked-ma', { lineWidth: 4, widthMode: 'pixels' });
    expect(store.symbol('AAPL').drawings[0].style).toEqual({ color: '#5ca9ff', lineWidth: 1, widthMode: 'atr' });
    expect(store.symbol('AAPL').indicators[0]).toMatchObject({ lineWidth: 1, widthMode: 'atr', locked: true });
    await store.flush();
    await storage.close();
  });
});

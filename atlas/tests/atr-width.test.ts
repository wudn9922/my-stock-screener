import { describe, expect, it, vi } from 'vitest';
import { averageTrueRange } from '../src/indicators/AverageTrueRange';
import {
  atrStrokeWidthCss,
  bitmapLineWidth,
  nativeLineWidth,
  resolveStrokeWidth,
} from '../src/chart/StrokeWidth';
import { IndicatorEngine } from '../src/indicators/IndicatorEngine';
import type { IndicatorInstance } from '../src/indicators/IndicatorRegistry';
import { TimeMapper } from '../src/chart/TimeMapper';
import { DrawingStateMachine } from '../src/drawing/DrawingStateMachine';
import type { DrawingProjection, DrawingStyle, ToolKind } from '../src/drawing/DrawingModel';
import type { Bar } from '../src/market-data/MarketDataProvider';
import type { IChartApi } from 'lightweight-charts';

function bar(time: number, open = 9, high = 11, low = 8, close = 10): Bar {
  return { time, open, high, low, close, volume: 100 };
}

describe('Wilder ATR sizing', () => {
  it('waits for a full seed, uses true range across gaps, then applies Wilder smoothing', () => {
    const seed = Array.from({ length: 14 }, (_, index) => bar(index * 10));
    expect(averageTrueRange(seed.slice(0, 13))).toBeNull();
    expect(averageTrueRange(seed)).toBe(3);
    expect(averageTrueRange([...seed, bar(10_000, 12, 15, 10, 12)])).toBeCloseTo(44 / 14);
    expect(averageTrueRange([bar(10, 10, 10, 10, 10), bar(100, 11, 13, 11, 12)], 2)).toBe(1.5);
  });

  it('returns null for invalid OHLC and zero for a valid flat series', () => {
    const flat = Array.from({ length: 14 }, (_, index) => bar(index, 10, 10, 10, 10));
    expect(averageTrueRange(flat)).toBe(0);
    expect(averageTrueRange([...flat.slice(0, 13), { ...bar(13), high: Number.NaN }])).toBeNull();
    expect(averageTrueRange([...flat.slice(0, 13), bar(13, 12, 10, 11, 11)])).toBeNull();
    expect(() => averageTrueRange(flat, 0)).toThrow('positive integer');
  });

  it('maps 0.02 ATR through price coordinates, clamps canvas widths, and keeps pixel fallback', () => {
    expect(atrStrokeWidthCss(10, 100, (price) => price * 10)).toBe(2);
    expect(atrStrokeWidthCss(1, 100, (price) => price)).toBe(0.5);
    expect(atrStrokeWidthCss(1, 100, (price) => price * 1_000)).toBe(4);
    expect(atrStrokeWidthCss(0, 100, (price) => price * 10)).toBeNull();
    expect(atrStrokeWidthCss(10, 100, () => null)).toBeNull();
    expect(resolveStrokeWidth('atr', 3, 0, 100, (price) => price)).toBe(3);
    expect(resolveStrokeWidth(undefined, 2, 10, 100, (price) => price * 10)).toBe(2);
    expect(nativeLineWidth(0.5)).toBe(1);
    expect(nativeLineWidth(1.5)).toBe(2);
    expect(nativeLineWidth(4.5)).toBe(4);
    expect(bitmapLineWidth(0.5, 1)).toBe(1);
    expect(bitmapLineWidth(2, 2)).toBe(4);
  });
});

it('defaults new drawings to ATR while preserving explicit legacy pixel defaults', () => {
  const mapper = new TimeMapper([bar(1)], '1D');
  const projection: DrawingProjection = {
    toPoint: (anchor) => ({ x: anchor.logical, y: anchor.price }),
    toAnchor: (point) => mapper.anchor(point.x, point.y),
    width: () => 500,
    height: () => 300,
  };
  const createDrawing = (defaults: (type: ToolKind) => Partial<DrawingStyle> = () => ({})) => {
    const machine = new DrawingStateMachine('AAPL', mapper, projection, [], vi.fn(), vi.fn(), defaults);
    machine.setTool('horizontal');
    machine.begin({ x: 0, y: 10 }, 1, false);
    machine.end({ x: 0, y: 10 }, 1);
    return machine.drawings[0];
  };

  expect(createDrawing().style).toMatchObject({ lineWidth: 1, widthMode: 'atr' });
  const legacy = createDrawing(() => ({ color: '#ffffff', lineWidth: 3 }));
  expect(legacy.style).toMatchObject({ color: '#ffffff', lineWidth: 3 });
  expect(legacy.style.widthMode).toBeUndefined();
});

it('updates only ATR SMA/EMA native widths and leaves Volume average width in pixel units', () => {
  const created: { applyOptions: ReturnType<typeof vi.fn> }[] = [];
  const chart = {
    addSeries: vi.fn(() => {
      const api = {
        setData: vi.fn(),
        applyOptions: vi.fn(),
        priceScale: () => ({ applyOptions: vi.fn() }),
      };
      created.push(api);
      return api;
    }),
    removeSeries: vi.fn(),
  } as unknown as IChartApi;
  const base: IndicatorInstance = {
    id: 'atr-sma',
    symbol: 'AAPL',
    type: 'SMA',
    period: 2,
    source: 'close',
    visible: true,
    locked: false,
    lineWidth: 2,
    widthMode: 'atr',
    color: '#f0b35b',
    scope: {},
  };
  const volume: IndicatorInstance = {
    ...base,
    id: 'volume',
    type: 'Volume',
  };
  const engine = new IndicatorEngine(chart);
  engine.sync([base, volume], [bar(1), bar(2)], '1D', 1);

  const coordinate = (price: number) => (price === 100 ? 100 : 103.8);
  engine.refreshStrokeWidths(1, 100, coordinate);
  engine.refreshStrokeWidths(1, 100, coordinate);
  expect(created[0].applyOptions).toHaveBeenLastCalledWith({ lineWidth: 4 });
  expect(created[0].applyOptions).toHaveBeenCalledTimes(2);
  expect(created[2].applyOptions).toHaveBeenCalledTimes(1);
  engine.clear();
});

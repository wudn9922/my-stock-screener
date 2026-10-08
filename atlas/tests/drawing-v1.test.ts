import { describe, expect, it, vi } from 'vitest';
import { TimeMapper } from '../src/chart/TimeMapper';
import { DrawingStateMachine } from '../src/drawing/DrawingStateMachine';
import { DrawingRenderer } from '../src/drawing/DrawingRenderer';
import { hitTest } from '../src/drawing/HitTester';
import { moveDrawing, editPoint } from '../src/drawing/Movement';
import type { Drawing, DrawingProjection, DrawingStyle, ToolKind } from '../src/drawing/DrawingModel';
import { fibHit, fibSegments, visibleFibLevels, drawFibonacci } from '../src/tools/Fibonacci';
import { measurementLabel, measurementValues } from '../src/tools/Measurement';
import { parallelChannelGeometry } from '../src/tools/ParallelChannel';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import { drawingSchema } from '../src/storage/schema';

const bars = Array.from({ length: 100 }, (_, i) => ({
  time: 1_700_000_000 + i * 86_400,
  open: 10,
  high: 14,
  low: 8,
  close: 12,
  volume: 100,
}));
const mapper = new TimeMapper(bars, '1D');
const projection: DrawingProjection = {
  toPoint: (anchor) => ({ x: mapper.toLogical(anchor.time) * 10, y: anchor.price * 10 }),
  toAnchor: (point) => mapper.anchor(point.x / 10, point.y / 10),
  logicalAt: (anchor) => mapper.toLogical(anchor.time),
  width: () => 1600,
  height: () => 600,
};

function drawing(type: ToolKind, points = [mapper.anchor(10, 10), mapper.anchor(20, 20)]): Drawing {
  return {
    id: type,
    symbol: 'AAPL',
    type,
    points,
    locked: false,
    visible: true,
    scope: { timeframes: 'all' },
    style: { color: '#5ca9ff', lineWidth: 2 },
  };
}

function machine(
  drawings: Drawing[] = [],
  defaults: (type: ToolKind) => Partial<DrawingStyle> = () => ({}),
) {
  const commit = vi.fn();
  const selection = vi.fn();
  return {
    m: new DrawingStateMachine('AAPL', mapper, projection, drawings, commit, selection, defaults),
    commit,
    selection,
  };
}

it('assigns newly created drawings to the first anchor timeframe', () => {
  const { m, commit } = machine();
  m.setTool('horizontal');
  m.begin({ x: 100, y: 100 }, 1, false);
  m.end({ x: 100, y: 100 }, 1);
  expect(commit).toHaveBeenCalledTimes(1);
  expect(m.drawings[0].scope.timeframes).toEqual(['1D']);
  expect(drawingSchema.parse(m.drawings[0]).scope.timeframes).toEqual(['1D']);
});

describe('Parallel Channel', () => {
  it('uses three release gestures and keeps P1/P2/P3 as canonical control anchors', () => {
    const defaultsStyle = {
      color: '#aabbcc',
      lineWidth: 3,
      lineStyle: 'dotted' as const,
      opacity: 0.6,
      fillOpacity: 0.12,
    };
    const { m, commit } = machine([], () => defaultsStyle);
    m.setTool('channel');
    m.begin({ x: 100, y: 100 }, 1, true);
    m.end({ x: 100, y: 100 }, 1);
    expect(m.first).toEqual(mapper.anchor(10, 10));
    expect(m.placed).toHaveLength(1);
    expect(commit).not.toHaveBeenCalled();

    m.begin({ x: 200, y: 200 }, 2, true);
    m.move({ x: 210, y: 210 }, 2);
    expect(m.preview?.points).toEqual([
      mapper.anchor(10, 10),
      mapper.anchor(21, 21),
      mapper.anchor(21, 21),
    ]);
    m.end({ x: 200, y: 200 }, 2);
    expect(m.placed).toEqual([mapper.anchor(10, 10), mapper.anchor(20, 20)]);
    expect(commit).not.toHaveBeenCalled();

    m.begin({ x: 150, y: 200 }, 3, true);
    m.end({ x: 150, y: 200 }, 3);
    expect(commit).toHaveBeenCalledTimes(1);
    const channel = m.drawings[0];
    expect(channel.type).toBe('channel');
    expect(channel.points).toEqual([
      mapper.anchor(10, 10),
      mapper.anchor(20, 20),
      mapper.anchor(15, 20),
    ]);
    expect(channel.style).toEqual(defaultsStyle);
    expect(m.placed).toEqual([]);
  });

  it('projects P3 at its x, hits the fill and actual P3 handle, and gives vertical edges horizontal width', () => {
    const channel = drawing('channel', [mapper.anchor(10, 10), mapper.anchor(20, 20), mapper.anchor(15, 20)]);
    const geometry = parallelChannelGeometry(channel, projection)!;
    expect(geometry.baseline).toEqual([{ x: 100, y: 100 }, { x: 200, y: 200 }]);
    expect(geometry.guideBase).toEqual({ x: 150, y: 150 });
    expect(geometry.parallel).toEqual([{ x: 100, y: 150 }, { x: 200, y: 250 }]);
    expect(hitTest([channel], { x: 160, y: 185 }, projection, false, null)).toEqual({
      id: 'channel',
      part: 'body',
    });
    expect(hitTest([channel], { x: 150, y: 200 }, projection, true, 'channel')?.part).toBe(2);

    const vertical = drawing('channel', [mapper.anchor(10, 10), mapper.anchor(10, 20), mapper.anchor(12, 22)]);
    const verticalGeometry = parallelChannelGeometry(vertical, projection)!;
    expect(verticalGeometry.guideBase).toEqual({ x: 100, y: 220 });
    expect(verticalGeometry.parallel).toEqual([{ x: 120, y: 100 }, { x: 120, y: 200 }]);
    expect(hitTest([vertical], { x: 110, y: 150 }, projection, false, null)?.part).toBe('body');
    expect(hitTest([vertical], { x: 120, y: 220 }, projection, true, 'channel')?.part).toBe(2);
    expect(JSON.stringify(verticalGeometry)).not.toContain('NaN');

    const collapsed = drawing('channel', [mapper.anchor(10, 10), mapper.anchor(10, 10), mapper.anchor(12, 22)]);
    const collapsedGeometry = parallelChannelGeometry(collapsed, projection)!;
    expect(collapsedGeometry.guideBase).toBeNull();
    expect(collapsedGeometry.parallel).toEqual(collapsedGeometry.baseline);
    expect(JSON.stringify(collapsedGeometry)).not.toContain('NaN');
    expect(Object.values(collapsedGeometry.parallel[0]).every(Number.isFinite)).toBe(true);
  });

  it('edits P3 directly and moves all anchors from one drag-start snapshot', () => {
    const channel = drawing('channel', [mapper.anchor(10, 10), mapper.anchor(20, 20), mapper.anchor(15, 20)]);
    const { m } = machine([channel]);
    m.select(channel.id);
    m.begin({ x: 150, y: 200 }, 1, false);
    m.end({ x: 170, y: 220 }, 1);
    expect(m.drawings[0].points).toEqual([
      channel.points[0],
      channel.points[1],
      mapper.anchor(17, 22),
    ]);

    const moved = moveDrawing(
      channel,
      mapper.anchor(15, 17.5),
      mapper.anchor(17, 18.5),
      mapper,
    );
    expect(moved.points).toEqual([
      mapper.anchor(12, 11),
      mapper.anchor(22, 21),
      mapper.anchor(17, 21),
    ]);
  });
});

describe('Range measurements', () => {
  it('places each two-anchor measurement in two release gestures', () => {
    for (const type of ['price-range', 'date-range', 'price-date-range'] as const) {
      const { m, commit } = machine();
      m.setTool(type);
      m.begin({ x: 100, y: 100 }, 1, true);
      m.end({ x: 100, y: 100 }, 1);
      expect(commit).not.toHaveBeenCalled();
      expect(m.first).toEqual(mapper.anchor(10, 10));
      m.begin({ x: 150, y: 150 }, 2, true);
      m.end({ x: 150, y: 150 }, 2);
      expect(commit).toHaveBeenCalledTimes(1);
      expect(m.drawings[0].type).toBe(type);
      expect(m.drawings[0].points).toEqual([mapper.anchor(10, 10), mapper.anchor(15, 15)]);
    }
  });

  it('reports signed price change and percent, with N/A for a zero P1 price', () => {
    const range = drawing('price-range', [mapper.anchor(10, 0), mapper.anchor(14, 5)]);
    expect(measurementValues(range, projection)?.priceDelta).toBe(5);
    expect(measurementLabel(range, projection)).toBe('+$5.00 (N/A)');
    const reverse = drawing('price-range', [mapper.anchor(14, 10), mapper.anchor(10, 5)]);
    expect(measurementLabel(reverse, projection)).toBe('−$5.00 (−50.00%)');
  });

  it('uses current-timeframe logical bar distance and separates wall-clock elapsed time', () => {
    const p1 = { ...mapper.anchor(10, 10), logical: 900 };
    const p2 = { ...mapper.anchor(16, 10), logical: -900 };
    const dateRange = drawing('date-range', [p1, p2]);
    const values = measurementValues(dateRange, projection)!;
    expect(values.barDelta).toBe(6);
    expect(values.elapsedSeconds).toBe(6 * 86_400);
    expect(values.humanDuration).toBe('+6d');
    expect(measurementLabel(dateRange, projection)).toBe('+6 bars · wall +518400s (+6d)');
    const reverse = drawing('date-range', [p2, p1]);
    expect(measurementLabel(reverse, projection)).toBe('−6 bars · wall −518400s (−6d)');
  });

  it('combines price, percent, bar distance and wall time; supports rectangle body hit and snapshot movement', () => {
    const combined = drawing('price-date-range', [mapper.anchor(10, 10), mapper.anchor(15, 12.5)]);
    expect(measurementLabel(combined, projection)).toBe(
      '+$2.50 (+25.00%) · +5 bars · wall +432000s (+5d)',
    );
    expect(hitTest([combined], { x: 125, y: 110 }, projection, false, null)?.part).toBe('body');
    const moved = moveDrawing(
      combined,
      mapper.anchor(12.5, 11),
      mapper.anchor(13.5, 12),
      mapper,
    );
    expect(moved.points).toEqual([mapper.anchor(11, 11), mapper.anchor(16, 13.5)]);
    expect(editPoint(combined, 1, mapper.anchor(16, 14)).points).toEqual([
      combined.points[0],
      mapper.anchor(16, 14),
    ]);
  });
});

describe('Vertical Line and style filters', () => {
  it('creates in one gesture, hits across the pane and keeps price independent while moving', () => {
    const { m, commit } = machine();
    m.setTool('vertical');
    m.begin({ x: 500, y: 100 }, 1, true);
    m.move({ x: 500, y: 500 }, 1);
    m.end({ x: 500, y: 500 }, 1);
    expect(commit).toHaveBeenCalledTimes(1);
    const vertical = m.drawings[0];
    expect(vertical.type).toBe('vertical');
    expect(vertical.points).toHaveLength(1);
    expect(hitTest([vertical], { x: 500, y: 50 }, projection, false, null)?.part).toBe('body');
    expect(hitTest([vertical], { x: 520, y: 50 }, projection, false, null)).toBeNull();

    m.begin({ x: 500, y: 200 }, 2, false);
    m.move({ x: 520, y: 260 }, 2);
    m.move({ x: 530, y: 320 }, 2);
    m.end({ x: 540, y: 350 }, 2);
    expect(m.drawings[0].points[0].logical).toBe(54);
    expect(m.drawings[0].points[0].price).toBe(vertical.points[0].price);

    const moved = moveDrawing(vertical, mapper.anchor(50, 40), mapper.anchor(52, 55), mapper);
    expect(moved.points).toEqual([mapper.anchor(52, vertical.points[0].price)]);
    const edited = editPoint(vertical, 0, mapper.anchor(55, 80));
    expect(edited.points[0]).toEqual(mapper.anchor(55, vertical.points[0].price));

    const context = {
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      setLineDash: vi.fn(),
    } as unknown as CanvasRenderingContext2D;
    const target = {
      useBitmapCoordinateSpace: (draw: (scope: {
        context: CanvasRenderingContext2D;
        horizontalPixelRatio: number;
        verticalPixelRatio: number;
        bitmapSize: { width: number; height: number };
      }) => void) =>
        draw({
          context,
          horizontalPixelRatio: 1,
          verticalPixelRatio: 1,
          bitmapSize: { width: 1600, height: 600 },
        }),
    } as unknown as CanvasRenderingTarget2D;
    const renderer = new DrawingRenderer(
      () => ({
        drawings: [vertical],
        preview: null,
        selectedId: null,
        candidate: null,
        loupe: false,
        pointer: null,
      }),
      projection,
    );
    renderer.draw(target);
    expect(context.moveTo).toHaveBeenCalledWith(500, 0);
    expect(context.lineTo).toHaveBeenCalledWith(500, 600);
  });

  it('rechecks the latest locked state before committing an edit', () => {
    const trend = drawing('trend');
    const { m, commit } = machine([trend]);
    m.select(trend.id);
    m.begin({ x: 150, y: 150 }, 1, false);
    m.move({ x: 170, y: 170 }, 1);
    m.drawings = [{ ...trend, locked: true }];
    m.end({ x: 180, y: 180 }, 1);
    expect(m.drawings[0].locked).toBe(true);
    expect(commit).not.toHaveBeenCalled();
  });

  it('filters hidden Fibonacci levels in geometry and hits and suppresses labels when requested', () => {
    const fib: Drawing = {
      ...drawing('fibonacci'),
      levels: [0, 0.5, 1],
      style: { color: '#5ca9ff', lineWidth: 2, hiddenLevels: [0.5], labelsVisible: false },
    };
    expect(visibleFibLevels(fib)).toEqual([0, 1]);
    expect(fibSegments(fib, projection).map((line) => line.level)).toEqual([0, 1]);
    expect(fibHit(fib, { x: 120, y: 150 }, projection, 3)).toBe(false);

    const context = {
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setLineDash: vi.fn(),
      measureText: vi.fn(() => ({ width: 10 })),
      fillRect: vi.fn(),
      fillText: vi.fn(),
      globalAlpha: 1,
      lineWidth: 1,
      font: '',
      fillStyle: '',
      strokeStyle: '',
    } as unknown as CanvasRenderingContext2D;
    drawFibonacci(context, fib, projection, 1, 1);
    expect(context.fillText).not.toHaveBeenCalled();
  });

  it('filters unsupported hidden-level defaults from new Fibonacci drawings and copies the remaining levels', () => {
    const hiddenLevels = [0.7, 0.5];
    const { m } = machine([], () => ({ hiddenLevels }));
    m.setTool('fibonacci');
    m.begin({ x: 100, y: 100 }, 1, false);
    m.end({ x: 100, y: 100 }, 1);
    m.begin({ x: 200, y: 200 }, 2, false);
    m.end({ x: 200, y: 200 }, 2);
    expect(m.drawings[0].style.hiddenLevels).toEqual([0.5]);
    expect(() => drawingSchema.parse(m.drawings[0])).not.toThrow();
    hiddenLevels.push(0.618);
    expect(m.drawings[0].style.hiddenLevels).toEqual([0.5]);
  });
});

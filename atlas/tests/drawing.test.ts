import { DEFAULT_FIB_LEVELS, fibPrice, fibSegments } from '../src/tools/Fibonacci';
import { describe, it, expect, vi } from 'vitest';
import { TimeMapper } from '../src/chart/TimeMapper';
import { DrawingStateMachine } from '../src/drawing/DrawingStateMachine';
import { hitTest } from '../src/drawing/HitTester';
import { magnet } from '../src/drawing/MagnetEngine';
import { moveDrawing, editPoint } from '../src/drawing/Movement';
import { DrawingHistory } from '../src/drawing/DrawingHistory';
import type { Drawing, DrawingProjection } from '../src/drawing/DrawingModel';
const bars = Array.from({ length: 100 }, (_, i) => ({
  time: 1700000000 + i * 86400,
  open: 10,
  high: 14,
  low: 8,
  close: 12,
  volume: 100,
}));
const mapper = new TimeMapper(bars, '1D');
const projection: DrawingProjection = {
  toPoint: (a) => ({ x: mapper.toLogical(a.time) * 10, y: a.price * 10 }),
  toAnchor: (p) => mapper.anchor(p.x / 10, p.y / 10),
  width: () => 1600,
  height: () => 600,
};
const drawing: Drawing = {
  id: 'line',
  symbol: 'AAPL',
  type: 'trend',
  points: [mapper.anchor(10, 10), mapper.anchor(20, 20)],
  locked: false,
  visible: true,
  scope: { timeframes: 'all' },
  style: { color: '#5ca9ff', lineWidth: 2 },
};
function machine(drawings: Drawing[] = []) {
  const commit = vi.fn(),
    select = vi.fn();
  return {
    m: new DrawingStateMachine('AAPL', mapper, projection, drawings, commit, select),
    commit,
    select,
  };
}
describe('Trend Line interaction regression', () => {
  it('commits P1 and P2 at RELEASE positions, previews P2 drag and only commits once', () => {
    const { m, commit } = machine();
    m.setTool('trend');
    expect(m.begin({ x: 90, y: 90 }, 1, true)).toBe(true);
    m.move({ x: 100, y: 100 }, 1);
    expect(m.scene().loupe).toBe(true);
    m.end({ x: 110, y: 120 }, 1);
    expect(commit).not.toHaveBeenCalled();
    expect(m.first).toEqual(mapper.anchor(11, 12));
    m.begin({ x: 180, y: 180 }, 2, true);
    m.move({ x: 220, y: 240 }, 2);
    expect(m.preview!.points).toEqual([mapper.anchor(11, 12), mapper.anchor(22, 24)]);
    m.end({ x: 230, y: 250 }, 2);
    expect(commit).toHaveBeenCalledTimes(1);
    expect(m.drawings[0].points).toEqual([mapper.anchor(11, 12), mapper.anchor(23, 25)]);
    expect(m.tool).toBe('select');
  });
  it('edits selected endpoints and uses a snapshot for whole-line move', () => {
    const { m, commit } = machine([drawing]);
    m.select('line');
    m.begin({ x: 100, y: 100 }, 1, false);
    m.move({ x: 120, y: 160 }, 1);
    m.end({ x: 130, y: 170 }, 1);
    expect(m.drawings[0].points[0]).toEqual(mapper.anchor(13, 17));
    expect(m.drawings[0].points[1]).toEqual(drawing.points[1]);
    expect(commit).toHaveBeenCalledTimes(1);
    const { m: n } = machine([drawing]);
    n.select('line');
    n.begin({ x: 150, y: 150 }, 1, false);
    n.move({ x: 170, y: 160 }, 1);
    n.move({ x: 180, y: 180 }, 1);
    n.end({ x: 160, y: 170 }, 1);
    expect(n.drawings[0].points).toEqual([mapper.anchor(11, 12), mapper.anchor(21, 22)]);
  });
  it('cancels without persisting, ignores secondary pointer, and retains no preview', () => {
    const { m, commit } = machine([drawing]);
    m.select('line');
    m.begin({ x: 100, y: 100 }, 1, true);
    m.move({ x: 200, y: 200 }, 2);
    expect(m.gesture!.candidate.price).toBe(10);
    m.cancel();
    expect(m.drawings).toEqual([drawing]);
    expect(m.scene().loupe).toBe(false);
    expect(commit).not.toHaveBeenCalled();
  });
  it('locked body permits chart pan and has no draggable endpoint hit', () => {
    const locked = { ...drawing, locked: true };
    const { m, commit, select } = machine([locked]);
    m.select('line');
    expect(m.begin({ x: 100, y: 100 }, 1, true)).toBe(false);
    expect(select).toHaveBeenLastCalledWith('line');
    expect(commit).not.toHaveBeenCalled();
    expect(hitTest([locked], { x: 100, y: 100 }, projection, true, 'line')!.part).toBe('body');
    expect(moveDrawing(locked, mapper.anchor(0, 0), mapper.anchor(2, 2), mapper)).toBe(locked);
    expect(editPoint(locked, 0, mapper.anchor(3, 3))).toBe(locked);
  });
  it('touch hitboxes remain generous independently of the 5px visible handle', () => {
    expect(hitTest([drawing], { x: 124, y: 100 }, projection, true, 'line')).toEqual({
      id: 'line',
      part: 0,
    });
    expect(hitTest([drawing], { x: 124, y: 100 }, projection, false, 'line')).toBeNull();
    expect(hitTest([drawing], { x: 150, y: 164 }, projection, true, null)?.part).toBe('body');
  });
  it('can place P2 beyond real candles without fake OHLC', () => {
    const { m } = machine();
    m.setTool('trend');
    m.begin({ x: 900, y: 100 }, 1, false);
    m.end({ x: 900, y: 100 }, 1);
    m.begin({ x: 1290, y: 200 }, 2, false);
    m.end({ x: 1290, y: 200 }, 2);
    expect(m.drawings[0].points[1].logical).toBe(129);
    expect(mapper.lastRealLogical).toBe(99);
    expect(mapper.futureWhitespace()[0]).not.toHaveProperty('open');
  });
  it('endpoint magnet snaps OHLC, disables in future and is forbidden for whole-line move', () => {
    const a = mapper.anchor(10, 12.1);
    expect(magnet(a, { x: 100, y: 121 }, mapper, projection, true).price).toBe(12);
    const future = mapper.anchor(129, 12.1);
    expect(magnet(future, { x: 1290, y: 121 }, mapper, projection, true)).toEqual(future);
    expect(magnet(a, { x: 100, y: 121 }, mapper, projection, false)).toEqual(a);
    const { m } = machine([drawing]);
    m.magnetOn = true;
    m.select('line');
    m.begin({ x: 150, y: 150 }, 1, false);
    m.end({ x: 160, y: 161 }, 1);
    expect(m.drawings[0].points[1].price).toBeCloseTo(21.1);
  });
});
describe('mapping and history', () => {
  it('round-trips fractional logical anchors including future area', () => {
    for (const l of [-3, 0, 30.4, 99, 129.2, 599.5])
      expect(mapper.toLogical(mapper.toTime(l))).toBeCloseTo(l, 7);
  });
  it('timestamp anchor remaps across timeframe without changing its price', () => {
    const t = drawing.points[0].time,
      other = new TimeMapper(
        bars.filter((_, i) => i % 2 === 0),
        '1D',
      );
    expect(other.toLogical(t)).toBe(5);
    expect(mapper.toLogical(t)).toBe(10);
  });
  it('undo/redo create, endpoint edits, move, delete, lock/unlock and branching', () => {
    const h = new DrawingHistory();
    let list: Drawing[] = [];
    for (const label of ['create', 'edit P1', 'edit P2', 'move', 'lock', 'unlock', 'delete']) {
      const next = label === 'delete' ? [] : [{ ...drawing, locked: label === 'lock' }];
      h.execute({ label, before: list, after: next });
      list = next;
    }
    for (let i = 0; i < 7; i++) expect(h.undo()).not.toBeNull();
    expect(h.undo()).toBeNull();
    for (let i = 0; i < 7; i++) expect(h.redo()).not.toBeNull();
    expect(h.redo()).toBeNull();
    h.undo();
    h.execute({ label: 'new', before: list, after: [drawing] });
    expect(h.canRedo).toBe(false);
  });
});
describe('Horizontal Line interaction regression (after Trend Line gate)', () => {
  it('creates on one press/drag/release and can move, edit, lock, and draw in future', () => {
    const { m, commit } = machine();
    m.setTool('horizontal');
    m.begin({ x: 1200, y: 120 }, 1, true);
    m.move({ x: 1290, y: 140 }, 1);
    expect(m.preview?.type).toBe('horizontal');
    m.end({ x: 1300, y: 150 }, 1);
    expect(m.drawings[0].points).toEqual([mapper.anchor(130, 15)]);
    expect(commit).toHaveBeenCalledTimes(1);
    const id = m.drawings[0].id;
    m.begin({ x: 300, y: 150 }, 2, false);
    m.end({ x: 300, y: 160 }, 2);
    expect(m.drawings[0].points[0].price).toBe(16);
    m.begin({ x: 1300, y: 160 }, 3, true);
    m.end({ x: 1350, y: 180 }, 3);
    expect(m.drawings[0].points[0]).toEqual(mapper.anchor(135, 18));
    m.drawings = [{ ...m.drawings[0], locked: true }];
    expect(m.begin({ x: 500, y: 180 }, 4, true)).toBe(false);
    expect(m.selectedId).toBe(id);
  });
});

it('tap selection never edits a timestamp anchor from another timeframe', () => {
  const foreign = {
    ...drawing,
    points: drawing.points.map((p) => ({
      ...p,
      timeframe: '5m' as const,
      logical: p.logical * 78,
    })),
  };
  const { m, commit } = machine([foreign]);
  m.begin({ x: 150, y: 150 }, 1, true);
  m.end({ x: 152, y: 151 }, 1);
  expect(m.drawings[0]).toEqual(foreign);
  expect(commit).not.toHaveBeenCalled();
});
it('grabbing a touch endpoint near its hitbox preserves the drag offset and loupe candidate', () => {
  const { m } = machine([drawing]);
  m.select('line');
  m.begin({ x: 124, y: 100 }, 1, true);
  expect(m.scene().candidate).toEqual(drawing.points[0]);
  m.move({ x: 144, y: 120 }, 1);
  expect(m.scene().candidate).toEqual(mapper.anchor(12, 12));
  m.end({ x: 144, y: 120 }, 1);
  expect(m.drawings[0].points[0]).toEqual(mapper.anchor(12, 12));
});

describe('Horizontal Ray gate', () => {
  it('uses two releases, horizontal baseline, future direction and generous endpoint hits', () => {
    const { m, commit } = machine();
    m.setTool('ray');
    m.begin({ x: 900, y: 100 }, 1, true);
    m.end({ x: 910, y: 120 }, 1);
    expect(commit).not.toHaveBeenCalled();
    m.begin({ x: 1290, y: 200 }, 2, true);
    m.move({ x: 1300, y: 240 }, 2);
    expect(m.scene().candidate!.price).toBe(12);
    expect(m.preview!.points[1]).toEqual(mapper.anchor(130, 12));
    m.end({ x: 1310, y: 250 }, 2);
    const d = m.drawings[0];
    expect(d.type).toBe('ray');
    expect(d.points).toEqual([mapper.anchor(91, 12), mapper.anchor(131, 12)]);
    expect(hitTest([d], { x: 1450, y: 120 }, projection, false, null)?.part).toBe('body');
    expect(hitTest([d], { x: 850, y: 120 }, projection, false, null)).toBeNull();
    expect(hitTest([d], { x: 1334, y: 120 }, projection, true, d.id)?.part).toBe(1);
    const left = { ...d, points: [mapper.anchor(91, 12), mapper.anchor(70, 12)] };
    expect(hitTest([left], { x: 500, y: 120 }, projection, false, null)?.part).toBe('body');
    expect(hitTest([left], { x: 950, y: 120 }, projection, false, null)).toBeNull();
  });
  it('edits either endpoint baseline, moves from snapshot without magnet, and locks to chart pan', () => {
    const ray: Drawing = {
      ...drawing,
      type: 'ray',
      points: [mapper.anchor(10, 10), mapper.anchor(30, 10)],
    };
    const { m } = machine([ray]);
    m.select(ray.id);
    m.magnetOn = true;
    m.begin({ x: 300, y: 100 }, 1, true);
    m.end({ x: 320, y: 121 }, 1);
    expect(m.drawings[0].points).toEqual([mapper.anchor(10, 12), mapper.anchor(32, 12)]);
    m.begin({ x: 100, y: 120 }, 2, false);
    m.end({ x: 110, y: 140 }, 2);
    expect(m.drawings[0].points).toEqual([mapper.anchor(11, 14), mapper.anchor(32, 14)]);
    m.begin({ x: 500, y: 140 }, 3, true);
    m.move({ x: 530, y: 160 }, 3);
    m.end({ x: 510, y: 151 }, 3);
    expect(m.drawings[0].points).toEqual([mapper.anchor(12, 15.1), mapper.anchor(33, 15.1)]);
    m.drawings = [{ ...m.drawings[0], locked: true }];
    expect(m.begin({ x: 500, y: 151 }, 4, true)).toBe(false);
  });
});

describe('Rectangle gate after Ray', () => {
  const rectangle: Drawing = { ...drawing, type: 'rectangle' };
  it('uses two release gestures and shows four independent corners with large touch hitboxes', () => {
    const { m, commit } = machine();
    m.setTool('rectangle');
    m.begin({ x: 90, y: 90 }, 1, true);
    m.end({ x: 100, y: 100 }, 1);
    expect(commit).not.toHaveBeenCalled();
    m.begin({ x: 1290, y: 240 }, 2, true);
    m.end({ x: 1300, y: 250 }, 2);
    expect(m.drawings[0].points).toEqual([mapper.anchor(10, 10), mapper.anchor(130, 25)]);
    expect(m.drawings[0].type).toBe('rectangle');
    for (const [x, y, index] of [
      [100, 100, 0],
      [200, 200, 1],
      [100, 200, 2],
      [200, 100, 3],
    ])
      expect(hitTest([rectangle], { x: x + 20, y }, projection, true, 'line')?.part).toBe(index);
    expect(hitTest([rectangle], { x: 150, y: 150 }, projection, false, null)?.part).toBe('body');
  });
  it('edits all four corners without storing pixels, preserves grab offsets and crosses safely', () => {
    for (const [index, point, target] of [
      [0, { x: 100, y: 100 }, { x: 80, y: 90 }],
      [1, { x: 200, y: 200 }, { x: 220, y: 210 }],
      [2, { x: 100, y: 200 }, { x: 80, y: 210 }],
      [3, { x: 200, y: 100 }, { x: 220, y: 90 }],
    ] as const) {
      const { m } = machine([rectangle]);
      m.select('line');
      m.begin(point, 1, true);
      expect(m.gesture?.kind).toBe('edit');
      expect(m.scene().candidate?.price).toBe(point.y / 10);
      m.end(target, 1);
      const edited = m.drawings[0];
      const actual =
        index < 2
          ? edited.points[index]
          : {
              ...edited.points[index === 2 ? 0 : 1],
              price: edited.points[index === 2 ? 1 : 0].price,
            };
      expect(projection.toPoint(actual)).toEqual(target);
      expect(edited.points).toHaveLength(2);
    }
    const crossed = editPoint(rectangle, 2, mapper.anchor(30, 5));
    expect(crossed.points).toEqual([mapper.anchor(30, 10), mapper.anchor(20, 5)]);
  });
  it('moves from immutable snapshot without magnet and locked rectangle delegates to pan', () => {
    const { m } = machine([rectangle]);
    m.select('line');
    m.magnetOn = true;
    m.begin({ x: 150, y: 150 }, 1, true);
    m.move({ x: 170, y: 170 }, 1);
    m.end({ x: 160, y: 161 }, 1);
    expect(m.drawings[0].points.map((p) => p.logical)).toEqual([11, 21]);
    expect(m.drawings[0].points[0].price).toBeCloseTo(11.1);
    expect(m.drawings[0].points[1].price).toBeCloseTo(21.1);
    m.drawings = [{ ...m.drawings[0], locked: true }];
    expect(m.begin({ x: 160, y: 160 }, 2, true)).toBe(false);
    expect(hitTest(m.drawings, { x: 110, y: 111 }, projection, true, 'line')?.part).toBe('body');
  });
});

describe('Fibonacci gate after Rectangle', () => {
  it('stores one drawing with seven retracement levels, supports either leg direction and price projection', () => {
    for (const [p1, p2] of [
      [10, 20],
      [20, 10],
    ]) {
      const { m, commit } = machine();
      m.setTool('fibonacci');
      m.begin({ x: 100, y: p1 * 10 }, 1, true);
      m.end({ x: 100, y: p1 * 10 }, 1);
      expect(commit).not.toHaveBeenCalled();
      m.begin({ x: 1300, y: p2 * 10 }, 2, true);
      m.end({ x: 1300, y: p2 * 10 }, 2);
      const d = m.drawings[0];
      expect(d.type).toBe('fibonacci');
      expect(d.levels).toEqual(DEFAULT_FIB_LEVELS);
      expect(fibPrice(p1, p2, 0)).toBe(p2);
      expect(fibPrice(p1, p2, 1)).toBe(p1);
      expect(fibSegments(d, projection).find((l) => l.level === 0.618)!.price).toBeCloseTo(
        p2 + (p1 - p2) * 0.618,
      );
      expect(m.drawings).toHaveLength(1);
      expect(hitTest([d], { x: 700, y: 150 }, projection, false, null)?.part).toBe('body');
    }
  });
  it('edits and magnetizes anchors only, snapshot movement preserves levels, and locked handles disappear', () => {
    const fib: Drawing = { ...drawing, type: 'fibonacci', levels: [...DEFAULT_FIB_LEVELS] };
    const { m } = machine([fib]);
    m.select('line');
    m.magnetOn = true;
    m.begin({ x: 100, y: 100 }, 1, true);
    m.end({ x: 110, y: 121 }, 1);
    expect(m.drawings[0].points[0]).toEqual(mapper.anchor(11, 12));
    m.begin({ x: 200, y: 200 }, 2, true);
    m.end({ x: 1300, y: 201 }, 2);
    expect(m.drawings[0].points[1]).toEqual(mapper.anchor(130, 20.1));
    const before = m.drawings[0];
    // Explicit guide midpoint is far from endpoint handles and OHLC magnet.
    const mid = { x: 705, y: 160.5 };
    m.begin(mid, 3, true);
    m.move({ x: 725, y: 180.5 }, 3);
    m.end({ x: 715, y: 171.5 }, 3);
    expect(m.drawings[0].points[0].price).toBeCloseTo(before.points[0].price + 1.1);
    expect(m.drawings[0].points[1].price).toBeCloseTo(before.points[1].price + 1.1);
    expect(m.drawings[0].levels).toEqual(DEFAULT_FIB_LEVELS);
    m.drawings = [{ ...m.drawings[0], locked: true }];
    expect(m.begin({ x: 715, y: 171.5 }, 4, true)).toBe(false);
  });
});

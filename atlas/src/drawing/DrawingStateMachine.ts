import { DEFAULT_FIB_LEVELS } from '../tools/Fibonacci';
import { controlAnchors, type ControlPart } from './DrawingModel';
import type {
  Drawing,
  ActiveTool,
  Point,
  Anchor,
  DrawingProjection,
  DrawingStyle,
  ToolKind,
} from './DrawingModel';
import { hitTest } from './HitTester';
import { magnet } from './MagnetEngine';
import { editPoint, moveDrawing } from './Movement';
import type { TimeMapper } from '../chart/TimeMapper';
export type Gesture =
  | {
      kind: 'place';
      pointerId: number;
      touch: boolean;
      tool: ToolKind;
      stage: 0 | 1 | 2;
      candidate: Anchor;
    }
  | {
      kind: 'edit';
      pointerId: number;
      touch: boolean;
      part: ControlPart | 'body';
      snapshot: Drawing;
      start: Anchor;
      startPoint: Point;
      endpointOffset: Point;
      moved: boolean;
      candidate: Anchor;
    };
export interface DrawingScene {
  drawings: Drawing[];
  preview: Drawing | null;
  selectedId: string | null;
  candidate: Anchor | null;
  loupe: boolean;
  pointer: Point | null;
}
export class DrawingStateMachine {
  tool: ActiveTool = 'select';
  magnetOn = false;
  selectedId: string | null = null;
  gesture: Gesture | null = null;
  first: Anchor | null = null;
  placed: Anchor[] = [];
  preview: Drawing | null = null;
  pointer: Point | null = null;
  constructor(
    public symbol: string,
    public mapper: TimeMapper,
    public projection: DrawingProjection,
    public drawings: Drawing[],
    private commit: (drawings: Drawing[], label: string) => void,
    private selection: (id: string | null) => void,
    private defaults: (type: ToolKind) => Partial<DrawingStyle> = () => ({}),
  ) {}
  setTool(tool: ActiveTool) {
    this.cancel();
    this.tool = tool;
  }
  select(id: string | null) {
    this.selectedId = id;
    this.selection(id);
  }
  private candidate(point: Point, snap: boolean) {
    const a = this.projection.toAnchor(point);
    if (!a) return null;
    const result = magnet(a, point, this.mapper, this.projection, snap && this.magnetOn);
    return this.tool === 'ray' && this.first ? { ...result, price: this.first.price } : result;
  }
  begin(point: Point, pointerId: number, touch: boolean): boolean {
    if (this.gesture) return false;
    const candidate = this.candidate(point, true);
    if (!candidate) return false;
    this.pointer = point;
    if (this.tool !== 'select') {
      const count = placementAnchorCount(this.tool);
      const stage = Math.min(this.placed.length, count - 1) as 0 | 1 | 2;
      this.gesture = { kind: 'place', pointerId, touch, tool: this.tool, stage, candidate };
      this.updatePreview(candidate);
      return true;
    }
    const hit = hitTest(this.drawings, point, this.projection, touch, this.selectedId);
    if (!hit) {
      this.select(null);
      return false;
    }
    this.select(hit.id);
    const d = this.drawings.find((d) => d.id === hit.id)!;
    if (d.locked) return false; // The chart owns a locked-line drag.
    const start = this.projection.toAnchor(point)!;
    const endpoint =
      hit.part === 'body' ? point : this.projection.toPoint(controlAnchors(d)[hit.part])!;
    this.gesture = {
      kind: 'edit',
      pointerId,
      touch,
      part: hit.part,
      snapshot: structuredClone(d),
      start,
      startPoint: point,
      endpointOffset: { x: endpoint.x - point.x, y: endpoint.y - point.y },
      moved: false,
      candidate: hit.part === 'body' ? start : controlAnchors(d)[hit.part],
    };
    return true;
  }
  move(point: Point, pointerId: number) {
    const g = this.gesture;
    if (!g || g.pointerId !== pointerId) return;
    this.pointer = point;
    if (g.kind === 'edit' && !g.moved) {
      if (Math.hypot(point.x - g.startPoint.x, point.y - g.startPoint.y) <= (g.touch ? 4 : 2))
        return;
      g.moved = true;
    }
    const effective =
      g.kind === 'edit' && g.part !== 'body'
        ? { x: point.x + g.endpointOffset.x, y: point.y + g.endpointOffset.y }
        : point;
    const a = this.candidate(effective, g.kind === 'place' || g.part !== 'body');
    if (!a) return;
    const adjusted = g.kind === 'edit' && g.snapshot.type === 'vertical'
      ? { ...a, price: g.snapshot.points[0].price }
      : a;
    g.candidate = adjusted;
    if (g.kind === 'place') this.updatePreview(adjusted);
    else
      this.preview =
        g.part === 'body'
          ? moveDrawing(g.snapshot, g.start, adjusted, this.mapper)
          : editPoint(g.snapshot, g.part, adjusted);
  }
  end(point: Point, pointerId: number) {
    const g = this.gesture;
    if (!g || g.pointerId !== pointerId) return;
    this.move(point, pointerId);
    if (g.kind === 'place') {
      const points = [...this.placed, g.candidate];
      if (points.length < placementAnchorCount(g.tool)) {
        this.placed = points;
        if (g.stage === 0) this.first = g.candidate;
        this.preview = this.makeDrawing(previewPoints(g.tool, points), 'preview', g.tool);
      } else {
        const d = this.makeDrawing(points, crypto.randomUUID(), g.tool);
        this.drawings = [...this.drawings, d];
        this.commit(this.drawings, 'create');
        this.first = null;
        this.placed = [];
        this.preview = null;
        this.select(d.id);
        this.tool = 'select';
      }
    } else if (this.preview) {
      const changed = this.preview;
      const latest = this.drawings.find((d) => d.id === g.snapshot.id);
      if (
        latest &&
        !latest.locked &&
        changed.points.some(
          (p, i) =>
            Math.abs(p.time - g.snapshot.points[i].time) > 1e-6 ||
            Math.abs(p.price - g.snapshot.points[i].price) > 1e-8,
        )
      ) {
        this.drawings = this.drawings.map((d) => (d.id === changed.id ? changed : d));
        this.commit(this.drawings, g.part === 'body' ? 'move' : `edit P${g.part + 1}`);
      }
      this.preview = null;
    }
    this.gesture = null;
    this.pointer = null;
  }
  private makeDrawing(
    points: Anchor[],
    id: string = crypto.randomUUID(),
    type: ToolKind = this.tool === 'select' ? 'trend' : this.tool,
  ): Drawing {
    const style = this.defaults(type);
    const { hiddenLevels, ...otherStyle } = style;
    const fibDefaultLevels = new Set<number>(DEFAULT_FIB_LEVELS);
    return {
      id,
      symbol: this.symbol,
      type,
      points,
      ...(type === 'fibonacci' ? { levels: [...DEFAULT_FIB_LEVELS] } : {}),
      locked: false,
      visible: true,
      scope: { timeframes: [points[0].timeframe] },
      style: {
        ...otherStyle,
        color: style.color ?? '#5ca9ff',
        lineWidth: style.lineWidth ?? 1,
        ...(style.widthMode !== undefined
          ? { widthMode: style.widthMode }
          : style.lineWidth === undefined
            ? { widthMode: 'atr' as const }
            : {}),
        ...(type === 'fibonacci' && hiddenLevels
          ? { hiddenLevels: hiddenLevels.filter((level) => fibDefaultLevels.has(level)) }
          : {}),
      },
    };
  }
  private updatePreview(candidate: Anchor) {
    const type = this.tool as ToolKind;
    this.preview = this.makeDrawing(previewPoints(type, [...this.placed, candidate]), 'preview', type);
  }
  cancel() {
    this.gesture = null;
    this.first = null;
    this.placed = [];
    this.preview = null;
    this.pointer = null;
  }
  scene(): DrawingScene {
    return {
      drawings: this.drawings,
      preview: this.preview,
      selectedId: this.selectedId,
      candidate: this.gesture?.candidate ?? null,
      loupe:
        !!this.gesture?.touch && (this.gesture.kind === 'place' || this.gesture.part !== 'body'),
      pointer: this.pointer,
    };
  }
}

function placementAnchorCount(tool: ToolKind): number {
  if (tool === 'horizontal' || tool === 'vertical') return 1;
  if (tool === 'channel') return 3;
  return 2;
}

function previewPoints(tool: ToolKind, anchors: Anchor[]): Anchor[] {
  const count = placementAnchorCount(tool);
  const points = anchors.slice(0, count);
  while (points.length < count) points.push(points[points.length - 1] ?? anchors[0]);
  return points;
}

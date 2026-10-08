import type { Drawing, DrawingProjection, Point } from '../drawing/DrawingModel';
import { segmentDistance } from './TrendLine';

export interface ParallelChannelGeometry {
  /** The canonical P1 → P2 edge. */
  baseline: [Point, Point];
  /** The edge offset by the projected P3 distance. */
  parallel: [Point, Point];
  /** P3 projected onto the baseline or its extended line. */
  guideBase: Point | null;
  /** The actual projected P3 handle location. */
  control: Point;
}

/**
 * Projects a channel from its three canonical anchors. P3 defines a vertical
 * screen-space offset from the P1/P2 baseline at P3.x. For a vertical P1/P2
 * edge, P3 defines horizontal width instead. A fully collapsed edge has no
 * direction, so it safely stays finite and zero-width.
 */
export function parallelChannelGeometry(
  drawing: Drawing,
  projection: DrawingProjection,
): ParallelChannelGeometry | null {
  if (drawing.points.length < 3) return null;
  const [p1, p2, p3] = drawing.points;
  const a = projection.toPoint(p1);
  const b = projection.toPoint(p2);
  const control = projection.toPoint(p3);
  if (!a || !b || !control) return null;
  if (![a.x, a.y, b.x, b.y, control.x, control.y].every(Number.isFinite)) return null;

  const dx = b.x - a.x;
  const dy = b.y - a.y;
  let parallelStart: Point;
  let parallelEnd: Point;
  let guideBase: Point | null;
  if (Math.abs(dx) < 1e-8 && Math.abs(dy) >= 1e-8) {
    const offsetX = control.x - a.x;
    parallelStart = { x: a.x + offsetX, y: a.y };
    parallelEnd = { x: b.x + offsetX, y: b.y };
    guideBase = { x: a.x, y: control.y };
  } else if (Math.abs(dx) < 1e-8) {
    parallelStart = { ...a };
    parallelEnd = { ...b };
    guideBase = null;
  } else {
    guideBase = { x: control.x, y: a.y + ((control.x - a.x) / dx) * dy };
    const offsetY = control.y - guideBase.y;
    parallelStart = { x: a.x, y: a.y + offsetY };
    parallelEnd = { x: b.x, y: b.y + offsetY };
  }
  if (
    ![parallelStart.x, parallelStart.y, parallelEnd.x, parallelEnd.y].every(Number.isFinite) ||
    (guideBase && ![guideBase.x, guideBase.y].every(Number.isFinite))
  )
    return null;

  return {
    baseline: [a, b],
    parallel: [parallelStart, parallelEnd],
    guideBase,
    control,
  };
}

function pointInPolygon(point: Point, polygon: readonly Point[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i];
    const b = polygon[j];
    const crosses = (a.y > point.y) !== (b.y > point.y);
    if (crosses && point.x < ((b.x - a.x) * (point.y - a.y)) / (b.y - a.y) + a.x)
      inside = !inside;
  }
  return inside;
}

export function parallelChannelHit(
  drawing: Drawing,
  point: Point,
  projection: DrawingProjection,
  tolerance: number,
): boolean {
  const geometry = parallelChannelGeometry(drawing, projection);
  if (!geometry) return false;
  const [a, b] = geometry.baseline;
  const [c, d] = geometry.parallel;
  if (
    segmentDistance(point, a, b) <= tolerance ||
    segmentDistance(point, c, d) <= tolerance ||
    (geometry.guideBase && segmentDistance(point, geometry.control, geometry.guideBase) <= tolerance)
  )
    return true;
  return pointInPolygon(point, [a, b, d, c]);
}

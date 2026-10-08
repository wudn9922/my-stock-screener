import { fibHit } from '../tools/Fibonacci';
import { controlAnchors, type ControlPart } from './DrawingModel';
import { insideRectangle } from '../tools/Rectangle';
import type { Drawing, DrawingProjection, Point } from './DrawingModel';
import { segmentDistance } from '../tools/TrendLine';
import { raySegment } from '../tools/HorizontalRay';
import { horizontalDistance } from '../tools/HorizontalLine';
import { parallelChannelHit } from '../tools/ParallelChannel';
export interface Hit {
  id: string;
  part: 'body' | ControlPart;
}
export function hitTest(
  drawings: readonly Drawing[],
  point: Point,
  projection: DrawingProjection,
  touch: boolean,
  selectedId: string | null,
): Hit | null {
  const handleRadius = touch ? 26 : 10,
    lineRadius = touch ? 12 : 7;
  const ordered = [...drawings]
    .reverse()
    .sort((a, b) => Number(b.id === selectedId) - Number(a.id === selectedId));
  // Handles only belong to the selected drawing. Visible radius remains 5 CSS px.
  for (const d of ordered) {
    if (!d.visible) continue;
    const ps = controlAnchors(d).map((a) => projection.toPoint(a));
    if (d.id === selectedId && !d.locked)
      for (let i = 0; i < ps.length; i++) {
        const p = ps[i];
        if (p && Math.hypot(point.x - p.x, point.y - p.y) <= handleRadius)
          return { id: d.id, part: i as ControlPart };
      }
  }
  for (const d of ordered) {
    if (!d.visible) continue;
    const a = projection.toPoint(d.points[0]);
    if (!a) continue;
    if (d.type === 'fibonacci') {
      if (fibHit(d, point, projection, lineRadius)) return { id: d.id, part: 'body' };
    } else if (d.type === 'horizontal') {
      if (horizontalDistance(point, a.y) <= lineRadius) return { id: d.id, part: 'body' };
    } else if (d.type === 'vertical') {
      if (Math.abs(point.x - a.x) <= lineRadius) return { id: d.id, part: 'body' };
    } else if (d.type === 'channel') {
      if (parallelChannelHit(d, point, projection, lineRadius)) return { id: d.id, part: 'body' };
    } else {
      const b = projection.toPoint(d.points[1]);
      if (
        d.type === 'rectangle' ||
        d.type === 'price-range' ||
        d.type === 'date-range' ||
        d.type === 'price-date-range'
      ) {
        if (b && insideRectangle(point, a, b, lineRadius)) return { id: d.id, part: 'body' };
        continue;
      }
      if (
        b &&
        segmentDistance(point, a, d.type === 'ray' ? raySegment(a, b, projection.width())[1] : b) <=
          lineRadius
      )
        return { id: d.id, part: 'body' };
    }
  }
  return null;
}

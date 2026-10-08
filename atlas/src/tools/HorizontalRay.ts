import type { Point } from '../drawing/DrawingModel';
/** P2's timestamp chooses direction. Both anchors share P1's horizontal baseline. */
export function raySegment(a: Point, b: Point, width: number): [Point, Point] {
  return [a, { x: b.x >= a.x ? width : 0, y: a.y }];
}

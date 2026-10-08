import type { Point } from '../drawing/DrawingModel';
export function horizontalDistance(p: Point, y: number): number {
  return Math.abs(p.y - y);
}

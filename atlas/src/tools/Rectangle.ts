import type { Anchor, Drawing, Point } from '../drawing/DrawingModel';
/** Identities remain stable when corners cross: two canonical and two derived anchors. */
export function rectangleAnchors(points: Anchor[]): Anchor[] {
  const [a, b] = points;
  return [a, b, { ...a, price: b.price }, { ...b, price: a.price }];
}
export function rectangleCorners(a: Point, b: Point): Point[] {
  return [a, b, { x: a.x, y: b.y }, { x: b.x, y: a.y }];
}
export function insideRectangle(p: Point, a: Point, b: Point, tolerance = 0): boolean {
  return (
    p.x >= Math.min(a.x, b.x) - tolerance &&
    p.x <= Math.max(a.x, b.x) + tolerance &&
    p.y >= Math.min(a.y, b.y) - tolerance &&
    p.y <= Math.max(a.y, b.y) + tolerance
  );
}
export function editRectangleCorner(d: Drawing, index: number, candidate: Anchor): Drawing {
  if (index < 2) return { ...d, points: d.points.map((a, i) => (i === index ? candidate : a)) };
  const xIndex = index === 2 ? 0 : 1,
    yIndex = 1 - xIndex;
  return {
    ...d,
    points: d.points.map((a, i) =>
      i === xIndex
        ? { ...candidate, price: a.price }
        : i === yIndex
          ? { ...a, price: candidate.price }
          : a,
    ),
  };
}

export function drawRectangle(
  ctx: CanvasRenderingContext2D,
  a: Point,
  b: Point,
  rx: number,
  ry: number,
  color: string,
  fillOpacity = 0.05,
) {
  const x = Math.min(a.x, b.x) * rx,
    y = Math.min(a.y, b.y) * ry;
  const w = Math.abs(a.x - b.x) * rx,
    h = Math.abs(a.y - b.y) * ry;
  ctx.save();
  ctx.globalAlpha *= Math.max(0, Math.min(0.3, fillOpacity));
  ctx.fillStyle = color;
  ctx.fillRect(x, y, w, h);
  ctx.restore();
  ctx.rect(x, y, w, h);
}

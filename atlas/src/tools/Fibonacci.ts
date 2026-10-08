import type { Drawing, DrawingProjection, Point } from '../drawing/DrawingModel';
import { segmentDistance } from './TrendLine';
import { getMarketProfile } from '../market-data/MarketProfile';
export const DEFAULT_FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1] as const;
/** Retracement 0 is the end of the leg (P2); 1 is its origin (P1). */
export function fibPrice(p1: number, p2: number, level: number): number {
  return p2 + (p1 - p2) * level;
}
export function fibSegments(d: Drawing, projection: DrawingProjection) {
  const [a, b] = d.points.map((p) => projection.toPoint(p));
  if (!a || !b) return [];
  return visibleFibLevels(d).flatMap((level) => {
    const price = fibPrice(d.points[0].price, d.points[1].price, level);
    const projected = projection.toPoint({ ...d.points[0], price });
    return projected
      ? [{ level, price, a: { x: a.x, y: projected.y }, b: { x: b.x, y: projected.y } }]
      : [];
  });
}

export function visibleFibLevels(d: Drawing): readonly number[] {
  const hidden = new Set(d.style.hiddenLevels ?? []);
  return (d.levels ?? DEFAULT_FIB_LEVELS).filter((level) => !hidden.has(level));
}

export function fibHit(d: Drawing, p: Point, projection: DrawingProjection, tolerance: number) {
  const [a, b] = d.points.map((point) => projection.toPoint(point));
  return (
    !!a &&
    !!b &&
    (segmentDistance(p, a, b) <= tolerance ||
      fibSegments(d, projection).some((line) => segmentDistance(p, line.a, line.b) <= tolerance))
  );
}
/** Labels are culled by spacing and visible bounds; narrow/mobile panes show ratios only. */
export function drawFibonacci(
  ctx: CanvasRenderingContext2D,
  d: Drawing,
  projection: DrawingProjection,
  rx: number,
  ry: number,
) {
  const lines = fibSegments(d, projection);
  ctx.beginPath();
  for (const line of lines) {
    ctx.moveTo(line.a.x * rx, line.a.y * ry);
    ctx.lineTo(line.b.x * rx, line.b.y * ry);
  }
  ctx.stroke();
  const [a, b] = d.points.map((p) => projection.toPoint(p));
  if (a && b) {
    ctx.save();
    ctx.lineWidth = rx;
    ctx.setLineDash([4 * rx, 4 * rx]);
    ctx.globalAlpha = 0.45;
    ctx.beginPath();
    ctx.moveTo(a.x * rx, a.y * ry);
    ctx.lineTo(b.x * rx, b.y * ry);
    ctx.stroke();
    ctx.restore();
  }
  if (d.style.labelsVisible === false) return;
  let lastY = -Infinity;
  ctx.font = `${11 * ry}px sans-serif`;
  for (const line of [...lines].sort((a, b) => a.a.y - b.a.y)) {
    const left = Math.max(4, Math.min(line.a.x, line.b.x)),
      right = Math.min(projection.width(), Math.max(line.a.x, line.b.x));
    if (
      right - left < 38 ||
      line.a.y < 14 ||
      line.a.y > projection.height() - 4 ||
      line.a.y - lastY < 16
    )
      continue;
    let text = String(line.level);
    if (projection.width() >= 500 && right - left >= 160) text += `  ${getMarketProfile(d.symbol).currency === 'TWD' ? 'TWD ' : '$'}${line.price.toFixed(2)}`;
    const width = ctx.measureText(text).width;
    ctx.fillStyle = '#0e1521e6';
    ctx.fillRect(left * rx, (line.a.y - 14) * ry, width + 6 * rx, 14 * ry);
    ctx.fillStyle = d.style.color;
    ctx.fillText(text, (left + 3) * rx, (line.a.y - 3) * ry);
    lastY = line.a.y;
  }
}

import type { Anchor, DrawingProjection, Point } from './DrawingModel';
import type { TimeMapper } from '../chart/TimeMapper';
export function magnet(
  candidate: Anchor,
  point: Point,
  mapper: TimeMapper,
  projection: DrawingProjection,
  enabled: boolean,
): Anchor {
  if (!enabled || mapper.isFuture(candidate.logical) || candidate.logical < 0) return candidate;
  const index = Math.round(candidate.logical),
    bar = mapper.bars[index];
  if (!bar) return candidate;
  let best = candidate,
    distance = 18;
  for (const price of [bar.open, bar.high, bar.low, bar.close]) {
    const anchor = mapper.anchor(index, price),
      p = projection.toPoint(anchor);
    if (!p) continue;
    const d = Math.hypot(p.x - point.x, p.y - point.y);
    if (d < distance) {
      distance = d;
      best = anchor;
    }
  }
  return best;
}

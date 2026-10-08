import { editRectangleCorner } from '../tools/Rectangle';
import type { Drawing, Anchor } from './DrawingModel';
import type { TimeMapper } from '../chart/TimeMapper';
/** Always derives from the immutable drag-start snapshot, never the preceding frame. */
export function moveDrawing(
  snapshot: Drawing,
  start: Anchor,
  current: Anchor,
  mapper: TimeMapper,
): Drawing {
  if (snapshot.locked) return snapshot;
  const dl = mapper.toLogical(current.time) - mapper.toLogical(start.time),
    dp = snapshot.type === 'vertical' ? 0 : current.price - start.price;
  return {
    ...snapshot,
    points: snapshot.points.map((a) => mapper.anchor(mapper.toLogical(a.time) + dl, a.price + dp)),
  };
}
export function editPoint(snapshot: Drawing, index: number, anchor: Anchor): Drawing {
  if (snapshot.locked) return snapshot;
  if (snapshot.type === 'rectangle') return editRectangleCorner(snapshot, index, anchor);
  return {
    ...snapshot,
    points: snapshot.points.map((p, i) =>
      i === index
        ? snapshot.type === 'vertical'
          ? { ...anchor, price: p.price }
          : anchor
        : snapshot.type === 'ray'
          ? { ...p, price: anchor.price }
          : p,
    ),
  };
}

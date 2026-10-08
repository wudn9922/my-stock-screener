export interface ViewRange {
  from: number;
  to: number;
  barCount?: number;
}
/** Logical ranges are tail-relative when provider history length changes. Anchors remain timestamps. */
export function restoreViewRange(range: ViewRange | undefined, barCount: number): ViewRange {
  const n = barCount - 1,
    defaultRange = { from: Math.max(0, n - 150), to: n + 40 };
  if (!range) return defaultRange;
  const offset = range.barCount === undefined ? 0 : barCount - range.barCount;
  const from = range.from + offset,
    to = range.to + offset;
  if (to < 0 || (range.barCount === undefined && from > n + 500)) return defaultRange;
  return { from, to };
}

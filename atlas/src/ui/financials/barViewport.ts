/** Half-open period range into the original series. Zooming never changes or fills source values. */
export interface BarViewport {
  start: number;
  end: number;
}

export function initialBarViewport(total: number, count: number): BarViewport {
  return { start: Math.max(0, total - Math.max(1, count)), end: total };
}

/** Keep the rightmost visible period while changing the time scale, expanding right only at the start. */
export function zoomBarViewport(view: BarViewport, total: number, direction: 'in' | 'out'): BarViewport {
  const current = view.end - view.start;
  const count = Math.min(total, Math.max(1, direction === 'in' ? Math.floor(current / 1.5) : Math.ceil(current * 1.5)));
  const start = Math.max(0, view.end - count);
  return { start, end: Math.min(total, start + count) };
}

export function panBarViewport(view: BarViewport, total: number, direction: -1 | 1): BarViewport {
  const count = view.end - view.start;
  const start = Math.max(0, Math.min(total - count, view.start + direction * Math.max(1, Math.floor(count / 2))));
  return { start, end: start + count };
}

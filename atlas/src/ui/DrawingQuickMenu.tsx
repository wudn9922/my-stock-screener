import { useLayoutEffect, useRef, type ReactNode, type RefObject } from 'react';
import type { Point } from '../drawing/DrawingModel';

/** Ephemeral click position only; drawing geometry remains in time/price coordinates. */
export function DrawingQuickMenu({ point, bounds, host, children }: {
  point: Point | null; bounds: { top: number; bottom: number } | null;
  host: RefObject<HTMLDivElement | null>; children: ReactNode;
}) {
  const menu = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = menu.current, chart = host.current;
    if (!element || !chart) return;
    const place = () => {
      const width = chart.clientWidth, height = chart.clientHeight;
      const x = point?.x ?? width / 2, y = point?.y ?? height / 3;
      // The indicator legend precedes the host inside chart-stage. Anchor to
      // the actual plot origin rather than the stage's top edge.
      element.style.left = `${chart.offsetLeft + Math.max(4, Math.min(width - element.offsetWidth - 4, x - element.offsetWidth / 2))}px`;
      // Prefer outside the whole shape, leaving its other handles/body reachable.
      const extent = bounds && bounds.bottom - bounds.top > 1 ? bounds : { top: y, bottom: y };
      const above = extent.top - element.offsetHeight - 14, below = extent.bottom + 14;
      const top = above >= 4 ? above : below + element.offsetHeight <= height - 4 ? below
        : y > element.offsetHeight + 20 ? y - element.offsetHeight - 14 : y + 14;
      element.style.top = `${chart.offsetTop + Math.max(4, Math.min(height - element.offsetHeight - 4, top))}px`;
    };
    place();
    const resize = new ResizeObserver(place);
    resize.observe(chart);
    return () => resize.disconnect();
  }, [point, bounds, host]);
  return <div ref={menu} className="drawing-quick-menu" role="toolbar" aria-label="所選畫線操作">{children}</div>;
}

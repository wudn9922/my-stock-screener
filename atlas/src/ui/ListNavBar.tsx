import { useRef, type PointerEvent } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import type { ChartListItem, ListPosition } from '../app/listContext';
import { displayTicker } from './format';

/** Minimum horizontal travel of a swipe on the bar, and how much more horizontal than vertical it must be. */
const SWIPE_DISTANCE = 48;
const SWIPE_RATIO = 1.5;

/**
 * 「‹ 上一檔｜第 N／M 檔 · 清單｜下一檔 ›」 above the chart or financials of a stock opened from a list. Touch swipes are
 * read on this bar only, never on the chart canvas (its pan/zoom/drawing gestures stay untouched).
 */
export function ListNavBar({ name, position, onStep }: {
  name: string;
  position: ListPosition;
  onStep: (item: ChartListItem) => void;
}) {
  const swipe = useRef<{ id: number; x: number; y: number } | null>(null);
  const label = (item: ChartListItem | null) => item ? `${displayTicker(item.symbol)}${item.name ? ` ${item.name}` : ''}` : undefined;
  const onPointerDown = (event: PointerEvent<HTMLElement>) => {
    if (event.pointerType === 'mouse' || !event.isPrimary) return;
    swipe.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
  };
  const onPointerUp = (event: PointerEvent<HTMLElement>) => {
    const start = swipe.current;
    swipe.current = null;
    if (!start || start.id !== event.pointerId) return;
    const dx = event.clientX - start.x, dy = event.clientY - start.y;
    if (Math.abs(dx) < SWIPE_DISTANCE || Math.abs(dx) < Math.abs(dy) * SWIPE_RATIO) return;
    const item = dx < 0 ? position.next : position.previous;
    if (item) onStep(item);
  };
  return (
    <nav className="list-nav-bar" aria-label="清單切換" data-testid="list-nav-bar"
      onPointerDown={onPointerDown} onPointerUp={onPointerUp} onPointerCancel={() => { swipe.current = null; }}>
      <button type="button" className="list-nav-step" disabled={!position.previous} aria-label="上一檔"
        title={position.previous ? `上一檔：${label(position.previous)}（[）` : '已是第一檔'}
        onClick={() => position.previous && onStep(position.previous)}>
        <ChevronLeft size={16} aria-hidden="true" /><span>上一檔</span>
      </button>
      <span className="list-nav-position" aria-live="polite">
        <b>第 {position.index + 1}／{position.total} 檔</b>
        <span className="list-nav-name" title={name}> · {name}</span>
      </span>
      <button type="button" className="list-nav-step" disabled={!position.next} aria-label="下一檔"
        title={position.next ? `下一檔：${label(position.next)}（]）` : '已是最後一檔'}
        onClick={() => position.next && onStep(position.next)}>
        <span>下一檔</span><ChevronRight size={16} aria-hidden="true" />
      </button>
    </nav>
  );
}

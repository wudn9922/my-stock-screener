import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

/**
 * One header button next to the P/E figures; the details (volatility, recommended MAs) open in a floating panel
 * over the chart instead of adding rows that shrink it. Children stay mounted while closed so their data loads once.
 */
export function StockStatsMenu({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      // Dialogs opened from the panel are portaled to <body>; clicks there must not close it.
      const target = event.target as Node;
      if (!root.current?.contains(target) && !(target instanceof Element && target.closest('dialog'))) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !document.querySelector('dialog[open]')) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);
  return (
    <div className="ws-stats-menu" ref={root}>
      <button
        type="button"
        className="ws-stats-toggle"
        aria-expanded={open}
        aria-controls={panelId}
        data-testid="stock-stats-toggle"
        onClick={() => setOpen((value) => !value)}
      >
        {label}
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      <div id={panelId} className="ws-stats-panel" role="region" aria-label={label} hidden={!open}>
        {children}
      </div>
    </div>
  );
}

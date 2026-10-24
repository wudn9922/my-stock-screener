import { useCallback, useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

/**
 * Header controls open their details in a floating panel
 * over the chart instead of adding rows that shrink it. Children stay mounted while closed so their data loads once.
 */
export function StockStatsMenu({ label, children, trigger, triggerLabel, testId = 'stock-stats-toggle' }: { label: string; children: ReactNode; trigger?: ReactNode; triggerLabel?: string; testId?: string }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const root = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<CSSProperties>();
  const placePanel = useCallback(() => {
    const bounds = root.current?.getBoundingClientRect();
    if (!bounds) return;
    const width = Math.min(380, window.innerWidth - 24);
    const navigation = document.querySelector('.site-tabbar')?.getBoundingClientRect();
    const bottom = navigation && navigation.height > 0 && !root.current?.closest('.chart-focus') ? navigation.top : window.innerHeight;
    const top = Math.max(8, Math.min(bounds.bottom + 6, bottom - 100));
    setPosition({ position: 'fixed', top, left: Math.max(12, Math.min(bounds.left, window.innerWidth - width - 12)), right: 'auto', width, maxHeight: Math.max(0, bottom - top - 12) });
  }, []);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      // Dialogs opened from the panel are portaled to <body>; clicks there must not close it.
      const target = event.target as Node;
      if (!root.current?.contains(target) && !(target instanceof Element && target.closest('dialog'))) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !document.querySelector('dialog[open]')) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('focusin', outside);
    document.addEventListener('keydown', escape);
    window.addEventListener('resize', placePanel);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('focusin', outside);
      document.removeEventListener('keydown', escape);
      window.removeEventListener('resize', placePanel);
    };
  }, [open, placePanel]);
  return (
    <div className="ws-stats-menu" ref={root}>
      <button
        type="button"
        className="ws-stats-toggle"
        aria-label={triggerLabel ?? label}
        aria-expanded={open}
        aria-controls={panelId}
        data-testid={testId}
        onClick={() => { placePanel(); setOpen((value) => !value); }}
      >
        {trigger ?? label}
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      <div id={panelId} className="ws-stats-panel" style={position} role="region" aria-label={label} hidden={!open}>
        {children}
      </div>
    </div>
  );
}

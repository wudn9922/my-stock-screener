import { useEffect, useState } from 'react';
import { earningsBadge, getEarnings, type EarningsEvent } from '../events/EarningsCalendar';
import './earnings.css';

/** Countdown to a US stock's next earnings date (screener row). Renders nothing when no badge applies. */
export function EarningsBadge({ symbol }: { symbol: string }) {
  const [event, setEvent] = useState<EarningsEvent | null>(null);
  useEffect(() => {
    let live = true;
    getEarnings(symbol).then(
      (found) => {
        if (live) setEvent(found);
      },
      () => {
        if (live) setEvent(null);
      },
    );
    return () => {
      live = false;
    };
  }, [symbol]);
  const badge = earningsBadge(event, Date.now());
  if (!badge) return null;
  return (
    <span className={`earn-badge ${badge.tone}`} title={badge.title}>
      {badge.text}
    </span>
  );
}

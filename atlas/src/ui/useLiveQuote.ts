import { useEffect, useState } from 'react';
import { liveQuotes, type LiveDaily } from '../market-data/LiveQuotes';
import { usePageActive } from '../app/pageActivity';

/** The latest live observation for one symbol while the caller is on screen (undefined until one arrives). */
export function useLiveQuote(symbol: string, enabled = true): LiveDaily | undefined {
  const [value, setValue] = useState<{ symbol: string; data: LiveDaily }>();
  // A hidden kept page leaves the shared poll loop; returning subscribes again (the feed decides what is stale).
  const active = usePageActive();
  useEffect(() => {
    if (!enabled || !active) return;
    return liveQuotes().subscribe(symbol, (data) => setValue({ symbol, data }));
  }, [symbol, enabled, active]);
  return value?.symbol === symbol ? value.data : undefined;
}

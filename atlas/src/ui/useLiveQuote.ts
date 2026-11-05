import { useEffect, useState } from 'react';
import { liveQuotes, type LiveDaily } from '../market-data/LiveQuotes';

/** The latest live observation for one symbol while the caller is mounted (undefined until one arrives). */
export function useLiveQuote(symbol: string, enabled = true): LiveDaily | undefined {
  const [value, setValue] = useState<{ symbol: string; data: LiveDaily }>();
  useEffect(() => {
    if (!enabled) return;
    return liveQuotes().subscribe(symbol, (data) => setValue({ symbol, data }));
  }, [symbol, enabled]);
  return value?.symbol === symbol ? value.data : undefined;
}

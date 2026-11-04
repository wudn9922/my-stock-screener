import type { BarResult, Quote } from './MarketDataProvider';
import type { LiveQuoteFeed } from './LiveQuotes';
import { archiveQuote } from './ArchiveFirstProvider';

/** Visible rows share the existing feed. A late historical response never replaces live prices. */
export function subscribeWatchlistQuotes(symbols: readonly string[], feed: Pick<LiveQuoteFeed, 'subscribe'>,
  history: (symbol: string) => Promise<BarResult>, receive: (symbol: string, quote: Quote) => void): () => void {
  let active = true;
  const received = new Set<string>();
  const stops = [...new Set(symbols)].map(symbol => {
    const stop = feed.subscribe(symbol, value => {
      if (!active || !value.quote) return;
      received.add(symbol); receive(symbol, value.quote);
    });
    void history(symbol).then(result => {
      if (active && !received.has(symbol) && result.bars.length) receive(symbol, result.quote ?? archiveQuote(symbol, result));
    }).catch(() => undefined);
    return stop;
  });
  return () => { active = false; for (const stop of stops) stop(); };
}

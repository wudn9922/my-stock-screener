import { displayTicker } from './format';

export interface LocalSymbol {
  symbol: string;
  name: string;
}

/** Ranks local (report) symbols: exact ticker, ticker prefix, name prefix, name contains. */
export function matchLocalSymbols(query: string, symbols: readonly LocalSymbol[], limit = 6): LocalSymbol[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const rank = (item: LocalSymbol) => {
    const ticker = displayTicker(item.symbol).replace(/^\^/, '').toLowerCase();
    const name = item.name.toLowerCase();
    if (ticker === q || item.symbol.toLowerCase() === q) return 0;
    if (ticker.startsWith(q.replace(/^\^/, ''))) return 1;
    if (name.startsWith(q)) return 2;
    if (name.includes(q)) return 3;
    return -1;
  };
  return symbols
    .map((item) => ({ item, score: rank(item) }))
    .filter((entry) => entry.score >= 0)
    .sort((a, b) => a.score - b.score)
    .slice(0, limit)
    .map((entry) => entry.item);
}

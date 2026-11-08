import type { FxRate, HoldingQuote } from './model';

/** RAM only. Missing/older quotes never erase a previously validated price. */
export function retainQuotes<T extends HoldingQuote>(
  previous: Record<string, T>,
  incoming: Record<string, T>,
) {
  const quotes = { ...previous };
  const retained: string[] = [];
  for (const [symbol, quote] of Object.entries(incoming)) {
    if (!previous[symbol] || Date.parse(quote.asOf) >= Date.parse(previous[symbol].asOf))
      quotes[symbol] = quote;
  }
  for (const symbol of Object.keys(previous))
    if (
      quotes[symbol] === previous[symbol] &&
      incoming[symbol] !== previous[symbol] &&
      (!incoming[symbol] || Date.parse(incoming[symbol].asOf) < Date.parse(previous[symbol].asOf))
    )
      retained.push(symbol);
  return { quotes, retained };
}
export function retainRates(previous: FxRate[], incoming: FxRate[]) {
  return (['USD', 'USDT'] as const).flatMap((currency) => {
    const old = previous.find((rate) => rate.currency === currency),
      next = incoming.find((rate) => rate.currency === currency);
    return next && (!old || next.asOf >= old.asOf) ? [next] : old ? [old] : [];
  });
}

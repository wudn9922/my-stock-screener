import { useEffect, useState, useSyncExternalStore } from 'react';
import { usePageActive } from '../app/pageActivity';
import { liveQuotes, isLiveSymbol, inRegularSession, type LiveDaily } from '../market-data/LiveQuotes';
import { getMarketProfile, sessionDate } from '../market-data/MarketProfile';
import { marketProxyAccess } from '../market-data/marketProxyAccess';
import type { HoldingQuote } from './model';

export function holdingLiveQuote(value: LiveDaily, now = Date.now()): HoldingQuote | undefined {
  const quote = value.quote;
  const market = quote?.market ?? getMarketProfile(value.symbol);
  // Use a validated regular-session quote, not a synthetic daily bar or an extended-hours price.
  if (!quote || !['live', 'delayed'].includes(quote.dataState ?? '') || quote.cacheStatus === 'stale'
    || !Number.isFinite(quote.price) || quote.price <= 0 || !Number.isFinite(quote.asOf)
    || quote.asOf * 1000 > now + 60_000 || now - quote.asOf * 1000 > 30 * 60_000
    || sessionDate(quote.asOf, market) !== sessionDate(now / 1000, market)
    || !inRegularSession(market, now)) return undefined;
  const prior = quote.price - quote.change;
  return {
    price: String(quote.price), previous: Number.isFinite(prior) && prior > 0 ? String(prior) : null,
    asOf: new Date(quote.asOf * 1000).toISOString(), source: quote.source ?? value.source ?? '行情代理',
    liveState: quote.dataState === 'live' ? 'live' : 'delayed',
  };
}

export function useHoldingLiveQuotes(symbols: string): Record<string, HoldingQuote> {
  const active = usePageActive();
  const access = useSyncExternalStore(marketProxyAccess.subscribe, marketProxyAccess.getSnapshot);
  const [book, setBook] = useState<{ key: string; quotes: Record<string, HoldingQuote> }>({ key: '', quotes: {} });
  const enabled = active && access.status === 'ready';
  useEffect(() => {
    if (!enabled || !symbols) return;
    let alive = true;
    const key = symbols;
    setBook({ key, quotes: {} });
    const update = (symbol: string, quote?: HoldingQuote) => {
      if (!alive) return;
      setBook(previous => {
        const quotes = { ...(previous.key === key ? previous.quotes : {}) };
        if (quote) quotes[symbol] = quote;
        else delete quotes[symbol];
        return { key, quotes };
      });
    };
    const stops = symbols.split(',').filter(isLiveSymbol).map(symbol => liveQuotes().subscribe(symbol,
      value => update(symbol, holdingLiveQuote(value)), { freshOnSubscribe: true, onError: () => update(symbol) }));
    const expire = () => {
      if (!alive) return;
      const now = Date.now();
      setBook(previous => {
        if (previous.key !== key) return previous;
        const quotes = Object.fromEntries(Object.entries(previous.quotes).filter(([symbol, quote]) =>
          now - Date.parse(quote.asOf) <= 30 * 60_000 && inRegularSession(getMarketProfile(symbol), now)));
        return Object.keys(quotes).length === Object.keys(previous.quotes).length ? previous : { key, quotes };
      });
    };
    const timer = setInterval(expire, 60_000);
    document.addEventListener('visibilitychange', expire);
    return () => {
      alive = false;
      stops.forEach(stop => stop());
      clearInterval(timer);
      document.removeEventListener('visibilitychange', expire);
    };
  }, [symbols, enabled]);
  return enabled && book.key === symbols ? book.quotes : {};
}

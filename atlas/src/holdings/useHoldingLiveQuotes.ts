import { useEffect, useState, useSyncExternalStore } from 'react';
import { usePageActive } from '../app/pageActivity';
import { liveQuotes, isLiveSymbol, inRegularSession, type LiveDaily } from '../market-data/LiveQuotes';
import { getMarketProfile, sessionDate } from '../market-data/MarketProfile';
import { marketProxyAccess } from '../market-data/marketProxyAccess';
import type { HoldingQuote } from './model';
import { holdingCloseQuote } from './quotes';

const RETAIN_QUOTE_MS = 30 * 60_000;
const browserVisible = () => typeof document === 'undefined' || !document.hidden;
function subscribeVisibility(onChange: () => void) {
  document.addEventListener('visibilitychange', onChange);
  return () => document.removeEventListener('visibilitychange', onChange);
}

export function holdingLiveQuote(value: LiveDaily, now = Date.now()): HoldingQuote | undefined {
  const quote = value.quote;
  const market = quote?.market ?? getMarketProfile(value.symbol);
  // Use a validated regular-session quote, not a synthetic daily bar or an extended-hours price.
  if (!quote || quote.symbol !== value.symbol || !['live', 'delayed'].includes(quote.dataState ?? '') || quote.cacheStatus === 'stale'
    || !Number.isFinite(quote.price) || quote.price <= 0 || !Number.isFinite(quote.asOf)
    || quote.asOf * 1000 > now + 60_000 || now - quote.asOf * 1000 > RETAIN_QUOTE_MS
    || sessionDate(quote.asOf, market) !== sessionDate(now / 1000, market)
    || !inRegularSession(market, quote.asOf * 1000, 0) || !inRegularSession(market, now, 0)) return undefined;
  const prior = quote.price - quote.change;
  return {
    price: String(quote.price), previous: Number.isFinite(prior) && prior > 0 ? String(prior) : null,
    asOf: new Date(quote.asOf * 1000).toISOString(), source: quote.source ?? value.source ?? '行情代理',
    liveState: quote.dataState === 'live' ? 'live' : 'delayed',
  };
}

export function useHoldingLiveQuotes(
  symbols: string,
  scope: string,
  updateClose: (symbol: string, quote: HoldingQuote) => void,
): Record<string, HoldingQuote> {
  const active = usePageActive();
  const visible = useSyncExternalStore(subscribeVisibility, browserVisible);
  const access = useSyncExternalStore(marketProxyAccess.subscribe, marketProxyAccess.getSnapshot);
  const [book, setBook] = useState<{ key: string; quotes: Record<string, HoldingQuote> }>({ key: '', quotes: {} });
  const enabled = active && visible && access.status === 'ready';
  const key = `${scope}|${symbols}`;
  useEffect(() => {
    if (!enabled || !symbols) {
      setBook({ key: '', quotes: {} });
      return;
    }
    let alive = true;
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
    const stops = symbols.split(',').filter(isLiveSymbol).map(symbol => liveQuotes().subscribe(symbol, value => {
      if (!alive || !browserVisible() || marketProxyAccess.getSnapshot().status !== 'ready') return;
      const now = Date.now();
      update(symbol, holdingLiveQuote(value, now));
      const close = holdingCloseQuote(symbol, {
        asOf: value.retrievedAt, quote: value.quote, market: value.quote?.market,
        source: value.source ?? '行情代理', bars: value.bars, dataState: value.quote?.dataState,
      }, now, true);
      if (close) updateClose(symbol, close);
    }, { freshOnSubscribe: true, onError: () => update(symbol) }));
    const expire = () => {
      if (!alive) return;
      const now = Date.now();
      setBook(previous => {
        if (previous.key !== key) return previous;
        const quotes = Object.fromEntries(Object.entries(previous.quotes).filter(([symbol, quote]) =>
          now - Date.parse(quote.asOf) <= RETAIN_QUOTE_MS && inRegularSession(getMarketProfile(symbol), now, 0)));
        return Object.keys(quotes).length === Object.keys(previous.quotes).length ? previous : { key, quotes };
      });
    };
    const timer = setInterval(expire, 60_000);
    return () => {
      alive = false;
      stops.forEach(stop => stop());
      clearInterval(timer);
    };
  }, [symbols, key, enabled, updateClose]);
  return enabled && book.key === key ? book.quotes : {};
}

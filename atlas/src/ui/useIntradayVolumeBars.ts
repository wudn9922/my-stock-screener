import { useEffect, useState } from 'react';
import type { BarResult, MarketDataProvider, Timeframe } from '../market-data/MarketDataProvider';
import { getMarketProfile } from '../market-data/MarketProfile';
import { inRegularSession, LIVE_INTERVAL_MS } from '../market-data/LiveQuotes';
import { marketCalendar } from '../market-data/MarketCalendar';

/**
 * Regular-session 5-minute bars for the open chart's volume readouts. A 5m chart reuses its own bars;
 * other timeframes read the provider's cached 5m series once the chart has loaded, then at most once a
 * minute while the symbol's market is in session and the page is visible. Symbols without 5m data
 * (static official series, world assets) make no request.
 */
export function useIntradayVolumeBars(
  symbol: string,
  timeframe: Timeframe,
  provider: MarketDataProvider,
  chartResult: BarResult | null,
  enabled: boolean,
): BarResult | null {
  const [loaded, setLoaded] = useState<{ key: string; result: BarResult | null }>();
  const key = `${provider.id}:${symbol}`;
  // Wait for the chart's own load: its timeframe is settled and it never competes with this request.
  const fetchOwn = enabled && timeframe !== '5m' && chartResult !== null;
  useEffect(() => {
    if (!fetchOwn) return;
    const abort = new AbortController();
    let pending = false;
    const load = async () => {
      if (pending || abort.signal.aborted) return;
      pending = true;
      try {
        const timeframes = await provider.getSymbolTimeframes?.(symbol, abort.signal);
        if (timeframes && !timeframes.includes('5m')) {
          setLoaded({ key, result: null });
          abort.abort();
          return;
        }
        const result = await provider.getBars(symbol, '5m', undefined, abort.signal);
        if (!abort.signal.aborted) setLoaded({ key, result });
      } catch {
        if (!abort.signal.aborted) setLoaded((value) => value?.key === key ? value : { key, result: null });
      } finally {
        pending = false;
      }
    };
    void load();
    const timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void marketCalendar.ensure(Date.now()).then(() => {
        if (inRegularSession(getMarketProfile(symbol), Date.now())) void load();
      });
    }, LIVE_INTERVAL_MS);
    return () => {
      abort.abort();
      clearInterval(timer);
    };
  }, [fetchOwn, key, provider, symbol]);
  if (!enabled) return null;
  if (timeframe === '5m') return chartResult;
  return loaded?.key === key ? loaded.result : null;
}

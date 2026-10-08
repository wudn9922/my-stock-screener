import type { Plugin } from 'vite';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { normalizeSymbol, timeframes, type Timeframe } from './MarketDataProvider';
import { fetch as proxyFetch, EnvHttpProxyAgent } from 'undici';
import { normalizeYahooCalendarResponse, normalizeYahooEvents, normalizeYahooResponse } from './YahooNormalizer';
import { isYahooTimeframe, yahooIntervals } from './YahooIntervals';
/** Local Vite server only. Unofficial free endpoint; no production availability guarantee. */
export function yahooProxy(): Plugin {
  const dispatcher = new EnvHttpProxyAgent();
  const cache = new Map<string, { at: number; data: unknown }>();
  const middleware = async (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    const requestUrl = req.url;
    const eventsRequest = requestUrl?.startsWith('/api/yahoo/events?') ?? false;
    if (!requestUrl || (!eventsRequest && !requestUrl.startsWith('/api/yahoo?'))) return next();
    let failureStatus = 502;
    try {
      const url = new URL(requestUrl, 'http://localhost');
      failureStatus = 400;
      const symbol = normalizeSymbol(url.searchParams.get('symbol') ?? '');
      const timeframe = eventsRequest ? undefined : (url.searchParams.get('timeframe') as Timeframe);
      if (!eventsRequest && (!timeframe || !timeframes.includes(timeframe) || !isYahooTimeframe(timeframe))) {
        throw new Error('Unsupported timeframe');
      }
      failureStatus = 502;
      const key = eventsRequest ? `${symbol}:events` : `${symbol}:${timeframe}`,
        prior = cache.get(key);
      let data = prior && Date.now() - prior.at < 60000 ? prior.data : null;
      if (!data) {
        const query = eventsRequest
          ? 'interval=1d&range=10y&events=div%2Csplits&includePrePost=false'
          : (() => {
              const mapping = yahooIntervals[timeframe! as keyof typeof yahooIntervals];
              return `interval=${mapping.interval}&range=${mapping.range}&includePrePost=false`;
            })();
        const signal = AbortSignal.timeout(12000);
        const fetchPayload = async (queryString: string) => {
          const response = await proxyFetch(
            `https://query2.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?${queryString}`,
            { dispatcher, signal, headers: { 'User-Agent': 'Mozilla/5.0 AtlasResearchTerminal/1.0' } },
          );
          if (!response.ok) {
            failureStatus = response.status === 429 ? 429 : response.status >= 500 ? 502 : 422;
            throw new Error('Yahoo returned ' + response.status);
          }
          failureStatus = 422;
          const payload: unknown = await response.json();
          failureStatus = 502;
          return payload;
        };
        const payload = await fetchPayload(query);
        if (eventsRequest) {
          const asOf = Date.now() / 1000;
          data = normalizeYahooEvents(payload, symbol, asOf);
        } else if (timeframe === '1W' || timeframe === '1M') {
          const rawDailyKey = `${symbol}:raw-daily`;
          const priorDaily = cache.get(rawDailyKey);
          const usedCachedDaily = !!priorDaily && Date.now() - priorDaily.at < 60000 && priorDaily.data !== null && priorDaily.data !== undefined;
          let rawDaily = usedCachedDaily ? priorDaily!.data : null;
          if (!rawDaily) {
            rawDaily = await fetchPayload('interval=1d&range=10y&events=div%2Csplits&includePrePost=false');
            cache.set(rawDailyKey, { at: Date.now(), data: rawDaily });
          }
          const asOf = Date.now() / 1000;
          try {
            data = normalizeYahooCalendarResponse(payload, rawDaily, asOf, timeframe, symbol);
          } catch (error) {
            if (
              !usedCachedDaily ||
              !(error instanceof Error) ||
              error.message !== 'Yahoo daily metadata is older than the native period response'
            ) throw error;
            rawDaily = await fetchPayload('interval=1d&range=10y&events=div%2Csplits&includePrePost=false');
            cache.set(rawDailyKey, { at: Date.now(), data: rawDaily });
            data = normalizeYahooCalendarResponse(payload, rawDaily, Date.now() / 1000, timeframe, symbol);
          }
        } else {
          const asOf = Date.now() / 1000;
          data = normalizeYahooResponse(payload, asOf, timeframe, symbol);
        }
        failureStatus = 502;
        cache.set(key, { at: Date.now(), data });
      }
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify(data));
    } catch {
      res.statusCode = failureStatus;
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ error: 'Prototype provider unavailable' }));
    }
  };
  return {
    name: 'atlas-yahoo-prototype',
    configureServer(server) {
      server.middlewares.use(middleware);
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware);
    },
  };
}

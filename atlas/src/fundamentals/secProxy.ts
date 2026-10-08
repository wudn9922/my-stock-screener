import type { Plugin } from 'vite';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { createSecClient, SecUnavailableError } from './SecClient';
import { FinancialNormalizer } from './FinancialNormalizer';
import { normalizeSymbol } from '../market-data/MarketDataProvider';
import { isSecEligibleSymbol } from './SecEligibility';
/** Same server boundary as the prototype market provider; normalized JSON only. */
export function createSecBackend(connection = createSecClient()) {
  const { client, close } = connection,
    normalizer = new FinancialNormalizer();
  const normalized = new WeakMap<
    object,
    Map<string, ReturnType<FinancialNormalizer['normalize']>>
  >();
  const middleware = async (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    if (!req.url?.startsWith('/api/fundamentals?')) return next();
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('Cache-Control', 'private, max-age=3600');
    try {
      if (req.method !== 'GET') {
        res.statusCode = 405;
        res.end(JSON.stringify({ error: 'GET required' }));
        return;
      }
      const url = new URL(req.url, 'http://localhost'),
        period = url.searchParams.get('period');
      let symbol: string;
      try {
        symbol = normalizeSymbol(url.searchParams.get('symbol') ?? '');
      } catch {
        res.statusCode = 400;
        res.setHeader('Cache-Control', 'no-store');
        res.end(JSON.stringify({ error: 'Invalid ticker' }));
        return;
      }
      if (period !== 'annual' && period !== 'quarterly') {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: 'Invalid financial period' }));
        return;
      }
      // Taiwan listings and `^` indices never file with the SEC: answer without an SEC request.
      if (!isSecEligibleSymbol(symbol)) {
        res.end('[]');
        return;
      }
      const result = await client.companyFacts(symbol);
      if (!result) {
        res.end('[]');
        return;
      }
      const object = result.data as object;
      const entries = normalized.get(object) ?? new Map();
      const key = symbol + ':' + period;
      const records = entries.get(key) ?? normalizer.normalize(result.data, symbol, period);
      entries.set(key, records);
      normalized.set(object, entries);
      res.end(JSON.stringify(records));
    } catch (error) {
      res.statusCode = error instanceof SecUnavailableError && error.status === 429 ? 429 : 502;
      res.setHeader('Cache-Control', 'no-store');
      res.end(
        JSON.stringify({ error: 'SEC unavailable; chart data and drawings remain independent' }),
      );
    }
  };
  return { middleware, close };
}
export function secProxy(): Plugin {
  const { middleware, close } = createSecBackend();
  return {
    name: 'atlas-sec-edgar',
    configureServer(server) {
      server.middlewares.use(middleware);
      server.httpServer?.once('close', () => {
        void close();
      });
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware);
      server.httpServer.once('close', () => {
        void close();
      });
    },
  };
}

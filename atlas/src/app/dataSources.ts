/**
 * The site's single integration point with the data layer (market data, symbol directory,
 * valuation). Pages and the chart workspace import from here only.
 */
import { createScreenerMarketProvider } from '../market-data/createScreenerMarketProvider';
import { loadSymbolDirectory, loadSymbolCatalog, searchSymbols } from '../market-data/SymbolCatalog';
import type { Bar, BarResult, MarketDataProvider } from '../market-data/MarketDataProvider';

export interface StockSearchEntry {
  symbol: string;
  name: string;
  exchange: string;
  market: 'TW' | 'US';
}

let provider: MarketDataProvider | null = null;
/** Delayed market data for any Taiwan/US stock or index (shared by pages and the chart). */
export function marketProvider(): MarketDataProvider {
  provider ??= createScreenerMarketProvider();
  return provider;
}

const dailyResults = new Map<string, Promise<BarResult>>();
/** Daily bars and their source metadata, shared by overview charts and analysis. */
export function getDailyBarResult(symbol: string): Promise<BarResult> {
  let request = dailyResults.get(symbol);
  if (!request) {
    request = marketProvider().getBars(symbol, '1D');
    dailyResults.set(symbol, request);
    request.catch(() => dailyResults.delete(symbol));
  }
  return request;
}

/** Daily bars for overview pages, de-duplicated per page session. */
export function getDailyBars(symbol: string): Promise<Bar[]> {
  return getDailyBarResult(symbol).then((result) => result.bars);
}

export async function searchStocks(query: string, limit = 20): Promise<StockSearchEntry[]> {
  await loadSymbolDirectory();
  return searchSymbols(query, limit).map((entry) => ({
    symbol: entry.symbol,
    name: entry.name,
    exchange: entry.exchange,
    market: entry.market,
  }));
}

/** Warms the directory so the first keystroke has results. Failures are silent. */
export function preloadSymbolDirectory(): void {
  void Promise.resolve()
    .then(() => loadSymbolDirectory())
    .catch(() => undefined);
}

/** Public symbol metadata only; portfolio quantities/costs never enter the provider layer. */
export async function getHoldingsSymbolDirectory() {
  const [directory, catalog] = await Promise.all([loadSymbolDirectory().catch(() => null), loadSymbolCatalog().catch(() => [])]);
  const entries = new Map((directory?.entries ?? []).map(entry => [entry.symbol, { symbol: entry.symbol, name: entry.name, market: entry.market, etf: false }]));
  for (const entry of catalog) entries.set(entry.symbol, { symbol: entry.symbol, name: entry.name, market: 'TW', etf: entry.kind === 'etf' });
  return [...entries.values()];
}

export { getPeFigures, getScreenerPeFigures, screenerPeValue, type PeFigures, type ScreenerPeFigures } from '../fundamentals/peFigures';

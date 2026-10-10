/**
 * The site's single integration point with the data layer (market data, symbol directory,
 * valuation). Pages and the chart workspace import from here only.
 */
import { createScreenerMarketProvider } from '../market-data/createScreenerMarketProvider';
import { loadSymbolDirectory, searchSymbols } from '../market-data/SymbolCatalog';
import { describePe, getValuation, type Valuation } from '../fundamentals/ValuationProvider';
import type { Bar, MarketDataProvider } from '../market-data/MarketDataProvider';
import { getMarketProfile } from '../market-data/MarketProfile';

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

const dailyBars = new Map<string, Promise<Bar[]>>();
/** Daily bars for overview pages, de-duplicated per page session. */
export function getDailyBars(symbol: string): Promise<Bar[]> {
  let request = dailyBars.get(symbol);
  if (!request) {
    request = marketProvider()
      .getBars(symbol, '1D')
      .then((result) => result.bars);
    dailyBars.set(symbol, request);
    request.catch(() => dailyBars.delete(symbol));
  }
  return request;
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

export interface PeFigures {
  /** Price / annual EPS. */
  pe: number | null;
  /** Price / TTM EPS (Taiwan: the exchange-published TTM P/E when present). */
  peTtm: number | null;
  /** True when annual EPS is zero or negative (P/E not meaningful: show 「虧損」). */
  peLoss: boolean;
  /** True when TTM EPS is zero or negative. */
  peTtmLoss: boolean;
  /** Why a figure is missing, for the tooltip. */
  reason: string | null;
  fiscalYear: number | null;
  source: string | null;
  asOf: string | null;
}

export async function getPeFigures(symbol: string, price: number | null | undefined): Promise<PeFigures> {
  const empty = (reason: string): PeFigures => ({
    pe: null,
    peTtm: null,
    peLoss: false,
    peTtmLoss: false,
    reason,
    fiscalYear: null,
    source: null,
    asOf: null,
  });
  if (symbol.startsWith('^')) return empty('指數不適用本益比');
  // Not memoized here: the provider caches (and periodically reloads) its market files itself.
  const valuation: Valuation | null = await getValuation(symbol).catch(() => null);
  if (!valuation) return empty('暫無 EPS 資料');
  const annual = describePe(price, valuation.epsAnnual);
  const exchangeTtm =
    getMarketProfile(symbol).market === 'TW' && typeof valuation.exchangePeTtm === 'number' && valuation.exchangePeTtm > 0
      ? valuation.exchangePeTtm
      : null;
  const ttm = exchangeTtm !== null ? { pe: exchangeTtm, negativeEarnings: false } : describePe(price, valuation.epsTtm);
  // Taiwan: the exchange leaves its TTM P/E blank when the last four quarters lost money. A stock that has a
  // full-year EPS on record but no TTM figure at all is therefore treated as loss-making over the last four quarters.
  const ttmLoss =
    ttm.negativeEarnings ||
    (getMarketProfile(symbol).market === 'TW' &&
      valuation.exchangePeTtm == null &&
      valuation.epsTtm == null &&
      valuation.epsAnnual != null);
  const reasonOf = (result: { pe: number | null; negativeEarnings: boolean; reason?: string }) =>
    result.pe !== null ? null : result.negativeEarnings ? '虧損，本益比不適用' : result.reason === 'missing-price' ? '暫無股價' : '暫無 EPS 資料';
  return {
    pe: annual.pe,
    peTtm: ttm.pe,
    peLoss: annual.negativeEarnings,
    peTtmLoss: ttmLoss,
    reason: annual.negativeEarnings || ttmLoss ? '虧損，本益比不適用' : reasonOf(annual) ?? reasonOf(ttm),
    fiscalYear: valuation.fiscalYear,
    source: valuation.source,
    asOf: valuation.asOf,
  };
}

/** Shared valuation math, usable by browser pages and the Node watchlist collector. */
import { describePe, getValuation, valuationProvider, type Valuation, type ValuationProvider } from './ValuationProvider';
import { getMarketProfile } from '../market-data/MarketProfile';

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

export async function getPeFigures(symbol: string, price: number | null | undefined, source = getValuation): Promise<PeFigures> {
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
  const valuation: Valuation | null = await source(symbol).catch(() => null);
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

export type ScreenerPeFigures = Readonly<Record<string, PeFigures>>;

/** Same TTM-first, annual-fallback figure shown in the screener; losses never pass a numeric range. */
export function screenerPeValue(figures: PeFigures | undefined): number | null {
  if (!figures || figures.peTtmLoss) return null;
  const value = figures.peTtm ?? figures.pe;
  return value !== null && Number.isFinite(value) && value > 0 ? value : null;
}

/** One cached market file, evaluated for every source row (including rows outside the viewport). */
export async function getScreenerPeFigures(
  items: readonly { symbol: string; close: number | null }[],
  market: 'TW' | 'US',
  provider: ValuationProvider = valuationProvider(),
): Promise<ScreenerPeFigures | null> {
  if (!await provider.loadFile(market)) return null;
  const source = provider.getValuation.bind(provider);
  return Object.fromEntries(await Promise.all(items.map(async item => [item.symbol, await getPeFigures(item.symbol, item.close, source)])));
}

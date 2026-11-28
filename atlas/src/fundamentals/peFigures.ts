/** Shared valuation math, usable by browser pages and the Node watchlist collector. */
import { describePe, getValuation, valuationProvider, type Valuation, type ValuationProvider } from './ValuationProvider';
import { getMarketProfile } from '../market-data/MarketProfile';

export interface PeFigures {
  /** Price / annual EPS. */
  pe: number | null;
  /** Price / TTM EPS (Taiwan: calculated financial EPS takes priority when supplied). */
  peTtm: number | null;
  /** True when annual EPS is zero or negative (P/E not meaningful: show 「虧損」). */
  peLoss: boolean;
  /** True when TTM EPS is zero or negative. */
  peTtmLoss: boolean;
  /** Taiwan TTM EPS is unknown: do not fall back to annual EPS for screening. */
  peTtmMissing: boolean;
  /** Why a figure is missing, for the tooltip. */
  reason: string | null;
  fiscalYear: number | null;
  source: string | null;
  asOf: string | null;
  /** Explanation of the build-time EPS calculation, for the help text. */
  epsNote?: string | null;
}

export async function getPeFigures(symbol: string, price: number | null | undefined, source = getValuation): Promise<PeFigures> {
  const empty = (reason: string): PeFigures => ({
    pe: null,
    peTtm: null,
    peLoss: false,
    peTtmLoss: false,
    peTtmMissing: false,
    reason,
    fiscalYear: null,
    source: null,
    asOf: null,
  });
  if (symbol.startsWith('^')) return empty('指數不適用本益比');
  // Not memoized here: the provider caches (and periodically reloads) its market files itself.
  const valuation: Valuation | null = await source(symbol).catch(() => null);
  if (!valuation) return empty('暫無 EPS 資料');
  const taiwan = getMarketProfile(symbol).market === 'TW';
  const annual = describePe(price, taiwan ? valuation.epsAnnualRestated ?? valuation.epsAnnual : valuation.epsAnnual);
  const exchangeTtm =
    taiwan && typeof valuation.exchangePeTtm === 'number' && valuation.exchangePeTtm > 0
      ? valuation.exchangePeTtm
      : null;
  const calculated = taiwan ? valuation.epsTtmCalculated : undefined;
  const ttm = calculated !== undefined ? describePe(price, calculated)
    : exchangeTtm !== null ? { pe: exchangeTtm, negativeEarnings: false } : describePe(price, valuation.epsTtm);
  const ttmMissing = taiwan && calculated === undefined && exchangeTtm === null && valuation.epsTtm == null;
  const reasonOf = (result: { pe: number | null; negativeEarnings: boolean; reason?: string }) =>
    result.pe !== null ? null : result.negativeEarnings ? '虧損，本益比不適用' : result.reason === 'missing-price' ? '暫無股價' : '暫無 EPS 資料';
  return {
    pe: annual.pe,
    peTtm: ttm.pe,
    peLoss: annual.negativeEarnings,
    peTtmLoss: ttm.negativeEarnings,
    peTtmMissing: ttmMissing,
    reason: ttmMissing ? '近四季 EPS 資料不足' : annual.negativeEarnings || ttm.negativeEarnings ? '虧損，本益比不適用' : reasonOf(annual) ?? reasonOf(ttm),
    ...(taiwan && valuation.epsNote ? { epsNote: valuation.epsNote } : {}),
    fiscalYear: valuation.fiscalYear,
    source: valuation.source,
    asOf: valuation.asOf,
  };
}

export type ScreenerPeFigures = Readonly<Record<string, PeFigures>>;

/** Same TTM-first, annual-fallback figure shown in the screener; losses never pass a numeric range. */
export function screenerPeValue(figures: PeFigures | undefined): number | null {
  if (!figures || figures.peTtmLoss || figures.peTtmMissing) return null;
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

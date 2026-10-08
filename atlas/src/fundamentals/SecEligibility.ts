import { getMarketProfile, isIndexSymbol } from '../market-data/MarketProfile';

/**
 * SEC EDGAR companyfacts exist only for US-listed issuers. Taiwan listings (.TW/.TWO) and Yahoo
 * `^` indices can never have them, so callers skip SEC requests for those symbols entirely.
 * ETFs and foreign issuers pass this check but usually resolve to no CIK / no supported facts.
 */
export function isSecEligibleSymbol(symbol: string): boolean {
  return getMarketProfile(symbol).market === 'US' && !isIndexSymbol(symbol);
}

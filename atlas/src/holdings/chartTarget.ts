import type { Holding } from './model';

export type HoldingChartTarget = { kind: 'chart'; symbol: string } | { kind: 'world' };

/**
 * Where tapping a holding's symbol goes. Imported TW/US symbols are already in Atlas chart form (2330.TW,
 * 6488.TWO, AAPL, BRK-B). Atlas only charts one crypto pair (BTC/USDT, on the 世界 commodities page); other
 * MEXC/crypto pairs have no chart, so they return null and stay non-clickable.
 */
export function holdingChartTarget(holding: Pick<Holding, 'market' | 'symbol'>): HoldingChartTarget | null {
  if (holding.market === 'CRYPTO') return holding.symbol === 'BTC/USDT' ? { kind: 'world' } : null;
  return holding.symbol.includes('/') ? null : { kind: 'chart', symbol: holding.symbol };
}

/** Leaf metadata shared by providers and market timing, with no runtime back-reference. */
export const timeframes = ['5m', '15m', '30m', '1H', '4H', '1D', '1W', '1M'] as const;
export type Timeframe = (typeof timeframes)[number];

export const intervalSeconds: Record<Timeframe, number> = {
  '5m': 300,
  '15m': 900,
  '30m': 1800,
  '1H': 3600,
  '4H': 14400,
  '1D': 86400,
  '1W': 604800,
  // Volatility sizing only. Monthly traversal and bar closes use calendar boundaries.
  '1M': 30 * 86400,
};

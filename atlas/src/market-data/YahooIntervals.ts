import type { Timeframe } from './MarketDataProvider';

/** Direct Yahoo intervals only. 4H is deliberately unavailable; no resampling. */
export const yahooIntervals = {
  '5m': { interval: '5m', range: '1mo' },
  '15m': { interval: '15m', range: '1mo' },
  '30m': { interval: '30m', range: '1mo' },
  '1H': { interval: '60m', range: '3mo' },
  '1D': { interval: '1d', range: '5y' },
  '1W': { interval: '1wk', range: '10y' },
  '1M': { interval: '1mo', range: '10y' },
} as const satisfies Partial<Record<Timeframe, { interval: string; range: string }>>;

export type YahooTimeframe = keyof typeof yahooIntervals;

export function isYahooTimeframe(timeframe: Timeframe): timeframe is YahooTimeframe {
  return Object.hasOwn(yahooIntervals, timeframe);
}

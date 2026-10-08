import type { Bar } from '../market-data/MarketDataProvider';

/** Return the latest Wilder ATR, or null until a complete, valid seed exists. */
export function averageTrueRange(bars: readonly Bar[], period = 14): number | null {
  if (!Number.isInteger(period) || period < 1) {
    throw new Error('ATR period must be a positive integer');
  }
  if (bars.length < period) return null;

  let seedTotal = 0;
  let atr = 0;
  let previousClose: number | null = null;

  for (let index = 0; index < bars.length; index += 1) {
    const bar = bars[index];
    if (
      !Number.isFinite(bar.open) ||
      !Number.isFinite(bar.high) ||
      !Number.isFinite(bar.low) ||
      !Number.isFinite(bar.close) ||
      bar.high < bar.low ||
      bar.high < Math.max(bar.open, bar.close) ||
      bar.low > Math.min(bar.open, bar.close)
    ) {
      return null;
    }

    const trueRange = previousClose === null
      ? bar.high - bar.low
      : Math.max(
          bar.high - bar.low,
          Math.abs(bar.high - previousClose),
          Math.abs(bar.low - previousClose),
        );
    if (!Number.isFinite(trueRange) || trueRange < 0) return null;

    if (index < period) {
      seedTotal += trueRange;
      if (!Number.isFinite(seedTotal)) return null;
      if (index === period - 1) atr = seedTotal / period;
    } else {
      atr = (atr * (period - 1) + trueRange) / period;
      if (!Number.isFinite(atr)) return null;
    }
    previousClose = bar.close;
  }

  return atr;
}

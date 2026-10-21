import type { Bar } from './MarketDataProvider';

export interface StockVolatility {
  atr: number;
  atrPercent: number;
  intradayRangePercent: number;
  time: number;
  close: number;
}

/** Completed daily bars only. Wilder ATR starts with 20 TRs, each with a known previous close. */
export function stockVolatility(bars: readonly Bar[]): StockVolatility | null {
  const period = 20;
  if (bars.length < period + 1) return null;
  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i];
    if (
      ![bar.time, bar.high, bar.low, bar.close].every(Number.isFinite) ||
      bar.close <= 0 ||
      bar.low <= 0 ||
      bar.high < Math.max(bar.low, bar.close) ||
      bar.low > bar.close ||
      (i > 0 && bar.time <= bars[i - 1].time)
    )
      return null;
  }
  let atr = 0;
  let intraday = 0;
  for (let i = 1; i < bars.length; i++) {
    const { high, low } = bars[i];
    const previousClose = bars[i - 1].close;
    const tr = Math.max(high - low, Math.abs(high - previousClose), Math.abs(low - previousClose));
    if (i <= period) {
      atr += tr;
      if (i === period) atr /= period;
    } else atr = (atr * (period - 1) + tr) / period;
    if (i >= bars.length - period) intraday += ((high - low) / previousClose) * 100;
  }
  const last = bars[bars.length - 1];
  intraday /= period;
  const atrPercent = (atr / last.close) * 100;
  if (![atr, atrPercent, intraday].every(Number.isFinite)) return null;
  return {
    atr,
    atrPercent,
    intradayRangePercent: intraday,
    time: last.time,
    close: last.close,
  };
}

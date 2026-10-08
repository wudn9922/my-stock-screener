import type { Bar } from '../market-data/MarketDataProvider';
import type { PriceSource } from './IndicatorRegistry';
export function sma(
  bars: readonly Bar[],
  period: number,
  source: PriceSource = 'close',
): { time: number; value: number }[] {
  if (!Number.isInteger(period) || period < 1)
    throw new Error('SMA period must be a positive integer');
  let sum = 0;
  const result = [];
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i][source];
    if (i >= period) sum -= bars[i - period][source];
    if (i >= period - 1) result.push({ time: bars[i].time, value: sum / period });
  }
  return result;
}

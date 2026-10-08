import type { Bar } from '../market-data/MarketDataProvider';
import type { PriceSource } from './IndicatorRegistry';

export function ema(
  bars: readonly Bar[],
  period: number,
  source: PriceSource = 'close',
): { time: number; value: number }[] {
  if (!Number.isInteger(period) || period < 1)
    throw new Error('EMA period must be a positive integer');
  if (bars.length < period) return [];

  const result: { time: number; value: number }[] = [];
  let seed = 0;
  for (let i = 0; i < period; i++) seed += bars[i][source];

  let value = seed / period;
  result.push({ time: bars[period - 1].time, value });
  const multiplier = 2 / (period + 1);
  for (let i = period; i < bars.length; i++) {
    value = (bars[i][source] - value) * multiplier + value;
    result.push({ time: bars[i].time, value });
  }
  return result;
}

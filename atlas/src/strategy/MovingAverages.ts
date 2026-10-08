import type { Bar } from '../market-data/MarketDataProvider';
import type { MovingAverageType } from './types';

/** Returns one slot per input bar; null means the average has not warmed up. */
export function movingAverage(
  bars: readonly Bar[],
  period: number,
  type: MovingAverageType,
): (number | null)[] {
  if (!Number.isInteger(period) || period < 1 || period > 5000) {
    throw new Error('Moving average period must be an integer from 1 through 5000');
  }
  const values: (number | null)[] = Array(bars.length).fill(null);
  if (bars.length < period) return values;

  let average = 0;
  for (let i = 0; i < period; i++) average += (bars[i].close - average) / (i + 1);
  values[period - 1] = average;
  if (type === 'SMA') {
    for (let i = period; i < bars.length; i++) {
      average += (bars[i].close - bars[i - period].close) / period;
      if (!Number.isFinite(average)) throw new Error('Moving average calculation produced a non-finite value');
      values[i] = average;
    }
  } else {
    const alpha = 2 / (period + 1);
    for (let i = period; i < bars.length; i++) {
      average = bars[i].close * alpha + average * (1 - alpha);
      if (!Number.isFinite(average)) throw new Error('Moving average calculation produced a non-finite value');
      values[i] = average;
    }
  }
  return values;
}

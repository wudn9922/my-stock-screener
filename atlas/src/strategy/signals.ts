import type { Bar } from '../market-data/MarketDataProvider';
import { movingAverage } from './MovingAverages';
import type { StrategyConfig, StrategySignal } from './types';

/** Generates signals at the close of each eligible closed bar. */
export function generateSignals(
  config: StrategyConfig,
  closedBars: readonly Bar[],
): StrategySignal[] {
  const fast = movingAverage(closedBars, config.fastPeriod, config.fastType);
  const slow = movingAverage(closedBars, config.slowPeriod, config.slowType);
  const signals: StrategySignal[] = [];

  for (let i = 1; i < closedBars.length; i++) {
    const current = closedBars[i];
    const previous = closedBars[i - 1];
    let crossedUp = false;
    let crossedDown = false;

    if (config.kind === 'ma-cross') {
      const priorFast = fast[i - 1], priorSlow = slow[i - 1], nowFast = fast[i], nowSlow = slow[i];
      if (priorFast !== null && priorSlow !== null && nowFast !== null && nowSlow !== null) {
        crossedUp = priorFast <= priorSlow && nowFast > nowSlow;
        crossedDown = priorFast >= priorSlow && nowFast < nowSlow;
      }
    } else {
      const priorAverage = slow[i - 1], currentAverage = slow[i];
      if (priorAverage !== null && currentAverage !== null) {
        crossedUp = previous.close <= priorAverage && current.close > currentAverage;
        crossedDown = previous.close >= priorAverage && current.close < currentAverage;
      }
    }

    const action = config.direction === 'long'
      ? crossedUp ? 'BUY' : crossedDown ? 'EXIT' : null
      : crossedDown ? 'SELL' : crossedUp ? 'COVER' : null;
    if (action) {
      signals.push({
        action,
        time: current.time,
        close: current.close,
        fastValue: fast[i],
        slowValue: slow[i],
      });
    }
  }
  return signals;
}

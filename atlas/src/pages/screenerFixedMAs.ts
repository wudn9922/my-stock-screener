import type { ParameterStock } from '../cloud/settingsApi';
import { parameterTickerKey, stockParameterMap } from '../cloud/stockParameters';
import type { Bar } from '../market-data/MarketDataProvider';
import { getMarketProfile, sessionDate } from '../market-data/MarketProfile';
import type { GroupItem, MetricMa } from '../report/schema';
import { maDistance, maValue } from '../ui/format';
import { maDeviation, type FixedScreenerMas } from './screenerFilters';
import { mapWithConcurrency } from './worldPerformance';

/** Explicit stock rows (original grouped row first) override EMA20; generic group defaults never do. */
export async function loadScreenerFixedMAs(
  items: readonly GroupItem[],
  stocks: readonly ParameterStock[],
  loadBars: (symbol: string) => Promise<Pick<Bar, 'time' | 'close'>[]>,
): Promise<FixedScreenerMas> {
  const fixed = stockParameterMap(stocks);
  const result: Record<string, { period: number; distance: number | null }> = {};
  await mapWithConcurrency(items.filter(item => fixed[parameterTickerKey(item.symbol)]), 4, async item => {
    const periods = fixed[parameterTickerKey(item.symbol)].filter(period => Number.isInteger(period) && period > 0 && period <= 1000);
    if (!periods.length) return;
    const period = Math.min(...periods);
    let distance = maDistance(item.close, maValue(item.maValues, period));
    if (distance === null && [20, 50, 200].includes(period)) distance = maDeviation(item, period as MetricMa);
    if (distance === null && item.close !== null && item.asOf) {
      try {
        const profile = getMarketProfile(item.symbol);
        // Align the reference to the report's observation date, never to a newer quote.
        const bars = (await loadBars(item.symbol)).filter(bar => sessionDate(bar.time, profile) <= item.asOf!);
        const tail = bars.slice(-period);
        if (tail.length === period && sessionDate(tail.at(-1)!.time, profile) === item.asOf && tail.every(bar => Number.isFinite(bar.close) && bar.close > 0)) {
          distance = maDistance(item.close, tail.reduce((sum, bar) => sum + bar.close, 0) / period);
        }
      } catch {
        // A missing fixed reference stays missing; it must not fall back to EMA20.
      }
    }
    result[item.symbol] = { period, distance };
  });
  return result;
}

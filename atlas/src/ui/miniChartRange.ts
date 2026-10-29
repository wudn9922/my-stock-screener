import type { Bar } from '../market-data/MarketDataProvider';

/** A calendar-month window anchored to the latest available session, including holidays/closures. */
export function miniChartStart(bars: readonly Pick<Bar, 'time'>[], months?: number): number {
  if (!months || !bars.length) return Math.max(0, bars.length - 130);
  const cutoff = new Date(bars.at(-1)!.time * 1000);
  const day = cutoff.getUTCDate();
  cutoff.setUTCDate(1);
  cutoff.setUTCMonth(cutoff.getUTCMonth() - months);
  const lastDay = new Date(Date.UTC(cutoff.getUTCFullYear(), cutoff.getUTCMonth() + 1, 0)).getUTCDate();
  cutoff.setUTCDate(Math.min(day, lastDay));
  cutoff.setUTCHours(0, 0, 0, 0);
  const index = bars.findIndex(bar => bar.time * 1000 >= cutoff.getTime());
  return Math.max(0, index);
}

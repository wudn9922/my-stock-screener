import { validKline } from '../market-data/KlineIntegrity';
import { closedBars, type BarResult } from '../market-data/MarketDataProvider';
import { calendarDate, getMarketProfile } from '../market-data/MarketProfile';
import type { DirectionalVolumeFeature } from './research';

export interface IndexVolumeFeature extends DirectionalVolumeFeature {
  date: string;
}

/** Adjacent completed sessions only; no RVOL, interpolation or substitute symbols. */
export function indexVolumeFeatures(result: BarResult, symbol: string, now = Date.now() / 1000): IndexVolumeFeature[] {
  const market = result.market ?? getMarketProfile(symbol);
  // A bar captured before close must not become a completed observation merely
  // because a stale cached response is read after the closing bell.
  const asOf = Math.min(now, result.asOf ?? now);
  const bars = closedBars({ ...result, market }, '1D', asOf);
  let previousValid = false;
  return bars.map((bar, index) => {
    const previous = bars[index - 1];
    const change = previous && previous.close > 0
      ? (bar.close / previous.close - 1) * 100 : null;
    const valid = validKline(bar) && bar.volume > 0 && (change === null || Math.abs(change) <= 40);
    const feature = {
      date: calendarDate(bar.time, market),
      volume: Number.isFinite(bar.volume) ? bar.volume : null,
      valid,
      returnPct: valid && previousValid ? change : null,
    };
    previousValid = valid;
    return feature;
  }).slice(-21);
}

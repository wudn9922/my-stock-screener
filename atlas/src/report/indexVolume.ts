import { validKline } from '../market-data/KlineIntegrity';
import { closedBars, type BarResult } from '../market-data/MarketDataProvider';
import { calendarDate, getMarketProfile } from '../market-data/MarketProfile';
import type { DirectionalVolumeFeature } from './research';

export interface IndexVolumeFeature extends DirectionalVolumeFeature {
  date: string;
}

/** Adjacent completed sessions only; no RVOL or interpolation (see indexVolumeSource for the one labelled substitute). */
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

/**
 * Indices whose price source publishes no volume at all. Their directional volume uses a labelled ETF's
 * volume on the same calendar date; prices, returns and dates stay the index's own.
 */
export const INDEX_VOLUME_PROXIES: Readonly<Record<string, { symbol: string; label: string }>> = {
  '^SOX': { symbol: 'SOXX', label: 'SOXX（iShares 半導體 ETF）' },
};

/** True when any recent bar carries a positive volume (a real index volume series). */
export function hasIndexVolume(result: BarResult): boolean {
  return result.bars.slice(-30).some((bar) => Number.isFinite(bar.volume) && bar.volume > 0);
}

/** Replaces each index bar's volume with the proxy's volume on the same calendar date (0 when the proxy has no bar). */
export function withProxyVolume(index: BarResult, proxy: BarResult, symbol: string): BarResult {
  const market = index.market ?? getMarketProfile(symbol);
  const proxyMarket = proxy.market ?? market;
  const volumes = new Map(proxy.bars.map((bar) => [calendarDate(bar.time, proxyMarket), bar.volume]));
  // A proxy observed earlier than the index cannot vouch for a later index session.
  const observed = [index.asOf, proxy.asOf].filter((value): value is number => typeof value === 'number');
  return {
    ...index,
    market,
    asOf: observed.length ? Math.min(...observed) : undefined,
    bars: index.bars.map((bar) => ({ ...bar, volume: volumes.get(calendarDate(bar.time, market)) ?? 0 })),
  };
}

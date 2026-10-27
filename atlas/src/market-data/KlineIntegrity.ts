import { quarantined } from '../../../docs/report/kline-corrections.json';
import type { Bar, BarResult, Timeframe } from './MarketDataProvider';
import { calendarPeriodStart } from './MarketDataProvider';
import { calendarDate, getMarketProfile } from './MarketProfile';

/** The published correction log is the quarantine authority, including offline cache reads. */
export function isQuarantinedBar(symbol: string, time: number, timeframe: Timeframe = '1D'): boolean {
  const dates = quarantined.filter(row => row.symbol === symbol.toUpperCase()).map(row => row.date);
  if (!dates.length) return false;
  const market = getMarketProfile(symbol);
  if (timeframe === '1W' || timeframe === '1M') {
    return dates.some(date => calendarPeriodStart(Date.parse(date + 'T04:00:00Z') / 1000, timeframe, market)
      === calendarPeriodStart(time, timeframe, market));
  }
  return dates.includes(calendarDate(time, market));
}

export function validKline(bar: Bar): boolean {
  return [bar.open, bar.high, bar.low, bar.close].every(value => Number.isFinite(value) && value > 0)
    && Number.isFinite(bar.volume) && bar.volume >= 0
    && bar.low <= Math.min(bar.open, bar.close) && Math.max(bar.open, bar.close) <= bar.high;
}

/**
 * A source's stand-in for a session that never traded (market closure, typhoon day, holiday): no volume and
 * one price equal to the previous close for open, high, low and close. A real session always differs in
 * volume or price, so only these flat copies are dropped (daily bars; never the first bar).
 */
export function isClosedSessionPlaceholder(bar: Bar, previous: Bar | undefined): boolean {
  return !!previous && bar.volume === 0 && bar.open === bar.close && bar.high === bar.close && bar.low === bar.close
    && bar.close === previous.close;
}

export function usableBars(bars: readonly Bar[], symbol: string, timeframe: Timeframe = '1D'): Bar[] {
  const usable: Bar[] = [];
  for (const bar of bars) {
    if (!validKline(bar) || isQuarantinedBar(symbol, bar.time, timeframe)) continue;
    if (timeframe === '1D' && isClosedSessionPlaceholder(bar, usable.at(-1))) continue;
    usable.push(bar);
  }
  return usable;
}

export function usableBarResult(result: BarResult, symbol: string, timeframe: Timeframe): BarResult {
  const bars = usableBars(result.bars, symbol, timeframe);
  if (!bars.length) throw new Error('沒有未隔離且有效的 K 線資料');
  return { ...result, bars, latestBarAt: bars.at(-1)!.time,
    ...(result.sessionCloseObservations ? { sessionCloseObservations: result.sessionCloseObservations.filter(time => bars.some(bar => bar.time === time)) } : {}) };
}

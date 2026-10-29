import { indicatorColors, type IndicatorInstance } from '../indicators/IndicatorRegistry';

const prefix = 'atlas-parameter-ma:';

/** Saved daily parameters are hydrated as editable indicators, without touching drawings or other scopes. */
export function reconcileParameterIndicators(symbol: string, indicators: readonly IndicatorInstance[], periods: readonly number[]): IndicatorInstance[] {
  periods = [...new Set(periods.filter(period => Number.isInteger(period) && period > 0 && period <= 1000))];
  const next = indicators.filter(indicator => !indicator.id.startsWith(prefix) || indicator.locked || periods.includes(indicator.period));
  for (const [index, period] of periods.entries()) {
    if (next.some(indicator => indicator.type === 'SMA' && indicator.scope.timeframe === '1D' && indicator.period === period)) continue;
    next.push({ id: `${prefix}${symbol}:${period}`, symbol, type: 'SMA', period, source: 'close', visible: true, locked: false,
      lineWidth: 2, widthMode: 'pixels', color: indicatorColors[index % indicatorColors.length]!, scope: { timeframe: '1D' } });
  }
  return next;
}

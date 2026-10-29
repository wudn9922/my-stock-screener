import type { ParameterStock } from './settingsApi';

export const FIXED_STOCK_GROUP = 'ungrouped';

/** Match the collector's ticker keys without confusing TWSE with TPEx. */
export function parameterTickerKey(ticker: string): string {
  const key = ticker.trim().toUpperCase();
  if (/^\d+$/.test(key)) return `${key}.TW`;
  return /\.(TW|TWO)$/.test(key) ? key : key.replaceAll('.', '-');
}

export function parameterMAs(stock: Pick<ParameterStock, 'ma1' | 'ma2' | 'ma3' | 'ma4'>): number[] {
  return [stock.ma1, stock.ma2, stock.ma3, stock.ma4].filter(n => n > 0);
}

/** Preserve the original grouped record; legacy dormant duplicates must not override it. */
export function stockParameterRows(stocks: readonly ParameterStock[], ticker: string): ParameterStock[] {
  const key = parameterTickerKey(ticker);
  return stocks.filter(stock => parameterTickerKey(stock.ticker) === key && stock.group_id && stock.group_id !== 'admin_index')
    .sort((a, b) => Number(a.group_id === FIXED_STOCK_GROUP) - Number(b.group_id === FIXED_STOCK_GROUP) || a.id - b.id);
}

export function stockParameterMap(stocks: readonly ParameterStock[]): Record<string, number[]> {
  const result: Record<string, number[]> = {};
  for (const ticker of new Set(stocks.map(stock => parameterTickerKey(stock.ticker)))) {
    const current = stockParameterRows(stocks, ticker)[0];
    if (current && parameterMAs(current).length) result[ticker] = parameterMAs(current);
  }
  return result;
}

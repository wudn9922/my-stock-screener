import type { Bar, Timeframe } from '../market-data/MarketDataProvider';
import type { WidthMode } from '../chart/StrokeWidth';
export type PriceSource = 'open' | 'high' | 'low' | 'close';
export interface IndicatorInstance {
  id: string;
  symbol: string;
  type: 'SMA' | 'EMA' | 'Volume';
  period: number;
  source: PriceSource;
  visible: boolean;
  locked: boolean;
  lineWidth: 1 | 2 | 3 | 4;
  widthMode?: WidthMode;
  color: string;
  scope: { timeframe?: Timeframe };
}
export type IndicatorType = IndicatorInstance['type'];
export const indicatorColors = ['#f0b35b', '#8e99f3', '#49cbbb', '#e479a0', '#97b85a'];
export interface IndicatorDefinition {
  type: IndicatorType;
  series: 'Line' | 'Histogram';
  calculate: (
    bars: readonly Bar[],
    period: number,
    source: PriceSource,
  ) => { time: number; value: number }[];
}
export class IndicatorRegistry {
  private definitions = new Map<IndicatorType, IndicatorDefinition>();
  register(definition: IndicatorDefinition) {
    this.definitions.set(definition.type, definition);
  }
  get(type: IndicatorType) {
    return this.definitions.get(type);
  }
}

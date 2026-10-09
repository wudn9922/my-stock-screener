import { rectangleAnchors } from '../tools/Rectangle';
import type { WidthMode } from '../chart/StrokeWidth';
import type { Timeframe } from '../market-data/MarketDataProvider';
export interface Anchor {
  time: number;
  logical: number;
  price: number;
  timeframe: Timeframe;
}
export type ToolKind = 'trend' | 'horizontal' | 'ray' | 'rectangle' | 'fibonacci' | 'channel' | 'price-range' | 'date-range' | 'price-date-range' | 'vertical';
export interface DrawingStyle {
  color: string;
  lineWidth: number;
  widthMode?: WidthMode;
  lineStyle?: 'solid' | 'dashed' | 'dotted';
  opacity?: number;
  fillOpacity?: number;
  labelsVisible?: boolean;
  hiddenLevels?: number[];
}
export type ActiveTool = ToolKind | 'select';
export interface Drawing {
  id: string;
  symbol: string;
  type: ToolKind;
  points: Anchor[];
  levels?: number[];
  locked: boolean;
  visible: boolean;
  scope: { timeframes: 'all' | Timeframe[] };
  style: DrawingStyle;
}
export interface Point {
  x: number;
  y: number;
}
export interface DrawingProjection {
  toPoint(anchor: Anchor): Point | null;
  toAnchor(point: Point): Anchor | null;
  width(): number;
  height(): number;
  logicalAt?(anchor: Anchor): number;
  strokeWidth?(style: DrawingStyle, referencePrice: number): number;
}
export function drawingVisible(d: Drawing, symbol: string, timeframe: Timeframe): boolean {
  return (
    d.symbol === symbol &&
    d.visible &&
    (d.scope.timeframes === 'all' || d.scope.timeframes.includes(timeframe))
  );
}

export const toolNames: Record<ToolKind, string> = {
  trend: '趨勢線',
  horizontal: '水平線',
  ray: '水平射線',
  rectangle: '矩形',
  fibonacci: '費波那契回撤',
  channel: '平行通道',
  'price-range': '價格區間',
  'date-range': '日期區間',
  'price-date-range': '價格與日期區間',
  vertical: '垂直線',
};

export type ControlPart = 0 | 1 | 2 | 3;
export function controlAnchors(d: Drawing): Anchor[] {
  return d.type === 'rectangle' ? rectangleAnchors(d.points) : d.points;
}

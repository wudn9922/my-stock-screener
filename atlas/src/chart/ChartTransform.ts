import type { IChartApi, ISeriesApi, Logical } from 'lightweight-charts';
import type { Anchor, DrawingProjection, DrawingStyle, Point } from '../drawing/DrawingModel';
import type { TimeMapper } from './TimeMapper';
import { resolveStrokeWidth } from './StrokeWidth';
export class ChartTransform implements DrawingProjection {
  constructor(
    readonly chart: IChartApi,
    readonly series: ISeriesApi<'Candlestick'>,
    public mapper: TimeMapper,
    private atr: number | null = null,
  ) {}
  strokeWidth(style: DrawingStyle, referencePrice: number): number {
    return resolveStrokeWidth(
      style.widthMode,
      style.lineWidth,
      this.atr,
      referencePrice,
      (price) => this.series.priceToCoordinate(price),
    );
  }
  toPoint(a: Anchor): Point | null {
    const x = this.chart.timeScale().logicalToCoordinate(this.mapper.toLogical(a.time) as Logical),
      y = this.series.priceToCoordinate(a.price);
    return x === null || y === null ? null : { x, y };
  }
  toAnchor(p: Point): Anchor | null {
    const logical = this.chart.timeScale().coordinateToLogical(p.x),
      price = this.series.coordinateToPrice(p.y);
    return logical === null || price === null
      ? null
      : this.mapper.anchor(Number(logical), Number(price));
  }
  logicalAt(anchor: Anchor) {
    return this.mapper.toLogical(anchor.time);
  }
  width() {
    return this.chart.paneSize().width;
  }
  height() {
    return this.chart.paneSize().height;
  }
}

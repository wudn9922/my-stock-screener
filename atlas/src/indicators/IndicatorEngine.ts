import {
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import { IndicatorRegistry, type IndicatorInstance, type IndicatorType } from './IndicatorRegistry';
import { ema } from './ExponentialMovingAverage';
import { sma } from './MovingAverage';
import { coloredVolume, volume, volumeSma, VOLUME_SMA_PERIOD } from './Volume';
import type { Bar, Timeframe } from '../market-data/MarketDataProvider';
import { nativeLineWidth, resolveStrokeWidth, type PriceToCoordinate } from '../chart/StrokeWidth';

type LineEntry = {
  kind: 'Line';
  type: IndicatorType;
  api: ISeriesApi<'Line'>;
  widthMode: IndicatorInstance['widthMode'];
  fallbackLineWidth: IndicatorInstance['lineWidth'];
  appliedLineWidth: IndicatorInstance['lineWidth'];
  signature: string;
  visible: boolean;
  values: Map<number, number>;
};

type HistogramEntry = {
  kind: 'Histogram';
  type: 'Volume';
  api: ISeriesApi<'Histogram'>;
  averageApi: ISeriesApi<'Line'>;
  dataSignature: string;
  averageSignature: string;
  visible: boolean;
  values: Map<number, number>;
  averageValues: Map<number, number>;
};

type SeriesEntry = LineEntry | HistogramEntry;

export class IndicatorEngine {
  private series = new Map<string, SeriesEntry>();
  readonly registry = new IndicatorRegistry();

  constructor(private chart: IChartApi) {
    this.registry.register({ type: 'SMA', series: 'Line', calculate: sma });
    this.registry.register({ type: 'EMA', series: 'Line', calculate: ema });
    this.registry.register({
      type: 'Volume',
      series: 'Histogram',
      calculate: (bars) => volume(bars),
    });
  }

  sync(
    instances: readonly IndicatorInstance[],
    bars: readonly Bar[],
    timeframe: Timeframe,
    dataRevision: number,
  ) {
    const active = instances.filter((i) => !i.scope.timeframe || i.scope.timeframe === timeframe);
    const ids = new Set(active.map((i) => i.id));
    for (const [id, entry] of this.series) {
      if (!ids.has(id)) {
        this.removeEntry(entry);
        this.series.delete(id);
      }
    }

    for (const instance of active) {
      const definition = this.registry.get(instance.type);
      if (!definition) continue;

      let entry = this.series.get(instance.id);
      if (entry && (entry.kind !== definition.series || entry.type !== instance.type)) {
        this.removeEntry(entry);
        this.series.delete(instance.id);
        entry = undefined;
      }

      if (!entry && definition.series === 'Line') {
        entry = {
          kind: 'Line',
          type: instance.type,
          api: this.chart.addSeries(LineSeries, {
            lineWidth: instance.lineWidth,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
            title: '',
          }),
          widthMode: instance.widthMode,
          fallbackLineWidth: instance.lineWidth,
          appliedLineWidth: instance.lineWidth,
          signature: '',
          visible: instance.visible,
          values: new Map(),
        };
        this.series.set(instance.id, entry);
      } else if (!entry && definition.series === 'Histogram') {
        const priceScaleId = `volume-indicator-${instance.id}`;
        const api = this.chart.addSeries(HistogramSeries, {
          priceFormat: { type: 'volume' },
          priceScaleId,
          priceLineVisible: false,
          lastValueVisible: false,
          title: '',
        });
        api.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
        const averageApi = this.chart.addSeries(LineSeries, {
          color: instance.color,
          lineWidth: instance.lineWidth,
          priceScaleId,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          title: '',
        });
        entry = {
          kind: 'Histogram',
          type: 'Volume',
          api,
          averageApi,
          dataSignature: '',
          averageSignature: '',
          visible: instance.visible,
          values: new Map(),
          averageValues: new Map(),
        };
        this.series.set(instance.id, entry);
      }

      if (!entry) continue;
      entry.visible = instance.visible;
      if (entry.kind === 'Line') {
        entry.widthMode = instance.widthMode;
        entry.fallbackLineWidth = instance.lineWidth;
        const options: { color: string; visible: boolean; title: string; lineWidth?: 1 | 2 | 3 | 4 } = {
          color: instance.color,
          visible: instance.visible,
          title: '',
        };
        if (instance.widthMode !== 'atr' && entry.appliedLineWidth !== instance.lineWidth) {
          options.lineWidth = instance.lineWidth;
          entry.appliedLineWidth = instance.lineWidth;
        }
        entry.api.applyOptions({
          ...options,
        });
        const signature = `${dataRevision}:${instance.type}:${instance.period}:${instance.source}`;
        if (entry.signature !== signature) {
          const data = definition.calculate(bars, instance.period, instance.source);
          entry.api.setData(data.map((point) => ({ ...point, time: point.time as UTCTimestamp })));
          entry.values = new Map(data.map((point) => [point.time, point.value]));
          entry.signature = signature;
        }
      } else {
        entry.api.applyOptions({ color: instance.color, visible: instance.visible, title: '' });
        entry.averageApi.applyOptions({
          color: instance.color,
          lineWidth: instance.lineWidth,
          visible: instance.visible,
          title: '',
        });
        const dataSignature = `${dataRevision}:Volume`;
        if (entry.dataSignature !== dataSignature) {
          const data = coloredVolume(bars);
          entry.api.setData(data.map((point) => ({ ...point, time: point.time as UTCTimestamp })));
          entry.values = new Map(data.map((point) => [point.time, point.value]));
          entry.dataSignature = dataSignature;
        }
        const averageSignature = `${dataRevision}:Volume:${VOLUME_SMA_PERIOD}`;
        if (entry.averageSignature !== averageSignature) {
          const averages = volumeSma(bars);
          entry.averageApi.setData(
            averages.map((point) => ({ ...point, time: point.time as UTCTimestamp })),
          );
          entry.averageValues = new Map(averages.map((point) => [point.time, point.value]));
          entry.averageSignature = averageSignature;
        }
      }
    }
  }

  valueAt(id: string, time: number): number | null {
    const entry = this.series.get(id);
    return entry?.visible ? (entry.values.get(time) ?? null) : null;
  }

  volumeAverageAt(id: string, time: number): number | null {
    const entry = this.series.get(id);
    return entry?.kind === 'Histogram' && entry.visible
      ? (entry.averageValues.get(time) ?? null)
      : null;
  }

  refreshStrokeWidths(
    atr: number | null,
    referencePrice: number,
    priceToCoordinate: PriceToCoordinate,
  ) {
    for (const entry of this.series.values()) {
      if (entry.kind !== 'Line' || entry.widthMode !== 'atr') continue;
      const cssWidth = resolveStrokeWidth(
        'atr',
        entry.fallbackLineWidth,
        atr,
        referencePrice,
        priceToCoordinate,
      );
      const width = nativeLineWidth(cssWidth);
      if (width === entry.appliedLineWidth) continue;
      entry.api.applyOptions({ lineWidth: width });
      entry.appliedLineWidth = width;
    }
  }

  private removeEntry(entry: SeriesEntry) {
    this.chart.removeSeries(entry.api);
    if (entry.kind === 'Histogram') this.chart.removeSeries(entry.averageApi);
  }

  clear() {
    for (const entry of this.series.values()) this.removeEntry(entry);
    this.series.clear();
  }
}

import {
  createChart,
  createSeriesMarkers,
  type ISeriesMarkersPluginApi,
  type Time,
  type SeriesMarker,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
  type MouseEventParams,
} from 'lightweight-charts';
import { restoreViewRange, type ViewRange } from './ChartPreferences';
import { TimeMapper } from './TimeMapper';
import { ChartTransform } from './ChartTransform';
import { DrawingStateMachine } from '../drawing/DrawingStateMachine';
import { DrawingPrimitive } from '../drawing/DrawingPrimitive';
import { DrawingController } from '../drawing/DrawingController';
import { IndicatorEngine } from '../indicators/IndicatorEngine';
import {
  drawingVisible,
  type ToolKind,
  type DrawingStyle,
  type ActiveTool,
  type Drawing,
} from '../drawing/DrawingModel';
import type { IndicatorInstance } from '../indicators/IndicatorRegistry';
import type { Bar, BarResult, Timeframe } from '../market-data/MarketDataProvider';
import type { StrategyResult } from '../strategy';
import type { FilingEvent } from '../events/FilingEvents';
import { coloredVolume, volumeSma } from '../indicators/Volume';
import { averageTrueRange } from '../indicators/AverageTrueRange';
import { getMarketProfile } from '../market-data/MarketProfile';
import { marketTimeOptions } from './MarketTimeLabels';
import { COMPACT_AXIS_FONT_FAMILY, COMPACT_AXIS_FONT_SIZE, compactAxisPrice } from './AxisAppearance';
const compactVolume = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 2,
});
export interface ChartCallbacks {
  defaults?: (type: ToolKind) => Partial<DrawingStyle>;
  commit: (symbol: string, drawings: Drawing[], label: string, timeframe: Timeframe) => void;
  selection: (id: string | null) => void;
  tool: (t: ActiveTool) => void;
  view: (symbol: string, timeframe: Timeframe, range: ViewRange) => void;
}
export class ChartEngine {
  readonly chart: IChartApi;
  readonly candles: ISeriesApi<'Candlestick'>;
  readonly volume: ISeriesApi<'Histogram'>;
  readonly volumeAverage: ISeriesApi<'Line'>;
  readonly indicators: IndicatorEngine;
  private markers: ISeriesMarkersPluginApi<Time>;
  private researchMarkers: SeriesMarker<Time>[] = [];
  private eventMarkers: SeriesMarker<Time>[] = [];
  private corporateMarkers: SeriesMarker<Time>[] = [];
  controller: DrawingController | null = null;
  mapper: TimeMapper | null = null;
  private symbol = '';
  private timeframe: Timeframe = '1D';
  private bars: Bar[] = [];
  private atr: number | null = null;
  private revision = 0;
  private barIndices = new Map<number, number>();
  private allDrawings: Drawing[] = [];
  private ohlcRaf = 0;
  private suppressCrosshairReadout = false;
  private ohlcBar: Bar | null = null;
  private legacyVolumeValues = new Map<number, number>();
  private legacyVolumeAverageValues = new Map<number, number>();
  private legacyVolumeVisible = false;
  private indicatorInstances: readonly IndicatorInstance[] = [];
  private activePointers = new Set<number>();
  private viewTimer: ReturnType<typeof setTimeout> | null = null;
  private header: HTMLElement;
  private sourceLabel: HTMLElement;
  private legend: HTMLElement | null;
  constructor(
    readonly host: HTMLElement,
    header: HTMLElement,
    sourceLabel: HTMLElement,
    private callbacks: ChartCallbacks,
    legend?: HTMLElement,
  ) {
    this.header = header;
    this.sourceLabel = sourceLabel;
    this.legend = legend ?? null;
    this.chart = createChart(host, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#0e1521' },
        textColor: '#8290a5',
        fontFamily: COMPACT_AXIS_FONT_FAMILY,
        fontSize: COMPACT_AXIS_FONT_SIZE,
        attributionLogo: true,
      },
      grid: { vertLines: { color: '#1a2332' }, horzLines: { color: '#1a2332' } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#73849b', width: 1, labelBackgroundColor: '#31415a' },
        horzLine: { color: '#73849b', width: 1, labelBackgroundColor: '#31415a' },
      },
      rightPriceScale: {
        borderColor: '#243043',
        minimumWidth: 0,
        scaleMargins: { top: 0.05, bottom: 0.22 },
      },
      timeScale: {
        borderColor: '#243043',
        rightOffset: 40,
        barSpacing: 6,
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: true,
      },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      localization: { locale: 'en-US', priceFormatter: compactAxisPrice },
    });
    this.candles = this.chart.addSeries(CandlestickSeries, {
      upColor: '#39baa0',
      downColor: '#ef6b7b',
      borderVisible: false,
      wickUpColor: '#39baa0',
      wickDownColor: '#ef6b7b',
      priceLineColor: '#39baa0',
    });
    this.markers = createSeriesMarkers(this.candles, [], { autoScale: false });
    this.volume = this.chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      priceLineVisible: false,
      lastValueVisible: false,
    });
    this.volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    this.volumeAverage = this.chart.addSeries(LineSeries, {
      color: '#f0b35b',
      lineWidth: 2,
      priceScaleId: 'volume',
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      visible: false,
      title: '',
    });
    this.indicators = new IndicatorEngine(this.chart);
    this.chart.subscribeCrosshairMove(this.crosshair);
    host.addEventListener('pointerup', this.scheduleView);
    host.addEventListener('pointerdown', this.pointerDown);
    host.addEventListener('pointermove', this.pointerMove, { passive: true });
    host.addEventListener('pointerup', this.pointerEnd);
    host.addEventListener('pointercancel', this.pointerEnd);
    host.addEventListener('lostpointercapture', this.pointerEnd);
    host.addEventListener('wheel', this.scheduleView, { passive: true });
    host.addEventListener('wheel', this.scaleInput, { passive: true });
    window.addEventListener('pointerup', this.pointerEnd, { passive: true });
    window.addEventListener('pointercancel', this.pointerEnd, { passive: true });
    window.addEventListener('blur', this.clearActivePointers);
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(this.logicalRangeChanged);
    this.chart.timeScale().subscribeSizeChange(this.scaleSizeChanged);
  }
  clear() {
    this.flushView();
    this.cancelOhlcFrame();
    this.activePointers.clear();
    this.ohlcBar = null;
    this.bars = [];
    this.atr = null;
    this.barIndices.clear();
    this.indicatorInstances = [];
    this.allDrawings = [];
    this.legacyVolumeValues.clear();
    this.legacyVolumeAverageValues.clear();
    this.legacyVolumeVisible = false;
    this.detachController();
    this.indicators.clear();
    this.suppressCrosshairReadout = true;
    this.chart.clearCrosshairPosition();
    this.suppressCrosshairReadout = false;
    this.researchMarkers = [];
    this.eventMarkers = [];
    this.corporateMarkers = [];
    this.markers.setMarkers([]);
    this.candles.setData([]);
    this.volume.setData([]);
    this.volumeAverage.setData([]);
    this.mapper = null;
    this.renderHeader();
    this.clearLegendReadouts();
    this.header.textContent = '載入行情…';
  }
  load(
    symbol: string,
    timeframe: Timeframe,
    result: BarResult,
    drawings: Drawing[],
    indicators: IndicatorInstance[],
    range?: ViewRange,
    legacyVolume = true,
  ) {
    this.cancelOhlcFrame();
    this.activePointers.clear();
    this.ohlcBar = null;
    this.bars = [];
    this.atr = null;
    this.barIndices.clear();
    this.legacyVolumeValues.clear();
    this.legacyVolumeAverageValues.clear();
    this.legacyVolumeVisible = false;
    this.clearLegendReadouts();
    this.detachController();
    this.indicators.clear();
    this.researchMarkers = [];
    this.eventMarkers = [];
    this.corporateMarkers = [];
    this.markers.setMarkers([]);
    this.suppressCrosshairReadout = true;
    this.chart.clearCrosshairPosition();
    this.suppressCrosshairReadout = false;
    this.symbol = symbol;
    this.timeframe = timeframe;
    this.bars = result.bars;
    this.atr = averageTrueRange(result.bars);
    this.barIndices = new Map(result.bars.map((b, i) => [b.time, i]));
    this.indicatorInstances = indicators;
    this.allDrawings = drawings;
    this.revision++;
    const market = result.market ?? getMarketProfile(symbol);
    this.mapper = new TimeMapper(result.bars, timeframe, 500, market);
    const transform = new ChartTransform(this.chart, this.candles, this.mapper, this.atr);
    this.candles.setData([
      ...result.bars.map((b) => ({ ...b, time: b.time as UTCTimestamp })),
      ...this.mapper.futureWhitespace().map((b) => ({ time: b.time as UTCTimestamp })),
    ]);
    const volumeData = coloredVolume(result.bars),
      volumeAverageData = volumeSma(result.bars);
    this.volume.setData(
      volumeData.map((point) => ({ ...point, time: point.time as UTCTimestamp })),
    );
    this.volumeAverage.setData(
      volumeAverageData.map((point) => ({ ...point, time: point.time as UTCTimestamp })),
    );
    this.legacyVolumeValues = new Map(volumeData.map((point) => [point.time, point.value]));
    this.legacyVolumeAverageValues = new Map(
      volumeAverageData.map((point) => [point.time, point.value]),
    );
    this.chart.applyOptions({
      ...marketTimeOptions(market, timeframe),
    });
    const visible = drawings.filter((d) => drawingVisible(d, symbol, timeframe));
    const machine = new DrawingStateMachine(
      symbol,
      this.mapper,
      transform,
      visible,
      (next, label) => {
        // Preserve any future timeframe-scoped objects not visible in this view.
        const hidden = this.allDrawings.filter((d) => !drawingVisible(d, symbol, timeframe));
        this.callbacks.commit(symbol, [...hidden, ...next], label, timeframe);
      },
      this.callbacks.selection,
      this.callbacks.defaults,
    );
    const primitive = new DrawingPrimitive(() => machine.scene(), transform);
    this.candles.attachPrimitive(primitive);
    this.controller = new DrawingController(
      this.host,
      this.chart,
      machine,
      primitive,
      this.callbacks.tool,
    );
    this.indicators.sync(indicators, result.bars, timeframe, this.revision);
    this.syncLegacyVolumeVisibility(indicators, legacyVolume);
    this.chart.timeScale().setVisibleLogicalRange(restoreViewRange(range, result.bars.length));
    this.ohlcBar = result.bars.at(-1) ?? null;
    this.renderHeader();
    this.scheduleVisualFrame();
    this.sourceLabel.textContent = `${result.source} · ${market.currency} · ${market.timezone} · ${result.session.toUpperCase()} · ${result.dataState ?? (result.delayed ? 'DELAYED' : 'SIMULATED')} · ${result.cacheStatus ?? 'fresh'} · ${result.priceBasis ?? 'unknown price basis'} · as-of ${result.asOf ? new Date(result.asOf * 1000).toLocaleString() : 'N/A'} · last bar ${new Date(result.bars.at(-1)!.time * 1000).toLocaleString()}`;
    if (result.sessionCloseObservations?.length) this.sourceLabel.textContent += ` · ${result.sessionCloseObservations.length} source-reported auction-close observations (instant samples)`;
    this.sourceLabel.title = this.sourceLabel.textContent;
  }
  sync(
    drawings: Drawing[],
    indicators: IndicatorInstance[],
    magnet: boolean,
    tool: ActiveTool,
    legacyVolume = true,
  ) {
    if (!this.controller) return;
    this.allDrawings = drawings;
    this.indicatorInstances = indicators;
    const m = this.controller.machine;
    m.drawings = drawings.filter((d) => drawingVisible(d, this.symbol, this.timeframe));
    m.magnetOn = magnet;
    if (m.tool !== tool) this.controller.setTool(tool);
    if (m.selectedId && !m.drawings.some((d) => d.id === m.selectedId)) {
      m.select(null);
    }
    this.indicators.sync(indicators, this.bars, this.timeframe, this.revision);
    this.syncLegacyVolumeVisibility(indicators, legacyVolume);
    this.controller.refresh();
    this.renderHeader();
  }
  private syncLegacyVolumeVisibility(instances: readonly IndicatorInstance[], enabled: boolean) {
    const hasExplicitVolume = instances.some((i) => i.type === 'Volume' && i.scope.timeframe === this.timeframe);
    this.legacyVolumeVisible = enabled && !hasExplicitVolume;
    this.volume.applyOptions({ visible: this.legacyVolumeVisible });
    this.volumeAverage.applyOptions({ visible: this.legacyVolumeVisible });
  }
  setStrategy(result: StrategyResult | null) {
    this.researchMarkers =
      result?.config.symbol === this.symbol && result.config.timeframe === this.timeframe
        ? result.markers
            .filter((m) => this.barIndices.has(m.time))
            .map((m) => ({
              time: m.time as UTCTimestamp,
              position:
                m.action === 'BUY' || m.action === 'COVER'
                  ? ('belowBar' as const)
                  : ('aboveBar' as const),
              color: m.action === 'BUY' || m.action === 'COVER' ? '#66dbbb' : '#f09baa',
              shape:
                m.action === 'BUY' || m.action === 'COVER'
                  ? ('arrowUp' as const)
                  : ('arrowDown' as const),
              text: m.action,
              id: `research-${m.time}-${m.action}`,
            }))
        : [];
    this.refreshMarkers();
  }
  private eventBar(time: number): number | null {
    if (this.timeframe === '1M') {
      const eventMonth = new Date(time * 1000);
      const key = eventMonth.getUTCFullYear() * 12 + eventMonth.getUTCMonth();
      const bar = this.bars.find(b => {
        const date = new Date(b.time * 1000);
        return date.getUTCFullYear() * 12 + date.getUTCMonth() === key;
      });
      return bar?.time ?? null;
    }
    if (
      !this.bars.length ||
      time < this.bars[0].time - 86400 ||
      time > this.bars.at(-1)!.time + 86400
    )
      return null;
    const day = Math.floor(time / 86400);
    let lo = 0,
      hi = this.bars.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (Math.floor(this.bars[mid].time / 86400) < day) lo = mid + 1;
      else hi = mid;
    }
    return this.bars[lo]?.time ?? null;
  }
  setFilings(events: readonly FilingEvent[]) {
    this.eventMarkers = events.flatMap((e) => {
      const time = this.eventBar(e.time);
      return time === null
        ? []
        : [
            {
              time: time as UTCTimestamp,
              position: 'aboveBar' as const,
              color: '#bca5e0',
              shape: 'circle' as const,
              text: `SEC ${e.form}`,
              id: e.accession,
            },
          ];
    });
    this.refreshMarkers();
  }
  setCorporateEvents(
    events: readonly { time: number; type: 'dividend' | 'split'; value: number }[],
  ) {
    this.corporateMarkers = events.flatMap((event) => {
      const time = this.eventBar(event.time);
      return time === null
        ? []
        : [
            {
              time: time as UTCTimestamp,
              position: 'belowBar' as const,
              color: '#e4c987',
              shape: 'square' as const,
              text: event.type === 'split' ? `Split ×${event.value}` : `Dividend ${event.value}`,
              id: `corporate-${event.type}-${event.time}`,
            },
          ];
    });
    this.refreshMarkers();
  }
  private refreshMarkers() {
    this.markers.setMarkers(
      [...this.eventMarkers, ...this.corporateMarkers, ...this.researchMarkers].sort(
        (a, b) => Number(a.time) - Number(b.time),
      ),
    );
  }
  setSelection(id: string | null) {
    this.controller?.machine.select(id);
    this.controller?.refresh();
  }
  resetView() {
    const n = this.bars.length - 1;
    this.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 150), to: n + 40 });
    this.scheduleView();
  }
  futureArea() {
    const n = this.bars.length - 1;
    this.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 80), to: n + 90 });
    this.scheduleView();
  }
  zoom(factor: number) {
    const r = this.chart.timeScale().getVisibleLogicalRange();
    if (r) {
      const center = (r.from + r.to) / 2,
        half = ((r.to - r.from) / 2) * factor;
      this.chart.timeScale().setVisibleLogicalRange({ from: center - half, to: center + half });
      this.scheduleView();
    }
  }
  private crosshair = (param: MouseEventParams) => {
    if (this.suppressCrosshairReadout || !this.bars.length) return;
    const data = param.seriesData.get(this.candles);
    this.ohlcBar = data && 'open' in data ? (data as unknown as Bar) : (this.bars.at(-1) ?? null);
    this.scheduleVisualFrame();
  };
  private scheduleVisualFrame() {
    if (this.ohlcRaf) return;
    this.ohlcRaf = requestAnimationFrame(() => {
      this.ohlcRaf = 0;
      this.renderHeader();
    });
  }
  private refreshStrokeWidths() {
    this.indicators.refreshStrokeWidths(
      this.atr,
      this.bars.at(-1)?.close ?? Number.NaN,
      (price) => this.candles.priceToCoordinate(price),
    );
  }
  private hasAtrWidths() {
    const hasIndicator = this.indicatorInstances.some(
      (instance) =>
        instance.type !== 'Volume' &&
        instance.widthMode === 'atr' &&
        (!instance.scope.timeframe || instance.scope.timeframe === this.timeframe),
    );
    const drawings = this.controller?.machine.drawings ??
      this.allDrawings.filter((drawing) => drawingVisible(drawing, this.symbol, this.timeframe));
    return hasIndicator || drawings.some((drawing) => drawing.visible && drawing.style.widthMode === 'atr');
  }
  private scheduleStrokeRefresh() {
    if (this.hasAtrWidths()) this.scheduleVisualFrame();
  }
  private pointerDown = (event: PointerEvent) => {
    this.activePointers.add(event.pointerId);
  };
  private pointerMove = (event: PointerEvent) => {
    if (event.buttons !== 0 || this.activePointers.has(event.pointerId)) this.scheduleStrokeRefresh();
  };
  private pointerEnd = (event: PointerEvent) => {
    if (this.activePointers.delete(event.pointerId)) this.scheduleStrokeRefresh();
  };
  private clearActivePointers = () => {
    if (!this.activePointers.size) return;
    this.activePointers.clear();
    this.scheduleStrokeRefresh();
  };
  private scaleInput = () => this.scheduleStrokeRefresh();
  private logicalRangeChanged = () => this.scheduleStrokeRefresh();
  private scaleSizeChanged = () => this.scheduleStrokeRefresh();
  private renderHeader() {
    this.refreshStrokeWidths();
    const b = this.ohlcBar;
    if (!b) {
      this.header.textContent = '';
      this.renderLegend();
      return;
    }
    const index = this.barIndices.get(b.time) ?? -1,
      previous = index > 0 ? this.bars[index - 1].close : b.open,
      change = b.close - previous;
    this.header.textContent = `O ${b.open.toFixed(2)}   H ${b.high.toFixed(2)}   L ${b.low.toFixed(2)}   C ${b.close.toFixed(2)}   ${change >= 0 ? '+' : ''}${change.toFixed(2)} (${((change / previous) * 100).toFixed(2)}%)`;
    this.header.style.color = change >= 0 ? '#59cfb7' : '#ef8692';
    this.renderLegend();
  }
  private renderLegend() {
    if (!this.legend) return;
    const bar = this.ohlcBar ?? this.bars.at(-1) ?? null;
    const setValue = (
      target: Element | null,
      value: number | null,
      format: 'number' | 'volume',
      hidden = false,
    ) => {
      if (!target) return;
      target.textContent = hidden
        ? 'Hidden'
        : value === null
          ? 'N/A'
          : format === 'volume'
            ? compactVolume.format(value)
            : value.toFixed(2);
    };
    const rows = this.legend.querySelectorAll<HTMLElement>('[data-indicator-id]');
    for (const row of rows) {
      const id = row.dataset.indicatorId,
        instance = this.indicatorInstances.find((item) => item.id === id),
        inScope =
          !!instance && (!instance.scope.timeframe || instance.scope.timeframe === this.timeframe),
        hidden = !!instance && (!instance.visible || !inScope),
        value = id && bar && instance && inScope ? this.indicators.valueAt(id, bar.time) : null;
      setValue(
        row.querySelector('[data-indicator-value]'),
        bar ? value : null,
        instance?.type === 'Volume' ? 'volume' : 'number',
        hidden,
      );
      const averageTarget = row.querySelector('[data-volume-average]');
      if (averageTarget) {
        const average =
          id && bar && instance?.type === 'Volume' && inScope
            ? this.indicators.volumeAverageAt(id, bar.time)
            : null;
        setValue(averageTarget, bar ? average : null, 'volume', hidden);
      }
    }
    const legacyRow = this.legend.querySelector('[data-legacy-volume]'),
      legacyValue = bar ? (this.legacyVolumeValues.get(bar.time) ?? null) : null,
      legacyAverage = bar ? (this.legacyVolumeAverageValues.get(bar.time) ?? null) : null;
    setValue(
      legacyRow?.querySelector('[data-volume-value]') ?? null,
      legacyValue,
      'volume',
      !this.legacyVolumeVisible,
    );
    setValue(
      legacyRow?.querySelector('[data-volume-average]') ?? null,
      legacyAverage,
      'volume',
      !this.legacyVolumeVisible,
    );
  }
  private clearLegendReadouts() {
    this.legend
      ?.querySelectorAll('[data-indicator-value], [data-volume-average], [data-volume-value]')
      .forEach((target) => {
        target.textContent = 'N/A';
      });
  }
  private cancelOhlcFrame() {
    if (this.ohlcRaf) cancelAnimationFrame(this.ohlcRaf);
    this.ohlcRaf = 0;
  }
  private scheduleView = () => {
    if (this.viewTimer) clearTimeout(this.viewTimer);
    this.viewTimer = setTimeout(() => {
      this.viewTimer = null;
      this.saveView();
    }, 350);
  };
  private saveView() {
    if (this.bars.length) {
      const r = this.chart.timeScale().getVisibleLogicalRange();
      if (r)
        this.callbacks.view(this.symbol, this.timeframe, {
          from: Number(r.from),
          to: Number(r.to),
          barCount: this.bars.length,
        });
    }
  }
  flushView() {
    if (this.viewTimer) {
      clearTimeout(this.viewTimer);
      this.viewTimer = null;
      this.saveView();
    }
  }
  private detachController() {
    if (this.controller) {
      this.controller.destroy();
      this.candles.detachPrimitive(this.controller.primitive);
      this.controller = null;
    }
  }
  destroy() {
    this.flushView();
    this.detachController();
    this.cancelOhlcFrame();
    this.activePointers.clear();
    this.ohlcBar = null;
    this.barIndices.clear();
    this.bars = [];
    this.atr = null;
    this.indicatorInstances = [];
    this.legacyVolumeValues.clear();
    this.legacyVolumeAverageValues.clear();
    this.legacyVolumeVisible = false;
    this.indicators.clear();
    this.volume.setData([]);
    this.volumeAverage.setData([]);
    this.renderHeader();
    this.clearLegendReadouts();
    this.legend = null;
    this.host.removeEventListener('pointerup', this.scheduleView);
    this.host.removeEventListener('pointerdown', this.pointerDown);
    this.host.removeEventListener('pointermove', this.pointerMove);
    this.host.removeEventListener('pointerup', this.pointerEnd);
    this.host.removeEventListener('pointercancel', this.pointerEnd);
    this.host.removeEventListener('lostpointercapture', this.pointerEnd);
    this.host.removeEventListener('wheel', this.scheduleView);
    this.host.removeEventListener('wheel', this.scaleInput);
    window.removeEventListener('pointerup', this.pointerEnd);
    window.removeEventListener('pointercancel', this.pointerEnd);
    window.removeEventListener('blur', this.clearActivePointers);
    this.activePointers.clear();
    this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this.logicalRangeChanged);
    this.chart.timeScale().unsubscribeSizeChange(this.scaleSizeChanged);
    this.chart.unsubscribeCrosshairMove(this.crosshair);
    this.chart.remove();
  }
}

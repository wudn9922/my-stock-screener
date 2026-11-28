import { useEffect, useRef } from 'react';
import './timeChart.css';
import { TimeBands, type TimeBand } from './TimeBands';
import {
  BaselineSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  PriceScaleMode,
  createChart,
  createSeriesMarkers,
  type LogicalRange,
  type SeriesMarker,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type SeriesType,
  type Time,
} from 'lightweight-charts';

/**
 * Categorical slots validated for the dark chart surface (same order as the world overlay chart;
 * duplicated here so the lazily loaded rates view does not import the world page module).
 */
export const RATES_COLORS = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#9085e9'] as const;
/** Warning hue for inverted spreads (a state, not a price direction, so it ignores 紅漲綠跌). */
export const WARN_COLOR = '#e0a43a';

export interface ChartLine {
  id: string;
  color: string;
  /** `baseline`: filled above / below zero (spreads); the area below zero uses WARN_COLOR. */
  kind?: 'line' | 'baseline';
  width?: 1 | 2 | 3;
  dashed?: boolean;
  scale?: 'left' | 'right';
  markers?: readonly SeriesMarker<Time>[];
  /** Ascending YYYY-MM-DD dates; null values leave a gap. */
  points: { time: string; value: number | null }[];
}

export interface ChartScale {
  format?: (value: number) => string;
  logarithmic?: boolean;
  range?: { min: number; max: number };
}

/** `2024-01-05`, a business-day object or a UTC timestamp → `2024-01-05`. */
export function timeKey(time: Time | undefined): string | null {
  if (time === undefined) return null;
  if (typeof time === 'string') return time;
  if (typeof time === 'number') return new Date(time * 1000).toISOString().slice(0, 10);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${time.year}-${pad(time.month)}-${pad(time.day)}`;
}

/** A small lightweight-charts time-series chart in the site's dark style. */
export function TimeChart({
  lines,
  format,
  ariaLabel,
  zeroLine = false,
  onHover,
  className = 'rates-time-chart',
  zoom = false,
  bands = [],
  leftScale,
  rightScale,
}: {
  lines: readonly ChartLine[];
  format: (value: number) => string;
  ariaLabel: string;
  zeroLine?: boolean;
  onHover?: (values: Record<string, number> | null, time: string | null) => void;
  className?: string;
  zoom?: boolean;
  bands?: readonly TimeBand[];
  leftScale?: ChartScale;
  rightScale?: ChartScale;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef(new Map<string, ISeriesApi<SeriesType>>());
  const countRef = useRef<HTMLOutputElement>(null);
  const hoverOutput = useRef<HTMLOutputElement>(null);
  const pointsRef = useRef(0);
  const scaleRef = useRef({ leftScale, rightScale });
  scaleRef.current = { leftScale, rightScale };
  const hoverRef = useRef(onHover);
  hoverRef.current = onHover;
  const formatRef = useRef(format);
  formatRef.current = format;
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const chart = createChart(host, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#8a93a3',
        fontSize: 11,
        fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: '#1b212b' } },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { color: '#6b7587', labelBackgroundColor: '#2b3340' },
        horzLine: { color: '#6b7587', labelBackgroundColor: '#2b3340' },
      },
      leftPriceScale: { visible: false, borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.08 } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.08 } },
      // A low minimum spacing lets fitContent show 20+ years of weekly points on a phone.
      timeScale: { borderVisible: false, rightOffset: 2, minBarSpacing: 0.05 },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: zoom, pinch: true, axisPressedMouseMove: zoom },
      localization: { locale: 'zh-TW', priceFormatter: (value: number) => formatRef.current(value) },
    });
    chartRef.current = chart;
    const series = seriesRef.current;
    const move = (param: MouseEventParams) => {
      if (!param.time || !param.point) {
        hoverRef.current?.(null, null);
        if (hoverOutput.current) hoverOutput.current.textContent = '';
        return;
      }
      const values: Record<string, number> = {};
      for (const [id, line] of series) {
        const point = param.seriesData.get(line) as { value?: number } | undefined;
        if (point && typeof point.value === 'number') values[id] = point.value;
      }
      hoverRef.current?.(values, timeKey(param.time));
      if (hoverOutput.current) hoverOutput.current.textContent = [timeKey(param.time), ...Object.entries(values).map(([id, value]) => {
        const api = series.get(id);
        const side = api?.options().priceScaleId === 'left' ? 'left' : 'right';
        const formatter = (side === 'left' ? scaleRef.current.leftScale : scaleRef.current.rightScale)?.format ?? formatRef.current;
        return id + ' ' + formatter(value);
      })].join(' · ');
    };
    chart.subscribeCrosshairMove(move);
    const updateRange = (range: LogicalRange | null) => {
      if (countRef.current && range) countRef.current.textContent = String(Math.min(pointsRef.current, Math.max(0, Math.floor(range.to) - Math.ceil(range.from) + 1)));
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(updateRange);
    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(updateRange);
      chart.unsubscribeCrosshairMove(move);
      chart.remove();
      chartRef.current = null;
      series.clear();
    };
  }, [zoom]);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const series = seriesRef.current;
    for (const line of series.values()) chart.removeSeries(line);
    series.clear();
    chart.applyOptions({
      leftPriceScale: { visible: lines.some(line => line.scale === 'left'), mode: leftScale?.logarithmic ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal },
      rightPriceScale: { visible: lines.some(line => line.scale !== 'left'), mode: rightScale?.logarithmic ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal },
    });
    pointsRef.current = new Set(lines.flatMap(line => line.points.map(point => point.time))).size;
    for (const line of lines) {
      const scale = line.scale === 'left' ? leftScale : rightScale;
      const formatter = scale?.format;
      const range = scale?.range;
      const common = {
        lineWidth: line.width ?? 2,
        lineStyle: line.dashed ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerRadius: 4,
        title: '',
        ...(line.scale ? { priceScaleId: line.scale } : {}),
        ...(formatter ? { priceFormat: { type: 'custom' as const, formatter, minMove: .01 } } : {}),
        ...(range ? { autoscaleInfoProvider: () => ({ priceRange: { minValue: range.min, maxValue: range.max } }) } : {}),
      } as const;
      const api: ISeriesApi<SeriesType> =
        line.kind === 'baseline'
          ? chart.addSeries(BaselineSeries, {
              ...common,
              baseValue: { type: 'price', price: 0 },
              topLineColor: line.color,
              topFillColor1: `${line.color}38`,
              topFillColor2: `${line.color}08`,
              bottomLineColor: WARN_COLOR,
              bottomFillColor1: `${WARN_COLOR}08`,
              bottomFillColor2: `${WARN_COLOR}55`,
            })
          : chart.addSeries(LineSeries, { ...common, color: line.color });
      api.setData(line.points.map((point) => (point.value === null ? { time: point.time } : { time: point.time, value: point.value })));
      if (line.markers?.length) createSeriesMarkers(api, [...line.markers]);
      series.set(line.id, api);
    }
    if (zeroLine) {
      const first = series.values().next().value;
      first?.createPriceLine({ price: 0, color: '#4a5363', lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: '' });
    }
    chart.timeScale().fitContent();
    if (countRef.current) countRef.current.textContent = String(pointsRef.current);
  }, [lines, zeroLine, leftScale, rightScale, zoom]);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !bands.length) return;
    const pane = chart.panes()[0];
    if (!pane) return;
    const overlay = new TimeBands(bands);
    pane.attachPrimitive(overlay);
    return () => { if (chartRef.current === chart) pane.detachPrimitive(overlay); };
  }, [bands, zoom]);
  const changeZoom = (factor: number) => {
    const scale = chartRef.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    const n = pointsRef.current;
    if (!scale || !range || !n) return;
    const count = Math.min(n, Math.max(Math.min(5, n), Math.round((range.to - range.from + 1) * factor)));
    const from = Math.max(0, Math.min(n - count, (range.from + range.to + 1 - count) / 2));
    scale.setVisibleLogicalRange({ from, to: from + count - 1 });
  };
  const plot = <div ref={hostRef} className={className} role="img" aria-label={ariaLabel}
    data-chart-series={lines.map(line => line.id + ':' + (line.scale ?? 'right')).join('|')}
    data-chart-bands={bands.length} />;
  if (!zoom) return plot;
  return <div className="time-chart-interactive">
    <div className="time-chart-controls" role="group" aria-label={ariaLabel + ' 縮放'}>
      <button type="button" aria-label="縮小圖表" onClick={() => changeZoom(1.5)}>−</button>
      <button type="button" aria-label="放大圖表" onClick={() => changeZoom(2 / 3)}>＋</button>
      <button type="button" onClick={() => chartRef.current?.timeScale().fitContent()}>重設</button>
      <output ref={countRef} aria-label="顯示資料點數">—</output><span>點</span>
    </div>
    {plot}
    <output ref={hoverOutput} className="time-chart-hover" aria-label="十字線數值" />
  </div>;
}

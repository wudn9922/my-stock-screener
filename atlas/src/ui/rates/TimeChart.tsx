import { useEffect, useRef } from 'react';
import {
  BaselineSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
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
  /** Ascending YYYY-MM-DD dates; null values leave a gap. */
  points: { time: string; value: number | null }[];
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
}: {
  lines: readonly ChartLine[];
  format: (value: number) => string;
  ariaLabel: string;
  zeroLine?: boolean;
  onHover?: (values: Record<string, number> | null, time: string | null) => void;
  className?: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef(new Map<string, ISeriesApi<SeriesType>>());
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
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.08 } },
      // A low minimum spacing lets fitContent show 20+ years of weekly points on a phone.
      timeScale: { borderVisible: false, rightOffset: 2, minBarSpacing: 0.05 },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: false },
      localization: { locale: 'zh-TW', priceFormatter: (value: number) => formatRef.current(value) },
    });
    chartRef.current = chart;
    const series = seriesRef.current;
    const move = (param: MouseEventParams) => {
      if (!param.time || !param.point) {
        hoverRef.current?.(null, null);
        return;
      }
      const values: Record<string, number> = {};
      for (const [id, line] of series) {
        const point = param.seriesData.get(line) as { value?: number } | undefined;
        if (point && typeof point.value === 'number') values[id] = point.value;
      }
      hoverRef.current?.(values, timeKey(param.time));
    };
    chart.subscribeCrosshairMove(move);
    return () => {
      chart.unsubscribeCrosshairMove(move);
      chart.remove();
      chartRef.current = null;
      series.clear();
    };
  }, []);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const series = seriesRef.current;
    for (const line of series.values()) chart.removeSeries(line);
    series.clear();
    for (const line of lines) {
      const common = {
        lineWidth: line.width ?? 2,
        lineStyle: line.dashed ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerRadius: 4,
        title: '',
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
      series.set(line.id, api);
    }
    if (zeroLine) {
      const first = series.values().next().value;
      first?.createPriceLine({ price: 0, color: '#4a5363', lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: '' });
    }
    chart.timeScale().fitContent();
  }, [lines, zeroLine]);
  return <div ref={hostRef} className={className} role="img" aria-label={ariaLabel} />;
}

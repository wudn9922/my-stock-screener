import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type LogicalRange,
  type UTCTimestamp,
} from 'lightweight-charts';
import { getDailyBars } from '../app/dataSources';
import { sitePreferences, upDownColors } from '../app/sitePreferences';
import type { Bar } from '../market-data/MarketDataProvider';
import { sma } from '../indicators/MovingAverage';
import { indicatorColors } from '../indicators/IndicatorRegistry';
import './mini-chart-controls.css';

const VISIBLE_BARS = 130;
const NO_MAS: readonly number[] = [];

/** Starts work only once the element is near the viewport (long market pages stay cheap). */
export function useNearViewport<T extends Element>(margin = '200px') {
  const ref = useRef<T>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element || near) return;
    if (typeof IntersectionObserver === 'undefined') {
      setNear(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setNear(true);
      },
      { rootMargin: margin },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [margin, near]);
  return { ref, near };
}

export type MiniChartStatus = 'idle' | 'loading' | 'ready' | 'error';

/**
 * Stock previews have no preset indicators. Market previews opt into their
 * explicit configured MAs, volume and public time-scale controls.
 */
export function MiniChart({
  symbol,
  height = 150,
  onStatus,
  maPeriods = NO_MAS,
  interactive = false,
  showVolume = false,
  wheelZoom = true,
}: {
  symbol: string;
  height?: number;
  onStatus?: (status: MiniChartStatus, bars: Bar[] | null) => void;
  maPeriods?: readonly number[];
  interactive?: boolean;
  showVolume?: boolean;
  /** Long review lists let the mouse wheel scroll the page; buttons/pinch still zoom. */
  wheelZoom?: boolean;
}) {
  const { ref, near } = useNearViewport<HTMLDivElement>();
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const countRef = useRef<HTMLOutputElement>(null);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [status, setStatus] = useState<MiniChartStatus>('idle');
  const colors = useSyncExternalStore(sitePreferences.subscribe, () => sitePreferences.get().colors);
  const statusRef = useRef(onStatus);
  statusRef.current = onStatus;
  const maKey = [...new Set(maPeriods.filter(period => Number.isInteger(period) && period > 0))].join(',');
  useEffect(() => {
    if (!near) return;
    let live = true;
    setBars(null);
    setStatus('loading');
    statusRef.current?.('loading', null);
    getDailyBars(symbol)
      .then((result) => {
        if (!live) return;
        setBars(result);
        setStatus(result.length ? 'ready' : 'error');
        statusRef.current?.(result.length ? 'ready' : 'error', result);
      })
      .catch(() => {
        if (!live) return;
        setStatus('error');
        statusRef.current?.('error', null);
      });
    return () => {
      live = false;
    };
  }, [near, symbol]);
  useEffect(() => {
    const host = hostRef.current;
    if (!host || !bars?.length) return;
    const { up, down } = upDownColors(colors);
    const chart: IChartApi = createChart(host, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#7d8696',
        fontSize: 10,
        fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
        attributionLogo: interactive,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: '#1b212b' } },
      crosshair: { mode: interactive ? CrosshairMode.Normal : CrosshairMode.Hidden },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: showVolume ? 0.25 : 0.08 } },
      timeScale: { borderVisible: false, rightOffset: 2, fixLeftEdge: true, fixRightEdge: true },
      handleScroll: interactive ? { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false } : false,
      handleScale: interactive ? { mouseWheel: wheelZoom, pinch: true, axisPressedMouseMove: { time: true, price: false } } : false,
      localization: { locale: 'zh-TW' },
    });
    chartRef.current = chart;
    const visible = interactive ? bars : bars.slice(-VISIBLE_BARS);
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderVisible: false,
      wickUpColor: up,
      wickDownColor: down,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    candles.setData(
      visible.map((bar) => ({
        time: bar.time as UTCTimestamp,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    );
    maKey.split(',').filter(Boolean).map(Number).forEach((period, index) => {
      const average = chart.addSeries(LineSeries, {
        color: indicatorColors[index % indicatorColors.length],
        lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      // Calculate with the full source history, including warmup before the
      // initial viewport; a long configured MA must not start from 130 bars.
      average.setData(sma(bars, period).filter(point => point.time >= visible[0]!.time)
        .map(point => ({ ...point, time: point.time as UTCTimestamp })));
    });
    if (showVolume) {
      const volume = chart.addSeries(HistogramSeries, {
        priceScaleId: 'volume', priceFormat: { type: 'volume' },
        priceLineVisible: false, lastValueVisible: false,
      });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      volume.setData(visible.map(bar => ({ time: bar.time as UTCTimestamp, value: bar.volume,
        color: `${bar.close >= bar.open ? up : down}99` })));
    }
    const scale = chart.timeScale();
    let rangeFrame = 0;
    const showRange = (range: LogicalRange | null) => {
      cancelAnimationFrame(rangeFrame);
      rangeFrame = requestAnimationFrame(() => {
        // Native pan/pinch updates the readout without React or persistence on
        // pointer movement. The range remains owned by Lightweight Charts.
        if (countRef.current && range)
          countRef.current.value = String(Math.min(bars.length, Math.round(range.to - range.from + 1)));
      });
    };
    if (interactive) {
      scale.subscribeVisibleLogicalRangeChange(showRange);
      scale.setVisibleLogicalRange({ from: Math.max(0, bars.length - VISIBLE_BARS), to: bars.length - 1 });
    } else scale.fitContent();
    return () => {
      cancelAnimationFrame(rangeFrame);
      scale.unsubscribeVisibleLogicalRangeChange(showRange);
      chartRef.current = null;
      chart.remove();
    };
  }, [bars, colors, maKey, interactive, showVolume, wheelZoom]);
  const zoom = (factor: number) => {
    const scale = chartRef.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    if (!range || !scale || !bars?.length) return;
    const count = Math.min(bars.length, Math.max(20, Math.round((range.to - range.from + 1) * factor)));
    const from = Math.max(0, Math.min(bars.length - count, (range.from + range.to + 1 - count) / 2));
    scale.setVisibleLogicalRange({ from, to: from + count - 1 });
  };
  return (
    <div ref={ref} className={`mini-chart${interactive ? ' mini-chart-interactive' : ''}`} style={{ height }} data-status={status}>
      {interactive && <div className="mini-chart-controls" role="group" aria-label={`${symbol} 圖表縮放`}>
        <span>日 K{showVolume ? ' · 成交量' : ''}</span>
        <button type="button" aria-label="縮小圖表" title="縮小（顯示更多 K 線）" disabled={status !== 'ready'} onClick={() => zoom(1.5)}>−</button>
        <button type="button" aria-label="放大圖表" title="放大（顯示較少 K 線）" disabled={status !== 'ready'} onClick={() => zoom(2 / 3)}>＋</button>
        <button type="button" disabled={status !== 'ready'} onClick={() => {
          if (bars?.length) chartRef.current?.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - VISIBLE_BARS), to: bars.length - 1 });
        }}>重設</button>
        <output ref={countRef} aria-label="顯示 K 線根數">—</output><span>根</span>
      </div>}
      {status === 'ready' ? (
        <div ref={hostRef} className="mini-chart-canvas" aria-hidden="true" />
      ) : status === 'error' ? (
        <span className="mini-chart-empty">暫無圖表資料</span>
      ) : (
        <span className="skeleton mini-chart-skeleton" aria-hidden="true" />
      )}
    </div>
  );
}

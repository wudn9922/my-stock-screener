import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import { getDailyBars } from '../app/dataSources';
import { sitePreferences, upDownColors } from '../app/sitePreferences';
import type { Bar } from '../market-data/MarketDataProvider';

const VISIBLE_BARS = 130;

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
 * Compact daily candles without preset indicators. Page scrolling passes through;
 * the parent handles taps.
 */
export function MiniChart({
  symbol,
  height = 150,
  onStatus,
}: {
  symbol: string;
  height?: number;
  onStatus?: (status: MiniChartStatus, bars: Bar[] | null) => void;
}) {
  const { ref, near } = useNearViewport<HTMLDivElement>();
  const hostRef = useRef<HTMLDivElement>(null);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [status, setStatus] = useState<MiniChartStatus>('idle');
  const colors = useSyncExternalStore(sitePreferences.subscribe, () => sitePreferences.get().colors);
  const statusRef = useRef(onStatus);
  statusRef.current = onStatus;
  useEffect(() => {
    if (!near) return;
    let live = true;
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
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: '#1b212b' } },
      crosshair: { mode: CrosshairMode.Hidden },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.08 } },
      timeScale: { borderVisible: false, rightOffset: 2, fixLeftEdge: true, fixRightEdge: true },
      handleScroll: false,
      handleScale: false,
      localization: { locale: 'zh-TW' },
    });
    const visible = bars.slice(-VISIBLE_BARS);
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
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, colors]);
  return (
    <div ref={ref} className="mini-chart" style={{ height }} data-status={status}>
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

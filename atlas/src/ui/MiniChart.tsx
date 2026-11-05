import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type UTCTimestamp,
} from 'lightweight-charts';
import { getDailyBarResult, getLegacyDailyResult, getWatchlistDailyResult } from '../app/dataSources';
import { LIVE_INTERVAL_MS, liveQuotes, mergeDailyBars, sameBars, type LiveDaily } from '../market-data/LiveQuotes';
import { mergeArchiveDaily } from '../market-data/KlineArchive';
import { sitePreferences, upDownColors } from '../app/sitePreferences';
import type { Bar } from '../market-data/MarketDataProvider';
import { sma } from '../indicators/MovingAverage';
import { indicatorColors } from '../indicators/IndicatorRegistry';
import './mini-chart-controls.css';
import { miniChartStart } from './miniChartRange';

const VISIBLE_BARS = 130;
const NO_MAS: readonly number[] = [];

/** Starts work only once the element is near the viewport (long market pages stay cheap). */
export function useNearViewport<T extends Element>(margin = '200px') {
  const ref = useRef<T>(null);
  const [near, setNear] = useState(false);
  const [visible, setVisible] = useState(false);
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
  useEffect(() => {
    if (!ref.current) return;
    if (typeof IntersectionObserver === 'undefined') { setVisible(true); return; }
    const observer = new IntersectionObserver(entries => setVisible(entries.some(entry => entry.isIntersecting)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return { ref, near, visible };
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
  initialMonths,
  live = false,
}: {
  symbol: string;
  height?: number;
  onStatus?: (status: MiniChartStatus, bars: Bar[] | null) => void;
  maPeriods?: readonly number[];
  interactive?: boolean;
  showVolume?: boolean;
  /** Long review lists let the mouse wheel scroll the page; buttons/pinch still zoom. */
  wheelZoom?: boolean;
  initialMonths?: number;
  /** Keep the latest daily bar moving with the site's once-a-minute live refresh (market cards). */
  live?: boolean;
}) {
  const { ref, near, visible } = useNearViewport<HTMLDivElement>();
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const viewRef = useRef<{ symbol: string; range: LogicalRange | null } | null>(null);
  const countRef = useRef<HTMLOutputElement>(null);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const barsRef = useRef<Bar[] | null>(null);
  const archivedThrough = useRef<string | undefined>(undefined);
  const recentRef = useRef<LiveDaily | null>(null);
  const mounted = useRef(true), splitPending = useRef(false);
  const context = useRef('');
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [sourceNote, setSourceNote] = useState('');
  // Series drawn from `bars`; a live update repaints them in place so zoom and pan survive.
  const paintRef = useRef<((next: Bar[]) => void) | null>(null);
  const drawnRef = useRef<Bar[] | null>(null);
  const [status, setStatus] = useState<MiniChartStatus>('idle');
  const colors = useSyncExternalStore(sitePreferences.subscribe, () => sitePreferences.get().colors);
  const statusRef = useRef(onStatus);
  statusRef.current = onStatus;
  const maKey = [...new Set(maPeriods.filter(period => Number.isInteger(period) && period > 0))].join(',');
  context.current = `${symbol}:${maKey}:${interactive}:${initialMonths}`;
  const replaceSplitHistory = () => {
    archivedThrough.current = undefined;
    setSourceNote('封存價格差異超過 2%，整段改用 Yahoo');
    if (splitPending.current) return;
    splitPending.current = true;
    const requestedContext = context.current;
    void getLegacyDailyResult(symbol).then(result => {
      if (!mounted.current || context.current !== requestedContext) return;
      const all = mergeDailyBars(result.bars, barsRef.current?.slice(-30) ?? []);
      const next = Math.max(0, ...maPeriods) > 126 ? all : all.slice(-126);
      barsRef.current = next; setBars(next); statusRef.current?.('ready', next);
    }).catch(() => { if (mounted.current && context.current === requestedContext) setSourceNote('封存價格差異超過 2%，暫用較短的 Yahoo 範圍；較長歷史暫時無法取得'); });
  };
  useEffect(() => {
    if (!near) return;
    let live = true;
    recentRef.current = null; barsRef.current = null; archivedThrough.current = undefined;
    splitPending.current = false; setSourceNote('');
    setBars(null);
    setStatus('loading');
    statusRef.current?.('loading', null);
    // Long configured MAs need historical warm-up; the visible chart still starts at six months.
    (Math.max(0, ...maPeriods) > 126 || (interactive && initialMonths !== 6) ? getDailyBarResult(symbol) : getWatchlistDailyResult(symbol))
      .then((result) => {
        if (!live) return;
        archivedThrough.current = result.archive?.lastDate;
        const recent = recentRef.current;
        const merged = result.archive && recent ? mergeArchiveDaily(result.bars, recent.bars, symbol, result.archive.lastDate) : null;
        const next = merged?.bars ?? (recent ? mergeDailyBars(result.bars, recent.bars) : result.bars);
        barsRef.current = next; setBars(next);
        setSourceNote(result.source);
        if (merged?.splitMismatch) replaceSplitHistory();
        setStatus(next.length ? 'ready' : 'error');
        statusRef.current?.(next.length ? 'ready' : 'error', next);
      })
      .catch(() => {
        if (!live) return;
        setStatus('error');
        statusRef.current?.('error', null);
      });
    return () => {
      live = false;
    };
  }, [near, symbol, maKey, interactive, initialMonths]);
  useEffect(() => {
    if (!visible) return;
    return liveQuotes().subscribe(symbol, value => {
      recentRef.current = value;
      const current = barsRef.current;
      if (!current?.length) return; // historical load owns initial/error state
      const merged = archivedThrough.current ? mergeArchiveDaily(current, value.bars, symbol, archivedThrough.current) : null;
      const next = merged?.bars ?? mergeDailyBars(current, value.bars);
      if (sameBars(current, next)) return;
      barsRef.current = next;
      if (paintRef.current) paintRef.current(next); else setBars(next);
      if (merged?.splitMismatch) replaceSplitHistory();
      statusRef.current?.('ready', next);
    }, { minIntervalMs: live ? LIVE_INTERVAL_MS : 300_000 });
  }, [visible, symbol, live]);
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
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderVisible: false,
      wickUpColor: up,
      wickDownColor: down,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    const averages: { period: number; series: ISeriesApi<'Line'> }[] = maKey.split(',').filter(Boolean).map(Number)
      .map((period, index) => ({ period, series: chart.addSeries(LineSeries, {
        color: indicatorColors[index % indicatorColors.length],
        lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      }) }));
    let volume: ISeriesApi<'Histogram'> | null = null;
    if (showVolume) {
      volume = chart.addSeries(HistogramSeries, {
        priceScaleId: 'volume', priceFormat: { type: 'volume' },
        priceLineVisible: false, lastValueVisible: false,
      });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    }
    const paint = (source: Bar[]) => {
      const visible = interactive ? source : source.slice(-VISIBLE_BARS);
      candles.setData(
        visible.map((bar) => ({
          time: bar.time as UTCTimestamp,
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        })),
      );
      // Calculate with the full source history, including warmup before the
      // initial viewport; a long configured MA must not start from 130 bars.
      for (const { period, series } of averages)
        series.setData(sma(source, period).filter(point => point.time >= visible[0]!.time)
          .map(point => ({ ...point, time: point.time as UTCTimestamp })));
      volume?.setData(visible.map(bar => ({ time: bar.time as UTCTimestamp, value: bar.volume,
        color: `${bar.close >= bar.open ? up : down}99` })));
      drawnRef.current = source;
    };
    const source = barsRef.current ?? bars;
    paint(source);
    paintRef.current = (next) => {
      const previous = drawnRef.current ?? bars;
      const range = chart.timeScale().getVisibleLogicalRange();
      const shift = (interactive ? next.length - previous.length : 0);
      paint(next);
      if (range) chart.timeScale().setVisibleLogicalRange({ from: range.from + shift, to: range.to + shift });
    };
    const scale = chart.timeScale();
    let rangeFrame = 0;
    const showRange = (range: LogicalRange | null) => {
      cancelAnimationFrame(rangeFrame);
      rangeFrame = requestAnimationFrame(() => {
        // Native pan/pinch updates the readout without React or persistence on
        // pointer movement. The range remains owned by Lightweight Charts.
        if (countRef.current && range)
          countRef.current.value = String(Math.min(barsRef.current?.length ?? bars.length, Math.round(range.to - range.from + 1)));
      });
    };
    if (interactive) {
      scale.subscribeVisibleLogicalRangeChange(showRange);
      const view = viewRef.current?.symbol === symbol ? viewRef.current.range : null;
      scale.setVisibleLogicalRange(view && view.to < source.length + 10 ? view : { from: miniChartStart(source, initialMonths), to: source.length - 1 });
    } else scale.fitContent();
    return () => {
      viewRef.current = { symbol, range: scale.getVisibleLogicalRange() };
      cancelAnimationFrame(rangeFrame);
      scale.unsubscribeVisibleLogicalRangeChange(showRange);
      chartRef.current = null;
      paintRef.current = null;
      drawnRef.current = null;
      chart.remove();
    };
  }, [bars, colors, maKey, interactive, showVolume, wheelZoom, initialMonths]);
  const zoom = (factor: number) => {
    const scale = chartRef.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    const current = barsRef.current;
    if (!range || !scale || !current?.length) return;
    const count = Math.min(current.length, Math.max(20, Math.round((range.to - range.from + 1) * factor)));
    const from = Math.max(0, Math.min(current.length - count, (range.from + range.to + 1 - count) / 2));
    scale.setVisibleLogicalRange({ from, to: from + count - 1 });
  };
  return (
    <div ref={ref} className={`mini-chart${interactive ? ' mini-chart-interactive' : ''}`} style={{ height }} data-status={status} title={sourceNote}>
      {interactive && <div className="mini-chart-controls" role="group" aria-label={`${symbol} 圖表縮放`}>
        <span>日 K{showVolume ? ' · 成交量' : ''}</span>
        <button type="button" aria-label="縮小圖表" title="縮小（顯示更多 K 線）" disabled={status !== 'ready'} onClick={() => zoom(1.5)}>−</button>
        <button type="button" aria-label="放大圖表" title="放大（顯示較少 K 線）" disabled={status !== 'ready'} onClick={() => zoom(2 / 3)}>＋</button>
        <button type="button" disabled={status !== 'ready'} onClick={() => {
          const current = barsRef.current;
          if (current?.length) chartRef.current?.timeScale().setVisibleLogicalRange({ from: miniChartStart(current, initialMonths), to: current.length - 1 });
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

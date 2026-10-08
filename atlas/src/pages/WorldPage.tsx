import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import {
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { PageProps } from './pageProps';
import { ChartAttribution, ReportGate, TrendBadge } from './ReportGate';
import type { IndexStatus, Report } from '../report/schema';
import { getDailyBars } from '../app/dataSources';
import {
  WINDOWS,
  mapWithConcurrency,
  performanceRow,
  rebase,
  sortRows,
  windowStart,
  type PerformanceRow,
  type PerformanceWindow,
  type SortKey,
} from './worldPerformance';
import type { Bar } from '../market-data/MarketDataProvider';
import { formatDateTime, formatPercent, formatPrice, toneClass } from '../ui/format';

/** Categorical slots validated for the dark chart surface (fixed order; color follows the index). */
export const SERIES_COLORS = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];
const DEFAULT_VISIBLE = 6;
const LEGEND_COLLAPSED = 8;
const CONCURRENCY = 4;

type Load = { status: 'loading' } | { status: 'ready'; bars: Bar[] } | { status: 'error' };

function seriesStyle(index: number) {
  return {
    color: SERIES_COLORS[index % SERIES_COLORS.length]!,
    // Past eight indices the hue repeats with a dashed line as the second encoding.
    dashed: index >= SERIES_COLORS.length,
  };
}

function OverlayChart({
  indices,
  loads,
  visible,
  window,
  onHover,
}: {
  indices: readonly IndexStatus[];
  loads: Record<string, Load>;
  visible: ReadonlySet<string>;
  window: PerformanceWindow;
  onHover: (values: Record<string, number> | null, time: number | null) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef(new Map<string, ISeriesApi<'Line'>>());
  const hoverRef = useRef(onHover);
  hoverRef.current = onHover;
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
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderVisible: false, rightOffset: 3 },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: false },
      localization: {
        locale: 'zh-TW',
        priceFormatter: (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(1)}%`,
      },
    });
    chartRef.current = chart;
    const series = seriesRef.current;
    const move = (param: MouseEventParams) => {
      if (!param.time || !param.point) {
        hoverRef.current(null, null);
        return;
      }
      const values: Record<string, number> = {};
      for (const [symbol, line] of series) {
        const point = param.seriesData.get(line) as { value?: number } | undefined;
        if (point && typeof point.value === 'number') values[symbol] = point.value;
      }
      hoverRef.current(values, Number(param.time));
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
    const ready = indices.filter((index) => visible.has(index.symbol) && loads[index.symbol]?.status === 'ready');
    const latest = Math.max(
      0,
      ...ready.map((index) => (loads[index.symbol] as { bars: Bar[] }).bars.at(-1)?.time ?? 0),
    );
    if (!latest) return;
    const start = windowStart(window, latest);
    // Zero line.
    indices.forEach((index, position) => {
      const load = loads[index.symbol];
      if (!visible.has(index.symbol) || load?.status !== 'ready') return;
      const { color, dashed } = seriesStyle(position);
      const line = chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        lineStyle: dashed ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerRadius: 4,
        title: '',
      });
      line.setData(rebase(load.bars, start).map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
      series.set(index.symbol, line);
    });
    const first = series.values().next().value;
    first?.createPriceLine({ price: 0, color: '#4a5363', lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: '' });
    chart.timeScale().fitContent();
  }, [indices, loads, visible, window]);
  return <div ref={hostRef} className="overlay-chart" role="img" aria-label="世界指數相對表現走勢圖（起點為 0%）" />;
}

const COLUMNS: { key: SortKey; label: string; numeric: boolean }[] = [
  { key: 'name', label: '指數', numeric: false },
  { key: 'last', label: '最新', numeric: true },
  { key: 'r1d', label: '1日', numeric: true },
  { key: 'r1w', label: '1週', numeric: true },
  { key: 'r1m', label: '1月', numeric: true },
  { key: 'ytd', label: '今年', numeric: true },
];

function WorldView({ report, openChart }: { report: Report } & Pick<PageProps, 'openChart'>) {
  const indices = report.worldIndices;
  const [loads, setLoads] = useState<Record<string, Load>>(() =>
    Object.fromEntries(indices.map((index) => [index.symbol, { status: 'loading' } as Load])),
  );
  const [window, setWindow] = useState<PerformanceWindow>('3M');
  const [visible, setVisible] = useState<Set<string>>(
    () => new Set(indices.slice(0, DEFAULT_VISIBLE).map((index) => index.symbol)),
  );
  const [sort, setSort] = useState<{ key: SortKey; direction: 'asc' | 'desc' }>({ key: 'ytd', direction: 'desc' });
  const [legendExpanded, setLegendExpanded] = useState(false);
  const [hover, setHover] = useState<{ values: Record<string, number> | null; time: number | null }>({ values: null, time: null });
  useEffect(() => {
    let live = true;
    setLoads(Object.fromEntries(indices.map((index) => [index.symbol, { status: 'loading' } as Load])));
    void mapWithConcurrency(indices, CONCURRENCY, async (index) => {
      try {
        const bars = await getDailyBars(index.symbol);
        if (live) setLoads((previous) => ({ ...previous, [index.symbol]: bars.length ? { status: 'ready', bars } : { status: 'error' } }));
      } catch {
        if (live) setLoads((previous) => ({ ...previous, [index.symbol]: { status: 'error' } }));
      }
    });
    return () => {
      live = false;
    };
  }, [indices]);
  const rows = useMemo(() => {
    const base = indices.map((index, position) => {
      const load = loads[index.symbol];
      const perf: PerformanceRow | null = load?.status === 'ready' ? performanceRow(load.bars) : null;
      return {
        index,
        position,
        name: index.name,
        status: load?.status ?? 'loading',
        last: perf?.last ?? index.close,
        r1d: perf?.r1d ?? index.changePct,
        r1w: perf?.r1w ?? null,
        r1m: perf?.r1m ?? null,
        ytd: perf?.ytd ?? null,
        asOf: perf?.asOf ?? null,
      };
    });
    return sortRows(base, sort.key, sort.direction);
  }, [indices, loads, sort]);
  const windowReturns = useMemo(() => {
    const out: Record<string, number | null> = {};
    const ready = indices.filter((index) => loads[index.symbol]?.status === 'ready');
    const latest = Math.max(0, ...ready.map((index) => (loads[index.symbol] as { bars: Bar[] }).bars.at(-1)?.time ?? 0));
    for (const index of ready) {
      const series = rebase((loads[index.symbol] as { bars: Bar[] }).bars, windowStart(window, latest));
      out[index.symbol] = series.at(-1)?.value ?? null;
    }
    return out;
  }, [indices, loads, window]);
  const toggle = (symbol: string) =>
    setVisible((previous) => {
      const next = new Set(previous);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  const sortBy = (key: SortKey) =>
    setSort((previous) =>
      previous.key === key
        ? { key, direction: previous.direction === 'desc' ? 'asc' : 'desc' }
        : { key, direction: key === 'name' ? 'asc' : 'desc' },
    );
  if (!indices.length)
    return (
      <div className="page-state empty" role="status">
        <b>報告沒有世界指數資料</b>
      </div>
    );
  const loadingCount = Object.values(loads).filter((load) => load.status === 'loading').length;
  return (
    <>
      <section className="card world-chart-card" aria-label="相對表現">
        <header className="card-head">
          <h2>
            相對表現
            <small>{hover.time ? formatDateTime(hover.time, false) : `起點 = 0%`}</small>
          </h2>
          <div className="segmented compact" role="group" aria-label="比較區間">
            {WINDOWS.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={window === value}
                className={window === value ? 'active' : ''}
                onClick={() => setWindow(value)}
              >
                {value === 'YTD' ? '今年' : value.replace('M', '個月').replace('1Y', '1年')}
              </button>
            ))}
          </div>
        </header>
        <div className="overlay-wrap">
          <OverlayChart
            indices={indices}
            loads={loads}
            visible={visible}
            window={window}
            onHover={(values, time) => setHover({ values, time })}
          />
          {loadingCount > 0 && <span className="overlay-loading">載入中 {indices.length - loadingCount}/{indices.length}</span>}
        </div>
        <ul className="series-legend" aria-label="圖例（點選顯示或隱藏）">
          {indices.map((index, position) => {
            // Collapsed: the first rows plus every series currently drawn.
            if (!legendExpanded && position >= LEGEND_COLLAPSED && !visible.has(index.symbol)) return null;
            const { color, dashed } = seriesStyle(position);
            const load = loads[index.symbol];
            const value = hover.values ? hover.values[index.symbol] : windowReturns[index.symbol];
            const on = visible.has(index.symbol);
            return (
              <li key={index.symbol}>
                <button
                  type="button"
                  aria-pressed={on}
                  className={on ? 'on' : ''}
                  disabled={load?.status === 'error'}
                  onClick={() => toggle(index.symbol)}
                  title={load?.status === 'error' ? '暫無資料' : on ? '點選隱藏' : '點選顯示'}
                  data-status={load?.status ?? 'loading'}
                >
                  <i className={dashed ? 'dashed' : ''} style={{ borderColor: color, background: dashed ? 'transparent' : color }} aria-hidden="true" />
                  <span className="legend-name">{index.name}</span>
                  <span className={`legend-value ${on ? toneClass(value) : 'flat'}`}>
                    {load?.status === 'error' ? '暫無資料' : load?.status !== 'ready' ? '…' : on ? formatPercent(value ?? null, 1) : '—'}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        {indices.length > LEGEND_COLLAPSED && (
          <button type="button" className="text-button legend-more" onClick={() => setLegendExpanded(!legendExpanded)}>
            {legendExpanded ? '收合指數清單' : `顯示全部 ${indices.length} 個指數`}
          </button>
        )}
      </section>
      <section className="card world-table-card" aria-label="世界指數表現">
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                {COLUMNS.map((column) => (
                  <th
                    key={column.key}
                    scope="col"
                    className={column.numeric ? 'num' : ''}
                    aria-sort={sort.key === column.key ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button type="button" onClick={() => sortBy(column.key)}>
                      {column.label}
                      {sort.key === column.key ? (
                        sort.direction === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />
                      ) : (
                        <ArrowUpDown size={12} className="sort-idle" />
                      )}
                    </button>
                  </th>
                ))}
                <th scope="col">趨勢</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.index.symbol}
                  tabIndex={0}
                  onClick={() => openChart({ symbol: row.index.symbol, name: row.name, tf: '1D', maList: row.index.maList })}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      openChart({ symbol: row.index.symbol, name: row.name, tf: '1D', maList: row.index.maList });
                    }
                  }}
                  aria-label={`${row.name}，開啟圖表`}
                >
                  <th scope="row">
                    <span className="row-name">
                      <i style={{ background: seriesStyle(row.position).color }} aria-hidden="true" />
                      <span>
                        <b>{row.name}</b>
                        <small>{row.index.symbol}</small>
                      </span>
                    </span>
                  </th>
                  <td className="num">{formatPrice(row.last)}</td>
                  {row.status === 'error' ? (
                    <>
                      <td className={`num ${toneClass(row.r1d)}`}>{formatPercent(row.r1d)}</td>
                      <td className="num no-data" colSpan={3}>
                        暫無資料
                      </td>
                    </>
                  ) : (
                    (['r1d', 'r1w', 'r1m', 'ytd'] as const).map((key) => (
                      <td key={key} className={`num ${toneClass(row[key])}`}>
                        {row.status === 'loading' && key !== 'r1d' ? <span className="skeleton inline" /> : formatPercent(row[key])}
                      </td>
                    ))
                  )}
                  <td>
                    <TrendBadge trend={row.index.trend} label={row.index.trendLabel} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="source-note">
          報酬率以每日收盤價計算（1週／1月取該日或之前最近一個交易日；今年＝相對去年最後一個交易日）。趨勢標籤來自每日報告。
        </p>
      </section>
    </>
  );
}

export default function WorldPage({ report, reload, openChart }: PageProps) {
  return (
    <div className="page world-page">
      <div className="page-head">
        <div>
          <h2 className="page-title">世界指數</h2>
          {report.status === 'ok' && (
            <p className="page-sub">
              報告日 {formatDateTime(report.report.reportDate, false)} · 延遲行情
            </p>
          )}
        </div>
      </div>
      <ReportGate report={report} reload={reload}>
        {(data) => <WorldView report={data} openChart={openChart} />}
      </ReportGate>
      <footer className="page-foot">
        <span>延遲行情，僅供研究參考，非投資建議。</span>
        <ChartAttribution />
      </footer>
    </div>
  );
}

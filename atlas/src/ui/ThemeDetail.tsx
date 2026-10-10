import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { ChartCandlestick, ChevronDown, X } from 'lucide-react';
import { MiniChart } from './MiniChart';
import { useDialogFocus } from './useDialogFocus';
import { formatDateTime, formatPercent, formatPrice, toneClass } from './format';
import { PERIODS, PERIOD_LABELS, type PeriodKey, type Theme, type ThemeConstituent, type ThemesData } from '../report/themes';
import type { OpenChartOptions } from '../app/Site';
import {
  CHART_RANGES,
  MIN_VALID_FOR_RANK,
  breadthLabel,
  chartData,
  relativeTo,
  type ChartRange,
} from '../pages/themesView';

const W = 340;
const H = 156;
const PAD = { top: 10, right: 52, bottom: 20, left: 6 };
const THEME_COLOR = '#3987e5';
const BENCH_COLOR = '#9aa3b2';
const CLOSE_DRAG = 90;

function pct(index: number) {
  return `${index > 0 ? '+' : ''}${index.toFixed(1)}%`;
}

/** Theme index versus SPY, both rebased to 0% on the first visible day. Drag or hover to read a day. */
function VersusChart({ theme, data }: { theme: Theme; data: ThemesData }) {
  const [range, setRange] = useState<ChartRange>('3m');
  const [hover, setHover] = useState<number | null>(null);
  const chart = useMemo(() => chartData(theme, data, range), [theme, data, range]);
  const count = chart.dates.length;
  const geometry = useMemo(() => {
    if (count < 2) return null;
    const values = [...chart.theme, ...chart.bench];
    const lo = Math.min(...values, 100);
    const hi = Math.max(...values, 100);
    const span = hi - lo || 1;
    const pad = span * 0.08;
    const min = lo - pad;
    const max = hi + pad;
    const x = (i: number) => PAD.left + (i / (count - 1)) * (W - PAD.left - PAD.right);
    const y = (v: number) => PAD.top + (1 - (v - min) / (max - min)) * (H - PAD.top - PAD.bottom);
    const path = (series: number[]) => series.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join('');
    const step = span > 60 ? 25 : span > 24 ? 10 : span > 10 ? 5 : 2;
    const ticks: number[] = [];
    for (let t = Math.ceil((min - 100) / step) * step; t <= max - 100; t += step) ticks.push(t);
    return { x, y, themePath: path(chart.theme), benchPath: path(chart.bench), ticks, min, max };
  }, [chart, count]);
  const shown = hover ?? count - 1;
  const move = (event: ReactPointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * W;
    const ratio = (px - PAD.left) / (W - PAD.left - PAD.right);
    setHover(Math.max(0, Math.min(count - 1, Math.round(ratio * (count - 1)))));
  };
  if (!geometry) return <p className="detail-muted">這個題材還沒有足夠的走勢資料。</p>;
  const t = chart.theme[shown]! - 100;
  const b = chart.bench[shown]! - 100;
  return (
    <div className="versus">
      <div className="versus-head">
        <div className="versus-legend">
          <span>
            <i style={{ background: THEME_COLOR }} />
            題材
            <b className={`num ${toneClass(t)}`}>{pct(t)}</b>
          </span>
          <span>
            <i className="dashed" style={{ borderColor: BENCH_COLOR }} />
            {data.benchmark.symbol}
            <b className={`num ${toneClass(b)}`}>{pct(b)}</b>
          </span>
          <span className="versus-date">{formatDateTime(chart.dates[shown], false)}</span>
        </div>
        <div className="segmented compact" role="group" aria-label="走勢區間">
          {CHART_RANGES.map((item) => (
            <button key={item.key} type="button" className={item.key === range ? 'active' : ''} aria-pressed={item.key === range} onClick={() => setRange(item.key)}>
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <svg
        className="versus-svg"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`題材指數與 ${data.benchmark.symbol} 走勢比較，區間起點為 0%`}
        onPointerMove={move}
        onPointerDown={move}
        onPointerLeave={() => setHover(null)}
      >
        {geometry.ticks.map((tick) => (
          <g key={tick}>
            <line className={tick === 0 ? 'zero' : 'grid'} x1={PAD.left} x2={W - PAD.right} y1={geometry.y(100 + tick)} y2={geometry.y(100 + tick)} />
            <text className="axis" x={W - PAD.right + 6} y={geometry.y(100 + tick) + 3.5}>
              {tick > 0 ? `+${tick}%` : `${tick}%`}
            </text>
          </g>
        ))}
        <path d={geometry.benchPath} fill="none" stroke={BENCH_COLOR} strokeWidth="1.6" strokeDasharray="4 3" strokeLinejoin="round" />
        <path d={geometry.themePath} fill="none" stroke={THEME_COLOR} strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round" />
        <text className="axis" x={PAD.left} y={H - 5}>
          {formatDateTime(chart.dates[0], false).slice(5)}
        </text>
        <text className="axis" x={W - PAD.right} y={H - 5} textAnchor="end">
          {formatDateTime(chart.dates[count - 1], false).slice(5)}
        </text>
        <line className="cursor" x1={geometry.x(shown)} x2={geometry.x(shown)} y1={PAD.top} y2={H - PAD.bottom} />
        <circle cx={geometry.x(shown)} cy={geometry.y(chart.bench[shown]!)} r="3" fill={BENCH_COLOR} />
        <circle cx={geometry.x(shown)} cy={geometry.y(chart.theme[shown]!)} r="3.5" fill={THEME_COLOR} />
      </svg>
    </div>
  );
}

function StatTable({ theme, data }: { theme: Theme; data: ThemesData }) {
  const rows: { label: string; values: (number | null)[]; tone: boolean }[] = [
    { label: '題材', values: PERIODS.map((p) => theme.returns[p]), tone: true },
    { label: data.benchmark.symbol, values: PERIODS.map((p) => data.benchmark.returns[p]), tone: true },
    { label: '相對', values: PERIODS.map((p) => relativeTo(theme.returns[p], data.benchmark.returns[p])), tone: true },
  ];
  return (
    <div className="stat-table" role="table" aria-label="各期間報酬">
      <div className="stat-row head" role="row">
        <span role="columnheader">%</span>
        {PERIODS.map((p) => (
          <span key={p} role="columnheader">
            {PERIOD_LABELS[p]}
          </span>
        ))}
      </div>
      {rows.map((row) => (
        <div key={row.label} className={`stat-row ${row.label === '相對' ? 'rel' : ''}`} role="row">
          <span className="stat-label" role="rowheader">
            {row.label}
          </span>
          {row.values.map((value, index) => (
            <span key={PERIODS[index]} role="cell" className={`num ${row.tone ? toneClass(value) : ''}`}>
              {formatPercent(value, 1).replace('%', '')}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

function ConstituentRow({
  item,
  period,
  expanded,
  onToggle,
  openChart,
}: {
  item: ThemeConstituent;
  period: PeriodKey;
  expanded: boolean;
  onToggle: () => void;
  openChart: (options: OpenChartOptions) => void;
}) {
  const value = item.returns[period];
  return (
    <li className={`constituent ${expanded ? 'open' : ''}`}>
      <button type="button" className="constituent-head" aria-expanded={expanded} onClick={onToggle}>
        <span className="constituent-id">
          <b>{item.symbol}</b>
          <span className="constituent-name">{item.name}</span>
          {item.note && <small>{item.note}</small>}
        </span>
        <span className="constituent-figures">
          <span className={`num constituent-value ${toneClass(value)}`}>{formatPercent(value, 1)}</span>
          <small className="num">
            {item.fromHi52 === null ? '—' : item.fromHi52 > -0.05 ? '創52週新高' : `距高點 ${formatPercent(item.fromHi52, 1)}`}
          </small>
        </span>
        <ChevronDown size={16} className="constituent-chevron" aria-hidden="true" />
      </button>
      {expanded && (
        <div className="constituent-body">
          <div className="constituent-returns">
            {PERIODS.map((p) => (
              <span key={p}>
                <small>{PERIOD_LABELS[p]}</small>
                <b className={`num ${toneClass(item.returns[p])}`}>{formatPercent(item.returns[p], 1)}</b>
              </span>
            ))}
          </div>
          <p className="constituent-facts">
            收盤 {formatPrice(item.close)} · 3月相對 SPY <b className={`num ${toneClass(item.rs3m)}`}>{formatPercent(item.rs3m, 1)}</b> ·{' '}
            {item.aboveMa50 === null ? '50日線資料不足' : item.aboveMa50 ? '站上50日線' : '跌破50日線'}
          </p>
          <MiniChart symbol={item.symbol} maList={[20, 50]} height={150} />
          <button
            type="button"
            className="ghost-button open-chart"
            onClick={() => openChart({ symbol: item.symbol, name: item.name, maList: [20, 50] })}
          >
            <ChartCandlestick size={16} aria-hidden="true" />
            開啟圖表
          </button>
        </div>
      )}
    </li>
  );
}

/**
 * Theme detail: a bottom sheet on phones, a right-hand drawer on wide screens (layout is CSS).
 * Plain-language description, SPY comparison, and the constituents with expandable candle charts.
 */
export function ThemeDetail({
  theme,
  data,
  initialPeriod,
  onClose,
  openChart,
}: {
  theme: Theme;
  data: ThemesData;
  initialPeriod: PeriodKey;
  onClose: () => void;
  openChart: (options: OpenChartOptions) => void;
}) {
  const [period, setPeriod] = useState<PeriodKey>(initialPeriod);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [drag, setDrag] = useState(0);
  const dragStart = useRef<number | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useDialogFocus(true, 'theme-detail');
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeRef.current();
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);
  const items = useMemo(
    () =>
      [...theme.constituents].sort((a, b) => {
        const av = a.returns[period];
        const bv = b.returns[period];
        if (av === null || bv === null) return av === bv ? 0 : av === null ? 1 : -1;
        return bv - av;
      }),
    [theme.constituents, period],
  );
  const parent = data.parents.find((p) => p.key === theme.parent)?.name;
  const rankedCount = data.themes.filter((t) => t.rank !== null).length;
  const thin = theme.validCount < MIN_VALID_FOR_RANK;
  // Dragging the header down dismisses the phone sheet.
  const onHeadDown = (event: ReactPointerEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest('button')) return;
    dragStart.current = event.clientY;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const onHeadMove = (event: ReactPointerEvent<HTMLElement>) => {
    if (dragStart.current === null) return;
    setDrag(Math.max(0, event.clientY - dragStart.current));
  };
  const onHeadUp = () => {
    if (dragStart.current === null) return;
    dragStart.current = null;
    if (drag > CLOSE_DRAG) onClose();
    else setDrag(0);
  };

  return (
    <div className="theme-sheet-backdrop" onClick={onClose}>
      <aside
        className="theme-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={`${theme.name} 題材詳情`}
        data-dialog-focus="theme-detail"
        style={drag ? { transform: `translateY(${drag}px)`, transition: 'none' } : undefined}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="sheet-head" onPointerDown={onHeadDown} onPointerMove={onHeadMove} onPointerUp={onHeadUp} onPointerCancel={onHeadUp}>
          <span className="sheet-grab" aria-hidden="true" />
          <div className="sheet-title">
            <div>
              <h2>{theme.name}</h2>
              <p>
                {parent && <span className="sheet-tag">{parent}</span>}
                {theme.rank !== null && (
                  <span>
                    動能排名 <b>{theme.rank}</b> / {rankedCount}
                  </span>
                )}
              </p>
            </div>
            <button type="button" className="icon-button" aria-label="關閉" onClick={onClose}>
              <X size={18} />
            </button>
          </div>
        </header>
        <div className="sheet-body">
          <section className="detail-about" aria-label="這是什麼題材">
            <p>{theme.description}</p>
            {theme.etf && (
              <p className="etf-line">
                參考 ETF：
                <button type="button" className="etf-link" onClick={() => openChart({ symbol: theme.etf!.symbol })}>
                  {theme.etf.symbol}
                </button>
                <span>（把這個主題的股票包成一檔基金）近 3 月</span>
                <b className={`num ${toneClass(theme.etf.returns.m3)}`}>{formatPercent(theme.etf.returns.m3, 1)}</b>
              </p>
            )}
          </section>

          <StatTable theme={theme} data={data} />
          <div className="detail-facts">
            <span>
              站上50日線 <b className="num">{theme.breadth50 === null ? '—' : `${Math.round(theme.breadth50)}%`}</b>
              <small>{breadthLabel(theme.breadth50)}</small>
            </span>
            <span>
              中位數（3月） <b className={`num ${toneClass(theme.median.m3)}`}>{formatPercent(theme.median.m3, 1)}</b>
              <small>不受單一股票影響</small>
            </span>
          </div>

          <VersusChart theme={theme} data={data} />

          <section className="detail-constituents" aria-label="成分股">
            <div className="constituents-head">
              <h3>
                成分股 <small>{thin ? `僅 ${theme.validCount}/${theme.count} 檔有資料` : `${theme.validCount}/${theme.count} 檔`}</small>
              </h3>
              <label className="period-select">
                <span>依</span>
                <select value={period} onChange={(event) => setPeriod(event.target.value as PeriodKey)} aria-label="成分股排序期間">
                  {PERIODS.map((p) => (
                    <option key={p} value={p}>
                      {PERIOD_LABELS[p]}報酬
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {thin && <p className="detail-muted">部分代號暫時抓不到資料（可能是新上市或改名），題材數字僅供參考，不列入排名。</p>}
            <ul className="constituent-list">
              {items.map((item) => (
                <ConstituentRow
                  key={item.symbol}
                  item={item}
                  period={period}
                  expanded={expanded === item.symbol}
                  onToggle={() => setExpanded(expanded === item.symbol ? null : item.symbol)}
                  openChart={openChart}
                />
              ))}
            </ul>
          </section>
          <p className="detail-disclaimer">股價為收盤資料；說明為簡化介紹，僅供研究參考，非投資建議。</p>
        </div>
      </aside>
    </div>
  );
}

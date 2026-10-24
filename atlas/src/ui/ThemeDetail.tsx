import { HelpDisclosure } from './HelpDisclosure';
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { ChartCandlestick, ChevronDown, X } from 'lucide-react';
import { MiniChart } from './MiniChart';
import { DrawableCandleChart } from './DrawableCandleChart';
import { getDailyBars } from '../app/dataSources';
import type { Bar } from '../market-data/MarketDataProvider';
import { useDialogFocus } from './useDialogFocus';
import { formatPercent, formatPrice, toneClass } from './format';
import { PERIODS, PERIOD_LABELS, type PeriodKey, type Theme, type ThemeConstituent, type ThemesData } from '../report/themes';
import type { OpenChartOptions } from '../app/Site';
import {
  MIN_VALID_FOR_RANK,
  breadthLabel,
  relativeTo,
} from '../pages/themesView';

const CLOSE_DRAG = 90;

/** Tradable ETF/constituent OHLC. Equal-weight close indexes have no observed intraday high/low. */
function ThemeCandleChart({ theme, data }: { theme: Theme; data: ThemesData }) {
  const options = [
    ...new Map([
      ...(theme.etf
        ? [[theme.etf.symbol, `${theme.etf.symbol}・參考 ETF`] as const]
        : []),
      ...theme.constituents.map(
        (c) => [c.symbol, `${c.symbol}・${c.name}`] as const,
      ),
    ]).entries(),
  ];
  const [symbol, setSymbol] = useState(options[0]?.[0] ?? "");
  const [loaded, setLoaded] = useState<{
    symbol: string;
    bars: Bar[];
    error?: string;
  }>();
  useEffect(() => {
    let active = true;
    if (symbol)
      void getDailyBars(symbol).then(
        (bars) => {
          if (active) setLoaded({ symbol, bars });
        },
        (e) => {
          if (active)
            setLoaded({
              symbol,
              bars: [],
              error: e instanceof Error ? e.message : "K線載入失敗",
            });
        },
      );
    return () => {
      active = false;
    };
  }, [symbol]);
  const current = loaded?.symbol === symbol ? loaded : undefined;
  return (
    <section aria-label="題材K線">
      <label className="candle-toolbar">
        K線標的
        <select
          aria-label="題材K線標的"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
        >
          {options.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <p className="detail-muted">
        圖表：{symbol}；上方報酬與寬度統計：題材等權組合。
      </p>
      <DrawableCandleChart
        key={symbol}
        identity={`theme:${data.market}:${theme.key}:${symbol}`}
        marketSymbol={symbol}
        bars={current?.bars ?? []}
        source={symbol || theme.name}
        unit={data.market === "tw" ? "TWD" : "USD"}
        loading={!current && !!symbol}
        error={current?.error}
      />
    </section>
  );
}

function StatTable({ theme, data }: { theme: Theme; data: ThemesData }) {
  const rows: { label: string; values: (number | null)[]; tone: boolean }[] = [
    { label: '題材', values: PERIODS.map((p) => theme.returns[p]), tone: true },
    { label: data.benchmark.label, values: PERIODS.map((p) => data.benchmark.returns[p]), tone: true },
    { label: '相對', values: PERIODS.map((p) => relativeTo(theme.returns[p], data.benchmark.returns[p])), tone: true },
  ];
  return (
    <div
      className="stat-table"
      role="table"
      aria-label="各期間報酬"
      // Wider label column for names such as 加權指數 (SPY fits the default 34px).
      style={data.benchmark.label.length > 3 ? { ['--stat-label' as string]: `${Math.ceil(data.benchmark.label.length * 12.5)}px` } : undefined}
    >
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
  benchmark,
  period,
  expanded,
  onToggle,
  openChart,
}: {
  item: ThemeConstituent;
  benchmark: string;
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
            收盤 {formatPrice(item.close)} · 3月相對 {benchmark} <b className={`num ${toneClass(item.rs3m)}`}>{formatPercent(item.rs3m, 1)}</b> ·{' '}
            {item.aboveMa50 === null ? '50日線資料不足' : item.aboveMa50 ? '站上50日線' : '跌破50日線'}
          </p>
          <MiniChart symbol={item.symbol} height={150} />
          <button
            type="button"
            className="ghost-button open-chart"
            onClick={() => openChart({ symbol: item.symbol, name: item.name })}
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
            <HelpDisclosure label="題材介紹">{theme.description}</HelpDisclosure>
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

          <ThemeCandleChart theme={theme} data={data} />

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
                  benchmark={data.benchmark.label}
                  period={period}
                  expanded={expanded === item.symbol}
                  onToggle={() => setExpanded(expanded === item.symbol ? null : item.symbol)}
                  openChart={openChart}
                />
              ))}
            </ul>
          </section>
          <HelpDisclosure label="資料備註">股價為收盤資料；說明為簡化介紹，僅供研究參考，非投資建議。</HelpDisclosure>
        </div>
      </aside>
    </div>
  );
}

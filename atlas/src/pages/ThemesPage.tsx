import { HelpDisclosure } from '../ui/HelpDisclosure';
import { userStorage } from '../auth/storage';
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react';
import { ArrowDown, ArrowUp, ChevronRight } from 'lucide-react';
import { ThemeRotationCard } from '../ui/ThemeRotationCard';
import type { PageProps } from './pageProps';
import { PageState } from '../ui/PageState';
import ThemesMap from './ThemesMap';
import { useMapSource } from './useMapSource';
import { INDUSTRY_PERIODS, clampIndustryPeriod } from './industryModel';
import { ThemeDetail } from '../ui/ThemeDetail';
import {
  PERIODS,
  PERIOD_LABELS,
  THEMES_MARKETS,
  THEMES_MARKET_META,
  useThemes,
  type PeriodKey,
  type Theme,
  type ThemesData,
  type ThemesMarket,
} from '../report/themes';
import {
  MIN_VALID_FOR_RANK,
  breadthLabel,
  dataAgeDays,
  defaultDirection,
  filterByParent,
  heat,
  isRanked,
  metricValue,
  relativeTo,
  sortThemes,
  sparkGeometry,
  sparkLength,
  sparkValues,
  themeByKey,
  tileOrder,
  type Direction,
  type ListSortKey,
  type Mode,
} from './themesView';
import { formatDateTime, formatPercent, toneClass } from '../ui/format';
import '../ui/themes.css';

const VIEW_KEY = 'atlas-themes-view-v1';
const SPARK_W = 120;
const SPARK_H = 30;

interface ViewPrefs {
  period: PeriodKey;
  mode: Mode;
}

function readPrefs(): ViewPrefs {
  const fallback: ViewPrefs = { period: 'm1', mode: 'return' };
  try {
    const raw = JSON.parse(userStorage.getItem(VIEW_KEY) ?? 'null') as Partial<ViewPrefs> | null;
    return {
      period: raw && (PERIODS as readonly string[]).includes(raw.period ?? '') ? (raw.period as PeriodKey) : fallback.period,
      mode: raw?.mode === 'rel' ? 'rel' : 'return',
    };
  } catch {
    return fallback;
  }
}

function writePrefs(prefs: ViewPrefs) {
  try {
    userStorage.setItem(VIEW_KEY, JSON.stringify(prefs));
  } catch {
    /* A remembered tab is only a convenience. */
  }
}

function Sparkline({ values, tone }: { values: number[]; tone: 'up' | 'down' | 'flat' }) {
  const geometry = useMemo(() => sparkGeometry(values, SPARK_W, SPARK_H), [values]);
  if (!geometry.last) return <span className="tile-spark empty" aria-hidden="true" />;
  return (
    <svg className={`tile-spark ${tone}`} viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} preserveAspectRatio="none" aria-hidden="true">
      <path className="spark-area" d={geometry.area} />
      <path className="spark-line" d={geometry.line} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Tile({
  theme,
  data,
  period,
  mode,
  onOpen,
}: {
  theme: Theme;
  data: ThemesData;
  period: PeriodKey;
  mode: Mode;
  onOpen: (key: string) => void;
}) {
  const thin = theme.validCount < MIN_VALID_FOR_RANK;
  const value = metricValue(theme, data, period, mode);
  const other = metricValue(theme, data, period, mode === 'return' ? 'rel' : 'return');
  const tone = thin ? { side: 'flat' as const, mix: 0 } : heat(value, period);
  const values = useMemo(
    () => sparkValues(theme, data, sparkLength(period, data.dates), mode),
    [theme, data, period, mode],
  );
  const label = `${theme.name}，${PERIOD_LABELS[period]}${mode === 'rel' ? `相對 ${data.benchmark.label}` : '報酬'} ${formatPercent(value, 1)}${
    isRanked(theme) ? `，動能排名第 ${theme.rank}` : ''
  }`;
  return (
    <button
      type="button"
      className={`theme-tile heat-${tone.side} ${thin ? 'thin' : ''}`}
      style={{ ['--mix' as string]: `${tone.mix}%` }}
      aria-label={label}
      onClick={() => onOpen(theme.key)}
    >
      <span className="tile-top">
        <b className="tile-name">{theme.name}</b>
        {isRanked(theme) && <span className="tile-rank" title="動能排名">#{theme.rank}</span>}
      </span>
      <span className="tile-value">{formatPercent(value, 1)}</span>
      <Sparkline values={values} tone={tone.side} />
      <span className="tile-sub">
        {thin ? (
          `資料不足（${theme.validCount}/${theme.count} 檔）`
        ) : (
          <>
            {mode === 'return' ? `相對 ${data.benchmark.label} ` : '報酬 '}
            <b>{formatPercent(mode === 'return' ? relativeTo(theme.returns[period], data.benchmark.returns[period]) : other, 1)}</b>
          </>
        )}
      </span>
    </button>
  );
}

function columnsFor(label: string): { key: ListSortKey; label: string; hint?: string }[] {
  return [
    { key: 'd1', label: PERIOD_LABELS.d1 },
    { key: 'w1', label: PERIOD_LABELS.w1 },
    { key: 'm1', label: PERIOD_LABELS.m1 },
    { key: 'm3', label: PERIOD_LABELS.m3 },
    { key: 'm6', label: PERIOD_LABELS.m6 },
    { key: 'ytd', label: PERIOD_LABELS.ytd },
    { key: 'rs3m', label: `3月相對${label}`, hint: `近 3 個月報酬比${label}多（少）賺多少` },
    { key: 'breadth', label: '站上50日線', hint: '題材內有幾成的股票股價在 50 日均線之上，越高代表漲勢越普遍' },
    { key: 'momentum', label: '動能', hint: `綜合 1 週、1 月、3 月報酬與相對${label}的分數，用來排名` },
  ];
}

function Value({ value, digits = 1 }: { value: number | null; digits?: number }) {
  return <span className={`num ${toneClass(value)}`}>{formatPercent(value, digits)}</span>;
}

function BreadthBar({ value }: { value: number | null }) {
  if (value === null) return <span className="num muted">—</span>;
  return (
    <span className="theme-breadth" title={breadthLabel(value)}>
      <span className="theme-breadth-bar" aria-hidden="true">
        <i style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
      </span>
      <span className="num">{Math.round(value)}%</span>
    </span>
  );
}

function ThemeTable({
  themes,
  data,
  sort,
  onSort,
  onOpen,
  columns,
}: {
  themes: Theme[];
  data: ThemesData;
  sort: { key: ListSortKey; direction: Direction };
  onSort: (key: ListSortKey) => void;
  onOpen: (key: string) => void;
  columns: ReturnType<typeof columnsFor>;
}) {
  const header = (key: ListSortKey, label: string, className = '', hint?: string) => {
    const active = sort.key === key;
    return (
      <th scope="col" className={className} aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
        <button type="button" className={active ? 'active' : ''} onClick={() => onSort(key)} title={hint}>
          {label}
          {active && (sort.direction === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}
        </button>
      </th>
    );
  };
  return (
    <table className="themes-table">
      <thead>
        <tr>
          {header('rank', '#', 'col-rank')}
          {header('name', '題材', 'col-name')}
          {columns.map((column) => header(column.key, column.label, '', column.hint))}
        </tr>
      </thead>
      <tbody>
        {themes.map((theme) => (
          <tr key={theme.key} tabIndex={0} onClick={() => onOpen(theme.key)} onKeyDown={(e) => e.key === 'Enter' && onOpen(theme.key)}>
            <td className="col-rank num muted">{isRanked(theme) ? theme.rank : '—'}</td>
            <th scope="row" className="col-name">
              <b>{theme.name}</b>
              <small>{data.parents.find((p) => p.key === theme.parent)?.name ?? ''}</small>
            </th>
            {PERIODS.map((period) => (
              <td key={period}>
                <Value value={theme.returns[period]} />
              </td>
            ))}
            <td>
              <Value value={theme.rs.m3} />
            </td>
            <td>
              <BreadthBar value={theme.breadth50} />
            </td>
            <td>
              <span className="num">{theme.momentum === null ? '—' : theme.momentum.toFixed(1)}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ThemeCards({ themes, label, onOpen }: { themes: Theme[]; label: string; onOpen: (key: string) => void }) {
  return (
    <ul className="themes-cards">
      {themes.map((theme) => (
        <li key={theme.key}>
          <button type="button" className="theme-card" onClick={() => onOpen(theme.key)}>
            <span className="card-top">
              <span className="card-title">
                {isRanked(theme) && <i className="card-rank">#{theme.rank}</i>}
                <b>{theme.name}</b>
              </span>
              <ChevronRight size={16} aria-hidden="true" />
            </span>
            <span className="card-grid">
              {PERIODS.map((period) => (
                <span key={period}>
                  <small>{PERIOD_LABELS[period]}</small>
                  <Value value={theme.returns[period]} />
                </span>
              ))}
            </span>
            <span className="card-foot">
              <span>
                3月相對 {label} <Value value={theme.rs.m3} />
              </span>
              <span>
                站上50日線 <b className="num">{theme.breadth50 === null ? '—' : `${Math.round(theme.breadth50)}%`}</b>
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function ThemesView({
  data,
  theme,
  view,
  navigate,
  openChart,
}: { data: ThemesData; theme?: string; view?: ThemesViewKey } & Pick<PageProps, 'navigate' | 'openChart'>) {
  const market = data.market;
  const label = data.benchmark.label;
  const columns = useMemo(() => columnsFor(label), [label]);
  const [prefs, setPrefs] = useState<ViewPrefs>(readPrefs);
  const [parent, setParent] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: ListSortKey; direction: Direction }>({ key: 'rank', direction: 'asc' });
  const { period, mode } = prefs;
  const update = (patch: Partial<ViewPrefs>) => {
    const next = { ...prefs, ...patch };
    setPrefs(next);
    writePrefs(next);
  };
  const selected = themeByKey(data, theme);
  // A link to a theme the file does not have (renamed or removed) falls back to the plain page.
  useEffect(() => {
    if (theme && !selected) navigate(themesRoute(market, view), { replace: true });
  }, [theme, selected, navigate, market, view]);
  const open = useCallback((key: string) => navigate({ ...themesRoute(market, view), theme: key }), [navigate, market, view]);
  const close = useCallback(() => navigate(themesRoute(market, view)), [navigate, market, view]);
  const isMap = view === 'map';
  const mapSource = useMapSource(market, isMap);
  // The industry map has 1日/1週/1月/3月 only (the universe carries no longer returns); the stored period is kept.
  const industryMap = isMap && mapSource.kind !== 'themes';
  const shownPeriods = industryMap ? PERIODS.filter((key) => (INDUSTRY_PERIODS as readonly string[]).includes(key)) : PERIODS;
  const shownPeriod = industryMap ? clampIndustryPeriod(period) : period;
  const visible = useMemo(() => filterByParent(data.themes, parent), [data, parent]);
  const tiles = useMemo(() => tileOrder(visible, data, period, mode), [visible, data, period, mode]);
  const rows = useMemo(() => sortThemes(visible, data, sort.key, sort.direction), [visible, data, sort]);
  const parentsWithThemes = data.parents.filter((p) => data.themes.some((t) => t.parent === p.key));
  const benchValue = data.benchmark.returns[period];
  const age = dataAgeDays(data.asOf, new Date());
  const onSort = (key: ListSortKey) =>
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
        : { key, direction: defaultDirection(key) },
    );

  return (
    <>
      <div className="themes-bar">
        <div className="segmented compact themes-view" role="group" aria-label="檢視">
          <button type="button" className={isMap ? '' : 'active'} aria-pressed={!isMap} onClick={() => !isMap || navigate(themesRoute(market))}>
            列表
          </button>
          <button type="button" className={isMap ? 'active' : ''} aria-pressed={isMap} onClick={() => isMap || navigate(themesRoute(market, 'map'))}>
            熱力圖
          </button>
        </div>
        <div className="themes-controls">
          <div className="segmented compact" role="group" aria-label="期間">
            {shownPeriods.map((key) => (
              <button
                key={key}
                type="button"
                className={key === shownPeriod ? 'active' : ''}
                aria-pressed={key === shownPeriod}
                onClick={() => update({ period: key })}
              >
                {PERIOD_LABELS[key]}
              </button>
            ))}
          </div>
          {!isMap && (
            <div className="segmented compact" role="group" aria-label="指標">
              <button type="button" className={mode === 'return' ? 'active' : ''} aria-pressed={mode === 'return'} onClick={() => update({ mode: 'return' })}>
                報酬
              </button>
              <button type="button" className={mode === 'rel' ? 'active' : ''} aria-pressed={mode === 'rel'} onClick={() => update({ mode: 'rel' })}>
                相對 {label}
              </button>
            </div>
          )}
        </div>
        {!industryMap && (
        <div className="chip-row themes-parents" role="group" aria-label="產業大類">
          <button type="button" className={`chip ${parent === null ? 'active' : ''}`} aria-pressed={parent === null} onClick={() => setParent(null)}>
            全部<span className="chip-count">{data.themes.length}</span>
          </button>
          {parentsWithThemes.map((p) => (
            <button
              key={p.key}
              type="button"
              className={`chip ${parent === p.key ? 'active' : ''}`}
              aria-pressed={parent === p.key}
              onClick={() => setParent(parent === p.key ? null : p.key)}
            >
              {p.name}
              <span className="chip-count">{data.themes.filter((t) => t.parent === p.key).length}</span>
            </button>
          ))}
        </div>
        )}
      </div>

      {isMap ? (
        <ThemesMap data={data} themes={visible} source={mapSource} period={period} onOpenTheme={open} openChart={openChart} />
      ) : (
        <>
        <div className="themes-legend" aria-hidden="true">
          <span>
            大盤 {label} {PERIOD_LABELS[period]} <Value value={benchValue} />
          </span>
          <span className="legend-scale">
            <i className="swatch-down" />
            <i className="swatch-mid" />
            <i className="swatch-up" />
          </span>
          <span className="legend-note">顏色越深＝{mode === 'rel' ? '比大盤強弱' : '漲跌'}越大</span>
        </div>

        <div className="themes-grid" role="list" aria-label="題材熱力圖">
          {tiles.map((item) => (
            <div role="listitem" key={item.key}>
              <Tile theme={item} data={data} period={period} mode={mode} onOpen={open} />
            </div>
          ))}
        </div>

        <section className="themes-list" aria-label="題材明細">
          <div className="section-bar">
            <h2>全部題材明細</h2>
            <label className="list-sort">
              <span className="sr-only">排序</span>
              <select
                value={sort.key}
                onChange={(event) => {
                  const key = event.target.value as ListSortKey;
                  setSort({ key, direction: defaultDirection(key) });
                }}
              >
                <option value="rank">動能排名</option>
                {columns.map((column) => (
                  <option key={column.key} value={column.key}>
                    {column.label}
                  </option>
                ))}
                <option value="name">名稱</option>
              </select>
            </label>
          </div>
          <ThemeTable themes={rows} data={data} sort={sort} onSort={onSort} onOpen={open} columns={columns} />
          <ThemeCards themes={rows} label={label} onOpen={open} />
        </section>

        </>
      )}

      <ThemeRotationCard data={data} onOpen={open} />
      <footer className="page-foot">
        <span>資料日 {formatDateTime(data.asOf, false)}{age !== null && age > 6 ? `（已 ${age} 天未更新）` : ''}</span>
        <HelpDisclosure label="資料與報酬備註">
          {industryMap ? (
            <p>產業熱力圖依全市場名單計算，口徑見熱力圖計算說明；每個交易日更新一次。僅供研究參考，非投資建議。</p>
          ) : (
            <p>題材報酬為成分股等權平均，基準 {label}，每個交易日更新一次。題材成分股由人工挑選，僅供研究參考，非投資建議。</p>
          )}
        </HelpDisclosure>
      </footer>

      {selected && (
        <ThemeDetail
          key={selected.key}
          theme={selected}
          data={data}
          initialPeriod={period}
          onClose={close}
          openChart={openChart}
        />
      )}
    </>
  );
}

type ThemesViewKey = 'map';

/** The themes route of a market: Taiwan carries `market`, the US one is the plain page; `map` selects the heatmap view. */
function themesRoute(market: ThemesMarket, view?: ThemesViewKey) {
  return {
    page: 'themes' as const,
    ...(market === 'tw' ? { market } : {}),
    ...(view ? { v: view } : {}),
  };
}

const HELP: Record<ThemesMarket, ReactElement> = {
  us: (
    <p>
      「題材」是把同一個故事的美股放成一籃子，例如「AI晶片」、「核能」。數字是這籃子股票的
      <b>等權平均</b>漲跌幅（每檔占一樣重）。顏色越深代表漲跌越多；切到「相對 SPY」可以看誰跑贏大盤（SPY
      是追蹤標普 500 的 ETF，可當作美股大盤）。點任一方塊會看到白話說明、走勢圖與成分股。
    </p>
  ),
  tw: (
    <p>
      「題材」是把同一個故事的台股放成一籃子，例如「晶圓代工」、「散熱」。數字是這籃子股票的
      <b>等權平均</b>漲跌幅（每檔占一樣重）。顏色越深代表漲跌越多；切到「相對 加權指數」可以看誰跑贏大盤（加權指數是台灣上市股票的整體走勢）。
      點任一方塊會看到白話說明、走勢圖與成分股。
    </p>
  ),
};

const MAP_HELP = (
  <p>
    切到「熱力圖」可以一次看到各產業的冷熱：每個方塊是<b>一個產業</b>（美股外層是 sector、裡面是 industry），顏色是產業在所選期間的報酬，預設以
    <b>市值加權</b>，也可切成每檔一票的<b>等權中位數</b>；方塊大小是產業的市值合計。小產業預設用「均衡」壓縮大小差距並保留最小面積，也可改成「等大小」或原始比例的「市值」。點方塊看該產業的成分股，再點個股開圖表。全市場產業資料還沒產生時，會暫時改用題材分組顯示。
  </p>
);

export default function ThemesPage({
  navigate,
  openChart,
  theme,
  market = 'us',
  view,
}: PageProps & { theme?: string; market?: ThemesMarket; view?: ThemesViewKey }) {
  const { state, reload } = useThemes(market);
  const meta = THEMES_MARKET_META[market];
  return (
    <div className="page themes-page">
      <div className="page-head">
        <div>
          <h2 className="page-title">題材輪動</h2>
          <p className="page-sub">
            {state.status === 'ok'
              ? `${meta.name} ${state.data.themes.length} 個題材 · 資料日 ${formatDateTime(state.data.asOf, false)} · 基準 ${state.data.benchmark.label}`
              : `${meta.name}產業題材 · 看資金現在流向哪裡`}
          </p>
        </div>
      </div>
      <div className="tabs market-tabs themes-market" role="tablist" aria-label="市場">
        {THEMES_MARKETS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={market === key}
            className={market === key ? 'active' : ''}
            onClick={() => market !== key && navigate(themesRoute(key, view))}
          >
            <span aria-hidden="true" className="flag">
              {THEMES_MARKET_META[key].flag}
            </span>
            {THEMES_MARKET_META[key].name}
          </button>
        ))}
      </div>
      <details className="themes-help">
        <summary>這頁怎麼看？</summary>
        {HELP[market]}
        <p>3 月相對強弱表示題材近 3 個月報酬比基準多（少）多少；「站上 50 日線」表示成分股中股價高於 50 日均線的比例。動能綜合 1 週、1 月、3 月報酬與相對基準的分數，用於題材排名。</p>
        {MAP_HELP}
      </details>
      {state.status === 'loading' ? (
        <PageState kind="loading" title="載入題材資料…" />
      ) : state.status === 'ok' ? (
        // Keyed by market so the parent filter and sort order start over when the market changes.
        <ThemesView key={market} data={state.data} theme={theme} view={view} navigate={navigate} openChart={openChart} />
      ) : (
        <PageState
          kind={state.status === 'error' ? 'error' : 'empty'}
          title={state.status === 'error' ? '題材資料暫時無法顯示' : '題材資料尚未產生'}
          message={state.message}
          onRetry={reload}
        />
      )}
    </div>
  );
}

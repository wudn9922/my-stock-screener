import { useCallback, useEffect, useMemo, useRef, useState, type UIEvent } from 'react';
import { ChevronRight, Globe2, Search, SlidersHorizontal, X } from 'lucide-react';
import type { PageProps } from './pageProps';
import { ReportGate } from './ReportGate';
import { itemStatusOf, type GroupItem, type Report, type ReportGroup } from '../report/schema';
import { getPeFigures, type PeFigures } from '../app/dataSources';
import { HelpDisclosure } from '../ui/HelpDisclosure';
import {
  fetchTrendingStocks, LINE_TRENDING_GROUP_KEY, trendingStocksGroup, trendingStocksStale, type TrendingStocksResult,
} from '../report/trendingStocks';
import { mapWithConcurrency } from './worldPerformance';
import {
  METRIC_SORTS,
  SORT_LABELS,
  STATUS_BADGE_LABELS,
  STATUS_VIEWS,
  STATUS_VIEW_LABELS,
  filterAndSortItems,
  gateForPill,
  itemsForView,
  metricPill,
  resultCountText,
  rowMetricPills,
  statusCounts,
  type MetricPill,
  type SortMode,
  type StatusView,
} from './screenerItems';
import {
  accelCoverage,
  activeChips,
  applyFilters,
  filterAvailability,
  gateCoverage,
  streakCoverage,
  needsGrowth,
  parseFilters,
  readStoredFilters,
  removeFilter,
  serializeFilters,
  writeStoredFilters,
  type ChipId,
  type FilterContext,
  type FilterState,
  type FundamentalKind,
  type FixedScreenerMas,
} from './screenerFilters';
import { useScreenerFixedMAs } from './useScreenerFixedMAs';
import {
  UNIVERSE_GROUP_KEYS,
  UNIVERSE_LABELS,
  loadUniverse,
  universeGroup,
  universeMarketOf,
  type UniverseLoadResult,
  type UniverseMarketKey,
} from '../report/universe';
import { loadMarketGrowth, type GrowthFile } from '../fundamentals/GrowthProvider';
import { FilterSheet } from '../ui/FilterSheet';
import {
  displayTicker,
  formatDateTime,
  formatPercent,
  formatPrice,
  formatRatio,
  maDistance,
  maValue,
  toneClass,
} from '../ui/format';
import { serializeRoute } from '../app/routes';
import { getMarketProfile } from '../market-data/MarketProfile';
import { PageState } from '../ui/PageState';
import '../ui/filter-sheet.css';
import '../ui/screener-status.css';
import { EarningsBadge } from '../ui/EarningsBadge';
import { createWatchlist } from '../watchlists/model';
import { watchlistStore } from '../watchlists/store';
import '../ui/research.css';

const ROW_HEIGHT = 64;
const OVERSCAN = 8;
const KIND_LABELS: Record<ReportGroup['kind'], string> = { fixed: '精選', custom: '自訂', scan: '掃描' };
const MARKETS: UniverseMarketKey[] = ['TW', 'US'];
const EMPTY_GROUP_ITEMS: GroupItem[] = [];

type UniverseState = { status: 'idle' } | { status: 'loading' } | UniverseLoadResult;

function GroupChips({
  groups,
  active,
  universeCounts,
  trendingGroup,
  onChange,
}: {
  groups: readonly ReportGroup[];
  active: string;
  universeCounts: Partial<Record<UniverseMarketKey, number>>;
  trendingGroup: ReportGroup;
  onChange: (key: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('[aria-pressed="true"]')?.scrollIntoView?.({ block: 'nearest', inline: 'center' });
  }, [active]);
  return (
    <div className="group-chips" ref={ref} role="group" aria-label="選股群組">
      {MARKETS.map((market) => {
        const list = groups.filter((group) => group.market === market);
        const universeKey = UNIVERSE_GROUP_KEYS[market];
        return (
          <div key={market} className="chip-row">
            <span className="chip-row-label">{market === 'TW' ? '台股' : '美股'}</span>
            <button
              type="button"
              className={`chip universe-chip ${universeKey === active ? 'active' : ''}`}
              aria-pressed={universeKey === active}
              aria-label={UNIVERSE_LABELS[market]}
              title={`${UNIVERSE_LABELS[market]}：用條件篩選整個市場`}
              onClick={() => onChange(universeKey)}
            >
              <Globe2 size={14} aria-hidden="true" />
              全市場
              {universeCounts[market] !== undefined && <span className="chip-count">{universeCounts[market]}</span>}
            </button>
            {market === 'US' && (
              <button type="button" className={`chip ${active === LINE_TRENDING_GROUP_KEY ? 'active' : ''}`}
                aria-pressed={active === LINE_TRENDING_GROUP_KEY} onClick={() => onChange(LINE_TRENDING_GROUP_KEY)}>
                LINE 熱門
                {trendingGroup.items.length > 0 && <span className="chip-count">{trendingGroup.items.length}</span>}
              </button>
            )}
            {list.map((group) => {
              // The chip counts the matched ("成功") rows; the rest is shown as a faint "+N".
              const { matched, unmatched } = statusCounts(group.items);
              return (
              <button
                key={group.key}
                type="button"
                className={`chip ${group.key === active ? 'active' : ''}`}
                aria-pressed={group.key === active}
                onClick={() => onChange(group.key)}
              >
                {group.name.replace(/^(?:🇹🇼|🇺🇸)?\s*(?:台股|美股)\s*[-－]?\s*/u, '')}
                <span className="chip-count">{matched}</span>
                {unmatched > 0 && (
                  <span className="chip-unmatched" title={`另有 ${unmatched} 檔未成功`}>
                    +{unmatched}
                  </span>
                )}
                {group.kind !== 'fixed' && <span className="chip-kind">{KIND_LABELS[group.kind]}</span>}
              </button>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

function Row({
  item,
  pe,
  pills,
  growth,
  filters,
  fixedMAs,
  top,
  onOpen,
  marketDate,
}: {
  item: GroupItem;
  pe: PeFigures | undefined;
  pills: readonly MetricPill[];
  growth: GrowthFile | null | undefined;
  /** The applied conditions: 「含虧轉盈」 (whether a turnaround counts) and the growth gates the pills count against. */
  filters: FilterState;
  fixedMAs?: FixedScreenerMas;
  top: number;
  onOpen: () => void;
  marketDate?: string | null;
}) {
  const tone = toneClass(item.changePct);
  const status = itemStatusOf(item);
  const ticker = <b>{displayTicker(item.symbol)}</b>;
  const metricPills = pills.flatMap((key) => {
    const pill = metricPill(item, key, growth, filters.turn, gateForPill(filters, key), fixedMAs);
    return pill ? [{ key, pill }] : [];
  });
  return (
    <a
      className={status === 'matched' ? 'screener-row' : `screener-row is-${status}`}
      style={{ transform: `translateY(${top}px)` }}
      href={serializeRoute({ page: 'chart', symbol: item.symbol, tf: '1D' })}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        onOpen();
      }}
    >
      <span className="row-id">
        {status === 'matched' ? (
          ticker
        ) : (
          <span className="row-sym">
            {ticker}
            <span className={`status-badge ${status}`} title={status === 'failed' ? '沒有取得行情' : '有行情，但未符合均線條件'}>
              {STATUS_BADGE_LABELS[status]}
            </span>
          </span>
        )}
        <EarningsBadge symbol={item.symbol} />
        <small>{item.name !== item.symbol ? item.name : getMarketProfile(item.symbol).market === 'TW' ? '台股' : '美股'}</small>
        {marketDate && item.asOf !== marketDate && (
          <small className="row-observation-date" title={`市場資料日 ${marketDate}；本列未與其他日期直接比較`}>
            {item.asOf ? `資料日 ${item.asOf}` : '資料日不明'}
          </small>
        )}
      </span>
      <span className={metricPills.length ? 'row-mas has-metrics' : 'row-mas'}>
        {metricPills.map(({ key, pill }) => (
          <span key={key} className={`metric-pill ${pill.tone}`} title={pill.title}>
            {pill.text}
          </span>
        ))}
        {/* A failed row has no close, so its MA pills would only read "—". */}
        {(status === 'failed' ? [] : item.maList).slice(0, 3).map((period) => {
          const distance = maDistance(item.close, maValue(item.maValues, period));
          return (
            <span key={period} className={`ma-pill ${toneClass(distance)}`} title={`收盤相對 MA${period} 的乖離`}>
              MA{period} {distance === null ? '—' : formatPercent(distance, 1)}
            </span>
          );
        })}
        {item.note && <span className="row-note">{item.note}</span>}
      </span>
      <span className="row-pe" title={pe?.reason ?? (pe ? `本益比 ${formatRatio(pe.pe)} · TTM ${formatRatio(pe.peTtm)}` : '')}>
        {pe && (pe.pe !== null || pe.peTtm !== null || pe.peTtmLoss || pe.peLoss) ? (
          <>
            <small>本益比</small>
            {pe.peTtmLoss || (pe.peTtm === null && pe.pe === null) ? (
              <span className="pe-loss">虧損</span>
            ) : (
              formatRatio(pe.peTtm ?? pe.pe)
            )}
          </>
        ) : null}
      </span>
      <span className={`row-quote ${tone}`}>
        <b>{formatPrice(item.close)}</b>
        <span className="change-pill">{formatPercent(item.changePct)}</span>
      </span>
      <ChevronRight size={16} className="row-chevron" aria-hidden="true" />
    </a>
  );
}

/** The screener's `f` parameter, read from the address bar (the site router keeps it there). */
/** Shortest 52-week window among rows that do not have a full year yet (median of the short ones). */
function shortHighWindow(items: readonly GroupItem[]): number | null {
  const bars = items.map((item) => item.metrics?.hiBars ?? 0).filter((n) => n > 0);
  if (!bars.length) return null;
  bars.sort((a, b) => a - b);
  const median = bars[Math.floor(bars.length / 2)]!;
  return median < 240 ? median : null;
}

/** Placeholder while a full-market list loads, or why it cannot be shown. */
function UniverseStatus({ state, market, onRetry }: { state: UniverseState; market: UniverseMarketKey; onRetry: () => void }) {
  if (state.status === 'idle' || state.status === 'loading') return <PageState kind="loading" title="載入全市場資料…" />;
  if (state.status === 'ok')
    return <PageState kind="empty" title={`${UNIVERSE_LABELS[market]}資料尚未產生`} message="每日報告更新後提供。你仍可使用其他群組。" />;
  return (
    <PageState
      kind={state.status === 'missing' ? 'empty' : 'error'}
      title={state.status === 'missing' ? '全市場資料尚未產生' : '全市場資料暫時無法顯示'}
      message={`${state.message}你仍可使用其他群組。`}
      onRetry={onRetry}
    />
  );
}

function useUniverse(market: UniverseMarketKey | null) {
  const [state, setState] = useState<UniverseState>({ status: 'idle' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!market) return;
    let live = true;
    setState((previous) => (previous.status === 'ok' ? previous : { status: 'loading' }));
    void loadUniverse().then((result) => {
      if (live) setState(result);
    });
    return () => {
      live = false;
    };
  }, [market, attempt]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { state, retry };
}

function useTrendingStocks(wanted: boolean) {
  const [state, setState] = useState<TrendingStocksResult>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!wanted) return;
    const abort = new AbortController();
    setState(undefined);
    void fetchTrendingStocks(abort.signal).then(result => {
      if (!abort.signal.aborted) setState(result);
    });
    return () => abort.abort();
  }, [wanted, attempt]);
  return { state, retry: () => setAttempt(n => n + 1) };
}

/** Growth data (EPS / revenue YoY) of one market, loaded once a fundamental condition or sort needs it. */
function useGrowth(wanted: boolean, market: 'TW' | 'US') {
  const [loaded, setLoaded] = useState<{ market: 'TW' | 'US'; file: GrowthFile | null } | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!wanted) return;
    let live = true;
    setLoading(true);
    void loadMarketGrowth(market).then((file) => {
      if (!live) return;
      setLoaded({ market, file });
      setLoading(false);
    });
    return () => {
      live = false;
      setLoading(false);
    };
  }, [wanted, market]);
  // Another market's data is never used for these rows.
  const growth = loaded?.market === market ? loaded.file : undefined;
  return { growth, loading };
}

function ScreenerView({
  report,
  group: requested,
  f: appliedFilter,
  navigate,
  openChart,
}: { report: Report; group?: string; f?: string } & Pick<PageProps, 'navigate' | 'openChart'>) {
  const groups = report.groups;
  const isTrending = requested === LINE_TRENDING_GROUP_KEY;
  const universeMarket = universeMarketOf(requested);
  const sourceMarket = isTrending ? 'US' : universeMarket;
  const { state: universe, retry: retryUniverse } = useUniverse(sourceMarket);
  const universeData = universe.status === 'ok' ? universe.universe : null;
  const sourceMarketData = sourceMarket ? universeData?.markets[sourceMarket] : undefined;
  const universeMarketData = universeMarket ? (universeData?.markets[universeMarket] ?? null) : null;
  const { state: trending, retry: retryTrending } = useTrendingStocks(isTrending);
  const trendingData = trending?.status === 'ok' ? trending.data : undefined;
  const trendingGroup = useMemo(() => trendingStocksGroup(trendingData, universeData?.markets.US), [trendingData, universeData]);
  const group: ReportGroup | undefined = isTrending ? trendingGroup : universeMarket
    ? universeMarketData
      ? universeGroup(universeMarketData)
      : { key: UNIVERSE_GROUP_KEYS[universeMarket], name: UNIVERSE_LABELS[universeMarket], market: universeMarket, kind: 'scan', maList: [20, 50, 200], items: EMPTY_GROUP_ITEMS }
    : (groups.find((g) => g.key === requested) ?? groups[0]);
  const market = group?.market ?? 'TW';

  // Conditions: the URL's `f` first, otherwise the last ones applied for this market.
  const urlF = appliedFilter;
  const filterText = urlF ?? readStoredFilters(market) ?? '';
  const filters = useMemo(() => parseFilters(filterText), [filterText]);
  const routeTo = useCallback(
    (key: string | undefined, text: string, replace: boolean) =>
      navigate({ page: 'screener', ...(key ? { group: key } : {}), ...(text ? { f: text } : {}) }, { replace }),
    [navigate],
  );
  // Restored conditions go into the address bar so the view can be shared as-is.
  useEffect(() => {
    if (urlF === undefined && filterText) routeTo(group?.key ?? requested, filterText, true);
  }, [group?.key, urlF, filterText]);
  const setFilters = (next: FilterState) => {
    const text = serializeFilters(next);
    writeStoredFilters(market, text);
    routeTo(group?.key ?? requested, text, true);
  };

  const [query, setQuery] = useState('');
  const [view, setView] = useState<StatusView>('all');
  const [sort, setSort] = useState<SortMode>('default');
  const [sheetOpen, setSheetOpen] = useState(false);
  const [savingList, setSavingList] = useState(false);
  const [listName, setListName] = useState('');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [noteOpen, setNoteOpen] = useState(false);
  const [draftNeedsGrowth, setDraftNeedsGrowth] = useState(false);
  const [scroll, setScroll] = useState({ top: 0, height: 600 });
  const [pe, setPe] = useState<Record<string, PeFigures>>({});
  const listRef = useRef<HTMLDivElement>(null);
  const filterButtonRef = useRef<HTMLButtonElement>(null);

  const groupItems = group?.items ?? EMPTY_GROUP_ITEMS;
  const groupDates = useMemo(() => [...new Set(groupItems.flatMap(item =>
    item.asOf && /^\d{4}-\d{2}-\d{2}$/.test(item.asOf) ? [item.asOf] : [],
  ))].sort(), [groupItems]);
  // Fixed and custom groups also list their unmatched / failed stocks; full-market lists never do.
  const counts = useMemo(() => statusCounts(groupItems), [groupItems]);
  const hasUnmatched = !universeMarket && counts.unmatched > 0;
  const activeView: StatusView = hasUnmatched ? view : 'all';
  const viewItems = useMemo(() => itemsForView(groupItems, activeView), [groupItems, activeView]);
  const hasMetrics = useMemo(() => groupItems.some((item) => item.metrics), [groupItems]);
  const sortModes = (Object.keys(SORT_LABELS) as SortMode[]).filter(
    (mode) => !METRIC_SORTS.includes(mode) || hasMetrics,
  );
  const activeSort = sortModes.includes(sort) ? sort : 'default';
  const { fixedMAs, maReferenceReason } = useScreenerFixedMAs(filters.ema20 !== null || sheetOpen || activeSort === 'ma', groupItems);
  const hasFixedMAs = !!fixedMAs && Object.keys(fixedMAs).length > 0;
  const wantGrowth = needsGrowth(filters) || activeSort === 'eps' || (sheetOpen && draftNeedsGrowth);
  const { growth, loading: growthLoading } = useGrowth(wantGrowth, market);
  const observationDate = sourceMarketData?.asOf;
  const context = useMemo<FilterContext>(() => ({ market, growth, growthLoading, asOf: observationDate, fixedMAs, maReferenceReason }), [market, growth, growthLoading, observationDate, fixedMAs, maReferenceReason]);
  const availability = useMemo(() => filterAvailability(groupItems, context), [groupItems, context]);
  const chips = useMemo(() => activeChips(filters, availability, market, hasFixedMAs), [filters, availability, market, hasFixedMAs]);
  const filtered = useMemo(() => applyFilters(viewItems, filters, context), [viewItems, filters, context]);
  const items = useMemo(
    () => filterAndSortItems(filtered, query, activeSort, { growth, asOf: observationDate, fixedMAs }),
    [filtered, query, activeSort, growth, observationDate, fixedMAs],
  );
  const countFor = useCallback(
    (draft: FilterState) => filterAndSortItems(applyFilters(viewItems, draft, context), query, 'default').length,
    [viewItems, context, query],
  );
  const coverage = useCallback(
    (kind: FundamentalKind | 'accel' | 'accelY' | 'streak', periods: number, includeTurn: boolean) => ({
      covered:
        kind === 'accel' || kind === 'accelY'
          ? accelCoverage(viewItems, growth, includeTurn, kind === 'accelY' ? 'annual' : 'quarterly')
          : kind === 'streak'
            ? streakCoverage(viewItems, growth, periods, includeTurn)
            : gateCoverage(viewItems, growth, kind, periods, includeTurn),
      total: viewItems.length,
    }),
    [viewItems, growth],
  );
  const pills = useMemo(() => rowMetricPills(activeSort, filters, group?.maList ?? []), [activeSort, filters, group?.maList]);

  useEffect(() => {
    listRef.current?.scrollTo?.({ top: 0 });
    setScroll((previous) => ({ ...previous, top: 0 }));
  }, [group?.key, query, activeSort, filterText, activeView]);
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const measure = () => setScroll((previous) => ({ ...previous, height: list.clientHeight || 600 }));
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(list);
    return () => observer.disconnect();
  }, [group?.key]);
  const start = Math.max(0, Math.floor(scroll.top / ROW_HEIGHT) - OVERSCAN);
  const end = Math.min(items.length, Math.ceil((scroll.top + scroll.height) / ROW_HEIGHT) + OVERSCAN);
  const slice = items.slice(start, end);
  // P/E for the rows on screen only, after scrolling settles.
  const visibleKey = slice.map((item) => item.symbol).join(',');
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      const wanted = slice.filter((item) => !pe[item.symbol] && !item.symbol.startsWith('^') && item.status !== 'failed');
      void mapWithConcurrency(wanted, 3, async (item) => {
        const figures = await getPeFigures(item.symbol, item.close);
        if (live) setPe((previous) => ({ ...previous, [item.symbol]: figures }));
      });
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // `slice`/`pe` are read at the moment the visible set settles.
  }, [visibleKey]);
  const closeSheet = useCallback(() => {
    setSheetOpen(false);
    setDraftNeedsGrowth(false);
  }, []);
  const onNeedGrowth = useCallback(() => setDraftNeedsGrowth(true), []);
  if (!group)
    return (
      <div className="page-state empty" role="status">
        <b>報告沒有選股群組</b>
      </div>
    );
  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    const top = event.currentTarget.scrollTop;
    setScroll((previous) => (Math.abs(previous.top - top) < ROW_HEIGHT / 2 ? previous : { ...previous, top }));
  };
  const changeGroup = (key: string) => {
    const nextMarket = key === LINE_TRENDING_GROUP_KEY ? 'US' : universeMarketOf(key) ?? groups.find((g) => g.key === key)?.market ?? market;
    const text = nextMarket === market ? serializeFilters(filters) : (readStoredFilters(nextMarket) ?? '');
    setView('all');
    routeTo(key, text, false);
  };
  const universeCounts: Partial<Record<UniverseMarketKey, number>> = {};
  for (const key of MARKETS) if (universeData?.markets[key]) universeCounts[key] = universeData.markets[key]!.items.length;
  const universePending = !!sourceMarket && !sourceMarketData;
  const groupPending = universePending || (isTrending && !trendingData);
  const appliedCount = chips.filter((chip) => !chip.unavailable).length;
  const latestReportDay = [report.reportDate, universeData?.reportDate, sourceMarketData?.asOf].filter((date): date is string => !!date).sort().at(-1);

  return (
    <div className="screener-body">
      <GroupChips groups={groups} active={group.key} universeCounts={universeCounts} trendingGroup={trendingGroup} onChange={changeGroup} />
      <div className="screener-tools">
        <label className="filter-field">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="在群組內篩選"
            placeholder={`在「${group.name}」中篩選`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button
          ref={filterButtonRef}
          type="button"
          className={`filter-open-button ${chips.length ? 'active' : ''}`}
          aria-haspopup="dialog"
          aria-expanded={sheetOpen}
          aria-label={chips.length ? `篩選條件（已設定 ${chips.length} 項）` : '篩選條件'}
          disabled={groupPending}
          onClick={() => setSheetOpen(true)}
        >
          <SlidersHorizontal size={15} aria-hidden="true" />
          <span className="filter-open-label">篩選</span>
          {chips.length > 0 && <span className="filter-badge">{chips.length}</span>}
        </button>
        <label className="sort-field">
          <span className="sr-only">排序</span>
          <select aria-label="排序" value={activeSort} onChange={(event) => setSort(event.target.value as SortMode)}>
            {sortModes.map((mode) => (
              <option key={mode} value={mode}>
                {SORT_LABELS[mode]}
              </option>
            ))}
          </select>
        </label>
        <span className="result-count">{resultCountText(items, viewItems.length, activeView, hasUnmatched)}</span>
      </div>
      <div className="save-watchlist">
        <button type="button" className="ghost-button" disabled={groupPending} onClick={() => { setSavingList((previous) => !previous); setListName(group.name); }}>存成自選清單</button>
        <button type="button" className="text-button" onClick={() => navigate({ page: 'watchlist' })}>開啟自選清單</button>
        {savingList && <form className="save-watchlist" onSubmit={(event) => {
          event.preventDefault();
          try {
            const list = createWatchlist({ name: listName, market, group: group.key, filters: serializeFilters(filters), maList: group.maList.length ? group.maList.slice(0, 4) : [20, 50, 200],
              ...(isTrending ? { kind: 'manual', symbols: groupItems.map(item => item.symbol) } : {}) });
            watchlistStore().save(list); navigate({ page: 'watchlist', list: list.id });
          } catch (error) { setSaveError(error instanceof Error ? error.message : '清單儲存失敗'); }
        }}><input aria-label="自選清單名稱" value={listName} onChange={(event) => setListName(event.target.value)} maxLength={40} required /><button type="submit" className="primary-button">儲存並追蹤清單</button></form>}
        {saveError && <span role="alert">{saveError}</span>}
      </div>
      {isTrending && trendingData && <div className="screener-meta line-trending-meta" aria-label="LINE 熱門資料時間">
        <p>抓取時間 {formatDateTime(trendingData.fetchedAt)} · LINE 推播時間 {formatDateTime(trendingData.sentAt)}</p>
        {trendingStocksStale(trendingData, latestReportDay) && <p className="screener-date-notice" role="status">
          LINE 熱門清單早於最新報告日 {latestReportDay}，目前顯示上次成功推播的名單。
        </p>}
      </div>}
      {hasUnmatched && (
        <div className="segmented compact status-seg" role="group" aria-label="成功與未成功股票">
          {STATUS_VIEWS.map((mode) => (
            <button
              key={mode}
              type="button"
              className={mode === activeView ? 'active' : ''}
              aria-pressed={mode === activeView}
              onClick={() => setView(mode)}
            >
              {STATUS_VIEW_LABELS[mode]}
              <span className="seg-count">
                {mode === 'all' ? groupItems.length : mode === 'matched' ? counts.matched : counts.unmatched}
              </span>
            </button>
          ))}
        </div>
      )}
      {chips.length > 0 && (
        <div className="active-filters" role="group" aria-label="已套用的篩選條件">
          <span className="active-filters-count" aria-live="polite">
            符合 <b>{items.length.toLocaleString('en-US')}</b> / {viewItems.length.toLocaleString('en-US')}
          </span>
          {chips.map((chip) => (
            <button
              key={chip.id}
              type="button"
              className={`filter-chip ${chip.unavailable ? 'unavailable' : ''}`}
              title={chip.unavailable ?? '移除這個條件'}
              aria-label={`移除條件：${chip.label}${chip.unavailable ? `（未套用：${chip.unavailable}）` : ''}`}
              onClick={() => setFilters(removeFilter(filters, chip.id as ChipId))}
            >
              {chip.label}
              {chip.unavailable && <span className="chip-off">未套用</span>}
              <X size={13} aria-hidden="true" />
            </button>
          ))}
          {chips.length > 1 && (
            <button type="button" className="text-button" onClick={() => setFilters(parseFilters(''))}>
              全部清除
            </button>
          )}
        </div>
      )}
      {universeMarketData && (
        <p className="screener-meta" title={universeMarketData.universe}>
          {universeMarketData.items.length.toLocaleString('en-US')} 檔
          {universeMarketData.asOf ? ` · 資料日 ${formatDateTime(universeMarketData.asOf, false)}` : ''} · 相對強弱基準{' '}
          {universeMarketData.benchmark === '^TWII' ? '加權指數' : universeMarketData.benchmark} · {universeMarketData.universe}
        </p>
      )}
      {!universeMarketData && groupDates.length > 0 && (
        <p className="screener-meta">
          資料日 {formatDateTime(groupDates[0]!, false)}
          {groupDates.length > 1 ? ` ～ ${formatDateTime(groupDates.at(-1)!, false)}` : ''}
        </p>
      )}
      {observationDate && groupItems.some(item => item.asOf !== observationDate) && (
        <p className="screener-date-notice" role="status">
          不同資料日分組排序；成交量、量比與 RS 條件只比較 {observationDate}，各列另標示不同日期。
        </p>
      )}
      {universeMarketData?.note && (
        <button
          type="button"
          className={`screener-note ${noteOpen ? 'open' : ''}`}
          aria-expanded={noteOpen}
          aria-label={`名單範圍說明：${universeMarketData.note}`}
          onClick={() => setNoteOpen((open) => !open)}
        >
          名單範圍說明{noteOpen && <span>{universeMarketData.note}</span>}
        </button>
      )}
      <div className="screener-head" aria-hidden="true">
        <span>代號／名稱</span>
        <span>{pills.length ? '指標・均線乖離' : '均線乖離・備註'}</span>
        <span className="num">本益比 TTM</span>
        <span className="num">收盤／漲跌</span>
        <span />
      </div>
      {isTrending && !trendingData ? (
        <PageState kind={trending ? 'empty' : 'loading'} title={trending?.status === 'error' ? trending.message : '載入 LINE 熱門美股…'}
          onRetry={trending ? retryTrending : undefined} />
      ) : universePending ? (
        <UniverseStatus state={universe} market={sourceMarket!} onRetry={retryUniverse} />
      ) : (
        <div className="screener-list card" ref={listRef} onScroll={onScroll} role="list" aria-label={`${group.name}股票清單`}>
          {items.length ? (
            <div className="screener-rows" style={{ height: items.length * ROW_HEIGHT }}>
              {slice.map((item, offset) => (
                <div role="listitem" key={item.symbol}>
                  <Row
                    item={item}
                    marketDate={observationDate}
                    pe={pe[item.symbol]}
                    pills={pills}
                    growth={growth}
                    filters={filters}
                    fixedMAs={fixedMAs}
                    top={(start + offset) * ROW_HEIGHT}
                    onOpen={() => openChart({ symbol: item.symbol, name: item.name, tf: '1D', maList: item.maList })}
                  />
                </div>
              ))}
            </div>
          ) : (
            <div className="list-empty">
              {query ? (
                `沒有符合「${query}」的股票`
              ) : appliedCount ? (
                <>
                  沒有符合篩選條件的股票
                  <br />
                  <button type="button" className="text-button" onClick={() => setSheetOpen(true)}>
                    調整條件
                  </button>
                </>
              ) : (
                `此群組今日沒有${activeView === 'matched' ? '成功' : activeView === 'unmatched' ? '未成功' : ''}股票`
              )}
            </div>
          )}
        </div>
      )}
      <FilterSheet
        open={sheetOpen}
        market={market}
        groupName={group.name}
        hasFixedMAs={hasFixedMAs}
        value={filters}
        availability={availability}
        countFor={countFor}
        growth={growth}
        growthLoading={growthLoading}
        coverage={coverage}
        onNeedGrowth={onNeedGrowth}
        shortHighWindow={shortHighWindow(groupItems)}
        onApply={(next) => {
          setFilters(next);
          closeSheet();
        }}
        onClose={closeSheet}
      />
    </div>
  );
}

export default function ScreenerPage({ report, reload, navigate, openChart, group, f }: PageProps & { group?: string; f?: string }) {
  return (
    <div className="page screener-page">
      <div className="page-head">
        <div>
          <h2 className="page-title">選股</h2>
          {report.status === 'ok' && (
            <HelpDisclosure label="選股說明">每日報告的群組與全市場條件篩選；點選股票可開啟圖表。</HelpDisclosure>
          )}
        </div>
      </div>
      <ReportGate report={report} reload={reload}>
        {(data) => <ScreenerView report={data} group={group} f={f} navigate={navigate} openChart={openChart} />}
      </ReportGate>
    </div>
  );
}

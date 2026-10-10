import { useCallback, useEffect, useMemo, useRef, useState, type UIEvent } from 'react';
import { ChevronRight, Globe2, Search, SlidersHorizontal, X } from 'lucide-react';
import type { PageProps } from './pageProps';
import { ReportGate } from './ReportGate';
import type { GroupItem, Report, ReportGroup } from '../report/schema';
import { getPeFigures, type PeFigures } from '../app/dataSources';
import { mapWithConcurrency } from './worldPerformance';
import {
  METRIC_SORTS,
  SORT_LABELS,
  filterAndSortItems,
  metricPill,
  rowMetricPills,
  type MetricPill,
  type SortMode,
} from './screenerItems';
import {
  activeChips,
  applyFilters,
  filterAvailability,
  needsGrowth,
  parseFilters,
  readStoredFilters,
  removeFilter,
  serializeFilters,
  writeStoredFilters,
  type ChipId,
  type FilterContext,
  type FilterState,
} from './screenerFilters';
import {
  UNIVERSE_GROUP_KEYS,
  UNIVERSE_LABELS,
  loadUniverse,
  universeGroup,
  universeMarketOf,
  type UniverseLoadResult,
  type UniverseMarketKey,
} from '../report/universe';
import { loadGrowth, type GrowthFile } from '../fundamentals/GrowthProvider';
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
import { parseRoute, serializeRoute } from '../app/routes';
import { getMarketProfile } from '../market-data/MarketProfile';
import { PageState } from '../ui/PageState';
import '../ui/filter-sheet.css';

const ROW_HEIGHT = 64;
const OVERSCAN = 8;
const KIND_LABELS: Record<ReportGroup['kind'], string> = { fixed: '精選', custom: '自訂', scan: '掃描' };
const MARKETS: UniverseMarketKey[] = ['TW', 'US'];

type UniverseState = { status: 'idle' } | { status: 'loading' } | UniverseLoadResult;

function GroupChips({
  groups,
  active,
  universeCounts,
  onChange,
}: {
  groups: readonly ReportGroup[];
  active: string;
  universeCounts: Partial<Record<UniverseMarketKey, number>>;
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
            {list.map((group) => (
              <button
                key={group.key}
                type="button"
                className={`chip ${group.key === active ? 'active' : ''}`}
                aria-pressed={group.key === active}
                onClick={() => onChange(group.key)}
              >
                {group.name.replace(/^(?:🇹🇼|🇺🇸)?\s*(?:台股|美股)\s*[-－]?\s*/u, '')}
                <span className="chip-count">{group.items.length}</span>
                {group.kind !== 'fixed' && <span className="chip-kind">{KIND_LABELS[group.kind]}</span>}
              </button>
            ))}
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
  top,
  onOpen,
}: {
  item: GroupItem;
  pe: PeFigures | undefined;
  pills: readonly MetricPill[];
  growth: GrowthFile | null | undefined;
  top: number;
  onOpen: () => void;
}) {
  const tone = toneClass(item.changePct);
  return (
    <a
      className="screener-row"
      style={{ transform: `translateY(${top}px)` }}
      href={serializeRoute({ page: 'chart', symbol: item.symbol, tf: '1D' })}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        onOpen();
      }}
    >
      <span className="row-id">
        <b>{displayTicker(item.symbol)}</b>
        <small>{item.name !== item.symbol ? item.name : getMarketProfile(item.symbol).market === 'TW' ? '台股' : '美股'}</small>
      </span>
      <span className="row-mas">
        {pills.map((key) => {
          const pill = metricPill(item, key, growth);
          return pill ? (
            <span key={key} className={`metric-pill ${pill.tone}`} title={pill.title}>
              {pill.text}
            </span>
          ) : null;
        })}
        {item.maList.slice(0, 3).map((period) => {
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
function urlFilterParam(): string | undefined {
  if (typeof window === 'undefined') return undefined;
  const { route } = parseRoute(window.location.search, 'screener');
  return route.page === 'screener' ? route.f : undefined;
}

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

function useGrowth(wanted: boolean) {
  const [growth, setGrowth] = useState<GrowthFile | null | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!wanted) return;
    let live = true;
    setLoading(true);
    void loadGrowth().then((file) => {
      if (!live) return;
      setGrowth(file);
      setLoading(false);
    });
    return () => {
      live = false;
      setLoading(false);
    };
  }, [wanted]);
  return { growth, loading };
}

function ScreenerView({
  report,
  group: requested,
  navigate,
  openChart,
}: { report: Report; group?: string } & Pick<PageProps, 'navigate' | 'openChart'>) {
  const groups = report.groups;
  const universeMarket = universeMarketOf(requested);
  const { state: universe, retry: retryUniverse } = useUniverse(universeMarket);
  const universeData = universe.status === 'ok' ? universe.universe : null;
  const universeMarketData = universeMarket ? (universeData?.markets[universeMarket] ?? null) : null;
  const group: ReportGroup | undefined = universeMarket
    ? universeMarketData
      ? universeGroup(universeMarketData)
      : { key: UNIVERSE_GROUP_KEYS[universeMarket], name: UNIVERSE_LABELS[universeMarket], market: universeMarket, kind: 'scan', maList: [20, 50, 200], items: [] }
    : (groups.find((g) => g.key === requested) ?? groups[0]);
  const market = group?.market ?? 'TW';

  // Conditions: the URL's `f` first, otherwise the last ones applied for this market.
  const urlF = urlFilterParam();
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
  const [sort, setSort] = useState<SortMode>('default');
  const [sheetOpen, setSheetOpen] = useState(false);
  const [draftNeedsGrowth, setDraftNeedsGrowth] = useState(false);
  const [scroll, setScroll] = useState({ top: 0, height: 600 });
  const [pe, setPe] = useState<Record<string, PeFigures>>({});
  const listRef = useRef<HTMLDivElement>(null);
  const filterButtonRef = useRef<HTMLButtonElement>(null);

  const groupItems = group?.items ?? [];
  const hasMetrics = useMemo(() => groupItems.some((item) => item.metrics), [groupItems]);
  const sortModes = (Object.keys(SORT_LABELS) as SortMode[]).filter(
    (mode) => (mode !== 'eps' || market === 'US') && (!METRIC_SORTS.includes(mode) || hasMetrics),
  );
  const activeSort = sortModes.includes(sort) ? sort : 'default';
  const wantGrowth = market === 'US' && (needsGrowth(filters) || activeSort === 'eps' || (sheetOpen && draftNeedsGrowth));
  const { growth, loading: growthLoading } = useGrowth(wantGrowth);
  const context = useMemo<FilterContext>(() => ({ market, growth, growthLoading }), [market, growth, growthLoading]);
  const availability = useMemo(() => filterAvailability(groupItems, context), [groupItems, context]);
  const chips = useMemo(() => activeChips(filters, availability), [filters, availability]);
  const filtered = useMemo(() => applyFilters(groupItems, filters, context), [groupItems, filters, context]);
  const items = useMemo(
    () => filterAndSortItems(filtered, query, activeSort, { growth }),
    [filtered, query, activeSort, growth],
  );
  const countFor = useCallback(
    (draft: FilterState) => filterAndSortItems(applyFilters(groupItems, draft, context), query, 'default').length,
    [groupItems, context, query],
  );
  const pills = useMemo(() => rowMetricPills(activeSort, filters, group?.maList ?? []), [activeSort, filters, group?.maList]);

  useEffect(() => {
    listRef.current?.scrollTo?.({ top: 0 });
    setScroll((previous) => ({ ...previous, top: 0 }));
  }, [group?.key, query, activeSort, filterText]);
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
      const wanted = slice.filter((item) => !pe[item.symbol] && !item.symbol.startsWith('^'));
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
    const nextMarket = universeMarketOf(key) ?? groups.find((g) => g.key === key)?.market ?? market;
    const text = nextMarket === market ? serializeFilters(filters) : (readStoredFilters(nextMarket) ?? '');
    routeTo(key, text, false);
  };
  const universeCounts: Partial<Record<UniverseMarketKey, number>> = {};
  for (const key of MARKETS) if (universeData?.markets[key]) universeCounts[key] = universeData.markets[key]!.items.length;
  const universePending = !!universeMarket && !universeMarketData;
  const appliedCount = chips.filter((chip) => !chip.unavailable).length;

  return (
    <div className="screener-body">
      <GroupChips groups={groups} active={group.key} universeCounts={universeCounts} onChange={changeGroup} />
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
          disabled={universePending}
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
        <span className="result-count">
          {items.length === groupItems.length ? `${items.length} 檔` : `${items.length} / ${groupItems.length} 檔`}
        </span>
      </div>
      {chips.length > 0 && (
        <div className="active-filters" role="group" aria-label="已套用的篩選條件">
          <span className="active-filters-count" aria-live="polite">
            符合 <b>{items.length.toLocaleString('en-US')}</b> / {groupItems.length.toLocaleString('en-US')}
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
      <div className="screener-head" aria-hidden="true">
        <span>代號／名稱</span>
        <span>{pills.length ? '指標・均線乖離' : '均線乖離・備註'}</span>
        <span className="num">本益比 TTM</span>
        <span className="num">收盤／漲跌</span>
        <span />
      </div>
      {universePending ? (
        <UniverseStatus state={universe} market={universeMarket!} onRetry={retryUniverse} />
      ) : (
        <div className="screener-list card" ref={listRef} onScroll={onScroll} role="list" aria-label={`${group.name}股票清單`}>
          {items.length ? (
            <div className="screener-rows" style={{ height: items.length * ROW_HEIGHT }}>
              {slice.map((item, offset) => (
                <div role="listitem" key={item.symbol}>
                  <Row
                    item={item}
                    pe={pe[item.symbol]}
                    pills={pills}
                    growth={growth}
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
                '此群組今日沒有股票'
              )}
            </div>
          )}
        </div>
      )}
      <FilterSheet
        open={sheetOpen}
        market={market}
        groupName={group.name}
        value={filters}
        availability={availability}
        countFor={countFor}
        growth={growth}
        growthLoading={growthLoading}
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

export default function ScreenerPage({ report, reload, navigate, openChart, group }: PageProps & { group?: string }) {
  return (
    <div className="page screener-page">
      <div className="page-head">
        <div>
          <h2 className="page-title">選股</h2>
          {report.status === 'ok' && (
            <p className="page-sub">每日報告的群組與全市場條件篩選 · 點選股票開啟圖表並套用群組均線</p>
          )}
        </div>
      </div>
      <ReportGate report={report} reload={reload}>
        {(data) => <ScreenerView report={data} group={group} navigate={navigate} openChart={openChart} />}
      </ReportGate>
    </div>
  );
}

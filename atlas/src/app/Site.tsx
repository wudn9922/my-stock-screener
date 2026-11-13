import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { ChartCandlestick, LayoutDashboard, ListFilter, Search, Settings, Star, UserRound, X } from 'lucide-react';
import { useRoute } from './useRoute';
import type { Page, Route } from './routes';
import { SCREENER_HOSTING } from './HostingMode';
import { useReport } from '../report/useReport';
import type { Report } from '../report/schema';
import { SymbolSearch, type LocalSymbol, type SearchResult } from '../ui/SymbolSearch';
import { applyColorConvention, sitePreferences } from './sitePreferences';
import { formatDateTime } from '../ui/format';
import { PageState } from '../ui/PageState';
import type { ChartRequest } from './chartRequest';
import type { StockView } from '../ui/StockTabs';
import type { Timeframe } from '../market-data/MarketDataProvider';
import './siteNav.css';
import { DataHealthPanel } from '../ui/DataHealthPanel';
import { StorageNotice } from '../auth/StorageNotice';
import { StorageModeNotice } from '../ui/StorageAdminPanel';
import { LegacyImportCard } from '../ui/LegacyImportCard';
import { RouteBoundary } from './RouteBoundary';
import { DisplaySettings } from '../ui/DisplaySettings';
import { MarketSectionNav, marketSectionOf } from '../ui/MarketSectionNav';
import { WORLD_ASSETS, worldAssetFor } from '../market-data/worldAssets';
import { KeptPage, scrollKeptPageToTop } from './KeptPage';
import {
  forgetDestination, INITIAL_ROUTE_MEMORY, isKeptPage, KEPT_PAGES, keptPagesOf, navDefault, navDestination, navPageOf,
  rememberRoute, routeIsDefault, scrollIdentity, scrollSection, type KeptPage as KeptPageName, type NavPage,
} from './routeMemory';
import { chartListFor, listPosition, listState, rememberChartList, type ChartListContext, type ChartListItem } from './listContext';
import { ListNavBar } from '../ui/ListNavBar';

const MarketsPage = lazy(() => import('../pages/MarketsPage'));
const WorldPage = lazy(() => import('../pages/WorldPage'));
const ThemesPage = lazy(() => import('../pages/ThemesPage'));
const ScreenerPage = lazy(() => import('../pages/ScreenerPage'));
// Not in the bottom bar; opened from the chart header (financials of the chart's symbol).
const FinancialsPage = lazy(() => import('../pages/FinancialsPage'));
// The workspace (engine, drawings, panels) loads only when the chart opens.
const ChartPage = lazy(() => import('./ChartPage'));
const ConsolePage = lazy(() => import('../pages/ConsolePage'));

/** The report pages exist only where the daily report is published (the screener site). */
const DEFAULT_PAGE: Page =
  import.meta.env?.VITE_DEFAULT_PAGE === 'chart' || (!SCREENER_HOSTING && import.meta.env?.VITE_DEFAULT_PAGE !== 'markets')
    ? 'chart'
    : import.meta.env?.VITE_DEFAULT_PAGE === 'markets' ? 'markets' : 'watchlist';

const NAV: { page: NavPage; label: string; icon: typeof Star }[] = [
  { page: 'watchlist', label: '自選', icon: Star },
  { page: 'markets', label: '市場', icon: LayoutDashboard },
  { page: 'screener', label: '選股', icon: ListFilter },
  { page: 'chart', label: '個股', icon: ChartCandlestick },
  { page: 'console', label: '我的', icon: UserRound },
];
const PAGE_TITLES: Record<Page, string> = {
  markets: '市場',
  world: '世界指數',
  themes: '題材輪動',
  screener: '選股',
  chart: '圖表',
  financials: '財報分析',
  watchlist: '自選',
  console: '我的',
};
const RATES_TITLE = '美債與利率';

const pageTitleOf = (route: Route) => route.page === 'world'
  ? route.view === 'jp-bonds' ? '日本國債利差' : route.view === 'commodities' ? '原物料' : route.view === 'fx' ? '匯率' : route.view === 'indices' ? PAGE_TITLES.world : RATES_TITLE
  : PAGE_TITLES[route.page];

const mobileQuery = window.matchMedia('(max-width: 1099px)');
const subscribeMobile = (callback: () => void) => {
  mobileQuery.addEventListener('change', callback);
  return () => mobileQuery.removeEventListener('change', callback);
};

export interface OpenChartOptions {
  symbol: string;
  name?: string;
  tf?: Timeframe;
  /** Report metadata only; chart indicators are always chosen by the user. */
  maList?: readonly number[];
  /**
   * The full ordered list the stock was opened from (as displayed), so the chart offers previous/next. Kept in memory
   * for this navigation only; search opens and reloads have none.
   */
  list?: ChartListContext;
}

function reportSymbols(report: Report | null): LocalSymbol[] {
  if (!report) return [];
  const map = new Map<string, string>();
  for (const market of report.markets) for (const index of market.indices) map.set(index.symbol, index.name);
  for (const index of report.worldIndices) if (!map.has(index.symbol)) map.set(index.symbol, index.name);
  for (const group of report.groups)
    for (const item of group.items) if (item.status !== 'failed' && !map.has(item.symbol)) map.set(item.symbol, item.name);
  return [...map].map(([symbol, name]) => ({ symbol, name }));
}

export function Site() {
  const mobile = useSyncExternalStore(subscribeMobile, () => mobileQuery.matches);
  const { route, id: navId, source: navSource, state: historyState, navigate, syncRoute } = useRoute(DEFAULT_PAGE);
  // Each main page remembers the route it was left at (screener group/filters, watchlist, 市場 section); the bottom
  // bar reopens it. 市場 also remembers every section as it was left.
  const routeMemory = useRef(INITIAL_ROUTE_MEMORY);
  routeMemory.current = rememberRoute(routeMemory.current, route);
  const marketSection = marketSectionOf(route);
  // Main pages stay mounted (detached from the document) after their first visit; a reset remounts one.
  const visited = useRef(new Set<KeptPageName>());
  const visits = useRef(new Map<KeptPageName, number>());
  const lastPage = useRef<Page | null>(null);
  if (isKeptPage(route.page)) {
    visited.current.add(route.page);
    if (lastPage.current !== route.page) visits.current.set(route.page, (visits.current.get(route.page) ?? 0) + 1);
  }
  lastPage.current = route.page;
  const [generations, setGenerations] = useState<Partial<Record<KeptPageName, number>>>({});

  // Builds without a published report (upstream Atlas) only fetch it when a report page opens.
  const { state: reportState, reload } = useReport(SCREENER_HOSTING || route.page !== 'chart');
  const report = reportState.status === 'ok' ? reportState.report : null;
  const [searchOpen, setSearchOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const preferences = useSyncExternalStore(sitePreferences.subscribe, sitePreferences.get);
  const [chartRequest, setChartRequest] = useState<ChartRequest | null>(null);
  // 「K線｜價量｜研究」 view of the chart page. Opening a stock shows K線 unless the financials tabs asked for a view.
  const [stockView, setStockView] = useState<StockView>('chart');
  const pendingView = useRef<StockView | null>(null);
  const chartVisited = useRef(route.page === 'chart');
  if (route.page === 'chart') chartVisited.current = true;
  const localSymbols = useMemo(() => [...reportSymbols(report), ...WORLD_ASSETS.map(asset => ({ symbol: asset.symbol, name: `${asset.ticker} ${asset.name}` }))], [report]);

  useEffect(() => applyColorConvention(preferences.colors), [preferences.colors]);
  // A navigation that names a symbol becomes one chart request (URL syncs never repeat it).
  useEffect(() => {
    if (route.page !== 'chart' || !route.symbol) return;
    setChartRequest({ id: navId, symbol: route.symbol, timeframe: route.tf, source: navSource });
    setStockView(pendingView.current ?? 'chart');
    pendingView.current = null;
    // Only a new navigation id issues a request; the route is read at that moment.
  }, [navId]);
  // The shell scrolls inside <main>. Returning to a kept page puts back its scroll (same view only); any other page,
  // and another market/group/view/symbol within a page, starts at the top.
  const pageScrolls = useRef(new Map<Page, { identity: string; top: number }>());
  const scrolled = useRef<{ page: Page; section: string; identity: string } | null>(null);
  useEffect(() => {
    const main = document.getElementById('main');
    if (!main) return;
    const record = () => {
      const current = scrolled.current;
      if (current) pageScrolls.current.set(current.page, { identity: current.identity, top: main.scrollTop });
    };
    main.addEventListener('scroll', record, { passive: true });
    return () => main.removeEventListener('scroll', record);
  }, []);
  const section = scrollSection(route);
  const identity = scrollIdentity(route);
  useLayoutEffect(() => {
    const previous = scrolled.current;
    scrolled.current = { page: route.page, section, identity };
    if (previous && previous.page === route.page && previous.section === section) return;
    const saved = previous && previous.page !== route.page && isKeptPage(route.page) ? pageScrolls.current.get(route.page) : undefined;
    document.getElementById('main')?.scrollTo?.({ top: saved?.identity === identity ? saved.top : 0 });
  }, [route.page, section, identity]);
  useEffect(() => {
    const titles =
      route.page === 'chart' && route.symbol
        ? `${worldAssetFor(route.symbol)?.ticker ?? route.symbol} · 圖表`
        : route.page === 'financials' && route.symbol
          ? `${route.symbol} · 財報`
          : pageTitleOf(route);
    document.title = `${titles} — Atlas 股市報告`;
  }, [route]);
  // `/` focuses the desktop search box.
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key !== '/' || target?.closest('input,textarea,select,[contenteditable]')) return;
      event.preventDefault();
      if (mobile) setSearchOpen(true);
      else document.querySelector<HTMLInputElement>('.site-topbar .search-field input')?.focus();
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [mobile]);

  const openChart = useCallback(
    (options: OpenChartOptions) => {
      setSearchOpen(false);
      navigate({ page: 'chart', symbol: options.symbol, ...(options.tf ? { tf: options.tf } : {}) },
        { state: options.list ? rememberChartList(options.list) : null });
    },
    [navigate],
  );
  // The list a chart was opened from follows its chart/financials entries (history state, this page load only).
  const onStockPage = route.page === 'chart' || route.page === 'financials';
  const chartList = onStockPage ? chartListFor(historyState) : null;
  const stockSymbol = onStockPage ? route.symbol : undefined;
  const position = useMemo(() => listPosition(chartList, stockSymbol), [chartList, stockSymbol]);
  /** Chart/financials navigations keep the list reference; any other destination drops it. */
  const navigateStock = useCallback(
    (next: Route, options: { replace?: boolean } = {}) =>
      navigate(next, { ...options, state: next.page === 'chart' || next.page === 'financials' ? listState(window.history.state) : null }),
    [navigate],
  );
  const onSearchSelect = useCallback(
    (result: SearchResult) => openChart({ symbol: result.symbol, name: result.name }),
    [openChart],
  );
  // 「K線｜財務」 round trips keep the annual/quarterly choice of the financials page. It is remembered here
  // (the chart URL stays as it was); the financials URL itself carries `p`.
  const periodMemory = useRef<'q' | 'a' | undefined>(undefined);
  useEffect(() => {
    if (route.page === 'financials') periodMemory.current = route.p;
  }, [route]);
  const onChartState = useCallback(
    (symbol: string, tf: Timeframe) => {
      syncRoute({ page: 'chart', symbol, tf });
    },
    [syncRoute],
  );
  const openFinancials = useCallback(
    (symbol: string, tf?: Timeframe) =>
      navigateStock({ page: 'financials', symbol, ...(tf ? { tf } : {}), ...(periodMemory.current ? { p: periodMemory.current } : {}) }),
    [navigateStock],
  );
  // Previous/next in the list: same timeframe, same K線／價量／研究 view (or the financials page); the entry is
  // replaced so Back still returns to the list.
  const stepList = useCallback((item: ChartListItem) => {
    const state = listState(window.history.state);
    if (route.page === 'financials')
      navigate({ page: 'financials', symbol: item.symbol, ...(route.tf ? { tf: route.tf } : {}), ...(route.p ? { p: route.p } : {}) }, { replace: true, state });
    else {
      pendingView.current = stockView;
      navigate({ page: 'chart', symbol: item.symbol, ...(route.page === 'chart' && route.tf ? { tf: route.tf } : {}) }, { replace: true, state });
    }
  }, [navigate, route, stockView]);
  // `[` / `]` step through the list, except while typing.
  useEffect(() => {
    if (!position) return;
    const key = (event: KeyboardEvent) => {
      if ((event.key !== '[' && event.key !== ']') || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])')) return;
      const item = event.key === '[' ? position.previous : position.next;
      if (!item) return;
      event.preventDefault();
      stepList(item);
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [position, stepList]);
  const listNav = position && chartList ? <ListNavBar name={chartList.name} position={position} onStep={stepList} /> : null;

  /**
   * Tapping the current destination again: a page with a non-default route (filters, a group, an open list, another
   * 市場 section) returns to its default and forgets its temporary view; otherwise it only scrolls to the top.
   * Saved data (watchlists, strategies, cloud settings) is never touched.
   */
  const resetDestination = (nav: NavPage) => {
    const main = document.getElementById('main');
    if (nav === 'chart' || !isKeptPage(route.page) || routeIsDefault(route)) {
      if (isKeptPage(route.page)) scrollKeptPageToTop(route.page);
      main?.scrollTo?.({ top: 0 });
      return;
    }
    // The default page remounts fresh; the destination's other pages (other 市場 sections) unmount until revisited.
    const target = navDefault(nav);
    const fresh = target.page as KeptPageName;
    routeMemory.current = forgetDestination(routeMemory.current, nav);
    for (const page of keptPagesOf(nav)) {
      pageScrolls.current.delete(page);
      if (page !== fresh) visited.current.delete(page);
    }
    setGenerations((previous) => ({ ...previous, [fresh]: (previous[fresh] ?? 0) + 1 }));
    main?.scrollTo?.({ top: 0 });
    navigate(target, { replace: true });
  };
  const go = (nav: NavPage) => {
    setSearchOpen(false);
    if (navPageOf(route.page) === nav && (nav !== 'chart' || route.page === 'chart')) {
      resetDestination(nav);
      return;
    }
    // Back from financials to the chart of the same symbol, with the timeframe it was opened with.
    if (nav === 'chart')
      navigateStock(route.page === 'financials' && route.symbol ? { page: 'chart', symbol: route.symbol, ...(route.tf ? { tf: route.tf } : {}) } : { page: 'chart' });
    else navigate(navDestination(routeMemory.current, nav));
  };
  const reportDate = report?.reportDate ?? null;
  const pageProps = { report: reportState, reload, navigate, openChart };
  const pageFallback = <PageState kind="loading" title="載入中…" />;
  const keptContent = (shown: Route): ReactNode => {
    switch (shown.page) {
      case 'markets': return <MarketsPage {...pageProps} market={shown.market} />;
      case 'world': return <WorldPage {...pageProps} view={shown.view} />;
      case 'themes': return <ThemesPage {...pageProps} theme={shown.theme} market={shown.market} view={shown.v} />;
      case 'screener': return <ScreenerPage {...pageProps} group={shown.group} f={shown.f} />;
      case 'watchlist': return <ConsolePage {...pageProps} section="lists" list={shown.list} />;
      case 'console': return <ConsolePage {...pageProps} section="settings" />;
      default: return null;
    }
  };

  return (
    <div className={`site ${route.page === 'chart' ? 'on-chart' : ''}`} data-page={route.page}>
      <header className="site-topbar">
        <a
          className="site-brand"
          href="?page=watchlist"
          onClick={(event) => {
            event.preventDefault();
            navigate({ page: 'watchlist' });
          }}
          aria-label="Atlas 股市報告首頁"
        >
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="18" height="18">
              <path d="M3 17l5-6 4 4 4-7 5 9" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <b>Atlas</b>
          <span className="brand-sub">股市報告</span>
        </a>
        <h1 className="site-page-title">{pageTitleOf(route)}</h1>
        {!mobile && (
          <div className="site-search">
            <SymbolSearch mode="dropdown" localSymbols={localSymbols} onSelect={onSearchSelect} />
          </div>
        )}
        <div className="site-topbar-end">
          {mobile && <button type="button" className="icon-button site-mobile-search" aria-label="搜尋股票" title="搜尋股票" onClick={() => setSearchOpen(true)}>
            <Search size={21} aria-hidden="true" />
          </button>}
          {reportDate && (
            <span className="report-date" title={report?.generatedAt ? `產生時間 ${formatDateTime(report.generatedAt)}` : undefined}>
              報告日 {formatDateTime(reportDate, false)}
            </span>
          )}
          <button type="button" className="icon-button" aria-label="顯示設定" title="顯示設定" onClick={() => setSettingsOpen(true)}>
            <Settings size={18} />
          </button>
        </div>
      </header>
      <nav className="site-rail" aria-label="主要頁面">
        {NAV.map(({ page, label, icon: Icon }) => (
          <button
            key={page}
            type="button"
            className={navPageOf(route.page) === page ? 'active' : ''}
            aria-current={navPageOf(route.page) === page ? 'page' : undefined}
            onClick={() => go(page)}
          >
            <Icon size={20} aria-hidden="true" />
            <span>{label}</span>
          </button>
        ))}
      </nav>
      <main className="site-main" id="main">
        {/* Full account controls on 我的; elsewhere only an in-progress login or handoff shows. */}
        <div className="site-account-flow">{SCREENER_HOSTING && <StorageNotice variant={route.page === 'console' ? 'full' : 'alerts'} />}</div>
        {(route.page === 'markets' || route.page === 'world' || route.page === 'themes') &&
          <MarketSectionNav active={marketSection!} routes={routeMemory.current.sections} navigate={navigate} />}
        {KEPT_PAGES.filter((page) => visited.current.has(page)).map((page) => {
          const shown = routeMemory.current.pages[page];
          return shown && (
            <KeptPage key={`${page}:${generations[page] ?? 0}`} page={page} active={route.page === page}>
              <RouteBoundary resetKey={visits.current.get(page)}>
                <Suspense fallback={pageFallback}>{keptContent(shown)}</Suspense>
              </RouteBoundary>
            </KeptPage>
          );
        })}
        {route.page === 'financials' && (
          <RouteBoundary key={route.page}>
            {listNav && <div className="financials-list-nav">{listNav}</div>}
            <Suspense fallback={pageFallback}>
              <FinancialsPage {...pageProps} navigate={navigateStock} symbol={route.symbol} p={route.p} tf={route.tf}
                onStockView={(view) => { pendingView.current = view; setStockView(view); }} />
            </Suspense>
          </RouteBoundary>
        )}
        {chartVisited.current && (
          <div className="chart-page-host" hidden={route.page !== 'chart'}>
            <RouteBoundary>
              <Suspense fallback={route.page === 'chart' ? <PageState kind="loading" title="載入圖表工作台…" /> : null}>
              <ChartPage
                listNav={route.page === 'chart' ? listNav : null}
                active={route.page === 'chart'}
                request={chartRequest}
                onStateChange={onChartState}
                onOpenSearch={() => setSearchOpen(true)}
                onOpenFinancials={openFinancials}
                view={stockView}
                onViewChange={setStockView}
                reportSymbols={localSymbols}
              />
              </Suspense>
            </RouteBoundary>
          </div>
        )}
        <div className="site-data-footer">
          <div id="site-page-notes" />
          {route.page === 'console' && <LegacyImportCard />}
          <StorageModeNotice />
          <DataHealthPanel report={reportState} />
        </div>
      </main>
      <nav className="site-tabbar" aria-label="主要頁面">
        {NAV.map(({ page, label, icon: Icon }) => (
          <button
            key={page}
            type="button"
            className={navPageOf(route.page) === page && !searchOpen ? 'active' : ''}
            aria-current={navPageOf(route.page) === page ? 'page' : undefined}
            onClick={() => go(page)}
          >
            <Icon size={22} aria-hidden="true" />
            <span>{label}</span>
          </button>
        ))}
      </nav>
      {searchOpen && (
        <div className="search-sheet-backdrop" onClick={() => setSearchOpen(false)}>
          <div className="search-sheet" role="dialog" aria-modal="true" aria-label="搜尋股票" onClick={(event) => event.stopPropagation()}>
            <div className="search-sheet-head">
              <SymbolSearch
                mode="sheet"
                autoFocus
                localSymbols={localSymbols}
                onSelect={onSearchSelect}
                onClose={() => setSearchOpen(false)}
              />
              <button type="button" className="text-button" onClick={() => setSearchOpen(false)}>
                取消
              </button>
            </div>
          </div>
        </div>
      )}
      {settingsOpen && (
        <div className="dialog-backdrop site-pwa-backdrop" onClick={() => setSettingsOpen(false)}>
          <div className="site-dialog site-pwa-dialog" role="dialog" aria-modal="true" aria-label="顯示設定" onClick={(event) => event.stopPropagation()}>
            <div className="dialog-head">
              <b>顯示設定</b>
              <button type="button" className="icon-button" aria-label="關閉" onClick={() => setSettingsOpen(false)}>
                <X size={18} />
              </button>
            </div>
            <DisplaySettings reportDate={reportDate} generatedAt={report?.generatedAt ?? null} />
          </div>
        </div>
      )}
    </div>
  );
}

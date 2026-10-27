import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
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
import { RouteBoundary } from './RouteBoundary';
import { DisplaySettings } from '../ui/DisplaySettings';
import { MarketSectionNav, type MarketSectionRoutes } from '../ui/MarketSectionNav';

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
    : 'markets';

const NAV: { page: Page; label: string; icon: typeof Star }[] = [
  { page: 'watchlist', label: '自選', icon: Star },
  { page: 'markets', label: '市場', icon: LayoutDashboard },
  { page: 'screener', label: '選股', icon: ListFilter },
  { page: 'chart', label: '個股', icon: ChartCandlestick },
  { page: 'console', label: '我的', icon: UserRound },
];
const PAGE_TITLES: Record<Page, string> = {
  markets: '大盤',
  world: '世界指數',
  themes: '題材輪動',
  screener: '選股',
  chart: '圖表',
  financials: '財報分析',
  watchlist: '自選清單',
  console: '我的',
};
const RATES_TITLE = '美債與利率';

/** Existing detail pages light their parent primary section. */
const navPageOf = (page: Page): Page => page === 'financials' ? 'chart' : page === 'world' || page === 'themes' ? 'markets' : page;
const pageTitleOf = (route: Route) => route.page === 'world'
  ? route.view === 'commodities' ? '原物料' : route.view === 'fx' ? '匯率' : route.view === 'indices' ? PAGE_TITLES.world : RATES_TITLE
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
  const { route, id: navId, source: navSource, navigate, syncRoute } = useRoute(DEFAULT_PAGE);
  const marketMemory = useRef<MarketSectionRoutes>({ markets: { page: 'markets', market: 'tw' }, world: { page: 'world' }, themes: { page: 'themes' } });
  if (route.page === 'markets') marketMemory.current.markets = route;
  if (route.page === 'world') marketMemory.current.world = route;
  if (route.page === 'themes') marketMemory.current.themes = { page: 'themes', ...(route.market ? { market: route.market } : {}), ...(route.v ? { v: route.v } : {}) };

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
  const localSymbols = useMemo(() => reportSymbols(report), [report]);

  useEffect(() => applyColorConvention(preferences.colors), [preferences.colors]);
  // A navigation that names a symbol becomes one chart request (URL syncs never repeat it).
  useEffect(() => {
    if (route.page !== 'chart' || !route.symbol) return;
    setChartRequest({ id: navId, symbol: route.symbol, timeframe: route.tf, source: navSource });
    setStockView(pendingView.current ?? 'chart');
    pendingView.current = null;
    // Only a new navigation id issues a request; the route is read at that moment.
  }, [navId]);
  // Each page starts at the top (the shell scrolls inside <main>).
  useEffect(() => {
    document.getElementById('main')?.scrollTo?.({ top: 0 });
  }, [
    route.page,
    route.page === 'markets'
      ? route.market
      : route.page === 'screener'
        ? route.group
        : route.page === 'world'
          ? route.view
          : route.page === 'themes'
            ? route.market
            : '',
  ]);
  useEffect(() => {
    const titles =
      route.page === 'chart' && route.symbol
        ? `${route.symbol} · 圖表`
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
      navigate({ page: 'chart', symbol: options.symbol, ...(options.tf ? { tf: options.tf } : {}) });
    },
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
      navigate({ page: 'financials', symbol, ...(tf ? { tf } : {}), ...(periodMemory.current ? { p: periodMemory.current } : {}) }),
    [navigate],
  );
  const go = (page: Page) => {
    setSearchOpen(false);
    if (page === route.page) return;
    // Back from financials to the chart of the same symbol, with the timeframe it was opened with.
    if (page === 'chart')
      navigate(route.page === 'financials' && route.symbol ? { page: 'chart', symbol: route.symbol, ...(route.tf ? { tf: route.tf } : {}) } : { page: 'chart' });
    else if (page === 'markets') navigate(marketMemory.current.markets);
    else navigate({ page } as Route);
  };
  const reportDate = report?.reportDate ?? null;
  const pageProps = { report: reportState, reload, navigate, openChart };
  const pageFallback = <PageState kind="loading" title="載入中…" />;

  return (
    <div className={`site ${route.page === 'chart' ? 'on-chart' : ''}`} data-page={route.page}>
      <header className="site-topbar">
        <a
          className="site-brand"
          href="?page=markets&market=tw"
          onClick={(event) => {
            event.preventDefault();
            navigate({ page: 'markets', market: 'tw' });
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
        {SCREENER_HOSTING && <StorageNotice />}
        {(route.page === 'markets' || route.page === 'world' || route.page === 'themes') &&
          <MarketSectionNav active={route.page} routes={marketMemory.current} navigate={navigate} />}
        <RouteBoundary key={route.page}>
          <Suspense fallback={pageFallback}>
            {route.page === 'markets' && <MarketsPage {...pageProps} market={route.market} />}
            {route.page === 'world' && <WorldPage {...pageProps} view={route.view} />}
            {route.page === 'themes' && <ThemesPage {...pageProps} theme={route.theme} market={route.market} view={route.v} />}
            {route.page === 'screener' && <ScreenerPage {...pageProps} group={route.group} f={route.f} />}
            {(route.page === 'console' || route.page === 'watchlist') && <ConsolePage {...pageProps} section={route.page === 'watchlist' ? 'lists' : 'settings'} list={route.page === 'watchlist' ? route.list : undefined} />}
            {route.page === 'financials' && (
              <FinancialsPage {...pageProps} symbol={route.symbol} p={route.p} tf={route.tf}
                onStockView={(view) => { pendingView.current = view; setStockView(view); }} />
            )}
          </Suspense>
        </RouteBoundary>
        {chartVisited.current && (
          <div className="chart-page-host" hidden={route.page !== 'chart'}>
            <RouteBoundary>
              <Suspense fallback={route.page === 'chart' ? <PageState kind="loading" title="載入圖表工作台…" /> : null}>
              <ChartPage
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
        <DataHealthPanel report={reportState} />
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
            <Icon size={21} aria-hidden="true" />
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

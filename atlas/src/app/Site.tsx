import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { ChartCandlestick, Globe, LayoutDashboard, ListFilter, Search, Settings, X } from 'lucide-react';
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
import type { Timeframe } from '../market-data/MarketDataProvider';

const MarketsPage = lazy(() => import('../pages/MarketsPage'));
const WorldPage = lazy(() => import('../pages/WorldPage'));
const ScreenerPage = lazy(() => import('../pages/ScreenerPage'));
// The workspace (engine, drawings, panels) loads only when the chart opens.
const ChartPage = lazy(() => import('./ChartPage'));

/** The report pages exist only where the daily report is published (the screener site). */
const DEFAULT_PAGE: Page =
  import.meta.env?.VITE_DEFAULT_PAGE === 'chart' || (!SCREENER_HOSTING && import.meta.env?.VITE_DEFAULT_PAGE !== 'markets')
    ? 'chart'
    : 'markets';

const NAV: { page: Page; label: string; icon: typeof Globe }[] = [
  { page: 'markets', label: '大盤', icon: LayoutDashboard },
  { page: 'world', label: '世界', icon: Globe },
  { page: 'screener', label: '選股', icon: ListFilter },
  { page: 'chart', label: '圖表', icon: ChartCandlestick },
];
const PAGE_TITLES: Record<Page, string> = {
  markets: '大盤',
  world: '世界指數',
  screener: '選股',
  chart: '圖表',
};

const mobileQuery = window.matchMedia('(max-width: 1099px)');
const subscribeMobile = (callback: () => void) => {
  mobileQuery.addEventListener('change', callback);
  return () => mobileQuery.removeEventListener('change', callback);
};

export interface OpenChartOptions {
  symbol: string;
  name?: string;
  tf?: Timeframe;
  /** Moving averages to add the first time this symbol is opened (report presets). */
  maList?: readonly number[];
}

function reportSymbols(report: Report | null): LocalSymbol[] {
  if (!report) return [];
  const map = new Map<string, string>();
  for (const market of report.markets) for (const index of market.indices) map.set(index.symbol, index.name);
  for (const index of report.worldIndices) if (!map.has(index.symbol)) map.set(index.symbol, index.name);
  for (const group of report.groups)
    for (const item of group.items) if (!map.has(item.symbol)) map.set(item.symbol, item.name);
  return [...map].map(([symbol, name]) => ({ symbol, name }));
}

export function Site() {
  const mobile = useSyncExternalStore(subscribeMobile, () => mobileQuery.matches);
  const { route, id: navId, source: navSource, navigate, syncRoute } = useRoute(DEFAULT_PAGE);
  // Builds without a published report (upstream Atlas) only fetch it when a report page opens.
  const { state: reportState, reload } = useReport(SCREENER_HOSTING || route.page !== 'chart');
  const report = reportState.status === 'ok' ? reportState.report : null;
  const [searchOpen, setSearchOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const preferences = useSyncExternalStore(sitePreferences.subscribe, sitePreferences.get);
  const presets = useRef(new Map<string, readonly number[]>());
  const [chartRequest, setChartRequest] = useState<ChartRequest | null>(null);
  const chartVisited = useRef(route.page === 'chart');
  if (route.page === 'chart') chartVisited.current = true;
  const localSymbols = useMemo(() => reportSymbols(report), [report]);

  useEffect(() => applyColorConvention(preferences.colors), [preferences.colors]);
  // A navigation that names a symbol becomes one chart request (URL syncs never repeat it).
  useEffect(() => {
    if (route.page !== 'chart' || !route.symbol) return;
    const maList = presets.current.get(route.symbol);
    presets.current.delete(route.symbol);
    setChartRequest({ id: navId, symbol: route.symbol, timeframe: route.tf, source: navSource, maList: maList ? [...maList] : undefined });
    // Only a new navigation id issues a request; the route is read at that moment.
  }, [navId]);
  // Each page starts at the top (the shell scrolls inside <main>).
  useEffect(() => {
    document.getElementById('main')?.scrollTo?.({ top: 0 });
  }, [route.page, route.page === 'markets' ? route.market : route.page === 'screener' ? route.group : '']);
  useEffect(() => {
    const titles = route.page === 'chart' && route.symbol ? `${route.symbol} · 圖表` : PAGE_TITLES[route.page];
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
      if (options.maList?.length) presets.current.set(options.symbol, options.maList);
      setSearchOpen(false);
      navigate({ page: 'chart', symbol: options.symbol, ...(options.tf ? { tf: options.tf } : {}) });
    },
    [navigate],
  );
  const onSearchSelect = useCallback(
    (result: SearchResult) => openChart({ symbol: result.symbol, name: result.name }),
    [openChart],
  );
  const onChartState = useCallback(
    (symbol: string, tf: Timeframe) => {
      syncRoute({ page: 'chart', symbol, tf });
    },
    [syncRoute],
  );
  const go = (page: Page) => {
    setSearchOpen(false);
    if (page === route.page) return;
    if (page === 'chart') navigate({ page: 'chart' });
    else if (page === 'markets') navigate({ page, market: route.page === 'markets' ? route.market : 'tw' });
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
        <h1 className="site-page-title">{PAGE_TITLES[route.page]}</h1>
        {!mobile && (
          <div className="site-search">
            <SymbolSearch mode="dropdown" localSymbols={localSymbols} onSelect={onSearchSelect} />
          </div>
        )}
        <div className="site-topbar-end">
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
            className={route.page === page ? 'active' : ''}
            aria-current={route.page === page ? 'page' : undefined}
            onClick={() => go(page)}
          >
            <Icon size={20} aria-hidden="true" />
            <span>{label}</span>
          </button>
        ))}
      </nav>
      <main className="site-main" id="main">
        <Suspense fallback={pageFallback}>
          {route.page === 'markets' && <MarketsPage {...pageProps} market={route.market} />}
          {route.page === 'world' && <WorldPage {...pageProps} />}
          {route.page === 'screener' && <ScreenerPage {...pageProps} group={route.group} />}
        </Suspense>
        {chartVisited.current && (
          <Suspense fallback={route.page === 'chart' ? <PageState kind="loading" title="載入圖表工作台…" /> : null}>
            <div className="chart-page-host" hidden={route.page !== 'chart'}>
              <ChartPage
                active={route.page === 'chart'}
                request={chartRequest}
                onStateChange={onChartState}
                onOpenSearch={() => setSearchOpen(true)}
                reportSymbols={localSymbols}
              />
            </div>
          </Suspense>
        )}
      </main>
      <nav className="site-tabbar" aria-label="主要頁面">
        {NAV.map(({ page, label, icon: Icon }) => (
          <button
            key={page}
            type="button"
            className={route.page === page && !searchOpen ? 'active' : ''}
            aria-current={route.page === page ? 'page' : undefined}
            onClick={() => go(page)}
          >
            <Icon size={21} aria-hidden="true" />
            <span>{label}</span>
          </button>
        ))}
        <button type="button" className={searchOpen ? 'active' : ''} onClick={() => setSearchOpen(true)}>
          <Search size={21} aria-hidden="true" />
          <span>搜尋</span>
        </button>
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
        <div className="dialog-backdrop" onClick={() => setSettingsOpen(false)}>
          <div className="site-dialog" role="dialog" aria-modal="true" aria-label="顯示設定" onClick={(event) => event.stopPropagation()}>
            <div className="dialog-head">
              <b>顯示設定</b>
              <button type="button" className="icon-button" aria-label="關閉" onClick={() => setSettingsOpen(false)}>
                <X size={18} />
              </button>
            </div>
            <fieldset className="segmented-field">
              <legend>漲跌顏色</legend>
              <div className="segmented">
                {(
                  [
                    ['tw', '紅漲綠跌'],
                    ['us', '綠漲紅跌'],
                  ] as const
                ).map(([value, label]) => (
                  <label key={value} className={preferences.colors === value ? 'active' : ''}>
                    <input
                      type="radio"
                      name="color-convention"
                      value={value}
                      checked={preferences.colors === value}
                      onChange={() => sitePreferences.setColors(value)}
                    />
                    <span className={`swatch ${value}`} aria-hidden="true" />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
            <dl className="dialog-facts">
              <div>
                <dt>報告日期</dt>
                <dd>{reportDate ? formatDateTime(reportDate, false) : '—'}</dd>
              </div>
              <div>
                <dt>產生時間</dt>
                <dd>{report?.generatedAt ? formatDateTime(report.generatedAt) : '—'}</dd>
              </div>
            </dl>
            <p className="dialog-note">行情為延遲資料，僅供研究參考，非投資建議。圖表設定與畫線只儲存在這台裝置。</p>
          </div>
        </div>
      )}
    </div>
  );
}

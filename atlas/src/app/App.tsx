import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import {
  Activity,
  BellRing,
  ChevronDown,
  Download,
  Upload,
  Undo2,
  Redo2,
  Expand,
  PanelRightClose,
  PanelRightOpen,
  Layers,
  List,
  LockKeyhole,
  UnlockKeyhole,
  Trash2,
  Settings2,
  SlidersHorizontal,
  ZoomIn,
  ZoomOut,
  Minimize2,
  MoreHorizontal,
  X,
  HelpCircle,
  FlaskConical,
  RefreshCw,
  Search,
} from 'lucide-react';
import { AppStore } from './AppStore';
import { ChartEngine } from '../chart/ChartEngine';
import { DemoProvider } from '../market-data/DemoProvider';
import { YahooProvider } from '../market-data/YahooProvider';
import { SnapshotProvider } from '../market-data/SnapshotProvider';
import {
  timeframes,
  type Quote,
  type BarResult,
  type MarketDataProvider,
  type Timeframe,
} from '../market-data/MarketDataProvider';
import { measurementValues } from '../tools/Measurement';
import { toolNames, type ActiveTool } from '../drawing/DrawingModel';
import { useDialogFocus } from '../ui/useDialogFocus';
import { useChartFocus } from '../ui/useChartFocus';
import { IconButton } from '../ui/IconButton';
import { DrawingToolGlyph, DrawingToolPicker } from '../ui/DrawingToolPicker';
import { Watchlist, companies } from '../ui/Watchlist';
import { IndicatorPanel } from '../ui/IndicatorPanel';
import { DrawingPanel } from '../ui/DrawingPanel';
import { DrawingSettings } from '../ui/DrawingSettings';
import { WorkspaceSettings } from '../ui/WorkspaceSettings';
import { BacktestPanel } from '../ui/BacktestPanel';
import { AlertPanel } from '../ui/AlertPanel';
import { PanelBoundary } from '../ui/PanelBoundary';
import { CachedMarketDataProvider } from '../market-data/CachedMarketDataProvider';
import { closedBars } from '../market-data/MarketDataProvider';
import { evaluateAlerts } from '../alerts/AlertEngine';
import type { StrategyResult } from '../strategy';
import { errorLog, reportError } from '../errors/UserErrors';
import { SCREENER_HOSTING, STATIC_HOSTING } from './HostingMode';
import { parseLaunchParams } from './LaunchParams';
import { legacyVolumeEnabled } from '../storage/schema';
import { getMarketProfile } from '../market-data/MarketProfile';
import {
  getLoadedSymbolDirectory,
  loadSymbolCatalog,
  loadSymbolDirectory,
  resolveSymbolInput,
  type SymbolCatalogEntry,
} from '../market-data/SymbolCatalog';
import { indicatorColors } from '../indicators/IndicatorRegistry';
import { marketProvider, getPeFigures, type PeFigures } from './dataSources';
import { sitePreferences, upDownColors } from './sitePreferences';
import { displayTicker, formatPercent, formatPrice, formatRatio, toneClass } from '../ui/format';
import type { ChartRequest } from './chartRequest';
import type { LocalSymbol } from '../ui/SymbolSearch';
import '../ui/workspace.css';

type Panel = 'indicators' | 'drawings' | 'backtest' | 'alerts' | 'settings';
type DockTab = 'watchlist' | Panel;
const panelNames: Record<DockTab, string> = {
  watchlist: '自選清單',
  indicators: '指標',
  drawings: '畫線物件',
  backtest: '策略回測',
  alerts: '價格提醒',
  settings: '工作區設定',
};
const dockTabs: { id: DockTab; icon: typeof List }[] = [
  { id: 'watchlist', icon: List },
  { id: 'indicators', icon: Activity },
  { id: 'drawings', icon: Layers },
  { id: 'backtest', icon: FlaskConical },
  { id: 'alerts', icon: BellRing },
  { id: 'settings', icon: Settings2 },
];
const store = new AppStore();
let storeInitialized = false;
const mobileMedia = window.matchMedia('(max-width:1099px)');
const subscribeMobile = (cb: () => void) => {
  mobileMedia.addEventListener('change', cb);
  return () => mobileMedia.removeEventListener('change', cb);
};
const providers = {
  // Already IndexedDB-cached by createScreenerMarketProvider.
  market: marketProvider(),
  demo: new CachedMarketDataProvider(new DemoProvider()),
  snapshot: new CachedMarketDataProvider(new SnapshotProvider()),
  yahoo: STATIC_HOSTING ? new YahooProvider() : new CachedMarketDataProvider(new YahooProvider()),
};
type ProviderId = keyof typeof providers;
const providerLabels: Record<ProviderId, string> = {
  market: '市場行情（延遲）',
  snapshot: '延遲快照',
  yahoo: STATIC_HOSTING ? 'Yahoo（需要後端）' : 'Yahoo 後端',
  demo: '模擬資料（DEMO）',
};
/**
 * Provider for a symbol opened from the site (search, report lists, deep links). Static deployments
 * open real delayed data, never Demo's simulated prices; elsewhere the chosen provider is kept.
 */
const linkProvider = (current: ProviderId): ProviderId =>
  SCREENER_HOSTING ? 'market' : STATIC_HOSTING ? 'snapshot' : current;
/** Demo has no Taiwan listings; a Taiwan symbol switches Demo to real delayed data. */
const taiwanProvider: ProviderId = SCREENER_HOSTING ? 'market' : 'snapshot';
const staticHostingText = SCREENER_HOSTING
  ? '行情為延遲資料（非即時），僅供研究參考，非投資建議。'
  : 'GitHub Pages：延遲行情快照，非即時；即時 Yahoo 需要後端。';
const demoDataText = 'DEMO 價格為模擬資料，並非市場行情。';
const supportsTimeframe = (
  provider: { supportedTimeframes: readonly Timeframe[] },
  timeframe: Timeframe,
) => provider.supportedTimeframes.includes(timeframe);

export interface ChartWorkspaceProps {
  /** False while another site page is shown (the workspace stays mounted but hidden). */
  active?: boolean;
  request?: ChartRequest | null;
  onStateChange?: (symbol: string, timeframe: Timeframe) => void;
  onOpenSearch?: () => void;
  reportSymbols?: readonly LocalSymbol[];
}

export function App({
  active = true,
  request = null,
  onStateChange,
  onOpenSearch,
  reportSymbols = [],
}: ChartWorkspaceProps = {}) {
  const mobile = useSyncExternalStore(subscribeMobile, () => mobileMedia.matches);
  const preferences = useSyncExternalStore(sitePreferences.subscribe, sitePreferences.get);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot),
    symbol = state.app.activeSymbol,
    s = store.symbol(symbol),
    tf = s.preferences.timeframe;
  const providerId = state.app.provider as ProviderId;
  const activeIndicators = useMemo(
    () => s.indicators.filter((indicator) => indicator.scope.timeframe === tf),
    [s.indicators, tf],
  );
  const activeDrawings = useMemo(
    () => s.drawings.filter((drawing) => drawing.scope.timeframes.includes(tf)),
    [s.drawings, tf],
  );
  const [tool, setTool] = useState<ActiveTool>('select'),
    [selected, setSelected] = useState<string | null>(null),
    [loading, setLoading] = useState(true),
    [dataError, setDataError] = useState(''),
    [notice, setNotice] = useState(''),
    [result, setResult] = useState<BarResult | null>(null),
    [quote, setQuote] = useState<{ symbol: string; provider: string; quote: Quote } | null>(null),
    [pe, setPe] = useState<{ symbol: string; figures: PeFigures } | null>(null),
    [quotes, setQuotes] = useState<Record<string, Quote>>({}),
    [eventsStatus, setEventsStatus] = useState('公司事件：模擬資料不提供'),
    [sheet, setSheet] = useState<DockTab | null>(null),
    [watchlistDock, setWatchlistDock] = useState(false),
    [help, setHelp] = useState(false),
    [drawingPickerOpen, setDrawingPickerOpen] = useState(false),
    [reloadToken, setReloadToken] = useState(0);
  const [catalogEntries, setCatalogEntries] = useState<SymbolCatalogEntry[]>([]);
  const [symbolTimeframes, setSymbolTimeframes] = useState<{ key: string; values: readonly Timeframe[] } | null>(null);
  const market = getMarketProfile(symbol);
  const isIndex = symbol.startsWith('^');
  const marketLabel = isIndex
    ? market.market === 'TW' ? '台股指數' : '指數'
    : market.market === 'TW' ? (market.exchange === 'TWSE' ? '上市' : '上櫃') : '美股';
  const companyName = [
    catalogEntries.find((entry) => entry.symbol === symbol)?.name,
    getLoadedSymbolDirectory()?.entries.find((entry) => entry.symbol === symbol)?.name,
    reportSymbols.find((entry) => entry.symbol === symbol)?.name,
    companies[symbol],
  ].find((name) => !!name && name !== symbol);
  const timeframeKey = `${providerId}:${symbol}`;
  const availableTimeframes = symbolTimeframes?.key === timeframeKey ? symbolTimeframes.values : providers[providerId].supportedTimeframes;
  const [, setDirectoryLoads] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    void loadSymbolCatalog(abort.signal).then(setCatalogEntries).catch(() => {
      // Explicit exchange suffixes and US navigation remain usable if the directory is unavailable.
    });
    // Company names for US symbols come from the full-market directory.
    void loadSymbolDirectory(abort.signal)
      .then(() => setDirectoryLoads((n) => n + 1))
      .catch(() => undefined);
    return () => abort.abort();
  }, []);
  useEffect(() => {
    if (!state.ready) return;
    const abort = new AbortController();
    const provider: MarketDataProvider = providers[providerId];
    setSymbolTimeframes(null);
    if (provider.getSymbolTimeframes) {
      void provider.getSymbolTimeframes(symbol, abort.signal).then((values) => {
        if (!abort.signal.aborted) setSymbolTimeframes({ key: timeframeKey, values });
      }).catch(() => {
        // The chart's provider error explains unavailable symbols; never invent intervals.
        if (!abort.signal.aborted) setSymbolTimeframes({ key: timeframeKey, values: [] });
      });
    }
    return () => abort.abort();
  }, [state.ready, symbol, providerId, timeframeKey, reloadToken]);
  const storedTab = state.app.workspace.rightTab;
  const right = state.app.workspace.rightOpen,
    rightTab: DockTab = watchlistDock ? 'watchlist' : storedTab === 'financials' ? 'indicators' : storedTab;
  const setRight = (rightOpen: boolean) =>
    store.updateApp({ workspace: { ...store.getSnapshot().app.workspace, rightOpen } });
  const setRightTab = (tab: DockTab) => {
    setWatchlistDock(tab === 'watchlist');
    if (tab !== 'watchlist') store.updateApp({ workspace: { ...store.getSnapshot().app.workspace, rightTab: tab } });
  };
  const tablet = mobile && window.innerWidth >= 768;
  const modal = active && ((!!sheet && !tablet) || help || drawingPickerOpen);
  const errors = useSyncExternalStore(errorLog.subscribe, errorLog.getSnapshot);
  const alertSession = useRef('');
  useDialogFocus(modal, drawingPickerOpen ? 'drawing-tool-picker' : undefined);
  const chartFocus = useChartFocus<HTMLDivElement>();
  const hostRef = useRef<HTMLDivElement>(null),
    headerRef = useRef<HTMLDivElement>(null),
    legendRef = useRef<HTMLDivElement>(null),
    sourceRef = useRef<HTMLDivElement>(null),
    timeframesRef = useRef<HTMLDivElement>(null),
    engine = useRef<ChartEngine | null>(null),
    importRef = useRef<HTMLInputElement>(null),
    drawingLauncherRef = useRef<HTMLButtonElement>(null);
  const closeDrawingPicker = useCallback(() => {
    setDrawingPickerOpen(false);
    requestAnimationFrame(() => drawingLauncherRef.current?.focus({ preventScroll: true }));
  }, []);
  const openDrawingPicker = useCallback(() => {
    engine.current?.controller?.cancel();
    setSheet(null);
    setHelp(false);
    setDrawingPickerOpen(true);
  }, []);
  const centerActiveTimeframe = useCallback(() => {
    const strip = timeframesRef.current;
    const activeButton = strip?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]');
    if (!strip || !activeButton) return;
    const stripRect = strip.getBoundingClientRect();
    const buttonRect = activeButton.getBoundingClientRect();
    const delta = buttonRect.left - stripRect.left + (buttonRect.width - stripRect.width) / 2;
    strip.scrollLeft = Math.max(
      0,
      Math.min(strip.scrollWidth - strip.clientWidth, strip.scrollLeft + delta),
    );
  }, []);
  useEffect(() => {
    if (!state.ready) return;
    const frame = requestAnimationFrame(centerActiveTimeframe);
    return () => cancelAnimationFrame(frame);
  }, [centerActiveTimeframe, chartFocus.isFocused, mobile, state.ready, tf, active]);
  useEffect(() => {
    const strip = timeframesRef.current;
    if (!strip || typeof ResizeObserver === 'undefined') return;
    let width = strip.getBoundingClientRect().width;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      const nextWidth = strip.getBoundingClientRect().width;
      if (Math.abs(nextWidth - width) < 0.5) return;
      width = nextWidth;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(centerActiveTimeframe);
    });
    observer.observe(strip);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [centerActiveTimeframe]);
  useEffect(() => {
    if (storeInitialized) return;
    storeInitialized = true;
    void store.initialize().then(() => {
      // The screener site serves delayed market data; settings saved by earlier versions may name
      // providers that the static site no longer publishes.
      const provider = store.getSnapshot().app.provider;
      if (SCREENER_HOSTING && provider !== 'market' && provider !== 'demo') store.updateApp({ provider: 'market' });
    });
  }, []);
  useEffect(() => {
    if (!state.ready || !hostRef.current || !headerRef.current || !sourceRef.current) return;
    const chart = new ChartEngine(
      hostRef.current,
      headerRef.current,
      sourceRef.current,
      {
        defaults: (type) => store.getSnapshot().app.drawingDefaults[type] ?? {},
        commit: (symbol, drawings, label, timeframe) => store.commitDrawings(symbol, drawings, label, timeframe),
        selection: setSelected,
        tool: setTool,
        view: (symbol, timeframe, range) =>
          store.updateSymbol(symbol, (s) => ({
            ...s,
            preferences: {
              ...s.preferences,
              views: { ...s.preferences.views, [timeframe]: range },
            },
          })),
      },
      legendRef.current ?? undefined,
    );
    chart.setUpDownColors(upDownColors(sitePreferences.get().colors));
    engine.current = chart;
    return () => {
      chart.destroy();
      engine.current = null;
    };
  }, [state.ready]);
  const colorConvention = preferences.colors;
  const appliedColors = useRef(colorConvention);
  useEffect(() => {
    if (appliedColors.current === colorConvention) return;
    appliedColors.current = colorConvention;
    engine.current?.setUpDownColors(upDownColors(colorConvention));
    // Volume bars carry per-bar colors; reload recolors them.
    setReloadToken((n) => n + 1);
  }, [colorConvention]);
  useEffect(() => {
    const chart = engine.current;
    if (!state.ready || !chart) return;
    const abort = new AbortController();
    let live = true;
    chart.clear();
    setTool('select');
    setSelected(null);
    setLoading(true);
    setResult(null);
    setDataError('');
    setEventsStatus(
      STATIC_HOSTING && providerId === 'yahoo' ? '公司事件：需要後端' : '公司事件：載入中…',
    );
    const provider = providers[providerId];
    if (!supportsTimeframe(provider, tf)) {
      store.updateSymbol(symbol, (s) => ({
        ...s,
        preferences: { ...s.preferences, timeframe: '1D' },
      }));
      return () => {
        live = false;
        abort.abort();
      };
    }
    void (provider as MarketDataProvider)
      .getBars(symbol, tf, undefined, abort.signal)
      .then((result) => {
        if (!live) return;
        const settings = store.symbol(symbol);
        chart.load(
          symbol,
          tf,
          result,
          settings.drawings,
          settings.indicators,
          settings.preferences.views[tf],
          legacyVolumeEnabled(settings.preferences, tf),
        );
        chart.sync(
          settings.drawings,
          settings.indicators,
          settings.preferences.magnet,
          'select',
          legacyVolumeEnabled(settings.preferences, tf),
        );
        setResult(result);
        setLoading(false);
        if (!provider.getCorporateEvents) {
          setEventsStatus('公司事件：此資料來源不提供');
          return;
        }
        void provider
          .getCorporateEvents(symbol, undefined, abort.signal)
          .then((events) => {
            if (!live) return;
            setEventsStatus(
              events.status === 'available'
                ? `公司事件 ${events.events.length} 筆 · ${events.source}`
                : '公司事件：此資料來源不提供',
            );
            if (events.status === 'available') chart.setCorporateEvents(events.events);
          })
          .catch(() => {
            if (live) setEventsStatus('公司事件：來源暫時無法使用');
          });
      })
      .catch((e) => {
        if (live) {
          setDataError(reportError('market', e));
          setLoading(false);
        }
      });
    return () => {
      live = false;
      abort.abort();
      chart.flushView();
    };
  }, [state.ready, symbol, tf, providerId, reloadToken]);
  // Header quote (daily change, independent of the chart interval) and P/E.
  useEffect(() => {
    if (!state.ready) return;
    const abort = new AbortController();
    void providers[providerId]
      .getQuote(symbol, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setQuote({ symbol, provider: providerId, quote: value });
      })
      .catch(() => undefined);
    return () => abort.abort();
  }, [state.ready, symbol, providerId, reloadToken]);
  const headerQuote =
    quote && quote.symbol === symbol && quote.provider === providerId
      ? quote.quote
      : result?.quote && result.quote.symbol === symbol
        ? result.quote
        : null;
  const headerPrice = headerQuote?.price ?? null;
  useEffect(() => {
    if (!state.ready || providerId === 'demo') return;
    let live = true;
    void getPeFigures(symbol, headerPrice).then((figures) => {
      if (live) setPe({ symbol, figures });
    });
    return () => {
      live = false;
    };
  }, [state.ready, symbol, headerPrice, providerId]);
  const peFigures = providerId !== 'demo' && pe?.symbol === symbol ? pe.figures : null;
  useEffect(() => {
    engine.current?.sync(
      s.drawings,
      s.indicators,
      s.preferences.magnet,
      tool,
      legacyVolumeEnabled(s.preferences, tf),
    );
  }, [s.drawings, s.indicators, s.preferences.magnet, s.preferences.legacyVolume, s.preferences.volumeOverrides, tf, tool]);
  useEffect(() => {
    if (!active) return;
    const keyboard = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        const target = e.target as HTMLElement | null;
        if (drawingPickerOpen) {
          e.preventDefault();
          engine.current?.controller?.cancel();
          closeDrawingPicker();
          return;
        }
        const hadGesture = !!engine.current?.controller?.machine.gesture;
        if (sheet || help || hadGesture || chartFocus.isFocused) {
          e.preventDefault();
          engine.current?.controller?.cancel();
          if (tool !== 'select') setTool('select');
          if (sheet) setSheet(null);
          if (help) setHelp(false);
          if (!sheet && !help && !hadGesture && tool === 'select' && chartFocus.isFocused) void chartFocus.exit();
          return;
        }
        if (target?.closest('input,select,textarea,[contenteditable]')) return;
        if (tool !== 'select') {
          e.preventDefault();
          engine.current?.controller?.cancel();
          setTool('select');
        }
        return;
      }
      const target = e.target as HTMLElement | null;
      if (target?.closest('input,select,textarea,[contenteditable]')) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        engine.current?.controller?.cancel();
        if (e.shiftKey) store.redo();
        else store.undo();
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && selected) {
        e.preventDefault();
        engine.current?.controller?.cancel();
        store.mutateDrawing(selected, 'delete');
      }
    };
    window.addEventListener('keydown', keyboard);
    return () => window.removeEventListener('keydown', keyboard);
  }, [active, selected, sheet, help, drawingPickerOpen, closeDrawingPicker, tool, chartFocus.isFocused, chartFocus.exit]);
  useEffect(() => {
    const save = () => engine.current?.flushView();
    window.addEventListener('pagehide', save);
    return () => window.removeEventListener('pagehide', save);
  }, []);
  useEffect(() => {
    // Leaving the chart page: persist the view and drop any half-finished gesture.
    if (active) return;
    engine.current?.controller?.cancel();
    engine.current?.flushView();
    setSheet(null);
    setHelp(false);
    setDrawingPickerOpen(false);
  }, [active]);
  /** Shared by the watchlist, site search and deep links. `provider` forces a data source. */
  const switchSymbol = (symbol: string, provider?: ProviderId) => {
    engine.current?.controller?.cancel();
    const current = store.getSnapshot().app.provider as ProviderId;
    store.updateApp({
      activeSymbol: symbol,
      ...(provider
        ? { provider }
        : getMarketProfile(symbol).market === 'TW' && current === 'demo' ? { provider: taiwanProvider } : {}),
      recentSymbols: [
        symbol,
        ...store.getSnapshot().app.recentSymbols.filter((x) => x !== symbol),
      ].slice(0, 20),
    });
    setSheet(null);
    setNotice('');
  };
  const selectSymbol = (value: string) => {
    try {
      switchSymbol(resolveSymbolInput(value, catalogEntries));
    } catch (e) {
      setNotice(String(e));
    }
  };
  // Apply each site navigation (search, report lists, deep links) once, after the store is ready.
  const appliedRequest = useRef(0);
  const pendingRequest = useRef(false);
  if (request && appliedRequest.current !== request.id) pendingRequest.current = true;
  useEffect(() => {
    if (!state.ready || !request || appliedRequest.current === request.id) return;
    appliedRequest.current = request.id;
    const launch = parseLaunchParams(
      `?symbol=${encodeURIComponent(request.symbol)}${request.timeframe ? `&tf=${request.timeframe}` : ''}`,
    );
    void (async () => {
      try {
        if (!launch || launch.error || !launch.symbol) {
          setNotice(String(new Error(launch?.error ?? '網址中的股票代號無效。')));
          return;
        }
        let target: string;
        try {
          const entries = launch.needsCatalog
            ? await loadSymbolCatalog().catch(() => [] as SymbolCatalogEntry[])
            : [];
          target = resolveSymbolInput(launch.symbol, entries);
        } catch (e) {
          setNotice(String(e));
          return;
        }
        // A reload of the synced URL (same symbol) keeps the user's data source.
        const currentApp = store.getSnapshot().app;
        const currentProvider = currentApp.provider as ProviderId;
        const nextProvider =
          request.source === 'app' || target === currentApp.activeSymbol
            ? currentProvider === 'demo' && getMarketProfile(target).market === 'TW'
              ? taiwanProvider
              : currentProvider
            : linkProvider(currentProvider);
        let message = '';
        if (launch.timeframe) {
          const provider: MarketDataProvider = providers[nextProvider];
          if (!supportsTimeframe(provider, launch.timeframe)) {
            message = `${launch.timeframe} 不適用於目前的資料來源，維持原週期。`;
          } else {
            const available = provider.getSymbolTimeframes
              ? await provider.getSymbolTimeframes(target).catch(() => undefined)
              : undefined;
            const current = store.symbol(target).preferences.timeframe;
            const timeframe: Timeframe | undefined =
              !available || available.includes(launch.timeframe)
                ? launch.timeframe
                : available.includes(current) ? undefined : available.includes('1D') ? '1D' : undefined;
            if (available && !available.includes(launch.timeframe))
              message = `${target} 沒有 ${launch.timeframe} 資料，${timeframe ? `改用 ${timeframe}` : '維持原週期'}。`;
            if (timeframe && timeframe !== current)
              store.updateSymbol(target, (s) => ({ ...s, preferences: { ...s.preferences, timeframe } }));
          }
        }
        // Report moving averages (daily) become SMA indicators the first time a symbol is opened.
        const periods = [...new Set(request.maList ?? [])].filter((n) => Number.isInteger(n) && n > 0 && n <= 5000);
        if (periods.length) {
          const existing = store.symbol(target).indicators.filter((i) => i.scope.timeframe === '1D' && i.type !== 'Volume');
          if (!existing.length)
            periods.forEach((period, index) =>
              store.addIndicator({
                id: crypto.randomUUID(),
                symbol: target,
                type: 'SMA',
                period,
                source: 'close',
                visible: true,
                locked: false,
                lineWidth: 1,
                widthMode: 'pixels',
                color: indicatorColors[index % indicatorColors.length]!,
                scope: { timeframe: '1D' },
              }),
            );
        }
        switchSymbol(target, nextProvider !== store.getSnapshot().app.provider ? nextProvider : undefined);
        if (launch.ignoredTimeframe) message = `網址週期 ${launch.ignoredTimeframe} 無效，已忽略。`;
        if (message) setNotice(message);
      } finally {
        pendingRequest.current = false;
      }
    })();
    // switchSymbol is recreated each render; each request id is applied once.
  }, [state.ready, request]);
  // Keep the page URL in step with the workspace (no new history entry).
  useEffect(() => {
    if (!state.ready || !active || pendingRequest.current) return;
    onStateChange?.(symbol, tf);
  }, [state.ready, active, symbol, tf, onStateChange]);
  const chooseTool = (next: ActiveTool) => {
    engine.current?.controller?.setTool(next);
    setTool(next);
    setSheet(null);
  };
  const toggleMagnet = () =>
    store.updateSymbol(symbol, (current) => ({
      ...current,
      preferences: { ...current.preferences, magnet: !current.preferences.magnet },
    }));
  const undoDrawing = () => {
    engine.current?.controller?.cancel();
    store.undo();
  };
  const redoDrawing = () => {
    engine.current?.controller?.cancel();
    store.redo();
  };
  const openPanel = (panel: DockTab) => {
    setRightTab(panel);
    if (mobile || chartFocus.isFocused) setSheet(panel);
    else setRight(true);
  };
  const toggleDock = (panel: DockTab) => {
    if (right && rightTab === panel) setRight(false);
    else {
      setRightTab(panel);
      setRight(true);
    }
  };
  const pickDrawing = (id: string) => {
    engine.current?.setSelection(id);
    setSelected(id);
  };
  const drawingAction = (id: string, action: 'lock' | 'delete') => {
    engine.current?.controller?.cancel();
    store.mutateDrawing(id, action);
  };
  const selectedDrawing = activeDrawings.find((d) => d.id === selected);
  const measurement =
    selectedDrawing && engine.current?.controller && selectedDrawing.type.includes('range')
      ? measurementValues(selectedDrawing, engine.current.controller.machine.projection)
      : null;
  const history = store.history(symbol);
  const exportAll = async () => {
    try {
      engine.current?.flushView();
      const data = await store.export(),
        blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
        url = URL.createObjectURL(blob),
        a = document.createElement('a');
      a.href = url;
      a.download = `atlas-settings-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice('所有股票設定已匯出');
    } catch (e) {
      setNotice(String(e));
    }
  };
  const importAll = async (file: File) => {
    try {
      const contents = await file.text();
      engine.current?.controller?.cancel();
      engine.current?.flushView();
      await store.import(contents);
      setSelected(null);
      setTool('select');
      setNotice('設定已還原');
      setReloadToken((n) => n + 1);
    } catch (e) {
      setNotice(reportError('import', e));
    }
  };
  const indicatorPanel = (
    <IndicatorPanel
      key={`${symbol}:${tf}`}
      store={store}
      symbol={symbol}
      timeframe={tf}
      indicators={activeIndicators}
    />
  );
  const drawingPanel = (
    <DrawingPanel
      drawings={activeDrawings}
      selected={selected}
      onSelect={pickDrawing}
      onAction={drawingAction}
      onVisibility={(id, visible) => {
        engine.current?.controller?.cancel();
        store.updateDrawing(id, { visible });
      }}
    />
  );
  const onStrategy = useCallback(
    (research: StrategyResult | null) => engine.current?.setStrategy(research),
    [],
  );
  const watchlistKey = state.app.watchlist.join(',');
  useEffect(() => {
    if (!state.ready) return;
    let live = true;
    const abort = new AbortController();
    setQuotes({});
    void (async () => {
      for (const ticker of watchlistKey.split(',').filter(Boolean)) {
        if (!live) return;
        try {
          const quote = await providers[providerId].getQuote(ticker, abort.signal);
          if (live) setQuotes((previous) => ({ ...previous, [ticker]: quote }));
        } catch {
          /* A missing quote never blocks chart or watchlist navigation. */
        }
      }
    })();
    return () => {
      live = false;
      abort.abort();
    };
  }, [state.ready, watchlistKey, providerId]);
  const evaluateCurrentAlerts = useCallback(
    (data: BarResult, initialize = false) => {
      const app = store.getSnapshot().app;
      const current = store.symbol(symbol);
      if (app.activeSymbol !== symbol || app.provider !== providerId || current.preferences.timeframe !== tf || document.hidden)
        return;
      const definitions = app.alerts.filter((a) => a.symbol === symbol && a.timeframe === tf);
      if (!definitions.length) return;
      try {
        const evaluated = evaluateAlerts(
          definitions,
          closedBars(data, tf, data.asOf ?? Date.now() / 1000),
          current.drawings,
          initialize,
        );
        const updates = new Map(evaluated.definitions.map((a) => [a.id, a]));
        const alerts = store.getSnapshot().app.alerts.map((a) => updates.get(a.id) ?? a);
        if (JSON.stringify(alerts) !== JSON.stringify(store.getSnapshot().app.alerts))
          store.updateAlerts(alerts);
        if (evaluated.events.length) setNotice(evaluated.events.at(-1)!.message);
      } catch (e) {
        setNotice(reportError('strategy', e));
      }
    },
    [activeDrawings, symbol, tf, providerId],
  );
  useEffect(() => {
    if (!result) return;
    const key = `${symbol}:${tf}:${providerId}`;
    evaluateCurrentAlerts(result, alertSession.current !== key);
    alertSession.current = key;
  }, [result, symbol, tf, providerId, state.app.alerts, s.drawings, evaluateCurrentAlerts]);
  useEffect(() => {
    let live = true,
      pending = false;
    const abort = new AbortController();
    const timer = setInterval(() => {
      if (
        pending ||
        document.hidden ||
        !store
          .getSnapshot()
          .app.alerts.some((a) => a.enabled && a.symbol === symbol && a.timeframe === tf)
      )
        return;
      pending = true;
      // Alert refresh is independent of chart/backtest state and never cancels a drawing gesture.
      void providers[providerId]
        .getBars(symbol, tf, undefined, abort.signal)
        .then((data) => {
          if (live) evaluateCurrentAlerts(data);
        })
        .catch((error) => {
          if (live && !abort.signal.aborted) reportError('market', error);
        })
        .finally(() => {
          pending = false;
        });
    }, 60000);
    return () => {
      live = false;
      abort.abort();
      clearInterval(timer);
    };
  }, [symbol, tf, providerId, evaluateCurrentAlerts]);
  const drawingControls =
    selectedDrawing && tool === 'select' ? (
      <>
        <span>{toolNames[selectedDrawing.type]}</span>
        <IconButton
          label="鎖定所選畫線"
          active={selectedDrawing.locked}
          onClick={() => drawingAction(selectedDrawing.id, 'lock')}
        >
          {selectedDrawing.locked ? <LockKeyhole size={16} /> : <UnlockKeyhole size={16} />}
        </IconButton>
        <IconButton
          label="刪除所選畫線"
          disabled={selectedDrawing.locked}
          onClick={() => drawingAction(selectedDrawing.id, 'delete')}
        >
          <Trash2 size={16} />
        </IconButton>
        <IconButton label="畫線設定" onClick={() => openPanel('drawings')}>
          <Settings2 size={16} />
        </IconButton>
        <IconButton
          label="取消選取"
          onClick={() => {
            engine.current?.setSelection(null);
            setSelected(null);
          }}
        >
          <X size={16} />
        </IconButton>
      </>
    ) : null;
  const backupActions = (
    <div className="backup-actions">
      <button type="button" className="ghost-button" onClick={() => void exportAll()}>
        <Download size={16} />
        匯出設定
      </button>
      <button type="button" className="ghost-button" onClick={() => importRef.current?.click()}>
        <Upload size={16} />
        匯入設定
      </button>
      <button type="button" className="ghost-button" onClick={() => setHelp(true)}>
        <HelpCircle size={16} />
        操作說明
      </button>
    </div>
  );
  const renderPanel = (panel: DockTab) => (
    <PanelBoundary key={`${symbol}:${panel}`}>
      {panel === 'watchlist' ? (
        watchPanel
      ) : panel === 'indicators' ? (
        indicatorPanel
      ) : panel === 'drawings' ? (
        <>
          {drawingPanel}
          {measurement && (
            <p className="small" aria-label="測量資訊">
              價差 {market.currency} {measurement.priceDelta.toFixed(2)} ·{' '}
              {measurement.pricePercent === null
                ? '不適用'
                : measurement.pricePercent.toFixed(2) + '%'}
              <br />
              {measurement.barDelta.toFixed(1)} 根 K 棒 · 經過時間 {measurement.humanDuration}
            </p>
          )}
          {selectedDrawing && (
            <DrawingSettings
              store={store}
              drawing={selectedDrawing}
              onCancelGesture={() => engine.current?.controller?.cancel()}
            />
          )}
        </>
      ) : panel === 'backtest' ? (
        <BacktestPanel symbol={symbol} timeframe={tf} market={market} result={result} onResult={onStrategy} />
      ) : panel === 'alerts' ? (
        <AlertPanel store={store} symbol={symbol} timeframe={tf} drawings={activeDrawings} />
      ) : (
        <>
          <label className="provider-field">
            <span>資料來源</span>
            <select
              aria-label="資料來源"
              value={providerId}
              onChange={(e) => store.updateApp({ provider: e.target.value as ProviderId })}
            >
              {(Object.keys(providers) as ProviderId[]).map((id) => (
                <option key={id} value={id} disabled={id === 'yahoo' && STATIC_HOSTING}>
                  {providerLabels[id]}
                </option>
              ))}
            </select>
          </label>
          <button className="primary-button" type="button" onClick={() => setReloadToken((n) => n + 1)}>
            <RefreshCw size={15} />
            重新整理行情
          </button>
          {backupActions}
          <WorkspaceSettings store={store} />
        </>
      )}
    </PanelBoundary>
  );
  const watchPanel = (
    <Watchlist
      symbols={state.app.watchlist}
      active={symbol}
      quotes={quotes}
      catalogEntries={catalogEntries}
      onSelect={selectSymbol}
      onChange={(watchlist) => store.updateApp({ watchlist })}
    />
  );
  const chartIndicators = [...activeIndicators].sort(
    (a, b) => Number(a.type === 'Volume') - Number(b.type === 'Volume'),
  );
  const showLegacyVolume =
    legacyVolumeEnabled(s.preferences, tf) && !activeIndicators.some((i) => i.type === 'Volume');
  const changeTone = toneClass(headerQuote?.change);
  const inWatchlist = state.app.watchlist.includes(symbol);
  const peTitle = (label: string, value: number | null) =>
    value !== null
      ? `${label}${peFigures?.source ? ` · ${peFigures.source}` : ''}${peFigures?.asOf ? ` · ${peFigures.asOf}` : ''}`
      : `${label}：${peFigures?.reason ?? '暫無資料'}`;
  return (
    <div
      ref={chartFocus.containerRef}
      className={`app-shell ${tablet && sheet ? 'tablet-context-open' : ''} ${chartFocus.isFocused ? 'chart-focus' : ''}`}
    >
      <header className="ws-header" inert={modal} aria-hidden={modal}>
        <button
          type="button"
          className="ws-symbol"
          aria-label={`切換股票（目前 ${symbol}）`}
          title="搜尋並切換股票"
          onClick={() => onOpenSearch?.()}
        >
          <span className="ws-symbol-text">
            <b data-testid="active-symbol">{symbol}</b>
            <small>
              {companyName ?? marketLabel}
              <span className="ws-market-tag">{marketLabel}</span>
            </small>
          </span>
          {onOpenSearch ? <Search size={16} className="ws-symbol-icon" /> : <ChevronDown size={16} className="ws-symbol-icon" />}
        </button>
        <div className={`ws-quote ${changeTone}`} aria-live="polite" data-testid="header-quote">
          <b>{headerQuote ? formatPrice(headerQuote.price) : '—'}</b>
          <span>
            {headerQuote ? `${headerQuote.change > 0 ? '+' : ''}${formatPrice(headerQuote.change)}` : ''}
            {headerQuote ? ` (${formatPercent(headerQuote.changePercent)})` : ''}
          </span>
          <small>{market.currency}</small>
        </div>
        {!isIndex && providerId !== 'demo' && (
          <dl className="ws-valuation" data-testid="valuation">
            <div title={peTitle(`本益比（${peFigures?.fiscalYear ?? '年度'} EPS）`, peFigures?.pe ?? null)}>
              <dt>本益比</dt>
              <dd>{formatRatio(peFigures?.pe)}</dd>
            </div>
            <div title={peTitle('本益比 TTM（近四季 EPS）', peFigures?.peTtm ?? null)}>
              <dt>本益比 TTM</dt>
              <dd>{formatRatio(peFigures?.peTtm)}</dd>
            </div>
          </dl>
        )}
        <div className="ws-header-actions">
          {providerId === 'demo' && (
            <button
              className="demo-switch"
              type="button"
              title="改用真實的延遲行情；你的設定仍保留在這台裝置。"
              onClick={() => store.updateApp({ provider: SCREENER_HOSTING ? 'market' : 'snapshot' })}
            >
              模擬資料 · 改用真實行情
            </button>
          )}
          <IconButton
            label={inWatchlist ? '已在自選清單' : '加入自選清單'}
            active={inWatchlist}
            disabled={inWatchlist}
            onClick={() => store.updateApp({ watchlist: [...state.app.watchlist, symbol] })}
          >
            <span className="star-glyph" aria-hidden="true">{inWatchlist ? '★' : '☆'}</span>
          </IconButton>
          <IconButton label="重新整理行情" onClick={() => setReloadToken((n) => n + 1)}>
            <RefreshCw size={17} />
          </IconButton>
        </div>
      </header>
      <div className="chart-toolbar" inert={modal} aria-hidden={modal}>
        <div className="timeframe-buttons" ref={timeframesRef} role="group" aria-label="K 線週期">
          {timeframes
            .filter((t) => availableTimeframes.includes(t))
            .map((t) => (
              <button
                key={t}
                type="button"
                disabled={!supportsTimeframe(providers[providerId], t)}
                title={!supportsTimeframe(providers[providerId], t) ? '此資料來源不支援' : undefined}
                className={tf === t ? 'active' : ''}
                aria-label={`週期 ${t}`}
                aria-pressed={tf === t}
                onClick={() =>
                  store.updateSymbol(symbol, (s) => ({
                    ...s,
                    preferences: { ...s.preferences, timeframe: t as Timeframe },
                  }))
                }
              >
                {t}
              </button>
            ))}
        </div>
        <span className="toolbar-divider" aria-hidden="true" />
        <button
          type="button"
          className="toolbar-button"
          aria-label="指標"
          onClick={() => (mobile || chartFocus.isFocused ? openPanel('indicators') : toggleDock('indicators'))}
        >
          <SlidersHorizontal size={16} />
          <span>指標</span>
        </button>
        <button
          ref={drawingLauncherRef}
          type="button"
          className={`toolbar-button drawing-tools-launcher ${tool !== 'select' ? 'active' : ''}`}
          aria-label="繪圖工具"
          aria-haspopup="dialog"
          aria-expanded={drawingPickerOpen}
          aria-pressed={tool !== 'select'}
          data-active-tool={tool}
          title={`繪圖工具 · ${tool === 'select' ? '選取 / 平移' : toolNames[tool]}`}
          onClick={openDrawingPicker}
        >
          <DrawingToolGlyph tool={tool} />
          <span>繪圖</span>
        </button>
        {!mobile && (
          <div className="chart-status-quick-actions" aria-label="快速操作">
            <IconButton label="復原畫線" disabled={!history.canUndo} onClick={undoDrawing}>
              <Undo2 size={17} />
            </IconButton>
            <IconButton label="重做畫線" disabled={!history.canRedo} onClick={redoDrawing}>
              <Redo2 size={17} />
            </IconButton>
            <IconButton label="放大圖表" onClick={() => engine.current?.zoom(0.8)}>
              <ZoomIn size={17} />
            </IconButton>
            <IconButton label="縮小圖表" onClick={() => engine.current?.zoom(1.25)}>
              <ZoomOut size={17} />
            </IconButton>
          </div>
        )}
        <div className="chart-toolbar-right">
          <IconButton
            className="focus-toggle"
            label={chartFocus.isFocused ? '離開全螢幕' : '全螢幕圖表'}
            active={chartFocus.isFocused}
            onClick={() => void (chartFocus.isFocused ? chartFocus.exit() : chartFocus.enter())}
          >
            {chartFocus.isFocused ? <Minimize2 size={18} /> : <Expand size={18} />}
          </IconButton>
          {mobile || chartFocus.isFocused ? (
            <IconButton label="更多面板" onClick={() => setSheet(rightTab)}>
              <MoreHorizontal size={19} />
            </IconButton>
          ) : (
            <IconButton label={right ? '收合側欄' : '展開側欄'} onClick={() => setRight(!right)}>
              {right ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}
            </IconButton>
          )}
        </div>
      </div>
      <div className="workspace" inert={modal} aria-hidden={modal}>
        <main className="main-workspace">
          <div className="chart-workspace">
            <div className="chart-stage">
              <div className="chart-overlay" role="group" aria-label={`${symbol} 圖例`}>
                <div className="chart-heading">
                  <b>{displayTicker(symbol)}</b>
                  <span>· {tf} · {marketLabel}</span>
                  <span className={`demo-badge ${providerId}`}>
                    {providerId === 'demo' ? '模擬' : providerId === 'yahoo' ? '後端' : '延遲'}
                  </span>
                </div>
                <div ref={headerRef} className="ohlc-header" data-testid="ohlc-header" />
                <div className="indicator-chips" ref={legendRef}>
                  {chartIndicators.map((i) => {
                    const repeatedPeriod =
                      i.type !== 'Volume' &&
                      activeIndicators.some(
                        (other) =>
                          other.id !== i.id && other.type !== 'Volume' && other.period === i.period,
                      );
                    return (
                      <div
                        className={`indicator-chip ${i.visible ? '' : 'hidden-indicator'}`}
                        key={i.id}
                        data-indicator-id={i.id}
                      >
                        <span className="indicator-name" style={{ color: i.color }}>
                          {i.type === 'Volume'
                            ? '成交量'
                            : `${i.type} ${i.period}${repeatedPeriod ? ` ${i.source.toUpperCase()}` : ''}`}
                        </span>
                        <span className="indicator-value" data-indicator-value />
                        {i.type === 'Volume' && (
                          <span className="indicator-average">
                            <span>MA20</span>
                            <span data-volume-average />
                          </span>
                        )}
                      </div>
                    );
                  })}
                  {showLegacyVolume && (
                    <div className="legacy-volume-chip" data-legacy-volume>
                      <span className="indicator-name">成交量</span>
                      <span className="indicator-value" data-volume-value />
                      <span className="indicator-average">
                        <span>MA20</span>
                        <span data-volume-average />
                      </span>
                    </div>
                  )}
                </div>
                <IconButton className="manage-indicators" label="管理指標" onClick={() => openPanel('indicators')}>
                  <Settings2 size={16} />
                </IconButton>
              </div>
              <div className="chart-host" ref={hostRef} data-testid="chart" />
              {(loading || !state.ready) && (
                <div className="chart-loading">
                  <span className="loading-ring" />
                  載入圖表…
                </div>
              )}
              {dataError && (
                <div className="chart-loading error">
                  <b>行情資料暫時無法取得</b>
                  <p>{dataError}</p>
                  <div className="error-actions">
                    <button className="primary-button" type="button" onClick={() => setReloadToken((n) => n + 1)}>
                      <RefreshCw size={15} />
                      重試
                    </button>
                    {providerId !== 'demo' && (
                      <button className="ghost-button" type="button" onClick={() => store.updateApp({ provider: 'demo' })}>
                        改看離線模擬資料
                      </button>
                    )}
                  </div>
                </div>
              )}
              <div className="chart-watermark" aria-hidden="true">
                {displayTicker(symbol)}
                <span>{companyName ?? 'ATLAS'}</span>
              </div>
              {!mobile && drawingControls && (
                <div className="floating-drawing-toolbar">{drawingControls}</div>
              )}
            </div>
          </div>
          <div className={`chart-status ${mobile && drawingControls ? 'has-drawing-controls' : ''}`}>
            <span>
              {tool === 'select'
                ? mobile ? '拖曳平移 · 雙指縮放' : '拖曳平移 · 滾輪縮放 · Esc 取消'
                : `${toolNames[tool]} · 每個端點：按住 → 拖曳 → 放開`}
            </span>
            <span className="status-counts">
              畫線 {activeDrawings.length} 條 <span className="status-divider">/</span> 指標 {activeIndicators.length} 個
              <span className="status-divider">/</span>
              <span className={`save-state ${state.storageError ? 'danger-text' : ''}`} data-testid="save-state">
                {state.storageError ? '儲存失敗' : state.saving ? '儲存中…' : '已儲存於本機'}
              </span>
            </span>
            {mobile && drawingControls && (
              <div className="mobile-drawing-controls" aria-label="所選畫線操作">
                {drawingControls}
              </div>
            )}
          </div>
          <div className="source-strip">
            <div ref={sourceRef} className="data-source" />
            {(STATIC_HOSTING || providerId === 'demo') && (
              <p
                className="static-hosting-note"
                title={`${STATIC_HOSTING ? staticHostingText : ''}${
                  providerId === 'demo' ? `${STATIC_HOSTING ? ' ' : ''}${demoDataText}` : ''
                }`}
              >
                {providerId === 'demo' ? demoDataText : staticHostingText}
              </p>
            )}
            <div className="events-source">{eventsStatus}</div>
          </div>
        </main>
        {right && !mobile && (
          <aside className="details-panel" aria-label={panelNames[rightTab]}>
            <div className="panel-heading">
              <h2>{panelNames[rightTab]}</h2>
              <span className="panel-symbol">{displayTicker(symbol)}</span>
            </div>
            <div className="details-content">{renderPanel(rightTab)}</div>
          </aside>
        )}
        {!mobile && (
          <nav className="dock-rail" aria-label="工作區面板">
            {dockTabs.map(({ id, icon: Icon }) => (
              <button
                key={id}
                type="button"
                className={right && rightTab === id ? 'active' : ''}
                aria-pressed={right && rightTab === id}
                aria-label={panelNames[id]}
                title={panelNames[id]}
                onClick={() => toggleDock(id)}
              >
                <Icon size={18} />
              </button>
            ))}
          </nav>
        )}
      </div>
      <input
        ref={importRef}
        className="sr-only"
        type="file"
        accept="application/json,.json"
        aria-label="設定檔"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void importAll(file);
          e.target.value = '';
        }}
      />
      {state.app.workspace.debug && (
        <pre className="debug-view" data-testid="debug">
          {JSON.stringify(
            {
              provider: providerId,
              symbol,
              timeframe: tf,
              bars: result?.bars.length ?? 0,
              drawings: activeDrawings.length,
              indicators: activeIndicators.length,
              storage: state.storageError ?? (state.saving ? 'saving' : 'saved'),
              errors,
            },
            null,
            2,
          )}
        </pre>
      )}
      {drawingPickerOpen && active && (
        <DrawingToolPicker
          activeTool={tool}
          canRedo={history.canRedo}
          canUndo={history.canUndo}
          magnetEnabled={s.preferences.magnet}
          mode={mobile ? 'sheet' : 'desktop'}
          onClose={closeDrawingPicker}
          onChooseTool={chooseTool}
          onRedo={redoDrawing}
          onResetView={() => engine.current?.resetView()}
          onShowFuture={() => engine.current?.futureArea()}
          onToggleMagnet={toggleMagnet}
          onUndo={undoDrawing}
          onZoomIn={() => engine.current?.zoom(0.8)}
          onZoomOut={() => engine.current?.zoom(1.25)}
        />
      )}
      {sheet && active && (
        <div
          className={`sheet-backdrop ${tablet ? 'tablet-context' : ''}`}
          onClick={() => setSheet(null)}
        >
          <div
            className="bottom-sheet"
            role="dialog"
            aria-modal={!tablet}
            aria-label={`${panelNames[sheet]}面板`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sheet-handle" />
            <div className="sheet-heading">
              <b>{panelNames[sheet]}</b>
              <IconButton label="關閉面板" onClick={() => setSheet(null)}>
                <X size={19} />
              </IconButton>
            </div>
            <div className="sheet-tabs" role="tablist" aria-label="面板">
              {dockTabs.map(({ id, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={sheet === id}
                  className={sheet === id ? 'active' : ''}
                  onClick={() => {
                    setSheet(id);
                    setRightTab(id);
                  }}
                >
                  <Icon size={16} />
                  <span>{panelNames[id]}</span>
                </button>
              ))}
            </div>
            {renderPanel(sheet)}
          </div>
        </div>
      )}
      {help && active && (
        <div className="sheet-backdrop" onClick={() => setHelp(false)}>
          <div
            className="help-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="操作說明"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sheet-heading">
              <b>精準畫線</b>
              <IconButton label="關閉說明" onClick={() => setHelp(false)}>
                <X size={18} />
              </IconButton>
            </div>
            <ol>
              <li>選擇趨勢線。按住、拖曳、放開設定第一點。</li>
              <li>再次按住、拖曳、放開設定第二點；過程中會顯示預覽。</li>
              <li>選取線條後可拖曳端點或整條線。</li>
              <li>手機放點／修改端點時會出現 2.75× 放大鏡。</li>
              <li>鎖定後線條固定，拖曳改為平移圖表。</li>
              <li>繪圖工具中的「未來區域」可在最後一根 K 棒右側直接畫線。</li>
            </ol>
            <p>
              Ctrl / Cmd + Z：復原
              <br />
              Ctrl / Cmd + Shift + Z：重做
              <br />
              Esc：取消手勢 · Delete：刪除未鎖定的畫線
            </p>
            <p className="muted small">匯入會以檔案內容取代所有本機設定，請先匯出備份。</p>
          </div>
        </div>
      )}
      {active && (notice || state.storageError) && (
        <div className="notice" role="status">
          {state.storageError ?? notice}
          <button aria-label="關閉通知" onClick={() => setNotice('')}>
            <X size={16} />
          </button>
        </div>
      )}
    </div>
  );
}

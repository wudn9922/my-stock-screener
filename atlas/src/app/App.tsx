import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import {
  Activity,
  ChartNoAxesCombined,
  ChevronRight,
  Download,
  Upload,
  Search,
  FileBarChart,
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
  ZoomIn,
  ZoomOut,
  Minimize2,
  X,
  HelpCircle,
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
import { FinancialPanel } from '../ui/FinancialPanel';
import { DrawingPanel } from '../ui/DrawingPanel';
import { DrawingSettings } from '../ui/DrawingSettings';
import { WorkspaceSettings } from '../ui/WorkspaceSettings';
import { BacktestPanel } from '../ui/BacktestPanel';
import { AlertPanel } from '../ui/AlertPanel';
import { PanelBoundary } from '../ui/PanelBoundary';
import { CachedMarketDataProvider } from '../market-data/CachedMarketDataProvider';
import { closedBars } from '../market-data/MarketDataProvider';
import { evaluateAlerts } from '../alerts/AlertEngine';
import { filingEvents } from '../events/FilingEvents';
import type { CompanyFundamentals } from '../fundamentals/FundamentalsProvider';
import type { StrategyResult } from '../strategy';
import { errorLog, reportError } from '../errors/UserErrors';
import { SCREENER_HOSTING, STATIC_HOSTING } from './HostingMode';
import { parseLaunchParams } from './LaunchParams';
import { legacyVolumeEnabled } from '../storage/schema';
import { getMarketProfile } from '../market-data/MarketProfile';
import { loadSymbolCatalog, resolveSymbolInput, searchSymbolCatalog, type SymbolCatalogEntry } from '../market-data/SymbolCatalog';
type Panel = 'indicators' | 'drawings' | 'financials' | 'backtest' | 'alerts' | 'settings';
const store = new AppStore();
const mobileMedia = window.matchMedia('(max-width:1099px)');
const subscribeMobile = (cb: () => void) => {
  mobileMedia.addEventListener('change', cb);
  return () => mobileMedia.removeEventListener('change', cb);
};
const providers = {
  demo: new CachedMarketDataProvider(new DemoProvider()),
  snapshot: new CachedMarketDataProvider(new SnapshotProvider()),
  yahoo: STATIC_HOSTING ? new YahooProvider() : new CachedMarketDataProvider(new YahooProvider()),
};
const staticHostingText = SCREENER_HOSTING
  ? 'GitHub Pages：Yahoo 延遲行情快照與預先下載的美股 SEC 財報，每日更新、非即時；即時 Yahoo 仍需 backend。台股、指數與 ETF 無 SEC 財報。'
  : 'GitHub Pages：延遲行情與 SEC 財報快照可用，均非即時；即時 Yahoo 仍需 backend。台股財報尚未提供。';
type ProviderId = keyof typeof providers;
/** `?symbol=…&tf=…` from the page URL (my-stock-screener report links), applied once per page load. */
const launchRequest = parseLaunchParams(window.location.search);
let launchApplied = false;
const demoDataText = 'DEMO 價格為模擬資料，並非市場行情。';
const supportsTimeframe = (
  provider: { supportedTimeframes: readonly Timeframe[] },
  timeframe: Timeframe,
) => provider.supportedTimeframes.includes(timeframe);
export function App() {
  const mobile = useSyncExternalStore(subscribeMobile, () => mobileMedia.matches);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot),
    symbol = state.app.activeSymbol,
    s = store.symbol(symbol),
    tf = s.preferences.timeframe;
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
    [search, setSearch] = useState(''),
    [result, setResult] = useState<BarResult | null>(null),
    [quotes, setQuotes] = useState<Record<string, Quote>>({}),
    [eventsStatus, setEventsStatus] = useState('Corporate events: unavailable in Demo'),
    [sheet, setSheet] = useState<'watchlist' | Panel | null>(null),
    [help, setHelp] = useState(false),
    [drawingPickerOpen, setDrawingPickerOpen] = useState(false),
    [reloadToken, setReloadToken] = useState(0);
  const [catalogEntries, setCatalogEntries] = useState<SymbolCatalogEntry[]>([]);
  const [symbolTimeframes, setSymbolTimeframes] = useState<{ key: string; values: readonly Timeframe[] } | null>(null);
  const market = getMarketProfile(symbol);
  const marketLabel = symbol.startsWith('^')
    ? (market.market === 'TW' ? '台股指數' : '指數')
    : market.market === 'TW' ? (market.exchange === 'TWSE' ? '台股上市' : '台股上櫃') : 'NASDAQ / NYSE';
  const companyName = companies[symbol] ?? catalogEntries.find((entry) => entry.symbol === symbol)?.name;
  const timeframeKey = `${state.app.provider}:${symbol}`;
  const availableTimeframes = symbolTimeframes?.key === timeframeKey ? symbolTimeframes.values : providers[state.app.provider].supportedTimeframes;
  useEffect(() => {
    const abort = new AbortController();
    void loadSymbolCatalog(abort.signal).then(setCatalogEntries).catch(() => {
      // Explicit exchange suffixes and US navigation remain usable if the directory is unavailable.
    });
    return () => abort.abort();
  }, []);
  useEffect(() => {
    if (!state.ready) return;
    const abort = new AbortController();
    const provider: MarketDataProvider = providers[state.app.provider];
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
  }, [state.ready, symbol, state.app.provider, timeframeKey, reloadToken]);
  const right = state.app.workspace.rightOpen,
    rightTab = state.app.workspace.rightTab;
  const setRight = (rightOpen: boolean) =>
    store.updateApp({ workspace: { ...store.getSnapshot().app.workspace, rightOpen } });
  const setRightTab = (rightTab: Panel) =>
    store.updateApp({ workspace: { ...store.getSnapshot().app.workspace, rightTab } });
  const tablet = mobile && window.innerWidth >= 768;
  const modal = (!!sheet && !tablet) || help || drawingPickerOpen;
  const errors = useSyncExternalStore(errorLog.subscribe, errorLog.getSnapshot);
  const alertSession = useRef('');
  const financialRecords = useRef<{ symbol: string; records: CompanyFundamentals[] }>({
    symbol: '',
    records: [],
  });
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
    const active = strip?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]');
    if (!strip || !active) return;
    const stripRect = strip.getBoundingClientRect();
    const buttonRect = active.getBoundingClientRect();
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
  }, [centerActiveTimeframe, chartFocus.isFocused, mobile, state.ready, tf]);
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
    void store.initialize();
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
    engine.current = chart;
    return () => {
      chart.destroy();
      engine.current = null;
    };
  }, [state.ready]);
  useEffect(() => {
    const chart = engine.current;
    if (!state.ready || !chart) return;
    const abort = new AbortController();
    let active = true;
    chart.clear();
    setTool('select');
    setSelected(null);
    setLoading(true);
    setResult(null);
    setDataError('');
      setEventsStatus(
        STATIC_HOSTING && state.app.provider === 'yahoo'
          ? 'Corporate events: backend required'
          : 'Corporate events: loading…',
      );
    const provider = providers[state.app.provider];
    if (!supportsTimeframe(provider, tf)) {
      store.updateSymbol(symbol, (s) => ({
        ...s,
        preferences: { ...s.preferences, timeframe: '1D' },
      }));
      return () => {
        active = false;
        abort.abort();
      };
    }
    void (provider as MarketDataProvider)
      .getBars(symbol, tf, undefined, abort.signal)
      .then((result) => {
        if (!active) return;
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
        if (financialRecords.current.symbol === symbol)
          chart.setFilings(filingEvents(financialRecords.current.records));
        setResult(result);
        setLoading(false);
        void provider
          .getCorporateEvents(symbol, undefined, abort.signal)
          .then((events) => {
            if (!active) return;
            setEventsStatus(
              events.status === 'available'
                ? `${events.events.length} corporate events · ${events.source}`
                : 'Corporate events: unavailable for this provider',
            );
            if (events.status === 'available') chart.setCorporateEvents(events.events);
          })
          .catch(() => {
            if (active) setEventsStatus('Corporate events: source unavailable');
          });
      })
      .catch((e) => {
        if (active) {
          setDataError(reportError('market', e));
          setLoading(false);
        }
      });
    return () => {
      active = false;
      abort.abort();
      chart.flushView();
    };
  }, [state.ready, symbol, tf, state.app.provider, reloadToken]);
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
  }, [selected, sheet, help, drawingPickerOpen, closeDrawingPicker, tool, chartFocus.isFocused, chartFocus.exit]);
  useEffect(() => {
    const save = () => engine.current?.flushView();
    window.addEventListener('pagehide', save);
    return () => window.removeEventListener('pagehide', save);
  }, []);
  /** Shared by the search box and URL launch parameters. `provider` forces a data source. */
  const switchSymbol = (symbol: string, provider?: ProviderId) => {
    engine.current?.controller?.cancel();
    store.updateApp({
      activeSymbol: symbol,
      ...(provider
        ? { provider }
        : getMarketProfile(symbol).market === 'TW' && store.getSnapshot().app.provider === 'demo' ? { provider: 'snapshot' as const } : {}),
      recentSymbols: [
        symbol,
        ...store.getSnapshot().app.recentSymbols.filter((x) => x !== symbol),
      ].slice(0, 20),
    });
    setSheet(null);
    setSearch('');
    setNotice(getMarketProfile(symbol).market === 'TW' ? '台股行情使用 TWD 延遲快照；尚未收錄的股票可在有 backend 的環境使用 Yahoo。' : '');
  };
  const selectSymbol = (value: string) => {
    try {
      switchSymbol(resolveSymbolInput(value, catalogEntries));
    } catch (e) {
      setNotice(String(e));
    }
  };
  useEffect(() => {
    if (!state.ready || launchApplied || !launchRequest) return;
    launchApplied = true;
    const request = launchRequest;
    void (async () => {
      if (request.error || !request.symbol) {
        setNotice(String(new Error(request.error ?? '網址中的股票代號無效。')));
        return;
      }
      let symbol: string;
      try {
        const entries = request.needsCatalog
          ? await loadSymbolCatalog().catch(() => [] as SymbolCatalogEntry[])
          : [];
        symbol = resolveSymbolInput(request.symbol, entries);
      } catch (e) {
        setNotice(String(e));
        return;
      }
      // Demo prices are fictional; a linked real symbol on Pages opens the delayed Yahoo snapshot.
      const providerId: ProviderId = STATIC_HOSTING ? 'snapshot' : store.getSnapshot().app.provider;
      let notice = request.ignoredTimeframe ? `網址週期 ${request.ignoredTimeframe} 無效，已忽略。` : '';
      if (request.timeframe) {
        const provider: MarketDataProvider = providers[providerId];
        if (!supportsTimeframe(provider, request.timeframe)) {
          notice = `${request.timeframe} 不適用於目前的資料來源，維持原週期。`;
        } else {
          const available = provider.getSymbolTimeframes
            ? await provider.getSymbolTimeframes(symbol).catch(() => undefined)
            : undefined;
          const current = store.symbol(symbol).preferences.timeframe;
          const target: Timeframe | undefined =
            !available || available.includes(request.timeframe)
              ? request.timeframe
              : available.includes(current) ? undefined : available.includes('1D') ? '1D' : undefined;
          if (available && !available.includes(request.timeframe))
            notice = `${symbol} 沒有 ${request.timeframe} 快照資料，${target ? `改用 ${target}` : '維持原週期'}。`;
          if (target && target !== current)
            store.updateSymbol(symbol, (s) => ({ ...s, preferences: { ...s.preferences, timeframe: target } }));
        }
      }
      switchSymbol(symbol, STATIC_HOSTING ? providerId : undefined);
      if (notice) setNotice(notice);
    })();
    // switchSymbol is recreated each render; this runs once, after the store is ready.
  }, [state.ready]);
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
  const manageIndicators = () => {
    setRightTab('indicators');
    setRight(true);
    if (mobile || chartFocus.isFocused) setSheet('indicators');
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
  const onFinancialRecords = useCallback(
    (records: CompanyFundamentals[]) => {
      if (store.getSnapshot().app.activeSymbol === symbol) {
        financialRecords.current = { symbol, records };
        engine.current?.setFilings(filingEvents(records));
      }
    },
    [symbol],
  );
  const onStrategy = useCallback(
    (research: StrategyResult | null) => engine.current?.setStrategy(research),
    [],
  );
  const watchlistKey = state.app.watchlist.join(',');
  useEffect(() => {
    if (!state.ready) return;
    let active = true;
    const abort = new AbortController();
    setQuotes({});
    void (async () => {
      for (const ticker of watchlistKey.split(',').filter(Boolean)) {
        if (!active) return;
        try {
          const quote = await providers[state.app.provider].getQuote(ticker, abort.signal);
          if (active) setQuotes((previous) => ({ ...previous, [ticker]: quote }));
        } catch {
          /* A missing quote never blocks chart or watchlist navigation. */
        }
      }
    })();
    return () => {
      active = false;
      abort.abort();
    };
  }, [state.ready, watchlistKey, state.app.provider]);
  const evaluateCurrentAlerts = useCallback(
    (data: BarResult, initialize = false) => {
      const app = store.getSnapshot().app;
      const current = store.symbol(symbol);
      if (app.activeSymbol !== symbol || app.provider !== state.app.provider || current.preferences.timeframe !== tf || document.hidden)
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
    [activeDrawings, symbol, tf, state.app.provider],
  );
  useEffect(() => {
    if (!result) return;
    const key = `${symbol}:${tf}:${state.app.provider}`;
    evaluateCurrentAlerts(result, alertSession.current !== key);
    alertSession.current = key;
  }, [result, symbol, tf, state.app.provider, state.app.alerts, s.drawings, evaluateCurrentAlerts]);
  useEffect(() => {
    let active = true,
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
      void providers[state.app.provider]
        .getBars(symbol, tf, undefined, abort.signal)
        .then((data) => {
          if (active) evaluateCurrentAlerts(data);
        })
        .catch((error) => {
          if (active && !abort.signal.aborted) reportError('market', error);
        })
        .finally(() => {
          pending = false;
        });
    }, 60000);
    return () => {
      active = false;
      abort.abort();
      clearInterval(timer);
    };
  }, [symbol, tf, state.app.provider, evaluateCurrentAlerts]);
  const drawingControls =
    selectedDrawing && tool === 'select' ? (
      <>
        <span>{toolNames[selectedDrawing.type]}</span>
        <IconButton
          label="Lock selected drawing"
          active={selectedDrawing.locked}
          onClick={() => drawingAction(selectedDrawing.id, 'lock')}
        >
          {selectedDrawing.locked ? <LockKeyhole size={16} /> : <UnlockKeyhole size={16} />}
        </IconButton>
        <IconButton
          label="Delete selected drawing"
          disabled={selectedDrawing.locked}
          onClick={() => drawingAction(selectedDrawing.id, 'delete')}
        >
          <Trash2 size={16} />
        </IconButton>
        <IconButton
          label="Drawing settings"
          onClick={() => {
            setRightTab('drawings');
            setRight(true);
            if (mobile) setSheet('drawings');
          }}
        >
          <Settings2 size={16} />
        </IconButton>
        <IconButton
          label="Deselect drawing"
          onClick={() => {
            engine.current?.setSelection(null);
            setSelected(null);
          }}
        >
          <X size={16} />
        </IconButton>
      </>
    ) : null;
  const renderPanel = (panel: Panel) => (
    <PanelBoundary key={`${symbol}:${panel}`}>
      {panel === 'indicators' ? (
        indicatorPanel
      ) : panel === 'drawings' ? (
        <>
          {drawingPanel}
          {measurement && (
            <p className="small" aria-label="Measurement details">
              Price Δ {market.currency} {measurement.priceDelta.toFixed(2)} ·{' '}
              {measurement.pricePercent === null
                ? 'N/A'
                : measurement.pricePercent.toFixed(2) + '%'}
              <br />
              {measurement.barDelta.toFixed(1)} trading bar intervals · elapsed wall time{' '}
              {measurement.humanDuration}
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
      ) : panel === 'financials' ? (
        <FinancialPanel
          symbol={symbol}
          simulatedMarket={state.app.provider === 'demo'}
          onRecords={onFinancialRecords}
        />
      ) : panel === 'backtest' ? (
        <BacktestPanel symbol={symbol} timeframe={tf} market={market} result={result} onResult={onStrategy} />
      ) : panel === 'alerts' ? (
        <AlertPanel store={store} symbol={symbol} timeframe={tf} drawings={activeDrawings} />
      ) : (
        <>
          <button className="primary-button" onClick={() => setReloadToken((n) => n + 1)}>
            Refresh market data
          </button>
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
  return (
    <div
      ref={chartFocus.containerRef}
      className={`app-shell ${tablet && sheet ? 'tablet-context-open' : ''} ${chartFocus.isFocused ? 'chart-focus' : ''}`}
    >
      <header className="topbar" inert={modal} aria-hidden={modal}>
        <a href="/" className="brand" aria-label="Atlas home">
          <span className="brand-icon">
            <ChartNoAxesCombined size={23} />
          </span>
          <b>ATLAS</b>
          <span className="brand-divider" />
          <small>RESEARCH TERMINAL</small>
        </a>
        <form
          className="symbol-search"
          onSubmit={(e) => {
            e.preventDefault();
            selectSymbol(search);
          }}
        >
          <Search size={16} />
          <input
            aria-label="Symbol search"
            list="symbol-options"
            placeholder="搜尋 AAPL / 台股 2330"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                selectSymbol(e.currentTarget.value);
              }
            }}
            required
          />
          <datalist id="symbol-options">
            {[...new Set([...state.app.watchlist, ...state.app.recentSymbols])].map((t) => (
              <option key={t} value={t}>
                {companies[t] ?? catalogEntries.find((entry) => entry.symbol === t)?.name ?? t}
              </option>
            ))}
            {searchSymbolCatalog(search, catalogEntries).filter((entry) => !state.app.watchlist.includes(entry.symbol) && !state.app.recentSymbols.includes(entry.symbol)).map((entry) => (
              <option key={entry.symbol} value={entry.symbol}>{entry.name} · {entry.market}</option>
            ))}
          </datalist>
          <kbd>↵</kbd>
        </form>
        <div className="top-actions">
          <span className="phase-tag">V1</span>
          <IconButton label="Export settings" onClick={() => void exportAll()}>
            <Download size={18} />
          </IconButton>
          <IconButton label="Import settings" onClick={() => importRef.current?.click()}>
            <Upload size={18} />
          </IconButton>
          <IconButton label="Interaction help" active={help} onClick={() => setHelp(!help)}>
            <HelpCircle size={18} />
          </IconButton>
        </div>
        <input
          ref={importRef}
          className="sr-only"
          type="file"
          accept="application/json,.json"
          aria-label="Settings file"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void importAll(file);
            e.target.value = '';
          }}
        />
      </header>
      <div className="workspace" inert={modal} aria-hidden={modal}>
        <aside className="watchlist-panel">{watchPanel}</aside>
        <main className="main-workspace">
          <div className="chart-toolbar">
            <div className="symbol-title">
              <b data-testid="active-symbol">{symbol}</b>
              <ChevronRight size={13} />
              <span>{companyName ?? marketLabel}</span>
            </div>
            <button
              ref={drawingLauncherRef}
              type="button"
              className={`drawing-tools-launcher ${tool !== 'select' ? 'active' : ''}`}
              aria-label="Drawing Tools"
              aria-haspopup="dialog"
              aria-expanded={drawingPickerOpen}
              aria-pressed={tool !== 'select'}
              data-active-tool={tool}
              title={`Drawing Tools · ${tool === 'select' ? '選取 / 平移' : toolNames[tool]}`}
              onClick={openDrawingPicker}
            >
              <DrawingToolGlyph tool={tool} />
              <span>繪圖</span>
            </button>
            <div className="timeframe-buttons" ref={timeframesRef}>
              {timeframes
                .filter((t) => availableTimeframes.includes(t))
                .map((t) => (
                  <button
                    key={t}
                    disabled={!supportsTimeframe(providers[state.app.provider], t)}
                    title={
                      !supportsTimeframe(providers[state.app.provider], t)
                        ? '此 provider 不支援'
                        : undefined
                    }
                    className={tf === t ? 'active' : ''}
                    aria-label={`Timeframe ${t}`}
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
            <div className="chart-toolbar-right">
              <IconButton label="Refresh market data" onClick={() => setReloadToken((n) => n + 1)}>
                <Activity size={18} />
              </IconButton>
              <select
                aria-label="Market data source"
                value={state.app.provider}
                onChange={(e) =>
                  store.updateApp({ provider: e.target.value as 'demo' | 'snapshot' | 'yahoo' })
                }
              >
                <option value="demo">DEMO · 模擬</option>
                <option value="snapshot">Yahoo · 延遲快照</option>
                <option value="yahoo" disabled={STATIC_HOSTING}>
                  {STATIC_HOSTING ? 'Yahoo · 需要 backend' : 'Yahoo · Backend'}
                </option>
              </select>
              {state.app.provider === 'demo' && (
                <button
                  className="primary-button"
                  type="button"
                  aria-label="Switch to real delayed snapshot quotes"
                  title="Switch to Yahoo delayed snapshot quotes. Your settings stay on this device."
                  onClick={() => store.updateApp({ provider: 'snapshot' })}
                >
                  切換延遲快照
                </button>
              )}
              <IconButton
                className="focus-toggle"
                label={chartFocus.isFocused ? 'Exit chart fullscreen' : 'Enter chart fullscreen'}
                active={chartFocus.isFocused}
                onClick={() => void (chartFocus.isFocused ? chartFocus.exit() : chartFocus.enter())}
              >
                {chartFocus.isFocused ? <Minimize2 size={18} /> : <Expand size={18} />}
              </IconButton>
              <IconButton
                label={right ? 'Collapse details' : 'Expand details'}
                onClick={() => setRight(!right)}
              >
                {right ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}
              </IconButton>
            </div>
          </div>
          <div className="chart-workspace">
            <div className="chart-stage">
              <div className="chart-overlay" role="group" aria-label={`${symbol} chart legend`}>
                <div className="chart-heading">
                  <span className="status-dot" />
                  <b>{symbol}</b>
                  <span>· {tf} · {marketLabel} · {market.currency}</span>
                  <span className="demo-badge">
                    {state.app.provider === 'demo'
                      ? 'SIMULATED'
                      : state.app.provider === 'snapshot'
                        ? 'DELAYED SNAPSHOT'
                        : 'BACKEND'}
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
                            ? 'Volume'
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
                      <span className="indicator-name">Volume</span>
                      <span className="indicator-value" data-volume-value />
                      <span className="indicator-average">
                        <span>MA20</span>
                        <span data-volume-average />
                      </span>
                    </div>
                  )}
                </div>
                <IconButton
                  className="manage-indicators"
                  label="Manage indicators"
                  onClick={manageIndicators}
                >
                  <Settings2 size={17} />
                </IconButton>
              </div>
              <div className="chart-host" ref={hostRef} data-testid="chart" />
              {(loading || !state.ready) && (
                <div className="chart-loading">
                  <span className="loading-ring" />
                  載入工作台…
                </div>
              )}
              {dataError && (
                <div className="chart-loading error">
                  <b>資料來源暫時無法使用</b>
                  <p>{dataError}</p>
                  <button
                    className="primary-button"
                    onClick={() => store.updateApp({ provider: 'demo' })}
                  >
                    使用離線 Demo
                  </button>
                </div>
              )}
              <div className="chart-watermark">
                ATLAS<span>PLAN · OBSERVE · REFINE</span>
              </div>
              {!mobile && drawingControls && (
                <div className="floating-drawing-toolbar">{drawingControls}</div>
              )}
            </div>
          </div>
          <div
            className={`chart-status ${mobile && drawingControls ? 'has-drawing-controls' : ''}`}
          >
            <span>
              {tool === 'select'
                ? '拖曳平移 · 滾輪／雙指縮放'
                : `${toolNames[tool]} · 每個端點：按住 → 拖曳 → 放開`}
            </span>
            {!mobile && (
              <div className="chart-status-quick-actions" aria-label="Quick chart actions">
                <IconButton label="Undo drawing" disabled={!history.canUndo} onClick={undoDrawing}>
                  <Undo2 size={17} />
                </IconButton>
                <IconButton label="Redo drawing" disabled={!history.canRedo} onClick={redoDrawing}>
                  <Redo2 size={17} />
                </IconButton>
                <IconButton label="Zoom in" onClick={() => engine.current?.zoom(0.8)}>
                  <ZoomIn size={17} />
                </IconButton>
                <IconButton label="Zoom out" onClick={() => engine.current?.zoom(1.25)}>
                  <ZoomOut size={17} />
                </IconButton>
              </div>
            )}
            <span>
              {activeDrawings.length} DRAWINGS <span className="status-divider">/</span>{' '}
              {activeIndicators.length} INDICATORS
            </span>
            {mobile && drawingControls && (
              <div className="mobile-drawing-controls" aria-label="Selected drawing controls">
                {drawingControls}
              </div>
            )}
          </div>
          <div ref={sourceRef} className="data-source" />
          {(STATIC_HOSTING || state.app.provider === 'demo') && (
            <p
              className="static-hosting-note"
              title={`${STATIC_HOSTING ? staticHostingText : ''}${
                state.app.provider === 'demo'
                  ? `${STATIC_HOSTING ? ' ' : ''}${demoDataText}`
                  : ''
              }`}
            >
              {STATIC_HOSTING ? staticHostingText : demoDataText}
            </p>
          )}
          <div className="events-source">
            {eventsStatus}
            {market.market === 'US' && ' · SEC Filing markers available when Financials loads'}
          </div>
        </main>
        {right && !mobile && (
          <aside className="details-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">SYMBOL WORKSPACE</span>
                <h2>
                  {symbol}
                  <span>設定</span>
                </h2>
              </div>
              <span className="local-tag">LOCAL</span>
            </div>
            <div className="details-tabs">
              <button
                className={rightTab === 'indicators' ? 'active' : ''}
                onClick={() => setRightTab('indicators')}
              >
                <Activity size={15} />
                Indicators
              </button>
              <button
                className={rightTab === 'drawings' ? 'active' : ''}
                onClick={() => setRightTab('drawings')}
              >
                <Layers size={15} />
                Drawings
              </button>
              <button
                className={rightTab === 'financials' ? 'active' : ''}
                onClick={() => setRightTab('financials')}
              >
                <FileBarChart size={15} />
                Financials
              </button>
            </div>
            <select
              className="research-selector"
              aria-label="Research panel"
              value={rightTab}
              onChange={(e) => setRightTab(e.target.value as Panel)}
            >
              {(
                ['indicators', 'drawings', 'financials', 'backtest', 'alerts', 'settings'] as const
              ).map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <div className="details-content">{renderPanel(rightTab)}</div>
            <div className="details-note">
              <LockKeyhole size={16} />
              <span>
                你的分析，留在你的裝置。
                <br />
                <small>定期 Export 保留備份。</small>
              </span>
            </div>
          </aside>
        )}
      </div>
      <footer className="app-footer" inert={modal} aria-hidden={modal}>
        <div>
          <span className={`status-dot ${state.storageError ? 'danger' : ''}`} />
          {state.storageError ? 'STORAGE ERROR' : state.saving ? 'SAVING…' : 'WORKSPACE SAVED'}
          <span className="footer-divider" />
          IndexedDB · device local
        </div>
        <span>
          Atlas V1 <span className="footer-divider" /> Lightweight Charts™ 5.2.1
        </span>
      </footer>
      <nav className="mobile-nav" inert={modal} aria-hidden={modal}>
        <button onClick={() => setSheet('watchlist')}>
          <List size={19} />
          Watchlist
        </button>
        <button
          onClick={() => {
            setSheet(null);
            chooseTool('select');
          }}
        >
          <ChartNoAxesCombined size={19} />
          Chart
        </button>
        <button onClick={() => setSheet('indicators')}>
          <Activity size={19} />
          SMA
        </button>
        <button onClick={() => setSheet('drawings')}>
          <Layers size={19} />
          Drawings
        </button>
        <button onClick={() => setSheet('backtest')}>
          <Activity size={19} />
          Research
        </button>
        <button onClick={() => setSheet('financials')}>
          <FileBarChart size={19} />
          Financials
        </button>
      </nav>
      {state.app.workspace.debug && (
        <pre className="debug-view" data-testid="debug">
          {JSON.stringify(
            {
              provider: state.app.provider,
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
      {drawingPickerOpen && (
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
      {sheet && (
        <div
          className={`sheet-backdrop ${tablet ? 'tablet-context' : ''}`}
          onClick={() => setSheet(null)}
        >
          <div
            className="bottom-sheet"
            role="dialog"
            aria-modal={!tablet}
            aria-label={`${sheet} panel`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sheet-handle" />
            <div className="sheet-heading">
              <b>{sheet === 'watchlist' ? 'Watchlist' : `${symbol} · ${sheet}`}</b>
              <IconButton label="Close panel" onClick={() => setSheet(null)}>
                <X size={19} />
              </IconButton>
            </div>
            {sheet !== 'watchlist' && (
              <select
                className="research-selector"
                aria-label="Research panel"
                value={sheet}
                onChange={(e) => {
                  setSheet(e.target.value as Panel);
                  setRightTab(e.target.value as Panel);
                }}
              >
                {(
                  [
                    'indicators',
                    'drawings',
                    'financials',
                    'backtest',
                    'alerts',
                    'settings',
                  ] as const
                ).map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            )}
            {sheet === 'watchlist' ? watchPanel : renderPanel(sheet)}
          </div>
        </div>
      )}
      {help && (
        <div className="sheet-backdrop" onClick={() => setHelp(false)}>
          <div
            className="help-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="Interaction guide"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sheet-heading">
              <b>精準畫線</b>
              <IconButton label="Close help" onClick={() => setHelp(false)}>
                <X size={18} />
              </IconButton>
            </div>
            <ol>
              <li>選 Trend Line。按住、拖曳、放開設定 P1。</li>
              <li>再次按住、拖曳、放開設定 P2；過程中顯示 preview。</li>
              <li>選取線條後可拖曳端點或整條線。</li>
              <li>手機放點／端點修改時出現 2.75× Loupe。</li>
              <li>Lock 後線條固定，拖曳改為平移 chart。</li>
              <li>右下 ↗ 按鈕展開 Future Area，最後 K 棒右側可直接畫線。</li>
            </ol>
            <p>
              Ctrl / Cmd + Z：Undo
              <br />
              Ctrl / Cmd + Shift + Z：Redo
              <br />
              Escape：取消手勢 · Delete：刪除未鎖定線條
            </p>
            <p className="muted small">Import 會以檔案內容取代所有本機設定，請先 Export 備份。</p>
          </div>
        </div>
      )}
      {(notice || state.storageError) && (
        <div className="notice" role="status">
          {state.storageError ?? notice}
          <button aria-label="Dismiss notification" onClick={() => setNotice('')}>
            <X size={16} />
          </button>
        </div>
      )}
    </div>
  );
}

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { ChartEngine } from "../chart/ChartEngine";
import { chartStore as store, initializeChartStore } from "../app/chartStore";
import { sitePreferences, upDownColors } from "../app/sitePreferences";
import { aggregateBars } from "../market-data/StaticSeriesProvider";
import {
  getMarketProfile,
  type Bar,
  type Timeframe,
} from "../market-data/MarketDataProvider";
import { toolNames, type ActiveTool } from "../drawing/DrawingModel";
import "./drawableCandles.css";
import { researchDrawingSymbol } from "../report/researchCandles";

export function DrawableCandleChart({
  identity,
  bars,
  source,
  unit,
  precision = 2,
  marketSymbol,
  loading,
  error,
}: {
  identity: string;
  bars: Bar[];
  source: string;
  unit: string;
  precision?: number;
  marketSymbol?: string;
  loading?: boolean;
  error?: string;
}) {
  const symbol = useMemo(() => researchDrawingSymbol(identity), [identity]);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const prefs = useSyncExternalStore(
    sitePreferences.subscribe,
    sitePreferences.get,
  );
  const saved = store.symbol(symbol);
  const [timeframe, setTimeframe] = useState<Timeframe>(
    ["1D", "1W", "1M"].includes(saved.preferences.timeframe)
      ? saved.preferences.timeframe
      : "1D",
  );
  const [tool, setTool] = useState<ActiveTool>("select");
  const [selected, setSelected] = useState<string | null>(null);
  const host = useRef<HTMLDivElement>(null),
    header = useRef<HTMLDivElement>(null),
    hiddenSource = useRef<HTMLDivElement>(null);
  const engine = useRef<ChartEngine | null>(null);
  const restored = useRef(false);
  const market = useMemo(() => getMarketProfile(marketSymbol), [marketSymbol]);
  const shown = useMemo(
    () =>
      timeframe === "1D"
        ? bars
        : aggregateBars(bars, timeframe as "1W" | "1M", market),
    [bars, timeframe, market],
  );
  useEffect(() => {
    void initializeChartStore();
  }, []);
  useEffect(() => {
    if (
      !state.ready ||
      !host.current ||
      !header.current ||
      !hiddenSource.current
    )
      return;
    const chart = new ChartEngine(
      host.current,
      header.current,
      hiddenSource.current,
      {
        defaults: (kind) => store.getSnapshot().app.drawingDefaults[kind] ?? {},
        commit: (id, drawings, label, tf) =>
          store.commitDrawings(id, drawings, label, tf),
        selection: setSelected,
        tool: setTool,
        view: (id, tf, range) =>
          store.updateSymbol(id, (s) => ({
            ...s,
            preferences: {
              ...s.preferences,
              views: { ...s.preferences.views, [tf]: range },
            },
          })),
      },
    );
    engine.current = chart;
    return () => {
      chart.destroy();
      engine.current = null;
    };
  }, [state.ready]);
  useEffect(() => {
    if (state.ready && !restored.current) {
      restored.current = true;
      const tf = store.symbol(symbol).preferences.timeframe;
      if (["1D", "1W", "1M"].includes(tf)) setTimeframe(tf);
    }
  }, [state.ready, symbol]);
  useEffect(() => {
    const chart = engine.current;
    if (!chart || !shown.length) return;
    const s = store.symbol(symbol);
    chart.load(
      symbol,
      timeframe,
      {
        bars: shown,
        source,
        session: "regular",
        delayed: true,
        adjusted: false,
        priceBasis: "unknown",
        dataState: "delayed",
        market,
      },
      s.drawings,
      [],
      s.preferences.views[timeframe],
      false,
    );
    chart.setPricePrecision(precision);
  }, [state.ready, symbol, timeframe, shown, source, market, precision]);
  useEffect(() => {
    engine.current?.setUpDownColors(upDownColors(prefs.colors));
    engine.current?.sync(
      saved.drawings,
      [],
      saved.preferences.magnet,
      tool,
      false,
    );
  }, [
    saved.drawings,
    saved.preferences.magnet,
    tool,
    prefs.colors,
    state.ready,
    shown,
    timeframe,
  ]);
  const changeTimeframe = (tf: Timeframe) => {
    engine.current?.flushView();
    setSelected(null);
    setTool("select");
    setTimeframe(tf);
    store.updateSymbol(symbol, (s) => ({
      ...s,
      preferences: { ...s.preferences, timeframe: tf },
    }));
  };
  const selectedDrawing = saved.drawings.find((d) => d.id === selected);
  const mutate = (remove: boolean) => {
    if (!selectedDrawing || (remove && selectedDrawing.locked)) return;
    const next = remove
      ? saved.drawings.filter((d) => d.id !== selected)
      : saved.drawings.map((d) =>
          d.id === selected ? { ...d, locked: !d.locked } : d,
        );
    store.commitDrawings(
      symbol,
      next,
      remove ? "delete" : selectedDrawing.locked ? "unlock" : "lock",
      timeframe,
    );
    if (remove) setSelected(null);
  };
  return (
    <section
      className="drawable-candles"
      data-chart-identity={identity}
      data-drawing-symbol={symbol}
      aria-label={`${source}可畫線K線圖`}
    >
      <div className="candle-toolbar">
        <div role="group" aria-label="K線週期">
          {(["1D", "1W", "1M"] as const).map((tf) => (
            <button
              key={tf}
              aria-pressed={timeframe === tf}
              onClick={() => changeTimeframe(tf)}
            >
              {{ "1D": "日K", "1W": "週K", "1M": "月K" }[tf]}
            </button>
          ))}
        </div>
        <label>
          繪圖
          <select
            aria-label="繪圖工具"
            value={tool}
            onChange={(e) => setTool(e.target.value as ActiveTool)}
          >
            <option value="select">選取／平移</option>
            {Object.entries(toolNames).map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <button
          aria-label="復原畫線"
          disabled={!store.history(symbol, timeframe).canUndo}
          onClick={() => store.undo(symbol, timeframe)}
        >
          復原
        </button>
        <button
          aria-label="重做畫線"
          disabled={!store.history(symbol, timeframe).canRedo}
          onClick={() => store.redo(symbol, timeframe)}
        >
          重做
        </button>
        <button
          aria-label="磁吸"
          aria-pressed={saved.preferences.magnet}
          onClick={() =>
            store.updateSymbol(symbol, (s) => ({
              ...s,
              preferences: { ...s.preferences, magnet: !s.preferences.magnet },
            }))
          }
        >
          磁吸
        </button>
        <button onClick={() => engine.current?.resetView()}>重設視圖</button>
        {selectedDrawing && (
          <>
            <button onClick={() => mutate(false)}>
              {selectedDrawing.locked ? "解鎖" : "鎖定"}
            </button>
            <button
              disabled={selectedDrawing.locked}
              onClick={() => mutate(true)}
            >
              刪除畫線
            </button>
          </>
        )}
      </div>
      <div ref={header} className="candle-ohlc" aria-live="off" />
      <div
        ref={host}
        className="candle-host"
        data-testid="research-candle-host"
      />
      <div ref={hiddenSource} hidden />
      {(loading || error || !bars.length) && (
        <p role="status">
          {error ??
            (loading
              ? "載入K線資料中…"
              : "暫無有效K線資料；不以單一價格推造開高低收。")}
        </p>
      )}
      <p className="source-note">
        {source} · {unit} · {shown.length} 根K線 · 最後日K{" "}
        {bars.at(-1)
          ? new Intl.DateTimeFormat("sv-SE", {
              timeZone: marketSymbol ? market.timezone : "UTC",
            }).format(new Date(bars.at(-1)!.time * 1000))
          : "—"}{" "}
        · 畫線
        {state.storageError
          ? `儲存失敗：${state.storageError}`
          : state.saving
            ? "儲存中…"
            : "已儲存"}
        {tool !== "select" && ` · ${toolNames[tool]}：依原圖表操作方式畫線`}
      </p>
    </section>
  );
}

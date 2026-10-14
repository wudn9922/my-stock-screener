import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react';
import { PERIOD_LABELS, type PeriodKey, type Theme, type ThemesData } from '../report/themes';
import { loadUniverse, type UniverseLoadResult } from '../report/universe';
import { storageName } from '../app/HostingMode';
import type { OpenChartOptions } from '../app/Site';
import { formatDateTime, formatPercent, toneClass } from '../ui/format';
import { heatWithCap } from './themesView';
import {
  BALANCED_FLOOR,
  DEFAULT_WEIGHT_MODE,
  WEIGHT_HINTS,
  WEIGHT_LABELS,
  WEIGHT_MODES,
  buildMapModel,
  formatTurnover,
  parseWeightMode,
  shortSymbol,
  stockHeatCap,
  themeName,
  toGroupInputs,
  turnoverLookup,
  type MapGroup,
  type MapLeaf,
  type WeightMode,
} from './themesMapModel';
import { DEFAULT_LAYOUT, fitLayout, labelLevel, type Rect } from './treemap';

/**
 * Treemap view of the 題材 page: outer blocks are themes, inner tiles are their constituents.
 * Size = 20-day average traded value (see themesMapModel), colour = the chosen period's return.
 * Small stocks stay visible: balanced weights with a floor, a minimum tile side that makes the map
 * taller instead of smaller, and a tooltip for tiles too small to carry text.
 */

/** Smallest tile side the layout aims for (CSS px); the map grows taller to honour it. */
const MIN_TILE_SIDE = 22;
const MAX_HEIGHT = 9000;
/** The raw-proportion mode may not stretch the page for ever: past this the small tiles simply stay small. */
const MAX_HEIGHT_RAW = { phone: 3200, wide: 2000 };
const LONG_PRESS_MS = 450;
const TIP_WIDTH = 224;
const PREFS_KEY = storageName('atlas-themes-map-v1');

function readWeightMode(): WeightMode {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) ?? 'null') as { weight?: unknown } | null;
    return parseWeightMode(raw?.weight);
  } catch {
    return DEFAULT_WEIGHT_MODE;
  }
}

function writeWeightMode(weight: WeightMode) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ weight }));
  } catch {
    /* A remembered size mode is only a convenience. */
  }
}

function baseHeightFor(width: number): number {
  return width < 600 ? 520 : Math.round(Math.min(680, Math.max(460, width * 0.5)));
}

function box(rect: Rect, inset: number): CSSProperties {
  return {
    left: rect.x + inset,
    top: rect.y + inset,
    width: Math.max(0, rect.w - inset * 2),
    height: Math.max(0, rect.h - inset * 2),
  };
}

/** Width of an element, kept current with a ResizeObserver (0 until measured). */
function useWidth() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const measure = (next: number) => setWidth((current) => (Math.abs(current - next) < 2 ? current : next));
    measure(Math.floor(node.getBoundingClientRect().width));
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => measure(Math.floor(entries[0]?.contentRect.width ?? 0)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

function useUniverse(): UniverseLoadResult | null {
  const [result, setResult] = useState<UniverseLoadResult | null>(null);
  useEffect(() => {
    let active = true;
    void loadUniverse().then((next) => {
      if (active) setResult(next);
    });
    return () => {
      active = false;
    };
  }, []);
  return result;
}

interface Tip {
  id: string;
  leaf: MapLeaf;
  group: MapGroup;
  rect: Rect;
}

function Legend({ period }: { period: PeriodKey }) {
  const cap = stockHeatCap(period);
  return (
    <div className="map-legend" aria-label={`顏色圖例：${PERIOD_LABELS[period]}報酬`} role="img">
      <span className="num">≤ -{cap}%</span>
      <span className="map-scale" aria-hidden="true" />
      <span className="num">≥ +{cap}%</span>
      <span className="legend-note">顏色＝{PERIOD_LABELS[period]}報酬，超過 ±{cap}% 以同一深度顯示</span>
    </div>
  );
}

export default function ThemesMap({
  data,
  themes,
  period,
  onOpenTheme,
  openChart,
}: {
  data: ThemesData;
  themes: Theme[];
  period: PeriodKey;
  onOpenTheme: (key: string) => void;
  openChart: (options: OpenChartOptions) => void;
}) {
  const [weight, setWeight] = useState<WeightMode>(readWeightMode);
  const universe = useUniverse();
  const { ref, width } = useWidth();
  const market = data.market;

  const lookup = useMemo(() => (universe?.status === 'ok' ? turnoverLookup(universe.universe, market) : null), [universe, market]);
  const usable = useMemo(() => themes.filter((theme) => theme.constituents.length > 0), [themes]);
  const model = useMemo(() => buildMapModel(usable, lookup?.values ?? null, weight), [usable, lookup, weight]);
  const layout = useMemo(
    () =>
      width > 0
        ? fitLayout(toGroupInputs(model), width, { baseHeight: baseHeightFor(width), maxHeight: model.mode === 'turnover' ? (width < 600 ? MAX_HEIGHT_RAW.phone : MAX_HEIGHT_RAW.wide) : MAX_HEIGHT, minSide: MIN_TILE_SIDE, layout: DEFAULT_LAYOUT })
        : null,
    [model, width],
  );
  const leafById = useMemo(() => {
    const map = new Map<string, { leaf: MapLeaf; group: MapGroup }>();
    for (const group of model.groups) for (const leaf of group.leaves) map.set(leaf.id, { leaf, group });
    return map;
  }, [model]);
  const rectById = useMemo(() => {
    const map = new Map<string, Rect>();
    for (const group of layout?.groups ?? []) for (const leaf of group.leaves) map.set(leaf.id, leaf.rect);
    return map;
  }, [layout]);
  const groupById = useMemo(() => new Map(model.groups.map((group) => [group.id, group])), [model]);

  const [tip, setTip] = useState<Tip | null>(null);
  const press = useRef<{ timer: number | null; x: number; y: number; fired: boolean }>({ timer: null, x: 0, y: 0, fired: false });
  const hideTimer = useRef<number | null>(null);
  const clearPress = useCallback(() => {
    if (press.current.timer !== null) window.clearTimeout(press.current.timer);
    press.current.timer = null;
  }, []);
  useEffect(
    () => () => {
      clearPress();
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    },
    [clearPress],
  );
  // A tooltip must not outlive the layout it points into.
  useEffect(() => setTip(null), [layout, period]);

  const show = useCallback(
    (id: string) => {
      const entry = leafById.get(id);
      const rect = rectById.get(id);
      if (!entry || !rect) return;
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
      hideTimer.current = null;
      setTip({ id, leaf: entry.leaf, group: entry.group, rect });
    },
    [leafById, rectById],
  );
  const hide = useCallback(() => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    hideTimer.current = null;
    setTip(null);
  }, []);

  const onPointerDown = (event: ReactPointerEvent<HTMLButtonElement>, id: string) => {
    clearPress();
    press.current = { timer: null, x: event.clientX, y: event.clientY, fired: false };
    if (event.pointerType === 'mouse') return;
    press.current.timer = window.setTimeout(() => {
      press.current.fired = true;
      press.current.timer = null;
      show(id);
    }, LONG_PRESS_MS);
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (press.current.timer === null) return;
    if (Math.hypot(event.clientX - press.current.x, event.clientY - press.current.y) > 8) clearPress();
  };
  const onPointerEnd = () => {
    clearPress();
    if (press.current.fired) {
      // Keep the long-press tooltip readable for a moment after the finger lifts.
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
      hideTimer.current = window.setTimeout(hide, 3200);
    }
  };
  const onTileClick = (leaf: MapLeaf) => {
    if (press.current.fired) {
      // The click that ends a long press only dismisses nothing and opens nothing.
      press.current.fired = false;
      return;
    }
    hide();
    openChart({ symbol: leaf.symbol, name: leaf.name, maList: [20, 50] });
  };

  const choose = (next: WeightMode) => {
    setWeight(next);
    writeWeightMode(next);
  };

  const cap = stockHeatCap(period);
  const unavailable = universe !== null && !lookup;
  const turnoverAsOf = lookup?.asOf ?? null;
  const currency = market === 'tw' ? '新台幣' : '美元';

  return (
    <section className="themes-map" aria-label="題材熱力圖">
      <div className="map-toolbar">
        <div className="segmented compact" role="group" aria-label="大小">
          {WEIGHT_MODES.map((key) => {
            const needsData = key !== 'equal';
            return (
              <button
                key={key}
                type="button"
                className={model.mode === key ? 'active' : ''}
                aria-pressed={model.mode === key}
                disabled={needsData && !lookup}
                title={WEIGHT_HINTS[key]}
                onClick={() => choose(key)}
              >
                {WEIGHT_LABELS[key]}
              </button>
            );
          })}
        </div>
        <Legend period={period} />
      </div>
      {unavailable && (
        <p className="map-note warn" role="status">
          成交值資料暫時無法取得，以等大小顯示
        </p>
      )}

      <div ref={ref} className="map-wrap">
        {universe === null ? (
          <div className="map-loading" role="status">
            載入成交值資料…
          </div>
        ) : !usable.length ? (
          <div className="map-loading" role="status">
            這些題材沒有可顯示的成分股。
          </div>
        ) : layout ? (
          <div className="map-canvas" style={{ height: layout.height }} role="group" aria-label={`題材熱力圖，${usable.length} 個題材、${model.leafCount} 檔股票`}>
            {layout.groups.map((placed) => {
              const group = groupById.get(placed.id);
              if (!group) return null;
              const value = group.theme.returns[period];
              const headLabel = `${group.theme.name}，${PERIOD_LABELS[period]}題材報酬 ${formatPercent(value, 1)}，開啟題材詳情`;
              return (
                <Fragment key={placed.id}>
                  <div className="map-group" style={box(placed.rect, 0.5)} aria-hidden="true" />
                  {placed.leaves.map((placedLeaf) => {
                    const entry = leafById.get(placedLeaf.id);
                    if (!entry) return null;
                    const { leaf } = entry;
                    const ret = leaf.constituent.returns[period];
                    const tone = heatWithCap(ret, cap);
                    const { w, h } = placedLeaf.rect;
                    const level = labelLevel(w, h);
                    const fontSize = Math.round(Math.min(18, Math.max(10, Math.sqrt(w * h) / 5.5)));
                    const label = `${leaf.symbol} ${leaf.name}，${PERIOD_LABELS[period]}報酬 ${formatPercent(ret, 1)}，${group.theme.name}`;
                    return (
                      <button
                        key={leaf.id}
                        type="button"
                        className={`map-tile ${ret === null ? 'heat-na' : `heat-${tone.side}`} lv-${level}`}
                        style={{ ...box(placedLeaf.rect, 0.5), fontSize, ['--mix' as string]: `${tone.mix}%` }}
                        aria-label={label}
                        onClick={() => onTileClick(leaf)}
                        onPointerEnter={(event) => event.pointerType === 'mouse' && show(leaf.id)}
                        onPointerLeave={(event) => event.pointerType === 'mouse' && hide()}
                        onFocus={(event) => event.currentTarget.matches(':focus-visible') && show(leaf.id)}
                        onBlur={hide}
                        onPointerDown={(event) => onPointerDown(event, leaf.id)}
                        onPointerMove={onPointerMove}
                        onPointerUp={onPointerEnd}
                        onPointerCancel={onPointerEnd}
                        onContextMenu={(event) => event.preventDefault()}
                      >
                        {level !== 'none' && <b className="map-code">{shortSymbol(leaf.symbol)}</b>}
                        {(level === 'full' || level === 'name') && <span className="map-pct num">{formatPercent(ret, 1)}</span>}
                        {level === 'name' && leaf.name && <small className="map-name">{leaf.name}</small>}
                      </button>
                    );
                  })}
                  <button
                    type="button"
                    className="map-group-head"
                    style={{ ...box(placed.header, 0.5), height: placed.header.h }}
                    aria-label={headLabel}
                    onClick={() => onOpenTheme(group.theme.key)}
                  >
                    <span className="map-group-name">{group.theme.name}</span>
                    {placed.rect.w >= 92 && <span className={`map-group-value num ${toneClass(value)}`}>{formatPercent(value, 1)}</span>}
                  </button>
                </Fragment>
              );
            })}
            {tip && (
              <div
                className="map-tip"
                role="tooltip"
                style={{
                  left: Math.max(4, Math.min(layout.width - TIP_WIDTH - 4, tip.rect.x + tip.rect.w / 2 - TIP_WIDTH / 2)),
                  top: tip.rect.y < 120 ? tip.rect.y + tip.rect.h + 6 : tip.rect.y - 6,
                  width: TIP_WIDTH,
                  transform: tip.rect.y < 120 ? undefined : 'translateY(-100%)',
                }}
              >
                <b>
                  {shortSymbol(tip.leaf.symbol)} {tip.leaf.name}
                </b>
                <span className="map-tip-theme">
                  {tip.group.theme.name}
                  {themeName(data, tip.group.theme) ? ` · ${themeName(data, tip.group.theme)}` : ''}
                </span>
                <span>
                  {PERIOD_LABELS[period]}報酬{' '}
                  <b className={`num ${toneClass(tip.leaf.constituent.returns[period])}`}>{formatPercent(tip.leaf.constituent.returns[period], 2)}</b>
                </span>
                <span>
                  近20日均成交值 <b className="num">{formatTurnover(tip.leaf.turnover, market)}</b>
                  {tip.leaf.imputed ? '（無資料，以題材中位數決定大小）' : ''}
                </span>
                <span className="muted">點一下開啟圖表</span>
              </div>
            )}
          </div>
        ) : null}
      </div>

      <p className="map-foot">
        {model.mode === 'equal'
          ? '大小：每檔一樣大（等大小）。'
          : `大小＝近20日平均成交值（${currency}${turnoverAsOf ? `，資料日 ${formatDateTime(turnoverAsOf, false)}` : ''}），不是市值${
              model.mode === 'balanced' ? `；均衡模式取平方根壓縮，每塊至少為題材中位數的 ${Math.round(BALANCED_FLOOR * 100)}%，小型股才不會縮到看不見` : '；小型股會很小，可切回「均衡」'
            }。`}
        {model.imputed > 0 ? ` ${model.imputed} 檔找不到成交值，以所屬題材的中位數代入。` : ''}
        {layout && !layout.satisfied ? ' 部分方塊小於建議點選尺寸，建議切到「均衡」或「等大小」。' : ''}
        {' '}
        方塊太小時不顯示文字，滑過或長按可看詳情。
      </p>
    </section>
  );
}

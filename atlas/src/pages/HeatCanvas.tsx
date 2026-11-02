import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { formatPercent, toneClass } from '../ui/format';
import { heatWithCap } from './themesView';
import { toGroupInputs, type MapModel } from './themesMapModel';
import { fitLayout, cellLabelFit, textWidth, tipPosition, type LayoutOptions, type Rect } from './treemap';

/**
 * The treemap canvas shared by the industry map and its theme fallback: lays the cells out for the
 * measured width, paints them with the heat colour, and carries the tooltip (hover on a mouse, long
 * press on touch) and the click. What a cell means (industry or theme) is decided by the caller.
 */

/** Smallest cell side the layout aims for (CSS px); the map grows taller to honour it. */
const MIN_CELL_SIDE = 22;
const MAX_HEIGHT = 9000;
/** The raw-proportion mode may not stretch the page for ever: past this the small cells simply stay small. */
const MAX_HEIGHT_RAW = { phone: 3200, wide: 2000 };
const LONG_PRESS_MS = 450;
const TIP_WIDTH = 248;
const TIP_HEIGHT = 230;

const GROUPED_LAYOUT: LayoutOptions = { headerHeight: 18, pad: 2, groupFloor: 0.5 };
const FLAT_LAYOUT: LayoutOptions = { headerHeight: 0, pad: 0, groupFloor: 0.5 };

/** Roomy cells have labels: aim for this much area per cell, so a many-cell map on a phone gets taller instead of being all unlabelled slivers. */
const AREA_PER_CELL = 4000;
const MAX_ROOMY_HEIGHT = 2400;

function baseHeightFor(width: number, cellCount = 0): number {
  const plain = width < 600 ? 520 : Math.round(Math.min(680, Math.max(460, width * 0.5)));
  return Math.max(plain, Math.min(MAX_ROOMY_HEIGHT, Math.ceil((cellCount * AREA_PER_CELL) / Math.max(width, 1))));
}

export interface HeatCellView {
  id: string;
  label: string;
  /** The value that colours the cell (null = no data, drawn hatched). */
  value: number | null;
  aria: string;
  tip: ReactNode;
  onOpen: () => void;
}
export interface HeatGroupView {
  id: string;
  label: string;
  /** Shown in the group header next to the name (e.g. the sector's return). */
  value?: number | null;
  cells: HeatCellView[];
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

export default function HeatCanvas({
  model,
  groups,
  flat,
  cap,
  ariaLabel,
  empty,
}: {
  /** Weights per cell (from buildMapModel). */
  model: MapModel;
  groups: HeatGroupView[];
  /** One level only: no group headers, the cells fill the canvas. */
  flat: boolean;
  /** Percent that saturates the colour scale. */
  cap: number;
  ariaLabel: string;
  empty: string;
}) {
  const { ref, width } = useWidth();
  const layoutOptions = flat ? FLAT_LAYOUT : GROUPED_LAYOUT;
  const layout = useMemo(
    () =>
      width > 0
        ? fitLayout(toGroupInputs(model), width, {
            baseHeight: baseHeightFor(width, model.cellCount),
            maxHeight: model.mode === 'size' ? (width < 600 ? MAX_HEIGHT_RAW.phone : MAX_HEIGHT_RAW.wide) : MAX_HEIGHT,
            minSide: model.mode === 'size' ? MIN_CELL_SIDE : 44,
            layout: layoutOptions,
          })
        : null,
    [model, width, layoutOptions],
  );
  const cellById = useMemo(() => {
    const map = new Map<string, HeatCellView>();
    for (const group of groups) for (const cell of group.cells) map.set(cell.id, cell);
    return map;
  }, [groups]);
  const groupById = useMemo(() => new Map(groups.map((group) => [group.id, group])), [groups]);
  const rectById = useMemo(() => {
    const map = new Map<string, Rect>();
    for (const group of layout?.groups ?? []) for (const leaf of group.leaves) map.set(leaf.id, leaf.rect);
    return map;
  }, [layout]);

  const [tipId, setTipId] = useState<string | null>(null);
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
  // A tooltip must not outlive the layout or the data it points into.
  useEffect(() => setTipId(null), [layout, groups]);

  const show = useCallback((id: string) => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    hideTimer.current = null;
    setTipId(id);
  }, []);
  const hide = useCallback(() => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    hideTimer.current = null;
    setTipId(null);
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
  const onCellClick = (cell: HeatCellView) => {
    if (press.current.fired) {
      // The click that ends a long press only shows the tooltip; it opens nothing.
      press.current.fired = false;
      return;
    }
    hide();
    cell.onOpen();
  };

  const tipCell = tipId ? cellById.get(tipId) : undefined;
  const tipRect = tipId ? rectById.get(tipId) : undefined;
  const tipAt = layout && tipRect ? tipPosition(tipRect, { w: layout.width, h: layout.height }, { w: TIP_WIDTH, h: TIP_HEIGHT }) : null;

  if (!groups.some((group) => group.cells.length)) {
    return (
      <div className="map-wrap">
        <div className="map-loading" role="status">
          {empty}
        </div>
      </div>
    );
  }
  return (
    <>
      <div ref={ref} className="map-wrap">
        {layout ? (
          <div className="map-canvas" style={{ height: layout.height }} role="group" aria-label={ariaLabel}>
            {layout.groups.map((placed) => {
              const group = groupById.get(placed.id);
              if (!group) return null;
              return (
                <Fragment key={placed.id}>
                  {!flat && <div className="map-group" style={box(placed.rect, 0.5)} aria-hidden="true" />}
                  {placed.leaves.map((placedLeaf) => {
                    const cell = cellById.get(placedLeaf.id);
                    if (!cell) return null;
                    const { w, h } = placedLeaf.rect;
                    const tone = heatWithCap(cell.value, cap);
                    const preferred = Math.min(15, Math.max(11, Math.sqrt(w * h) / 7));
                    const { fit, fontSize, text } = cellLabelFit(cell.label, w - 1, h - 1, preferred);
                    return (
                      <button
                        key={cell.id}
                        type="button"
                        className={`map-cell ${cell.value === null ? 'heat-na' : `heat-${tone.side}`} fit-${fit}`}
                        style={{ ...box(placedLeaf.rect, 0.5), fontSize, ['--mix' as string]: `${tone.mix}%` }}
                        aria-label={cell.aria}
                        title={cell.label}
                        onClick={() => onCellClick(cell)}
                        onPointerEnter={(event) => event.pointerType === 'mouse' && show(cell.id)}
                        onPointerLeave={(event) => event.pointerType === 'mouse' && hide()}
                        onFocus={(event) => event.currentTarget.matches(':focus-visible') && show(cell.id)}
                        onBlur={hide}
                        onPointerDown={(event) => onPointerDown(event, cell.id)}
                        onPointerMove={onPointerMove}
                        onPointerUp={onPointerEnd}
                        onPointerCancel={onPointerEnd}
                        onContextMenu={(event) => event.preventDefault()}
                      >
                        {fit !== 'none' && <b className="map-code">{text}</b>}
                        {fit === 'full' && <span className="map-pct num">{formatPercent(cell.value, 1)}</span>}
                      </button>
                    );
                  })}
                  {!flat && (
                    <div className="map-group-head" style={{ ...box(placed.header, 0.5), height: placed.header.h }} title={group.label}>
                      <span className="map-group-name">{group.label}</span>
                      {group.value !== undefined && placed.rect.w - 20 >= textWidth(group.label, 11) + 48 && (
                        <span className={`map-group-value num ${toneClass(group.value)}`}>{formatPercent(group.value, 1)}</span>
                      )}
                    </div>
                  )}
                </Fragment>
              );
            })}
            {tipCell && tipAt && (
              <div
                className="map-tip"
                role="tooltip"
                style={{ left: tipAt.left, top: tipAt.top, width: TIP_WIDTH, transform: tipAt.above ? 'translateY(-100%)' : undefined }}
              >
                {tipCell.tip}
              </div>
            )}
          </div>
        ) : null}
      </div>
      {layout && !layout.satisfied && (
        <p className="map-note" role="status">
          部分方塊小於建議點選尺寸，建議切到「均衡」或「等大小」。
        </p>
      )}
    </>
  );
}

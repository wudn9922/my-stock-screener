import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { barLayout, barPath, labelPlan, labelWidth, linePath, overlayScale } from './barGeometry';
import { digitsFor, formatNumber } from './financialsView';

export interface BarChartProps {
  /** Values in display units (億 / 兆 already applied), one per bar, oldest first. */
  values: readonly (number | null)[];
  /** Value labels as drawn (same order); null for a missing value. */
  texts: readonly (string | null)[];
  /** Axis text per bar; `sub` is a second line (fiscal year under Q1). */
  axis: readonly { main: string; sub?: string }[];
  /** Spoken label per bar for screen readers. */
  ariaLabels: readonly string[];
  labelMode: 'all' | 'extremes';
  selected: number | null;
  onSelect: (index: number) => void;
  title: string;
  /** Year-over-year line in percent on a right-hand axis, one value per bar (null: no point there). */
  overlay?: { values: readonly (number | null)[]; label: string };
}

const PLOT = 120;
/** Room for the right-hand % axis when the YoY line is drawn. */
const RIGHT_AXIS = 46;

/** Measured width of the chart box; SVG text then renders at its real pixel size (no scaling blur). */
function useWidth<T extends HTMLElement>(fallback: number) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const measure = () => setWidth(Math.max(160, Math.round(node.getBoundingClientRect().width)));
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/**
 * Column chart for one metric: zero baseline, positive bars in the accent color, negative bars drawn
 * downward in the 「跌」 color (follows the 紅漲綠跌 / 綠漲紅跌 preference through `--down`). Each slot is a
 * button; the selected bar is emphasised and always labelled. With `overlay`, a YoY polyline is drawn over
 * the bars against its own % axis on the right; points where YoY is undefined are left out.
 */
export function BarChart({ values, texts, axis, ariaLabels, labelMode, selected, onSelect, title, overlay }: BarChartProps) {
  const [ref, width] = useWidth<HTMLDivElement>(330);
  const plotWidth = overlay ? Math.max(120, width - RIGHT_AXIS) : width;
  const layout = useMemo(() => barLayout(values, plotWidth, { plot: PLOT, axis: axis.some((a) => a.sub) ? 30 : 18 }), [values, plotWidth, axis]);
  const plan = useMemo(
    () => labelPlan(texts, layout.bars[0]?.slotWidth ?? plotWidth, labelMode, values, selected),
    [texts, layout, plotWidth, labelMode, values, selected],
  );
  const line = useMemo(() => {
    if (!overlay) return null;
    const scale = overlayScale(overlay.values, layout.top, layout.bottom);
    if (!scale) return null;
    const points = layout.bars.map((bar) => {
      const value = overlay.values[bar.index] ?? null;
      return value === null ? null : { x: bar.cx, y: scale.yOf(value), value };
    });
    return { scale, points, path: linePath(points), digits: digitsFor([scale.hi, scale.lo], 'pct') };
  }, [overlay, layout]);
  const axisTop = layout.height - (axis.some((a) => a.sub) ? 30 : 18);
  // Labels stay inside the plot: a first or last bar's label moves in instead of being cut off.
  const clampX = (text: string, size: number, cx: number) => {
    const half = labelWidth(text, size) / 2;
    return Math.min(Math.max(cx, half), plotWidth - half);
  };
  const onKey = (event: KeyboardEvent, index: number) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onSelect(index);
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      const next = Math.min(values.length - 1, Math.max(0, (selected ?? values.length - 1) + (event.key === 'ArrowLeft' ? -1 : 1)));
      onSelect(next);
      const sibling = (event.currentTarget.parentNode as Element | null)?.children[next] as SVGGElement | undefined;
      sibling?.focus?.();
    }
  };
  return (
    <div className="fin-chart" ref={ref}>
      <svg width={width} height={layout.height} viewBox={`0 0 ${width} ${layout.height}`} role="group" aria-label={title}>
        <line className="fin-zero" x1={0} x2={plotWidth} y1={layout.zeroY} y2={layout.zeroY} />
        <g className="fin-bars">
          {layout.bars.map((bar) => {
            const isSelected = selected === bar.index;
            const labelled = plan.indices.has(bar.index);
            const text = texts[bar.index];
            const labelY = bar.negative ? bar.yEnd + plan.fontSize + 3 : bar.yEnd - 4;
            return (
              <g
                key={bar.index}
                className={`fin-bar${bar.negative ? ' negative' : ''}${isSelected ? ' selected' : ''}`}
                role="button"
                tabIndex={isSelected || (selected === null && bar.index === values.length - 1) ? 0 : -1}
                aria-pressed={isSelected}
                aria-label={ariaLabels[bar.index]}
                onClick={() => onSelect(bar.index)}
                onKeyDown={(event) => onKey(event, bar.index)}
              >
                <rect className="fin-hit" x={bar.slotX} y={0} width={bar.slotWidth} height={layout.height} />
                {bar.value === null ? (
                  <text className="fin-null" x={bar.cx} y={layout.zeroY - 4} textAnchor="middle">
                    —
                  </text>
                ) : (
                  <path className="fin-bar-shape" d={barPath(bar, layout.zeroY)} />
                )}
                {labelled && text && bar.value !== null && (
                  <text className="fin-value" x={clampX(text, plan.fontSize, bar.cx)} y={labelY} textAnchor="middle" fontSize={plan.fontSize}>
                    {text}
                  </text>
                )}
                <text className="fin-axis" x={clampX(axis[bar.index]?.main ?? '', 10, bar.cx)} y={axisTop + 12} textAnchor="middle">
                  {axis[bar.index]?.main}
                </text>
                {axis[bar.index]?.sub && (
                  <text className="fin-axis sub" x={bar.cx} y={axisTop + 25} textAnchor="middle">
                    {axis[bar.index]!.sub}
                  </text>
                )}
              </g>
            );
          })}
        </g>
        {line && overlay && (
          <g className="fin-yoy" aria-hidden="true">
            <line className="fin-yoy-rule" x1={plotWidth + 3} x2={plotWidth + 3} y1={layout.top} y2={layout.bottom} />
            {line.scale.ticks.map((tick) => (
              <text key={tick} className="fin-yoy-tick" x={plotWidth + 8} y={line.scale.yOf(tick) + 3}>
                {`${tick > 0 ? '+' : ''}${formatNumber(tick, line.digits)}%`}
              </text>
            ))}
            <path className="fin-yoy-line" d={line.path} />
            {line.points.map((point, index) =>
              point ? (
                <circle key={index} className="fin-yoy-dot" cx={point.x} cy={point.y} r={2.5}>
                  <title>{`${overlay.label} ${point.value > 0 ? '+' : ''}${formatNumber(point.value, 1)}%`}</title>
                </circle>
              ) : null,
            )}
          </g>
        )}
      </svg>
    </div>
  );
}

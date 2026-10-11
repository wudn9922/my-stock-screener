import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { barLayout, barPath, labelPlan } from './barGeometry';

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
}

const PLOT = 120;

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
 * button; the selected bar is emphasised and always labelled.
 */
export function BarChart({ values, texts, axis, ariaLabels, labelMode, selected, onSelect, title }: BarChartProps) {
  const [ref, width] = useWidth<HTMLDivElement>(330);
  const layout = useMemo(() => barLayout(values, width, { plot: PLOT, axis: axis.some((a) => a.sub) ? 30 : 18 }), [values, width, axis]);
  const plan = useMemo(
    () => labelPlan(texts, layout.bars[0]?.slotWidth ?? width, labelMode, values, selected),
    [texts, layout, width, labelMode, values, selected],
  );
  const axisTop = layout.height - (axis.some((a) => a.sub) ? 30 : 18);
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
        <line className="fin-zero" x1={0} x2={width} y1={layout.zeroY} y2={layout.zeroY} />
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
                  <text className="fin-value" x={bar.cx} y={labelY} textAnchor="middle" fontSize={plan.fontSize}>
                    {text}
                  </text>
                )}
                <text className="fin-axis" x={bar.cx} y={axisTop + 12} textAnchor="middle">
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
      </svg>
    </div>
  );
}

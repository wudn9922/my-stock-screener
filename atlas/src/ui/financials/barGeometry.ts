/**
 * Geometry for the financials column charts (pure, unit-tested): a zero baseline, positive bars up,
 * negative bars down, rounded only at the data end, and which bars get a value label.
 */

export interface BarBox {
  index: number;
  value: number | null;
  /** Slot (hit area) left edge and width. */
  slotX: number;
  slotWidth: number;
  /** Bar centre and width. */
  cx: number;
  width: number;
  /** Data end and baseline (SVG y grows downward). */
  yEnd: number;
  negative: boolean;
}

export interface BarLayout {
  bars: BarBox[];
  zeroY: number;
  /** Plot band (bars only), excluding the label pads. */
  top: number;
  bottom: number;
  height: number;
}

export interface LayoutOptions {
  /** Height of the bar band. */
  plot?: number;
  /** Room for value labels above positive and below negative bars. */
  labelPad?: number;
  /** Room for the period axis below everything. */
  axis?: number;
  maxBarWidth?: number;
}

export function barLayout(values: readonly (number | null)[], width: number, options: LayoutOptions = {}): BarLayout {
  const plot = options.plot ?? 120;
  const pad = options.labelPad ?? 16;
  const axis = options.axis ?? 28;
  const present = values.filter((value): value is number => value !== null);
  const max = Math.max(0, ...present);
  const min = Math.min(0, ...present);
  const hasNegative = min < 0;
  const top = pad;
  const bottom = top + plot;
  const span = max - min || 1;
  const yOf = (value: number) => top + ((max - value) / span) * plot;
  const zeroY = present.length ? yOf(0) : bottom;
  const n = Math.max(values.length, 1);
  const slotWidth = width / n;
  const barWidth = Math.max(4, Math.min(options.maxBarWidth ?? 36, slotWidth * 0.62));
  const bars = values.map((value, index) => {
    const slotX = index * slotWidth;
    return {
      index,
      value,
      slotX,
      slotWidth,
      cx: slotX + slotWidth / 2,
      width: barWidth,
      yEnd: value === null ? zeroY : yOf(value),
      negative: value !== null && value < 0,
    };
  });
  return { bars, zeroY, top, bottom, height: bottom + (hasNegative ? pad : 4) + axis };
}

/**
 * Path for one bar: square at the baseline, 4px rounded corners at the data end (up or down).
 * Bars shorter than 1px still draw a 1px sliver so a tiny value is visible.
 */
export function barPath(bar: BarBox, zeroY: number, radius = 4): string {
  if (bar.value === null) return '';
  const left = bar.cx - bar.width / 2;
  const right = bar.cx + bar.width / 2;
  const up = bar.yEnd <= zeroY;
  const length = Math.max(1, Math.abs(zeroY - bar.yEnd));
  const end = up ? zeroY - length : zeroY + length;
  const r = Math.min(radius, bar.width / 2, length);
  const f = (value: number) => Math.round(value * 10) / 10;
  if (up)
    return `M${f(left)},${f(zeroY)}V${f(end + r)}Q${f(left)},${f(end)} ${f(left + r)},${f(end)}H${f(right - r)}Q${f(right)},${f(end)} ${f(right)},${f(end + r)}V${f(zeroY)}Z`;
  return `M${f(left)},${f(zeroY)}V${f(end - r)}Q${f(left)},${f(end)} ${f(left + r)},${f(end)}H${f(right - r)}Q${f(right)},${f(end)} ${f(right)},${f(end - r)}V${f(zeroY)}Z`;
}

/** Approximate rendered width of a numeric label (tabular digits ≈ 0.6em, separators narrower). */
export function labelWidth(text: string, fontSize: number): number {
  let width = 0;
  for (const char of text) width += /[0-9]/.test(char) ? 0.6 : /[,.]/.test(char) ? 0.3 : 0.62;
  return width * fontSize;
}

/**
 * Which bars carry a value label. `all`: every bar (annual) — falling back to the extremes when the
 * labels cannot fit their slots even at 9px; `extremes`: latest, max and min (quarterly). The selected
 * bar is always labelled. Returns the indices and the font size to use.
 */
export function labelPlan(
  texts: readonly (string | null)[],
  slotWidth: number,
  mode: 'all' | 'extremes',
  values: readonly (number | null)[],
  selected: number | null,
): { indices: Set<number>; fontSize: number } {
  const extremes = new Set<number>();
  let latest = -1;
  let maxIndex = -1;
  let minIndex = -1;
  values.forEach((value, index) => {
    if (value === null) return;
    latest = index;
    if (maxIndex < 0 || value > values[maxIndex]!) maxIndex = index;
    if (minIndex < 0 || value < values[minIndex]!) minIndex = index;
  });
  for (const index of [latest, maxIndex, minIndex]) if (index >= 0) extremes.add(index);
  const widest = Math.max(0, ...texts.map((text) => (text ? labelWidth(text, 10) : 0)));
  let fontSize = 10;
  let indices = mode === 'all' ? new Set(texts.flatMap((text, index) => (text ? [index] : []))) : extremes;
  if (mode === 'all' && widest > slotWidth - 2) {
    if (widest * 0.9 <= slotWidth - 2) fontSize = 9;
    else indices = extremes;
  }
  if (selected !== null && texts[selected]) indices = new Set([...indices, selected]);
  return { indices, fontSize };
}

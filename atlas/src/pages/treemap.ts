/**
 * Pure treemap layout for the 題材 heatmap: a squarified layout (Bruls, Huizing, van Wijk) plus a
 * two-level wrapper (groups → leaves) that keeps small groups and small leaves usable.
 *
 * Nothing here touches the DOM, so it is deterministic and unit-tested. The heatmap component only
 * measures its container width and paints the rectangles returned here.
 */

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Placed<T> {
  item: T;
  /** Index of `item` in the input array (results are returned in input order). */
  index: number;
  rect: Rect;
}

const finitePositive = (value: number) => (Number.isFinite(value) && value > 0 ? value : 0);

/** Worst aspect ratio of a row with total area `sum`, largest `max`, smallest `min`, laid along `side`. */
function worstRatio(sum: number, max: number, min: number, side: number): number {
  const s2 = sum * sum;
  const side2 = side * side;
  return Math.max((side2 * max) / s2, s2 / (side2 * min));
}

/**
 * Squarified treemap of `items` inside `rect`. Areas are proportional to `weight` and sum to the
 * rectangle's area. Non-finite or non-positive weights get an empty rectangle; when no item has a
 * positive weight every item counts the same. Equal weights keep their input order, so the result
 * is a pure function of the input. An empty input or a degenerate rectangle yields empty rectangles.
 */
export function squarify<T extends { weight: number }>(items: readonly T[], rect: Rect): Placed<T>[] {
  const out: Placed<T>[] = items.map((item, index) => ({ item, index, rect: { x: rect.x, y: rect.y, w: 0, h: 0 } }));
  if (!items.length || !(rect.w > 0) || !(rect.h > 0)) return out;
  let weights = items.map((item) => finitePositive(item.weight));
  let total = weights.reduce((sum, w) => sum + w, 0);
  if (total <= 0) {
    weights = items.map(() => 1);
    total = items.length;
  }
  const area = rect.w * rect.h;
  const areaOf = (index: number) => (weights[index]! / total) * area;
  const order = weights
    .map((weight, index) => ({ weight, index }))
    .filter((entry) => entry.weight > 0)
    .sort((a, b) => b.weight - a.weight || a.index - b.index)
    .map((entry) => entry.index);

  let { x, y, w, h } = rect;
  let i = 0;
  while (i < order.length) {
    const horizontal = w >= h; // the row is a column on the left when the free space is wider than tall
    const side = horizontal ? h : w;
    let sum = areaOf(order[i]!);
    const max = sum;
    let min = sum;
    let j = i + 1;
    while (j < order.length) {
      const next = areaOf(order[j]!);
      if (worstRatio(sum + next, max, next, side) > worstRatio(sum, max, min, side)) break;
      sum += next;
      min = next;
      j++;
    }
    const last = j >= order.length;
    // The final row takes whatever is left, so rounding never leaves a gap.
    const thickness = last ? (horizontal ? w : h) : Math.min(sum / side, horizontal ? w : h);
    let cursor = horizontal ? y : x;
    for (let k = i; k < j; k++) {
      const index = order[k]!;
      const length = k === j - 1 ? (horizontal ? y + h : x + w) - cursor : areaOf(index) / thickness;
      out[index]!.rect = horizontal ? { x, y: cursor, w: thickness, h: length } : { x: cursor, y, w: length, h: thickness };
      cursor += length;
    }
    if (horizontal) {
      x += thickness;
      w -= thickness;
    } else {
      y += thickness;
      h -= thickness;
    }
    i = j;
    if (w <= 1e-9 || h <= 1e-9) break;
  }
  return out;
}

export function median(values: readonly number[]): number {
  const sorted = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (!sorted.length) return 0;
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

/**
 * Lifts every weight to at least `ratio` × the median of the positive weights. Returns a new array;
 * a list without any positive weight comes back as all ones (equal size).
 */
export function floorWeights(weights: readonly number[], ratio: number): number[] {
  const clean = weights.map(finitePositive);
  const positive = clean.filter((w) => w > 0);
  if (!positive.length) return clean.map(() => 1);
  const floor = Math.max(0, ratio) * median(positive);
  return clean.map((w) => Math.max(w, floor));
}

export interface GroupInput {
  id: string;
  leaves: { id: string; weight: number }[];
}

export interface PlacedLeaf {
  id: string;
  rect: Rect;
}

export interface PlacedGroup {
  id: string;
  /** The whole group block, header and padding included. */
  rect: Rect;
  /** The strip across the top that carries the group name. */
  header: Rect;
  leaves: PlacedLeaf[];
}

export interface LayoutOptions {
  /** Height of the group name strip. */
  headerHeight: number;
  /** Padding between a group's border and its leaves. */
  pad: number;
  /** A group's weight is never below this × the median group weight. */
  groupFloor: number;
}

export const DEFAULT_LAYOUT: LayoutOptions = { headerHeight: 18, pad: 2, groupFloor: 0.5 };

export interface TreemapLayout {
  width: number;
  height: number;
  groups: PlacedGroup[];
  /** Smallest side of any leaf that has weight (Infinity when there is none). */
  minLeafSide: number;
}

/** Group blocks first (weight = sum of its leaves, floored), then each group's leaves in what is left of its block. */
export function layoutGroups(groups: readonly GroupInput[], width: number, height: number, options: LayoutOptions = DEFAULT_LAYOUT): TreemapLayout {
  const sums = groups.map((group) => group.leaves.reduce((sum, leaf) => sum + finitePositive(leaf.weight), 0));
  const weights = floorWeights(sums, options.groupFloor);
  const blocks = squarify(
    groups.map((group, index) => ({ group, weight: group.leaves.length ? weights[index]! : 0 })),
    { x: 0, y: 0, w: width, h: height },
  );
  let minLeafSide = Infinity;
  const placed: PlacedGroup[] = blocks.map(({ item, rect }, groupIndex) => {
    const header = { x: rect.x, y: rect.y, w: rect.w, h: Math.min(options.headerHeight, rect.h) };
    const inner: Rect = {
      x: rect.x + options.pad,
      y: rect.y + options.headerHeight,
      w: Math.max(0, rect.w - options.pad * 2),
      h: Math.max(0, rect.h - options.headerHeight - options.pad),
    };
    const leaves = squarify(item.group.leaves, inner).map((leaf) => {
      // Leaves without weight get no area by design; a group with no weight at all is laid out equally.
      if (finitePositive(leaf.item.weight) > 0 || sums[groupIndex] === 0) minLeafSide = Math.min(minLeafSide, leaf.rect.w, leaf.rect.h);
      return { id: leaf.item.id, rect: leaf.rect };
    });
    return { id: item.group.id, rect, header, leaves };
  });
  return { width, height, groups: placed, minLeafSide };
}

export interface FitOptions {
  baseHeight: number;
  maxHeight: number;
  /** Target for the smallest leaf side, in pixels. */
  minSide: number;
  layout?: LayoutOptions;
}

export interface FitResult extends TreemapLayout {
  /** True when every leaf with weight reaches `minSide` (false only when `maxHeight` was hit first). */
  satisfied: boolean;
}

/**
 * Lays the groups out at `baseHeight` and, when some leaf is thinner than `minSide`, makes the map
 * taller (up to `maxHeight`) instead of letting it shrink: the page scrolls, the tiles stay tappable.
 * The final height is the smallest one found that satisfies the minimum.
 */
export function fitLayout(groups: readonly GroupInput[], width: number, options: FitOptions): FitResult {
  const layoutOptions = options.layout ?? DEFAULT_LAYOUT;
  const at = (height: number) => layoutGroups(groups, width, height, layoutOptions);
  const ok = (layout: TreemapLayout) => layout.minLeafSide >= options.minSide;
  let low = Math.max(1, options.baseHeight);
  let layout = at(low);
  if (ok(layout) || !(width > 0)) return { ...layout, satisfied: ok(layout) };
  let guard = 0;
  let high = low;
  while (!ok(layout) && high < options.maxHeight && guard++ < 40) {
    low = high;
    const ratio = Math.max(1.08, Math.min(1.6, options.minSide / Math.max(layout.minLeafSide, 1)));
    high = Math.min(options.maxHeight, Math.ceil(high * ratio));
    layout = at(high);
  }
  if (!ok(layout)) return { ...layout, satisfied: false };
  // Tighten between the last failing height and the first passing one.
  let best = layout;
  for (let step = 0; step < 8 && high - low > 4; step++) {
    const mid = Math.round((low + high) / 2);
    const candidate = at(mid);
    if (ok(candidate)) {
      best = candidate;
      high = mid;
    } else low = mid;
  }
  return { ...best, satisfied: true };
}

export type LabelFit = 'full' | 'name' | 'none';

const isWide = (char: string) => char.charCodeAt(0) >= 0x2e80;

/** Estimated rendered width of `text` in px at `fontSize` (CJK 1.05em, Latin 0.62em per character). */
export function textWidth(text: string, fontSize: number): number {
  return [...text].reduce((sum, char) => sum + (isWide(char) ? 1.05 : 0.62), 0) * fontSize;
}

/** Lines `text` needs at `fontSize` inside `width` px (greedy wrap; CJK breaks anywhere, Latin only at spaces). -1 when a word is wider than the box. */
export function wrapLines(text: string, width: number, fontSize: number): number {
  if (!(width > 0)) return -1;
  const em = (token: string) => textWidth(token, fontSize);
  const tokens: string[] = [];
  for (const word of text.split(/\s+/).filter(Boolean)) {
    let latin = '';
    for (const char of word) {
      if (isWide(char)) {
        if (latin) tokens.push(latin);
        latin = '';
        tokens.push(char);
      } else latin += char;
    }
    if (latin) tokens.push(latin);
    tokens.push(' ');
  }
  let lines = 1;
  let used = 0;
  const space = fontSize * 0.3;
  for (const token of tokens) {
    if (token === ' ') {
      used += space;
      continue;
    }
    const size = em(token);
    if (size > width) return -1;
    if (used + size > width && used > 0) {
      lines++;
      used = 0;
    }
    used += size;
  }
  return lines;
}

/**
 * How much text an industry cell can carry without clipping: the name wrapped over as many lines as the
 * cell is tall, plus the return line when there is room. The font shrinks from `preferred` towards 10px
 * before giving up; `none` cells stay clickable and use the tooltip.
 */
export function labelFit(name: string, width: number, height: number, preferred: number): { fit: LabelFit; fontSize: number } {
  const avail = width - 12;
  for (let fontSize = Math.round(preferred); fontSize >= 10; fontSize--) {
    const lines = wrapLines(name, avail, fontSize);
    if (lines < 0) continue;
    const textHeight = lines * fontSize * 1.2;
    if (textHeight + fontSize * 1.1 + 6 <= height) return { fit: 'full', fontSize };
    if (textHeight + 6 <= height) return { fit: 'name', fontSize };
  }
  return { fit: 'none', fontSize: 10 };
}

/** Prefer the complete name; a visibly abbreviated name is still useful in a narrow cell. */
export function cellLabelFit(name: string, width: number, height: number, preferred: number) {
  const full = labelFit(name, width, height, preferred);
  if (full.fit !== 'none') return { ...full, text: name };
  const letters = [...name];
  for (let length = letters.length - 1; length >= 1; length--) {
    const text = `${letters.slice(0, length).join('')}…`;
    const fit = labelFit(text, width, height, Math.min(preferred, 11));
    if (fit.fit !== 'none') return { ...fit, text };
  }
  return { ...full, text: name };
}

/** Where the tooltip for a cell goes: above it, else below it, else inside the cell, always inside the canvas horizontally. */
export function tipPosition(
  rect: Rect,
  canvas: { w: number; h: number },
  tip: { w: number; h: number },
): { left: number; top: number; above: boolean } {
  const left = Math.max(4, Math.min(canvas.w - tip.w - 4, rect.x + rect.w / 2 - tip.w / 2));
  if (rect.y - 6 >= tip.h) return { left, top: rect.y - 6, above: true };
  if (rect.y + rect.h + 6 + tip.h <= canvas.h) return { left, top: rect.y + rect.h + 6, above: false };
  return { left, top: Math.max(4, Math.min(rect.y + 6, canvas.h - tip.h - 4)), above: false };
}

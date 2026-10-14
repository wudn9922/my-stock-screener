import { describe, expect, it } from 'vitest';
import {
  DEFAULT_LAYOUT,
  fitLayout,
  floorWeights,
  labelFit,
  layoutGroups,
  median,
  squarify,
  tipPosition,
  wrapLines,
  type GroupInput,
  type Rect,
} from '../src/pages/treemap';

const area = (r: Rect) => r.w * r.h;
const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);
const items = (weights: number[]) => weights.map((weight) => ({ weight }));
const inside = (inner: Rect, outer: Rect) =>
  inner.x >= outer.x - 1e-6 && inner.y >= outer.y - 1e-6 && inner.x + inner.w <= outer.x + outer.w + 1e-6 && inner.y + inner.h <= outer.y + outer.h + 1e-6;

describe('squarify', () => {
  const rect = { x: 10, y: 20, w: 400, h: 250 };

  it('keeps the total area and each area proportional to its weight', () => {
    const weights = [60, 30, 25, 10, 10, 5, 3, 1];
    const placed = squarify(items(weights), rect);
    expect(sum(placed.map((p) => area(p.rect)))).toBeCloseTo(area(rect), 6);
    const total = sum(weights);
    placed.forEach((p, i) => expect(area(p.rect)).toBeCloseTo((weights[i]! / total) * area(rect), 6));
    for (const p of placed) expect(inside(p.rect, rect)).toBe(true);
  });

  it('never overlaps tiles', () => {
    const placed = squarify(items([9, 8, 7, 6, 5, 4, 3, 2, 1, 1, 1]), rect);
    for (let i = 0; i < placed.length; i++)
      for (let j = i + 1; j < placed.length; j++) {
        const a = placed[i]!.rect;
        const b = placed[j]!.rect;
        const overlapW = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
        const overlapH = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        expect(overlapW > 1e-6 && overlapH > 1e-6).toBe(false);
      }
  });

  it('is deterministic and keeps input order for the results', () => {
    const weights = [3, 3, 3, 1, 5, 5, 2];
    const first = squarify(items(weights), rect);
    const second = squarify(items(weights), rect);
    expect(second).toEqual(first);
    expect(first.map((p) => p.index)).toEqual(weights.map((_, i) => i));
  });

  it('handles empty, single and degenerate input', () => {
    expect(squarify([], rect)).toEqual([]);
    const [only] = squarify(items([7]), rect);
    expect(only!.rect).toEqual(rect);
    expect(squarify(items([1, 2]), { x: 0, y: 0, w: 0, h: 100 }).every((p) => area(p.rect) === 0)).toBe(true);
    expect(squarify(items([1, 2]), { x: 0, y: 0, w: NaN, h: 100 }).every((p) => area(p.rect) === 0)).toBe(true);
  });

  it('treats all-zero (or invalid) weights as equal sizes and never yields NaN', () => {
    for (const weights of [
      [0, 0, 0, 0],
      [NaN, -1, 0, Infinity * 0],
    ]) {
      const placed = squarify(items(weights), rect);
      expect(sum(placed.map((p) => area(p.rect)))).toBeCloseTo(area(rect), 6);
      for (const p of placed) {
        expect(Object.values(p.rect).every(Number.isFinite)).toBe(true);
        expect(area(p.rect)).toBeCloseTo(area(rect) / 4, 6);
      }
    }
  });

  it('gives a zero-weight item no area while the others still fill the rectangle', () => {
    const placed = squarify(items([5, 0, 3]), rect);
    expect(area(placed[1]!.rect)).toBe(0);
    expect(sum(placed.map((p) => area(p.rect)))).toBeCloseTo(area(rect), 6);
  });

  it('produces reasonable aspect ratios for equal weights', () => {
    const placed = squarify(items(Array.from({ length: 12 }, () => 1)), { x: 0, y: 0, w: 400, h: 300 });
    for (const p of placed) expect(Math.max(p.rect.w / p.rect.h, p.rect.h / p.rect.w)).toBeLessThan(2.1);
  });
});

describe('floorWeights', () => {
  it('lifts small weights to the floor and leaves large ones alone', () => {
    const out = floorWeights([100, 100, 100, 1, 0.5], 0.4);
    expect(out.slice(0, 3)).toEqual([100, 100, 100]);
    expect(out[3]).toBeCloseTo(40);
    expect(out[4]).toBeCloseTo(40);
    expect(Math.min(...out)).toBeGreaterThanOrEqual(0.4 * median([100, 100, 100, 1, 0.5]));
  });
  it('does not change its input and is deterministic', () => {
    const input = [4, 1, 9];
    const copy = [...input];
    expect(floorWeights(input, 0.5)).toEqual(floorWeights(input, 0.5));
    expect(input).toEqual(copy);
  });
  it('turns empty, all-zero and invalid input into something usable', () => {
    expect(floorWeights([], 0.4)).toEqual([]);
    expect(floorWeights([0, 0, 0], 0.4)).toEqual([1, 1, 1]);
    expect(floorWeights([NaN, -3], 0.4)).toEqual([1, 1]);
    expect(floorWeights([5], 0.4)).toEqual([5]);
  });
});

function groups(spec: number[][]): GroupInput[] {
  return spec.map((weights, g) => ({ id: `g${g}`, leaves: weights.map((weight, l) => ({ id: `g${g}:l${l}`, weight })) }));
}

describe('layoutGroups', () => {
  const spec = [[50, 20, 10, 5, 5], [30, 30, 10], [8, 4, 2, 1], [1], [12, 12]];

  it('conserves area: groups fill the canvas, leaves fill their group body', () => {
    const layout = layoutGroups(groups(spec), 600, 420);
    expect(sum(layout.groups.map((g) => area(g.rect)))).toBeCloseTo(600 * 420, 4);
    for (const g of layout.groups) {
      const body = {
        x: g.rect.x + DEFAULT_LAYOUT.pad,
        y: g.rect.y + DEFAULT_LAYOUT.headerHeight,
        w: g.rect.w - DEFAULT_LAYOUT.pad * 2,
        h: g.rect.h - DEFAULT_LAYOUT.headerHeight - DEFAULT_LAYOUT.pad,
      };
      expect(sum(g.leaves.map((l) => area(l.rect)))).toBeCloseTo(area(body), 4);
      for (const l of g.leaves) expect(inside(l.rect, g.rect)).toBe(true);
    }
  });

  it('keeps a tiny group at least groupFloor × the median group weight', () => {
    const spec2 = [[100, 100], [100, 100], [100, 100], [100, 100], [1]];
    const layout = layoutGroups(groups(spec2), 800, 600, { ...DEFAULT_LAYOUT, groupFloor: 0.5 });
    const tiny = layout.groups.find((g) => g.id === 'g4')!;
    const big = layout.groups.find((g) => g.id === 'g0')!;
    // median group weight is 200 → floor 100, half of a regular group
    expect(area(tiny.rect) / area(big.rect)).toBeCloseTo(0.5, 2);
  });

  it('is deterministic', () => {
    expect(layoutGroups(groups(spec), 500, 500)).toEqual(layoutGroups(groups(spec), 500, 500));
  });

  it('survives empty, single and zero-weight input', () => {
    expect(layoutGroups([], 300, 200).groups).toEqual([]);
    const single = layoutGroups(groups([[5]]), 300, 200);
    expect(single.groups).toHaveLength(1);
    expect(area(single.groups[0]!.rect)).toBeCloseTo(300 * 200, 6);
    const zero = layoutGroups(groups([[0, 0], [0, 0, 0]]), 300, 200);
    for (const g of zero.groups) for (const l of g.leaves) expect(Object.values(l.rect).every(Number.isFinite)).toBe(true);
    expect(Number.isFinite(zero.minLeafSide)).toBe(true);
    const emptyGroup = layoutGroups([{ id: 'a', leaves: [] }, ...groups([[1, 2]])], 300, 200);
    expect(area(emptyGroup.groups[0]!.rect)).toBe(0);
  });
});

describe('fitLayout', () => {
  // 30 themes × 8 stocks with weights between 1 and 3, on a phone-width canvas.
  const many = groups(
    Array.from({ length: 30 }, (_, g) => Array.from({ length: 8 }, (_, l) => 1 + ((g * 7 + l * 13) % 5) / 2)),
  );

  it('grows the canvas until the smallest tile reaches the minimum side', () => {
    const fit = fitLayout(many, 390, { baseHeight: 520, maxHeight: 12000, minSide: 22 });
    expect(fit.satisfied).toBe(true);
    expect(fit.minLeafSide).toBeGreaterThanOrEqual(22);
    expect(fit.height).toBeGreaterThan(520);
    // ... and no taller than needed: a few pixels less would not satisfy it
    const tighter = layoutGroups(many, 390, fit.height - 12);
    expect(tighter.minLeafSide).toBeLessThan(22);
    for (const g of fit.groups) for (const l of g.leaves) expect(Math.min(l.rect.w, l.rect.h)).toBeGreaterThanOrEqual(22 - 1e-6);
  });

  it('keeps the base height when everything already fits', () => {
    const few = groups([[1, 1, 1, 1], [1, 1, 1, 1]]);
    const fit = fitLayout(few, 900, { baseHeight: 500, maxHeight: 9000, minSide: 22 });
    expect(fit.height).toBe(500);
    expect(fit.satisfied).toBe(true);
  });

  it('stops at maxHeight and says so when the minimum cannot be met', () => {
    const skewed = groups([[1e9, ...Array.from({ length: 40 }, () => 1)]]);
    const fit = fitLayout(skewed, 360, { baseHeight: 400, maxHeight: 900, minSide: 22 });
    expect(fit.satisfied).toBe(false);
    expect(fit.height).toBeLessThanOrEqual(900);
  });

  it('does not crash on empty input or zero width', () => {
    expect(fitLayout([], 300, { baseHeight: 300, maxHeight: 900, minSide: 22 }).groups).toEqual([]);
    expect(() => fitLayout(many, 0, { baseHeight: 300, maxHeight: 900, minSide: 22 })).not.toThrow();
  });
});

describe('wrapLines', () => {
  it('wraps CJK anywhere and Latin at spaces, and refuses a word wider than the box', () => {
    expect(wrapLines('半導體業', 100, 12)).toBe(1);
    expect(wrapLines('電子零組件業', 40, 12)).toBe(2);
    expect(wrapLines('Semiconductors', 200, 12)).toBe(1);
    expect(wrapLines('Semiconductor Equipment & Materials', 120, 12)).toBeGreaterThan(1);
    expect(wrapLines('Semiconductors', 30, 12)).toBe(-1);
    expect(wrapLines('x', 0, 12)).toBe(-1);
  });
});

describe('labelFit', () => {
  it('shows name and return when both fit, the name alone when only it fits, nothing in tiny cells', () => {
    expect(labelFit('半導體業', 120, 80, 14).fit).toBe('full');
    expect(labelFit('半導體業', 60, 20, 12).fit).toBe('name');
    expect(labelFit('半導體業', 22, 22, 12).fit).toBe('none');
    expect(labelFit('Semiconductor Equipment & Materials', 40, 22, 12).fit).toBe('none');
    expect(labelFit('x', 0, 0, 12).fit).toBe('none');
  });
  it('shrinks the font before giving up, never below 10', () => {
    const fit = labelFit('電子零組件業', 60, 30, 15);
    expect(fit.fontSize).toBeGreaterThanOrEqual(10);
    expect(fit.fontSize).toBeLessThanOrEqual(15);
  });
});

describe('tipPosition', () => {
  const canvas = { w: 400, h: 600 };
  const tip = { w: 240, h: 200 };
  it('goes above a cell with room, below one near the top, inside a tall cell at the top', () => {
    expect(tipPosition({ x: 100, y: 300, w: 50, h: 40 }, canvas, tip)).toMatchObject({ above: true, top: 294 });
    expect(tipPosition({ x: 100, y: 20, w: 50, h: 40 }, canvas, tip)).toMatchObject({ above: false, top: 66 });
    const tall = tipPosition({ x: 100, y: 20, w: 50, h: 560 }, canvas, tip);
    expect(tall.above).toBe(false);
    expect(tall.top).toBeGreaterThanOrEqual(4);
    expect(tall.top + tip.h).toBeLessThanOrEqual(canvas.h - 4 + 1e-6);
  });
  it('keeps the tooltip inside the canvas horizontally', () => {
    expect(tipPosition({ x: 0, y: 300, w: 20, h: 20 }, canvas, tip).left).toBe(4);
    expect(tipPosition({ x: 380, y: 300, w: 20, h: 20 }, canvas, tip).left).toBe(156);
  });
});

import type { Theme, ThemeConstituent, ThemesData, ThemesMarket } from '../report/themes';
import type { Universe, UniverseMarketKey } from '../report/universe';
import { floorWeights, median, type GroupInput } from './treemap';

/**
 * Shared data side of the 題材 heatmap. Every cell of the map is an industry (全市場產業) or, while the
 * full-market industry data does not exist yet, a theme. This file holds what both share: the size
 * modes, cell weights, the grouped model handed to the treemap, and the theme-based fallback cells.
 */

export const WEIGHT_MODES = ['balanced', 'equal', 'size'] as const;
export type WeightMode = (typeof WEIGHT_MODES)[number];
export const DEFAULT_WEIGHT_MODE: WeightMode = 'balanced';

/** `turnover` is what the first version of the map stored for 「成交值」; it is now `size`. */
export function parseWeightMode(value: unknown): WeightMode {
  if (value === 'turnover') return 'size';
  return (WEIGHT_MODES as readonly unknown[]).includes(value) ? (value as WeightMode) : DEFAULT_WEIGHT_MODE;
}

/** How an industry's return is averaged: market-cap (or traded-value) weighted, or the equal-weight median. */
export const AGG_MODES = ['weighted', 'median'] as const;
export type AggMode = (typeof AGG_MODES)[number];
export const DEFAULT_AGG_MODE: AggMode = 'weighted';
export function parseAggMode(value: unknown): AggMode {
  return (AGG_MODES as readonly unknown[]).includes(value) ? (value as AggMode) : DEFAULT_AGG_MODE;
}

/** 均衡: a cell is never smaller than this × the median cell weight (after the square root). */
export const BALANCED_FLOOR = 0.5;
/** 市值/成交值: only keeps a zero-size cell from vanishing completely; far below any visible size. */
export const SIZE_FLOOR = 0.002;

export interface SizedInput {
  id: string;
  label: string;
  /** Raw size (market cap or traded value summed over the members); null when unknown. */
  size: number | null;
}
export interface GroupedInput {
  id: string;
  label: string;
  cells: SizedInput[];
}
export interface MapCell extends SizedInput {
  weight: number;
  /** True when the size was unknown and the median stands in for it. */
  imputed: boolean;
}
export interface MapGroup {
  id: string;
  label: string;
  cells: MapCell[];
}
export interface MapModel {
  /** The mode actually used: `equal` when no cell has a size whatever was asked for. */
  mode: WeightMode;
  groups: MapGroup[];
  imputed: number;
  cellCount: number;
}

/**
 * Cell weights.
 * - 均衡: sqrt(size ÷ median size), floored at BALANCED_FLOOR × the median weight. The square root squeezes the
 *   range (a mega-cap industry is not 100× a small one) and the floor keeps small industries visible and tappable.
 * - 市值 (or 成交值): proportional to the raw size (honest, small cells get small).
 * - 等大小, or no usable size at all: every cell weighs 1.
 * An unknown size counts as the median.
 */
export function buildMapModel(groups: readonly GroupedInput[], requested: WeightMode): MapModel {
  const all = groups.flatMap((group) => group.cells);
  const positive = all.map((cell) => cell.size).filter((v): v is number => v !== null && Number.isFinite(v) && v > 0);
  const mid = median(positive);
  const mode: WeightMode = requested === 'equal' || !positive.length ? 'equal' : requested;
  const known = (cell: SizedInput) => (cell.size !== null && Number.isFinite(cell.size) && cell.size > 0 ? cell.size : null);
  let raw: number[];
  if (mode === 'equal') raw = all.map(() => 1);
  else if (mode === 'balanced') raw = floorWeights(all.map((cell) => Math.sqrt((known(cell) ?? mid) / mid)), BALANCED_FLOOR);
  else raw = floorWeights(all.map((cell) => known(cell) ?? mid), SIZE_FLOOR);
  let index = 0;
  let imputed = 0;
  const out = groups.map((group): MapGroup => ({
    id: group.id,
    label: group.label,
    cells: group.cells.map((cell): MapCell => {
      const missing = mode !== 'equal' && known(cell) === null;
      if (missing) imputed++;
      return { ...cell, weight: raw[index++]!, imputed: missing };
    }),
  }));
  return { mode, groups: out, imputed, cellCount: all.length };
}

export function toGroupInputs(model: MapModel): GroupInput[] {
  return model.groups.map((group) => ({ id: group.id, leaves: group.cells.map((cell) => ({ id: cell.id, weight: cell.weight })) }));
}

export function universeKey(market: ThemesMarket): UniverseMarketKey {
  return market === 'tw' ? 'TW' : 'US';
}

/** Symbol → 20-day average traded value (positive values only). Null when the universe has no such market. */
export function turnoverLookup(universe: Universe | null | undefined, market: ThemesMarket): { values: Map<string, number>; asOf: string | null } | null {
  const entry = universe?.markets[universeKey(market)];
  if (!entry) return null;
  const values = new Map<string, number>();
  for (const item of entry.items) {
    const turnover = item.metrics?.turnover20 ?? null;
    if (turnover !== null && Number.isFinite(turnover) && turnover > 0) values.set(item.symbol.toUpperCase(), turnover);
  }
  return values.size ? { values, asOf: entry.asOf } : null;
}

/** The constituent's own 20-session turnover (themes file), when it carries a positive one. */
export function ownTurnover(constituent: ThemeConstituent): number | null {
  const value = constituent.turnover20 ?? null;
  return value !== null && Number.isFinite(value) && value > 0 ? value : null;
}

/** True when the themes file itself carries turnover for at least one constituent. */
export function themesHaveTurnover(themes: readonly Theme[]): boolean {
  return themes.some((theme) => theme.constituents.some((c) => ownTurnover(c) !== null));
}

export const NO_PARENT = '__none';

/** Theme turnover = the sum over members with a known value (themes file first, then the universe list). Null when none is known. */
export function themeTurnover(theme: Theme, lookup: Map<string, number> | null): { total: number | null; known: number } {
  let total = 0;
  let known = 0;
  for (const constituent of theme.constituents) {
    const value = ownTurnover(constituent) ?? lookup?.get(constituent.symbol) ?? null;
    if (value !== null) {
      total += value;
      known++;
    }
  }
  return { total: known ? total : null, known };
}

/** Fallback map input: one cell per theme, grouped by the theme's parent category (in the file's order). */
export function themeGroups(data: ThemesData, themes: readonly Theme[], lookup: Map<string, number> | null): GroupedInput[] {
  const usable = themes.filter((theme) => theme.constituents.length > 0);
  const groups: GroupedInput[] = [];
  for (const parent of data.parents) {
    const cells = usable.filter((theme) => theme.parent === parent.key);
    if (cells.length) groups.push({ id: parent.key, label: parent.name, cells: cells.map((t) => themeCell(t, lookup)) });
  }
  const orphans = usable.filter((theme) => !data.parents.some((p) => p.key === theme.parent));
  if (orphans.length) groups.push({ id: NO_PARENT, label: '其他', cells: orphans.map((t) => themeCell(t, lookup)) });
  return groups;
}

function themeCell(theme: Theme, lookup: Map<string, number> | null): SizedInput {
  return { id: theme.key, label: theme.name, size: themeTurnover(theme, lookup).total };
}

export function parentName(data: ThemesData, theme: Theme): string {
  return data.parents.find((p) => p.key === theme.parent)?.name ?? '';
}

/** Traded value / market cap for tooltips: 兆 / 億 / 萬, 元 for Taiwan and 美元 for the US. */
export function formatMoney(value: number | null, market: UniverseMarketKey): string {
  if (value === null || !Number.isFinite(value)) return '—';
  const currency = market === 'TW' ? '元' : '美元';
  if (value >= 1e12) return `${(value / 1e12).toFixed(2)} 兆${currency}`;
  if (value >= 1e8) return `${(value / 1e8).toFixed(value >= 1e10 ? 0 : 1)} 億${currency}`;
  return `${Math.round(value / 1e4).toLocaleString('en-US')} 萬${currency}`;
}

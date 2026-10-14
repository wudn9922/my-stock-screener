import type { PeriodKey, Theme, ThemeConstituent, ThemesData, ThemesMarket } from '../report/themes';
import type { Universe, UniverseMarketKey } from '../report/universe';
import { floorWeights, median, type GroupInput } from './treemap';

/**
 * Data side of the 題材 heatmap: turns themes + the full-market universe into treemap groups.
 * Tile size comes from `turnover20` (20-day average traded value) because the constituents carry no
 * market cap. It is a liquidity measure, not a size measure, and the page says so.
 */

export const WEIGHT_MODES = ['balanced', 'equal', 'turnover'] as const;
export type WeightMode = (typeof WEIGHT_MODES)[number];
export const WEIGHT_LABELS: Record<WeightMode, string> = { balanced: '均衡', equal: '等大小', turnover: '成交值' };
export const WEIGHT_HINTS: Record<WeightMode, string> = {
  balanced: '題材大小主要看檔數，題材內依成交值（開根號）決定相對大小並設最小面積，小型股也看得到、點得到',
  equal: '每檔股票一樣大',
  turnover: '大小與近 20 日平均成交值成正比，小型股會很小',
};
export const DEFAULT_WEIGHT_MODE: WeightMode = 'balanced';

export function parseWeightMode(value: unknown): WeightMode {
  return (WEIGHT_MODES as readonly unknown[]).includes(value) ? (value as WeightMode) : DEFAULT_WEIGHT_MODE;
}

/** 均衡: a leaf is never smaller than this × the median leaf of its theme (after the square root). */
export const BALANCED_FLOOR = 0.5;
/** 成交值: only keeps a zero-turnover stock from vanishing completely; far below any visible size. */
export const TURNOVER_FLOOR = 0.002;

/** Percent that saturates a stock tile's color; a stock moves further than a theme average does. */
export const STOCK_HEAT_CAP: Record<PeriodKey, number> = { d1: 5, w1: 10, m1: 20, m3: 35, m6: 60, ytd: 90 };

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

export interface MapLeaf {
  /** Unique per theme: a stock can sit in several themes. */
  id: string;
  symbol: string;
  name: string;
  constituent: ThemeConstituent;
  turnover: number | null;
  /** True when the turnover was not found and a median stands in for it. */
  imputed: boolean;
  weight: number;
}

export interface MapGroup {
  id: string;
  theme: Theme;
  leaves: MapLeaf[];
}

export interface MapModel {
  /** The mode actually used: `equal` when no turnover data is available whatever was asked for. */
  mode: WeightMode;
  groups: MapGroup[];
  /** Leaves whose size is a stand-in (no turnover found). */
  imputed: number;
  leafCount: number;
}

/**
 * Leaf weights for `themes`.
 * - 均衡: inside a theme, weight = sqrt(turnover ÷ the theme's median turnover), floored at BALANCED_FLOOR × the
 *   theme's median weight. Dividing by the theme median keeps themes comparable in size (mostly by head count)
 *   instead of letting a theme of mega caps swallow a theme of small caps; with raw magnitudes the
 *   smallest tile needs a 6000px+ tall map on a phone (Taiwan, measured), with it about 2400px.
 *   A missing turnover counts as the theme median; a theme with no data at all is laid out equally.
 * - 成交值: proportional to the raw turnover across all themes (honest, but small names get tiny).
 *   A missing turnover counts as the theme median, or the median of everything for a theme with none.
 * - 等大小, or no usable turnover at all (`lookup` null / empty): every leaf weighs 1.
 */
export function buildMapModel(themes: readonly Theme[], lookup: Map<string, number> | null, requested: WeightMode): MapModel {
  const known = (theme: Theme) => theme.constituents.map((c) => lookup?.get(c.symbol) ?? null);
  const everything = lookup ? themes.flatMap((theme) => known(theme).filter((v): v is number => v !== null)) : [];
  const globalMedian = median(everything);
  const mode: WeightMode = requested === 'equal' || !lookup || !everything.length ? 'equal' : requested;
  let imputed = 0;
  let leafCount = 0;
  const groups = themes.map((theme): MapGroup => {
    const turnovers = known(theme);
    const themeMedian = median(turnovers.filter((v): v is number => v !== null));
    let weights: number[];
    if (mode === 'equal' || (mode === 'balanced' && !(themeMedian > 0))) weights = turnovers.map(() => 1);
    else if (mode === 'balanced') weights = floorWeights(turnovers.map((v) => Math.sqrt((v ?? themeMedian) / themeMedian)), BALANCED_FLOOR);
    else weights = floorWeights(turnovers.map((v) => v ?? (themeMedian || globalMedian)), TURNOVER_FLOOR);
    const leaves = theme.constituents.map((constituent, index): MapLeaf => {
      const missing = turnovers[index] === null;
      if (mode !== 'equal' && missing) imputed++;
      leafCount++;
      return {
        id: `${theme.key}:${constituent.symbol}`,
        symbol: constituent.symbol,
        name: constituent.name,
        constituent,
        turnover: turnovers[index] ?? null,
        imputed: mode !== 'equal' && missing,
        weight: weights[index]!,
      };
    });
    return { id: theme.key, theme, leaves };
  });
  return { mode, groups, imputed, leafCount };
}

export function toGroupInputs(model: MapModel): GroupInput[] {
  return model.groups.map((group) => ({ id: group.id, leaves: group.leaves.map((leaf) => ({ id: leaf.id, weight: leaf.weight })) }));
}

/** Short ticker for a tile: `2330.TW` → `2330`. */
export function shortSymbol(symbol: string): string {
  return symbol.replace(/\.(TW|TWO)$/i, '');
}

/** Traded value for the tooltip: 新台幣 for Taiwan, 美元 for the US. */
export function formatTurnover(value: number | null, market: ThemesMarket): string {
  if (value === null || !Number.isFinite(value)) return '—';
  const currency = market === 'tw' ? '元' : '美元';
  if (value >= 1e8) return `${(value / 1e8).toFixed(value >= 1e10 ? 0 : 1)} 億${currency}`;
  return `${Math.round(value / 1e4).toLocaleString('en-US')} 萬${currency}`;
}

/** Color scale for the legend and the tiles: the cap that saturates `period`. */
export function stockHeatCap(period: PeriodKey): number {
  return STOCK_HEAT_CAP[period];
}

export function themeName(data: ThemesData, theme: Theme): string {
  return data.parents.find((p) => p.key === theme.parent)?.name ?? '';
}

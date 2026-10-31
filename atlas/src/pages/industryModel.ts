import type { PeriodKey, ThemesMarket } from '../report/themes';
import type { GroupItem } from '../report/schema';
import type { UniverseLoadResult, UniverseMarket, UniverseMarketKey } from '../report/universe';
import { turnoverLookup, universeKey, type AggMode, type GroupedInput } from './themesMapModel';
import { median } from './treemap';

/**
 * Industry side of the 題材 heatmap: every cell is an industry of the full-market universe.
 * Taiwan has one level (官方產業別); the US has two (sector outside, industry inside).
 * Industries with fewer than MIN_INDUSTRY_STOCKS stocks are merged into 「其他」 of the same sector
 * (Taiwan: of the whole market), and so are stocks without any classification.
 * All functions are pure; the page only paints what they return.
 */

export const MIN_INDUSTRY_STOCKS = 3;
export const OTHER_LABEL = '其他';
export const UNCLASSIFIED_LABEL = '未分類';
/** The industry map needs at least this many classified stocks, and this share of the list, to be worth showing. */
export const MIN_CLASSIFIED = 20;
export const MIN_CLASSIFIED_SHARE = 0.5;
/** Market cap is the size basis only when at least this share of the stocks carries one. */
export const CAP_COVERAGE = 0.6;

export const INDUSTRY_PERIODS = ['d1', 'w1', 'm1', 'm3'] as const;
export type IndustryPeriod = (typeof INDUSTRY_PERIODS)[number] & PeriodKey;
export const isIndustryPeriod = (period: string): period is IndustryPeriod => (INDUSTRY_PERIODS as readonly string[]).includes(period);
/** The universe has no 6-month / YTD return, so those fall back to 3 months on the industry map. */
export const clampIndustryPeriod = (period: PeriodKey): IndustryPeriod => (isIndustryPeriod(period) ? period : 'm3');

/** What the cells are sized by (and what 「加權」 weights with). */
export type SizeBasis = 'cap' | 'turnover' | 'none';

export interface IndustryStock {
  symbol: string;
  name: string;
  marketCap: number | null;
  turnover: number | null;
  returns: Record<IndustryPeriod, number | null>;
}

export interface IndustryCell {
  id: string;
  label: string;
  groupId: string;
  stocks: IndustryStock[];
  /** True for a 「其他」 cell: the industries folded into it, by name (unclassified stocks are not listed). */
  mergedFrom: string[];
  merged: boolean;
}

export interface IndustryGroup {
  id: string;
  label: string;
  cells: IndustryCell[];
  stocks: IndustryStock[];
}

export interface IndustryModel {
  market: UniverseMarketKey;
  /** US: sector → industry. Taiwan: industries only (one flat group). */
  twoLevel: boolean;
  basis: SizeBasis;
  groups: IndustryGroup[];
  cellCount: number;
  asOf: string | null;
  universe: string;
  scope: 'full' | 'liquid';
  coverage: { total: number; classified: number; withCap: number; withTurnover: number };
}

const posOrNull = (value: number | null | undefined) => (value !== null && value !== undefined && Number.isFinite(value) && value > 0 ? value : null);

export function stockOf(item: GroupItem): IndustryStock {
  return {
    symbol: item.symbol,
    name: item.name,
    marketCap: posOrNull(item.profile?.marketCap),
    turnover: posOrNull(item.metrics?.turnover20),
    returns: { d1: item.changePct, w1: item.metrics?.r5 ?? null, m1: item.metrics?.r21 ?? null, m3: item.metrics?.r63 ?? null },
  };
}

/** Chinese names for the Nasdaq screener's US sectors (industries keep the source's English names). */
export const US_SECTOR_ZH: Readonly<Record<string, string>> = {
  'Basic Materials': '基礎原物料',
  'Consumer Discretionary': '非必需消費',
  'Consumer Staples': '必需消費',
  Energy: '能源',
  Finance: '金融',
  'Health Care': '醫療保健',
  Industrials: '工業',
  Miscellaneous: '綜合',
  'Real Estate': '不動產',
  Technology: '科技',
  Telecommunications: '電信',
  Utilities: '公用事業',
};

/** Heatmap can omit the English suffix; other research views retain their existing source labels. */
export function sectorLabel(sector: string, chineseOnly = false): string {
  const zh = US_SECTOR_ZH[sector];
  return zh ? chineseOnly ? zh : `${zh} ${sector}` : sector;
}

/** TW: the industry (官方產業別), else the sector. US: the sector and the industry stay separate. */
function classify(item: GroupItem, market: UniverseMarketKey): { sector: string | null; industry: string | null } {
  const sector = item.profile?.sector ?? null;
  const industry = item.profile?.industry ?? null;
  return market === 'TW' ? { sector: null, industry: industry ?? sector } : { sector, industry };
}

/** Which size the map uses: market cap when it covers CAP_COVERAGE of the stocks, else traded value, else nothing. */
export function sizeBasis(total: number, withCap: number, withTurnover: number): SizeBasis {
  if (total > 0 && withCap / total >= CAP_COVERAGE) return 'cap';
  return withTurnover > 0 ? 'turnover' : 'none';
}

/** Groups the market's stocks into industries; null when the list is empty. */
export function buildIndustryModel(market: UniverseMarket): IndustryModel | null {
  const key = market.market;
  const twoLevel = key === 'US';
  const classified = market.items.map((item) => ({ stock: stockOf(item), ...classify(item, key) }));
  const total = classified.length;
  if (!total) return null;
  const withCap = classified.filter((row) => row.stock.marketCap !== null).length;
  const withTurnover = classified.filter((row) => row.stock.turnover !== null).length;
  const classifiedCount = classified.filter((row) => (twoLevel ? row.sector !== null || row.industry !== null : row.industry !== null)).length;

  // sector → industry → stocks, in first-seen order.
  const sectors = new Map<string, Map<string, IndustryStock[]>>();
  for (const row of classified) {
    const sector = twoLevel ? (row.sector ?? UNCLASSIFIED_LABEL) : '';
    const industry = row.industry ?? OTHER_LABEL;
    let bucket = sectors.get(sector);
    if (!bucket) sectors.set(sector, (bucket = new Map()));
    const list = bucket.get(industry);
    if (list) list.push(row.stock);
    else bucket.set(industry, [row.stock]);
  }

  const groups: IndustryGroup[] = [];
  for (const [sector, industries] of sectors) {
    const groupId = twoLevel ? `s:${sector}` : 'all';
    const cells: IndustryCell[] = [];
    const other: IndustryStock[] = [];
    const mergedFrom: string[] = [];
    for (const [industry, stocks] of industries) {
      if (industry !== OTHER_LABEL && stocks.length >= MIN_INDUSTRY_STOCKS) {
        cells.push({ id: `${groupId}|${industry}`, label: industry, groupId, stocks, mergedFrom: [], merged: false });
      } else {
        other.push(...stocks);
        if (industry !== OTHER_LABEL) mergedFrom.push(industry);
      }
    }
    if (other.length) cells.push({ id: `${groupId}|${OTHER_LABEL}`, label: OTHER_LABEL, groupId, stocks: other, mergedFrom, merged: true });
    groups.push({ id: groupId, label: sectorLabel(sector), cells, stocks: cells.flatMap((cell) => cell.stocks) });
  }
  return {
    market: key,
    twoLevel,
    basis: sizeBasis(total, withCap, withTurnover),
    groups,
    cellCount: groups.reduce((n, g) => n + g.cells.length, 0),
    asOf: market.asOf,
    universe: market.universe,
    scope: market.scope,
    coverage: { total, classified: classifiedCount, withCap, withTurnover },
  };
}

/** Raw size of a set of stocks under `basis` (the sum of what the basis measures); null when nothing is known. */
export function sizeOf(stocks: readonly IndustryStock[], basis: SizeBasis): number | null {
  if (basis === 'none') return null;
  let sum = 0;
  for (const stock of stocks) sum += (basis === 'cap' ? stock.marketCap : stock.turnover) ?? 0;
  return sum > 0 ? sum : null;
}

export interface StockPick {
  symbol: string;
  name: string;
  value: number;
}

export interface IndustrySummary {
  count: number;
  /** Stocks with a return in the period. */
  validCount: number;
  /** Return weighted by market cap (or traded value, per the basis); null without weights. */
  weighted: number | null;
  /** Equal-weight median. */
  median: number | null;
  /** Share of the valid stocks that rose (0–1). */
  upShare: number | null;
  /** Market cap summed over the stocks that have one (null when none). */
  marketCap: number | null;
  capCount: number;
  turnover: number | null;
  best: StockPick[];
  worst: StockPick[];
}

/** Everything the tooltip and the colour need for `stocks` in `period`. */
export function summarize(stocks: readonly IndustryStock[], period: IndustryPeriod, basis: SizeBasis): IndustrySummary {
  const valid: { stock: IndustryStock; value: number }[] = [];
  let weightSum = 0;
  let weighted = 0;
  let marketCap = 0;
  let capCount = 0;
  let turnover = 0;
  for (const stock of stocks) {
    if (stock.marketCap !== null) {
      marketCap += stock.marketCap;
      capCount++;
    }
    turnover += stock.turnover ?? 0;
    const value = stock.returns[period];
    if (value === null || !Number.isFinite(value)) continue;
    valid.push({ stock, value });
    const weight = basis === 'cap' ? stock.marketCap : basis === 'turnover' ? stock.turnover : null;
    if (weight !== null && weight > 0) {
      weightSum += weight;
      weighted += weight * value;
    }
  }
  const ranked = [...valid].sort((a, b) => b.value - a.value || a.stock.symbol.localeCompare(b.stock.symbol));
  // Best and worst never overlap: a small industry splits its stocks between the two lists.
  const bestCount = Math.min(3, Math.ceil(ranked.length / 2));
  const worstCount = Math.min(3, ranked.length - bestCount);
  const pick = ({ stock, value }: { stock: IndustryStock; value: number }): StockPick => ({ symbol: stock.symbol, name: stock.name, value });
  return {
    count: stocks.length,
    validCount: valid.length,
    weighted: weightSum > 0 ? weighted / weightSum : null,
    median: valid.length ? median(valid.map((v) => v.value)) : null,
    upShare: valid.length ? valid.filter((v) => v.value > 0).length / valid.length : null,
    marketCap: capCount ? marketCap : null,
    capCount,
    turnover: turnover > 0 ? turnover : null,
    best: ranked.slice(0, bestCount).map(pick),
    worst: ranked.slice(ranked.length - worstCount).reverse().map(pick),
  };
}

/** The number a cell is coloured by. A cell with no weights falls back to the median and says so. */
export function colorValue(summary: IndustrySummary, agg: AggMode): { value: number | null; fellBack: boolean } {
  if (agg === 'median') return { value: summary.median, fellBack: false };
  if (summary.weighted !== null) return { value: summary.weighted, fellBack: false };
  return { value: summary.median, fellBack: summary.median !== null };
}

/** Heatmap input for the treemap: sectors (or one flat group) of industry cells sized by `basis`. */
export function industryGroups(model: IndustryModel): GroupedInput[] {
  return model.groups.map((group) => ({
    id: group.id,
    label: group.label,
    cells: group.cells.map((cell) => ({ id: cell.id, label: cell.label, size: sizeOf(cell.stocks, model.basis) })),
  }));
}

/** The industry map is only worth drawing when the list is classified well enough. */
export function hasUsableProfile(model: IndustryModel | null): model is IndustryModel {
  if (!model) return false;
  const { classified, total } = model.coverage;
  return classified >= MIN_CLASSIFIED && classified / total >= MIN_CLASSIFIED_SHARE && model.cellCount >= 3;
}

export type MapSource =
  | { kind: 'loading' }
  | { kind: 'industry'; model: IndustryModel }
  | {
      kind: 'themes';
      reason: 'no-profile' | 'failed';
      /** Traded value by symbol from the universe list, when it loaded (sizes the theme cells). */
      lookup: Map<string, number> | null;
      lookupAsOf: string | null;
    };

export const FALLBACK_NOTICE = '全市場產業資料尚未產生，暫以題材分組顯示';

/**
 * Picks the map's data: the industry map when the universe carries enough profile data for the market,
 * the theme map otherwise (no profile columns yet, market missing, or the universe failed to load).
 * `null` result = the universe is still loading.
 */
export function resolveMapSource(universe: UniverseLoadResult | null, market: ThemesMarket): MapSource {
  if (universe === null) return { kind: 'loading' };
  if (universe.status !== 'ok') return { kind: 'themes', reason: 'failed', lookup: null, lookupAsOf: null };
  const entry = universe.universe.markets[universeKey(market)];
  if (entry) {
    const model = buildIndustryModel(entry);
    if (hasUsableProfile(model)) return { kind: 'industry', model };
  }
  const lookup = turnoverLookup(universe.universe, market);
  return { kind: 'themes', reason: 'no-profile', lookup: lookup?.values ?? null, lookupAsOf: lookup?.asOf ?? null };
}

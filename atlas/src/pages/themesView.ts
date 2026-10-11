import type { PeriodKey, Theme, ThemesData } from '../report/themes';

/** Pure view helpers for the 題材 page: metrics, ordering, heat colors and sparkline geometry. */

export type Mode = 'return' | 'rel';
export type ListSortKey = 'rank' | 'name' | PeriodKey | 'rs3m' | 'breadth' | 'momentum';
export type Direction = 'asc' | 'desc';

/** 題材 with fewer valid constituents than this are shown but never ranked or colored. */
export const MIN_VALID_FOR_RANK = 3;
const SPARK_SHORT = 22;
const SPARK_QUARTER = 64;

/** ((1 + theme) / (1 + benchmark) - 1) × 100, the same formula the Python job uses for RS. */
export function relativeTo(value: number | null, benchmark: number | null): number | null {
  if (value === null || benchmark === null) return null;
  return ((1 + value / 100) / (1 + benchmark / 100) - 1) * 100;
}

export function isRanked(theme: Theme): boolean {
  return theme.validCount >= MIN_VALID_FOR_RANK && theme.rank !== null;
}

export function metricValue(theme: Theme, data: ThemesData, period: PeriodKey, mode: Mode): number | null {
  const value = theme.returns[period];
  return mode === 'rel' ? relativeTo(value, data.benchmark.returns[period]) : value;
}

/** Percent that saturates the color scale; a day and a half-year do not move on the same scale. */
export const HEAT_CAP: Record<PeriodKey, number> = { d1: 3, w1: 6, m1: 14, m3: 24, m6: 36, ytd: 50 };

export interface Heat {
  side: 'up' | 'down' | 'flat';
  /** 0–100: share of the tone mixed into the tile background. */
  mix: number;
}

/**
 * Diverging scale centered on 0, clipped at ±HEAT_CAP. Which side is red or green follows the
 * site's color convention through the `--up` / `--down` CSS variables, so only the side is decided here.
 */
export function heat(value: number | null, period: PeriodKey): Heat {
  if (value === null || !Number.isFinite(value) || Math.abs(value) < 0.005) return { side: 'flat', mix: 0 };
  const strength = Math.min(Math.abs(value) / HEAT_CAP[period], 1);
  return { side: value > 0 ? 'up' : 'down', mix: Math.round(14 + strength * 56) };
}

export function filterByParent(themes: readonly Theme[], parent: string | null): Theme[] {
  return parent ? themes.filter((theme) => theme.parent === parent) : [...themes];
}

/** Hottest first by the chosen metric; themes without enough data sink to the bottom. */
export function tileOrder(themes: readonly Theme[], data: ThemesData, period: PeriodKey, mode: Mode): Theme[] {
  return [...themes].sort((a, b) => {
    const aThin = a.validCount < MIN_VALID_FOR_RANK;
    const bThin = b.validCount < MIN_VALID_FOR_RANK;
    if (aThin !== bThin) return aThin ? 1 : -1;
    const av = metricValue(a, data, period, mode);
    const bv = metricValue(b, data, period, mode);
    if (av === null || bv === null) return av === bv ? 0 : av === null ? 1 : -1;
    return bv - av;
  });
}

function listValue(theme: Theme, data: ThemesData, key: ListSortKey): number | string | null {
  switch (key) {
    case 'rank':
      return theme.rank;
    case 'name':
      return theme.name;
    case 'rs3m':
      return theme.rs.m3;
    case 'breadth':
      return theme.breadth50;
    case 'momentum':
      return theme.momentum;
    default:
      return metricValue(theme, data, key, 'return');
  }
}

/** Table/card ordering. Missing values always come last whatever the direction. */
export function sortThemes(themes: readonly Theme[], data: ThemesData, key: ListSortKey, direction: Direction): Theme[] {
  const sign = direction === 'asc' ? 1 : -1;
  return [...themes].sort((a, b) => {
    const av = listValue(a, data, key);
    const bv = listValue(b, data, key);
    if (av === null || bv === null) return av === bv ? 0 : av === null ? 1 : -1;
    if (typeof av === 'string' || typeof bv === 'string') return sign * String(av).localeCompare(String(bv), 'zh-Hant');
    return sign * (av - bv);
  });
}

/** Default direction when a column header is first chosen: rank counts up, everything else down. */
export function defaultDirection(key: ListSortKey): Direction {
  return key === 'rank' || key === 'name' ? 'asc' : 'desc';
}

/** How many of the 126 daily points a period's sparkline shows. */
export function sparkLength(period: PeriodKey, dates: readonly string[]): number {
  const total = dates.length;
  if (period === 'm6') return total;
  if (period === 'm3') return Math.min(SPARK_QUARTER, total);
  if (period === 'ytd') {
    const yearStart = `${(dates.at(-1) ?? '').slice(0, 4)}-01-01`;
    const inYear = dates.filter((date) => date >= yearStart).length;
    return Math.min(Math.max(inYear, SPARK_SHORT), total);
  }
  return Math.min(SPARK_SHORT, total);
}

/** Theme index (or theme ÷ benchmark in relative mode) over the last `length` points. */
export function sparkValues(theme: Theme, data: ThemesData, length: number, mode: Mode): number[] {
  const own = theme.series;
  if (!own.length) return [];
  if (mode === 'return') return own.slice(-length);
  const bench = data.benchmark.series;
  if (bench.length !== own.length) return own.slice(-length);
  return own.map((value, index) => (value / bench[index]!) * 100).slice(-length);
}

export interface SparkGeometry {
  line: string;
  area: string;
  last: { x: number; y: number } | null;
}

/** SVG path data for a sparkline filling width × height (padding keeps strokes and the end dot inside). */
export function sparkGeometry(values: readonly number[], width: number, height: number, pad = 2): SparkGeometry {
  if (values.length < 2) return { line: '', area: '', last: null };
  let min = Infinity;
  let max = -Infinity;
  for (const value of values) {
    if (value < min) min = value;
    if (value > max) max = value;
  }
  const span = max - min || 1;
  const usableW = width - pad * 2;
  const usableH = height - pad * 2;
  const points = values.map((value, index) => ({
    x: pad + (index / (values.length - 1)) * usableW,
    y: pad + (1 - (value - min) / span) * usableH,
  }));
  const fmt = (n: number) => Number(n.toFixed(1));
  const line = points.map((p, index) => `${index ? 'L' : 'M'}${fmt(p.x)} ${fmt(p.y)}`).join('');
  const first = points[0]!;
  const last = points.at(-1)!;
  const area = `${line}L${fmt(last.x)} ${fmt(height)}L${fmt(first.x)} ${fmt(height)}Z`;
  return { line, area, last: { x: fmt(last.x), y: fmt(last.y) } };
}

export type ChartRange = '1m' | '3m' | '6m';
export const CHART_RANGES: { key: ChartRange; label: string; points: number }[] = [
  { key: '1m', label: '1月', points: SPARK_SHORT },
  { key: '3m', label: '3月', points: SPARK_QUARTER },
  { key: '6m', label: '6月', points: Infinity },
];

export interface ChartData {
  dates: string[];
  theme: number[];
  bench: number[];
}

/** Theme and benchmark rebased to 100 at the first visible day. Empty when the series do not align. */
export function chartData(theme: Theme, data: ThemesData, range: ChartRange): ChartData {
  const total = data.dates.length;
  if (!theme.series.length || theme.series.length !== total || data.benchmark.series.length !== total)
    return { dates: [], theme: [], bench: [] };
  const wanted = CHART_RANGES.find((r) => r.key === range)!.points;
  const length = Math.min(wanted, total);
  const rebase = (values: number[]) => {
    const slice = values.slice(-length);
    const base = slice[0]!;
    return slice.map((value) => (value / base) * 100);
  };
  return { dates: data.dates.slice(-length), theme: rebase(theme.series), bench: rebase(data.benchmark.series) };
}

/** Whole days between the report's as-of date and `now` (UTC dates), for the "data is old" note. */
export function dataAgeDays(asOf: string | null, now: Date): number | null {
  if (!asOf || !/^\d{4}-\d{2}-\d{2}$/.test(asOf)) return null;
  const then = Date.parse(`${asOf}T00:00:00Z`);
  if (Number.isNaN(then)) return null;
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Math.round((today - then) / 86_400_000);
}

export function breadthLabel(breadth: number | null): string {
  if (breadth === null) return '—';
  if (breadth >= 80) return '幾乎全數站上';
  if (breadth >= 60) return '多數站上';
  if (breadth > 40) return '強弱參半';
  if (breadth > 20) return '多數跌破';
  return '幾乎全數跌破';
}

export function themeByKey(data: ThemesData, key: string | undefined): Theme | undefined {
  return key ? data.themes.find((theme) => theme.key === key) : undefined;
}

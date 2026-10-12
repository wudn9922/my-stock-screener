import { NOMINAL_SERIES, REAL_SERIES, type HistoryBlock, type RatesHistory, type RatesSpread } from '../report/rates';

/**
 * Pure view helpers for the rates research view: spread series from the yield history, inversion
 * episodes, rebasing total-return indices to a window, and one frame of the yield-curve replay.
 */

export interface SpreadDef {
  key: string;
  label: string;
  long: string;
  short: string;
}
/** Only the two visible spreads; FEDTARGETMID = (DFEDTARU + DFEDTARL) / 2. */
export const SPREAD_DEFS: readonly SpreadDef[] = [
  { key: '10yfed', label: '10Y−Fed', long: 'DGS10', short: 'FEDTARGETMID' },
  { key: '10y3m', label: '10Y−3M', long: 'DGS10', short: 'DGS3MO' },
];

const finite = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value);
const round1 = (value: number) => Math.round(value * 10) / 10;

/** Long minus short leg in basis points (one decimal); null where either leg is missing. */
export function spreadSeries(block: HistoryBlock, long: string, short: string): { dates: string[]; values: (number | null)[] } {
  const a = block.series[long] ?? [];
  const b = block.series[short] ?? [];
  return {
    dates: block.dates,
    values: block.dates.map((_, index) => {
      const x = a[index];
      const y = b[index];
      return finite(x) && finite(y) ? round1((x - y) * 100) : null;
    }),
  };
}

export interface InversionRange {
  start: string;
  /** Last negative observation; null while the inversion is still running at the end of the data. */
  end: string | null;
  startIndex: number;
  endIndex: number;
  minBp: number;
  minDate: string;
}

const dayNumber = (date: string) => Date.parse(`${date}T00:00:00Z`) / 86400000;

/**
 * Runs of negative values. Missing points neither end nor extend a run. Runs separated by at most
 * `mergeGapDays` calendar days are merged (so a one-week flip above zero does not split an episode),
 * then runs shorter than `minDays` calendar days are dropped unless they are still running.
 */
export function inversionRanges(
  dates: readonly string[],
  values: readonly (number | null)[],
  options: { mergeGapDays?: number; minDays?: number } = {},
): InversionRange[] {
  const { mergeGapDays = 0, minDays = 0 } = options;
  const runs: InversionRange[] = [];
  let current: InversionRange | null = null;
  let lastIndex = -1;
  dates.forEach((date, index) => {
    const value = values[index];
    if (!finite(value)) return;
    lastIndex = index;
    if (value < 0) {
      if (!current) current = { start: date, end: date, startIndex: index, endIndex: index, minBp: value, minDate: date };
      current.end = date;
      current.endIndex = index;
      if (value < current.minBp) {
        current.minBp = value;
        current.minDate = date;
      }
    } else if (current) {
      runs.push(current);
      current = null;
    }
  });
  if (current) runs.push(current);
  const merged: InversionRange[] = [];
  for (const run of runs) {
    const previous = merged.at(-1);
    if (previous && previous.end !== null && dayNumber(run.start) - dayNumber(previous.end) <= mergeGapDays) {
      previous.end = run.end;
      previous.endIndex = run.endIndex;
      if (run.minBp < previous.minBp) {
        previous.minBp = run.minBp;
        previous.minDate = run.minDate;
      }
    } else merged.push({ ...run });
  }
  const last = merged.at(-1);
  const ongoing = !!last && last.endIndex === lastIndex;
  return merged
    .filter((run) => (ongoing && run === last) || dayNumber(run.end!) - dayNumber(run.start) >= minDays)
    .map((run) => (ongoing && run === last ? { ...run, end: null } : run));
}

/** First index whose date is on or after `years` before the last date; 0 for the whole history. */
export function windowStartIndex(dates: readonly string[], years: number | null): number {
  if (years === null || !dates.length) return 0;
  const last = new Date(`${dates.at(-1)}T00:00:00Z`);
  last.setUTCFullYear(last.getUTCFullYear() - years);
  const target = last.toISOString().slice(0, 10);
  const index = dates.findIndex((date) => date >= target);
  return index < 0 ? 0 : index;
}

/**
 * The slice from `startIndex`, rebased so the first available value is 100. A series that starts
 * later (fund launched inside the window) is rebased at its own first value; `lateStart` says so.
 */
export function rebase(values: readonly (number | null)[], startIndex = 0): { values: (number | null)[]; baseIndex: number | null; lateStart: boolean } {
  const from = Math.max(0, startIndex);
  const slice = values.slice(from);
  const offset = slice.findIndex((value) => finite(value) && value > 0);
  if (offset < 0) return { values: slice.map(() => null), baseIndex: null, lateStart: false };
  const base = slice[offset]!;
  return {
    values: slice.map((value, index) => (index < offset || !finite(value) ? null : (value / base) * 100)),
    baseIndex: from + offset,
    lateStart: offset > 1,
  };
}

/** Latest non-null value of a series on or before `date` (YYYY-MM-DD). */
export function valueOnOrBefore(block: HistoryBlock, id: string, date: string): { date: string; value: number } | null {
  const series = block.series[id];
  if (!series) return null;
  for (let index = block.dates.length - 1; index >= 0; index -= 1) {
    if (block.dates[index]! > date) continue;
    const value = series[index];
    if (finite(value)) return { date: block.dates[index]!, value };
  }
  return null;
}

export interface CurveFrame {
  date: string;
  nominal: (number | null)[];
  real: (number | null)[];
  /** 10Y − 2Y of that week in basis points. */
  spread10y2yBp: number | null;
}

/** One week of the yield-curve replay (weekly history), tenors in the given order. */
export function curveFrame(
  history: RatesHistory,
  index: number,
  tenors: readonly string[] = Object.keys(NOMINAL_SERIES),
  realTenors: readonly string[] = Object.keys(REAL_SERIES),
): CurveFrame | null {
  const block = history.weekly;
  if (index < 0 || index >= block.dates.length) return null;
  const pick = (id: string | undefined) => {
    const value = id ? block.series[id]?.[index] : null;
    return finite(value) ? value : null;
  };
  const ten = pick('DGS10');
  const two = pick('DGS2');
  return {
    date: block.dates[index]!,
    nominal: tenors.map((tenor) => pick(NOMINAL_SERIES[tenor])),
    real: realTenors.map((tenor) => pick(REAL_SERIES[tenor])),
    spread10y2yBp: ten !== null && two !== null ? Math.round((ten - two) * 100) : null,
  };
}

/** Rounded y-axis bounds for a set of curves (percent), with at least `minSpan` points of range. */
export function curveDomain(curves: readonly (readonly (number | null)[])[], minSpan = 1): [number, number] {
  const values = curves.flat().filter(finite);
  if (!values.length) return [0, 5];
  let low = Math.min(...values);
  let high = Math.max(...values);
  if (high - low < minSpan) {
    const mid = (high + low) / 2;
    low = mid - minSpan / 2;
    high = mid + minSpan / 2;
  }
  const pad = (high - low) * 0.08;
  return [Math.floor((low - pad) * 4) / 4, Math.ceil((high + pad) * 4) / 4];
}

const month = (date: string) => `${date.slice(0, 4)}/${date.slice(5, 7)}`;
const slash = (date: string) => date.replaceAll('-', '/');

/** Plain-language lines for a spread: current state, the last inversion and the 5-year percentile. */
export function spreadSummary(spread: RatesSpread, label: string): string[] {
  const lines: string[] = [];
  const value = spread.valueBp;
  if (value === null) lines.push(`${label} 目前沒有資料。`);
  else if (spread.inverted) {
    // invertedDays counts trading days (observations) of the current episode.
    const days = spread.invertedDays !== null && spread.invertedDays > 0 ? `，已持續 ${spread.invertedDays} 個交易日` : '';
    const since = spread.lastInversion && spread.lastInversion.end === null ? `（自 ${slash(spread.lastInversion.start)} 起）` : '';
    lines.push(`目前倒掛 ${Math.round(value)}bp${days}${since}。`);
  } else lines.push(`目前未倒掛（${value > 0 ? '+' : ''}${Math.round(value)}bp）。`);
  const last = spread.lastInversion;
  if (last && last.end !== null) {
    const deepest = last.minBp !== null ? `，最深 ${Math.round(last.minBp)}bp${last.minDate ? `（${slash(last.minDate)}）` : ''}` : '';
    lines.push(`最近一次倒掛 ${month(last.start)}–${month(last.end)}${deepest}。`);
  } else if (last && last.end === null && last.minBp !== null) {
    lines.push(`這段倒掛最深 ${Math.round(last.minBp)}bp${last.minDate ? `（${slash(last.minDate)}）` : ''}。`);
  } else if (!last) lines.push('資料期間內沒有倒掛紀錄。');
  if (spread.pct5y !== null) lines.push(`目前利差位於近 5 年第 ${Math.round(spread.pct5y)} 百分位（0＝最低、100＝最高）。`);
  return lines;
}

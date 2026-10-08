/**
 * Pure performance math for the world-index comparison page. Bars are daily, sorted by time
 * (Unix seconds); calendars differ per market and may contain holidays or missing sessions.
 */
export interface CloseBar {
  time: number;
  close: number;
}
export const WINDOWS = ['1M', '3M', '6M', 'YTD', '1Y'] as const;
export type PerformanceWindow = (typeof WINDOWS)[number];
export type ReturnPeriod = '1D' | '1W' | '1M' | 'YTD';

const DAY = 86400;

function utcDate(time: number) {
  return new Date(time * 1000);
}

/** Subtracts calendar months in UTC, clamping to the month's last day (Mar 31 − 1M → Feb 28/29). */
export function minusMonths(time: number, months: number): number {
  const date = utcDate(time);
  const year = date.getUTCFullYear();
  const month = date.getUTCMonth() - months;
  const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return (
    Date.UTC(
      year,
      month,
      Math.min(date.getUTCDate(), lastDay),
      date.getUTCHours(),
      date.getUTCMinutes(),
      date.getUTCSeconds(),
    ) / 1000
  );
}

export function yearStart(time: number): number {
  return Date.UTC(utcDate(time).getUTCFullYear(), 0, 1) / 1000;
}

/** Start of a comparison window, anchored at the latest bar time. */
export function windowStart(window: PerformanceWindow, latest: number): number {
  switch (window) {
    case '1M':
      return minusMonths(latest, 1);
    case '3M':
      return minusMonths(latest, 3);
    case '6M':
      return minusMonths(latest, 6);
    case '1Y':
      return minusMonths(latest, 12);
    case 'YTD':
      return yearStart(latest);
  }
}

function validBars(bars: readonly CloseBar[]): CloseBar[] {
  return bars.filter((bar) => Number.isFinite(bar.time) && Number.isFinite(bar.close) && bar.close > 0);
}

/** Last bar at or before `time`, or null. */
export function closeAtOrBefore(bars: readonly CloseBar[], time: number): CloseBar | null {
  let lo = 0,
    hi = bars.length - 1,
    found: CloseBar | null = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid]!.time <= time) {
      found = bars[mid]!;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

/**
 * Rebases closes to 0 % at the base close: the last close at or before the window start (so a
 * window starting on a holiday uses the previous session), or the first bar inside the window when
 * the history starts later. Returns [] when there is no usable bar.
 */
export function rebase(bars: readonly CloseBar[], start: number): { time: number; value: number }[] {
  const clean = validBars(bars);
  if (!clean.length) return [];
  const base = closeAtOrBefore(clean, start) ?? clean.find((bar) => bar.time >= start);
  if (!base) return [];
  return clean
    .filter((bar) => bar.time >= base.time)
    .map((bar) => ({ time: bar.time, value: (bar.close / base.close - 1) * 100 }));
}

/** Maximum distance between the requested reference date and the session actually used. */
const MAX_REFERENCE_GAP: Record<ReturnPeriod, number> = {
  '1D': 15 * DAY,
  '1W': 10 * DAY,
  '1M': 15 * DAY,
  YTD: 31 * DAY,
};

/**
 * Percentage return over a period, measured from the reference close:
 * 1D – previous session; 1W/1M – last close at or before latest − 7 days / − 1 month;
 * YTD – last close of the previous calendar year (e.g. Dec 30 when Dec 31 was a holiday).
 * Returns null when the history does not reach the reference or the gap is implausibly large.
 */
export function periodReturn(bars: readonly CloseBar[], period: ReturnPeriod): number | null {
  const clean = validBars(bars);
  const last = clean.at(-1);
  if (!last) return null;
  let reference: CloseBar | null;
  let target: number;
  if (period === '1D') {
    reference = clean.at(-2) ?? null;
    target = last.time - DAY;
  } else if (period === '1W') {
    target = last.time - 7 * DAY;
    reference = closeAtOrBefore(clean, target);
  } else if (period === '1M') {
    target = minusMonths(last.time, 1);
    reference = closeAtOrBefore(clean, target);
  } else {
    target = yearStart(last.time) - 1;
    reference = closeAtOrBefore(clean, target);
  }
  if (!reference || reference === last) return null;
  if (target - reference.time > MAX_REFERENCE_GAP[period]) return null;
  return (last.close / reference.close - 1) * 100;
}

export interface PerformanceRow {
  last: number | null;
  r1d: number | null;
  r1w: number | null;
  r1m: number | null;
  ytd: number | null;
  asOf: number | null;
}

export function performanceRow(bars: readonly CloseBar[]): PerformanceRow {
  const clean = validBars(bars);
  const last = clean.at(-1);
  return {
    last: last?.close ?? null,
    r1d: periodReturn(clean, '1D'),
    r1w: periodReturn(clean, '1W'),
    r1m: periodReturn(clean, '1M'),
    ytd: periodReturn(clean, 'YTD'),
    asOf: last?.time ?? null,
  };
}

export type SortKey = 'name' | 'last' | 'r1d' | 'r1w' | 'r1m' | 'ytd';

/** Sorts rows by a numeric/text key; missing values always sort last regardless of direction. */
export function sortRows<T extends { name: string } & Partial<Record<Exclude<SortKey, 'name'>, number | null>>>(
  rows: readonly T[],
  key: SortKey,
  direction: 'asc' | 'desc',
): T[] {
  const sign = direction === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    if (key === 'name') return sign * a.name.localeCompare(b.name, 'zh-Hant');
    const x = a[key] ?? null,
      y = b[key] ?? null;
    if (x === null && y === null) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    return sign * (x - y);
  });
}

/** Runs `task` over `items` with at most `limit` in flight; results keep input order. */
export async function mapWithConcurrency<T, R>(
  items: readonly T[],
  limit: number,
  task: (item: T, index: number) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const index = next++;
      try {
        results[index] = { status: 'fulfilled', value: await task(items[index]!, index) };
      } catch (reason) {
        results[index] = { status: 'rejected', reason };
      }
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, worker));
  return results;
}

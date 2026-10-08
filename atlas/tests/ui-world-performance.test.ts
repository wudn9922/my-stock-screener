import { describe, expect, it } from 'vitest';
import {
  closeAtOrBefore,
  mapWithConcurrency,
  minusMonths,
  performanceRow,
  periodReturn,
  rebase,
  sortRows,
  windowStart,
  yearStart,
  type CloseBar,
} from '../src/pages/worldPerformance';

const day = (iso: string) => Date.parse(`${iso}T13:30:00Z`) / 1000;
const bars = (rows: [string, number][]): CloseBar[] => rows.map(([date, close]) => ({ time: day(date), close }));
const iso = (time: number) => new Date(time * 1000).toISOString().slice(0, 10);

describe('calendar windows', () => {
  it('subtracts months with end-of-month clamping and keeps the time of day', () => {
    expect(iso(minusMonths(day('2026-03-31'), 1))).toBe('2026-02-28');
    expect(iso(minusMonths(day('2024-03-31'), 1))).toBe('2024-02-29');
    expect(iso(minusMonths(day('2026-01-15'), 3))).toBe('2025-10-15');
    expect(minusMonths(day('2026-10-08'), 12) % 86400).toBe(day('2026-10-08') % 86400);
  });

  it('anchors 1M/3M/6M/1Y/YTD at the latest bar', () => {
    const latest = day('2026-10-08');
    expect(iso(windowStart('1M', latest))).toBe('2026-09-08');
    expect(iso(windowStart('3M', latest))).toBe('2026-07-08');
    expect(iso(windowStart('6M', latest))).toBe('2026-04-08');
    expect(iso(windowStart('1Y', latest))).toBe('2025-10-08');
    expect(windowStart('YTD', latest)).toBe(yearStart(latest));
    expect(iso(yearStart(latest))).toBe('2026-01-01');
  });
});

describe('rebasing', () => {
  const series = bars([
    ['2026-09-03', 100],
    ['2026-09-04', 102],
    // 2026-09-07: holiday
    ['2026-09-08', 110],
    ['2026-09-09', 99],
  ]);

  it('uses the last close at or before the window start (holiday → previous session)', () => {
    const rebased = rebase(series, day('2026-09-07'));
    expect(rebased.map((p) => iso(p.time))).toEqual(['2026-09-04', '2026-09-08', '2026-09-09']);
    expect(rebased[0]!.value).toBe(0);
    expect(rebased[1]!.value).toBeCloseTo((110 / 102 - 1) * 100, 10);
    expect(rebased[2]!.value).toBeCloseTo((99 / 102 - 1) * 100, 10);
  });

  it('starts at the first bar when history begins inside the window', () => {
    const rebased = rebase(series, day('2026-01-01'));
    expect(rebased).toHaveLength(4);
    expect(rebased[0]).toEqual({ time: day('2026-09-03'), value: 0 });
  });

  it('ignores invalid closes and returns [] without usable data', () => {
    expect(rebase([], day('2026-01-01'))).toEqual([]);
    expect(rebase([{ time: day('2026-01-02'), close: 0 }, { time: day('2026-01-03'), close: Number.NaN }], 0)).toEqual([]);
    expect(rebase([{ time: day('2026-01-02'), close: -5 }, { time: day('2026-01-05'), close: 10 }], day('2026-01-01'))).toEqual([
      { time: day('2026-01-05'), value: 0 },
    ]);
  });

  it('finds the close at or before a time', () => {
    expect(closeAtOrBefore(series, day('2026-09-07'))?.close).toBe(102);
    expect(closeAtOrBefore(series, day('2026-09-01'))).toBeNull();
    expect(closeAtOrBefore(series, day('2026-12-31'))?.close).toBe(99);
  });
});

describe('period returns', () => {
  it('computes 1D, 1W and 1M from the reference session', () => {
    const series = bars([
      ['2026-09-07', 90],
      ['2026-09-08', 100],
      ['2026-09-30', 104],
      ['2026-10-01', 120],
      ['2026-10-07', 125],
      ['2026-10-08', 132],
    ]);
    expect(periodReturn(series, '1D')).toBeCloseTo((132 / 125 - 1) * 100, 10);
    expect(periodReturn(series, '1W')).toBeCloseTo((132 / 120 - 1) * 100, 10);
    expect(periodReturn(series, '1M')).toBeCloseTo((132 / 100 - 1) * 100, 10);
  });

  it('measures YTD from the last session of the previous year, across a Dec 31 holiday', () => {
    const series = bars([
      ['2025-12-29', 95],
      ['2025-12-30', 100],
      // 2025-12-31 and 2026-01-01 closed
      ['2026-01-02', 103],
      ['2026-06-30', 120],
    ]);
    expect(periodReturn(series, 'YTD')).toBeCloseTo(20, 10);
  });

  it('returns null when history does not reach the reference or the gap is implausible', () => {
    expect(periodReturn(bars([['2026-01-05', 10], ['2026-03-01', 12]]), 'YTD')).toBeNull();
    // Last prior-year bar is months old (data gap): no YTD rather than a wrong one.
    expect(periodReturn(bars([['2025-06-30', 10], ['2026-02-02', 12]]), 'YTD')).toBeNull();
    expect(periodReturn(bars([['2026-10-08', 10]]), '1D')).toBeNull();
    expect(periodReturn(bars([['2026-08-01', 10], ['2026-10-08', 11]]), '1W')).toBeNull();
    expect(periodReturn([], '1M')).toBeNull();
  });

  it('builds a table row', () => {
    const row = performanceRow(bars([['2025-12-31', 100], ['2026-10-07', 110], ['2026-10-08', 121]]));
    expect(row.last).toBe(121);
    expect(row.r1d).toBeCloseTo(10, 10);
    expect(row.ytd).toBeCloseTo(21, 10);
    expect(row.r1w).toBeNull();
    expect(row.asOf).toBe(day('2026-10-08'));
  });
});

describe('table sorting and fetching', () => {
  const rows = [
    { name: '日經225', ytd: 5 as number | null },
    { name: '台灣加權', ytd: null },
    { name: 'S&P 500', ytd: 12 },
  ];

  it('sorts numerically with missing values last in both directions', () => {
    expect(sortRows(rows, 'ytd', 'desc').map((r) => r.name)).toEqual(['S&P 500', '日經225', '台灣加權']);
    expect(sortRows(rows, 'ytd', 'asc').map((r) => r.name)).toEqual(['日經225', 'S&P 500', '台灣加權']);
    expect(sortRows(rows, 'name', 'asc')).toHaveLength(3);
  });

  it('limits concurrency, keeps input order and isolates failures', async () => {
    let active = 0,
      peak = 0;
    const results = await mapWithConcurrency([1, 2, 3, 4, 5, 6, 7], 3, async (n) => {
      active++;
      peak = Math.max(peak, active);
      await new Promise((resolve) => setTimeout(resolve, 5 * (8 - n)));
      active--;
      if (n === 4) throw new Error('暫無資料');
      return n * 10;
    });
    expect(peak).toBe(3);
    expect(results.map((r) => (r.status === 'fulfilled' ? r.value : 'x'))).toEqual([10, 20, 30, 'x', 50, 60, 70]);
    expect(await mapWithConcurrency([], 4, async () => 1)).toEqual([]);
  });
});

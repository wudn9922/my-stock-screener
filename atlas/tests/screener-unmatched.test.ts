import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { filterAndSortItems, itemsForView, resultCountText, statusCounts } from '../src/pages/screenerItems';
import { groupItemSchema, itemStatusOf, reportGroupSchema, reportSchema, type GroupItem } from '../src/report/schema';

const sample: unknown = JSON.parse(readFileSync('src/report/fixtures/latest.sample.json', 'utf8'));
const item = (raw: Record<string, unknown>): GroupItem => groupItemSchema.parse(raw);

const matched = (symbol: string, changePct: number) =>
  item({ symbol, close: 100, changePct, maList: [20], maValues: { 20: 98 } });
const unmatched = (symbol: string, changePct: number) =>
  item({ symbol, status: 'unmatched', close: 100, changePct, maList: [20], maValues: { 20: 80 }, note: '未符合均線條件' });
const failed = (symbol: string) => item({ symbol, status: 'failed', close: null, changePct: null, note: '下載失敗' });

describe('report schema: item status', () => {
  it('reads a report written before the field existed as all matched', () => {
    const old = reportGroupSchema.parse({ key: 'tw_g1', name: 'g', items: [{ symbol: '2330.TW', close: 1 }, { symbol: '2317.TW' }] });
    expect(old.items.map(itemStatusOf)).toEqual(['matched', 'matched']);
    expect(old.items.every((row) => row.status === undefined)).toBe(true);
  });

  it('keeps unmatched and failed rows, and failed rows may have no price', () => {
    const group = reportGroupSchema.parse({
      key: 'custom_1',
      items: [
        { symbol: 'AAA', close: 5, changePct: 1 },
        { symbol: 'bbb', status: 'unmatched', close: 4, changePct: -1, maValues: { 20: 3 }, note: '未符合均線條件' },
        { symbol: 'ccc', status: 'failed', close: null, changePct: null, note: '下載失敗' },
        { symbol: 'ddd', status: 'FAILED' },
      ],
    });
    expect(group.items.map((row) => [row.symbol, itemStatusOf(row)])).toEqual([
      ['AAA', 'matched'],
      ['BBB', 'unmatched'],
      ['CCC', 'failed'],
      ['DDD', 'failed'],
    ]);
    expect(group.items[2]).toMatchObject({ close: null, changePct: null, note: '下載失敗' });
  });

  it('treats an unknown status as matched and drops a repeated unmatched symbol', () => {
    const group = reportGroupSchema.parse({
      key: 'custom_1',
      items: [
        { symbol: 'AAA', status: 'bogus' },
        { symbol: 'AAA', status: 'unmatched' },
        { symbol: 'BBB', status: 'unmatched' },
        { symbol: 'BBB', status: 'failed' },
      ],
    });
    expect(group.items.map((row) => [row.symbol, itemStatusOf(row)])).toEqual([
      ['AAA', 'matched'],
      ['BBB', 'unmatched'],
    ]);
  });

  it('parses the sample report with all three kinds of rows', () => {
    const report = reportSchema.parse(sample);
    const group = report.groups.find((g) => g.key === 'tw_g1')!;
    expect(group.items.map((row) => itemStatusOf(row))).toEqual(['matched', 'matched', 'matched', 'unmatched', 'unmatched', 'failed']);
    // scan groups stay matched-only
    expect(report.groups.filter((g) => g.kind === 'scan').every((g) => g.items.every((row) => itemStatusOf(row) === 'matched'))).toBe(true);
  });
});

describe('matched / unmatched view', () => {
  const rows = [matched('A', 1), matched('B', 2), unmatched('C', 3), failed('D')];

  it('counts matched and not-matched rows', () => {
    expect(statusCounts(rows)).toEqual({ matched: 2, unmatched: 2 });
    expect(statusCounts([])).toEqual({ matched: 0, unmatched: 0 });
  });

  it('selects rows for each view and keeps the report order', () => {
    expect(itemsForView(rows, 'all').map((row) => row.symbol)).toEqual(['A', 'B', 'C', 'D']);
    expect(itemsForView(rows, 'matched').map((row) => row.symbol)).toEqual(['A', 'B']);
    expect(itemsForView(rows, 'unmatched').map((row) => row.symbol)).toEqual(['C', 'D']);
  });

  it('words the result count', () => {
    expect(resultCountText(rows, 4, 'all', true)).toBe('2 檔成功＋2 檔未成功');
    expect(resultCountText(rows.slice(0, 3), 4, 'all', true)).toBe('2 檔成功＋1 檔未成功');
    expect(resultCountText(rows.slice(0, 2), 2, 'matched', true)).toBe('2 檔');
    expect(resultCountText(rows.slice(0, 1), 2, 'matched', true)).toBe('1 / 2 檔');
    expect(resultCountText(rows.slice(0, 2), 2, 'all', false)).toBe('2 檔');
  });
});

describe('sorting with unmatched rows', () => {
  // Report order: matched, unmatched, failed.
  const rows = [matched('B', -2), matched('A', 1), unmatched('U', 5), failed('F1'), failed('F0')];
  const order = (sort: Parameters<typeof filterAndSortItems>[2], list: readonly GroupItem[] = rows) =>
    filterAndSortItems(list, '', sort).map((row) => row.symbol);

  it('keeps the report order by default', () => {
    expect(order('default')).toEqual(['B', 'A', 'U', 'F1', 'F0']);
  });

  it('puts failed rows (no numbers) at the end in every sort, even from the middle of the list', () => {
    const shuffled = [failed('F1'), matched('A', 1), failed('F0'), unmatched('U', 5), matched('B', -2)];
    expect(order('gain', shuffled)).toEqual(['U', 'A', 'B', 'F1', 'F0']);
    expect(order('loss', shuffled)).toEqual(['B', 'A', 'U', 'F1', 'F0']);
    expect(order('ma', shuffled)).toEqual(['U', 'A', 'B', 'F1', 'F0']);
    expect(order('symbol', shuffled)).toEqual(['A', 'B', 'U', 'F0', 'F1']);
    expect(order('r21', shuffled).slice(-2)).toEqual(['F1', 'F0']);
  });

  it('never mutates its input', () => {
    const copy = [...rows];
    order('symbol');
    expect(rows).toEqual(copy);
  });

  it('applies the query to every status', () => {
    expect(filterAndSortItems(rows, '下載失敗', 'default').map((row) => row.symbol)).toEqual(['F1', 'F0']);
    expect(filterAndSortItems(rows, '未符合', 'default').map((row) => row.symbol)).toEqual(['U']);
  });
});

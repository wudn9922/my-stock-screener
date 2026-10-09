import type { GroupItem } from '../report/schema';
import { maDistance, maValue } from '../ui/format';

export type SortMode = 'default' | 'gain' | 'loss' | 'symbol' | 'ma';
export const SORT_LABELS: Record<SortMode, string> = {
  default: '報告順序',
  gain: '漲幅高→低',
  loss: '跌幅高→低',
  symbol: '代號',
  ma: '均線乖離（大→小）',
};
/** Filters (ticker / name / note) and sorts a group's rows. Pure; exported for tests. */
export function filterAndSortItems(items: readonly GroupItem[], query: string, sort: SortMode): GroupItem[] {
  const q = query.trim().toLowerCase();
  const filtered = q
    ? items.filter(
        (item) =>
          item.symbol.toLowerCase().includes(q) ||
          item.name.toLowerCase().includes(q) ||
          (item.note ?? '').toLowerCase().includes(q),
      )
    : [...items];
  const firstMa = (item: GroupItem) =>
    item.maList.length ? maDistance(item.close, maValue(item.maValues, item.maList[0]!)) : null;
  const byNumber = (value: (item: GroupItem) => number | null, direction: 1 | -1) => (a: GroupItem, b: GroupItem) => {
    const x = value(a),
      y = value(b);
    if (x === null && y === null) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    return direction * (x - y);
  };
  if (sort === 'gain') filtered.sort(byNumber((item) => item.changePct, -1));
  else if (sort === 'loss') filtered.sort(byNumber((item) => item.changePct, 1));
  else if (sort === 'ma') filtered.sort(byNumber(firstMa, -1));
  else if (sort === 'symbol') filtered.sort((a, b) => a.symbol.localeCompare(b.symbol));
  return filtered;
}


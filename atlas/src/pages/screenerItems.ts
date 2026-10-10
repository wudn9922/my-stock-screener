import type { GroupItem } from '../report/schema';
import type { GrowthFile } from '../fundamentals/GrowthProvider';
import { formatPercent, maDistance, maValue, toneClass } from '../ui/format';
import { growthRecordFor, maDeviation, type FilterState } from './screenerFilters';

export type SortMode = 'default' | 'gain' | 'loss' | 'symbol' | 'ma' | 'rs' | 'hi' | 'vr' | 'ma50' | 'r21' | 'eps';
export const SORT_LABELS: Record<SortMode, string> = {
  default: '報告順序',
  gain: '漲幅高→低',
  loss: '跌幅高→低',
  symbol: '代號',
  ma: '均線乖離（大→小）',
  rs: 'RS 百分位（強→弱）',
  hi: '距52週高（近→遠）',
  vr: '量比（大→小）',
  ma50: 'MA50 乖離（大→小）',
  r21: '1月報酬（高→低）',
  eps: 'EPS YoY（高→低）',
};
/** Sorts that need the screener metrics (`metrics`) or, for `eps`, the US growth file. */
export const METRIC_SORTS: readonly SortMode[] = ['rs', 'hi', 'vr', 'ma50', 'r21'];

export interface SortContext {
  growth?: GrowthFile | null;
}

/** Latest-quarter EPS YoY (a loss-to-profit turn sorts above every finite value). */
function epsYoY(item: GroupItem, growth: GrowthFile | null | undefined): number | null {
  const record = growthRecordFor(item, growth);
  if (!record) return null;
  return record.epsYoY[0] ?? (record.epsTurn ? Number.MAX_VALUE : null);
}

/** Filters (ticker / name / note) and sorts a group's rows. Pure; exported for tests. */
export function filterAndSortItems(
  items: readonly GroupItem[],
  query: string,
  sort: SortMode,
  context: SortContext = {},
): GroupItem[] {
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
  const byNumber = (value: (item: GroupItem) => number | null | undefined, direction: 1 | -1) => (a: GroupItem, b: GroupItem) => {
    const x = value(a) ?? null,
      y = value(b) ?? null;
    if (x === null && y === null) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    return direction * (x - y);
  };
  if (sort === 'gain') filtered.sort(byNumber((item) => item.changePct, -1));
  else if (sort === 'loss') filtered.sort(byNumber((item) => item.changePct, 1));
  else if (sort === 'ma') filtered.sort(byNumber(firstMa, -1));
  else if (sort === 'symbol') filtered.sort((a, b) => a.symbol.localeCompare(b.symbol));
  else if (sort === 'rs') {
    const rank = byNumber((item) => item.metrics?.rsRank, -1),
      rs63 = byNumber((item) => item.metrics?.rs63, -1);
    filtered.sort((a, b) => rank(a, b) || rs63(a, b));
  } else if (sort === 'hi') filtered.sort(byNumber((item) => item.metrics?.fromHi52, -1));
  else if (sort === 'vr') filtered.sort(byNumber((item) => item.metrics?.volRatio, -1));
  else if (sort === 'ma50') filtered.sort(byNumber((item) => maDeviation(item, 50), -1));
  else if (sort === 'r21') filtered.sort(byNumber((item) => item.metrics?.r21, -1));
  else if (sort === 'eps') filtered.sort(byNumber((item) => epsYoY(item, context.growth), -1));
  return filtered;
}

// ---------------------------------------------------------------- row pills

export type MetricPill = 'rs' | 'hi' | 'vr' | 'ma50' | 'r21' | 'eps' | 'rev';
const MAX_PILLS = 2;

/** Extra row pills: the sort's metric first, then the filtered ones (at most two; MA pills follow). */
export function rowMetricPills(sort: SortMode, state: FilterState, maList: readonly number[]): MetricPill[] {
  const keys: MetricPill[] = [];
  const add = (key: MetricPill) => {
    if (!keys.includes(key) && !(key === 'ma50' && maList.slice(0, 3).includes(50))) keys.push(key);
  };
  if (sort === 'rs' || sort === 'hi' || sort === 'vr' || sort === 'ma50' || sort === 'r21' || sort === 'eps') add(sort);
  if (state.rs !== null || state.rs3 !== null) add('rs');
  if (state.hi !== null) add('hi');
  if (state.vr !== null) add('vr');
  if (state.eps) add('eps');
  if (state.rev) add('rev');
  if (state.ma['50']) add('ma50');
  return keys.slice(0, MAX_PILLS);
}

export interface PillContent {
  text: string;
  title: string;
  tone: 'up' | 'down' | 'flat';
}

/** Text of one metric pill for a row; null when the row has no value for it. */
export function metricPill(item: GroupItem, key: MetricPill, growth?: GrowthFile | null): PillContent | null {
  const m = item.metrics;
  switch (key) {
    case 'rs': {
      if (m?.rsRank == null && m?.rs63 == null) return null;
      const bench = m.rsBench === '^TWII' ? '加權指數' : (m.rsBench ?? '大盤');
      return {
        text: m.rsRank !== null ? `RS ${m.rsRank}` : `RS3M ${formatPercent(m.rs63, 0)}`,
        title: `相對強弱百分位 ${m.rsRank ?? '—'}（1–99）· 3個月相對${bench} ${formatPercent(m.rs63, 1)}`,
        tone: m.rs63 === null ? 'flat' : toneClass(m.rs63),
      };
    }
    case 'hi': {
      if (m?.fromHi52 == null) return null;
      const window = m.hiBars && m.hiBars < 240 ? `近${m.hiBars}日高點` : '52週高點';
      return { text: `距高 ${formatPercent(m.fromHi52, 1)}`, title: `收盤距${window} ${formatPercent(m.fromHi52, 2)}`, tone: 'flat' };
    }
    case 'vr':
      if (m?.volRatio == null) return null;
      return {
        text: `量比 ${m.volRatio.toFixed(m.volRatio >= 10 ? 0 : 1)}x`,
        title: `今日成交量 ÷ 前20日均量 = ${m.volRatio.toFixed(2)}`,
        tone: 'flat',
      };
    case 'ma50': {
      const distance = maDeviation(item, 50);
      if (distance === null) return null;
      return { text: `MA50 ${formatPercent(distance, 1)}`, title: '收盤相對 MA50 的乖離', tone: toneClass(distance) };
    }
    case 'r21':
      if (m?.r21 == null) return null;
      return { text: `1月 ${formatPercent(m.r21, 1)}`, title: '近 21 個交易日報酬', tone: toneClass(m.r21) };
    case 'eps':
    case 'rev': {
      const record = growthRecordFor(item, growth);
      const values = key === 'eps' ? record?.epsYoY : record?.revYoY;
      const latest = values?.[0] ?? null;
      const name = key === 'eps' ? 'EPS' : '營收';
      if (key === 'eps' && latest === null && record?.epsTurn)
        return { text: 'EPS 轉盈', title: '最新一季由虧轉盈', tone: 'up' };
      if (latest === null) return null;
      const history = values!.map((value) => (value === null ? '—' : formatPercent(value, 0))).join(' / ');
      return {
        text: `${name} ${formatPercent(latest, 0)}`,
        title: `${name} YoY（最新→較舊）：${history}${record?.latest ? ` · 最新季 ${record.latest}` : ''}`,
        tone: toneClass(latest),
      };
    }
  }
}

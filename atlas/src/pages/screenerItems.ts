import { itemStatusOf, type GroupItem } from '../report/schema';
import type { GrowthFile, GrowthOrigin, GrowthRecord } from '../fundamentals/GrowthProvider';
import { formatPercent, maDistance, maValue, toneClass } from '../ui/format';
import { growthRecordFor, growthStreak, maDeviation, type FilterState } from './screenerFilters';

export type SortMode = 'default' | 'gain' | 'loss' | 'symbol' | 'ma' | 'rs' | 'hi' | 'vr' | 'ma50' | 'r21' | 'eps';
export const SORT_LABELS: Record<SortMode, string> = {
  default: '報告順序',
  gain: '漲幅高→低',
  loss: '跌幅高→低',
  symbol: '代號',
  ma: '均線乖離（大→小）',
  rs: 'RS 百分位（強→弱）',
  hi: '距52週高（近→遠）',
  vr: '量比（今日÷昨日，大→小）',
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
  else if (sort === 'vr') filtered.sort(byNumber((item) => item.metrics?.volVsPrev, -1));
  else if (sort === 'ma50') filtered.sort(byNumber((item) => maDeviation(item, 50), -1));
  else if (sort === 'r21') filtered.sort(byNumber((item) => item.metrics?.r21, -1));
  else if (sort === 'eps') filtered.sort(byNumber((item) => epsYoY(item, context.growth), -1));
  // Failed rows have no prices or metrics: they always close the list, whatever the sort.
  return filtered.some((item) => item.status === 'failed')
    ? [...filtered.filter((item) => item.status !== 'failed'), ...filtered.filter((item) => item.status === 'failed')]
    : filtered;
}

// ---------------------------------------------------------------- matched / unmatched view

/** Which rows of a fixed or custom group are listed: all, only the matched ("成功") or only the rest. */
export type StatusView = 'all' | 'matched' | 'unmatched';
export const STATUS_VIEWS: readonly StatusView[] = ['all', 'matched', 'unmatched'];
export const STATUS_VIEW_LABELS: Record<StatusView, string> = { all: '全部', matched: '成功', unmatched: '未成功' };
export const STATUS_BADGE_LABELS = { unmatched: '未符合', failed: '失敗' } as const;

/** Matched rows ("成功股票") and unmatched + failed rows ("未成功股票") of a group. */
export function statusCounts(items: readonly GroupItem[]): { matched: number; unmatched: number } {
  let unmatched = 0;
  for (const item of items) if (itemStatusOf(item) !== 'matched') unmatched += 1;
  return { matched: items.length - unmatched, unmatched };
}

/** The rows of a group for the chosen view; the report's order (matched, unmatched, failed) is kept. */
export function itemsForView(items: readonly GroupItem[], view: StatusView): readonly GroupItem[] {
  if (view === 'all') return items;
  return items.filter((item) => (itemStatusOf(item) === 'matched') === (view === 'matched'));
}

/** `result-count` text: the "7 檔成功＋2 檔未成功" form for the all-rows view of a group that has unmatched rows. */
export function resultCountText(shown: readonly GroupItem[], total: number, view: StatusView, hasUnmatched: boolean): string {
  if (view === 'all' && hasUnmatched) {
    const { matched, unmatched } = statusCounts(shown);
    return `${matched} 檔成功＋${unmatched} 檔未成功`;
  }
  return shown.length === total ? `${shown.length} 檔` : `${shown.length} / ${total} 檔`;
}

// ---------------------------------------------------------------- row pills

export type MetricPill = 'rs' | 'hi' | 'vr' | 'ma50' | 'ema20' | 'r21' | 'eps' | 'rev' | 'epsY' | 'revY' | 'epsS' | 'revS';
const MAX_PILLS = 2;
/** Fundamental pills (EPS / revenue growth) have their own allowance so a growth filter always shows its numbers. */
const MAX_GROWTH_PILLS = 4;
const GROWTH_PILLS: readonly MetricPill[] = ['eps', 'rev', 'epsY', 'revY', 'epsS', 'revS'];

/**
 * Extra row pills: the sort's metric first, then the filtered ones (at most two technical ones plus the
 * growth ones of the active growth filters; MA pills follow).
 */
export function rowMetricPills(sort: SortMode, state: FilterState, maList: readonly number[]): MetricPill[] {
  const keys: MetricPill[] = [];
  const add = (key: MetricPill) => {
    if (!keys.includes(key) && !(key === 'ma50' && maList.slice(0, 3).includes(50))) keys.push(key);
  };
  if (sort === 'rs' || sort === 'hi' || sort === 'vr' || sort === 'ma50' || sort === 'r21' || sort === 'eps') add(sort);
  if (state.rs !== null || state.rs3 !== null || state.rs1 !== null) add('rs');
  if (state.hi !== null) add('hi');
  if (state.vr !== null) add('vr');
  if (state.eps || state.accel) add('eps');
  if (state.epsY) add('epsY');
  if (state.epsStreak !== null) add('epsS');
  if (state.rev) add('rev');
  if (state.revY) add('revY');
  if (state.revStreak !== null) add('revS');
  if (state.ema20) add('ema20');
  const technical = keys.filter((key) => !GROWTH_PILLS.includes(key)).slice(0, MAX_PILLS);
  const growth = keys.filter((key) => GROWTH_PILLS.includes(key)).slice(0, MAX_GROWTH_PILLS);
  return keys.filter((key) => technical.includes(key) || growth.includes(key));
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
        title: `相對強弱百分位 ${m.rsRank ?? '—'}（1–99）· 相對${bench}：1個月 ${formatPercent(m.rs21, 1)}、3個月 ${formatPercent(m.rs63, 1)}`,
        tone: m.rs63 === null ? 'flat' : toneClass(m.rs63),
      };
    }
    case 'hi': {
      if (m?.fromHi52 == null) return null;
      const window = m.hiBars && m.hiBars < 240 ? `近${m.hiBars}日高點` : '52週高點';
      return { text: `距高 ${formatPercent(m.fromHi52, 1)}`, title: `收盤距${window} ${formatPercent(m.fromHi52, 2)}`, tone: 'flat' };
    }
    case 'vr':
      if (m?.volVsPrev == null) return null;
      return {
        text: `量比 ${m.volVsPrev.toFixed(m.volVsPrev >= 10 ? 0 : 1)}`,
        title: `今日成交量 ÷ 前一交易日成交量 = ${m.volVsPrev.toFixed(2)}`,
        tone: 'flat',
      };
    case 'ema20':
      if (m?.ema20 == null) return null;
      return { text: `EMA20 ${formatPercent(m.ema20, 1)}`, title: '收盤相對 EMA20 的乖離', tone: toneClass(m.ema20) };
    case 'ma50': {
      const distance = maDeviation(item, 50);
      if (distance === null) return null;
      return { text: `MA50 ${formatPercent(distance, 1)}`, title: '收盤相對 MA50 的乖離', tone: toneClass(distance) };
    }
    case 'r21':
      if (m?.r21 == null) return null;
      return { text: `1月 ${formatPercent(m.r21, 1)}`, title: '近 21 個交易日報酬', tone: toneClass(m.r21) };
    case 'eps':
    case 'rev':
    case 'epsY':
    case 'revY': {
      const record = growthRecordFor(item, growth);
      if (!record) return null;
      const annual = key === 'epsY' || key === 'revY';
      const isEps = key === 'eps' || key === 'epsY';
      const values = annual ? (isEps ? record.epsYoYAnnual : record.revYoYAnnual) : isEps ? record.epsYoY : record.revYoY;
      const turns = isEps ? (annual ? record.epsTurnAnnual : record.epsTurnQ) : [];
      const latest = values[0] ?? null;
      const name = { eps: 'EPS', rev: '營收', epsY: 'EPS年', revY: '營收年' }[key];
      const detail = growthDetail(record, isEps);
      if (latest === null && turns[0])
        return { text: `${name} 轉盈`, title: `${annual ? '最新一年' : '最新一季'}由虧轉盈（去年同期為虧損，YoY 無法計算）。${detail}`, tone: 'up' };
      if (latest === null) return null;
      return { text: `${name} ${formatPercent(latest, 0)}`, title: `${isEps ? 'EPS' : '營收'} YoY ${detail}`, tone: toneClass(latest) };
    }
    case 'epsS':
    case 'revS': {
      const record = growthRecordFor(item, growth);
      if (!record) return null;
      const isEps = key === 'epsS';
      const values = isEps ? record.epsYoYAnnual : record.revYoYAnnual;
      if (!values.length && !(isEps && record.epsTurnAnnual.some(Boolean))) return null;
      const years = growthStreak(values, isEps ? record.epsTurnAnnual : []);
      return {
        text: `${isEps ? 'EPS' : '營收'}連增 ${years}年`,
        title: `${isEps ? 'EPS' : '營收'}連續成長 ${years} 年（從最新年度往回，年增率 > 0${isEps ? '，虧轉盈也算成長' : ''}；遇到衰退、虧損或缺資料即停止）。${growthDetail(record, isEps)}`,
        tone: years > 0 ? 'up' : 'flat',
      };
    }
  }
}

/** Plain names of the growth sources (growth.json `src` / `srcY`, the us-growth file). */
export const GROWTH_ORIGIN_LABELS: Record<GrowthOrigin, string> = {
  sec: 'SEC 財報（companyfacts）',
  yahoo: 'Yahoo 財報',
  mops: '公開資訊觀測站（官方財報）',
  frames: 'SEC XBRL frames',
  'us-growth': 'Yahoo Finance 季資料',
};
const originText = (origin: GrowthOrigin | null) => (origin ? GROWTH_ORIGIN_LABELS[origin] : '—');
/** `+30% / +20% / — / —`: newest first; a dash is a period without a number, 轉盈 a loss-to-profit turn. */
const historyText = (values: readonly (number | null)[], turns: readonly boolean[]) =>
  values.length || turns.some(Boolean)
    ? Array.from({ length: Math.max(values.length, turns.length) }, (_, slot) =>
        values[slot] != null ? formatPercent(values[slot]!, 0) : turns[slot] ? '轉盈' : '—',
      ).join(' / ')
    : '—';

/**
 * Tooltip text with both quarterly and annual YoY (newest → older), the latest period ends, the source of each
 * block and whether a quarterly slot compares a derived Q4 (full year − first three quarters).
 */
function growthDetail(record: GrowthRecord, eps: boolean): string {
  const quarterlyValues = eps ? record.epsYoY : record.revYoY;
  const quarterly = historyText(quarterlyValues, eps ? record.epsTurnQ : []);
  // Annual: up to 10 slots, so only as many as the stock has (the turnaround mask always has 10 bits)
  const annualTurns = eps ? record.epsTurnAnnual.slice(0, record.epsTurnAnnual.lastIndexOf(true) + 1) : [];
  const annual = historyText(eps ? record.epsYoYAnnual : record.revYoYAnnual, annualTurns);
  const origin = eps ? record.originQ.eps : record.originQ.rev;
  // derivedQ describes growth.json's quarters; the Yahoo quarterly file has no derived Q4 flags
  const derived =
    origin !== 'us-growth' &&
    record.derivedQ.some((flag, slot) => flag && (quarterlyValues[slot] != null || (eps && record.epsTurnQ[slot])));
  const annualValues = eps ? record.epsYoYAnnual : record.revYoYAnnual;
  const streak = growthStreak(annualValues, eps ? record.epsTurnAnnual : []);
  return [
    `季度（最新→較舊）${quarterly}${record.latest ? `，最新季 ${record.latest}` : ''}${derived ? '（含以全年減前三季推算的第 4 季）' : ''}`,
    `年度（最新→較舊）${annual}${record.latestAnnual ? `，最新年度 ${record.latestAnnual}` : ''}，連續成長 ${streak} 年`,
    `來源：季度 ${originText(origin)}、年度 ${originText(record.originY)}`,
  ].join('；');
}

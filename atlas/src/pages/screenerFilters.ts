import type { GroupItem, MetricMa } from '../report/schema';
import { maDistance, maValue } from '../ui/format';
import { growthKey, type GrowthFile, type GrowthRecord } from '../fundamentals/GrowthProvider';

/**
 * Screener filter conditions: state, the compact `f` URL encoding and the pure filter itself.
 *
 * Encoding (`;`-separated, canonical order, open range ends left empty, `~` also accepted):
 * `chg:0:3;ma50:-5:5;tr:a20,a50,stack;hi:10;rs:80;rs3:0;vr:1.5;eps:2:20;rev:1:10;nd:keep`
 * - chg / ma20 / ma50 / ma200: inclusive ranges in % (today's change, close vs SMA distance)
 * - tr: trend flags (above MA20/50/200, MA20 rising, bullish stack 0 < d20 < d50 < d200, no downtrend)
 * - hi: within N% of the 52-week high; rs: RS percentile ≥ N; rs3: 3-month RS vs the benchmark > N%
 * - vr: volume ratio ≥ N; eps / rev: the latest Q quarters all have YoY ≥ N% (US only)
 * - nd:keep keeps stocks whose fundamentals are missing (the default excludes them)
 * Unknown or malformed tokens are ignored, so old or hand-edited links never break the page.
 */
export interface FilterRange {
  min: number | null;
  max: number | null;
}
export interface GrowthGate {
  /** Latest N quarters (1–4) must all pass. */
  quarters: number;
  /** Minimum YoY %. */
  min: number;
}
export const TREND_FLAGS = ['a20', 'a50', 'a200', 'slope', 'stack', 'nobear'] as const;
export type TrendFlag = (typeof TREND_FLAGS)[number];
export const TREND_LABELS: Record<TrendFlag, string> = {
  a20: '站上MA20',
  a50: '站上MA50',
  a200: '站上MA200',
  slope: 'MA20上彎',
  stack: '多頭排列',
  nobear: '排除空頭',
};

export interface FilterState {
  chg: FilterRange | null;
  ma: Partial<Record<`${MetricMa}`, FilterRange>>;
  tr: TrendFlag[];
  hi: number | null;
  rs: number | null;
  rs3: number | null;
  vr: number | null;
  eps: GrowthGate | null;
  rev: GrowthGate | null;
  /** Keep stocks with missing fundamentals instead of excluding them. */
  keepMissing: boolean;
}

export const EMPTY_FILTERS: FilterState = Object.freeze({
  chg: null,
  ma: {},
  tr: [],
  hi: null,
  rs: null,
  rs3: null,
  vr: null,
  eps: null,
  rev: null,
  keepMissing: false,
}) as FilterState;

export const FILTER_PARAM_MAX = 300;
export const FILTER_PARAM_PATTERN = /^[A-Za-z0-9:.,;_~-]{1,300}$/;
const MA_KEYS = ['20', '50', '200'] as const;

const number = (text: string | undefined): number | null => {
  if (text === undefined) return null;
  const trimmed = text.trim();
  if (!trimmed || trimmed === '~') return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
};
const rangeOf = (min: number | null, max: number | null): FilterRange | null => {
  if (min === null && max === null) return null;
  return min !== null && max !== null && min > max ? { min: max, max: min } : { min, max };
};
const gateOf = (parts: string[]): GrowthGate | null => {
  const [first, second] = parts;
  const quarters = second === undefined ? 1 : number(first);
  const min = number(second === undefined ? first : second);
  if (quarters === null || min === null || !Number.isInteger(quarters) || quarters < 1 || quarters > 4) return null;
  return { quarters, min };
};

/** Parses an `f` parameter (or stored filters). Never throws; anything unusable is dropped. */
export function parseFilters(value: string | null | undefined): FilterState {
  const state: FilterState = { ...EMPTY_FILTERS, ma: {}, tr: [] };
  if (!value || value.length > FILTER_PARAM_MAX * 2) return state;
  for (const token of value.split(';')) {
    const [rawKey, ...parts] = token.split(':');
    const key = rawKey?.trim().toLowerCase();
    if (!key) continue;
    if (key === 'chg') state.chg = rangeOf(number(parts[0]), number(parts[1]));
    else if (key === 'ma20' || key === 'ma50' || key === 'ma200') {
      const range = rangeOf(number(parts[0]), number(parts[1]));
      const period = key.slice(2) as `${MetricMa}`;
      if (range) state.ma[period] = range;
      else delete state.ma[period];
    } else if (key === 'tr') {
      const flags = new Set((parts[0] ?? '').split(',').map((flag) => flag.trim().toLowerCase()));
      state.tr = TREND_FLAGS.filter((flag) => flags.has(flag));
    } else if (key === 'hi') {
      const hi = number(parts[0]);
      state.hi = hi !== null && hi > 0 && hi <= 100 ? hi : null;
    } else if (key === 'rs') {
      const rs = number(parts[0]);
      state.rs = rs !== null && rs >= 1 && rs <= 99 ? rs : null;
    } else if (key === 'rs3') state.rs3 = number(parts[0]);
    else if (key === 'vr') {
      const vr = number(parts[0]);
      state.vr = vr !== null && vr > 0 ? vr : null;
    } else if (key === 'eps') state.eps = gateOf(parts);
    else if (key === 'rev') state.rev = gateOf(parts);
    else if (key === 'nd') state.keepMissing = parts[0]?.trim().toLowerCase() === 'keep';
  }
  return state;
}

const formatNumber = (value: number) => String(Number(value.toFixed(2)));
const formatRangeParam = (range: FilterRange) =>
  `${range.min === null ? '' : formatNumber(range.min)}:${range.max === null ? '' : formatNumber(range.max)}`;

/** Canonical `f` text; '' when nothing is set. */
export function serializeFilters(state: FilterState): string {
  const tokens: string[] = [];
  if (state.chg) tokens.push(`chg:${formatRangeParam(state.chg)}`);
  for (const period of MA_KEYS) {
    const range = state.ma[period];
    if (range) tokens.push(`ma${period}:${formatRangeParam(range)}`);
  }
  const flags = TREND_FLAGS.filter((flag) => state.tr.includes(flag));
  if (flags.length) tokens.push(`tr:${flags.join(',')}`);
  if (state.hi !== null) tokens.push(`hi:${formatNumber(state.hi)}`);
  if (state.rs !== null) tokens.push(`rs:${formatNumber(state.rs)}`);
  if (state.rs3 !== null) tokens.push(`rs3:${formatNumber(state.rs3)}`);
  if (state.vr !== null) tokens.push(`vr:${formatNumber(state.vr)}`);
  if (state.eps) tokens.push(`eps:${state.eps.quarters}:${formatNumber(state.eps.min)}`);
  if (state.rev) tokens.push(`rev:${state.rev.quarters}:${formatNumber(state.rev.min)}`);
  // `nd` only matters with a fundamental gate.
  if (state.keepMissing && (state.eps || state.rev)) tokens.push('nd:keep');
  return tokens.join(';').slice(0, FILTER_PARAM_MAX);
}

// ---------------------------------------------------------------- values

/** Close vs SMA distance in %: the report metrics first, then the group's own MA values. */
export function maDeviation(item: GroupItem, period: MetricMa): number | null {
  return item.metrics?.ma[`${period}`] ?? maDistance(item.close, maValue(item.maValues, period));
}

export function growthRecordFor(item: GroupItem, growth: GrowthFile | null | undefined): GrowthRecord | null {
  return growth?.items.get(growthKey(item.symbol)) ?? null;
}

/**
 * The latest `quarters` YoY values all ≥ min. Missing quarters fail unless `keepMissing`; a latest
 * quarter that turned from a loss to a profit (`turnLatest`, YoY undefined) counts as growth.
 */
export function growthGatePasses(
  values: readonly (number | null)[] | undefined,
  gate: GrowthGate,
  keepMissing: boolean,
  turnLatest = false,
): boolean {
  if (!values) return keepMissing;
  for (let quarter = 0; quarter < gate.quarters; quarter++) {
    const value = values[quarter] ?? null;
    if (value === null) {
      if (quarter === 0 && turnLatest) continue;
      if (!keepMissing) return false;
    } else if (value < gate.min) return false;
  }
  return true;
}

// ---------------------------------------------------------- availability

export type FilterKey = 'chg' | 'ma20' | 'ma50' | 'ma200' | 'tr' | 'hi' | 'rs' | 'rs3' | 'vr' | 'eps' | 'rev';
export const FILTER_KEYS: readonly FilterKey[] = ['chg', 'ma20', 'ma50', 'ma200', 'tr', 'hi', 'rs', 'rs3', 'vr', 'eps', 'rev'];

export interface FilterContext {
  market: 'TW' | 'US';
  /** Growth file: undefined = not loaded (yet), null = unavailable. */
  growth?: GrowthFile | null;
  /** True while the growth file is being fetched. */
  growthLoading?: boolean;
}

/** Why each filter cannot run on these rows (null = usable). Unusable filters are skipped, not applied. */
export type FilterAvailability = Record<FilterKey, string | null>;

export const NO_METRICS_REASON = '此群組的每日報告尚未包含這項指標（新版報告產生後提供）';
export const TW_FUNDAMENTALS_REASON = '台股基本面篩選尚未提供：官方月營收／季報資料還沒有整合進來，目前只支援美股季度 EPS 與營收。';

export function filterAvailability(items: readonly GroupItem[], context: FilterContext): FilterAvailability {
  const any = (value: (item: GroupItem) => number | null | undefined) => items.some((item) => {
    const v = value(item);
    return v !== null && v !== undefined;
  });
  const reason = (ok: boolean) => (ok ? null : NO_METRICS_REASON);
  let fundamentals: string | null = null;
  if (context.market === 'TW') fundamentals = TW_FUNDAMENTALS_REASON;
  else if (context.growthLoading || context.growth === undefined) fundamentals = '基本面資料載入中…';
  else if (context.growth === null) fundamentals = '美股基本面資料尚未產生或暫時無法取得。';
  else if (!items.some((item) => growthRecordFor(item, context.growth))) fundamentals = '這個群組的股票沒有基本面資料。';
  return {
    chg: items.some((item) => item.changePct !== null) ? null : '此群組沒有漲跌資料',
    ma20: reason(any((item) => maDeviation(item, 20))),
    ma50: reason(any((item) => maDeviation(item, 50))),
    ma200: reason(any((item) => maDeviation(item, 200))),
    tr: reason(items.some((item) => item.metrics)),
    hi: reason(any((item) => item.metrics?.fromHi52)),
    rs: reason(any((item) => item.metrics?.rsRank)),
    rs3: reason(any((item) => item.metrics?.rs63)),
    vr: reason(any((item) => item.metrics?.volRatio)),
    eps: fundamentals,
    rev: fundamentals,
  };
}

/** Trend flags reuse the MA distances, so most work on report rows without metrics too. */
export function trendFlagUnavailable(flag: TrendFlag, availability: FilterAvailability): string | null {
  if (flag === 'a20') return availability.ma20;
  if (flag === 'a50') return availability.ma50;
  if (flag === 'a200') return availability.ma200;
  if (flag === 'stack') return availability.ma20 ?? availability.ma50 ?? availability.ma200;
  return availability.tr;
}

// ---------------------------------------------------------------- filter

const inRange = (value: number | null, range: FilterRange) =>
  value !== null && (range.min === null || value >= range.min) && (range.max === null || value <= range.max);

const TREND_TESTS: Record<TrendFlag, (item: GroupItem) => boolean> = {
  a20: (item) => (maDeviation(item, 20) ?? 0) > 0,
  a50: (item) => (maDeviation(item, 50) ?? 0) > 0,
  a200: (item) => (maDeviation(item, 200) ?? 0) > 0,
  slope: (item) => (item.metrics?.ma20Slope5 ?? 0) > 0,
  stack: (item) => {
    const d20 = maDeviation(item, 20),
      d50 = maDeviation(item, 50),
      d200 = maDeviation(item, 200);
    return d20 !== null && d50 !== null && d200 !== null && 0 < d20 && d20 < d50 && d50 < d200;
  },
  // An exclusion: only a known downtrend is removed.
  nobear: (item) => item.metrics?.trend !== 'down',
};

/**
 * Rows that pass every usable condition (see `filterAvailability`). A stock missing the value a
 * condition needs is excluded; fundamentals follow `keepMissing`.
 */
export function applyFilters(items: readonly GroupItem[], state: FilterState, context: FilterContext): GroupItem[] {
  const available = filterAvailability(items, context);
  const tests: ((item: GroupItem) => boolean)[] = [];
  const chg = state.chg;
  if (chg && !available.chg) tests.push((item) => inRange(item.changePct, chg));
  for (const period of MA_KEYS) {
    const range = state.ma[period];
    if (range && !available[`ma${period}`]) tests.push((item) => inRange(maDeviation(item, Number(period) as MetricMa), range));
  }
  for (const flag of state.tr) if (!trendFlagUnavailable(flag, available)) tests.push(TREND_TESTS[flag]);
  const { hi, rs, rs3, vr, eps, rev, keepMissing } = state;
  if (hi !== null && !available.hi) tests.push((item) => (item.metrics?.fromHi52 ?? -Infinity) >= -hi);
  if (rs !== null && !available.rs) tests.push((item) => (item.metrics?.rsRank ?? -Infinity) >= rs);
  if (rs3 !== null && !available.rs3) tests.push((item) => (item.metrics?.rs63 ?? -Infinity) > rs3);
  if (vr !== null && !available.vr) tests.push((item) => (item.metrics?.volRatio ?? -Infinity) >= vr);
  if (eps && !available.eps)
    tests.push((item) => {
      const record = growthRecordFor(item, context.growth);
      return growthGatePasses(record?.epsYoY, eps, keepMissing, record?.epsTurn ?? false);
    });
  if (rev && !available.rev) tests.push((item) => growthGatePasses(growthRecordFor(item, context.growth)?.revYoY, rev, keepMissing));
  return tests.length ? items.filter((item) => tests.every((test) => test(item))) : [...items];
}

// ----------------------------------------------------------------- chips

export type ChipId = Exclude<FilterKey, 'tr'> | `tr:${TrendFlag}`;
export interface FilterChip {
  id: ChipId;
  key: FilterKey;
  label: string;
  /** Why the condition is not applied to the current rows; null when it is. */
  unavailable: string | null;
}

const signed = (value: number) => `${value > 0 ? '+' : ''}${formatNumber(value)}`;
export function formatRange(range: FilterRange, unit = '%'): string {
  if (range.min !== null && range.max !== null) return `${signed(range.min)}～${signed(range.max)}${unit}`;
  if (range.min !== null) return `≥${signed(range.min)}${unit}`;
  return `≤${signed(range.max ?? 0)}${unit}`;
}
export function gateLabel(name: string, gate: GrowthGate): string {
  return gate.quarters === 1 ? `${name} YoY ≥${formatNumber(gate.min)}%` : `${name} YoY 近${gate.quarters}季皆≥${formatNumber(gate.min)}%`;
}

/** One removable chip per condition (each trend flag separately), in the encoding's order. */
export function activeChips(state: FilterState, availability?: FilterAvailability): FilterChip[] {
  const chips: Omit<FilterChip, 'unavailable'>[] = [];
  if (state.chg) chips.push({ id: 'chg', key: 'chg', label: `今日漲跌 ${formatRange(state.chg)}` });
  for (const period of MA_KEYS) {
    const range = state.ma[period];
    if (range) chips.push({ id: `ma${period}`, key: `ma${period}`, label: `MA${period} 乖離 ${formatRange(range)}` });
  }
  for (const flag of TREND_FLAGS)
    if (state.tr.includes(flag)) chips.push({ id: `tr:${flag}`, key: 'tr', label: TREND_LABELS[flag] });
  if (state.hi !== null) chips.push({ id: 'hi', key: 'hi', label: `距52週高 ≤${formatNumber(state.hi)}%` });
  if (state.rs !== null) chips.push({ id: 'rs', key: 'rs', label: `RS 百分位 ≥${formatNumber(state.rs)}` });
  if (state.rs3 !== null)
    chips.push({ id: 'rs3', key: 'rs3', label: state.rs3 === 0 ? '3月強於大盤' : `3月相對強弱 >${signed(state.rs3)}%` });
  if (state.vr !== null) chips.push({ id: 'vr', key: 'vr', label: `量比 ≥${formatNumber(state.vr)}x` });
  if (state.eps) chips.push({ id: 'eps', key: 'eps', label: gateLabel('EPS', state.eps) });
  if (state.rev) chips.push({ id: 'rev', key: 'rev', label: gateLabel('營收', state.rev) });
  return chips.map((chip) => ({
    ...chip,
    unavailable: !availability
      ? null
      : chip.id.startsWith('tr:')
        ? trendFlagUnavailable(chip.id.slice(3) as TrendFlag, availability)
        : availability[chip.key],
  }));
}

export function countFilters(state: FilterState): number {
  return activeChips(state).length;
}

/** The state without one chip's condition. */
export function removeFilter(state: FilterState, id: ChipId): FilterState {
  const next: FilterState = { ...state, ma: { ...state.ma }, tr: [...state.tr] };
  if (id.startsWith('tr:')) next.tr = next.tr.filter((flag) => `tr:${flag}` !== id);
  else if (id === 'ma20' || id === 'ma50' || id === 'ma200') delete next.ma[id.slice(2) as `${MetricMa}`];
  else if (id === 'chg' || id === 'eps' || id === 'rev') next[id] = null;
  else if (id === 'hi' || id === 'rs' || id === 'rs3' || id === 'vr') next[id] = null;
  return next;
}

/** True when the state needs the US growth file. */
export function needsGrowth(state: FilterState): boolean {
  return !!(state.eps || state.rev);
}

// --------------------------------------------------------------- storage

/** Last applied conditions per market (`f` text), used when the URL carries none. */
export const FILTER_STORAGE_KEY = 'atlas-screener-filters-v1';

function storage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

/** Stored `f` text for a market; null when nothing was saved (or storage is unavailable). */
export function readStoredFilters(market: 'TW' | 'US', store: Storage | null = storage()): string | null {
  try {
    const raw = store?.getItem(FILTER_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    const value = parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>)[market] : undefined;
    return typeof value === 'string' && (value === '' || FILTER_PARAM_PATTERN.test(value)) ? value : null;
  } catch {
    return null;
  }
}

export function writeStoredFilters(market: 'TW' | 'US', text: string, store: Storage | null = storage()): void {
  try {
    if (!store) return;
    let record: Record<string, unknown> = {};
    try {
      const parsed: unknown = JSON.parse(store.getItem(FILTER_STORAGE_KEY) ?? '{}');
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) record = parsed as Record<string, unknown>;
    } catch {
      // A corrupt entry is replaced.
    }
    store.setItem(FILTER_STORAGE_KEY, JSON.stringify({ ...record, [market]: text }));
  } catch {
    // Private mode / quota: the URL still carries the conditions.
  }
}

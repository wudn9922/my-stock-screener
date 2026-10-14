import type { GroupItem, MetricMa } from '../report/schema';
import { maDistance, maValue } from '../ui/format';
import { growthKey, type GrowthFile, type GrowthRecord } from '../fundamentals/GrowthProvider';

/**
 * Screener filter conditions: state, the compact `f` URL encoding and the pure filter itself.
 *
 * Encoding (`;`-separated, canonical order, open range ends left empty, `~` also accepted):
 * `chg:0:3;ema20:-5:5;tr:a20,a50,stack;hi:10;rs:80;rs3:0;rs1:0;vr:1.2;eps:2:20;epsy:2:15;rev:1:10;revy:3:0;acc:1;tn:0;nd:keep`
 * - chg / ema20: inclusive ranges in % (today's change, close vs EMA20 distance)
 * - tr: trend flags (above MA20/50/200, MA20 rising, bullish stack 0 < d20 < d50 < d200, no downtrend)
 * - hi: within N% of the 52-week high; rs: RS percentile ≥ N (rank within the market's universe);
 *   rs3 / rs1: 3-month / 1-month relative return vs the market's benchmark index > N%
 * - vr: volume vs the previous session ≥ N (today ÷ yesterday)
 * - eps / rev: the latest Q (1–4) quarters all have YoY ≥ N%; epsy / revy: the latest Y (1–3) fiscal years all do
 * - acc:1: EPS growth accelerates (the latest quarter's YoY is above the previous quarter's)
 * - tn:0: loss-to-profit turnarounds do NOT count as growth (the default counts them, in any period of the window)
 * - nd:keep keeps stocks whose fundamentals are missing (the default excludes them)
 * Unknown or malformed tokens are ignored, so old or hand-edited links never break the page. That includes the
 * retired `ma20` / `ma50` / `ma200` ranges: they are dropped, not migrated. An old `vr:N` keeps its number
 * but now means today ÷ yesterday. Serialization keeps whole tokens within FILTER_PARAM_MAX (a token that would
 * not fit is dropped, never cut in half).
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
export interface YearGate {
  /** Latest N fiscal years (1–3) must all pass. */
  years: number;
  /** Minimum YoY %. */
  min: number;
}
export const MAX_GATE_QUARTERS = 4;
export const MAX_GATE_YEARS = 3;
/** Growth thresholds offered by the filter sheet (a link may carry any other number). */
export const GROWTH_PRESETS: readonly number[] = [0, 10, 20, 30, 50];
/** An applied threshold that is not one of the presets (an old or hand-made link): shown as 「目前條件」. */
export const isCustomGrowthMin = (value: number | null | undefined): value is number =>
  value !== null && value !== undefined && !GROWTH_PRESETS.includes(value);

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
  ema20: FilterRange | null;
  tr: TrendFlag[];
  hi: number | null;
  rs: number | null;
  rs3: number | null;
  /** 1-month relative return vs the benchmark index > N%. */
  rs1: number | null;
  vr: number | null;
  eps: GrowthGate | null;
  rev: GrowthGate | null;
  /** Annual EPS / revenue YoY. */
  epsY: YearGate | null;
  revY: YearGate | null;
  /** EPS growth accelerates: the latest quarter's YoY is above the previous quarter's. */
  accel: boolean;
  /** Loss-to-profit turnarounds count as growth (in any period of the window). */
  turn: boolean;
  /** Keep stocks with missing fundamentals instead of excluding them. */
  keepMissing: boolean;
}

export const EMPTY_FILTERS: FilterState = Object.freeze({
  chg: null,
  ema20: null,
  tr: [],
  hi: null,
  rs: null,
  rs3: null,
  rs1: null,
  vr: null,
  eps: null,
  rev: null,
  epsY: null,
  revY: null,
  accel: false,
  turn: true,
  keepMissing: false,
}) as FilterState;

/** Volume-vs-previous-day thresholds offered by the filter sheet (a link may carry any other positive number). */
export const VOLUME_PRESETS: readonly number[] = [1.1, 1.2, 1.5];
/** Tapping the applied preset again clears it. */
export const toggleVolume = (current: number | null, next: number | null): number | null => (next === current ? null : next);
/** An applied threshold that is not one of the presets (an old link): shown as 「目前條件」, no preset lit. */
export const isCustomVolume = (value: number | null): value is number => value !== null && !VOLUME_PRESETS.includes(value);

export const FILTER_PARAM_MAX = 300;
export const FILTER_PARAM_PATTERN = /^[A-Za-z0-9:.,;_~-]{1,300}$/;

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
/** `N:min` (or just `min` = 1 period) → periods within 1..maxPeriods and the minimum YoY %. */
const gateOf = (parts: string[], maxPeriods: number): { periods: number; min: number } | null => {
  const [first, second] = parts;
  const periods = second === undefined ? 1 : number(first);
  const min = number(second === undefined ? first : second);
  if (periods === null || min === null || !Number.isInteger(periods) || periods < 1 || periods > maxPeriods) return null;
  return { periods, min };
};
const quarterGate = (parts: string[]): GrowthGate | null => {
  const gate = gateOf(parts, MAX_GATE_QUARTERS);
  return gate && { quarters: gate.periods, min: gate.min };
};
const yearGate = (parts: string[]): YearGate | null => {
  const gate = gateOf(parts, MAX_GATE_YEARS);
  return gate && { years: gate.periods, min: gate.min };
};

/** Parses an `f` parameter (or stored filters). Never throws; anything unusable is dropped. */
export function parseFilters(value: string | null | undefined): FilterState {
  const state: FilterState = { ...EMPTY_FILTERS, tr: [] };
  if (!value || value.length > FILTER_PARAM_MAX * 2) return state;
  for (const token of value.split(';')) {
    const [rawKey, ...parts] = token.split(':');
    const key = rawKey?.trim().toLowerCase();
    if (!key) continue;
    if (key === 'chg') state.chg = rangeOf(number(parts[0]), number(parts[1]));
    else if (key === 'ema20') state.ema20 = rangeOf(number(parts[0]), number(parts[1]));
    else if (key === 'tr') {
      const flags = new Set((parts[0] ?? '').split(',').map((flag) => flag.trim().toLowerCase()));
      state.tr = TREND_FLAGS.filter((flag) => flags.has(flag));
    } else if (key === 'hi') {
      const hi = number(parts[0]);
      state.hi = hi !== null && hi > 0 && hi <= 100 ? hi : null;
    } else if (key === 'rs') {
      const rs = number(parts[0]);
      state.rs = rs !== null && rs >= 1 && rs <= 99 ? rs : null;
    } else if (key === 'rs3') state.rs3 = number(parts[0]);
    else if (key === 'rs1') state.rs1 = number(parts[0]);
    else if (key === 'vr') {
      const vr = number(parts[0]);
      state.vr = vr !== null && vr > 0 ? vr : null;
    } else if (key === 'eps') state.eps = quarterGate(parts);
    else if (key === 'rev') state.rev = quarterGate(parts);
    else if (key === 'epsy') state.epsY = yearGate(parts);
    else if (key === 'revy') state.revY = yearGate(parts);
    else if (key === 'acc') state.accel = parts[0]?.trim() !== '0';
    else if (key === 'tn') state.turn = parts[0]?.trim() !== '0';
    else if (key === 'nd') state.keepMissing = parts[0]?.trim().toLowerCase() === 'keep';
  }
  return state;
}

const formatNumber = (value: number) => String(Number(value.toFixed(2)));
const formatRangeParam = (range: FilterRange) =>
  `${range.min === null ? '' : formatNumber(range.min)}:${range.max === null ? '' : formatNumber(range.max)}`;

/** Canonical `f` text; '' when nothing is set. Whole tokens only: one that would not fit is dropped. */
export function serializeFilters(state: FilterState): string {
  const tokens: string[] = [];
  if (state.chg) tokens.push(`chg:${formatRangeParam(state.chg)}`);
  if (state.ema20) tokens.push(`ema20:${formatRangeParam(state.ema20)}`);
  const flags = TREND_FLAGS.filter((flag) => state.tr.includes(flag));
  if (flags.length) tokens.push(`tr:${flags.join(',')}`);
  if (state.hi !== null) tokens.push(`hi:${formatNumber(state.hi)}`);
  if (state.rs !== null) tokens.push(`rs:${formatNumber(state.rs)}`);
  if (state.rs3 !== null) tokens.push(`rs3:${formatNumber(state.rs3)}`);
  if (state.rs1 !== null) tokens.push(`rs1:${formatNumber(state.rs1)}`);
  if (state.vr !== null) tokens.push(`vr:${formatNumber(state.vr)}`);
  if (state.eps) tokens.push(`eps:${state.eps.quarters}:${formatNumber(state.eps.min)}`);
  if (state.epsY) tokens.push(`epsy:${state.epsY.years}:${formatNumber(state.epsY.min)}`);
  if (state.rev) tokens.push(`rev:${state.rev.quarters}:${formatNumber(state.rev.min)}`);
  if (state.revY) tokens.push(`revy:${state.revY.years}:${formatNumber(state.revY.min)}`);
  if (state.accel) tokens.push('acc:1');
  // `tn` only matters with an EPS condition, `nd` with any fundamental one.
  if (!state.turn && (state.eps || state.epsY || state.accel)) tokens.push('tn:0');
  if (state.keepMissing && needsGrowth(state)) tokens.push('nd:keep');
  let text = '';
  for (const token of tokens) {
    const next = text ? `${text};${token}` : token;
    if (next.length <= FILTER_PARAM_MAX) text = next;
  }
  return text;
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
 * The latest `n` YoY values all ≥ min. Missing periods fail unless `keepMissing`. A period that turned
 * from a loss to a profit (`turn`: its YoY is undefined) counts as growth: `true` = the latest period
 * only (the old signature), an array = per period (index 0 = latest).
 */
export function growthGatePasses(
  values: readonly (number | null)[] | undefined,
  gate: { min: number } & ({ quarters: number } | { years: number }),
  keepMissing: boolean,
  turn: boolean | readonly boolean[] = false,
): boolean {
  if (!values) return keepMissing;
  const periods = 'years' in gate ? gate.years : gate.quarters;
  const turns = turn === true ? [true] : turn || [];
  for (let slot = 0; slot < periods; slot++) {
    const value = values[slot] ?? null;
    if (value === null) {
      if (turns[slot]) continue;
      if (!keepMissing) return false;
    } else if (value < gate.min) return false;
  }
  return true;
}

/** EPS acceleration: yes / no, or unknown when the two quarters cannot be compared. */
export function epsAccelStatus(record: GrowthRecord | null, includeTurn: boolean): 'yes' | 'no' | 'unknown' {
  if (!record) return 'unknown';
  const latest = record.epsYoY[0] ?? null;
  const previous = record.epsYoY[1] ?? null;
  const latestTurn = includeTurn && (record.epsTurnQ[0] ?? false);
  if (latest !== null && previous !== null) return latest > previous ? 'yes' : 'no';
  // Turned to a profit right after a growth quarter: the sharpest acceleration.
  if (latestTurn && previous !== null) return 'yes';
  // The quarter before was the turnaround (no number to beat) or the latest quarter lost money.
  if (latest !== null && includeTurn && (record.epsTurnQ[1] ?? false)) return 'no';
  return 'unknown';
}

export type FundamentalKind = 'eps' | 'rev' | 'epsY' | 'revY';

/** How many of the rows have every one of the latest `periods` values (a turnaround counts when allowed). */
export function gateCoverage(
  items: readonly GroupItem[],
  growth: GrowthFile | null | undefined,
  kind: FundamentalKind,
  periods: number,
  includeTurn: boolean,
): number {
  let covered = 0;
  for (const item of items) {
    const record = growthRecordFor(item, growth);
    if (!record) continue;
    const values = kind === 'eps' ? record.epsYoY : kind === 'rev' ? record.revYoY : kind === 'epsY' ? record.epsYoYAnnual : record.revYoYAnnual;
    const turns = !includeTurn ? [] : kind === 'eps' ? record.epsTurnQ : kind === 'epsY' ? record.epsTurnAnnual : [];
    if (growthGatePasses(values, { quarters: periods, min: -Infinity }, false, turns)) covered += 1;
  }
  return covered;
}

/** How many of the rows have the two quarters needed to tell whether EPS growth accelerates. */
export function accelCoverage(items: readonly GroupItem[], growth: GrowthFile | null | undefined, includeTurn: boolean): number {
  let covered = 0;
  for (const item of items) if (epsAccelStatus(growthRecordFor(item, growth), includeTurn) !== 'unknown') covered += 1;
  return covered;
}

// ---------------------------------------------------------- availability

export type FilterKey = 'chg' | 'ema20' | 'tr' | 'hi' | 'rs' | 'rs3' | 'rs1' | 'vr' | 'eps' | 'rev' | 'epsY' | 'revY' | 'accel';
export const FILTER_KEYS: readonly FilterKey[] = ['chg', 'ema20', 'tr', 'hi', 'rs', 'rs3', 'rs1', 'vr', 'eps', 'rev', 'epsY', 'revY', 'accel'];
/** SMA distances are no longer a filter, but the trend flags still need them; their availability is tracked too. */
export type TrendMaKey = 'ma20' | 'ma50' | 'ma200';

export interface FilterContext {
  market: 'TW' | 'US';
  /** Growth data of the market (see `loadMarketGrowth`): undefined = not loaded (yet), null = unavailable. */
  growth?: GrowthFile | null;
  /** True while the growth data is being fetched. */
  growthLoading?: boolean;
}

/** Why each filter cannot run on these rows (null = usable). Unusable filters are skipped, not applied. */
export type FilterAvailability = Record<FilterKey | TrendMaKey, string | null>;

export const NO_METRICS_REASON = '此群組的每日報告尚未包含這項指標（新版報告產生後提供）';
export const NO_GROWTH_REASON = '基本面資料（財報成長）尚未產生或暫時無法取得。';
export const NO_GROWTH_ROWS_REASON = '這個群組的股票沒有這項基本面資料。';

export function filterAvailability(items: readonly GroupItem[], context: FilterContext): FilterAvailability {
  const any = (value: (item: GroupItem) => number | null | undefined) => items.some((item) => {
    const v = value(item);
    return v !== null && v !== undefined;
  });
  const reason = (ok: boolean) => (ok ? null : NO_METRICS_REASON);
  // Fundamentals: the data must be loaded and some row must have the specific number.
  const fundamentals = (has: (record: GrowthRecord) => boolean): string | null => {
    if (context.growthLoading || context.growth === undefined) return '基本面資料載入中…';
    if (context.growth === null) return NO_GROWTH_REASON;
    return items.some((item) => {
      const record = growthRecordFor(item, context.growth);
      return record !== null && has(record);
    })
      ? null
      : NO_GROWTH_ROWS_REASON;
  };
  const someNumber = (values: readonly (number | null)[], turns: readonly boolean[] = []) =>
    values.some((value) => value !== null) || turns.some(Boolean);
  return {
    chg: items.some((item) => item.changePct !== null) ? null : '此群組沒有漲跌資料',
    ema20: reason(any((item) => item.metrics?.ema20)),
    ma20: reason(any((item) => maDeviation(item, 20))),
    ma50: reason(any((item) => maDeviation(item, 50))),
    ma200: reason(any((item) => maDeviation(item, 200))),
    tr: reason(items.some((item) => item.metrics)),
    hi: reason(any((item) => item.metrics?.fromHi52)),
    rs: reason(any((item) => item.metrics?.rsRank)),
    rs3: reason(any((item) => item.metrics?.rs63)),
    rs1: reason(any((item) => item.metrics?.rs21)),
    vr: reason(any((item) => item.metrics?.volVsPrev)),
    eps: fundamentals((record) => someNumber(record.epsYoY, record.epsTurnQ)),
    rev: fundamentals((record) => someNumber(record.revYoY)),
    epsY: fundamentals((record) => someNumber(record.epsYoYAnnual, record.epsTurnAnnual)),
    revY: fundamentals((record) => someNumber(record.revYoYAnnual)),
    accel: fundamentals((record) => record.epsYoY.length > 1 || record.epsTurnQ[0] === true),
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
  const ema20 = state.ema20;
  if (ema20 && !available.ema20) tests.push((item) => inRange(item.metrics?.ema20 ?? null, ema20));
  for (const flag of state.tr) if (!trendFlagUnavailable(flag, available)) tests.push(TREND_TESTS[flag]);
  const { hi, rs, rs3, rs1, vr, eps, rev, epsY, revY, accel, turn, keepMissing } = state;
  if (hi !== null && !available.hi) tests.push((item) => (item.metrics?.fromHi52 ?? -Infinity) >= -hi);
  if (rs !== null && !available.rs) tests.push((item) => (item.metrics?.rsRank ?? -Infinity) >= rs);
  if (rs3 !== null && !available.rs3) tests.push((item) => (item.metrics?.rs63 ?? -Infinity) > rs3);
  if (rs1 !== null && !available.rs1) tests.push((item) => (item.metrics?.rs21 ?? -Infinity) > rs1);
  if (vr !== null && !available.vr) tests.push((item) => (item.metrics?.volVsPrev ?? -Infinity) >= vr);
  const record = (item: GroupItem) => growthRecordFor(item, context.growth);
  if (eps && !available.eps)
    tests.push((item) => {
      const r = record(item);
      return growthGatePasses(r?.epsYoY, eps, keepMissing, turn ? (r?.epsTurnQ ?? []) : []);
    });
  if (epsY && !available.epsY)
    tests.push((item) => {
      const r = record(item);
      return growthGatePasses(r?.epsYoYAnnual, epsY, keepMissing, turn ? (r?.epsTurnAnnual ?? []) : []);
    });
  if (rev && !available.rev) tests.push((item) => growthGatePasses(record(item)?.revYoY, rev, keepMissing));
  if (revY && !available.revY) tests.push((item) => growthGatePasses(record(item)?.revYoYAnnual, revY, keepMissing));
  if (accel && !available.accel)
    tests.push((item) => {
      const status = epsAccelStatus(record(item), turn);
      return status === 'yes' || (status === 'unknown' && keepMissing);
    });
  return tests.length ? items.filter((item) => tests.every((test) => test(item))) : [...items];
}

// ----------------------------------------------------------------- chips

export type ChipId = Exclude<FilterKey, 'tr'> | `tr:${TrendFlag}` | 'turn';
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
export function yearGateLabel(name: string, gate: YearGate): string {
  return gate.years === 1 ? `${name} 年增 ≥${formatNumber(gate.min)}%` : `${name} 年增 近${gate.years}年皆≥${formatNumber(gate.min)}%`;
}

/** One removable chip per condition (each trend flag separately), in the encoding's order. */
export function activeChips(state: FilterState, availability?: FilterAvailability): FilterChip[] {
  const chips: Omit<FilterChip, 'unavailable'>[] = [];
  if (state.chg) chips.push({ id: 'chg', key: 'chg', label: `今日漲跌 ${formatRange(state.chg)}` });
  if (state.ema20) chips.push({ id: 'ema20', key: 'ema20', label: `EMA20 乖離 ${formatRange(state.ema20)}` });
  for (const flag of TREND_FLAGS)
    if (state.tr.includes(flag)) chips.push({ id: `tr:${flag}`, key: 'tr', label: TREND_LABELS[flag] });
  if (state.hi !== null) chips.push({ id: 'hi', key: 'hi', label: `距52週高 ≤${formatNumber(state.hi)}%` });
  if (state.rs !== null) chips.push({ id: 'rs', key: 'rs', label: `RS 百分位 ≥${formatNumber(state.rs)}` });
  if (state.rs3 !== null)
    chips.push({ id: 'rs3', key: 'rs3', label: state.rs3 === 0 ? '3月強於大盤' : `3月相對強弱 >${signed(state.rs3)}%` });
  if (state.rs1 !== null)
    chips.push({ id: 'rs1', key: 'rs1', label: state.rs1 === 0 ? '1月強於大盤' : `1月相對強弱 >${signed(state.rs1)}%` });
  if (state.vr !== null) chips.push({ id: 'vr', key: 'vr', label: `量比 ≥${formatNumber(state.vr)}` });
  if (state.eps) chips.push({ id: 'eps', key: 'eps', label: gateLabel('EPS', state.eps) });
  if (state.epsY) chips.push({ id: 'epsY', key: 'epsY', label: yearGateLabel('EPS', state.epsY) });
  if (state.rev) chips.push({ id: 'rev', key: 'rev', label: gateLabel('營收', state.rev) });
  if (state.revY) chips.push({ id: 'revY', key: 'revY', label: yearGateLabel('營收', state.revY) });
  if (state.accel) chips.push({ id: 'accel', key: 'accel', label: 'EPS 成長加速' });
  if (!state.turn && (state.eps || state.epsY || state.accel)) chips.push({ id: 'turn', key: 'eps', label: '不含虧轉盈' });
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
  const next: FilterState = { ...state, tr: [...state.tr] };
  if (id.startsWith('tr:')) next.tr = next.tr.filter((flag) => `tr:${flag}` !== id);
  else if (id === 'chg' || id === 'ema20' || id === 'eps' || id === 'rev' || id === 'epsY' || id === 'revY') next[id] = null;
  else if (id === 'hi' || id === 'rs' || id === 'rs3' || id === 'rs1' || id === 'vr') next[id] = null;
  else if (id === 'accel') next.accel = false;
  else if (id === 'turn') next.turn = true;
  return next;
}

/** True when the state needs the growth data. */
export function needsGrowth(state: FilterState): boolean {
  return !!(state.eps || state.rev || state.epsY || state.revY || state.accel);
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

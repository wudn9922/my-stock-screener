import { userStorage } from '../auth/storage';
import type { GroupItem, MetricMa } from '../report/schema';
import { formatMarketVolume, maDistance, maValue } from '../ui/format';
import { growthKey, type GrowthFile, type GrowthRecord } from '../fundamentals/GrowthProvider';
import { screenerPeValue, type ScreenerPeFigures } from '../app/dataSources';

/**
 * Screener filter conditions: state, the compact `f` URL encoding and the pure filter itself.
 *
 * Encoding (`;`-separated, canonical order, open range ends left empty, `~` also accepted):
 * `ema20:-5:5;tr:a20,a50,stack;hi:10;rs:80;rs3:0;rs1:0;vr:1.2;eps:8:15:75;epsy:2:15;epss:3;rev:4:10;revy:3:0;acc:1;tn:0;nd:keep`
 * - ema20: inclusive distance range in % (smallest fixed SMA when configured, otherwise EMA20)
 * - tr: trend flags (above MA20/50/200, MA20 rising, bullish stack 0 < d20 < d50 < d200, no downtrend)
 * - hi: within N% of the 52-week high; rs: RS percentile ≥ N (rank within the market's universe);
 *   rs3 / rs1: 3-month / 1-month relative return vs the market's benchmark index > N%
 * - vr: volume vs the previous session ≥ N (today ÷ yesterday)
 * - vol: 20-session average volume ≥ N (canonical integer shares; Taiwan input/display uses lots)
 * - pe: inclusive nonnegative P/E bounds, `pe:10:20`, `pe::20` or `pe:10:`. TTM first, annual fallback,
 *   matching the displayed figure; losses and missing figures are always excluded.
 * - eps / rev: `N:X[:P]` quarterly YoY gate, epsy / revy: `N:X[:P]` annual one. Over the latest N periods (quarters
 *   1–12, years 1–10) a period is growth when its YoY ≥ X% (a turnaround counts, see tn), otherwise a decline; the
 *   stock passes when growth ÷ periods with data ≥ P% (the share, 1–100, default 100 = every period). Missing
 *   periods are left out of that count. Fewer than ceil(N/2) periods with data is 「資料不足」: kept only with nd:keep.
 *   The old `N:X` and `X` shapes mean P = 100. See `growthGatePasses`.
 * - epss:N: EPS grew for at least N fiscal years in a row, counting back from the latest one (see `epsStreak`)
 * - Repeated eps / epsy / rev / revy tokens add independent conditions, all combined with AND.
 * - acc:1 / accy:1: quarterly / annual EPS growth accelerates (the latest YoY is above the previous one).
 * - tn:0: loss-to-profit turnarounds do NOT count as growth: in an EPS gate they count as a decline, in the streak and
 *   acceleration they are missing (the default counts them as growth, in any period of the window)
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
  /** Latest N quarters (1–12) are judged. */
  quarters: number;
  /** Threshold X: a quarter with YoY ≥ X % is growth, below it a decline. */
  min: number;
  /** 成長佔比 P (1–100, default 100): growth periods ÷ periods with data must be ≥ P %. */
  share: number;
}
export interface YearGate {
  /** Latest N fiscal years (1–10) are judged. */
  years: number;
  /** Threshold X: a year with YoY ≥ X % is growth, below it a decline. */
  min: number;
  /** 成長佔比 P (1–100, default 100). */
  share: number;
}
export type FundamentalKind = 'eps' | 'rev' | 'epsY' | 'revY';
/** Additional instances of a fundamental gate. Primary fields keep existing saved strategies compatible. */
export type ExtraGrowthGate =
  | { kind: 'eps' | 'rev'; gate: GrowthGate }
  | { kind: 'epsY' | 'revY'; gate: YearGate };
export type AccelPeriod = 'quarterly' | 'annual';
/** The newest quarters judged by a quarterly gate (growth.json QUARTER_SLOTS). */
export const MAX_GATE_QUARTERS = 12;
/** Annual YoY values in growth.json (build_growth.py YEAR_SLOTS): the longest annual window. */
export const MAX_GATE_YEARS = 10;
/** Quarter counts offered as quick choices by the filter sheet (any 1–12 can be typed). */
export const QUARTER_PRESETS: readonly number[] = [1, 2, 4, 8, 12];
/** Year counts offered as quick choices by the filter sheet (any 1–10 can be typed). */
export const YEAR_PRESETS: readonly number[] = [1, 2, 3, 5, 10];
/** Longest EPS growth streak that can be counted (the stored annual horizon). */
export const MAX_STREAK_YEARS = MAX_GATE_YEARS;
/** Growth thresholds offered by the filter sheet (a link may carry any other number). */
export const GROWTH_PRESETS: readonly number[] = [0, 10, 20, 30, 50];
/** An applied threshold that is not one of the presets (an old or hand-made link): shown as 「目前條件」. */
export const isCustomGrowthMin = (value: number | null | undefined): value is number =>
  value !== null && value !== undefined && !GROWTH_PRESETS.includes(value);
/** 成長佔比 P: 100 = every judged period must grow (the old rule). The sheet offers the presets; any 1–100 can be typed. */
export const SHARE_DEFAULT = 100;
export const SHARE_PRESETS: readonly number[] = [50, 75, 100];
/** Periods with data a gate needs before it judges a stock: ceil(N ÷ 2). Fewer is 「資料不足」 (see keepMissing). */
export const enoughPeriods = (periods: number): number => Math.ceil(periods / 2);

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
  /** Retired condition; ignored when parsing, serializing and filtering. */
  chg: FilterRange | null;
  ema20: FilterRange | null;
  tr: TrendFlag[];
  hi: number | null;
  rs: number | null;
  rs3: number | null;
  /** 1-month relative return vs the benchmark index > N%. */
  rs1: number | null;
  vr: number | null;
  /** Minimum 20-session average volume in shares; null means no volume threshold. */
  vol: number | null;
  /** Optional for compatibility with existing saved conditions; null/absent means unrestricted. */
  pe?: FilterRange | null;
  eps: GrowthGate | null;
  rev: GrowthGate | null;
  /** Annual EPS / revenue YoY. */
  epsY: YearGate | null;
  revY: YearGate | null;
  /** EPS grew for at least N fiscal years in a row (see `epsStreak`). */
  epss: number | null;
  /** EPS growth accelerates: the latest quarter's YoY is above the previous quarter's. */
  accel: boolean;
  /** Annual acceleration can be applied independently, or together with quarterly acceleration. */
  accelY?: boolean;
  /** Repeated fundamental conditions, all combined with AND. */
  extraGates?: ExtraGrowthGate[];
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
  vol: null,
  eps: null,
  rev: null,
  epsY: null,
  revY: null,
  epss: null,
  accel: false,
  turn: true,
  keepMissing: false,
}) as FilterState;

/** Volume-vs-previous-day thresholds offered by the filter sheet (a link may carry any other positive number). */
export const VOLUME_PRESETS: readonly number[] = [1.1, 1.2, 1.5];
export const SHARE_VOLUME_PRESETS: readonly number[] = [100_000, 500_000, 1_000_000, 5_000_000, 10_000_000];
/** Parse display units without floating-point multiplication (e.g. 1.001 lots = exactly 1,001 shares). */
export function volumeThresholdOf(text: string, market: 'TW' | 'US'): number | null | undefined {
  const input = text.trim();
  if (!input) return null;
  if (!(market === 'TW' ? /^\d+(?:\.\d{1,3})?$/ : /^\d+$/).test(input)) return undefined;
  const [whole, decimal = ''] = input.split('.');
  const shares = Number(market === 'TW' ? `${whole}${decimal.padEnd(3, '0')}` : whole);
  return Number.isSafeInteger(shares) ? shares || null : undefined;
}
export const formatShares = (shares: number): string =>
  shares >= 10_000 && shares % 10_000 === 0 ? `${(shares / 10_000).toLocaleString('en-US')} 萬股` : `${shares.toLocaleString('en-US')} 股`;
/** Tapping the applied preset again clears it. */
export const toggleVolume = (current: number | null, next: number | null): number | null => (next === current ? null : next);
/** An applied threshold that is not one of the presets (an old link): shown as 「目前條件」, no preset lit. */
export const isCustomVolume = (value: number | null): value is number => value !== null && !VOLUME_PRESETS.includes(value);

export const FILTER_PARAM_MAX = 2000;
export const FILTER_PARAM_PATTERN = /^[A-Za-z0-9:.,;_~-]{1,2000}$/;

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
/** Blank bounds are unrestricted; invalid, negative or reversed bounds cannot be applied. */
export function peRangeOf(lower: string, upper: string): FilterRange | null | undefined {
  const parse = (text: string): number | null | undefined => {
    if (!text.trim()) return null;
    if (!/^\d+(?:\.\d{1,2})?$/.test(text.trim())) return undefined;
    const value = Number(text);
    return Number.isFinite(value) && Number.isSafeInteger(Math.round(value * 100)) ? value : undefined;
  };
  const min = parse(lower), max = parse(upper);
  if (min === undefined || max === undefined || (min !== null && max !== null && min > max)) return undefined;
  return min === null && max === null ? null : { min, max };
}
/**
 * `N:X:P`, `N:X` (P = 100) or just `X` (1 period, P = 100) → periods within 1..maxPeriods, threshold X and share P
 * (1–100). Anything else (a wrong count of numbers, a share outside 1–100) is dropped.
 */
const gateOf = (parts: string[], maxPeriods: number): { periods: number; min: number; share: number } | null => {
  if (parts.length > 3) return null;
  const single = parts.length === 1;
  const periods = single ? 1 : number(parts[0]);
  const min = number(single ? parts[0] : parts[1]);
  const share = parts.length === 3 ? number(parts[2]) : SHARE_DEFAULT;
  if (periods === null || min === null || share === null) return null;
  if (!Number.isInteger(periods) || periods < 1 || periods > maxPeriods) return null;
  if (!(share > 0 && share <= SHARE_DEFAULT)) return null;
  return { periods, min, share };
};
const quarterGate = (parts: string[]): GrowthGate | null => {
  const gate = gateOf(parts, MAX_GATE_QUARTERS);
  return gate && { quarters: gate.periods, min: gate.min, share: gate.share };
};
const yearGate = (parts: string[]): YearGate | null => {
  const gate = gateOf(parts, MAX_GATE_YEARS);
  return gate && { years: gate.periods, min: gate.min, share: gate.share };
};
/** `epss:N` → the streak length 1–10 (exactly one number), or null. */
const streakOf = (parts: string[]): number | null => {
  const years = parts.length === 1 ? number(parts[0]) : null;
  return years !== null && Number.isInteger(years) && years >= 1 && years <= MAX_STREAK_YEARS ? years : null;
};

/** Parses an `f` parameter (or stored filters). Never throws; anything unusable is dropped. */
export function parseFilters(value: string | null | undefined): FilterState {
  const state: FilterState = { ...EMPTY_FILTERS, tr: [] };
  if (!value || value.length > FILTER_PARAM_MAX * 2) return state;
  for (const token of value.split(';')) {
    const [rawKey, ...parts] = token.split(':');
    const key = rawKey?.trim().toLowerCase();
    if (!key) continue;
    if (key === 'ema20') state.ema20 = rangeOf(number(parts[0]), number(parts[1]));
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
    } else if (key === 'vol') {
      const vol = parts.length === 1 ? number(parts[0]) : null;
      state.vol = vol !== null && Number.isSafeInteger(vol) && vol > 0 ? vol : null;
    } else if (key === 'pe' && parts.length === 2) {
      const range = peRangeOf(parts[0]!, parts[1]!);
      if (range) state.pe = range;
    } else if (key === 'eps' || key === 'rev' || key === 'epsy' || key === 'revy') {
      const kind = key === 'epsy' ? 'epsY' : key === 'revy' ? 'revY' : key;
      const gate = kind === 'epsY' || kind === 'revY' ? yearGate(parts) : quarterGate(parts);
      if (gate) {
        if (!state[kind]) Object.assign(state, { [kind]: gate });
        else state.extraGates = [...(state.extraGates ?? []), { kind, gate } as ExtraGrowthGate];
      }
    } else if (key === 'epss') state.epss = streakOf(parts);
    else if (key === 'acc') state.accel = parts[0]?.trim() !== '0';
    else if (key === 'accy') state.accelY = parts[0]?.trim() !== '0';
    else if (key === 'tn') state.turn = parts[0]?.trim() !== '0';
    else if (key === 'nd') state.keepMissing = parts[0]?.trim().toLowerCase() === 'keep';
  }
  return state;
}

const formatNumber = (value: number) => String(Number(value.toFixed(2)));
const formatRangeParam = (range: FilterRange) =>
  `${range.min === null ? '' : formatNumber(range.min)}:${range.max === null ? '' : formatNumber(range.max)}`;
/** `eps:N:X`, with `:P` only when the share is not the default (so the old links are canonical). */
const gateParam = (name: string, periods: number, min: number, share: number) =>
  `${name}:${periods}:${formatNumber(min)}${share === SHARE_DEFAULT ? '' : `:${formatNumber(share)}`}`;

/** Canonical `f` text; '' when nothing is set. Whole tokens only: one that would not fit is dropped. */
export function serializeFilters(state: FilterState): string {
  const tokens: string[] = [];
  if (state.ema20) tokens.push(`ema20:${formatRangeParam(state.ema20)}`);
  const flags = TREND_FLAGS.filter((flag) => state.tr.includes(flag));
  if (flags.length) tokens.push(`tr:${flags.join(',')}`);
  if (state.hi !== null) tokens.push(`hi:${formatNumber(state.hi)}`);
  if (state.rs !== null) tokens.push(`rs:${formatNumber(state.rs)}`);
  if (state.rs3 !== null) tokens.push(`rs3:${formatNumber(state.rs3)}`);
  if (state.rs1 !== null) tokens.push(`rs1:${formatNumber(state.rs1)}`);
  if (state.vr !== null) tokens.push(`vr:${formatNumber(state.vr)}`);
  if (state.vol !== null) tokens.push(`vol:${state.vol}`);
  if (state.pe) tokens.push(`pe:${formatRangeParam(state.pe)}`);
  if (state.eps) tokens.push(gateParam('eps', state.eps.quarters, state.eps.min, state.eps.share));
  if (state.epsY) tokens.push(gateParam('epsy', state.epsY.years, state.epsY.min, state.epsY.share));
  if (state.epss !== null) tokens.push(`epss:${state.epss}`);
  if (state.rev) tokens.push(gateParam('rev', state.rev.quarters, state.rev.min, state.rev.share));
  if (state.revY) tokens.push(gateParam('revy', state.revY.years, state.revY.min, state.revY.share));
  for (const { kind, gate } of state.extraGates ?? [])
    tokens.push(gateParam(kind.toLowerCase(), 'years' in gate ? gate.years : gate.quarters, gate.min, gate.share));
  if (state.accel) tokens.push('acc:1');
  if (state.accelY) tokens.push('accy:1');
  // `tn` only matters with an EPS condition, `nd` with any fundamental one.
  if (!state.turn && hasEpsCondition(state)) tokens.push('tn:0');
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

/** Fixed per-stock parameters override EMA20, independently of the report group's MAs. */
export interface FixedScreenerMa {
  period: number;
  distance: number | null;
}
export type FixedScreenerMas = Readonly<Record<string, FixedScreenerMa>>;
export function screenerMa(item: GroupItem, fixed?: FixedScreenerMas): { label: string; distance: number | null } {
  const reference = fixed?.[item.symbol];
  return reference
    ? { label: `MA${reference.period}`, distance: reference.distance }
    : { label: 'EMA20', distance: item.metrics?.ema20 ?? null };
}

export function growthRecordFor(item: GroupItem, growth: GrowthFile | null | undefined): GrowthRecord | null {
  return growth?.items.get(growthKey(item.symbol)) ?? null;
}

/** Per period of a gate: growth, decline, or missing (no number and no counted turnaround). */
export interface GateTally {
  growth: number;
  decline: number;
  missing: number;
  /** Periods with data: growth + decline (the share's denominator). */
  measured: number;
}

/**
 * The latest `periods` values (index 0 = latest) sorted into growth (YoY ≥ min), decline (YoY < min) and missing.
 * `turns` flags the periods that turned from a loss to a profit (their YoY is undefined): with `includeTurn`
 * (含虧轉盈) such a period is growth, without it a decline. A period with no number and no turnaround is missing.
 */
export function gateTally(
  values: readonly (number | null)[] | undefined,
  periods: number,
  min: number,
  turns: readonly boolean[] = [],
  includeTurn = true,
): GateTally {
  let growth = 0;
  let decline = 0;
  let missing = 0;
  for (let slot = 0; slot < periods; slot++) {
    const value = values?.[slot] ?? null;
    if (value === null) {
      if (!turns[slot]) missing += 1;
      else if (includeTurn) growth += 1;
      else decline += 1;
    } else if (value >= min) growth += 1;
    else decline += 1;
  }
  return { growth, decline, missing, measured: growth + decline };
}

/**
 * A gate over the latest `gate.quarters` / `gate.years` periods: growth periods ÷ periods with data ≥ the share P
 * (missing periods are left out of the count). Fewer than ceil(N/2) periods with data is 「資料不足」: the stock is
 * kept only with `keepMissing`. P = 100 with no missing period is the old rule: every period grows.
 */
export function growthGatePasses(
  values: readonly (number | null)[] | undefined,
  gate: { min: number; share?: number } & ({ quarters: number } | { years: number }),
  keepMissing: boolean,
  turns: readonly boolean[] = [],
  includeTurn = true,
): boolean {
  const periods = 'years' in gate ? gate.years : gate.quarters;
  const { growth, measured } = gateTally(values, periods, gate.min, turns, includeTurn);
  if (measured < enoughPeriods(periods)) return keepMissing;
  return growth * 100 >= (gate.share ?? SHARE_DEFAULT) * measured;
}

/** Compare consecutive YoY rates of the chosen period; never substitute quarterly data for annual data. */
export function epsAccelStatus(record: GrowthRecord | null, includeTurn: boolean, period: AccelPeriod = 'quarterly'): 'yes' | 'no' | 'unknown' {
  if (!record) return 'unknown';
  const values = period === 'annual' ? record.epsYoYAnnual : record.epsYoY;
  const turns = period === 'annual' ? record.epsTurnAnnual : record.epsTurnQ;
  const latest = values[0] ?? null;
  const previous = values[1] ?? null;
  const latestTurn = includeTurn && (turns[0] ?? false);
  if (latest !== null && previous !== null) return latest > previous ? 'yes' : 'no';
  // Turned to a profit after a period with a numeric YoY: preserve the existing turnaround rule.
  if (latestTurn && previous !== null) return 'yes';
  // The previous period was a turnaround (no numeric rate to beat).
  if (latest !== null && includeTurn && (turns[1] ?? false)) return 'no';
  return 'unknown';
}

/**
 * EPS growth streak: how many fiscal years in a row, counting back from the latest one (index 0), have an annual
 * EPS YoY above 0; a loss-to-profit turn counts as growth when `includeTurn`. The count stops at the first year that
 * does not grow, and never passes the stored horizon (10 years). `stop` says why: 'decline' = a year with YoY ≤ 0,
 * 'missing' = no number (no year-ago EPS base, a gap in the years, or a turn while turns do not count), null when
 * every stored year grew.
 */
export function epsStreak(
  record: GrowthRecord | null | undefined,
  includeTurn: boolean,
): { years: number; stop: 'decline' | 'missing' | null } {
  const values = record?.epsYoYAnnual ?? [];
  const turns = record?.epsTurnAnnual ?? [];
  for (let slot = 0; slot < MAX_STREAK_YEARS; slot++) {
    const value = values[slot] ?? null;
    if (value === null) {
      if (includeTurn && turns[slot]) continue;
      return { years: slot, stop: 'missing' };
    }
    if (value <= 0) return { years: slot, stop: 'decline' };
  }
  return { years: MAX_STREAK_YEARS, stop: null };
}

/**
 * `epss:N`: the streak is at least N years. A streak that stops at missing data before N years is kept only with
 * `keepMissing` (the same rule as the YoY gates); a year of decline always excludes the stock.
 */
export function streakPasses(record: GrowthRecord | null | undefined, years: number, keepMissing: boolean, includeTurn: boolean): boolean {
  const streak = epsStreak(record, includeTurn);
  return streak.years >= years || (keepMissing && streak.stop === 'missing');
}

/** How many of the rows have enough periods with data (ceil(N/2) of the latest N; a turnaround counts when allowed) for the gate to judge them. */
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
    const turns = kind === 'eps' ? record.epsTurnQ : kind === 'epsY' ? record.epsTurnAnnual : [];
    if (gateTally(values, periods, -Infinity, turns, includeTurn).measured >= enoughPeriods(periods)) covered += 1;
  }
  return covered;
}

/**
 * How many rows have all of the latest `years` annual EPS values (a turnaround when it counts): the data a streak of
 * `years` can be judged on at all. Rows with a gap in those years cannot show such a streak.
 */
export function streakCoverage(items: readonly GroupItem[], growth: GrowthFile | null | undefined, years: number, includeTurn: boolean): number {
  let covered = 0;
  for (const item of items) {
    const record = growthRecordFor(item, growth);
    if (!record) continue;
    const { measured } = gateTally(record.epsYoYAnnual, years, -Infinity, includeTurn ? record.epsTurnAnnual : [], true);
    if (measured === years) covered += 1;
  }
  return covered;
}

/** How many rows have comparable YoY values for the chosen acceleration period. */
export function accelCoverage(items: readonly GroupItem[], growth: GrowthFile | null | undefined, includeTurn: boolean, period: AccelPeriod = 'quarterly'): number {
  let covered = 0;
  for (const item of items) if (epsAccelStatus(growthRecordFor(item, growth), includeTurn, period) !== 'unknown') covered += 1;
  return covered;
}

// ---------------------------------------------------------- availability

export type FilterKey = 'chg' | 'ema20' | 'tr' | 'hi' | 'rs' | 'rs3' | 'rs1' | 'vr' | 'vol' | 'pe' | 'eps' | 'rev' | 'epsY' | 'revY' | 'epss' | 'accel' | 'accelY';
export const FILTER_KEYS: readonly FilterKey[] = ['chg', 'ema20', 'tr', 'hi', 'rs', 'rs3', 'rs1', 'vr', 'vol', 'pe', 'eps', 'rev', 'epsY', 'revY', 'epss', 'accel', 'accelY'];
/** SMA distances are no longer a filter, but the trend flags still need them; their availability is tracked too. */
export type TrendMaKey = 'ma20' | 'ma50' | 'ma200';

export interface FilterContext {
  market: 'TW' | 'US';
  /** Universe's modal observation date; absent for legacy report groups. */
  asOf?: string | null;
  /** Growth data of the market (see `loadMarketGrowth`): undefined = not loaded (yet), null = unavailable. */
  growth?: GrowthFile | null;
  /** True while the growth data is being fetched. */
  growthLoading?: boolean;
  /** Undefined while loading; null when the valuation market file could not be read. */
  pe?: ScreenerPeFigures | null;
  fixedMAs?: FixedScreenerMas;
  /** A loading/error state must not silently replace a saved fixed MA with EMA20. */
  maReferenceReason?: string | null;
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
    ema20: context.maReferenceReason ?? reason(any((item) => screenerMa(item, context.fixedMAs).distance)),
    ma20: reason(any((item) => maDeviation(item, 20))),
    ma50: reason(any((item) => maDeviation(item, 50))),
    ma200: reason(any((item) => maDeviation(item, 200))),
    tr: reason(items.some((item) => item.metrics)),
    hi: reason(any((item) => item.metrics?.fromHi52)),
    rs: reason(any((item) => item.metrics?.rsRank)),
    rs3: reason(any((item) => item.metrics?.rs63)),
    rs1: reason(any((item) => item.metrics?.rs21)),
    vr: reason(any((item) => item.metrics?.volVsPrev)),
    vol: any((item) => item.metrics?.volumeAvg20) ? null : '此群組沒有近 20 日平均成交量資料（需完整 20 個交易日）',
    pe: context.pe === undefined ? '本益比資料載入中…' : context.pe === null ? '本益比資料尚未產生或暫時無法取得' : null,
    eps: fundamentals((record) => someNumber(record.epsYoY, record.epsTurnQ)),
    rev: fundamentals((record) => someNumber(record.revYoY)),
    epsY: fundamentals((record) => someNumber(record.epsYoYAnnual, record.epsTurnAnnual)),
    revY: fundamentals((record) => someNumber(record.revYoYAnnual)),
    epss: fundamentals((record) => someNumber(record.epsYoYAnnual, record.epsTurnAnnual)),
    accel: fundamentals((record) => record.epsYoY.length > 1 || record.epsTurnQ[0] === true),
    accelY: fundamentals((record) => record.epsYoYAnnual.some((value) => value !== null) || record.epsTurnAnnual.some(Boolean)),
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
  const dateSensitive = state.vol !== null || state.vr !== null
    || state.rs !== null || state.rs1 !== null || state.rs3 !== null;
  if (context.asOf && dateSensitive) items = items.filter(item => item.asOf === context.asOf);
  const available = filterAvailability(items, context);
  const tests: ((item: GroupItem) => boolean)[] = [];
  const ema20 = state.ema20;
  if (ema20 && !available.ema20) tests.push((item) => inRange(screenerMa(item, context.fixedMAs).distance, ema20));
  for (const flag of state.tr) if (!trendFlagUnavailable(flag, available)) tests.push(TREND_TESTS[flag]);
  const { hi, rs, rs3, rs1, vr, vol, eps, rev, epsY, revY, epss, accel, turn, keepMissing } = state;
  if (hi !== null && !available.hi) tests.push((item) => (item.metrics?.fromHi52 ?? -Infinity) >= -hi);
  if (rs !== null && !available.rs) tests.push((item) => (item.metrics?.rsRank ?? -Infinity) >= rs);
  if (rs3 !== null && !available.rs3) tests.push((item) => (item.metrics?.rs63 ?? -Infinity) > rs3);
  if (rs1 !== null && !available.rs1) tests.push((item) => (item.metrics?.rs21 ?? -Infinity) > rs1);
  if (vr !== null && !available.vr) tests.push((item) => (item.metrics?.volVsPrev ?? -Infinity) >= vr);
  if (vol !== null && !available.vol) tests.push((item) => (item.metrics?.volumeAvg20 ?? -Infinity) >= vol);
  // An unavailable P/E source must never turn an applied numeric condition into an unfiltered list.
  if (state.pe) tests.push(item => item.status !== 'failed' && inRange(screenerPeValue(context.pe?.[item.symbol]), state.pe!));
  const record = (item: GroupItem) => growthRecordFor(item, context.growth);
  if (eps && !available.eps)
    tests.push((item) => {
      const r = record(item);
      return growthGatePasses(r?.epsYoY, eps, keepMissing, r?.epsTurnQ ?? [], turn);
    });
  if (epsY && !available.epsY)
    tests.push((item) => {
      const r = record(item);
      return growthGatePasses(r?.epsYoYAnnual, epsY, keepMissing, r?.epsTurnAnnual ?? [], turn);
    });
  if (epss !== null && !available.epss) tests.push((item) => streakPasses(record(item), epss, keepMissing, turn));
  if (rev && !available.rev) tests.push((item) => growthGatePasses(record(item)?.revYoY, rev, keepMissing));
  if (revY && !available.revY) tests.push((item) => growthGatePasses(record(item)?.revYoYAnnual, revY, keepMissing));
  if (accel && !available.accel)
    tests.push((item) => {
      const status = epsAccelStatus(record(item), turn);
      return status === 'yes' || (status === 'unknown' && keepMissing);
    });
  if (state.accelY && !available.accelY)
    tests.push((item) => {
      const status = epsAccelStatus(record(item), turn, 'annual');
      return status === 'yes' || (status === 'unknown' && keepMissing);
    });
  for (const { kind, gate } of state.extraGates ?? []) {
    if (available[kind]) continue;
    tests.push((item) => {
      const r = record(item);
      const values = kind === 'eps' ? r?.epsYoY : kind === 'epsY' ? r?.epsYoYAnnual : kind === 'rev' ? r?.revYoY : r?.revYoYAnnual;
      const turns = kind === 'eps' ? r?.epsTurnQ : kind === 'epsY' ? r?.epsTurnAnnual : [];
      return growthGatePasses(values, gate, keepMissing, turns ?? [], turn);
    });
  }
  return tests.length ? items.filter((item) => tests.every((test) => test(item))) : [...items];
}

// ----------------------------------------------------------------- chips

export type ChipId = Exclude<FilterKey, 'tr'> | `tr:${TrendFlag}` | `growth:${number}` | 'turn';
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
export function formatPeRange(range: FilterRange): string {
  if (range.min !== null && range.max !== null) return `${formatNumber(range.min)}～${formatNumber(range.max)} 倍`;
  return range.min !== null ? `≥${formatNumber(range.min)} 倍` : `≤${formatNumber(range.max ?? 0)} 倍`;
}
/** Chip text of a growth gate: `EPS YoY 近8季皆≥15%`, or with a share `EPS YoY 近8季成長≥75%（≥15%）`. */
const gateChipText = (head: string, unit: '季' | '年', periods: number, min: number, share: number): string => {
  const threshold = formatNumber(min);
  if (periods === 1) return `${head} ≥${threshold}%`;
  if (share >= SHARE_DEFAULT) return `${head} 近${periods}${unit}皆≥${threshold}%`;
  return `${head} 近${periods}${unit}成長≥${formatNumber(share)}%（≥${threshold}%）`;
};
export function gateLabel(name: string, gate: GrowthGate): string {
  return gateChipText(`${name} YoY`, '季', gate.quarters, gate.min, gate.share);
}
export function yearGateLabel(name: string, gate: YearGate): string {
  return gateChipText(`${name} 年增`, '年', gate.years, gate.min, gate.share);
}

/**
 * The condition in words, for the filter sheet: 「近 8 季中至少 75% 的季度 EPS 年增 ≥ 15%」, 「近 8 季的 EPS 年增皆 ≥ 15%」
 * (P = 100) or 「最近 1 季的 EPS 年增 ≥ 15%」. `subject` is e.g. 「EPS 年增」.
 */
export function gateSentence(subject: string, unit: '季' | '年', periods: number, min: number, share: number): string {
  const threshold = `${formatNumber(min)}%`;
  if (periods === 1) return `最近 1 ${unit}的 ${subject} ≥ ${threshold}`;
  if (share >= SHARE_DEFAULT) return `近 ${periods} ${unit}的 ${subject}皆 ≥ ${threshold}`;
  return `近 ${periods} ${unit}中至少 ${formatNumber(share)}% 的${unit === '季' ? '季度' : '年度'} ${subject} ≥ ${threshold}`;
}
/**
 * The pass count in words: 「成長 6 次／衰退 2 次」 = with all N periods present, at least ceil(N × P) growth periods
 * and at most the rest declining. With the periods missing the same ratio applies to those with data.
 */
export function gateCountText(periods: number, share: number): string {
  const growth = Math.ceil((periods * share) / SHARE_DEFAULT - 1e-9);
  return `成長 ${growth} 次／衰退 ${periods - growth} 次`;
}
export const streakLabel = (years: number): string => `EPS 連續成長 ≥${years} 年`;

/** One removable chip per condition (each trend flag separately), in the encoding's order. */
export function activeChips(state: FilterState, availability?: FilterAvailability, market: 'TW' | 'US' = 'US', hasFixedMAs = false): FilterChip[] {
  const chips: Omit<FilterChip, 'unavailable'>[] = [];
  if (state.ema20) chips.push({ id: 'ema20', key: 'ema20', label: `${hasFixedMAs ? '固定最小均線／EMA20' : 'EMA20'} 乖離 ${formatRange(state.ema20)}` });
  for (const flag of TREND_FLAGS)
    if (state.tr.includes(flag)) chips.push({ id: `tr:${flag}`, key: 'tr', label: TREND_LABELS[flag] });
  if (state.hi !== null) chips.push({ id: 'hi', key: 'hi', label: `距52週高 ≤${formatNumber(state.hi)}%` });
  if (state.rs !== null) chips.push({ id: 'rs', key: 'rs', label: `RS 百分位 ≥${formatNumber(state.rs)}` });
  if (state.rs3 !== null)
    chips.push({ id: 'rs3', key: 'rs3', label: state.rs3 === 0 ? '3月強於大盤' : `3月相對強弱 >${signed(state.rs3)}%` });
  if (state.rs1 !== null)
    chips.push({ id: 'rs1', key: 'rs1', label: state.rs1 === 0 ? '1月強於大盤' : `1月相對強弱 >${signed(state.rs1)}%` });
  if (state.vr !== null) chips.push({ id: 'vr', key: 'vr', label: `量比 ≥${formatNumber(state.vr)}` });
  if (state.vol !== null) chips.push({ id: 'vol', key: 'vol', label: `20 日均量 ≥${formatMarketVolume(state.vol, market)}` });
  if (state.pe) chips.push({ id: 'pe', key: 'pe', label: `本益比 ${formatPeRange(state.pe)}` });
  if (state.eps) chips.push({ id: 'eps', key: 'eps', label: gateLabel('EPS', state.eps) });
  if (state.epsY) chips.push({ id: 'epsY', key: 'epsY', label: yearGateLabel('EPS', state.epsY) });
  if (state.epss !== null) chips.push({ id: 'epss', key: 'epss', label: streakLabel(state.epss) });
  if (state.rev) chips.push({ id: 'rev', key: 'rev', label: gateLabel('營收', state.rev) });
  if (state.revY) chips.push({ id: 'revY', key: 'revY', label: yearGateLabel('營收', state.revY) });
  (state.extraGates ?? []).forEach(({ kind, gate }, index) => {
    const name = kind.startsWith('eps') ? 'EPS' : '營收';
    chips.push({ id: `growth:${index}`, key: kind, label: 'years' in gate ? yearGateLabel(name, gate) : gateLabel(name, gate) });
  });
  if (state.accel) chips.push({ id: 'accel', key: 'accel', label: '季度 EPS 成長加速' });
  if (state.accelY) chips.push({ id: 'accelY', key: 'accelY', label: '年度 EPS 成長加速' });
  if (!state.turn && hasEpsCondition(state)) chips.push({ id: 'turn', key: 'eps', label: '不含虧轉盈' });
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
  else if (id.startsWith('growth:')) next.extraGates = (state.extraGates ?? []).filter((_, index) => `growth:${index}` !== id);
  else if (id === 'eps' || id === 'rev' || id === 'epsY' || id === 'revY') {
    const index = next.extraGates?.findIndex(({ kind }) => kind === id) ?? -1;
    if (index >= 0) {
      Object.assign(next, { [id]: next.extraGates![index].gate });
      next.extraGates = next.extraGates!.filter((_, i) => i !== index);
    } else next[id] = null;
  } else if (id === 'chg' || id === 'ema20' || id === 'epss' || id === 'pe') next[id] = null;
  else if (id === 'hi' || id === 'rs' || id === 'rs3' || id === 'rs1' || id === 'vr' || id === 'vol') next[id] = null;
  else if (id === 'accel') next.accel = false;
  else if (id === 'accelY') next.accelY = false;
  else if (id === 'turn') next.turn = true;
  return next;
}

/** True when the state needs the growth data. */
export function needsGrowth(state: FilterState): boolean {
  return !!(state.eps || state.rev || state.epsY || state.revY || state.epss !== null || state.accel || state.accelY || state.extraGates?.length);
}

function hasEpsCondition(state: FilterState): boolean {
  return !!(state.eps || state.epsY || state.epss !== null || state.accel || state.accelY || state.extraGates?.some(({ kind }) => kind.startsWith('eps')));
}

// --------------------------------------------------------------- storage

/** Legacy per-market conditions, retained for settings import/export compatibility. */
export const FILTER_STORAGE_KEY = 'atlas-screener-filters-v1';

type FilterStorage = Pick<Storage, 'getItem' | 'setItem'>;
function storage(): FilterStorage {
  return userStorage;
}

/** Stored `f` text for a market; null when nothing was saved (or storage is unavailable). */
export function readStoredFilters(market: 'TW' | 'US', store: FilterStorage | null = storage()): string | null {
  try {
    const raw = store?.getItem(FILTER_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    const value = parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>)[market] : undefined;
    return typeof value === 'string' && (value === '' || FILTER_PARAM_PATTERN.test(value)) ? value : null;
  } catch {
    return null;
  }
}

export function writeStoredFilters(market: 'TW' | 'US', text: string, store: FilterStorage | null = storage()): void {
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

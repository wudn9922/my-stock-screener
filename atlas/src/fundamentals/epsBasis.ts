import type { FinancialHistory, FinancialSeries, PeriodKind, StockDividendEvent } from './FinancialHistory';
import { dividendFactor, eventText } from './stockDividendAdjust';

/**
 * EPS on today's share basis (還原 EPS). Reported EPS stays in the file untouched; this module only works out, per
 * period, the factor that puts it on the current share count, with the events behind it for provenance:
 *
 *  - Taiwan stock dividends (配股): stockDividendAdjust.ts (ex-rights date after the report was due).
 *  - Stock splits / reverse splits from the file's `split` block (ratio ≥ 1.5 or ≤ 1/1.5): applied only where the
 *    share count the EPS implies (net income ÷ EPS) confirms that the period is still on the pre-split basis, the
 *    same test as build_financials.adjust_splits. SEC files the builder already adjusted pass the test with factor 1;
 *    Taiwan FinMind files (par-value changes such as 8422.TW 1 拆 10 on 2025-11-06) are not adjusted by the builder.
 *  - Not handled: capital reductions (減資) and cash capital increases (no event data in the files).
 *
 * Restated EPS = reported EPS ÷ factor. Null adjustment (Taiwan file without a stockDividends block) → no restated
 * value: callers show the reported EPS and never guess.
 */

export type EpsDisplayBasis = 'restated' | 'reported';

export interface SplitEvent {
  date: string;
  ratio: number;
}

export interface EpsAdjustment {
  /** Reported EPS ÷ factor = EPS on today's share basis (6 decimals). */
  factor: number;
  /** Stock dividends applied (Taiwan). */
  dividends: StockDividendEvent[];
  /** Splits applied (confirmed by the implied share count) and their product. */
  splits: SplitEvent[];
  splitFactor: number;
  /**
   * A derived fourth quarter (annual − Q1..Q3) whose EPS sign contradicts its net income next to a split: the annual
   * EPS was restated after the split, Q1..Q3 were not, so the difference is on no share basis at all.
   */
  mixedQ4: boolean;
}

export const MIN_SPLIT_RATIO = 1.5;
/** Implied share counts must agree within ±25% (build_financials SPLIT_MATCH). */
export const SPLIT_MATCH = 0.25;

/** Smallest |EPS| whose implied share count is used as split evidence. */
export const MIN_EVIDENCE_EPS = 0.05;

const DAY_MS = 86_400_000;
const parse = (day: string) => Date.parse(`${day}T00:00:00Z`);
const usableRatio = (ratio: number) => ratio >= MIN_SPLIT_RATIO || ratio <= 1 / MIN_SPLIT_RATIO;

/** Diluted EPS, else basic (the figure the financials page shows). */
export function reportedEpsAt(series: FinancialSeries, index: number): number | null {
  return series.values.epsDiluted[index] ?? series.values.epsBasic[index] ?? null;
}

/**
 * Split events to test, oldest first. A US file the builder already split-adjusted (`split.adjusted`: adjust_splits and
 * repair_split_eps on its share counts) is trusted as is. Taiwan FinMind statements are never split-adjusted by the
 * builder (the flag there is carried over from the earlier Yahoo file), so Taiwan events are always tested; a split on a
 * 配股 ex-rights date is that dividend, not a second event.
 */
export function splitEventsOf(history: FinancialHistory): SplitEvent[] {
  if (history.market !== 'TW' && history.split.adjusted) return [];
  const dividends = history.market === 'TW' ? (history.stockDividends ?? []) : [];
  return history.split.events
    .filter((event) => usableRatio(event.ratio))
    .filter((event) => !dividends.some((dividend) => Math.abs(parse(dividend.exDate) - parse(event.date)) <= 5 * DAY_MS))
    .sort((a, b) => (a.date < b.date ? -1 : 1));
}

/**
 * Split factor per period. Walking from the newest period back, a period's implied share count (net income ÷ EPS)
 * × the product of a suffix of the splits after its end must land within ±SPLIT_MATCH of the reference — the median
 * of the next (up to) three newer periods' adjusted counts, so one odd period (a derived Q4) cannot set the basis —
 * and beat leaving it alone; it must match the next newer period's count as well. The splits must also fall before
 * that newer period's report, unless the period just continues its factor. Periods without an implied count (no net income, loss/profit sign
 * mismatch, |EPS| < MIN_EVIDENCE_EPS) keep factor 1.
 */
export function splitFactors(
  ends: readonly string[],
  eps: readonly (number | null)[],
  netIncome: readonly (number | null)[],
  splits: readonly SplitEvent[],
): { factor: number; splits: SplitEvent[] }[] {
  const out = ends.map(() => ({ factor: 1, splits: [] as SplitEvent[] }));
  if (!splits.length) return out;
  const implied = (index: number): number | null => {
    const value = eps[index] ?? null;
    const income = netIncome[index] ?? null;
    // EPS printed to cents: below 0.05 the rounding alone can move the implied count by more than 10%.
    if (value === null || income === null || Math.abs(value) < MIN_EVIDENCE_EPS || income === 0) return null;
    const shares = income / value;
    return shares > 0 && Number.isFinite(shares) ? shares : null;
  };
  const order = ends
    .map((_, index) => index)
    .filter((index) => implied(index) !== null)
    .sort((a, b) => (ends[a]! < ends[b]! ? 1 : -1));
  const newer: number[] = [];
  let previous: number | null = null;
  for (const index of order) {
    const shares = implied(index)!;
    const reference = newer.length ? median(newer.slice(-3)) : null;
    if (reference !== null && previous !== null) {
      const after = splits.filter((event) => event.date > ends[index]!);
      let best = 1;
      let bestCount = 0;
      let bestDistance = Math.abs(Math.log(shares / reference));
      let product = 1;
      for (let k = after.length - 1; k >= 0; k--) {
        product *= after[k]!.ratio;
        const distance = Math.abs(Math.log((shares * product) / reference));
        if (distance < bestDistance) {
          best = product;
          bestDistance = distance;
          bestCount = after.length - k;
        }
      }
      const applied = after.slice(after.length - bestCount);
      // The basis changes where the splits fall: between this period and the next newer one's report (its end + 90
      // days), or this period continues the newer period's pre-split basis. A jump elsewhere (a merger, a spin-off
      // recorded as a split) is not a split.
      const dueOfNewer = parse(ends[previous]!) + 90 * DAY_MS;
      const known = new Set(out[previous]!.splits.map((event) => event.date));
      const transition = applied.every((event) => known.has(event.date) || parse(event.date) <= dueOfNewer);
      const continuation = out[previous]!.factor === best;
      const neighbour = newer[newer.length - 1]!;
      const matches = (count: number) => Math.abs((shares * best) / count - 1) <= SPLIT_MATCH;
      if (best !== 1 && matches(reference) && matches(neighbour) && (transition || continuation))
        out[index] = { factor: best, splits: applied };
    }
    newer.push(shares * out[index]!.factor);
    previous = index;
  }
  return out;
}

function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle]! : (sorted[middle - 1]! + sorted[middle]!) / 2;
}

/** Per period of one block (index-aligned with `history[kind].end`). */
export function epsAdjustments(history: FinancialHistory, kind: PeriodKind): (EpsAdjustment | null)[] {
  const series = history[kind];
  const annual = kind === 'annual';
  const events = history.market === 'TW' ? history.stockDividends : [];
  const dividends = series.end.map((end) => dividendFactor(end, events, undefined, annual));
  const reported = series.end.map((_, index) => reportedEpsAt(series, index));
  // Split evidence on the dividend-restated basis, so a 配股 between two periods does not blur the share counts.
  const base = reported.map((value, index) => (value === null || dividends[index] === null ? null : value / dividends[index]!.factor));
  const splits = splitEventsOf(history);
  const factors = splitFactors(series.end, base, series.values.netIncome, splits);
  const derivedQ4 = history.quality.issues.includes('q4-derived');
  return series.end.map((end, index) => {
    const dividend = dividends[index];
    if (dividend === null || dividend === undefined) return null;
    const split = factors[index]!;
    const value = reported[index];
    const income = series.values.netIncome[index] ?? null;
    const endMs = parse(end);
    const mixedQ4 =
      !annual &&
      derivedQ4 &&
      series.fq[index] === 4 &&
      value !== null &&
      income !== null &&
      value !== 0 &&
      income !== 0 &&
      Math.sign(value) !== Math.sign(income) &&
      splits.some((event) => parse(event.date) > endMs - 365 * DAY_MS && parse(event.date) <= endMs + 90 * DAY_MS);
    return {
      factor: Math.round(dividend.factor * split.factor * 1e6) / 1e6,
      dividends: dividend.events,
      splits: split.splits,
      splitFactor: split.factor,
      mixedQ4,
    };
  });
}

/** Reported ÷ factor (4 decimals); null without an EPS or an adjustment, or for a mixed-basis Q4. */
export function restatedEps(reported: number | null, adjustment: EpsAdjustment | null | undefined): number | null {
  if (reported === null || !adjustment || adjustment.mixedQ4) return null;
  return Math.round((reported / adjustment.factor) * 1e4) / 1e4 + 0;
}

const trim = (value: number) => (Number.isInteger(value) ? String(value) : value.toFixed(2));
/** 4 → 「1 拆 4」; a reverse split 0.1 → 「10 併 1」. */
export const splitRatioText = (ratio: number) => (ratio >= 1 ? `1 拆 ${trim(ratio)}` : `${trim(1 / ratio)} 併 1`);

/** 「配股 1.9738 元（除權 2025-09-04）、股票分割 1 拆 10（2025-11-06）」 */
export function adjustmentText(adjustment: EpsAdjustment): string {
  const parts = [];
  if (adjustment.dividends.length) parts.push(eventText(adjustment.dividends));
  for (const split of adjustment.splits) parts.push(`股票分割 ${splitRatioText(split.ratio)}（${split.date}）`);
  return parts.join('、');
}

const changed = (adjustment: EpsAdjustment | null) => !!adjustment && (adjustment.factor !== 1 || adjustment.mixedQ4);

/** True when some period's EPS differs on today's share basis (the 公告／還原 switch is offered only then). */
export function hasEpsRestatement(history: FinancialHistory): boolean {
  return (['annual', 'quarterly'] as const).some((kind) => epsAdjustments(history, kind).some(changed));
}

/** 「還原 EPS（依配股追溯調整）」, 「…（依分割追溯調整）」 or 「…（依配股與分割追溯調整）」 after the events that apply. */
export function restatedEpsLabel(history: FinancialHistory): string {
  let dividend = false;
  let split = false;
  for (const kind of ['annual', 'quarterly'] as const)
    for (const adjustment of epsAdjustments(history, kind)) {
      if (!adjustment) continue;
      dividend ||= adjustment.dividends.length > 0;
      split ||= adjustment.splits.length > 0 || adjustment.mixedQ4;
    }
  const basis = dividend && split ? '配股與分割' : split ? '分割' : '配股';
  return `還原 EPS（依${basis}追溯調整）`;
}

export const RESTATED_EPS_HELP = '還原 EPS：公告 EPS ÷ 之後的配股（1 + 配股元 ÷ 10）與股票分割比例，換算成目前股數，前後期才能比較；公告 EPS 為公司當時財報的數字。減資與現金增資沒有事件資料，未調整。';
export const MIXED_Q4_REASON = '第四季 EPS 由全年減前三季推算，但該年度有股票分割（全年已用新股數、前三季仍是舊股數），口徑不一致，不顯示';

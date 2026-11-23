import type { CapitalReductionEvent, FinancialHistory, FinancialSeries, PeriodKind, StockDividendEvent } from './FinancialHistory';
import { dividendFactor, eventText, reductionFactor, reductionText, reportDelayDays, restatesEps } from './stockDividendAdjust';

/**
 * EPS on today's share basis (還原 EPS). Reported EPS stays in the file untouched; this module only works out, per
 * period, the factor that puts it on the current share count, with the events behind it for provenance:
 *
 *  - Taiwan stock dividends (配股): stockDividendAdjust.ts (ex-rights date after the report was due).
 *  - Taiwan capital reductions to offset losses (減資彌補虧損, file block `capitalReductions`): shares cancelled
 *    without cash, restated like a reverse split by new ÷ old shares (resumption date after the report was due).
 *    Cash capital reductions (現金減資) and cash capital increases are never restated (IAS 33).
 *  - Stock splits / reverse splits from the file's `split` block (ratio ≥ 1.5 or ≤ 1/1.5): applied only where the
 *    share count the EPS implies (net income ÷ EPS) confirms that the period is still on the pre-split basis, the
 *    same test as build_financials.adjust_splits. SEC files the builder already adjusted pass the test with factor 1;
 *    Taiwan FinMind files (par-value changes such as 8422.TW 1 拆 10 on 2025-11-06) are not adjusted by the builder.
 *    Taiwan files also carry FinMind split / par-value change events (block `twSplits`, atlas/scripts/tw_splits.py),
 *    and without any recorded event a clean jump of the implied share count is inferred as a split (inferSplits,
 *    marked 推定 with its evidence). The annual block also walks the quarters after its newest year for evidence
 *    (5904.TWO 2025: the split is between the 2026 Q2 and Q3 reports, before any annual report on the new basis).
 *  - Derived fourth quarters (annual − Q1..Q3, quality issue `q4-derived`) across a share-basis change between the Q3
 *    and the annual report (8422.TW 2025: annual after the split, Q1..Q3 before it) are recomputed on today's basis: restated annual −
 *    Σ restated Q1..Q3 (`derivedQ4`). The builder leaves Q4 as published (build_financials_tw_finmind.py); this is the
 *    one place the rule lives. Without the annual or one of Q1..Q3 such a Q4 has no restated value (`mixedQ4`).
 *
 * Restated EPS = reported EPS ÷ factor (or the recomputed derived Q4). Null adjustment (Taiwan file without a
 * stockDividends block) → no restated value: callers show the reported EPS and never guess.
 */

export type EpsDisplayBasis = 'restated' | 'reported';

/** Evidence of an inferred split: the two quarters whose implied share counts (net income ÷ EPS) jump. */
export interface InferredSplitEvidence {
  before: string;
  after: string;
  sharesBefore: number;
  sharesAfter: number;
  periodsBefore: number;
  periodsAfter: number;
}

export interface SplitEvent {
  date: string;
  ratio: number;
  /** Where the event comes from: the file's `split` block (Yahoo era), FinMind `twSplits`, or inferSplits. */
  source?: 'yahoo' | 'finmind' | 'inferred';
  /** FinMind kind (par: 面額變更). */
  kind?: 'split' | 'reverse' | 'par';
  evidence?: InferredSplitEvidence;
}

/** One piece of a recomputed derived Q4: its label, reported EPS and restated EPS. */
export interface DerivedQ4Part {
  label: string;
  reported: number;
  restated: number;
}

/** A derived Q4 across a share-basis change, recomputed on today's basis: annual − (Q1 + Q2 + Q3), all restated. */
export interface DerivedQ4 {
  /** Restated annual − Σ restated Q1..Q3 (4 decimals). */
  restated: number;
  /** Annual first, then Q1..Q3. */
  parts: DerivedQ4Part[];
  /** Events behind the pieces' factors (for the note). */
  events: string;
}

export interface EpsAdjustment {
  /** Reported EPS ÷ factor = EPS on today's share basis (6 decimals); not used when `derivedQ4` is set. */
  factor: number;
  /** Stock dividends applied (Taiwan). */
  dividends: StockDividendEvent[];
  /** Loss-offset capital reductions applied (Taiwan). */
  reductions: CapitalReductionEvent[];
  /** Splits applied (confirmed by the implied share count) and their product. */
  splits: SplitEvent[];
  splitFactor: number;
  /** Dividends × capital reductions (no splits): the factor of the events an unadjusted price also goes through. */
  shareFactor: number;
  /** A derived Q4 across a share-basis change, recomputed from its restated pieces; null otherwise. */
  derivedQ4: DerivedQ4 | null;
  /**
   * A derived Q4 across a share-basis change (its pieces have different factors, or its EPS sign contradicts its net
   * income next to a split) that cannot be recomputed because the annual EPS or one of Q1..Q3 is missing: no restated
   * value is shown.
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

const memo = new WeakMap<FinancialHistory, SplitEvent[]>();

/**
 * Split events to test, oldest first. A US file the builder already split-adjusted (`split.adjusted`: adjust_splits and
 * repair_split_eps on its share counts) is trusted as is. Taiwan FinMind statements are never split-adjusted by the
 * builder (the flag there is carried over from the earlier Yahoo file), so Taiwan events are always tested; a split on a
 * 配股 ex-rights date is that dividend, not a second event. Taiwan: the applied FinMind events (`twSplits`) win over a
 * Yahoo event within 7 days (the same split), and with a stockDividends block the splits inferSplits finds are added.
 * Mirror of atlas/scripts/build_growth.py tw_split_events.
 */
export function splitEventsOf(history: FinancialHistory): SplitEvent[] {
  const cached = memo.get(history);
  if (cached) return cached;
  const events = computeSplitEvents(history);
  memo.set(history, events);
  return events;
}

function computeSplitEvents(history: FinancialHistory): SplitEvent[] {
  if (history.market !== 'TW' && history.split.adjusted) return [];
  const taiwan = history.market === 'TW';
  const dividends = taiwan ? (history.stockDividends ?? []) : [];
  // Yahoo records a 減資 as a reverse split: a loss-offset one is restated from the reduction block instead, a cash
  // one must not be restated at all.
  const reductions = taiwan ? (history.capitalReductions ?? []) : [];
  const finmind: SplitEvent[] = taiwan
    ? (history.twSplits ?? [])
        .filter((event) => event.applied && usableRatio(event.ratio))
        .map((event) => ({ date: event.date, ratio: event.ratio, source: 'finmind', kind: event.kind }))
    : [];
  const yahoo: SplitEvent[] = history.split.events
    .filter((event) => usableRatio(event.ratio))
    .filter((event) => !dividends.some((dividend) => Math.abs(parse(dividend.exDate) - parse(event.date)) <= 5 * DAY_MS))
    .filter((event) => !reductions.some((reduction) => Math.abs(parse(reduction.date) - parse(event.date)) <= 7 * DAY_MS))
    .filter((event) => !finmind.some((other) => Math.abs(parse(other.date) - parse(event.date)) <= 7 * DAY_MS))
    .map((event) => ({ date: event.date, ratio: event.ratio, source: 'yahoo' }));
  const recorded = [...finmind, ...yahoo].sort((a, b) => (a.date < b.date ? -1 : 1));
  if (!taiwan || history.stockDividends === null) return recorded;
  const blockers = [
    ...recorded.map((event) => event.date),
    ...history.split.events.map((event) => event.date),
    ...dividends.filter((event) => event.perShare >= BIG_STOCK_DIVIDEND).map((event) => event.exDate),
    ...reductions.filter((event) => !restatesEps(event)).map((event) => event.date),
  ];
  const q = history.quarterly;
  const points = q.end.map((end, index) => {
    const reported = reportedEpsAt(q, index);
    const dividend = dividendFactor(end, history.stockDividends);
    const reduction = reductionFactor(end, history.capitalReductions);
    return {
      end,
      eps: reported === null || dividend === null ? null : reported / (dividend.factor * reduction.factor),
      netIncome: q.values.netIncome[index] ?? null,
      revenue: q.values.revenue[index] ?? null,
      due: addDays(end, reportDelayDays(end)),
    };
  });
  const inferred = inferSplits(points, blockers);
  return [...recorded, ...inferred].sort((a, b) => (a.date < b.date ? -1 : 1));
}

/** Inferred-split rule constants (atlas/scripts/tw_splits.py has the same values and the measurement behind them). */
export const INFER = { minRatio: 2, tolerance: 0.01, minEps: 0.2, stable: 0.15, minBefore: 3, maxGapDays: 200, revenue: 2.5, window: 4 };
/** 配股 of at least this NT$ per share (par 10) is a ≥ 1.5x share change. */
const BIG_STOCK_DIVIDEND = 5;

export interface InferencePoint {
  end: string;
  /** EPS on the 配股／減資-restated basis. */
  eps: number | null;
  netIncome: number | null;
  revenue: number | null;
  /** Report due date (YYYY-MM-DD). */
  due: string;
}

const addDays = (day: string, days: number) => new Date(parse(day) + days * DAY_MS).toISOString().slice(0, 10);

/**
 * Splits inferred from consecutive quarterly implied share counts (net income ÷ EPS) when no event is recorded: a jump
 * by a near-integer n ≥ 2 (or 1/n) between two quarters ≤ 200 days apart, within 1% + cent rounding of both EPS, with a
 * stable level before (≥ 3 quarters within ±15%) and after (the newer quarter and up to 3 later ones within ±15%),
 * revenue continuing (median after ÷ before within [1/2.5, 2.5]), positive net income on both sides when the newer
 * quarter is the newest one, and no blocker (recorded split, big 配股, unrestated 減資) between the older quarter's end
 * and the newer one's report. A constant offset (2006.TW: NI includes minority interests) is no jump, a small issuance
 * is not near-integer, a merger brings revenue with it. Dated the day after the older quarter's end.
 * Mirror of atlas/scripts/tw_splits.py infer_splits.
 */
export function inferSplits(points: readonly InferencePoint[], blockers: readonly string[]): SplitEvent[] {
  const usable = points
    .flatMap((point) => {
      const { eps, netIncome } = point;
      if (eps === null || netIncome === null || netIncome === 0 || Math.abs(eps) < INFER.minEps) return [];
      const shares = netIncome / eps;
      return shares > 0 && Number.isFinite(shares) ? [{ ...point, eps, netIncome, shares }] : [];
    })
    .sort((a, b) => (a.end < b.end ? -1 : 1));
  const found: SplitEvent[] = [];
  for (let index = 1; index < usable.length; index++) {
    const older = usable[index - 1]!;
    const newer = usable[index]!;
    if ((parse(newer.end) - parse(older.end)) / DAY_MS > INFER.maxGapDays) continue;
    const jump = newer.shares / older.shares;
    const size = jump >= 1 ? jump : 1 / jump;
    const whole = Math.round(size);
    if (whole < INFER.minRatio) continue;
    if (Math.abs(size / whole - 1) > INFER.tolerance + 0.005 / Math.abs(older.eps) + 0.005 / Math.abs(newer.eps)) continue;
    const before = usable.slice(Math.max(0, index - INFER.window), index);
    const after = usable.slice(index, index + INFER.window);
    if (before.length < INFER.minBefore) continue;
    const levelBefore = median(before.map((point) => point.shares));
    const levelAfter = median(after.map((point) => point.shares));
    if (before.some((point) => Math.abs(point.shares / levelBefore - 1) > INFER.stable)) continue;
    if (after.some((point) => Math.abs(point.shares / levelAfter - 1) > INFER.stable)) continue;
    const revenueBefore = before.flatMap((point) => (point.revenue ? [point.revenue] : []));
    const revenueAfter = after.flatMap((point) => (point.revenue ? [point.revenue] : []));
    if (!revenueBefore.length || !revenueAfter.length || median(revenueBefore) <= 0) continue;
    const revenue = median(revenueAfter) / median(revenueBefore);
    if (revenue < 1 / INFER.revenue || revenue > INFER.revenue) continue;
    if (after.length < 2 && !(older.netIncome > 0 && newer.netIncome > 0)) continue;
    if (blockers.some((day) => day > older.end && day <= newer.due)) continue;
    found.push({
      date: addDays(older.end, 1),
      ratio: jump >= 1 ? whole : Math.round((1 / whole) * 1e6) / 1e6,
      source: 'inferred',
      evidence: {
        before: older.end,
        after: newer.end,
        sharesBefore: Math.round(older.shares),
        sharesAfter: Math.round(newer.shares),
        periodsBefore: before.length,
        periodsAfter: after.length,
      },
    });
  }
  return found;
}

/**
 * Split factor per period. Walking from the newest period back, a period's implied share count (net income ÷ EPS)
 * × the product of a suffix of the splits after its end must land within ±SPLIT_MATCH of the reference — the median
 * of the next (up to) three newer periods' adjusted counts, so one odd period (a derived Q4) cannot set the basis —
 * and beat leaving it alone; it must match the next newer period's count as well. The splits must also fall before
 * that newer period's report, unless the period just continues its factor. Periods without an implied count (no net income, loss/profit sign
 * mismatch, |EPS| < MIN_EVIDENCE_EPS) keep factor 1.
 *
 * Dated events (FinMind `twSplits` and inferred splits, `dues` given: each period's report due date): a period whose
 * report was due before such an event is on the pre-split basis whatever its neighbours show (FinMind EPS are as
 * published), so where the evidence above does not settle it, it takes the product of those events unless its implied
 * count already matches the newer periods' basis without them (a report that restated its comparatives). This keeps
 * periods across an unrelated share change (2327.TW: a cash capital reduction in 2022) and periods without an implied
 * count on today's basis.
 */
export function splitFactors(
  ends: readonly string[],
  eps: readonly (number | null)[],
  netIncome: readonly (number | null)[],
  splits: readonly SplitEvent[],
  dues?: readonly string[],
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
  const datedAfter = (index: number): SplitEvent[] =>
    dues ? splits.filter((event) => (event.source === 'finmind' || event.source === 'inferred') && event.date > dues[index]!) : [];
  const productOf = (events: readonly SplitEvent[]) => events.reduce((product, event) => product * event.ratio, 1);
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
    const dated = out[index]!.factor === 1 ? datedAfter(index) : [];
    if (dated.length) {
      const product = productOf(dated);
      const reference = newer.length ? median(newer.slice(-3)) : null;
      const already =
        reference !== null &&
        Math.abs(shares / reference - 1) <= SPLIT_MATCH &&
        Math.abs(Math.log(shares / reference)) < Math.abs(Math.log((shares * product) / reference));
      if (!already) out[index] = { factor: Math.round(product * 1e6) / 1e6, splits: dated };
    }
    newer.push(shares * out[index]!.factor);
    previous = index;
  }
  // No implied count: nothing contradicts the dates.
  ends.forEach((_, index) => {
    if (implied(index) !== null) return;
    const dated = datedAfter(index);
    if (dated.length) out[index] = { factor: Math.round(productOf(dated) * 1e6) / 1e6, splits: dated };
  });
  return out;
}

function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle]! : (sorted[middle - 1]! + sorted[middle]!) / 2;
}

/** Per period of one block (index-aligned with `history[kind].end`). */
export function epsAdjustments(history: FinancialHistory, kind: PeriodKind): (EpsAdjustment | null)[] {
  const base = baseAdjustments(history, kind);
  if (kind === 'annual' || !history.quality.issues.includes('q4-derived')) return base;
  return withDerivedQ4(history, base, baseAdjustments(history, 'annual'));
}

/** Factors per period from the events alone (no derived-Q4 rule). */
function baseAdjustments(history: FinancialHistory, kind: PeriodKind): (EpsAdjustment | null)[] {
  const series = history[kind];
  const annual = kind === 'annual';
  const taiwan = history.market === 'TW';
  const events = taiwan ? history.stockDividends : [];
  const dividends = series.end.map((end) => dividendFactor(end, events, undefined, annual));
  const reductions = series.end.map((end) => reductionFactor(end, taiwan ? history.capitalReductions : null, undefined, annual));
  const reported = series.end.map((_, index) => reportedEpsAt(series, index));
  // Split evidence on the dividend / reduction-restated basis, so a 配股 or 減資 between two periods does not blur the
  // share counts.
  const base = reported.map((value, index) =>
    value === null || dividends[index] === null ? null : value / (dividends[index]!.factor * reductions[index]!.factor),
  );
  const splits = splitEventsOf(history);
  const dues = series.end.map((end) => addDays(end, annual ? 90 : reportDelayDays(end)));
  const factors =
    annual && taiwan && splits.length ? annualSplitFactors(history, base, splits) : splitFactors(series.end, base, series.values.netIncome, splits, dues);
  const derivedQ4 = history.quality.issues.includes('q4-derived');
  return series.end.map((end, index) => {
    const dividend = dividends[index];
    if (dividend === null || dividend === undefined) return null;
    const reduction = reductions[index]!;
    const split = factors[index]!;
    const value = reported[index];
    const income = series.values.netIncome[index] ?? null;
    const endMs = parse(end);
    // The old evidence of a mixed Q4: its sign contradicts its net income next to a split.
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
    const shareFactor = dividend.factor * reduction.factor;
    return {
      factor: Math.round(shareFactor * split.factor * 1e6) / 1e6,
      dividends: dividend.events,
      reductions: reduction.events,
      splits: split.splits,
      splitFactor: split.factor,
      shareFactor: Math.round(shareFactor * 1e6) / 1e6,
      derivedQ4: null,
      mixedQ4,
    };
  });
}

/**
 * Taiwan annual split factors: the quarters after the newest year (on their 配股／減資-restated basis) join the walk as
 * evidence only, so a year before a split no annual report reaches yet (5904.TWO 2025) is still confirmed. Mirror of
 * build_growth.restate_splits(annual, splits, anchors).
 */
function annualSplitFactors(history: FinancialHistory, base: readonly (number | null)[], splits: readonly SplitEvent[]) {
  const a = history.annual;
  const q = history.quarterly;
  const newest = a.end[a.end.length - 1];
  const anchors = q.end.flatMap((end, index) => {
    if (newest === undefined || end <= newest) return [];
    const reported = reportedEpsAt(q, index);
    const dividend = dividendFactor(end, history.stockDividends);
    const reduction = reductionFactor(end, history.capitalReductions);
    const eps = reported === null || dividend === null ? null : reported / (dividend.factor * reduction.factor);
    return [{ end, eps, netIncome: q.values.netIncome[index] ?? null }];
  });
  const factors = splitFactors(
    [...a.end, ...anchors.map((anchor) => anchor.end)],
    [...base, ...anchors.map((anchor) => anchor.eps)],
    [...a.end.map((_, index) => a.values.netIncome[index] ?? null), ...anchors.map((anchor) => anchor.netIncome)],
    splits,
    [...a.end.map((end) => addDays(end, 90)), ...anchors.map((anchor) => addDays(anchor.end, reportDelayDays(anchor.end)))],
  );
  return factors.slice(0, a.end.length);
}

const restatedBy = (reported: number, adjustment: EpsAdjustment) => reported / adjustment.factor;

/**
 * Derived fourth quarters (annual − Q1..Q3) across a share-basis change: when Q3 (whose report's year-to-date figure
 * the published Q4 was taken from) or the Q4 itself carries another factor than the annual (or the Q4 shows the old
 * mixed-basis evidence), Q4 = restated annual − Σ restated Q1..Q3, every piece on today's share basis (the annual
 * restated for the events after its own report due date). A 配股 between the Q2 and Q3 reports is no crossing: the Q3
 * report's year-to-date figure is already on the new basis (2755.TWO 2025).
 * With equal factors the plain factor gives the same number, so nothing changes there. A piece missing → the Q4 keeps
 * its own factor, or no restated value when it shows the mixed-basis evidence (mixedQ4).
 */
function withDerivedQ4(history: FinancialHistory, quarterly: (EpsAdjustment | null)[], annual: (EpsAdjustment | null)[]): (EpsAdjustment | null)[] {
  const q = history.quarterly;
  const a = history.annual;
  return quarterly.map((adjustment, index) => {
    if (!adjustment || q.fq[index] !== 4) return adjustment;
    const end = q.end[index]!;
    const endMs = parse(end);
    const yearIndex = a.end.indexOf(end);
    const pieces: { label: string; reported: number | null; adjustment: EpsAdjustment | null }[] = [
      { label: '全年', reported: yearIndex < 0 ? null : reportedEpsAt(a, yearIndex), adjustment: yearIndex < 0 ? null : (annual[yearIndex] ?? null) },
    ];
    for (const fq of [1, 2, 3]) {
      const at = q.end.findIndex((other, i) => q.fq[i] === fq && q.fy[i] === q.fy[index] && other < end && endMs - parse(other) < 300 * DAY_MS);
      pieces.push({ label: `Q${fq}`, reported: at < 0 ? null : reportedEpsAt(q, at), adjustment: at < 0 ? null : (quarterly[at] ?? null) });
    }
    // The published Q4 is the annual less the Q3 report's year-to-date figure, which is on Q3's basis: a share-basis
    // change between the Q3 and the annual report (or a Q4 factor of its own) makes the difference mixed.
    const yearFactor = pieces[0]!.adjustment?.factor;
    const q3Factor = pieces[3]!.adjustment?.factor;
    const differs = (factor: number | undefined) => yearFactor !== undefined && factor !== undefined && Math.abs(factor / yearFactor - 1) > 1e-6;
    const crossing = differs(q3Factor) || differs(adjustment.factor);
    if (!crossing && !adjustment.mixedQ4) return adjustment;
    // A piece missing: the file cannot have derived this Q4 from these pieces, so only the mixed-basis evidence hides it.
    if (pieces.some((piece) => piece.reported === null || !piece.adjustment)) return adjustment;
    const restated = pieces.map((piece) => restatedBy(piece.reported!, piece.adjustment!));
    const value = restated[0]! - restated[1]! - restated[2]! - restated[3]!;
    const unique = <T,>(items: T[], key: (item: T) => string) => [...new Map(items.map((item) => [key(item), item])).values()];
    const all = pieces.map((piece) => piece.adjustment!);
    const events = adjustmentText({
      ...adjustment,
      dividends: unique(all.flatMap((item) => item.dividends), (event) => `${event.exDate}|${event.perShare}`).sort((x, y) => (x.exDate < y.exDate ? -1 : 1)),
      reductions: unique(all.flatMap((item) => item.reductions), (event) => event.date).sort((x, y) => (x.date < y.date ? -1 : 1)),
      splits: unique(all.flatMap((item) => item.splits), (event) => event.date).sort((x, y) => (x.date < y.date ? -1 : 1)),
    });
    return {
      ...adjustment,
      mixedQ4: false,
      derivedQ4: {
        restated: round4(value),
        parts: pieces.map((piece, i) => ({ label: piece.label, reported: piece.reported!, restated: round4(restated[i]!) })),
        events,
      },
    };
  });
}

const round4 = (value: number) => Math.round(value * 1e4) / 1e4 + 0;

/** Reported ÷ factor (4 decimals), or the recomputed derived Q4; null without an EPS or an adjustment, or for a mixed-basis Q4. */
export function restatedEps(reported: number | null, adjustment: EpsAdjustment | null | undefined): number | null {
  if (reported === null || !adjustment || adjustment.mixedQ4) return null;
  if (adjustment.derivedQ4) return adjustment.derivedQ4.restated;
  return round4(reported / adjustment.factor);
}

const trim = (value: number) => (Number.isInteger(value) ? String(value) : value.toFixed(2));
/** 4 → 「1 拆 4」; a reverse split 0.1 → 「10 併 1」. */
export const splitRatioText = (ratio: number) => (ratio >= 1 ? `1 拆 ${trim(ratio)}` : `${trim(1 / ratio)} 併 1`);

const sharesText = (shares: number) => `${(shares / 1e8).toFixed(2)} 億股`;

/** 「面額變更 1 拆 10（恢復買賣 2026-08-10）」, 「推定股票分割 1 拆 10（隱含股數 …）」, 「股票分割 1 拆 10（2025-11-06）」 */
export function splitEventText(split: SplitEvent): string {
  if (split.source === 'inferred' && split.evidence) {
    const e = split.evidence;
    return `推定股票分割 ${splitRatioText(split.ratio)}（無分割公告資料，依隱含股數 ${e.before} ${sharesText(e.sharesBefore)} → ${e.after} ${sharesText(e.sharesAfter)} 推定）`;
  }
  if (split.kind === 'par') return `面額變更 ${splitRatioText(split.ratio)}（恢復買賣 ${split.date}）`;
  if (split.kind === 'reverse') return `反分割 ${splitRatioText(split.ratio)}（${split.date}）`;
  return `股票分割 ${splitRatioText(split.ratio)}（${split.date}）`;
}

/** 「配股 1.9738 元（除權 2025-09-04）、股票分割 1 拆 10（2025-11-06）」 */
export function adjustmentText(adjustment: EpsAdjustment): string {
  const parts = [];
  if (adjustment.dividends.length) parts.push(eventText(adjustment.dividends));
  if (adjustment.reductions.length) parts.push(reductionText(adjustment.reductions));
  for (const split of adjustment.splits) parts.push(splitEventText(split));
  return parts.join('、');
}

/** Page note for Taiwan FinMind / inferred split events (Yahoo-era events have their own note); null without any. */
export function twSplitNote(history: FinancialHistory): string | null {
  if (history.market !== 'TW') return null;
  const events = splitEventsOf(history).filter((event) => event.source === 'finmind' || event.source === 'inferred');
  if (!events.length) return null;
  const sources = [
    events.some((event) => event.source === 'finmind') && 'FinMind 分割／面額變更資料',
    events.some((event) => event.source === 'inferred') && '推定事件僅在財報隱含股數證實時套用',
  ].filter((part): part is string => !!part);
  return `還原 EPS 依股數變動換算：${events.map(splitEventText).join('、')}。${sources.join('；')}。`;
}

const changed = (adjustment: EpsAdjustment | null) => !!adjustment && (adjustment.factor !== 1 || adjustment.mixedQ4 || !!adjustment.derivedQ4);

/** True when some period's EPS differs on today's share basis (the 公告／還原 switch is offered only then). */
export function hasEpsRestatement(history: FinancialHistory): boolean {
  return (['annual', 'quarterly'] as const).some((kind) => epsAdjustments(history, kind).some(changed));
}

/** 「還原 EPS（依配股追溯調整）」, 「…（依分割追溯調整）」, 「…（依配股與分割追溯調整）」, 「…（依配股、分割與減資追溯調整）」… after the events that apply. */
export function restatedEpsLabel(history: FinancialHistory): string {
  let dividend = false;
  let split = false;
  let reduction = false;
  for (const kind of ['annual', 'quarterly'] as const)
    for (const adjustment of epsAdjustments(history, kind)) {
      if (!adjustment) continue;
      dividend ||= adjustment.dividends.length > 0;
      split ||= adjustment.splits.length > 0 || adjustment.mixedQ4;
      reduction ||= adjustment.reductions.length > 0;
    }
  const parts = [dividend && '配股', split && '分割', reduction && '減資'].filter((part): part is string => !!part);
  if (!parts.length) parts.push('配股');
  const basis = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join('、')}與${parts[parts.length - 1]}`;
  return `還原 EPS（依${basis}追溯調整）`;
}

export const RESTATED_EPS_HELP =
  '還原 EPS：公告 EPS ÷ 之後的配股（1 + 配股元 ÷ 10）、股票分割／面額變更與減資彌補虧損（新股數 ÷ 舊股數）比例，換算成目前股數，前後期才能比較；由全年減前三季推算的第四季，以還原全年 EPS 減還原前三季 EPS 計算。公告 EPS 為公司當時財報的數字。現金減資（退還股款）與現金增資依 IAS 33 不追溯調整。';
export const MIXED_Q4_REASON = '第四季 EPS 由全年減前三季推算且跨越股數變動，但缺全年或前三季 EPS，無法換算成目前股數，不顯示';
/** 公告 mode note of a derived Q4 whose pieces sit on different share bases. */
export const DERIVED_Q4_REPORTED_NOTE = '此季為全年減前三季推算，跨越股數變動，請參考還原值';

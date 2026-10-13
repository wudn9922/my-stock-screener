/**
 * Duration / convexity scenario math for the long Treasury ETFs (rates research view).
 *
 * Units, used everywhere in this module:
 * - duration D in years (modified / effective duration);
 * - convexity C in years², matching ΔP/P ≈ −D·Δy + ½·C·Δy² with Δy as a decimal (100bp = 0.01).
 *   iShares publishes convexity per 100bp² (TLT ≈ 3.6); multiply by 100 to get years² (≈ 360).
 * - rate shocks in basis points; results in percent.
 *
 * The second-order approximation is good to about 0.5pp for a 30-year zero at ±100bp and drifts to
 * a few pp at ±200bp and beyond (it understates both the gain on a fall and, less, the loss on a rise).
 * A leveraged daily-reset fund (L > 1) only follows L × the formula for an instantaneous one-day move;
 * over longer holding periods its path (volatility drag, financing) dominates.
 */

export const SCENARIO_SHOCKS_BP = [-200, -100, -50, -25, 25, 50, 100, 200] as const;
export const SCENARIO_RANGE_BP = { min: -300, max: 300, step: 5 } as const;

const finite = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value);

/**
 * Price change in percent for an instantaneous parallel yield shift of `dyBp` basis points:
 * L·(−D·Δy + ½·C·Δy²) × 100, Δy = dyBp / 10000, floored at −100% (a fund cannot lose more than
 * everything). A missing convexity counts as zero; a missing duration or bad input gives null.
 */
export function scenarioPct(
  duration: number | null | undefined,
  convexity: number | null | undefined,
  dyBp: number,
  leverage = 1,
): number | null {
  if (!finite(duration) || !finite(dyBp) || !finite(leverage) || leverage <= 0) return null;
  const c = finite(convexity) ? convexity : 0;
  const dy = dyBp / 10000;
  const pct = leverage * (-duration * dy + 0.5 * c * dy * dy) * 100;
  return Math.max(-100, pct);
}

/** iShares-style published convexity (per 100bp²) → years². */
export function fromPublishedConvexity(published: number | null | undefined): number | null {
  return finite(published) ? published * 100 : null;
}

/** The fields the scenario math needs from an ETF entry of rates.json. */
export interface ScenarioEtf {
  symbol: string;
  /** Daily leverage multiple (1 for plain funds, 3 for TMF). */
  leverage: number;
  /** Annual expense ratio in percent. */
  expenseRatio: number | null;
  /** Issuer-published effective duration of the (unlevered) portfolio or index, years. */
  effectiveDuration: number | null;
  /** Model duration (years) and convexity (years²) of the unlevered proxy portfolio. */
  model: { duration: number | null; convexity: number | null } | null;
}

export type DurationBasis = 'published' | 'model';

/** Published duration when available (default), otherwise the model; `prefer: 'model'` forces the model. */
export function pickDuration(
  etf: ScenarioEtf,
  prefer: DurationBasis = 'published',
): { value: number | null; basis: DurationBasis | null } {
  const published = finite(etf.effectiveDuration) && etf.effectiveDuration > 0 ? etf.effectiveDuration : null;
  const model = etf.model && finite(etf.model.duration) && etf.model.duration > 0 ? etf.model.duration : null;
  if (prefer === 'published' && published !== null) return { value: published, basis: 'published' };
  if (model !== null) return { value: model, basis: 'model' };
  if (published !== null) return { value: published, basis: 'published' };
  return { value: null, basis: null };
}

/** Convexity always comes from the model (published convexity is not hand-entered). */
export function pickConvexity(etf: ScenarioEtf): number | null {
  return etf.model && finite(etf.model.convexity) ? etf.model.convexity : null;
}

export interface ScenarioCell {
  bp: number;
  pct: number | null;
  /** True when the formula went below −100% and was floored. */
  floored: boolean;
}
export interface ScenarioRow {
  symbol: string;
  leverage: number;
  duration: number | null;
  durationBasis: DurationBasis | null;
  convexity: number | null;
  /** Leveraged daily-reset fund: the numbers only hold for a one-day instantaneous move. */
  singleDayOnly: boolean;
  cells: ScenarioCell[];
}

export function scenarioCell(etf: ScenarioEtf, bp: number, prefer: DurationBasis = 'published'): ScenarioCell {
  const { value: duration } = pickDuration(etf, prefer);
  const convexity = pickConvexity(etf);
  const pct = scenarioPct(duration, convexity, bp, etf.leverage);
  if (pct === null || duration === null) return { bp, pct: null, floored: false };
  const c = convexity ?? 0;
  const dy = bp / 10000;
  const raw = etf.leverage * (-duration * dy + 0.5 * c * dy * dy) * 100;
  return { bp, pct, floored: raw < -100 };
}

/** Scenario table: one row per ETF, one cell per shock (default ±25/50/100/200bp). */
export function scenarioTable(
  etfs: readonly ScenarioEtf[],
  shocks: readonly number[] = SCENARIO_SHOCKS_BP,
  prefer: DurationBasis = 'published',
): ScenarioRow[] {
  return etfs.map((etf) => {
    const { value, basis } = pickDuration(etf, prefer);
    return {
      symbol: etf.symbol,
      leverage: etf.leverage,
      duration: value,
      durationBasis: basis,
      convexity: pickConvexity(etf),
      singleDayOnly: etf.leverage > 1,
      cells: shocks.map((bp) => scenarioCell(etf, bp, prefer)),
    };
  });
}

export interface HoldEstimate {
  /** Income over the year in percent: yield for plain funds; L×yield − (L−1)×short rate when levered. */
  carry: number | null;
  /** Price change from an instantaneous shift at the start of the year. */
  price: number | null;
  /** Expense ratio in percent. */
  cost: number;
  total: number | null;
  /** Leveraged funds: path-dependent, so the estimate is only a rough guide. */
  approximate: boolean;
}

/**
 * One-year hold estimate ≈ carry + price change − expense ratio, assuming the shift happens at once
 * and then the curve stays put. `carryYield` and `shortRate` are in percent.
 */
export function oneYearHold(
  etf: ScenarioEtf,
  input: { carryYield: number | null; shortRate: number | null; dyBp: number },
  prefer: DurationBasis = 'published',
): HoldEstimate {
  const leverage = etf.leverage;
  const cost = finite(etf.expenseRatio) ? etf.expenseRatio : 0;
  let carry: number | null = null;
  if (finite(input.carryYield)) {
    if (leverage === 1) carry = input.carryYield;
    else if (finite(input.shortRate)) carry = leverage * input.carryYield - (leverage - 1) * input.shortRate;
  }
  const price = scenarioCell(etf, input.dyBp, prefer).pct;
  const total = carry === null || price === null ? null : Math.max(-100, carry + price - cost);
  return { carry, price, cost, total, approximate: leverage > 1 };
}

/**
 * Builds the scenario input from an ETF record. Durations in rates.json describe the unlevered
 * portfolio; if a leveraged fund's duration looks already multiplied (above 35 years, which no
 * unlevered Treasury index reaches here), it is divided back so the leverage is not applied twice.
 */
export function toScenarioEtf(etf: {
  symbol: string;
  leverage: number;
  expenseRatio: number | null;
  effectiveDuration: number | null;
  model: { duration: number | null; convexity: number | null } | null;
}): ScenarioEtf {
  const leverage = finite(etf.leverage) && etf.leverage > 0 ? etf.leverage : 1;
  const unlever = (value: number | null) =>
    value !== null && leverage > 1 && value > 35 ? value / leverage : value;
  const unleverC = (value: number | null, duration: number | null) =>
    value !== null && leverage > 1 && duration !== null && duration > 35 ? value / leverage : value;
  return {
    symbol: etf.symbol,
    leverage,
    expenseRatio: etf.expenseRatio,
    effectiveDuration: unlever(etf.effectiveDuration),
    model: etf.model
      ? { duration: unlever(etf.model.duration), convexity: unleverC(etf.model.convexity, etf.model.duration) }
      : null,
  };
}

/**
 * A leveraged fund with no published duration of its own (TMF) tracks the same index as an unleveraged one
 * (TLT): borrow that fund's published duration, so the table reads 3 × TLT instead of mixing a published
 * and a model duration. Returns the funds with `effectiveDuration` filled in, plus where it came from.
 */
export function withBorrowedDuration<T extends { symbol: string; leverage: number; index: string | null; effectiveDuration: number | null }>(
  etfs: readonly T[],
): { etfs: T[]; borrowedFrom: Record<string, string> } {
  const base = (index: string | null) => (index ?? '').replace(/（[^）]*）\s*$/u, '').trim();
  const borrowedFrom: Record<string, string> = {};
  const out = etfs.map((etf) => {
    if (etf.effectiveDuration !== null || !(etf.leverage > 1) || !etf.index) return etf;
    const donor = etfs.find(
      (other) => other.leverage === 1 && other.effectiveDuration !== null && base(other.index) === base(etf.index),
    );
    if (!donor) return etf;
    borrowedFrom[etf.symbol] = donor.symbol;
    return { ...etf, effectiveDuration: donor.effectiveDuration };
  });
  return { etfs: out, borrowedFrom };
}

export interface FormulaParts {
  /** The shock in percentage points (100bp → 1). */
  dyPct: number;
  /** −D·Δy in percent. */
  durationTerm: number;
  /** ½·C·Δy² in percent (C in years²). */
  convexityTerm: number;
  /** The same convexity in the per-100bp² convention issuers publish (C / 100). */
  publishedConvexity: number;
  leverage: number;
  /** L·(durationTerm + convexityTerm), floored at −100%. */
  total: number;
  floored: boolean;
}

/** The pieces of the scenario formula, for the worked example under the scenario table. */
export function formulaParts(duration: number, convexity: number, dyBp: number, leverage = 1): FormulaParts {
  const dy = dyBp / 10000;
  const durationTerm = -duration * dy * 100;
  const convexityTerm = 0.5 * convexity * dy * dy * 100;
  const raw = leverage * (durationTerm + convexityTerm);
  return {
    dyPct: dyBp / 100,
    durationTerm,
    convexityTerm,
    publishedConvexity: convexity / 100,
    leverage,
    total: Math.max(-100, raw),
    floored: raw < -100,
  };
}

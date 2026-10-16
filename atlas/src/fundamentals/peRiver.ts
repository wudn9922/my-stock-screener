import type { FinancialHistory, FinancialSeries } from './FinancialHistory';

/**
 * P/E river (本益比河流圖): price against EPS(TTM) × a set of P/E multiples taken from the stock's own
 * history. Everything here is pure (no I/O, no clock) so the rules can be unit-tested:
 *
 *  1. EPS(TTM) of a date = sum of the latest four *published* single-quarter EPS that are consecutive
 *     fiscal quarters; if that is impossible the latest *published* annual EPS is used instead
 *     (basis 'annual'); if neither exists the date has no P/E.
 *  2. No look-ahead: a period's EPS is usable only from `period end + publish delay` (constants below).
 *  3. Splits: prices from the provider are split-adjusted, so EPS must be on the same share basis
 *     (`split.adjusted`). Unadjusted files drop the periods that end before their last split.
 *  4. EPS ≤ 0 → no P/E (the river breaks there and the point is left out of the statistics).
 *  5. Multiples are the P10/P30/P50/P70/P90 of the stock's own valid P/E series; the current P/E gets a
 *     percent-rank and a premium/discount against the median.
 *
 * It deliberately does not read valuation/{us,tw}.json: those hold only the latest EPS. The header P/E on
 * the chart page uses that latest figure (US: Yahoo TTM; TW: the exchange's own P/E), so it can differ
 * slightly from the current P/E here, which is rebuilt from the financial statements file.
 */

export const DAY_SECONDS = 86_400;

/**
 * Days after a period's end before its EPS may be used. Quarterly 45 days is the 10-Q / Taiwan Q1 & Q3
 * deadline. Annual figures and the derived fiscal Q4 come from the 10-K (60 days for large accelerated
 * filers; small caps may file later, up to 90) and from Taiwan's annual report (90 days); Taiwan's
 * semi-annual (Q2) report is due after 60 days. Longer than the usual publication date on purpose:
 * a too-early date would leak the future into the series.
 */
export const PUBLISH_DELAY_DAYS = {
  US: { quarter: 45, yearEnd: 60 },
  TW: { quarter: 45, secondQuarter: 60, yearEnd: 90 },
} as const;

/** A weekly bar is judged at its Friday (bar start + 4 days); the in-progress bar at the data date. */
export const BAR_OBSERVATION_OFFSET_DAYS = 4;
/** Consecutive fiscal quarters end 3 months apart (52/53-week calendars: ~91 days). */
export const QUARTER_GAP_DAYS = { min: 60, max: 130 } as const;
/** Newest quarter in a TTM sum may be at most this old (one quarter + the longest delay + slack). */
export const MAX_TTM_AGE_DAYS = 220;
/** Annual fallback: a year plus the longest delay plus slack; older means the file stopped updating. */
export const MAX_ANNUAL_AGE_DAYS = 550;
/** P/E above this (EPS close to zero) is treated as meaningless and kept out of the statistics. */
export const PE_MAX = 200;
/** Fewer valid weekly P/E points than this: no river. */
export const MIN_VALID_POINTS = 8;
/**
 * Fewer distinct EPS figures than this: no river either. With one or two EPS values the "history" of the
 * P/E is just the price path divided by a constant, which says nothing about the stock's own valuation range.
 */
export const MIN_EPS_PERIODS = 3;
/** Below these the river is drawn with a "short history" warning. */
export const SHORT_COVERAGE_YEARS = 3;
export const SHORT_EPS_PERIODS = 8;
export const RIVER_PERCENTILES = [10, 30, 50, 70, 90] as const;
export type RiverPercentile = (typeof RIVER_PERCENTILES)[number];

export type EpsBasis = 'ttm' | 'annual';

export interface EpsPeriod {
  end: string;
  endSec: number;
  /** First instant the figure may be used (end + publish delay). */
  availSec: number;
  /** Diluted EPS, else basic; null when the file has neither. */
  eps: number | null;
  /** True when this period only had basic EPS. */
  basic: boolean;
}

export interface EpsPeriods {
  quarters: EpsPeriod[];
  annuals: EpsPeriod[];
  notes: string[];
}

export interface EpsPoint {
  eps: number;
  basis: EpsBasis;
  /** End date of the newest period in the figure. */
  periodEnd: string;
  /** True when any summed period only had basic EPS. */
  usedBasic: boolean;
}

export const dateToSec = (day: string): number => Date.parse(`${day}T00:00:00Z`) / 1000;

/** Publish delay (days) of a period of this market; `yearEnd` for fiscal Q4 and annual figures. */
export function publishDelayDays(market: 'US' | 'TW', kind: 'quarter' | 'annual', fq: number | null, yearEnd = false): number {
  if (kind === 'annual' || fq === 4 || yearEnd) return PUBLISH_DELAY_DAYS[market].yearEnd;
  if (market === 'TW' && fq === 2) return PUBLISH_DELAY_DAYS.TW.secondQuarter;
  return PUBLISH_DELAY_DAYS[market].quarter;
}

function periodsOf(series: FinancialSeries, kind: 'quarter' | 'annual', market: 'US' | 'TW', annualEnds: ReadonlySet<string>, cutoff: string | null): EpsPeriod[] {
  const out: EpsPeriod[] = [];
  series.end.forEach((end, index) => {
    if (cutoff !== null && end < cutoff) return;
    const evidence = series.epsQuality?.[index];
    const isQ4 = market === 'US' && kind === 'quarter' && series.fq[index] === 4;
    const gradeDisallowsSelection = (grade: string | undefined) => market === 'US' &&
      (grade === 'C' || grade === 'D' || (isQ4 && grade !== 'A' && grade !== 'B'));
    const diluted = gradeDisallowsSelection(evidence?.epsDiluted) ? null : (series.values.epsDiluted[index] ?? null);
    const basicValue = gradeDisallowsSelection(evidence?.epsBasic) ? null : (series.values.epsBasic[index] ?? null);
    const eps = diluted ?? basicValue;
    const filed = diluted !== null ? evidence?.epsDilutedFiled : evidence?.epsBasicFiled;
    const delay = publishDelayDays(market, kind, kind === 'quarter' ? (series.fq[index] ?? null) : null, annualEnds.has(end));
    const endSec = dateToSec(end);
    const filingSec = filed ? dateToSec(filed) : NaN;
    const defaultAvailableSec = endSec + delay * DAY_SECONDS;
    const availSec = Number.isFinite(filingSec) ? Math.max(defaultAvailableSec, filingSec) : defaultAvailableSec;
    out.push({ end, endSec, availSec, eps, basic: diluted === null && basicValue !== null });
  });
  return out.sort((a, b) => a.endSec - b.endSec);
}

/**
 * EPS periods of a financials file, ready for `epsAt`. Unadjusted files that list a split keep only the
 * periods ending on or after the last split (earlier EPS is on the pre-split share basis, the prices are
 * not); a file flagged as cumulative quarters contributes annual figures only.
 */
export function buildEpsPeriods(history: FinancialHistory): EpsPeriods {
  const notes: string[] = [];
  const events = history.split.events;
  let cutoff: string | null = null;
  if (!history.split.adjusted && events.length > 0) {
    const last = events.reduce((max, event) => (event.date > max ? event.date : max), events[0]!.date);
    const earliest = [history.annual.end[0], history.quarterly.end[0]].filter((end): end is string => !!end).sort()[0];
    if (earliest !== undefined && last > earliest) {
      cutoff = last;
      notes.push(`財報檔未還原股票分割（${last}），${last} 之前的期別 EPS 已略過。`);
    }
  }
  const annualEnds = new Set(history.annual.end);
  const annuals = periodsOf(history.annual, 'annual', history.market, annualEnds, cutoff);
  let quarters = periodsOf(history.quarterly, 'quarter', history.market, annualEnds, cutoff);
  if (history.quality.issues.includes('quarterly-ytd-suspected')) {
    quarters = [];
    notes.push('季度 EPS 疑似為年初累計值，改用年度 EPS。');
  }
  return { quarters, annuals, notes };
}

const daysBetween = (a: number, b: number) => (b - a) / DAY_SECONDS;

/** EPS(TTM) as known at `obsSec`, or null when neither four published quarters nor a recent annual exist. */
export function epsAt(obsSec: number, periods: Pick<EpsPeriods, 'quarters' | 'annuals'>): EpsPoint | null {
  const published = periods.quarters.filter((period) => period.availSec <= obsSec);
  const last4 = published.slice(-4);
  if (last4.length === 4 && last4.every((period) => period.eps !== null)) {
    const consecutive = last4.every((period, i) => {
      if (i === 0) return true;
      const gap = daysBetween(last4[i - 1]!.endSec, period.endSec);
      return gap >= QUARTER_GAP_DAYS.min && gap <= QUARTER_GAP_DAYS.max;
    });
    const newest = last4[3]!;
    if (consecutive && daysBetween(newest.endSec, obsSec) <= MAX_TTM_AGE_DAYS) {
      return {
        eps: last4.reduce((sum, period) => sum + period.eps!, 0),
        basis: 'ttm',
        periodEnd: newest.end,
        usedBasic: last4.some((period) => period.basic),
      };
    }
  }
  const annual = periods.annuals.filter((period) => period.availSec <= obsSec).at(-1);
  if (annual && annual.eps !== null && daysBetween(annual.endSec, obsSec) <= MAX_ANNUAL_AGE_DAYS) {
    return { eps: annual.eps, basis: 'annual', periodEnd: annual.end, usedBasic: annual.basic };
  }
  return null;
}

/** Linear-interpolation percentile (numpy "linear") of an ascending array; p in 0..100. */
export function percentile(sorted: readonly number[], p: number): number {
  if (sorted.length === 0) return NaN;
  if (sorted.length === 1) return sorted[0]!;
  const position = (Math.min(100, Math.max(0, p)) / 100) * (sorted.length - 1);
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  return sorted[lower]! + (sorted[upper]! - sorted[lower]!) * (position - lower);
}

/** Mid-rank percent rank (0..100) of `value` in an ascending array: ties count half. */
export function percentRank(sorted: readonly number[], value: number): number {
  if (sorted.length === 0) return NaN;
  let below = 0;
  let equal = 0;
  for (const item of sorted) {
    if (item < value) below++;
    else if (item === value) equal++;
  }
  return ((below + equal / 2) / sorted.length) * 100;
}

export type ExcludedReason = 'no-eps' | 'loss' | 'outlier';

export interface PePoint {
  /** Bar start (Unix seconds), as the provider returns it. */
  time: number;
  close: number;
  eps: number | null;
  basis: EpsBasis | null;
  /** End date of the newest period behind `eps`. */
  periodEnd: string | null;
  pe: number | null;
  excluded: ExcludedReason | null;
}

export interface RiverLevel {
  p: RiverPercentile;
  multiple: number;
}

export interface CurrentPe {
  pe: number;
  /** Percent rank 0..100 within the valid history (the current point included). */
  percentile: number;
  /** (current ÷ median − 1) × 100. */
  premiumPct: number;
  median: number;
  eps: number;
  basis: EpsBasis;
  epsPeriodEnd: string;
  close: number;
  time: number;
}

export interface PeRiverModel {
  points: PePoint[];
  validCount: number;
  levels: RiverLevel[];
  current: CurrentPe | null;
  /** Why there is no current P/E although the river exists (latest EPS ≤ 0 or missing). */
  currentReason: string | null;
  /** Years from the first valid point to the last bar. */
  coverageYears: number;
  /** Years covered by the price bars. */
  priceYears: number;
  basisCounts: Record<EpsBasis, number>;
  /** Distinct EPS figures behind the valid points (the true number of independent observations). */
  epsPeriodCount: number;
  excluded: Record<ExcludedReason, number>;
  usedBasic: boolean;
  short: boolean;
  notes: string[];
  lastTime: number;
}

export type PeRiverUnavailableReason = 'no-bars' | 'price-basis' | 'foreign' | 'currency' | 'no-eps' | 'insufficient';

export type PeRiverResult =
  | { status: 'ok'; model: PeRiverModel }
  | {
      status: 'unavailable';
      reason: PeRiverUnavailableReason;
      message: string;
      /** Present for 'insufficient': what there was. */
      validCount?: number;
      epsPeriodCount?: number;
      coverageYears?: number;
      priceYears?: number;
      notes: string[];
    };

export interface PriceBar {
  time: number;
  close: number;
}

export interface PeRiverOptions {
  /** Data date in Unix seconds (the retrieval time); caps the observation date of the newest bar. */
  asOfSec: number;
  /** `BarResult.priceBasis`; anything but 'split-adjusted' makes the river unavailable. */
  priceBasis: string | undefined;
}

export const INSUFFICIENT_MESSAGE = '歷史資料不足，無法畫出本益比河流';

const unavailable = (reason: PeRiverUnavailableReason, message: string, extra: Partial<Extract<PeRiverResult, { status: 'unavailable' }>> = {}): PeRiverResult => ({
  status: 'unavailable',
  reason,
  message,
  notes: [],
  ...extra,
});

const YEAR_SECONDS = 365.25 * DAY_SECONDS;

/** Builds the P/E river model for one stock from its financials file and weekly (or monthly) bars. */
export function buildPeRiver(history: FinancialHistory, bars: readonly PriceBar[], options: PeRiverOptions): PeRiverResult {
  const sorted = bars.filter((bar) => Number.isFinite(bar.time) && Number.isFinite(bar.close) && bar.close > 0).sort((a, b) => a.time - b.time);
  if (sorted.length === 0) return unavailable('no-bars', '沒有股價資料，無法畫出本益比河流。');
  if (options.priceBasis !== 'split-adjusted')
    return unavailable('price-basis', '股價資料未確認已還原股票分割，與 EPS 口徑可能不一致，不畫本益比河流。');
  if (history.foreignReporter || history.quality.issues.includes('foreign-filer'))
    return unavailable('foreign', '外國發行人的 EPS 為每普通股，股價為每 ADR，口徑不同，不畫本益比河流。');
  const priceCurrency = history.market === 'TW' ? 'TWD' : 'USD';
  if (history.currency !== priceCurrency || history.quality.issues.includes('foreign-currency') || history.quality.issues.includes('currency-changed'))
    return unavailable('currency', `財報幣別（${history.currency}）與股價幣別（${priceCurrency}）不一致，不畫本益比河流。`);

  const periods = buildEpsPeriods(history);
  if (!periods.quarters.some((p) => p.eps !== null) && !periods.annuals.some((p) => p.eps !== null))
    return unavailable('no-eps', '沒有可用的 EPS 資料，無法畫出本益比河流。', { notes: periods.notes });

  const points: PePoint[] = [];
  const excluded: Record<ExcludedReason, number> = { 'no-eps': 0, loss: 0, outlier: 0 };
  const basisCounts: Record<EpsBasis, number> = { ttm: 0, annual: 0 };
  const epsKeys = new Set<string>();
  const valid: number[] = [];
  let usedBasic = false;
  let firstValid: number | null = null;
  for (const bar of sorted) {
    const observation = Math.min(bar.time + BAR_OBSERVATION_OFFSET_DAYS * DAY_SECONDS, options.asOfSec);
    const point = epsAt(observation, periods);
    if (!point) {
      excluded['no-eps']++;
      points.push({ time: bar.time, close: bar.close, eps: null, basis: null, periodEnd: null, pe: null, excluded: 'no-eps' });
      continue;
    }
    if (!(point.eps > 0)) {
      excluded.loss++;
      points.push({ time: bar.time, close: bar.close, eps: point.eps, basis: point.basis, periodEnd: point.periodEnd, pe: null, excluded: 'loss' });
      continue;
    }
    const pe = bar.close / point.eps;
    if (!(pe <= PE_MAX)) {
      excluded.outlier++;
      points.push({ time: bar.time, close: bar.close, eps: point.eps, basis: point.basis, periodEnd: point.periodEnd, pe: null, excluded: 'outlier' });
      continue;
    }
    basisCounts[point.basis]++;
    epsKeys.add(`${point.basis}|${point.periodEnd}`);
    valid.push(pe);
    usedBasic ||= point.usedBasic;
    firstValid ??= bar.time;
    points.push({ time: bar.time, close: bar.close, eps: point.eps, basis: point.basis, periodEnd: point.periodEnd, pe, excluded: null });
  }

  const lastBar = sorted[sorted.length - 1]!;
  const priceYears = (lastBar.time - sorted[0]!.time) / YEAR_SECONDS;
  const coverageYears = firstValid === null ? 0 : (lastBar.time - firstValid) / YEAR_SECONDS;
  if (valid.length < MIN_VALID_POINTS || epsKeys.size < MIN_EPS_PERIODS)
    return unavailable('insufficient', INSUFFICIENT_MESSAGE, {
      validCount: valid.length,
      epsPeriodCount: epsKeys.size,
      coverageYears,
      priceYears,
      notes: periods.notes,
    });

  const ascending = [...valid].sort((a, b) => a - b);
  const levels: RiverLevel[] = RIVER_PERCENTILES.map((p) => ({ p, multiple: percentile(ascending, p) }));
  const median = percentile(ascending, 50);
  const lastPoint = points[points.length - 1]!;
  let current: CurrentPe | null = null;
  let currentReason: string | null = null;
  if (lastPoint.pe !== null && lastPoint.basis !== null && lastPoint.eps !== null && lastPoint.periodEnd !== null) {
    current = {
      pe: lastPoint.pe,
      percentile: percentRank(ascending, lastPoint.pe),
      premiumPct: (lastPoint.pe / median - 1) * 100,
      median,
      eps: lastPoint.eps,
      basis: lastPoint.basis,
      epsPeriodEnd: lastPoint.periodEnd,
      close: lastPoint.close,
      time: lastPoint.time,
    };
  } else {
    currentReason =
      lastPoint.excluded === 'loss'
        ? '最近的 EPS 為負值（虧損），目前本益比不適用。'
        : lastPoint.excluded === 'outlier'
          ? `目前本益比高於 ${PE_MAX} 倍（EPS 趨近零），視為無意義。`
          : '最新一期沒有可用的 EPS，無法計算目前本益比。';
  }
  const notes = [...periods.notes];
  return {
    status: 'ok',
    model: {
      points,
      validCount: valid.length,
      levels,
      current,
      currentReason,
      coverageYears,
      priceYears,
      basisCounts,
      epsPeriodCount: epsKeys.size,
      excluded,
      usedBasic,
      short: coverageYears < SHORT_COVERAGE_YEARS || epsKeys.size < SHORT_EPS_PERIODS,
      notes,
      lastTime: lastBar.time,
    },
  };
}

/** Price of one river line at every bar: EPS × multiple where the P/E is valid, otherwise null (gap). */
export function riverLine(model: PeRiverModel, level: RiverLevel): (number | null)[] {
  return model.points.map((point) => (point.pe !== null && point.eps !== null ? point.eps * level.multiple : null));
}

export type PeZone = 'very-low' | 'low' | 'middle' | 'high' | 'very-high';

/** Where a percent rank sits against the P10 / P30 / P70 / P90 lines. */
export function zoneOf(percentRankValue: number): PeZone {
  if (percentRankValue < 10) return 'very-low';
  if (percentRankValue < 30) return 'low';
  if (percentRankValue < 70) return 'middle';
  if (percentRankValue < 90) return 'high';
  return 'very-high';
}

export const ZONE_TEXT: Record<PeZone, string> = {
  'very-low': '低於歷史 P10，落在自己歷史最便宜的一成',
  low: '介於 P10–P30，相對自己的歷史偏低',
  middle: '介於 P30–P70，落在自己歷史的中間區',
  high: '介於 P70–P90，相對自己的歷史偏高',
  'very-high': '高於歷史 P90，落在自己歷史最貴的一成',
};

/** 12.34 → 「12.3x」. */
export const formatMultiple = (value: number): string => `${value.toFixed(1)}x`;
